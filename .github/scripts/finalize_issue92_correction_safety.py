from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {text.count(old)}")
    return text.replace(old, new, 1)


mapper_path = Path("app_v2/services/memory_mapper.py")
text = mapper_path.read_text(encoding="utf-8")

text = replace_once(
    text,
    '''        if lowered in {"забудь это", "забудь", "forget this", "forget it"}:
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
''',
    '''        if lowered in {"забудь это", "забудь", "forget this", "forget it"}:
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
''',
    "forget target safety",
)

text = replace_once(
    text,
    '''            try:
                card = self._upsert_candidate(event, candidate, explicit=True, recent_context=())
            except Exception as exc:
                return MapperResult(failed=True, reason=f"mapper_failure:{type(exc).__name__}")
            return MapperResult(written=(card,) if card is not None else (), reason="explicit_remember")
''',
    '''            if event.event_type is EventType.EDITED_MESSAGE:
                candidates = self._assign_source_candidate_slots(event, (candidate,), ())
                if len(candidates) != 1:
                    return MapperResult(failed=True, reason="edited_source_invalid_candidate")
                return self._reconcile_edited_source(event, candidates, (), explicit=True)
            try:
                card = self._upsert_candidate(event, candidate, explicit=True, recent_context=())
            except Exception as exc:
                return MapperResult(failed=True, reason=f"mapper_failure:{type(exc).__name__}")
            return MapperResult(written=(card,) if card is not None else (), reason="explicit_remember")
''',
    "explicit edited reconciliation",
)

text = replace_once(
    text,
    '''        parsed = result.parsed or {"candidates": []}
        candidates = [
            dict(candidate)
            for candidate in parsed.get("candidates", [])
            if candidate.get("memory_type") in _ALLOWED_TYPES
        ]
        candidates = self._assign_source_candidate_slots(event, candidates, context_items)
        if event.event_type is EventType.EDITED_MESSAGE:
            return self._reconcile_edited_source(event, candidates, context_items)
''',
    '''        parsed = result.parsed or {"candidates": []}
        raw_candidates = parsed.get("candidates", [])
        if not isinstance(raw_candidates, list):
            return MapperResult(failed=True, reason="mapper_invalid_candidates")
        candidates = [
            dict(candidate)
            for candidate in raw_candidates
            if isinstance(candidate, dict) and candidate.get("memory_type") in _ALLOWED_TYPES
        ]
        if event.event_type is EventType.EDITED_MESSAGE and len(candidates) != len(raw_candidates):
            return MapperResult(failed=True, reason="edited_source_invalid_candidate")
        positioned_candidates = self._assign_source_candidate_slots(event, candidates, context_items)
        if event.event_type is EventType.EDITED_MESSAGE:
            # A genuinely empty mapper result means the edited source no longer
            # contains a memory candidate. If candidates existed but failed
            # provenance validation, fail closed instead of erasing old memory.
            if candidates and len(positioned_candidates) != len(candidates):
                return MapperResult(failed=True, reason="edited_source_invalid_candidate")
            return self._reconcile_edited_source(event, positioned_candidates, context_items)
        candidates = positioned_candidates
''',
    "edit raw candidate validation",
)

start = text.index("    def _reconcile_edited_source(\n")
end = text.index("    @staticmethod\n    def _extract_explicit_remember", start)
replacement = '''    def _detach_source_from_card(
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

                by_slot: dict[int, MemoryCard] = {}
                for card in stable_cards:
                    slot = card.payload.get("source_candidate_index")
                    if not isinstance(slot, int) or slot in by_slot:
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
                            failed=True,
                            reason="edited_source_reconciliation_ambiguous",
                        )
                    by_slot[slot] = card

                used_ids: set[str] = set()
                try:
                    for candidate, evidence, subjects in prepared:
                        slot = int(candidate["_source_candidate_index"])
                        matched = by_slot.get(slot)
                        semantic_key = str(candidate["semantic_key"]).strip()
                        old_semantic_key = (
                            str(matched.payload.get("semantic_key") or "").strip()
                            if matched is not None else ""
                        )
                        shared_sources = (
                            len(self._unique_sources(matched.evidence)) > 1
                            if matched is not None else False
                        )

                        # If one source in a corroborated card changes logical
                        # identity, detach only that source. The old card keeps
                        # the other independent evidence and the edited source
                        # becomes/merges into a separate semantic card.
                        stable_for_upsert: tuple[MemoryCard, ...] = (matched,) if matched else ()
                        if matched is not None and shared_sources and old_semantic_key != semantic_key:
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

'''
text = text[:start] + replacement + text[end:]

text = replace_once(
    text,
    '''        merged_status = existing.status
        if explicit or proposed_status is MemoryStatus.ACTIVE:
            merged_status = MemoryStatus.ACTIVE

        if correction:
            merged_confidence = existing.confidence
            merged_last_confirmed = existing.last_confirmed_at
            merged_subject_keys = subject_keys or list(existing.subject_keys)
        else:
            merged_confidence = max(existing.confidence, confidence)
            if not explicit and new_source and source_count >= 2:
                merged_confidence = min(0.92, merged_confidence + 0.04)
            merged_last_confirmed = now if explicit else existing.last_confirmed_at
            merged_subject_keys = list(existing.subject_keys) or subject_keys
''',
    '''        existing_source_count = len(self._unique_sources(existing_evidence))
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
''',
    "correction demotion",
)
mapper_path.write_text(text, encoding="utf-8")

# Group forget: explicit, code-validated target IDs are allowed. A bot reply's
# selected_memory_ids are retrieval context, not proof that those memories were
# explicitly referenced to the user, so do not bulk-delete them.
group_path = Path("app_v2/services/group_pipeline.py")
group = group_path.read_text(encoding="utf-8")
old_group = '''        lowered = (event.text or "").strip().lower()
        if lowered not in {"забудь", "забудь это", "forget it", "forget this"}:
            return ()
        if not event.reply_to_message_id:
            return ()
        resolver = getattr(self.intervention_repo, "selected_memory_ids_for_bot_message", None)
        if resolver is None:
            return ()
        try:
            resolved = resolver(
                scope_type=ScopeType.GROUP,
                scope_id=event.scope_id,
                telegram_message_id=event.reply_to_message_id,
            )
        except Exception:
            return ()
        return tuple(str(item) for item in resolved if str(item).strip())
'''
new_group = '''        # selected_memory_ids on an intervention are the full retrieval/context
        # set used during generation, not a verified list of facts mentioned in
        # the sent text. Without explicit referenced-memory provenance, reply
        # bound "забудь это" is ambiguous and must ask for clarification rather
        # than deleting unrelated context memories.
        return ()
'''
group = replace_once(group, old_group, new_group, "group forget target safety")
group_path.write_text(group, encoding="utf-8")

receipt_path = Path("app_v2/services/operation_receipts.py")
receipt = receipt_path.read_text(encoding="utf-8")
receipt = replace_once(
    receipt,
    '''    elif reason == "insufficient_context":
        status = "needs_clarification"
''',
    '''    elif reason in {"insufficient_context", "forget_target_ambiguous"}:
        status = "needs_clarification"
''',
    "forget clarification receipt",
)
receipt_path.write_text(receipt, encoding="utf-8")

# Update an older regression whose unsafe expectation was exactly the new P1.
guard_path = Path("tests_v2/unit/test_issue92_final_guards.py")
guards = guard_path.read_text(encoding="utf-8")
guards = replace_once(
    guards,
    '''def test_group_forget_resolves_memories_from_replied_bot_intervention() -> None:
    repo = FakeInterventionRepo(("mem-a", "mem-b"))
    pipeline = _pipeline_with_interventions(repo)
    event = _forget_event()
    assert pipeline._target_memory_ids(event) == ("mem-a", "mem-b")
    assert repo.calls == [
        {
            "scope_type": ScopeType.GROUP,
            "scope_id": "-100777",
            "telegram_message_id": "700",
        }
    ]
''',
    '''def test_group_forget_does_not_delete_reply_retrieval_context() -> None:
    repo = FakeInterventionRepo(("mem-a", "mem-b"))
    pipeline = _pipeline_with_interventions(repo)
    event = _forget_event()
    assert pipeline._target_memory_ids(event) == ()
    assert repo.calls == []
''',
    "unsafe group forget regression",
)
guards += '''\n\ndef test_group_forget_single_reply_context_is_still_not_proven_target() -> None:\n    repo = FakeInterventionRepo(("mem-a",))\n    pipeline = _pipeline_with_interventions(repo)\n    assert pipeline._target_memory_ids(_forget_event()) == ()\n    assert repo.calls == []\n'''
guard_path.write_text(guards, encoding="utf-8")

memory_test_path = Path("tests_v2/unit/test_memory_mapper.py")
unit = memory_test_path.read_text(encoding="utf-8")
marker = "# issue92-correction-safety-final"
if marker not in unit:
    unit += r'''

# issue92-correction-safety-final

def test_edit_with_rejected_candidate_fails_without_erasing_memory():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(source_message_id="m-safe", evidence_excerpt="Завтра отправлю отчёт")]},
        {"candidates": [candidate(
            semantic_key="commitment:changed",
            source_message_id="m-safe",
            evidence_excerpt="Фрагмент которого нет в edit",
        )]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Завтра отправлю отчёт", message_id="m-safe")
    first = mapper.map_event(base)
    memory_id = first.written[0].id
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Отчёт пока под вопросом",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is True
    assert result.reason == "edited_source_invalid_candidate"
    assert result.written == ()
    assert result.forgotten_ids == ()
    assert store.cards[memory_id].status is not MemoryStatus.ARCHIVED
    assert store.cards[memory_id].evidence[0].excerpt == "Завтра отправлю отчёт"


def test_shared_card_edit_new_semantic_key_detaches_only_edited_source():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            summary="Отправка запланирована.",
            source_message_id="m-shared-a",
            evidence_excerpt="Отправка запланирована",
        )]},
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            summary="Отправка запланирована.",
            source_message_id="m-shared-b",
            evidence_excerpt="Да, отправка запланирована",
        )]},
        {"candidates": [candidate(
            semantic_key="shipment:cancelled",
            summary="Отправка отменена.",
            source_message_id="m-shared-a",
            evidence_excerpt="Отправка отменена",
        )]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    first_event = event("Отправка запланирована", message_id="m-shared-a")
    second_event = event("Да, отправка запланирована", message_id="m-shared-b", occurred_at=NOW + timedelta(hours=7))
    first = mapper.map_event(first_event)
    mapper.map_event(second_event)
    shared_id = first.written[0].id
    assert store.cards[shared_id].source_count == 2

    edited = first_event.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Отправка отменена",
        "occurred_at": NOW + timedelta(hours=8),
    })
    result = mapper.map_event(edited)
    assert result.failed is False

    old_card = store.cards[shared_id]
    assert old_card.status is not MemoryStatus.ARCHIVED
    assert old_card.payload["semantic_key"] == "shipment:planned"
    assert old_card.summary == "Отправка запланирована."
    assert old_card.source_count == 1
    assert [item.message_id for item in old_card.evidence] == ["m-shared-b"]

    new_cards = [
        card for card in store.cards.values()
        if card.status is not MemoryStatus.ARCHIVED
        and card.payload.get("semantic_key") == "shipment:cancelled"
    ]
    assert len(new_cards) == 1
    assert new_cards[0].source_count == 1
    assert [item.message_id for item in new_cards[0].evidence] == ["m-shared-a"]
    assert {card.id for card in result.written} == {old_card.id, new_cards[0].id}


def test_zero_candidate_edit_detaches_source_from_shared_card_instead_of_archiving_claim():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            source_message_id="m-zero-a",
            evidence_excerpt="Отправка запланирована",
        )]},
        {"candidates": [candidate(
            semantic_key="shipment:planned",
            source_message_id="m-zero-b",
            evidence_excerpt="Да, отправка запланирована",
        )]},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    first_event = event("Отправка запланирована", message_id="m-zero-a")
    second_event = event("Да, отправка запланирована", message_id="m-zero-b", occurred_at=NOW + timedelta(hours=7))
    shared_id = mapper.map_event(first_event).written[0].id
    mapper.map_event(second_event)

    edited = first_event.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Этой информации больше нет",
        "occurred_at": NOW + timedelta(hours=8),
    })
    result = mapper.map_event(edited)
    assert result.failed is False
    assert result.forgotten_ids == ()
    assert [card.id for card in result.written] == [shared_id]
    old_card = store.cards[shared_id]
    assert old_card.status is not MemoryStatus.ARCHIVED
    assert old_card.source_count == 1
    assert [item.message_id for item in old_card.evidence] == ["m-zero-b"]


def test_weaker_single_source_edit_demotes_active_card_and_does_not_raise_confidence():
    store = FakeStore()
    strong = candidate(
        memory_type="commitment",
        semantic_key="decision:report",
        summary="Отчёт точно будет завтра.",
        source_message_id="m-demote",
        evidence_excerpt="Отчёт точно будет завтра",
        confidence=0.95,
    )
    weak = candidate(
        memory_type="observation",
        semantic_key="decision:report",
        summary="Отчёт, возможно, будет позже.",
        source_message_id="m-demote",
        evidence_excerpt="Отчёт, возможно, будет позже",
        confidence=0.3,
        usage_policy={"assist": True, "callback": False, "roast": False, "proactive": False},
    )
    adapter = FakeAdapter(responses=[{"candidates": [strong]}, {"candidates": [weak]}])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Отчёт точно будет завтра", message_id="m-demote")
    first = mapper.map_event(base).written[0]
    assert first.status is MemoryStatus.ACTIVE

    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Отчёт, возможно, будет позже",
        "occurred_at": NOW + timedelta(minutes=2),
    })
    corrected = mapper.map_event(edited).written[-1]
    assert corrected.id == first.id
    assert corrected.status is MemoryStatus.CANDIDATE
    assert corrected.memory_type == "observation"
    assert corrected.confidence <= first.confidence
'''
    memory_test_path.write_text(unit, encoding="utf-8")

receipt_test_path = Path("tests_v2/unit/test_operation_receipts.py")
receipt_tests = receipt_test_path.read_text(encoding="utf-8")
marker2 = "test_ambiguous_forget_receipt_needs_clarification"
if marker2 not in receipt_tests:
    receipt_tests += '''\n\ndef test_ambiguous_forget_receipt_needs_clarification():\n    from app_v2.services.memory_mapper import MapperResult\n    receipt = memory_receipt(MapperResult(reason="forget_target_ambiguous"), attempted=True)\n    assert receipt["status"] == "needs_clarification"\n    assert receipt["changed"] is False\n'''
    receipt_test_path.write_text(receipt_tests, encoding="utf-8")

integration_path = Path("tests_v2/integration/test_trusted_feedback_memory_postgres.py")
integration = integration_path.read_text(encoding="utf-8")
marker3 = "# issue92-correction-safety-postgres-final"
if marker3 not in integration:
    integration += r'''

# issue92-correction-safety-postgres-final

def test_shared_card_edit_splits_changed_source_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-shared-split-{uuid.uuid4().hex}"

    def c(key, summary, source, excerpt, memory_type="commitment", confidence=0.9):
        value = _edit_candidate(key, summary, excerpt)
        value["source_message_id"] = source
        value["memory_type"] = memory_type
        value["confidence"] = confidence
        return value

    adapter = _EditAdapter([
        {"candidates": [c("shipment:planned", "Отправка запланирована.", "880010001", "Отправка запланирована")]},
        {"candidates": [c("shipment:planned", "Отправка запланирована.", "880010002", "Да, отправка запланирована")]},
        {"candidates": [c("shipment:cancelled", "Отправка отменена.", "880010001", "Отправка отменена")]},
    ])
    first_event = EventEnvelope(
        event_id=f"it:{scope_id}:a",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880010001",
        text="Отправка запланирована",
    )
    second_event = first_event.model_copy(update={
        "event_id": f"it:{scope_id}:b",
        "message_id": "880010002",
        "occurred_at": NOW + timedelta(hours=7),
        "text": "Да, отправка запланирована",
    })
    edited = first_event.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(hours=8),
        "text": "Отправка отменена",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        old_id = mapper.map_event(first_event).written[0].id
        mapper.map_event(second_event)
        result = mapper.map_event(edited)
        assert result.failed is False
        rows = conn.execute(
            """
            SELECT id, status, source_count, payload ->> 'semantic_key', summary, evidence
            FROM memory_cards
            WHERE scope_type='personal' AND scope_id=%s AND status IN ('candidate','active')
            ORDER BY payload ->> 'semantic_key'
            """,
            (scope_id,),
        ).fetchall()
        assert len(rows) == 2
        by_key = {row[3]: row for row in rows}
        planned = by_key["shipment:planned"]
        cancelled = by_key["shipment:cancelled"]
        assert planned[0] == old_id
        assert planned[2] == 1
        assert planned[4] == "Отправка запланирована."
        assert [item["message_id"] for item in planned[5]] == ["880010002"]
        assert cancelled[2] == 1
        assert [item["message_id"] for item in cancelled[5]] == ["880010001"]
        assert {card.id for card in result.written} == {planned[0], cancelled[0]}
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


def test_rejected_edit_candidate_does_not_erase_postgres_memory() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-invalid-edit-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [_edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го")]},
        {"candidates": [_edit_candidate("alpha:cancelled", "Альфа отменена.", "Фрагмента нет в edit")]},
    ])
    base = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880009999",
        text="Альфа отправится 20-го.",
    )
    edited = base.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "Альфа пока под вопросом.",
    })
    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        memory_id = mapper.map_event(base).written[0].id
        result = mapper.map_event(edited)
        assert result.failed is True
        assert result.reason == "edited_source_invalid_candidate"
        row = conn.execute("SELECT status, evidence FROM memory_cards WHERE id=%s", (memory_id,)).fetchone()
        assert row[0] in {"candidate", "active"}
        assert row[1][0]["excerpt"] == "Альфа отправится 20-го"
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()
'''
    integration_path.write_text(integration, encoding="utf-8")
