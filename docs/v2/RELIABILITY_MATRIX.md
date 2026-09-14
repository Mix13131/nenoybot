# НеНой 2.0 — Reliability Matrix

Task 24 closes the failure modes that must be safe before Railway deployment.

| Failure / retry case | Expected v2 behavior | Automated coverage |
|---|---|---|
| Classifier/OpenAI unavailable | Conservative scene; unsolicited Group humor/callback goes quiet; deterministic direct mention/reply signal survives | `tests_v2/unit/test_scene_analyzer.py::test_adapter_failure_returns_conservative_fallback_and_keeps_direct_signal` |
| Malformed classifier output | Conservative scene, roast/callback 0 | `tests_v2/unit/test_scene_analyzer.py::test_invalid_model_payload_returns_conservative_fallback` |
| Group LONG memory unavailable | No callback/roast claims, unsolicited cooldown forced, callback ids empty | `tests_v2/unit/test_group_behavior_engine.py::test_memory_failure_forces_unsolicited_silence_and_no_callback_claims` |
| Group memory unavailable + direct mention | Direct reply still allowed, never callback mode | `tests_v2/unit/test_group_behavior_engine.py::test_direct_mention_still_replies_when_group_memory_is_unavailable` |
| Group initiative/history state unavailable | Unsolicited silence; explicit direct reply still allowed | `tests_v2/unit/test_group_behavior_engine.py::test_initiative_failure_forces_unsolicited_silence_but_keeps_explicit_path` |
| Personal LONG memory unavailable | Context contains zero memories + degraded marker; no fabricated callback claim | `tests_v2/unit/test_context_builder.py::test_memory_failure_degrades_to_empty_memory_without_fabrication` |
| HOT history unavailable | Direct context still builds with empty HOT slice | `tests_v2/unit/test_context_builder.py::test_hot_history_failure_does_not_block_direct_context_build` |
| Generator failure | Intervention logged, no outbound row | `tests_v2/scenarios/test_personal_pipeline.py::test_generation_failure_logs_but_does_not_enqueue` and Group pipeline scenario |
| Telegram API error / 5xx | Sender raises; Outbox worker retries, does not mark sent | `tests_v2/integration/test_outbox_worker.py` |
| Telegram timeout | Outbox worker retries, does not mark sent | `tests_v2/integration/test_outbox_worker.py::test_outbox_worker_retries_real_timeout_without_marking_sent` |
| Event handler failure | Event retry path, no completion | `tests_v2/integration/test_event_worker.py::test_worker_retries_failed_event` |
| Worker restart / stale event lease | `recover_stale()` runs before every claim; maintenance also recovers stale leases | event worker + maintenance coverage |
| Stale outbox lease | Outbox worker/maintenance recover stale rows | outbox worker + maintenance coverage |
| Duplicate Telegram update | Second ingest returns duplicate; no second event | `tests_v2/integration/test_telegram_webhook.py::test_ingestor_duplicate_is_idempotent` |
| Duplicate Personal event processing | Stable `reply:<event_id>` outbox dedupe; same outbox id | `tests_v2/scenarios/test_personal_pipeline.py::test_same_event_retry_does_not_create_duplicate_outbox` |
| Duplicate reminder scheduler tick | Deterministic `reminder:<id>:<due_at>` event id + `ON CONFLICT DO NOTHING` | reminder repository contract / Task 16 DB validation |
| Maintenance repeated run | Age ceiling/compaction is bounded; pinned memory protected | `tests_v2/unit/test_maintenance.py`, `test_maintenance_worker.py` |

## Global rule

When a dependency is uncertain, НеНой must **degrade quieter, not more confidently**. In particular, memory failures must never produce an invented callback, and an uncertain Group state must not create a new unsolicited interruption.
