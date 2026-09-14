# TASK 23 — Maintenance + Retention + Compaction Jobs

## Goal
Keep raw chat, memory, events, and outbox bounded without deleting durable evidence blindly.

## Required behavior
- WARM raw message text expires after the configured retention window;
- cleanup clears raw text but preserves message metadata and LONG-memory evidence stored on Memory Cards;
- memory freshness decays deterministically and idempotently;
- duplicate Memory Cards are compacted by merging evidence into a winner and superseding duplicates;
- Personal/Group active-memory soft caps trigger targeted compaction, never blind deletion;
- stale running jokes can be archived when exhausted/stale;
- stale event/outbox processing leases recover;
- old terminal event/outbox rows are pruned only after retention windows;
- maintenance can run repeatedly and safely.

## Default policy
- WARM messages: 30 days
- terminal events/outbox: 30 days
- Personal soft cap: 150 active cards
- Group soft cap: 250 active cards
- low-quality archive threshold: freshness <= 0.15 and confidence < 0.65, non-pinned, unused >= 90 days
- stale running joke archive: freshness <= 0.25 and unused >= 30 days

## Acceptance
- ordinary raw message text older than retention is cleared;
- LONG Memory evidence remains intact;
- duplicate evidence is merged and source_count preserved/increased;
- pinned memory is never archived by cap compaction;
- over-cap scope triggers compaction, not blind delete;
- stale worker leases recover;
- repeated maintenance runs are safe;
- full `tests_v2` green.
