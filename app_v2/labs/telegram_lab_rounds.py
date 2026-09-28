"""Second-round UI for the isolated lab. Original /data artifacts are never overwritten."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from html import escape

from app_v2.labs.cohost_replay import REVISION, make_cohost_runner, revision_manifest
from app_v2.labs.telegram_lab import LabBot, LabError, Telegram, atomic_json, render_report

LOG = logging.getLogger("group_lab_rounds")
ROUND_BUTTONS = {"inline_keyboard": [
    [{"text": "Второй раунд", "callback_data": "lab_round2"}],
    [{"text": "Следующий кейс", "callback_data": "lab_next"},
     {"text": "Прогнать выборку", "callback_data": "lab_run"}],
    [{"text": "Статус", "callback_data": "lab_status"},
     {"text": "Отчёт текущего раунда", "callback_data": "lab_report"}],
    [{"text": "Первый отчёт — До", "callback_data": "lab_before"}],
]}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot_round(destination: Path, source: Path, manifest: dict[str, Any], *, empty_results: bool) -> None:
    """Publish a complete directory before changing the active pointer. Never reset an existing round."""
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".round-", dir=destination.parent))
    try:
        for name in ("state.json", "pack.json", "results.json"):
            src = source / name
            if name == "results.json" and empty_results:
                atomic_json(staging / name, {})
            elif src.exists():
                shutil.copyfile(src, staging / name)
                os.chmod(staging / name, 0o600)
            else:
                value = {"offset": 0} if name == "state.json" else {"cases": []} if name == "pack.json" else {}
                atomic_json(staging / name, value)
        hashes = {name: hashlib.sha256((staging / name).read_bytes()).hexdigest()
                  for name in ("pack.json", "results.json")}
        atomic_json(staging / "manifest.json", {**manifest, "initial_artifact_hashes": hashes})
        staging.replace(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def round_report(root: Path, pack: dict[str, Any], results: dict[str, Any]) -> bytes:
    meta = read_json(root / "manifest.json")
    page = render_report(pack, results).decode("utf-8")
    # Replace only the old renderer's introductory paragraph, never case text.
    start = page.index("</h1>") + len("</h1>")
    end = page.index("</p>", start) + len("</p>")
    intro = ("<p><strong>Раунд " + escape(str(meta["round"])) + " · " + escape(meta["revision"]) + "</strong><br>"
             "Одинаковые исходные кейсы. Память, реальные действия и динамические лимиты не воспроизводятся. "
             "Во втором раунде используется отдельная лабораторная политика выбора помощи и инструкция cohost; "
             "это не неизменённый production routing и не автоматическая оценка качества.</p>"
             "<details><summary>Версия проверки</summary><pre>" + escape(json.dumps(meta, ensure_ascii=False, indent=2)) + "</pre></details>")
    return (page[:start] + intro + page[end:]).encode("utf-8")


class RoundTelegram(Telegram):
    def message(self, chat: int, text: str, buttons: bool = False) -> None:
        chunks = [text[start:start + 3500] for start in range(0, len(text), 3500)]
        for index, chunk in enumerate(chunks):
            args: dict[str, Any] = {"chat_id": chat, "text": chunk, "link_preview_options": {"is_disabled": True}}
            if buttons and index == len(chunks) - 1:
                args["reply_markup"] = ROUND_BUTTONS
            self.call("sendMessage", **args)


class RoundLabBot(LabBot):
    def __init__(self, tg: Any, root: Path, pairing: str, approved_sha: str,
                 runner_factory: Any = make_cohost_runner):
        self.base_root = root
        root.mkdir(parents=True, exist_ok=True)
        self.pointer = root / "lab_round_state.json"
        snapshot_round(root / "runs" / "round01", root,
                       {"round": 1, "revision": "education_community_v1_baseline",
                        "source_commit": "528eeec1d48cebb2bc0d9b52273449399b8bf43f",
                        "approved_upload_sha256": approved_sha}, empty_results=False)
        active = read_json(self.pointer).get("active") if self.pointer.exists() else "round01"
        if active not in {"round01", "round02"}:
            raise LabError("Неизвестный сохранённый раунд. Данные не изменены.")
        active_root = root / "runs" / active
        if not (active_root / "manifest.json").exists():
            raise LabError("Раунд не найден. Данные не изменены.")
        super().__init__(tg, active_root, pairing, approved_sha, runner_factory)
        self.active_round = active

    def status(self) -> str:
        done = sum(r.get("status") == "done" for r in self.results.values())
        errors = sum(r.get("status") in {"error", "interrupted"} for r in self.results.values())
        label = "1 — До (сохранён)" if self.active_round == "round01" else "2 — После"
        return (f"Раунд {label}.\nСитуаций: {len(self.pack['cases'])}. Обработано: {done}. Ошибок: {errors}.\n"
                + ("Прогон идёт." if self.busy else "Готово к управлению кнопками.")
                + "\nПервый результат сохранён отдельно. Память и реальные действия в группах отключены.")

    def select_round2(self) -> None:
        with self.lock:
            if self.busy:
                raise LabError("Сначала дождись завершения текущего прогона.")
            if not self.pack["cases"]:
                raise LabError("Проверенная выборка ещё не загружена.")
            if self.active_round == "round02":
                return  # Repeated callbacks never erase data or restart paid calls.
            target = self.base_root / "runs" / "round02"
            snapshot_round(target, self.root, {"round": 2, **revision_manifest(),
                           "approved_upload_sha256": self.approved_sha}, empty_results=True)
            atomic_json(self.pointer, {"active": "round02"})
            self.root = target
            self.active_round = "round02"
            self.state = read_json(target / "state.json")
            self.pack = read_json(target / "pack.json")
            self.results = read_json(target / "results.json")

    def _authorized(self, update: dict[str, Any]) -> tuple[dict[str, Any], str, int] | None:
        cb = update.get("callback_query")
        msg = cb.get("message", {}) if cb else update.get("message", {})
        actor = cb.get("from", {}) if cb else msg.get("from", {})
        chat = msg.get("chat", {})
        if actor.get("is_bot") or msg.get("sender_chat") or chat.get("type") not in {"group", "supergroup"}:
            return None
        if chat.get("id") != self.state.get("chat_id") or actor.get("id") != self.state.get("owner_id"):
            return None
        text = "/" + cb.get("data", "") if cb else str(msg.get("text", ""))
        token = text.strip().split(" ", 1)[0]
        if "@" in token and token.split("@", 1)[1].lower() != self.username.lower():
            return None
        return msg, token.split("@", 1)[0].lower(), chat["id"]

    def handle(self, update: dict[str, Any]) -> None:
        authorized = self._authorized(update)
        if not authorized:
            # Pairing is the only accepted action before an owner exists.
            if not self.state.get("chat_id"):
                super().handle(update)
            return
        msg, command, chat = authorized
        if command in {"/lab_round2", "/lab_before", "/lab_report", "/lab_status", "/start", "/help", "/lab_help"}:
            cb = update.get("callback_query")
            if cb:
                self.tg.call("answerCallbackQuery", callback_query_id=cb["id"])
            try:
                if command == "/lab_round2":
                    self.select_round2()
                    self.tg.message(chat, "Второй раунд выбран. Первый отчёт не изменён.\nНажми «Прогнать выборку».\n" + self.status(), True)
                elif command in {"/lab_before", "/lab_report"}:
                    with self.lock:
                        report_root = self.base_root / "runs" / "round01" if command == "/lab_before" else self.root
                        pack, results = read_json(report_root / "pack.json"), read_json(report_root / "results.json")
                        report = round_report(report_root, pack, results)
                    self.tg.document(chat, f"group_lab_{report_root.name}.html", report)
                else:
                    self.tg.message(chat, self.status() + "\nДля новой проверки выбери «Второй раунд», затем «Прогнать выборку». Повторная загрузка файла не нужна.", True)
            except LabError as exc:
                self.tg.message(chat, str(exc))
            return
        if "document" in msg and self.active_round == "round01" and self.pack["cases"]:
            self.tg.message(chat, "Первый раунд сохранён. Повторная загрузка не нужна — выбери «Второй раунд».", True)
            return
        super().handle(update)

    def start_run(self, chat: int, only_one: bool) -> None:
        if self.active_round != "round02":
            self.tg.message(chat, "Первый прогон сохранён как «До». Нажми «Второй раунд», затем «Прогнать выборку».", True)
            return
        stored = read_json(self.root / "manifest.json")
        live = revision_manifest()
        if any(stored.get(key) != live.get(key) for key in ("revision", "policy_sha256", "classifier_model", "generator_model")):
            raise LabError("Конфигурация отличается от сохранённого раунда. Смешивать версии нельзя.")
        super().start_run(chat, only_one)

    def run_cases(self, chat: int, cases: list[dict[str, Any]], only_one: bool) -> None:
        # All saves go only to the active round directory, never legacy /data/results.json.
        try:
            runner, adapter = self.runner_factory()
            for case in cases:
                with self.lock:
                    self.results[case["id"]] = {"status": "running", "revision": REVISION}
                    atomic_json(self.root / "results.json", self.results)
                prior = adapter.failures
                try:
                    result = runner.evaluate_case(case)
                    if adapter.failures != prior:
                        raise LabError("API fallback is not a valid test result")
                    result.update(status="done", revision=REVISION)
                except Exception as exc:
                    result = {"status": "error", "revision": REVISION, "error_type": type(exc).__name__,
                              "error": "Вызов модели не завершён; это не решение промолчать."}
                    LOG.warning("case_failed type=%s", type(exc).__name__)
                with self.lock:
                    self.results[case["id"]] = result
                    atomic_json(self.root / "results.json", self.results)
                if only_one:
                    self.tg.message(chat, f"Раунд 2 · {case['id']} · {result.get('decision', {}).get('primary_action', 'ошибка')}\n\n"
                                    + (result.get("generated_text") or result.get("error") or "Промолчал."), True)
                if result["status"] == "error":
                    break
            with self.lock:
                report = round_report(self.root, self.pack, self.results)
            self.tg.document(chat, "group_lab_round02.html", report)
            LOG.info("round_complete round=2 completed=%s errors=%s",
                     sum(r.get("status") == "done" for r in self.results.values()),
                     sum(r.get("status") == "error" for r in self.results.values()))
            self.tg.message(chat, "Второй прогон остановлен или завершён. Отчёт выше.\n" + self.status().replace("Прогон идёт.", ""), True)
        except Exception as exc:
            LOG.error("round_job_failed type=%s", type(exc).__name__)
            try:
                self.tg.message(chat, "Техническая ошибка. Уже полученные ответы сохранены отдельно от первого прогона.", True)
            except LabError:
                pass
        finally:
            with self.lock:
                self.busy = False


def main() -> None:
    import fcntl
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for name in ("httpx", "httpx2", "httpcore", "httpcore2", "openai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    os.umask(0o077)
    token = os.environ.get("NENOY_V2_TELEGRAM_BOT_TOKEN", "").strip()
    pairing = os.environ.get("NENOY_LAB_PAIRING_CODE", "").strip()
    approved = os.environ.get("NENOY_LAB_APPROVED_PACK_SHA256", "").strip()
    if not token or not os.environ.get("NENOY_V2_OPENAI_API_KEY") or len(pairing) < 16 or not re.fullmatch(r"[a-f0-9]{64}", approved):
        raise SystemExit("Lab credentials, pairing and approved digest are required.")
    root = Path(os.environ.get("NENOY_LAB_DATA_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    process_lock = (root / "process.lock").open("a")
    fcntl.flock(process_lock, fcntl.LOCK_EX)
    tg = RoundTelegram(token)
    me = tg.call("getMe")
    if tg.call("getWebhookInfo").get("url"):
        raise SystemExit("Existing webhook; no takeover performed.")
    bot = RoundLabBot(tg, root, pairing, approved)
    bot.username = me["username"]
    LOG.info("rounds_ready revision=%s active=%s bound=%s cases=%s baseline_preserved=true",
             REVISION, bot.active_round, bool(bot.state.get("chat_id")), len(bot.pack["cases"]))

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ok = time.monotonic() - bot.last_poll < 120
            self.send_response((200 if ok else 503) if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "revision": REVISION, "round": bot.active_round}).encode())
        def log_message(self, *args: Any) -> None:
            pass
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    LOG.info("lab_rounds_polling_started")
    while True:
        try:
            updates = tg.call("getUpdates", offset=bot.state.get("offset", 0), timeout=25,
                              allowed_updates=["message", "callback_query"])
            bot.last_poll = time.monotonic()
            for update in updates:
                with bot.lock:
                    bot.state["offset"] = update["update_id"] + 1
                    atomic_json(bot.root / "state.json", bot.state)
                bot.handle(update)
        except Exception as exc:
            LOG.warning("poll_error type=%s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
