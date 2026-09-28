from __future__ import annotations

import copy
import json

import pytest

from app_v2.labs import request_resolution as core
from app_v2.labs import resolution_delivery as ux
from app_v2.labs import telegram_lab_resolution as base
from app_v2.labs.telegram_lab import LabError
from app_v2.labs.telegram_lab_delivery import DeliveryBot, DRAFT_TTL, implementation
from tests_v2.unit.test_lab_request_resolution import Adapter, TG, AT, seed, source, update

URL_TEXT = "Ссылка на занятие https://example.org/demo"


def bot(tmp_path, *, clock=None):
    adapter, tg = Adapter(), TG()
    b = DeliveryBot(tg, tmp_path, seed(tmp_path), adapter_factory=lambda: adapter,
                    background=False, **({"clock": clock} if clock else {}))
    b.username = "test_lab_bot"
    return b, adapter, tg


def run(b, n, text, **kw):
    b.handle(update(n, text, **kw))
    if "U" + str(n) in b.data["jobs"]:
        b.execute("U" + str(n))
        assert b.data["jobs"]["U" + str(n)]["status"] == "done"


def session(b):
    return b.data["sessions"][b.data["active"]]


def waiting_pair(b):
    run(b, 1, "/lab_flow")
    run(b, 2, "/lab_ask Где ссылка?", user=910002)


def test_shared_send_all_waiters_recorded_and_later_repeat_is_a_new_answer(tmp_path):
    b, adapter, tg = bot(tmp_path)
    old = {p: p.read_bytes() for p in tmp_path.glob("runs/**/*.json")}
    waiting_pair(b)
    calls, posts = len(adapter.calls), len(tg.sent)
    b.handle(update(3, URL_TEXT))
    assert len(adapter.calls) == calls
    assert session(b)["sources"] == []
    assert len(tg.sent) == posts
    assert "/lab_attach D001 T001" in tg.messages[-1]
    assert "U3" not in b.data["jobs"]
    run(b, 4, "/lab_attach D001 T001")
    assert len(tg.sent) == posts + 1
    action = b.data["jobs"]["U4"]["actions"][0]
    assert len(action["targets"]) == 2
    assert "reply_parameters" not in tg.sent[-1][1]
    assert tg.sent[-1][1]["text"] == URL_TEXT
    waiters = session(b)["tickets"]["T001"]["waiters"]
    assert all(w["status"] == "answered" for w in waiters.values())
    assert len({w["delivery_message_id"] for w in waiters.values()}) == 1
    assert core.metrics(session(b))["confirmed"] == 0
    run(b, 5, "/lab_ask Напомните ссылку", user=910002)
    assert len(tg.sent) == posts + 2
    assert "reply_parameters" in tg.sent[-1][1]
    totals = ux.metrics(session(b))
    assert totals["organizational_question_messages_total"] == 3
    assert totals["waiter_ticket_pairs"] == 2
    assert totals["unique_tickets"] == totals["owner_clarifications_total"] == 1
    assert totals["tracked_repeats_delivered_without_new_owner"] == 1
    assert totals["tracked_messages_fully_delivered"] == 3
    assert totals["group_answer_posts_since_upgrade"] == 2
    assert all(p.read_bytes() == original for p, original in old.items())


def test_duplicate_update_and_second_attach_never_charge_or_send_again(tmp_path):
    b, adapter, tg = bot(tmp_path)
    waiting_pair(b)
    run(b, 3, URL_TEXT)
    run(b, 4, "/lab_attach D001 T001")
    calls, sent, owner_inputs = len(adapter.calls), len(tg.sent), session(b)["owner_clarifications"]
    b.handle(update(4, "/lab_attach D001 T001"))
    b.execute("U4")
    b.handle(update(5, "/lab_attach D001 T001"))
    assert "U5" not in b.data["jobs"]
    assert (len(adapter.calls), len(tg.sent), session(b)["owner_clarifications"]) == (calls, sent, owner_inputs)


def test_success_receipt_can_be_applied_idempotently(tmp_path):
    b, _, _ = bot(tmp_path)
    waiting_pair(b)
    run(b, 3, "/lab_answer T001 " + URL_TEXT)
    s = session(b)
    action = b.data["jobs"]["U3"]["actions"][0]
    before = copy.deepcopy(s)
    ux.record_delivery(s, action, action["receipt"])
    assert s == before


def test_ambiguous_shared_send_is_not_delivery_for_any_waiter_and_restart_does_not_resend(tmp_path):
    b, adapter, tg = bot(tmp_path)
    waiting_pair(b)
    tg.fail = True
    b.handle(update(3, "/lab_answer T001 " + URL_TEXT))
    b.execute("U3")
    assert b.data["jobs"]["U3"]["status"] == "delivery_unknown"
    assert core.metrics(session(b))["answer_ready"] == 2
    assert ux.metrics(session(b))["tracked_messages_fully_delivered"] == 0
    calls, sends = len(adapter.calls), len(tg.sent)
    again = DeliveryBot(tg, tmp_path, "a" * 64, adapter_factory=lambda: adapter, background=False)
    again.execute("U3")
    again.handle(update(4, "/lab_retry U3"))
    assert (len(adapter.calls), len(tg.sent)) == (calls, sends)
    assert all(w["status"] == "answer_ready" for w in session(again)["tickets"]["T001"]["waiters"].values())


@pytest.mark.parametrize("difference", ["body", "source", "ticket"])
def test_differing_answers_are_never_coalesced(difference):
    e = core.ResolutionEngine(Adapter(), [])
    s, _ = e.ask(core.new_session("s", AT), "a", "Где ссылка?")
    s, _ = e.ask(s, "b", "Где ссылка?")
    s, actions = e.supply(s, "T001", URL_TEXT)
    if difference == "body":
        s["tickets"]["T001"]["waiters"]["b"]["answer"] += " Другой подтверждённый фрагмент."
    elif difference == "source":
        s["sources"].append({**s["sources"][0], "id": "S0002"})
        s["tickets"]["T001"]["waiters"]["b"]["source_ids"] = ["S0002"]
    else:
        s["tickets"]["T002"] = copy.deepcopy(s["tickets"]["T001"])
        s["tickets"]["T002"]["id"] = "T002"
        actions[1]["ticket"] = "T002"
    assert len(ux.group_answers(actions, s, [], batch=True)) == 2


def test_private_source_cannot_be_group_broadcast_even_with_one_recipient():
    s = core.new_session("s", AT)
    e = core.ResolutionEngine(Adapter(), [source(recipient="a")])
    s, actions = e.ask(s, "a", "Где ссылка?")
    with pytest.raises(ValueError, match="shareable"):
        ux.group_answers(actions, s, [source(recipient="a")], batch=True)


@pytest.mark.parametrize("how", ["member", "other_chat", "other_bot", "sender_chat", "bot_sender"])
def test_owner_confirmation_authentication(how, tmp_path):
    b, adapter, _ = bot(tmp_path)
    waiting_pair(b)
    run(b, 3, URL_TEXT)
    u = update(4, "/lab_attach@test_lab_bot D001 T001")
    if how == "member":
        u["message"]["from"]["id"] = 910002
    elif how == "other_chat":
        u["message"]["chat"]["id"] = -910002
    elif how == "other_bot":
        u["message"]["text"] = "/lab_attach@other_bot D001 T001"
    elif how == "sender_chat":
        u["message"]["sender_chat"] = {"id": -910001}
    else:
        u["message"]["from"]["is_bot"] = True
    count = len(adapter.calls)
    b.handle(u)
    assert "U4" not in b.data["jobs"]
    assert len(adapter.calls) == count
    assert session(b)["sources"] == []


@pytest.mark.parametrize("how", ["expired", "discarded", "session", "ticket_changed", "wrong_ticket"])
def test_stale_draft_does_not_become_a_source(how, tmp_path):
    now = [100.0]
    b, adapter, _ = bot(tmp_path, clock=lambda: now[0])
    waiting_pair(b)
    run(b, 3, URL_TEXT)
    command = "/lab_attach D001 T001"
    if how == "expired":
        now[0] += DRAFT_TTL + 1
    elif how == "discarded":
        run(b, 4, "/lab_discard D001")
    elif how == "session":
        run(b, 4, "/lab_flow live")
    elif how == "ticket_changed":
        run(b, 4, "/lab_answer T001 Позже уточню.")
    else:
        command = "/lab_attach D001 T999"
    sources = copy.deepcopy(session(b)["sources"])
    calls = len(adapter.calls)
    b.handle(update(5, command))
    assert "U5" not in b.data["jobs"]
    assert session(b)["sources"] == sources
    assert len(adapter.calls) == calls


def test_draft_survives_same_version_restart_without_model_call(tmp_path):
    b, adapter, tg = bot(tmp_path)
    waiting_pair(b)
    run(b, 3, URL_TEXT)
    count = len(adapter.calls)
    again = DeliveryBot(tg, tmp_path, "a" * 64, adapter_factory=lambda: adapter, background=False)
    assert len(adapter.calls) == count
    run(again, 4, "/lab_attach D001 T001")
    assert core.metrics(session(again))["owner_clarifications"] == 1


def test_legacy_upgrade_preserves_ticket_sources_history_jobs_and_exact_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "old-build")
    approved = seed(tmp_path)
    adapter, tg = Adapter(), TG()
    previous = base.ResolutionBot(tg, tmp_path, approved, adapter_factory=lambda: adapter, background=False)
    waiting_pair(previous)
    run(previous, 3, "/lab_answer T001 " + URL_TEXT)
    run(previous, 4, "/lab_ask Где ссылка?", user=910002)
    original = copy.deepcopy(previous.data)
    raw = previous.path.read_bytes()
    old_rounds = {p: p.read_bytes() for p in tmp_path.glob("runs/**/*.json")}
    calls = len(adapter.calls)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "new-build")
    b = DeliveryBot(tg, tmp_path, approved, adapter_factory=lambda: adapter, background=False)
    assert len(adapter.calls) == calls
    assert b.data["jobs"] == original["jobs"]
    assert b.data["binding"] == original["binding"]
    assert b.data["active"] == original["active"]
    for key in ("tickets", "sources", "queries", "at", "archive_cutoff", "owner_clarifications"):
        assert session(b)[key] == session(previous)[key]
    assert list((b.path.parent / "upgrade_backups").glob("*.json"))[0].read_bytes() == raw
    assert all(p.read_bytes() == data for p, data in old_rounds.items())
    totals = ux.metrics(session(b))
    assert totals["organizational_question_messages_total"] == 3
    assert totals["waiter_ticket_pairs"] == 2
    assert totals["legacy_query_messages_without_delivery_audit"] == 3
    assert totals["tracked_repeats_delivered_without_new_owner"] == 0
    run(b, 5, "/lab_ask Напомните ссылку", user=910002)
    assert ux.metrics(session(b))["tracked_repeats_delivered_without_new_owner"] == 1
    assert session(b)["owner_clarifications"] == 1


@pytest.mark.parametrize("invalid", ["running", "delivery_unknown", "different_model", "different_core"])
def test_unsafe_upgrade_leaves_original_bytes_untouched(invalid, tmp_path, monkeypatch):
    approved = seed(tmp_path)
    old = base.ResolutionBot(TG(), tmp_path, approved, adapter_factory=Adapter, background=False)
    run(old, 1, "/lab_flow")
    if invalid in {"running", "delivery_unknown"}:
        old.data["jobs"]["U1"]["status"] = invalid
    elif invalid == "different_model":
        session(old)["implementation"]["classifier_model"] = "different"
    else:
        session(old)["implementation"]["code_sha256"] = "different"
    old.save()
    raw = old.path.read_bytes()
    with pytest.raises(LabError):
        DeliveryBot(TG(), tmp_path, approved, adapter_factory=Adapter, background=False)
    assert old.path.read_bytes() == raw


def test_report_escapes_content_hides_transport_and_distinguishes_metrics(tmp_path):
    b, _, tg = bot(tmp_path)
    waiting_pair(b)
    run(b, 3, "/lab_answer T001 " + URL_TEXT)
    session(b)["context"].append({"text": "<script>alert(1)</script>", "actor": "x"})
    run(b, 4, "/lab_ops_report")
    report = tg.documents[-1][1].decode()
    assert "<script>" not in report and "&lt;script&gt;" in report
    assert "-910001" not in report and '"message_ref"' not in report
    assert "delivery_message_id" not in report and "owner_drafts" not in report
    assert "Сообщений с организационными вопросами" in report
    assert "Повторы обслужены без нового уточнения" in report
    assert "после успешной доставки" in report


def test_partially_delivered_multipart_question_is_not_counted_fully_answered():
    s = core.new_session("s", AT)
    s["queries"] = [{"actor": "a", "kind": "organizational", "tickets": ["T001", "T002"]}]
    ux.track_question(None, s)
    event = s["delivery_audit"]["events"][0]
    event["outcomes"][0]["delivery"] = "sent"
    event["outcomes"][0]["repeat_without_new_owner"] = True
    assert ux.metrics(s)["tracked_messages_fully_delivered"] == 0
    assert ux.metrics(s)["tracked_repeats_delivered_without_new_owner"] == 0


def test_help_after_upgrade_does_not_trigger_models_or_reset_scenario(tmp_path):
    b, adapter, _ = bot(tmp_path)
    waiting_pair(b)
    before = copy.deepcopy(session(b))
    count = len(adapter.calls)
    run(b, 3, "/lab_status")
    assert len(adapter.calls) == count and session(b) == before
    assert implementation()["revision"] == ux.REVISION
