#!/usr/bin/env bash
set -euo pipefail

# Codex Cloud/local worktree bootstrap for НеНой 2.0.
# Safe to re-run. It prepares tooling only; it does not write secrets or start services.

PYTHON_BIN="${PYTHON_BIN:-python}"

"$PYTHON_BIN" --version
"$PYTHON_BIN" -m pip --version

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt

# Bootstrap packages needed by the first v2 build tasks. These are installed in the
# Codex environment so TASK 01 can run tests before the edited requirements file
# can be reinstalled. The task must still add every real runtime dependency to
# requirements.txt; this bootstrap is not a substitute for repository metadata.
"$PYTHON_BIN" -m pip install fastapi uvicorn httpx

echo "Codex environment ready."
