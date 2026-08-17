"""Tests for tiered agent loading."""

from __future__ import annotations

from pathlib import Path

from open_maestro.agents.definition import AgentDefinition
from open_maestro.agents.loader import AgentLoader
from open_maestro.agents.registry import AgentRegistry


class TestAgentLoader:
    def test_project_overrides_user_overrides_bundled(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        user = tmp_path / "user"
        project = tmp_path / "project"
        bundled.mkdir()
        user.mkdir()
        project.mkdir()

        (bundled / "engineer.md").write_text(
            "---\nid: engineer\nname: Bundled Engineer\nrole: engineer\n---\n"
        )
        (user / "engineer.md").write_text(
            "---\nid: engineer\nname: User Engineer\nrole: engineer\n---\n"
        )
        (project / "engineer.md").write_text(
            "---\nid: engineer\nname: Project Engineer\nrole: engineer\n---\n"
        )

        registry = AgentLoader.load_tiered_dirs(project, user, bundled)
        assert registry.get("engineer").name == "Project Engineer"

    def test_missing_dirs_are_ignored(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "engineer.md").write_text(
            "---\nid: engineer\nname: Engineer\nrole: engineer\n---\n"
        )

        registry = AgentLoader.load_tiered_dirs(
            project_dir=tmp_path / "missing_project",
            user_dir=tmp_path / "missing_user",
            bundled_dir=bundled,
        )
        assert len(registry.list()) == 1

    def test_empty_tiers_return_empty_registry(self, tmp_path: Path):
        registry = AgentLoader.load_tiered_dirs(
            project_dir=tmp_path / "missing_project",
            user_dir=tmp_path / "missing_user",
            bundled_dir=tmp_path / "missing_bundled",
        )
        assert registry.list() == []


class TestAgentRegistryMerge:
    def test_merge_other_overrides_self(self):
        a = AgentDefinition(id="engineer", name="A", role="engineer")
        b = AgentDefinition(id="engineer", name="B", role="engineer")
        base = AgentRegistry({"engineer": a})
        override = AgentRegistry({"engineer": b})

        merged = base.merge(override)
        assert merged.get("engineer").name == "B"

    def test_merge_preserves_distinct_ids(self):
        a = AgentDefinition(id="engineer", name="Engineer", role="engineer")
        b = AgentDefinition(id="researcher", name="Researcher", role="research")
        merged = AgentRegistry({"engineer": a}).merge(
            AgentRegistry({"researcher": b})
        )
        assert {agent.id for agent in merged.list()} == {"engineer", "researcher"}
