# Verify dispatch

### Doubt-Verify Dispatch Rule

After `integration-verifier` returns PASS, dispatch `craftflow:doubt-verifier` when ANY of these conditions are true:
- `verification_rigor: critical_path` is set in the workflow artifact
- The phase included irreversible operations: data migration, schema change, auth flow, payment path, secret rotation
- The phase changed ≥3 files across ≥2 directories (cross-module blast radius)

**Do NOT dispatch doubt-verifier when:**
- `verification_rigor: standard` and no irreversible ops
- Integration verifier returned FAIL (remediate first)
- A doubt cycle already returned DOUBT_THEATER (escalate instead)

**Doubt-verify task shape:**
```text
TaskCreate({
  subject: "CRAFTFLOW doubt-verifier: Adversarial review cycle {N}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:doubt-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:critical_path adversarial pass\n\nCycle: {N}\n\n## Artifact\n{artifact description: plan name, diff summary, or scenario table}\n\n## Contract\n{acceptance criteria from plan or integration-verifier exit criteria}",
  activeForm: "Adversarial doubt review"
})
```

In task-tool fallback mode (`references/task-tool-fallback.md`) include `## Cycle` N (per-phase, from 1) in the `craftflow:doubt-verifier` dispatch prompt; there is no `Cycle:` task line for the agent to read.

**After doubt-verifier returns:**
- First run `python3 "$CRAFTFLOW_SCRIPTS/craftflow_gate_check.py" --gate doubt_cycles_max --point post_verdict --cycle {N} --wf {workflow_uuid} --project-root "$PROJECT_ROOT"` (N = the `Cycle:` of this cycle's task; in task-tool fallback mode (`references/task-tool-fallback.md`), the `## Cycle` value of this dispatch's prompt, else the agent's echoed `Cycle number:`, never `telemetry.loop_counts`). `advance` → apply the bullet for the returned verdict, except that no REM-FIX is created; its loop-counter increment still applies; then advance regardless of verdict, as the hard-stop bullet does (advance once in total). `proceed` → apply the verdict bullets below. Any other result → `loop_cap_check_failed` (§ 14).
- `DOUBT_VERDICT: CONFIRMED` → advance phase cursor, continue workflow
- `DOUBT_VERDICT: REFUTED` → create REM-FIX task (same as normal verification failure), increment loop counter
- `DOUBT_VERDICT: DOUBT_THEATER` → log event, skip further doubt cycles, advance to Memory Update with advisory note
- `CYCLE_COMPLETE: true` on cycle `policy.doubt_cycles_max` (= 3) → advance regardless of verdict (`policy.doubt_cycles_max` (= 3)-cycle hard stop)

**Doubt cycle counter:** Track in workflow artifact under `telemetry.loop_counts.doubt_verify`. Max `policy.doubt_cycles_max` (= 3) cycles; router enforces the hard stop.

**Reliability-gates evidence recording (best-effort, non-blocking):** After the `DOUBT_VERDICT`
is captured, check whether this cycle's `plan:`/`reason:` text loosely matches any seeded gate in
`.craftflow/state/project/reliability-gates.json` — a case-insensitive substring match against 3+
significant words from the gate's `title`, or against any `assertionRefs` basename, is sufficient
(this is bookkeeping, not a hard gate). For every matching gate, call:
```bash
python3 {plugin_root}/scripts/craftflow_reliability_gates.py --record-evidence {gate_id} \
  --wf {workflow_uuid} --outcome {pass|fail} --note "{one-line cycle summary}" \
  --state-dir .craftflow/state
```
Map `DOUBT_VERDICT: CONFIRMED` to `--outcome pass`; `REFUTED` or `DOUBT_THEATER` to `--outcome
fail`. A non-zero exit from this call is non-blocking — log the error to the workflow event log
and continue; same best-effort posture as the existing skill-ledger `--observe`/`--prune` calls.

### Fix-Verify Dispatch Rule

After `integration-verifier` returns PASS, evaluate `fix_verify_gate` independently of
`doubt_verify_gate` above — either, both, or neither may fire for the same phase. Unlike
`doubt_verify_gate`, `fix_verify_gate` is NOT restricted to `build_mode: "standard"` — it fires
on fast-path and escalated BUILD too (see `references/fast-path.md → Gate Table`).

**DEBUG workflows:** always fires. A DEBUG workflow is by definition fixing a demonstrated bug
— `bug-investigator`'s own contract already requires a reproduced `Regression:` scenario with
`ROOT_CAUSE`, so a pre-fix state to compare against always exists.

**BUILD workflows:** fires only when the current phase is fixing an existing defect, not
adding new functionality. Determine this via `fix_verify_keyword_scan`
(`references/verify-dispatch.md → fix_verify_keyword_scan — Keyword Table`, below):
1. If a plan exists (`plan_file != null`): scan `normalized_phases[phase_cursor].objective`
   (case-insensitive) against the keyword table.
2. If `plan:N/A`: scan the workflow artifact's own `user_request` field — the same text
   `risk_keyword_scan` already scans at BUILD prep. There is no separate phase-objective
   structure to read when no plan exists (`normalized_phases` is empty).
3. If the text to scan (from either source) is empty, missing, or otherwise unreadable:
   default to **not** firing. This is a fail-safe default, not fail-open: a false negative here
   just means today's existing verification level still applies (no regression); a false
   positive would add unnecessary review cost to genuine new-feature BUILD work.
4. A keyword match fires the gate; no match does not.

**Fix-verify task shape:**
```text
TaskCreate({
  subject: "CRAFTFLOW doubt-verifier: Fix-verification cycle {N}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:fix-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Prove the fix is load-bearing and scan for dormant sibling defects\n\nCycle: {N}\n\n## Artifact\n{diff summary or files changed in this phase}\n\n## Original Defect\n{DEBUG: bug-investigator's ROOT_CAUSE + reproduced Regression scenario. BUILD: the phase objective/exit-criteria text that matched fix_verify_keyword_scan.}\n\n## Contract\nReproduce the pre-fix state against the exact reported reproduction input and confirm it exhibits the defect; confirm the post-fix state closes it on the same input; search for other reachable call sites of the same root-cause logic.",
  activeForm: "Verifying fix is load-bearing"
})
```

In task-tool fallback mode (`references/task-tool-fallback.md`) include `## Cycle` N (per-phase, from 1) in the fix-verify dispatch prompt too; there is no `Cycle:` task line to read.

**After doubt-verifier returns** (see `agents/doubt-verifier.md → ## Fix-Verify Contract`):
- First run `python3 "$CRAFTFLOW_SCRIPTS/craftflow_gate_check.py" --gate fix_verify_cycles_max --point post_verdict --cycle {N} --wf {workflow_uuid} --project-root "$PROJECT_ROOT"` (after the contract-override check below has been applied; N = the `Cycle:` of the fix-verify task; in task-tool fallback mode (`references/task-tool-fallback.md`), the `## Cycle` value of this dispatch's prompt). `advance` → apply the bullet for the returned (post-override) verdict, except that no REM-FIX is created; its loop-counter increment still applies; then advance regardless of verdict (the `policy.fix_verify_cycles_max` hard stop that the cycle-counter paragraph inherits from `doubt_verify`; advance once in total). If `doubt_verify_gate` also fired on this phase, a capped verdict does not block phase exit under the stricter-verdict rule; the other gate's verdict still applies. `proceed` → apply the verdict bullets below. Any other result → `loop_cap_check_failed` (§ 14).
- `FIX_VERDICT: LOAD_BEARING` → advance phase cursor, continue workflow.
- `FIX_VERDICT: NOT_LOAD_BEARING` or `SIBLING_FOUND` → create a REM-FIX task (same shape as any
  other verification failure — `origin:router`, `phase:fix-verify`), increment
  `telemetry.loop_counts.fix_verify`.
- Contract-override check (`references/post-agent-validation.md → ### Contract overrides`, in the Post-Agent Validation section 8): a claimed
  `FIX_VERDICT: LOAD_BEARING` where `PRE_FIX_OUTPUT` does not actually differ from
  `POST_FIX_OUTPUT` in the direction the fix claims is downgraded to `NOT_LOAD_BEARING` before
  the router acts on it.
- If `doubt_verify_gate` also fired on this same phase, the two verdicts are independent —
  treat the stricter of the two as authoritative (existing Hard Rule, `SKILL.md → ## 12. Chain Execution
  Loop` step 6, "if two agents in the same phase return contradictory verdicts... treat the
  stricter verdict as authoritative"). Either a `DOUBT_VERDICT: REFUTED` or a
  `FIX_VERDICT: NOT_LOAD_BEARING`/`SIBLING_FOUND` blocks phase-exit; never average or reconcile
  the two contracts into one.
- When both gates fire for the same phase, dispatch sequentially — fix-verify first, then
  doubt-verify — never in parallel. `doubt-verifier` has not previously been parallelized
  against itself; both dispatches are read-only and cheap enough to run in sequence, and
  introducing a new same-agent parallel-dispatch pattern is out of scope for this change.

**Fix-verify cycle counter:** Track in workflow artifact under
`telemetry.loop_counts.fix_verify`. Max `policy.fix_verify_cycles_max` (= 3) cycles; router enforces the hard stop (same rule as
`doubt_verify`'s own counter above).

**Reliability-gates evidence recording (best-effort, non-blocking):** After `FIX_VERDICT` is
captured (and any contract-override downgrade from `references/post-agent-validation.md → ### Contract overrides`
in the Post-Agent Validation section 8 has already been applied), always call:
```bash
python3 {plugin_root}/scripts/craftflow_reliability_gates.py --record-evidence \
  fix-verify-evidence-completeness --wf {workflow_uuid} --outcome {pass|fail} \
  --note "{one-line cycle summary}" --state-dir .craftflow/state
```
(`LOAD_BEARING` → `--outcome pass`; `NOT_LOAD_BEARING`/`SIBLING_FOUND` → `--outcome fail`) — every
fix-verify cycle is definitionally evidence for this gate, recorded unconditionally. Additionally,
apply the same loose-match check as the Doubt-Verify Dispatch Rule above against this cycle's
`plan:`/`reason:`/diff-summary text for any OTHER seeded gate, and record evidence there too if it
matches. A non-zero exit from either call is non-blocking — log and continue.

#### fix_verify_keyword_scan — Keyword Table

Scans phase-objective text (or, for `plan:N/A` BUILDs, the workflow artifact's own
`user_request` field) case-insensitively. Used only by BUILD workflows to decide whether the
current phase is fixing an existing defect (fires `fix_verify_gate`) versus adding new
functionality (does not fire). DEBUG workflows never need this scan — `fix_verify_gate` always
fires for DEBUG (see `references/verify-dispatch.md → ### Fix-Verify Dispatch Rule`, pointed to from `SKILL.md § 7`).

| Group | Keywords |
|-------|----------|
| Fix-indicating language | `fix`, `close`, `patch`, `vulnerability`, `bug`, `defect`, `corrupt` |

**Fail-safe principle (the opposite of `risk_keyword_scan`'s conservative-add principle
in `references/fast-path.md`):** when the text to scan is missing or empty, default to **not** firing
`fix_verify_gate`. A false negative here costs nothing (today's existing verification level
still applies); a false positive would add unnecessary review overhead to genuine new-feature
BUILD work.

### Distill gate task shapes

For the paths that do not load `build-graph-standard.md` (a `fast_path` BUILD, a DEBUG workflow), these repeat the two gate task shapes and the `chain_tail_task_id` rule of its `#### Learn-Distill Gate` and `#### Skill-Distill Gate`. The gate checks, eligibility rules and outcomes are in `references/fast-path.md` (`#### Learn-Distill Gate`, `#### Skill-Distill Gate`); the Skill-Distill candidate is the one with the highest `distinct_workflows`, ties broken by earliest `first_seen`. `chain_tail_task_id` starts as `verifier_task_id` (no doc-sync step).

Learn-Distill task (create when `remediation_history` is non-empty):

```text
TaskCreate({
  subject: "CRAFTFLOW learn-distiller: Distill recurring failure signatures",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:learn-distill\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Non-empty remediation_history triggered the recurrence gate\n\nRun `python3 {plugin_root}/scripts/craftflow_learn_scan.py` via Bash, read this workflow's remediation_history and event log, and distill any recurring failure signature into project/patterns.md ## Common Gotchas per your own agent contract. Emit a Router Contract when done.",
  activeForm: "Distilling recurring failure signatures"
}) -> learn_distill_task_id
TaskUpdate({ taskId: learn_distill_task_id, addBlockedBy: [chain_tail_task_id] })
```

Set `chain_tail_task_id = learn_distill_task_id`. After `learn-distiller` completes, append `learn_distilled` to the event log.

Skill-Distill task (create when the gate check finds an eligible candidate):

```text
TaskCreate({
  subject: "CRAFTFLOW skill-author: Propose skill from candidate {candidate_id}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:skill-distill\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Ledger has a gate_eligible candidate not already promoted/rejected\n\nCandidate id: {candidate_id}\n\nRead this candidate from the ledger, apply the anti-slop rubric, and stage a proposal or emit STATUS: SKIPPED per your own agent contract.",
  activeForm: "Distilling a project skill proposal"
}) -> skill_distill_task_id
TaskUpdate({ taskId: skill_distill_task_id, addBlockedBy: [chain_tail_task_id] })
```

Set `chain_tail_task_id = skill_distill_task_id`. Handle the `skill-author` outcomes as `references/fast-path.md` (`#### Skill-Distill Gate`) says.
