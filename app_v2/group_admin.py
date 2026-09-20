from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from typing import Any

from app_v2.adapters.postgres import connect
from app_v2.repositories.group_context_repo import GroupContextRepository


FRIENDS_DAY1_PROFILE: dict[str, Any] = {
    "profile": "friends",
    "unsolicited_enabled": True,
    "initiative": 3,
    "humor": 8,
    "sarcasm": 8,
    "roast": 7,
    "callback": 8,
    "profanity_level": 6,
    "profanity_frequency": 3,
    "sensitivity": 8,
    "callback_fatigue_minutes": 180,
    "timezone": "Europe/Moscow",
    "silence_wakeup_enabled": True,
    "silence_wakeup_after_minutes": 180,
    "silence_wakeup_start_hour": 10,
    "silence_wakeup_end_hour": 22,
    "silence_wakeup_daily_limit": 1,
    "silence_wakeup_attempt_gap_minutes": 30,
}


class GroupAdminError(RuntimeError):
    pass


def list_groups(repo: GroupContextRepository, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = repo.list_groups(limit=limit)
    result: list[dict[str, Any]] = []
    for row in rows:
        data = asdict(row)
        data["updated_at"] = row.updated_at.isoformat()
        result.append(data)
    return result


def activate_friends_group(repo: GroupContextRepository, telegram_chat_id: str) -> dict[str, Any]:
    changed = repo.configure_friends_test(
        telegram_chat_id,
        profile=FRIENDS_DAY1_PROFILE,
        enabled=True,
    )
    if not changed:
        raise GroupAdminError(
            "Group not found. Add the bot to the target Telegram group and send at least one group update first."
        )
    return {
        "telegram_chat_id": str(telegram_chat_id),
        "whitelisted": True,
        "profile": FRIENDS_DAY1_PROFILE,
    }


def deactivate_group(repo: GroupContextRepository, telegram_chat_id: str) -> dict[str, Any]:
    changed = repo.set_whitelisted(telegram_chat_id, False)
    if not changed:
        raise GroupAdminError("Group not found")
    return {"telegram_chat_id": str(telegram_chat_id), "whitelisted": False}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="НеНой 2.0 controlled Group administration")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List recently seen Telegram groups")
    list_parser.add_argument("--limit", type=int, default=20)

    activate = sub.add_parser("activate-friends", help="Whitelist one group with Day-1 Friends profile")
    activate.add_argument("telegram_chat_id")

    deactivate = sub.add_parser("deactivate", help="Remove one group from the whitelist")
    deactivate.add_argument("telegram_chat_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with connect() as conn:
            repo = GroupContextRepository(conn)
            if args.command == "list":
                payload: Any = list_groups(repo, limit=args.limit)
            elif args.command == "activate-friends":
                payload = activate_friends_group(repo, args.telegram_chat_id)
            elif args.command == "deactivate":
                payload = deactivate_group(repo, args.telegram_chat_id)
            else:  # pragma: no cover - argparse prevents this
                raise GroupAdminError(f"Unsupported command: {args.command}")
    except GroupAdminError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2

    print(json.dumps({"ok": True, "result": payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
