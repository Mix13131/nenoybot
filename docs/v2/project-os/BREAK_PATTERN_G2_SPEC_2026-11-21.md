# G2 Product Specification — «АнтиАвтопилот» / Break Pattern Engine

**Status:** REWORK v1.6 / REVIEW_REQUIRED. Independent Codex review of memory-first v1.5 found P1 gaps in archive-independent ingestion and source authorization at delivery. This revision addresses both in specification; it has not been independently verified. No G8/G9 or production permission.
**Date:** 2026-11-21
**Request:** Issue #163 (https://github.com/Mix13131/nenoybot/issues/163)
**Parent:** Reflection Coach #161 (https://github.com/Mix13131/nenoybot/issues/161)
**Related:** on-demand Personal Growth Track #166 (https://github.com/Mix13131/nenoybot/issues/166) — history-backed on demand when authorized archive/retrieval is available; no hidden tracking beyond consent.
**G1:** Draft PR #162 (https://github.com/Mix13131/nenoybot/pull/162), PARTIAL / ADVISORY.
**Product baseline when authored:** nenoybot/v2 @ 805d2aafc709cb2323f228d791b16bb1f63f64b8.
**Audience:** two consenting members of one existing Telegram group; no rollout to other groups.
**Document discipline:** public repository; synthetic examples only; no personal chat data, real names or group IDs.

## 1. Product question and outcome

**Problem:** A person can explain familiar actions and recurring difficulties yet fail to imagine alternatives outside their established behavioral repertoire. Routine tips such as “be bolder”, “leave your comfort zone” or “try something new” do not help.

**North Star:** increase the number of *different, meaningful, voluntarily chosen responses* a participant can use in real situations.

**Behavioral value, not clinical claim:** identify a grounded habitual choice, offer one surprising, safe, concrete change in approach, let the participant try/decline, and learn what actually happened.

**Initial functional user story (memory-first):** In one configured Telegram group, each consenting participant authorizes relevant topic(s) and durable history retention. The bot stores the **complete received permitted message/event history** with actor, topic, source and edits (not only current reports), retrieves only **relevant** evidence for analysis, and autonomously decides whether to contribute a useful question or unconventional experiment after new reports. Participants can also address it on demand and request a grounded personal trajectory. There are **no numerical limits on useful replies or 48-hour cooldown**. Permissions to store, analyze history, post proactively and reply to direct requests are distinct; no permanent psychological diagnosis.

**Later enhancements (P1):** higher-quality cross-week pattern models, optional experiment ledger, enhanced analytics and additional topic coverage. **History capture and source-backed retrieval are no longer P1-only**; they are prerequisites of the initial memory-first version. Importing older pre-activation Telegram exports is a separate authorized operation, not an automatic Bot API capability.

**Non-goals:** personality diagnosis; treatment; forcing discomfort; ranking friends; public disclosure of sensitive life history; fabricated past or “never done before” claims; daily nagging; random novelty; new services, external data fetch or automatic chat import.

## 1.1. Release boundary: P0 is not P1

| Stage | Evidence allowed | Response | Hard boundary |
| --- | --- | --- | --- |
| P0-OFFLINE | Synthetic/anonymized fixtures only | Evaluate whether suggestions are concretely unusual and safe | No Telegram access or production claims |
| INITIAL-LIVE (requires separate owner/participant permission) | All **newly received and individually permitted** messages in approved topics of one group, with author/time/topic/provenance; optional authorized prior export | On new reports choose SILENCE / ONE_QUESTION / ONE_EXPERIMENT; direct questions, evidence-backed earlier-context recall, **on-demand** personal trajectory | No unconsented author/topic, no unverified continuity claims, no all-history dump to LLM, no numerical reply quota; no indefinite sensitive inference labels |
| P1-ENHANCEMENTS (later) | Established, consented source archive and its verified coverage | Optional experiment ledger, deeper long-horizon pattern analysis and quality improvements | Must not claim additional history beyond actual ingested/exported coverage |

**Initial memory contract:** Permanently recallable (within accepted retention) **source archive** of actually received, authorized messages with exact provenance; indexed retrieval, source-backed observations and time-bounded report coverage. Do not cap history at 30 days for opted-in archived messages. Long-term semantic cards may be summarized, corrected or superseded, but must not replace the source archive or invent absent messages. A user saying “другой вариант” may invoke **authorized sourced prior suggestions**, not an invented answer. On-demand 7/30-day track may describe **only periods with verified data**. The chat archive is not to be pasted wholesale into the model prompt.

**Later P1 contract:** derive richer trajectories and experiment statuses only from already authorized/verified history; no unsolicited personal scoreboard. Group's topic allowlist is intersected with each author's individual grants. All new purposes require separate permission.

No stage may silently change the generic group profile. The existing Telegram ingest stores raw messages before the Group Dispatcher; the Reflection consent gate alone **does not prevent baseline collection**. Resolve disclosure and data minimization as a separate privacy requirement before P0-LIVE.

## 1.2. Owner-required memory and intervention policy (supersedes prior limited-memory design)

**Meaning of “remember everything”:** faithfully archive **every message actually received from consenting authors in authorized topics**, retaining message text/source, edit/delete events, timestamps, reply links, topic ID and coverage gaps under a separately accepted retention policy. The archive is not an infinitely large LLM context. HOT/WARM/semantic summaries and targeted evidence retrieval must point back to originals and distinguish fact, hypothesis, correction and uncertainty. Source expiry of the approved archive must not silently follow today's generic 30-day maintenance default.

**Historical completeness:** bot API update delivery is not a general chat-history export: prior, lost or already expired messages are **not assumed recovered**. Optional Telegram Desktop export import requires explicit author/group permissions, provenance/hash checks and a no-leak test. The user can ask “какие даты/темы/авторы у тебя есть?”, and the bot must state the actual coverage.

**No answer quota:** withdraw `2 unsolicited messages per person/week`, `48h cooldown`, any per-day response count, and the previously proposed `3 assessments/person/day`, `6 assessments/group/day` caps. Decide for each new authorized report whether the response adds value; dedupe same event, suppress repetition, respond to direct requests, and honor opt-out. Several genuinely useful consecutive reports may each receive an answer. Unlimited **response count** does not mean unlimited provider throughput or spending: track usage/cost, surface warnings and use an owner-accepted exceptional financial runaway breaker, not an arbitrary silence quota.

**Privacy:** separate author/topic-specific consent to long-lived archive, history-backed inference, public proactive comments and on-demand replies; do not store/quote non-consenting authors' material in long-lived Reflection archive. The pre-existing bot's ambient message capture needs disclosure and review. Allow scoped correction, consent revocation, archive deletion where feasible and truthful backup/Telegram-message limitations.

**Acceptance direction:** tests must cover 30+ day recall across authorized topics, per-user chronology, edited/deleted sources, source citation, misses/retention status, on-demand track, multiple genuinely useful reports in rapid succession, no numeric reply cap, proper silence on low-value reports, dedupe and cost/anomaly safety controls.

## 1.2.1. Source archiving MUST be independent of AI intervention

**Two independent lanes, not a single flow.** Ingest every new message/update that satisfies the **archival** group/topic/author grant into a durable source store **even if** (a) the author rejects autonomous coaching, (b) the message is a non-report status, attachment/caption, reply/edit, or ordinary conversation, (c) the language model chooses SILENCE, or (d) AI quota, network or spend breaker is unavailable. Do not place the source archive behind report classification, generation, public output consent or an LLM/paid worker. A missing author/topic grant produces no Reflection archival copy; the pre-existing raw ingest remains a separately disclosed concern.

For every source, record exact author/topic/event/message IDs, original/revision timestamps, reply relationship, content kind, consent version, durable source availability and coverage gaps. A Telegram media message without retrievable/described media bytes must be represented truthfully by metadata/coverage, not falsely “remembered” in full. Avoid saving nonconsenting third-party quoted text as evidence just because an opted-in author forwarded it.

**Evaluation is a separate consumer** of that archive, with its own purpose grants, safety/value gate, selected evidence and cost budget. Free source ingestion must continue during any AI downtime. An archive-receipt must not assert that the model analyzed or understood the message.

**Historical answer send-time rule:** both unsolicited **and explicitly requested** output carry the exact source event/version IDs that supported generated claims. Immediately before each send/retry, revalidate **every cited/used evidence source** against its author's current archival/historical-use consent, approved topic, correction, forget/delete status and current feature scope. If any evidence was revoked/deleted or made unavailable, suppress and regenerate only after a fresh authorized read; never ship a stale queued answer just because direct reply consent still exists. Direct answers continue to ignore autonomous mute, but not source revocation. Tests cover queued 30-day review after the participant withdraws access to one historical topic.

## 1.3. P0 selective proactive observation — replaces silence-first P0 rule

**Owner's product direction (2026-11-21):** A participant should not have to call the bot after every daily report. For an independently opted-in participant, the bot may examine **each newly arrived author-owned report** in the exact approved Telegram group/forum topic and decide whether intervention adds real value. **The default output is silence**; explicit on-demand reflection/AntiAutopilot remains available. Group-owned opt-in never overrides an individual's consent.

**Decision contract, in strict order:**

1. **Deterministic analysis pre-LLM gates (NOT archival eligibility):** exact configured Telegram chat ID/topic ID, known author, authorized current-text event, active Reflection flag and versioned scope. **AUTO_REPORT:** require separate per-author `auto_report_analysis=true` AND `unsolicited_group_reply=true`, no autonomous mute/revoke, eligible new report, available *mandatory* per-day assessment budget. **DIRECT_REQUEST:** require independent `direct_request_group_reply=true`, correct author/request/topic and public-output permission, but DO NOT require autonomous flags or proactive cooldown. Unknown topic/actor, unconsented event or incorrect purpose -> no Reflection model call; non-Reflection groups keep existing behavior.
2. **Sourced history-aware analysis:** current author's report + **only relevant, pre-filtered permitted archive evidence**, with allowed author/topic/time/message IDs; no cross-group/Personal material or other person's unconsented texts. SQL/authorization gates must run before any retrieval text reaches an LLM. Show missing source coverage honestly; no raw full-history dump.
3. **Opportunity evaluation:** determine if one concrete, safe, genuinely novel action or one precise question could reveal a missed alternative **in this report**. Candidate result = `SILENCE | ONE_QUESTION | ONE_EXPERIMENT`, with explainable reason code and a short expected learning value. Low relevance, generic praise, ungrounded psychology, ambiguous source, sensitive distress, or high stakes => SILENCE; never invent stable patterns or claim "never done".
4. **No reply-count limits:** evaluate every newly received authorized report, choose relevance/safety rather than fixed user/time quotas. Keep event dedupe, provider anti-abuse/rate limits, cost measurement and an explicit owner-approved *exceptional financial circuit breaker* against runaway API spending. No preselected `$0.50/day`, `3 assessments/day`, `6/day`, `2 replies/week` or `48-hour` policy. When an operational breaker triggers, state service unavailability accurately, do not invent memory or pretend to have analyzed the message.
5. **Independent archival state and semantic memory:** archive all newly received source events allowed by archival consent **before and independently of any eligibility/AI decision**. Store topic/author/version/time/reply IDs; derive sourced memories only under separate historical-inference permission. Do not require intervention eligibility, unsolicited consent or model availability for archival. Maintain retractable observations and experiment outcomes without diagnosing a person. Archive retention, correction, visibility, forget and backups require explicit individual/owner acceptance.
6. **Purpose + source-conditioned send-time veto:** every Reflection reply carries `unsolicited=true|false`, exact group/topic/subject, **all used source-version IDs**, authorized-purpose and consent version. For unsolicited replies, revalidate analysis/public-reply grants and mute; for direct replies, direct-scope/public-output grant only (auto mute irrelevant). **For BOTH**, independently revalidate source owner/topic historical-use grants and deletion/correction status of EVERY used source just before sending any queued/claimed/retried item. Global stop or feature OFF blocks both. No reply-count quota. If a used source becomes disallowed, cancel/regenerate with fresh evidence; never send stale sensitive text. Telegram HTTP already in flight cannot be retracted.
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
| BPM-11 | No repetitions | Avoid reoffering same completed/declined idea using authorized sourced history and voluntary feedback; respect corrections and refusals without arbitrary frequency caps |
| BPM-12 | Optional self-report outcomes | With opt-in preserve purpose-bounded status offered/accepted/tried_self_reported/declined/unknown, accurately separating self-report from observed outcome |
| BPM-13 | Participant correction and forget controls | Explicit purpose-scoped correction/revocation, cessation of new retrieval, deletion contract for derived data and pending jobs; do not promise retroactive erasure from Telegram or backups |
| BPM-14 | Explainable source boundaries | Message IDs are not automatically verified clickable links; only cite actually authorized and validated evidence, with dates/limits when deep links cannot be verified |
| BPM-15 | Group and topic isolation | Do not leak Personal or other-group memory; do not mix Chat/Reports topics for unsupported comparisons |
| BPM-16 | Isolation from existing persona | Reflection Coach has no aggressive roast; other existing friends groups unchanged |
| BPM-17 | Graceful degradation | Missing history/LLM/store/permission -> transparent clarification or NO_ACTION, never fake memory |
| BPM-18 | Short Telegram default | 2–5 sentences; details only on request |
| BPM-19 | Collaborative exercise consent | No publicly assigned challenge for the other person without their own consent |
| BPM-20 | Controlled learning loop | On-demand Personal Growth Track #166 uses only existing permitted history and evidence coverage; no fabricated improvements or unsolicited psychological rating |

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

A. Check direct/automatic purpose permissions, source-archive consent and exact group/topic/author. Reflection routing must precede generic Group LLM/action/memory side effects. Build an allowlisted current-report PLUS **selected relevant archived authorized source** context; never use generic chat-wide HOT/LONG, Personal memory or unconsented quoted reply text. Denial terminates without generic fallback.
B. Check sensitive context and known individual constraints. If unsafe/insufficient, use care, question or NO_ACTION.
C. Evidence = current author's report plus SQL-filtered **authorized historical source events** by exact group, consented topics, author(s), time and provenance. A reply to someone else's message never implies consent to use their content. Dedupe copied/forwarded episodes and edits; only 2+ independent episodes support a recurring-choice claim.
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
- **Request another:** “слишком банально, другой вариант” -> materially different transformation; retrieve a prior authorized suggestion and its response only when provenance and access permit, otherwise ask for the missing context.
- **Reduce stakes:** “помягче” -> smaller reversible step; refuse unsafe variants.
- **Decline/mute:** “не сегодня / пропускаю / НеНой, не вмешивайся в мои отчёты” -> no shaming; stop autonomous evaluations and pending group replies for the user, with truthful baseline-data retention caveats.
- **Report:** “попробовал, вот что произошло” -> distinguish performed action from expected impact.
- **Review:** “что из нового я действительно попробовал за неделю?” -> on-demand source-backed review of **actually available authorized** reports/experiments; disclose dates/topics missing. Do not say it knows the entire month when earlier history was unavailable.
- **Forget:** “не используй эту тему” / scoped forget -> do not surface the retired content in callbacks or future experiments.

## 8. Privacy, consent and data contract — decisions still required

- Bot owner consent is **not** consent from every group participant. Each participant must opt in for personal reports/patterns; scope and purpose disclosed in advance. **Cross-topic scope requires BOTH a group-level allowlist and that participant's explicit consent to every additional topic**; expanded owner-only permission never counts.
- Group memories are not allowed to pull from private conversations, other groups, private documents, or earlier chat exports without separate explicit opt-in.
- Group-specific messages can still include third parties: do not propose experiments involving non-consenting people in ways that burden or manipulate them.
- Separate **purpose-specific opt-in** from Telegram's *pre-existing* general bot ingest. Legacy `event_ingestor` currently persists `messages.text` and `events.payload` before Group policies. Decide and disclose baseline processing/retention instead of promising “nothing is stored until consent”.
- Initial live mode may evaluate a consented new report and retrieve **historical authorized evidence** in the same group/topic coverage or participant-approved additional topics. Strictly exclude unconsented/Personal/other-group messages and unrelated quoted reply text; direct-request permission and autonomous-analysis/archive grants remain separate.
- Telegram forum topic isolation must be implemented/tested before retrospective pattern analysis across reports. Current v2 group context is chat-scoped, with partial threaded fail-closed handling.
- Proposed minimum retained fields: authorized group/topic/user, consent version, evidence references (not secret raw quote), technique ID, suggestion ID, offered status, optional self-report, opt-out/forget state. Exact retention TTL, evidence deletion behavior, migration, source-link policy and base-bot data notice require owner decision at G2/G3.
- Forget semantics must account for raw `messages`, `events.payload`, `memory_cards`, `interventions.generated_text`, `outbox`, derived summaries and any pending jobs; backups and already delivered Telegram messages may not be erasable by the bot. It must never falsely confirm complete deletion.
- Neither a group admin nor a friend's encouragement replaces another person's explicit opt-in; a private/sensitive third-party disclosure is not a suitable source for public experiments.
- Existing MEMORY_SPEC's 30-day raw text policy is a reference default, not blanket authorization to store sensitive psychological narratives for 30 days or forever.
- This public repository must contain only invented/anonymized fixtures, no conversation export, Telegram IDs, personal medical facts or production credentials.

## 9. Acceptance and product quality

**Gate A: mode-specific privacy (mandatory):** obtain informed individual consent for the exact group/topic and group-visible output. **AUTO_REPORT** may analyze a passive report only with separate automatic-analysis and unsolicited-public-reply grants; otherwise no proactive Reflection model call or post. **DIRECT_REQUEST** may process the user's *own current request* with independent direct-request public-reply permission even when the autonomous flags are OFF or muted. Global Reflection stop/revocation blocks both. Group owner permission never replaces member consent.

**Gate B: memory-first tests:** verify >30-day archive and **archive-only authors / routine non-report messages / no-model and spend-outage ingestion** independently of LLM or output consent. Verify edit/version lineage, topic/author/consent retrieval, source coverage gaps, corrections/forget, backups, and no numerical reply cap. For each queued response, check that all used historical source versions are still authorized before send/retry, including a direct request whose archived topic was revoked. No unsupported personality labels.

**Gate C: value-sensitive positive benchmark (PROPOSED):** freeze 12 eligible safe P0 reports pre-labelled for **6 high-value questions + 6 concrete experiments**, with explicit case-level objective, plus 12 neutral/routine reports and 12+ unsafe/insufficient cases. Two independent reviewers assess source grounding, learning value, suitability of ONE_QUESTION vs ONE_EXPERIMENT, autonomy and proportionality. A useful specific question is a **positive success**, not a failed experiment; an action case requires a concrete reversible change. Provisional release target >=9/12 overall AND >=4/6 in each type, >=11/12 correct SILENCE on routine cases, **zero privacy/safety breaches**; reviewer disagreement fails until separately adjudicated. NO_ACTION on eligible = miss. No unvalidated psychological score; baseline comparison documented. Fix thresholds *before* running the benchmark.

**Gate D: controlled real pilot:** after explicit authorization, auto-opted-in users post reports and receive selective intervention; participants with **direct-request-only consent** may still request private-to-their-report group-visible analysis. The model budget is mandatory and includes silent evaluations; count assessment requests, token cost, replies, false interruptions and feedback over a 7-day trial. Record per proposal: useful/unhelpful, surprised/obvious, tried/declined, what was observed, annoyance, repeated suggestion. With only two users, do not present percentage claims as statistical evidence.

**Gate E: stop conditions:** long-lived archive of unconsented author, cross-topic leak, wrong author, fabricated source/continuity, unsafe suggestion, unauthorized proactive post, ignored stop/revocation or loss of data integrity are STOP/REWORK triggers. **High but relevant reply count alone is not a defect**.

## 10. Dependencies and actual Project OS state

- G0 change intent is clear; exact product and privacy contract remain PROPOSED.
- G1 is PARTIAL: Draft PR #162 proposes non-default v2 governance trust model. It is not merged, independent review not verified and no compatible Steward is installed.
- G2 v1.5 is a new owner memory-first scope; older independent reviews on report-only v1.4 cannot certify it. Requires exact-HEAD review and separate owner acceptance.
- G3 requires topic-aware evidence, memory minimization, consent model, and risk gates before code.
- G4–G5: BLOCKED_FOR_BUILD by current Project OS policy; no CI activation, app changes, DB migrations, bot runtime changes or production updates.

**Recommended safe next decision:** re-review memory-first G2/G3 v1.5 and G4 #167, then accept source archive retention/forget, author/topic consent, optional old-export import, accurate data coverage and financial emergency controls. No code/merge/deployment.
