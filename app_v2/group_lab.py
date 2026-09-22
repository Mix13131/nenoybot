from __future__ import annotations

import argparse
from pathlib import Path

from app_v2.labs.group_history import (
    GroupLabOptions,
    ReplayOptions,
    build_frozen_replay,
    load_json,
    sanitize_telegram_export,
    write_json,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app_v2.group_lab",
        description="Prepare sanitized, deterministic Telegram Group Lab replay artifacts.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="sanitize an export and build frozen replay cases")
    prepare.add_argument("--input", required=True, help="Telegram Desktop result.json path")
    prepare.add_argument("--output-dir", required=True, help="directory for sanitized lab artifacts")
    prepare.add_argument("--owner-source-id", default=None, help="optional source actor id mapped to admin")
    prepare.add_argument("--owner-name", default=None, help="optional exact display name mapped to admin")
    prepare.add_argument("--exclude-service", action="store_true", help="omit Telegram service rows")
    prepare.add_argument("--gap-minutes", type=int, default=90)
    prepare.add_argument("--max-episode-messages", type=int, default=60)
    prepare.add_argument("--context-messages", type=int, default=12)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.command == "prepare":
        raw = load_json(args.input)
        dataset = sanitize_telegram_export(
            raw,
            options=GroupLabOptions(
                owner_source_id=args.owner_source_id,
                owner_display_name=args.owner_name,
                include_service_messages=not args.exclude_service,
            ),
        )
        replay = build_frozen_replay(
            dataset,
            options=ReplayOptions(
                inactivity_gap_minutes=args.gap_minutes,
                max_episode_messages=args.max_episode_messages,
                context_messages=args.context_messages,
            ),
        )

        output_dir = Path(args.output_dir)
        write_json(output_dir / "sanitized_history.json", dataset)
        write_json(output_dir / "frozen_replay.json", replay)

        print(
            "Group Lab prepared: "
            f"messages={dataset['stats']['message_count']} "
            f"participants={dataset['stats']['participant_count']} "
            f"episodes={replay['stats']['episode_count']} "
            f"cases={replay['stats']['case_count']}"
        )
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
