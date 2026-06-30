# Craftflow

Router-first AI development orchestration for Claude Code and Cursor AI.

Every build, debug, review, and plan task routes through a single entry point that dispatches the right agent chain, tracks workflow state, and enforces quality gates before marking anything complete.

---

## What it does

- **Routes all dev tasks** — one router (`craftflow-router`) classifies intent and dispatches to the right agent chain automatically
- **Agent chain** — 11 specialized agents: planner, component-builder, bug-investigator, code-reviewer, silent-failure-hunter, integration-verifier, and more
- **16 skills** — planning patterns, TDD, code generation, debugging, diff-driven docs, and others
- **Hook system** — Python lifecycle hooks for memory protection, write guards, URL caching, and session continuity
- **Shared state** — `.craftflow/state/` is readable by both Claude Code and Cursor
- **Cursor support** — inline sequential execution with progress blocks in Cursor chat; no sub-agents required

## Workflow types

| Signal | Workflow | Agent chain |
|--------|----------|-------------|
| build, implement, create | BUILD | component-builder → code-reviewer → silent-failure-hunter → integration-verifier |
| error, bug, fix, crash | DEBUG | bug-investigator → code-reviewer → integration-verifier |
| plan, design, spec | PLAN | planner → plan-gap-reviewer |
| review, audit | REVIEW | code-reviewer (advisory) |

---

## Install — Claude Code

```bash
claude plugin marketplace add aicraft-sdk/craftflow
```

Then add to `~/.claude/CLAUDE.md`:

```markdown
[Craftflow]|entry: craftflow:craftflow-router
```

## Install — Cursor AI

Copy the two MDC rules to your global Cursor rules directory:

```bash
mkdir -p ~/.cursor/rules/core
cp rules/craftflow-router.mdc ~/.cursor/rules/core/
cp rules/craftflow-state.mdc ~/.cursor/rules/core/
```

Craftflow will activate automatically on every dev request via `alwaysApply: true`.

---

## How it works

### Claude Code

```
User request
  → craftflow-router (Skill)
    → dispatches Agent(agentType="craftflow:component-builder", ...)
    → dispatches Agent(agentType="craftflow:code-reviewer", ...)
    → dispatches Agent(agentType="craftflow:integration-verifier", ...)
    → writes .craftflow/state/workflows/{wf}.json
    → updates .craftflow/state/project/activeContext.md
```

### Cursor AI

```
User request
  → craftflow-router.mdc (auto-injected)
    → Read("skills/cursor-router/SKILL.md")
    → inline execution: Read agent .md → follow inline → progress block
    → writes .craftflow/state/cursor-wf.json
    → updates .craftflow/state/project/activeContext.md
```

Progress blocks appear inline in Cursor chat at each phase transition:

```
╔══ CRAFTFLOW BUILD ════════════════════════════════╗
║ Phase 2 / 4 · code-reviewer                       ║
╠═══════════════════════════════════════════════════╣
║ ✅ component-builder      DONE                    ║
║ ⏳ code-reviewer          RUNNING...              ║
║ ○  silent-failure-hunter  WAITING                 ║
║ ○  integration-verifier   WAITING                 ║
╚═══════════════════════════════════════════════════╝
```

---

## State

Workflow state lives at `.craftflow/state/` in the project root:

| Path | Purpose |
|------|---------|
| `project/activeContext.md` | Current focus, decisions, learnings — persists across sessions |
| `project/patterns.md` | Durable code patterns and gotchas |
| `project/progress.md` | Completed workflows and verification evidence |
| `workflows/{wf-id}.json` | Per-workflow artifact (plan file, phase status, evidence) |

---

## License

MIT — see [LICENSE](LICENSE)
