#!/usr/bin/env python3
"""Prose tests for the P6 gate_check wiring (ADR-0073 BC-6, DD-13, PP-5 companion; router context cut r2).

The loop-cap verdict-handling lines stay verbatim apart from literal -> key substitutions (`policy.<key>` (= <value>)), the
four plan-review blocks stay byte-identical, the Policy table and the documentation-only Loop caps table equal the effective
policy, and each call line defines every action its gate can return. Baseline text comes from `git show <BASELINE_SHA>`.

Run: python3 tests/fixtures/test_router_policy_prose.py
Prints `Results: N passed, M failed`; exits 1 on any failure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[3]
SCRIPTS = PLUGIN_ROOT / "scripts"
FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))

import craftflow_gate_check as gc  # noqa: E402
import craftflow_policy as pol  # noqa: E402

SKILL = "skills/craftflow-router/SKILL.md"
RR = "skills/craftflow-router/references/remediation-and-research.md"
PLAN = "skills/craftflow-router/references/plan-workflow.md"
ART = "skills/craftflow-router/references/workflow-artifact-and-hook-policy.md"

_passes = 0
_errors: List[str] = []
_skips: List[str] = []


def ok(name):
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name, reason):
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name, cond, reason=""):
    if cond:
        ok(name)
    else:
        fail(name, reason or "assertion false")


def current(rel: str) -> str:
    return (PLUGIN_ROOT / rel).read_text(encoding="utf-8")


def baseline_sha() -> str:
    return json.loads((FIXTURES / "router-baseline.lock.json").read_text(encoding="utf-8"))["baseline_sha"]


def baseline(rel: str) -> Optional[str]:
    res = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{baseline_sha()}:{PLUGIN_ROOT.relative_to(REPO_ROOT).as_posix()}/{rel}"],
                         capture_output=True)
    return res.stdout.decode("utf-8") if res.returncode == 0 else None


EFFECTIVE = pol.load_policy(log=False)[0]
SUB_VALUE = re.compile(r"`policy\.(\w+)` \(= (\d+)\)")  # `policy.<key>` (= <v>)
SUB_IN_SPAN = re.compile(r">= policy\.(\w+)` \(= (\d+)\)")  # `circuit_breaker.remfix_count >= policy.<key>` (= <v>)
CALL_LINE = "- First run `python3"


def unsubstitute(text: str) -> str:
    """Reverse the listed literal -> key substitutions: every one carries its `(= <value>)` pin."""
    text = SUB_IN_SPAN.sub(lambda m: f">= {m.group(2)}`", text)
    return SUB_VALUE.sub(lambda m: m.group(2), text)


def segment(text: str, start: str, end: str, from_index: int = 0) -> Optional[str]:
    """Lines from the first line starting with `start` (at or after from_index) up to, not including, the next line starting with `end`."""
    lines = text[from_index:].splitlines(keepends=True)
    first = next((i for i, line in enumerate(lines) if line.startswith(start)), None)
    if first is None:
        return None
    last = next((i for i in range(first + 1, len(lines)) if lines[i].startswith(end)), None)
    return "".join(lines[first:last]) if last is not None else None


def without_call_lines(seg: str) -> str:
    """Drop the inserted call line (it may wrap onto continuation lines up to the next list item or blank line)."""
    out, skipping = [], False
    for line in seg.splitlines(keepends=True):
        if line.startswith(CALL_LINE):
            skipping = True
            continue
        if skipping and line.strip() and not line.startswith(("- ", "**")) and line.startswith("  "):
            continue
        skipping = False
        out.append(line)
    return "".join(out)


SEGMENTS = (
    # (label, file, start, end): P6 may insert a call line above the bullets and substitute listed literals, nothing else
    ("doubt_verdict_bullets_and_counter_skill_md_977_983", SKILL, "**After doubt-verifier returns:**", "**Reliability-gates evidence recording", True),
    ("fix_verify_bullets_and_counter_skill_md_1045_1067", SKILL, "**After doubt-verifier returns** (see", "**Fix-verify cycle counter:**", True),
    ("fix_verify_counter_skill_md_1065_1067", SKILL, "**Fix-verify cycle counter:**", "**Reliability-gates evidence recording", True),
    ("remediation_bullets_rr_27_33", RR, "Before creating a new remediation task:", "### Rule matrix", True),
    ("plan_review_four_blocks_rr_145_177", RR, "When `plan-gap-reviewer` pass 1 returns `PASS`:", "### Investigator continuation", False),
    ("hard_rule_remediation_skill_md_1615", SKILL, "- Never let a remediation loop run more than", "- Only parallelize agents", True),
    ("plan_review_max_plan_workflow_66_stays_literal", PLAN, "   - Maximum fresh-review passes:", "   - ", True),
    ("remfix_breaker_artifact_policy", ART, "    `remfix_count` reaches", "    ", True),
)


def segment_pair(label, rel, start, end, subs):
    base = baseline(rel)
    if base is None:
        return None
    b, c = segment(base, start, end), segment(current(rel), start, end)
    if b is None or c is None:
        return label, None, None
    c = without_call_lines(c)
    return label, b, (unsubstitute(c) if subs else c)


def test_verdict_lines_verbatim_except_substitutions():
    print("\n[verdict_lines_verbatim_except_substitutions]")
    if baseline(SKILL) is None:
        if os.environ.get("CI"):
            fail("verdict_lines_verbatim_except_substitutions", "baseline ref unavailable under CI (fetch-depth 0 required)")
        else:
            _skips.append("SKIP [verdict_lines_verbatim_except_substitutions]: baseline ref unavailable (shallow clone?)")
            print("  SKIP: baseline ref unavailable")
        return
    for label, rel, start, end, subs in SEGMENTS:
        # the one-line segments: the end marker must be searched after the start line, so a single-line segment ends at the next item
        got = segment_pair(label, rel, start, end, subs)
        check(f"verbatim:{label}", got is not None and got[1] is not None and got[1] == got[2],
              f"anchor missing or text differs: baseline={None if got is None else repr(got[1])[:160]} current={None if got is None else repr(got[2])[:160]}")
    # controls: the comparison must catch a changed threshold, a changed verb, and an edit inside the plan-review blocks
    label, rel, start, end, subs = SEGMENTS[0]
    base_seg = segment(baseline(rel), start, end)
    live = without_call_lines(segment(current(rel), start, end))
    check("verbatim:control_changed_threshold_is_caught", "(= 3)" in live and unsubstitute(live.replace("(= 3)", "(= 2)")) != base_seg, "")
    check("verbatim:control_inserted_line_is_caught", unsubstitute(live + "- extra rule\n") != base_seg, "")
    blocks = segment(current(RR), SEGMENTS[4][2], SEGMENTS[4][3])
    check("verbatim:control_block_edit_is_caught", blocks is not None and blocks.replace("pass 2 returns `FINDINGS`", "pass 2 returns `FINDING`") != segment(baseline(RR), SEGMENTS[4][2], SEGMENTS[4][3]), "")


def test_plan_review_cap_pin_follows_the_literal_line():
    print("\n[plan-workflow.md:66 stays literal; the key pin is the next bullet]")
    plan = current(PLAN)
    line = "   - Maximum fresh-review passes: 2.\n   - Cap: `policy.plan_review_passes_max` (= 2).\n"
    check("plan_review_cap_pin_follows_the_literal_line", plan.count(line) == 1, "pin bullet missing or not directly under the literal line")


def test_prose_policy_values_match():
    print("\n[prose_policy_values_match]")
    tokens = []
    for rel in (SKILL, RR, PLAN, ART):
        for m in list(SUB_VALUE.finditer(current(rel))) + list(SUB_IN_SPAN.finditer(current(rel))):
            tokens.append((rel, m.group(1), int(m.group(2))))
    bad = []
    for rel, key, value in tokens:
        try:
            if pol.policy_value(EFFECTIVE, key) != value:
                bad.append((rel, key, value))
        except KeyError:
            bad.append((rel, key, "unknown key"))
    check("prose_policy_values_match", len(tokens) >= 7 and not bad, f"tokens={len(tokens)} bad={bad}")
    expected = {"doubt_cycles_max": 2, "fix_verify_cycles_max": 1, "remediation_cycles_max": 2, "plan_review_passes_max": 1}
    keys = {k for _r, k, _v in tokens}
    check("prose_policy_values_cover_all_four_caps", keys == set(expected), str(keys))
    check("prose_policy_values_control_catches_a_wrong_value", pol.policy_value(EFFECTIVE, "doubt_cycles_max") != 4, "")


def extract_table(text: str, heading: str) -> Optional[str]:
    lines = text.splitlines()
    try:
        i = lines.index(heading)
    except ValueError:
        return None
    rows = []
    for line in lines[i + 1:]:
        if line.startswith("|"):
            rows.append(line)
        elif rows:
            break
    return "\n".join(rows) if rows else None


def test_kernel_policy_table_matches_policy_json():
    print("\n[test_kernel_policy_table_matches_policy_json]")
    kernel = current(SKILL)
    table = extract_table(kernel, "### Policy table")
    printed = subprocess.run([sys.executable, str(SCRIPTS / "craftflow_policy.py"), "--print-table"], capture_output=True, text=True)
    check("kernel_policy_table_matches_policy_json", table is not None and printed.returncode == 0 and table == printed.stdout.rstrip("\n")
          and table == pol.print_table(EFFECTIVE), f"table={table!r} printed={printed.stdout!r}")
    check("kernel_policy_table_is_under_section_14", kernel.index("## 14. Hard Rules") < kernel.index("### Policy table"), "table is above section 14")
    check("kernel_policy_table_control_catches_a_changed_value", table is not None and table.replace("`3`", "`2`", 1) != pol.print_table(EFFECTIVE), "")
    check("kernel_policy_table_mentions_exist", all(f"`{name}`" in kernel for name in (
        "craftflow_pretooluse_guard.py", "craftflow_pretooluse_bash_guard.py", "craftflow_gate_check.py")), "")


def test_loop_caps_table_matches_policy():
    print("\n[loop_caps_table_matches_policy]")
    table = extract_table(current(SKILL), "### Loop caps")
    rows: Dict[str, List[str]] = {}
    for line in (table or "").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0].startswith("`"):
            rows[cells[0].strip("`")] = cells
    caps = ("remediation_cycles_max", "doubt_cycles_max", "fix_verify_cycles_max", "plan_review_passes_max")
    header = (table or "").splitlines()[:1]
    check("loop_caps_table_matches_policy", sorted(rows) == sorted(caps) and all(rows[k][1] == str(pol.policy_value(EFFECTIVE, k)) for k in caps),
          f"rows={rows}")
    check("loop_caps_table_header", header == ["| gate | max | evaluation point | input | at-cap action | operative prose |"], str(header))
    check("loop_caps_table_points_at_operative_prose", all(rows[k][5] for k in rows) and "documentation" in current(SKILL).lower(), "")
    check("loop_caps_table_is_not_a_call_site", "craftflow_gate_check.py" not in (table or ""), "table must stay documentation only")


def command_of(line: str) -> str:
    m = re.search(r"`(python3 \"\$CRAFTFLOW_SCRIPTS/craftflow_gate_check\.py\"[^`]*)`", line)
    return m.group(1) if m else ""


def test_call_lines_define_every_action():
    print("\n[call_lines_define_every_action]")
    kernel, rr = current(SKILL), current(RR)
    kernel_calls = [ln for ln in kernel.splitlines() if CALL_LINE in ln and "craftflow_gate_check.py" in ln]
    rr_pre = [ln for ln in rr.splitlines() if CALL_LINE in ln and "remediation_cycles_max" in ln]
    rr_plan = [ln for ln in rr.splitlines() if "plan_review_passes_max" in ln and "craftflow_gate_check.py" in ln]
    doubt = [ln for ln in kernel_calls if "--gate doubt_cycles_max" in ln]
    fix = [ln for ln in kernel_calls if "--gate fix_verify_cycles_max" in ln]
    check("call_lines_exist_once_each", len(doubt) == 1 and len(fix) == 1 and len(rr_pre) == 1 and len(rr_plan) == 1,
          f"doubt={len(doubt)} fix={len(fix)} pre={len(rr_pre)} plan={len(rr_plan)}")
    if not (doubt and fix and rr_pre and rr_plan):
        return
    common = ("proceed", "loop_cap_check_failed")
    capped = ("`human_checkpoint`", "`reason: cap-reached`")  # only the gates that can return human_checkpoint(cap-reached) name it
    # the doubt and fix-verify gates return only proceed or advance (plus the failure reasons): no dead human_checkpoint clause
    loop_gates = ("`advance`", "no REM-FIX is created", "advance once in total", "--point post_verdict", "--cycle {N}", "advance regardless of verdict")
    dead = ("human_checkpoint", "cap-reached", "no ask block of its own", "CYCLE_COMPLETE")
    check("doubt_call_line_defines_every_action", all(t in doubt[0] for t in common + loop_gates) and not any(t in doubt[0] for t in dead)
          and "never `telemetry.loop_counts`" in doubt[0] and "as the hard-stop bullet does" in doubt[0], doubt[0])
    check("fix_verify_call_line_defines_every_action", all(t in fix[0] for t in common + loop_gates) and not any(t in fix[0] for t in dead)
          and "contract-override check below has been applied" in fix[0] and "`policy.fix_verify_cycles_max` hard stop" in fix[0], fix[0])
    check("remediation_call_line_defines_every_action", all(t in rr_pre[0] for t in common + capped + ("--point pre_create",)), rr_pre[0])
    check("plan_review_call_line_names_pass_and_verdict", all(t in rr_plan[0] for t in common + capped + ("--pass {N}", "--verdict {PASS|FINDINGS}", "--point post_verdict",
          '--wf {workflow_uuid} --project-root "$PROJECT_ROOT"', "increment above already applied", "must not repeat it")), rr_plan[0])
    check("plan_review_call_line_precedes_the_first_block_and_not_between_blocks",
          rr.index("--gate plan_review_passes_max") < rr.index("When `plan-gap-reviewer` pass 1 returns `PASS`:")
          and rr.count("craftflow_gate_check.py") == 2, f"mentions={rr.count('craftflow_gate_check.py')}")
    check("fallback_mode_carries_the_cycle_and_pass_identity",
          "In fallback mode include `## Cycle` N" in kernel and kernel.count("In fallback mode include `## Cycle` N") == 2
          and "N is 1 for the first fresh review and 2 for the one after `re-plan`" in rr, "")
    # every call line must parse with the real CLI once its placeholders are filled in (wiring, not wording)
    fill = {"{N}": "1", "{workflow_uuid}": "wf-x", '"$PROJECT_ROOT"': "/p", "{PASS|FINDINGS}": "PASS"}
    bad = []
    for line in (doubt[0], fix[0], rr_pre[0], rr_plan[0]):
        cmd = command_of(line)
        for k, v in fill.items():
            cmd = cmd.replace(k, v)
        argv = cmd.split()[2:]  # parsed exactly as written: no argv patching
        try:
            gc.parse_args(argv)
        except gc.Usage as exc:
            bad.append((cmd, str(exc)))
    check("call_lines_parse_with_the_real_cli", not bad, str(bad))
    check("gate_check_wired_only_at_the_four_decision_points",
          sum(r.count('python3 "$CRAFTFLOW_SCRIPTS/craftflow_gate_check.py"') for r in (kernel, rr, current(PLAN), current(ART))) == 4, "")


def test_not_wired_remediation_264_and_plan_review_blocks_untouched():
    print("\n[remediation-and-research.md:264 is not wired]")
    rr = current(RR)
    line = next((ln for ln in rr.splitlines() if "run the cycle-cap gate before continuing" in ln), None)
    check("rr_264_stays_verbatim_and_unwired", line is not None and "gate_check" not in line and "craftflow_gate_check" not in line
          and "If count >= 2" in line, str(line))
    base = baseline(RR)
    base_line = next((ln for ln in (base or "").splitlines() if "run the cycle-cap gate before continuing" in ln), None) if base else None
    if base is not None:
        check("rr_264_equals_baseline", line == base_line, f"{line!r} vs {base_line!r}")


def test_dd9_script_resolution_sentence_once_in_shared_preparation():
    print("\n[DD-9 CRAFTFLOW_SCRIPTS]")
    kernel = current(SKILL)
    shared = segment(kernel, "### Shared preparation", "### BUILD preparation") or ""
    check("dd9_sentence_in_shared_preparation", shared.count("`CRAFTFLOW_SCRIPTS`") == 1 and "Base directory" in shared and "$CRAFTFLOW_INSTALL/scripts" in shared
          and kernel.count("**Script directory (DD-9):**") == 1, shared[-400:])


def test_loop_cap_check_failed_ask_line_present():
    print("\n[loop_cap_check_failed_ask_line_present]")
    kernel, art = current(SKILL), current(ART)
    i = kernel.find("**`loop_cap_check_failed`.**")
    para = kernel[i:kernel.find("\n\n", i)] if i >= 0 else ""
    ask = ("Loop-cap check for `{gate}` gave no usable decision (`{reason or \"exit \" + code}`), so I have not acted on the `{verdict}` verdict for cycle/pass {N}. "
           "Reply `retry` to run the check again, `advance` to apply the hard-stop handling, or `proceed` to apply the verdict as written.")
    check("loop_cap_check_failed_ask_line_present", i > kernel.index("## 14. Hard Rules") and ask in para
          and 'persist `pending_gate: "loop_cap_check_failed"`' in para and "apply no verdict, create no task, increment no further counter" in para
          and "On resume with this `pending_gate`, re-ask the same line" in para and "`advance` is not offered" in para, para[:300])
    entry = next((ln for ln in art.splitlines() if ln.startswith("- `loop_cap_check_failed`")), "")
    check("artifact_policy_lists_loop_cap_check_failed_pending_gate", "`SKILL.md` § 14" in entry and "on resume, re-ask it" in entry, entry)
    pg = art.index("- `pending_gate` is required whenever")
    check("pending_gate_entry_follows_the_pending_gate_rule", art.index(entry) > pg and art.index(entry) - pg < 600 if entry else False, "")


def main():
    print("test_router_policy_prose: running")
    for fn in (test_verdict_lines_verbatim_except_substitutions, test_plan_review_cap_pin_follows_the_literal_line, test_prose_policy_values_match, test_kernel_policy_table_matches_policy_json,
               test_loop_caps_table_matches_policy, test_call_lines_define_every_action, test_not_wired_remediation_264_and_plan_review_blocks_untouched,
               test_dd9_script_resolution_sentence_once_in_shared_preparation, test_loop_cap_check_failed_ask_line_present):
        try:
            fn()
        except Exception as exc:  # a crashed test is a failed test, never a silent pass
            fail(fn.__name__, f"raised {exc!r}")
    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed, {len(_skips)} skipped")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
