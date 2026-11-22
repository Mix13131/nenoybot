"""Explicit personal controls for the dedicated coach; commands never call AI."""
from __future__ import annotations

import re

from app_v2.domain.coach import ControlResult


_ALIASES = {
    "/start": "help", "/help": "help", "помощь": "help", "/settings": "status",
    "настройки": "status", "/status": "status", "/coach_status": "status", "статус": "status",
    "/privacy": "privacy", "приватность": "privacy",
    "/archive_on": "archive_on", "/archive on": "archive_on", "память вкл": "archive_on", "включи память": "archive_on",
    "/archive_off": "archive_off", "/archive off": "archive_off", "память выкл": "archive_off", "выключи память": "archive_off",
    "/history_on": "history_on", "/history on": "history_on", "анализ истории вкл": "history_on",
    "/history_off": "history_off", "/history off": "history_off", "анализ истории выкл": "history_off",
    "/auto_on": "auto_on", "/auto on": "auto_on", "авто вкл": "auto_on",
    "/auto_off": "auto_off", "/auto off": "auto_off", "авто выкл": "auto_off",
    "/direct_on": "direct_on", "/direct on": "direct_on", "ответы вкл": "direct_on",
    "/direct_off": "direct_off", "/direct off": "direct_off", "ответы выкл": "direct_off",
    "/track_start": "track_start", "/track start": "track_start", "трек начать": "track_start",
    "/track_pause": "track_pause", "/track pause": "track_pause", "трек пауза": "track_pause",
    "/track_delete": "track_delete", "/track delete": "track_delete", "трек удалить": "track_delete",
    "/stop": "stop", "стоп": "stop", "/forget": "forget", "забудь мои сообщения": "forget",
}


def _command_text(text: str | None, *, bot_username=None, aliases=()) -> str | None:
    if not isinstance(text,str):
        return None
    normalized = " ".join(text.strip().lower().split())
    username = (bot_username or "").lstrip("@").lower()
    if username and normalized.startswith("@"+username+" "):
        normalized=normalized[len(username)+2:]
    for alias in sorted(aliases,key=len,reverse=True):
        match=re.match(r"^"+re.escape(alias.lower().strip())+r"[\s,:!—-]+",normalized)
        if match:
            normalized=normalized[match.end():]
            break
    if normalized.startswith("/"):
        first,separator,rest=normalized.partition(" ")
        command,at,target=first.partition("@")
        if at:
            if not username or target!=username:
                return None
            normalized=command+(separator+rest if separator else "")
    return normalized


def parse_control(text: str | None, *, bot_username: str | None = None, aliases=()) -> str | None:
    return _ALIASES.get(_command_text(text,bot_username=bot_username,aliases=aliases))


class CoachControls:
    def __init__(self, repo, *, archive_policy: str, bot_username: str | None = None, aliases: tuple[str,...] = ()):
        if archive_policy != "until_author_deletes":
            raise ValueError("An explicit supported archive retention policy is required")
        self.repo,self.archive_policy,self.bot_username=repo,archive_policy,bot_username
        self.aliases=aliases

    def disclosure_text(self) -> str:
        hours=self.repo.transient_ttl_seconds/3600
        return (
            "Прежде чем включать возможности:\n"
            "• Память сохраняет только ваши новые сообщения и правки в этой теме этой группы. "
            "Старую историю и личные чаты не импортирую; медиа сохраняю только как подпись и ссылку, без содержимого файла.\n"
            "• Архив хранится до вашего удаления или отключения памяти. Разрешение на хранение не включает анализ и ответы.\n"
            "• Анализ истории — отдельное разрешение. Авто — разрешение самостоятельно анализировать ваши записи "
            "и писать в группу. Ответы по запросу можно включить без архива и анализа истории.\n"
            "• Любой ответ здесь виден участникам этой группы. Для личного обзора или трека потребуется дополнительное явное подтверждение публикации.\n"
            f"• Для текста временных заданий и очереди ответов задан срок {hours:g} ч; после обработки/отправки текст очищается. "
            "При остановке сервиса очистка продолжится после запуска.\n"
            "• /forget удаляет ваш архив и производные записи этого бота в этой теме. "
            "Это не удаляет сообщения Telegram и прежние резервные копии; восстановление старой копии требует повторного применения удалений.\n\n"
            "Выберите отдельно: /archive_on — память; /history_on — анализ истории; "
            "/auto_on — самостоятельные комментарии; /direct_on — ответы по запросу. "
            "Выключение: соответствующая команда с _off. /status — мои настройки. "
            "Команду включения отправьте после этого уведомления ещё раз.\n"
            "/ask текст — задать вопрос; /review 7 — обзор; /track_start, /track_pause, /track_delete — управление треком. "
            "Отметки эксперимента: /accept ID, /decline ID, /tried ID, /not_tried ID, /close ID. "
            "Согласие попробовать и сообщение о попытке сохраняются как разные события."
        )

    def _status(self, permissions) -> str:
        yes=lambda value:"вкл" if value else "выкл"
        return (f"Ваши настройки этой темы:\nПамять: {yes(permissions.retain_source)}. "
                f"Анализ истории: {yes(permissions.historical_inference)}.\n"
                f"Автоанализ: {yes(permissions.auto_report_analysis)}. "
                f"Самостоятельные ответы: {yes(permissions.unsolicited_group_reply)}.\n"
                f"Ответы по запросу: {yes(permissions.direct_request_group_reply)}. "
                f"Трек: { {'off':'выключен','active':'активен','paused':'на паузе'}[permissions.track_status] }.\n"
                "Авто работает только при включённых памяти, анализе истории и обоих разрешениях авто. "
                "/privacy — что хранится; /stop — остановить самостоятельные ответы; /forget — удалить мои данные этой темы.")

    def handle(self, normalized) -> ControlResult:
        message=self.repo.authenticated_message(normalized)
        if message is None or message.is_edited or message.contains_external_content:
            return ControlResult(False)
        normalized_text=_command_text(message.available_text,bot_username=self.bot_username,aliases=self.aliases)
        action=_ALIASES.get(normalized_text)
        review=re.fullmatch(r"/(track|review)(?: (\d{1,4}))?(?: (confirm))?",normalized_text or "")
        ask=re.fullmatch(r"/ask(?: (.+))?",normalized_text or "")
        feedback=re.fullmatch(r"/(?:experiment )?(accept|decline|tried|not_tried|close) ([a-f0-9]{32})",normalized_text or "")
        if review:
            action="track_request" if review[1]=="track" else "review_request"
        if ask:action="ask"
        if feedback:action="experiment_feedback"
        if action is None:
            return ControlResult(False)
        with self.repo.author_lock(message.author_user_id,require_enabled=False):
            if not self.repo.claim_control(message):
                return ControlResult(True)
            permission=self.repo.permissions(message.author_user_id)
            if action in {"privacy","help"}:
                return ControlResult(True,self.disclosure_text(),disclosure=True)
            if action=="status":
                return ControlResult(True,self._status(permission))
            if action in {"ask","experiment_feedback","track_request","review_request"} and not self.repo.scope_enabled():
                return ControlResult(True,"Тема временно отключена владельцем. Доступны настройки, отзыв разрешений и удаление своих данных.")
            if action=="ask":
                if not ask[1]:
                    return ControlResult(True,"После /ask напишите свой вопрос.")
                if not permission.direct_request_group_reply:
                    if not permission.disclosure_delivered:
                        return ControlResult(True,self.disclosure_text(),disclosure=True)
                    return ControlResult(True,"Ответы по вашему запросу выключены. /direct_on включает их без архива и анализа истории.")
                return ControlResult(True,job_mode="direct")
            if action=="experiment_feedback":
                if not (permission.retain_source and permission.historical_inference):
                    return ControlResult(True,"Чтобы сохранять ваши отметки эксперимента, нужны отдельно включённые память и анализ истории.")
                statuses={"accept":"accepted","decline":"declined","tried":"tried_self_reported",
                          "not_tried":"not_tried_self_reported","close":"retired"}
                updated=self.repo.record_experiment_feedback(message,experiment_id=feedback[2],status=statuses[feedback[1]])
                if not updated:
                    return ControlResult(True,"Эксперимент не найден среди ваших актуальных записей или его источники недоступны.")
                labels={"accept":"согласен попробовать","decline":"отказываюсь","tried":"сообщил, что попробовал",
                        "not_tried":"сообщил, что не пробовал","close":"закрыл эксперимент"}
                return ControlResult(True,"Сохранена ваша отметка: «"+labels[feedback[1]]+"». Это ваша самооценка, не независимая проверка результата.")
            enabling=action.endswith("_on") or action=="track_start"
            if enabling and not self.repo.scope_enabled():
                return ControlResult(True,"Тема временно отключена владельцем. Вы можете посмотреть настройки, отозвать разрешения или удалить свои данные.")
            if enabling and not permission.disclosure_delivered:
                return ControlResult(True,self.disclosure_text(),disclosure=True)
            changes={}
            if action in {"archive_on","archive_off","history_on","history_off","direct_on","direct_off"}:
                flag={"archive":"retain_source","history":"historical_inference","direct":"direct_request_group_reply"}[action.split("_")[0]]
                changes={flag:action.endswith("_on")}
            elif action in {"auto_on","auto_off","stop"}:
                if action=="auto_on" and not (permission.retain_source and permission.historical_inference):
                    return ControlResult(True,"Для авто сначала отдельно включите /archive_on и /history_on. Ваши разрешения не изменены.")
                changes={"auto_report_analysis":action=="auto_on","unsolicited_group_reply":action=="auto_on"}
            elif action=="track_start":
                if not (permission.retain_source and permission.historical_inference):
                    return ControlResult(True,"Для персонального трека сначала отдельно включите /archive_on и /history_on.")
                self.repo.set_track_status(message.author_user_id,"active")
                return ControlResult(True,"Персональный трек включён. Для обзора за 7 дней запросите /track 7; "
                                     "для публикации обзора в этой группе потребуется подтверждение. /track_pause — пауза.")
            elif action=="track_pause":
                self.repo.set_track_status(message.author_user_id,"paused")
                return ControlResult(True,"Трек на паузе. Память и ответы по запросу сохраняют свои отдельные настройки.")
            elif action in {"track_request","review_request"}:
                days=int(review[2] or 7)
                if not 1<=days<=3660:
                    return ControlResult(True,"Укажите диапазон от 1 до 3660 дней.")
                if not (permission.retain_source and permission.historical_inference and permission.direct_request_group_reply):
                    return ControlResult(True,"Для обзора отдельно включите /archive_on, /history_on и /direct_on. Это личные разрешения только на ваши источники.")
                if action=="track_request" and permission.track_status!="active":
                    return ControlResult(True,"Сначала включите персональный трек командой /track_start.")
                if not review[3]:
                    return ControlResult(True,f"Обзор за {days} дней будет опубликован здесь и виден всем участникам группы. "
                                         f"Если это подходит, отправьте /{review[1]} {days} confirm.",window_days=days)
                return ControlResult(True,job_mode="track" if action=="track_request" else "weekly_review",
                                     window_days=days,public_confirmed=True)
            elif action=="track_delete":
                count=self.repo.delete_track(message)
                return ControlResult(True,f"Удалены ваши производные записи и эксперименты этой темы: {count}; трек выключен. "
                                     "Исходный архив и отдельные разрешения сохранены. Для удаления своих источников используйте /forget.")
            elif action=="forget":
                receipt=self.repo.forget(message)
                return ControlResult(True,f"Удалены ваши источники этой темы: сообщений {receipt.messages}, версий {receipt.revisions}; "
                                     "производные записи и ожидающие ответы очищены, трек выключен. "
                                     "Удаление не распространяется на сообщения Telegram и старые резервные копии. "
                                     "Настройка хранения новых сообщений не изменена; /archive_off отключает память.")
            if any(getattr(permission,key)!=value for key,value in changes.items()):
                self.repo.change_permissions(message,**changes)
            if action=="stop":
                self.repo.set_track_status(message.author_user_id,"paused")
            updated=self.repo.permissions(message.author_user_id)
            prefix=""
            if action=="archive_off":
                prefix="Память выключена; тексты её живого архива очищены. Старые резервные копии и Telegram не удалены.\n"
            elif action=="stop":
                prefix="Самостоятельные ответы остановлены. Разрешение отвечать по вашему запросу не изменено.\n"
            return ControlResult(True,prefix+self._status(updated))
