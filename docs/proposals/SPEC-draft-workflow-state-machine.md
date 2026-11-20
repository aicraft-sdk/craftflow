# SPEC (draft): workflow state machines, Stage A

Status: **draft, unsigned**; goes with the ADR draft in this folder. Stage A ships in this change and makes
no behavior change: nothing in the router, the hooks or the Cursor router calls `craftflow_machine.py` yet.

## Scope

Five machines are in scope:
- `build-standard` and `build-fast` (BUILD)
- `plan` (PLAN)
- `debug` (DEBUG)
- `review` (REVIEW)

TRIAGE, CODEBASE-HEALTH, QA and ORIENT follow in a later Stage A change. The fixture check reports ORIENT's fixture
under `no_machine` instead of failing.

## Rules (each one is a test in `tests/fixtures/test_craftflow_machine.py`)

- FR-001: A machine file lists `id`, `route`, `version`, `initial`, `counters` and `states`. A state's `type` is
  `step`, `choice`, `gate` or `final`. A transition is a target name, or `{target, inc}` when it bumps a counter.
- SC-001 [check: test | phase: A | keep: an unsound graph would ship a router that can hang or skip memory]
  `validate` rejects a machine when any of these hold:
  - a state is unreachable
  - a non-final state has no exit
  - a gate has no resume event
  - a loop passes no gate and no capped counter
  - a counter's cap is not an integer `config/policy.json` key, or `plan:phases`
  - a counter's `on_cap` is not a gate
  - a final state is entered from a state that does not run `finalize_memory`
  - it uses an unknown state or gate name
- SC-002 [check: test | phase: A | keep: the transition function is what Stage B compares the router against] `next`
  returns the target state, the target's effects and the updated counters. At a cap it returns the counter's gate,
  and it refuses an event the state does not handle.
- SC-003 [check: test | phase: A | keep: disagreements are how Stage B finds prose the machine got wrong] `replay`
  reads `agent_completed` rows. For each one, the next `agent_started` phase must be one of the phases of the state
  `next` returns. Every mismatch is a disagreement.
- SC-004 [check: test | phase: A | keep: the fixtures are the existing executable description of routing] `fixtures`:
  every replay fixture's `expected.next_action` is a state or an `actions` entry of a machine for its
  `workflow_type`.
- SC-005 [check: read | phase: A] The shipped machines describe today's prose and don't redesign it. A disagreement
  between a machine and the prose is fixed in whichever one is wrong before Stage B.

## Open points to confirm before Stage B

- DEBUG re-investigation shares `remediation_cycles_max` in the model. Confirm the cap that the prose actually uses.
- The gate names in `KNOWN_GATES` were collected from the artifact policy. Confirm them against every `pending_gate`
  the router writes.
- The parallel review and hunt step is one state (`review-and-hunt`) with two phases. Stage B logs it once both
  agents return.
