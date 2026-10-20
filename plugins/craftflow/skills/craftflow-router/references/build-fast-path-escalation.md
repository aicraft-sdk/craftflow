# BUILD fast-path escalation

### Fast Path Escalation

When `build_mode == "fast_path"` AND integration-verifier returns FAIL:

```text
1. Update artifact: build_mode → "fast_path_escalated", fast_path_escalated → true
2. Append event: {"event":"fast_path_escalated","reason":"verifier FAIL on fast path","ts":"{iso_now}"}
3. Announce: "-> FAST-PATH BUILD [ESCALATED] (verifier FAIL — reviewer + hunter spawned)"
4. Spawn reviewer + hunter in parallel (identical to standard BUILD §5 pattern):

**Arch Lint (advisory):** before dispatching code-reviewer, run the check per `references/harness-self-checks.md § Arch Lint` and fold any findings into its dispatch prompt.

TaskCreate({
  subject: "CRAFTFLOW code-reviewer: Review implementation (escalated)",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:re-review\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Fast-path escalation — verifier FAIL triggered reviewer spawn\n\nReview the files and scope of the current phase. Verifier failed on fast path — this is the first reviewer pass.",
  activeForm: "Reviewing code (escalated)"
}) -> escalated_reviewer_task_id

TaskCreate({
  subject: "CRAFTFLOW silent-failure-hunter: Hunt edge cases (escalated)",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:re-hunt\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Fast-path escalation — verifier FAIL triggered hunter spawn\n\nFind silent failures and edge cases adjacent to the current phase. Verifier failed on fast path — this is the first hunter pass.",
  activeForm: "Hunting failures (escalated)"
}) -> escalated_hunter_task_id

5. Wait for BOTH reviewer + hunter to complete.
6. Build merged findings summary (standard BUILD §5 + §6 pattern from build-workflow.md).
7. Apply 1a-SCOPE rule (RESTORED on escalated path — same threshold as standard parallel review phase):
   - If totalCritical ≥ 1 AND totalHigh ≥ 1 (from escalated reviewer+hunter output) → run the "When `1a-SCOPE` fires" procedure from `references/remediation-and-research.md` `### Scope resolution` (including its Jev-assisted step 2), then stop. Wait for reply before creating REM-FIX unless step 2 auto-decided.
   - Otherwise → auto-proceed with ALL_ISSUES (standard rule 1a applies)
   - If totalCritical ≥ 1 AND totalHigh == 0: auto-proceed with ALL_ISSUES (no user scope gate) — this matches the canonical 1a-SCOPE rule: the gate fires only when BOTH signals are present.
8. Create REM-FIX task if needed — conditional on step 7 not already having created one: if step 7's delegated `### Scope resolution` procedure auto-decided via its Jev-assisted step 2 (`decision == "applied"`, which creates the REM-FIX immediately and skips steps 3-4), that task IS the REM-FIX for this cycle — reuse it as `remfix_task_id` below; do NOT create a second REM-FIX task for the same finding (this would double-increment `circuit_breaker.remfix_count`). Otherwise (step 7 stopped at its step 3/4 human ask and a reply resolved the scope, or step 7 fell through to standard rule 1a), create the REM-FIX task now per the standard `remediation-and-research.md` rules, and that new task becomes `remfix_task_id`.
9. Re-verify with merged findings. Create a new re-verify task:

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Re-verify integration (fast-path escalated)",
  description: "wf:{workflow_uuid}\nkind:reverify\norigin:router\nphase:re-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Fast-path escalation re-verify after reviewer+hunter+REM-FIX\n\nESCALATED FAST-PATH RE-VERIFY: Previous Agent Findings section IS required — include the merged reviewer+hunter findings from the escalation round. Run full scenario coverage against the REM-FIX changes. Report whether all issues from the escalated review+hunt pass are resolved.",
  activeForm: "Re-verifying integration (escalated)"
}) -> re_verify_task_id
TaskUpdate({ taskId: re_verify_task_id, addBlockedBy: [remfix_task_id] })

10. Escalation cap enforcement:
    - Re-verify PASS → set `chain_tail_task_id = re_verify_task_id`, then run the Learn-Distill Gate and Skill-Distill Gate (identical gate checks, identical `TaskCreate` shape, identical `chain_tail_task_id` update rule as described in `build-graph-fast.md` → `### BUILD task graph — fast path` — `remediation_history` is now populated by this escalation cycle, so the Learn-Distill Gate fires) BEFORE creating the Memory Update task, then proceed to memory-finalize
    - Re-verify FAIL → failure_stop_gate fires → stop with BLOCKING: true
    - No further escalation cycles permitted (max one REM-FIX after escalation)
```

**Doc-syncer on escalated path:** SKIP. Fast-path work is unlikely to have doc impact significant enough to warrant it. Do not create a `build-doc-sync` task even after escalation.
