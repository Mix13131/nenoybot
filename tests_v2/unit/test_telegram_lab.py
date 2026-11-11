from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app_v2.labs.telegram_lab import (
    LabBot, LabError, MAX_UPLOAD, ObservedAdapter, checked_pack, render_report,
)

PAIR = "synthetic-pairing-code-for-tests"


def payload():
    return {"lab_replay_version": 1, "cases": [{
        "id": "c000001", "episode_id": "e1", "review_label": "stay_silent",
        "expected_bot_text": "DO NOT LEAK RUBRIC", "curation": {"private_note": "rubric"},
        "context": [{"id": "m1", "kind": "message", "actor": "admin",
                     "occurred_at": "2024-01-01T10:00:00Z", "text": "Завтра занятие.", "reply_to": None}],
        "current": {"id": "m2", "kind": "message", "actor": "member_001",
                    "occurred_at": "2024-01-01T10:01:00Z", "text": "Во сколько?", "reply_to": "m1"},
    }]}


def encoded(value=None):
    data = json.dumps(value or payload(), ensure_ascii=False).encode()
    return data, hashlib.sha256(data).hexdigest()


class FakeTelegram:
    def __init__(self):
        self.calls = []
        self.messages = []
        self.documents = []
        self.data = encoded()[0]

    def call(self, method, **params):
        self.calls.append((method, params))
        if method == "getChatAdministrators":
            return [{"user": {"id": 7}}]
        return True

    def message(self, chat, text, buttons=False):
        self.messages.append((chat, text))

    def document(self, chat, name, data):
        self.documents.append((chat, name, data))

    def download(self, document):
        return self.data


def update(text, *, chat=-10, user=7, kind="supergroup", document=None):
    message = {"chat": {"id": chat, "type": kind}, "from": {"id": user, "is_bot": False},
               "message_id": 1, "text": text}
    if document:
        message["document"] = document
    return {"update_id": 1, "message": message}


def bot(tmp_path, factory=None):
    tg = FakeTelegram()
    kwargs = {"runner_factory": factory} if factory else {}
    return LabBot(tg, tmp_path, PAIR, encoded()[1], **kwargs), tg


def test_approved_pack_strips_rubric_before_model():
    data, digest = encoded()
    pack = checked_pack(data, digest)
    assert len(pack["cases"]) == 1
    assert "DO NOT LEAK" not in json.dumps(pack)
    assert "curation" not in pack["cases"][0]
    assert "review_label" not in pack["cases"][0]


@pytest.mark.parametrize("data", [b'{"messages":[]}', b"not json", b'{"cases":[]}'])
def test_unknown_file_is_rejected_without_echoing_contents(data):
    with pytest.raises(LabError) as exc:
        checked_pack(data, encoded()[1])
    assert data.decode() not in str(exc.value)


def test_oversize_file_fails_before_parse():
    data = b"x" * (MAX_UPLOAD + 1)
    with pytest.raises(LabError):
        checked_pack(data, hashlib.sha256(data).hexdigest())


def test_future_context_is_rejected_even_with_matching_digest():
    p = payload()
    p["cases"][0]["context"][0]["occurred_at"] = "2024-02-01T10:00:00Z"
    data, digest = encoded(p)
    with pytest.raises(LabError):
        checked_pack(data, digest)


@pytest.mark.parametrize("kwargs,code", [({}, "wrong"), ({"user": 9}, PAIR), ({"kind": "private"}, PAIR)])
def test_pairing_requires_secret_and_group_admin(tmp_path, kwargs, code):
    b, tg = bot(tmp_path)
    b.handle(update("/lab_bind " + code, **kwargs))
    assert not b.state.get("chat_id")
    assert not tg.messages


def test_pairing_is_durable_and_cannot_rebind_to_another_group(tmp_path):
    b, tg = bot(tmp_path)
    b.handle(update("/lab_bind " + PAIR))
    assert b.state["chat_id"] == -10
    restarted = LabBot(tg, tmp_path, PAIR, encoded()[1])
    tg.messages.clear()
    restarted.handle(update("/lab_bind " + PAIR, chat=-11))
    assert restarted.state["chat_id"] == -10
    assert tg.messages == []


def test_other_member_and_other_group_cannot_upload_or_run(tmp_path):
    b, tg = bot(tmp_path)
    b.handle(update("/lab_bind " + PAIR))
    tg.messages.clear()
    b.handle(update("", user=8, document={"file_id": "synthetic"}))
    b.handle(update("/lab_run", chat=-99))
    assert not b.pack["cases"]
    assert not tg.messages


def test_upload_is_not_a_model_call_and_duplicate_keeps_results(tmp_path):
    def forbidden():
        raise AssertionError("upload must not invoke models")
    b, tg = bot(tmp_path, forbidden)
    b.handle(update("/lab_bind " + PAIR))
    b.handle(update("", document={"file_id": "synthetic"}))
    b.results["c000001"] = {"status": "done"}
    b.handle(update("", document={"file_id": "synthetic"}))
    assert len(b.pack["cases"]) == 1
    assert b.results["c000001"]["status"] == "done"


def test_report_escapes_model_and_message_html():
    pack = payload()
    pack["cases"][0]["current"]["text"] = "<script>bad</script>"
    results = {"c000001": {"status": "done", "generated_text": "<img onerror=x>",
                           "decision": {"primary_action": "reply"}}}
    report = render_report(pack, results).decode()
    assert "<script>bad" not in report
    assert "<img onerror" not in report
    assert "&lt;script&gt;" in report


def test_model_fallback_is_error_not_valid_silence(tmp_path):
    adapter = SimpleNamespace(failures=0)
    class Runner:
        def evaluate_case(self, case):
            adapter.failures += 1
            return {"decision": {"primary_action": "ignore"}}
    b, tg = bot(tmp_path, lambda: (Runner(), adapter))
    data, digest = encoded()
    b.pack = checked_pack(data, digest)
    b.run_cases(-10, b.pack["cases"], False)
    assert b.results["c000001"]["status"] == "error"
    assert "decision" not in b.results["c000001"]
    assert not b.busy


def test_success_uses_injected_brain_and_preserves_original_cases(tmp_path):
    class Runner:
        def evaluate_case(self, case):
            return {"decision": {"primary_action": "reply", "reason_codes": ["question_to_bot"]},
                    "generated_text": "Уточните у организатора."}
    b, tg = bot(tmp_path, lambda: (Runner(), SimpleNamespace(failures=0)))
    data, digest = encoded()
    b.pack = checked_pack(data, digest)
    before = json.dumps(b.pack, sort_keys=True)
    b.run_cases(-10, b.pack["cases"], True)
    assert b.results["c000001"]["status"] == "done"
    assert json.dumps(b.pack, sort_keys=True) == before
    assert tg.documents


def test_interrupted_case_is_not_silently_replayed(tmp_path):
    (tmp_path / "results.json").write_text(json.dumps({"c000001": {"status": "running"}}))
    b, _ = bot(tmp_path)
    assert b.results["c000001"]["status"] == "interrupted"


def test_observed_adapter_counts_hidden_failures():
    class Fails:
        def generate_json(self, *args, **kwargs):
            raise ValueError("synthetic")
    adapter = ObservedAdapter(Fails())
    with pytest.raises(ValueError):
        adapter.generate_json("role", "input")
    assert adapter.failures == adapter.calls == 1
