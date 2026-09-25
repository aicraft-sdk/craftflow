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
