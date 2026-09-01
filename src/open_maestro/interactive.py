"""Interactive / chat mode for Open Maestro.

Loads the project context once and then loops on user input, maintaining
conversation history and a single session across turns.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shlex
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from open_maestro.agents.loader import AgentLoader
from open_maestro.agents.registry import AgentRegistry
from open_maestro.config.capabilities import (
    CodingStrength,
    CostLevel,
    LatencyHint,
    ReasoningLevel,
    TaskProfile,
    TaskProfiler,
)
from open_maestro.context.budget import ContextBudget
from open_maestro.events.bus import EventBus
from open_maestro.events.progress import InteractiveProgressHandler, ProgressIndicator
from open_maestro.events.stream import StreamingHandler
from open_maestro.mcp.config import load_mcp_config
from open_maestro.memory.kuzu_client import KuzuMemoryClient
from open_maestro.milestones import (
    DashboardPublishHistoryStore,
    MilestoneDetector,
    MilestoneStatus,
    MilestoneStore,
    PromptHistoryStore,
    advance_milestone_on_prompt,
    format_run_indicator,
    get_current_or_next_milestone_prompts,
    get_prompts_for_milestone,
    handle_blocker_command,
    handle_complete_command,
    handle_next_command,
    handle_prompts_command,
    handle_track_command,
)
from open_maestro.monitor.live import Monitor
from open_maestro.orchestrator.pm import ProjectManager
from open_maestro.orchestrator.router import LLMTaskRouter
from open_maestro.runtime.base import AgentConfig
from open_maestro.runtime.factory import create_runtime, select_runtime_for_task
from open_maestro.search.vector_client import VectorSearchClient
from open_maestro.session.store import SessionStore
from open_maestro.sources.config import SourceRegistry
from open_maestro.sources.sync import sync_source
from open_maestro.todos.commands import handle_todo_command

logger = logging.getLogger(__name__)

try:
    import readline
except ImportError:  # pragma: no cover
    readline = None  # type: ignore[assignment]

_HISTORY_FILE = Path.home() / ".open-maestro" / "interactive_history"


def _setup_readline() -> None:
    """Enable line editing, arrow-key navigation, and persistent history."""
    if readline is None:
        return
    try:
        if _HISTORY_FILE.exists():
            readline.read_history_file(str(_HISTORY_FILE))
    except Exception as exc:
        logger.debug("Failed to load interactive history: %s", exc)
    try:
        readline.set_history_length(1000)
    except Exception:
        pass


def _save_readline_history() -> None:
    """Persist interactive command history for the next session."""
    if readline is None:
        return
    try:
        _HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        readline.write_history_file(str(_HISTORY_FILE))
    except Exception as exc:
        logger.debug("Failed to save interactive history: %s", exc)


@dataclass
class InteractiveState:
    """Mutable state for an interactive Maestro session."""

    history: list[dict[str, str]] = field(default_factory=list)
    session_id: str | None = None
    # Runtime that produced session_id (e.g. "kimi-cli", "claude-cli").
    # Used to avoid resuming a session on a different backend.
    session_runtime: str | None = None
    agent_id: str | None = None
    model: str | None = None
    show_plan_next: bool = False
    dry_run_next: bool = False
    reasoning: bool = False
    fast: bool = False
    chain: bool = True
    prefer_local: bool = False
    turn: int = 0
    # Prompts most recently shown by /next or /prompts, available for selection
    # by typing their number (1-indexed). Each tuple is (prompt_id, title, rendered_text).
    suggested_prompts: list[tuple[str, str, str]] = field(default_factory=list)
    # Prompts queued by /select for execution in subsequent turns.
    # Each tuple is (prompt_id, rendered_text, edited, title).
    pending_prompts: list[tuple[str, str, bool, str]] = field(default_factory=list)
    # Epic/milestone context for the most recently shown suggested prompts.
    current_epic_id: str | None = None
    current_milestone_id: str | None = None


def _banner(
    session_id: str | None = None,
    publish_line: str | None = None,
) -> str:
    session_line = f"[session: {session_id}]\n" if session_id else ""
    publish_section = f"{publish_line}\n" if publish_line else ""
    return (
        "Open Maestro interactive mode\n"
        + session_line
        + publish_section
        + "Type a task and press Enter. Commands:\n"
        "  /agent <id>       pin an agent for the next turn(s)\n"
        "  /model <model>    override the model for the next turn(s)\n"
        "  /plan             show the execution plan for the next prompt only\n"
        "  /dry              dry-run the next prompt only\n"
        "  /milestones       show project milestone progress\n"
        "  /next             suggest the next milestone action\n"
        "  /select           open a TUI to select and edit suggested prompts\n"
        "  /prompts <milestone> [epic]  list playbook prompts for a milestone\n"
        "  /complete <id>    mark a milestone complete (add --force to override)\n"
        "  /blocker <id> <reason>  record a milestone blocker\n"
        "  /track <id> <status>    update a milestone status inside an epic\n"
        "  /remember <text>  store a key decision or finding to project memory\n"
        "  /memory <query>   recall relevant memories from project memory\n"
        "  /todo ...         manage project todos (add/list/done/block/delete/clear)\n"
        "  /reasoning        toggle reasoning preference\n"
        "  /fast             toggle fast/cheap preference\n"
        "  /local            prefer local models; ask before escalating to frontier\n"
        "  /chain            toggle multi-agent chain mode (default: on)\n"
        "  /reset            clear conversation history\n"
        "  /help             show this message\n"
        "  /exit, /quit      leave interactive mode\n"
        "\nProject memory is enabled by default in interactive mode."
    )


def _build_task_profile(
    prompt: str,
    state: InteractiveState,
    args: Any,
) -> TaskProfile:
    """Build a task profile from the current state and CLI defaults."""
    reasoning_depth = ReasoningLevel.DEEP if state.reasoning else None
    if state.fast:
        latency_preference = LatencyHint.LOW
        cost_preference = CostLevel.LOW
    else:
        latency_preference = (
            LatencyHint(args.latency_preference) if args.latency_preference else None
        )
        cost_preference = (
            CostLevel(args.cost_preference) if args.cost_preference else None
        )

    return TaskProfiler.from_prompt(
        prompt,
        reasoning_depth=reasoning_depth,
        coding_strength=CodingStrength(args.coding_strength) if args.coding_strength else None,
        context_tokens_estimate=args.context_tokens,
        latency_preference=latency_preference,
        cost_preference=cost_preference,
        needs_vision=args.vision or None,
    )


async def _store_turn_memory(
    state: InteractiveState,
    result: Any,
    memory: KuzuMemoryClient,
) -> None:
    """Store a concise summary of the just-completed turn to project memory."""
    user_prompt = ""
    for turn in reversed(state.history):
        if turn["role"] == "user":
            user_prompt = turn["content"]
            break

    summary = result.text[:800].strip()
    content = (
        f"Turn {state.turn}: {user_prompt}\n"
        f"Agent: {result.metadata.get('selected_agent', 'unknown')}\n"
        f"Finding: {summary}"
    )
    memory_type = "decision" if _looks_like_decision(user_prompt) else "note"
    try:
        await memory.store(
            content,
            memory_type=memory_type,
            metadata={
                "source": "interactive",
                "turn": state.turn,
                "session_id": state.session_id or "",
            },
        )
    except Exception as exc:
        logger.warning("Failed to store turn memory: %s", exc)


def _looks_like_decision(prompt: str) -> bool:
    """Heuristic for whether a prompt produced a decision or finding."""
    keywords = {
        "decide",
        "decision",
        "conclude",
        "conclusion",
        "finding",
        "recommend",
        "choose",
        "select",
    }
    lowered = prompt.lower()
    return any(kw in lowered for kw in keywords)


def _assemble_prompt(prompt: str, history: list[dict[str, str]]) -> str:
    """Combine conversation history with the current user prompt."""
    parts: list[str] = []
    if history:
        parts.append("Conversation so far:")
        for turn in history:
            role = turn["role"].capitalize()
            parts.append(f"{role}: {turn['content']}")
        parts.append("")
    parts.append(f"Current task: {prompt}")
    return "\n".join(parts)


async def _handle_command(
    raw: str,
    state: InteractiveState,
    registry: AgentRegistry,
    memory: KuzuMemoryClient | None,
) -> str | None:
    """Parse a slash command and update state.

    Returns a message to print, or None if the input should be processed as a
    normal prompt.
    """
    raw = raw.strip()
    if not raw.startswith("/"):
        return None

    parts = shlex.split(raw[1:])
    if not parts:
        return None

    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("exit", "quit"):
        return "__EXIT__"

    if cmd == "help":
        return _banner()

    if cmd == "reset":
        state.history.clear()
        state.session_id = None
        return "Conversation history and session cleared."

    if cmd == "plan":
        state.show_plan_next = True
        return "Next response will show the execution plan."

    if cmd == "dry":
        state.dry_run_next = True
        return "Next response will be a dry run."

    if cmd == "reasoning":
        state.reasoning = not state.reasoning
        return f"Reasoning preference: {'on' if state.reasoning else 'off'}."

    if cmd == "fast":
        state.fast = not state.fast
        return f"Fast/cheap preference: {'on' if state.fast else 'off'}."

    if cmd == "local":
        state.prefer_local = not state.prefer_local
        return (
            f"Local preference: {'on' if state.prefer_local else 'off'}."
            + (" Maestro will ask before escalating to a frontier model." if state.prefer_local else "")
        )

    if cmd == "chain":
        state.chain = not state.chain
        return f"Multi-agent chain mode: {'on' if state.chain else 'off'}."

    if cmd == "agent":
        if not args:
            available = ", ".join(sorted(a.id for a in registry.list()))
            return f"Usage: /agent <id>. Available agents: {available}"
        agent_id = args[0]
        try:
            found = registry.get(agent_id)
        except KeyError:
            found = None
        if found is None:
            available = ", ".join(sorted(a.id for a in registry.list()))
            return f"Unknown agent '{agent_id}'. Available: {available}"
        state.agent_id = agent_id
        return f"Agent pinned to '{agent_id}' for this session."

    if cmd == "model":
        if not args:
            return "Usage: /model <model-alias>"
        state.model = args[0]
        return f"Model override set to '{state.model}' for this session."

    if cmd == "remember":
        if not args:
            return "Usage: /remember <key decision or finding>"
        if memory is None:
            return "Memory is not available (kuzu-memory CLI missing)."
        note = " ".join(args)
        try:
            await memory.store(
                note,
                memory_type="decision",
                metadata={"source": "interactive", "turn": state.turn},
            )
            return "Stored to project memory."
        except Exception as exc:
            logger.debug("Failed to store memory: %s", exc)
            return "Memory storage failed (run with --verbose for details)."

    if cmd == "memory":
        if memory is None:
            return "Memory is not available (kuzu-memory CLI missing)."
        query = " ".join(args) if args else " ".join(
            turn["content"]
            for turn in state.history[-6:]
            if turn["role"] == "user"
        )
        try:
            memories = await memory.recall(query)
        except Exception as exc:
            logger.debug("Failed to recall memory: %s", exc)
            return "Memory recall failed (run with --verbose for details)."
        if not memories:
            return "No relevant memories found."
        return "Relevant memories:\n" + "\n".join(f"  - {m}" for m in memories)

    if cmd == "milestones":
        return _handle_milestones_command(Path.cwd())

    if cmd == "next":
        prompts, epic_id, milestone_id = get_current_or_next_milestone_prompts(
            Path.cwd()
        )
        state.current_epic_id = epic_id
        state.current_milestone_id = milestone_id
        state.suggested_prompts = [
            (t.id, t.title, rendered) for t, rendered in prompts
        ]
        # Show milestone context without the prompt list; the TUI will present
        # the prompts and let the user pick, edit, or skip them in one step.
        info = handle_next_command(Path.cwd(), include_prompts=False)
        print(info)
        if not state.suggested_prompts:
            return "No suggested prompts for this milestone."
        history_store = PromptHistoryStore(Path.cwd())
        history_store.backfill_from_artifacts()
        run_history = history_store.load()
        try:
            selected = await _select_prompts_tui(
                state.suggested_prompts,
                run_history=run_history,
                epic_id=epic_id,
                milestone_id=milestone_id,
            )
        except TUICancelled:
            state.suggested_prompts = []
            state.pending_prompts = []
            return "Cancelled."
        state.suggested_prompts = []
        if not selected:
            return "No prompts selected."
        pending: list[tuple[str, str, bool, str]] = []
        for prompt_id, title, rendered in selected:
            try:
                action = await _prompt_action_tui(title)
            except TUICancelled:
                state.pending_prompts = []
                return "Cancelled."
            if action == "skip":
                continue
            edited = False
            if action == "edit":
                try:
                    rendered = await _edit_prompt_tui(rendered)
                    edited = True
                except TUICancelled:
                    state.pending_prompts = []
                    return "Cancelled."
            text = rendered.strip()
            if text:
                pending.append((prompt_id, text, edited, title))
        if not pending:
            return "No prompts selected for execution."
        state.pending_prompts = pending
        return f"Selected prompt: {selected[0][1]}"

    if cmd == "prompts":
        result = handle_prompts_command(Path.cwd(), args)
        if args:
            milestone_id = args[0]
            epic_id = args[1] if len(args) > 1 else None
            state.current_epic_id = epic_id
            state.current_milestone_id = milestone_id
            store = MilestoneStore(Path.cwd())
            plan = store.load()
            prompts = get_prompts_for_milestone(
                Path.cwd(), milestone_id, plan=plan, epic_id=epic_id
            )
            state.suggested_prompts = [
                (t.id, t.title, rendered) for t, rendered in prompts
            ]
        return result

    if cmd == "select":
        if not state.suggested_prompts:
            return "No suggested prompts to select. Run /next or /prompts first."
        # Run questionary asynchronously so it does not start a nested event loop.
        history_store = PromptHistoryStore(Path.cwd())
        history_store.backfill_from_artifacts()
        run_history = history_store.load()
        try:
            selected = await _select_prompts_tui(
                state.suggested_prompts,
                run_history=run_history,
                epic_id=state.current_epic_id,
                milestone_id=state.current_milestone_id,
            )
        except TUICancelled:
            state.suggested_prompts = []
            state.pending_prompts = []
            return "Cancelled."
        if not selected:
            return "No prompts selected."
        # For each selected prompt, ask execute/edit/skip and queue for execution.
        pending: list[tuple[str, str, bool, str]] = []
        for prompt_id, title, rendered in selected:
            try:
                action = await _prompt_action_tui(title)
            except TUICancelled:
                state.suggested_prompts = []
                state.pending_prompts = []
                return "Cancelled."
            if action == "skip":
                continue
            edited = False
            if action == "edit":
                try:
                    rendered = await _edit_prompt_tui(rendered)
                    edited = True
                except TUICancelled:
                    state.suggested_prompts = []
                    state.pending_prompts = []
                    return "Cancelled."
            text = rendered.strip()
            if text:
                pending.append((prompt_id, text, edited, title))
        state.suggested_prompts = []
        if not pending:
            return "No prompts selected for execution."
        state.pending_prompts = pending
        return f"Selected prompt: {selected[0][1]}"

    if cmd == "complete":
        return handle_complete_command(Path.cwd(), args)

    if cmd == "blocker":
        return handle_blocker_command(Path.cwd(), args)

    if cmd == "track":
        return handle_track_command(Path.cwd(), args)

    if cmd == "todo":
        return handle_todo_command(Path.cwd(), args)

    return f"Unknown command '/{cmd}'. Type /help for available commands."


_REPO_ANALYSIS_KEYWORDS = {
    "analyze",
    "analysis",
    "analyse",
    "codebase",
    "code base",
    "repo",
    "repository",
    "project",
    "review",
    "examine",
    "inspect",
    "audit",
    "explore",
    "understand",
    "study",
}

# Phrases that indicate project-management follow-ups rather than repo analysis.
_PROJECT_MANAGEMENT_PHRASES = {
    "milestone",
    "milestones",
    "epic",
    "epics",
    "backlog",
    "sprint",
    "sprints",
}

_PATH_RE = re.compile(
    r"(?:\s|^)((?:~|\.\.?)?/[a-zA-Z0-9_./-]+)(?:\s|$)"
)

# Remote repository URLs (http, https, git, ssh).
_URL_RE = re.compile(
    r"(?:\s|^)(?:https?://|git@|git://)[^\s]+(?:\.git)?(?:\s|$)",
    re.IGNORECASE,
)


def _extract_remote_urls(prompt: str) -> list[str]:
    """Return remote repository URLs mentioned in *prompt*."""
    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(prompt):
        raw = match.group(0).strip()
        if raw and raw not in seen:
            urls.append(raw)
            seen.add(raw)
    return urls


def _extract_candidate_paths(prompt: str) -> list[Path]:
    """Return existing filesystem paths mentioned in *prompt*."""
    candidates: list[Path] = []
    seen: set[Path] = set()
    for match in _PATH_RE.finditer(prompt):
        raw = match.group(0).strip()
        if not raw:
            continue
        try:
            expanded = Path(raw).expanduser()
        except Exception:
            continue
        try:
            resolved = expanded.resolve()
        except Exception:
            resolved = expanded
        if resolved.exists() and resolved not in seen:
            candidates.append(resolved)
            seen.add(resolved)
    return candidates


def _looks_like_repo_analysis(prompt: str) -> bool:
    lowered = prompt.lower()
    return any(kw in lowered for kw in _REPO_ANALYSIS_KEYWORDS)


def _is_url(text: str) -> bool:
    """Return True if *text* looks like a remote URL rather than a local path."""
    lowered = text.lower()
    return lowered.startswith(("http://", "https://", "git@", "git://"))


_FOLLOW_UP_PHRASES = {
    "did you",
    "have you",
    "was it",
    "the findings",
    "your analysis",
    "that repo",
    "the result",
    "what you said",
    "what we",
    "you found",
    "summarize",
    "explain",
    "clarify",
    "recap",
    "tell me about",
    "update the dashboard",
    "sync the dashboard",
    "publish the dashboard",
}

_QUESTION_PREFIXES = {
    "did you",
    "have you",
    "has it",
    "which",
    "what did",
    "what do",
    "what was",
    "what is",
    "when did",
    "where did",
    "was it",
    "were they",
    "is it",
    "are they",
    "how did",
    "how do",
    "how was",
    "how is",
    "why did",
    "why do",
    "why was",
    "why is",
    "why are",
    "why were",
    "can you tell me",
    "could you tell me",
}


def _looks_like_follow_up(
    prompt: str,
    history: list[dict[str, str]],
    memories: list[str],
) -> bool:
    """Return True if the prompt refers to previous work rather than new repo work."""
    lowered = prompt.lower()

    # Direct question about a past action.
    if any(lowered.startswith(prefix) for prefix in _QUESTION_PREFIXES):
        return True

    # Contains follow-up phrasing and does NOT also request new repo exploration.
    has_follow_up_phrase = any(phrase in lowered for phrase in _FOLLOW_UP_PHRASES)
    requests_new_repo_work = any(
        action in lowered for action in _REPO_ANALYSIS_KEYWORDS
    )
    if has_follow_up_phrase and not requests_new_repo_work:
        return True

    # Short, vague prompt with conversation/memory context and no explicit path.
    if (
        (len(history) > 2 or memories)
        and len(prompt.split()) <= 6
        and not _extract_remote_urls(prompt)
        and not _extract_candidate_paths(prompt)
    ):
        return True

    return False


async def _maybe_clarify_repo_path(
    prompt: str,
    history: list[dict[str, str]],
    memory: KuzuMemoryClient | None,
    from_playbook: bool = False,
) -> tuple[str, Path | None]:
    """Ask the user which repo to analyze when the target is ambiguous.

    Returns (updated_prompt, resolved_path). If no clarification is needed,
    returns the original prompt and None.
    """
    remote_urls = _extract_remote_urls(prompt)

    memories: list[str] = []
    if memory is not None:
        try:
            memories = await memory.recall(prompt)
        except Exception:
            pass

    if remote_urls:
        pass  # proceed to clarification
    elif _looks_like_follow_up(prompt, history, memories):
        return prompt, None
    elif not _looks_like_repo_analysis(prompt):
        return prompt, None
    elif from_playbook and not remote_urls:
        # Playbook prompts are scoped to the current project; only ask if the
        # prompt itself points somewhere else explicitly.
        return prompt, None

    candidates = _extract_candidate_paths(prompt)

    # Project-management prompts (milestones, epics, backlogs) refer to the
    # current project context, not a codebase to analyze, unless they include
    # an explicit path or URL.
    if (
        not remote_urls
        and not candidates
        and any(p in prompt.lower() for p in _PROJECT_MANAGEMENT_PHRASES)
    ):
        return prompt, None

    cwd = Path.cwd().resolve()

    # One clear, existing local path that differs from cwd -> use it without asking.
    if len(candidates) == 1 and candidates[0] != cwd and not remote_urls:
        path = candidates[0]
        return (
            f"{prompt}\n\n[Clarified repo location: analyze the codebase at {path}]",
            path,
        )

    import questionary

    default = str(cwd)
    choices: list[Any] = [
        questionary.Choice(title=f"Current directory: {default}", value=default),
    ]
    for candidate in candidates:
        if candidate != cwd:
            choices.append(
                questionary.Choice(title=str(candidate), value=str(candidate))
            )
    if remote_urls:
        choices.append(
            questionary.Choice(
                title=f"Clone remote repo: {remote_urls[0]}",
                value=f"__clone__:{remote_urls[0]}",
            )
        )
    choices.append(
        questionary.Choice(title="Clone remote repo...", value="__clone_prompt__")
    )
    choices.append(questionary.Choice(title="Other local path...", value="__other__"))

    question = questionary.select(
        "Which repository should I analyze?",
        choices=choices,
    )
    _add_escape_binding(question)
    try:
        selected = await question.application.run_async()
    except TUICancelled:
        return prompt, None

    if selected == "__other__":
        question = questionary.text(
            "Enter the local repository path:",
            default=default,
        )
        _add_escape_binding(question)
        try:
            typed = await question.application.run_async()
        except TUICancelled:
            return prompt, None
        selected = typed.strip() if typed else default
    elif selected == "__clone_prompt__":
        question = questionary.text(
            "Enter the remote repository URL (e.g. https://github.com/user/repo):",
        )
        _add_escape_binding(question)
        try:
            url = await question.application.run_async()
        except TUICancelled:
            return prompt, None
        url = url.strip() if url else ""
        if not url:
            return prompt, None
        question = questionary.text(
            f"Local path to clone {url} into:",
            default=str(cwd / _default_clone_dir(url)),
        )
        _add_escape_binding(question)
        try:
            typed = await question.application.run_async()
        except TUICancelled:
            return prompt, None
        clone_path = typed.strip() if typed else str(cwd / _default_clone_dir(url))
        path = Path(clone_path).expanduser().resolve()
        if not path.exists():
            print(f"Cloning {url} into {path}...")
            try:
                import subprocess

                subprocess.run(
                    ["git", "clone", url, str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except Exception as exc:
                print(f"Failed to clone {url}: {exc}")
                return prompt, None
        return (
            f"{prompt}\n\n[Clarified repo location: analyze the codebase at {path}]",
            path,
        )
    elif selected.startswith("__clone__:"):
        url = selected.split(":", 2)[1]
        question = questionary.text(
            f"Local path to clone {url} into:",
            default=str(cwd / _default_clone_dir(url)),
        )
        _add_escape_binding(question)
        try:
            typed = await question.application.run_async()
        except TUICancelled:
            return prompt, None
        clone_path = typed.strip() if typed else str(cwd / _default_clone_dir(url))
        path = Path(clone_path).expanduser().resolve()
        if not path.exists():
            print(f"Cloning {url} into {path}...")
            try:
                import subprocess

                subprocess.run(
                    ["git", "clone", url, str(path)],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except Exception as exc:
                print(f"Failed to clone {url}: {exc}")
                return prompt, None
        return (
            f"{prompt}\n\n[Clarified repo location: analyze the codebase at {path}]",
            path,
        )

    if _is_url(selected):
        print(
            "Remote URLs cannot be analyzed directly. "
            "Choose 'Clone remote repo' or provide a local clone path."
        )
        return prompt, None

    path = Path(selected).expanduser().resolve()
    if not path.exists():
        print(f"Warning: {path} does not exist. Using current directory.")
        path = cwd

    if path == cwd:
        return prompt, None

    return (
        f"{prompt}\n\n[Clarified repo location: analyze the codebase at {path}]",
        path,
    )


def _default_clone_dir(url: str) -> str:
    """Return a default local directory name for cloning *url*."""
    lowered = url.lower().rstrip("/")
    if lowered.endswith(".git"):
        lowered = lowered[:-4]
    for prefix in ("https://", "http://", "git://"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
    if "@" in lowered:
        lowered = lowered.split("@", 1)[1]
    parts = lowered.split("/")
    name = parts[-1] if parts else "repo"
    return re.sub(r"[^a-z0-9_-]+", "-", name).strip("-") or "repo"


def _resolve_suggested_prompt(
    user_input: str,
    suggested_prompts: list[tuple[str, str, str]],
) -> tuple[str, str | None, str | None]:
    """If user_input is a number matching a suggested prompt, return its text.

    Returns (resolved_input, selected_title, prompt_id). If the input is not a
    selection, returns (user_input, None, None).
    """
    if not user_input.isdigit() or not suggested_prompts:
        return user_input, None, None
    idx = int(user_input) - 1
    if 0 <= idx < len(suggested_prompts):
        prompt_id, title, selected_prompt = suggested_prompts[idx]
        return selected_prompt, title, prompt_id
    return user_input, None, None


def _format_choice_title(
    title: str,
    rendered: str,
    run_record: Any | None = None,
) -> str:
    """Return a TUI choice title that includes the full prompt body.

    The body lines are indented to align under the title text after the
    checkbox/pointer prefix (5 columns). If *run_record* is provided, a
    "Ran" indicator is appended to the title.
    """
    indicator = format_run_indicator(run_record)
    display_title = f"{title}{indicator}" if indicator else title
    body = rendered.strip()
    if not body:
        return display_title
    indented = "\n".join(f"     {line}" for line in body.splitlines())
    return f"{display_title}\n{indented}"


class TUICancelled(Exception):
    """Raised when the user cancels a questionary TUI with Escape."""


async def _run_with_interrupt(coro: Any, indicator: ProgressIndicator | None = None) -> Any:
    """Run *coro* and allow the user to cancel it with two Escape presses.

    On Unix TTYs a background thread listens for raw keystrokes. The first Esc
    shows a "press again to cancel" hint; a second Esc within one second cancels
    the task. On non-TTY or Windows platforms the coroutine runs normally.
    """
    if sys.platform == "win32" or not sys.stdin.isatty():
        return await coro

    try:
        import select
        import termios
        import tty
    except ImportError:
        return await coro

    task: asyncio.Task[Any] = asyncio.create_task(coro)
    cancelled_by_user = False

    def _keyboard_listener() -> None:
        nonlocal cancelled_by_user
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            esc_count = 0
            last_esc = 0.0
            while not task.done():
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                try:
                    ch = sys.stdin.read(1)
                except Exception:
                    continue
                if not ch:
                    continue
                if ch == "\x1b":
                    now = time.monotonic()
                    if now - last_esc > 1.0:
                        esc_count = 1
                    else:
                        esc_count += 1
                    last_esc = now
                    if esc_count == 1:
                        print(
                            "\n→ Press Esc again within 1 second to cancel",
                            file=sys.stderr,
                            flush=True,
                        )
                    elif esc_count >= 2:
                        cancelled_by_user = True
                        task.cancel()
                        break
                else:
                    esc_count = 0
        finally:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass

    listener = asyncio.create_task(asyncio.to_thread(_keyboard_listener))
    try:
        return await task
    except asyncio.CancelledError:
        if cancelled_by_user:
            raise TUICancelled("Execution cancelled")
        raise
    finally:
        listener.cancel()
        try:
            await listener
        except asyncio.CancelledError:
            pass


def _add_escape_binding(question: Any) -> None:
    """Add an Escape key binding that cancels a questionary prompt."""
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings

    existing = question.application.key_bindings

    new_kb = KeyBindings()

    @new_kb.add(Keys.Escape, eager=True)
    def _cancel(event: Any) -> None:
        event.app.exit(exception=TUICancelled, style="class:aborting")

    if existing is None:
        question.application.key_bindings = new_kb
    elif hasattr(existing, "add"):
        existing.add(Keys.Escape, eager=True)(_cancel)
    else:
        # prompt_toolkit may return a merged key bindings object; wrap it.
        question.application.key_bindings = merge_key_bindings([existing, new_kb])


async def _select_prompts_tui(
    suggested_prompts: list[tuple[str, str, str]],
    *,
    run_history: Any | None = None,
    epic_id: str | None = None,
    milestone_id: str | None = None,
) -> list[tuple[str, str, str]]:
    """Show a checkbox TUI to select one or more suggested prompts.

    Returns the list of selected (prompt_id, title, rendered) tuples. Raises
    :class:`TUICancelled` if the user presses Escape.
    """
    import questionary

    run_history = run_history or None
    choices = []
    for prompt_id, title, rendered in suggested_prompts:
        record = None
        if run_history is not None and epic_id and milestone_id:
            record = run_history.get(epic_id, milestone_id, prompt_id)
        choices.append(
            questionary.Choice(
                title=_format_choice_title(title, rendered, run_record=record),
                value=(prompt_id, title, rendered),
            )
        )
    question = questionary.checkbox(
        "Select prompts (Space to check, Enter to confirm, Esc to cancel):",
        choices=choices,
    )
    _add_escape_binding(question)
    selected = await question.application.run_async()
    return selected if selected else []


async def _edit_prompt_tui(prompt_text: str) -> str:
    """Show a multi-line text prompt pre-filled with prompt_text for editing.

    Raises :class:`TUICancelled` if the user presses Escape.
    """
    import questionary

    question = questionary.text(
        "Edit the prompt (Esc to cancel, Ctrl+J for new line, Enter to submit):",
        default=prompt_text.replace("\n", " "),
        multiline=False,
    )
    _add_escape_binding(question)
    edited = await question.application.run_async()
    return edited if edited is not None else prompt_text


async def _prompt_action_tui(title: str) -> str:
    """Ask whether to execute, edit, or skip a selected prompt.

    Raises :class:`TUICancelled` if the user presses Escape.
    """
    import questionary

    question = questionary.select(
        f"Selected: {title} (Esc to cancel)",
        choices=[
            questionary.Choice("Execute as-is", value="execute"),
            questionary.Choice("Edit before executing", value="edit"),
            questionary.Choice("Skip", value="skip"),
        ],
    )
    _add_escape_binding(question)
    action = await question.application.run_async()
    return action if action else "skip"


async def _confirm_escalation(runtime: str, model: str) -> bool:
    """Ask the user for permission to escalate from a local to a frontier model."""
    import questionary

    question = questionary.confirm(
        f"No capable local model found. Escalate to {runtime}/{model}?"
    )
    _add_escape_binding(question)
    result = await question.application.run_async()
    return bool(result)


def _read_input_with_paste(prompt: str = "> ") -> str:
    """Read a line, then drain any immediately-pending stdin bytes.

    Why: When a user pastes multi-line text into an interactive terminal,
    each newline would otherwise be consumed as a separate prompt. By
    checking stdin for more data right after the first line, we can collect
    the whole paste into a single prompt.

    The short timeout (50ms) means normal typing still gets one line at a
    time, while a paste (which arrives as a burst) is captured in full.
    """
    import select
    import sys

    first = input(prompt)
    lines = [first]

    # Drain pasted lines for up to ~50ms after the first newline.
    while True:
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.05)
        except (OSError, ValueError):
            break
        if not ready:
            break
        line = sys.stdin.readline()
        if not line:
            break
        lines.append(line.rstrip("\n"))

    # Remove a single trailing blank line that some terminals inject.
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()

    return "\n".join(lines)


async def _read_line(prompt: str = "> ") -> str:
    """Read a line (or pasted multi-line block) from stdin."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _read_input_with_paste, prompt)


def _format_milestone_status(plan: Any) -> str:
    """Return a concise text rendering of milestone progress."""
    lines = [
        f"Project: {plan.project_id}",
        f"Overall: {plan.summary.overall_completion}% complete",
    ]
    if plan.summary.current_milestone_ids:
        lines.append(
            "Current: " + ", ".join(plan.summary.current_milestone_ids)
        )
    if plan.summary.next_milestone_ids:
        lines.append("Next: " + ", ".join(plan.summary.next_milestone_ids))
    if plan.summary.active_blockers:
        lines.append(
            f"Blockers: {len(plan.summary.active_blockers)} active"
        )
    lines.append("")
    for epic in sorted(plan.epics, key=lambda x: x.order):
        lines.append(f"Epic: {epic.name} ({epic.completion()}%)")
        for m in sorted(epic.milestones, key=lambda x: x.order):
            marker = "✓" if m.status == MilestoneStatus.COMPLETED else "○"
            lines.append(
                f"  {marker} {m.name}: {m.status.value} ({m.completion()}%)"
            )
        lines.append("")
    return "\n".join(lines)


async def _discover_milestones_interactive(project_path: Path) -> str:
    """Detect milestones on first launch and ask for confirmation.

    Returns a status message to display to the user.
    """
    store = MilestoneStore(project_path)
    plan = store.load()

    if store.exists():
        return _format_milestone_status(plan)

    detector = MilestoneDetector(project_path)
    suggestions = detector.detect(plan)

    print("\nNo milestone plan found. Scanning project artifacts...")
    for s in suggestions:
        print(
            f"  {s.epic_id}/{s.milestone_id}: {s.suggested_status.value} "
            f"(confidence {s.confidence:.0%}) — {s.reason}"
        )

    try:
        answer = await _read_line(
            "\nAccept these suggestions and initialize the milestone plan? [y/N] "
        )
    except (EOFError, KeyboardInterrupt):
        answer = "n"

    if answer.strip().lower() == "y":
        confirmed = {f"{s.epic_id}/{s.milestone_id}" for s in suggestions}
        detector.apply_suggestions(plan, suggestions, confirmed_ids=confirmed)
        store.update(plan)
        return "Milestone plan initialized.\n\n" + _format_milestone_status(plan)

    # Save the empty template so we do not ask again next launch.
    store.save(plan)
    return "Milestone plan initialized empty. Use /milestones to view or update it."


def _handle_milestones_command(project_path: Path) -> str:
    """Handle the /milestones slash command."""
    store = MilestoneStore(project_path)
    plan = store.load()
    return _format_milestone_status(plan)


async def run_interactive(args: Any) -> int:
    """Run the interactive Open Maestro loop."""
    _setup_readline()

    # Make OpenAI-compatible endpoint credentials visible to availability checks
    # and runtime creation.
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url
    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key

    source_registry = SourceRegistry.load()
    if not args.skip_sync:
        for source in source_registry.sources:
            try:
                sync_source(source, force=args.sync_sources)
            except Exception as exc:
                print(
                    f"Warning: failed to sync source '{source.name}': {exc}",
                    file=sys.stderr,
                )
        source_registry.save()

    project_dir, user_dir, bundled_dir = _resolve_agent_tiers(args.agents_dir)
    registry = AgentLoader.load_tiered_dirs(
        project_dir,
        user_dir,
        bundled_dir,
        project_skills_dir=args.skills_dir,
        agent_sources=source_registry.list("agents"),
        skill_sources=source_registry.list("skills"),
    )
    if not registry.list():
        print("No agent definitions found.", file=sys.stderr)
        _save_readline_history()
        return 1

    session_base_dirs = [args.session_dir] if args.session_dir else None
    session_store = SessionStore(base_dirs=session_base_dirs)

    # Interactive mode always tries to use memory for recall and storage.
    # Bind to the project directory where maestro was launched and ensure a
    # project-specific memory database exists.
    memory = None
    try:
        memory = KuzuMemoryClient(project_root=str(Path.cwd()))
        if not await memory.ensure_initialized():
            logger.warning(
                "Project memory could not be initialized for %s", Path.cwd()
            )
    except Exception as exc:
        logger.warning("kuzu-memory unavailable: %s", exc)
        memory = None

    search = VectorSearchClient() if args.search else None

    context_budget = ContextBudget(
        max_context_tokens=args.max_context_tokens,
        warning_threshold=args.warning_threshold,
        critical_threshold=args.critical_threshold,
    )

    event_bus = EventBus()
    indicator = ProgressIndicator(message="Thinking")
    event_bus.on("*", InteractiveProgressHandler(indicator=indicator))
    if args.stream:
        event_bus.on("*", StreamingHandler(format=args.stream_format))

    mcp_config = load_mcp_config(args.mcp_config)

    state = InteractiveState(
        agent_id=args.agent,
        model=args.model,
        reasoning=args.reasoning,
        fast=args.fast,
        chain=getattr(args, "chain", True),
        prefer_local=args.prefer_local or getattr(args, "ask_escalate", False),
    )

    publish_line = DashboardPublishHistoryStore(Path.cwd()).format_last()
    print(_banner(session_id=state.session_id, publish_line=publish_line))

    milestone_msg = await _discover_milestones_interactive(Path.cwd())
    if milestone_msg:
        print("\n" + milestone_msg)

    while True:
        pending_prompt_id: str | None = None
        pending_edited = False
        selected_title: str | None = None
        from_playbook = bool(state.pending_prompts)
        if state.pending_prompts:
            pending_prompt_id, user_input, pending_edited, selected_title = (
                state.pending_prompts.pop(0)
            )
            print(f"> {user_input}")
        else:
            try:
                user_input = await _read_line("> ")
            except (EOFError, KeyboardInterrupt):
                print("\nExiting.")
                _save_readline_history()
                return 0

        user_input = user_input.strip()
        if not user_input:
            continue

        resolved_input, selected_title, selected_prompt_id = _resolve_suggested_prompt(
            user_input, state.suggested_prompts
        )
        if selected_title is not None:
            print(f"Selected prompt {user_input}: {selected_title}")
            # Use questionary's async API; the sync .ask() tries asyncio.run()
            # which fails when an event loop is already running.
            try:
                action = await _prompt_action_tui(selected_title)
            except TUICancelled:
                state.suggested_prompts = []
                state.pending_prompts = []
                print("Cancelled.")
                continue
            if action == "skip":
                state.suggested_prompts = []
                continue
            if action == "edit":
                try:
                    resolved_input = await _edit_prompt_tui(resolved_input)
                    pending_edited = True
                except TUICancelled:
                    state.suggested_prompts = []
                    state.pending_prompts = []
                    print("Cancelled.")
                    continue
            user_input = resolved_input.strip()
            pending_prompt_id = selected_prompt_id
            # Clear suggestions so a later bare number is not misinterpreted.
            state.suggested_prompts = []
            if not user_input:
                continue

        cmd_result = await _handle_command(user_input, state, registry, memory)
        if cmd_result == "__EXIT__":
            print("Exiting.")
            _save_readline_history()
            return 0
        if cmd_result is not None:
            print(cmd_result)
            continue

        state.turn += 1

        # Ask for repo location when an analysis task's target is ambiguous.
        # Playbook prompts queued by /next are already scoped to the current
        # project, so skip the clarification unless they contain an explicit URL
        # or filesystem path.
        clarified_prompt, _ = await _maybe_clarify_repo_path(
            user_input, state.history, memory, from_playbook=from_playbook
        )
        user_input = clarified_prompt

        profile = _build_task_profile(user_input, state, args)
        prompt = _assemble_prompt(user_input, state.history)

        # Per-turn runtime selection: pick the cheapest backend that can handle
        # this specific task profile, unless the user pinned a runtime/model.
        turn_runtime = args.runtime
        turn_model = state.model
        prefer_local = state.prefer_local or args.prefer_local
        if turn_runtime is None:
            try:
                selected_runtime, selected_model = select_runtime_for_task(
                    profile,
                    latency_tolerance=args.latency_tolerance,
                    max_cost_level=CostLevel(args.max_cost_level) if args.max_cost_level else None,
                    prefer_local=prefer_local,
                )
            except RuntimeError as exc:
                if state.prefer_local:
                    # Ask permission before escalating to a frontier (cloud) model.
                    try:
                        frontier_runtime, frontier_model = select_runtime_for_task(
                            profile,
                            latency_tolerance=args.latency_tolerance,
                            max_cost_level=CostLevel(args.max_cost_level) if args.max_cost_level else None,
                            prefer_local=False,
                        )
                    except RuntimeError:
                        print(f"Error: {exc}", file=sys.stderr)
                        print(
                            "No local or frontier model can handle this task.",
                            file=sys.stderr,
                        )
                        continue
                    try:
                        approved = await _confirm_escalation(frontier_runtime, frontier_model)
                    except TUICancelled:
                        print("Escalation cancelled.", file=sys.stderr)
                        continue
                    if not approved:
                        print("Escalation declined. Keeping the previous state.", file=sys.stderr)
                        continue
                    turn_runtime = frontier_runtime
                    if turn_model is None:
                        turn_model = frontier_model
                    print(f"Escalating to {turn_runtime}/{turn_model}.")
                elif args.prefer_local:
                    print(f"Error: {exc}", file=sys.stderr)
                    print(
                        "No local models are available. Start Ollama or set "
                        "OPENAI_BASE_URL to a local OpenAI-compatible endpoint "
                        "(e.g., http://localhost:11434/v1).",
                        file=sys.stderr,
                    )
                    continue
                else:
                    print(f"Error: {exc}", file=sys.stderr)
                    print(
                        "Check that a backend is installed and configured "
                        "(kimi, claude, openai SDK, or a local endpoint via "
                        "OPENAI_BASE_URL).",
                        file=sys.stderr,
                    )
                    continue
            turn_runtime = selected_runtime
            if turn_model is None:
                turn_model = selected_model

        runtime_config = AgentConfig(
            extra={
                "api_key": args.api_key,
                "base_url": args.base_url,
            }
        )
        runtime = create_runtime(turn_runtime, config=runtime_config)
        if not runtime.is_available() and not (
            state.dry_run_next or state.show_plan_next
        ):
            print(
                f"Runtime '{runtime.runtime_name}' is not available",
                file=sys.stderr,
            )
            continue

        dry_run = (
            state.dry_run_next
            or state.show_plan_next
            or args.dry_run
            or args.show_plan
        )

        router = (
            None
            if args.no_llm_route or dry_run
            else LLMTaskRouter(runtime=runtime, model=turn_model or "fast")
        )

        pm = ProjectManager(
            runtime=runtime,
            registry=registry,
            memory=memory if not args.no_memory else None,
            search=search,
            router=router,
            session_store=session_store,
            context_budget=context_budget,
            event_bus=event_bus,
        )

        indicator.set_message("Thinking")
        if not args.monitor:
            indicator.start()

        # Only resume a session if the selected runtime is the one that created it.
        can_resume = state.session_runtime == turn_runtime
        effective_session_id = state.session_id if can_resume else None

        async def _execute_turn() -> Any:
            if args.monitor:
                async with Monitor(event_bus) as monitor:
                    monitor.state.turn = state.turn
                    return await pm.handle(
                        prompt,
                        agent_id=state.agent_id,
                        task_profile=profile,
                        model=turn_model,
                        allowed_tools=args.allowed_tools,
                        blocked_tools=args.block_tools,
                        permission_mode=args.permission_mode,
                        deny_dangerous=args.deny_dangerous,
                        max_turns=args.max_turns,
                        mcp_servers=mcp_config,
                        session_id=effective_session_id,
                        resume=effective_session_id is not None,
                        dry_run=dry_run,
                        chain=state.chain,
                        runtime_config=runtime_config,
                    )
            return await pm.handle(
                prompt,
                agent_id=state.agent_id,
                task_profile=profile,
                model=turn_model,
                allowed_tools=args.allowed_tools,
                blocked_tools=args.block_tools,
                permission_mode=args.permission_mode,
                deny_dangerous=args.deny_dangerous,
                max_turns=args.max_turns,
                mcp_servers=mcp_config,
                session_id=effective_session_id,
                resume=effective_session_id is not None,
                dry_run=dry_run,
                chain=state.chain,
                runtime_config=runtime_config,
            )

        try:
            result = await _run_with_interrupt(_execute_turn(), indicator=indicator)
        except TUICancelled:
            print("Cancelled by user.")
            continue
        except Exception as exc:
            logger.exception("Task handling failed")
            print(f"Error: {exc}", file=sys.stderr)
            continue
        finally:
            if not args.monitor:
                await indicator.stop()
            state.dry_run_next = False
            state.show_plan_next = False

        print(f"\n─── Turn {state.turn} ───\n")
        print(f"{result.text}\n")

        if not dry_run and result.session_id:
            sid = result.session_id
            if sid.startswith("session_"):
                maybe_uuid = sid[8:]
                if re.match(
                    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                    maybe_uuid,
                    re.IGNORECASE,
                ):
                    sid = maybe_uuid
            state.session_id = sid
            state.session_runtime = turn_runtime
            print(f"[session: {state.session_id}]")

        # Record that a suggested playbook prompt was executed and advance the
        # milestone status if appropriate.
        if (
            not dry_run
            and pending_prompt_id
            and state.current_epic_id
            and state.current_milestone_id
        ):
            try:
                history_store = PromptHistoryStore(Path.cwd())
                history_store.record(
                    epic_id=state.current_epic_id,
                    milestone_id=state.current_milestone_id,
                    prompt_id=pending_prompt_id,
                    prompt_title=selected_title or pending_prompt_id,
                    edited=pending_edited,
                )
            except Exception as exc:
                logger.warning("Failed to record prompt run history: %s", exc)

            try:
                update_msg = advance_milestone_on_prompt(
                    Path.cwd(),
                    state.current_epic_id,
                    state.current_milestone_id,
                )
                if update_msg:
                    print(update_msg)
            except Exception as exc:
                logger.debug("Failed to advance milestone status: %s", exc)

        state.history.append({"role": "user", "content": user_input})
        state.history.append({"role": "assistant", "content": result.text})

        if not dry_run and memory is not None:
            await _store_turn_memory(state, result, memory)

    _save_readline_history()
    return 0


def _resolve_agent_tiers(explicit: Path | None) -> tuple[Path | None, Path | None, Path]:
    """Return (project_dir, user_dir, bundled_dir) for tiered agent loading."""
    project_dir = explicit or (Path.cwd() / ".open-maestro" / "agents")
    user_dir = Path.home() / ".open-maestro" / "agents"
    # Installed wheel layout: open_maestro/_bundled_agents/
    bundled_dir = (Path(__file__).resolve().parent / "_bundled_agents").resolve()
    if not bundled_dir.exists():
        # Development layout: project-root/agents/
        bundled_dir = (
            Path(__file__).resolve().parent.parent.parent / "agents"
        ).resolve()

    if explicit is None and not project_dir.exists():
        project_dir = None
    if not user_dir.exists():
        user_dir = None

    return project_dir, user_dir, bundled_dir
