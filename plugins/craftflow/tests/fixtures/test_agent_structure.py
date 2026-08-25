#!/usr/bin/env python3
"""
Structural assertions for craftflow agent markdown files.

Checks that agent .md files document required optional-section handling
without needing a live dispatch. Run from the plugin root:

    python3 tests/fixtures/test_agent_structure.py
"""
import os

_FIXTURES_DIR = os.path.dirname(__file__)
PLANNER_MD_PATH = os.path.join(_FIXTURES_DIR, "../../agents/planner.md")
SKILL_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/craftflow-router/SKILL.md")
ROUTER_PROTOCOL_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/_shared/router-protocol.md")
PLAN_WORKFLOW_MD_PATH = os.path.join(
    _FIXTURES_DIR, "../../skills/craftflow-router/references/plan-workflow.md"
)

PASS = 0
FAIL = 0


def check(name: str, actual, expected):
    global PASS, FAIL
    if actual == expected:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected: {expected!r}")
        print(f"    actual:   {actual!r}")
        FAIL += 1


def check_contains(name: str, haystack: str, needle: str):
    global PASS, FAIL
    if needle in haystack:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected to find: {needle!r}")
        FAIL += 1


def check_count(name: str, haystack: str, needle: str, expected_count: int):
    global PASS, FAIL
    actual_count = haystack.count(needle)
    if actual_count == expected_count:
        print(f"  PASS: {name}")
        PASS += 1
    else:
        print(f"  FAIL: {name}")
        print(f"    expected count: {expected_count}")
        print(f"    actual count:   {actual_count}")
        FAIL += 1


with open(PLANNER_MD_PATH, "r", encoding="utf-8") as f:
    planner_text = f.read()

# ---------------------------------------------------------------------------
# test_planner_documents_target_plan_file_override
# ---------------------------------------------------------------------------
print("\n[test_planner_documents_target_plan_file_override]")

check_contains(
    "planner.md contains '## Target Plan File' optional-section heading",
    planner_text,
    "## Target Plan File",
)

check_contains(
    "Process step 14 (Save plan) references Target Plan File override",
    planner_text,
    "Save plan** - use `## Target Plan File`",
)

# ---------------------------------------------------------------------------
# test_skill_md_write_literal_declares_bakeoff_fields (Phase 3 Step 1b/Step 8)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_write_literal_declares_bakeoff_fields]")

with open(SKILL_MD_PATH, "r", encoding="utf-8") as f:
    skill_text = f.read()

_write_literal_line = None
for _line in skill_text.splitlines():
    if "workflow_started" in _line and '\\"task_ids\\"' in _line:
        _write_literal_line = _line
        break

check(
    "found the § 6 Parent Workflow Creation Write() literal line "
    "(contains both workflow_started and task_ids)",
    _write_literal_line is not None,
    True,
)

_WRITE_LITERAL_FIELDS = [
    '\\"plan_file_stem\\":null',
    '\\"bakeoff_n\\":null',
    '\\"bakeoff_n_requested\\":null',
    '\\"bakeoff_models\\":[]',
    '\\"bakeoff_triggered\\":false',
    '\\"bakeoff_all_failed\\":false',
    '\\"bakeoff_candidate_failures\\":[]',
    '\\"plan_bakeoff_candidates\\":{}',
    '\\"plan_bakeoff_judge\\":null',
    '\\"bakeoff\\":[]',
]

for _field in _WRITE_LITERAL_FIELDS:
    check_contains(
        f"Write() literal declares {_field}",
        _write_literal_line or "",
        _field,
    )

# ---------------------------------------------------------------------------
# test_dispatcher_table_lists_bakeoff_phases (Phase 4 Step 1)
# ---------------------------------------------------------------------------
print("\n[test_dispatcher_table_lists_bakeoff_phases]")

with open(ROUTER_PROTOCOL_MD_PATH, "r", encoding="utf-8") as f:
    router_protocol_text = f.read()

check_contains(
    "dispatcher table has a row for the 4 plan-bakeoff-candidate-{model} phases",
    router_protocol_text,
    "| `plan-bakeoff-candidate-opus`, `plan-bakeoff-candidate-sonnet`, "
    "`plan-bakeoff-candidate-haiku`, `plan-bakeoff-candidate-fable` | `craftflow:planner`",
)

check_contains(
    "dispatcher table has a row for plan-bakeoff-judge -> craftflow:plan-bakeoff-judge",
    router_protocol_text,
    "| `plan-bakeoff-judge` | `craftflow:plan-bakeoff-judge` |",
)

# ---------------------------------------------------------------------------
# test_optional_sections_lists_candidates (Phase 4 Step 1b)
# ---------------------------------------------------------------------------
print("\n[test_optional_sections_lists_candidates]")

check_contains(
    "Optional sections list has '## Candidates' scoped to plan-bakeoff-judge, sourced from results.bakeoff[]",
    router_protocol_text,
    "- `## Candidates` only for `plan-bakeoff-judge`, assembled from `results.bakeoff[]`.",
)

# ---------------------------------------------------------------------------
# test_dispatch_time_prompt_assembly_covers_bakeoff (Phase 4 Step 1b)
# ---------------------------------------------------------------------------
print("\n[test_dispatch_time_prompt_assembly_covers_bakeoff]")

with open(PLAN_WORKFLOW_MD_PATH, "r", encoding="utf-8") as f:
    plan_workflow_text = f.read()

check_contains(
    "dispatch-time prompt assembly derives Target Plan File per candidate model from the phase suffix",
    plan_workflow_text,
    'phase matches "plan-bakeoff-candidate-{model}":\n'
    "  assemble ## Target Plan File: docs/plans/{plan_file_stem}-candidate-{model}.md",
)

check_contains(
    "dispatch-time prompt assembly derives judge's Target Plan File as the canonical path",
    plan_workflow_text,
    'phase == "plan-bakeoff-judge":\n'
    "  assemble ## Target Plan File: docs/plans/{plan_file_stem}-plan.md",
)

check_contains(
    "dispatch-time prompt assembly derives judge's Candidates from results.bakeoff[]",
    plan_workflow_text,
    "assemble ## Candidates from results.bakeoff[]",
)

# ---------------------------------------------------------------------------
# test_bakeoff_fanout_block (Phase 4 Step 3/4)
# ---------------------------------------------------------------------------
print("\n[test_bakeoff_fanout_block]")

check_contains(
    "plan-workflow.md has a '### PLAN bake-off fan-out' section heading",
    plan_workflow_text,
    "### PLAN bake-off fan-out",
)

check_contains(
    "N-1 dispatch loop creates one task per model with phase:plan-bakeoff-candidate-{model}",
    plan_workflow_text,
    "phase:plan-bakeoff-candidate-{model}",
)

check_contains(
    "N-1 dispatch loop has no addBlockedBy among candidates (all parallel)",
    plan_workflow_text,
    "# No addBlockedBy — all N-1 are parallel, no ordering among themselves.",
)

check_contains(
    "degraded-tolerance rule marks a failing candidate bakeoff_candidate_failed and excludes it",
    plan_workflow_text,
    "Mark that candidate `bakeoff_candidate_failed`, persist to `bakeoff_candidate_failures` with the\n"
    "reason, exclude it from `results.bakeoff[]`, and continue.",
)

check_contains(
    "router-owned cleanup section is explicitly labeled ROUTER-OWNED CLEANUP",
    plan_workflow_text,
    "**ROUTER-OWNED CLEANUP.**",
)

check_contains(
    "router-owned cleanup Globs the failed candidate's OWN assigned path only",
    plan_workflow_text,
    'Glob(f"docs/plans/{plan_file_stem}-candidate-{model}.md")',
)

check_contains(
    "router-owned cleanup deletes via an exact literal rm path, never a wildcard",
    plan_workflow_text,
    "Bash(f\"rm 'docs/plans/{plan_file_stem}-candidate-{model}.md'\")",
)

check_contains(
    "all-candidates-failed fallback dispatches one fresh planner with model: inherit and no override",
    plan_workflow_text,
    "IF valid_count == 0: abandon the bake-off. Dispatch ONE fresh planner task, model: inherit,",
)

check_contains(
    "all-candidates-failed fallback persists bakeoff_all_failed = true",
    plan_workflow_text,
    "bakeoff_all_failed = true is also persisted.",
)

check_contains(
    "all-candidates-failed fallback constructs Block B with upstream_task_id = the fresh planner's own task id",
    plan_workflow_text,
    "construct Block B (`### PLAN task graph`) NOW,\n"
    "    with upstream_task_id = this fresh planner task's own task id",
)

# ---------------------------------------------------------------------------
# test_judge_dispatch_and_post_judge_validation_blocks (Phase 4 Step 5/6/7)
# ---------------------------------------------------------------------------
print("\n[test_judge_dispatch_and_post_judge_validation_blocks]")

check_contains(
    "plan-workflow.md has a '### PLAN judge dispatch' section heading",
    plan_workflow_text,
    "### PLAN judge dispatch",
)

check_contains(
    "judge dispatch creates a task with phase:plan-bakeoff-judge",
    plan_workflow_text,
    "phase:plan-bakeoff-judge",
)

check_contains(
    "judge dispatch blocks on every valid candidate's task_id",
    plan_workflow_text,
    "TaskUpdate({ taskId: judge_task_id, addBlockedBy: [every valid candidate's task_id] })",
)

check_contains(
    "plan-workflow.md has a '### PLAN post-judge validation' section heading",
    plan_workflow_text,
    "### PLAN post-judge validation",
)

check_contains(
    "post-judge Glob check for leftover candidate files MUST return zero matches",
    plan_workflow_text,
    'Glob(f"docs/plans/{plan_file_stem}-candidate-*.md") — MUST return zero matches.',
)

check_contains(
    "post-judge success constructs Block B with upstream_task_id = judge_task_id",
    plan_workflow_text,
    "<invoke Block B, upstream_task_id = judge_task_id>",
)

check_contains(
    "Task*-tool-fallback note cross-references the existing SKILL.md § 6 generalized rule",
    plan_workflow_text,
    "**Task*-tool-fallback note (cross-reference, not new logic):**",
)

# ---------------------------------------------------------------------------
# test_skill_md_phase_enum_lists_bakeoff_phases (Phase 4 Step 2)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_phase_enum_lists_bakeoff_phases]")

_phase_enum_line = None
for _line in skill_text.splitlines():
    if _line.startswith("phase:{") and "plan-create" in _line:
        _phase_enum_line = _line
        break

check(
    "found the § 3 Task Metadata Contract phase enum line (starts with phase:{, contains plan-create)",
    _phase_enum_line is not None,
    True,
)

_PHASE_ENUM_VALUES = [
    "plan-bakeoff-candidate-opus",
    "plan-bakeoff-candidate-sonnet",
    "plan-bakeoff-candidate-haiku",
    "plan-bakeoff-candidate-fable",
    "plan-bakeoff-judge",
]

for _value in _PHASE_ENUM_VALUES:
    check_contains(
        f"§ 3 phase enum lists {_value}",
        _phase_enum_line or "",
        _value,
    )

# ---------------------------------------------------------------------------
# test_skill_md_chain_loop_has_5b_bakeoff_step (Phase 5 Step 1)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_chain_loop_has_5b_bakeoff_step]")

check_count(
    "§ 12 Chain Execution Loop has exactly one '5b.' bake-off dispatch step",
    skill_text,
    "5b. If N-1 `plan-bakeoff-candidate-*` tasks are all runnable in the same round (PLAN workflow,",
    1,
)

check_contains(
    "step 5b waits for all dispatched candidates before creating the plan-bakeoff-judge task",
    skill_text,
    "Wait for ALL dispatched candidates to return (validly or failed) before creating the\n"
    "     `plan-bakeoff-judge` task.",
)

check_contains(
    "step 5b falls back to sequential dispatch and logs event=parallel_fallback",
    skill_text,
    "fall back to sequential dispatch, one\n"
    "     candidate at a time. Log event=parallel_fallback.",
)

# ---------------------------------------------------------------------------
# test_skill_md_write_agent_table_has_judge_row (Phase 5 Step 2)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_write_agent_table_has_judge_row]")

check_count(
    "write-agent YAML-fields table has exactly one plan-bakeoff-judge row",
    skill_text,
    "| plan-bakeoff-judge | `STATUS`, `SUMMARY`, `PLAN_MODE`, `VERIFICATION_RIGOR`, `CONFIDENCE`, "
    "`PLAN_FILE`, `WINNING_MODEL`, `SYNTHESIZED`, `CANDIDATES_COMPARED`,",
    1,
)

check_contains(
    "write-agent table judge row includes CANDIDATES_COMPARED field",
    skill_text,
    "`CANDIDATES_COMPARED`",
)

# ---------------------------------------------------------------------------
# test_skill_md_contract_overrides_has_judge_row (Phase 5 Step 3)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_contract_overrides_has_judge_row]")

check_count(
    "Contract overrides table has exactly one plan-bakeoff-judge row",
    skill_text,
    "| plan-bakeoff-judge | `STATUS=PLAN_CREATED` or `STATUS=DECISION_RFC_CREATED` requires every "
    "threshold the `planner` override row already requires",
    1,
)

check_contains(
    "contract override judge row requires a router-run Glob confirming zero surviving candidate files",
    skill_text,
    "A router-run `Glob` confirming zero surviving `docs/plans/{plan_file_stem}-candidate-*.md` "
    "files is required",
)

# ---------------------------------------------------------------------------
# test_skill_md_phase_enum_lists_bakeoff_opus_exact_count (Phase 5 Step 4 cross-check)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_phase_enum_lists_bakeoff_opus_exact_count]")

check_count(
    "§ 3 phase enum line contains plan-bakeoff-candidate-opus exactly once",
    _phase_enum_line or "",
    "plan-bakeoff-candidate-opus",
    1,
)

# ---------------------------------------------------------------------------
# test_router_protocol_dispatcher_has_judge_row_exact_count (Phase 5 Step 4 cross-check)
# ---------------------------------------------------------------------------
print("\n[test_router_protocol_dispatcher_has_judge_row_exact_count]")

check_count(
    "_shared/router-protocol.md dispatcher table has exactly one plan-bakeoff-judge row",
    router_protocol_text,
    "| `plan-bakeoff-judge` | `craftflow:plan-bakeoff-judge` |",
    1,
)

# ---------------------------------------------------------------------------
# test_router_protocol_optional_sections_lists_target_plan_file
# (Phase 5 Step 4, fresh-review finding: under_scoped_integrations)
# ---------------------------------------------------------------------------
print("\n[test_router_protocol_optional_sections_lists_target_plan_file]")

check_contains(
    "Optional sections list has '## Target Plan File' bullet for planner/plan-bakeoff-judge",
    router_protocol_text,
    "- `## Target Plan File` for `planner`/`plan-bakeoff-judge` dispatches",
)

# ---------------------------------------------------------------------------
# test_plan_workflow_has_dispatch_time_prompt_assembly_heading
# (Phase 5 Step 4, fresh-review finding: under_scoped_integrations)
# ---------------------------------------------------------------------------
print("\n[test_plan_workflow_has_dispatch_time_prompt_assembly_heading]")

check_contains(
    "plan-workflow.md contains the literal heading '### PLAN dispatch-time prompt assembly'",
    plan_workflow_text,
    "### PLAN dispatch-time prompt assembly",
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    raise SystemExit(1)
else:
    print("PASS")
    raise SystemExit(0)
