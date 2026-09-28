from __future__ import annotations

from datetime import timedelta

import pytest

from app_v2.labs import request_resolution as r
from tests_v2.unit.test_lab_request_resolution import AT, Adapter, need, source


def test_advancing_simulation_never_exposes_future_historical_source():
    s = r.new_session("historical", AT)
    corpus = [source(), source("future", available_at="2024-03-05T10:00:01Z")]
    for _ in range(100):
        s["at"] = r.iso(r.stamp(s["at"]) + timedelta(seconds=1))
        assert [x["id"] for x in r.visible_sources(s, corpus, "member")] == ["H1"]
    assert s["archive_cutoff"] == AT


def test_new_owner_input_is_visible_without_unlocking_later_archive():
    e = r.ResolutionEngine(Adapter(), [source("future", available_at="2024-03-05T10:00:01Z")])
    s, _ = e.ask(r.new_session("historical", AT), "member", "Где ссылка?")
    s["at"] = "2024-03-05T10:00:02Z"
    s, out = e.supply(s, "T001", "Ссылка https://example.org/scenario")
    assert out[0]["kind"] == "answer"
    assert [x["id"] for x in r.visible_sources(s, e.corpus, "member")] == ["S0001"]
    assert s["archive_cutoff"] == AT


def test_old_historical_session_without_cutoff_fails_closed():
    s = r.new_session("historical", AT)
    del s["archive_cutoff"]
    with pytest.raises(ValueError, match="cutoff missing"):
        r.visible_sources(s, [source()], "member")


@pytest.mark.parametrize("aspect,text", [
    ("terms", "Условия: можно присоединиться после 25 мая. [link]"),
    ("time", "Сегодня занятие в 19:00. [Вложение: отсутствует]"),
    ("change_scope", "Это разовый перенос. [link]"),
])
def test_independent_text_is_not_lost_due_to_adjacent_hidden_artifact(aspect, text):
    src = source(text=text)
    f = r.Finding(coverage="complete", evidence=[r.Evidence(source_id="H1", quote=text)], missing="")
    checked = r.checked_finding(f, [src], r.Need(**need(aspect=aspect)))
    assert checked.coverage == "complete"
    assert checked.evidence[0].quote == text


@pytest.mark.parametrize("aspect", ["terms", "material", "link"])
def test_placeholder_alone_still_cannot_supply_an_answer(aspect):
    text = "[link] [Вложение: содержимое отсутствует]"
    f = r.Finding(coverage="complete", evidence=[r.Evidence(source_id="H1", quote=text)], missing="")
    checked = r.checked_finding(f, [source(text=text)], r.Need(**need(aspect=aspect)))
    assert checked.coverage == "none"
