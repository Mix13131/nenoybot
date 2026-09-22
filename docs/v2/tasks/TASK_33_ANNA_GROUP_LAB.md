# TASK 33 — Anna Group Lab

**Status:** Phase A+B implementation in progress

## Goal

Prepare the exported Telegram history of Anna's community for deterministic,
privacy-safe offline evaluation before any visible Telegram sandbox or Anna demo.

The raw Telegram archive is input material only. It must never be inserted into
a live Telegram group or mixed with production Group memory.

## Phase A — Import / normalize

Input: Telegram Desktop JSON export.

Output: canonical Group Lab dataset with:

- source message id;
- message/service event type;
- timestamp;
- stable pseudonymous actor alias;
- owner role when explicitly configured;
- reply-to source message id;
- edited timestamp;
- service action;
- safe media metadata only;
- aggregate reactions;
- safe source metadata.

Dropped from canonical output:

- original Telegram chat id/title;
- source user/channel ids;
- original media filenames;
- reaction-user identities;
- member names in service events.

## Phase B — Anonymize / redact

Deterministic local transformation, no model/network call.

Required protections:

- stable pseudonyms within one dataset;
- participant names removed from text using known export identities;
- phone numbers redacted;
- emails / Telegram handles redacted;
- payment/card details redacted;
- meeting ids/access codes redacted;
- credential-bearing Zoom/Telegram/VK call links redacted;
- public resource links may remain, with tracking query/fragment removed;
- raw values never appear in canonical stats.

Owner identification is supplied explicitly at import time (for example by
display name) and becomes only an owner alias in output. If that display name
belongs to more than one stable source ID, import fails closed and the operator
must identify the owner by source ID instead.

Canonical labels are neutral dataset classes only (`community_archive`,
`friends_archive`, `work_archive`, `channel_archive`, `generic_archive`).
Real community/person names stay outside the canonical dataset.

## CLI

Example:

    python -m app_v2.lab.telegram_importer \
      /local/private/export.json \
      --output /local/private/anna_lab.safe.json \
      --label community_archive \
      --owner-name "OWNER DISPLAY NAME"

Run this only against a local/private source file. Do not commit the raw export,
canonical output derived from real private conversations, or owner names/IDs.

## Acceptance — Phase A+B

1. Telegram mixed text arrays normalize to plain canonical text.
2. message/service/reply/edit/reaction/media structure is preserved.
3. aliases are stable across repeated import of the same export.
4. original participant/source ids do not occur in canonical output.
5. original known participant names do not occur in canonical output.
6. phone/payment/access credentials do not occur in canonical output.
7. sensitive meeting/invite/call URLs are removed.
8. safe public resource URLs can remain without tracking fragments.
9. service-member lists become counts, not names.
10. no network/LLM/database/production-memory access is used.
11. full tests_v2 remains green.

## Out of scope for this bounded phase

- episode detection;
- Group DNA;
- schedule/current-fact extraction;
- FAQ extraction;
- Frozen Replay;
- LLM graders;
- loading the dataset into production memory;
- visible Telegram sandbox;
- inviting Anna.

## Next after Phase A+B PASS

Phase C: deterministic episode segmentation + first structured fact candidates,
starting with schedule/material/FAQ episodes.
