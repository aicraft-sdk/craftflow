# Shared doc: approving a staged skill proposal

## Skill-Distill Approval Flow

After `craftflow:skill-author` (the `skill-distill` task) returns `STATUS: COMPLETE`
(meaning it staged a real proposal under `.craftflow/state/project/skill-proposals/{candidate_id}/`),
the router must surface it to the user via `AskUserQuestion` before doing anything else —
before doc-sync, before Memory Update, before any other pending task in the chain. Read
`PROPOSAL.md` from the staged proposal directory to build the evidence-trail summary shown
to the user. Present exactly these four options:

- **Approve** → run:
  ```bash
  python3 {plugin_root}/scripts/craftflow_skill_promote.py --approve {candidate_id} --project-root {project_root} --ledger {state_root}/project/skill-candidates.json --proposals-dir {state_root}/project/skill-proposals
  ```
  On exit 0, the canonical skill now exists at `.claude/skills/{name}/SKILL.md` (synced to
  `.cursor/skills/{name}`) and the ledger candidate is marked `promoted`. On non-zero exit,
  do not advance the workflow — surface the script's stderr to the user and stop.
- **Approve + register in SKILL_HINTS** → run the identical promote command above, PLUS emit
  a `MEMORY_NOTES` entry (routed through the normal memory-finalize persistence path, never a
  direct edit) adding the new skill's id to `patterns.md ## Project SKILL_HINTS` so future
  craftflow-dispatched subagents pick it up automatically.
- **Reject** → the router does NOT call `craftflow_skill_promote.py` for this option. It runs:
  ```bash
  python3 {plugin_root}/scripts/craftflow_skill_ledger.py --reject {candidate_id} --reason "{user-stated or inferred reason}" --ledger {state_root}/project/skill-candidates.json
  ```
  This tombstones the candidate (`status: rejected`, permanent unless `distinct_workflows`
  at least doubles from the value recorded at rejection time — enforced by the ledger's own
  revival logic in `--observe`, not by the router).
- **Defer** → take no script action. Leave the candidate as `status: proposed`/`candidate`
  as-is; it remains gate-eligible and will be re-surfaced the next time `skill-distill` gates
  in for a future workflow.

**Non-matching reply:** If the user's reply does not clearly match one of these four options
(exact label or an unambiguous restatement), re-issue the same `AskUserQuestion` with the same
four options. Never guess, never fall through to a default on an ambiguous reply — only the
JUST_GO carve-out below is permitted to auto-select without an explicit human answer.

**JUST_GO carve-out:** This `AskUserQuestion` gate is treated as a de facto REVERT-class gate
for JUST_GO purposes, overriding the general "auto-default all non-REVERT `AskUserQuestion`
gates to the recommended option" rule in `## 2. Memory Load And Template Validation` — there is
no textual "recommended option" signal for this gate, and the highest-consequence misread
(auto-Approve) would promote an LLM-authored skill into `.claude/skills` with zero human
review. Under `JUST_GO=true` (`AUTO_PROCEED: true`):
- **Never** auto-select **Approve** or **Approve + register in SKILL_HINTS** — both require an
  explicit human answer regardless of `AUTO_PROCEED`.
- Auto-select **Reject** only when a router-derivable reason exists (e.g., the proposal fails
  an objective, router-checkable rubric condition, or the candidate signature duplicates an
  already-`promoted`/`rejected` entry). Log the derived reason in `## Decisions`.
- Otherwise (no router-derivable reject reason), the fail-closed default is **Defer**. Log the
  auto-Defer in `## Decisions`.

If `skill-distill` returns `STATUS: SKIPPED`, this is a passing state — advance directly to
Memory Update, no `AskUserQuestion`. If it returns `STATUS: FAIL`, or does not return at all
(stuck/timeout): `skill-author` is NOT a `kind:remfix` origin (see `## 7. Dispatcher And Agent
Prompt Contract`) — do not create a REM-FIX task and do not block the remaining chain tail
(doc-sync/Memory Update). Append a `skill_distill_failed` event to the workflow event log with
the failure reason if available, leave the candidate's ledger status unchanged (still
`candidate`, flagged for manual retry the next time this gate fires), and advance directly to
Memory Update.

(Host-specific additions: `cursor-router/SKILL.md`'s `## 5a. Skill-Distill Gate` mirrors this
flow in intent — same four options, same two scripts, same JUST_GO-never-auto-Approve
carve-out — but presents them via plain-text chat plus `cursor-wf.json`'s
`pending_skill_approval` field instead of `AskUserQuestion`, since Cursor has no equivalent
tool. See that file's own section for its host-specific mechanics.)

