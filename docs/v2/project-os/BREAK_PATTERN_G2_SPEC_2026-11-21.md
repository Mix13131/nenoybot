# G2 Product Specification — «АнтиАвтопилот» / Break Pattern Engine

**Status:** REWORK v1.4 / REVIEW_REQUIRED. Codex reviewed proactive v1.3 at HEAD `8fbeea905e912a53d6456038b6011eadf981cff0` and raised three P2 findings on direct-request consent, mandatory silent-evaluation cost caps, and question-vs-experiment acceptance. This new revision is not yet independently reviewed; no owner or build authorization.
**Date:** 2026-11-21
**Request:** Issue #163 (https://github.com/Mix13131/nenoybot/issues/163)
**Parent:** Reflection Coach #161 (https://github.com/Mix13131/nenoybot/issues/161)
**Related:** on-demand Personal Growth Track #166 (https://github.com/Mix13131/nenoybot/issues/166) — shared P0 user experience, P1 longitudinal history deferred.
**G1:** Draft PR #162 (https://github.com/Mix13131/nenoybot/pull/162), PARTIAL / ADVISORY.
**Product baseline when authored:** nenoybot/v2 @ 805d2aafc709cb2323f228d791b16bb1f63f64b8.
**Audience:** two consenting members of one existing Telegram group; no rollout to other groups.
**Document discipline:** public repository; synthetic examples only; no personal chat data, real names or group IDs.

## 1. Product question and outcome

**Problem:** A person can explain familiar actions and recurring difficulties yet fail to imagine alternatives outside their established behavioral repertoire. Routine tips such as “be bolder”, “leave your comfort zone” or “try something new” do not help.

**North Star:** increase the number of *different, meaningful, voluntarily chosen responses* a participant can use in real situations.

**Behavioral value, not clinical claim:** identify a grounded habitual choice, offer one surprising, safe, concrete change in approach, let the participant try/decline, and learn what actually happened.

**P0 user story (selective autonomous + requested):** In one configured group's Reports topic, an author who granted **autonomous assessment AND unsolicited group-reply permission** can have a freshly submitted report evaluated without addressing the bot; output is SILENCE, ONE_QUESTION or ONE_EXPERIMENT, with silence preferred. Separately, a participant who granted **direct-request group-reply permission** can ask NeНой for a one-report analysis even while autonomous assessment is disabled or muted. Both modes need verified group/topic/author, approved public visibility and safety, but use different consent gates. No longitudinal inference or ongoing personal track.

**P1 user story (later, separately accepted):** Only after proven forum-topic provenance, separate participant consent and purpose-bound retention, analyze permitted history to identify a **source-backed recurring choice**, propose an alternative and learn from voluntary self-reports. The P1 aim to "analyze the whole chat" is **not** a P0 capability or permission.

**Non-goals:** personality diagnosis; treatment; forcing discomfort; ranking friends; public disclosure of sensitive life history; fabricated past or “never done before” claims; daily nagging; random novelty; new services, external data fetch or automatic chat import.

## 1.1. Release boundary: P0 is not P1

| Stage | Evidence allowed | Response | Hard boundary |
| --- | --- | --- | --- |
| P0-OFFLINE | Synthetic/anonymized fixtures only | Evaluate whether suggestions are concretely unusual and safe | No Telegram access or production claims |
| P0-LIVE (requires separate owner permit) | An opted-in author's **new current report** in the exact allowed topic even without mentioning the bot, OR their direct request | On reports: SILENCE / ONE_QUESTION / ONE_EXPERIMENT. On direct request: specific optional answer or clarification. One-off track snapshot only on request | No group HOT/LONG, third-party reply, longitudinal pattern, experiment ledger or week/month track; minimal consent/cooldown counters only under approved TTL |
| P1-HISTORY (future) | Explicit per-participant consent + group topic allowlist intersected with individual permitted topics; history with verified provenance | 2+ independent episodes for recurring choice; purpose-bound experiment ledger and on-demand weekly/monthly Personal Growth Track #166 | No analysis until topic-aware SQL retrieval, consent/revocation and retention tests pass |

**P0 behavior contract (intentional simplification):** Automatic one-report evaluation is allowed **after** separate author consent, but there is no persistent experiment ledger, completed/declined-history suppression, week/month personal track, or automatic progress tracking. A minimal scoped operational cooldown/mute/dedupe record is allowed only under an owner-accepted TTL. “Другой вариант” in a separate turn must include the earlier suggestion or current context in the user's own new message; otherwise NeНой asks to repeat the context. P0 may generate several alternatives internally in a **single** requested turn but shows one. A one-off snapshot must explicitly state “только по этому отчёту” and never present a 30-day history.

**P1 behavior contract (separate acceptance):** user-initiated ongoing track + limited experiment ledger may be supported after exact, purpose-bound consent, source retention, per-topic retrieval, forget/correction and queued-message revocation are implemented. Group owner cannot expand another participant's topic permissions; effective topics = group policy allowlist ∩ each participant's granted topics. No unsolicited outputs without later separate opt-in.

No stage may silently change the generic group profile. The existing Telegram ingest stores raw messages before the Group Dispatcher; the Reflection consent gate alone **does not prevent baseline collection**. Resolve disclosure and data minimization as a separate privacy requirement before P0-LIVE.

## 1.3. P0 selective proactive observation — replaces silence-first P0 rule

**Owner's product direction (2026-11-21):** A participant should not have to call the bot after every daily report. For an independently opted-in participant, the bot may examine **each newly arrived author-owned report** in the exact approved Telegram group/forum topic and decide whether intervention adds real value. **The default output is silence**; explicit on-demand reflection/AntiAutopilot remains available. Group-owned opt-in never overrides an individual's consent.

**Decision contract, in strict order:**

1. **Deterministic pre-LLM gates:** exact configured Telegram chat ID/topic ID, known author, authorized current-text event, active Reflection flag and versioned scope. **AUTO_REPORT:** require separate per-author `auto_report_analysis=true` AND `unsolicited_group_reply=true`, no autonomous mute/revoke, eligible new report, available *mandatory* per-day assessment budget. **DIRECT_REQUEST:** require independent `direct_request_group_reply=true`, correct author/request/topic and public-output permission, but DO NOT require autonomous flags or proactive cooldown. Unknown topic/actor, unconsented event or incorrect purpose -> no Reflection model call; non-Reflection groups keep existing behavior.
2. **Current report only:** one source message from the author, small allowlisted metadata. No historical chats, cross-topic HOT/LONG, personal conversations, third-party reply text or generic scene/mapper. A user quoting a third party does not authorize profiling that party.
3. **Opportunity evaluation:** determine if one concrete, safe, genuinely novel action or one precise question could reveal a missed alternative **in this report**. Candidate result = `SILENCE | ONE_QUESTION | ONE_EXPERIMENT`, with explainable reason code and a short expected learning value. Low relevance, generic praise, ungrounded psychology, ambiguous source, sensitive distress, or high stakes => SILENCE; never invent stable patterns or claim "never done".
4. **Mandatory pre-LLM assessment budget and hard quotas:** proactive mode CANNOT be enabled unless owner explicitly accepts non-null maximum **daily AI cost per target group** and **maximum assessment calls per person and group per 24h**, tracked for SILENCE decisions, failed calls and retries as well as published answers. Suggested initial trial caps (not yet authorized): **USD 0.50 per group per UTC day**, **3 assessments per person per UTC day**, **6 per group per UTC day**; no more than 1 billable evaluation per deduplicated inbound report. Reserve bounded estimated tokens/cost *before* invoking the model and reconcile measured usage; on stale/missing budget ledger or limit reached -> SILENCE without another model call. Independently, max **2 unsolicited public interventions/user/rolling 7 days**, >=**48h** apart and <=1/report. Group-wide noise cap may be tighter. Direct requests need their **own separately accepted per-day budget**, not the unsolicited-intervention cooldown. All caps are upper bounds, not performance targets.
5. **Operational state only:** To apply the above quotas, store minimal user/group/topic scoped `last_unsolicited_at`, count window, opt-in version, mute and event/outbox dedupe. This is NOT a longitudinal behavior ledger, psychological profile or permission to retrieve report bodies. Exact TTL and deletion/receipt contract must be accepted before build.
6. **Purpose-conditioned send-time veto:** every Reflection message carries `unsolicited=true|false`, source event, group/topic/author and purpose/consent version. For `unsolicited=true`, revalidate autonomous-analysis AND unsolicited-public-reply grants, proactive mute and quotas. For `unsolicited=false`, revalidate direct-request public-reply grant and scope, but **do not apply autonomous mute/cooldown or unsolicited consent**. Global stop/revocation or group feature OFF blocks both. Check immediately before send/retry including claimed leases; already-started Telegram HTTP cannot be retracted.
7. **Consent and visibility:** both passive AI assessment and occasional public intervention must be explained before each participant opts in. The regular bot already ingests some chat text outside Reflection; do not claim enabling/disabling this mode controls all baseline collection. Sensitive report -> do not post uninvited public psychological interpretation.
8. **No scheduled follow-ups:** group reports trigger evaluation when received, not a timer. No daily automatic challenges, no forced participation, no comparisons between friends. Individual can explicitly request an answer whenever desired.

**P0 quality acceptance is two-sided, question-aware:** freeze **12 valuable/low-risk cases**, provisionally labelled **6 QUESTION-EXPECTED and 6 EXPERIMENT-EXPECTED** by two independent reviewers. A ONE_QUESTION result passes when it poses a precise, grounded, non-leading and actionable learning/discovery question; it need not issue an experiment. A ONE_EXPERIMENT passes when it proposes a concrete, reversible, non-generic behavioral action with an observation question. Wrong-type output may pass only by documented agreement that it meets the *case-specific* learning objective, never just for sounding creative. Provisional overall threshold **>=9/12** and **>=4/6 within each intended type**; on 12 routine reports >=11 correct SILENCE, zero unauthorized/harmful suggestions in negative suite. NO_ACTION on eligible = miss; unsolicited generic chatter on routine = false positive. Owners must freeze version/thresholds before G4.

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
| BPM-08 | Consent and user autonomy | Separate per-person permissions for `direct_request_group_reply`, `auto_report_analysis` and `unsolicited_group_reply`. Autonomous mute does not disable direct requests. Global stop blocks both |
| BPM-09 | Personalization without labels | Do not infer disorders, character flaws, hidden motives or stable personality from group logs |
| BPM-10 | Sensitive-context safety | Distress, exhaustion, conflict or health concerns switch to care/no challenge; risky experiment blocked |
| BPM-11 | No repetitions | **P1**: avoid repeated completed/declined proposals via purpose-bound ledger; **P0**: within one bounded generation only, or compare explicitly supplied previous suggestion, never secretly load group history |
| BPM-12 | Optional self-report outcomes | **P1 only:** purpose-bound ledger distinguishes offered / accepted / tried_self_reported / not_tried_self_reported / declined / unknown / expired. **P0:** no durable self-report tracking or status inference |
| BPM-13 | Participant correction and forget controls | Explicit purpose-scoped correction/revocation, cessation of new retrieval, deletion contract for derived data and pending jobs; do not promise retroactive erasure from Telegram or backups |
| BPM-14 | Explainable source boundaries | Message IDs are not automatically verified clickable links; only cite actually authorized and validated evidence, with dates/limits when deep links cannot be verified |
| BPM-15 | Group and topic isolation | Do not leak Personal or other-group memory; do not mix Chat/Reports topics for unsupported comparisons |
| BPM-16 | Isolation from existing persona | Reflection Coach has no aggressive roast; other existing friends groups unchanged |
| BPM-17 | Graceful degradation | Missing history/LLM/store/permission -> transparent clarification or NO_ACTION, never fake memory |
| BPM-18 | Short Telegram default | 2–5 sentences; details only on request |
| BPM-19 | Collaborative exercise consent | No publicly assigned challenge for the other person without their own consent |
| BPM-20 | Controlled learning loop | **P1 only:** weekly review and Personal Growth Track #166 show proposed / tried / declined / observed with provenance; **P0:** one-off report-specific snapshot, no claimed longitudinal evidence |

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

A. Check either an explicit authorized request or a newly arrived report in exact approved group/topic from an **individually opted-in** author. **P0-LIVE must enter an exclusive route before generic Scene Analyzer, scheduling, reminders, memory mapping, statement watching, HOT/LONG retrieval or ordinary roast**. Build a new allowlisted packet with current authorized author text and safe metadata only; do not pass original event metadata wholesale, including `reply_to_text`. Denial returns a terminal NO_ACTION or a minimal consent-specific response; never fall back into generic generation.
B. Check sensitive context and known individual constraints. If unsafe/insufficient, use care, question or NO_ACTION.
C. P0: evidence is the user's own current addressed report only. If the user replies to **someone else's** message, do not pass that quoted text, original event metadata, scene/mapper inputs or earlier context to any Reflection LLM call. P1: SQL retrieval must use `group_id + permitted_topics_intersection + subject_id + time` with source IDs, actor and date; dedupe episodes, forwards and edits.
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

- **Automatic consideration:** an opted-in participant posts a normal report in the approved Reports topic; НеНой evaluates only this report and usually stays silent, exceptionally asks one useful question or offers one unusual experiment.
- **Ask:** direct mention or reply such as “НеНой, покажи необычный ход по этому отчёту” remains supported.
- **Request another:** “слишком банально, другой вариант” -> materially different transformation. P0 requires the earlier suggestion/current report to be provided again if no valid bounded current-request context; it does **not** silently retrieve history.
- **Reduce stakes:** “помягче” -> smaller reversible step; refuse unsafe variants.
- **Decline/mute:** “не сегодня / пропускаю / НеНой, не вмешивайся в мои отчёты” -> no shaming; stop autonomous evaluations and pending group replies for the user, with truthful baseline-data retention caveats.
- **Report:** “попробовал, вот что произошло” -> distinguish performed action from expected impact.
- **Review:** “что из нового я действительно попробовал за неделю?” -> **P1 only** after own ongoing-track consent and history storage. In P0, say that a weekly track is unavailable and offer a snapshot of the currently provided report.
- **Forget:** “не используй эту тему” / scoped forget -> do not surface the retired content in callbacks or future experiments.

## 8. Privacy, consent and data contract — decisions still required

- Bot owner consent is **not** consent from every group participant. Each participant must opt in for personal reports/patterns; scope and purpose disclosed in advance. **Cross-topic scope requires BOTH a group-level allowlist and that participant's explicit consent to every additional topic**; expanded owner-only permission never counts.
- Group memories are not allowed to pull from private conversations, other groups, private documents, or earlier chat exports without separate explicit opt-in.
- Group-specific messages can still include third parties: do not propose experiments involving non-consenting people in ways that burden or manipulate them.
- Separate **purpose-specific opt-in** from Telegram's *pre-existing* general bot ingest. Legacy `event_ingestor` currently persists `messages.text` and `events.payload` before Group policies. Decide and disclose baseline processing/retention instead of promising “nothing is stored until consent”.
- P0-LIVE may evaluate **only a freshly arrived report by an individually opted-in author** even without bot address, or that person's current explicit request. Never load other chat turns, generic HOT/LONG or third-party reply text for Reflection; direct-request consent and *continuous automatic analysis consent* must not be conflated.
- Telegram forum topic isolation must be implemented/tested before retrospective pattern analysis across reports. Current v2 group context is chat-scoped, with partial threaded fail-closed handling.
- Proposed minimum retained fields: authorized group/topic/user, consent version, evidence references (not secret raw quote), technique ID, suggestion ID, offered status, optional self-report, opt-out/forget state. Exact retention TTL, evidence deletion behavior, migration, source-link policy and base-bot data notice require owner decision at G2/G3.
- Forget semantics must account for raw `messages`, `events.payload`, `memory_cards`, `interventions.generated_text`, `outbox`, derived summaries and any pending jobs; backups and already delivered Telegram messages may not be erasable by the bot. It must never falsely confirm complete deletion.
- Neither a group admin nor a friend's encouragement replaces another person's explicit opt-in; a private/sensitive third-party disclosure is not a suitable source for public experiments.
- Existing MEMORY_SPEC's 30-day raw text policy is a reference default, not blanket authorization to store sensitive psychological narratives for 30 days or forever.
- This public repository must contain only invented/anonymized fixtures, no conversation export, Telegram IDs, personal medical facts or production credentials.

## 9. Acceptance and product quality

**Gate A: mode-specific privacy (mandatory):** obtain informed individual consent for the exact group/topic and group-visible output. **AUTO_REPORT** may analyze a passive report only with separate automatic-analysis and unsolicited-public-reply grants; otherwise no proactive Reflection model call or post. **DIRECT_REQUEST** may process the user's *own current request* with independent direct-request public-reply permission even when the autonomous flags are OFF or muted. Global Reflection stop/revocation blocks both. Group owner permission never replaces member consent.

**Gate B: synthetic functional tests:** run separate acceptance matrix in companion file; all privacy, safety and attribution negative tests must pass before human pilot. Include P0 context minimization, P1 topic/consent filtering, revoked consent with pending retries, and backup/Telegram deletion limits. No unsupported personal labels or claims of “never” are permitted.

**Gate C: value-sensitive positive benchmark (PROPOSED):** freeze 12 eligible safe P0 reports pre-labelled for **6 high-value questions + 6 concrete experiments**, with explicit case-level objective, plus 12 neutral/routine reports and 12+ unsafe/insufficient cases. Two independent reviewers assess source grounding, learning value, suitability of ONE_QUESTION vs ONE_EXPERIMENT, autonomy and proportionality. A useful specific question is a **positive success**, not a failed experiment; an action case requires a concrete reversible change. Provisional release target >=9/12 overall AND >=4/6 in each type, >=11/12 correct SILENCE on routine cases, **zero privacy/safety breaches**; reviewer disagreement fails until separately adjudicated. NO_ACTION on eligible = miss. No unvalidated psychological score; baseline comparison documented. Fix thresholds *before* running the benchmark.

**Gate D: controlled real pilot:** after explicit authorization, auto-opted-in users post reports and receive selective intervention; participants with **direct-request-only consent** may still request private-to-their-report group-visible analysis. The model budget is mandatory and includes silent evaluations; count assessment requests, token cost, replies, false interruptions and feedback over a 7-day trial. Record per proposal: useful/unhelpful, surprised/obvious, tried/declined, what was observed, annoyance, repeated suggestion. With only two users, do not present percentage claims as statistical evidence.

**Gate E: stop conditions:** LLM assessment of unconsented author, cross-topic input, false author attribution, unsafe suggestion, unauthorized proactive post, quota breach, ignored per-user stop/mute, fabricated pattern or post-revocation reply is a pilot STOP/REWORK trigger.

## 10. Dependencies and actual Project OS state

- G0 change intent is clear; exact product and privacy contract remain PROPOSED.
- G1 is PARTIAL: Draft PR #162 proposes non-default v2 governance trust model. It is not merged, independent review not verified and no compatible Steward is installed.
- G2 this v1.2 revision addresses **independent Codex findings from reviewed prior HEAD `87ffe1fd02c050fb64bcd5207edc2b9ce5bd2b0d`** but remains an unapproved owner-review candidate; **a fresh exact-HEAD independent review is required**, not a G6 PASS.
- G3 requires topic-aware evidence, memory minimization, consent model, and risk gates before code.
- G4–G5: BLOCKED_FOR_BUILD by current Project OS policy; no CI activation, app changes, DB migrations, bot runtime changes or production updates.

**Recommended safe next decision:** independent review of P0 v1.3 proactive requirements + matching G3; owner decides exact topic, per-person automatic-analysis and public-reply permission, pilot frequency, logging/retention and stop controls. P1 track #166 is separate. No code/merge/deployment.
