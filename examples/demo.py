"""Demo of Open Maestro's vendor-agnostic orchestration.

Usage:
    OPEN_MAESTRO_RUNTIME=kimi-cli python examples/demo.py
    OPEN_MAESTRO_RUNTIME=claude-cli python examples/demo.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Allow running from the repo root without installing
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from open_maestro.agents.loader import AgentLoader
from open_maestro.memory.kuzu_client import KuzuMemoryClient
from open_maestro.orchestrator.pm import ProjectManager
from open_maestro.orchestrator.router import LLMTaskRouter
from open_maestro.runtime.factory import create_runtime, list_runtimes
from open_maestro.search.vector_client import VectorSearchClient

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


async def main() -> int:
    print("Available runtimes:")
    for name, available in list_runtimes().items():
        print(f"  {name}: {'available' if available else 'not available'}")

    runtime = create_runtime()
    print(f"\nUsing runtime: {runtime.runtime_name}")

    registry = AgentLoader.load_defaults()
    print("\nLoaded agents:")
    for agent in registry.list():
        print(f"  - {agent.id} ({agent.role}): {agent.name}")

    memory = None
    try:
        memory = KuzuMemoryClient()
    except RuntimeError as exc:
        print(f"\nMemory unavailable: {exc}")

    search = None
    try:
        search = VectorSearchClient()
    except RuntimeError as exc:
        print(f"Search unavailable: {exc}")

    router = LLMTaskRouter(runtime=runtime, model="fast")
    pm = ProjectManager(
        runtime=runtime,
        registry=registry,
        memory=memory,
        search=search,
        router=router,
    )

    # Example 1: route an implementation task to the engineer agent
    prompt = "Explain how the Open Maestro runtime factory works and suggest one improvement."
    print(f"\n--- Task: {prompt} ---")
    result = await pm.handle(prompt, agent_id="researcher")
    print("Agent: researcher")
    print(f"Duration: {result.duration_ms}ms")
    print(f"Session: {result.session_id}")
    print("Response:")
    print(result.text)

    # Example 2: ask the PM to pick an agent automatically
    prompt2 = "Write a short Python test for the KimiCLIRuntime._parse_output method."
    print(f"\n--- Task: {prompt2} ---")
    result2 = await pm.handle(prompt2)
    print(f"Selected agent: {result2.metadata.get('selected_agent', 'n/a')}")
    print("Response:")
    print(result2.text)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
