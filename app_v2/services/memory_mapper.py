from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from app_v2.domain.enums import EventType, MemoryOrigin, MemoryStatus, ScopeType
from app_v2.domain.events import EventEnvelope
from app_v2.domain.memory import MemoryCard, MemoryEvidence, UsagePolicy
from app_v2.services.model_router import ModelRole


_ALLOWED_TYPES = {
    "goal", "project", "commitment", "decision", "plan", "event",
    "preference", "observation", "contradiction", "quote",
    "running_joke", "pattern",
}
_DIRECT_STATEMENT_TYPES = {"commitment", "decision", "quote"}
_STATEMENT_KINDS = {
    "none", "commitment", "decision", "prediction", "boast", "rule", "quote",
}
_STATEMENT_STATUSES = {
    "unknown", "open", "active", "kept", "broken", "missed", "fulfilled",
}
_CLAIM_KINDS = {"fact", "plan", "estimate", "conditional", "unknown"}
_EPISODE_GAP = timedelta(hours=6)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "memory_type": {"type": "string", "enum": sorted(_ALLOWED_TYPES)},
                    "semantic_key": {"type": "string", "minLength": 1},
                    "summary": {"type": "string", "minLength": 1},
                    "subject_keys": {"type": "array", "items": {"type": "string"}},
                    "source_message_id": {"type": "string", "minLength": 1},
                    "evidence_excerpt": {"type": "string", "minLength": 1},
                    "payload": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "statement_kind": {"type": "string", "enum": sorted(_STATEMENT_KINDS)},
                            "status": {"type": "string", "enum": sorted(_STATEMENT_STATUSES)},
                            "due_at": {"type": ["string", "null"]},
                            "verbatim": {"type": ["string", "null"]},
                            "claim_kind": {"type": "string", "enum": sorted(_CLAIM_KINDS)},
                        },
                        "required": ["statement_kind", "status", "due_at", "verbatim", "claim_kind"],
                    },
                    "importance": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "usage_policy": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "assist": {"type": "boolean"},
                            "callback": {"type": "boolean"},
                            "roast": {"type": "boolean"},
                            "proactive": {"type": "boolean"},
                        },
                        "required": ["assist", "callback", "roast", "proactive"],
                    },
                },
                "required": [
                    "memory_type", "semantic_key", "summary", "subject_keys",
                    "source_message_id", "evidence_excerpt", "payload",
                    "importance", "confidence", "usage_policy",
                ],
            },
        }
    },
    "required": ["candidates"],
}


@dataclass(frozen=True)
class MapperResult:
    written: tuple[MemoryCard, ...] = ()
    forgotten_ids: tuple[str, ...] = ()
    failed: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class _TrustedSource:
    message_id: str
    author_id: str | None
    timestamp: datetime
    text: str


class MemoryMapperStore:
    """Small persistence facade for mapper-specific semantic merge operations."""

    def __init__(self, memory_repo: Any) -> None:
        self.repo = memory_repo

    def find_semantic_match(
        self,
        scope_type: ScopeType,
        scope_id: str,
        semantic_key: str,
    ) -> MemoryCard | None:
        conn = getattr(self.repo, "conn", None)
        if conn is None:
            finder = getattr(self.repo, "find_semantic_match", None)
            return finder(scope_type, scope_id, semantic_key) if finder else None

        row = conn.execute(
            """
            SELECT id
            FROM memory_cards
            WHERE scope_type=%s
              AND scope_id=%s
              AND status IN ('candidate','active')
              AND payload ->> 'semantic_key' = %s
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (scope_type.value, scope_id, semantic_key),
        ).fetchone()
        if not row:
            return None
        return self.repo.get(scope_type, scope_id, row[0])

    def find_semantic_match_for_subject(
        self,
        scope_type: ScopeType,
        scope_id: str,
        semantic_key: str,
        subject_user_key: str,
    ) -> MemoryCard | None:
        """Find a semantic card owned by the same trusted person subject."""

        conn = getattr(self.repo, "conn", None)
        if conn is None:
            finder = getattr(self.repo, "find_semantic_match_for_subject", None)
            if finder is not None:
                return finder(scope_type, scope_id, semantic_key, subject_user_key)
            existing = self.find_semantic_match(scope_type, scope_id, semantic_key)
            if existing is None or subject_user_key not in existing.subject_keys:
                return None
            return existing

        row = conn.execute(
            """
            SELECT id
            FROM memory_cards
            WHERE scope_type=%s
              AND scope_id=%s
              AND status IN ('candidate','active')
              AND payload ->> 'semantic_key' = %s
              AND %s = ANY(subject_keys)
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (scope_type.value, scope_id, semantic_key, subject_user_key),
        ).fetchone()
        if not row:
            return None
        return self.repo.get(scope_type, scope_id, row[0])

    def find_source_match(
        self,
        scope_type: ScopeType,
        scope_id: str,
        memory_type: str,
        evidence: MemoryEvidence,
    ) -> MemoryCard | None:
        """Find an exact source+excerpt match for ordinary retry detection."""

        conn = getattr(self.repo, "conn", None)
        if conn is None:
            finder = getattr(self.repo, "find_source_match", None)
            return finder(scope_type, scope_id, memory_type, evidence) if finder else None

        probe = json.dumps(
            [{
                "message_id": evidence.message_id,
                "author_id": evidence.author_id,
                "excerpt": evidence.excerpt,
            }],
            ensure_ascii=False,
        )
        row = conn.execute(
            """
            SELECT id
            FROM memory_cards
            WHERE scope_type=%s
              AND scope_id=%s
              AND status IN ('candidate','active')
              AND memory_type=%s
              AND evidence @> %s::jsonb
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (scope_type.value, scope_id, memory_type, probe),
        ).fetchone()
        if not row:
            return None
        return self.repo.get(scope_type, scope_id, row[0])

    def find_source_identity_matches(
        self,
        scope_type: ScopeType,
        scope_id: str,
        evidence: MemoryEvidence,
    ) -> tuple[MemoryCard, ...]:
        """Find cards grounded in the same stable Telegram source identity.

        Excerpt and semantic key are intentionally excluded. This lookup is
        used only for EDITED_MESSAGE reconciliation. Multiple matches are kept
        visible so the mapper can fail conservatively instead of overwriting an
        arbitrary card from a long multi-memory source message.
        """

        conn = getattr(self.repo, "conn", None)
        if conn is None:
            finder = getattr(self.repo, "find_source_identity_matches", None)
            if finder is None:
                return ()
            value = finder(scope_type, scope_id, evidence)
            return tuple(value or ())

        rows = conn.execute(
            """
            SELECT DISTINCT mc.id
            FROM memory_cards mc
            WHERE mc.scope_type=%s
              AND mc.scope_id=%s
              AND mc.status IN ('candidate','active')
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(mc.evidence) AS ev
                  WHERE ev ->> 'message_id' = %s
                    AND COALESCE(ev ->> 'author_id', '') = %s
              )
            ORDER BY mc.id
            """,
            (
                scope_type.value,
                scope_id,
                str(evidence.message_id or ""),
                str(evidence.author_id or ""),
            ),
        ).fetchall()
        cards: list[MemoryCard] = []
        for row in rows:
            card = self.repo.get(scope_type, scope_id, row[0])
            if card is not None:
                cards.append(card)
        return tuple(cards)

    @contextmanager
    def _session_locks(self, materials: Iterable[str]) -> Iterator[None]:
        conn = getattr(self.repo, "conn", None)
        ordered = sorted({item for item in materials if item})
        if conn is None or not ordered:
            yield
            return

        acquired: list[str] = []
        try:
            for material in ordered:
                conn.execute(
                    "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
                    (material,),
                ).fetchone()
                acquired.append(material)
            # Session locks survive the create/update commits below.
            conn.commit()
            yield
        finally:
            try:
                conn.rollback()
            except Exception:
                pass
            for material in reversed(acquired):
                try:
                    conn.execute(
                        "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                        (material,),
                    ).fetchone()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
            try:
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

    @contextmanager
    def source_lock(
        self,
        scope_type: ScopeType,
        scope_id: str,
        evidence: MemoryEvidence,
    ) -> Iterator[None]:
        material = "|".join(
            (
                "source",
                scope_type.value,
                scope_id,
                str(evidence.message_id or ""),
                str(evidence.author_id or ""),
            )
        )
        with self._session_locks((material,)):
            yield

    @contextmanager
    def semantic_locks(
        self,
        scope_type: ScopeType,
        scope_id: str,
        semantic_keys: Iterable[str],
    ) -> Iterator[None]:
        materials = [
            "|".join(("semantic", scope_type.value, scope_id, key))
            for key in semantic_keys
            if key
        ]
        with self._session_locks(materials):
            yield

    @contextmanager
    def semantic_lock(
        self,
        scope_type: ScopeType,
        scope_id: str,
        semantic_key: str,
    ) -> Iterator[None]:
        with self.semantic_locks(scope_type, scope_id, (semantic_key,)):
            yield

    def create(self, card: MemoryCard) -> MemoryCard:
        return self.repo.create(card)

    def update(self, card: MemoryCard) -> MemoryCard:
        return self.repo.update(card)

    def archive(self, scope_type: ScopeType, scope_id: str, memory_id: str) -> bool:
        return self.repo.archive(scope_type, scope_id, memory_id)


class MemoryMapper:
    def __init__(
        self,
        *,
        store: Any,
        adapter: Any | None = None,
        prompt_path: Path | None = None,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.prompt_path = prompt_path or Path(__file__).resolve().parents[1] / "prompts" / "memory_mapper.md"

    def map_event(
        self,
        event: EventEnvelope,
        *,
        target_memory_ids: Iterable[str] = (),
        recent_context: Iterable[dict[str, Any]] | str = (),
    ) -> MapperResult:
        text = (event.text or "").strip()
        if not text:
            return MapperResult(reason="no_text")

        lowered = text.lower()
        if lowered in {"забудь это", "забудь", "forget this", "forget it"}:
            targets = tuple(dict.fromkeys(str(item) for item in target_memory_ids if str(item).strip()))
            if not targets:
                return MapperResult(reason="forget_target_ambiguous")
            forgotten: list[str] = []
            for memory_id in targets:
                try:
                    archived = self.store.archive(event.scope_type, event.scope_id, memory_id)
                except Exception as exc:
                    return MapperResult(
                        forgotten_ids=tuple(forgotten),
                        failed=True,
                        reason=f"mapper_failure:{type(exc).__name__}",
                    )
                if archived:
                    forgotten.append(memory_id)
            return MapperResult(forgotten_ids=tuple(forgotten), reason="explicit_forget")

        explicit = self._extract_explicit_remember(text)
        if explicit is not None:
            source_id = str(event.message_id or event.event_id)
            candidate = {
                "memory_type": "observation",
                "semantic_key": self._semantic_key("explicit", explicit),
                "summary": explicit,
                "subject_keys": self._default_subject_keys(event),
                "source_message_id": source_id,
                "evidence_excerpt": explicit,
                "payload": {
                    "statement_kind": "none",
                    "status": "active",
                    "due_at": None,
                    "verbatim": explicit,
                    "claim_kind": "unknown",
                },
                "importance": 0.9,
                "confidence": 0.98,
                "usage_policy": {
                    "assist": True,
                    "callback": True,
                    "roast": False,
                    "proactive": False,
                },
            }
            if event.event_type is EventType.EDITED_MESSAGE:
                candidates = self._assign_source_candidate_slots(event, (candidate,), ())
                if len(candidates) != 1:
                    return MapperResult(failed=True, reason="edited_source_invalid_candidate")
                return self._reconcile_edited_source(event, candidates, (), explicit=True)
            try:
                card = self._upsert_candidate(event, candidate, explicit=True, recent_context=())
            except Exception as exc:
                return MapperResult(failed=True, reason=f"mapper_failure:{type(exc).__name__}")
            return MapperResult(written=(card,) if card is not None else (), reason="explicit_remember")

        context_items = self._normalize_context(recent_context)
        if self._needs_context(text) and not context_items:
            return MapperResult(reason="insufficient_context")
        if self.adapter is None:
            return MapperResult(reason="no_mapper_adapter")

        try:
            prompt = self.prompt_path.read_text(encoding="utf-8")
            input_text = json.dumps(
                {
                    "event": event.model_dump(mode="json"),
                    "recent_context": context_items,
                },
                ensure_ascii=False,
            )
            result = self.adapter.generate_json(
                ModelRole.MEMORY,
                input_text,
                schema_name="nenoy_memory_mapper",
                schema=_SCHEMA,
                instructions=prompt,
                event_id=event.event_id,
                max_output_tokens=1400,
            )
        except Exception as exc:
            return MapperResult(failed=True, reason=f"mapper_failure:{type(exc).__name__}")

        parsed = result.parsed or {"candidates": []}
        raw_candidates = parsed.get("candidates", [])
        if not isinstance(raw_candidates, list):
            return MapperResult(failed=True, reason="mapper_invalid_candidates")
        candidates = [
            dict(candidate)
            for candidate in raw_candidates
            if isinstance(candidate, dict) and candidate.get("memory_type") in _ALLOWED_TYPES
        ]
        rejected_candidate = len(candidates) != len(raw_candidates)
        if event.event_type is EventType.EDITED_MESSAGE and rejected_candidate:
            return MapperResult(failed=True, reason="edited_source_invalid_candidate")
        positioned_candidates = self._assign_source_candidate_slots(event, candidates, context_items)
        rejected_candidate = rejected_candidate or len(positioned_candidates) != len(candidates)
        if event.event_type is EventType.EDITED_MESSAGE:
            # A genuinely empty mapper result means the edited source no longer
            # contains a memory candidate. If candidates existed but failed
            # provenance validation, fail closed instead of erasing old memory.
            if candidates and len(positioned_candidates) != len(candidates):
                return MapperResult(failed=True, reason="edited_source_invalid_candidate")
            return self._reconcile_edited_source(event, positioned_candidates, context_items)
        candidates = positioned_candidates

        written: list[MemoryCard] = []
        for candidate in candidates:
            evidence = self._candidate_evidence(event, candidate, context_items)
            if (
                not str(candidate.get("semantic_key") or "").strip()
                or evidence is None
                or self._trusted_subject_keys(candidate, evidence) is None
            ):
                rejected_candidate = True
                continue
            try:
                card = self._upsert_candidate(
                    event,
                    candidate,
                    explicit=False,
                    recent_context=context_items,
                )
            except Exception as exc:
                # MemoryRepository currently commits card writes individually.
                # Preserve already-committed IDs so the receipt can say partial
                # instead of falsely claiming that nothing changed.
                return MapperResult(
                    written=tuple(written),
                    failed=True,
                    reason=f"mapper_failure:{type(exc).__name__}",
                )
            if card is not None:
                written.append(card)
        if rejected_candidate:
            return MapperResult(
                written=tuple(written),
                failed=True,
                reason="mapper_rejected_candidate_provenance",
            )
        return MapperResult(written=tuple(written))

    def _assign_source_candidate_slots(
        self,
        event: EventEnvelope,
        candidates: Iterable[dict[str, Any]],
        recent_context: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Assign deterministic positions to candidates grounded in each source."""

        sources = self._trusted_sources(event, recent_context)
        positioned: list[tuple[str, int, int, dict[str, Any]]] = []
        for order, candidate in enumerate(candidates):
            evidence = self._candidate_evidence(event, candidate, recent_context)
            if evidence is None:
                continue
            source = sources.get(str(evidence.message_id or ""))
            offset = source.text.find(evidence.excerpt) if source is not None else -1
            positioned.append((str(evidence.message_id or ""), offset, order, candidate))

        slots: dict[int, int] = {}
        grouped: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
        for source_id, offset, order, candidate in positioned:
            grouped.setdefault(source_id, []).append((offset, order, candidate))
        for items in grouped.values():
            for slot, (_, order, _) in enumerate(sorted(items, key=lambda item: (item[0], item[1]))):
                slots[order] = slot

        result: list[dict[str, Any]] = []
        for _, _, order, candidate in positioned:
            value = dict(candidate)
            value["_source_candidate_index"] = slots[order]
            result.append(value)
        return result

    def _detach_source_from_card(
        self,
        event: EventEnvelope,
        card: MemoryCard,
        source_evidence: MemoryEvidence,
    ) -> tuple[MemoryCard | None, str | None]:
        """Remove one trusted source from a card without rewriting other provenance."""

        source_identity = self._source_identity(source_evidence)
        remaining = [
            item for item in card.evidence
            if self._source_identity(item) != source_identity
        ]
        if len(remaining) == len(card.evidence):
            return None, None
        if not remaining:
            archived = self.store.archive(event.scope_type, event.scope_id, card.id)
            return None, card.id if archived else None

        unique_sources = self._unique_sources(remaining)
        source_count = len(unique_sources)
        episode_count = self._episode_count(unique_sources)
        anchor = unique_sources[-1]
        payload = dict(card.payload)
        payload.update(
            {
                "source_message_id": anchor.message_id,
                "source_author_id": anchor.author_id,
                "evidence_count": source_count,
                "episode_count": episode_count,
                "episode_policy": "unique_source_messages_6h_window",
            }
        )
        # Candidate slot is source-local. Once that source is detached we can no
        # longer prove the slot for the remaining provenance, so fail closed on
        # a future ambiguous edit instead of carrying a false mapping.
        payload.pop("source_candidate_index", None)

        memory_type = card.memory_type
        status = card.status
        if memory_type == "pattern" and (source_count < 3 or episode_count < 2):
            memory_type = "observation"
            status = MemoryStatus.CANDIDATE

        updated = card.model_copy(
            update={
                "memory_type": memory_type,
                "payload": payload,
                "evidence": remaining,
                "source_count": source_count,
                "status": status,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return self.store.update(updated), None

    def _reconcile_edited_source(
        self,
        event: EventEnvelope,
        candidates: list[dict[str, Any]],
        recent_context: Iterable[dict[str, Any]],
        *,
        explicit: bool = False,
    ) -> MapperResult:
        """Reconcile every card grounded in an edited current source as one batch."""

        current_source_id = str(event.message_id or event.event_id)
        source_evidence = MemoryEvidence(
            message_id=current_source_id,
            author_id=event.actor_user_id,
            timestamp=event.occurred_at,
            excerpt=(event.text or "").strip(),
        )
        prepared: list[tuple[dict[str, Any], MemoryEvidence, list[str]]] = []
        for candidate in candidates:
            evidence = self._candidate_evidence(event, candidate, recent_context)
            if evidence is None or evidence.message_id != current_source_id:
                return MapperResult(failed=True, reason="edited_source_invalid_candidate")
            subjects = self._trusted_subject_keys(candidate, evidence)
            if subjects is None:
                return MapperResult(failed=True, reason="edited_source_invalid_candidate")
            prepared.append((candidate, evidence, subjects))

        source_lock_factory = getattr(self.store, "source_lock", None)
        source_context = (
            source_lock_factory(event.scope_type, event.scope_id, source_evidence)
            if callable(source_lock_factory)
            else nullcontext()
        )
        with source_context:
            stable_cards = self._source_identity_matches(event, source_evidence)
            old_keys = [str(card.payload.get("semantic_key") or "") for card in stable_cards]
            new_keys = [str(candidate["semantic_key"]).strip() for candidate, _, _ in prepared]
            locks_factory = getattr(self.store, "semantic_locks", None)
            locks = (
                locks_factory(event.scope_type, event.scope_id, (*old_keys, *new_keys))
                if callable(locks_factory)
                else nullcontext()
            )
            with locks:
                stable_cards = self._source_identity_matches(event, source_evidence)
                written: list[MemoryCard] = []
                forgotten: list[str] = []

                if not prepared:
                    try:
                        for stale in stable_cards:
                            kept, archived_id = self._detach_source_from_card(event, stale, source_evidence)
                            if kept is not None:
                                written.append(kept)
                            if archived_id is not None:
                                forgotten.append(archived_id)
                    except Exception as exc:
                        return MapperResult(
                            written=tuple(written),
                            forgotten_ids=tuple(forgotten),
                            failed=True,
                            reason=f"mapper_failure:{type(exc).__name__}",
                        )
                    return MapperResult(
                        written=tuple(written),
                        forgotten_ids=tuple(forgotten),
                        reason="edited_source_cleared",
                    )

                # Resolve the full old↔new mapping before the first persistence
                # mutation. Exact semantic identity is stronger than positional
                # slots because removing an earlier fact renumbers current slots.
                # Positional fallback is allowed only when cardinality is stable
                # and the remaining mapping is provably one-to-one.
                semantic_cards: dict[str, list[MemoryCard]] = {}
                for card in stable_cards:
                    key = str(card.payload.get("semantic_key") or "").strip()
                    if key:
                        semantic_cards.setdefault(key, []).append(card)

                resolved_matches: dict[int, MemoryCard | None] = {}
                mapped_ids: set[str] = set()
                pending: list[int] = []
                seen_new_keys: set[str] = set()
                for index, (candidate, _evidence, _subjects) in enumerate(prepared):
                    semantic_key = str(candidate["semantic_key"]).strip()
                    if semantic_key in seen_new_keys:
                        return MapperResult(
                            failed=True,
                            reason="edited_source_reconciliation_ambiguous",
                        )
                    seen_new_keys.add(semantic_key)
                    matches = [
                        card for card in semantic_cards.get(semantic_key, ())
                        if card.id not in mapped_ids
                    ]
                    if len(matches) > 1:
                        return MapperResult(
                            failed=True,
                            reason="edited_source_reconciliation_ambiguous",
                        )
                    if len(matches) == 1:
                        resolved_matches[index] = matches[0]
                        mapped_ids.add(matches[0].id)
                    else:
                        pending.append(index)

                unmatched_stable = [card for card in stable_cards if card.id not in mapped_ids]
                if pending and unmatched_stable:
                    if len(pending) == 1 and len(unmatched_stable) == 1:
                        # With exactly one old and one new unmatched object, the
                        # mapping is unambiguous even if a legacy/shared card no
                        # longer carries a source-local slot.
                        index = pending[0]
                        resolved_matches[index] = unmatched_stable[0]
                        mapped_ids.add(unmatched_stable[0].id)
                    elif len(prepared) == len(stable_cards) and len(pending) == len(unmatched_stable):
                        by_slot: dict[int, MemoryCard] = {}
                        for card in unmatched_stable:
                            slot = card.payload.get("source_candidate_index")
                            if not isinstance(slot, int) or slot in by_slot:
                                return MapperResult(
                                    failed=True,
                                    reason="edited_source_reconciliation_ambiguous",
                                )
                            by_slot[slot] = card
                        slot_mapped_ids: set[str] = set()
                        for index in pending:
                            candidate = prepared[index][0]
                            slot = candidate.get("_source_candidate_index")
                            if not isinstance(slot, int):
                                return MapperResult(
                                    failed=True,
                                    reason="edited_source_reconciliation_ambiguous",
                                )
                            matched = by_slot.get(slot)
                            if matched is None or matched.id in slot_mapped_ids:
                                return MapperResult(
                                    failed=True,
                                    reason="edited_source_reconciliation_ambiguous",
                                )
                            resolved_matches[index] = matched
                            slot_mapped_ids.add(matched.id)
                            mapped_ids.add(matched.id)
                    else:
                        # Candidate insertion/removal plus an identity change is
                        # not safely resolvable from position. Reject before any
                        # detach/archive/update rather than guessing.
                        return MapperResult(
                            failed=True,
                            reason="edited_source_reconciliation_ambiguous",
                        )
                elif pending:
                    # All existing cards were matched semantically; remaining
                    # candidates are genuine additions to this source.
                    for index in pending:
                        resolved_matches[index] = None

                used_ids: set[str] = set()
                try:
                    for index, (candidate, evidence, subjects) in enumerate(prepared):
                        matched = resolved_matches[index]
                        semantic_key = str(candidate["semantic_key"]).strip()
                        old_semantic_key = (
                            str(matched.payload.get("semantic_key") or "").strip()
                            if matched is not None else ""
                        )
                        shared_sources = (
                            len(self._unique_sources(matched.evidence)) > 1
                            if matched is not None else False
                        )

                        # If the correction changes logical identity, detach
                        # only the edited source before writing the new claim.
                        # This is required not only for corroborated cards but
                        # also when the destination semantic card already
                        # exists: otherwise _upsert_candidate_locked() refuses
                        # the cross-card merge and the stale source card is
                        # incorrectly treated as retained.
                        stable_for_upsert: tuple[MemoryCard, ...] = (matched,) if matched else ()
                        semantic_destination = self._find_semantic_candidate_match(
                            event,
                            candidate,
                            evidence,
                            str(candidate["memory_type"]),
                            semantic_key,
                            subjects,
                            explicit=explicit,
                        )
                        destination_is_other_card = (
                            matched is not None
                            and semantic_destination is not None
                            and semantic_destination.id != matched.id
                        )
                        if (
                            matched is not None
                            and old_semantic_key != semantic_key
                            and (shared_sources or destination_is_other_card)
                        ):
                            kept, archived_id = self._detach_source_from_card(event, matched, source_evidence)
                            used_ids.add(matched.id)
                            if kept is not None:
                                written.append(kept)
                            if archived_id is not None:
                                forgotten.append(archived_id)
                            stable_for_upsert = ()

                        card = self._upsert_candidate_locked(
                            event,
                            candidate,
                            explicit=explicit,
                            evidence=evidence,
                            subject_keys=subjects,
                            requested_memory_type=str(candidate["memory_type"]),
                            semantic_key=semantic_key,
                            stable_source_cards=stable_for_upsert,
                        )
                        if matched:
                            used_ids.add(matched.id)
                        if card is not None:
                            written.append(card)

                    for stale in stable_cards:
                        if stale.id not in used_ids:
                            kept, archived_id = self._detach_source_from_card(event, stale, source_evidence)
                            if kept is not None:
                                written.append(kept)
                            if archived_id is not None:
                                forgotten.append(archived_id)
                except Exception as exc:
                    return MapperResult(
                        written=tuple(written),
                        forgotten_ids=tuple(forgotten),
                        failed=True,
                        reason=f"mapper_failure:{type(exc).__name__}",
                    )
                return MapperResult(
                    written=tuple(written),
                    forgotten_ids=tuple(forgotten),
                )

    @staticmethod
    def _extract_explicit_remember(text: str) -> str | None:
        lowered = text.lower()
        for prefix in ("запомни:", "запомни ", "remember:", "remember "):
            if lowered.startswith(prefix):
                value = text[len(prefix):].strip()
                return value or None
        return None

    @staticmethod
    def _needs_context(text: str) -> bool:
        normalized = " ".join(text.lower().replace("ё", "е").split()).strip(" .,!?:;")
        return normalized in {"я тоже", "да я тоже", "да, я тоже", "тоже", "у меня тоже", "мне тоже"}

    @staticmethod
    def _semantic_key(kind: str, text: str) -> str:
        normalized = " ".join(text.lower().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
        return f"{kind}:{digest}"

    @staticmethod
    def _default_subject_keys(event: EventEnvelope) -> list[str]:
        if event.actor_user_id:
            return [f"user:{event.actor_user_id}"]
        return []

    @staticmethod
    def _normalize_context(value: Iterable[dict[str, Any]] | str) -> list[dict[str, Any]]:
        raw: Any = value
        if isinstance(value, str):
            try:
                raw = json.loads(value)
            except Exception:
                return []
        if not isinstance(raw, (list, tuple)):
            return []
        result: list[dict[str, Any]] = []
        for item in raw[-12:]:
            if not isinstance(item, dict):
                continue
            if not str(item.get("message_id") or "").strip() or not str(item.get("text") or "").strip():
                continue
            result.append(dict(item))
        return result

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed

    def _trusted_sources(
        self,
        event: EventEnvelope,
        recent_context: Iterable[dict[str, Any]],
    ) -> dict[str, _TrustedSource]:
        sources: dict[str, _TrustedSource] = {}
        current_id = str(event.message_id or event.event_id)
        current_text = (event.text or "").strip()
        if current_text:
            sources[current_id] = _TrustedSource(
                message_id=current_id,
                author_id=event.actor_user_id,
                timestamp=event.occurred_at,
                text=current_text,
            )

        for item in recent_context:
            message_id = str(item.get("message_id") or "").strip()
            text = str(item.get("text") or "").strip()
            timestamp = self._parse_timestamp(item.get("created_at"))
            if not message_id or not text or timestamp is None:
                continue
            if timestamp > event.occurred_at:
                continue
            if message_id == current_id:
                continue
            sources[message_id] = _TrustedSource(
                message_id=message_id,
                author_id=(str(item.get("author_user_id")) if item.get("author_user_id") is not None else None),
                timestamp=timestamp,
                text=text,
            )
        return sources

    @staticmethod
    def _validated_excerpt(source_text: str, requested: str) -> str | None:
        excerpt = requested.strip()
        if not excerpt:
            return None
        direct = source_text.find(excerpt)
        if direct >= 0:
            return source_text[direct : direct + len(excerpt)]
        folded_source = source_text.casefold()
        folded_excerpt = excerpt.casefold()
        pos = folded_source.find(folded_excerpt)
        if pos >= 0:
            return source_text[pos : pos + len(excerpt)]
        return None

    def _candidate_evidence(
        self,
        event: EventEnvelope,
        candidate: dict[str, Any],
        recent_context: Iterable[dict[str, Any]],
    ) -> MemoryEvidence | None:
        sources = self._trusted_sources(event, recent_context)
        source_id = str(candidate.get("source_message_id") or event.message_id or event.event_id)
        source = sources.get(source_id)
        if source is None:
            return None

        requested = str(candidate.get("evidence_excerpt") or "").strip()
        if not requested:
            payload = candidate.get("payload") or {}
            requested = str(payload.get("verbatim") or "").strip()
        if not requested and source_id == str(event.message_id or event.event_id) and len(source.text) <= 240:
            requested = source.text
        excerpt = self._validated_excerpt(source.text, requested)
        if excerpt is None:
            return None
        return MemoryEvidence(
            message_id=source.message_id,
            author_id=source.author_id,
            timestamp=source.timestamp,
            excerpt=excerpt,
        )

    @staticmethod
    def _source_identity(evidence: MemoryEvidence) -> tuple[str | None, str | None]:
        return (evidence.message_id, evidence.author_id)

    @classmethod
    def _unique_sources(cls, evidence_items: Iterable[MemoryEvidence]) -> list[MemoryEvidence]:
        result: list[MemoryEvidence] = []
        seen: set[tuple[str | None, str | None]] = set()
        for item in evidence_items:
            identity = cls._source_identity(item)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(item)
        return result

    @classmethod
    def _episode_count(cls, evidence_items: Iterable[MemoryEvidence]) -> int:
        sources = sorted(cls._unique_sources(evidence_items), key=lambda item: item.timestamp)
        if not sources:
            return 0
        episodes = 1
        episode_start = sources[0].timestamp
        for item in sources[1:]:
            if item.timestamp - episode_start >= _EPISODE_GAP:
                episodes += 1
                episode_start = item.timestamp
        return episodes

    @staticmethod
    def _memory_id(
        event: EventEnvelope,
        requested_memory_type: str,
        semantic_key: str,
        evidence: MemoryEvidence,
    ) -> str:
        material = "|".join(
            (
                event.scope_type.value,
                event.scope_id,
                requested_memory_type,
                str(evidence.message_id or event.event_id),
                str(evidence.author_id or ""),
                semantic_key,
            )
        )
        return f"mem_{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"

    @staticmethod
    def _trusted_subject_keys(
        candidate: dict[str, Any],
        evidence: MemoryEvidence,
    ) -> list[str] | None:
        """Keep user subject identity grounded in provenance.

        Non-user semantic keys (project/topic tags) may be retained. A model
        cannot assign a source-grounded person claim to a different user unless
        a future code-validated relation explicitly supports that case.
        """

        model_keys = [str(item).strip() for item in candidate.get("subject_keys", []) if str(item).strip()]
        trusted_user = f"user:{evidence.author_id}" if evidence.author_id else None
        model_user_keys = [item for item in model_keys if item.startswith("user:")]
        if model_user_keys:
            if trusted_user is None or any(item != trusted_user for item in model_user_keys):
                return None
        non_user = [item for item in model_keys if not item.startswith("user:")]
        if trusted_user:
            return [*non_user, trusted_user]
        return non_user

    @staticmethod
    def _person_specific_subject_key(
        candidate: dict[str, Any],
        evidence: MemoryEvidence,
        requested_memory_type: str,
        trusted_subject_keys: Iterable[str],
        *,
        explicit: bool,
    ) -> str | None:
        if not evidence.author_id:
            return None

        trusted_user = f"user:{evidence.author_id}"
        trusted_subjects = {
            str(item).strip()
            for item in trusted_subject_keys
            if str(item).strip()
        }
        trusted_targets_author = trusted_user in trusted_subjects

        # Explicit remember is an intentional personal-memory operation. Its
        # subject is assigned from trusted source provenance by construction,
        # so it remains author-bound even when Russian first-person verb forms
        # omit an explicit pronoun (for example, ``люблю чай``). Never use the
        # explicit signal to accept a conflicting model-provided user subject.
        if explicit and trusted_targets_author:
            return trusted_user
        if requested_memory_type in _DIRECT_STATEMENT_TYPES:
            return trusted_user
        if requested_memory_type in {"goal", "plan", "preference"} and trusted_targets_author:
            return trusted_user

        # Explicit remembers and model observations may still be speaker-bound
        # even though their stored type is "observation". Require both trusted
        # subject provenance and first-person wording so generic project facts
        # remain corroboratable across participants.
        normalized = (
            (evidence.excerpt or "")
            .lower()
            .replace("ё", "е")
            .replace(",", " ")
            .replace(".", " ")
            .replace("!", " ")
            .replace("?", " ")
            .replace(":", " ")
            .replace(";", " ")
        )
        tokens = set(normalized.split())
        first_person_tokens = {
            "я", "мне", "меня", "мной", "мой", "моя", "мое", "мои",
            "мою", "моего", "моей", "моем", "моим", "моими",
            "i", "me", "my", "mine",
        }
        if trusted_targets_author and tokens.intersection(first_person_tokens):
            return trusted_user
        return None

    def _find_semantic_candidate_match(
        self,
        event: EventEnvelope,
        candidate: dict[str, Any],
        evidence: MemoryEvidence,
        requested_memory_type: str,
        semantic_key: str,
        trusted_subject_keys: Iterable[str],
        *,
        explicit: bool,
    ) -> MemoryCard | None:
        subject_user_key = self._person_specific_subject_key(
            candidate,
            evidence,
            requested_memory_type,
            trusted_subject_keys,
            explicit=explicit,
        )
        if subject_user_key:
            finder = getattr(self.store, "find_semantic_match_for_subject", None)
            if callable(finder):
                return finder(
                    event.scope_type,
                    event.scope_id,
                    semantic_key,
                    subject_user_key,
                )
        existing = self.store.find_semantic_match(
            event.scope_type,
            event.scope_id,
            semantic_key,
        )
        if (
            existing is not None
            and subject_user_key
            and subject_user_key not in existing.subject_keys
        ):
            return None
        return existing

    @classmethod
    def _is_exact_source_retry(
        cls,
        candidate: dict[str, Any],
        evidence: MemoryEvidence,
        stable_source_cards: Iterable[MemoryCard],
    ) -> bool:
        """Prove a replay from trusted provenance before model classification."""

        source_identity = cls._source_identity(evidence)
        candidate_slot = candidate.get("_source_candidate_index")
        for card in stable_source_cards:
            card_slot = card.payload.get("source_candidate_index")
            if (
                isinstance(candidate_slot, int)
                and isinstance(card_slot, int)
                and candidate_slot != card_slot
            ):
                continue
            for item in card.evidence:
                if (
                    cls._source_identity(item) == source_identity
                    and item.excerpt == evidence.excerpt
                ):
                    return True
        return False

    def _source_identity_matches(
        self,
        event: EventEnvelope,
        evidence: MemoryEvidence,
    ) -> tuple[MemoryCard, ...]:
        finder = getattr(self.store, "find_source_identity_matches", None)
        if finder is None:
            return ()
        try:
            value = finder(event.scope_type, event.scope_id, evidence)
        except Exception:
            return ()
        return tuple(value or ())

    def _upsert_candidate(
        self,
        event: EventEnvelope,
        candidate: dict[str, Any],
        *,
        explicit: bool,
        recent_context: Iterable[dict[str, Any]],
    ) -> MemoryCard | None:
        requested_memory_type = str(candidate["memory_type"])
        semantic_key = str(candidate["semantic_key"]).strip()
        if not semantic_key:
            return None

        evidence = self._candidate_evidence(event, candidate, recent_context)
        if evidence is None:
            return None
        subject_keys = self._trusted_subject_keys(candidate, evidence)
        if subject_keys is None:
            return None

        edited = event.event_type is EventType.EDITED_MESSAGE
        source_lock_factory = getattr(self.store, "source_lock", None)
        source_context = (
            source_lock_factory(event.scope_type, event.scope_id, evidence)
            if callable(source_lock_factory)
            else nullcontext()
        )
        with source_context:
            stable_cards = self._source_identity_matches(event, evidence)
            old_keys = [
                str(card.payload.get("semantic_key") or "")
                for card in stable_cards
                if str(card.payload.get("semantic_key") or "")
            ]
            semantic_locks_factory = getattr(self.store, "semantic_locks", None)
            if callable(semantic_locks_factory):
                semantic_context = semantic_locks_factory(
                    event.scope_type,
                    event.scope_id,
                    (semantic_key, *old_keys),
                )
            else:
                lock_factory = getattr(self.store, "semantic_lock", None)
                semantic_context = (
                    lock_factory(event.scope_type, event.scope_id, semantic_key)
                    if callable(lock_factory)
                    else nullcontext()
                )
            with semantic_context:
                # Re-read after all relevant locks are held. For an edited
                # source this prevents semantic-key changes from racing another
                # worker or leaving the old card behind merely because the new
                # excerpt/key no longer matches it.
                stable_cards = self._source_identity_matches(event, evidence)
                return self._upsert_candidate_locked(
                    event,
                    candidate,
                    explicit=explicit,
                    evidence=evidence,
                    subject_keys=subject_keys,
                    requested_memory_type=requested_memory_type,
                    semantic_key=semantic_key,
                    stable_source_cards=stable_cards,
                )

    def _upsert_candidate_locked(
        self,
        event: EventEnvelope,
        candidate: dict[str, Any],
        *,
        explicit: bool,
        evidence: MemoryEvidence,
        subject_keys: list[str],
        requested_memory_type: str,
        semantic_key: str,
        stable_source_cards: tuple[MemoryCard, ...] = (),
    ) -> MemoryCard | None:
        now = datetime.now(timezone.utc)
        raw_confidence = float(candidate.get("confidence", 0.5))

        if (
            event.event_type is not EventType.EDITED_MESSAGE
            and stable_source_cards
            and self._is_exact_source_retry(candidate, evidence, stable_source_cards)
        ):
            return None

        semantic_existing = self._find_semantic_candidate_match(
            event,
            candidate,
            evidence,
            requested_memory_type,
            semantic_key,
            subject_keys,
            explicit=explicit,
        )
        existing = semantic_existing

        if event.event_type is EventType.EDITED_MESSAGE and stable_source_cards:
            stable_by_id = {card.id: card for card in stable_source_cards}
            if semantic_existing is not None:
                if semantic_existing.id not in stable_by_id:
                    # The edited source points at one card while the new key is
                    # already occupied by another. Do not guess a merge target.
                    return None
            elif len(stable_source_cards) == 1:
                existing = stable_source_cards[0]
            else:
                # A long source message may ground multiple cards. Without a
                # unique semantic match an edited candidate is ambiguous; fail
                # closed instead of overwriting an arbitrary memory.
                return None

        if existing is None:
            source_finder = getattr(self.store, "find_source_match", None)
            if source_finder is not None:
                existing = source_finder(
                    event.scope_type,
                    event.scope_id,
                    requested_memory_type,
                    evidence,
                )

        existing_evidence = list(existing.evidence) if existing is not None else []
        source_identity = self._source_identity(evidence)
        same_source_index = next(
            (
                index
                for index, item in enumerate(existing_evidence)
                if self._source_identity(item) == source_identity
            ),
            None,
        )
        correction = False
        new_source = same_source_index is None
        if same_source_index is not None:
            if existing_evidence[same_source_index].excerpt == evidence.excerpt:
                # Same trusted source and same content: exact delivery retry,
                # even if a nondeterministic mapper proposed another key.
                return None
            correction = True
            evidence_items = list(existing_evidence)
            evidence_items[same_source_index] = evidence
        else:
            evidence_items = existing_evidence + [evidence]

        unique_sources = self._unique_sources(evidence_items)
        source_count = len(unique_sources)
        episode_count = self._episode_count(unique_sources)

        memory_type = requested_memory_type
        if requested_memory_type == "pattern" and (source_count < 3 or episode_count < 2):
            memory_type = "observation"

        usage_data = dict(candidate.get("usage_policy", {}))
        if memory_type in _DIRECT_STATEMENT_TYPES:
            usage_data["callback"] = True
            usage_data["proactive"] = True
        usage_policy = UsagePolicy(**usage_data)

        confidence = raw_confidence
        if not explicit:
            confidence = min(confidence, 0.88 if memory_type in _DIRECT_STATEMENT_TYPES else 0.70)

        if explicit:
            proposed_status = MemoryStatus.ACTIVE
        elif requested_memory_type == "pattern":
            proposed_status = (
                MemoryStatus.ACTIVE
                if source_count >= 3 and episode_count >= 2
                else MemoryStatus.CANDIDATE
            )
        elif memory_type in {"commitment", "decision"} and raw_confidence >= 0.65:
            proposed_status = MemoryStatus.ACTIVE
        elif memory_type == "quote" and raw_confidence >= 0.78:
            proposed_status = MemoryStatus.ACTIVE
        else:
            proposed_status = MemoryStatus.CANDIDATE

        payload = dict(candidate.get("payload", {}))
        payload.setdefault("statement_kind", "none")
        payload.setdefault("status", "unknown")
        payload.setdefault("due_at", None)
        payload.setdefault("verbatim", None)
        payload.setdefault("claim_kind", "unknown")
        if payload["claim_kind"] not in _CLAIM_KINDS:
            payload["claim_kind"] = "unknown"
        payload.update(
            {
                "semantic_key": semantic_key,
                "source_message_id": evidence.message_id,
                "source_author_id": evidence.author_id,
                "evidence_count": source_count,
                "episode_count": episode_count,
                "episode_policy": "unique_source_messages_6h_window",
            }
        )
        source_candidate_index = candidate.get("_source_candidate_index")
        if isinstance(source_candidate_index, int):
            payload["source_candidate_index"] = source_candidate_index

        if existing is None:
            card = MemoryCard(
                id=self._memory_id(event, requested_memory_type, semantic_key, evidence),
                scope_type=event.scope_type,
                scope_id=event.scope_id,
                memory_type=memory_type,
                subject_keys=subject_keys,
                summary=str(candidate["summary"]).strip(),
                payload=payload,
                importance=float(candidate.get("importance", 0.5)),
                confidence=confidence,
                freshness=1.0,
                status=proposed_status,
                origin=MemoryOrigin.EXPLICIT if explicit else MemoryOrigin.INFERRED,
                usage_policy=usage_policy,
                evidence=[evidence],
                source_count=source_count,
                created_at=now,
                updated_at=now,
                last_confirmed_at=now if explicit else None,
            )
            return self.store.create(card)

        existing_source_count = len(self._unique_sources(existing_evidence))
        single_source_correction = correction and existing_source_count == 1
        if single_source_correction:
            # A correction replaces the only evidence supporting this card, so
            # status may demote as well as promote. Confidence may decrease, but
            # a same-source edit never earns a corroboration boost.
            merged_status = proposed_status
            merged_confidence = min(existing.confidence, confidence)
            merged_last_confirmed = existing.last_confirmed_at
            merged_subject_keys = subject_keys or list(existing.subject_keys)
        else:
            merged_status = existing.status
            if explicit or proposed_status is MemoryStatus.ACTIVE:
                merged_status = MemoryStatus.ACTIVE
            if correction:
                # Same semantic claim with other independent support: update the
                # edited excerpt without allowing it to weaken the corroborated
                # card solely because one source changed wording.
                merged_confidence = existing.confidence
                merged_last_confirmed = existing.last_confirmed_at
                merged_subject_keys = subject_keys or list(existing.subject_keys)
            else:
                merged_confidence = max(existing.confidence, confidence)
                if not explicit and new_source and source_count >= 2:
                    merged_confidence = min(0.92, merged_confidence + 0.04)
                merged_last_confirmed = now if explicit else existing.last_confirmed_at
                merged_subject_keys = list(existing.subject_keys) or subject_keys

        updated = existing.model_copy(
            update={
                "memory_type": memory_type,
                "subject_keys": merged_subject_keys,
                "summary": str(candidate["summary"]).strip(),
                "payload": {**existing.payload, **payload},
                "importance": max(existing.importance, float(candidate.get("importance", 0.5))),
                "confidence": merged_confidence,
                "freshness": 1.0,
                "status": merged_status,
                "origin": MemoryOrigin.EXPLICIT if explicit else existing.origin,
                "usage_policy": usage_policy,
                "evidence": evidence_items,
                "source_count": source_count,
                "updated_at": now,
                "last_confirmed_at": merged_last_confirmed,
            }
        )
        return self.store.update(updated)
