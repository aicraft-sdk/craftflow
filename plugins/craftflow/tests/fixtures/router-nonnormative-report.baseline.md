# Non-normative units at 0439c33932463d257b881ce50f136ab7661c0a39

Total: 2683 bytes over 23 whole units.

## skills/craftflow-router/SKILL.md (13 units, 730 bytes)

- L59 [0a. task tool capability detection] 20 B: Detection procedure:
- L165 [2. memory load and template validation] 15 B: v10 trust rule:
- L171 [2a. workflow artifact and hook policy] 9 B: Core law:
- L194 [3. task metadata contract] 6 B: Rules:
- L225 [4. resume and hydration] 16 B: Hydration rules:
- L955 [doubt-verify dispatch rule] 28 B: **Doubt-verify task shape:**
- L1024 [fix-verify dispatch rule] 26 B: **Fix-verify task shape:**
- L1094 [task metrics and timing telemetry] 43 B: **Legacy path (Claude Code task metrics):**
- L1099 [task metrics and timing telemetry] 44 B: **Verifier workload telemetry (unchanged):**
- L1115 [read-only contracts] 19 B: Verdict extraction:
- L1139 [read-only contracts] 19 B: Compatibility rule:
- L1147 [write-agent yaml contracts] 16 B: Expected fields:
- L1224 [write-agent contract schema validation (pilot: component-builder, planner)] 469 B: **Known gap (not fixed by this pilot):** `skills/cursor-router/SKILL.md`'s own "Verdict by agent" table still documents `component-builder`/`planner` using the old flat field names, because Cursor dispatches the same `agents/component-builder.md`/`agents/planner.md` files and will therefore receive the new nested output without its own routing doc being updated. This is a known, tracked follow-up, not an oversight — see the schema-validation plan's Risks section.

## skills/_shared/router-protocol.md (3 units, 887 bytes)

- L190 [jev auto-detect consent request contract] 86 B: See `docs/ai/decisions/0038-craftflow-jev-auto-detect-consent.md` for the full design.
- L205 [intent routing] 6 B: Rules:
- L411 [explicit dispatcher (phase-to-agent table)] 795 B: (Host-specific: this table states WHICH agent role handles each phase — the routing intent itself, shared across hosts. HOW each host turns an agent role into something invocable is host-specific and stays in each binding doc: Claude Code resolves these via `Task()`/ `TaskCreate()` against registered subagent types using the `craftflow:` name directly; Cursor's `Task` tool cannot select custom subagent types by name at all, so `cursor-router/SKILL.md`'s `## Agent File Paths` maps each role to a literal `.md` file path instead. See each binding doc's own section for its resolution mechanics. `craftflow-router/SKILL.md` also keeps its own short trailing note on `skill-author` not being a `kind:remfix` origin inline, since it cross-references that file's own `## 6.` section numbering.)

## skills/craftflow-router/references/workflow-artifact-and-hook-policy.md (4 units, 509 bytes)

- L14 [2a. workflow artifact and hook policy] 233 B: - Context-nudge binding: the nudge binds a payload `session_id` to the artifact whose `session_id` matches exactly. After `/clear` or a new session the nudge sees `no_live_candidate` for stamped artifacts until the router restamps.
- L16 [2a. workflow artifact and hook policy] 251 B: - The plan-then-build ambiguity (A4): a plan workflow and the build workflow that follows it can run in the same session, so the chosen artifact's `session_id` equalling the payload `session_id` does not by itself identify which of the two is meant.
- L57 [2a. workflow artifact and hook policy] 6 B: Rules:
- L219 [2a. workflow artifact and hook policy] 19 B: Workflow event log:

## skills/craftflow-router/references/build-workflow.md (0 units, 0 bytes)


## skills/craftflow-router/references/fast-path.md (1 units, 7 bytes)

- L48 [workflow artifact fields] 7 B: Values:

## skills/craftflow-router/references/plan-workflow.md (0 units, 0 bytes)


## skills/craftflow-router/references/debug-workflow.md (2 units, 550 bytes)

- L33 [debug task graph] 308 B: - `references/fast-path.md`'s own Learn-Distill Gate section already states `learn-distill` is dispatched "at the end of BUILD (standard and fast-path) and DEBUG workflows" — DEBUG was already in scope for that gate by design, it simply had no `TaskCreate` to back it, exactly like BUILD's dead-wiring gap.
- L34 [debug task graph] 242 B: - The Skill-Distill Gate's eligibility check (`craftflow_skill_ledger.py --query`) is workflow-type agnostic — it reads a project-wide ledger, not anything DEBUG-specific — so there is no reason to exclude DEBUG from it once BUILD has it.

## skills/craftflow-router/references/review-workflow.md (0 units, 0 bytes)


