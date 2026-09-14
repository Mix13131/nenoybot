# TASK 17 — Group Whitelist + Membership Context

## Goal
Enable Group Mode only for explicitly approved test groups and load the group/participant profiles required by later Group pipelines.

## Scope
- `app_v2/repositories/group_context_repo.py`
- `app_v2/services/group_access.py`
- `tests_v2/unit/test_group_access.py`

Telegram ingest remains responsible for user/chat/member upsert; this task consumes those normalized DB records.

## Required behavior
- Group access requires `chats.chat_type='group'`, `is_active=true`, and `is_whitelisted=true`.
- Unknown/unapproved/inactive groups are denied without generation or outbound side effects.
- Approved group context includes group profile, silence window, member role, and participant profile.
- Missing participant row does not make a whitelisted group unsafe; it returns an empty participant profile and can be completed by normal ingest on the next event.
- Personal chats are never interpreted as Group context.

## Acceptance
- approved group is allowed;
- non-whitelisted group is denied;
- inactive group is denied;
- unknown group is denied;
- private chat is denied by Group gate;
- participant/group profile fields are loaded exactly;
- no legacy `app/` changes.
