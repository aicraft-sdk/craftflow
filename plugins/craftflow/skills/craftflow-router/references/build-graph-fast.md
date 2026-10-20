# BUILD task graph: fast path

### BUILD task graph — fast path

When `build_mode == "fast_path"`, use this reduced task graph instead of the standard BUILD task graph in `build-graph-standard.md`.

Agents skipped: code-reviewer, silent-failure-hunter, doc-syncer.
Gates surviving: phase_exit_gate, failure_stop_gate, memory_sync_gate.
Gates/rules dropped: 1a-SCOPE rule (no reviewer/hunter findings to scope), doubt_verify_gate.

```text
TaskCreate({
  subject: "CRAFTFLOW component-builder: Execute phase {phase_id}",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-implement\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Execute approved phase (fast path)\n\nFAST-PATH BUILD: Execute ONLY the phase at phase_cursor. Recover objective, inputs, expected artifacts, required checks, checkpoint type, and exit criteria from the approved phase. Stop if blocked, partial, or proof remains incomplete.\n\n## Worktree\nWORKTREE_PATH: {worktree_path | 'main tree'}\nWhen WORKTREE_PATH is a real path (not 'main tree'): all file reads, edits, and writes must use paths rooted at WORKTREE_PATH. Do not modify files outside WORKTREE_PATH during this phase.",
  activeForm: "Building components"
}) -> builder_task_id

TaskCreate({
  subject: "CRAFTFLOW integration-verifier: Verify integration (fast path)",
  description: "wf:{workflow_uuid}\nkind:agent\norigin:router\nphase:build-verify\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Phase exit verification (fast path — no Previous Agent Findings)\n\nFAST-PATH BUILD: Run required checks for the current phase. IMPORTANT: Previous Agent Findings section is OMITTED — no reviewer or hunter ran. Run your own independent scenario coverage. Report whether truths, artifacts, wiring, and phase exit criteria are all satisfied.",
  activeForm: "Verifying integration"
}) -> verifier_task_id
TaskUpdate({ taskId: verifier_task_id, addBlockedBy: [builder_task_id] })
```

Track `chain_tail_task_id` starting as `verifier_task_id` (no doc-sync on fast path). Apply the SAME Learn-Distill Gate and Skill-Distill Gate described in `build-graph-standard.md` — identical gate checks, identical `phase:learn-distill` / `phase:skill-distill` `TaskCreate` shape, identical `chain_tail_task_id` update rule (the shapes and the rule are repeated for this path in `references/verify-dispatch.md → ### Distill gate task shapes`, loaded at verifier PASS) — the only difference is the starting chain tail (`verifier_task_id` here vs `doc_sync_task_id` in standard BUILD). On a clean fast-path pass, `remediation_history` is empty so the Learn-Distill Gate never fires (see `fast-path.md`'s own Learn-Distill Gate note); the Skill-Distill Gate is independent of `remediation_history` and can still fire on a clean fast-path pass if the ledger has an eligible candidate. On an escalated fast path (verifier FAIL → reviewer/hunter/REM-FIX → re-verify), `remediation_history` is populated and `learn-distill` runs exactly like standard BUILD.

**Feature Backlog (advisory):** before creating the Memory Update task below, when `integration-verifier` returned PASS on the final phase, run the complete + VCR calls per `references/harness-self-checks.md § Feature Backlog`.

**Clean-State Check (advisory):** when `worktree_mode != "auto_created"` (no worktree exists — `references/worktree.md` covers the worktree-exists case) and `integration-verifier` returned PASS on the final phase, also run the clean-state check per `references/harness-self-checks.md § Clean-State Check` before creating the Memory Update task below.

```text
TaskCreate({
  subject: "CRAFTFLOW Memory Update: Persist workflow learnings",
  description: "wf:{workflow_uuid}\nkind:memory\norigin:router\nphase:memory-finalize\nplan:{plan_file or 'N/A'}\nscope:N/A\nreason:Persist captured Memory Notes. ROUTER ONLY: execute inline. Follow references/memory-finalize.md § Memory Update task instructions (read it now if not in context). Never spawn Agent() for this task.",
  activeForm: "Persisting workflow learnings"
}) -> memory_task_id
TaskUpdate({ taskId: memory_task_id, addBlockedBy: [chain_tail_task_id] })
```

**Verifier PASS on fast path:** Advance `phase_exit_gate` → run the Learn-Distill Gate and Skill-Distill Gate → proceed to memory-finalize.

**Context boundary (advisory):** after each fast-path `phase_exit_gate` pass, run the check per `references/context-boundary.md` before the next phase or memory-finalize.

**Verifier FAIL on fast path:** Do NOT advance phase cursor. Trigger Fast Path Escalation (see `build-fast-path-escalation.md` → `### Fast Path Escalation`).

