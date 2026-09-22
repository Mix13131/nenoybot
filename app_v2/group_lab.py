from __future__ import annotations

import argparse
import os
from pathlib import Path

from app_v2.adapters.openai_adapter import OpenAIAdapter
from app_v2.config import load_config
from app_v2.labs.behavior_replay import (
    BehaviorReplayOptions,
    GroupBehaviorReplayRunner,
    run_behavior_replay,
)
from app_v2.labs.group_history import (
    GroupLabOptions,
    ReplayOptions,
    build_frozen_replay,
    load_json,
    sanitize_telegram_export,
    write_json,
)
from app_v2.services.personality_engine import PersonalityEngine
from app_v2.services.response_generator import ResponseGenerator
from app_v2.services.scene_analyzer import SceneAnalyzer


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

    run = sub.add_parser(
        "run",
        help="run selected frozen cases through current v2 Brain components without live side effects",
    )
    run.add_argument("--replay", required=True, help="frozen_replay.json path")
    run.add_argument("--output", required=True, help="behavior replay result path")
    run.add_argument("--preset", default="education_community_v1")
    run.add_argument("--case-id", action="append", default=None, help="evaluate one case id; repeatable")
    run.add_argument("--limit", type=int, default=None)

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

    if args.command == "run":
        replay = load_json(args.replay)
        lab_env = dict(os.environ)
        lab_env["NENOY_V2_ENV"] = "development"
        config = load_config(lab_env)
        adapter = OpenAIAdapter(config)
        runner = GroupBehaviorReplayRunner(
            scene_analyzer=SceneAnalyzer(adapter),
            personality_engine=PersonalityEngine(),
            response_generator=ResponseGenerator(adapter=adapter),
            options=BehaviorReplayOptions(preset_name=args.preset),
        )
        result = run_behavior_replay(
            replay,
            runner=runner,
            case_ids=set(args.case_id) if args.case_id else None,
            limit=args.limit,
        )
        write_json(args.output, result)
        print(
            "Group Lab behavior replay complete: "
            f"cases={result['stats']['evaluated_cases']} "
            f"reply={result['stats']['reply_cases']} "
            f"ignore={result['stats']['ignore_cases']}"
        )
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
