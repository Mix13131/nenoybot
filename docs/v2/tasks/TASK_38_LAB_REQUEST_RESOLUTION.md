# TASK 38 — isolated request resolution lab

## User-approved goal — 2026-09-23

The user rejected silence as success on unresolved organizational requests. Implement a vertical slice: search permitted sources -> register an unresolved request -> obtain one organizer clarification -> deliver a supported answer -> answer an equivalent repeat without another organizer decision. Public silence is not task completion.

This supersedes TASK 37's exclusion of request storage ONLY for the new opt-in laboratory entry point. No shared Brain or existing live connector behavior changes. Branch derives from deployed a353df670335aaf29b07446220f2f24ebd7577cc; v2/main remain untouched. Prior rounds 1/2/3 are read-only.

## Concrete first release

- Same bot, same paired owner and test group, same volume; separate resolution state.
- Owner /lab_flow starts a scenario from the admitted 28-case pack (default: the missing-link case). No automatic model calls on deploy. /lab_flow live opens a clean scenario without historical knowledge.
- /lab_ask TEXT from a human in this bound group submits a question in the active scenario; owner can use it as the participant too.
- Search ALL available reviewed sources, not only the current case's tiny context. This is the reviewed corpus reconstructed from the admitted pack, NOT the full multiyear archive. Real raw import remains disallowed. Source coverage is disclosed.
- Historical source availability is conservative: a version becomes available no earlier than the first checked case where it was observed. Future/unknown edited versions and unapproved raw exports cannot enter retrieval.
- Planning separates public information (including published enrollment/billing terms) from exceptions, granting permissions, and personal access. No blanket topic ban.
- Use validated exact source excerpts for informational answers. A redacted [link] is not a usable URL. No web fetch, credentials recovery or media-content invention.
- Unresolved needs become durable tickets; shared duplicates join the same ticket, personal requests do not merge across participants. Multi-part requests remain separate needs.
- Only the paired owner can answer a ticket using /lab_answer T001 TEXT or a reply to its bot card. This is explicitly new simulated organizer input, not a historical statement by Anna. It remains session-scoped; it is never retroactively injected into historical reports.
- Acknowledging or promising an answer is not sufficient evidence. A usable clarification is rechecked; sending/confirmation have separate states. Private access is never granted or exposed in the group.
- /lab_queue and /lab_ops_report expose unresolved work, delivered answers and participant-confirmed results separately. A recorded ticket is not counted as solved. Owner /lab_confirm T001 is an explicit test acknowledgement, not an automated quality verdict.
- /lab_flow is idempotent per scenario, not an implicit reset/rebill. /lab_pause disables accepting questions. Commands targeting other bots are ignored.

## Execution safety

Single process/worker; durable input and delivery receipts. API/schema errors remain errors, not silence. Completed input updates cannot trigger duplicate model calls. Queued/running work interrupted by restart requires an explicit owner retry; no paid replay on startup. Outgoing calls transition to sending before network; ambiguous delivery stays unknown and is never automatically resent. Only observed Telegram send success records delivery. Unsupported operation claims cannot be generated because source answers are extractive.

New runtime never imports production workers/repositories/outbox, changes a webhook, modifies old run files, reads personal memory, or sends to the source group/Anna. All destinations are the paired test group, selected by trusted runtime, not by the model. Non-owner messages cannot author authoritative sources.

## Tests and gate

Synthetic tests: as-of search; conservative version availability; exact-quote validation; placeholders; public terms vs personal actions; shared dedup/private isolation; partial/multipart requests; one clarification and equivalent repeat; unhelpful admin acknowledgement; delivery/confirmation separation; reopen; no cross-session source reuse; bot command/auth controls; duplicate events; durable restart; old report hashes unchanged. Full PostgreSQL tests_v2 must be green before isolated deploy.

Model semantic correctness is not proved by mocks. Manual acceptance: /lab_flow -> inspect saved missing-link ticket -> reply with a synthetic test URL -> /lab_ask an equivalent question -> no second admin ticket, supported answer delivered. Then /lab_ops_report. This is a sequential integration test, not a new 12/28-case statistical result.

## Deployment

Only nenoy-group-lab / group-lab; preserve all variables, /data, one replica, Dockerfile and /health. Source feat/lab-request-resolution; start python -m app_v2.labs.telegram_lab_resolution. Never merge into v2 to deploy.

Local git clone was attempted and failed: Could not resolve host: github.com. No local repository preflight/full pytest is claimed; use authenticated GitHub reads/writes and Actions for full-suite evidence.
