# TASK 28 — Controlled Friends Test

Status: **IN PROGRESS**
Restarted: **2026-10-07**
Production baseline: `v2@37e7b51039380f9c44776a32f24898ddc3a511ca`
Social Brain checkpoint before merge: `5c413743434147e9ac63294310a3057fff40e7f3`

## Goal

Проверить не то, умеет ли НеНой технически отвечать, а становится ли он органичным участником живой группы.

Главное правило после Social Brain P1:

> **Молчание — default. Инициатива даёт право оценить момент, но не обязанность писать. Без grounded hook — NO_ACTION.**

## North Star

**Количество участников, кроме владельца, которые сами повторно обращаются к НеНою.**

## Secondary signals

- organic participants;
- direct mentions / replies;
- повторные обращения одного участника;
- reaction quality;
- roast hit / miss;
- callback hit / miss;
- ignored unsolicited interventions;
- negative feedback / mute;
- intervention rate per 100 human messages;
- доля unsolicited interventions, после которых был положительный engagement;
- доля unsolicited interventions, которые были проигнорированы;
- organic use of memory / reminders / scheduled actions;
- cost per active group / active participant.

## 7-day protocol

### Days 1–2 — Observe / low pressure

Не усиливать инициативу вручную.

Смотрим:
- правильно ли НеНой молчит в обычной болтовне;
- отвечает ли на direct mention / reply;
- не лезет ли после одной только тишины;
- есть ли ложные grounded hooks;
- остаются ли реакции органичными.

Если unsolicited intervention выглядит лишним — сохраняем конкретный event / decision reason и разбираем его как live finding.

### Days 3–4 — Cautious callbacks

Ничего не повышать автоматически только по календарю.

Разрешено оставить текущий policy как есть и оценивать:
- callback уместен / неуместен;
- память действительно относится к текущему контексту;
- negative feedback быстро охлаждает повторение;
- callback fatigue работает.

Усиление policy допустимо только если Days 1–2 не дают явного раздражения.

### Days 5–7 — Character pressure only if earned

НеНой может быть ярче только если предыдущие дни показывают здоровую реакцию.

Смотрим:
- roast / humor hit rate;
- повторные обращения к НеНою;
- используют ли участники его как участника группы, а не как кнопку;
- не возникает ли fatigue от постоянного присутствия.

## Hard stop / rollback signals

Немедленно считаем live finding блокирующим, если:
- НеНой регулярно вмешивается без понятного grounded hook;
- повторяет одну и ту же callback-memory несмотря на fatigue;
- негативная реакция не уменьшает инициативу;
- proactive=false memory влияет на unsolicited ответ;
- direct mention / reply перестаёт отвечать;
- group memory пересекается между группами;
- reminder / birthday / scheduled action нарушает гарантированный operational path;
- явно отключённая группа реактивируется сама.

## Process rule

Во время TASK 28:

- **не добавляем новые функции без реального live-сигнала**;
- не запускаем новый широкий hardening review после каждого edge case;
- чиним только воспроизводимые P1/P2, которые ломают живой сценарий, privacy, truthful behavior или safety;
- после bounded fix: targeted tests → full `tests_v2` with PostgreSQL → один bounded delta review → deploy → снова live observation.

Conversation ownership / third-wheel inference остаётся **OUT OF SCOPE**.

## Daily checkpoint

К концу каждого дня фиксируем:

1. human messages;
2. direct interactions with НеНой;
3. unsolicited interventions;
4. organic / awkward / clearly wrong split;
5. positive reactions / negative feedback / ignored;
6. повторные обращения не владельца;
7. конкретные live findings;
8. что менять / что не трогать.

## Exit criteria

TASK 28 можно закрыть только после 7 живых дней и Product Review v0.2.

Перед закрытием ответить:

- Люди сами возвращаются к НеНою?
- Он умеет молчать?
- Его инициативные реплики чаще добавляют ценность, чем раздражают?
- Память и callbacks ощущаются уместными?
- Можно ли переходить к настройкам/закрытой бете без ещё одного фундаментального redesign?
