---
id: testing
name: Testing Guidelines
tags:
  - testing
  - pytest
  - quality
---

# Testing Guidelines

Write fast, deterministic tests that document behavior and guard against regressions.

- Use `pytest` fixtures for shared setup and temporary resources.
- Name tests so their intent is obvious: `test_<scenario>_<expected_result>`.
- Assert on outcomes, not implementation details.
- Cover edge cases and error paths, not just the happy path.
- Keep unit tests independent; avoid ordering dependencies.
- Mock external services and I/O, but avoid over-mocking the system under test.
