#!/usr/bin/env python3
"""Tests for craftflow_jev_prompt_hint.py (Phase 1: inert skeleton + registration).

Run: python3 tests/fixtures/test_craftflow_jev_prompt_hint.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_prompt_hint import (  # noqa: E402
    TELEMETRY_KEYS,
    WORKFLOWS,
    build_questions,
    build_roster,
    build_state,
    gate_answers,
    render_block,
    telemetry_rows,
)

TELEMETRY_KEYS_SKILL = set(TELEMETRY_KEYS)
TELEMETRY_KEYS_ROUTING = set(TELEMETRY_KEYS) | {"agree_risk"}

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def run_hook(payload: dict, env: dict) -> tuple[int, str, str]:
    """Run the real craftflow_jev_prompt_hint.py hook as a subprocess.

    Same technique as craftflow_hook_unit_tests.py:57-67's run_hook(), copied
    here (not imported) per the plan's "do not import the monolith" rule.
    `env` fully controls CLAUDE_PROJECT_DIR/CLAUDE_PLUGIN_ROOT/TYPESAFE_API_KEY;
    TYPESAFE_API_KEY is stripped from the inherited environment unless `env`
    re-adds it explicitly.
    """
    merged_env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
    merged_env.update(env)
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "craftflow_jev_prompt_hint.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=merged_env,
    )
    return result.returncode, result.stdout.strip(), result.stderr


def _setup(enabled: bool) -> tuple[tempfile.TemporaryDirectory, Path, Path, dict]:
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    project = root / "project"
    plugin = root / "plugin"
    project.mkdir(parents=True)
    (plugin / "config").mkdir(parents=True)
    cfg = json.loads((PLUGIN_ROOT / "config" / "jev.json").read_text())
    if enabled:
        cfg["enabled"] = True
    (plugin / "config" / "jev.json").write_text(json.dumps(cfg))
    env = {"CLAUDE_PROJECT_DIR": str(project), "CLAUDE_PLUGIN_ROOT": str(plugin)}
    return tmp, project, plugin, env


def test_disabled_config_exits_silently_and_writes_nothing() -> None:
    tmp, project, plugin, env = _setup(enabled=False)
    with tmp:
        code, out, err = run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "fix the bug"}, env)
        jev_dir = project / ".craftflow/state/jev"
        log_path = project / ".craftflow/state/craftflow-hook-events.log"
        log_ok = (not log_path.exists()) or ("jev" not in log_path.read_text())
        if (code, out) == (0, "") and not jev_dir.exists() and log_ok:
            ok("disabled config exits silently and writes nothing")
        else:
            fail(
                "disabled-config-silent",
                f"code={code} out={out!r} err={err!r} jev_dir_exists={jev_dir.exists()} log_ok={log_ok}",
            )


def test_corrupt_config_file_logs_config_unparseable_but_stays_silent() -> None:
    tmp, project, plugin, env = _setup(enabled=False)
    with tmp:
        (plugin / "config" / "jev.json").write_text("{not json")
        code, out, err = run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "fix the bug"}, env)
        log_path = project / ".craftflow/state/craftflow-hook-events.log"
        log_text = log_path.read_text() if log_path.exists() else ""
        count = log_text.count('"decision": "config_unparseable"')
        if (code, out) == (0, "") and count == 1:
            ok("corrupt config logs config_unparseable exactly once but stays silent")
        else:
            fail(
                "corrupt-config-logs-once",
                f"code={code} out={out!r} err={err!r} count={count} log={log_text!r}",
            )


def test_enabled_but_no_key_exits_silently_no_network() -> None:
    tmp, project, plugin, env = _setup(enabled=True)
    with tmp:
        env_no_key = dict(env)
        # DD-14 loopback override: if any code path DID reach the network it would hit a
        # refused local port and log jev_call_failed -- the assertion below proves no such
        # line exists, i.e. is_active() correctly short-circuited before any network call.
        env_no_key["CRAFTFLOW_JEV_ENDPOINT"] = "http://127.0.0.1:9/"
        code, out, err = run_hook({"hook_event_name": "UserPromptSubmit", "prompt": "fix the bug"}, env_no_key)
        jev_dir = project / ".craftflow/state/jev"
        log_path = project / ".craftflow/state/craftflow-hook-events.log"
        log_ok = (not log_path.exists()) or ("jev_call_failed" not in log_path.read_text())
        if (code, out) == (0, "") and not jev_dir.exists() and log_ok:
            ok("enabled but no API key exits silently, no network attempted")
        else:
            fail(
                "enabled-no-key-silent",
                f"code={code} out={out!r} err={err!r} jev_dir_exists={jev_dir.exists()} log_ok={log_ok}",
            )


def test_hooks_json_registers_userpromptsubmit_with_5s_timeout() -> None:
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())
    entries = hooks["hooks"].get("UserPromptSubmit", [])
    cmds = [h for e in entries for h in e.get("hooks", [])]
    matches = [h for h in cmds if "craftflow_jev_prompt_hint.py" in h.get("command", "") and h.get("timeout") == 5]
    if matches:
        ok("hooks.json registers UserPromptSubmit -> craftflow_jev_prompt_hint.py with timeout 5")
    else:
        fail("hooks-json-registration", f"entries={entries!r}")


def test_contract_doc_discloses_hookeventname_carveout() -> None:
    hooklib = (PLUGIN_ROOT / "scripts" / "craftflow_hooklib.py").read_text()
    literal = hooklib.split("HookEventName = Literal[", 1)[1].split("]", 1)[0]
    members = re.findall(r'"([A-Za-z]+)"', literal)
    doc = (PLUGIN_ROOT / "docs" / "craftflow-event-contract.md").read_text()
    checks = (
        len(members) == 10,
        "UserPromptSubmit" not in members,
        "| `UserPromptSubmit` |" in doc,
        "`UserPromptSubmit` is intentionally absent from `HookEventName`" in doc,
    )
    if all(checks):
        ok("contract doc discloses the HookEventName 11-vs-10 carve-out")
    else:
        fail("contract-doc-carveout", f"checks={checks!r} members={members!r}")


def test_new_modules_are_import_cheap() -> None:
    # DD-12a: selfcheck imports every sibling under a 5s sweep budget; each new module must
    # import in well under 0.5s with no side effects (no files created, nothing on stdout).
    # Discovered by glob, so later phases add modules without touching this test.
    failures = []
    for mod_path in sorted(SCRIPTS.glob("craftflow_jev_*.py")):
        mod = mod_path.stem
        with tempfile.TemporaryDirectory() as tmp:
            t0 = time.monotonic()
            env = {k: v for k, v in os.environ.items() if k != "TYPESAFE_API_KEY"}
            env["PYTHONPATH"] = str(SCRIPTS)
            proc = subprocess.run(
                [sys.executable, "-c", f"import {mod}"],
                cwd=tmp,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            elapsed = time.monotonic() - t0
            listing = os.listdir(tmp)
            if proc.returncode != 0 or proc.stdout != "" or elapsed >= 0.5 or listing != []:
                failures.append((mod, proc.returncode, proc.stdout, proc.stderr, elapsed, listing))
    if not failures:
        ok("all craftflow_jev_*.py modules import cheaply (no side effects, <0.5s)")
    else:
        fail("import-cheap", f"failures={failures!r}")


# ---------------------------------------------------------------------------
# Task 3.1: pure builders -- roster, state, questions, gating, rendering,
# telemetry rows
# ---------------------------------------------------------------------------


def advise_cfg(thresholds: dict) -> dict:
    return {
        "features": {"routingHint": "advise", "skillHint": "advise"},
        "thresholds": thresholds,
    }


def audit_cfg(thresholds: dict) -> dict:
    return {
        "features": {"routingHint": "audit", "skillHint": "audit"},
        "thresholds": thresholds,
    }


def test_roster_excludes_internal_and_host_skills_and_appends_none() -> None:
    plugin = [
        ("architecture-patterns", "Internal skill. Use craftflow-router for all development tasks."),
        ("debugging-patterns", "Use when a bug, flaky test, or runtime/build failure needs root-cause tracing."),
        ("craftflow-router", "THE ONLY ENTRY POINT FOR CRAFTFLOW."),
        ("status", "Read-only status of the current workflow."),
    ]
    roster = build_roster(project=[], plugin=plugin, hint_bullets=[])
    ids = [r["id"] for r in roster]
    if ids == ["craftflow:debugging-patterns", "none"]:
        ok("roster excludes Internal-skill and host/ops skills, appends none")
    else:
        fail("roster-excludes-internal-host", f"ids={ids!r}")


def test_roster_project_first_then_plugin_sorted_then_hints_deduped() -> None:
    project = [("zeta", "d-zeta"), ("alpha", "d-alpha")]
    plugin = [("gamma", "Use gamma."), ("beta", "Use beta.")]
    hint_bullets = ["craftflow:beta", "alpha", "hintskill"]
    roster = build_roster(project=project, plugin=plugin, hint_bullets=hint_bullets)
    ids = [r["id"] for r in roster]
    expected = ["alpha", "zeta", "craftflow:beta", "craftflow:gamma", "hintskill", "none"]
    if ids == expected:
        ok("roster orders project-first, plugin-sorted, then deduped hints, then none")
    else:
        fail("roster-order-dedup", f"ids={ids!r} expected={expected!r}")


def test_roster_cap_255_keeps_project_and_reports_truncation() -> None:
    plugin = [(f"s{i:03d}", "Use when needed.") for i in range(300)]
    roster, truncated = build_roster(
        project=[("mine", "desc")], plugin=plugin, hint_bullets=[], with_truncation_flag=True
    )
    if len(roster) == 255 and roster[0]["id"] == "mine" and roster[-1]["id"] == "none" and truncated:
        ok("roster caps at 255, keeps project entries first, reports truncation")
    else:
        fail(
            "roster-cap-255",
            f"len={len(roster)} first={roster[0]['id']!r} last={roster[-1]['id']!r} truncated={truncated}",
        )


def test_state_caps_prompt_and_uses_basename() -> None:
    st = build_state("x" * 5000, cwd="/Users/me/proj", workflow_type="PLAN", max_chars=4000)
    if (
        len(st["prompt"]) == 4000
        and st["prompt_truncated"] is True
        and st["project"] == "proj"
        and st["active_workflow_type"] == "PLAN"
    ):
        ok("build_state caps prompt to max_chars and uses cwd basename")
    else:
        fail("state-caps-basename", f"st={st!r}")


def test_questions_have_four_ids_and_workflow_criteria_from_table() -> None:
    roster = build_roster(project=[], plugin=[], hint_bullets=[])
    q = build_questions(roster)
    ids_ok = set(q) == {"workflow", "risk_full_chain", "needs_skill", "skill"}
    workflow_ok = set(q["workflow"]["criteria"]) == set(WORKFLOWS)
    skill_ok = "none" in q["skill"]["criteria"]
    if ids_ok and workflow_ok and skill_ok:
        ok("build_questions has 4 ids with workflow criteria from WORKFLOWS and none in skill criteria")
    else:
        fail("questions-shape", f"q={q!r}")


_GATE_ANSWERS = {
    "workflow": {"type": "choice", "choice": "DEBUG", "confidence": 0.85, "probabilities": {}},
    "risk_full_chain": {"type": "noul", "noul": 0.12},
    "needs_skill": {"type": "noul", "noul": 0.9},
    "skill": {"type": "choice", "choice": "craftflow:debugging-patterns", "confidence": 0.7, "probabilities": {}},
}
_GATE_ROSTER_IDS = {"craftflow:debugging-patterns", "none"}


def test_gate_threshold_boundaries() -> None:
    cfg = advise_cfg(thresholds={"routing": 0.85, "skill": 0.7})
    lines = gate_answers(_GATE_ANSWERS, cfg, _GATE_ROSTER_IDS)
    expected = [
        "workflow: DEBUG (confidence 0.85) | risk_full_chain: 0.12",
        "skill: craftflow:debugging-patterns (0.70)",
    ]
    cfg2 = advise_cfg(thresholds={"routing": 0.851, "skill": 0.701})
    lines2 = gate_answers(_GATE_ANSWERS, cfg2, _GATE_ROSTER_IDS)
    if lines == expected and lines2 == []:
        ok("gate_answers injects at/above threshold (equality injects) and withholds just below")
    else:
        fail("gate-threshold-boundaries", f"lines={lines!r} lines2={lines2!r}")


def test_gate_never_injects_in_audit_and_ignores_none_or_unknown_skill() -> None:
    cfg_audit = audit_cfg(thresholds={"routing": 0.85, "skill": 0.7})
    lines_audit = gate_answers(_GATE_ANSWERS, cfg_audit, _GATE_ROSTER_IDS)

    answers_none_skill = dict(_GATE_ANSWERS, skill={"type": "choice", "choice": "none", "confidence": 0.99})
    cfg = advise_cfg(thresholds={"routing": 0.85, "skill": 0.7})
    lines_none_skill = gate_answers(answers_none_skill, cfg, _GATE_ROSTER_IDS)

    answers_unknown_skill = dict(
        _GATE_ANSWERS, skill={"type": "choice", "choice": "craftflow:does-not-exist", "confidence": 0.99}
    )
    lines_unknown_skill = gate_answers(answers_unknown_skill, cfg, _GATE_ROSTER_IDS)

    if lines_audit == [] and all("skill:" not in ln for ln in lines_none_skill) and all(
        "skill:" not in ln for ln in lines_unknown_skill
    ):
        ok("gate_answers never injects in audit mode, ignores skill=none and unknown-roster skill")
    else:
        fail(
            "gate-audit-none-unknown",
            f"lines_audit={lines_audit!r} lines_none_skill={lines_none_skill!r} "
            f"lines_unknown_skill={lines_unknown_skill!r}",
        )


def test_gate_skips_malformed_workflow_and_degrades_malformed_risk() -> None:
    cfg = advise_cfg(thresholds={"routing": 0.85, "skill": 0.7})

    missing_workflow = {k: v for k, v in _GATE_ANSWERS.items() if k != "workflow"}
    lines_missing = gate_answers(missing_workflow, cfg, _GATE_ROSTER_IDS)

    none_confidence = dict(_GATE_ANSWERS, workflow=dict(_GATE_ANSWERS["workflow"], confidence=None))
    lines_none_conf = gate_answers(none_confidence, cfg, _GATE_ROSTER_IDS)

    bad_risk = dict(_GATE_ANSWERS, risk_full_chain={"type": "noul", "noul": "high"})
    lines_bad_risk = gate_answers(bad_risk, cfg, _GATE_ROSTER_IDS)

    routing_lines_bad_risk = [ln for ln in lines_bad_risk if ln.startswith("workflow:")]
    if (
        all("workflow:" not in ln for ln in lines_missing)
        and all("workflow:" not in ln for ln in lines_none_conf)
        and routing_lines_bad_risk == ["workflow: DEBUG (confidence 0.85)"]
    ):
        ok("gate_answers skips malformed workflow answers, degrades a malformed risk value gracefully")
    else:
        fail(
            "gate-malformed-answers",
            f"lines_missing={lines_missing!r} lines_none_conf={lines_none_conf!r} "
            f"routing_lines_bad_risk={routing_lines_bad_risk!r}",
        )


def test_render_block_is_byte_stable() -> None:
    rendered = render_block(["workflow: DEBUG (confidence 0.91) | risk_full_chain: 0.12"], "jev-latest")
    expected = (
        '<craftflow_routing_hint source="jev" model="jev-latest">\n'
        "workflow: DEBUG (confidence 0.91) | risk_full_chain: 0.12\n"
        "Advisory. ERROR keyword signals still take precedence. Ignore if it does not fit the request.\n"
        "</craftflow_routing_hint>"
    )
    if rendered == expected:
        ok("render_block is byte-stable")
    else:
        fail("render-block-byte-stable", f"rendered={rendered!r}")


def test_telemetry_rows_exact_keys_and_agreement() -> None:
    result = {
        "answers": _GATE_ANSWERS,
        "usage": {"tokens": 42},
        "model": "jev-latest",
        "latency_ms": 123,
        "cache_hit": False,
    }
    heuristic = {"workflow": "DEBUG", "risk_signals": [], "skill": "none"}
    cfg = advise_cfg(thresholds={"routing": 0.85, "skill": 0.7})
    meta = {
        "call_id": "call-1",
        "session_id": "sess-1",
        "prompt_chars": 12,
        "prompt_truncated": False,
        "roster_size": 17,
        "injected": True,
    }
    rows = telemetry_rows(result, heuristic, cfg, meta)
    checks = (
        len(rows) == 2,
        set(rows[0]) == TELEMETRY_KEYS_ROUTING,
        set(rows[1]) == TELEMETRY_KEYS_SKILL,
        rows[0]["feature"] == "routing",
        rows[0]["confidence"] == 0.85,
        rows[0]["heuristic_result"] == {"workflow": "DEBUG", "risk_signals": []},
        rows[0]["agree"] is True,
        rows[0]["agree_risk"] is True,
        rows[1]["feature"] == "skill",
        rows[1]["heuristic_result"] == "none",
        rows[1]["agree"] is False,
        "prompt" not in rows[0],
        "prompt" not in rows[1],
    )
    if all(checks):
        ok("telemetry_rows has exact key schema and correct agreement flags")
    else:
        fail("telemetry-rows-schema", f"checks={checks!r} rows={rows!r}")


def main() -> int:
    print("test_craftflow_jev_prompt_hint: running")
    test_disabled_config_exits_silently_and_writes_nothing()
    test_corrupt_config_file_logs_config_unparseable_but_stays_silent()
    test_enabled_but_no_key_exits_silently_no_network()
    test_hooks_json_registers_userpromptsubmit_with_5s_timeout()
    test_contract_doc_discloses_hookeventname_carveout()
    test_new_modules_are_import_cheap()

    test_roster_excludes_internal_and_host_skills_and_appends_none()
    test_roster_project_first_then_plugin_sorted_then_hints_deduped()
    test_roster_cap_255_keeps_project_and_reports_truncation()
    test_state_caps_prompt_and_uses_basename()
    test_questions_have_four_ids_and_workflow_criteria_from_table()
    test_gate_threshold_boundaries()
    test_gate_never_injects_in_audit_and_ignores_none_or_unknown_skill()
    test_gate_skips_malformed_workflow_and_degrades_malformed_risk()
    test_render_block_is_byte_stable()
    test_telemetry_rows_exact_keys_and_agreement()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
