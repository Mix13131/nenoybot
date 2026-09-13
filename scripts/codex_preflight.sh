#!/usr/bin/env bash
set -euo pipefail

echo "== НеНой 2.0 Codex preflight =="
echo "cwd: $(pwd)"
echo "branch: $(git branch --show-current 2>/dev/null || echo unknown)"
echo "commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo

echo "== git status =="
git status --short || true
echo

echo "== runtimes =="
python --version
python -m pip --version
pytest --version || true
echo

echo "== required project files =="
for path in AGENTS.md docs/v2/MVP_BUILD_PLAN.md docs/v2/ARCHITECTURE.md; do
  if [[ -f "$path" ]]; then
    echo "OK  $path"
  else
    echo "MISS $path"
    exit 1
  fi
done

echo
if [[ -d app_v2 ]]; then
  echo "app_v2 exists"
else
  echo "app_v2 does not exist yet (expected before TASK 01 completion)"
fi

if [[ -d tests_v2 ]]; then
  echo "tests_v2 exists"
else
  echo "tests_v2 does not exist yet (expected before TASK 01 completion)"
fi

echo

echo "Preflight complete. Read the active task spec before editing."
