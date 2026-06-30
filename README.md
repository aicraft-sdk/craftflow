# Craftflow Plugin

Router-first AI development orchestration for Claude Code. Every build, debug, review, and plan task routes through `craftflow:craftflow-router`, which dispatches the right agent chain and tracks workflow state in `.craftflow/state/`.

---

## What it does

- **Routes all dev tasks** — one entry point (`craftflow-router`) dispatches to the right agent automatically
- **Agent chain** — 11 specialized agents: planner, component-builder, bug-investigator, code-reviewer, integration-verifier, and more
- **16 skills** — planning, TDD, code-generation, debugging patterns, diff-driven docs, and others
- **Hook system** — 24 Python lifecycle hooks for memory protection, write guards, URL caching, and session continuity
- **Shared state** — `.craftflow/state/` is readable by both Claude Code and Cursor

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

## Install (Cursor AI)

Installs Craftflow into Cursor as the single team orchestrator, replacing AIDLC.

### Prerequisites

- Cursor AI with agent mode enabled
- This repo cloned locally (any path)
- Phase 1 of the Cursor integration plan complete:
  `tools/craftflow-plugin/plugins/craftflow/skills/cursor-router/SKILL.md` must exist

### One command

```bash
bash tools/craftflow-plugin/install-cursor.sh
```

### What it does

1. Creates a symlink: `~/.cursor/skills/cursor-router → <repo>/tools/craftflow-plugin/plugins/craftflow/skills/cursor-router`
2. Copies `craftflow-router.mdc` and `craftflow-state.mdc` to `~/.cursor/rules/core/`
   (copied, not symlinked — MDC files reference local paths at copy time)
3. Archives AIDLC: moves `~/.cursor/skills/aidlc/` to `~/.cursor/skills/aidlc.bak/`
4. Removes `~/.cursor/rules/core/aidlc-routing.mdc`
5. Prints a confirmation summary

The script is **idempotent** — safe to re-run after `git pull` to refresh the copied MDC rules.

### Verify

Open Cursor on any project and send a development request (e.g., "add a helper function to…").
The Cursor agent should read `cursor-router/SKILL.md` and route through the Craftflow workflow.

### Notes

- Zero Claude Code files are changed — only `~/.cursor/` is modified
- To roll back AIDLC: `mv ~/.cursor/skills/aidlc.bak ~/.cursor/skills/aidlc` then restore `aidlc-routing.mdc` from the plugin's `rules/` directory
- After `git pull` on this repo, re-run the script to refresh the MDC copies

---

## Plugin structure

```
plugins/craftflow/
├── agents/          # 11 agent definitions (markdown)
├── skills/          # 16 skill definitions (each has SKILL.md)
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
