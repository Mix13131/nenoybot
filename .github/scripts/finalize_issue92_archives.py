from pathlib import Path

mapper_path = Path("app_v2/services/memory_mapper.py")
text = mapper_path.read_text(encoding="utf-8")
start = text.index("    def _reconcile_edited_source(\n")
end = text.index("    @staticmethod\n    def _extract_explicit_remember", start)
replacement = '''    def _reconcile_edited_source(
        self,
        event: EventEnvelope,
        candidates: list[dict[str, Any]],
        recent_context: Iterable[dict[str, Any]],
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
                forgotten: list[str] = []

                if not prepared:
                    try:
                        for stale in stable_cards:
                            if self.store.archive(event.scope_type, event.scope_id, stale.id):
                                forgotten.append(stale.id)
                    except Exception as exc:
                        return MapperResult(
                            forgotten_ids=tuple(forgotten),
                            failed=True,
                            reason=f"mapper_failure:{type(exc).__name__}",
                        )
                    return MapperResult(
                        forgotten_ids=tuple(forgotten),
                        reason="edited_source_cleared",
                    )

                by_slot: dict[int, MemoryCard] = {}
                for card in stable_cards:
                    slot = card.payload.get("source_candidate_index")
                    if not isinstance(slot, int) or slot in by_slot:
                        try:
                            for stale in stable_cards:
                                if self.store.archive(event.scope_type, event.scope_id, stale.id):
                                    forgotten.append(stale.id)
                        except Exception as exc:
                            return MapperResult(
                                forgotten_ids=tuple(forgotten),
                                failed=True,
                                reason=f"mapper_failure:{type(exc).__name__}",
                            )
                        return MapperResult(
                            forgotten_ids=tuple(forgotten),
                            failed=True,
                            reason="edited_source_reconciliation_ambiguous",
                        )
                    by_slot[slot] = card

                written: list[MemoryCard] = []
                used_ids: set[str] = set()
                try:
                    for candidate, evidence, subjects in prepared:
                        slot = int(candidate["_source_candidate_index"])
                        matched = by_slot.get(slot)
                        card = self._upsert_candidate_locked(
                            event,
                            candidate,
                            explicit=False,
                            evidence=evidence,
                            subject_keys=subjects,
                            requested_memory_type=str(candidate["memory_type"]),
                            semantic_key=str(candidate["semantic_key"]).strip(),
                            stable_source_cards=(matched,) if matched else (),
                        )
                        if matched:
                            used_ids.add(matched.id)
                        if card is not None:
                            written.append(card)
                    for stale in stable_cards:
                        if stale.id not in used_ids:
                            if self.store.archive(event.scope_type, event.scope_id, stale.id):
                                forgotten.append(stale.id)
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
mapper_path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")

unit_path = Path("tests_v2/unit/test_memory_mapper.py")
unit = unit_path.read_text(encoding="utf-8")
marker = "# issue92-final-archive-regressions"
if marker not in unit:
    unit += r'''

# issue92-final-archive-regressions

def test_edited_zero_candidates_archives_source_and_reports_receipt_change():
    from app_v2.services.operation_receipts import personal_operation_receipts

    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate()]},
        {"candidates": []},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    original = event("Завтра отправлю отчёт", message_id="m-zero")
    first = mapper.map_event(original)
    memory_id = first.written[0].id
    edited = original.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Это сообщение больше не содержит обещания.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    cleared = mapper.map_event(edited)
    assert cleared.failed is False
    assert cleared.reason == "edited_source_cleared"
    assert cleared.forgotten_ids == (memory_id,)
    assert store.cards[memory_id].status is MemoryStatus.ARCHIVED
    receipt = personal_operation_receipts(cleared)["memory"]
    assert receipt["status"] == "succeeded"
    assert receipt["changed"] is True
    assert receipt["forgotten_ids"] == [memory_id]

    retried = mapper.map_event(edited)
    retry_receipt = personal_operation_receipts(retried)["memory"]
    assert retried.failed is False
    assert retried.forgotten_ids == ()
    assert retry_receipt["changed"] is False


def test_edited_source_retains_unchanged_sibling_and_reports_stale_archive():
    from app_v2.services.operation_receipts import personal_operation_receipts

    store = FakeStore()
    original = "Альфа отправится 20-го. Бета стоит 900 USD."
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event(original, message_id="m-sibling")
    first = mapper.map_event(base)
    alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
    beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Альфа отправится 20-го.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is False
    assert result.written == ()
    assert result.forgotten_ids == (beta.id,)
    assert store.cards[alpha.id].status is not MemoryStatus.ARCHIVED
    assert store.cards[beta.id].status is MemoryStatus.ARCHIVED
    receipt = personal_operation_receipts(result)["memory"]
    assert receipt["changed"] is True
    assert receipt["written_ids"] == []
    assert receipt["forgotten_ids"] == [beta.id]


def test_edited_zero_candidates_does_not_archive_same_message_id_in_other_scope():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [candidate()]},
        {"candidates": [candidate()]},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    personal = event("Завтра отправлю отчёт", message_id="m-shared", scope=ScopeType.PERSONAL, scope_id="u1")
    group = event("Завтра отправлю отчёт", message_id="m-shared", scope=ScopeType.GROUP, scope_id="g1")
    personal_card = mapper.map_event(personal).written[0]
    group_card = mapper.map_event(group).written[0]
    edited = personal.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Обещание удалено.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.forgotten_ids == (personal_card.id,)
    assert store.cards[personal_card.id].status is MemoryStatus.ARCHIVED
    assert store.cards[group_card.id].status is not MemoryStatus.ARCHIVED


def test_edited_zero_candidates_partial_archive_failure_reports_durable_ids():
    from app_v2.services.operation_receipts import personal_operation_receipts

    class FailSecondArchiveStore(FakeStore):
        def __init__(self):
            super().__init__()
            self.archive_calls = 0

        def archive(self, scope_type, scope_id, memory_id):
            self.archive_calls += 1
            if self.archive_calls == 2:
                raise RuntimeError("simulated archive failure")
            return super().archive(scope_type, scope_id, memory_id)

    store = FailSecondArchiveStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
        {"candidates": []},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа отправится 20-го. Бета стоит 900 USD.", message_id="m-partial-archive")
    first = mapper.map_event(base)
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Старые сведения полностью удалены.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is True
    assert result.reason == "mapper_failure:RuntimeError"
    assert len(result.forgotten_ids) == 1
    assert result.forgotten_ids[0] in {card.id for card in first.written}
    receipt = personal_operation_receipts(result)["memory"]
    assert receipt["status"] == "partial"
    assert receipt["changed"] is True
    assert receipt["forgotten_ids"] == list(result.forgotten_ids)
'''
    unit_path.write_text(unit, encoding="utf-8")

integration_path = Path("tests_v2/integration/test_trusted_feedback_memory_postgres.py")
integration = integration_path.read_text(encoding="utf-8")
marker2 = "# issue92-final-archive-postgres-regressions"
if marker2 not in integration:
    integration += r'''

# issue92-final-archive-postgres-regressions

def test_edited_zero_candidates_archives_source_and_receipt_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-edit-clear-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [_edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го")]},
        {"candidates": []},
        {"candidates": []},
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
        "text": "План отправки удалён из сообщения.",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(base)
        memory_id = first.written[0].id
        cleared = mapper.map_event(edited)
        assert cleared.failed is False
        assert cleared.forgotten_ids == (memory_id,)
        receipt = personal_operation_receipts(cleared)["memory"]
        assert receipt["status"] == "succeeded"
        assert receipt["changed"] is True
        assert receipt["forgotten_ids"] == [memory_id]
        row = conn.execute(
            "SELECT status, source_count FROM memory_cards WHERE id=%s",
            (memory_id,),
        ).fetchone()
        assert row == ("archived", 1)

        retried = mapper.map_event(edited)
        assert retried.forgotten_ids == ()
        assert personal_operation_receipts(retried)["memory"]["changed"] is False
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()


def test_edited_source_archives_removed_sibling_and_reports_change_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-edit-sibling-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [
            _edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го"),
            _edit_candidate("beta:900", "Бета стоит 900 USD.", "Бета стоит 900 USD"),
        ]},
        {"candidates": [
            _edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го"),
        ]},
    ])
    base = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880009999",
        text="Альфа отправится 20-го. Бета стоит 900 USD.",
    )
    edited = base.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "Альфа отправится 20-го.",
    })

    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(base)
        alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
        beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")
        result = mapper.map_event(edited)
        assert result.failed is False
        assert result.written == ()
        assert result.forgotten_ids == (beta.id,)
        receipt = personal_operation_receipts(result)["memory"]
        assert receipt["changed"] is True
        assert receipt["forgotten_ids"] == [beta.id]
        rows = dict(conn.execute(
            "SELECT id, status FROM memory_cards WHERE scope_type='personal' AND scope_id=%s",
            (scope_id,),
        ).fetchall())
        assert rows[alpha.id] in {"active", "candidate"}
        assert rows[beta.id] == "archived"
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()
'''
    integration_path.write_text(integration, encoding="utf-8")
