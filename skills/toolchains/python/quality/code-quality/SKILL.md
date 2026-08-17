---
id: code-quality
name: Code Quality
tags:
- python
- code-quality
- anti-patterns
- code-review
- pep8
- ruff
- pylint
- static-analysis
---

# Python Code Quality

High-value Python code-quality anti-patterns to check during review or self-review.
This skill is **review-focused**: it covers correctness and readability defects that a
reviewer (or a linter) should flag, separate from testing mechanics (`pytest`) and
whole-codebase health scoring (`code-quality-scoring`).

> **Source note:** These anti-patterns are derived from CAST Highlight's Python code
> quality indicators (https://doc.casthighlight.com/), which reference **PEP 8** and the
> Python data model as primary sources. Where a rule mirrors PEP 8, the PEP is the
> authoritative source. All examples are original.

## When to Use This Skill

Use it when the task is **"is this Python code clean and correct?"** — for example:

- Reviewing a pull request and checking for the defects below.
- Self-reviewing before opening a PR.
- Configuring `ruff`/`pylint`/`mypy` rules so CI catches these automatically.
- Writing or updating a team's Python code-quality guidance.

Do **not** use it for testing mechanics (use the `pytest` skill) or for scoring a whole
codebase's health and technical debt (use the `code-quality-scoring` skill).

## Core Anti-Patterns (Summary)

Six highest-value Python anti-patterns. Each has a non-compliant/compliant example and a
"how to test" note in the reference doc:

- **Custom exceptions must derive from `Exception`** — a class meant to be raised that
  inherits from `object` fails at runtime and breaks every `except` clause.
- **Compare singletons with `is`, not `==`** — use `is`/`is not` for `None`/`True`/`False`
  (PEP 8); use `is` *only* for singletons, never for value comparison.
- **Avoid bare / overly broad `except`** — catch the narrowest type you can handle; a
  generic `except Exception` only as a last-position fallback that logs or re-raises.
- **Avoid wildcard imports** (`from x import *`) — they hide dependencies, risk silent
  name collisions, and defeat static analysis.
- **Replace magic numbers with named constants** — promote non-obvious literals to
  documented, named constants.
- **Remove unused local variables** — a dead assignment misleads readers and can hide a
  bug where a value was meant to be used.

## Best Practices

- **Gate these in CI.** Most are enforceable cheaply with `ruff` (F403/F405 wildcard,
  F841 unused locals, `E711`/`E712` singleton comparison), `pylint`, and `mypy`. Put the
  lint step in CI so review effort focuses on judgment, not mechanics.
- **Prefer specific exception handlers.** Order handlers narrowest-first; reserve a
  generic `except Exception` for a logging/re-raising last resort.
- **Name intent, not values.** A constant's *name* documents why a threshold exists; a
  bare literal documents nothing.

## Anti-Patterns (What to Avoid)

- Inheriting custom exceptions from `object` or directly from `BaseException`.
- `== None`, `== True`, or `is "some literal"`.
- Bare `except:` or `except BaseException:` that swallows control-flow signals.
- `from module import *` outside a curated `__init__.py` with explicit `__all__`.
- Unexplained numeric literals in business logic.
- Assigned-but-never-read locals left behind by a stale refactor.

## Navigation

- **[quality-antipatterns.md](references/quality-antipatterns.md)**: Full non-compliant
  vs compliant examples and a "how to test" note for each of the six anti-patterns.

## Related Skills

- **pytest** (`toolchains/python/testing/pytest`): testing mechanics — fixtures,
  parametrization, mocking. Several anti-patterns here (broad `except`, malformed
  exception classes) directly cause flaky tests.
- **code-review-standards** (`universal/process/code-review-standards`): the
  project-wide, severity-tagged review checklist that incorporates equivalents of these.
- **code-quality-scoring** (`universal/quality/code-quality-scoring`): whole-codebase
  health and technical-debt scoring, rather than individual findings.