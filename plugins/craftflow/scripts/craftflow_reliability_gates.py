#!/usr/bin/env python3
"""
craftflow_reliability_gates.py

Append-only, durable ledger of proven invariants for tools/craftflow-plugin,
mirroring craftflow_skill_ledger.py's atomic-write/lock/fail-closed idiom.
Borrowed from stablyai/orca's config/reliability-gates.jsonc, trimmed to this
repo's scale (3 seeded gates, manual maturity promotion, no automatic
soak-period policy). See docs/plans/2026-08-09-craftflow-reliability-gates-ledger-design.md.

Subcommands:
    --seed [--state-dir PATH] [--ledger PATH]
        Idempotent bootstrap: create the ledger file with the 3 seeded gates
        ONLY if it does not already exist. A no-op (exit 0) if the file is
        already present -- never overwrites existing evidenceRuns/maturity.

    --record-evidence GATE_ID --wf WF_ID --outcome pass|fail [--note TEXT]
        [--state-dir PATH] [--ledger PATH]
        Append one evidenceRuns entry to the matching gate. Fails (exit 1, no
        write) if the ledger does not exist yet or GATE_ID is unknown.

    --promote GATE_ID --to soak|blocking [--state-dir PATH] [--ledger PATH]
        Manual maturity transition. Appends one maturity_history entry;
        never touches evidenceRuns/invariant/oracle/assertionRefs/title.
        No enforced sequence (experimental->blocking directly is allowed) --
        promotion is a human judgment call, not a state-machine policy.

    --list [--state-dir PATH] [--ledger PATH]
        Print a compact per-gate summary (id, title, maturity, evidence_count,
        last evidence run) as JSON.

    --query [--state-dir PATH] [--ledger PATH]
        Print the full raw ledger as JSON (mirrors craftflow_skill_ledger.py's
        own --query).

Ledger file default: .craftflow/state/project/reliability-gates.json
Exit 0 on success. Exit 1 on usage error, unknown gate_id, or fail-closed
corruption.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_LEDGER_PATH = ".craftflow/state/project/reliability-gates.json"
DEFAULT_STATE_DIR = ".craftflow/state"
# No MAX_EVIDENCE_PER_GATE cap, deliberately: evidenceRuns grows unboundedly by
# design (fresh-review pass 1, 2026-08-09 -- an earlier draft of this script
# truncated to the newest/oldest 25 entries past 50, which was removed because
# it silently discarded evidence history with zero test coverage, directly
# contradicting this ledger's own purpose -- a durable record of what has
# actually been proven, "not just the last verifier said PASS." This repo's
# remediation volume is far below Orca's soak-policy scale; revisit only if
# evidence volume becomes a real, measured problem.
_MATURITY_LEVELS = ("experimental", "soak", "blocking", "accepted-gap", "deprecated")
_PROMOTABLE_TARGETS = ("soak", "blocking")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_gates() -> list:
    """The 3 gates this repo has already been burned by. Gate 1's
    invariant/oracle/assertionRefs were corrected during planning (2026-08-09
    plan, "Plan-vs-Code Gaps") against the REAL code path -- the design
    draft's "git merge-tree --write-tree" claim does not exist anywhere in
    this codebase (grep-verified); the real, already-comprehensive proof is
    craftflow_worktree_merge_guard_check.py's 22 real subprocess scenarios."""
    return [
        {
            "id": "worktree-merge-safety",
            "title": (
                "BUILD worktree merges never apply while the main tree is dirty, "
                "and a real merge conflict releases the lock without deleting the worktree"
            ),
            "maturity": "experimental",
            "invariant": (
                "Before any worktree branch is merged or copy-fallback-applied into "
                "$PROJECT_ROOT (SKILL.md Worktree Isolation step 4d->4e), the main tree's "
                "clean-tree check must gate first; a real merge conflict must release the "
                "merge lock and leave the worktree intact for the user to resolve, never "
                "silently discard work."
            ),
            "oracle": (
                "Run `python3 tools/craftflow-plugin/plugins/craftflow/scripts/"
                "craftflow_worktree_merge_guard_check.py` (22 scenarios against the real "
                "guard scripts via subprocess). Falsified if scenario_dirty_main_tree, "
                "scenario_merge_conflict_unaffected, or scenario_copy_fallback_dirty_tree_"
                "regression fail."
            ),
            "assertionRefs": [
                "scripts/craftflow_worktree_merge_guard_check.py::scenario_dirty_main_tree",
                "scripts/craftflow_worktree_merge_guard_check.py::scenario_merge_conflict_unaffected",
                "scripts/craftflow_worktree_merge_guard_check.py::scenario_copy_fallback_dirty_tree_regression",
            ],
            "evidenceRuns": [],
            "maturity_history": [],
        },
        {
            "id": "memory-write-guard-symmetry",
            "title": (
                "craftflow_pretooluse_guard.py denies direct protected-memory writes "
                "symmetrically across Edit/Write and Bash write mechanisms"
            ),
            "maturity": "experimental",
            "invariant": (
                "A raw Edit/Write or Bash-based write (redirect, tee, python "
                "open()/write_text()/os.system/subprocess/shutil, cp/mv/ln/install/rsync/dd) "
                "targeting activeContext.md, patterns.md, progress.md, or the "
                ".memory-finalize permit sentinel is denied unless a valid memory-finalize "
                "permit token is present for the active workflow -- enforced identically "
                "whether the write arrives via the Edit/Write tool or via Bash."
            ),
            "oracle": (
                "Run `python3 tools/craftflow-plugin/plugins/craftflow/scripts/"
                "craftflow_hook_unit_tests.py`. Falsified if any pretooluse-guard test in "
                "the [ pretooluse-guard ] / [ pretooluse-guard: Phase 4 protected-path + "
                "Bash-write inspection + confinement ] sections fails."
            ),
            "assertionRefs": [
                "scripts/craftflow_hook_unit_tests.py::test_pretooluse_guard_blocks_memory_write_without_permit",
                "scripts/craftflow_hook_unit_tests.py::test_pretooluse_guard_allows_memory_write_with_permit",
                "scripts/craftflow_hook_unit_tests.py::test_pretooluse_guard_denies_bash_heredoc_write_to_memory_md",
                "scripts/craftflow_hook_unit_tests.py::test_pretooluse_guard_denies_bash_tee_write_to_memory_md",
            ],
            "evidenceRuns": [],
            "maturity_history": [],
        },
        {
            "id": "fix-verify-evidence-completeness",
            "title": (
                "Every FIX_VERDICT: LOAD_BEARING claim carries all 6 required non-empty "
                "evidence fields, and a claim without real pre/post divergence is downgraded"
            ),
            "maturity": "experimental",
            "invariant": (
                "A doubt-verifier phase:fix-verify cycle may only report "
                "FIX_VERDICT: LOAD_BEARING when PRE_FIX_COMMAND, PRE_FIX_OUTPUT, "
                "POST_FIX_COMMAND, POST_FIX_OUTPUT, SIBLING_SCAN_COMMAND, and "
                "SIBLING_SCAN_RESULT are all present and non-empty, and PRE_FIX_OUTPUT must "
                "meaningfully differ from POST_FIX_OUTPUT in the direction the fix claims -- "
                "otherwise the router downgrades the verdict to NOT_LOAD_BEARING before "
                "acting on it."
            ),
            "oracle": (
                "No automated test exists for this invariant today -- it is enforced by the "
                "router's own contract-override judgment each cycle (SKILL.md ### Contract "
                "overrides, doubt-verifier (phase:fix-verify) row), applied by an LLM reading "
                "the agent's own reported fields. This is the exact gap exposed by the "
                "2026-08-08 incident (a reviewer cited a stale test count unnoticed by "
                "contract-override logic); this gate's own evidenceRuns log is the primary "
                "durable proof mechanism until an automated check exists (deferred, out of "
                "scope for this plan)."
            ),
            "assertionRefs": [
                "skills/craftflow-router/SKILL.md::### Contract overrides -> doubt-verifier (phase:fix-verify) row",
                "agents/doubt-verifier.md::## Fix-Verify Contract (phase:fix-verify)",
            ],
            "evidenceRuns": [],
            "maturity_history": [],
        },
    ]


def _empty_ledger() -> dict:
    return {"schema_version": SCHEMA_VERSION, "gates": []}


class LedgerCorruptError(Exception):
    """Ledger file EXISTS but content cannot be trusted (parse failure or
    invalid top-level shape) -- distinguished from "file does not exist"
    (benign). Mirrors craftflow_skill_ledger.py's own LedgerCorruptError."""


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return _empty_ledger()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise LedgerCorruptError(f"ledger file at {path} exists but failed to parse: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("gates"), list):
        raise LedgerCorruptError(f"ledger file at {path} exists but has an invalid schema shape")
    return data


@contextlib.contextmanager
def _ledger_file_lock(ledger_path: Path):
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def save_ledger_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".reliability-gates-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def find_gate(ledger: dict, gate_id: str):
    for g in ledger.get("gates", []):
        if isinstance(g, dict) and g.get("id") == gate_id:
            return g
    return None


def cmd_seed(args) -> int:
    ledger_path_ = Path(args.ledger)
    with _ledger_file_lock(ledger_path_):
        if ledger_path_.exists():
            load_ledger(ledger_path_)  # validates the file parses; raises LedgerCorruptError if not
            print(json.dumps({"seeded": False, "reason": "ledger already exists"}, indent=2))
            return 0
        ledger = {"schema_version": SCHEMA_VERSION, "gates": _seed_gates()}
        save_ledger_atomic(ledger_path_, ledger)
    print(json.dumps({"seeded": True, "gate_count": len(ledger["gates"])}, indent=2))
    return 0


def cmd_record_evidence(args) -> int:
    ledger_path_ = Path(args.ledger)
    with _ledger_file_lock(ledger_path_):
        ledger = load_ledger(ledger_path_)
        if not ledger.get("gates"):
            print(json.dumps({"error": "ledger has no gates -- run --seed first"}), file=sys.stderr)
            return 1
        gate = find_gate(ledger, args.record_evidence)
        if gate is None:
            print(json.dumps({"error": f"unknown gate_id: {args.record_evidence!r}"}), file=sys.stderr)
            return 1
        gate.setdefault("evidenceRuns", [])
        gate["evidenceRuns"].append({
            "wf": args.wf,
            "ts": now_iso(),
            "outcome": args.outcome,
            "note": args.note or "",
        })
        # evidenceRuns grows unboundedly -- no truncation. See the
        # "no MAX_EVIDENCE_PER_GATE" comment near the module constants (Task 1)
        # for why (fresh-review pass 1, 2026-08-09 blocking finding #2).
        save_ledger_atomic(ledger_path_, ledger)
    print(json.dumps({
        "recorded": args.record_evidence,
        "wf": args.wf,
        "outcome": args.outcome,
        "total_evidence_runs": len(gate["evidenceRuns"]),
    }, indent=2))
    return 0


def cmd_promote(args) -> int:
    ledger_path_ = Path(args.ledger)
    with _ledger_file_lock(ledger_path_):
        ledger = load_ledger(ledger_path_)
        gate = find_gate(ledger, args.promote)
        if gate is None:
            print(json.dumps({"error": f"unknown gate_id: {args.promote!r}"}), file=sys.stderr)
            return 1
        old_maturity = gate.get("maturity", "experimental")
        gate["maturity"] = args.to
        gate.setdefault("maturity_history", [])
        gate["maturity_history"].append({"ts": now_iso(), "from": old_maturity, "to": args.to})
        save_ledger_atomic(ledger_path_, ledger)
    print(json.dumps({"promoted": args.promote, "from": old_maturity, "to": args.to}, indent=2))
    return 0


def cmd_list(args) -> int:
    ledger = load_ledger(Path(args.ledger))
    summary = []
    for g in ledger.get("gates", []):
        runs = g.get("evidenceRuns", [])
        summary.append({
            "id": g.get("id"),
            "title": g.get("title"),
            "maturity": g.get("maturity"),
            "evidence_count": len(runs),
            "last_evidence": runs[-1] if runs else None,
        })
    print(json.dumps(summary, indent=2))
    return 0


def cmd_query(args) -> int:
    ledger = load_ledger(Path(args.ledger))
    print(json.dumps(ledger, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Craftflow reliability-gates ledger: durable, cross-workflow record of proven invariants."
    )
    parser.add_argument("--seed", action="store_true", help="Idempotently create the ledger with the 3 seeded gates.")
    parser.add_argument("--record-evidence", metavar="GATE_ID", help="Append one evidenceRuns entry to GATE_ID.")
    parser.add_argument("--promote", metavar="GATE_ID", help="Manually transition GATE_ID's maturity.")
    parser.add_argument("--to", choices=_PROMOTABLE_TARGETS, help="Target maturity for --promote.")
    parser.add_argument("--wf", help="Workflow id (required with --record-evidence).")
    parser.add_argument("--outcome", choices=("pass", "fail"), help="Outcome (required with --record-evidence).")
    parser.add_argument("--note", default=None, help="Optional one-line note.")
    parser.add_argument("--list", action="store_true", help="Print a compact per-gate summary.")
    parser.add_argument("--query", action="store_true", help="Print the full raw ledger as JSON.")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help=f"Craftflow state dir (default: {DEFAULT_STATE_DIR})")
    parser.add_argument("--ledger", default=None, help=f"Ledger file path (default: derived from --state-dir, {DEFAULT_LEDGER_PATH})")
    return parser


def _resolve_ledger_path(args) -> str:
    if args.ledger:
        return args.ledger
    return str(Path(args.state_dir) / "project" / "reliability-gates.json")


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    args.ledger = _resolve_ledger_path(args)

    if args.record_evidence and (not args.wf or not args.outcome):
        parser.error("--record-evidence requires --wf and --outcome")
    if args.promote and not args.to:
        parser.error("--promote requires --to")

    try:
        if args.seed:
            return cmd_seed(args)
        if args.record_evidence:
            return cmd_record_evidence(args)
        if args.promote:
            return cmd_promote(args)
        if args.list:
            return cmd_list(args)
        if args.query:
            return cmd_query(args)
    except (OSError, UnicodeDecodeError, AttributeError, TypeError, LedgerCorruptError) as exc:
        print(json.dumps({"error": f"unexpected error: {exc}"}), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
