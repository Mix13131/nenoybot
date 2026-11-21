# G3 CONDITIONAL ARCHITECTURE — Reflection Coach + Antiautopilot

**Status:** `OWNER CHANGE REQUEST v1.5 / NOT BUILD-READY`. Full authorized history and no numerical reply quotas supersede reviewed single-report P0. Needs new architecture review and permission to change source retention/schema.
**Date:** 2026-11-21
**Parent:** [Reflection Coach #161](https://github.com/Mix13131/nenoybot/issues/161)
**Feature:** [Antiautopilot #163](https://github.com/Mix13131/nenoybot/issues/163) + [Personal Growth Track #166](https://github.com/Mix13131/nenoybot/issues/166) (history-backed **on demand** when authorized archive available)
**G2:** [Draft PR #164](https://github.com/Mix13131/nenoybot/pull/164) memory-first change request; earlier P0 report-only and later P1-history split withdrawn.
**G1:** [Draft PR #162](https://github.com/Mix13131/nenoybot/pull/162) — non-default `v2` Project OS trust unresolved.
**Observed product base:** `nenoybot/v2@805d2aafc709cb2323f228d791b16bb1f63f64b8` (must recheck when implementation starts).
**This file:** conditional architecture exploration for owner and external reviewer; not authority to alter runtime, CI, DB or production.

## 1. Problem, result and scope

Users post in one Telegram group. With individually authorized long-lived history, НеНой can archive every actually received permitted message and recover relevant past sources to compare behavior, plans and attempts. It autonomously evaluates fresh reports and may offer a useful question/experiment with **no artificial reply-count or 48-hour cap**; direct requests remain separate and valid. On-demand track summarizes only verified stored history.

Avoid (1) unsolicited psychotherapy, (2) random “get out of comfort zone” tasks, (3) false “you have never done X” claims, (4) other-person attribution, (5) cross-topic/private memory contamination, (6) pressure to comply.

**North Star:** increased behavioral options demonstrated by voluntary real-life experimentation, not frequency of model output or amount of discomfort.

**Product acceptance is separate:** both participants can independently consent to their own history. Agreement by the group owner cannot authorize personal profiling of the other person.

## 2. Source-backed inspection / existing reuse

| Component | In `v2` today | Reuse or modification |
| --- | --- | --- |
| Telegram webhook `app_v2/adapters/telegram_webhook.py` | Event metadata already captures `message_thread_id` | Reuse, extend normalization to explicitly distinguish known forum topic vs unknown/general |
| Ingest `event_ingestor.py` + `ingest_repo.py` | Persists message text and event payload **before** Group Dispatcher | Existing baseline retention is distinct from Reflection consent; add purpose-bound eligibility before Reflection indexing and consider ingest minimization in scoped group |
| `messages` / `message_repo.py` | Raw source text currently cleared after ~30 days and group retrieval chat-wide, lacking persisted topic | Initial launch requires durable source archive for **every received permitted message**, topic/author/time/versions/evidence, verified scoped retrieval and retention override; migration/backup/privacy review mandatory |
| `events.payload` | Includes normalized `metadata.message_thread_id` and text | Can serve bounded audit source, not an indefinitely retained longitudinal index |
| `memory_cards` + `retrieval_engine.py` | Scope separation but unproven source provenance per topic | Build sourced, correctable episodic/semantic memory and retrieve only relevant consented evidence; never paste all history into prompt |
| `connector_repo` and `connector_presets.py` | Versioned group connector and isolated role/profile features available | Use a dedicated opt-in reflection profile; do not change default friends persona |
| `group_pipeline.py` + Dispatcher | Existing event and Group pipelines; current no-mention reports may enter generic initiative/memory handlers | Enter exclusive Reflection before generic handlers for authorized topic messages; distinguish passive report and direct request; group/topic/individual opt-in and cooldown before analysis; terminal SILENCE or reply, no generic fallback |
| `telegram_sender.py` / outbox worker | Existing sender accepts `message_thread_id`; outbox worker currently handles generic send | Reuse only with Reflection-specific pre-send authorization check for claimed/retried output and test correct-topic delivery; no claim to cancel an already-started Telegram HTTP request |
| `llm_usage` | Existing per-call token, latency and cost telemetry | Reuse; no third-party analytics product or new infrastructure |
| `maintenance_repo.py` | Existing raw retention/cleanup ~30 days, events/outbox separately | Approved archived messages must not be silently purged after 30 days; explicit scoped retention, integrity/backup, revoke/forget and restoration tests; other groups unaffected |
| Group Lab `app_v2/group_lab.py` / Task 37 | Existing synthetic/sanitized replay infrastructure; JSON-import path, not automatic HTML history import | Reuse for synthetic quality/evidence tests, **not** as permission to upload actual Telegram exports |

**Source limitations:** architecture based on static code inspection. Independent Codex reviewed the **previous G3 HEAD** with three P1 findings, now addressed in this draft; current revision still needs exact-HEAD review. No trusted Project OS Context Pack, Telegram runtime test, production query or implementation tests were executed.

## 3. Architecture options

| Option | Benefit | Weakness | Decision |
| --- | --- | --- | --- |
| A. Add “be unusual” instructions to generic `group_generator.md` | Very cheap and quick | Roast-first personality, chat-wide context and no evidence/consent guarantee | REJECT |
| B. Dedicated `reflection_coach_v1` route, evidence/consent policy, structured candidate + deterministic release guard in existing v2 runtime | Isolated, auditable, reversible, can reuse Postgres/outbox/model routing | Requires focused topic/memory migration, bounded new state | **RECOMMENDED / PROPOSED** |
| C. New bot, separate AI agent server, new database | Isolated deployment | Unnecessary overhead, duplicate memory, split product | REJECT for P0 |

## 3.1. Codex G3 review findings now in scope

The independent Codex review on previous HEAD `658bdab29b07191f9228f1cd91f4c046c7ab6f2d` identified three P1 issues:

- G3-REV-01: an exclusive Reflection route **before** generic action/reminder, statement/humanity, memory and ordinary Group Generator, not merely a prompt before the final roast.
- G3-REV-02: a short **P0 experiment first**, without topic-history persistence/retrieval migrations before pilot; move longitudinal ledger, monthly track and weekly review to separate P1 authorization.
- G3-REV-03: consent/feature flag **recheck at outbox send time**, including claimed processing leases and late generation; cancel before send and document impossible-to-revoke already-started Telegram requests.
- G3-REV-04 (Codex proactive v1.3): **purpose-sensitive delivery checks**: autonomous mute must not block a separately authorized direct-request response.

These are design corrections, not implemented code or G6 PASS. The revised HEAD must be independently reviewed again.

## 3.2. Owner-required memory and intervention model

The **initial release** now requires a durable source archive of **all actually received messages from consenting authors in approved group topics**. "Remember all" does **not** mean posting raw history to the LLM or inferring truth from unrecorded periods. Archive each authorized message's author, timestamp, topic, update/message ID, edit/delete version, reply chain and provenance. Ensure the authorized archive is exempted from existing 30-day raw-text cleanup only through a reviewed retention/consent plan; other groups retain current policy. Semantic summaries and Memory Cards reference original events and can be corrected/deleted independently.

Telegram Bot API supplies only new updates, not an arbitrary old-history listing. Prior, missed, or already expired messages require a **separately permitted Telegram Desktop export and verified import**, otherwise provide a truthful coverage report. No real chat archive is allowed in public GitHub.

**No fixed count or time limit on useful answers:** replace 2/week, 48-hour cooldown and fixed assessments/day with value/novelty/safety and user feedback gates, event dedupe and anti-spam semantic repeat checks. Model/provider limits and an owner-approved **financial emergency stop** protect infrastructure without intentionally suppressing useful replies after some arbitrary number.

Every participant has distinct opt-ins to long-lived source retention, historical inference, autonomous observation/public answers and on-demand answers, scoped by topic; owner group permission is not a substitute. Revocation and deletion propagate to source and derived indexes under documented backup/Telegram limits.

## 4. Request path and trust boundaries

```text
Telegram new update -> exact group/topic/actor and consent gate
     |
     +-- unauthorized source -> no Reflection archival profiling
     |
     v
Durable topic-aware source archive (all received permitted text/edits,
author, timestamp, reply source, source ID; coverage/integrity recorded)
     |
     v
Source-backed memory cards, summaries, author/topic retrieval index
     |
     +-- Direct request / Personal Growth Track (on demand)
     |      -> retrieve only relevant authorized evidence, cite dates
     |
     +-- New report -> assess source-grounded opportunity
            +-- no added value -> SILENCE
            +-- real value -> ONE_QUESTION / ONE_EXPERIMENT
                    |
                    v
          Topic-safe consent-versioned outbox
                    |
                    v
           Per-purpose pre-send/retry grant check
                    |
                    v
                Telegram reply
```
Important: **all Reflection model invocations** must receive only a newly constructed, allowlisted P0 input. Neither the raw `EventEnvelope` (its `metadata.reply_to_text` may include third-party text), a generic scene, ordinary HOT/WARM/LONG, nor related group Memory Cards may reach the classifier, mapper, candidate generator or final response path. Context minimization solely in the final generator is too late. Replying to a third party's report does **not** by itself authorize analysis of that third person's content.

Other groups retain ordinary routing. In the configured Reports topic, **eligible passive reports route to selective evaluation**; unauthorized, routine or low-value reports produce terminal SILENCE (no fallback to generic group LLM). Unrelated explicitly authorized commands can use the existing path, but Reflection report text containing future dates must never turn into a generic task or reminder. The user can still directly address the bot.

## 5. Forum-topic and provenance contract

Source identity is an internal tuple:
`(chat_id, topic_scope, topic_id?, telegram_message_id, author_user_id, event_id, occurred_at)`.

- `topic_scope` has explicit values `TOPIC`, `GENERAL`, `UNKNOWN`, `NOT_APPLICABLE`. Never silently equate missing `message_thread_id` to General; normalize from the actual Telegram update and explicit `is_topic_message`/chat forum context.
- A forwarded excerpt or reply describing the *same real-world event* is a single possible episode, even if there are multiple message IDs. Edits supersede original observation instead of adding evidence.
- Keep origin `self_report`, `group_message`, `forwarded_quote`, `bot_generated`, `inferred` explicit. A bot suggestion is not independent proof of user behavior.
- `pattern` may be called recurring only when 2+ genuinely distinct episodes by the same authorized participant support the same observed action. This is a **minimum**, not automatic permission to diagnose or generalize “always”.
- Text retrieval must filter `chat_id + topic_scope/topic_id + consenting author + time/window` **in SQL** before passing source excerpts to any model. An intentionally accepted cross-topic review must explicitly list topics; no implicit `*all topics*` fallback.
- LONG memory is not topic-safe by default. Until topic-tagged evidence retrieval exists, Reflection route excludes general group Memory Cards / related-card expansion.
- Telegram message ID proves the bot saw an event, **not** that it remains visible or that a fabricated deep link works. Use a bounded internal evidence pointer; show clickable URL only when link construction and permission are validated, otherwise provide date/short source description without a misleading link.
- If topic/source is absent, fail closed for historical pattern claims. A *single currently addressed report* may still yield a clearly qualified exploratory suggestion, not a “life pattern”.

Telegram Bot API has native `message_thread_id` on messages and sends; no separate Telegram message-scraper infrastructure is required.

## 6. Data model changes to design, not to execute

**Initial memory-first build requires topic-aware durable source persistence, author consent, source-complete retrieval and retention change.** The previous no-migration shortcut is withdrawn. Any additive schema/index change or import of old exports is separate reviewed scope requiring explicit acceptance; don't change current production cleanup until authorized.

### 6.1 Archived `messages` topic/source metadata (**initial launch requirement**, proposed)
- `message_thread_id BIGINT NULL` (stored only when reported);
- `topic_scope TEXT NOT NULL` (`TOPIC | GENERAL | UNKNOWN | NOT_APPLICABLE`);
- index on `(chat_id, topic_scope, message_thread_id, user_id, created_at)` where appropriate;
- do not assign existing historical rows a guessed topic: mark `UNKNOWN`, exclude from topic-sensitive inference, or import only through separately reviewed owner/participant consent.

### 6.2 Reflection consent (P0+P1, proposed)
Use a purpose/version-specific consent record, either a small `reflection_consents` table or a deliberately isolated versioned `chat_members.participant_profile` key. Prefer a **small table** if status, scope changes and revocations require durable audit/atomic lookup.

Minimum consent fields: `chat_id`, `user_id`, exact permitted topic IDs, version, `direct_request_group_reply`, `auto_report_analysis`, `unsolicited_group_reply`, `autonomous_muted`, `global_revoked`, `consented_at`, `revoked_at`. These are **separate purpose grants**. Missing or unverifiable consent => deny. No personality labels, no group-owner consent in place of individual permission.

**Pre-ingest issue:** base bot already records ambient messages from new groups. Reflection consent gates must not be presented as if they prevent the original bot's collection. Approving Reflection means separately agreeing on the underlying bot's data notice and minimizing old raw text where feasible. If strong “no storing before opt-in” is required, design an ingestion change as a separate security task.

### 6.3 Derived experiment state and on-demand Personal Growth Track (**initial memory-first design**, proposed)
Scoped derived experiment/status records with explicit source IDs and correction/forget rules. Personal Growth Track uses only authorized source timeline **on request**; advanced behavior modeling can remain later:
- `proposal_id`, `group_id`, `topic_scope/id`, `subject_user_id`;
- `created_by_request_event_id` unique (idempotent), `strategy_kind`, `source_event_ids`;
- `status=offered|declined|accepted|tried_self_reported|not_tried_self_reported|expired|retired`;
- `first_time_confirmed` **only if self-reported**;
- `created_at`, `expires_at`, `retired_at`;
- optional minimal answer `what_surprised` only with explicit consent; redact sensitive narrative and apply agreed TTL;
- never infer success from `offered`, `accepted` or absence of refusal.

**Data deletion:** only promised to the extent each affected store and backup can truly be handled. Scope all reflection-specific state and allow participant correction/retire/delete. Audit deletion across memory, proposals, any derived summaries, pending jobs and generated outbox; explain limits for previously published Telegram messages and backups. Existing general `events`/raw retention remains a separate obligation, not implicitly solved by deletion of experiment record.

## 7. Generator and decision contracts (not code)

**Initial intents:** `AUTO_REPORT_EVALUATION | REFLECTION_ASK | ANTI_AUTOPILOT_ASK | ALTERNATIVE_ASK | LOWER_INTENSITY | DECLINE | MUTE_AUTONOMOUS | REVIEW_WEEK_MONTH | SOURCE_COVERAGE | FORGET_TRACK`. Historical claims must cite permitted archived events; advanced dynamic models are later.

Example structured candidate:
```json
{
  "subject_ref": "scoped_user",
  "pattern_claim": {
    "kind": "one_off_observation",
    "behavior": "Typically offers solution before asking for a concrete example",
    "source_episode_ids": ["synthetic_episode_1"],
    "uncertainty": "single instance"
  },
  "candidate": {
    "strategy": "reverse_role",
    "first_action": "Ask for a concrete recent example and listen for 10 minutes",
    "context": "next voluntary low-stakes discussion",
    "learning_question": "What did I learn before proposing an answer?",
    "alternative_if_declined": "skip"
  },
  "risk_flags": [],
  "novelty_claim": "not_known",
  "suggested_action": "offer"
}
```

The generator must not control consent/permissions or persist state directly. An exclusive routed request cannot invoke generic reminder creation, memory mapper, statement watch or other Group actions, even if the input mentions dates/goals. A downstream denial/NO_ACTION must end processing without restarting the generic pipeline. Deterministic policy validates that each proposed source reference exists, belongs to the requested scope and consenting subject, that no harm/retired topic is violated, and that duplicate proposals are suppressed. Safety and behavioral novelty also need qualitative adversarial review: simple keywords cannot classify all harmful social situations.

**Candidate/context budget:** retrieve only relevant source evidence within token limits and consider 1–3 distinct candidates; **no numeric cap on published useful replies**. Track model latency/cost with owner-approved emergency runaway breaker, not arbitrary weekly/daily reply limits.

**Telegram response:** normally 2–5 human sentences, one grounded observation, one unusual experiment, one question/opt-out. No structured diagnostic report; no “ты всегда”, no “никогда не делал” without confirmation.

## 8. Consent, privacy and sensitive-context behavior

- P0 can evaluate a newly arrived report only if the author granted **both automatic analysis and group-visible unsolicited output**. Someone who grants **direct-request group-reply permission only** may still invoke НеНой; an autonomous pause must not affect that direct path. Global stop blocks both. No automatic progress track or daily challenges. P1 history needs separate authorization.
- Public group response may quote a consenting participant's *own* message only under agreed scope; do not pull private or third-party sensitive details into public summaries.
- If participant describes severe exhaustion, medical trouble, distress, family conflict or unsafe conditions, avoid “push harder”; choose care / clarification / NO_ACTION. Do not assume bot provides medical or psychotherapy services.
- One participant declining does not authorize analysis of them via another person's prompt.
- History imported from an exported HTML/JSON is *separate opt-in and import project*, not part of P0 and never included in public repository.
- Proposed retention for seven-day pilot: bounded source references only; raw text governed by independently reviewed current v2 policy, with shorter Reflection-specific summaries where feasible. Owner must approve exact TTL, forget semantics, and backup disclosures at G2/G3.
- Existing `group_profile` friends behavior is not consent to intensive Reflection analysis. Use a dedicated explicit feature flag per group/topic with separate per-user consent, default OFF.
- Logging/telemetry: do not write raw reflection excerpts, medical claims, chat IDs or API secrets into public artifacts. Existing runtime logs may contain chat identity; evaluate access/retention separately.

### G2 v1.2 phase boundary to preserve in implementation

- **INITIAL-LIVE** may evaluate an opted-in fresh report or direct request and retrieve **relevant permitted prior messages** from a topic/author/consent-filtered archive; never pass default chat-wide HOT/LONG or non-consenting quoted text to the model.
- **INITIAL-LIVE** supports sourced history but cannot claim uncollected or lost messages exist; recurring behavior requires distinct evidence and 'first ever' requires self-confirmation.
- **INITIAL HISTORY** must prove topic-tagged provenance, consent and retrieval correctness, with missing legacy topic IDs marked unknown and excluded pending verification.
- Group owner's consent to bot operation is **not** member consent to personalized interpretation. Raw ingestion by the pre-existing bot remains a separate privacy exposure requiring a truthful notice before launch.

## 8.1. P0 outbox consent version and revocation-safe send contract

Reflection outbox metadata identifies exact group/topic/author/event, consent version and purpose. **No numeric reply or 48-hour limit.** Keep event dedupe and participant mute/revocation, usage/cost telemetry and an owner-approved financial runaway breaker. Enqueue never grants permanent send permission.

Immediately before **each** Reflection send/retry, including a previously **claimed**/leased processing job, the delivery worker must:
1. Reload group feature/topic, exact user consent version, and **purpose** from trusted current store before every queued/claimed/retried send.
2. If unsolicited, require permitted history/auto-analysis/public reply and mute OFF but **no 2/week or 48-hour check**. If direct, require direct-request permission independently. Global stop, source revocation, mismatched version or feature OFF blocks both. Suppress unauthorized queued items.
3. if generation completed after revocation, discard generated output and do not enqueue it; handle transaction races;
4. keep ordinary non-Reflection outbox messages unchanged.

A check-before-HTTP **cannot retract a Telegram request already in flight** or a message already delivered. Document the race window and do not promise impossible retroactive deletion. Test revocation before queue, after queue, after claim, between generation and send, on worker restart/retry, and while HTTP is in flight. Any unreconciled unsafe send path is a STOP.

## 9. Minimal vertical slices and sequencing

**S0 — owner-approved product/privacy/trust controls (blocking):** G1 manual exact-SHA governance choice, purpose-specific consent, data notice for existing bot raw ingest, agreed output visibility and retention/forget semantics. No production changes.

**S1 — memory-first foundation:** per-user/topic consent, source event archive with durable author/topic/time/version/reply IDs and approved retention, integrity/forget/recovery, evidence retrieval; additive migration separately authorized. Keep other groups' behavior unchanged.

**S2 — history-backed selection and delivery:** exclusive route; evaluate new reports with current + selected relevant sourced history, SILENCE/ONE_QUESTION/ONE_EXPERIMENT, **no answer-count cap**; semantic de-duplication, safety and feedback, model cost telemetry/runaway breaker, purpose-specific send-time consent. On-demand track #166 from actual archived coverage.

**S3 — P0 tests and controlled pilot:** frozen **6 useful questions + 6 experiments + 12 routine/silent + 12 unsafe** fixtures, per-purpose consent tests, mandatory silent-evaluation budget, revocation/race and other-group regressions; after G8/G9, opt-in 7-day pilot. P1 history is not prerequisite.

**S4 — optional older history import:** pre-activation Telegram Desktop export separately authorized and validated, with explicit coverage gaps; never pretend Bot API provided old history.

**S5 — later advanced analytics:** optional deep longitudinal experience/experiment modeling; on-demand source-backed Growth Track is already an initial target once historical archive works.


S1–S5 describe the **proposed** order, not permission to start implementation. Do not open multiple coding tasks or broad refactors automatically. P0 is useful without P1.

## 10. Test, operational reliability and cost gate

Follow G2 [50 specified synthetic acceptance scenarios, split by stage](BREAK_PATTERN_G2_RED_TEAM_2026-11-21.md); **none executed**. Add specific G3 tests:
- SQL query under user A topic Reports never returns user B/topic Chat/Personal data; failed metadata never falls back across topics.
- Two forwards/edits of one episode are one piece of behavioral evidence.
- After consent revocation: no future Reflection generation/enqueue; **pre-send recheck on already claimed outbox rows** (and retries), no authorized sends after revoked consent except the documented in-flight Telegram race; truthful deletion/limitation.
- Threaded reply preserves exact topic and reply-to IDs; dedupe after webhook retry.
- Crashed LLM/memory DB yields clear response or silence; never fabricated historical claim.
- Existing birthday/reminder/URL Reader routes and normal group persona remain unchanged.
- Test retention/forget against all affected tables, outbox and event trails, and document backup caveats.
- Privacy-negative fixtures and adversarial user text cannot change scope/policy; sender/outbox test distinguishes current Reflection output from unrelated birthday/reminder messages. Add positive tests for useful proactive response without a command and negative tests for neutral report (silence), unknown topic, unconsenting author, 2/week cap, 48h cooldown, negative feedback and revoked queued output.
- Independent qualitative reviewers compare actual novel vs generic alternatives on synthetic scenarios; source correctness and safety are hard gates even when creativity is excellent.

**Monitoring:** use existing `llm_usage`, `interventions` and sanitized feature counters for latency/cost, rejected candidates, explicit requests, declined/attempted results and unwanted noise. No new monitoring infrastructure. Don't call 7-day pilot “statistically proven effectiveness”.

**Failure stop:** wrong author, wrong topic, unauthorized private data, fabricated clickable evidence, unsafe challenge, unauthorized proactive message, ignored opt-out, excessive cost or stale source attribution means stop Reflection feature and investigate.

**Rollback:** group-level `reflection_enabled=false` / feature flag disables new Reflection routing, retains unrelated bot behavior; **pre-send consent check blocks queued and claimed Reflection outbox**, cancel pending work and report in-flight HTTP limitations; migration is additive, schema downgrade only with separate reviewed plan; privacy deletion semantics require tested method and cannot promise to erase previously delivered Telegram messages.

## 11. G3 decisions requiring owner and independent reviewer

1. First rollout: Reports topic only (recommended for P0) vs broader consented multi-topic dialogue (later goal to analyze “the whole chat”).
2. Where/how to record independent per-person consent and explain ambient base-bot collection.
3. Exact Reflection TTL, outcome recording, deletion/backup disclosure, opt-out UX.
4. Whether the initial single-report experiment is useful enough for early P0 before full longitudinal context.
5. Separate approval for additive DB migration and any production data access.
6. Review proposed model boundary (candidate generation vs deterministic gates) and reliability tests.

**Project OS status:** `G3 v1.5 OWNER CHANGE REQUEST / DRAFT`; memory-first source archive and no numerical reply caps are new, not yet reviewed. G4/G5 BLOCKED_FOR_BUILD, G7 not active, G8/G9 not granted.

**One next safe operation:** independent exact-HEAD Codex review of newly expanded *selective proactive P0* design and revised #167 task contract, then owner acceptance of exact group/topic, per-person background-analysis/public-reply consent, 2/week + 48h caps, cost, retention and silent/active quality thresholds. No merge, deploy or CI activation.
