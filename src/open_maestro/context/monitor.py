"""Context-pressure monitoring and resume-log generation."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from open_maestro.context.budget import ContextBudget

if TYPE_CHECKING:
    from open_maestro.orchestrator.pm import OrchestrationContext
    from open_maestro.runtime.base import AgentResult

logger = logging.getLogger(__name__)


@dataclass
class ContextSnapshot:
    """Cumulative context usage for a session."""

    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    num_turns: int = 0


class ContextMonitor:
    """Track cumulative token usage against a budget and produce resume logs."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()
        self.snapshot = ContextSnapshot()

    def update(self, result: AgentResult) -> str | None:
        """Incorporate *result* usage and return ``warning``/``critical`` or None."""
        input_tokens = result.input_tokens
        output_tokens = result.output_tokens
        total_tokens = result.tokens_used

        if total_tokens is None:
            if input_tokens is not None and output_tokens is not None:
                total_tokens = input_tokens + output_tokens
            else:
                total_tokens = _estimate_tokens(result.text)

        self.snapshot.input_tokens += input_tokens or 0
        self.snapshot.output_tokens += output_tokens or 0
        self.snapshot.tokens_used += total_tokens or 0
        self.snapshot.cost_usd += result.cost_usd or 0.0
        if result.num_turns:
            self.snapshot.num_turns += result.num_turns
        else:
            self.snapshot.num_turns += 1

        ratio = self.snapshot.tokens_used / max(self.budget.max_context_tokens, 1)
        if ratio >= self.budget.critical_threshold:
            return "critical"
        if ratio >= self.budget.warning_threshold:
            return "warning"
        return None

    def build_resume_log(
        self,
        ctx: OrchestrationContext,
        *,
        original_prompt: str,
    ) -> str:
        """Build a concise resume log summarizing progress so far."""
        agent = ctx.selected_agent
        lines: list[str] = [
            "# Context-pressure resume log",
            "",
            "## Mission",
            original_prompt,
            "",
        ]
        if agent is not None:
            lines.extend(
                [
                    "## Assigned agent",
                    f"- id: {agent.id}",
                    f"- name: {agent.name}",
                    f"- role: {agent.role}",
                    "",
                ]
            )

        lines.append("## Context snapshot")
        lines.append(f"- tokens used: {self.snapshot.tokens_used}")
        lines.append(f"- input tokens: {self.snapshot.input_tokens}")
        lines.append(f"- output tokens: {self.snapshot.output_tokens}")
        lines.append(f"- estimated cost USD: ${self.snapshot.cost_usd:.4f}")
        lines.append(f"- turns: {self.snapshot.num_turns}")
        lines.append("")

        if ctx.memories:
            lines.append("## Relevant context recalled")
            for memory in ctx.memories:
                lines.append(f"- {memory}")
            lines.append("")

        if ctx.code_results:
            lines.append("## Code references")
            for item in ctx.code_results[:5]:
                path = item.get("file_path", "unknown")
                lines.append(f"- {path}")
            lines.append("")

        lines.extend(
            [
                "## Key findings / decisions",
                "- (Populate this section with concrete decisions made during the run.)",
                "",
                "## Next steps",
                "- (Populate this section with the immediate next action.)",
            ]
        )
        return "\n".join(lines)


def _estimate_tokens(text: str | None) -> int:
    """Rough token estimate: ~4 characters per token."""
    if not text:
        return 0
    return max(1, len(text) // 4)
