"""Delivery-only helpers. The resolution policy and source verification stay unchanged."""
from __future__ import annotations

import copy
import json
from html import escape
from typing import Any

from app_v2.labs import request_resolution as core

REVISION = "lab_resolution_delivery_v1"


def audit(session: dict[str, Any], baseline: int | None = None) -> dict[str, Any]:
    return session.setdefault("delivery_audit", {
        "revision": REVISION,
        "legacy_query_messages": len(session["queries"]) if baseline is None else baseline,
        "events": [], "posts": [],
    })


def track_question(before: dict[str, Any] | None, after: dict[str, Any]) -> None:
    """Record a question occurrence, not merely the latest waiter value."""
    count = len(before["queries"]) if before else 0
    journal = audit(after, count)
    old_tickets = before["tickets"] if before else {}
    for index, query in enumerate(after["queries"][count:], start=count + 1):
        outcomes = []
        for tid in dict.fromkeys(query["tickets"]):
            previous = old_tickets.get(tid, {})
            old_answers = [sorted(w["source_ids"]) for w in previous.get("waiters", {}).values()
                           if w["status"] in {"answered", "confirmed"} and w.get("source_ids")]
            outcomes.append({"ticket": tid, "delivery": "pending", "source_ids": [],
                             "known_answer_sources": old_answers,
                             "owner_inputs_at_ask": previous.get("owner_inputs", 0),
                             "repeat_without_new_owner": False})
        journal["events"].append({"query_index": index, "actor": query["actor"],
                                  "kind": query["kind"], "at": after["at"], "outcomes": outcomes})


def group_answers(actions: list[dict[str, Any]], session: dict[str, Any],
                  corpus: list[dict[str, Any]], *, batch: bool) -> list[dict[str, Any]]:
    """One prepared operation only; never a global suppress-identical-text cache."""
    output: list[dict[str, Any]] = []
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    sources = {s["id"]: s for s in [*corpus, *session["sources"]]}
    for original in actions:
        action = copy.deepcopy(original)
        if action.get("kind") != "answer":
            output.append(action)
            continue
        ticket = session["tickets"][action["ticket"]]
        waiter = ticket["waiters"][action["actor"]]
        ids = sorted(waiter["source_ids"])
        # This controller has only a public group destination. Fail closed for
        # any recipient-restricted evidence, even when there is just one waiter.
        if (ticket["need"]["kind"] != "information" or not ids
                or any(sid not in sources or sources[sid].get("recipient") is not None for sid in ids)):
            raise ValueError("answer is not shareable in the sandbox group")
        body = waiter.get("answer")
        if not isinstance(body, str) or not body.strip() or len(body) > 3900:
            raise ValueError("invalid public answer body")
        action.update(text=body, targets=[action["actor"]], source_ids=ids)
        key = (action["ticket"], body, tuple(ids))
        if batch and key in groups:
            grouped = groups[key]
            if action["actor"] not in grouped["targets"]:
                grouped["targets"].append(action["actor"])
            grouped.pop("reply_to", None)  # one group response for all waiters
        else:
            groups[key] = action
            output.append(action)
    return output


def record_delivery(session: dict[str, Any], action: dict[str, Any], message_id: int) -> None:
    """A success receipt marks all included recipients; never marks confirmation."""
    if action.get("kind") != "answer":
        return
    journal = audit(session)
    if any(p["receipt"] == message_id for p in journal["posts"]):
        return
    actors = action.get("targets", [action["actor"]])
    ticket = session["tickets"][action["ticket"]]
    for actor in actors:
        core.delivered(session, {**action, "actor": actor}, message_id)
        waiter = ticket["waiters"][actor]
        ids = sorted(waiter["source_ids"])
        for event in journal["events"]:
            if event["actor"] != actor:
                continue
            for item in event["outcomes"]:
                if item["ticket"] != ticket["id"] or item["delivery"] == "sent":
                    continue
                item.update(delivery="sent", source_ids=ids,
                            repeat_without_new_owner=(ids in item["known_answer_sources"]
                                and ticket["owner_inputs"] == item["owner_inputs_at_ask"]))
    journal["posts"].append({"ticket": ticket["id"], "targets": list(actors),
                              "source_ids": list(action.get("source_ids", [])),
                              "at": session["at"], "receipt": message_id})


def metrics(session: dict[str, Any]) -> dict[str, Any]:
    journal = session.get("delivery_audit", {})
    events = journal.get("events", [])
    organizational = [e for e in events if e["kind"] == "organizational"]
    complete = [e for e in organizational if e["outcomes"]
                and all(o["delivery"] == "sent" for o in e["outcomes"])]
    waiters = [w for t in session["tickets"].values() for w in t["waiters"].values()]
    return {
        "organizational_question_messages_total": sum(q["kind"] == "organizational" for q in session["queries"]),
        "unique_tickets": len(session["tickets"]),
        "waiter_ticket_pairs": len(waiters),
        "distinct_requester_roles": len({a for t in session["tickets"].values() for a in t["waiters"]}),
        "currently_waiting_admin": sum(w["status"] == "waiting_admin" for w in waiters),
        "currently_answer_ready": sum(w["status"] == "answer_ready" for w in waiters),
        "currently_answered_or_confirmed": sum(w["status"] in {"answered", "confirmed"} for w in waiters),
        "confirmed_waiters": sum(w["status"] == "confirmed" for w in waiters),
        "owner_clarifications_total": session["owner_clarifications"],
        "tracked_question_messages_since_upgrade": len(organizational),
        "tracked_messages_fully_delivered": len(complete),
        "tracked_repeats_delivered_without_new_owner": sum(
            all(o["repeat_without_new_owner"] for o in e["outcomes"]) for e in complete),
        "group_answer_posts_since_upgrade": len(journal.get("posts", [])),
        "legacy_query_messages_without_delivery_audit": journal.get("legacy_query_messages", len(session["queries"])),
    }


def operations_report(state: dict[str, Any]) -> bytes:
    def dump(value: Any) -> str:
        return escape(json.dumps(value, ensure_ascii=False, indent=2))
    labels = {
        "organizational_question_messages_total": "Сообщений с организационными вопросами — всего",
        "unique_tickets": "Отдельных задач организатору / обращений",
        "waiter_ticket_pairs": "Учтённых ожиданий (участник + обращение)",
        "distinct_requester_roles": "Участников/ролей, включая историческую тестовую роль",
        "currently_waiting_admin": "Ожидают уточнения организатора сейчас",
        "currently_answer_ready": "Ответ готов, доставка ещё не подтверждена",
        "currently_answered_or_confirmed": "Получили ответ по данным Telegram",
        "confirmed_waiters": "Явно подтвердили, что ответ помог",
        "owner_clarifications_total": "Уточнений организатора — всего",
        "tracked_question_messages_since_upgrade": "Орг. вопросов с новым учётом доставки",
        "tracked_messages_fully_delivered": "Ответы доставлены на все части вопроса — новый учёт",
        "tracked_repeats_delivered_without_new_owner": "Повторы обслужены без нового уточнения организатора — новый учёт",
        "group_answer_posts_since_upgrade": "Публичных ответов — после обновления",
        "legacy_query_messages_without_delivery_audit": "Прежних сообщений без нового поштучного учёта",
    }
    page = ["<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>",
            "<title>Resolution Lab</title><h1>Обработка обращений</h1>",
            "<p>Проверенный неполный корпус. Это последовательный тест. Уточнения владельца — данные песочницы. "
            "Отправленный ответ не равен подтверждённому решению. Личный доступ, покупки и реальные действия "
            "в исходной группе отключены.</p>",
            "<p>Уточнение организатора считается один раз. Использование его источника в следующем ответе "
            "не означает повторного участия организатора. Новый показатель повторов считается только с момента "
            "обновления и только после успешной доставки; прошлым вопросам результат не приписывается. "
            "Подтверждение участника — отдельное действие.</p>"]
    for session in state["sessions"].values():
        totals = metrics(session)
        page.append("<h2>" + escape(session["id"]) + "</h2><table>")
        page.extend("<tr><td>" + escape(labels[k]) + "</td><td>" + str(v) + "</td></tr>" for k, v in totals.items())
        page.append("</table><details><summary>Точные показатели</summary><pre>" + dump(totals) + "</pre></details>")
        cleaned = copy.deepcopy(session)
        for ticket in cleaned["tickets"].values():
            for waiter in ticket["waiters"].values():
                waiter.pop("message_ref", None)
                waiter.pop("delivery_message_id", None)
        for post in cleaned.get("delivery_audit", {}).get("posts", []):
            post.pop("receipt", None)
        page.append("<details><summary>Источники, обращения, версии и доставка</summary><pre>" + dump(cleaned) + "</pre></details>")
    jobs = [{"id": jid, "status": j["status"], "attempts": j.get("attempts", 0),
             "error_code": j.get("error_code"), "deliveries": [a.get("delivery") for a in j.get("actions", [])],
             "answer_recipient_counts": [len(a.get("targets", [a.get("actor")])) for a in j.get("actions", []) if a.get("kind") == "answer"]}
            for jid, j in state["jobs"].items()]
    page.append("<h2>Технический журнал</h2><pre>" + dump(jobs) + "</pre>")
    page.append("<details><summary>Обновления без пересчёта старых результатов</summary><pre>" + dump(state.get("delivery_upgrades", [])) + "</pre></details>")
    page.append("<style>body{font:16px/1.5 sans-serif;max-width:1000px;margin:30px auto;padding:15px}pre{white-space:pre-wrap;overflow-wrap:anywhere}td{padding:6px 12px;border-bottom:1px solid #ccc}td+td{font-weight:bold}</style></html>")
    return "\n".join(page).encode("utf-8")
