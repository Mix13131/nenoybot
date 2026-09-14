# TASK 18 — Group End-to-End Silence First

## Goal
Wire the first Group pipeline with unsolicited initiative disabled by default. The core product behavior at this stage is safe silence plus explicit direct interaction.

## Flow
`group event -> whitelist gate -> Scene Analyzer -> Group Dispatcher -> group-scoped memory retrieval/context -> Personality Engine -> Response Generator only when replying -> intervention log -> outbox`

## Required behavior
- ordinary approved-group chat is silent;
- direct mention replies;
- reply-to-bot replies;
- serious/sensitive context never triggers unsolicited roast;
- unknown/non-whitelisted groups do not enter the pipeline;
- all retrieval/context is Group scope only;
- unsolicited initiative feature flag defaults OFF;
- no direct Telegram send from the pipeline.

## Acceptance
- ordinary group message -> no outbox;
- direct mention -> one outbox reply;
- reply-to-bot -> one outbox reply;
- serious ordinary group message -> silence;
- Group context cannot contain Personal Memory;
- same event retry cannot duplicate outbox;
- full `tests_v2` remains green.
