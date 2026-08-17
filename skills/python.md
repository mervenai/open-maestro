---
id: python
name: Python Best Practices
tags:
  - python
  - coding
  - style
---

# Python Best Practices

Write idiomatic, maintainable Python code.

- Prefer type hints and dataclasses over loose dictionaries.
- Keep functions small and focused; use early returns to reduce nesting.
- Use `pathlib.Path` for filesystem operations instead of raw strings.
- Favor composition and explicit dependencies over deep inheritance.
- Add concise docstrings for public modules, classes, and functions.
- Run `pytest` and `ruff` before considering a change complete.
