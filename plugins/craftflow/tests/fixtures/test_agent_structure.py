#!/usr/bin/env python3
"""
Structural assertions for craftflow agent markdown files.

Checks that agent .md files document required optional-section handling
without needing a live dispatch. Run from the plugin root:

    python3 tests/fixtures/test_agent_structure.py
"""
import json
import os
import subprocess
import sys
import tempfile

_FIXTURES_DIR = os.path.dirname(__file__)
PLANNER_MD_PATH = os.path.join(_FIXTURES_DIR, "../../agents/planner.md")
SKILL_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/craftflow-router/SKILL.md")
ROUTER_PROTOCOL_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/_shared/router-protocol.md")
PLAN_WORKFLOW_MD_PATH = os.path.join(
    _FIXTURES_DIR, "../../skills/craftflow-router/references/plan-workflow.md"
)
PLAN_BAKEOFF_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/craftflow-router/references/plan-bakeoff.md")
# P10 (K retarget): section 3 (the phase enum) moved to workflow-start.md; section 8 (the write-agent table and the Contract
# overrides table) moved to post-agent-validation.md; chain-loop step 5b moved to plan-workflow.md with step 5a (P11: on to plan-bakeoff.md).
WORKFLOW_START_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/craftflow-router/references/workflow-start.md")
POST_AGENT_MD_PATH = os.path.join(_FIXTURES_DIR, "../../skills/craftflow-router/references/post-agent-validation.md")

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

# Since the one-call mint, SKILL.md no longer carries an inline Write() literal for the parent artifact: the
# artifact is written by `craftflow_workflow_id.py --init-artifact`. Assert the defaults on what that
# command actually writes (a real run in a throwaway project dir), not on router prose.
WORKFLOW_ID_SCRIPT = os.path.join(_FIXTURES_DIR, "../../scripts/craftflow_workflow_id.py")


def _init_artifact_defaults():
    """Run `--init-artifact` in a scratch project and return the parsed artifact (None when it was not written)."""
    with tempfile.TemporaryDirectory() as tmp:
        project = os.path.join(tmp, "project")
        os.makedirs(os.path.join(project, ".craftflow"))
        request_file = os.path.join(tmp, "request.txt")
        with open(request_file, "w", encoding="utf-8") as handle:
            handle.write("agent structure artifact defaults")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_SESSION_ID"}
        proc = subprocess.run(
            [sys.executable, WORKFLOW_ID_SCRIPT, "--request-file", request_file, "--branch", "main",
             "--project", project, "--init-artifact", "--workflow-type", "PLAN",
             "--task-tools-available", "true", "--phase", "plan"],
            capture_output=True, text=True, env=env,
        )
        if proc.returncode != 0:
            print(f"    --init-artifact exited {proc.returncode}: {proc.stderr.strip()}")
            return None
        artifact_path = os.path.join(project, ".craftflow", "state", "workflows", proc.stdout.strip() + ".json")
        if not os.path.isfile(artifact_path):
            return None
        with open(artifact_path, "r", encoding="utf-8") as handle:
            return json.load(handle)


_artifact = _init_artifact_defaults()

check(
    "found the parent workflow artifact written by --init-artifact "
    "(first status_history event is workflow_started and task_ids is present)",
    _artifact is not None
    and _artifact["status_history"][0]["event"] == "workflow_started"
    and "task_ids" in _artifact,
    True,
)

_missing = object()


def _artifact_value(*path):
    node = _artifact
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return _missing
        node = node[key]
    return node


# (display name, key path(s) that must all hold the default, default value)
_ARTIFACT_DEFAULT_FIELDS = [
    ("plan_file_stem", [("plan_file_stem",)], None),
    ("bakeoff_n", [("bakeoff_n",)], None),
    ("bakeoff_n_requested", [("bakeoff_n_requested",)], None),
    ("bakeoff_models", [("bakeoff_models",)], []),
    ("bakeoff_triggered", [("bakeoff_triggered",)], False),
    ("bakeoff_all_failed", [("bakeoff_all_failed",)], False),
    ("bakeoff_candidate_failures", [("bakeoff_candidate_failures",)], []),
    ("plan_bakeoff_candidates", [("task_ids", "plan_bakeoff_candidates")], {}),
    ("plan_bakeoff_judge", [("task_ids", "plan_bakeoff_judge"), ("results", "plan_bakeoff_judge")], None),
    ("bakeoff", [("results", "bakeoff")], []),
]

for _field, _paths, _default in _ARTIFACT_DEFAULT_FIELDS:
    check(
        f"--init-artifact artifact declares {_field} = {_default!r}",
        [_artifact_value(*_path) if _artifact is not None else _missing for _path in _paths],
        [_default] * len(_paths),
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
# P11 (K retarget): the bake-off fan-out, judge dispatch, post-judge validation and chain-loop step 5b moved to plan-bakeoff.md.
with open(PLAN_BAKEOFF_MD_PATH, "r", encoding="utf-8") as f:
    bakeoff_text = f.read()

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
    "plan-bakeoff.md has a '### PLAN bake-off fan-out' section heading",
    bakeoff_text,
    "### PLAN bake-off fan-out",
)

check_contains(
    "N-1 dispatch loop creates one task per model with phase:plan-bakeoff-candidate-{model}",
    bakeoff_text,
    "phase:plan-bakeoff-candidate-{model}",
)

check_contains(
    "N-1 dispatch loop has no addBlockedBy among candidates (all parallel)",
    bakeoff_text,
    "# No addBlockedBy — all N-1 are parallel, no ordering among themselves.",
)

check_contains(
    "degraded-tolerance rule marks a failing candidate bakeoff_candidate_failed and excludes it",
    bakeoff_text,
    "Mark that candidate `bakeoff_candidate_failed`, persist to `bakeoff_candidate_failures` with the\n"
    "reason, exclude it from `results.bakeoff[]`, and continue.",
)

check_contains(
    "router-owned cleanup section is explicitly labeled ROUTER-OWNED CLEANUP",
    bakeoff_text,
    "**ROUTER-OWNED CLEANUP.**",
)

check_contains(
    "router-owned cleanup Globs the failed candidate's OWN assigned path only",
    bakeoff_text,
    'Glob(f"docs/plans/{plan_file_stem}-candidate-{model}.md")',
)

check_contains(
    "router-owned cleanup deletes via an exact literal rm path, never a wildcard",
    bakeoff_text,
    "Bash(f\"rm 'docs/plans/{plan_file_stem}-candidate-{model}.md'\")",
)

check_contains(
    "all-candidates-failed fallback dispatches one fresh planner with model: inherit and no override",
    bakeoff_text,
    "IF valid_count == 0: abandon the bake-off. Dispatch ONE fresh planner task, model: inherit,",
)

check_contains(
    "all-candidates-failed fallback persists bakeoff_all_failed = true",
    bakeoff_text,
    "bakeoff_all_failed = true is also persisted.",
)

check_contains(
    "all-candidates-failed fallback constructs Block B with upstream_task_id = the fresh planner's own task id",
    bakeoff_text,
    "construct Block B (`references/plan-workflow.md → ### PLAN task graph`) NOW,\n"
    "    with upstream_task_id = this fresh planner task's own task id",
)

# ---------------------------------------------------------------------------
# test_judge_dispatch_and_post_judge_validation_blocks (Phase 4 Step 5/6/7)
# ---------------------------------------------------------------------------
print("\n[test_judge_dispatch_and_post_judge_validation_blocks]")

check_contains(
    "plan-bakeoff.md has a '### PLAN judge dispatch' section heading",
    bakeoff_text,
    "### PLAN judge dispatch",
)

check_contains(
    "judge dispatch creates a task with phase:plan-bakeoff-judge",
    bakeoff_text,
    "phase:plan-bakeoff-judge",
)

check_contains(
    "judge dispatch blocks on every valid candidate's task_id",
    bakeoff_text,
    "TaskUpdate({ taskId: judge_task_id, addBlockedBy: [every valid candidate's task_id] })",
)

check_contains(
    "plan-bakeoff.md has a '### PLAN post-judge validation' section heading",
    bakeoff_text,
    "### PLAN post-judge validation",
)

check_contains(
    "post-judge Glob check for leftover candidate files MUST return zero matches",
    bakeoff_text,
    'Glob(f"docs/plans/{plan_file_stem}-candidate-*.md") — MUST return zero matches.',
)

check_contains(
    "post-judge success constructs Block B with upstream_task_id = judge_task_id",
    bakeoff_text,
    "<invoke Block B, upstream_task_id = judge_task_id>",
)

check_contains(
    "Task*-tool-fallback note cross-references the existing SKILL.md § 6 generalized rule",
    bakeoff_text,
    "**Task*-tool-fallback note (cross-reference, not new logic):**",
)

# ---------------------------------------------------------------------------
# test_skill_md_phase_enum_lists_bakeoff_phases (Phase 4 Step 2)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_phase_enum_lists_bakeoff_phases]")

with open(WORKFLOW_START_MD_PATH, "r", encoding="utf-8") as f:
    workflow_start_text = f.read()
with open(POST_AGENT_MD_PATH, "r", encoding="utf-8") as f:
    post_agent_text = f.read()

_phase_enum_line = None
for _line in workflow_start_text.splitlines():
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
    bakeoff_text,
    "5b. If N-1 `plan-bakeoff-candidate-*` tasks are all runnable in the same round (PLAN workflow,",
    1,
)

check_contains(
    "step 5b waits for all dispatched candidates before creating the plan-bakeoff-judge task",
    bakeoff_text,
    "Wait for ALL dispatched candidates to return (validly or failed) before creating the\n"
    "     `plan-bakeoff-judge` task.",
)

check_contains(
    "step 5b falls back to sequential dispatch and logs event=parallel_fallback",
    bakeoff_text,
    "fall back to sequential dispatch, one\n"
    "     candidate at a time. Log `event=parallel_fallback` in the event log.",
)

check_contains(
    "step 5b cross-references PLAN dispatch-time prompt assembly and PLAN bake-off fan-out"
    " (not the incorrect PLAN task graph section)",
    bakeoff_text,
    "see `references/plan-workflow.md → ### PLAN\n"
    "     dispatch-time prompt assembly` for the exact per-candidate model/target-file construction\n"
    "     formula and `### PLAN bake-off fan-out` for the actual per-candidate dispatch loop)",
)

# ---------------------------------------------------------------------------
# test_skill_md_write_agent_table_has_judge_row (Phase 5 Step 2)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_write_agent_table_has_judge_row]")

_JUDGE_ROW_FULL = (
    "| plan-bakeoff-judge | `STATUS`, `SUMMARY`, `PLAN_MODE`, `VERIFICATION_RIGOR`, `CONFIDENCE`, "
    "`PLAN_FILE`, `WINNING_MODEL`, `SYNTHESIZED`, `CANDIDATES_COMPARED`, `PHASES`, "
    "`RISKS_IDENTIFIED`, `SCENARIOS`, `OPEN_DECISIONS`, `DIFFERENCES_FROM_AGREEMENT`, "
    "`ALTERNATIVES`, `DRAWBACKS`, `PROVABLE_PROPERTIES`, `BLOCKING`, `REMEDIATION_NEEDED`, "
    "`REQUIRES_REMEDIATION`, `REMEDIATION_REASON`, `GATE_PASSED`, `MEMORY_NOTES` |"
)

check_count(
    "write-agent YAML-fields table has exactly one plan-bakeoff-judge row matching the full,"
    " corrected field list (no trailing-field truncation)",
    post_agent_text,
    _JUDGE_ROW_FULL,
    1,
)

check_contains(
    "write-agent table judge row includes CANDIDATES_COMPARED field",
    post_agent_text,
    "`CANDIDATES_COMPARED`",
)

_JUDGE_ROW_LINE = None
for _line in post_agent_text.splitlines():
    if _line.startswith("| plan-bakeoff-judge | `STATUS`, `SUMMARY`, `PLAN_MODE`"):
        _JUDGE_ROW_LINE = _line
        break

check(
    "found the write-agent table plan-bakeoff-judge row as a single line",
    _JUDGE_ROW_LINE is not None,
    True,
)

for _wrong_field in ("`ASSUMPTIONS`", "`DECISIONS`", "`NEXT_ACTION`"):
    check(
        f"write-agent table plan-bakeoff-judge row does NOT list {_wrong_field}"
        " (agent's actual Router Contract YAML never emits it — copy-paste artifact from"
        " the planner row regression guard)",
        _wrong_field in (_JUDGE_ROW_LINE or ""),
        False,
    )

# ---------------------------------------------------------------------------
# test_skill_md_contract_overrides_has_judge_row (Phase 5 Step 3)
# ---------------------------------------------------------------------------
print("\n[test_skill_md_contract_overrides_has_judge_row]")

check_count(
    "Contract overrides table has exactly one plan-bakeoff-judge row",
    post_agent_text,
    "| plan-bakeoff-judge | `STATUS=PLAN_CREATED` or `STATUS=DECISION_RFC_CREATED` requires every "
    "threshold the `planner` override row already requires",
    1,
)

check_contains(
    "contract override judge row requires a router-run Glob confirming zero surviving candidate files",
    post_agent_text,
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
# Default agent model pins (ADR-0049)
# ---------------------------------------------------------------------------
AGENTS_DIR = os.path.join(_FIXTURES_DIR, "../../agents")

EXPECTED_AGENT_MODELS = {
    "planner": "opus",
    "plan-gap-reviewer": "opus",
    "plan-bakeoff-judge": "opus",
    "bug-investigator": "opus",
    "doubt-verifier": "opus",
    "component-builder": "sonnet",
    "code-reviewer": "sonnet",
    "silent-failure-hunter": "sonnet",
    "integration-verifier": "sonnet",
    "web-researcher": "sonnet",
    "github-researcher": "sonnet",
    "doc-syncer": "sonnet",
    "learn-distiller": "sonnet",
    "skill-author": "sonnet",
}


def read_frontmatter_model(path: str):
    """Return the list of `model:` values found in the frontmatter block
    (the text between the first two `---` lines)."""
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    delimiters = [i for i, line in enumerate(lines) if line.strip() == "---"]
    if len(delimiters) < 2 or delimiters[0] != 0:
        return []
    block = lines[delimiters[0] + 1 : delimiters[1]]
    return [line.split(":", 1)[1].strip() for line in block if line.startswith("model:")]


_agent_files = sorted(f for f in os.listdir(AGENTS_DIR) if f.endswith(".md"))

print("\n[test_every_agent_declares_expected_model]")
for _name, _expected in EXPECTED_AGENT_MODELS.items():
    _models = read_frontmatter_model(os.path.join(AGENTS_DIR, f"{_name}.md"))
    check(f"agents/{_name}.md frontmatter model is {_expected!r}", _models, [_expected])

print("\n[test_expected_model_map_covers_every_agent_file]")
check(
    "set of agents/*.md stems equals EXPECTED_AGENT_MODELS keys",
    sorted(f[: -len(".md")] for f in _agent_files),
    sorted(EXPECTED_AGENT_MODELS),
)

print("\n[test_no_agent_uses_haiku_or_inherit]")
for _f in _agent_files:
    _models = read_frontmatter_model(os.path.join(AGENTS_DIR, _f))
    check(
        f"agents/{_f} does not use haiku or inherit (ADR-0047/0049)",
        [m for m in _models if m in ("haiku", "inherit")],
        [],
    )

print("\n[test_agent_frontmatter_has_single_model_line]")
for _f in _agent_files:
    _models = read_frontmatter_model(os.path.join(AGENTS_DIR, _f))
    check(f"agents/{_f} has exactly one frontmatter model line", len(_models), 1)

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
