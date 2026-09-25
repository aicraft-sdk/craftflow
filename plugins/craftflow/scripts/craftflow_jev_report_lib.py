#!/usr/bin/env python3
"""Tiny shared pure-helper module for craftflow_jev_report.py and
craftflow_jev_ab_report.py -- holds only _manifest_by_call_id (the
replay_manifest.jsonl -> {call_id: workflow_type} join both scripts' own
ground-truth accuracy computations need).

Extracted here instead of letting the two sibling report scripts import
from each other in both directions: craftflow_jev_ab_report.py already
imports _is_number/_percentile/_usage_tokens FROM craftflow_jev_report.py.
Adding a second, opposite-direction import (craftflow_jev_report.py
importing FROM craftflow_jev_ab_report.py) would make each module start
importing the other while it is itself only partially initialized --
whichever module is imported first, Python raises `ImportError: cannot
import name ... from partially initialized module ... (most likely due to
a circular import)` once the partner module reaches its own import
statement. A one-way import from this lib module avoids that failure mode
entirely.

Import-cheap: no file reads, env lookups, network, or argparse parsing at
import time -- pure function only. Auto-discovered by
test_craftflow_jev_prompt_hint.py's test_new_modules_are_import_cheap glob
(craftflow_jev_*.py), so it must stay side-effect-free at import time.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _manifest_by_call_id(manifest_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    lookup: Dict[str, Any] = {}
    for row in manifest_rows:
        call_id = row.get("call_id")
        if isinstance(call_id, str) and call_id:
            lookup[call_id] = row.get("workflow_type")
    return lookup
