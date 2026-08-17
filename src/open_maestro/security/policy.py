"""Permission policy and tool-guard evaluation for Open Maestro.

The policy engine is intentionally fail-open by default, matching the behavior
of `claude-mpm`.  Explicit agent `blocked_tools`, CLI flags, and dangerous
command patterns can turn individual checks on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from open_maestro.agents.definition import AgentDefinition

# Tools that mutate the filesystem or execute arbitrary shell commands.
_MUTATING_TOOLS: set[str] = {
    "Write",
    "Edit",
    "MultiEdit",
    "NotebookEdit",
    "Bash",
    "CreateTerminal",
    "WriteFile",
    "ApplyPatch",
}

# Agent roles that should never be allowed to mutate state.
_READ_ONLY_ROLES: set[str] = {
    "research",
    "qa",
    "documentation-reviewer",
    "code-reviewer",
    "ticketing",
    "reviewer",
}

# Dangerous shell command patterns.  These are matched against the full command
# string, not individual tokens, so whitespace variations are tolerated.
_DEFAULT_DANGEROUS_PATTERNS: list[str] = [
    r"rm\s+-rf\s+/",
    r"sudo\s+rm",
    r"\bmkfs\b",
    r"\bdd\s+if=.+\s+of=/dev/",
    r"\bshutdown\b",
    r"\breboot\b",
    r":\(\)\s*\{\s*:\|:\s*\&\s*\}",  # fork bomb
]

# Tools whose input should be scanned for dangerous patterns.
_DANGEROUS_TOOL_NAMES: set[str] = {"Bash", "CreateTerminal"}


@dataclass
class PermissionPolicy:
    """A configurable permission policy for tool calls.

    Fields:
        mode: ``allow`` (default), ``auto``, ``yolo``, or ``read-only``.
            ``yolo`` skips policy checks but still honours explicit
            ``blocked_tools``.  ``read-only`` denies all mutating tools.
        dangerous_checks_enabled: If True, scan Bash/terminal commands for
            dangerous patterns and deny them.
        blocked_tools: Explicitly blocked tool names.
        safe_tools: Tools that are always allowed, even in read-only mode.
        read_only_roles: Agent roles that are denied mutating tools.
        dangerous_patterns: Regex patterns used when dangerous checks are on.
    """

    mode: str = "allow"
    dangerous_checks_enabled: bool = False
    blocked_tools: set[str] = field(default_factory=set)
    allowed_tools: set[str] | None = None
    safe_tools: set[str] = field(
        default_factory=lambda: {
            "Read",
            "Glob",
            "Grep",
            "WebSearch",
            "WebFetch",
            "TodoRead",
            "TodoWrite",
            "NotebookRead",
            "LS",
        }
    )
    read_only_roles: set[str] = field(default_factory=lambda: set(_READ_ONLY_ROLES))
    dangerous_patterns: list[re.Pattern[str]] = field(
        default_factory=lambda: [re.compile(p, re.IGNORECASE) for p in _DEFAULT_DANGEROUS_PATTERNS]
    )

    def __post_init__(self) -> None:
        self.mode = (self.mode or "allow").lower()
        if self.mode not in {"allow", "auto", "yolo", "read-only"}:
            raise ValueError(f"Invalid permission mode: {self.mode!r}")

    def is_active(self) -> bool:
        """Return True if the policy would ever deny a tool call."""
        if self.mode == "yolo":
            return False
        if self.blocked_tools:
            return True
        if self.mode == "read-only":
            return True
        if self.read_only_roles and self.dangerous_checks_enabled:
            return True
        if self.dangerous_checks_enabled:
            return True
        return False

    def guard_text(self, extra_blocked: set[str] | None = None) -> str:
        """Return a short system-prompt guard describing active restrictions."""
        parts: list[str] = []
        blocked = self.blocked_tools | (extra_blocked or set())
        if self.allowed_tools is not None:
            parts.append(
                "You may only use these tools: "
                + ", ".join(sorted(self.allowed_tools))
                + "."
            )
        if blocked:
            parts.append(
                "You are forbidden from using these tools under any circumstances: "
                + ", ".join(sorted(blocked))
                + "."
            )
        if self.mode == "read-only":
            parts.append(
                "You are operating in read-only mode. Do not modify files, "
                "create or edit notebooks, or execute shell commands."
            )
        elif self.dangerous_checks_enabled:
            parts.append(
                "Never execute destructive system commands such as reformatting "
                "disks, deleting system directories, rebooting, or fork bombs."
            )
        return " ".join(parts)


async def evaluate(
    tool_name: str,
    tool_input: dict[str, Any],
    agent: AgentDefinition | None,
    policy: PermissionPolicy,
) -> bool:
    """Return True if the tool call is allowed under *policy*.

    Evaluation order:
    1. Explicit allow-list, if set, denies anything not listed (safe tools excepted).
    2. Explicit blocked tools are always denied.
    3. Safe tools are always allowed.
    4. ``yolo`` mode allows everything else.
    5. Read-only mode denies mutating tools.
    6. Read-only agent roles deny mutating tools.
    7. Dangerous command patterns deny Bash/terminal commands.
    """
    # 1. Explicit allow-list: if set, deny anything not allowed (except safe tools).
    if policy.allowed_tools is not None and tool_name not in policy.allowed_tools:
        if tool_name in policy.safe_tools:
            return True
        return False

    # 2. Explicit block list.
    if tool_name in policy.blocked_tools:
        return False

    # 3. Safe-tool allowlist.
    if tool_name in policy.safe_tools:
        return True

    # 4. Yolo bypasses policy checks (but not explicit blocked_tools or allowed_tools).
    if policy.mode == "yolo":
        return True

    # 5. Global read-only mode.
    if policy.mode == "read-only" and tool_name in _MUTATING_TOOLS:
        return False

    # 6. Read-only agent role.
    if (
        agent is not None
        and agent.role.lower() in {r.lower() for r in policy.read_only_roles}
        and tool_name in _MUTATING_TOOLS
    ):
        return False

    # 7. Dangerous command patterns.
    if policy.dangerous_checks_enabled and tool_name in _DANGEROUS_TOOL_NAMES:
        command_text = _extract_command_text(tool_input)
        for pattern in policy.dangerous_patterns:
            if pattern.search(command_text):
                return False

    return True


def _extract_command_text(tool_input: dict[str, Any]) -> str:
    """Flatten a tool-input dict into a single command string for matching."""
    for key in ("command", "cmd", "script", "input"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    # Fallback: concatenate any string values.
    return " ".join(
        str(v) for v in tool_input.values() if isinstance(v, (str, int, float))
    )
