"""Tests for the permission policy engine."""

from __future__ import annotations

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.security.policy import PermissionPolicy, evaluate


class TestPermissionPolicyDefaults:
    async def test_default_policy_allows_any_tool(self):
        policy = PermissionPolicy()
        assert await evaluate("Bash", {"command": "rm -rf /"}, None, policy) is True

    async def test_explicit_blocked_tool_denied(self):
        policy = PermissionPolicy(blocked_tools={"Write"})
        assert await evaluate("Write", {"path": "x"}, None, policy) is False

    async def test_safe_tools_allowed_even_when_blocked_elsewhere(self):
        policy = PermissionPolicy(blocked_tools={"Read"})
        # blocked_tools takes precedence over safe list
        assert await evaluate("Read", {"path": "x"}, None, policy) is False


class TestReadOnlyMode:
    async def test_read_only_mode_denies_mutating_tools(self):
        policy = PermissionPolicy(mode="read-only")
        assert await evaluate("Edit", {"path": "x"}, None, policy) is False
        assert await evaluate("Read", {"path": "x"}, None, policy) is True

    async def test_read_only_agent_role_denies_mutating_tools(self):
        policy = PermissionPolicy()
        agent = AgentDefinition(id="qa", name="QA", role="qa")
        assert await evaluate("Edit", {"path": "x"}, agent, policy) is False
        assert await evaluate("Read", {"path": "x"}, agent, policy) is True


class TestDangerousCommandPatterns:
    async def test_dangerous_patterns_denied_when_enabled(self):
        policy = PermissionPolicy(dangerous_checks_enabled=True)
        assert await evaluate("Bash", {"command": "rm -rf /"}, None, policy) is False
        assert await evaluate("Bash", {"command": "echo hello"}, None, policy) is True

    async def test_dangerous_patterns_allowed_when_disabled(self):
        policy = PermissionPolicy(dangerous_checks_enabled=False)
        assert await evaluate("Bash", {"command": "rm -rf /"}, None, policy) is True

    async def test_non_bash_tools_ignore_dangerous_checks(self):
        policy = PermissionPolicy(dangerous_checks_enabled=True)
        assert await evaluate("Write", {"command": "rm -rf /"}, None, policy) is True


class TestYoloMode:
    async def test_yolo_bypasses_policy_but_not_blocked_tools(self):
        policy = PermissionPolicy(mode="yolo", blocked_tools={"Write"})
        assert await evaluate("Write", {"path": "x"}, None, policy) is False
        assert await evaluate("Bash", {"command": "rm -rf /"}, None, policy) is True


class TestAllowedTools:
    async def test_allowed_tools_denies_anything_not_allowed(self):
        policy = PermissionPolicy(allowed_tools={"Read", "Grep"})
        assert await evaluate("Read", {"path": "x"}, None, policy) is True
        assert await evaluate("Grep", {"pattern": "x"}, None, policy) is True
        assert await evaluate("Write", {"path": "x"}, None, policy) is False

    async def test_safe_tools_allowed_even_when_not_in_allowed_list(self):
        policy = PermissionPolicy(allowed_tools={"Bash"})
        assert await evaluate("Read", {"path": "x"}, None, policy) is True

    async def test_blocked_tools_still_denied_when_allowed_list_present(self):
        policy = PermissionPolicy(allowed_tools={"Read", "Write"}, blocked_tools={"Write"})
        assert await evaluate("Write", {"path": "x"}, None, policy) is False
        assert await evaluate("Read", {"path": "x"}, None, policy) is True

    def test_guard_text_lists_allowed_tools(self):
        policy = PermissionPolicy(allowed_tools={"Read", "Grep"})
        text = policy.guard_text()
        assert "Read" in text
        assert "Grep" in text
        assert "only use these tools" in text.lower()


class TestGuardText:
    def test_guard_text_lists_blocked_tools(self):
        policy = PermissionPolicy(blocked_tools={"Write", "Bash"})
        text = policy.guard_text()
        assert "Write" in text
        assert "Bash" in text

    def test_guard_text_describes_read_only_mode(self):
        policy = PermissionPolicy(mode="read-only")
        text = policy.guard_text()
        assert "read-only" in text.lower()

    def test_guard_text_describes_dangerous_checks(self):
        policy = PermissionPolicy(dangerous_checks_enabled=True)
        text = policy.guard_text()
        assert "destructive" in text.lower()


class TestPolicyValidation:
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            PermissionPolicy(mode="invalid")
