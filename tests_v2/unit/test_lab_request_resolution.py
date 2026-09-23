from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_v2.labs import request_resolution as r
from app_v2.labs.telegram_lab import atomic_json
from app_v2.labs.telegram_lab_resolution import ResolutionBot, bootstrap, digest, operations_report

AT = "2024-03-05T10:00:00Z"


def need(**kw):
    return {"topic": "access", "aspect": "link", "kind": "information", "entity": "занятие 2024-03-05",
            "question": "Где ссылка на занятие?", "request_span": "Где ссылка?", "same_ticket_id": None, **kw}


def source(sid="H1", text="Ссылка на занятие https://example.org/demo", **kw):
    return {"id": sid, "text": text, "occurred_at": "2024-03-05T09:00:00Z", "available_at": "2024-03-05T09:00:00Z",
            "origin": "reviewed_excerpt", "session": None, "ticket": None, "recipient": None, "disabled": False, **kw}


class Adapter:
    """Synthetic policy, not an assertion of real model semantic accuracy."""
    def __init__(self, needs=None, finding=None, kind="organizational"):
        self.needs, self.finding, self.kind = needs, finding, kind
        self.calls = []
    def generate_json(self, role, text, **kwargs):
        p = json.loads(text)
        self.calls.append((p, kwargs))
        if kwargs["schema_name"].endswith("inquiry"):
            items = copy.deepcopy(self.needs) if self.needs is not None else [need()]
            for n in items:
                n["request_span"] = p["current"]
                if p["tickets"] and n["same_ticket_id"] is None:
                    n["same_ticket_id"] = p["tickets"][0]["id"]
            out = {"kind": self.kind, "needs": items if self.kind == "organizational" else []}
        elif self.finding is not None:
            out = self.finding(p)
        else:
            found = next((s for s in p["sources"] if "https://" in s["text"] or "Условия:" in s["text"]), None)
            out = {"coverage": "complete" if found else "none", "evidence": [{"source_id": found["id"], "quote": found["text"]}] if found else [], "missing": "Нужна ссылка на занятие."}
        return SimpleNamespace(parsed=out)


def test_search_is_asof_session_scoped_and_never_uses_private_source_for_another_user():
    s = r.new_session("a", AT)
    corpus = [source(), source("future", available_at="2024-03-06T09:00:00Z"),
              source("other", session="b"), source("private", recipient="member2"),
              source("expired", valid_until="2024-03-05T09:59:00Z"), source("off", disabled=True)]
    assert [x["id"] for x in r.visible_sources(s, corpus, "member1")] == ["H1"]
    assert r.visible_sources(r.new_session("clean", AT, mode="live"), corpus, "member1") == []


def test_reviewed_versions_become_available_only_when_actually_observed():
    row = {"id": "m1", "actor": "admin", "text": "Новый текст", "occurred_at": "2024-03-01T10:00:00Z"}
    pack = {"cases": [{"context": [row], "current": {"id": "m2", "actor": "member", "text": "вопрос", "occurred_at": AT}}]}
    corpus = r.reviewed_corpus(pack)
    assert corpus[0]["available_at"] == AT
    assert not r.visible_sources(r.new_session("a", "2024-03-04T10:00:00Z"), corpus, "member")


def test_wrong_quote_or_unknown_source_is_not_a_resolved_request():
    n = r.Need(**need())
    for sid, quote in (("absent", "Текст"), ("H1", "Я всё придумал")):
        with pytest.raises(ValueError, match="unsupported"):
            r.checked_finding(r.Finding(coverage="complete", evidence=[r.Evidence(source_id=sid, quote=quote)], missing=""), [source()], n)
    with pytest.raises(ValueError, match="without evidence"):
        r.checked_finding(r.Finding(coverage="complete", evidence=[], missing=""), [source()], n)


def test_hidden_link_cannot_count_as_an_answer():
    src = source(text="Ссылка на занятие [link]")
    f = r.Finding(coverage="complete", evidence=[r.Evidence(source_id="H1", quote=src["text"])], missing="")
    assert r.checked_finding(f, [src], r.Need(**need())).coverage == "none"


def test_one_admin_clarification_closes_information_gap_and_repeat_needs_no_new_admin_card():
    e = r.ResolutionEngine(Adapter(), [])
    s, actions = e.ask(r.new_session("test", AT), "first", "Где ссылка?", 10)
    assert actions[0]["kind"] == "admin_card"
    assert r.metrics(s)["waiting_admin"] == 1
    s, actions = e.ask(s, "second", "Мне тоже нужна ссылка", 11)
    assert len(s["tickets"]) == 1
    assert not any(a["kind"] == "admin_card" for a in actions)
    s, actions = e.supply(s, "T001", "Ссылка на занятие https://example.org/demo")
    assert len(actions) == 2 and all(a["kind"] == "answer" for a in actions)
    assert r.metrics(s)["answer_ready"] == 2
    assert r.metrics(s)["confirmed"] == 0
    for a in actions:
        r.delivered(s, a, 123)
    assert r.metrics(s)["delivered_after_admin"] == 2
    s, actions = e.ask(s, "third", "Подскажите адрес этого занятия", 12)
    assert len(s["tickets"]) == 1
    assert actions[0]["kind"] == "answer"
    assert "https://example.org/demo" in actions[0]["text"]
    assert r.metrics(s)["owner_clarifications"] == 1
    r.delivered(s, actions[0], 124)
    r.acknowledge(s, "T001", "third", helped=True)
    assert r.metrics(s)["confirmed"] == 1
    r.acknowledge(s, "T001", "third", helped=False)
    assert r.metrics(s)["confirmed"] == 0
    assert not r.visible_sources(s, [], "third")


def test_repeat_from_same_person_still_returns_supported_answer_not_redirect_to_report():
    e = r.ResolutionEngine(Adapter(), [source()])
    s, out = e.ask(r.new_session("test", AT), "a", "Где ссылка?", 10)
    r.delivered(s, out[0], 101)
    s, out = e.ask(s, "a", "Напомните ссылку", 11)
    assert out[0]["kind"] == "answer"
    assert "example.org" in out[0]["text"]


def test_admin_acknowledgement_does_not_resolve_anything():
    e = r.ResolutionEngine(Adapter(), [])
    s, _ = e.ask(r.new_session("test", AT), "a", "Где ссылка?")
    s, out = e.supply(s, "T001", "Позже посмотрю, пока не знаю.")
    assert r.metrics(s)["waiting_admin"] == 1
    assert not any(a["kind"] == "answer" for a in out)


@pytest.mark.parametrize("topic", ["billing", "enrollment"])
def test_published_terms_are_not_blanket_redirected_to_admin(topic):
    n = need(topic=topic, aspect="terms")
    e = r.ResolutionEngine(Adapter(needs=[n]), [source(text="Условия: участие после 25 мая разрешено.")])
    s, actions = e.ask(r.new_session("t", AT), "a", "Какие условия?")
    assert actions[0]["kind"] == "answer"
    assert r.metrics(s)["waiting_admin"] == 0


def test_personal_requests_never_merge_across_users_or_publish_personal_owner_link():
    e = r.ResolutionEngine(Adapter(needs=[need(kind="personal_action", aspect="personal_access")]), [source()])
    s, _ = e.ask(r.new_session("t", AT), "a", "Восстановите мой доступ")
    s, _ = e.ask(s, "b", "Мне тоже восстановите доступ")
    assert len(s["tickets"]) == 2
    s, actions = e.supply(s, "T001", "СЕКРЕТ https://example.org/private-token")
    assert "СЕКРЕТ" not in json.dumps(s, ensure_ascii=False)
    assert "private-token" not in json.dumps(actions, ensure_ascii=False)
    assert r.metrics(s)["waiting_admin"] == 2


def test_multicomponent_request_is_not_closed_by_answering_only_one_part():
    needs = [need(), need(topic="enrollment", aspect="terms", entity="другой курс", question="Есть ли другой курс?")]
    e = r.ResolutionEngine(Adapter(needs=needs), [])
    s, actions = e.ask(r.new_session("t", AT), "a", "Ссылка и есть ли другой курс?")
    assert len(s["tickets"]) == 2
    s, _ = e.supply(s, "T001", "Ссылка https://example.org/demo")
    assert s["tickets"]["T002"]["waiters"]["a"]["status"] == "waiting_admin"


def test_social_input_does_not_create_administrative_work():
    e = r.ResolutionEngine(Adapter(kind="social"), [])
    s, actions = e.ask(r.new_session("t", AT), "a", "Спасибо, увидимся!")
    assert not s["tickets"] and not actions


def seed(tmp: Path):
    approved = "a" * 64
    pack = {"approved_sha256": approved, "lab_replay_version": 1, "cases": [{
        "id": "c005544", "episode_id": "e1", "context": [],
        "current": {"id": "m1", "kind": "message", "actor": "member_001", "occurred_at": AT, "text": "Где ссылка?", "reply_to": None}}]}
    r2, r3 = tmp / "runs/round02", tmp / "runs/round03"
    atomic_json(r2 / "pack.json", pack)
    atomic_json(r2 / "manifest.json", {"approved_upload_sha256": approved, "initial_artifact_hashes": {"pack.json": digest(r2 / "pack.json")}})
    atomic_json(r2 / "results.json", {"m1": "preserve"})
    atomic_json(r3 / "state.json", {"chat_id": -910001, "owner_id": 910001, "offset": 99})
    atomic_json(r3 / "results.json", {"m1": "preserve3"})
    return approved


class TG:
    def __init__(self, fail=False):
        self.sent, self.messages, self.documents = [], [], []
        self.fail = fail
    def message(self, chat, text):
        self.messages.append(text)
    def document(self, chat, name, data):
        self.documents.append((name, data))
    def call(self, method, **params):
        self.sent.append((method, params))
        if self.fail:
            raise RuntimeError("PRIVATE EXCEPTION MUST NOT LEAK")
        return {"message_id": 1000 + len(self.sent)}


def update(n, text, *, user=910001, chat=-910001, reply=None):
    msg = {"chat": {"id": chat, "type": "group"}, "from": {"id": user}, "message_id": n, "text": text}
    if reply:
        msg["reply_to_message"] = {"message_id": reply}
    return {"update_id": n, "message": msg}


def test_runtime_end_to_end_duplicate_update_and_old_rounds_immutable(tmp_path):
    approved = seed(tmp_path)
    old = {p: p.read_bytes() for p in tmp_path.glob("runs/**/*.json")}
    adapter, tg = Adapter(), TG()
    b = ResolutionBot(tg, tmp_path, approved, adapter_factory=lambda: adapter, background=False)
    b.username = "test_lab_bot"
    b.handle(update(1, "/lab_flow@test_lab_bot"))
    assert not adapter.calls
    b.execute("U1")
    b.handle(update(1, "/lab_flow@test_lab_bot"))
    before = len(adapter.calls)
    b.execute("U1")
    assert len(adapter.calls) == before
    card_id = int(next(iter(b.data["cards"])))
    b.handle(update(2, "Ссылка https://example.org/demo", reply=card_id))
    b.execute("U2")
    s = b.data["sessions"][b.data["active"]]
    assert r.metrics(s)["delivered_after_admin"] == 1
    b.handle(update(3, "/lab_ask Где ссылка?", user=910002))
    b.execute("U3")
    assert r.metrics(b.data["sessions"][b.data["active"]])["owner_clarifications"] == 1
    assert len(b.data["sessions"][b.data["active"]]["tickets"]) == 1
    assert len(b.data["cards"]) == 1
    for p, data in old.items():
        assert p.read_bytes() == data
    report = operations_report(b.data).decode()
    assert "-910001" not in report
    assert "example.org" in report


def test_only_bound_owner_can_start_or_supply_and_other_bots_are_ignored(tmp_path):
    b = ResolutionBot(TG(), tmp_path, seed(tmp_path), adapter_factory=Adapter, background=False)
    b.username = "test_lab_bot"
    b.handle(update(1, "/lab_flow", user=910002))
    b.handle(update(2, "/lab_flow", chat=-910002))
    b.handle(update(3, "/lab_flow@other_bot"))
    assert b.data["jobs"] == {}


def test_restart_does_not_start_paid_jobs_and_preserves_binding(tmp_path):
    approved = seed(tmp_path)
    adapter = Adapter()
    b = ResolutionBot(TG(), tmp_path, approved, adapter_factory=lambda: adapter, background=False)
    b.handle(update(1, "/lab_flow"))
    b.data["jobs"]["U1"]["status"] = "running"
    b.save()
    again = ResolutionBot(TG(), tmp_path, approved, adapter_factory=lambda: adapter, background=False)
    assert again.data["jobs"]["U1"]["status"] == "interrupted"
    assert not adapter.calls
    assert again.data["binding"]["offset"] == 99


def test_ambiguous_telegram_send_is_not_marked_delivered_or_automatically_resent(tmp_path):
    b = ResolutionBot(TG(fail=True), tmp_path, seed(tmp_path), adapter_factory=Adapter, background=False)
    b.handle(update(1, "/lab_flow"))
    b.execute("U1")
    assert b.data["jobs"]["U1"]["status"] == "delivery_unknown"
    count = len(b.tg.sent)
    b.execute("U1")
    assert len(b.tg.sent) == count
    assert "PRIVATE EXCEPTION" not in operations_report(b.data).decode()


def test_error_is_not_silence_and_can_be_retried_only_explicitly(tmp_path):
    class Broken:
        def generate_json(self, *a, **k):
            raise RuntimeError("SECRET")
    b = ResolutionBot(TG(), tmp_path, seed(tmp_path), adapter_factory=Broken, background=False)
    b.handle(update(1, "/lab_flow"))
    b.execute("U1")
    assert b.data["jobs"]["U1"]["status"] == "error"
    assert b.data["sessions"] == {}
    assert "SECRET" not in operations_report(b.data).decode()
    b.adapter_factory = Adapter
    b.handle(update(2, "/lab_retry U1"))
    b.execute("U1")
    assert b.data["jobs"]["U1"]["status"] == "done"
