#!/usr/bin/env python3
"""Tests for craftflow:retro v2 (read-only recurrence, LV-R4 widening).

Run: python3 tests/fixtures/test_craftflow_retro_v2.py
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

SCRIPT = SCRIPTS / "craftflow_retro.py"
FIXTURES = Path(__file__).resolve().parent

CLEAN = "wf-retro-clean-20261006-000000-aaaa0001"
REMFIX = "wf-retro-remfix-20261006-000000-aaaa0002"
ALL = "wf-retro-all-20261006-000000-aaaa0003"
BREAKER = "wf-retro-breaker-20261006-000000-aaaa0004"
NEAR = "wf-retro-near-20261006-000000-aaaa0005"

SKILL = PLUGIN_ROOT / "skills" / "retro" / "SKILL.md"
INSTALL = PLUGIN_ROOT / "install-cursor.sh"
MANIFEST = PLUGIN_ROOT / "tests" / "live" / "manifests" / "retro.json"
README_PLUGIN = PLUGIN_ROOT / "README.md"

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, reason)


def run_cli(args: list, state_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--state-dir", str(state_dir)],
        capture_output=True,
        text=True,
    )


def materialize(tmp: Path, names: list) -> Path:
    wf = tmp / ".craftflow" / "state" / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    for n in names:
        for suffix in (".json", ".events.jsonl"):
            shutil.copy(FIXTURES / "retro" / (n + suffix), wf / (n + suffix))
    return tmp / ".craftflow" / "state"


def write_wf(state: Path, name: str, artifact, events_text) -> None:
    wf = state / "workflows"
    wf.mkdir(parents=True, exist_ok=True)
    if artifact is not None:
        text = artifact if isinstance(artifact, str) else json.dumps(artifact)
        (wf / (name + ".json")).write_text(text)
    if events_text is not None:
        (wf / (name + ".events.jsonl")).write_text(events_text)


def base_artifact(name: str) -> dict:
    art = json.loads((FIXTURES / "retro" / (CLEAN + ".json")).read_text())
    art["workflow_uuid"] = name
    art["workflow_id"] = name
    return art


def expect_error(name: str, proc: subprocess.CompletedProcess, code: str, extra: str = "") -> None:
    err = proc.stderr.strip()
    good = (
        proc.returncode == 1
        and proc.stdout == ""
        and err.startswith(f"ERROR: {code}:")
        and "\n" not in err
        and extra in err
    )
    check(name, good, f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")


def _snapshot(root: Path) -> list:
    snap = []
    for dirpath, dirnames, filenames in os.walk(root):
        for n in sorted(dirnames + filenames):
            p = Path(dirpath) / n
            st = p.stat()
            snap.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns))
    return sorted(snap)


def frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_text, body) split on the first two '---' lines."""
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(idx) < 2 or idx[0] != 0:
        return "", text
    return "\n".join(lines[1:idx[1]]), "\n".join(lines[idx[1] + 1:])


def section(body: str, heading_prefix: str) -> str:
    m = re.search(r"^## " + re.escape(heading_prefix) + r".*?$", body, re.M)
    if not m:
        return ""
    rest = body[m.end():]
    nxt = re.search(r"^## ", rest, re.M)
    return rest[: nxt.start()] if nxt else rest


# --- Phase 2: LV-R4 widened (SPEC-0032 FR-006) ---------------------------------


def test_manifest_valid_for_runner() -> None:
    import craftflow_live_harness_runner as runner

    m = runner.load_manifest(MANIFEST)
    runner.ensure_steps("scenarios", m["scenarios"], require_scenario_fields=True)
    runner.ensure_steps("healthcheck", m["healthcheck"])
    ok("manifest_valid_for_runner")


def test_lvr4_links() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    sc = [s for s in m["scenarios"] if s["name"].startswith("LV-R4")]
    check("lvr4_present", len(sc) == 1, f"found {len(sc)} LV-R4 scenarios")
    if len(sc) != 1:
        return
    got_m = re.search(r"for n in ([^;]*); do", sc[0]["command"])
    want_m = re.search(r"^\s*for SKILL_NAME in ([^;\n]*); do", INSTALL.read_text(encoding="utf-8"), re.M)
    got = got_m.group(1).split() if got_m else None
    want = want_m.group(1).split() if want_m else None
    check("lvr4_links_match_installer", got is not None and got == want, f"manifest={got} installer={want}")
    text = sc[0]["name"] + " " + sc[0]["then"]
    check("lvr4_text_names_four", all(n in text for n in ("retro", "status", "failure-digest")),
          f"name/then lack a skill name: {text!r}")


# --- Phase 3: --recurrence (SPEC-0032 FR-001..FR-004) ----------------------------

ALL5 = [CLEAN, REMFIX, ALL, BREAKER, NEAR]


def _state(tmp: str, names=None) -> Path:
    return materialize(Path(tmp), ALL5 if names is None else names)


def _rec(args: list, state: Path) -> dict:
    proc = run_cli(args, state)
    if proc.returncode != 0:
        raise AssertionError(f"rc={proc.returncode} stderr={proc.stderr!r}")
    return json.loads(proc.stdout)


def _ledger_path(state: Path) -> Path:
    return state / "project" / "skill-candidates.json"


def _write_ledger(state: Path, payload) -> None:
    p = _ledger_path(state)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def _cand(cid, workflows, dw, status="candidate", surface="unscoped", signature="sig"):
    return {"id": cid, "workflows": workflows, "distinct_workflows": dw, "status": status,
            "surface": surface, "signature": signature}


def _c1_c5() -> list:
    return [
        _cand("a1", [ALL, "wf-o"], 2, "candidate", "unscoped", "s1"),
        _cand("b2", [ALL], 1),
        _cand("c3", ["wf-o"], 1),
        _cand("d4", [ALL, "wf-o"], 2, "rejected"),
        "junk",
        _cand("e5", [ALL], True),
    ]


def test_rec_counts_all_fixture() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = _rec(["--wf", ALL, "--recurrence"], _state(tmp))
    rec = out["recurrence"]
    check("rec_counts_all_fixture.corpus", rec["corpus"] == {"workflows_scanned": 5, "skipped_unparseable": 0},
          f"corpus={rec['corpus']}")
    check("rec_counts_all_fixture.ids", [r["id"] for r in rec["signals"]] == [s["id"] for s in out["signals"]],
          f"ids={[r['id'] for r in rec['signals']]}")
    counts = {r["id"]: r["workflows"] for r in rec["signals"]}
    want = {sid: (2 if sid in ("remfix_cycles", "circuit_breaker_tripped") else 1) for sid in counts}
    check("rec_counts_all_fixture.counts", counts == want and len(counts) == 11, f"counts={counts}")


def test_rec_examples_exclude_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = _rec(["--wf", ALL, "--recurrence"], _state(tmp))
    rows = {r["id"]: r for r in out["recurrence"]["signals"]}
    check("rec_examples_exclude_target",
          rows["remfix_cycles"]["other_examples"] == [REMFIX]
          and rows["circuit_breaker_tripped"]["other_examples"] == [BREAKER]
          and all(ALL not in r["other_examples"] for r in rows.values()),
          f"rows={rows}")


def test_rec_examples_cap_3() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        remfix = json.loads((FIXTURES / "retro" / (REMFIX + ".json")).read_text())
        for i in range(1, 5):
            name = f"wf-x{i}"
            art = base_artifact(name)
            art["remediation_history"] = remfix["remediation_history"]
            art["updated_at"] = f"2026-10-07T0{i}:00:00Z"
            write_wf(state, name, art, '{"event":"workflow_started"}\n')
        out = _rec(["--wf", ALL, "--recurrence"], state)
    row = {r["id"]: r for r in out["recurrence"]["signals"]}["remfix_cycles"]
    check("rec_examples_cap_3", row["workflows"] == 6 and row["other_examples"] == ["wf-x4", "wf-x3", "wf-x2"],
          f"row={row}")


def test_rec_clean_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = _rec(["--wf", CLEAN, "--recurrence"], _state(tmp))
    rec = out["recurrence"]
    check("rec_clean_target", rec["signals"] == [] and rec["corpus"]["workflows_scanned"] == 5, f"rec={rec}")


def test_rec_skips_unparseable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        write_wf(state, "wf-bad", "{bad", None)
        write_wf(state, "wf-noev", base_artifact("wf-noev"), None)
        proc = run_cli(["--wf", ALL, "--recurrence"], state)
        out = json.loads(proc.stdout) if proc.returncode == 0 else {}
    corpus = out.get("recurrence", {}).get("corpus")
    check("rec_skips_unparseable", proc.returncode == 0 and corpus == {"workflows_scanned": 5, "skipped_unparseable": 2},
          f"rc={proc.returncode} corpus={corpus} stderr={proc.stderr!r}")


def test_rec_latest_ok() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = run_cli(["--latest", "--recurrence"], _state(tmp))
        out = json.loads(proc.stdout) if proc.returncode == 0 else {}
    check("rec_latest_ok",
          proc.returncode == 0 and out.get("workflow", {}).get("workflow_uuid") == ALL and "recurrence" in out,
          f"rc={proc.returncode} stderr={proc.stderr!r}")


def test_rec_list_usage_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = run_cli(["--list", "--recurrence"], _state(tmp))
    check("rec_list_usage_error",
          proc.returncode == 2 and proc.stdout == "" and "--recurrence cannot be used with --list" in proc.stderr,
          f"rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}")


def test_rec_target_still_fails_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        proc = run_cli(["--wf", "wf-missing", "--recurrence"], _state(tmp))
    expect_error("rec_target_still_fails_closed", proc, "artifact_not_found")


def test_rec_without_flag_unchanged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        out = _rec(["--wf", ALL], _state(tmp))
    check("rec_without_flag_unchanged", "recurrence" not in out, f"keys={list(out)}")


def test_rec_strip_equals_v1() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        without_flag = _rec(["--wf", ALL], state)
        with_flag = _rec(["--wf", ALL, "--recurrence"], state)
    stripped = {k: v for k, v in with_flag.items() if k != "recurrence"}
    check("rec_strip_equals_v1", stripped == without_flag and list(with_flag)[-1] == "recurrence"
          and with_flag["schema_version"] == 1, f"keys={list(with_flag)}")


def test_rec_deterministic() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        _write_ledger(state, _c1_c5())
        a = run_cli(["--wf", ALL, "--recurrence"], state)
        b = run_cli(["--wf", ALL, "--recurrence"], state)
    check("rec_deterministic", a.returncode == 0 and a.stdout == b.stdout and a.stdout != "",
          f"rc={a.returncode},{b.returncode}")


def test_ledger_absent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        out = _rec(["--wf", ALL, "--recurrence"], state)
        want = {"path": str(_ledger_path(state)), "status": "absent", "error": None, "candidates": [], "omitted": 0}
    check("ledger_absent", out["recurrence"]["ledger"] == want, f"ledger={out['recurrence']['ledger']}")


def test_ledger_matches_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        _write_ledger(state, {"schema_version": 1, "candidates": _c1_c5()})
        led = _rec(["--wf", ALL, "--recurrence"], state)["recurrence"]["ledger"]
    check("ledger_matches_target.status", led["status"] == "ok", f"status={led['status']} error={led['error']}")
    check("ledger_matches_target.order", [c["id"] for c in led["candidates"]] == ["a1", "d4", "b2", "e5"],
          f"ids={[c['id'] for c in led['candidates']]}")
    check("ledger_matches_target.eligible",
          [c["skill_distill_eligible"] for c in led["candidates"]] == [True, False, False, False],
          f"eligible={[c['skill_distill_eligible'] for c in led['candidates']]}")


def test_ledger_unreadable_json() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        plain = _rec(["--wf", ALL], state)
        _write_ledger(state, "{bad")
        proc = run_cli(["--wf", ALL, "--recurrence"], state)
        out = json.loads(proc.stdout) if proc.returncode == 0 else {}
    led = out.get("recurrence", {}).get("ledger", {})
    check("ledger_unreadable_json",
          proc.returncode == 0 and led.get("status") == "unreadable"
          and str(led.get("error")).startswith("not valid JSON") and led.get("candidates") == []
          and out.get("data_gaps") == plain["data_gaps"],
          f"rc={proc.returncode} ledger={led}")


def test_ledger_unreadable_shape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        _write_ledger(state, {"candidates": 5})
        led = _rec(["--wf", ALL, "--recurrence"], state)["recurrence"]["ledger"]
    check("ledger_unreadable_shape", led["status"] == "unreadable" and led["error"] == "candidates is not a list",
          f"ledger={led}")


def test_ledger_cap_10() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        _write_ledger(state, {"candidates": [_cand(f"id{i:02d}", [ALL], 1) for i in range(12)]})
        led = _rec(["--wf", ALL, "--recurrence"], state)["recurrence"]["ledger"]
    check("ledger_cap_10", len(led["candidates"]) == 10 and led["omitted"] == 2,
          f"len={len(led['candidates'])} omitted={led['omitted']}")


def test_ledger_signature_clipped() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        _write_ledger(state, {"candidates": [_cand("z9", [ALL], 1, signature="x" * 300)]})
        led = _rec(["--wf", ALL, "--recurrence"], state)["recurrence"]["ledger"]
    sig = led["candidates"][0]["signature"] if led["candidates"] else ""
    check("ledger_signature_clipped", len(sig) == 120 and sig.endswith("..."), f"len={len(sig)}")


def test_ledger_gate_parity() -> None:
    import craftflow_retro as R
    import craftflow_skill_ledger as L

    bad = []
    for dw in (0, 1, 2, 3):
        for st in ("candidate", "proposed", "promoted", "rejected"):
            got = R._skill_distill_eligible({"distinct_workflows": dw, "status": st})
            want = bool(L.gate_eligible({"distinct_workflows": dw})) and st == "candidate"
            if got != want:
                bad.append((dw, st, got, want))
    check("ledger_gate_parity", not bad, f"mismatches={bad}")


def test_rec_read_only_snapshot_and_no_lock() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state = _state(tmp)
        _write_ledger(state, {"schema_version": 1, "candidates": _c1_c5()})
        before = _snapshot(Path(tmp))
        procs = [run_cli(a, state) for a in (["--wf", ALL, "--recurrence"], ["--latest", "--recurrence"],
                                             ["--wf", CLEAN, "--recurrence"])]
        after = _snapshot(Path(tmp))
        lock_exists = (state / "project" / "skill-candidates.json.lock").exists()
    check("rec_read_only_snapshot", all(p.returncode == 0 for p in procs) and after == before,
          f"rcs={[p.returncode for p in procs]} changed={sorted(set(after) ^ set(before))}")
    check("rec_no_lock_created", all(p.returncode == 0 for p in procs) and not lock_exists,
          f"rcs={[p.returncode for p in procs]} lock_exists={lock_exists}")


def test_rec_no_ledger_module() -> None:
    src = (SCRIPTS / "craftflow_retro.py").read_text(encoding="utf-8")
    banned = ["import craftflow_skill_ledger", "from craftflow_skill_ledger", "fcntl", "import subprocess",
              '"w"', "'w'"]
    found = [b for b in banned if b in src]
    check("rec_no_ledger_module", not found, f"found banned tokens: {found}")


def main() -> int:
    tests = [
        test_manifest_valid_for_runner,
        test_lvr4_links,
        test_rec_counts_all_fixture,
        test_rec_examples_exclude_target,
        test_rec_examples_cap_3,
        test_rec_clean_target,
        test_rec_skips_unparseable,
        test_rec_latest_ok,
        test_rec_list_usage_error,
        test_rec_target_still_fails_closed,
        test_rec_without_flag_unchanged,
        test_rec_strip_equals_v1,
        test_rec_deterministic,
        test_ledger_absent,
        test_ledger_matches_target,
        test_ledger_unreadable_json,
        test_ledger_unreadable_shape,
        test_ledger_cap_10,
        test_ledger_signature_clipped,
        test_ledger_gate_parity,
        test_rec_read_only_snapshot_and_no_lock,
        test_rec_no_ledger_module,
    ]
    for t in tests:
        try:
            t()
        except Exception as exc:  # a crashing test is a failing test, never a silent pass
            fail(t.__name__, f"{type(exc).__name__}: {exc}")
    print(f"\nResults: {_passes} passed, {len(_errors)} failed")
    for e in _errors:
        print(e)
    print("PASS" if not _errors else "FAIL")
    return 0 if not _errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
