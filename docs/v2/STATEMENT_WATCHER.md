# Statement Watcher — НеНой 2.0

Goal: let Group НеНой notice a participant contradicting their own earlier words and intervene without a direct mention.

Flow:
1. Scene Analyzer marks promises, decisions, bold predictions and quotable statements as memory-worthy.
2. Memory Mapper stores grounded statement cards with the original Telegram message evidence and same-author subject key.
3. On later ordinary group messages, Group Behavior retrieves callback memories only inside the same group and same actor.
4. Statement Watcher compares the current text against those grounded cards. It never invents history and fails closed.
5. High-confidence contradiction/broken-commitment matches become priority statement events. They may bypass ordinary unsolicited cooldown, but group mute, hard daily limit and sensitive/serious-context gates still win.
6. Generator receives the exact statement-watch evidence and produces a short callback/roast, not a lecture.
7. Used callback memories are marked used so callback fatigue prevents hammering the same quote repeatedly.

Safety / isolation:
- Personal memory is never queried for Group statement watching.
- Group A memory is never queried for Group B.
- Only the same author's evidence is eligible.
- No proactive roast in serious, conflict-heavy or sensitive scenes.
- Strong false-negative bias: ambiguous relation => silence.

Initial target examples:
- "Я только одну" -> later "наливай ещё".
- "Завтра баня, я буду" -> later "не, я не иду".
- "Больше в этот проект денег не вкладываю" -> later "ещё сервер оплатил".
