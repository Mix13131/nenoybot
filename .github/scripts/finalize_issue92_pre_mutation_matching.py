from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, got {count}")
    return text.replace(old, new, 1)


mapper_path = Path("app_v2/services/memory_mapper.py")
text = mapper_path.read_text(encoding="utf-8")
old = '''                by_slot: dict[int, MemoryCard] = {}
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
'''
new = '''                # Resolve the full old↔new mapping before the first persistence
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
'''
text = replace_once(text, old, new, "pre-mutation edit matcher")
mapper_path.write_text(text, encoding="utf-8")

unit_path = Path("tests_v2/unit/test_memory_mapper.py")
unit = unit_path.read_text(encoding="utf-8")
marker = "# issue92-pre-mutation-matching-final"
if marker not in unit:
    unit += r'''

# issue92-pre-mutation-matching-final

def test_ambiguous_edited_source_fails_before_any_memory_mutation():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:old", summary="Альфа старая.", evidence_excerpt="Альфа старая"),
            candidate(semantic_key="beta:old", summary="Бета старая.", evidence_excerpt="Бета старая"),
        ]},
        {"candidates": [
            candidate(semantic_key="alpha:new", summary="Альфа новая.", evidence_excerpt="Альфа новая"),
            candidate(semantic_key="beta:new", summary="Бета новая.", evidence_excerpt="Бета новая"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа старая. Бета старая.", message_id="m-ambiguous")
    first = mapper.map_event(base)
    assert len(first.written) == 2

    snapshots = {}
    for card in first.written:
        payload = dict(card.payload)
        payload.pop("source_candidate_index", None)
        legacy = card.model_copy(update={"payload": payload})
        store.cards[card.id] = legacy
        snapshots[card.id] = legacy

    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Альфа новая. Бета новая.",
        "occurred_at": NOW + timedelta(minutes=1),
    })
    result = mapper.map_event(edited)

    assert result.failed is True
    assert result.reason == "edited_source_reconciliation_ambiguous"
    assert result.written == ()
    assert result.forgotten_ids == ()
    assert store.cards == snapshots
    assert all(card.status is not MemoryStatus.ARCHIVED for card in store.cards.values())


def test_edit_retaining_later_candidate_matches_semantic_identity_not_renumbered_slot():
    from app_v2.services.operation_receipts import personal_operation_receipts

    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:planned", summary="Альфа запланирована.", evidence_excerpt="Альфа отправится 20-го"),
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
        {"candidates": [
            candidate(semantic_key="beta:900", summary="Бета стоит 900 USD.", evidence_excerpt="Бета стоит 900 USD"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа отправится 20-го. Бета стоит 900 USD.", message_id="m-retain-beta")
    first = mapper.map_event(base)
    alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
    beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")

    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Бета стоит 900 USD.",
        "occurred_at": NOW + timedelta(minutes=1),
    })
    result = mapper.map_event(edited)

    assert result.failed is False
    assert result.written == ()
    assert result.forgotten_ids == (alpha.id,)
    assert store.cards[alpha.id].status is MemoryStatus.ARCHIVED
    assert store.cards[beta.id].status is not MemoryStatus.ARCHIVED
    assert store.cards[beta.id].payload["semantic_key"] == "beta:900"
    assert store.cards[beta.id].summary == "Бета стоит 900 USD."
    assert store.cards[beta.id].evidence[0].excerpt == "Бета стоит 900 USD"
    receipt = personal_operation_receipts(result)["memory"]
    assert receipt["changed"] is True
    assert receipt["forgotten_ids"] == [alpha.id]


def test_removed_plus_identity_changed_candidate_fails_closed_without_guessing():
    store = FakeStore()
    adapter = FakeAdapter(responses=[
        {"candidates": [
            candidate(semantic_key="alpha:old", summary="Альфа старая.", evidence_excerpt="Альфа старая"),
            candidate(semantic_key="beta:old", summary="Бета старая.", evidence_excerpt="Бета старая"),
        ]},
        {"candidates": [
            candidate(semantic_key="beta:new", summary="Бета новая.", evidence_excerpt="Бета новая"),
        ]},
    ])
    mapper = MemoryMapper(store=store, adapter=adapter)
    base = event("Альфа старая. Бета старая.", message_id="m-remove-change")
    first = mapper.map_event(base)
    before = dict(store.cards)
    edited = base.model_copy(update={
        "event_type": EventType.EDITED_MESSAGE,
        "text": "Бета новая.",
        "occurred_at": NOW + timedelta(minutes=1),
    })

    result = mapper.map_event(edited)
    assert result.failed is True
    assert result.reason == "edited_source_reconciliation_ambiguous"
    assert result.written == ()
    assert result.forgotten_ids == ()
    assert store.cards == before
    assert {card.id for card in first.written} == set(store.cards)
'''
    unit_path.write_text(unit, encoding="utf-8")

integration_path = Path("tests_v2/integration/test_trusted_feedback_memory_postgres.py")
integration = integration_path.read_text(encoding="utf-8")
marker2 = "# issue92-pre-mutation-matching-postgres-final"
if marker2 not in integration:
    integration += r'''

# issue92-pre-mutation-matching-postgres-final

def test_edit_retaining_later_candidate_uses_semantic_identity_postgres() -> None:
    psycopg, database_url = _psycopg_and_url()
    scope_id = f"it-retain-beta-{uuid.uuid4().hex}"
    adapter = _EditAdapter([
        {"candidates": [
            _edit_candidate("alpha:planned", "Альфа запланирована.", "Альфа отправится 20-го"),
            _edit_candidate("beta:900", "Бета стоит 900 USD.", "Бета стоит 900 USD"),
        ]},
        {"candidates": [
            _edit_candidate("beta:900", "Бета стоит 900 USD.", "Бета стоит 900 USD"),
        ]},
    ])
    base = EventEnvelope(
        event_id=f"it:{scope_id}:original",
        event_type=EventType.PRIVATE_MESSAGE,
        occurred_at=NOW,
        scope_type=ScopeType.PERSONAL,
        scope_id=scope_id,
        actor_user_id="991000001",
        message_id="880020001",
        text="Альфа отправится 20-го. Бета стоит 900 USD.",
    )
    edited = base.model_copy(update={
        "event_id": f"it:{scope_id}:edit",
        "event_type": EventType.EDITED_MESSAGE,
        "occurred_at": NOW + timedelta(minutes=5),
        "text": "Бета стоит 900 USD.",
    })
    with psycopg.connect(database_url) as conn:
        mapper = MemoryMapper(store=MemoryMapperStore(MemoryRepository(conn)), adapter=adapter)
        first = mapper.map_event(base)
        alpha = next(card for card in first.written if card.payload["semantic_key"] == "alpha:planned")
        beta = next(card for card in first.written if card.payload["semantic_key"] == "beta:900")
        result = mapper.map_event(edited)
        assert result.failed is False
        assert result.forgotten_ids == (alpha.id,)
        rows = dict(conn.execute(
            "SELECT id, status FROM memory_cards WHERE scope_type='personal' AND scope_id=%s",
            (scope_id,),
        ).fetchall())
        assert rows[alpha.id] == "archived"
        assert rows[beta.id] in {"candidate", "active"}
        beta_row = conn.execute(
            "SELECT payload ->> 'semantic_key', summary, evidence FROM memory_cards WHERE id=%s",
            (beta.id,),
        ).fetchone()
        assert beta_row[0] == "beta:900"
        assert beta_row[1] == "Бета стоит 900 USD."
        assert beta_row[2][0]["excerpt"] == "Бета стоит 900 USD"
        conn.execute("DELETE FROM memory_cards WHERE scope_type='personal' AND scope_id=%s", (scope_id,))
        conn.commit()
'''
    integration_path.write_text(integration, encoding="utf-8")
