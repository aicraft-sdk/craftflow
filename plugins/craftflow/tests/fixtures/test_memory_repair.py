#!/usr/bin/env python3
"""
Fixture-based unit tests for craftflow_memory_repair.py

Uses small SYNTHETIC fixtures that reproduce the corruption pattern
(demoted anchors, stranded bullets below "## Last Updated", chained
confidence suffixes, legacy [cc10x-internal] orphans, duplicate
"- Plan:" lines) — not the large real-shaped fixture copies, which are
exercised separately as scenario/CLI evidence.

Run from the plugin root:
    python3 tests/fixtures/test_memory_repair.py
"""
import subprocess
import sys
import os

# Allow importing scripts from scripts/ directory
SCRIPTS_DIR = os.path.join(os.path.dirname(__file__), "../../scripts")
sys.path.insert(0, SCRIPTS_DIR)

from craftflow_memory_repair import (
    restore_demoted_anchors,
    move_stranded_bullets,
    is_skill_id_bullet,
    relocate_misfiled_anchor_bullets,
    collapse_confidence_suffixes,
    cap_section_bullets,
    remove_legacy_internal_orphans,
    dedupe_plan_lines,
    repair_patterns_md,
    repair_progress_md,
    repair_active_context_md,
    _unique_backup_dir,
)

SCRIPT_PATH = os.path.join(SCRIPTS_DIR, "craftflow_memory_repair.py")

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


# --- restore_demoted_anchors ---
print("\n[restore_demoted_anchors]")
lines = ["## Common Gotchas", "- a gotcha", "", "API Patterns", "- an api note", "## Last Updated"]
result = restore_demoted_anchors(lines)
check(
    "prepends ## to exact bare-line match only",
    result,
    ["## Common Gotchas", "- a gotcha", "", "## API Patterns", "- an api note", "## Last Updated"],
)

lines2 = ["## Common Gotchas", "- mentions API Patterns in prose", "Dependencies"]
result2 = restore_demoted_anchors(lines2)
check(
    "does not touch lines that merely CONTAIN an anchor string",
    result2,
    ["## Common Gotchas", "- mentions API Patterns in prose", "## Dependencies"],
)

check(
    "does not double-prefix an already-restored heading",
    restore_demoted_anchors(["## API Patterns"]),
    ["## API Patterns"],
)

# --- move_stranded_bullets ---
print("\n[move_stranded_bullets]")
lines3 = [
    "## Common Gotchas",
    "- old gotcha 1",
    "- old gotcha 2",
    "## Last Updated",
    "2026-07-30 changelog line",
    "- stranded 1",
    "- stranded 2",
]
new_lines, moved = move_stranded_bullets(lines3, "Common Gotchas")
check(
    "moves stranded bullets to end of target section, preserves order",
    new_lines,
    [
        "## Common Gotchas",
        "- old gotcha 1",
        "- old gotcha 2",
        "- stranded 1",
        "- stranded 2",
        "## Last Updated",
        "2026-07-30 changelog line",
    ],
)
check("returns the moved bullets in original relative order", moved, ["- stranded 1", "- stranded 2"])

lines4 = ["## Completed", "- done 1", "## Last Updated", "date line", "no bullets here"]
new_lines4, moved4 = move_stranded_bullets(lines4, "Completed")
check("no-op (structurally) when nothing is stranded", new_lines4, lines4)
check("empty moved list when nothing stranded", moved4, [])

lines5 = [
    "## A",
    "- a1",
    "## B",
    "- b1",
    "## Last Updated",
    "- stranded for a",
]
new_lines5, moved5 = move_stranded_bullets(lines5, "A")
check(
    "uses the LAST '## Last Updated' occurrence and reattaches to the named target section only",
    new_lines5,
    ["## A", "- a1", "- stranded for a", "## B", "- b1", "## Last Updated"],
)

# --- is_skill_id_bullet ---
print("\n[is_skill_id_bullet]")
check("accepts a namespaced skill id (no spaces)", is_skill_id_bullet("- craftflow:some-skill-name"), True)
check("accepts a plugin:skill id", is_skill_id_bullet("- plugin:skill"), True)
check("accepts the literal template placeholder", is_skill_id_bullet("- [full-skill-id]"), True)
check(
    "rejects a full gotcha-style paragraph (contains spaces)",
    is_skill_id_bullet(
        "- craftflow fast path routing (wf-...): BUILD default is now fast path"
    ),
    False,
)
check("rejects a non-bullet line", is_skill_id_bullet("not a bullet at all"), False)

# --- relocate_misfiled_anchor_bullets ---
print("\n[relocate_misfiled_anchor_bullets]")
lines11 = [
    "## API Patterns",
    "- craftflow:api-skill",
    "- a full paragraph gotcha with spaces stuck under the wrong anchor",
    "## Error Handling",
    "- plugin:err-skill",
    "## Common Gotchas",
    "- existing gotcha",
]
new_lines11, relocated11 = relocate_misfiled_anchor_bullets(
    lines11, ["API Patterns", "Error Handling"], "Common Gotchas"
)
check(
    "leaves skill-id-shaped bullets in place, relocates non-skill-id bullets to Common Gotchas",
    new_lines11,
    [
        "## API Patterns",
        "- craftflow:api-skill",
        "## Error Handling",
        "- plugin:err-skill",
        "## Common Gotchas",
        "- existing gotcha",
        "- a full paragraph gotcha with spaces stuck under the wrong anchor",
    ],
)
check(
    "reports the relocated bullets in document order",
    relocated11,
    ["- a full paragraph gotcha with spaces stuck under the wrong anchor"],
)

lines12 = ["## Project SKILL_HINTS", "- craftflow:kept-skill", "## Common Gotchas", "- old"]
new_lines12, relocated12 = relocate_misfiled_anchor_bullets(lines12, ["Project SKILL_HINTS"], "Common Gotchas")
check("no-op when every bullet is genuinely skill-id-shaped", new_lines12, lines12)
check("nothing relocated when nothing is misfiled", relocated12, [])

# --- collapse_confidence_suffixes ---
print("\n[collapse_confidence_suffixes]")
check(
    "collapses two chained suffixes to the last one",
    collapse_confidence_suffixes("- insight (conf: 0.9) (conf: 0.8)"),
    "- insight (conf: 0.8)",
)
check(
    "collapses three chained suffixes (generic, not just 2) to the last one",
    collapse_confidence_suffixes("- insight (conf: 0.5) (conf: 0.7) (conf: 0.9)"),
    "- insight (conf: 0.9)",
)
check(
    "no-op on a single suffix",
    collapse_confidence_suffixes("- insight (conf: 0.9)"),
    "- insight (conf: 0.9)",
)
check(
    "no-op on a bullet with no suffix",
    collapse_confidence_suffixes("- plain bullet"),
    "- plain bullet",
)

# --- cap_section_bullets ---
print("\n[cap_section_bullets]")
lines6 = ["## Completed", "- one (conf: 0.8)", "- two (conf: 0.8)", "- three (conf: 0.8)", "## Verification", "- kept"]
new_lines6, evicted6 = cap_section_bullets(lines6, "Completed", 2)
check(
    "evicts oldest-first via apply_cap, leaves other sections untouched",
    new_lines6,
    ["## Completed", "- two (conf: 0.8)", "- three (conf: 0.8)", "## Verification", "- kept"],
)
check("reports number evicted", evicted6, 1)

lines7 = ["## Completed", "- one", "- two", "## Verification"]
new_lines7, evicted7 = cap_section_bullets(lines7, "Completed", 5)
check("no-op when under cap", new_lines7, lines7)
check("zero evicted when under cap", evicted7, 0)

# --- remove_legacy_internal_orphans ---
print("\n[remove_legacy_internal_orphans]")
lines8 = [
    "## References",
    "- [craftflow-internal] memory_task_id: 3 wf:wf-current",
    "- [cc10x-internal] memory_task_id: 6 wf:wf-old",
    "- Plan: `docs/plans/x.md`",
]
new_lines8, removed8 = remove_legacy_internal_orphans(lines8)
check(
    "deletes only [cc10x-internal] lines, keeps [craftflow-internal] and other lines",
    new_lines8,
    ["## References", "- [craftflow-internal] memory_task_id: 3 wf:wf-current", "- Plan: `docs/plans/x.md`"],
)
check("reports removed orphan lines", removed8, ["- [cc10x-internal] memory_task_id: 6 wf:wf-old"])

# --- dedupe_plan_lines ---
print("\n[dedupe_plan_lines]")
lines9 = [
    "## References",
    "- Plan: `docs/plans/a.md`",
    "- Plan: `docs/plans/b.md`",
    "- Plan: `docs/plans/a.md`",
    "## Blockers",
]
new_lines9, removed9 = dedupe_plan_lines(lines9)
check(
    "keeps first occurrence of a duplicate path, drops the rest",
    new_lines9,
    ["## References", "- Plan: `docs/plans/a.md`", "- Plan: `docs/plans/b.md`", "## Blockers"],
)
check("reports removed duplicate lines", removed9, ["- Plan: `docs/plans/a.md`"])

lines10 = ["## References", "- Plan: `docs/plans/a.md`", "- Plan: `docs/plans/b.md`", "## Blockers"]
new_lines10, removed10 = dedupe_plan_lines(lines10)
check("does not touch genuinely different paths", new_lines10, lines10)
check("nothing removed when all paths distinct", removed10, [])

# --- repair_patterns_md: full synthetic file, zero data loss ---
print("\n[repair_patterns_md: end-to-end synthetic fixture]")
synthetic_patterns = (
    "## User Standards\n"
    "- TDD always\n"
    "## Common Gotchas\n"
    "- old gotcha 1 (conf: 0.8)\n"
    "- old gotcha 2 (conf: 0.9) (conf: 0.7)\n"
    "\n"
    "API Patterns\n"
    "Error Handling\n"
    "\n"
    "Dependencies\n"
    "\n"
    "Project SKILL_HINTS\n"
    "- craftflow:kept-skill\n"
    "- a misfiled full paragraph gotcha with real prose and spaces, found under SKILL_HINTS\n"
    "## Last Updated\n"
    "2026-07-30 changelog\n"
    "- stranded 1 (conf: 0.9)\n"
    "- stranded 2\n"
)
repaired_text, report = repair_patterns_md(synthetic_patterns)
check(
    "restored anchors are real ## headings in the repaired output",
    all(h in repaired_text for h in ["## API Patterns", "## Error Handling", "## Dependencies", "## Project SKILL_HINTS"]),
    True,
)
check("no bullet lines remain after the (last) ## Last Updated heading", (
    "- " not in repaired_text.split("## Last Updated\n", 1)[1]
), True)
check(
    "stranded bullets landed in Common Gotchas with confidence suffix collapsed and preserved",
    "- old gotcha 2 (conf: 0.7)" in repaired_text
    and "- stranded 1 (conf: 0.9)" in repaired_text
    and "- stranded 2" in repaired_text,
    True,
)
skill_hints_body = repaired_text.split("## Project SKILL_HINTS\n", 1)[1].split("## Last Updated\n", 1)[0]
check(
    "genuine skill-id bullet stays under Project SKILL_HINTS",
    "- craftflow:kept-skill" in skill_hints_body,
    True,
)
check(
    "misfiled paragraph bullet is NOT left under Project SKILL_HINTS",
    "misfiled full paragraph gotcha" in skill_hints_body,
    False,
)
check(
    "misfiled paragraph bullet relocated into Common Gotchas instead",
    "- a misfiled full paragraph gotcha with real prose and spaces, found under SKILL_HINTS" in repaired_text,
    True,
)
check("zero data loss check passes", report["zero_data_loss_ok"], True)
check("nothing evicted (well under the 60 cap)", report["evicted_count"], 0)
check("2 bullets moved from below Last Updated", report["moved_count"], 2)
check("1 bullet relocated out of a misfiled anchor section", report["relocated_count"], 1)

# --- repair_progress_md: full synthetic file with cap eviction ---
print("\n[repair_progress_md: end-to-end synthetic fixture with cap eviction]")
completed_bullets = "\n".join(f"- [x] task {i} (conf: 0.8)" for i in range(1, 61))
synthetic_progress = (
    "## Tasks\n"
    "- [ ] todo\n"
    "## Completed\n"
    f"{completed_bullets}\n"
    "## Verification\n"
    "- [x] verified\n"
    "## Last Updated\n"
    "2026-07-30 changelog\n"
    "- [x] stranded task 61 (conf: 0.9)\n"
)
repaired_progress, progress_report = repair_progress_md(synthetic_progress)
check("zero data loss check accounts for cap eviction", progress_report["zero_data_loss_ok"], True)
check("exactly 1 bullet evicted (61 bullets, cap 60)", progress_report["evicted_count"], 1)
check(
    "oldest completed bullet (task 1) was evicted, newest stranded bullet survived",
    "task 1 (conf: 0.8)" not in repaired_progress and "stranded task 61" in repaired_progress,
    True,
)
check(
    "unrelated Verification section bullet untouched",
    "- [x] verified" in repaired_progress,
    True,
)

# --- repair_active_context_md: orphans + dedupe together ---
print("\n[repair_active_context_md: end-to-end synthetic fixture]")
synthetic_active_context = (
    "## Current Focus\n"
    "focus text\n"
    "## References\n"
    "- Plan: `docs/plans/a.md`\n"
    "- [craftflow-internal] memory_task_id: 9 wf:wf-current\n"
    "- [cc10x-internal] memory_task_id: 6 wf:wf-old\n"
    "- Plan: `docs/plans/a.md`\n"
    "## Blockers\n"
    "- None\n"
)
repaired_ac, ac_report = repair_active_context_md(synthetic_active_context)
check(
    "cc10x-internal orphan removed, craftflow-internal kept, duplicate Plan line dropped",
    repaired_ac,
    (
        "## Current Focus\n"
        "focus text\n"
        "## References\n"
        "- Plan: `docs/plans/a.md`\n"
        "- [craftflow-internal] memory_task_id: 9 wf:wf-current\n"
        "## Blockers\n"
        "- None\n"
    ),
)
check("report accounts for both removed lines", ac_report["zero_data_loss_ok"], True)
check("1 orphan removed", ac_report["orphans_removed"], 1)
check("1 duplicate removed", ac_report["duplicates_removed"], 1)

# --- CLI: --target-dir is a required argument ---
print("\n[CLI: --target-dir required]")
# Run in an isolated scratch cwd with CLAUDE_PROJECT_DIR unset so that, if
# this regresses to a default/optional --target-dir, the failure mode is
# obvious rather than silently touching a real state directory.
_scratch_cwd = os.path.join(os.path.dirname(__file__), ".tmp_cli_required_check")
os.makedirs(_scratch_cwd, exist_ok=True)
_env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
try:
    proc = subprocess.run(
        [sys.executable, SCRIPT_PATH],
        cwd=_scratch_cwd,
        env=_env,
        capture_output=True,
        text=True,
    )
    check("missing --target-dir exits 2 (argparse required-arg contract)", proc.returncode, 2)
    check(
        "stderr reports --target-dir as required",
        "--target-dir" in proc.stderr and "required" in proc.stderr,
        True,
    )
    check("stderr includes a usage line", proc.stderr.strip().startswith("usage:"), True)
    check(
        "no default state directory is silently created when --target-dir is omitted",
        os.path.isdir(os.path.join(_scratch_cwd, ".craftflow")),
        False,
    )
finally:
    import shutil as _shutil

    _shutil.rmtree(_scratch_cwd, ignore_errors=True)

# --- _unique_backup_dir: backup-directory collision avoidance ---
print("\n[_unique_backup_dir]")
_collision_root = os.path.join(os.path.dirname(__file__), ".tmp_backup_collision_check")
os.makedirs(_collision_root, exist_ok=True)
try:
    ts = "20260731T120000Z"
    first = _unique_backup_dir(_collision_root, ts)
    check(
        "no collision: returns the base timestamped path unchanged",
        first,
        os.path.join(_collision_root, f".memory-repair-backup-{ts}"),
    )

    # Simulate a prior --apply run that already created this exact backup dir.
    os.makedirs(first)
    second = _unique_backup_dir(_collision_root, ts)
    check(
        "collision: appends a numeric suffix distinct from the existing dir",
        second,
        os.path.join(_collision_root, f".memory-repair-backup-{ts}-1"),
    )
    check("second path is not the same as the first (no overwrite)", second != first, True)

    # A second collision (two prior runs in the same UTC second) must
    # produce a third, still-distinct path.
    os.makedirs(second)
    third = _unique_backup_dir(_collision_root, ts)
    check(
        "double collision: increments suffix again to stay distinct",
        third,
        os.path.join(_collision_root, f".memory-repair-backup-{ts}-2"),
    )
finally:
    import shutil as _shutil

    _shutil.rmtree(_collision_root, ignore_errors=True)

# --- .gitignore covers backup-dir names via real git semantics ---
# Uses `git check-ignore` (not fnmatch) against this repo's actual .gitignore
# because gitignore's `**` glob semantics are git-specific; fnmatch would not
# prove real git behavior. Sample paths mirror exactly what
# _unique_backup_dir() generates: bare timestamp, "-1" suffix, "-12" suffix.
print("\n[.gitignore backup-dir coverage: git check-ignore]")
_repo_root = (
    subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=os.path.dirname(__file__),
        capture_output=True,
        text=True,
    )
    .stdout.strip()
)


def _check_ignore(rel_path: str):
    proc = subprocess.run(
        ["git", "check-ignore", "-v", rel_path],
        cwd=_repo_root,
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout.strip()


for _label, _rel_path in [
    ("bare timestamp", ".craftflow/state/.memory-repair-backup-20260731T999999Z/patterns.md"),
    ("-1 collision suffix", ".craftflow/state/.memory-repair-backup-20260731T999999Z-1/patterns.md"),
    ("-12 collision suffix", ".craftflow/state/.memory-repair-backup-20260731T999999Z-12/patterns.md"),
]:
    _code, _out = _check_ignore(_rel_path)
    check(f"git check-ignore reports ignored ({_label})", _code, 0)
    check(f"matched rule references the backup-dir pattern ({_label})", "memory-repair-backup" in _out, True)

# --- --apply output warns about git clean -fdx residual risk ---
print("\n[CLI --apply: git-hygiene warning]")
_apply_scratch = os.path.join(os.path.dirname(__file__), ".tmp_apply_warning_check")
_target_dir = os.path.join(_apply_scratch, "target")
os.makedirs(_target_dir, exist_ok=True)
try:
    with open(os.path.join(_target_dir, "patterns.md"), "w", encoding="utf-8") as f:
        f.write("## Common Gotchas\n- a gotcha\n## Last Updated\n2026-07-30\n")
    with open(os.path.join(_target_dir, "progress.md"), "w", encoding="utf-8") as f:
        f.write("## Completed\n- a task\n## Last Updated\n2026-07-30\n")
    with open(os.path.join(_target_dir, "activeContext.md"), "w", encoding="utf-8") as f:
        f.write("## References\n- Plan: `docs/plans/a.md`\n## Last Updated\n2026-07-30\n")

    proc = subprocess.run(
        [sys.executable, SCRIPT_PATH, "--target-dir", _target_dir, "--apply"],
        capture_output=True,
        text=True,
    )
    check("--apply exits 0", proc.returncode, 0)
    check("stdout still reports the backup path", "Applied. Backup written to" in proc.stdout, True)
    check(
        "stdout warns to copy the backup outside the repo before git hygiene ops",
        "git clean -fdx" in proc.stdout and "outside" in proc.stdout,
        True,
    )
finally:
    import shutil as _shutil

    _shutil.rmtree(_apply_scratch, ignore_errors=True)

# --- Summary ---
print(f"\n{'='*40}")
print(f"Results: {PASS} passed, {FAIL} failed")
if FAIL > 0:
    print("FAIL")
    sys.exit(1)
else:
    print("PASS")
    sys.exit(0)
