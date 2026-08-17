"""Tests for BASE-AGENT inheritance."""

from __future__ import annotations

from pathlib import Path

import pytest

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.loader import AgentLoader
from open_maestro.config.capabilities import (
    CodingStrength,
    ReasoningLevel,
    RequiredCapabilities,
)


class TestAgentDefinitionMerge:
    def test_child_overrides_base_scalars(self):
        base = AgentDefinition(
            id="base-engineer",
            name="Base Engineer",
            role="engineer",
            model="fast",
            tools=["Read"],
            description="Base desc",
            instructions="Base instructions.",
        )
        child = AgentDefinition(
            id="engineer",
            name="Child Engineer",
            role="engineer",
            model="smart",
            tools=["Edit"],
            instructions="Child instructions.",
        )
        merged = AgentDefinition.merge(base, child)
        assert merged.name == "Child Engineer"
        assert merged.model == "smart"
        assert set(merged.tools) == {"Read", "Edit"}
        assert "Base instructions" in merged.instructions
        assert "Child instructions" in merged.instructions

    def test_inherits_base_model_when_child_default(self):
        base = AgentDefinition(
            id="base", name="Base", role="r", model="smart"
        )
        child = AgentDefinition(id="c", name="C", role="r")
        merged = AgentDefinition.merge(base, child)
        assert merged.model == "smart"

    def test_required_capabilities_merged(self):
        base = AgentDefinition(
            id="base",
            name="Base",
            role="r",
            required_capabilities=RequiredCapabilities(
                reasoning=ReasoningLevel.DEEP
            ),
        )
        child = AgentDefinition(
            id="c",
            name="C",
            role="r",
            required_capabilities=RequiredCapabilities(
                coding_strength=CodingStrength.HIGH
            ),
        )
        merged = AgentDefinition.merge(base, child)
        assert merged.required_capabilities.reasoning == ReasoningLevel.DEEP
        assert merged.required_capabilities.coding_strength == CodingStrength.HIGH


class TestAgentLoaderInheritance:
    def test_child_extends_base(self, tmp_path: Path):
        (tmp_path / "base-engineer.md").write_text(
            "---\n"
            "id: base-engineer\n"
            "name: Base Engineer\n"
            "role: engineer\n"
            "model: fast\n"
            "tools:\n  - Read\n"
            "---\n\n"
            "Base instructions."
        )
        (tmp_path / "engineer.md").write_text(
            "---\n"
            "id: engineer\n"
            "extends: base-engineer\n"
            "name: Engineer\n"
            "role: engineer\n"
            "tools:\n  - Edit\n"
            "---\n\n"
            "Child instructions."
        )

        registry = AgentLoader.load_tiered_dirs(tmp_path, None, tmp_path)
        engineer = registry.get("engineer")
        assert engineer.name == "Engineer"
        assert set(engineer.tools) == {"Read", "Edit"}
        assert engineer.model == "fast"
        assert "Base instructions" in engineer.instructions
        assert "Child instructions" in engineer.instructions
        assert "base-engineer" not in {a.id for a in registry.list()}

    def test_deep_inheritance_chain(self, tmp_path: Path):
        (tmp_path / "base.md").write_text(
            "---\nid: base\nname: Base\nrole: r\nmodel: fast\n---\n"
        )
        (tmp_path / "middle.md").write_text(
            "---\nid: middle\nname: Middle\nrole: r\nextends: base\nmodel: smart\n---\n"
        )
        (tmp_path / "child.md").write_text(
            "---\nid: child\nname: Child\nrole: r\nextends: middle\n---\n"
        )

        registry = AgentLoader.load_tiered_dirs(tmp_path, None, tmp_path)
        assert registry.get("child").model == "smart"

    def test_cyclic_inheritance_raises(self, tmp_path: Path):
        (tmp_path / "a.md").write_text(
            "---\nid: a\nname: A\nrole: r\nextends: b\n---\n"
        )
        (tmp_path / "b.md").write_text(
            "---\nid: b\nname: B\nrole: r\nextends: a\n---\n"
        )

        with pytest.raises(ValueError, match="Cyclic agent inheritance"):
            AgentLoader.load_tiered_dirs(tmp_path, None, tmp_path)
