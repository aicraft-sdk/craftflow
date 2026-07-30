# Skill-Distillation Feature — Hot-Path Latency Benchmark

Date: 2026-07-30

## Method

This is a hot-path latency benchmark, not a cross-repo scoring benchmark
(compare `2026-07-29-reference-benchmark.md`, which scores structural trust
signals). The skill-distillation feature (`docs/plans/2026-07-29-craftflow-skill-distillation-plan.md`,
uncommitted at benchmark time) touches two surfaces that now run
unconditionally on genuine hot paths:

1. `craftflow_pretooluse_guard.py` — fires on **every** Edit/Write/Bash tool
   call in any session using this plugin. This feature added
   `_is_protected_skill_ledger_or_proposal_path()` (skill-ledger/proposal
   protection) and `_cp_mv_like_write_targets()`/`_dd_write_targets()`
   (cp/mv/ln/install/rsync/dd destination-argument detection).
2. `craftflow_skill_ledger.py --prune` — wired unconditionally into
   `SKILL.md § 13. Memory Finalization` immediately after `--observe`, so it
   now runs once per workflow, forever, independent of `SKILL_DISTILL: skip`.

Both scripts are invoked exactly as `hooks.json` / the router invoke them —
`python3 <script>` as a fresh subprocess, JSON payload on stdin (matching the
real Claude Code `PreToolUse` hook contract: `session_id`, `transcript_path`,
`cwd`, `hook_event_name`, `tool_name`, `tool_input`) for the guard, and CLI
args (`--prune --state-dir ... --project-root ...`, matching the router's own
invocation in `skills/craftflow-router/SKILL.md`) for the ledger. Timings are
wall-clock `subprocess.run()` durations measured from a scratch harness (not
a shipped component; deleted after the run), against this repo's REAL state:
149 workflow JSON artifacts already in `.craftflow/state/workflows/`, so the
guard's `latest_workflow_file()` glob-and-stat cost reflects genuine current
scale, not a synthetic empty state dir.

## 1. `craftflow_pretooluse_guard.py` — PreToolUse hot path (50 runs/payload)

| Payload | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) | Min (ms) |
|---|---|---|---|---|---|
| `plain_edit` (unrelated Edit) | 209.6 | 206.3 | 245.2 | 277.9 | 187.4 |
| `write_protected_ledger` (Write to skill-candidates.json) | 231.1 | 205.7 | 365.4 | 585.5 | 194.3 |
| `bash_cp_dest` (`cp ... skill-proposals/.../SKILL.md`) | 209.5 | 209.2 | 227.0 | 228.2 | 191.4 |
| `bash_plain_noop` (`ls -la ...`, no new checks triggered) | 213.0 | 208.0 | 246.8 | 314.4 | 193.9 |
| `bash_long_complex` (long multi-stage pipeline) | 212.2 | 208.3 | 236.3 | 273.2 | 198.0 |

`write_protected_ledger`'s p95/max (365ms / 585ms) did not reproduce on a
30-run re-check of the same payload in isolation (mean 204.0ms, max 226.9ms,
no outliers) — treated as transient OS scheduling noise on this machine, not
a cost specific to `_is_protected_skill_ledger_or_proposal_path()`. All five
payload types land in the same ~190–250ms band on repeat measurement.

**Where the ~200ms floor comes from:** a bare `python3 -c pass` subprocess on
this machine costs ~40ms. `python3 -X importtime craftflow_pretooluse_guard.py`
shows `craftflow_skill_ledger` (pulled in via `import craftflow_skill_ledger as
skill_ledger` at module top level, dragging in `tempfile` → `shutil` →
`random`/`bz2`/`lzma`) at ~15–70ms cumulative import cost across repeated
measurements, plus `craftflow_skill_promote` at ~5–12ms. The remainder is
per-process Python/dynamic-linker startup overhead on macOS, which this
benchmark cannot separate further without instrumenting the interpreter
itself. The guard's own decision logic (path comparisons, one glob over 149
workflow JSON files — measured at ~2.5ms in-process) is a small fraction of
the measured wall time; **process-spawn + import cost, not the new
protected-path/cp-mv logic, dominates this hook's latency.**

**New-logic marginal cost:** comparing `plain_edit` (209.6ms mean, exercises
none of the new checks) against `bash_cp_dest` (209.5ms mean, exercises the
new cp/mv destination-argument detector) and `write_protected_ledger` (231ms
mean / 204ms on re-check, exercises the new ledger/proposal path check) shows
no measurable marginal cost from either new check — they're inside the same
noise band as the pre-existing checks.

## 2. `craftflow_skill_ledger.py --prune` — per-workflow lifecycle call (10 runs/size)

Ledger status mix: ~40% `candidate` / ~30% `rejected` / ~30% `promoted`.
Each `promoted` entry has a REAL `.claude/skills/<name>/SKILL.md` fixture
(valid frontmatter, a `craftflow-referenced-paths` entry pointing at a file
that genuinely exists on disk, a future `craftflow-review-after`), so the
anti-rot check's frontmatter read + referenced-path `.exists()` check runs
its real logic for every promoted entry rather than fast-pathing on a
missing name.

| Candidates | Mean (ms) | Median (ms) | p95 (ms) | Max (ms) | Min (ms) |
|---|---|---|---|---|---|
| 10 | 84.2 | 81.9 | 98.6 | 104.4 | 75.4 |
| 100 | 100.9 | 98.5 | 112.1 | 113.6 | 88.4 |
| 500 | 157.1 | 154.8 | 187.6 | 205.9 | 135.5 |
| 1000 | 220.8 | 219.6 | 236.1 | 237.8 | 207.5 |

**Scaling: linear, not worse.** Marginal cost per additional candidate:
~0.19ms/candidate (10→100), ~0.14ms/candidate (100→500), ~0.13ms/candidate
(500→1000) — flat-to-slightly-sublinear as ledger size grows (fixed
process/import overhead amortizes over more candidates). No quadratic or
worse behavior observed up to 1000 candidates. Even at 1000 candidates
(300 real promoted-skill file reads + stats), total wall time stays under
a quarter of a second.

**Forward-looking scale concern — re-examined and downgraded (not a bug,
and practically bounded, not truly "unbounded"):** `MAX_CANDIDATES = 200`
caps only `status: candidate` entries — `rejected` entries are permanent
tombstones and `promoted` entries are permanent records; neither is ever
evicted by the size cap, and `--prune` now runs unconditionally after every
workflow's memory-finalize. The first pass of this report framed the
resulting cost growth as unbounded; a closer look at what actually drives
`rejected`/`promoted` entry count shows it is bounded in practice by three
independent factors, not by workflow volume:

1. **Ledger keys collapse by (surface, signature), not by event.**
   `candidate_id()` hashes a coarse directory-prefix bucket (top 2 path
   segments) plus a normalized reason string (`upsert_candidates()`,
   `derive_surface()`) — repeated occurrences of the SAME underlying pattern
   across many workflows update ONE entry in place, they never create new
   ones. Ledger size tracks the count of genuinely DISTINCT recurring
   patterns a codebase produces, which grows far slower than workflow count.
2. **Reaching `rejected` or `promoted` status requires an explicit human
   decision every time**, via the router's Phase-3 `AskUserQuestion`
   approval gate (`Approve` / `Approve+SKILL_HINTS` / `Reject` / `Defer`) —
   `cmd_reject`/`cmd_approve` both refuse to run without a real, existing
   candidate id and both only ever fire from that human-gated flow. There is
   no automatic path to either terminal status.
3. **Empirically, in this exact project, gate-eligible recurrence is rare.**
   Phase 1's own backtest mined 200 candidates from 141 real workflow logs
   and found only ~4 reached `distinct_workflows >= 2` (the gate threshold)
   — roughly 1 gate-eligible candidate per ~35 workflows. This repo's
   `.craftflow/state/workflows/` directory holds 147 workflow artifacts at
   benchmark time (2026-07-30), for scale.

Combining these: even under a deliberately generous worst case — every
single gate-eligible candidate gets a human decision (100% conversion,
higher than realistic, since some get `Defer`red with no status change) —
reaching 1,000 permanent `rejected`+`promoted` entries would require on the
order of **~35,000 workflows** at this project's own observed recurrence
rate, a scale this project is nowhere near and would take years to
approach even at its current heavy pace. At that hypothetical 1,000-entry
scale, measured cost is still under a quarter of a second (§2 above). This
is a real, disclosed, zero-cap structural property worth keeping in mind
(e.g. if a future project has a much higher recurrence rate), but it is not
a near-term operational risk for THIS project — downgraded from "worth
revisiting" to "correctly bounded by human-gating and observed recurrence
rate, re-check only if either assumption changes materially." (This
project's real skill ledger does not exist yet —
`.craftflow/state/project/skill-candidates.json` is not present at
benchmark time — so there is zero current production impact either way.)

## 3. `craftflow_runtime_benchmark.py` — always-on context re-run

Re-ran the existing benchmark (`python3 craftflow_runtime_benchmark.py`),
output at `docs/benchmarks/2026-07-30-runtime-complexity.{json,md}`.

| Metric (craftflow target) | 2026-07-02 (last run before this feature) | 2026-07-30 (this run) | Delta |
|---|---|---|---|
| `agent_files` | 12 | 13 | +1 (`agents/skill-author.md`) |
| `skill_files` | 24 | 27 | +3 (`skills/skill-distillation/SKILL.md` + others already pending) |
| `always_on_bytes` | 9,307 | 11,546 | +2,239 |
| **`always_on_tokens`** | **2,326** | **2,886** | **+560 (+24.1%)** |

**Verdict on this number: a real, measurable jump, not a rounding blip, but
not structurally alarming.** +560 tokens of always-on (frontmatter-only)
context per agent turn is the cost of one new agent (`skill-author.md`) and
one new skill (`skill-distillation`, plus its `references/rubric.md`, though
reference files are lazy-loaded and not counted in `always_on_tokens` — only
frontmatter is). A 24% relative jump in the always-on budget is worth
tracking if more skills/agents land on top of this in the same cycle, but
2,886 tokens/turn total is still a small fraction of typical context budgets
(low thousands out of 100K–200K token windows).

## Plain-Language Verdict

**Per-tool-call hook latency (`craftflow_pretooluse_guard.py`): acceptable.**
All five payload shapes land in the same ~190–250ms band, and the new
protected-path/cp-mv-destination checks add no measurable latency over the
pre-existing checks — the entire cost is dominated by unavoidable Python
subprocess-spawn overhead that predates this feature. This does add
~200ms of latency to *every* Edit/Write/Bash call in a craftflow session,
which is a real, felt cost in an interactive agent loop, but it is a
pre-existing property of "one Python process per hook call" — not something
this feature made meaningfully worse.

**Per-workflow lifecycle call (`craftflow_skill_ledger.py --prune`):
acceptable today, and scales the right way (linear).** Well under a quarter
of a second even at 1000 realistic candidates with real file-backed rot
checks running for every promoted entry. The one thing worth flagging for
later, not now: `rejected`/`promoted` ledger entries are permanent and
uncapped, so this is a cost that will only grow over the project's lifetime,
never self-prune. Worth a follow-up (e.g. an opt-in archival/compaction pass
for very old promoted/rejected entries) if this project is still running
`--prune` on every workflow in a year and the ledger has grown into the
thousands — not something to build now.

**Always-on context footprint: a real +24% jump, acceptable as a one-time
cost of two new context-consuming units (one agent, one skill), worth
re-measuring if more land in the same area.**

## Next Actions

- Re-run this benchmark after the skill-distillation feature is committed
  and again after any further additions to `agents/` or `skills/*/SKILL.md`
  frontmatter, to confirm the +24% always-on jump doesn't compound silently.
- If `.craftflow/state/project/skill-candidates.json` grows past a few
  hundred `rejected`+`promoted` entries in real usage, re-run the `--prune`
  benchmark against the REAL ledger (not a synthetic fixture) to confirm the
  linear-scaling assumption still holds at production data shapes.
- No action needed on `craftflow_pretooluse_guard.py` — the new checks
  introduced no measurable marginal latency.
