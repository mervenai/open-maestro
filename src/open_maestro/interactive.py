"""Interactive / chat mode for Open Maestro.

Loads the project context once and then loops on user input, maintaining
conversation history and a single session across turns.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import sys
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
    MilestoneDetector,
    MilestoneStatus,
    MilestoneStore,
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
    agent_id: str | None = None
    model: str | None = None
    show_plan_next: bool = False
    dry_run_next: bool = False
    reasoning: bool = False
    fast: bool = False
    chain: bool = True
    turn: int = 0
    # Prompts most recently shown by /next or /prompts, available for selection
    # by typing their number (1-indexed).
    suggested_prompts: list[tuple[str, str]] = field(default_factory=list)
    # Prompts queued by /select for execution in subsequent turns.
    pending_prompts: list[str] = field(default_factory=list)


def _banner(session_id: str | None = None) -> str:
    session_line = f"[session: {session_id}]\n" if session_id else ""
    return (
        "Open Maestro interactive mode\n"
        + session_line
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
        "  /reasoning        toggle reasoning preference\n"
        "  /fast             toggle fast/cheap preference\n"
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
        prompts, _ = get_current_or_next_milestone_prompts(Path.cwd())
        state.suggested_prompts = [(t.title, rendered) for t, rendered in prompts]
        return handle_next_command(Path.cwd())

    if cmd == "prompts":
        result = handle_prompts_command(Path.cwd(), args)
        if args:
            milestone_id = args[0]
            epic_id = args[1] if len(args) > 1 else None
            store = MilestoneStore(Path.cwd())
            plan = store.load()
            prompts = get_prompts_for_milestone(
                Path.cwd(), milestone_id, plan=plan, epic_id=epic_id
            )
            state.suggested_prompts = [(t.title, rendered) for t, rendered in prompts]
        return result

    if cmd == "select":
        if not state.suggested_prompts:
            return "No suggested prompts to select. Run /next or /prompts first."
        selected = await asyncio.get_event_loop().run_in_executor(
            None, _select_prompts_tui, state.suggested_prompts
        )
        if not selected:
            return "No prompts selected."
        # For each selected prompt, ask execute/edit/skip and queue for execution.
        pending: list[str] = []
        for title, rendered in selected:
            action = await asyncio.get_event_loop().run_in_executor(
                None, _prompt_action_tui, title
            )
            if action == "skip":
                continue
            if action == "edit":
                rendered = await asyncio.get_event_loop().run_in_executor(
                    None, _edit_prompt_tui, rendered
                )
            text = rendered.strip()
            if text:
                pending.append(text)
        state.suggested_prompts = []
        if not pending:
            return "No prompts selected for execution."
        # Store pending prompts and return the first one to the caller.
        state.pending_prompts = pending[1:]
        return f"Selected prompt: {selected[0][0]}\n\n{pending[0]}"

    if cmd == "complete":
        return handle_complete_command(Path.cwd(), args)

    if cmd == "blocker":
        return handle_blocker_command(Path.cwd(), args)

    if cmd == "track":
        return handle_track_command(Path.cwd(), args)

    return f"Unknown command '/{cmd}'. Type /help for available commands."


def _resolve_suggested_prompt(
    user_input: str,
    suggested_prompts: list[tuple[str, str]],
) -> tuple[str, str | None]:
    """If user_input is a number matching a suggested prompt, return its text.

    Returns (resolved_input, selected_title). If the input is not a selection,
    returns (user_input, None).
    """
    if not user_input.isdigit() or not suggested_prompts:
        return user_input, None
    idx = int(user_input) - 1
    if 0 <= idx < len(suggested_prompts):
        title, selected_prompt = suggested_prompts[idx]
        return selected_prompt, title
    return user_input, None


def _select_prompts_tui(
    suggested_prompts: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Show a checkbox TUI to select one or more suggested prompts.

    Returns the list of selected (title, prompt) tuples.
    """
    import questionary

    choices = [
        questionary.Choice(title=title, value=(title, rendered))
        for title, rendered in suggested_prompts
    ]
    selected = questionary.checkbox(
        "Select prompts (Space to check, Enter to confirm, Esc to cancel):",
        choices=choices,
    ).ask()
    return selected if selected else []


def _edit_prompt_tui(prompt_text: str) -> str:
    """Show a multi-line text prompt pre-filled with prompt_text for editing."""
    import questionary

    edited = questionary.text(
        "Edit the prompt (Ctrl+J for new line, Enter to submit):",
        default=prompt_text.replace("\n", " "),
        multiline=False,
    ).ask()
    return edited if edited is not None else prompt_text


def _prompt_action_tui(title: str) -> str:
    """Ask whether to execute, edit, or skip a selected prompt."""
    import questionary

    action = questionary.select(
        f"Selected: {title}",
        choices=[
            questionary.Choice("Execute as-is", value="execute"),
            questionary.Choice("Edit before executing", value="edit"),
            questionary.Choice("Skip", value="skip"),
        ],
    ).ask()
    return action if action else "skip"


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
    )

    print(_banner(session_id=state.session_id))

    milestone_msg = await _discover_milestones_interactive(Path.cwd())
    if milestone_msg:
        print("\n" + milestone_msg)

    while True:
        if state.pending_prompts:
            user_input = state.pending_prompts.pop(0)
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

        resolved_input, selected_title = _resolve_suggested_prompt(
            user_input, state.suggested_prompts
        )
        if selected_title is not None:
            print(f"Selected prompt {user_input}: {selected_title}")
            action = await asyncio.get_event_loop().run_in_executor(
                None, _prompt_action_tui, selected_title
            )
            if action == "skip":
                state.suggested_prompts = []
                continue
            if action == "edit":
                resolved_input = await asyncio.get_event_loop().run_in_executor(
                    None, _edit_prompt_tui, resolved_input
                )
            user_input = resolved_input.strip()
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
        profile = _build_task_profile(user_input, state, args)
        prompt = _assemble_prompt(user_input, state.history)

        # Per-turn runtime selection: pick the cheapest backend that can handle
        # this specific task profile, unless the user pinned a runtime/model.
        turn_runtime = args.runtime
        turn_model = state.model
        if turn_runtime is None:
            try:
                selected_runtime, selected_model = select_runtime_for_task(
                    profile,
                    latency_tolerance=args.latency_tolerance,
                    max_cost_level=CostLevel(args.max_cost_level) if args.max_cost_level else None,
                    prefer_local=args.prefer_local,
                )
            except RuntimeError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                if args.prefer_local:
                    print(
                        "No local models are available. Start Ollama or set "
                        "OPENAI_BASE_URL to a local OpenAI-compatible endpoint "
                        "(e.g., http://localhost:11434/v1).",
                        file=sys.stderr,
                    )
                else:
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
        try:
            if args.monitor:
                async with Monitor(event_bus) as monitor:
                    monitor.state.turn = state.turn
                    result = await pm.handle(
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
                        session_id=state.session_id,
                        resume=state.session_id is not None,
                        dry_run=dry_run,
                        chain=state.chain,
                        runtime_config=runtime_config,
                    )
            else:
                result = await pm.handle(
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
                    session_id=state.session_id,
                    resume=state.session_id is not None,
                    dry_run=dry_run,
                    chain=state.chain,
                    runtime_config=runtime_config,
                )
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
            state.session_id = result.session_id
            print(f"[session: {state.session_id}]")

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
