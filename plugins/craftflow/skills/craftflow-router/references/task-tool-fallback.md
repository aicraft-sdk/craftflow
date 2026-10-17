# Task*-tool fallback

Apply the matching section when `capabilities.task_tools_available == false` or a router-owned native Task* call fails mid-session.

Each section was moved verbatim from the kernel site it names, so read its "above", "below" and "this section" as that site: Resume is `SKILL.md → ## 4. Resume And Hydration`; Parent creation and per-type task graphs are `SKILL.md → ## 6. Workflow Task Graphs`; Dispatcher, Task ID, Doubt-Verify and Fix-Verify are `SKILL.md → ## 7. Dispatcher And Agent Prompt Contract`; Chain loop step 1 and After agent completion are `SKILL.md → ## 12. Chain Execution Loop`.

## Resume fallback

**Task*-tool fallback:** when `capabilities.task_tools_available == false` (or still
`"unknown"` — run `SKILL.md → ## 0a. Task Tool Capability Detection` first in that case, then re-evaluate), skip the `TaskList()` call
above entirely. Identify the active workflow by finding the most recently updated live workflow
artifact under `.craftflow/state/workflows/*.json` (by file modification time), applying the
same never-unscoped-fallback safety rule this section already documents for `TaskList()`-derived
resume — if more than one live artifact is ambiguous for the current conversation, do not resume
without explicit user scoping. Reconstruct runnable phases from that artifact's
`phase_status`/`phase_cursor`/`normalized_phases` fields plus the tail of the matching
`.craftflow/state/workflows/{workflow_uuid}.events.jsonl`, instead of `TaskGet()`/`TaskList()`-
derived task state. The Resume algorithm's steps 3-5 below and the Safety rules apply unchanged
in spirit — substitute "read the workflow artifact + events.jsonl" everywhere those steps say
"read tasks whose descriptions contain `wf:`". When `capabilities.task_tools_available == true`,
this section runs exactly as documented today — no behavior change on that path.

## Parent creation fallback

**Task*-tool fallback:** when `capabilities.task_tools_available == false`, skip step 2
(`TaskCreate()`) entirely — there is no parent orchestration task to create; the artifact is already written by step 1.

### Task*-tool fallback for per-type task graphs and remediation/research task sites

Wherever any `TaskCreate()` call site in `references/build-workflow.md`,
`references/debug-workflow.md`, `references/review-workflow.md`, `references/plan-workflow.md`,
or `references/remediation-and-research.md` would create a child task, and
`capabilities.task_tools_available == false`: skip that `TaskCreate()` call and instead append a
`{phase}` entry with `status: "pending"` directly into the workflow artifact's `phase_status` map
(and into `normalized_phases`, where the referenced block also populates that field) instead of
calling `TaskCreate()`. This is the same fallback mechanism as the other `capabilities.task_tools_available == false` branches in this document (skip
`TaskCreate()`, append the equivalent tracking entry to the workflow artifact instead), applied to
every `TaskCreate()` site in these 5 files — the rule is scoped to "any `TaskCreate()` call site
in these 5 files," never gated on matching a specific heading name or pattern, so it is not
invalidated by any file reorganizing its own subsections later. `blockedBy` relationships
declared at any of these sites become ordering constraints enforced by `phase_cursor` advancement
in `SKILL.md → ## 12. Chain Execution Loop`'s fallback rather than by task-graph blocker fields. Skip every
paired `TaskUpdate({ taskId: <var bound by a skipped TaskCreate()>, addBlockedBy: [...] })` call
in the same block too — do not attempt these; the ordering they would declare is captured
structurally by insertion order in `phase_status`/`normalized_phases` instead. When
`capabilities.task_tools_available == true`, every one of these sites runs exactly
as written today — no behavior change on that path.

## Dispatcher fallback

**Task*-tool fallback (Design Success Criterion 3):** when `capabilities.task_tools_available ==
false`, dispatch every agent via the `Agent` tool directly instead of `Task()`/`TaskCreate()`.
The `## Task Context` scaffold (per `### Prompt scaffold for every agent` in `SKILL.md` below) already embeds
`wf:`/`kind:`/`phase:` metadata directly in the prompt text — this is the metadata a dispatched
agent relies on in place of a `TaskCreate()`-backed task description's structured fields, which do
not exist in this state. Substitute the scaffold's `Task ID:` field with the same no-real-task-id
placeholder defined in `### Prompt scaffold for every agent` in `SKILL.md` below
(`'n/a — task-tool fallback active (capabilities.task_tools_available=false)'`). This mirrors the
pattern already documented as used ad hoc across multiple prior sessions before this rule existed.
When `capabilities.task_tools_available == true`, dispatch continues via
`Task()`/`TaskCreate()` exactly as documented today — no behavior change on that path.

## Task ID substitution

**`Task ID:` runtime substitution rule:** in the `## Task Context` section of every dispatched
agent's prompt (per the shared scaffold's field list), the `Task ID:` field's runtime value
follows this rule: when `capabilities.task_tools_available == false`, substitute the literal
string `'n/a — task-tool fallback active (capabilities.task_tools_available=false)'` for
`{task_id}` instead of a real `TaskCreate()`-minted task id. The dispatched agent's own contract
text (see the 7 agent `.md` files' stop-and-report guard paragraph) tells it not to attempt
`TaskUpdate` in this state. When `capabilities.task_tools_available == true`, `Task ID:` is
populated with the real task id exactly as documented today — no behavior change on that path.

## Doubt-Verify fallback

**Task*-tool fallback:** when `capabilities.task_tools_available == false`, skip the
`TaskCreate()` call above entirely — there is no cycle-tracking task to create. Dispatch
`craftflow:doubt-verifier` directly (same `## Task Context` prompt scaffold as any other agent
dispatch, per `SKILL.md`'s `## 7 -> Prompt scaffold for every agent`), substituting `Task ID:` with the same
no-real-task-id placeholder defined in `### Prompt scaffold for every agent` in `SKILL.md`
(`'n/a — task-tool fallback active (capabilities.task_tools_available=false)'`). Track the doubt
cycle via the workflow artifact's own `telemetry.loop_counts.doubt_verify` counter (already
incremented per cycle today) and the current `phase_cursor`/`phase_status` entry — never via a
real task. The rest of this rule (dispatch conditions, `DOUBT_VERDICT` handling, the 3-cycle hard
stop, reliability-gates evidence recording) applies unchanged; only the task-creation mechanism
changes. When `capabilities.task_tools_available == true`, this rule runs exactly as documented
today — no behavior change on that path.

## Fix-Verify fallback

**Task*-tool fallback:** when `capabilities.task_tools_available == false`, skip the
`TaskCreate()` call above entirely — there is no cycle-tracking task to create. Dispatch
`craftflow:doubt-verifier` directly for the fix-verify cycle (same `## Task Context` prompt
scaffold, same `Task ID:` placeholder substitution as the Doubt-Verify Dispatch Rule above).
Track the fix-verify cycle via `telemetry.loop_counts.fix_verify` and the current
`phase_cursor`/`phase_status` entry instead of a real task. The rest of this rule
(gate-independence from `doubt_verify_gate`, `FIX_VERDICT` handling, the contract-override check,
the stricter-verdict-wins rule, the 3-cycle hard stop, reliability-gates evidence recording)
applies unchanged; only the task-creation mechanism changes. When
`capabilities.task_tools_available == true`, this rule runs exactly as documented today — no
behavior change on that path.

## Chain loop step 1 fallback

**Task*-tool fallback for step 1:** when `capabilities.task_tools_available == false`, step 1
below (`TaskList()`) is skipped. Select runnable phases directly from the workflow artifact's
`phase_status` map and `phase_cursor` (populated per `SKILL.md → ## 6. Workflow Task Graphs`'s fallback
above): a phase is runnable when its `phase_status` entry is `"pending"` or `"in_progress"` and
every phase it is ordered after (per the `blockedBy`-equivalent ordering constraint established
in `SKILL.md → ## 6. Workflow Task Graphs`'s fallback) has `phase_status == "completed"`. Steps 2-7 below apply unchanged in
spirit — substitute "the selected runnable phase" everywhere the fenced block says "task". When
`capabilities.task_tools_available == true`, step 1 runs exactly as documented today — no
behavior change on that path.

## After agent completion fallback

**Task*-tool fallback for steps 1-3:** when `capabilities.task_tools_available == false`: step 1
above (`TaskGet({ taskId })` or `TaskList()`) is skipped — there is no task state to re-check.
Steps 2 and 3's WRITE-vs-READ-ONLY distinction is GENERALIZED to apply identically to every
agent, write-capable or read-only: no agent's `TaskUpdate` call can succeed in this state,
regardless of what its own contract text instructs it to attempt (every write-capable agent's
contract now includes the stop-and-report guard, so it will not attempt one).
The router becomes the sole writer of `phase_status` completion state for every agent in this
state, parsing only the returned Router Contract YAML's `STATUS` field to decide whether to mark
the phase `"completed"`, `"partial"`, or `"blocked"` in the workflow artifact directly —
consistent with the existing Hard Rule "Router is the only orchestration state owner" (see the
new bullet in `SKILL.md → ## 14. Hard Rules` (now `## Hard rule` below), which states this generalization explicitly as
router law). When `capabilities.task_tools_available == true`, steps 1-3 run exactly as
documented today for every agent type — no behavior change on that path.

## Stale true capability

**Stale `true` capability — router's own native Task* call fails mid-session:** the fallback
branches above are all gated on `capabilities.task_tools_available == false`.
This paragraph covers the remaining gap: the flag was correctly recorded `true` at session start
(`## 0a` succeeded), but a router-owned native call — any `TaskCreate()`/`TaskUpdate()`/
`TaskList()` call, wherever it currently occurs across `SKILL.md` or the `references/*.md` files
`## 4`'s Resume And Hydration fallback, `## 6`'s per-type task graph fallback, `## 12`'s Chain
Execution Loop fallback, and `## 7`'s Doubt-Verify/Fix-Verify Dispatch Rules already cover — then
errors unexpectedly later in the same
session (a genuine tool-call failure: tool-not-found, permission error, unhandled exception, or
any error response distinct from a normal empty-result or task-not-found response). When this
happens:
1. Treat it identically to `capabilities.task_tools_available == false` for the remainder of
   THIS session, starting immediately: flip the in-context `capabilities.task_tools_available`
   value from `true` to `false` (do not leave it `true`; do not re-probe with `## 0a` to "double
   check" — the failed call is itself stronger, fresher evidence than a session-start probe).
2. Log the transition: record a `status_history`/event entry in the current workflow artifact
   noting which native call failed, its error, and that `task_tools_available` flipped
   `true -> false` mid-session — this transition must be visible in durable state, not only in
   conversational context that compaction can drop.
3. Fall through immediately to the same artifact-based fallback path every other fallback branch
   in this document already defines for `capabilities.task_tools_available == false` (Resume And
   Hydration, Workflow Task Graphs, Dispatcher scaffold, Chain Execution Loop) for this call and
   every subsequent step this session. Do not retry the same native call a second time in the same
   session on the theory it might succeed next time — a stale `true` that already failed once is
   not trusted again this session, matching the existing `## 0a` policy of never re-probing
   mid-session once a value is recorded.
4. This must not silently stall. A native call failing and the router simply stopping, or
   silently repeating the same failing call, is exactly the gap this rule closes — it is the
   router's own version of Design Success Criterion 2 ("no step silently fails or stalls"),
   extended from sub-agents' `TaskUpdate` calls to the router's own `TaskList()`/
   `TaskCreate()` calls.

Distinguish a genuine call failure (this rule) from a normal empty/negative response: `TaskList()`
returning zero tasks, or `TaskCreate()` succeeding but the newly created task having no immediate
matches, are NOT failures and do not trigger this rule — only an actual tool-call error does. When
`capabilities.task_tools_available` was `false` from the start (`## 0a`'s own detection), this
rule is moot — the other fallback branches already cover that path unconditionally; this rule
exists only for the narrower "was `true`, then broke" transition.

## Hard rule

- When `capabilities.task_tools_available == false`, no agent's `TaskUpdate` call can succeed —
  the router is the sole writer of `phase_status` completion state for every agent, write-capable
  or read-only alike, derived only from each returned Router Contract YAML's `STATUS` field (see
  `SKILL.md → ## 12. Chain Execution Loop`'s fallback). A dispatched agent that discovers `TaskGet`/
  `TaskUpdate` unavailable or failing mid-task — whether `capabilities.task_tools_available` was
  already `false` at dispatch time or the tools disappeared after — must stop its turn after
  emitting its Router Contract YAML block and state plainly in its final output that
  `TaskUpdate` was unavailable; it must never attempt to write directly to the workflow artifact
  JSON, `events.jsonl`, or any `.craftflow/state/*.md` memory file itself, and never self-report
  another agent's role or verdict (e.g., a fabricated verifier pass) to compensate. This rule
  exists to prevent a repeat of `wf-20260720-053825-af03998a`, where a component-builder lacking
  `TaskGet`/`TaskUpdate` self-dispatched a router role and wrote a fabricated
  "integration-verifier PASS" into durable state.
