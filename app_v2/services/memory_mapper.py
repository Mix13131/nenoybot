from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from app_v2.domain.enums import MemoryOrigin, MemoryStatus, ScopeType
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

    def find_source_match(
        self,
        scope_type: ScopeType,
        scope_id: str,
        memory_type: str,
        evidence: MemoryEvidence,
    ) -> MemoryCard | None:
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

    @contextmanager
    def semantic_lock(
        self,
        scope_type: ScopeType,
        scope_id: str,
        semantic_key: str,
    ) -> Iterator[None]:
        """Serialize every merge for one logical Memory Card.

        Session advisory locks deliberately survive the commits performed by the
        repository create/update methods. Source/message identity is NOT part of
        this key: independent evidence for the same semantic card must serialize
        through the same lock domain.
        """

        conn = getattr(self.repo, "conn", None)
        if conn is None or not semantic_key:
            yield
            return

        lock_material = "|".join((scope_type.value, scope_id, semantic_key))
        conn.execute(
            "SELECT pg_advisory_lock(hashtextextended(%s, 0))",
            (lock_material,),
        ).fetchone()
        # Session-level locks survive transaction boundaries, including commits
        # inside MemoryRepository.create/update.
        conn.commit()
        try:
            yield
        finally:
            try:
                conn.rollback()
            except Exception:
                pass
            try:
                conn.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    (lock_material,),
                ).fetchone()
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass

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
            forgotten: list[str] = []
            for memory_id in target_memory_ids:
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
        written: list[MemoryCard] = []
        for candidate in parsed.get("candidates", []):
            if candidate.get("memory_type") not in _ALLOWED_TYPES:
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
        return MapperResult(written=tuple(written))

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
    def _source_identity(evidence: MemoryEvidence) -> tuple[str | None, str | None, datetime]:
        return (evidence.message_id, evidence.author_id, evidence.timestamp)

    @classmethod
    def _unique_sources(cls, evidence_items: Iterable[MemoryEvidence]) -> list[MemoryEvidence]:
        result: list[MemoryEvidence] = []
        seen: set[tuple[str | None, str | None, datetime]] = set()
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

        lock_factory = getattr(self.store, "semantic_lock", None)
        lock_context = (
            lock_factory(event.scope_type, event.scope_id, semantic_key)
            if callable(lock_factory)
            else nullcontext()
        )
        with lock_context:
            return self._upsert_candidate_locked(
                event,
                candidate,
                explicit=explicit,
                evidence=evidence,
                subject_keys=subject_keys,
                requested_memory_type=requested_memory_type,
                semantic_key=semantic_key,
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
    ) -> MemoryCard | None:
        now = datetime.now(timezone.utc)
        raw_confidence = float(candidate.get("confidence", 0.5))
        existing = self.store.find_semantic_match(event.scope_type, event.scope_id, semantic_key)

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
                # Exact delivery retry: persistence and receipt no-op.
                return None
            # Telegram edits retain source identity. Replace the old evidence;
            # an edit is a correction, not independent corroboration.
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

        merged_status = existing.status
        if explicit or proposed_status is MemoryStatus.ACTIVE:
            merged_status = MemoryStatus.ACTIVE

        if correction:
            # Same source was edited. Update content/provenance, but never award
            # an independent-source confidence or confirmation bonus.
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
