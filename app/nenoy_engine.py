from __future__ import annotations

import re

STATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "crisis": (
        "не хочу жить",
        "покончить с собой",
        "суицид",
        "самоубий",
        "навредить себе",
        "хочу себе навредить",
        "сейчас сорвусь и сделаю глупость",
        "не контролирую себя",
    ),
    "instruction_request": (
        "системную инструкцию",
        "системная инструкция",
        "внутренние правила",
        "скрытые настройки",
        "покажи промпт",
        "раскрой промпт",
        "служебную информацию",
        "алгоритмы внутреннего",
        "как ты принимаешь решения",
    ),
    "quit": (
        "бросить",
        "бросаю",
        "отказаться от",
        "все бросить",
        "всё бросить",
        "не хочу продолжать",
        "надоело",
        "не вижу смысла",
        "нет смысла",
        "бессмысленно",
        "это не мое",
        "это не моё",
        "устал от всего",
    ),
    "breakdown": (
        "сорвался",
        "сорвалась",
        "срыв",
        "провалил",
        "не сделал",
        "не сделала",
        "пропустил",
        "забил",
        "ничего не делал",
        "ничего не делаю",
        "третий день ничего",
        "опять откатился",
    ),
    "fatigue": (
        "я устал",
        "я устала",
        "устала",
        "устал",
        "не справлюсь",
        "не справлюсь с задачей",
        "нет сил",
        "нет настроения",
        "не могу больше",
        "сил нет",
        "ресурса нет",
        "очень устал",
        "очень устала",
        "энергии нет",
        "выжат",
        "выжата",
        "хочу отдохнуть",
        "перегорел",
        "сегодня не тяну",
        "сегодня тяжело",
        "голова не варит",
        "не вывожу",
        "уже не хватает",
    ),
    "procrastination": (
        "не могу",
        "не могу начать",
        "лень",
        "потом",
        "позже",
        "сделаю завтра",
        "завтра начну",
        "отложу",
        "начну позже",
        "не соберусь",
        "не готов",
        "не готова",
    ),
    "excuse": (
        "не сделал потому",
        "не сделала потому",
        "не успел",
        "не успела",
        "помешало",
        "мешали",
        "мешали обстоятельства",
        "из-за",
        "из за",
        "из-за них",
        "обстоятельства",
        "не получилось потому что",
        "если бы не",
        "много дел",
        "нет времени",
        "времени не было",
        "не было времени",
    ),
    "overloaded": (
        "слишком много",
        "слишком много задач",
        "все навалилось",
        "всё навалилось",
        "за что хвататься",
        "голова кругом",
        "перегружен",
        "перегружена",
        "не знаю за что взяться",
        "не знаю с чего начать",
        "куча задач",
        "задач куча",
        "хаос",
    ),
    "doubt": (
        "сомневаюсь",
        "не уверен",
        "не уверена",
        "боюсь ошибиться",
        "страшно ошибиться",
        "вдруг получится плохо",
        "не знаю выбрать",
        "не знаю, правильно ли",
        "какой вариант",
        "может лучше",
        "а вдруг",
        "вдруг не получится",
        "какой вариант выберу",
    ),
    "stuck": (
        "застрял",
        "застряла",
        "не понимаю",
        "не понимаю что делать дальше",
        "не знаю как",
        "что дальше",
        "уперся",
        "упёрся",
        "уперлась",
        "упёрлась",
        "не выходит",
        "не получается",
        "сломалось",
    ),
    "report": (
        "сделал",
        "сделала",
        "выполнил",
        "выполнила",
        "готово",
        "отправил",
        "отправила",
        "закончил",
        "закончила",
        "начал",
        "начала",
        "провел",
        "провёл",
        "провела",
        "закрыл",
        "закрыла",
        "получил",
        "получила",
    ),
    "music_request": (
        "какой музон",
        "что включить",
        "что послушать",
        "какой трек",
        "какую музыку",
        "медленная музыка",
        "подборка",
        "плейлист",
    ),
    "off_topic": (
        "разговор ушел",
        "разговор ушёл",
        "погода",
        "сериал",
        "мем",
        "поиграю",
        "новости",
        "фильм",
        "кино",
        "пиво",
        "пивка",
        "пивом",
        "бар",
        "пошли пить",
        "пойдём пить",
        "пойдем пить",
        "девочек",
        "девчонок",
        "девушки",
        "сказки",
        "рассказывать друг другу",
        "потусим",
        "тусить",
        "гулять",
        "за жизнь",
        "потрендим",
    ),
    "goal_focus": (
        "по целям",
        "что по целям",
        "по цель",
        "по цели",
        "как по целям",
        "где у нас цели",
    ),
    "goal_start_request": (
        "пни меня",
        "давай начнем",
        "давай начнём",
        "помоги действовать",
        "контролируй меня",
        "хочу дисциплину",
    ),
    "clarification": (
        "это как",
        "как это",
        "объясни",
        "поясни",
        "что ты имеешь в виду",
        "что значит",
        "уменьшаем шаг",
    ),
    "bot_error": (
        "бот не ответил",
        "бот не работает",
        "бот упал",
        "ошибка бота",
        "ошибки в ответе",
        "сбой в ответе",
    ),
    "whining": (
        "все плохо",
        "всё плохо",
        "меня бесит",
        "сложно",
        "тяжело",
        "ничего не хочу",
    ),
    "overplanning": (
        "планирую",
        "распланирую",
        "стратегия",
        "схема",
        "составлю план",
        "надо все продумать",
        "надо всё продумать",
        "сначала продумаю",
        "анализирую",
        "анализ вариантов",
    ),
    "motivation_request": (
        "замотивируй",
        "мотивируй",
        "пни",
        "дай фразу",
        "поддержи",
        "дай мотивацию",
        "нужна мотивация",
    ),
}

EMOJI_BY_STATE: dict[str, tuple[str, ...]] = {
    "procrastination": ("🥊", "✂️", "⛓️"),
    "excuse": ("✂️", "🪞", "⛓️"),
    "overloaded": ("⚙️", "🧠", "🧱"),
    "fatigue": ("🧱", "🧠"),
    "doubt": ("🧠", "🧱"),
    "breakdown": ("🧱", "✊"),
    "report": ("💥", "✊", "🚀"),
    "music_request": ("🚀", "🔥"),
    "off_topic": ("🛌", "🪞", "🔥"),
    "deadline_missing": ("📆", "⛓️"),
    "bot_error": ("🔧", "⚙️"),
    "reminder": ("🔔",),
    "checkin": ("🔔", "💥"),
    "default": ("⛓️", "🧱", "🔥"),
}

_EMOJI_PALLET = tuple(sorted({emoji for values in EMOJI_BY_STATE.values() for emoji in values}))

STYLE_SPARKS: dict[str, tuple[str, ...]] = {
    "work_task": (
        "Китай ждёт ТЗ, не твою внутреннюю драму.",
        "Красоту наведёшь потом. Сейчас нужен грязный черновик.",
        "Сначала скелет заказа, потом бантики.",
        "Офисный театр табло не принимает.",
    ),
    "deadline": (
        "Дедлайн не ждёт, он просто молча закрывает дверь.",
        "К финишу без лирики.",
        "Почти готово — сувенир из страны отмазок.",
    ),
    "sandbox": (
        "Медаль за открытый файл не выдаём.",
        "Это вход в зал, не тренировка.",
        "Не продавай себе имитацию как прогресс.",
    ),
    "procrastination": (
        "Диван опять баллотируется в президенты твоего дня.",
        "Не хочется — это погода, не приказ.",
        "Настроение сегодня не закупщик.",
    ),
    "report": (
        "Факт на стол.",
        "Табло без романтики: что сделано?",
        "Без легенд. Где результат?",
        "Показывай добычу.",
    ),
}

_STYLE_SPARK_MARKERS = (
    "бантики",
    "бой",
    "грязный черновик",
    "дедлайн",
    "диван",
    "добыч",
    "дымовая шашка",
    "китай ждёт",
    "медаль",
    "не корми",
    "офисный театр",
    "погода, не приказ",
    "скелет",
    "табло",
    "фанфары",
    "финиш",
)

_STYLE_SPARK_CATEGORY_BY_STATE = {
    "deadline_missing": "deadline",
    "postpone": "deadline",
    "postpone_after_fatigue": "deadline",
    "procrastination": "procrastination",
    "overplanning": "procrastination",
    "report": "report",
    "default": "work_task",
    "goal_focus": "work_task",
    "goal_start_request": "work_task",
    "overloaded": "work_task",
    "stuck": "work_task",
    "bot_error": "work_task",
}

RESPONSES: dict[str, tuple[str, ...]] = {
    "crisis": (
        "Стоп. Это уже не задача дисциплины. Обратись к близкому человеку или в экстренную "
        "помощь прямо сейчас. Напиши, где ты сейчас и есть ли рядом кто-то живой.",
    ),
    "instruction_request": (
        "Не отвлекайся. Возвращайся к цели.",
    ),
    "overplanning": (
        "Планов достаточно. {goal_line} Планируй не проект, а вход. Сейчас один микрошаг на 5 минут. {closing}",
        "Стратегия без входа — это декорация. {goal_line} Открой один файл и вытащи следующий реальный кусок. {closing}",
        "Сначала не карта, а точка контакта с действием. {goal_line} Выбери один микро-действие и запусти его. "
        "Дальше уже не бумага, а станция старта. {closing}",
    ),
    "motivation_request": (
        "Мотивация не придёт спасать тебя. {goal_line} Открой задачу и сделай первый кусок за 10 минут. {closing}",
        "Нужен толчок? Точка входа уже сейчас. {goal_line} Минимум: открыть файл и проверить статус первого шага. {closing}",
        "Сейчас ты не в поиске настроения, ты в поиске движения. {goal_line} Берёшь минимум и двигаешься. {closing}",
    ),
    "excuse": (
        "Причина есть. Но она не должна становиться домом. {goal_line} Какой самый маленький шаг сделаешь сейчас? ⛓️",
        "Не объяснение. Действие. {goal_line} Один вход — и смотришь результат по факту. {closing}",
    ),
    "overloaded": (
        "Много всего сразу — это иллюзия активности. {goal_line} Сфокусируйся на одной задаче и жми 20 минут. {closing}",
        "Сейчас ты в хаосе, потому и никуда не идёшь. {goal_line} Назначь одну цель на 15 минут и выполни её полностью. {closing}",
        "Переключить всё — значит ничего не закрыть. {goal_line} Оставь один рычаг и прокатай его до результата. {closing}",
    ),
    "deadline_missing": (
        "Шаг есть. Срока нет. {goal_line} Назначь реальное окно: без времени это намерение, а не обязательство. {closing}",
        "Заявил действие, но не дал времени. {goal_line} Назначь реальное окно и не откладывай это окно. {closing}",
    ),
    "quit": (
        "Бросить легко. {goal_line} Возьми один маленький кусок и вернись в ритм. {closing}",
        "Слабость не отменяет решения. {goal_line} Один факт за ближайшие 10 минут важнее, чем новый пессимизм. {closing}",
        "Капитуляция сейчас — это тоже выбор. {goal_line} Сделай один безопасный минимум и потом смотри результат. {closing}",
    ),
    "breakdown": (
        "Срыв был. {goal_line} Спектакль окончен, теперь один шаг сегодня закрывает стыковку. {closing}",
        "Ты не сломан, ты остановился. {goal_line} Дай себе одну внятную точку входа и доведи до конца. {closing}",
    ),
    "procrastination": (
        "Не надо делать всё. {goal_line} Сделай первый подход: 2 минуты входа и один факт. {closing}",
        "Сейчас задача страшнее слов про неё. {goal_line} Таймер на 2 минуты: один вход, один факт, потом перехватим движение. {closing}",
        "Прокрастинация громче, когда ты ждёшь идеала. {goal_line} Один круг: выбери шаг и запусти его. {closing}",
    ),
    "fatigue": (
        "Не справишься — не трагедия. Удаляем лишний вес, оставляем вход. "
        "{goal_line} 2 минуты входа + один маленький факт.",
        "Устал — ресурс ниже, это факт. {goal_line} Короткий подход: 20 секунд в задаче, один вход и один факт.",
        "Устал — это факт, не приговор. {goal_line} Снижаем героизм: короткая разминка, 20 секунд в задаче, один вход и факт.",
        "Да, ресурс ниже. {goal_line} Снижаем планку: сегодня только короткий подход — открой задачу и зафиксируй первый файл.",
    ),
    "postpone": (
        "Завтра можно. Только не как обещание, а как план. {goal_line} Первый шаг завтра — открыть проект и сделать пустой /webhook. Во сколько? {closing}",
        "Ок, переносим. Значит, теперь твой первый шаг закреплён: без него завтра не стартуем. {goal_line} Какой конкретный вход в 10 минут? {closing}",
        "Время уже выбрался, просто ты сместил его. {goal_line} Завтра: найти точку входа, запустить один вызов, не больше. Какой слот? {closing}",
    ),
    "postpone_after_fatigue": (
        "Усталость видна. Её можно принять, если есть точка входа. {goal_line} Выбирай: 2 минуты сейчас или завтра первый шаг с 12:00. Что выбираешь? {closing}",
        "Ок, ресурс просел. Уважение к себе — это конкретный режим. {goal_line} Или сделай 2 минуты прямо сейчас, или закрой перенос: завтра во сколько входишь с первым шагом? {closing}",
        "Да, сегодня тяжело. Значит режем задачу до нуля: открыть проект и зафиксировать /webhook. {goal_line} Выбирай: сейчас 2 минуты или завтра в 10:00 этот вход. {closing}",
    ),
    "stuck": (
        "Где конкретно упёрся? {goal_line} Одним предложением. Туман режем фактом. {closing}",
        "Укажи первую точку ошибки. {goal_line} Один узел, один вопрос, один шаг — тогда цепь идёт дальше. {closing}",
        "Не давай задаче быть абстракцией. {goal_line} Назови конкретный блок, где остановился. {closing}",
    ),
    "doubt": (
        "Сомнения не строят результат. {goal_line} Выбери рабочий вариант и проверь его действием. {closing}",
        "Думать до бесконечности — ещё не решение. {goal_line} Назначь один тестовый шаг и закрепи факт. {closing}",
        "Сомнение растёт, пока ты его кормишь. {goal_line} Один выбор сейчас лучше чем десять альтернатив. {closing}",
    ),
    "clarification": (
        "Снижаем задачу до размера шага, иначе мозг продолжит спорить. {goal_line} Схема на сегодня:\n1) Открыть проект\n2) Найти файл входа\n3) Создать пустой `/webhook`\n4) Запустить хотя бы один тестовый запрос.",
        "Ты хочешь не объяснение, а удобный вход. {goal_line} Двигай по трём пунктам: вход, точка, проверка. Не нужно дожимать всё в голове.",
        "Это нормально звучит сложно, потому что ты ещё не выбрал вход. {goal_line} Делим на 1) открыть задачу, 2) выбрать функцию, 3) выдать один проверочный результат.",
    ),
    "bot_error": (
        "Ошибка — не повод стоять. Покажи факт: где именно отвалилось и что ответил бот. Тогда сужаем до одного рабочего шага. {closing}",
        "Техшум есть, но цель не ждёт. {goal_line} Укажи конкретный сбой и что нужно проверить в следующем шаге. {closing}",
        "Не обсуждаем гипотезу. {goal_line} Скажи, какой запрос/шаг упал и что хотел увидеть как факт. {closing}",
    ),
    "report": (
        "Факт есть: ты сделал. {goal_line} Теперь закрепляем. Что будет следующим шагом? {closing}",
        "Ок, шаг зафиксирован. {goal_line} Далее один удар за одной точкой. {closing}",
    ),
    "off_topic": (
        "Пиво — сильная заявка на финал дня. {goal_line} Только финал бывает после матча. Один маленький результат — и можно к пенному совету. {closing}",
        "Бар подождёт, он не стареет так быстро, как твои обещания себе. {goal_line} Один подход на 10 минут — и отдых уже не побег, а награда. {closing}",
        "Девочки, пиво, сказки — программа мощная. {goal_line} Но сначала один факт, чтобы ты вышел не нытиком, а человеком с табло. {closing}",
        "Сказки после фактов. Шахерезада на скамейке. {goal_line} Что закрываешь первым маленьким шагом? {closing}",
    ),
    "music_request": (
        "Для старта лучше без «плюшевой» пафосной сцены: Phonk, драм-н-бейс или старый Eminem. {goal_line} Пока играет — открой задачу и сделай один вход без переговоров. {closing}",
        "Берём короткую, брутальную раскладку: drum-n-bass, хард-рок, Eye of the Tiger. {goal_line} Включи один трек и 2 минуты входа в задачу. {closing}",
        "Не спорим с вайбом: для первого рывка возьми ритм, который бьет ровно. Phonk на 2 трека и после него первый факт. {goal_line} {closing}",
    ),
    "goal_focus": (
        "По факту цель сейчас одна: {goal_line} Не растекайся. Один вход сейчас, не больше. {closing}",
        "Цель в работе есть. {goal_line} Не разрубайся общим, выбирай первый рабочий шаг. {closing}",
    ),
    "goal_start_request": (
        "Цель есть. {goal_line} Не проси пинок — назови ближайшее действие на 15 минут. {closing}",
        "Режим начала включён. {goal_line} Формулируй один вход и делай его уже. {closing}",
    ),
    "avoidance": (
        "Уклонение выглядит спокойным, пока цель стоит. {goal_line} Возвращайся к факту: что сделал в зоне действия. {closing}",
        "Не нужно прятаться за разговором. {goal_line} Один короткий шаг закрывает уклонение. {closing}",
    ),
    "whining": (
        "Жалоба услышана. Действий пока не видно. {goal_line} Включай микро-удар: два коротких шага и факт по ним. {closing}",
        "Слова про тяжесть звучат знакомо. Дальше нужен шаг, который можно закрыть. {goal_line} Укажи вход и сделай его. {closing}",
    ),
    "default": (
        "{goal_line} Не строим театр из планов. Назови один ближайший шаг и время, когда вернёшься с фактом.",
        "{goal_line} Не загоняйся в разговор. Один факт, один шаг, один следующий промежуток. {closing}",
    ),
}

NO_GOAL_PROMPT_VARIANTS = (
    "Цель не указана. Напиши коротко: результат, срок и первый шаг.",
    "Без цели это не спорт, а болото. Четко сформулируй задачу и точку входа сегодня.",
    "Окей, не грузимся: цель, дедлайн, первый микрошаг — в одну строку.",
    "Нужен фокус. Назови цель, до какого времени и с какого входа идем.",
)

ACTION_WITHOUT_DEADLINE_KEYWORDS = (
    "сделаю",
    "начну",
    "напишу",
    "отправлю",
    "позвоню",
    "открою",
    "закрою",
    "разберу",
)

DEADLINE_MARKERS = (
    "сегодня",
    "завтра",
    "утром",
    "днем",
    "днём",
    "вечером",
    "ночью",
    "до ",
    "через",
    "минут",
    "час",
    "в 1",
    "в 2",
    "в 3",
    "в 4",
    "в 5",
    "в 6",
    "в 7",
    "в 8",
    "в 9",
)

POSTPONE_KEYWORDS = (
    "сделаю завтра",
    "завтра начну",
    "перенесу",
    "завтра",
    "давай завтра",
    "потом",
    "позже",
    "начну позже",
)

EXACT_WORD_KEYWORDS = {
    "потом",
    "начал",
    "начала",
}

CLOSING_PHRASES_DEFAULT = (
    "Первый подход — сейчас.",
    "Таймер пошёл. Что делаешь первым?",
    "Без парада. Один повтор.",
    "Разминка закончилась — заходи в задачу.",
    "Табло ждёт факт, не настроение.",
    "Сейчас не спорим: заходи и делай.",
    "Удар начинается после первого движения.",
)

CLOSING_PHRASES_VARIANTS = (
    "Берёшь этот минимум?",
    "Сделаешь сейчас 2 минуты — и свободен.",
    "Выбирай: 2 минуты сейчас или честный перенос на завтра с первым шагом.",
    "Не думай весь проект. Сделай вход.",
    "Что закрываешь первым?",
    "Разминка была. Теперь заходи в рабочий круг.",
    "Табло не ждёт паузу — давай первый повтор.",
    "Ставим таймер: три, два... шаг.",
)


def _pick_no_goal_prompt(recent_messages: tuple[tuple[str, str], ...], user_message: str) -> str:
    recent_assistant = _extract_recent_assistant_messages(recent_messages, limit=3)
    seed = abs(sum(ord(char) for char in _normalize(user_message)))

    def is_repeated_in_recent(candidate: str) -> bool:
        normalized_candidate = _normalize(candidate)
        return any(
            normalized_candidate in message
            for message in recent_assistant
        )

    for shift in range(len(NO_GOAL_PROMPT_VARIANTS)):
        candidate = NO_GOAL_PROMPT_VARIANTS[(seed + shift) % len(NO_GOAL_PROMPT_VARIANTS)]
        if not is_repeated_in_recent(candidate):
            return candidate

    return NO_GOAL_PROMPT_VARIANTS[seed % len(NO_GOAL_PROMPT_VARIANTS)]


def _normalize(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _extract_recent_assistant_messages(recent_messages: tuple[tuple[str, str], ...], limit: int = 3) -> tuple[str, ...]:
    assistant_messages = [
        _normalize(content) for role, content in recent_messages if role == "assistant"
    ]
    return tuple(assistant_messages[-limit:])


def _has_style_spark(text: str) -> bool:
    normalized_text = _normalize(text)
    return any(marker in normalized_text for marker in _STYLE_SPARK_MARKERS)


def add_style_spark(base: str, state: str) -> str:
    if state in {"crisis", "instruction_request", "fatigue", "doubt", "clarification"}:
        return base

    if _has_style_spark(base):
        return base

    category = _STYLE_SPARK_CATEGORY_BY_STATE.get(state)
    if category is None:
        return base

    sparks = STYLE_SPARKS.get(category)
    if not sparks:
        return base

    spark = sparks[0]
    if spark in base:
        return base

    return f"{spark}\n\n{base}"


def pick_state_emoji(state: str, recent_text: str = "") -> str:
    if state in {"crisis", "instruction_request"}:
        return ""

    variants = EMOJI_BY_STATE.get(state) or EMOJI_BY_STATE["default"]
    for emoji in variants:
        if emoji not in recent_text:
            return emoji

    return variants[0]


def _contains_state_emoji(text: str) -> bool:
    return any(emoji in text for emoji in _EMOJI_PALLET)


def _append_state_emoji(state: str, response: str) -> str:
    if state in {"crisis", "instruction_request"}:
        return response

    emoji = pick_state_emoji(state, recent_text=response)
    if not emoji:
        return response
    if _contains_state_emoji(response):
        return response
    return f"{response} {emoji}"


def _previous_user_state_was_fatigue(recent_messages: tuple[tuple[str, str], ...]) -> bool:
    for role, message in reversed(recent_messages):
        if role != "user":
            continue
        return detect_state(message) == "fatigue"
    return False




def _state_has_closing_variant(state: str) -> bool:
    return state not in {"crisis", "instruction_request", "clarification"}


def _has_recent_start_phrase(recent_messages: tuple[tuple[str, str], ...]) -> bool:
    if not recent_messages:
        return False
    return any(
        "когда стартуешь" in message
        for message in _extract_recent_assistant_messages(recent_messages, limit=3)
    )


def _pick_closing_phrase(recent_messages: tuple[tuple[str, str], ...], user_message: str) -> str:
    index_seed = abs(sum(ord(char) for char in _normalize(user_message)))
    if _has_recent_start_phrase(recent_messages):
        return CLOSING_PHRASES_VARIANTS[index_seed % len(CLOSING_PHRASES_VARIANTS)]
    return CLOSING_PHRASES_DEFAULT[index_seed % len(CLOSING_PHRASES_DEFAULT)]


def _pick_template(replies: tuple[str, ...], user_message: str) -> str:
    if not replies:
        return ""
    seed = abs(sum(ord(char) for char in _normalize(user_message)))
    return replies[seed % len(replies)]


def detect_state(user_message: str) -> str:
    normalized_message = user_message.lower().replace("ё", "е")

    for state, keywords in STATE_KEYWORDS.items():
        for keyword in keywords:
            normalized_keyword = keyword.lower().replace("ё", "е")
            if normalized_keyword in EXACT_WORD_KEYWORDS:
                pattern = rf"(?<!\w){re.escape(normalized_keyword)}(?!\w)"
                if re.search(pattern, normalized_message):
                    return state
                continue
            if normalized_keyword in normalized_message:
                return state

    has_action = any(keyword in normalized_message for keyword in ACTION_WITHOUT_DEADLINE_KEYWORDS)
    has_deadline = any(marker in normalized_message for marker in DEADLINE_MARKERS)
    if has_action and not has_deadline:
        return "deadline_missing"

    return "default"


def generate_response(
    user_message: str,
    goal: str | None = None,
    recent_messages: tuple[tuple[str, str], ...] = (),
) -> str:
    clean_message = user_message.strip()
    clean_goal = goal.strip() if goal else None

    if not clean_message:
        return "Пустой ввод не считается работой. Напиши цель или ближайший шаг."

    state = detect_state(clean_message)
    if state in {"crisis", "instruction_request"}:
        template = RESPONSES.get(state, RESPONSES["default"])
        return _append_state_emoji(
            state,
            template[0].format(goal_line="Цель сейчас — безопасность."),
        )

    if not clean_goal and state not in {"goal_focus", "music_request"}:
        return _pick_no_goal_prompt(recent_messages, clean_message)

    goal_line = "Цель сейчас — безопасность." if not clean_goal else f"Цель: {clean_goal}."
    normalized_message = _normalize(clean_message)

    if any(keyword in normalized_message for keyword in POSTPONE_KEYWORDS):
        if _previous_user_state_was_fatigue(recent_messages):
            state = "postpone_after_fatigue"
        else:
            state = "postpone"

    if state == "postpone_after_fatigue" and "postpone_after_fatigue" not in RESPONSES:
        state = "postpone"

    if state not in RESPONSES:
        state = "default"

    closing = _pick_closing_phrase(recent_messages, clean_message)

    if state == "fatigue":
        if any(
            marker in normalized_message
            for marker in ("не справлюсь", "не справлюсь с задачей")
        ):
            template = RESPONSES[state][0]
        elif any(marker in normalized_message for marker in ("я устал", "устала", "устал")):
            template = RESPONSES[state][1]
        else:
            template = _pick_template(RESPONSES[state], clean_message)
    else:
        template = _pick_template(RESPONSES[state], clean_message)

    response = (
        template.format(goal_line=goal_line)
        if not _state_has_closing_variant(state)
        else template.format(goal_line=goal_line, closing=closing)
    )
    return _append_state_emoji(state, add_style_spark(response, state))
