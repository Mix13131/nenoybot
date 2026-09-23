"""Owner-triggered recovery bookkeeping; no model calls, network or production state."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app_v2.labs.telegram_lab import LabError, atomic_json

VERSION_KEYS = ("revision", "policy_sha256", "classifier_model", "generator_model", "git_commit")
SOURCE_COMMIT = "16bd5af625b68c39de3b25de4097f3479ad3cc60"
SOURCE_POLICY = "3aa8ba8f1b368f6890f7b21cb1d5c426e3b45dc1f0b5c33666db0411bc9a21d9"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def effective_manifest(root: Path) -> dict[str, Any]:
    continuation = root / "continuation.json"
    return load(continuation)["implementation"] if continuation.exists() else load(root / "manifest.json")


def same_implementation(stored: dict[str, Any], live: dict[str, Any]) -> bool:
    return all(stored.get(key) == live.get(key) for key in VERSION_KEYS)


def prepare_resume(root: Path, results: dict[str, Any], live: dict[str, Any]) -> None:
    """Snapshot BEFORE retry. Only this incident's known old implementation may migrate.

    The original manifest and completed results remain immutable. A continuation
    manifest explicitly records mixed execution versions; it never relabels the
    earlier successful response as produced by the repaired implementation.
    Caller must hold the lab lock and authorize the owner before calling.
    """
    stored = effective_manifest(root)
    same = same_implementation(stored, live)
    allowed_repair = (
        not (root / "continuation.json").exists()
        and stored.get("git_commit") == SOURCE_COMMIT
        and stored.get("policy_sha256") == SOURCE_POLICY
        and all(stored.get(key) == live.get(key)
                for key in ("revision", "classifier_model", "generator_model"))
        and live.get("revision") == "education_cohost_lab_v2"
        and live.get("policy_sha256") != SOURCE_POLICY
    )
    if not same and not allowed_repair:
        raise LabError("Версия не соответствует разрешённому восстановлению. Результаты не изменены.")
    if any(r.get("status") not in {"done", "error", "interrupted"} for r in results.values()):
        raise LabError("Есть незавершённое или неизвестное состояние. Восстановление остановлено.")

    snapshots = root / "recovery_snapshots"
    snapshots.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".pending-", dir=snapshots))
    try:
        for name in ("manifest.json", "results.json", "continuation.json"):
            source = root / name
            if source.exists():
                shutil.copyfile(source, staging / name)
                (staging / name).chmod(0o600)
        # Atomic publication precedes continuation changes and every paid call.
        snapshot = snapshots / staging.name.removeprefix(".pending-")
        staging.replace(snapshot)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    previous = load(root / "continuation.json") if (root / "continuation.json").exists() else {}
    retained = dict(previous.get("retained_result_versions", {}))
    for cid, result in results.items():
        if result.get("status") == "done":
            retained.setdefault(cid, result.get("execution_version") or
                                {key: stored.get(key) for key in VERSION_KEYS})
    atomic_json(root / "continuation.json", {
        "implementation": {key: live.get(key) for key in VERSION_KEYS},
        "original_manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
        "retained_result_versions": retained,
        "last_snapshot": snapshot.name,
        "explicit_resume_count": int(previous.get("explicit_resume_count", 0)) + 1,
        "note": "Explicit operator continuation after repair. Retained done cases were not rerun; their original versions are listed separately.",
    })


def safe_error(exc: Exception) -> tuple[str, str]:
    """Never serialize exception bodies, request URLs or private model responses."""
    kind = type(exc).__name__
    if kind == "PlanEvidenceError":
        return "unknown_evidence_id", "Модель указала неизвестный источник. Это ошибка проверки, не решение промолчать."
    if kind == "ValidationError":
        return "invalid_plan_schema", "Формат плана модели не прошёл проверку. Решение по ситуации не получено."
    if kind == "OpenAIAdapterError":
        return "model_api_error", "Вызов API модели не завершён; это не решение промолчать."
    if kind == "ResponseGenerationError":
        return "generation_error", "Ответ модели не получен; это не решение промолчать."
    if kind == "ValueError":
        return "validation_error", "Ошибка проверки данных или плана. Это не решение промолчать."
    return "runtime_error", "Техническая ошибка выполнения ситуации. Это не решение промолчать."
