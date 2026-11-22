"""Natural self-controls for the configured group; deterministic controls use no AI."""
from __future__ import annotations

import re

from app_v2.domain.coach import ControlResult, GRANT_FIELDS


_ALIASES = {
    "/start":"help", "/help":"help", "помощь":"help", "/settings":"status",
    "настройки":"status", "/status":"status", "/coach_status":"status", "статус":"status",
    "/privacy":"privacy", "приватность":"privacy", "что ты хранишь":"privacy",
    "/archive_on":"archive_on", "/archive on":"archive_on", "память вкл":"archive_on", "включи память":"archive_on",
    "/archive_off":"archive_off", "/archive off":"archive_off", "память выкл":"archive_off", "выключи память":"archive_off",
    "не запоминай":"storage_off", "не запоминай мои сообщения":"storage_off", "не храни мои сообщения":"storage_off",
    "/history_on":"history_on", "/history on":"history_on", "анализ истории вкл":"history_on", "используй историю":"history_on",
    "/history_off":"history_off", "/history off":"history_off", "анализ истории выкл":"history_off", "не используй историю":"history_off",
    "не анализируй историю":"history_off",
    "/auto_on":"auto_on", "/auto on":"auto_on", "авто вкл":"auto_on",
    "/auto_off":"auto_off", "/auto off":"auto_off", "авто выкл":"auto_off",
    "/direct_on":"direct_on", "/direct on":"direct_on", "ответы вкл":"direct_on", "отвечай на мои вопросы":"direct_on",
    "/direct_off":"direct_off", "/direct off":"direct_off", "ответы выкл":"direct_off", "не отвечай мне":"direct_off",
    "/track_start":"track_start", "/track start":"track_start", "трек начать":"track_start", "включи мой трек":"track_start",
    "/track_pause":"track_pause", "/track pause":"track_pause", "трек пауза":"track_pause", "приостанови мой трек":"track_pause",
    "/track_delete":"track_delete", "/track delete":"track_delete", "трек удалить":"track_delete", "удали мой трек":"track_delete",
    "/stop":"stop", "стоп":"stop", "пока не комментируй":"stop", "не комментируй мои сообщения":"stop", "пока помолчи":"stop",
    "/resume":"resume", "продолжай":"resume", "снова подключайся":"resume", "можешь снова комментировать":"resume",
    "снова запоминай и разбирай":"full_resume", "снова запоминай и анализируй":"full_resume",
    "/forget":"forget", "забудь мои сообщения":"forget", "забудь все мои сообщения":"forget", "удали мои данные":"forget",
}
_FEEDBACK = {
    "попробую":"accepted", "давай попробую":"accepted", "согласен попробовать":"accepted",
    "не буду":"declined", "не хочу этот вариант":"declined", "отказываюсь":"declined",
    "попробовал":"tried_self_reported", "попробовала":"tried_self_reported", "сделал":"tried_self_reported", "сделала":"tried_self_reported",
    "не пробовал":"not_tried_self_reported", "не пробовала":"not_tried_self_reported", "не сделал":"not_tried_self_reported",
    "закрой этот эксперимент":"retired",
}


def _command_text(text: str | None, *, bot_username=None, aliases=()) -> str | None:
    if not isinstance(text,str):
        return None
    normalized=" ".join(text.strip().casefold().split())
    username=(bot_username or "").lstrip("@").casefold()
    names=tuple(aliases)+(("@"+username,) if username else ())
    for alias in sorted(names,key=len,reverse=True):
        match=re.match(r"^"+re.escape(alias.casefold().strip())+r"[\s,:!—-]+",normalized)
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
    return normalized.rstrip(".!?…")


def _has_address(text: str | None, *, bot_username=None, aliases=()) -> bool:
    if not isinstance(text,str):
        return False
    names=tuple(aliases)+(("@"+bot_username.lstrip("@"),) if bot_username else ())
    return any(re.match(r"^\s*"+re.escape(name)+r"(?=$|[\s,:!?.—-])",text,re.I) for name in names)


def parse_control(text: str | None, *, bot_username: str | None = None, aliases=()) -> str | None:
    """Parse a phrase; handle() independently requires natural speech to address the bot."""
    return _ALIASES.get(_command_text(text,bot_username=bot_username,aliases=aliases))


class CoachControls:
    def __init__(self, repo, *, archive_policy: str, bot_username: str | None = None, aliases: tuple[str,...] = ()):
        if archive_policy!="until_author_deletes":
            raise ValueError("An explicit supported archive retention policy is required")
        self.repo,self.archive_policy,self.bot_username=repo,archive_policy,bot_username
        self.aliases=aliases

    def disclosure_text(self) -> str:
        hours=self.repo.transient_ttl_seconds/3600
        return (
            "В этой настроенной группе память, разбор, самостоятельные ответы и персональный трек включены по умолчанию. "
            "Ваши отдельные остановки имеют приоритет.\n\n"
            "Сохраняю только ваши новые сообщения и правки в этой теме этой группы. Старую историю и личные чаты не импортирую. "
            "У медиа сохраняются подпись и ссылка, без содержимого файла. Архив хранится до вашего удаления или отключения памяти. "
            "Мои доставленные ответы тоже сохраняются для продолжения разговора, пока включена работа с историей. "
            "Они очищаются при удалении трека или памяти, отключении истории либо исправлении сообщения, на котором основан ответ. "
            "Любой ответ здесь виден участникам этой группы.\n\n"
            "Можно обратиться ко мне обычными словами: «пока не комментируй», «не запоминай мои сообщения», "
            "«забудь мои сообщения», «продолжай», «снова запоминай и разбирай». Ответы по запросу могут работать без архива и анализа истории. "
            f"Текст временных заданий и очереди ответов хранится до {hours:g} ч и очищается после обработки; "
            "при остановке сервиса очистка продолжится после запуска. Служебные отметки без текста не дают повторно обработать старую доставку. "
            "Удаление не удаляет сообщения Telegram и прежние резервные копии; восстановление старой копии требует повторного применения удалений."
        )

    def _status(self, permission) -> str:
        yes=lambda value:"включена" if value else "выключена"
        return (f"Память {yes(permission.retain_source)}, работа с историей {yes(permission.historical_inference)}. "
                f"Самостоятельные ответы {'включены' if permission.automatic_allowed else 'остановлены'}. "
                f"Ответы на ваши вопросы {'включены' if permission.direct_request_group_reply else 'выключены'}. "
                f"Трек { {'off':'выключен','active':'активен','paused':'на паузе'}[permission.track_status] }. "
                "Менять это можно обычными словами, обращаясь ко мне.")

    def handle(self, normalized, *, addressed: bool = False) -> ControlResult:
        message=self.repo.authenticated_message(normalized)
        if message is None or message.is_edited or message.contains_external_content:
            return ControlResult(False)
        text=_command_text(message.available_text,bot_username=self.bot_username,aliases=self.aliases)
        if not text:
            return ControlResult(False)
        slash=text.startswith("/")
        natural=addressed or _has_address(message.available_text,bot_username=self.bot_username,aliases=self.aliases)
        if not slash and not natural:
            return ControlResult(False)
        action=_ALIASES.get(text)
        review=re.fullmatch(r"/(track|review)(?: (\d{1,4}))?(?: confirm)?",text)
        natural_review=re.fullmatch(r"(?:(?:покажи|собери|сделай|подведи) )?(?:мой |мои )?(трек|обзор|итоги)"
            r"(?: за (?:(\d{1,4}) (?:день|дня|дней)|(неделю|месяц)))?",text) if natural else None
        days=int(review[2] or 7) if review else 7
        if review:
            action="track_request" if review[1]=="track" else "review_request"
        if natural_review:
            action="track_request" if natural_review[1]=="трек" else "review_request"
            days=int(natural_review[2]) if natural_review[2] else 30 if natural_review[3]=="месяц" else 7
        ask=re.fullmatch(r"/ask(?: (.+))?",text)
        feedback=re.fullmatch(r"/(?:experiment )?(accept|decline|tried|not_tried|close) ([a-f0-9]{32})",text)
        feedback_status=_FEEDBACK.get(text) if natural else None
        if ask:action="ask"
        if feedback or feedback_status:action="experiment_feedback"
        if action is None:
            return ControlResult(False)
        with self.repo.author_lock(message.author_user_id,require_enabled=False):
            if not self.repo.claim_control(message):
                return ControlResult(True)
            permission=self.repo.permissions(message.author_user_id)
            if action=="help":
                return ControlResult(True,"Расскажи, что произошло, чего хотел и что получилось. Я могу сопоставить это с твоей историей, "
                    "задать вопрос или предложить следующий ход. Можно попросить «покажи мой трек» или «подведи итоги за неделю». "
                    "Если мои комментарии сейчас не нужны, скажи мне «пока не комментируй».")
            if action=="privacy":
                return ControlResult(True,self.disclosure_text())
            if action=="status":
                return ControlResult(True,self._status(permission))
            enabling=action.endswith("_on") or action in {"track_start","resume","full_resume"}
            if (enabling or action in {"ask","experiment_feedback","track_request","review_request"}) and not self.repo.scope_enabled():
                return ControlResult(True,"Эта тема сейчас отключена. Остановить обработку или удалить свои данные по-прежнему можно.")
            if action=="ask":
                if not ask[1]:
                    return ControlResult(True,"Какой вопрос хочешь разобрать?")
                if not permission.direct_request_group_reply:
                    return ControlResult(True,"Ты выключил ответы на свои вопросы. Если хочешь вернуть их, скажи мне «отвечай на мои вопросы».")
                return ControlResult(True,job_mode="direct")
            if action=="experiment_feedback":
                if not (permission.retain_source and permission.historical_inference):
                    return ControlResult(True,"Твоя работа с памятью сейчас выключена, поэтому отметку в трек не записываю.")
                if feedback:
                    statuses={"accept":"accepted","decline":"declined","tried":"tried_self_reported",
                              "not_tried":"not_tried_self_reported","close":"retired"}
                    identifier,status=feedback[2],statuses[feedback[1]]
                else:
                    candidates=self.repo.feedback_candidates(message)
                    if len(candidates)!=1:
                        return ControlResult(True,"Про какой из предложенных вариантов ты говоришь? Ответь на сообщение с ним или уточни словами.")
                    identifier,status=candidates[0],feedback_status
                if not self.repo.record_experiment_feedback(message,experiment_id=identifier,status=status):
                    return ControlResult(True,"Не нашёл этот вариант среди твоих актуальных записей с доступными источниками.")
                labels={"accepted":"ты хочешь попробовать","declined":"ты отказываешься от варианта",
                        "tried_self_reported":"по твоим словам, ты попробовал","not_tried_self_reported":"по твоим словам, ты не пробовал","retired":"ты закрыл вариант"}
                return ControlResult(True,"Запомнил: "+labels[status]+". Намерение, попытка и результат остаются разными отметками.")
            changes={}
            if action in {"archive_on","archive_off","history_on","history_off","direct_on","direct_off"}:
                flag={"archive":"retain_source","history":"historical_inference","direct":"direct_request_group_reply"}[action.split("_")[0]]
                changes={flag:action.endswith("_on")}
            elif action=="storage_off":
                changes={"retain_source":False,"historical_inference":False}
            elif action=="full_resume":
                changes={name:True for name in GRANT_FIELDS}
            elif action in {"auto_on","auto_off","stop","resume"}:
                positive=action in {"auto_on","resume"}
                if positive and not (permission.retain_source and permission.historical_inference):
                    return ControlResult(True,"Память или работа с историей у тебя выключена. Чтобы вернуть весь разбор, скажи мне «снова запоминай и разбирай».")
                changes={"auto_report_analysis":positive,"unsolicited_group_reply":positive}
            elif action=="track_start":
                if not (permission.retain_source and permission.historical_inference):
                    return ControlResult(True,"Для трека нужна история. Если хочешь вернуть её обработку, скажи «снова запоминай и разбирай».")
                self.repo.set_track_status(message.author_user_id,"active")
                return ControlResult(True,"Трек снова активен. Можешь попросить показать его или подвести итоги за нужный период.")
            elif action=="track_pause":
                self.repo.set_track_status(message.author_user_id,"paused")
                return ControlResult(True,"Трек на паузе. Память и ответы на вопросы сохраняют свои настройки.")
            elif action in {"track_request","review_request"}:
                if not 1<=days<=3660:
                    return ControlResult(True,"Какой период разобрать? Можно от одного дня до десяти лет.")
                if not (permission.retain_source and permission.historical_inference and permission.direct_request_group_reply):
                    return ControlResult(True,"Для такого разбора у тебя выключена память, работа с историей или ответы. Скажи «снова запоминай и разбирай», если хочешь вернуть всё.")
                if action=="track_request" and permission.track_status!="active":
                    return ControlResult(True,"Трек сейчас на паузе. Скажи «продолжай», если хочешь его вернуть.")
                return ControlResult(True,job_mode="track" if action=="track_request" else "weekly_review",window_days=days)
            elif action=="track_delete":
                count=self.repo.delete_track(message)
                return ControlResult(True,f"Удалил твои производные записи и эксперименты: {count}. Трек выключен. "
                    "Исходный архив сохранился; если его тоже нужно удалить, скажи мне «забудь мои сообщения».")
            elif action=="forget":
                receipt=self.repo.forget(message)
                self.repo.change_permissions(message,retain_source=False,historical_inference=False,
                                             auto_report_analysis=False,unsolicited_group_reply=False)
                return ControlResult(True,f"Удалил твои источники этой темы: сообщений {receipt.messages}, версий {receipt.revisions}, "
                    "а также производные записи и ожидающие ответы. Память, анализ и трек остановлены до явного возвращения. "
                    "Это не удаляет сообщения Telegram и старые резервные копии. Можно вернуться словами «снова запоминай и разбирай».")
            if any(getattr(permission,key)!=value for key,value in changes.items()):
                self.repo.change_permissions(message,**changes)
            if action=="stop":
                self.repo.set_track_status(message.author_user_id,"paused")
                return ControlResult(True,"Пока не буду самостоятельно комментировать; трек на паузе. Настройку памяти не меняю. "
                    +("На твой вопрос могу ответить." if permission.direct_request_group_reply else "Ответы на твои вопросы тоже остаются выключенными."))
            if action in {"resume","full_resume"}:
                self.repo.set_track_status(message.author_user_id,"active")
            return ControlResult(True,self._status(self.repo.permissions(message.author_user_id)))
