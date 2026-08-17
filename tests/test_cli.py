"""Tests for the maestro CLI argument parsing."""

from __future__ import annotations

from open_maestro import cli


class TestCLIParsing:
    def test_multi_word_prompt_joined(self):
        parser = cli._build_parser()
        args = parser.parse_args(
            ["--runtime", "kimi-cli", "the", "budget", "import", "feature"]
        )
        assert " ".join(args.prompt) == "the budget import feature"

    def test_quoted_prompt_preserved(self):
        parser = cli._build_parser()
        args = parser.parse_args(["the budget import feature"])
        assert " ".join(args.prompt) == "the budget import feature"

    def test_prompt_after_double_dash_excludes_separator(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--", "-p", "is", "a", "flag"])
        assert args.prompt == ["-p", "is", "a", "flag"]

    def test_reasoning_flag_not_absorbed_into_prompt(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--reasoning", "design", "the", "architecture"])
        assert args.reasoning is True
        assert args.prompt == ["design", "the", "architecture"]

    def test_fast_flag_not_absorbed_into_prompt(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--fast", "quick", "summary"])
        assert args.fast is True
        assert args.prompt == ["quick", "summary"]

    def test_dry_run_flag_parsed(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--dry-run", "hello"])
        assert args.dry_run is True
        assert args.prompt == ["hello"]

    def test_show_plan_flag_parsed(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--show-plan", "hello"])
        assert args.show_plan is True
        assert args.prompt == ["hello"]

    def test_dry_run_and_show_plan_are_equivalent_in_parser(self):
        parser = cli._build_parser()
        dry = parser.parse_args(["--dry-run", "hello"])
        plan = parser.parse_args(["--show-plan", "hello"])
        assert dry.dry_run is True
        assert plan.show_plan is True
        assert dry.prompt == plan.prompt

    def test_list_runtimes_requires_no_prompt(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--list-runtimes"])
        assert args.list_runtimes is True

    def test_allowed_tools_flag_parsed(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--allowed-tools", "Read,Grep", "search"])
        assert args.allowed_tools == ["Read", "Grep"]
        assert args.prompt == ["search"]

    def test_block_tools_flag_parsed(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--block-tools", "Write,Bash", "hello"])
        assert args.block_tools == ["Write", "Bash"]

    def test_max_turns_flag_parsed(self):
        parser = cli._build_parser()
        args = parser.parse_args(["--max-turns", "10", "hello"])
        assert args.max_turns == 10

    def test_mcp_config_flag_parsed(self, tmp_path):
        parser = cli._build_parser()
        cfg = tmp_path / "mcp.json"
        cfg.write_text('{"mcpServers": {}}')
        args = parser.parse_args(["--mcp-config", str(cfg), "hello"])
        assert args.mcp_config == cfg

    def test_context_budget_flags_parsed(self):
        parser = cli._build_parser()
        args = parser.parse_args(
            [
                "--max-context-tokens",
                "128000",
                "--warning-threshold",
                "0.6",
                "--critical-threshold",
                "0.8",
                "hello",
            ]
        )
        assert args.max_context_tokens == 128000
        assert args.warning_threshold == 0.6
        assert args.critical_threshold == 0.8
