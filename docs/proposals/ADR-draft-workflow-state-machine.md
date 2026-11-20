# ADR (draft): workflow state machines as data

Status: **proposed, unsigned.** This draft belongs in the monorepo's ADR folder with a number. It continues ADR-0073
(policy values out of router prose) and works inside the ADR-0074 growth envelope. David signs it there.

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
   - **Stage B:** shadow mode. After each agent returns, the router also calls `next` and logs whether it agrees.
     It exits after an agreed number of real workflows (suggested 20) with zero disagreements.
   - **Stage C:** cut over. The router carries out `next`'s effects. `craftflow_gate_check.py` folds into the
     machine's caps, and artifacts gain `schema_version` with a migration on resume. The prose the machine replaced
     is deleted with signed policy-map rows, and the read-set ceilings are lowered.

## Consequences

- One source of truth for both hosts: the Cursor router calls the same script.
- Unsoundness becomes a CI failure, not a production surprise. Examples: an unreachable state, a loop with no cap and
  no human gate, a gate with no resume, or a terminal state that skips memory-finalize.
- Cost: every routing change edits a JSON machine as well as prose until Stage C, and Stage B adds one script call
  per agent return.
- Out of scope: the XState library (it needs Node) and durable timers (Craftflow waits on people, not clocks).

## Signatures needed

- This ADR.
- The Stage B exit criterion: the number of workflows.
- Each Stage C policy-map deletion row and ceiling change.
