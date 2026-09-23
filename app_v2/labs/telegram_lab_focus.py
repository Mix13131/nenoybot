"""Focused, owner-triggered round 3. Prior rounds are read-only inputs."""
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
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from app_v2.labs.cohost_replay import REVISION, make_cohost_runner, revision_manifest
from app_v2.labs.round_recovery import VERSION_KEYS, prepare_resume, safe_error, same_implementation
from app_v2.labs.telegram_lab import LabBot, LabError, Telegram, atomic_json, render_report
from app_v2.labs.telegram_lab_rounds import RoundLabBot, read_json, round_report

LOG = logging.getLogger("group_lab_focus")
# Evaluation selection ONLY. Never supplied to planner/generator instructions.
# Issue cases plus positive/silence/safety controls; fixed before model execution.
FOCUS_CASE_IDS = (
    "c000224", "c001132", "c005312", "c005343", "c005544", "c000168",
    "c000378", "c006118", "c000332", "c000326", "c003435", "c000303",
)
BUTTONS = {"inline_keyboard": [
    [{"text": "Проверить 12 ситуаций", "callback_data": "lab_check"}],
    [{"text": "Статус", "callback_data": "lab_status"},
     {"text": "Отчёт раунда 3", "callback_data": "lab_report"}],
    [{"text": "Продолжить после ошибки", "callback_data": "lab_resume"}],
    [{"text": "Отчёт раунда 2", "callback_data": "lab_previous"},
     {"text": "Первый отчёт", "callback_data": "lab_before"}],
]}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_focus_cases(pack: dict[str, Any]) -> dict[str, Any]:
    cases = pack.get("cases", [])
    by_id = {case["id"]: case for case in cases}
    if len(by_id) != len(cases) or not set(FOCUS_CASE_IDS).issubset(by_id):
        raise LabError("Проверенная выборка не содержит всех контрольных ситуаций. Данные не изменены.")
    return {**pack, "cases": [by_id[cid] for cid in FOCUS_CASE_IDS]}


def prepare_focus(root: Path, approved_sha: str) -> Path:
    """Atomically publish a new subset without writing to round01/round02 or legacy files."""
    destination = root / "runs" / "round03"
    if destination.exists():
        manifest = read_json(destination / "manifest.json")
        if (manifest.get("round") != 3 or manifest.get("approved_upload_sha256") != approved_sha
                or manifest.get("input_sha256") != digest(destination / "pack.json")):
            raise LabError("Целостность третьего раунда не подтверждена. Запуск запрещён.")
        return destination
    source = root / "runs" / "round02"
    if not all((source / name).is_file() for name in ("manifest.json", "pack.json", "state.json", "results.json")):
        raise LabError("Не найден сохранённый второй раунд. Новая привязка не выполнялась.")
    source_meta = read_json(source / "manifest.json")
    if (source_meta.get("approved_upload_sha256") != approved_sha
            or source_meta.get("initial_artifact_hashes", {}).get("pack.json") != digest(source / "pack.json")):
        raise LabError("Исходная проверенная выборка изменилась. Запуск запрещён.")
    pack, state, results = (read_json(source / name) for name in ("pack.json", "state.json", "results.json"))
    if not state.get("owner_id") or not state.get("chat_id"):
        raise LabError("Не найдена привязка владельца и тестовой группы.")
    if any(results.get(case["id"], {}).get("status") != "done" for case in pack.get("cases", [])):
        raise LabError("Второй раунд не завершён. Его результаты оставлены без изменений.")
    focused = select_focus_cases(pack)
    staging = Path(tempfile.mkdtemp(prefix=".focus-", dir=source.parent))
    try:
        atomic_json(staging / "state.json", state)
        atomic_json(staging / "pack.json", focused)
        atomic_json(staging / "results.json", {})
        manifest = {
            "round": 3, **revision_manifest(), "approved_upload_sha256": approved_sha,
            "selection": "targeted_regression_subset_not_full_replay",
            "selected_case_ids": list(FOCUS_CASE_IDS), "source_case_count": len(pack["cases"]),
            "source_pack_sha256": digest(source / "pack.json"),
            "parent_manifest_sha256": digest(source / "manifest.json"),
            "parent_results_sha256": digest(source / "results.json"),
            "input_sha256": digest(staging / "pack.json"),
        }
        atomic_json(staging / "manifest.json", manifest)
        staging.replace(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def focus_report(root: Path, pack: dict[str, Any], results: dict[str, Any]) -> bytes:
    page = render_report(pack, results).decode("utf-8")
    start = page.index("</h1>") + len("</h1>")
    end = page.index("</p>", start) + len("</p>")
    meta = read_json(root / "manifest.json")
    done = sum(r.get("status") == "done" for r in results.values())
    errors = sum(r.get("status") in {"error", "interrupted"} for r in results.values())
    intro = (f"<p><strong>Раунд 3 · {escape(str(meta['revision']))}</strong><br>"
             f"Проверка правок: {len(pack['cases'])} ситуаций из {meta['source_case_count']}. "
             f"Обработано: {done}; ошибок: {errors}. Это не повтор всех ситуаций. "
             "Раунды 1 и 2 сохранены без пересчёта. Память, реальные действия, публикации "
             "в исходную группу и динамические лимиты отключены. Качество оценивается отдельно.</p>"
             "<details><summary>Версия и происхождение выборки</summary><pre>"
             + escape(json.dumps(meta, ensure_ascii=False, indent=2)) + "</pre></details>")
    diagnostics = {cid: {k: r[k] for k in ("error_type", "error_code") if k in r}
                   for cid, r in results.items() if r.get("status") in {"error", "interrupted"}}
    if diagnostics:
        intro += "<pre>" + escape(json.dumps(diagnostics, ensure_ascii=False)) + "</pre>"
    return (page[:start] + intro + page[end:]).encode("utf-8")


class FocusTelegram(Telegram):
    def message(self, chat: int, text: str, buttons: bool = False) -> None:
        chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)]
        for index, chunk in enumerate(chunks):
            args = {"chat_id": chat, "text": chunk, "link_preview_options": {"is_disabled": True}}
            if buttons and index == len(chunks) - 1:
                args["reply_markup"] = BUTTONS
            self.call("sendMessage", **args)


class FocusLabBot(LabBot):
    def __init__(self, tg: Any, root: Path, pairing: str, approved_sha: str,
                 runner_factory: Any = make_cohost_runner):
        self.base_root = root
        active_root = prepare_focus(root, approved_sha)
        super().__init__(tg, active_root, pairing, approved_sha, runner_factory)

    def status(self) -> str:
        done = sum(r.get("status") == "done" for r in self.results.values())
        errors = sum(r.get("status") in {"error", "interrupted"} for r in self.results.values())
        return (f"Раунд 3 — проверка правок. Ситуаций: {len(self.pack['cases'])}. "
                f"Обработано: {done}. Ошибок: {errors}.\n"
                + ("Прогон идёт." if self.busy else "Для запуска: /lab_check")
                + "\nПервые два отчёта сохранены. Живое общение и реальные действия отключены.")

    def handle(self, update: dict[str, Any]) -> None:
        authorized = RoundLabBot._authorized(self, update)
        if not authorized:
            return
        msg, command, chat = authorized
        if update.get("callback_query"):
            self.tg.call("answerCallbackQuery", callback_query_id=update["callback_query"]["id"])
        try:
            if "document" in msg:
                self.tg.message(chat, "Файл уже загружен и проверен. Повторная загрузка не нужна. /lab_check", True)
            elif command in {"/lab_check", "/lab_run", "/lab_next", "/lab_resume"}:
                self.start_run(chat, only_one=command == "/lab_next", retry=command == "/lab_resume")
            elif command in {"/lab_before", "/lab_previous", "/lab_report"}:
                with self.lock:
                    if command == "/lab_report":
                        report = focus_report(self.root, self.pack, self.results)
                        name = "group_lab_round03.html"
                    else:
                        previous = "round01" if command == "/lab_before" else "round02"
                        source = self.base_root / "runs" / previous
                        report = round_report(source, read_json(source / "pack.json"), read_json(source / "results.json"))
                        name = f"group_lab_{previous}.html"
                self.tg.document(chat, name, report)
            elif command in {"/start", "/help", "/lab_help", "/lab_status", "/lab_round2", "/lab_round3"}:
                self.tg.message(chat, self.status(), True)
        except LabError as exc:
            self.tg.message(chat, str(exc), True)

    def _check_version(self) -> None:
        stored = read_json(self.root / "manifest.json")
        if not same_implementation(stored, revision_manifest()):
            raise LabError("Версия изменилась. Смешивание результатов и автоматический пересчёт запрещены.")
        if stored["input_sha256"] != digest(self.root / "pack.json"):
            raise LabError("Проверенная выборка изменилась. Запуск запрещён.")

    def start_run(self, chat: int, only_one: bool = False, *, retry: bool = False) -> None:
        with self.lock:
            if self.busy:
                raise LabError("Прогон уже идёт. Повторных вызовов нет.")
            self._check_version()
            if not retry and any(r.get("status") in {"error", "interrupted"} for r in self.results.values()):
                raise LabError("Есть незавершённый кейс. Для явного повтора ошибок: /lab_resume")
            pending = [c for c in self.pack["cases"] if self.results.get(c["id"], {}).get("status") != "done"]
            if not pending:
                self.tg.message(chat, "Все 12 ситуаций обработаны. Повторных вызовов нет. Нажми «Отчёт раунда 3».", True)
                return
            selected = pending[:1] if only_one else pending
            if retry:
                prepare_resume(self.root, self.results, revision_manifest())
            self.tg.message(chat, f"Проверяю {len(selected)} ситуаций в раунде 3. Старые ответы не пересчитываются.")
            self.busy = True
            try:
                threading.Thread(target=self.run_cases, args=(chat, selected, only_one), daemon=True).start()
            except Exception:
                self.busy = False
                raise

    def run_cases(self, chat: int, cases: list[dict[str, Any]], only_one: bool) -> None:
        try:
            self._check_version()
            runner, adapter = self.runner_factory()
            execution = {key: revision_manifest().get(key) for key in VERSION_KEYS}
            for case in cases:
                with self.lock:
                    self.results[case["id"]] = {"status": "running", "execution_version": execution}
                    atomic_json(self.root / "results.json", self.results)
                prior = adapter.failures
                try:
                    result = runner.evaluate_case(case)
                    if adapter.failures != prior:
                        raise LabError("API fallback is not a valid test result")
                    result.update(status="done", revision=REVISION, execution_version=execution)
                except Exception as exc:
                    code, message = safe_error(exc)
                    result = {"status": "error", "error_type": type(exc).__name__, "error_code": code,
                              "error": message, "revision": REVISION, "execution_version": execution}
                    LOG.warning("case_failed type=%s code=%s", type(exc).__name__, code)
                with self.lock:
                    self.results[case["id"]] = result
                    atomic_json(self.root / "results.json", self.results)
                if only_one:
                    self.tg.message(chat, f"Раунд 3 · {case['id']}\n" + (result.get("generated_text") or result.get("error") or "Промолчал."), True)
                if result["status"] == "error":
                    break
            with self.lock:
                report = focus_report(self.root, self.pack, self.results)
                done = sum(r.get("status") == "done" for r in self.results.values())
                errors = sum(r.get("status") in {"error", "interrupted"} for r in self.results.values())
            self.tg.document(chat, "group_lab_round03.html", report)
            label = "Проверка остановлена на ошибке." if errors else "Проверка правок завершена." if done == len(self.pack["cases"]) else "Выбранный кейс обработан."
            self.tg.message(chat, f"{label}\nОбработано: {done}/{len(self.pack['cases'])}. Ошибок: {errors}. Отчёт выше.", True)
            LOG.info("focus_complete round=3 completed=%s errors=%s", done, errors)
        except Exception as exc:
            LOG.error("focus_job_failed type=%s", type(exc).__name__)
            try:
                self.tg.message(chat, "Техническая ошибка. Уже полученные результаты сохранены.", True)
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
    tg = FocusTelegram(token)
    me = tg.call("getMe")
    if tg.call("getWebhookInfo").get("url"):
        raise SystemExit("Existing webhook; no takeover performed.")
    bot = FocusLabBot(tg, root, pairing, approved)
    bot.username = me["username"]
    LOG.info("focus_ready revision=%s cases=%s bound=%s previous_rounds_preserved=true automatic_replay=false",
             REVISION, len(bot.pack["cases"]), bool(bot.state.get("chat_id")))

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            ok = time.monotonic() - bot.last_poll < 120
            self.send_response((200 if ok else 503) if self.path == "/health" else 404)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "revision": REVISION, "round": 3}).encode())
        def log_message(self, *args: Any) -> None:
            pass
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    LOG.info("focus_polling_started")
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
