# TASK 38 follow-up — delivery, owner hints and honest metrics

## Approved scope — 2026-09-25

The sequential source/clarification/repeat experiment succeeded, but exposed three concrete defects. Fix ONLY the isolated group-lab:

1. Coalesce identical PUBLIC answers produced by ONE operation for the same ticket/source set into one group message. A Telegram success receipt covers all included waiting actors. Later questions still get a fresh answer. Different tickets, bodies, sources or private audiences are never merged.
2. When the paired owner sends a standalone clarification while informational tickets await an answer, offer explicit association. Keep a bounded, expiring draft; /lab_attach D001 T001 confirms the exact captured message for the chosen ticket. No guessing, no model call or authoritative source before confirmation. Session, ticket version, owner and expiry are rechecked. Existing reply and /lab_answer continue working.
3. Ordinary informational replies contain the verified quotation only. Delivery, sources and confirmation stay in the report. Distinguish question messages, tickets, waiting actors and repeated questions answered without a NEW organizer clarification. Previously uninstrumented repeats are not assigned invented outcomes.

## Isolation and state

Derive from deployed bc3fed3222edc5e1f3c8507f3b07bbada809de26. Add an opt-in controller and delivery/audit helpers; keep old core, routing, prompts, presets, previous controllers and main/v2 refs unchanged. Reuse the existing resolution engine and model adapter without monkeypatching.

Keep the same bot, bound owner/group, /data volume, active scenario, T001/source/history, polling offset and all old reports. Before the first compatible upgrade, store an exact-byte, private, hash-named backup of resolution_v1/state.json. Update only implementation metadata and additive audit state. Never replay old jobs or rewrite old query/source/result records. Refuse upgrade with unfinished/ambiguous old operations or incompatible core/model. Same-version restart still requires explicit recovery of interrupted work. Completed old jobs remain immutable.

No automatic model calls or group messages on deployment. New participant input still uses /lab_ask. No raw archive import, free conversation, private delivery, restored access or permissions. Owner draft hints are control-plane UI, not an expansion of assistant initiative in real groups.

## Validation

Synthetic tests cover: one send/multiple receipts, differing answers, later repeats, failed/unknown delivery, explicit owner association and rejection of stale/foreign/duplicate choices, no calls on plain draft/deploy, preserved T001/source/old artifacts, upgrade recovery, separate question/waiter counts, multipart delivery vs completed-message counts, HTML escaping/privacy and no fictional legacy repeat metric.

Full PostgreSQL `pytest tests_v2 -q` must be green before deploy. Mock tests do not prove model semantics. No real exports, names, results, chat IDs or credentials are committed.

Local clone attempted: `git clone --depth 1 --branch fix/lab-resolution-cutoff https://github.com/Mix13131/nenoybot.git /mnt/data/nenoy-delivery-work` failed with `Could not resolve host: github.com`. No local full-suite/preflight result is claimed. Use authenticated GitHub reads/writes and Actions.

## Deployment and operator

Only existing nenoy-group-lab / group-lab. Preserve variables, /data, one replica, Dockerfile and /health. Branch fix/lab-delivery-and-owner-hints; start `python -m app_v2.labs.telegram_lab_delivery`. Do NOT merge to v2/main. No webhook changes.

Owner can retrieve /lab_ops_report immediately after upgrade. Existing T001 remains usable through /lab_ask. For a separate missing-information case, ask, then post a standalone owner clarification, explicitly /lab_attach the draft, and inspect one public answer. Do not reset/rebill the completed historical checks. Draft hints and metrics do not constitute solved work; participant confirmation remains independent.
