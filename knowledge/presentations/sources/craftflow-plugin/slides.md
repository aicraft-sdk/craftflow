---
marp: true
theme: default
paginate: true
style: |
  section {
    font-family: 'Inter', system-ui, sans-serif;
  }
  h1 { color: #1e293b; }
  h2 { color: #334155; }
  strong { color: #3157ff; }
  code { background: #f1f5f9; padding: 0.1em 0.3em; border-radius: 4px; }
---

# Working with Craftflow

One router, two tools, one shared brain.

*How we build, debug, review, and plan with the Craftflow plugin — in Claude Code and Cursor.*

---

## Agenda

1. The problem Craftflow solves
2. The mental model
3. Claude Code — setup + live demo
4. Cursor — setup + live demo
5. Safety net & visibility
6. Takeaways

---

## Why we needed this

- Ad-hoc prompting → inconsistent quality, no repeatable gate before "done"
- No shared memory: Claude Code and Cursor sessions didn't know about each other's work
- Easy to skip steps under pressure — spec, review, verification

**Craftflow's answer:** one entry point every dev request goes through, in both tools.

---

## The mental model

```
User request
   → craftflow-router (single entry point)
       → classifies intent
       → dispatches the right agent chain
       → writes shared workflow state
```

Same shape in Claude Code and Cursor — only the dispatch mechanism differs.

---

## Four request types, four agent chains

| Signal | Workflow | Agent chain |
|---|---|---|
| build, implement, create | **BUILD** | component-builder → code-reviewer → silent-failure-hunter → integration-verifier |
| error, bug, fix, crash | **DEBUG** | bug-investigator → code-reviewer → integration-verifier |
| plan, design, spec | **PLAN** | planner → plan-gap-reviewer |
| review, audit | **REVIEW** | code-reviewer (advisory) |

You don't pick the chain — the router classifies your request and picks it for you.

---

## What the chain actually checks

- **Spec first** — traceable `FR-###` / `SC-###` requirement IDs
- **TDD** — red → green → refactor
- **Code review** — a dedicated agent, not the builder grading its own work
- **Silent-failure hunt** — catches scope creep and quietly-swallowed errors
- **Integration verification** — classifies any gap as `Missing` / `Partial` / `Contradicts` / `Unrequested`, with severity

Nothing is marked done without evidence.

---

## Claude Code — setup

One line in `~/.claude/CLAUDE.md`:

```
[Craftflow]|entry: craftflow:craftflow-router
```

That's it — every build/debug/review/plan request now routes through Craftflow automatically.

---

## Claude Code — how it runs

```
User request
  → craftflow-router (Skill)
    → Agent(agentType="craftflow:component-builder", ...)
    → Agent(agentType="craftflow:code-reviewer", ...)
    → Agent(agentType="craftflow:integration-verifier", ...)
    → writes .craftflow/state/workflows/{wf}.json
    → updates .craftflow/state/project/activeContext.md
```

Real, isolated subagents — not role-play in the router's own turn.

---

## 🎬 Demo — Claude Code

**Script:**
1. Type a small, real build request in Claude Code
2. Narrate: router activates → classifies as BUILD → announces the chain
3. Watch the agent dispatches happen one by one
4. Open `.craftflow/state/workflows/{wf-id}.json` — show the live artifact
5. Point out the statusline: `⚡ feature-name 60% · 🟢 phase_2`

---

## Cursor — setup

An always-on MDC rule (`craftflow-router.mdc`) auto-injects Craftflow into **every** dev request — no per-message trigger needed.

```bash
bash tools/craftflow-plugin/plugins/craftflow/install-cursor.sh
```

Installs the rule and symlinks the `cursor-router` skill into `~/.cursor/skills/`.

---

## Cursor — how it runs

```
User request
  → craftflow-router.mdc (auto-injected)
    → Read("skills/cursor-router/SKILL.md")
    → Task(subagent_type: generalPurpose, prompt: <agent role>)  — per phase
    → code-reviewer + silent-failure-hunter dispatched in parallel
    → writes .craftflow/state/cursor-wf.json
    → updates .craftflow/state/project/activeContext.md
```

Cursor has no native task-tracking tools, so phase state is self-managed in `cursor-wf.json`.

---

## 🎬 Demo — Cursor

**Script:**
1. Make the same kind of build request in Cursor
2. Narrate: MDC rule fires silently, cursor-router takes over
3. Watch the inline progress block update per phase:

```
╔══ CRAFTFLOW BUILD ════════════════════════════════╗
║ Phase 2 / 4 · code-reviewer                       ║
╠═══════════════════════════════════════════════════╣
║ ✅ component-builder      DONE                    ║
║ ⏳ code-reviewer          RUNNING...              ║
╚═══════════════════════════════════════════════════╝
```

4. Open `.craftflow/state/cursor-wf.json` and compare it to the Claude Code artifact

---

## Safety net

- **Worktree isolation** — BUILD work happens in a dedicated git worktree by default; merged back only after a clean-tree check and lock
- **Hooks** — pre-write/bash guards block risky edits, session-start hooks resume context, stop hooks persist state
- **Constitution** — `constitution.md` holds immutable project MUST-rules; PLAN halts if intent violates one

---

## Visibility

- **Shared state** — `.craftflow/state/` is read by *both* tools: `project/` (durable), `workflows/{wf-id}/` (per-run), `workspace/` (cross-repo)
- **Feature-named workflows** — `wf-{slug}-{date}-{hex}`, easy to recognize at a glance
- **Live status, any time:**

```bash
craftflow status          # in-session
cfstatus --all            # from a second terminal, non-interrupting
```

---

## Takeaways

- Just describe your task naturally — **the router decides the workflow**, you don't pick it
- Same quality bar and same shared memory whether you're in **Claude Code or Cursor**
- Want visibility? Check `.craftflow/state/` or run `craftflow status`
- One-line setup per tool: CLAUDE.md entry, or `install-cursor.sh`

**Next step:** try a real BUILD or DEBUG request today and watch the chain run.
