"""Single-group, volume-backed control plane for reviewed Group Lab cases.

This entry point does not import the production runtime, repositories, worker,
reminders or Telegram outbox. Telegram is only the lab operator's interface.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_UPLOAD = 2 * 1024 * 1024
MAX_CASES = 40
LOG = logging.getLogger("group_lab")
BUTTONS = {"inline_keyboard": [
    [{"text": "Следующий кейс", "callback_data": "lab_next"},
     {"text": "Прогнать выборку", "callback_data": "lab_run"}],
    [{"text": "Статус", "callback_data": "lab_status"},
     {"text": "Скачать отчёт", "callback_data": "lab_report"}],
]}
HELP = (
    "Это изолированная лаборатория, а не подключение к настоящей группе.\n"
    "Прикрепи review_pack_checked.json. Затем нажми «Следующий кейс» "
    "или «Прогнать выборку».\n"
    "В отчёте будут реальные решения и ответы модели. "
    "История не загружается в память живого НеНоя."
)


class LabError(RuntimeError):
    """Operator-safe error; never include remote bodies, tokens or file contents."""


def atomic_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def checked_pack(data: bytes, approved_sha: str) -> dict[str, Any]:
    """Content allowlist: unreviewed/raw exports are never sent to a model."""
    if len(data) > MAX_UPLOAD:
        raise LabError("Файл слишком большой. Нужна проверенная выборка, не полный архив.")
    actual = hashlib.sha256(data).hexdigest()
    if not re.fullmatch(r"[a-f0-9]{64}", approved_sha) or not hmac.compare_digest(actual, approved_sha):
        raise LabError("Этот файл не входит в проверенную выборку. Пришли review_pack_checked.json без изменений.")
    try:
        pack = json.loads(data)
        cases = pack["cases"]
        if pack.get("lab_replay_version") != 1 or not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
            raise ValueError("shape")
        result = []
        ids: set[str] = set()
        for case in cases:
            cid = case["id"]
            if not isinstance(cid, str) or not re.fullmatch(r"c\d+", cid) or cid in ids:
                raise ValueError("case id")
            ids.add(cid)
            current = case["current"]
            context = case["context"]
            if not isinstance(context, list) or len(context) > 40:
                raise ValueError("context")
            cleaned = []
            for row in [*context, current]:
                if not isinstance(row, dict) or not isinstance(row.get("text"), str) or len(row["text"]) > 20000:
                    raise ValueError("message")
                if row.get("kind") not in {"message", "service"}:
                    raise ValueError("kind")
                datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00"))
                cleaned.append({k: row[k] for k in (
                    "id", "kind", "actor", "occurred_at", "text", "reply_to", "media"
                ) if k in row})
            current_time = datetime.fromisoformat(current["occurred_at"].replace("Z", "+00:00"))
            for row in context:
                if datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00")) > current_time:
                    raise ValueError("future context")
            result.append({"id": cid, "episode_id": str(case.get("episode_id", "lab")),
                           "current": cleaned[-1], "context": cleaned[:-1]})
        return {"lab_replay_version": 1, "approved_sha256": actual, "cases": result}
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise LabError("Неверный формат или временной порядок проверенной выборки.") from exc


class Telegram:
    def __init__(self, token: str):
        import httpx
        self.client = httpx.Client(timeout=45, follow_redirects=False)
        self.base = "https://api.telegram.org/bot" + token + "/"
        self.files_base = "https://api.telegram.org/file/bot" + token + "/"

    def call(self, method: str, **params: Any) -> Any:
        try:
            r = self.client.post(self.base + method, json=params)
            body = r.json()
        except Exception as exc:
            raise LabError("Telegram временно недоступен.") from exc
        if not r.is_success or not body.get("ok"):
            code = int(body.get("error_code", r.status_code))
            raise LabError(f"Telegram: ошибка {code} при {method}.")
        return body["result"]

    def message(self, chat: int, text: str, buttons: bool = False) -> None:
        for start in range(0, len(text), 3500):
            params: dict[str, Any] = {"chat_id": chat, "text": text[start:start + 3500],
                                      "link_preview_options": {"is_disabled": True}}
            if buttons and start + 3500 >= len(text):
                params["reply_markup"] = BUTTONS
            self.call("sendMessage", **params)

    def download(self, document: dict[str, Any]) -> bytes:
        if not str(document.get("file_name", "")).lower().endswith(".json"):
            raise LabError("Нужен JSON-файл review_pack_checked.json, не ZIP и не исходный архив.")
        if int(document.get("file_size", MAX_UPLOAD + 1)) > MAX_UPLOAD:
            raise LabError("Файл слишком большой: нужна небольшая проверенная выборка.")
        info = self.call("getFile", file_id=document["file_id"])
        path = info.get("file_path", "")
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path or path.startswith("/"):
            raise LabError("Telegram вернул недопустимый путь файла.")
        chunks = bytearray()
        try:
            with self.client.stream("GET", self.files_base + path) as r:
                r.raise_for_status()
                for chunk in r.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > MAX_UPLOAD:
                        raise LabError("Файл превышает лимит загрузки.")
        except LabError:
            raise
        except Exception as exc:
            raise LabError("Не удалось получить файл из Telegram.") from exc
        return bytes(chunks)

    def document(self, chat: int, name: str, data: bytes) -> None:
        try:
            r = self.client.post(self.base + "sendDocument", data={"chat_id": str(chat)},
                                 files={"document": (name, data, "text/html; charset=utf-8")})
            ok = r.is_success and r.json().get("ok")
        except Exception as exc:
            raise LabError("Не удалось отправить отчёт.") from exc
        if not ok:
            raise LabError("Telegram не принял отчёт.")


class ObservedAdapter:
    """Notice model failures even when a production component uses a fallback."""
    def __init__(self, adapter: Any):
        self.adapter = adapter
        self.failures = 0
        self.calls = 0

    def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        try:
            return getattr(self.adapter, method)(*args, **kwargs)
        except Exception:
            self.failures += 1
            raise

    def generate_json(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("generate_json", *args, **kwargs)

    def generate_text(self, *args: Any, **kwargs: Any) -> Any:
        return self._call("generate_text", *args, **kwargs)


def make_runner() -> tuple[Any, ObservedAdapter]:
    from app_v2.adapters.openai_adapter import OpenAIAdapter
    from app_v2.config import load_config
    from app_v2.labs.behavior_replay import BehaviorReplayOptions, GroupBehaviorReplayRunner
    from app_v2.services.personality_engine import PersonalityEngine
    from app_v2.services.response_generator import ResponseGenerator
    from app_v2.services.scene_analyzer import SceneAnalyzer

    # Explicit minimal environment: production DB/token/webhook cannot reach Brain.
    allowed = {k: v for k, v in os.environ.items()
               if k.startswith("NENOY_V2_MODEL_") or k in {
                   "NENOY_V2_OPENAI_API_KEY", "NENOY_V2_OPENAI_TIMEOUT_SECONDS"}}
    allowed["NENOY_V2_ENV"] = "development"
    config = load_config(allowed)
    adapter = ObservedAdapter(OpenAIAdapter(config))
    runner = GroupBehaviorReplayRunner(
        scene_analyzer=SceneAnalyzer(adapter), personality_engine=PersonalityEngine(),
        response_generator=ResponseGenerator(adapter=adapter),
        options=BehaviorReplayOptions(preset_name="education_community_v1"),
    )
    return runner, adapter


def render_report(pack: dict[str, Any], results: dict[str, Any]) -> bytes:
    parts = ["<!doctype html><html lang='ru'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>",
             "<title>Group Lab — результаты</title><style>body{max-width:900px;margin:32px auto;padding:16px;font:16px/1.5 sans-serif}article{border-top:1px solid #bbb;padding:20px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}</style>",
             "<h1>Group Lab — реальные результаты модели</h1><p>education_community_v1. Изолированные исторические ситуации. LONG memory, реальные действия и динамические лимиты инициативы не воспроизводятся. Это не автоматическая оценка качества.</p>"]
    for case in pack.get("cases", []):
        result = results.get(case["id"], {})
        decision = result.get("decision", {})
        parts.append("<article><h2>" + escape(case["id"]) + "</h2>")
        parts.append("<details><summary>Доступный исторический контекст</summary><pre>" + escape(
            "\n\n".join(f"{m.get('occurred_at')} · {m.get('actor')}\n{m['text']}" for m in case["context"])) + "</pre></details>")
        parts.append("<h3>Сообщение</h3><pre>" + escape(case["current"]["text"]) + "</pre>")
        parts.append("<p>Статус: " + escape(str(result.get("status", "not_run"))) + "; решение: " + escape(str(decision.get("primary_action", "—"))) + "</p>")
        parts.append("<pre>" + escape(str(result.get("generated_text") or result.get("error") or "Ответ не сгенерирован.")) + "</pre>")
        parts.append("<p>Причины: " + escape(", ".join(decision.get("reason_codes", []))) + "</p></article>")
    return ("\n".join(parts) + "</html>").encode("utf-8")


class LabBot:
    def __init__(self, tg: Any, root: Path, pairing: str, approved_sha: str, runner_factory: Any = make_runner):
        self.tg, self.root, self.pairing = tg, root, pairing
        self.approved_sha, self.runner_factory = approved_sha, runner_factory
        self.lock = threading.RLock()
        self.busy = False
        self.username = ""
        self.last_poll = time.monotonic()
        self.state = json.loads((root / "state.json").read_text()) if (root / "state.json").exists() else {"offset": 0}
        self.pack = json.loads((root / "pack.json").read_text()) if (root / "pack.json").exists() else {"cases": []}
        self.results = json.loads((root / "results.json").read_text()) if (root / "results.json").exists() else {}
        for result in self.results.values():
            if result.get("status") == "running":
                result.update(status="interrupted", error="Сервис перезапущен во время вызова. Автоповтор отключён.")
        atomic_json(self.root / "results.json", self.results)

    def status(self) -> str:
        done = sum(r.get("status") == "done" for r in self.results.values())
        failed = sum(r.get("status") in {"error", "interrupted"} for r in self.results.values())
        return (f"Group Lab подключён. Профиль: education_community_v1.\n"
                f"Ситуаций: {len(self.pack['cases'])}. Обработано: {done}. Ошибок: {failed}.\n"
                f"{'Прогон идёт.' if self.busy else 'Готов к запуску.'}\n"
                "Это replay: память живых групп, реальные напоминания и публикации в исходный чат отключены.")

    def handle(self, update: dict[str, Any]) -> None:
        cb = update.get("callback_query")
        msg = cb.get("message", {}) if cb else update.get("message", {})
        actor = cb.get("from", {}) if cb else msg.get("from", {})
        chat = msg.get("chat", {})
        if not actor or actor.get("is_bot") or msg.get("sender_chat") or chat.get("type") not in {"group", "supergroup"}:
            return
        chat_id, user_id = chat["id"], actor["id"]
        text = "/" + cb.get("data", "") if cb else str(msg.get("text", ""))
        command, _, argument = text.strip().partition(" ")
        command = command.split("@", 1)[0].lower()
        if not self.state.get("chat_id"):
            if command != "/lab_bind" or not hmac.compare_digest(argument.strip(), self.pairing):
                return
            admins = self.tg.call("getChatAdministrators", chat_id=chat_id)
            if not any(a.get("user", {}).get("id") == user_id for a in admins):
                return
            self.state.update(chat_id=chat_id, owner_id=user_id)
            atomic_json(self.root / "state.json", self.state)
            self.tg.message(chat_id, "Песочница привязана только к этой группе.\n\n" + HELP)
            LOG.info("group_bound=true")
            return
        if chat_id != self.state["chat_id"] or user_id != self.state["owner_id"]:
            return
        if cb:
            self.tg.call("answerCallbackQuery", callback_query_id=cb["id"])
        try:
            if "document" in msg:
                with self.lock:
                    if self.busy:
                        raise LabError("Сейчас идёт прогон. Дождись отчёта перед загрузкой.")
                    pack = checked_pack(self.tg.download(msg["document"]), self.approved_sha)
                    atomic_json(self.root / "pack.json", pack)
                    self.pack = pack
                self.tg.message(chat_id, f"Проверенная выборка принята: {len(pack['cases'])} ситуаций.\nНажми «Прогнать выборку». Повторная загрузка не стирает результаты.", True)
                LOG.info("reviewed_pack_loaded cases=%s", len(pack["cases"]))
            elif command in {"/start", "/lab_help", "/help", "/lab_bind"}:
                self.tg.message(chat_id, HELP, bool(self.pack["cases"]))
            elif command == "/lab_status":
                self.tg.message(chat_id, self.status(), bool(self.pack["cases"]))
            elif command in {"/lab_run", "/lab_next"}:
                self.start_run(chat_id, only_one=command == "/lab_next")
            elif command == "/lab_report":
                with self.lock:
                    report = render_report(self.pack, self.results)
                self.tg.document(chat_id, "group_lab_results.html", report)
        except LabError as exc:
            self.tg.message(chat_id, str(exc))

    def start_run(self, chat: int, only_one: bool) -> None:
        with self.lock:
            if self.busy:
                raise LabError("Прогон уже идёт. Повторно модель не запускаю.")
            if not self.pack["cases"]:
                raise LabError("Сначала прикрепи review_pack_checked.json.")
            pending = [c for c in self.pack["cases"] if c["id"] not in self.results]
            if not pending:
                self.tg.message(chat, "Все ситуации уже обработаны. Нажми «Скачать отчёт».", True)
                return
            selected = pending[:1] if only_one else pending
            self.tg.message(chat, f"Запускаю {len(selected)} ситуаций через модель. Ответы не попадут в исходную группу.")
            self.busy = True
        threading.Thread(target=self.run_cases, args=(chat, selected, only_one), daemon=True).start()

    def run_cases(self, chat: int, cases: list[dict[str, Any]], only_one: bool) -> None:
        try:
            runner, adapter = self.runner_factory()
            for case in cases:
                with self.lock:
                    self.results[case["id"]] = {"status": "running"}
                    atomic_json(self.root / "results.json", self.results)
                prior_failures = adapter.failures
                try:
                    result = runner.evaluate_case(case)
                    if adapter.failures != prior_failures:
                        raise LabError("Ошибка API модели: резервный ответ не засчитывается как результат теста.")
                    result["status"] = "done"
                except Exception as exc:
                    result = {"status": "error", "error": "Вызов модели не завершён; это не решение промолчать.",
                              "error_type": type(exc).__name__}
                    LOG.warning("case_failed type=%s", type(exc).__name__)
                with self.lock:
                    self.results[case["id"]] = result
                    atomic_json(self.root / "results.json", self.results)
                if only_one:
                    action = result.get("decision", {}).get("primary_action", "ошибка")
                    response = result.get("generated_text") or result.get("error") or "Промолчал."
                    self.tg.message(chat, f"{case['id']} · {action}\n\nСообщение: {case['current']['text'][:700]}\n\n{response[:2500]}", True)
                if result["status"] == "error":
                    break  # A key/quota/model error must not incur 28 repeated calls.
            with self.lock:
                report = render_report(self.pack, self.results)
            self.tg.document(chat, "group_lab_results.html", report)
            self.tg.message(chat, "Прогон завершён. Отчёт выше.\n" + self.status().replace("Прогон идёт.", ""), True)
            LOG.info("replay_complete completed=%s", sum(r.get("status") == "done" for r in self.results.values()))
        except Exception as exc:
            LOG.error("lab_job_failed type=%s", type(exc).__name__)
            try:
                self.tg.message(chat, "Прогон остановлен из-за технической ошибки. Уже полученные результаты сохранены.")
            except LabError:
                pass
        finally:
            with self.lock:
                self.busy = False


def main() -> None:
    import fcntl
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    for name in ("httpx", "httpcore", "openai"):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    os.umask(0o077)
    token = os.environ.get("NENOY_V2_TELEGRAM_BOT_TOKEN", "").strip()
    pairing = os.environ.get("NENOY_LAB_PAIRING_CODE", "").strip()
    approved = os.environ.get("NENOY_LAB_APPROVED_PACK_SHA256", "").strip()
    if not token or not os.environ.get("NENOY_V2_OPENAI_API_KEY") or len(pairing) < 16 or not re.fullmatch(r"[a-f0-9]{64}", approved):
        raise SystemExit("Group Lab requires bot/API credentials, pairing code and approved pack digest.")
    root = Path(os.environ.get("NENOY_LAB_DATA_DIR", "/data"))
    root.mkdir(parents=True, exist_ok=True)
    lock_file = (root / "process.lock").open("a")
    fcntl.flock(lock_file, fcntl.LOCK_EX)  # One poller per persistent volume.
    tg = Telegram(token)
    me = tg.call("getMe")
    if tg.call("getWebhookInfo").get("url"):
        raise SystemExit("This bot already has a webhook. Refusing to replace or delete it.")
    bot = LabBot(tg, root, pairing, approved)
    bot.username = me["username"]
    LOG.info("telegram_ready username=@%s can_read_all=%s", me["username"], bool(me.get("can_read_all_group_messages")))
    # Synthetic API probe; no archive text, group ids, tokens or raw exceptions in logs.
    try:
        runner, adapter = make_runner()
        probe = runner.evaluate_case({"id": "c0", "episode_id": "probe", "context": [], "current": {
            "id": "m0", "actor": "member_001", "occurred_at": "2026-01-01T00:00:00Z",
            "text": "НеНой, ответь одним словом: готов", "kind": "message", "reply_to": None}})
        LOG.info("model_probe=%s calls=%s action=%s", "ok" if not adapter.failures else "failed",
                 adapter.calls, probe.get("decision", {}).get("primary_action"))
    except Exception as exc:
        LOG.warning("model_probe=failed type=%s", type(exc).__name__)

    class Health(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            healthy = time.monotonic() - bot.last_poll < 120
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": healthy, "bound": bool(bot.state.get("chat_id")),
                                        "cases": len(bot.pack["cases"]), "busy": bot.busy}).encode())

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    LOG.info("lab_polling_started")
    while True:
        try:
            updates = tg.call("getUpdates", offset=bot.state.get("offset", 0), timeout=25,
                              allowed_updates=["message", "callback_query"])
            bot.last_poll = time.monotonic()
            for update in updates:
                # At-most-once operator commands: persist before model/network effects.
                with bot.lock:
                    bot.state["offset"] = update["update_id"] + 1
                    atomic_json(root / "state.json", bot.state)
                bot.handle(update)
        except Exception as exc:
            LOG.warning("poll_error type=%s", type(exc).__name__)
            time.sleep(5)


if __name__ == "__main__":
    main()
