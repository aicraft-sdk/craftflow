# Craftflow

Router-first AI development orchestration for Claude Code and Cursor AI.

Every build, debug, review, and plan task routes through a single entry point that dispatches the right agent chain, tracks workflow state, and enforces quality gates before marking anything complete.

---

## What it does

- **Routes all dev tasks** — one router (`craftflow-router`) classifies intent and dispatches to the right agent chain automatically
- **Agent chain** — 13 specialized agents: planner, component-builder, bug-investigator, code-reviewer, silent-failure-hunter, integration-verifier, and more
- **27 skills** — planning patterns, TDD, code generation, debugging, diff-driven docs, workflow status, and others
- **Hook system** — Python lifecycle hooks for memory protection, write guards, URL caching, and session continuity
- **Shared state** — `.craftflow/state/` is readable by both Claude Code and Cursor
- **Feature-named workflows** — workflow folders, files, and worktrees are named after the feature (`wf-auth-refactor-20260706-d4e5f6a7`) so you can identify them at a glance
- **Live statusline progress** — a `⚡ feature-name 60% · 🟢 phase_2` segment appended to claude-hud, updates every ~300ms without interrupting the running agent
- **Cursor support** — each workflow phase dispatched via a real, isolated Cursor `Task` call (`subagent_type: generalPurpose`), with code-reviewer and silent-failure-hunter dispatched in parallel; progress blocks appear in Cursor chat at each phase transition
- **Reliability-gates ledger** — `craftflow_reliability_gates.py` tracks proven invariants
  (append-only, fail-closed evidence log) across workflows
- **Skill-distillation pipeline** — `craftflow_skill_ledger.py` mines recurring workflow patterns
  into candidate skills, staged via `craftflow_skill_propose.py` and promoted via
  `craftflow_skill_promote.py`
- **Safe-shell / stop-verify / hook-trust guards** — `craftflow_safe_shell_guard.py` blocks
  catastrophic shell command patterns pre-execution, `craftflow_stop_verify.py` is an opt-in
  end-of-session verification gate (inert by default), and `craftflow_hook_trust.py` is a standalone
  hash-manifest trust gate for repo-local hook scripts (not itself wired into `hooks.json`)

## Workflow types

| Signal | Workflow | Agent chain |
|--------|----------|-------------|
| build, implement, create | BUILD | component-builder → code-reviewer → silent-failure-hunter → integration-verifier |
| error, bug, fix, crash | DEBUG | bug-investigator → code-reviewer → integration-verifier |
| plan, design, spec | PLAN | planner → plan-gap-reviewer |
| review, audit | REVIEW | code-reviewer (advisory) |

---

## Quality layer

Craftflow enforces a quality contract from spec through verification. These conventions are active on every workflow:

### Spec conventions (AI_FIRST rules 11–14)

| Rule | Convention |
|------|-----------|
| `FR-###` / `SC-###` | Stable functional-requirement and success-criteria identifiers in `docs/ai/specs/`. Plans and verifier scenarios reference these IDs for traceability. |
| `[NEEDS CLARIFICATION]` | Any unresolved spec or plan item is tagged. `plan-gap-reviewer` blocks advancement until all markers are resolved. |
| `[P]` parallel markers | Steps within a plan phase are marked `[P]` when they can run concurrently. Each phase also declares a delivery strategy: `mvp_first`, `incremental`, or `parallel_team`. |
| Tech-agnostic AC | Success criteria must describe user-observable outcomes, not implementation metrics. "User sees results in 3 s" is valid; "API response time < 200 ms" is not — restate it in user terms. |

### Gap classification

When verification fails, every FAIL scenario is classified before remediation begins:

| Type | Meaning |
|------|---------|
| `Missing` | Required work is entirely absent from the implementation |
| `Partial` | Exists but incompletely satisfies the acceptance criterion |
| `Contradicts` | Code conflicts with the spec, plan, or a MUST constraint in the constitution |
| `Unrequested` | Code implements behavior not present in the accepted plan (scope creep) |

Severity: `CRITICAL` / `HIGH` / `MEDIUM` / `LOW`

Classification is written by `integration-verifier` (step 3.5, `### Gap Classification` block) and by `silent-failure-hunter` (Unrequested gap detection). `craftflow_contract_validate.py` machine-validates the `GAP_CLASSIFICATION` field in every agent contract.

### Constitution

Project immutable principles live at `.craftflow/state/project/constitution.md`. The PLAN workflow reads this file before brainstorming and halts if the user's intent violates a MUST constraint. SHOULD violations are logged as advisories but do not block. Amendment requires explicit user approval and a version bump.

---

## Feature-named workflow folders

Every new workflow gets an id that embeds a feature slug:

```
wf-{slug}-{YYYYMMDD-HHMMSS}-{8hex}
e.g.  wf-auth-refactor-20260706-140312-d4e5f6a7
```

The slug comes from the current git branch name (if it's a genuine feature branch — not `main`/`master`/`develop`) or from the user request text. The timestamp + 8-hex suffix keeps ids unique, so two concurrent workflows for the same feature never collide.

Worktrees follow the same pattern: `.claude/worktrees/{slug}-{hex}` and branch `wf-{slug}-{hex}`, so they're identifiable AND traceable back to their workflow.

Old on-disk ids (pre-slug format) are fully backward-compatible — nothing changes for existing workflows.

---

## Live % progress in the statusline

When Craftflow is active, the statusline shows a live progress segment:

```
⚡ auth-refactor 60% · 🟢 phase_2 (3/5)
```

Updated every ~300ms alongside claude-hud, derived from the workflow's `.craftflow/state/` data with no agent interruption. The segment disappears when no workflow is active.

The progress % uses a 4-tier fallback: explicit phase list → phase-status map → coarse stage estimate (e.g. `fast_path_selected`→15%, `phase_exit_gate_passed`→80%) → 0%.

**Setup** — wire the wrapper once in `~/.claude/settings.json`:
```json
{
  "statusLine": {
    "type": "command",
    "command": "bash /path/to/craftflow-plugin/plugins/craftflow/scripts/craftflow_statusline.sh"
  }
}
```

**Revert** to plain claude-hud by restoring the original command (preserved as a comment in `craftflow_statusline.sh`).

---

## Check workflow status — non-interrupting

While a Craftflow agent is running, a second terminal can read live status from the
shared `.craftflow/state/` directory at any time:

```bash
# Resolve the script path (run once):
python3 -c "
import json, pathlib
try:
    reg = json.loads(pathlib.Path('~/.claude/plugins/installed_plugins.json').expanduser().read_text())
    ip  = reg['plugins']['craftflow@craftflow'][0]['installPath']
    print(pathlib.Path(ip) / 'scripts' / 'craftflow_status_report.py')
except Exception as e:
    print(f'# {e}')
"

# Add a shell alias (replace PATH with the output above):
alias cfstatus='python3 /path/to/craftflow_status_report.py'

# Examples:
cfstatus                              # current / last-active workflow
cfstatus --all                        # one-line summary of every workflow
cfstatus --verbose                    # phases + agent chain + event timeline + narrative
cfstatus --feature "auth-refactor"    # find by feature slug, goal text, or request
cfstatus --worktree auth-refactor-d4e5f6a7  # find by worktree branch/slug suffix
cfstatus --statusline                 # single-line % segment (used by the wrapper)
cfstatus --project /path/to/project  # explicit root (auto-detected otherwise)
cfstatus --json                       # machine-readable JSON for tooling
```

You can also invoke it in-session (between agent turns) with:
```
craftflow status
```

---

## Install — Claude Code

```bash
claude plugin marketplace add aicraft-sdk/craftflow
claude plugin install craftflow
```

Then add to `~/.claude/CLAUDE.md`:

```markdown
[Craftflow]|entry: craftflow:craftflow-router
```

## Install — Cursor AI

If you have a local checkout of this plugin, run the script directly — it wires up the MDC rules **and** symlinks the `cursor-router` skill into `~/.cursor/skills/cursor-router` automatically (idempotent; backs up any stale content it finds there):

```bash
bash tools/craftflow-plugin/plugins/craftflow/install-cursor.sh
```

Without a local checkout (curl-piped), the script can only install the MDC rules — it has no local plugin directory to link the skill from, and prints a fallback note. In that case, install the skill separately first:

```bash
# 1. Install the cursor-router skill
npx skills add aicraft-sdk/craftflow --skill cursor-router

# 2. Install MDC rules (auto-activates Craftflow on every dev request)
curl -fsSL https://raw.githubusercontent.com/aicraft-sdk/craftflow/main/install-cursor.sh | bash
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
    → dispatches each phase via Task(subagent_type: generalPurpose, prompt: <agent role + overrides>)
    → dispatches code-reviewer + silent-failure-hunter in parallel (two Task calls, same message)
    → writes .craftflow/state/cursor-wf.json
    → updates .craftflow/state/project/activeContext.md
```

There is no real task-tracking system in Cursor (no `TaskCreate`/`TaskUpdate`/`TaskList`/`TaskGet`) — phase-state tracking is self-managed via `cursor-wf.json`.

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
| `workflows/{wf-id}.json` | Per-workflow artifact (plan, phase status, evidence). New format: `wf-{slug}-{date}-{hex}.json` |
| `precompact-state.json` | Per-turn pointer to the current active workflow (written by the `Stop` hook) |

---

## Architecture graph

`docs/generated/architecture.md` is a generated (not hand-maintained) view of
this plugin's actual hook/agent/skill wiring — hook event → matcher → script,
agent → declared skills, agent → declared tools — introspected straight from
`hooks/hooks.json` and `agents/*.md` / `skills/*/SKILL.md` frontmatter.
Regenerate with `pnpm run gen:craftflow-graph` after touching any of those;
`pnpm run verify:craftflow-graph` checks it isn't stale.

---

## License

MIT — see [LICENSE](LICENSE)
