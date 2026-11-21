# G2 Product Specification — «АнтиАвтопилот» / Break Pattern Engine

**Status:** REWORK v1.1 / G2 REVIEW REQUIRED. No independent review, G8 acceptance or build permission.
**Date:** 2026-11-21
**Request:** Issue #163 (https://github.com/Mix13131/nenoybot/issues/163)
**Parent:** Reflection Coach #161 (https://github.com/Mix13131/nenoybot/issues/161)
**G1:** Draft PR #162 (https://github.com/Mix13131/nenoybot/pull/162), PARTIAL / ADVISORY.
**Product baseline when authored:** nenoybot/v2 @ 805d2aafc709cb2323f228d791b16bb1f63f64b8.
**Audience:** two consenting members of one existing Telegram group; no rollout to other groups.
**Document discipline:** public repository; synthetic examples only; no personal chat data, real names or group IDs.

## 1. Product question and outcome

**Problem:** A person can explain familiar actions and recurring difficulties yet fail to imagine alternatives outside their established behavioral repertoire. Routine tips such as “be bolder”, “leave your comfort zone” or “try something new” do not help.

**North Star:** increase the number of *different, meaningful, voluntarily chosen responses* a participant can use in real situations.

**Behavioral value, not clinical claim:** identify a grounded habitual choice, offer one surprising, safe, concrete change in approach, let the participant try/decline, and learn what actually happened.

**P0 user story (current-report only):** With consent and verified group/topic access, when a participant directly asks НеНой for an unexpected approach to the **current report or explicitly supplied scenario**, НеНой proposes one reversible alternative, does not assert a longitudinal pattern, and accepts refusal without follow-ups.

**P1 user story (later, separately accepted):** Only after proven forum-topic provenance, separate participant consent and purpose-bound retention, analyze permitted history to identify a **source-backed recurring choice**, propose an alternative and learn from voluntary self-reports. The P1 aim to "analyze the whole chat" is **not** a P0 capability or permission.

**Non-goals:** personality diagnosis; treatment; forcing discomfort; ranking friends; public disclosure of sensitive life history; fabricated past or “never done before” claims; daily nagging; random novelty; new services, external data fetch or automatic chat import.

## 1.1. Release boundary: P0 is not P1

| Stage | Evidence allowed | Response | Hard boundary |
| --- | --- | --- | --- |
| P0-OFFLINE | Synthetic/anonymized fixtures only | Evaluate whether suggestions are concretely unusual and safe | No Telegram access or production claims |
| P0-LIVE (requires separate owner permit) | Requesting consenting author's **current addressed report** or their explicitly supplied text | One optional counter-pattern action; transparent about not knowing long history | No retrieval of ordinary group HOT/LONG history; no pattern or "first-time" claim |
| P1-HISTORY (future) | Permitted topic-filtered, participant-consented history with provenance | Verify 2+ independent real episodes before "recurring" claim | No analysis until topic-aware storage/retrieval and opt-in tests pass |

No stage may silently change the generic group profile. The existing Telegram ingest stores raw messages before the Group Dispatcher; the Reflection consent gate alone **does not prevent baseline collection**. Resolve disclosure and data minimization as a separate privacy requirement before P0-LIVE.

## 2. Terms and evidence

- **Single observation:** one specific event attributed to the right author and Telegram topic; cannot justify “you always”.
- **Recurring pattern:** at least two distinct, source-backed events of substantially similar behavior by the same person. Count distinct occurrences rather than messages quoting the same occurrence; a prior assistant suggestion is not an independent observation.
- **Unusual alternative:** a concrete choice that differs meaningfully from a *specific evidenced current approach*; mere variation of words, scale, timing or random route is not enough.
- **Experiment:** low-risk, reversible, optional action plus a relevant observation question. It is not a mission, contract or behavioral diagnosis.
- **New-to-user:** never inferred from absence of historical messages; can be marked confirmed only if the participant explicitly reports that it is new.
- **Fact:** externally cited group message or explicit participant report. **Hypothesis:** possible explanation or predicted effect. **Unknown:** missing evidence, consent or feasibility.
- **No action:** an acceptable decision whenever evidence, novelty, safety, relevance or permission is insufficient.

## 3. Functional requirements

| ID | Requirement | Acceptance |
| --- | --- | --- |
| BPM-01 | Only within one opted-in group and permitted topic(s); individual consent is required | Unconsented participant / wrong chat / forbidden topic cannot be profiled |
| BPM-02 | Accurate participant, message, time and thread attribution | Two authors/replies/quotes cannot be conflated |
| BPM-03 | Evidence threshold for repeated patterns | One instance is framed as an observation or uncertainty, never as a stable trait |
| BPM-04 | Distinct behavioral transformation | A proposed action visibly changes role, sequence, channel, boundary, support or interpretation |
| BPM-05 | Distinct candidate generation | Use a bounded 1–3-candidate strategy (not necessarily 3 LLM calls); choose one user-facing alternative only when genuinely different and worth testing; otherwise NO_ACTION |
| BPM-06 | Specific first step and observation | Includes what, in which appropriate setting, what to notice and how to opt out |
| BPM-07 | Genuine novelty, honestly qualified | No claim about “never before” without self-confirmation |
| BPM-08 | Consent and user autonomy | Direct mention/reply only in P0; “pass”, “not now” and “less intense” are accepted immediately |
| BPM-09 | Personalization without labels | Do not infer disorders, character flaws, hidden motives or stable personality from group logs |
| BPM-10 | Sensitive-context safety | Distress, exhaustion, conflict or health concerns switch to care/no challenge; risky experiment blocked |
| BPM-11 | No repetitions | Reject duplicate/completed/declined proposals; allow an explicit retry request |
| BPM-12 | Optional self-report outcomes | Distinguish offered / accepted / tried_self_reported / not_tried_self_reported / declined / unknown / expired; no reply is never evidence of failure or success |
| BPM-13 | Participant correction and forget controls | Explicit purpose-scoped correction/revocation, cessation of new retrieval, deletion contract for derived data and pending jobs; do not promise retroactive erasure from Telegram or backups |
| BPM-14 | Explainable source boundaries | Message IDs are not automatically verified clickable links; only cite actually authorized and validated evidence, with dates/limits when deep links cannot be verified |
| BPM-15 | Group and topic isolation | Do not leak Personal or other-group memory; do not mix Chat/Reports topics for unsupported comparisons |
| BPM-16 | Isolation from existing persona | Reflection Coach has no aggressive roast; other existing friends groups unchanged |
| BPM-17 | Graceful degradation | Missing history/LLM/store/permission -> transparent clarification or NO_ACTION, never fake memory |
| BPM-18 | Short Telegram default | 2–5 sentences; details only on request |
| BPM-19 | Collaborative exercise consent | No publicly assigned challenge for the other person without their own consent |
| BPM-20 | Controlled learning loop | Weekly review says proposed / tried / declined / observed, with uncertainty and no fake long-term improvement |

## 4. Transformations catalogue (internal strategies, not a list to send)

1. **Reverse role:** a usual leader participates as a learner; an expert asks rather than advises.
2. **Reverse sequence:** discover real objections before creating a full solution; rest before planning when depleted.
3. **Remove a crutch:** try a safe bounded activity without the default tool, script or AI assistant.
4. **Change communication mode:** appropriately substitute phone/text/face-to-face, respecting the other person's preference.
5. **Ask for disconfirming evidence:** invite respectful criticism of one's idea, then listen before defending.
6. **Beginner's mind:** attempt a harmless unfamiliar craft/topic without monetization or performance pressure.
7. **Set or renegotiate a boundary:** respectfully decline a minor request or propose a different arrangement.
8. **Delegate or step back:** let someone else select the approach within mutually agreed safe limits.
9. **Non-instrumental play:** do something solely for interest or enjoyment without a measurable business benefit.
10. **Cross-domain transfer:** test a practice from a different domain with explicit relevance and small downside.

A transformation is NOT relevant merely because its name is unusual. It must change the identified default response in the actual situation. Avoid suggestions that burden uninvolved people.

## 5. Decision policy (proposed; implementation G3 only)

A. Check user is explicitly addressing the bot, opted in, permitted topic, and access boundaries. For P0-LIVE, build a **minimal context packet limited to the current authorized message**; do not pass existing chat-wide HOT/LONG context.
B. Check sensitive context and known individual constraints. If unsafe/insufficient, use care, question or NO_ACTION.
C. P0: evidence is current addressed report only. P1: retrieve evidence filtered in SQL by exact group/topic/consenting participant with source IDs, author and date; dedupe real-world episodes, forwards and edits, not just message IDs.
D. Identify an **observable** default behavior, not a hidden psychological cause.
E. Generate three diverse candidates using different transformations, each with a minimal action and learning question.
F. Remove ungrounded, unsafe, infeasible, high-cost, coerced, duplicated, overly generic and irrelevant candidates using deterministic policies plus semantic review; uncertainty falls back to no action.
G. Select one genuinely different option, phrase as an invitation with a clear skip/lower-intensity option. Do not pretend this is medical/psychological assessment.
H. On voluntary outcome, store verified event status and reported learning under the agreed retention/privacy contract. No automated compliance score.
I. Respect stop/forget/preferences; no unsolicited follow-up in P0.

**Implementation boundary:** do not claim a purely generative model can guarantee safety or originality. Hard permissions, topic boundaries, and stop conditions must be deterministic. Human review is required for ambiguous high-stakes behavior.

## 6. UX examples (synthetic and illustrative)

**Scenario A — solution-first pattern:** “In two earlier discussions, you jumped to recommendations before hearing a concrete example. Unusual move: in the next low-stakes customer interview, ask questions only for 15 minutes, without pitching. Notice what you learn that a ready-made solution would have hidden. Want to try or pass?”

**Scenario B — need for external agreement:** “You have described discomfort about possible negative reactions. Try asking a cooperative colleague for one specific critique of a work draft; listen and write down the objection before explaining yourself. What actually happens compared with what you expected? Not today is also fine.”

**Scenario C — no pattern evidence:** “I do not have enough permitted history to claim this is your habit. I can offer a small exploratory experiment, or you can show me an example of a situation you want to approach differently.”

**Scenario D — repeated suggestion:** “We already tried a variation of that. Another move would be to reverse the roles: instead of giving advice, ask the other person to teach you their approach.”

**Scenario E — genuine first time:** “I do not know whether you have ever done this. If you decide to try and confirm it was new, I can record that in your own experiment history.”

Never make a person publicly disclose private health, family or relationship details to the group.

## 7. User paths

- **Ask:** direct mention or reply such as “НеНой, покажи необычный ход по этому отчёту”.
- **Request another:** “слишком банально, другой вариант” -> different transformation, not superficial rewording.
- **Reduce stakes:** “помягче” -> smaller reversible step; refuse unsafe variants.
- **Decline:** “не сегодня / пропускаю” -> acknowledge without shame or further chasing.
- **Report:** “попробовал, вот что произошло” -> distinguish performed action from expected impact.
- **Review:** “что из нового я действительно попробовал за неделю?” -> report attempted/declined/unknown separately.
- **Forget:** “не используй эту тему” / scoped forget -> do not surface the retired content in callbacks or future experiments.

## 8. Privacy, consent and data contract — decisions still required

- Bot owner consent is **not** consent from every group participant. Each participant must opt in for personal reports/patterns; scope and purpose disclosed in advance.
- Group memories are not allowed to pull from private conversations, other groups, private documents, or earlier chat exports without separate explicit opt-in.
- Group-specific messages can still include third parties: do not propose experiments involving non-consenting people in ways that burden or manipulate them.
- Separate **purpose-specific opt-in** from Telegram's *pre-existing* general bot ingest. Legacy `event_ingestor` currently persists `messages.text` and `events.payload` before Group policies. Decide and disclose baseline processing/retention instead of promising “nothing is stored until consent”.
- P0-LIVE must use only the requesting consenting author's current supplied report; no automatic reading of other chat turns for personalized suggestions, even though the bot's ordinary runtime could technically retrieve them.
- Telegram forum topic isolation must be implemented/tested before retrospective pattern analysis across reports. Current v2 group context is chat-scoped, with partial threaded fail-closed handling.
- Proposed minimum retained fields: authorized group/topic/user, consent version, evidence references (not secret raw quote), technique ID, suggestion ID, offered status, optional self-report, opt-out/forget state. Exact retention TTL, evidence deletion behavior, migration, source-link policy and base-bot data notice require owner decision at G2/G3.
- Forget semantics must account for raw `messages`, `events.payload`, `memory_cards`, `interventions.generated_text`, `outbox`, derived summaries and any pending jobs; backups and already delivered Telegram messages may not be erasable by the bot. It must never falsely confirm complete deletion.
- Neither a group admin nor a friend's encouragement replaces another person's explicit opt-in; a private/sensitive third-party disclosure is not a suitable source for public experiments.
- Existing MEMORY_SPEC's 30-day raw text policy is a reference default, not blanket authorization to store sensitive psychological narratives for 30 days or forever.
- This public repository must contain only invented/anonymized fixtures, no conversation export, Telegram IDs, personal medical facts or production credentials.

## 9. Acceptance and product quality

**Gate A: requirements / privacy:** both participants can understand the data use and voluntarily opt in; consent absence means no personalized suggestions. Explicit default: no unsolicited bot message.

**Gate B: synthetic functional tests:** run separate acceptance matrix in companion file; all privacy, safety and attribution negative tests must pass before human pilot. Include P0 context minimization, P1 topic/consent filtering, revoked consent with pending retries, and backup/Telegram deletion limits. No unsupported personal labels or claims of “never” are permitted.

**Gate C: qualitative creativity:** two independent reviewers judge a bounded synthetic sample for (a) relevance, (b) material difference from routine, (c) concreteness, (d) low downside, (e) learning potential. Disagreements remain visible; do not replace disagreement with fake precision.

**Gate D: controlled real pilot:** after explicit permission for the P0-LIVE boundary, two consenting users run *current-report-only* requests voluntarily for 7 days. Record per proposal: useful/unhelpful, surprised/obvious, tried/declined, what was observed, annoyance, repeated suggestion. With only two users, do not present percentage claims as statistical evidence.

**Gate E: stop conditions:** any unconsented reveal, mixed-topic memory, false source attribution, high-risk unsafe suggestion, persistent shaming or reply after opted-out challenge is a pilot STOP/REWORK trigger.

## 10. Dependencies and actual Project OS state

- G0 change intent is clear; exact product and privacy contract remain PROPOSED.
- G1 is PARTIAL: Draft PR #162 proposes non-default v2 governance trust model. It is not merged, independent review not verified and no compatible Steward is installed.
- G2 this v1.1 document addresses known first-party red-team issues but remains an owner-review candidate, **not an independent G6 PASS**.
- G3 requires topic-aware evidence, memory minimization, consent model, and risk gates before code.
- G4–G5: BLOCKED_FOR_BUILD by current Project OS policy; no CI activation, app changes, DB migrations, bot runtime changes or production updates.

**Recommended safe next decision:** accept/rework the P0 behavior and privacy scope, then commission independent G2 review and resolve G1 governance before coding.
