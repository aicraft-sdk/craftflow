# Two-axis review: router change for signature (P5)

Status: **draft, not applied.** The code-reviewer side ships now (`agents/code-reviewer.md` § Review Axis). With no
`REVIEW_AXIS` in its prompt, the reviewer runs both axes in one dispatch, Spec first, then Standards, and reports
them in two sections. That changes the reviewer's output but not the router's behavior. The router edits below
rewrite inventoried router units. They also grow a start file whose read-set ceiling has no headroom today. Both
need a person: signed policy-map rows (`Name (YYYY-MM-DD)`) and a ceiling envelope. An agent cannot sign either, so
they are not in this PR.

## What changes when it is applied

The standard BUILD chain becomes spec reviewer ‖ standards reviewer ‖ silent-failure-hunter, then the verifier.
REVIEW runs both axes as two isolated dispatches. Fast path is unchanged: it still skips review.

1. `plugins/craftflow/skills/craftflow-router/references/build-graph-standard.md`: replace the single
   `CRAFTFLOW code-reviewer: Review implementation` task with two tasks. Both use `phase:build-review` and are
   blocked by `builder_task_id`. Their subjects are `... code-reviewer: Review spec axis` and
   `... code-reviewer: Review standards axis`, and their descriptions end in `REVIEW_AXIS: spec` and
   `REVIEW_AXIS: standards`. The verifier's `addBlockedBy` lists both reviewer ids and the hunter.
2. `plugins/craftflow/skills/craftflow-router/references/review-workflow.md`: the same split for `phase:review-audit`.
   Memory Update blocks on both reviewer tasks.
3. `plugins/craftflow/skills/craftflow-router/SKILL.md` step 5: "both ready" becomes "the two reviewer tasks and
   silent-failure-hunter are ready". All three are invoked in one message. The sequential fallback order is spec,
   standards, then hunter.
4. `plugins/craftflow/skills/craftflow-router/references/post-agent-validation.md`: the merged findings summary keeps
   one section per axis, each with its own worst issue, and does not rerank them. BLOCKING is true when either axis
   has a critical finding. The existing code-reviewer rows apply to each dispatch.
5. `plugins/craftflow/skills/cursor-router/SKILL.md`: the same chain at lines 677 to 679, the parallel-dispatch rule at
   line 1167, and the Previous Agent Findings block, which gets a section per axis.
6. `plugins/craftflow/skills/_shared/router-protocol.md` row 8 and `references/fast-path.md`: the `build-review` row
   names both axes.

## What a person has to sign

- **`docs/router-policy-map.md`, Rewrites table:** one row for each rewritten unit in items 1 to 6. Each row carries
  the baseline id that `craftflow_rule_inventory.py --diff` reports after the edit, and `Reviewed by` holds the
  signature. The diff fails while any row is unsigned.
- **`plugins/craftflow/tests/fixtures/router-budget.json`:** `readset.BUILD` and `readset.REVIEW` are at their
  ceilings (103,560 and 77,126 bytes; `craftflow_router_readset.py --workflow BUILD` shows zero headroom). Item 1 adds
  about 520 bytes to a BUILD start file, and item 2 adds about 480 to a REVIEW start file. There are two ways to fit:
  - **Byte-neutral (preferred):** cut the historical paragraph that opens `#### Learn-Distill Gate` in
    `build-graph-standard.md` (it starts "Fixes the pre-existing dead-wiring gap", about 330 bytes) and shorten the two
    reviewer descriptions to fit. That cut needs its own signed row.
  - **Raise:** add a `ceiling_raises` entry under a signed envelope ADR, the way ADR-0074 was used.

## Tests to add with the router change

- Write a replay fixture first: a standard BUILD that expects two `phase:build-review` tasks with distinct
  `REVIEW_AXIS` values. It fails until item 1 lands.
- A Spec-fail and Standards-pass fixture proves the merged summary stays blocking (one axis can't hide the other).
- Re-run `craftflow_rule_inventory.py --diff` with the signed rows, and run the full local gate.
- Expect the full chain to cost one more sonnet dispatch. Measure it on the runtime benchmark.
