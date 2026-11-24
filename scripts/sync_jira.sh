#!/usr/bin/env bash
# Run the maestro-user-stories -> Jira (MSTRO) sync.
# Sources credentials from ~/.config/maestro/jira.env (override with JIRA_ENV_FILE)
# so JIRA_EMAIL/JIRA_API_TOKEN never need to be pasted on the command line.
set -euo pipefail

ENV_FILE="${JIRA_ENV_FILE:-$HOME/.config/maestro/jira.env}"
if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$SCRIPT_DIR/../.venv/bin/python"
[ -x "$PY" ] || PY=python3

exec "$PY" "$SCRIPT_DIR/sync_jira.py" "$@"
