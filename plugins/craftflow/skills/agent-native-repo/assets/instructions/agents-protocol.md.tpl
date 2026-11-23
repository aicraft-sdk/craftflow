## Agent Session Protocol

Many people run agents (Claude Code, Cursor, Codex, cloud agents) in this repository at once. These rules keep them from overlapping and keep quality independent of who runs which tool. CI enforces the parts marked (CI).

### 1. Start: claim exactly one task

{{CLAIM_STEPS}}

- Never start work on a task someone else holds, and never hold two tasks at once.
- Your claim expires after {{CLAIM_TTL_HOURS}} hours without a push or comment; the claim sweeper then returns the task to `ready`.

### 2. Work: one task, one branch, one PR

- Branch `agent/<task-id>-<slug>`, ideally in its own git worktree. Push early and often: each push keeps your claim alive.
- The PR description references the task (`Closes #N` or `Task: <id>`). (CI) A PR without a task reference, or for a task another open PR already references, fails `agent-guards`.
- Stay inside the task's scope. Found something else? Open a new task in `ready`; do not fix it here.

### 3. Done means proven

- Done = every acceptance criterion met, every verification command passing, and evidence pasted in the PR. A claim without evidence is not done.
- Bug fixes start with a failing test that reproduces the bug.
- (CI) Never delete or skip tests, remove assertions, or add lint/type suppressions (`noqa` without a code, `ts-ignore`, `eslint-disable`, `nolint`) to make checks pass. `agent-guards` rejects these.
- (CI) Do not edit harness files (`AGENTS.md`, `CLAUDE.md`, `.claude/`, `.cursor/`, CI workflows, lint config, `.agents/guards.json`) unless the task is a harness change; a human must add the `harness-change` label.

### 4. Review

- (CI) A reviewer agent with a clean context checks every PR against the task's acceptance criteria. Address each finding or explain in the PR why it does not apply.
- A human merges. Agents never merge their own PRs.

### 5. Stop: leave a handoff, not a diary

- Before ending a session on unfinished work, fill in the PR's Handoff section: state, decisions and why, next step, blockers.
- Do not append to shared log files (`progress.md`, `TODO.md`, `session-handoff.md`) during task work: concurrent agents overwrite each other there. Handoffs live in the PR; durable lessons go into `AGENTS.md` or `docs/` through a `harness-change` PR.
- Abandoning a task? Remove yourself as assignee and move it back to `ready`.

### 6. Memory

- Rules and how the system works: `AGENTS.md`, nested `AGENTS.md`, `docs/`. These are canon.
- Who is doing what: the task tracker. Never a markdown list.
- Plans, specs and notes: {{NON_CANON_DIR}}. Useful context, never the source of truth.
- A mistake that happened twice becomes a rule here, then a lint rule or test.
