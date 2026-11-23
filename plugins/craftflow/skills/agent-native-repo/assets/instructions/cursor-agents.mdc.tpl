---
description: Repository agent protocol. AGENTS.md is the source of truth; this rule only points to it.
alwaysApply: true
---

Follow `AGENTS.md` at the repository root and the nearest `AGENTS.md` in every directory you edit.
Before writing code, complete the Agent Session Protocol in the root `AGENTS.md`: claim one task, work on its own branch, open a draft PR that references the task.
Do not restate AGENTS.md rules in Cursor rules. Add a `.cursor/rules/*.mdc` file only for rules scoped by `globs`.
