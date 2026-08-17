"""Tests for Git-based agent/skill source syncing."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from open_maestro.agents.loader import AgentLoader
from open_maestro.skills.registry import SkillRegistry
from open_maestro.sources.config import (
    CLAUDE_MPM_SKILLS_URL,
    DEFAULT_SKILL_SOURCE_NAME,
    SourceRegistry,
    default_source_name,
)
from open_maestro.sources.sync import GitSource, sync_source


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a local Git repo with an agent and a skill."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    (repo / "agents").mkdir()
    (repo / "skills").mkdir()

    (repo / "agents" / "frontend.md").write_text(
        "---\nid: frontend\nname: Frontend Engineer\nrole: engineer\n---\n\nUI work.\n"
    )
    (repo / "skills" / "react.md").write_text(
        "---\nid: react\nname: React\ntags: [frontend]\n---\n\nUse hooks.\n"
    )

    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return repo


class TestGitSourceSync:
    def test_clone_new_source(self, tmp_path: Path, tmp_git_repo: Path):
        source = GitSource(
            name="demo",
            kind="agents",
            url=str(tmp_git_repo),
            ref="main",
            subdir="agents",
        )
        updated = sync_source(source)

        assert updated.last_sync is not None
        assert updated.checkout_path.exists()
        assert (updated.content_path / "frontend.md").exists()

    def test_update_existing_source(self, tmp_path: Path, tmp_git_repo: Path):
        source = GitSource(
            name="demo",
            kind="agents",
            url=str(tmp_git_repo),
            ref="main",
            subdir="agents",
        )
        sync_source(source)

        # Add a new file upstream.
        (tmp_git_repo / "agents" / "backend.md").write_text(
            "---\nid: backend\nname: Backend Engineer\nrole: engineer\n---\n\nAPI work.\n"
        )
        subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add backend"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        updated = sync_source(source, force=True)
        assert (updated.content_path / "backend.md").exists()

    def test_skip_sync_when_fresh(self, tmp_path: Path, tmp_git_repo: Path):
        source = GitSource(
            name="demo",
            kind="agents",
            url=str(tmp_git_repo),
            ref="main",
            subdir="agents",
        )
        sync_source(source)

        # Add a new file upstream.
        (tmp_git_repo / "agents" / "backend.md").write_text(
            "---\nid: backend\nname: Backend Engineer\nrole: engineer\n---\n\nAPI work.\n"
        )
        subprocess.run(["git", "add", "."], cwd=tmp_git_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add backend"],
            cwd=tmp_git_repo,
            check=True,
            capture_output=True,
        )

        # Sync with a long TTL should skip.
        updated = sync_source(source, ttl_seconds=3600)
        assert not (updated.content_path / "backend.md").exists()


class TestSourceRegistry:
    def test_round_trip_save_load(self, tmp_path: Path):
        registry = SourceRegistry()
        registry.add(GitSource(name="skills", kind="skills", url="https://example.com/s"))
        registry.add(GitSource(name="agents", kind="agents", url="https://example.com/a"))

        path = tmp_path / "sources.yaml"
        registry.save(path)

        loaded = SourceRegistry.load(path)
        assert len(loaded.sources) == 2
        assert loaded.get("skills", "skills").url == "https://example.com/s"

    def test_add_replaces_existing(self, tmp_path: Path):
        registry = SourceRegistry()
        registry.add(GitSource(name="skills", kind="skills", url="https://old"))
        registry.add(GitSource(name="skills", kind="skills", url="https://new"))
        assert len(registry.sources) == 1
        assert registry.sources[0].url == "https://new"

    def test_default_source_name(self):
        assert (
            default_source_name("https://github.com/bobmatnyc/claude-mpm-skills", "skills")
            == "skills"
        )
        assert default_source_name("https://github.com/user/my-agents.git", "agents") == "my-agents"

    def test_default_skill_source_when_registry_missing(self, tmp_path: Path):
        missing = tmp_path / "does-not-exist.yaml"
        loaded = SourceRegistry.load(missing)
        source = loaded.get(DEFAULT_SKILL_SOURCE_NAME, "skills")
        assert source is not None
        assert source.url == CLAUDE_MPM_SKILLS_URL
        assert "docs/**/*.md" in source.exclude

    def test_default_sources_not_added_when_registry_exists(self, tmp_path: Path):
        path = tmp_path / "sources.yaml"
        path.write_text("sources: []\n")
        loaded = SourceRegistry.load(path)
        assert loaded.get(DEFAULT_SKILL_SOURCE_NAME, "skills") is None


class TestSkillRegistryExclude:
    def test_skips_excluded_paths(self, tmp_path: Path):
        (tmp_path / "README.md").write_text("# readme")
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "guide.md").write_text("---\nid: guide\n---\n")
        skill_dir = tmp_path / "universal" / "web"
        skill_dir.mkdir(parents=True)
        (skill_dir / "api.md").write_text("---\nid: api-design\n---\nDesign APIs.\n")

        registry = SkillRegistry.from_directory(
            tmp_path,
            exclude=["**/README.md", "docs/**/*.md"],
        )
        ids = {skill.id for skill in registry.list()}
        assert "api-design" in ids
        assert "guide" not in ids
        assert "README" not in ids


class TestAgentLoaderSources:
    def test_loads_agents_from_source(self, tmp_path: Path, tmp_git_repo: Path):
        source = GitSource(
            name="demo",
            kind="agents",
            url=str(tmp_git_repo),
            ref="main",
            subdir="agents",
        )
        sync_source(source)

        registry = AgentLoader.load_tiered_dirs(
            None,
            None,
            tmp_path / "empty",
            agent_sources=[source],
        )
        assert "frontend" in {agent.id for agent in registry.list()}

    def test_loads_skills_from_source(self, tmp_path: Path, tmp_git_repo: Path):
        source = GitSource(
            name="demo",
            kind="skills",
            url=str(tmp_git_repo),
            ref="main",
            subdir="skills",
        )
        sync_source(source)

        agent_source = tmp_path / "agents"
        agent_source.mkdir()
        (agent_source / "fe.md").write_text(
            "---\nid: fe\nname: FE\nrole: engineer\nskills:\n  - react\n---\n\nDo UI.\n"
        )

        registry = AgentLoader.load_tiered_dirs(
            agent_source,
            None,
            tmp_path / "empty",
            skill_sources=[source],
        )
        fe = registry.get("fe")
        assert "Use hooks" in fe.instructions
