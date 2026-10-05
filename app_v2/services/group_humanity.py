from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


ResponseIntent = Literal["SILENCE", "MICRO", "SHORT", "NORMAL"]

_REPAIR_RE = re.compile(
    r"(?:"
    r"\b(?:перегибаешь|перегнул|перегнула|перебарщиваешь|перебор)\b"
    r"|\b(?:будь|стань)\s+(?:полезнее|полезным|нормальнее)\b"
    r"|\b(?:не\s+надо|хватит|прекрати)\b.{0,80}\b(?:наезжать|атаковать|сталкивать|ссорить|подъебывать|подкалывать)\b"
    r"|\b(?:ты\s+)?(?:сталкиваешь|ссоришь)\b.{0,80}\b(?:людей|участников|нас)\b"
    r"|\b(?:stop\s+attacking|be\s+useful|you(?:'re| are)\s+overdoing\s+it)\b"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)

_RETIRE_TOPIC_RE = re.compile(
    r"(?:"
    r"\b(?:хватит|прекрати|перестань)\b.{0,80}\b(?:эту|этой|про\s+эту|про\s+это)?\s*(?:тему|шутку|прикол)\b"
    r"|\b(?:не\s+мусоль|не\s+повторяй|давай\s+что[- ]?то\s+новенькое)\b"
    r"|\b(?:забудь|забей)\b.{0,60}\b(?:про|эту\s+тему|эту\s+шутку)\b"
    r"|\b(?:stop\s+repeating|drop\s+this\s+topic|new\s+topic)\b"
    r")",
    flags=re.IGNORECASE | re.DOTALL,
)

_EMOJI_ONLY_RE = re.compile(
    r"^[\s\W_]+$",
    flags=re.UNICODE,
)

_MICRO_ACKS = {
    "ок", "окей", "ага", "угу", "ясно", "понял", "поняла", "спасибо",
    "принято", "круто", "лучший", "красава", "норм", "да", "нет",
}


@dataclass(frozen=True)
class GroupHumanitySignal:
    social_repair: bool = False
    retire_topic: bool = False
    response_intent: ResponseIntent = "NORMAL"

    def as_action_state(self) -> dict[str, object]:
        return {
            "social_repair": self.social_repair,
            "retire_topic": self.retire_topic,
            "response_intent": self.response_intent,
        }


def classify_group_humanity(text: str | None) -> GroupHumanitySignal:
    value = " ".join((text or "").strip().split())
    lowered = value.casefold().replace("ё", "е")

    repair = bool(_REPAIR_RE.search(value))
    retire_topic = bool(_RETIRE_TOPIC_RE.search(value))

    if repair:
        intent: ResponseIntent = "SHORT"
    elif value and _EMOJI_ONLY_RE.fullmatch(value):
        intent = "SILENCE"
    elif lowered in _MICRO_ACKS:
        intent = "MICRO"
    elif len(value.split()) <= 3 and value and not value.endswith("?"):
        intent = "MICRO"
    else:
        intent = "NORMAL"

    return GroupHumanitySignal(
        social_repair=repair,
        retire_topic=retire_topic,
        response_intent=intent,
    )
