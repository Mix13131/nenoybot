"""Opt-in UX/delivery layer; original resolution engine and controllers are untouched."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app_v2.labs import resolution_delivery as ux
from app_v2.labs import telegram_lab_resolution as base
from app_v2.labs.telegram_lab import LabError, Telegram, atomic_json

LOG = logging.getLogger("lab_delivery")
DRAFT_TTL = 1200
HELP = base.HELP + (
    "\nУточнение без reply: бот предложит черновик D001. "
    "/lab_attach D001 T001 — явно связать его с вопросом; /lab_discard D001 — не использовать. "
    "Без подтверждения текст не становится источником. Срок черновика — 20 минут."
)


def implementation() -> dict[str, Any]:
    return {"revision": ux.REVISION, "core": base.implementation(),
            "delivery_sha256": hashlib.sha256(Path(ux.__file__).read_bytes() + Path(__file__).read_bytes()).hexdigest()}


def upgrade_state(root: Path, approved: str) -> None:
    """Compatible metadata-only continuation, with an exact pre-upgrade backup."""
    path = root / "resolution_v1" / "state.json"
    if not path.exists():
        return
    raw = path.read_bytes()
    state = json.loads(raw)
    current = implementation()
    if state.get("delivery_implementation") == current:
        return
    pack_path = root / "runs" / "round02" / "pack.json"
    manifest = base.read_json(pack_path.with_name("manifest.json"))
    if (manifest.get("approved_upload_sha256") != approved
            or manifest.get("initial_artifact_hashes", {}).get("pack.json") != base.digest(pack_path)
            or state.get("pack_sha256") != base.digest(pack_path)):
        raise LabError("Нарушена целостность источников; данные не изменены.")
    if any(j["status"] != "done" or any(a.get("delivery") != "sent" for a in j.get("actions", []))
           for j in state["jobs"].values()):
        raise LabError("Есть незавершённые или неоднозначные операции. Обновление без их разбора запрещено.")
    target_core = current["core"]
    for session in state["sessions"].values():
        previous = session.get("implementation", {})
        if any(previous.get(k) != target_core[k] for k in ("revision", "classifier_model", "code_sha256")):
            raise LabError("Несовместимая версия механизма/модели. Старые данные не изменены.")
    checksum = hashlib.sha256(raw).hexdigest()
    backup = path.parent / "upgrade_backups" / (checksum + ".json")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if backup.exists():
        if backup.read_bytes() != raw:
            raise LabError("Контрольная копия не совпала. Обновление остановлено.")
    else:
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    for session in state["sessions"].values():
        session["implementation"] = copy.deepcopy(target_core)
        ux.audit(session)  # baseline only; never backfill historical outcomes
    state.setdefault("delivery_upgrades", []).append({
        "backup_sha256": checksum, "from": state.get("delivery_implementation", state.get("implementation")),
        "to": current, "at": datetime.now(timezone.utc).isoformat(),
        "preserved_tickets": sum(len(s["tickets"]) for s in state["sessions"].values()),
        "preserved_sources": sum(len(s["sources"]) for s in state["sessions"].values()),
        "preserved_queries": sum(len(s["queries"]) for s in state["sessions"].values()),
    })
    state.update(delivery_implementation=current, implementation=target_core)
    atomic_json(path, state)


class DeliveryBot(base.ResolutionBot):
    def __init__(self, tg: Any, root: Path, approved: str, *, adapter_factory: Any = None,
                 background: bool = True, clock: Any = time.time):
        upgrade_state(root, approved)
        super().__init__(tg, root, approved, adapter_factory=adapter_factory, background=False)
        self.clock = clock
        self.data.setdefault("delivery_implementation", implementation())
        self.data.setdefault("owner_drafts", {})
        self.data.setdefault("draft_sequence", 0)
        self.save()
        self.background = background
        if background:
            threading.Thread(target=self.worker, daemon=True).start()

    def enqueue(self, jid: str, operation: dict[str, Any]) -> None:
        super().enqueue(jid, {**operation, "delivery_implementation": implementation()})

    def handle(self, update: dict[str, Any]) -> None:
        msg = update.get("message", {})
        person, chat = msg.get("from", {}), msg.get("chat", {})
        binding = self.data["binding"]
        owner = (person.get("id") == binding["owner_id"] and not person.get("is_bot")
                 and not msg.get("sender_chat") and chat.get("id") == binding["chat_id"]
                 and chat.get("type") in {"group", "supergroup"})
        text = str(msg.get("text") or "").strip()
        if not owner or not text:
            return super().handle(update)
        command, _, rest = text.partition(" ")
        if command.startswith("/") and "@" in command:
            command, target = command.split("@", 1)
            if target.lower() != self.username.lower():
                return
        command = command.lower()
        jid = "U" + str(update["update_id"])
        special = command in {"/lab_attach", "/lab_discard", "/lab_ops_report", "/lab_help", "/lab_status", "/help", "/start"}
        plain = not command.startswith("/") and not msg.get("reply_to_message")
        if not special and not plain:
            return super().handle(update)
        with self.lock:
            if jid in self.data["seen"] or jid in self.data["jobs"]:
                return
            try:
                if command in {"/lab_help", "/lab_status", "/help", "/start"}:
                    self.notify(HELP)
                elif command == "/lab_ops_report":
                    self.tg.document(binding["chat_id"], "group_lab_resolution.html", ux.operations_report(self.data))
                elif command == "/lab_attach":
                    self.attach(jid, rest)
                elif command == "/lab_discard":
                    draft = self.data["owner_drafts"].get(rest.strip().upper())
                    if draft and not draft.get("job"):
                        draft["discarded"] = True
                    self.notify("Черновик не будет использован. Уже принятые команды этим не отменяются.")
                elif plain:
                    self.hint(text)
                self.data["seen"] = (self.data["seen"] + [jid])[-1000:]
                self.save()
            except LabError as exc:
                self.notify(str(exc))

    def hint(self, text: str) -> None:
        sid = self.data.get("active")
        if not self.data.get("enabled") or not sid:
            return
        session = self.data["sessions"][sid]
        candidates = {tid: t["owner_inputs"] for tid, t in session["tickets"].items()
                      if t["need"]["kind"] == "information" and base.core.ticket_status(t) == "waiting_admin"}
        if not candidates:
            return
        if len(text) > 4000:
            raise LabError("Уточнение слишком длинное: максимум 4000 символов. Источник не сохранён.")
        now = self.clock()
        drafts = self.data["owner_drafts"]
        for key in list(drafts):
            if drafts[key]["expires"] < now:
                del drafts[key]
        if len(drafts) >= 20:
            raise LabError("Слишком много неподтверждённых черновиков. Используй /lab_answer T001 текст.")
        self.data["draft_sequence"] += 1
        did = f"D{self.data['draft_sequence']:03d}"
        drafts[did] = {"text": text, "session": sid, "candidates": candidates,
                       "expires": now + DRAFT_TTL, "job": None, "discarded": False,
                       "implementation": implementation()}
        self.save()
        options = [f"{tid}: {session['tickets'][tid]['need']['question'][:110]}\n/lab_attach {did} {tid}"
                   for tid in list(candidates)[:10]]
        self.notify("Сохранить это сообщение как уточнение к обращению? Выбери нужную команду:\n\n"
                    + "\n\n".join(options)
                    + f"\n\nПока это черновик {did}, не источник ответа. /lab_discard {did} — не использовать.")

    def validate_draft(self, did: str, tid: str, *, job: str | None = None) -> dict[str, Any]:
        draft = self.data["owner_drafts"].get(did)
        if not draft or draft["discarded"] or self.clock() > draft["expires"]:
            raise LabError("Черновик отсутствует или истёк. Отправь уточнение заново либо /lab_answer.")
        if draft["implementation"] != implementation():
            raise LabError("Черновик от другой версии. Отправь уточнение заново.")
        session = self.active()
        ticket = session["tickets"].get(tid)
        if (draft["session"] != session["id"] or tid not in draft["candidates"] or not ticket
                or ticket["need"]["kind"] != "information"
                or base.core.ticket_status(ticket) != "waiting_admin"
                or ticket["owner_inputs"] != draft["candidates"][tid]):
            raise LabError("Обращение или сценарий изменились. Уточнение не привязано автоматически.")
        if draft["job"] is not None and draft["job"] != job:
            raise LabError("Этот черновик уже принят. Повторных вызовов модели нет.")
        return draft

    def attach(self, jid: str, rest: str) -> None:
        parts = rest.upper().split()
        if len(parts) != 2:
            raise LabError("Формат: /lab_attach D001 T001")
        did, tid = parts
        draft = self.validate_draft(did, tid)
        draft["job"] = jid
        try:
            self.enqueue(jid, {"type": "answer", "session": draft["session"], "ticket": tid,
                               "text": draft["text"], "owner_draft": did, "draft_job": jid})
        except Exception:
            # Admission may already be durable; never release a draft in that case.
            if jid not in self.data["jobs"]:
                draft["job"] = None
            raise

    def evaluate(self, op: dict[str, Any]) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        if op.get("delivery_implementation") != implementation():
            raise LabError("Версия задания отличается. Автоматический пересчёт запрещён.")
        with self.lock:
            if op.get("owner_draft"):
                self.validate_draft(op["owner_draft"], op["ticket"], job=op["draft_job"])
            before = copy.deepcopy(self.data["sessions"].get(op.get("session", "")))
        sid, session, actions = super().evaluate(op)
        if session is None:
            return sid, session, actions
        if op["type"] in {"ask", "flow"}:
            ux.track_question(before, session)
        else:
            ux.audit(session)
        return sid, session, ux.group_answers(actions, session, self.corpus, batch=op["type"] == "answer")

    def deliver(self, jid: str) -> None:
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
                    session = self.data["sessions"].get(job["session"])
                    if session is not None:
                        ux.record_delivery(session, action, mid)
                    action.update(delivery="sent", receipt=mid)
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
    bot = DeliveryBot(tg, root, approved)
    bot.username = me["username"]
    LOG.info("delivery_ready sources=%s tickets=%s clarifications=%s old_rounds_preserved=true automatic_model_calls=false",
             len(bot.corpus), sum(len(s["tickets"]) for s in bot.data["sessions"].values()),
             sum(s["owner_clarifications"] for s in bot.data["sessions"].values()))
    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ok = time.monotonic() - bot.last_poll < 120
            self.send_response((200 if ok else 503) if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "revision": ux.REVISION}).encode())
        def log_message(self, *args: Any) -> None:
            pass
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    LOG.info("delivery_polling_started")
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
