#!/usr/bin/env bash
set -euo pipefail

# --- Configuration ---
WHEEL_NAME="open_maestro-1.5.2-py3-none-any.whl"
WHEEL_DIR="${1:-/tmp}"
VENV_DIR="$HOME/.open-maestro/venv"

# Backends to install.
#   "openai"    -> OpenAI SDK runtime. Required for GPT-4o, o3-mini, AND for
#                  open-source models served via OpenAI-compatible endpoints
#                  (Qwen via DashScope, Ollama, vLLM, etc.).
#   "anthropic" -> Anthropic SDK runtime for Claude models via API.
#   "claude-sdk"-> Claude Agent SDK runtime.
#   "kimi-acp"  -> Kimi Agent Client Protocol runtime.
BACKENDS="openai,anthropic,claude-sdk,kimi-acp"
# ---

WHEEL_PATH="$WHEEL_DIR/$WHEEL_NAME"

if [[ ! -f "$WHEEL_PATH" ]]; then
    echo "Wheel not found: $WHEEL_PATH" >&2
    echo "Usage: $0 [wheel-directory]" >&2
    exit 1
fi

echo "Creating virtual environment at $VENV_DIR ..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip

echo "Installing Open Maestro wheel with backends: $BACKENDS ..."
"$VENV_DIR/bin/pip" install "$WHEEL_PATH[$BACKENDS]"

echo "Installing kuzu-memory and mcp-vector-search ..."
"$VENV_DIR/bin/pip" install kuzu-memory mcp-vector-search

# Make sure the venv binaries are on PATH.
SHELL_RC="$HOME/.bashrc"
if [[ -n "${ZSH_VERSION:-}" ]] || [[ "$SHELL" == */zsh ]]; then
    SHELL_RC="$HOME/.zshrc"
fi

if ! grep -q "$VENV_DIR/bin" "$SHELL_RC" 2>/dev/null; then
    echo "Adding $VENV_DIR/bin to PATH in $SHELL_RC ..."
    echo "export PATH=\"$VENV_DIR/bin:\$PATH\"" >> "$SHELL_RC"
fi

echo ""
echo "Installation complete."
echo "Reload your shell or run: source $SHELL_RC"
echo "Then verify with:"
echo "  maestro --version"
echo "  kuzu-memory --version"
echo "  mcp-vector-search --version"
