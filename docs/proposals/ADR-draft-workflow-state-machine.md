# ADR (draft): workflow state machines as data

Status: **accepted as ADR-0080** in the ai-craft monorepo (`docs/ai/decisions/0080-craftflow-workflow-state-machine.md`),
signed for David by Claude on David's delegation (2026-11-21). This copy ships with the plugin; the monorepo ADR wins
where they differ. It continues ADR-0073 (policy values out of router prose).

## Context

The router prose describes each route's phases, gates, verdict handling and loop caps. That prose loads on every
workflow: the BUILD start read set is 103,560 bytes and sits at its ceiling. A transition that only prose describes
can't be checked, so drift between the router, the Cursor router and the replay fixtures shows up late. XState v6
treats a machine as data plus a pure transition function, with effects returned as data. Lauren Tan's talk argues
for moving rules into checks. Both point the same way.

## Decision

1. Each route variant is a JSON machine in `plugins/craftflow/config/machines/`:
   - States are phase tokens.
   - Events are agent verdicts and user replies.
   - Gates are `pending_gate` values.
   - Loop caps name `config/policy.json` keys.
2. `scripts/craftflow_machine.py` holds the pure transition function (`next`) and the static soundness checks
   (`validate`).
3. It also has `simulate`, `replay` for `.events.jsonl`, and `fixtures`, which cross-checks the replay fixtures.
4. The change lands in three stages, so behavior never changes without evidence:
   - **Stage A:** model and checks only, with no behavior change. The router does not call the script.
   - **Stage B:** shadow mode. After each agent returns, the SubagentStop audit hook asks the machine which agents it
     expected and logs a `machine_shadow` row (`scripts/craftflow_machine_shadow.py`). It exits after 20 real
     workflows with zero disagreements (`craftflow_machine.py shadow-report --need 20`).
   - **Stage C:** cut over. The router carries out `next`'s effects. `craftflow_gate_check.py` folds into the
     machine's caps, and artifacts gain `schema_version` with a migration on resume. The prose the machine replaced
     is deleted with signed policy-map rows, and the read-set ceilings are lowered.

## Consequences

- One source of truth for both hosts: the Cursor router calls the same script.
- Unsoundness becomes a CI failure, not a production surprise. Examples: an unreachable state, a loop with no cap and
  no human gate, a gate with no resume, or a terminal state that skips memory-finalize.
- Cost: every routing change edits a JSON machine as well as prose until Stage C, and Stage B adds one fail-open
  step to the audit hook per agent return.
- Out of scope: the XState library (it needs Node) and durable timers (Craftflow waits on people, not clocks).

## Signatures

- This ADR and the Stage B exit criterion (20 workflows): signed as ADR-0080 (delegated, 2026-11-21).
- Still needed: each Stage C policy-map deletion row and ceiling change.
