#!/usr/bin/env python3
"""Standalone CLI -- Jev (TypeSafe AI) assisted decision for the router's
`1a-SCOPE` REM-FIX gate (references/remediation-and-research.md, and its
escalated-path twin in references/build-workflow.md, which delegates to the
same router-doc procedure rather than calling this script a second time).
Invoked inline by the router, never a hook. Never raises; always exits 0 and
prints exactly one line of JSON. See
docs/plans/2026-09-24-jev-remfix-scope-decision-design.md and
docs/plans/2026-09-24-jev-assisted-rem-scope-decision-plan.md.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from craftflow_hooklib import now_iso, plugin_config_dir, state_root
from craftflow_jev_client import call as jev_call
from craftflow_jev_config import api_key, is_active, load_config
from craftflow_jev_heuristic import classify_remfix_scope

_VALID_CHOICES = ("critical_only", "all_issues")


def build_state(critical: List[str], high: List[str], *, max_chars: int) -> Dict[str, Any]:
    """Pure. Caps combined critical/high text at max_chars (split evenly),
    same truncation-flag idiom as craftflow_jev_prompt_hint.build_state()."""
    budget = max(max_chars // 2, 1)
    critical_text = "\n".join(critical)
    high_text = "\n".join(high)
    truncated = len(critical_text) > budget or len(high_text) > budget
    return {
        "critical_issues": critical_text[:budget],
        "high_issues": high_text[:budget],
        "critical_count": len(critical),
        "high_count": len(high),
        "truncated": truncated,
    }


def build_questions() -> Dict[str, Dict[str, Any]]:
    """Pure. One batched choice question -- same shape convention as
    craftflow_jev_prompt_hint.build_questions()."""
    return {
        "scope": {
            "type": "choice",
            "instructions": (
                "Given the CRITICAL and HIGH severity issues found in this BUILD phase "
                "(see `critical_issues`/`high_issues`), should remediation fix only the "
                "CRITICAL issue(s), or all issues (CRITICAL + HIGH)? Prefer `critical_only` "
                "unless the HIGH issue(s) are clearly related to, or would block verifying, "
                "the CRITICAL fix."
            ),
            "criteria": {
                "critical_only": "Fix only the CRITICAL issue(s) now; defer HIGH issue(s) to a follow-up.",
                "all_issues": "Fix both CRITICAL and HIGH issue(s) in this remediation.",
            },
        }
    }


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def decide(mode: str, answer: Optional[Dict[str, Any]], threshold: float) -> Tuple[str, Optional[str], Optional[float]]:
    """Pure. `mode` is "audit" or "advise" only -- caller resolves "off" before
    ever calling this (see main()). Returns (decision, choice, confidence)
    where decision in {"logged","no_decision","below_threshold","applied"}.
    Never raises -- P2 (see plan's Provable Properties). NOTE: this function
    has no notion of telemetry persistence -- main() downgrades "applied" to
    "below_threshold" if the events.jsonl append fails (P1/P6)."""
    choice = answer.get("choice") if isinstance(answer, dict) else None
    confidence = answer.get("confidence") if isinstance(answer, dict) else None
    valid = (
        isinstance(choice, str)
        and choice in _VALID_CHOICES
        and _is_number(confidence)
        and confidence == confidence  # reject NaN (self-inequality)
        and 0.0 <= confidence <= 1.0
    )
    if not valid:
        return "no_decision", None, None
    confidence = float(confidence)
    if mode == "audit":
        return "logged", choice, confidence
    if confidence >= threshold:
        return "applied", choice, confidence
    return "below_threshold", choice, confidence


def telemetry_row(
    *, decision: str, choice: Optional[str], confidence: Optional[float],
    result: Optional[Dict[str, Any]], mode: str, model: str,
    workflow_uuid: Optional[str], critical_count: int, high_count: int,
) -> Dict[str, Any]:
    """Pure. One row per script invocation that actually attempted a call
    (mode != "off" and active). Never includes raw finding text -- matches
    the "never includes prompt text" privacy rule craftflow_jev_prompt_hint.py
    already applies to routing/skill rows. `decision` here reflects the
    *attempted* decision (from decide()); main() may still print a
    downgraded decision to stdout if this row itself fails to persist --
    see Task 3.3."""
    result = result if isinstance(result, dict) else {}
    heuristic = classify_remfix_scope()
    return {
        "ts": now_iso(),
        "call_id": uuid.uuid4().hex,
        "workflow_uuid": workflow_uuid,
        "feature": "remfix_scope",
        "mode": mode,
        "model": result.get("model", model),
        "latency_ms": result.get("latency_ms"),
        "cache_hit": result.get("cache_hit", False),
        "usage": result.get("usage"),
        "answers": {"choice": choice, "confidence": confidence},
        "confidence": confidence if confidence is not None else 0.0,
        "heuristic_result": heuristic,
        "agree": choice == heuristic,
        "decision": decision,
        "critical_count": critical_count,
        "high_count": high_count,
    }


def _append_event(path: Path, row: Dict[str, Any]) -> bool:
    """Never raises. Returns True only if the row was actually written --
    the caller (main()) uses this to decide whether an "applied" decision
    may be printed at all (P1/P6: no auto-apply without a persisted audit
    row). A write failure here must not crash the script, but it must not
    be silently reported as success either."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=True) + "\n")
        return True
    except Exception:
        return False


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="craftflow_jev_remfix_scope.py",
        description="Jev-assisted decision for the router's 1a-SCOPE REM-FIX scope gate.",
    )
    parser.add_argument("--critical", action="append", default=[], help="One CRITICAL finding summary line; repeatable.")
    parser.add_argument("--high", action="append", default=[], help="One HIGH finding summary line; repeatable.")
    parser.add_argument("--workflow-uuid", default=None, help="Workflow correlation id for the telemetry row.")
    parser.add_argument("--config", default=None, help="Override config/jev.json path (default: plugin config dir).")
    parser.add_argument("--state-dir", default=None, help="Override state dir for events.jsonl (default: state_root()).")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = build_arg_parser().parse_args(argv)
        config_path = Path(args.config) if args.config else plugin_config_dir() / "jev.json"
        state_dir = Path(args.state_dir) if args.state_dir else state_root()
        cfg, _decisions = load_config(config_path)
        env = os.environ
        mode = (cfg.get("features") or {}).get("remediationScope", "off")

        if mode == "off" or not is_active(cfg, env):
            print(json.dumps({"decision": "off", "choice": None, "confidence": None}))
            return 0

        if not args.critical and not args.high:
            print(json.dumps({"decision": "no_decision", "choice": None, "confidence": None}))
            return 0

        state = build_state(args.critical, args.high, max_chars=cfg["maxStateChars"])
        questions = build_questions()
        result = jev_call(
            state, questions,
            api_key=api_key(env), model=cfg["model"], timeout=cfg["timeoutSeconds"], cache_dir=None,
        )
        answer = None
        if isinstance(result, dict) and isinstance(result.get("answers"), dict):
            answer = result["answers"].get("scope")
        threshold = (cfg.get("thresholds") or {}).get("remediationScope", 0.85)
        decision, choice, confidence = decide(mode, answer, threshold)

        row = telemetry_row(
            decision=decision, choice=choice, confidence=confidence, result=result,
            mode=mode, model=cfg["model"], workflow_uuid=args.workflow_uuid,
            critical_count=len(args.critical), high_count=len(args.high),
        )
        persisted = _append_event(state_dir / "jev" / "events.jsonl", row)

        # P1/P6: "applied" is only ever printed to the router if the audit row
        # backing it actually landed on disk. A confidence-qualifying answer
        # whose telemetry write failed is reported as below_threshold instead
        # -- never silently promoted to applied with no evidence behind it.
        if decision == "applied" and not persisted:
            decision = "below_threshold"

        print(json.dumps({"decision": decision, "choice": choice, "confidence": confidence}))
        return 0
    except Exception:
        print(json.dumps({"decision": "no_decision", "choice": None, "confidence": None}))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
