### Fast Path Reference

This file is canonical law for fast-path BUILD routing. Read by the router before BUILD preparation when `build_mode` is `"fast_path"` or `"fast_path_escalated"`.

#### risk_keyword_scan — Keyword Table

Scans request text (case-insensitive). Returns matched keywords. Empty array = fast path.

| Group | Keywords |
|-------|----------|
| Security | `auth`, `authz`, `oauth`, `jwt`, `password`, `credential`, `secret`, `cert`, `ssl`, `tls`, `encrypt`, `decrypt`, `permission`, `role`, `session`, `access control` |
| Database / schema | `migration`, `schema change`, `alter table`, `drop table`, `seed`, `remove column`, `drop column`, `export data`, `data export` |
| Payment | `payment`, `billing`, `stripe`, `checkout`, `subscription`, `invoice` |
| Explicit risk markers | `critical path`, `production data`, `irreversible`, `truncate`, `delete all`, `purge` |

**Conservative principle:** When in doubt, add the keyword to the list. The cost of a false positive (full chain on a simple request) is wasted tokens. The cost of a false negative (fast path on auth code) is a missed review.

#### fix_verify_keyword_scan — Keyword Table

Scans phase-objective text (or, for `plan:N/A` BUILDs, the workflow artifact's own
`user_request` field) case-insensitively. Used only by BUILD workflows to decide whether the
current phase is fixing an existing defect (fires `fix_verify_gate`) versus adding new
functionality (does not fire). DEBUG workflows never need this scan — `fix_verify_gate` always
fires for DEBUG (see `SKILL.md § 7 → Fix-Verify Dispatch Rule`).

| Group | Keywords |
|-------|----------|
| Fix-indicating language | `fix`, `close`, `patch`, `vulnerability`, `bug`, `defect`, `corrupt` |

**Fail-safe principle (the opposite of `risk_keyword_scan`'s conservative-add principle
above):** when the text to scan is missing or empty, default to **not** firing
`fix_verify_gate`. A false negative here costs nothing (today's existing verification level
still applies); a false positive would add unnecessary review overhead to genuine new-feature
BUILD work.

#### Workflow Artifact Fields

Three fields are added to the artifact schema and initialized at workflow creation:

```json
{
  "build_mode": "fast_path",
  "fast_path_risk_signals": [],
  "fast_path_escalated": false
}
```

Values:
- `build_mode`: `"fast_path"` | `"fast_path_escalated"` | `"standard"`
- `fast_path_risk_signals`: `[]` (fast path taken) or `["auth", "schema change"]` (full chain)
- `fast_path_escalated`: `false` | `true`

#### Agent Dispatch Table

| Phase | Agent | Standard BUILD | Fast path | Escalated | Effort |
|-------|-------|---------------|-----------|-----------|--------|
| `build-implement` | component-builder | ✓ | ✓ | ✓ (already done) | low (fast) / medium (std) |
| `build-review` | code-reviewer | ✓ | ✗ skip | ✓ | medium |
| `build-hunt` | silent-failure-hunter | ✓ | ✗ skip | ✓ | medium |
| `build-verify` | integration-verifier | ✓ | ✓ | ✓ (re-verify) | high |
| `build-doc-sync` | doc-syncer | ✓ | ✗ skip | ✗ skip | low |
| `learn-distill` | learn-distiller | ✓ (gated) | ✓ (gated) | conditional (gated) | low |
| `skill-distill` | skill-author | ✓ (gated) | ✓ (gated) | conditional (gated) | low |
| `memory-finalize` | (inline) | ✓ | ✓ | ✓ | low |

#### Effort Steering Directives

The router appends a one-line steering directive to each dispatched agent prompt based on the phase's effort profile:

| Effort | Directive appended to agent prompt |
|--------|-----------------------------------|
| low | `Note: Be terse. Skip exploratory narration. Output only required contract fields.` |
| medium | `Note: Reason at medium depth. Surface key risks but keep prose tight.` |
| high | `Note: Reason fully before concluding. Surface all edge cases and alternatives. Do not abbreviate.` |

Steering is appended as a new line at the end of the `## Task Context` section in the agent scaffold.
Steering is informational — it does not change which fields are required in the Router Contract.

#### Learn-Distill Gate

`learn-distill` is dispatched at the end of BUILD (standard and fast-path) and DEBUG workflows ONLY when `remediation_history` in the workflow artifact is non-empty (i.e., at least one remediation cycle ran). This prevents per-build cost for clean workflows.

When gated out (empty `remediation_history`): skip `learn-distill` entirely, proceed directly to `memory-finalize`.
When gated in: run `craftflow_learn_scan.py` via Bash, pass output to learn-distiller, append `learn_distilled` to event log, then proceed to `memory-finalize`.

Fast-path note: fast-path BUILD with a clean verifier pass never enters the gate (empty `remediation_history`). Fast-path BUILD that escalated (verifier fail → escalation → re-verify) will have `remediation_history` populated and runs `learn-distill` exactly like standard BUILD.

#### Skill-Distill Gate

`skill-distill` is dispatched at the end of BUILD (standard and fast-path) ONLY when the skill-candidate ledger has at least one new `gate_eligible` candidate not already `promoted`/`rejected`. This is a separate, more specific gate than Learn-Distill's above — it does not depend on `remediation_history` at all, and it can fire on a clean pass (no remediation needed) if the ledger has an eligible candidate left over from prior workflows.

**Gate check:** `python3 {plugin_root}/scripts/craftflow_skill_ledger.py --query --ledger .craftflow/state/project/skill-candidates.json`. A candidate qualifies when `distinct_workflows >= 2` (`gate_eligible()`'s own threshold — plain `--query` does not pre-filter, so the router applies this filter itself) AND `status == "candidate"` (excludes `proposed`, `promoted`, `rejected`). Note: `status` is one of `candidate|proposed|promoted|rejected` only — "stale" is never an assignable status value; stale `candidate` entries (>90 days, no new evidence) are instead pruned (deleted outright) by `--prune`, never transitioned to a "stale" status.

**When gated out** (no qualifying candidate): skip `skill-distill` entirely, proceed to `learn-distill`'s own gate check (if not already evaluated) or directly to `memory-finalize` if that gate is also gated out.

**When gated in:** dispatch `skill-author` with the chosen candidate id. After it returns:
- `STATUS: COMPLETE` → apply the Skill-Distill Approval Flow (`craftflow-router/SKILL.md` § 8) — `AskUserQuestion` with Approve / Approve + register in SKILL_HINTS / Reject / Defer — before proceeding to `memory-finalize`. Append `skill_proposed` to the event log, then `skill_promoted` or `skill_rejected` once the user's choice is executed (no event for Defer — the candidate is unchanged). Under `JUST_GO=true`, this gate is a de facto REVERT-class gate (fail-closed default `Defer`) — see `SKILL.md` § 8's JUST_GO carve-out; never auto-select Approve or Approve + SKILL_HINTS.
- `STATUS: SKIPPED` → passing state, append `skill_distill_skipped` (if not already logged this workflow) and proceed straight to `memory-finalize`, no `AskUserQuestion`. (Distinct from `skill_candidates_observed`, which is reserved for the unconditional `--observe` step at memory-finalize — see `SKILL.md` § 13.)
- `STATUS: FAIL` or no return (stuck/timeout) → `skill-author` is NOT a `kind:remfix` origin (no code-defect remediation applies to a proposal-authoring failure). Do not create a REM-FIX task and do not block the chain. Append `skill_distill_failed` to the event log with the failure reason if available, leave the candidate's ledger status unchanged (still `candidate`, so it is flagged for retry the next time this gate fires on a future workflow), and proceed straight to `memory-finalize` using `chain_tail_task_id` as it stood before this gate fired.

Runs identically on standard BUILD and both fast-path variants (clean and escalated) — the gate check itself has no fast-path-specific behavior, unlike Learn-Distill's `remediation_history` dependency.

#### Gate Table

| Gate / Rule | Standard | Fast path | Escalated |
|-------------|----------|-----------|-----------|
| `phase_exit_gate` | ✓ | ✓ | ✓ |
| `failure_stop_gate` | ✓ | ✓ | ✓ |
| `memory_sync_gate` | ✓ | ✓ | ✓ |
| `1a-SCOPE rule` | ✓ | ✗ dropped | ✓ RESTORED |
| `doubt_verify_gate` | conditional | ✗ | ✗ |
| `learn_distill_gate` | conditional | conditional | conditional |
| `skill_distill_gate` | conditional | conditional | conditional |
| `fix_verify_gate` | conditional | conditional | conditional |

`1a-SCOPE rule`: dropped on fast path (no reviewer/hunter findings to scope); RESTORED on escalated path. On escalated fast path, `1a-SCOPE` applies using the same CRITICAL+HIGH threshold (at least one CRITICAL and at least one HIGH in the escalated reviewer+hunter output) as the standard parallel review phase. Rationale: the escalated spawn produces equivalent output to the standard parallel review phase — same agents, same output shape.

`fix_verify_gate`: unlike `doubt_verify_gate` (Standard-only), this gate's eligibility does not
depend on `build_mode` at all — it can fire on fast-path and escalated BUILD exactly as on
standard BUILD, since a fast-path BUILD can just as easily be a small, otherwise-low-risk bug
fix (the exact incident that motivated this gate's own existence,
`wf-dormant-legacy-section-text-relo-20260731-160917-cbe203b6`, was itself a user-approved
fast-path BUILD). Its trigger condition is `fix_verify_keyword_scan` above (BUILD) or
unconditional (DEBUG), evaluated independently of `build_mode`.

#### Escalation Cap

Max one REM-FIX cycle after escalation. After one REM-FIX + re-verify:
- Re-verify PASS → memory-finalize → done
- Re-verify FAIL → `failure_stop_gate` fires → stop with `BLOCKING: true`
- No further escalation cycles are permitted

#### Announcement Protocol

Router announces path before child task creation (one line each):
- `-> FAST-PATH BUILD (no risk signals)` — fast path taken
- `-> FULL BUILD (risk signals: {matched keywords})` — standard path taken
- `-> FAST-PATH BUILD [ESCALATED] (verifier FAIL — reviewer + hunter spawned)` — escalation event

#### Fast-Path Verifier Prompt Note

On fast path, the `## Previous Agent Findings` section is OMITTED from the verifier prompt (no reviewer/hunter ran). The verifier runs its own independent scenario coverage rather than reconciling reviewer/hunter findings.

On escalated path, the standard merged findings handoff is used (same as standard BUILD §5 pattern).
