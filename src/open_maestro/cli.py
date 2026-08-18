"""Minimal CLI for Open Maestro."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

import open_maestro
from open_maestro.agents.loader import AgentLoader
from open_maestro.config.capabilities import (
    CodingStrength,
    CostLevel,
    LatencyHint,
    ReasoningLevel,
    TaskProfiler,
)
from open_maestro.context.budget import ContextBudget
from open_maestro.events.bus import EventBus
from open_maestro.events.stream import StreamingHandler
from open_maestro.interactive import run_interactive
from open_maestro.mcp.config import load_mcp_config
from open_maestro.memory.kuzu_client import KuzuMemoryClient
from open_maestro.milestones import (
    DashboardPublisher,
    MervenSyncError,
    MilestoneDetector,
    MilestoneStore,
    export_dashboard_html,
    export_dashboard_json,
    export_dashboard_markdown,
    serve_dashboard,
    summarize_suggestions,
    sync_from_merven,
)
from open_maestro.monitor.live import Monitor
from open_maestro.orchestrator.pm import ProjectManager
from open_maestro.orchestrator.router import LLMTaskRouter
from open_maestro.runtime.base import AgentConfig
from open_maestro.runtime.factory import (
    create_runtime,
    list_runtimes,
    select_runtime_for_task,
)
from open_maestro.search.vector_client import VectorSearchClient
from open_maestro.session.store import SessionStore
from open_maestro.sources.config import SourceRegistry, default_source_name
from open_maestro.sources.sync import GitSource, sync_source

logger = logging.getLogger(__name__)


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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="maestro",
        description="Vendor-agnostic multi-agent orchestration.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {open_maestro.__version__}",
    )
    parser.add_argument(
        "--runtime",
        choices=list(list_runtimes().keys()),
        help="Runtime backend (default: auto-detect)",
    )
    parser.add_argument(
        "--agents-dir",
        type=Path,
        default=None,
        help=(
            "Project agent directory "
            "(default: ./.open-maestro/agents, then ~/.open-maestro/agents, then bundled agents)"
        ),
    )
    parser.add_argument(
        "--skills-dir",
        type=Path,
        default=None,
        help=(
            "Project skills directory "
            "(default: ./.open-maestro/skills, then ~/.open-maestro/skills, then bundled skills)"
        ),
    )
    parser.add_argument(
        "--agent",
        help="Specific agent to use (default: auto-select)",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Run semantic code search before delegating",
    )
    parser.add_argument(
        "--memory",
        action="store_true",
        help="Recall project memory before delegating",
    )
    parser.add_argument(
        "--no-memory",
        dest="no_memory",
        action="store_true",
        help="Disable project memory (interactive mode uses memory by default)",
    )
    parser.add_argument(
        "--model",
        help="Override model alias for this task",
    )
    parser.add_argument(
        "--base-url",
        dest="base_url",
        help="OpenAI-compatible API base URL (e.g. http://localhost:11434/v1)",
    )
    parser.add_argument(
        "--api-key",
        dest="api_key",
        help="API key for the selected runtime (OpenAI, DashScope, etc.)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Prefer low-latency, low-cost models",
    )
    parser.add_argument(
        "--reasoning",
        action="store_true",
        help="Request deep reasoning / architectural analysis",
    )
    parser.add_argument(
        "--vision",
        action="store_true",
        help="Task requires vision/multimodal capability",
    )
    parser.add_argument(
        "--coding-strength",
        choices=["low", "medium", "high"],
        help="Required coding strength (default: inferred from prompt)",
    )
    parser.add_argument(
        "--context-tokens",
        type=int,
        help="Estimated context size in tokens",
    )
    parser.add_argument(
        "--max-context-tokens",
        type=int,
        default=None,
        help="Maximum context window in tokens (default: 200000)",
    )
    parser.add_argument(
        "--warning-threshold",
        type=float,
        default=None,
        help="Context usage ratio that triggers a warning (default: 0.70)",
    )
    parser.add_argument(
        "--critical-threshold",
        type=float,
        default=None,
        help="Context usage ratio that triggers a critical/resume log (default: 0.90)",
    )
    parser.add_argument(
        "--cost-preference",
        choices=["low", "medium", "high"],
        help="Cost preference (default: inferred from prompt)",
    )
    parser.add_argument(
        "--latency-preference",
        choices=["low", "medium", "high"],
        help="Latency preference (default: inferred from prompt)",
    )
    parser.add_argument(
        "--prefer-local",
        action="store_true",
        help="Prefer local/self-hosted models (Ollama, vLLM, etc.)",
    )
    parser.add_argument(
        "--latency-tolerance",
        type=float,
        default=1.2,
        help="Max latency ratio vs. the fastest model when picking the cheapest (default: 1.2)",
    )
    parser.add_argument(
        "--max-cost-level",
        choices=["low", "medium", "high"],
        default=None,
        help="Exclude models more expensive than this cost level",
    )
    parser.add_argument(
        "--list-runtimes",
        action="store_true",
        help="List available runtimes and exit",
    )
    parser.add_argument(
        "--discover-milestones",
        action="store_true",
        help="Scan project artifacts and suggest milestone statuses",
    )
    parser.add_argument(
        "--export-dashboard",
        choices=["json", "markdown", "html"],
        help="Export a client-facing milestone dashboard and exit",
    )
    parser.add_argument(
        "--serve-dashboard",
        action="store_true",
        help="Start a lightweight HTTP server for the milestone dashboard",
    )
    parser.add_argument(
        "--dashboard-port",
        type=int,
        default=8080,
        help="Port for --serve-dashboard (default: 8080)",
    )
    parser.add_argument(
        "--dashboard-host",
        default="127.0.0.1",
        help="Host for --serve-dashboard (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--publish-dashboard",
        metavar="URL",
        help="Publish the dashboard JSON to a remote endpoint (e.g. https://merven.ai/api/maestro/dashboard)",
    )
    parser.add_argument(
        "--sync-milestones",
        action="store_true",
        help=(
            "Fetch the canonical milestone plan from Merven and overwrite "
            ".open-maestro/milestones.yaml"
        ),
    )
    parser.add_argument(
        "--dashboard-api-key",
        help="API key for --publish-dashboard (or set MAESTRO_DASHBOARD_API_KEY)",
    )
    parser.add_argument(
        "--dashboard-project-token",
        help="Project token for --publish-dashboard (or set MAESTRO_DASHBOARD_PROJECT_TOKEN)",
    )
    parser.add_argument(
        "--add-agent-source",
        metavar="URL",
        help="Add a Git source for agents and exit",
    )
    parser.add_argument(
        "--add-skill-source",
        metavar="URL",
        help="Add a Git source for skills and exit",
    )
    parser.add_argument(
        "--remove-agent-source",
        metavar="NAME",
        help="Remove an agent source by name and exit",
    )
    parser.add_argument(
        "--remove-skill-source",
        metavar="NAME",
        help="Remove a skill source by name and exit",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List configured agent/skill sources and exit",
    )
    parser.add_argument(
        "--sync-sources",
        action="store_true",
        help="Force sync all Git sources before running",
    )
    parser.add_argument(
        "--skip-sync",
        action="store_true",
        help="Skip Git source syncing",
    )
    parser.add_argument(
        "--no-llm-route",
        action="store_true",
        help="Use keyword matching instead of LLM-based agent selection",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan the task but do not call the LLM",
    )
    parser.add_argument(
        "--show-plan",
        action="store_true",
        help="Print the selected agent, task profile, and resolved model without invoking an LLM",
    )
    parser.add_argument(
        "--chain",
        action="store_true",
        help="Decompose the task into a multi-agent chain (research → engineer → QA, etc.)",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Stream live events to stderr during execution",
    )
    parser.add_argument(
        "--stream-format",
        choices=["json", "text"],
        default="text",
        help="Event stream format (default: text)",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Show a live Rich activity monitor during execution",
    )
    parser.add_argument(
        "--permission-mode",
        choices=["allow", "auto", "yolo", "read-only"],
        help="Permission mode for mutating/destructive tools",
    )
    parser.add_argument(
        "--deny-dangerous",
        action="store_true",
        help="Deny destructive shell commands (e.g., rm -rf /, mkfs, dd to /dev)",
    )
    parser.add_argument(
        "--allowed-tools",
        type=lambda s: [v.strip() for v in s.split(",") if v.strip()],
        default=None,
        help="Comma-separated list of tools the agent is allowed to use",
    )
    parser.add_argument(
        "--block-tools",
        type=lambda s: [v.strip() for v in s.split(",") if v.strip()],
        default=None,
        help="Comma-separated list of tools to block",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        default=None,
        help="Maximum number of agent turns",
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        default=None,
        help="Path to an MCP server configuration JSON file",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const=None,
        default=False,
        metavar="SESSION_ID",
        help="Resume a previous session (omit ID to list recent sessions)",
    )
    parser.add_argument(
        "--fork",
        metavar="SESSION_ID",
        help="Fork a previous session",
    )
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=None,
        help=(
            "Directory for session storage "
            "(default: .open-maestro/sessions, then ~/.open-maestro/sessions)"
        ),
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Start an interactive session and keep the project context loaded",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress INFO-level log output (warnings and errors still shown)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show DEBUG-level log output",
    )
    parser.add_argument(
        "prompt",
        nargs="*",
        help="Task prompt to delegate (use '--' before prompt text that starts with '-')",
    )
    return parser


async def main_async() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    if args.verbose:
        log_level = logging.DEBUG
    elif args.quiet:
        log_level = logging.WARNING
    else:
        # Interactive mode is cleaner without INFO chatter.
        log_level = logging.INFO if not args.interactive else logging.WARNING
    logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")
    # httpx logs every request at INFO; that's too noisy for normal CLI use.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    # Make OpenAI-compatible endpoint credentials visible to availability checks
    # and runtime creation as early as possible.
    if args.base_url:
        os.environ["OPENAI_BASE_URL"] = args.base_url
    if args.api_key:
        os.environ["OPENAI_API_KEY"] = args.api_key

    if args.list_runtimes:
        for name, available in list_runtimes().items():
            print(f"{name}: {'available' if available else 'not available'}")
        return 0

    if args.discover_milestones:
        store = MilestoneStore(Path.cwd())
        plan = store.load()
        detector = MilestoneDetector(Path.cwd())
        suggestions = detector.detect(plan)
        summary = summarize_suggestions(suggestions)
        print(f"Project: {plan.project_id}")
        print(f"Overall completion: {plan.summary.overall_completion}%")
        print("\nDetected milestone suggestions:")
        for s in summary["suggestions"]:
            print(
                f"  {s['epic_id']}/{s['milestone_id']}: {s['suggested_status']} "
                f"(confidence {s['confidence']}) — {s['reason']}"
            )
            if s["missing_required"]:
                for missing in s["missing_required"]:
                    print(f"    missing: {missing}")
        return 0

    if args.export_dashboard:
        store = MilestoneStore(Path.cwd())
        plan = store.load()
        if args.export_dashboard == "json":
            print(export_dashboard_json(plan))
        elif args.export_dashboard == "markdown":
            print(export_dashboard_markdown(plan))
        elif args.export_dashboard == "html":
            print(export_dashboard_html(plan))
        return 0

    if args.serve_dashboard:
        serve_dashboard(
            Path.cwd(),
            host=args.dashboard_host,
            port=args.dashboard_port,
            blocking=True,
        )
        return 0

    if args.sync_milestones:
        try:
            plan = sync_from_merven(Path.cwd())
            total_milestones = sum(len(e.milestones) for e in plan.epics)
            print(
                f"Synced milestone plan from Merven: {len(plan.epics)} epic(s), "
                f"{total_milestones} milestone(s)"
            )
            for epic in plan.epics:
                print(f"Epic: {epic.name}")
                for m in epic.milestones:
                    print(f"  [{m.status.value}] {epic.id}/{m.id}: {m.name}")
        except MervenSyncError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.publish_dashboard:
        store = MilestoneStore(Path.cwd())
        plan = store.load()
        publisher = DashboardPublisher(
            api_key=args.dashboard_api_key,
            project_token=args.dashboard_project_token,
        )
        try:
            response = publisher.publish(
                plan,
                url=args.publish_dashboard,
                extra_metadata={"source": "maestro-cli"},
            )
            print("Dashboard published.")
            print(response)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.interactive:
        return await run_interactive(args)

    source_registry = SourceRegistry.load()
    source_action = (
        args.add_agent_source
        or args.add_skill_source
        or args.remove_agent_source
        or args.remove_skill_source
        or args.list_sources
    )

    if args.add_agent_source:
        source_registry.add(
            GitSource(
                name=default_source_name(args.add_agent_source, "agents"),
                url=args.add_agent_source,
                kind="agents",
            )
        )
        source_registry.save()
        print(f"Added agent source: {args.add_agent_source}")
        return 0

    if args.add_skill_source:
        source_registry.add(
            GitSource(
                name=default_source_name(args.add_skill_source, "skills"),
                url=args.add_skill_source,
                kind="skills",
            )
        )
        source_registry.save()
        print(f"Added skill source: {args.add_skill_source}")
        return 0

    if args.remove_agent_source:
        removed = source_registry.remove(args.remove_agent_source, "agents")
        if removed is None:
            print(f"Agent source '{args.remove_agent_source}' not found", file=sys.stderr)
            return 1
        source_registry.save()
        print(f"Removed agent source: {args.remove_agent_source}")
        return 0

    if args.remove_skill_source:
        removed = source_registry.remove(args.remove_skill_source, "skills")
        if removed is None:
            print(f"Skill source '{args.remove_skill_source}' not found", file=sys.stderr)
            return 1
        source_registry.save()
        print(f"Removed skill source: {args.remove_skill_source}")
        return 0

    if args.list_sources:
        print("Agent sources:")
        for source in source_registry.list("agents"):
            print(f"  {source.name}: {source.url} ({source.kind})")
        print("Skill sources:")
        for source in source_registry.list("skills"):
            print(f"  {source.name}: {source.url} ({source.kind})")
        return 0

    if source_action and not args.prompt:
        return 0

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        parser.error("A prompt is required unless --list-runtimes is used")

    # Sync configured Git sources unless skipped.
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

    session_base_dirs = [args.session_dir] if args.session_dir else None
    session_store = SessionStore(base_dirs=session_base_dirs)

    resume = args.resume is not False
    session_id: str | None = args.resume if isinstance(args.resume, str) else None
    fork = bool(args.fork)
    fork_session_id: str | None = args.fork

    if resume and fork:
        parser.error("Use either --resume or --fork, not both")

    if args.resume is None:
        # --resume was supplied with no ID: list recent sessions.
        for rec in session_store.list_recent():
            if hasattr(rec.updated_at, "strftime"):
                ts = rec.updated_at.strftime("%Y-%m-%d %H:%M")
            else:
                ts = str(rec.updated_at)
            print(f"{rec.session_id:24s} {ts}  {rec.prompt_summary[:60]}")
        return 0

    if fork and fork_session_id and not session_store.get(fork_session_id):
        print(f"Session '{fork_session_id}' not found.", file=sys.stderr)
        return 1
    if resume and session_id and not session_store.get(session_id):
        print(f"Session '{session_id}' not found.", file=sys.stderr)
        return 1

    prompt = " ".join(args.prompt).strip()
    if not prompt:
        parser.error("A prompt is required unless --list-runtimes is used")

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
        return 1

    profile = TaskProfiler.from_prompt(
        prompt,
        reasoning_depth=ReasoningLevel.DEEP if args.reasoning else None,
        coding_strength=CodingStrength(args.coding_strength) if args.coding_strength else None,
        context_tokens_estimate=args.context_tokens,
        latency_preference=LatencyHint.LOW if args.fast else (
            LatencyHint(args.latency_preference) if args.latency_preference else None
        ),
        cost_preference=CostLevel.LOW if args.fast else (
            CostLevel(args.cost_preference) if args.cost_preference else None
        ),
        needs_vision=args.vision or None,
    )

    # When no runtime is explicitly requested, pick the cheapest available
    # runtime + model that satisfies the task profile.
    if args.runtime is None:
        # Default to a medium cost floor so cheap/local models are not selected
        # for general work. --fast, --prefer-local, or --cost-preference low
        # lowers the floor; --cost-preference high raises it to frontier-only.
        if args.fast or args.prefer_local or args.cost_preference == "low":
            min_cost_level = CostLevel.LOW
        elif args.cost_preference == "high":
            min_cost_level = CostLevel.HIGH
        else:
            min_cost_level = CostLevel.MEDIUM

        try:
            selected_runtime, selected_model = select_runtime_for_task(
                profile,
                latency_tolerance=args.latency_tolerance,
                max_cost_level=CostLevel(args.max_cost_level) if args.max_cost_level else None,
                min_cost_level=min_cost_level,
                prefer_local=args.prefer_local,
            )
        except RuntimeError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            if args.prefer_local:
                print(
                    "No local models are available. Start Ollama or set OPENAI_BASE_URL to a local "
                    "OpenAI-compatible endpoint (e.g., http://localhost:11434/v1).",
                    file=sys.stderr,
                )
            else:
                print(
                    "Check that a backend is installed and configured (kimi, claude, openai SDK, "
                    "or a local endpoint via OPENAI_BASE_URL).",
                    file=sys.stderr,
                )
            return 1
        args.runtime = selected_runtime
        if args.model is None:
            args.model = selected_model

    runtime_config = AgentConfig(
        extra={
            "api_key": args.api_key,
            "base_url": args.base_url,
        }
    )
    runtime = create_runtime(args.runtime, config=runtime_config)
    if not runtime.is_available() and not (args.dry_run or args.show_plan):
        print(f"Runtime '{runtime.runtime_name}' is not available", file=sys.stderr)
        return 1

    memory = None
    if args.memory:
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
    router = (
        None
        if args.no_llm_route or args.dry_run or args.show_plan
        else LLMTaskRouter(runtime=runtime, model=args.model or "fast")
    )

    context_budget = ContextBudget(
        max_context_tokens=args.max_context_tokens,
        warning_threshold=args.warning_threshold,
        critical_threshold=args.critical_threshold,
    )

    event_bus = EventBus()
    if args.stream:
        event_bus.on("*", StreamingHandler(format=args.stream_format))

    pm = ProjectManager(
        runtime=runtime,
        registry=registry,
        memory=memory,
        search=search,
        router=router,
        session_store=session_store,
        context_budget=context_budget,
        event_bus=event_bus,
    )

    mcp_config = load_mcp_config(args.mcp_config)
    mcp_servers = mcp_config

    dry_run = args.dry_run or args.show_plan
    if args.monitor:
        async with Monitor(event_bus):
            result = await pm.handle(
                prompt,
                agent_id=args.agent,
                task_profile=profile,
                model=args.model,
                allowed_tools=args.allowed_tools,
                blocked_tools=args.block_tools,
                permission_mode=args.permission_mode,
                deny_dangerous=args.deny_dangerous,
                max_turns=args.max_turns,
                mcp_servers=mcp_servers,
                session_id=session_id or fork_session_id,
                resume=resume,
                fork=fork,
                dry_run=dry_run,
                chain=args.chain,
                runtime_config=runtime_config,
            )
    else:
        result = await pm.handle(
            prompt,
            agent_id=args.agent,
            task_profile=profile,
            model=args.model,
            allowed_tools=args.allowed_tools,
            blocked_tools=args.block_tools,
            permission_mode=args.permission_mode,
            deny_dangerous=args.deny_dangerous,
            max_turns=args.max_turns,
            mcp_servers=mcp_servers,
            session_id=session_id or fork_session_id,
            resume=resume,
            fork=fork,
            dry_run=dry_run,
            chain=args.chain,
            runtime_config=runtime_config,
        )
    print(result.text)
    if not dry_run and result.session_id:
        print(f"\nSession: {result.session_id}")
    return 1 if result.is_error else 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
