# Craftflow Plugin

Router-first AI development orchestration for Claude Code. Every build, debug, review, and plan task routes through `craftflow:craftflow-router`, which dispatches the right agent chain and tracks workflow state in `.craftflow/state/`.

---

## What it does

- **Routes all dev tasks** — one entry point (`craftflow-router`) dispatches to the right agent automatically
- **Agent chain** — 11 specialized agents: planner, component-builder, bug-investigator, code-reviewer, integration-verifier, and more
- **24 skills** — planning, TDD, code-generation, debugging patterns, diff-driven docs, and others
- **Hook system** — 24 Python lifecycle hooks for memory protection, write guards, URL caching, and session continuity
- **Shared state** — `.craftflow/state/` is readable by both Claude Code and Cursor

---

## Benchmarks

Craftflow ships three benchmark scripts that measure structural coverage, runtime cost, and behavior correctness. Pre-computed results live in [`docs/benchmarks/`](docs/benchmarks/).

### Latest results (2026-06-30)

**Signal coverage** — 15 trust-harness signals (orchestration ownership, durable state, plan/build trust gates, skill precedence, debug generalization, fail-closed verification, replay coverage):

**33/33 signals passing**

**Runtime metrics:**

| Dimension | Score |
|-----------|-------|
| Enforcement gates | 9/9 |
| Context management signals | 6/6 |
| Parallelism signals | 4/4 |

**Behavior bakeoff** — 8 critical orchestration scenarios (plan divergence, phase gating, memory persistence, fail-closed verification, etc.): all **pass**.

**Real telemetry** from 46 production workflows (BUILD=38, DEBUG=2, PLAN=6):

| Metric | Value |
|--------|-------|
| Mean events per workflow | 9.09 (median 8, max 35) |
| Re-review triggered | 13% of runs |
| Re-verify triggered | 2.2% of runs |

### Run benchmarks yourself

All scripts run from the plugin root (`tools/craftflow-plugin`):

```bash
# Signal benchmark — scores craftflow against any ref repos in ref-/
python3 plugins/craftflow/scripts/craftflow_reference_benchmark.py

# Runtime complexity — context load, chain depth, gates, parallelism + real telemetry
python3 plugins/craftflow/scripts/craftflow_runtime_benchmark.py

# Full suite — inventory, delta register, behavior bakeoff, roadmap (needs ref repos)
python3 plugins/craftflow/scripts/craftflow_worldclass_benchmark.py
```

The first two scripts score craftflow on its own even without reference repos. The worldclass script produces the full comparative suite; see [`docs/benchmarks/`](docs/benchmarks/) for the last generated outputs.

---

## Install (new machine)

Requires Claude Code CLI and a GitHub account with access to `aicraft-sdk/ai-craft`.

### 1. Install the plugin

```bash
# Sparse-clones only what's needed from the repo
claude plugin marketplace add aicraft-sdk/ai-craft --sparse .claude-plugin tools/craftflow-plugin

# Install at user scope (available across all projects)
claude plugin install craftflow@craftflow --scope user
```

### 2. Add the router to `~/.claude/CLAUDE.md`

Open `~/.claude/CLAUDE.md` (create it if it doesn't exist) and add:

```markdown
# Craftflow Orchestration (Always On)

IMPORTANT: ALWAYS invoke craftflow-router on ANY development task. First action, no exceptions.
IMPORTANT: Explore project first, then invoke the router.
IMPORTANT: Prefer retrieval-led reasoning over pre-training-led reasoning for orchestration decisions.
IMPORTANT: Never bypass the router. It is the system.
IMPORTANT: NEVER use Edit, Write, or Bash (for code changes) without first invoking craftflow-router.

**Skip Craftflow ONLY when:**
- User EXPLICITLY says "don't use craftflow", "without craftflow", or "skip craftflow"
- No interpretation. No guessing. Only these exact opt-out phrases.

[Craftflow]|entry: craftflow:craftflow-router
```

### 3. Add permissions to `~/.claude/settings.json`

Add the following entries to the `permissions` array:

```json
"Bash(mkdir -p .craftflow)",
"Bash(mkdir -p .claude/craftflow)",
"Edit(.craftflow/*)",
"Write(.craftflow/*)",
"Edit(.claude/craftflow/*)",
"Write(.claude/craftflow/*)"
```

### 4. Restart Claude Code

Fully quit and reopen. Craftflow-router is now active on every dev task across all projects.

---

## Updating

```bash
claude plugin update craftflow
```

---

## Plugin structure

```
plugins/craftflow/
├── agents/          # 11 agent definitions (markdown)
├── skills/          # 24 skill definitions (each has SKILL.md)
├── scripts/         # 24 Python hook scripts
├── hooks/           # Hook event bindings for Claude Code
├── hooks.json       # Hook bindings for Cursor
├── config/          # hook-mode.json (audit vs enforce)
├── templates/       # Reusable doc and harness templates
└── tests/           # Fixture-based replay tests (28 fixtures)
```

## State convention

State lives in `.craftflow/state/` at the project root:

| Path | Purpose |
|------|---------|
| `project/` | Long-lived state across sessions (architecture decisions, blockers) |
| `workflows/<wf-id>/` | Per-workflow state scoped to a single run |
| `activeContext.md` / `patterns.md` / `progress.md` | Fallback root files |

---

## Detailed docs

- [Installation guide](../../docs/craftflow-install.md)
- [Full reference](../../docs/craftflow.md)
