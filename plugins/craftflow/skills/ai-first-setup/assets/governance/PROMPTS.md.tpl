# Prompts and craftflow usage

## Skills and entry point

- Development workflows are driven by **craftflow**, a Claude Code plugin. If craftflow is running, it is already installed — there is no separate package install step.
- The entry skill is `craftflow:craftflow-router`. It is always-on: invoke it first on any development task (build, plan, debug, review). See the repository's root `CLAUDE.md` for the always-on directive.
- The router dispatches to craftflow's own pipeline of skills/agents (planner, component-builder, code-reviewer, silent-failure-hunter, integration-verifier, memory) — no external CLI or registry is involved.

## Router

- The entry skill is `craftflow:craftflow-router` (or the equivalent always-on router rule installed in your editor — e.g. Cursor's `cursor-router`).
- Use it for build / plan / debug / review routing.

## Practical flow in this repo

1. Create or update `docs/ai/specs/<id>.md`.
2. Let the router produce or reference `docs/plans/<slug>.md`.
3. Implement with TDD; run `{{TEST_CMD}}` (and other targets your plan lists).
4. Put **Spec:** and **Plan:** links plus verification output in the PR ([pull request template](../../.github/pull_request_template.md)).

## Craftflow memory

- Craftflow persists session memory under `.craftflow/state/` (`activeContext.md`, `patterns.md`, `progress.md`), shared across Claude Code and any connected editor integration.
- These files are created and maintained by the router/workflow finalizer — do not hand-author them.
