"""Tests for the skills system."""

from __future__ import annotations

from pathlib import Path

from open_maestro.agents.loader import AgentLoader
from open_maestro.skills.registry import Skill, SkillRegistry


class TestSkillRegistry:
    def test_load_skill_from_markdown(self, tmp_path: Path):
        skill_file = tmp_path / "python.md"
        skill_file.write_text(
            "---\n"
            "id: python\n"
            "name: Python Best Practices\n"
            "tags:\n  - python\n  - style\n"
            "---\n\n"
            "# Python Best Practices\n\n"
            "Write idiomatic Python.\n"
        )

        skill = Skill.from_markdown(skill_file)
        assert skill.id == "python"
        assert skill.name == "Python Best Practices"
        assert "python" in skill.tags
        assert "Write idiomatic Python" in skill.content

    def test_tiered_skill_loading(self, tmp_path: Path):
        bundled = tmp_path / "bundled"
        user = tmp_path / "user"
        project = tmp_path / "project"
        bundled.mkdir()
        user.mkdir()
        project.mkdir()

        (bundled / "python.md").write_text("---\nid: python\nname: Bundled Python\n---\n")
        (user / "python.md").write_text("---\nid: python\nname: User Python\n---\n")
        (project / "python.md").write_text("---\nid: python\nname: Project Python\n---\n")

        registry = SkillRegistry.load_tiered_dirs(project, user, bundled)
        assert registry.get("python").name == "Project Python"

    def test_resolve_for_agent_ignores_missing_skills(self):
        agent_file = {
            "id": "engineer",
            "name": "Engineer",
            "role": "engineer",
            "skills": ["python", "missing-skill"],
        }
        from open_maestro.agents.definition import AgentDefinition

        agent = AgentDefinition(**agent_file)
        registry = SkillRegistry(
            {"python": Skill(id="python", name="Python", tags=[], content="abc")}
        )
        skills = registry.resolve_for_agent(agent)
        assert [skill.id for skill in skills] == ["python"]


class TestAgentSkillResolution:
    def test_skill_content_appended_to_instructions(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        skills_dir = tmp_path / "skills"
        agents_dir.mkdir()
        skills_dir.mkdir()

        (skills_dir / "python.md").write_text(
            "---\nid: python\nname: Python\n---\n\nUse type hints.\n"
        )
        (agents_dir / "engineer.md").write_text(
            "---\n"
            "id: engineer\n"
            "name: Engineer\n"
            "role: engineer\n"
            "skills:\n  - python\n"
            "---\n\n"
            "Implement features.\n"
        )

        registry = AgentLoader.load_tiered_dirs(agents_dir, None, agents_dir)
        engineer = registry.get("engineer")
        assert "Implement features" in engineer.instructions
        assert "Use type hints" in engineer.instructions
        assert "## Skill: Python" in engineer.instructions

    def test_skills_inherited_from_base_agent(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        skills_dir = tmp_path / "skills"
        agents_dir.mkdir()
        skills_dir.mkdir()

        (skills_dir / "testing.md").write_text(
            "---\nid: testing\nname: Testing\n---\n\nWrite unit tests.\n"
        )
        (agents_dir / "base-engineer.md").write_text(
            "---\n"
            "id: base-engineer\n"
            "name: Base Engineer\n"
            "role: engineer\n"
            "skills:\n  - testing\n"
            "---\n\n"
            "Base instructions.\n"
        )
        (agents_dir / "engineer.md").write_text(
            "---\n"
            "id: engineer\n"
            "name: Engineer\n"
            "role: engineer\n"
            "extends: base-engineer\n"
            "---\n\n"
            "Child instructions.\n"
        )

        registry = AgentLoader.load_tiered_dirs(agents_dir, None, agents_dir)
        engineer = registry.get("engineer")
        assert "Write unit tests" in engineer.instructions
        assert "Child instructions" in engineer.instructions
        assert "base-engineer" not in {agent.id for agent in registry.list()}

    def test_agents_without_skills_unchanged(self, tmp_path: Path):
        agents_dir = tmp_path / "agents"
        agents_dir.mkdir()

        (agents_dir / "researcher.md").write_text(
            "---\n"
            "id: researcher\n"
            "name: Researcher\n"
            "role: research\n"
            "---\n\n"
            "Research only.\n"
        )

        registry = AgentLoader.load_tiered_dirs(agents_dir, None, agents_dir)
        assert registry.get("researcher").instructions == "Research only."
