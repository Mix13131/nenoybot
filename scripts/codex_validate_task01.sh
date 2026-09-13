#!/usr/bin/env bash
set -euo pipefail

echo "== TASK 01 validation =="

python -c "import app_v2"
python -c "from app_v2.main import app; print(app.title)"
pytest tests_v2 -q

echo
echo "== changed files vs v2 =="
git diff --name-only v2...HEAD

echo
echo "== legacy protection =="
legacy_changes="$(git diff --name-only v2...HEAD -- app tests)"
if [[ -n "$legacy_changes" ]]; then
  echo "ERROR: legacy app/ or tests/ changed:"
  echo "$legacy_changes"
  exit 1
fi

echo "OK: legacy app/ and tests/ unchanged."
