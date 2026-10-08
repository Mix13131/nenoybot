"""Opt-in resolution lab. Only the paired sandbox is ever a Telegram destination."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app_v2.labs import request_resolution as core
from app_v2.labs.cohost_helpfulness import make_cohost_runner
from app_v2.labs.round_recovery import safe_error
from app_v2.labs.telegram_lab import LabError, Telegram, atomic_json
from app_v2.labs.telegram_lab_rounds import read_json, round_report
from app_v2.labs.telegram_lab_focus import focus_report

LOG = logging.getLogger("lab_resolution")
DEFAULT_CASE = "c005544"
HELP = (
    "Лаборатория обращений: поиск → вопрос организатору → ответ → повтор без новой задачи.\n"
    "/lab_flow — начать случай с недостающей ссылкой из проверенной истории.\n"
    "/lab_flow c001132 — случай о сроке переноса; /lab_flow live — чистый сценарий.\n"
    "Организатор отвечает обычным reply на карточку вопроса или /lab_answer T001 текст.\n"
    "/lab_ask вопрос — задать новый или повторный вопрос. Доступно и второму аккаунту.\n"
    "/lab_queue — нерешённое; /lab_ops_report — журнал и результаты.\n"
    "/lab_confirm T001 — явно подтвердить, что полученный ответ помог.\n"
    "/lab_reopen T001 — ответ не помог; /lab_pause — выключить приём вопросов.\n"
    "Источники — только проверенные фрагменты, НЕ полный архив. Уточнения организатора — "
    "новые данные тестового сценария, не исторические факты. Личный доступ не выдаётся."
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def implementation() -> dict[str, Any]:
    from app_v2.config import load_config
    env = {k: v for k, v in os.environ.items() if k.startswith("NENOY_V2_MODEL_")}
    cfg = load_config({**env, "NENOY_V2_ENV": "development"})
    return {"revision": core.REVISION, "classifier_model": cfg.model_classifier,
            "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA", "unknown"),
            "code_sha256": hashlib.sha256(Path(core.__file__).read_bytes() + Path(__file__).read_bytes()).hexdigest()}


def bootstrap(root: Path, approved_sha: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    source = root / "runs" / "round02"
    pack = read_json(source / "pack.json")
    manifest = read_json(source / "manifest.json")
    if (manifest.get("approved_upload_sha256") != approved_sha or
            manifest.get("initial_artifact_hashes", {}).get("pack.json") != digest(source / "pack.json")):
        raise LabError("Проверенная выборка изменилась. Запуск остановлен.")
    last = root / "runs" / "round03"
    if not (last / "state.json").is_file():
        raise LabError("Не найден третий раунд и привязка лаборатории.")
    path = root / "resolution_v1" / "state.json"
    if path.exists():
        state = read_json(path)
        if state.get("pack_sha256") != digest(source / "pack.json"):
            raise LabError("Источники изменились. Смешивать версии нельзя.")
    else:
        binding = read_json(last / "state.json")
        if not binding.get("chat_id") or not binding.get("owner_id"):
            raise LabError("Привязка владельца отсутствует.")
        state = {"version": 1, "binding": binding, "pack_sha256": digest(source / "pack.json"),
                 "active": None, "enabled": False, "sessions": {}, "aliases": {}, "jobs": {},
                 "cards": {}, "seen": [], "implementation": implementation()}
    for job in state["jobs"].values():
        if job["status"] in {"queued", "running", "prepared"}:
            job["status"] = "interrupted"
        for action in job.get("actions", []):
            if action.get("delivery") == "sending":
                action["delivery"] = "unknown"
    atomic_json(path, state)
    return path, state, pack


def operations_report(state: dict[str, Any]) -> bytes:
    safe_sessions = []
    for session in state["sessions"].values():
        # Telegram user/chat/message IDs and transport bindings are not report content.
        cleaned = copy.deepcopy(session)
        for ticket in cleaned["tickets"].values():
            for waiter in ticket["waiters"].values():
                waiter.pop("message_ref", None)
                waiter.pop("delivery_message_id", None)
        safe_sessions.append(cleaned)
    jobs = [{"id": jid, "status": j["status"], "error_code": j.get("error_code"),
             "attempts": j.get("attempts", 0), "deliveries": [a.get("delivery") for a in j.get("actions", [])]}
            for jid, j in state["jobs"].items()]
    page = "<!doctype html><meta charset='utf-8'><title>Resolution Lab</title><h1>Обработка обращений</h1>"
    page += "<p>Проверенный неполный корпус. Это последовательный тест, не четвёртый статистический прогон. "
    page += "Уточнения владельца — данные песочницы. Отправленный ответ не равен подтверждённому решению. "
    page += "Личный доступ, покупки и реальные действия в исходной группе отключены.</p>"
    for session in safe_sessions:
        page += "<h2>" + escape(session["id"]) + "</h2><pre>" + escape(json.dumps(core.metrics(session), ensure_ascii=False, indent=2)) + "</pre>"
        page += "<details><summary>Источники, обращения и результаты</summary><pre>" + escape(json.dumps(session, ensure_ascii=False, indent=2)) + "</pre></details>"
    page += "<h2>Технический журнал</h2><pre>" + escape(json.dumps(jobs, ensure_ascii=False, indent=2)) + "</pre>"
    page += "<style>body{font:16px/1.5 sans-serif;max-width:1000px;margin:30px auto}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>"
    return page.encode()


class ResolutionBot:
    def __init__(self, tg: Any, root: Path, approved: str, *, adapter_factory: Any = None, background: bool = True):
        self.tg, self.base_root = tg, root
        self.path, self.data, self.pack = bootstrap(root, approved)
        self.corpus = core.reviewed_corpus(self.pack)
        self.adapter_factory = adapter_factory or (lambda: make_cohost_runner()[1])
        self.username = ""
        self.lock = threading.RLock()
        self.last_poll = time.monotonic()
        self.work: queue.Queue[str] = queue.Queue(maxsize=20)
        self.background = background
        if background:
            threading.Thread(target=self.worker, daemon=True).start()

    def save(self) -> None:
        atomic_json(self.path, self.data)

    def alias(self, user: int) -> str:
        key = str(user)
        if key not in self.data["aliases"]:
            self.data["aliases"][key] = f"tester_{len(self.data['aliases']) + 1:03d}"
        return self.data["aliases"][key]

    def notify(self, text: str) -> None:
        self.tg.message(self.data["binding"]["chat_id"], text)

    def active(self) -> dict[str, Any]:
        sid = self.data["active"]
        if not self.data["enabled"] or not sid or sid not in self.data["sessions"]:
            raise LabError("Сначала владелец запускает /lab_flow. Обычная беседа не обрабатывается.")
        return self.data["sessions"][sid]

    def handle(self, update: dict[str, Any]) -> None:
        # Plain commands/replies only: no accepting arbitrary uploads or callback actions.
        msg = update.get("message", {})
        chat, person = msg.get("chat", {}), msg.get("from", {})
        binding = self.data["binding"]
        if (chat.get("id") != binding["chat_id"] or chat.get("type") not in {"group", "supergroup"}
                or person.get("is_bot") or msg.get("sender_chat") or not person.get("id")):
            return
        text = str(msg.get("text") or "").strip()
        if not text:
            return
        command, _, rest = text.partition(" ")
        if command.startswith("/") and "@" in command:
            command, target = command.split("@", 1)
            if target.lower() != self.username.lower():
                return
        command = command.lower()
        owner = person["id"] == binding["owner_id"]
        jid = "U" + str(update["update_id"])
        with self.lock:
            if jid in self.data["seen"] or jid in self.data["jobs"]:
                return
            try:
                if command in {"/start", "/help", "/lab_help", "/lab_status"}:
                    if owner:
                        self.notify(HELP)
                elif command in {"/lab_before", "/lab_previous", "/lab_third"} and owner:
                    name = {"/lab_before": "round01", "/lab_previous": "round02", "/lab_third": "round03"}[command]
                    r = self.base_root / "runs" / name
                    renderer = focus_report if name == "round03" else round_report
                    self.tg.document(binding["chat_id"], f"group_lab_{name}.html", renderer(r, read_json(r / "pack.json"), read_json(r / "results.json")))
                elif command == "/lab_ops_report" and owner:
                    self.tg.document(binding["chat_id"], "group_lab_resolution.html", operations_report(self.data))
                elif command == "/lab_queue" and owner:
                    s = self.active()
                    rows = [f"{t['id']} · {core.ticket_status(t)} · {t['need']['question']}" for t in s["tickets"].values()]
                    self.notify("Обращения сценария " + s["id"] + ":\n" + ("\n".join(rows) or "Пока нет.") + "\nanswered — доставлен ответ, confirmed — явно подтверждено. waiting_admin — не решено.")
                elif command == "/lab_pause" and owner:
                    self.data["enabled"] = False
                    self.notify("Приём вопросов выключен. Уже принятые задания и результаты сохранены.")
                elif command == "/lab_flow" and owner:
                    self.enqueue(jid, {"type": "flow", "case": rest.strip() or DEFAULT_CASE,
                                     "actor": "scenario_member", "message_ref": msg["message_id"]})
                elif command == "/lab_ask":
                    s = self.active()
                    self.enqueue(jid, {"type": "ask", "session": s["id"], "actor": self.alias(person["id"]),
                                      "text": rest.strip(), "message_ref": msg["message_id"]})
                elif command == "/lab_answer" and owner:
                    tid, _, answer = rest.strip().partition(" ")
                    self.enqueue(jid, {"type": "answer", "session": self.active()["id"], "ticket": tid.upper(), "text": answer})
                elif command in {"/lab_confirm", "/lab_reopen"}:
                    s = self.active()
                    actor = self.alias(person["id"])
                    tid = rest.strip().upper()
                    if owner and actor not in s["tickets"].get(tid, {}).get("waiters", {}):
                        actor = "scenario_member"
                    self.enqueue(jid, {"type": "confirm", "session": s["id"], "ticket": tid, "actor": actor,
                                      "helped": command == "/lab_confirm", "confirmation_by": "operator" if owner else "requester"})
                elif command == "/lab_retry" and owner:
                    target = rest.strip()
                    job = self.data["jobs"].get(target)
                    if not job or job["status"] not in {"error", "interrupted"} or job.get("attempts", 0) >= 3:
                        raise LabError("Нет доступного задания для повтора (максимум 3 попытки).")
                    if any(a.get("delivery") == "unknown" for a in job.get("actions", [])):
                        raise LabError("Доставка неоднозначна. Автоповтор запрещён; смотрите /lab_ops_report.")
                    self._enqueue_existing(target)
                elif owner and not command.startswith("/"):
                    ref = str(msg.get("reply_to_message", {}).get("message_id", ""))
                    card = self.data["cards"].get(ref)
                    if card and card["kind"] == "admin_card":
                        self.enqueue(jid, {"type": "answer", "session": card["session"], "ticket": card["ticket"], "text": text})
                self.data["seen"] = (self.data["seen"] + [jid])[-1000:]
                self.save()
            except (LabError, ValueError) as exc:
                message = str(exc) if isinstance(exc, LabError) else "Неверные данные команды. /lab_help"
                self.notify(message)

    def enqueue(self, jid: str, operation: dict[str, Any]) -> None:
        if len(self.data["jobs"]) >= 160 or self.work.full():
            raise LabError("Лимит текущего теста или очередь заполнены. Новые вызовы не запущены.")
        if len(operation.get("text", "")) > 6000:
            raise LabError("Слишком длинное сообщение для одного теста.")
        self.data["jobs"][jid] = {"operation": operation, "status": "queued", "attempts": 0, "implementation": implementation()}
        self.save()  # Admission is durable before model work or Telegram offset acknowledgement.
        if self.background:
            self.work.put_nowait(jid)

    def _enqueue_existing(self, jid: str) -> None:
        if self.work.full():
            raise LabError("Очередь заполнена.")
        job = self.data["jobs"][jid]
        if job["implementation"] != implementation():
            raise LabError("Версия изменилась; смешивать результаты нельзя.")
        job["status"] = "queued"
        self.save()
        if self.background:
            self.work.put_nowait(jid)

    def worker(self) -> None:
        while True:
            jid = self.work.get()
            try:
                self.execute(jid)
            finally:
                self.work.task_done()

    def execute(self, jid: str) -> None:
        try:
            with self.lock:
                job = self.data["jobs"][jid]
                if job["status"] != "queued":
                    return
                if job["implementation"] != implementation():
                    raise ValueError("implementation changed")
                job["status"] = "running"
                job["attempts"] += 1
                self.save()
                op = copy.deepcopy(job["operation"])
                prepared = "actions" in job
            if not prepared:
                sid, session, actions = self.evaluate(op)
                with self.lock:
                    if session is not None:
                        self.data["sessions"][sid] = session
                    if op["type"] == "flow":
                        self.data.update(active=sid, enabled=True)
                    job.update(session=sid, actions=[{**a, "delivery": "pending"} for a in actions], status="prepared")
                    self.save()
            self.deliver(jid)
            with self.lock:
                job["status"] = "done" if all(a["delivery"] == "sent" for a in job["actions"]) else "delivery_unknown"
                self.save()
            LOG.info("operation_complete status=%s", job["status"])
        except Exception as exc:
            with self.lock:
                code, _ = safe_error(exc)
                self.data["jobs"][jid].update(status="error", error_code=code)
                self.save()
            LOG.warning("operation_failed code=%s type=%s", code, type(exc).__name__)
            try:
                self.notify(f"Техническая ошибка {jid}. Это не молчание и не решённое обращение. Владелец может повторить: /lab_retry {jid}")
            except Exception:
                pass

    def evaluate(self, op: dict[str, Any]) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        if op["type"] == "flow":
            cid = op["case"]
            sid = "live" if cid == "live" else "history_" + cid
            with self.lock:
                if sid in self.data["sessions"]:
                    return sid, None, [{"kind": "notice", "text": "Сценарий уже создан; результаты не пересчитаны. /lab_queue или /lab_ask вопрос"}]
            case = next((c for c in self.pack["cases"] if c["id"] == cid), None)
            if cid != "live" and case is None:
                raise LabError("Такого кейса нет в проверенной выборке.")
            at = case["current"]["occurred_at"] if case else core.iso(datetime.now(timezone.utc))
            session = core.new_session(sid, at, mode="historical" if case else "live")
            session["implementation"] = implementation()
            if case:
                session["context"] = [{k: m[k] for k in ("actor", "text", "occurred_at")} for m in case["context"]]
                engine = core.ResolutionEngine(self.adapter_factory(), self.corpus)
                session, actions = engine.ask(session, "scenario_member", case["current"]["text"], op["message_ref"])
                intro = "Исторический сценарий: " + case["current"]["text"][:1500] + "\nПоиск только по проверенным фрагментам, доступным на тот момент. Твои последующие ответы будут данными симуляции."
            else:
                actions = []
                intro = "Чистый сценарий включён, без старого расписания. /lab_ask вопрос."
            return sid, session, [{"kind": "notice", "text": intro}, *actions]
        sid = op["session"]
        with self.lock:
            session = copy.deepcopy(self.data["sessions"][sid])
        if session["implementation"] != implementation():
            raise LabError("Версия сценария отличается. Старые результаты не изменены.")
        session["at"] = (core.iso(core.stamp(session["at"]) + timedelta(seconds=1)) if session["mode"] == "historical"
                          else core.iso(datetime.now(timezone.utc)))
        if op["type"] == "confirm":
            core.acknowledge(session, op["ticket"], op["actor"], helped=op["helped"])
            session["tickets"][op["ticket"]]["waiters"][op["actor"]]["confirmation_by"] = op["confirmation_by"]
            return sid, session, [{"kind": "notice", "text": "Подтверждение сохранено." if op["helped"] else "Обращение снова открыто; прежний ответ не считается решением."}]
        engine = core.ResolutionEngine(self.adapter_factory(), self.corpus)
        if op["type"] == "ask":
            session, actions = engine.ask(session, op["actor"], op["text"], op["message_ref"])
        elif op["type"] == "answer":
            session, actions = engine.supply(session, op["ticket"], op["text"])
        else:
            raise ValueError("unknown operation")
        return sid, session, actions

    def deliver(self, jid: str) -> None:
        with self.lock:
            job = self.data["jobs"][jid]
        for action in job["actions"]:
            with self.lock:
                if action["delivery"] != "pending":
                    continue
                action["delivery"] = "sending"
                self.save()
            try:
                params: dict[str, Any] = {"chat_id": self.data["binding"]["chat_id"], "text": action["text"][:4000],
                                         "link_preview_options": {"is_disabled": True}}
                if action.get("reply_to"):
                    params["reply_parameters"] = {"message_id": action["reply_to"], "allow_sending_without_reply": True}
                result = self.tg.call("sendMessage", **params)
                mid = int(result["message_id"])
                with self.lock:
                    action.update(delivery="sent", receipt=mid)
                    session = self.data["sessions"].get(job["session"])
                    if session is not None:
                        core.delivered(session, action, mid)
                    if action["kind"] == "admin_card":
                        self.data["cards"][str(mid)] = {"kind": "admin_card", "session": job["session"], "ticket": action["ticket"]}
                    self.save()
            except Exception:
                with self.lock:
                    action["delivery"] = "unknown"
                    self.save()
                break


def main() -> None:
    import fcntl
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for name in ("httpx", "httpx2", "httpcore", "httpcore2", "openai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    os.umask(0o077)
    token = os.environ.get("NENOY_V2_TELEGRAM_BOT_TOKEN", "").strip()
    approved = os.environ.get("NENOY_LAB_APPROVED_PACK_SHA256", "").strip()
    if not token or len(approved) != 64 or not os.environ.get("NENOY_V2_OPENAI_API_KEY"):
        raise SystemExit("Lab token, model key and approved digest required")
    root = Path(os.environ.get("NENOY_LAB_DATA_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    process_lock = (root / "process.lock").open("a")
    fcntl.flock(process_lock, fcntl.LOCK_EX)
    tg = Telegram(token)
    if tg.call("getWebhookInfo").get("url"):
        raise SystemExit("Existing webhook; no takeover performed")
    me = tg.call("getMe")
    bot = ResolutionBot(tg, root, approved)
    bot.username = me["username"]
    LOG.info("resolution_ready reviewed_sources=%s bound=true old_rounds_preserved=true automatic_model_calls=false", len(bot.corpus))
    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ok = time.monotonic() - bot.last_poll < 120
            self.send_response((200 if ok else 503) if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "revision": core.REVISION}).encode())
        def log_message(self, *args: Any) -> None:
            pass
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    LOG.info("resolution_polling_started")
    while True:
        try:
            updates = tg.call("getUpdates", offset=bot.data["binding"].get("offset", 0), timeout=25, allowed_updates=["message"])
            bot.last_poll = time.monotonic()
            for update in updates:
                bot.handle(update)
                with bot.lock:
                    bot.data["binding"]["offset"] = update["update_id"] + 1
                    bot.save()
        except Exception as exc:
            LOG.warning("poll_error type=%s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
