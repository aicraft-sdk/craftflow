---
name: retro
description: |
  Read-only retrospective of ONE finished Craftflow workflow — extracts friction
  signals (REM-FIX cycles, tripped breaker, gates, loop counts, refutations,
  proof gaps, stop failures, fallbacks, slow agents, contradictions, compactions)
  and proposes ranked, evidence-cited environment fixes. Never writes anything.

  Use this skill when: retro, retrospective, what went wrong in this workflow,
  post-mortem this workflow, why did this build take so many cycles, how do we
  stop this happening again.

  Triggers: retro, /retro, craftflow retro, retro this workflow, workflow
  retrospective, post-mortem.

  NOTE: This skill does NOT go through craftflow-router. It is an inspection tool — read-only, same exception as the `status` and `failure-digest` skills.
allowed-tools: Read, Bash, Glob
---

# craftflow Retro

Non-mutating retrospective of one workflow. A script extracts friction signals from the workflow artifact and event log; this skill maps them to ranked, evidence-cited proposals.

**Does NOT go through craftflow-router.** This is an inspection tool, not a development task — the same explicitly allowed exception as `craftflow:status` and `craftflow:failure-digest`. A request to implement, apply, or fix a retro proposal is NOT exempt: it is a normal development request and goes through craftflow-router (PLAN/BUILD).

This complements `failure-digest` (cross-workflow ranking of recurring failures) and `learn-distiller` (recurrence-gated folding into `patterns.md`): retro looks at ONE workflow and proposes environment fixes from it, with no recurrence required.

---

## Step 1 — Resolve Script Path

The first rule that yields an existing file wins.

**(a) Skill-relative (both hosts).** `SKILL_FILE` is this skill's own SKILL.md: in Claude Code, `<Base directory for this skill>/SKILL.md`; in Cursor (context contains `CRAFTFLOW_PLATFORM: cursor`), `~/.cursor/skills/retro/SKILL.md`. Run:

```bash
python3 -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]).expanduser().resolve().parents[2]/'scripts'/'craftflow_retro.py'; print(p if p.is_file() else '')" "<SKILL_FILE>"
```

A non-empty output is `SCRIPT`. `resolve()` follows the Cursor symlink into the plugin checkout, and in Claude Code it points at the same plugin copy the skill was loaded from.

**(b) Claude Code only, and only if (a) printed nothing.** Read `~/.claude/plugins/installed_plugins.json`, take `craftflow@craftflow` → `installPath`, then run:

```bash
test -f "<installPath>/scripts/craftflow_retro.py"
```

Use it only if that exits 0. Never use an `installPath` script without that check (an older installed version has no retro script).

**(c) Otherwise** print exactly the following and stop. Never fall back to reading the artifact by hand.

```
craftflow_retro.py not found next to this skill. craftflow:retro needs the whole craftflow plugin (skill + scripts/). Claude Code: update the craftflow plugin. Cursor: run install-cursor.sh from a local craftflow checkout (npx skills add copies only the skill folder and is not supported for retro).
```

---

## Step 2 — Select the Workflow

- User gave a wf id: use `--wf <id>`.
- User said "this workflow", "last workflow", or nothing specific: use `--latest`.
- User gave a feature phrase: run `python3 "$SCRIPT" --state-dir .craftflow/state --list` and pick only if exactly one candidate's `workflow_uuid` or `user_request` contains the phrase; otherwise show the candidates and ask.
- If the script reports `ERROR: ambiguous_selection`: run `--list`, show the candidates, ask the user to pick, and stop until answered.
- Never guess.

---

## Step 3 — Run the Extractor

```bash
python3 "$SCRIPT" --state-dir .craftflow/state (--wf <id> | --latest)
```

On any non-zero exit: show the stderr `ERROR:` line verbatim, say the retro could not run, and stop. Never report "no friction" on an error. Do not `Read` the artifact or events files directly (oversized state reads are redirected by the PreToolUse hook, and the script is the single source of truth).

---

## Step 4 — No Friction

If `friction_found` is false, print `No friction found in <workflow_uuid> (<workflow_type>).` plus any `data_gaps`, and stop. Zero proposals.

---

## Step 5 — Map Signals to Proposals

Ordered rubric. The first matching row wins, so each proposal gets exactly one category.

| # | Signal pattern (cite the evidence) | Category | Destination |
|---|---|---|---|
| 1 | `remfix_cycles` / `stop_failures` / `contradictions` evidence excerpts showing path/location confusion or wrong-root writes | nav pointer | `CLAUDE.md` or `docs/ai/` |
| 2 | `remfix_cycles` with ≥2 entries in one phase (`details.by_phase`) whose reasons share one mechanical defect shape; `circuit_breaker_tripped`, `loop_counts`, `proof_gaps`, `pending_gate` attach as amplifiers to this proposal | deterministic check | `skills/craftflow-router/references/harness-self-checks.md`, `scripts/craftflow_arch_lint.py` rule, or a `hooks/hooks.json` hook |
| 3 | `contradictions` or `doubt_refutations` (a reviewer approved what a later verifier/hunter refuted), or `remfix_cycles` reasons that are judgment calls | reviewer standard | `skills/code-review-patterns/SKILL.md` or `docs/ai/QUALITY.md` |
| 4 | `compactions` | pruning | `.craftflow/state/project/patterns.md` / `activeContext.md` (router-owned memory: proposal only) |
| 5 | `slow_agents`, `fallbacks`, `stop_failures` without nav evidence | tool/telemetry change | proposal text only |
| 6 | `circuit_breaker_tripped` or `loop_counts` not attached to a row-2 proposal | tool/telemetry change (loop budget, breaker threshold, or the telemetry that would explain the loop) | proposal text only |
| 7 | `proof_gaps` not attached to a row-2 proposal | deterministic check (a check that would have proven the gapped phase or `proof_status`) | `skills/craftflow-router/references/harness-self-checks.md` or `scripts/craftflow_arch_lint.py` rule |
| 8 | `pending_gate` not attached to a row-2 proposal | nav pointer (where the gate's resolution is documented) | `CLAUDE.md` or `docs/ai/` |

Rules:

- Mechanical violations become checks; judgment issues become reviewer prose rules. Never propose adding steering-file bloat.
- Rows are tried in order. A signal used as a row-2 amplifier is not reused by rows 6-8. Rows 6-8 exist so every signal id has a standalone category.
- Every proposal cites ≥1 evidence item verbatim as `artifact:<key>` or `events:L<line>`. If a proposal has no citable evidence, drop it.
- Any fired signal id not cited by any surviving proposal (dropped for lack of evidence, or beyond the cap) MUST be listed on one line after the proposals: `Signals with no mappable proposal: <id>, <id>`. If every signal is in that line, `m = 0` and the header still shows `<n> signals, 0 proposals`. It is never reported as "No friction found".
- Rank proposals by the highest `weight` of the signals they cite; at most 5 proposals.
- Do not editorialize beyond the evidence; if a cause is unclear, say "cause unclear from evidence".
- Pruning proposals (row 4) MUST state the observed compaction count and cite the `compact_occurred` events (`events:L<line>`). They MUST NOT assert that memory or context caused or amplified any friction; say "cause unclear from evidence" unless the script's evidence itself shows the cause.

---

## Step 6 — Present

One header line:

```
Retro: <workflow_uuid> (<type>) — <n> signals, <m> proposals
```

Then numbered proposals, each followed by indented `Evidence:` and `Why:` lines:

```
N. [category] <title> → <destination>
   Evidence: artifact:<key> | events:L<line> ...
   Why: <one sentence tied to the evidence>
```

Then the `Signals with no mappable proposal: <ids>` line if any fired signal is uncited. Closing line:

`To act on a proposal, ask for it as a normal request — it will go through craftflow-router (PLAN/BUILD).`

---

## Step 7 — Boundaries

**Do NOT call craftflow-router. Do NOT create tasks. Do NOT modify files.**

No ledger writes (v2), no transcript mining (v2). Proposals are text only; applying one is a separate normal request that goes through craftflow-router.

---

## Terminal Usage

Run from any terminal; the script reads on-disk state and never writes:

```bash
alias cfretro='python3 /path/to/craftflow_retro.py'
cfretro --state-dir .craftflow/state --latest
cfretro --state-dir .craftflow/state --list
cfretro --state-dir .craftflow/state --wf <wf-id>
```
