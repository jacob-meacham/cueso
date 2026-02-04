#!/usr/bin/env bash
# Run the Cueso eval suite against a running backend.
# Usage:
#   ./evals.sh              # run all evals
#   ./evals.sh 1 3 6        # run specific evals
#   ./evals.sh --list       # list available evals
set -euo pipefail
cd "$(dirname "$0")"
exec uv run python -m evals "$@"
