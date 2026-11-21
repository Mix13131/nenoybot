# G3 CONDITIONAL ARCHITECTURE — Reflection Coach + Antiautopilot

**Status:** `PROPOSED / NOT APPROVED / NOT BUILD-READY`  
**Date:** 2026-11-21  
**Parent:** [Reflection Coach #161](https://github.com/Mix13131/nenoybot/issues/161)  
**Feature:** [Antiautopilot #163](https://github.com/Mix13131/nenoybot/issues/163)  
**G2:** [Draft PR #164](https://github.com/Mix13131/nenoybot/pull/164) `87ffe1fd02c050fb64bcd5207edc2b9ce5bd2b0d` — **REWORK v1.1 with P0 current-report-only / P1 history stages; no independent review**.  
**G1:** [Draft PR #162](https://github.com/Mix13131/nenoybot/pull/162) — non-default `v2` Project OS trust unresolved.  
**Observed product base:** `nenoybot/v2@805d2aafc709cb2323f228d791b16bb1f63f64b8` (must recheck when implementation starts).
**This file:** conditional architecture exploration for owner and external reviewer; not authority to alter runtime, CI, DB or production.

## 1. Problem, result and scope

Users already write daily reflection reports in a threaded Telegram group. The new mode should, **only at the requesting and consenting person's initiative**, examine permitted reports and dialogue, identify a source-grounded familiar response, propose one genuinely different safe action, and use voluntary feedback for a later suggestion.

Avoid (1) unsolicited psychotherapy, (2) random “get out of comfort zone” tasks, (3) false “you have never done X” claims, (4) other-person attribution, (5) cross-topic/private memory contamination, (6) pressure to comply.

**North Star:** increased behavioral options demonstrated by voluntary real-life experimentation, not frequency of model output or amount of discomfort.

**Product acceptance is separate:** both participants can independently consent to their own history. Agreement by the group owner cannot authorize personal profiling of the other person.

## 2. Source-backed inspection / existing reuse

| Component | In `v2` today | Reuse or modification |
| --- | --- | --- |
| Telegram webhook `app_v2/adapters/telegram_webhook.py` | Event metadata already captures `message_thread_id` | Reuse, extend normalization to explicitly distinguish known forum topic vs unknown/general |
| Ingest `event_ingestor.py` + `ingest_repo.py` | Persists message text and event payload **before** Group Dispatcher | Existing baseline retention is distinct from Reflection consent; add purpose-bound eligibility before Reflection indexing and consider ingest minimization in scoped group |
| `messages` / `message_repo.py` | Messages stored by chat, read with `recent_for_scope` by chat ID; no topic column | Add reviewed migration/topic-aware read path for new mode; avoid modifying unrelated group query semantics |
| `events.payload` | Includes normalized `metadata.message_thread_id` and text | Can serve bounded audit source, not an indefinitely retained longitudinal index |
| `memory_cards` + `retrieval_engine.py` | Group vs Personal isolated at scope level, but no reliable topic filter for all HOT/LONG retrieval | New Reflection retrieval must NOT blindly reuse chat-wide memory cards or relation expansion |
| `connector_repo` and `connector_presets.py` | Versioned group connector and isolated role/profile features available | Use a dedicated opt-in reflection profile; do not change default friends persona |
| `group_pipeline.py` + Dispatcher | Group silence-first, direct mention/reply routing, existing reminders and other actions | Insert a gated route for reflection mode before ordinary roast generation; preserve unrelated paths |
| `telegram_sender.py` | Already sends `message_thread_id` when supplied in outbox metadata | Reuse with tested correct-topic delivery and idempotent dedupe |
| `llm_usage` | Existing per-call token, latency and cost telemetry | Reuse; no third-party analytics product or new infrastructure |
| `maintenance_repo.py` | `messages.text` expires, terminal events and outbox pruned separately | Reflection-specific retention, opt-out deletion and generated-text handling need explicit scope |
| Group Lab `app_v2/group_lab.py` / Task 37 | Existing synthetic/sanitized replay infrastructure; JSON-import path, not automatic HTML history import | Reuse for synthetic quality/evidence tests, **not** as permission to upload actual Telegram exports |

**Source limitations:** this is static code inspection. No trusted Project OS Context Pack, runtime Telegram proof, database query, tests or independent reviewer executed.

## 3. Architecture options

| Option | Benefit | Weakness | Decision |
| --- | --- | --- | --- |
| A. Add “be unusual” instructions to generic `group_generator.md` | Very cheap and quick | Roast-first personality, chat-wide context and no evidence/consent guarantee | REJECT |
| B. Dedicated `reflection_coach_v1` route, evidence/consent policy, structured candidate + deterministic release guard in existing v2 runtime | Isolated, auditable, reversible, can reuse Postgres/outbox/model routing | Requires focused topic/memory migration, bounded new state | **RECOMMENDED / PROPOSED** |
| C. New bot, separate AI agent server, new database | Isolated deployment | Unnecessary overhead, duplicate memory, split product | REJECT for P0 |

## 4. Request path and trust boundaries

```text
Telegram group update
    │
    ▼
Existing webhook → normalize (chat, actor, message, topic, reply, update)
    │
    ▼
Existing ingest / durable queue  [existing raw-data policy is separately reviewed]
    │
    ▼
Existing Group Pipeline
    │
    ├─ not reflection-enabled group, unrelated topic/intent → unchanged path
    │
    └─ reflection route candidate
          │
          ▼
    Reflection Scope Gate (DETERMINISTIC)
      explicit mention/reply? group/topic enabled?
      requesting participant opted in? sensitive-context policy?
          │
          ├─ deny → short truthful decline or NO_ACTION
          ▼
    Topic-aware Evidence Retriever (DETERMINISTIC)
      exact group + allowed topic(s) + subject + temporal bounds
      event IDs + source references + distinct episodes
          │
          ├─ missing/untrusted → question / NO_ACTION
          ▼
    Pattern Hypothesis Generator (LLM as inference only)
      observed behavior, not diagnosis or unverified cause
          │
          ▼
    Candidate Engine (LLM bounded candidates; novelty strategies)
          │
          ▼
    Quality + Safety + Consent + De-dup Gate (DETERMINISTIC controls
      plus semantic checks; unsafe or uncertain -> NO_ACTION)
          │
          ▼
    One short proposed experiment + opt-out
          │
          ▼
    Existing outbox → Telegram in same topic
          │
          ▼
    Optional user self-report → purpose-bounded experiment status
```

Important: permissions and source selection occur *before* any model may see another participant's history. A prompt instruction saying “don't mention private data” does not prevent the model from receiving it. Cached HOT, related Memory Cards, and other group behaviors must not be mixed into Reflection generation.

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

Prefer **additive** PostgreSQL migration within the existing v2 DB, version/name chosen at build time after checking all active PRs. No schema modification in this G3 PR.

### 6.1 `messages` topic metadata (proposed)
- `message_thread_id BIGINT NULL` (stored only when reported);
- `topic_scope TEXT NOT NULL` (`TOPIC | GENERAL | UNKNOWN | NOT_APPLICABLE`);
- index on `(chat_id, topic_scope, message_thread_id, user_id, created_at)` where appropriate;
- do not assign existing historical rows a guessed topic: mark `UNKNOWN`, exclude from topic-sensitive inference, or import only through separately reviewed owner/participant consent.

### 6.2 Reflection consent (proposed)
Use a purpose/version-specific consent record, either a small `reflection_consents` table or a deliberately isolated versioned `chat_members.participant_profile` key. Prefer a **small table** if status, scope changes and revocations require durable audit/atomic lookup.

Minimal fields: `chat_id`, `user_id`, `purpose_version`, `allowed_topic_ids`, `status=opted_in|revoked`, `consented_at`, `revoked_at`. Do **not** store personal health or psychological trait labels. Participant must independently consent to analysis of their own history, including whether others in the group may read the resulting suggestions. Deny on missing/unconfirmed consent.

**Pre-ingest issue:** base bot already records ambient messages from new groups. Reflection consent gates must not be presented as if they prevent the original bot's collection. Approving Reflection means separately agreeing on the underlying bot's data notice and minimizing old raw text where feasible. If strong “no storing before opt-in” is required, design an ingestion change as a separate security task.

### 6.3 Optional experiment state (proposed)
A scoped `reflection_experiments` table or similarly auditable store:
- `proposal_id`, `group_id`, `topic_scope/id`, `subject_user_id`;
- `created_by_request_event_id` unique (idempotent), `strategy_kind`, `source_event_ids`;
- `status=offered|declined|accepted|tried_self_reported|not_tried_self_reported|expired|retired`;
- `first_time_confirmed` **only if self-reported**;
- `created_at`, `expires_at`, `retired_at`;
- optional minimal answer `what_surprised` only with explicit consent; redact sensitive narrative and apply agreed TTL;
- never infer success from `offered`, `accepted` or absence of refusal.

**Data deletion:** scope all reflection-specific state and allow participant correction/retire/delete. Audit deletion across memory, proposals, any derived summaries, pending jobs and generated outbox; explain limits for previously published Telegram messages and backups. Existing general `events`/raw retention remains a separate obligation, not implicitly solved by deletion of experiment record.

## 7. Generator and decision contracts (not code)

**Intent:** `REFLECTION_ASK | ANTI_AUTOPILOT_ASK | ALTERNATIVE_ASK | LOWER_INTENSITY | DECLINE | RESULT_REPORT | FORGET | REVIEW`.

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

The generator must not control consent/permissions or persist state directly. Deterministic policy validates that each proposed source reference exists, belongs to the requested scope and consenting subject, that no harm/retired topic is violated, and that duplicate proposals are suppressed. Safety and behavioral novelty also need qualitative adversarial review: simple keywords cannot classify all harmful social situations.

**Candidate budget:** G2 asks for 3 internal diverse alternatives; treat that as a proposed tuning choice, not an inviolate requirement. Start with 2–3 different strategies *within a bounded model call*, measure cost and quality; prefer `NO_ACTION` over more model calls without evidence of benefit.

**Telegram response:** normally 2–5 human sentences, one grounded observation, one unusual experiment, one question/opt-out. No structured diagnostic report; no “ты всегда”, no “никогда не делал” without confirmation.

## 8. Consent, privacy and sensitive-context behavior

- P0 explicit requests only; no proactive prompts, scheduled experiments, spam or daily punishment. Independent consent from both participants is needed before bot analyzes their respective histories.
- Public group response may quote a consenting participant's *own* message only under agreed scope; do not pull private or third-party sensitive details into public summaries.
- If participant describes severe exhaustion, medical trouble, distress, family conflict or unsafe conditions, avoid “push harder”; choose care / clarification / NO_ACTION. Do not assume bot provides medical or psychotherapy services.
- One participant declining does not authorize analysis of them via another person's prompt.
- History imported from an exported HTML/JSON is *separate opt-in and import project*, not part of P0 and never included in public repository.
- Proposed retention for seven-day pilot: bounded source references only; raw text governed by independently reviewed current v2 policy, with shorter Reflection-specific summaries where feasible. Owner must approve exact TTL, forget semantics, and backup disclosures at G2/G3.
- Existing `group_profile` friends behavior is not consent to intensive Reflection analysis. Use a dedicated explicit feature flag per group/topic with separate per-user consent, default OFF.
- Logging/telemetry: do not write raw reflection excerpts, medical claims, chat IDs or API secrets into public artifacts. Existing runtime logs may contain chat identity; evaluate access/retention separately.

### G2 v1.1 phase boundary to preserve in implementation

- **P0-LIVE may use only the currently addressed report** or explicitly provided user scenario. Reusing the existing `ContextBuilder.build()` unmodified would retrieve group-wide HOT messages and Memory Cards; **a new minimal-allowlisted Reflection context constructor is required**, with negative tests verifying excluded members/topics and no Personal/LONG fetch.
- **P0-LIVE is not a full-history psychology engine**. The output must not claim recurring behavior or first-time novelty without self-confirmation. The two-person historical analysis goal belongs to P1 and must not be smuggled into P0 through generic retrieval.
- **P1-HISTORY** must separately prove topic-tagged provenance and consent-aware retrieval, and account for older legacy rows without topic metadata.
- Group owner's consent to bot operation is **not** member consent to personalized interpretation. Raw ingestion by the pre-existing bot remains a separate privacy exposure requiring a truthful notice before launch.

## 9. Minimal vertical slices and sequencing

**S0 — Governance/contract (blocking):** resolve Project OS nondefault `v2` G1 trust (#162), approve/refine G2 (#164) and purpose/retention/consent. No product code.

**S1 — Provenance and opt-in (first actual engineering task):** per-user consent gate, topic-aware message persistence and retrieval, negative tests for wrong actor/topic/legacy unknown. Do not yet generate experiments. Proposed additive migration/reversible flag; exact files and SHA in a dedicated G4 Task Contract.

**S2 — Isolated Reflexion route:** group-specific opt-in connector/profile, own generator prompt, direct mention/reply, no unsolicited or ROAST; regressions against other groups.

**S3 — Antiautopilot single-report mode:** generate one safe, specific alternative based on *current* authorized report; cannot claim a longitudinal pattern. This can be tested quickly without falsely asserting whole-history knowledge.

**S4 — Longitudinal evidence + history-based experiments:** consented topic-aware 2+ episode verification, controlled deduplication and strategy diversity; add optional self-reported proposal status.

**S5 — Weekly review + opt-in pilot:** optional on-demand digest, direct source pointers, 7-day two-user test, product acceptance; only after evidence consider any proactive suggestion, with separate authorization.

S1–S5 are dependencies, **not** approvals to start implementation. Do not open five coding tasks or perform broad refactors automatically.

## 10. Test, operational reliability and cost gate

Follow G2 [30 synthetic acceptance scenarios](BREAK_PATTERN_G2_RED_TEAM_2026-11-21.md). Add specific G3 tests:
- SQL query under user A topic Reports never returns user B/topic Chat/Personal data; failed metadata never falls back across topics.
- Two forwards/edits of one episode are one piece of behavioral evidence.
- After consent revocation: no future Reflection retrieval/generation, no retries generating an opted-out message; deletion handled truthfully.
- Threaded reply preserves exact topic and reply-to IDs; dedupe after webhook retry.
- Crashed LLM/memory DB yields clear response or silence; never fabricated historical claim.
- Existing birthday/reminder/URL Reader routes and normal group persona remain unchanged.
- Test retention/forget against all affected tables, outbox and event trails, and document backup caveats.
- Privacy-negative fixtures and adversarial user text cannot change scope/policy.
- Independent qualitative reviewers compare actual novel vs generic alternatives on synthetic scenarios; source correctness and safety are hard gates even when creativity is excellent.

**Monitoring:** use existing `llm_usage`, `interventions` and sanitized feature counters for latency/cost, rejected candidates, explicit requests, declined/attempted results and unwanted noise. No new monitoring infrastructure. Don't call 7-day pilot “statistically proven effectiveness”.

**Failure stop:** wrong author, wrong topic, unauthorized private data, fabricated clickable evidence, unsafe challenge, unauthorized proactive message, ignored opt-out, excessive cost or stale source attribution means stop Reflection feature and investigate.

**Rollback:** group-level `reflection_enabled=false` / feature flag immediately disables route, retains unrelated bot behavior; cancel any Reflection-specific pending outbox/jobs; migration is additive, schema downgrade only with separate reviewed plan; privacy deletion semantics require tested method and cannot promise to erase previously delivered Telegram messages.

## 11. G3 decisions requiring owner and independent reviewer

1. First rollout: Reports topic only (recommended for P0) vs broader consented multi-topic dialogue (later goal to analyze “the whole chat”).
2. Where/how to record independent per-person consent and explain ambient base-bot collection.
3. Exact Reflection TTL, outcome recording, deletion/backup disclosure, opt-out UX.
4. Whether the initial single-report experiment is useful enough for early P0 before full longitudinal context.
5. Separate approval for additive DB migration and any production data access.
6. Review proposed model boundary (candidate generation vs deterministic gates) and reliability tests.

**Project OS status:** `G3 PROPOSED/DRAFT` to aid architecture review. `G1 PARTIAL`, `G2 REWORK`, `G4 BLOCKED_FOR_BUILD`, `G6/G7 NOT PASSED`, `G8/G9 NOT GRANTED`.

**One next safe operation:** independent review of exact G2/G3 documentation heads, then resolve G2 and consent policy through explicit owner decision; only afterwards create a single S1 task contract. No merge, deploy or CI activation.
