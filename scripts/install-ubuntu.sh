#!/usr/bin/env bash
# Install Open Maestro on Ubuntu / WSL with an internet-connected shell.
#
# Usage:
#   ./scripts/install-ubuntu.sh /path/to/open_maestro-0.1.0-py3-none-any.whl
#   ./scripts/install-ubuntu.sh https://your-artifacts.example.com/open_maestro-0.1.0-py3-none-any.whl
#
# Optional extras may be enabled with environment variables:
#   OPENAI=1 CLAUDE_SDK=1 KIMI_ACP=1 ./scripts/install-ubuntu.sh <wheel>

set -euo pipefail

WHEEL_URL_OR_PATH="${1:-}"
VENV_DIR="${VENV_DIR:-$HOME/.open-maestro/venv}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"

if [[ -z "$WHEEL_URL_OR_PATH" ]]; then
    echo "Usage: $0 <wheel-file-or-url>"
    echo ""
    echo "Examples:"
    echo "  $0 ./dist/open_maestro-0.1.0-py3-none-any.whl"
    echo "  $0 https://artifacts.example.com/open_maestro-0.1.0-py3-none-any.whl"
    exit 1
fi

echo "==> Installing Open Maestro"
echo "    Wheel:        $WHEEL_URL_OR_PATH"
echo "    Venv:         $VENV_DIR"
echo "    Bin symlink:  $BIN_DIR/maestro"

# Ensure Python 3.11+ is available.
PYTHON_CMD=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
        version=$("$cmd" --version 2>&1 | awk '{print $2}')
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON_CMD" ]]; then
    echo "ERROR: Python 3.11+ is required but was not found."
    echo "Install it with: sudo apt-get install python3.11 python3.11-venv"
    exit 1
fi

echo "==> Using Python $PYTHON_CMD ($($PYTHON_CMD --version 2>&1))"

# Create virtual environment.
if [[ ! -d "$VENV_DIR" ]]; then
    echo "==> Creating virtual environment at $VENV_DIR"
    "$PYTHON_CMD" -m venv "$VENV_DIR"
else
    echo "==> Using existing virtual environment at $VENV_DIR"
fi

PIP="$VENV_DIR/bin/pip"

# Build extras list.
extras=""
[[ "${OPENAI:-0}" == "1" ]] && extras="${extras:+$extras,}openai"
[[ "${CLAUDE_SDK:-0}" == "1" ]] && extras="${extras:+$extras,}claude-sdk"
[[ "${KIMI_ACP:-0}" == "1" ]] && extras="${extras:+$extras,}kimi-acp"

# Install the wheel.
if [[ "$WHEEL_URL_OR_PATH" == http* ]]; then
    echo "==> Downloading and installing wheel"
    if [[ -n "$extras" ]]; then
        "$PIP" install "open-maestro[$extras] @ $WHEEL_URL_OR_PATH"
    else
        "$PIP" install "open-maestro @ $WHEEL_URL_OR_PATH"
    fi
else
    if [[ ! -f "$WHEEL_URL_OR_PATH" ]]; then
        echo "ERROR: Wheel not found: $WHEEL_URL_OR_PATH"
        exit 1
    fi
    echo "==> Installing wheel"
    if [[ -n "$extras" ]]; then
        "$PIP" install "$WHEEL_URL_OR_PATH[$extras]"
    else
        "$PIP" install "$WHEEL_URL_OR_PATH"
    fi
fi

# Ensure the bin directory exists and symlink maestro.
mkdir -p "$BIN_DIR"
MAESTRO_BIN="$VENV_DIR/bin/maestro"
if [[ ! -f "$MAESTRO_BIN" ]]; then
    echo "ERROR: maestro binary not found in $VENV_DIR/bin after install"
    exit 1
fi

ln -sf "$MAESTRO_BIN" "$BIN_DIR/maestro"
echo "==> Created symlink $BIN_DIR/maestro -> $MAESTRO_BIN"

# Verify.
echo ""
echo "==> Verifying installation"
if ! command -v maestro >/dev/null 2>&1; then
    if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
        echo ""
        echo "WARNING: $BIN_DIR is not on your PATH."
        echo "Add the following line to ~/.bashrc or ~/.zshrc:"
        echo "    export PATH=\"$BIN_DIR:\$PATH\""
        echo ""
    fi
fi

if ! "$MAESTRO_BIN" --help >/dev/null 2>&1; then
    echo "ERROR: maestro command is not working"
    exit 1
fi

agent_count=$("$VENV_DIR/bin/python" -c "
from open_maestro.agents.loader import AgentLoader
print(len(AgentLoader.load_defaults().list()))
" 2>/dev/null || echo "unknown")

echo "    maestro binary: OK"
echo "    Agents bundled: $agent_count"
echo ""
echo "==> Installation complete"
echo ""
echo "Next steps:"
echo "  1. Ensure $BIN_DIR is on your PATH."
echo "  2. Install at least one backend CLI: claude (Claude Code) or kimi (Kimi Code)."
echo "  3. Run: maestro --list-runtimes"
echo "  4. Run: maestro --interactive"
echo ""
echo "To enable SDK/API runtimes, reinstall with extras:"
echo "  OPENAI=1 ./scripts/install-ubuntu.sh <wheel>"
echo "  OPENAI=1 CLAUDE_SDK=1 KIMI_ACP=1 ./scripts/install-ubuntu.sh <wheel>"
