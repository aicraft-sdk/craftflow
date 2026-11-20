#!/usr/bin/env python3
"""Local code-health gate: the checks that can actually run in THIS checkout, as one fail-closed command.

(craftflow_release_gate.py is the monorepo release gate; it hardcodes tools/craftflow-plugin/ paths and Node scripts
that are absent here, so it cannot run in this repository. This gate does not replace it.)

Steps, always in this order, each a subprocess (see --list):
    ruff                  `ruff check` (dev-only tool). Not installed: FAIL with --require-ruff or CI=true, else SKIP
                          with an install hint. Never a silent pass.
    suppression-ratchet   craftflow_suppression_ratchet.py
    file-size-ratchet     craftflow_file_size_ratchet.py
    clean-state           craftflow_clean_state_check.py --suppression-mode block. That script scans the uncommitted diff
                          against HEAD plus untracked files and has no base-ref option, so by default this judges the
                          change being committed. With --base-ref the gate judges the committed PR diff instead (see
                          clean_state_pr_diff) by replaying `merge-base..working tree` as uncommitted changes in a
                          throwaway worktree; untracked files are not seen in that mode.
    baseline-integrity    craftflow_baseline_integrity.py: ratchet baselines, ruff.toml and hook-mode.json may only get
                          stricter than at the merge-base. Without CI=true / --require-base it passes
                          --allow-missing-base, so a shallow clone with no default branch SKIPS (loudly) instead of
                          breaking every commit.
    py-compile            every tracked .py under plugins/craftflow/scripts and tests/fixtures compiles (compile(); no
                          .pyc is written)
    guardrail-refs        craftflow_guardrail_refs.py: every backticked path in AGENT_CRITICAL_GUARDRAILS.md resolves here
    suite:<name>          the curated test suites in SUITES below

Every step runs even after a failure (so one run shows everything that is wrong); fail-closed means a step that cannot
start, times out, or exits non-zero is FAIL. Test suites run in a temporary working directory, so they cannot leave
state in the repository.

Exit codes: 0 every non-skipped step passed, 1 gate/tool error (bad arguments, unreadable --steps-file, flags refused in
CI), 2 at least one step FAILED. Skips are never hidden: SKIP (tool missing, root) and SKIPPED BY FLAG (--skip, --only,
--fast) are listed in the summary, and the flags are refused when CI=true.

Usage:
    craftflow_local_gate.py [--format text|json] [--list] [--only STEP|GROUP ...] [--skip STEP|GROUP ...] [--fast]
        [--require-ruff] [--require-base] [--base-ref REF] [--approved-increase FILE ...] [--steps-file FILE]
GROUP is `suites` or a step name. --fast skips the steps marked slow (used by .githooks/pre-commit). --steps-file
replaces the built-in steps with a JSON list of {name, argv, group?, needs?, hint?, cwd?, tags?, desc?} (test hook).
Python 3.9, standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = Path(__file__).resolve().parent
FIXTURES = "plugins/craftflow/tests/fixtures"
EXIT_OK, EXIT_TOOL_ERROR, EXIT_FAIL = 0, 1, 2
TAIL_LINES = 30
STEP_TIMEOUT = 900

# Curated test suites. INCLUSION RULE: a tests/fixtures/test_*.py suite is listed only if it passes in this checkout
# when run from a clean temporary directory, as root AND as an unprivileged user (measured with `setpriv
# --reuid=nobody`), and is deterministic. Add a suite by appending (stem, tags); tag "slow" (over ~5s, skipped by
# --fast) and/or "root" (passes unprivileged but fails as uid 0: the gate reports it as SKIP (root) there, never as
# a pass). Do not add a suite to make the gate pass: a suite not listed is excluded for one of these reasons:
#   - assumes the monorepo layout (docs/, .github/, tools/craftflow-plugin/ resolved above this checkout): test_ci_fixture_runner,
#     test_craftflow_cursor_exempt_wiring, test_craftflow_docs_rot, test_craftflow_release_gate, test_router_budget,
#     test_router_growth_envelope, test_rule_inventory_rows2, test_rule_inventory_doubt3, test_router_policy_prose
#   - needs the pinned baseline commit 0439c33 (absent from this clone; needs full history): test_rule_inventory,
#     test_rule_inventory_doubt, test_rule_inventory_doubt2, test_rule_inventory_sbc, test_router_context_impact,
#     test_router_readset
#   - fails here for other environment reasons (also as non-root): test_craftflow_context_nudge,
#     test_craftflow_routing_law, test_memory_repair, test_policy_guards, test_resolve_workspace_root
#   - timing-based, fails under load: test_craftflow_jev_prompt_hint (import-cheap wall-clock budget)
#   - flaky: test_harvest_live_driver (1 of 3 runs failed "orphan grandchild alive" when run through this gate on 3.9)
#   - turns its skips into failures under CI=true and needs the pinned baseline commit 0439c33, absent from this
#     repository: test_craftflow_policy
# scripts/craftflow_hook_unit_tests.py (30k lines) is not a suite here: it crashes on a missing monorepo path
# (/home/packages/agent-workflow/resources/craftflow/agents/bug-investigator.md).
SUITES = [
    ("test_agent_structure", ""), ("test_architecture_scanner_agent", ""), ("test_contract_validate", ""),
    ("test_contract_validate_impact", ""), ("test_contract_validate_pilot", ""), ("test_contract_validate_scanner", ""),
    ("test_craftflow_arch_lint", "root"), ("test_craftflow_arch_rule_selftest", ""),
    ("test_craftflow_baseline_integrity", ""), ("test_craftflow_bite_check", ""), ("test_repro_first_debug", ""),
    ("test_craftflow_clean_state_check", "slow"), ("test_craftflow_conductor_inspection", ""),
    ("test_craftflow_feature_backlog", ""), ("test_craftflow_file_size_ratchet", ""), ("test_craftflow_guardrail_refs", ""),
    ("test_craftflow_harness_capabilities_impact", ""), ("test_craftflow_jev_ab_report", ""),
    ("test_craftflow_jev_client", ""), ("test_craftflow_jev_config", ""), ("test_craftflow_jev_corpus_build", ""),
    ("test_craftflow_jev_heuristic", ""), ("test_craftflow_jev_remfix_scope", ""), ("test_craftflow_jev_replay", ""),
    ("test_craftflow_jev_report", "slow"), ("test_craftflow_jev_risk_gate", ""), ("test_craftflow_jev_session_cache", ""),
    ("test_craftflow_jev_session_check", ""), ("test_craftflow_jev_setup", ""), ("test_craftflow_local_gate", ""),
    ("test_craftflow_lvr5_script", ""), ("test_craftflow_model_report", ""),     ("test_craftflow_retro", ""), ("test_craftflow_retro_v2", ""), ("test_craftflow_retro_wiring", ""),
    ("test_craftflow_review_package", ""), ("test_craftflow_route_types", ""), ("test_craftflow_status_chains", ""),
    ("test_craftflow_stop_gate", "slow"), ("test_craftflow_stop_gate_act", "root"),
    ("test_craftflow_suppression_ratchet", ""), ("test_craftflow_transcript_usage", ""),
    ("test_craftflow_untangle_rules", ""), ("test_craftflow_workflow_handoff", ""),
    ("test_craftflow_workflow_handoff_2", "slow"), ("test_cursor_routing_pointer", ""), ("test_gate_check", ""),
    ("test_learn_scan", ""), ("test_memory_merge", ""),
    ("test_reliability_gates", ""), ("test_route_law_static", ""), ("test_route_replay_ch", "slow"),
    ("test_route_replay_handoff", "slow"), ("test_router_relocation_drills", ""), ("test_router_test_anchors", "slow"),
    ("test_router_xrefs", ""), ("test_rule_inventory_hardening", ""), ("test_test_value_gate", ""), ("test_version_bump", ""),
    ("test_version_consistency", ""), ("test_workflow_id_and_progress", ""), ("test_workspace_init", ""),
    ("test_worktree_finalize", "slow"),
]
ROOT_SKIP_REASON = "uid 0: this suite's chmod-based checks cannot hold for root; run the gate as a non-root user to include it"
RUFF_HINT = "install it with `pip install ruff==0.16.9` (or pipx/uv), then re-run; CI installs it and passes --require-ruff"


def make_step(name: str, argv: List[str], **extra: object) -> Dict[str, object]:
    step: Dict[str, object] = {"name": name, "argv": argv, "group": name, "needs": None, "hint": "", "cwd": None, "tags": [], "desc": ""}
    step.update(extra)
    return step


def build_steps(root: Path, args: argparse.Namespace) -> List[Dict[str, object]]:
    py = sys.executable

    def script(name: str) -> str:
        return str(SCRIPTS / name)

    return [
        make_step("ruff", ["ruff", "check"], needs="ruff", hint=RUFF_HINT, desc="ruff check (dev-only linter)"),
        make_step("suppression-ratchet", [py, "-B", script("craftflow_suppression_ratchet.py"), "--root", str(root)],
                  desc="suppression counts may not exceed the baseline"),
        make_step("file-size-ratchet", [py, "-B", script("craftflow_file_size_ratchet.py"), "--root", str(root)],
                  desc="files over 500 lines may not grow; no new ones"),
        make_step("clean-state", clean_state_argv(root, args), desc="newly added noqa/type-ignore need a code and a reason (block mode)"),
        make_step("baseline-integrity", integrity_argv(root, args), desc="baselines, ruff.toml and hook-mode.json may not loosen"),
        make_step("py-compile", [py, "-B", str(Path(__file__).resolve()), "--internal", "compile"],
                  desc="tracked scripts/ and tests/fixtures .py files compile"),
        make_step("guardrail-refs", [py, "-B", script("craftflow_guardrail_refs.py"), "--root", str(root)],
                  desc="paths cited in AGENT_CRITICAL_GUARDRAILS.md exist in this repository"),
    ] + [suite_step(stem, tags) for stem, tags in SUITES]


def clean_state_argv(root: Path, args: argparse.Namespace) -> List[str]:
    if args.base_ref:
        return [sys.executable, "-B", str(Path(__file__).resolve()), "--internal", "clean-state-pr-diff", args.base_ref]
    return [sys.executable, "-B", str(SCRIPTS / "craftflow_clean_state_check.py"), "--project-root", str(root), "--suppression-mode", "block"]


def integrity_argv(root: Path, args: argparse.Namespace) -> List[str]:
    argv = [sys.executable, "-B", str(SCRIPTS / "craftflow_baseline_integrity.py"), "--root", str(root)]
    if args.base_ref:
        argv += ["--base-ref", args.base_ref]
    if not is_strict(args.require_base):
        argv.append("--allow-missing-base")
    for rel in args.approved_increase or []:
        argv += ["--approved-increase", rel]
    return argv


def suite_step(stem: str, tags: str) -> Dict[str, object]:
    path = SCRIPTS.parent / "tests" / "fixtures" / f"{stem}.py"
    return make_step(f"suite:{stem}", [sys.executable, "-B", str(path)], group="suites", cwd="tmp", tags=tags.split(), desc=f"{FIXTURES}/{stem}.py")


def is_strict(flag: bool = False) -> bool:
    return flag or os.environ.get("CI", "").lower() == "true"


def tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def run_step(step: Dict[str, object], timeout: int = STEP_TIMEOUT) -> Dict[str, object]:
    """Runs one step's argv. Anything but exit 0 (including failure to start and timeout) is FAIL."""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    start = time.time()
    tmp = tempfile.mkdtemp(prefix="craftflow-gate-") if step["cwd"] == "tmp" else None
    try:
        proc = subprocess.run(step["argv"], capture_output=True, text=True, env=env, timeout=timeout,
                              cwd=tmp or step["cwd"] or str(ROOT))
        code, output, reason = proc.returncode, proc.stdout + proc.stderr, ""
    except subprocess.TimeoutExpired as exc:
        code, output, reason = -1, str(exc.stdout or "") + str(exc.stderr or ""), f"timed out after {timeout}s"
    except OSError as exc:
        code, output, reason = -1, "", f"could not start: {exc}"
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    status = "pass" if code == 0 else "fail"
    return {"status": status, "returncode": code, "seconds": round(time.time() - start, 1),
            "reason": reason or (f"exit {code}" if code else ""), "output_tail": "" if code == 0 else tail(output)}


def result(step: Dict[str, object], status: str, reason: str) -> Dict[str, object]:
    return {"name": step["name"], "status": status, "returncode": None, "seconds": 0.0, "reason": reason, "output_tail": ""}


def decide(step: Dict[str, object], opts: Dict[str, object]) -> Optional[Dict[str, object]]:
    """A non-run outcome for the step (flag skip, root skip, missing tool), or None when it should run."""
    only, skip = opts["only"], opts["skip"]
    tokens = (step["name"], step["group"])
    if only and not any(t in only for t in tokens):
        return result(step, "flag", "not selected by --only")
    if any(t in skip for t in tokens):
        return result(step, "flag", "--skip")
    if opts["fast"] and "slow" in step["tags"]:
        return result(step, "flag", "--fast (slow step)")
    if "root" in step["tags"] and opts["is_root"]:
        return result(step, "skip", f"root: {ROOT_SKIP_REASON}")
    if step["needs"] and not opts["which"](str(step["needs"])):
        msg = f"`{step['needs']}` is not installed: {step['hint']}"
        if opts["require"].get(step["needs"]):
            return result(step, "fail", f"required but missing: {msg}")
        return result(step, "skip", f"tool missing: {msg}")
    return None


def run_gate(steps: Sequence[Dict[str, object]], opts: Dict[str, object], runner: Callable = run_step,
             emit: Callable[[str], None] = lambda line: None) -> List[Dict[str, object]]:
    results = []
    for step in steps:
        res = decide(step, opts)
        if res is None:
            res = dict(runner(step), name=step["name"])
        results.append(res)
        if res["status"] != "flag":  # flagged steps are listed together in the summary
            emit(format_line(res))
        if res["status"] == "fail" and res["output_tail"]:
            emit("\n".join("    | " + ln for ln in str(res["output_tail"]).splitlines()))
    return results


LABELS = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "flag": "SKIPPED BY FLAG"}


def format_line(res: Dict[str, object]) -> str:
    extra = f" ({res['reason']})" if res["reason"] else ""
    return f"[{LABELS[str(res['status'])]}] {res['name']} {res['seconds']}s{extra}"


def summarize(results: Sequence[Dict[str, object]]) -> Dict[str, object]:
    count = {s: sum(1 for r in results if r["status"] == s) for s in LABELS}
    failed = [r["name"] for r in results if r["status"] == "fail"]
    return {"passed": count["pass"], "failed": count["fail"], "skipped": count["skip"], "skipped_by_flag": count["flag"],
            "failed_steps": failed, "exit": EXIT_FAIL if failed else EXIT_OK}


def render_summary(results: Sequence[Dict[str, object]], summary: Dict[str, object], elapsed: float) -> str:
    lines = ["", "=== craftflow local gate summary ==="]
    lines += [format_line(r) for r in results if r["status"] in ("fail", "skip")] or ["(no failed or skipped steps)"]
    flagged = [r for r in results if r["status"] == "flag"]
    if 0 < len(flagged) <= 15:
        lines += [format_line(r) for r in flagged]
    elif flagged:
        lines.append(f"[SKIPPED BY FLAG] {len(flagged)} steps ({flagged[0]['reason']}): " + ", ".join(str(r["name"]) for r in flagged[:6]) + ", ... (full list: --format json)")
    lines.append(f"{summary['passed']} passed, {summary['failed']} FAILED, {summary['skipped']} skipped, "
                 f"{summary['skipped_by_flag']} SKIPPED BY FLAG in {elapsed:.0f}s")
    if flagged:
        lines.append(f"WARNING: {len(flagged)} step(s) SKIPPED BY FLAG: this run is NOT the full gate and proves nothing about them.")
    if any(r["status"] == "skip" for r in results):
        lines.append("NOTE: steps marked SKIP did not run (missing tool or root); a green result excludes them.")
    if summary["failed"]:
        lines.append("GATE FAILED: " + ", ".join(summary["failed_steps"]) + ". Fix the cause shown above and re-run; the output tail is the first place to look.")
    else:
        lines.append("GATE PASSED (for the steps that ran).")
    return "\n".join(lines)


# --- internal subcommands (run as `craftflow_local_gate.py --internal NAME`, so each stays a subprocess step) ------

def internal_compile() -> int:
    proc = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "--", "plugins/craftflow/scripts", FIXTURES],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"git ls-files failed: {proc.stderr.strip()}")
        return 1
    bad = 0
    names = [n for n in proc.stdout.split("\0") if n.endswith(".py")]
    for rel in names:
        try:
            compile((ROOT / rel).read_text(encoding="utf-8"), rel, "exec")
        except (SyntaxError, ValueError, OSError) as exc:
            print(f"{rel}: {exc}")
            bad += 1
    print(f"py-compile: {len(names) - bad}/{len(names)} files compile")
    return 1 if bad else 0


def clean_state_pr_diff(base_ref: str, root: Path = ROOT) -> int:
    """Judge `merge-base(HEAD, base_ref)..working tree` with the unmodified clean-state script: check out the merge-base
    in a throwaway worktree, apply the diff there as uncommitted changes, and scan that."""
    def git(*a: str, cwd: Path = root, data: Optional[bytes] = None) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(cwd), *a], capture_output=True, input=data)
    mb = git("merge-base", "HEAD", base_ref)
    if mb.returncode != 0 or not mb.stdout.strip():
        print(f"cannot find a merge-base of HEAD and {base_ref}: fetch more history (`git fetch --unshallow`, or actions/checkout fetch-depth: 0)")
        return 1
    sha = mb.stdout.decode().strip()
    work = Path(tempfile.mkdtemp(prefix="craftflow-prdiff-")) / "wt"
    try:
        added = git("worktree", "add", "--detach", str(work), sha)
        if added.returncode != 0:
            print(f"git worktree add failed: {added.stderr.decode().strip()}")
            return 1
        diff = git("diff", "--binary", sha).stdout
        if diff and git("apply", "--whitespace=nowarn", cwd=work, data=diff).returncode != 0:
            print("could not replay the PR diff onto its merge-base")
            return 1
        proc = subprocess.run([sys.executable, "-B", str(SCRIPTS / "craftflow_clean_state_check.py"), "--project-root", str(work),
                               "--suppression-mode", "block"], capture_output=True, text=True)
        print(f"clean-state over {sha[:12]}..working tree\n{proc.stdout}{proc.stderr}", end="")
        return proc.returncode
    finally:
        git("worktree", "remove", "--force", str(work))
        shutil.rmtree(work.parent, ignore_errors=True)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # argparse exits 2 on usage errors, which would read as "check failed"
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        sys.exit(EXIT_TOOL_ERROR)


def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = _Parser(description="craftflow local code-health gate (fail-closed)")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--list", action="store_true", help="print the steps and exit")
    p.add_argument("--only", action="append", default=[], metavar="STEP", help="run only this step or group (repeatable); the rest are SKIPPED BY FLAG")
    p.add_argument("--skip", action="append", default=[], metavar="STEP", help="skip this step or group (repeatable); shown as SKIPPED BY FLAG")
    p.add_argument("--fast", action="store_true", help="skip steps marked slow (shown as SKIPPED BY FLAG)")
    p.add_argument("--require-ruff", action="store_true", help="FAIL instead of SKIP when ruff is not installed (implied by CI=true)")
    p.add_argument("--require-base", action="store_true", help="do not pass --allow-missing-base to baseline-integrity (implied by CI=true)")
    p.add_argument("--base-ref", default=None, help="compare against merge-base(HEAD, REF): baseline-integrity and the clean-state PR-diff replay")
    p.add_argument("--approved-increase", action="append", metavar="FILE", help="human override forwarded to baseline-integrity; agents must not use it")
    p.add_argument("--steps-file", default=None, help="JSON list of steps replacing the built-in ones (test hook)")
    p.add_argument("--internal", nargs="+", help=argparse.SUPPRESS)
    return p.parse_args(argv)


def load_steps_file(path: str) -> List[Dict[str, object]]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return [make_step(item["name"], list(item["argv"]), **{k: v for k, v in item.items() if k not in ("name", "argv")}) for item in raw]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"bad --steps-file {path}: {exc}") from exc


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.internal:
        return internal_compile() if args.internal[0] == "compile" else clean_state_pr_diff(args.internal[1])
    try:
        steps = load_steps_file(args.steps_file) if args.steps_file else build_steps(ROOT, args)
    except ValueError as exc:
        print(f"gate error: {exc}", file=sys.stderr)
        return EXIT_TOOL_ERROR
    if args.list:
        for s in steps:
            print(f"{s['name']:<48} {','.join(s['tags']) or '-':<8} {s['desc']}")
        return EXIT_OK
    names = {str(s["name"]) for s in steps} | {str(s["group"]) for s in steps}
    unknown = [t for t in args.only + args.skip if t not in names]
    if unknown:
        print(f"gate error: unknown step(s) {', '.join(unknown)}; see --list", file=sys.stderr)
        return EXIT_TOOL_ERROR
    if (args.only or args.skip or args.fast) and is_strict():
        print("gate error: --only/--skip/--fast are refused when CI=true: CI runs every step", file=sys.stderr)
        return EXIT_TOOL_ERROR
    opts = {"only": args.only, "skip": args.skip, "fast": args.fast, "is_root": hasattr(os, "geteuid") and os.geteuid() == 0,
            "which": shutil.which, "require": {"ruff": is_strict(args.require_ruff)}}
    text = args.format == "text"
    started = time.time()
    results = run_gate(steps, opts, emit=(lambda line: print(line, flush=True)) if text else (lambda line: None))
    summary = summarize(results)
    if text:
        print(render_summary(results, summary, time.time() - started))
    else:
        print(json.dumps({"exit": summary["exit"], "summary": summary, "steps": results}, indent=2))
    return int(summary["exit"])


if __name__ == "__main__":
    sys.exit(main())
