#!/usr/bin/env python3
"""Strict-input guards for the router read-set measurer and projector (P2 REM-FIX 1).

Everything here fails closed: an unknown phase id, condition name or flag value, a CR byte or invalid UTF-8 in a router file, and an
unanchored cut-plan row without a size estimate are errors (the CLI exits 2) instead of a silent "false", "nothing selected" or
"0 bytes". Also the byte-exact reader (UTF-8 decoded explicitly, never through the locale) and the disclosure of unanchored rows
(`unanchored` list and the net `bias` summary of router-projection.json). Python 3.9 compatible.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from craftflow_router_readset_core import ReadsetError

ALL_PHASES = [f"P{i}" for i in range(2, 14)]
ROWLESS_PHASES = ("P2C",)  # a phase PR may append its id to merged_phases without owning cut-plan rows
FLAG_NAMES = ("difference_17", "live_verified_enterplanmode")  # the keys of router-budget.json `conditions`
CONDITION_NAMES = ("always",) + FLAG_NAMES
WORKFLOWS = ("BUILD", "PLAN", "DEBUG", "REVIEW")
KERNEL = "skills/craftflow-router/SKILL.md"


class ProjectionError(ReadsetError):
    """An anchor matched zero or several places, or an input is malformed or unknown: fail closed."""


# ---------------------------------------------------------------------------------------------- inputs


def check_phase_ids(ids: Iterable, where: str, rowless_ok: bool) -> None:
    ok = set(ALL_PHASES) | (set(ROWLESS_PHASES) if rowless_ok else set())
    bad = sorted({str(p) for p in ids if not isinstance(p, str) or p not in ok})
    if bad:
        raise ProjectionError(f"{where}: unknown phase id(s) {bad} (known: {ALL_PHASES[0]}-{ALL_PHASES[-1]}"
                              f"{', ' + ', '.join(ROWLESS_PHASES) if rowless_ok else ''})")


def check_flags(flags) -> Dict[str, bool]:
    """Condition flags must be a dict of known names to real booleans ("false" is a truthy string)."""
    if not isinstance(flags, dict):
        raise ProjectionError(f"conditions must be an object of flag name -> boolean, got {type(flags).__name__}")
    unknown = sorted(set(flags) - set(FLAG_NAMES))
    if unknown:
        raise ProjectionError(f"unknown condition flag(s) {unknown} (known: {list(FLAG_NAMES)})")
    notbool = sorted(k for k, v in flags.items() if not isinstance(v, bool))
    if notbool:
        raise ProjectionError(f"condition flag(s) {notbool} must be true or false (JSON booleans), not {[flags[k] for k in notbool]!r}")
    return dict(flags)


def check_row_conditions(rows: Iterable[dict]) -> None:
    for r in rows:
        if r.get("condition") not in CONDITION_NAMES:
            raise ProjectionError(f"{r.get('id')}: unknown condition {r.get('condition')!r} "
                                  f"(a row's condition is one of {list(CONDITION_NAMES)}); an unknown name used to read as false")
        if "feasibility" in r and not (isinstance(r["feasibility"], str) and r["feasibility"].strip()):
            raise ProjectionError(f"{r.get('id')}: feasibility must be a non-empty string (the reason the row cannot pass the P0 reach rule)")


REACH_AMENDMENTS = (None, "D17-B")  # router-budget.json `reach_amendment`: null until David records the P2C decision


def check_reach_amendment(value) -> Optional[str]:
    if value not in REACH_AMENDMENTS or isinstance(value, bool):
        raise ProjectionError(f"reach_amendment must be one of {list(REACH_AMENDMENTS)}, got {value!r}")
    return value


def tier_row_sets(plain: List[dict], cont: List[dict], flags: Dict[str, bool], reach_amendment: Optional[str] = None) -> Dict[str, List[dict]]:
    """The row sets behind every tier, in application order. A row tagged `feasibility` (it needs a reach-model amendment, so the
    P0 tooling would refuse it) is left out of the default sets and appears only in the `_if_reach_amended` ones. `effective` (the
    state the budget records) drops them too, unless the budget records `reach_amendment: "D17-B"`; `effective_without_infeasible_rows`
    always drops them."""
    feasible = [r for r in plain if not r.get("feasibility")]
    on = lambda r: r["condition"] == "always" or bool(flags.get(r["condition"]))  # noqa: E731
    eff = [r for r in (plain if check_reach_amendment(reach_amendment) else feasible) if on(r)]
    return {"guaranteed": [r for r in feasible if r["condition"] == "always"], "conditional": feasible,
            "with_contingencies": feasible + cont, "guaranteed_if_reach_amended": [r for r in plain if r["condition"] == "always"],
            "conditional_if_reach_amended": plain, "with_contingencies_if_reach_amended": plain + cont,
            "effective": eff, "effective_without_infeasible_rows": [r for r in feasible if on(r)]}


def parse_conditions(text: Optional[str]) -> Dict[str, bool]:
    """`--conditions difference_17=true,live_verified_enterplanmode=false`: only true|false, only known names, each once."""
    out: Dict[str, bool] = {}
    for item in [x for x in (text or "").split(",") if x.strip()]:
        key, sep, val = item.partition("=")
        key, val = key.strip(), val.strip()
        if not sep or val not in ("true", "false"):
            raise ProjectionError(f"--conditions item {item!r} must look like name=true or name=false")
        if key in out:
            raise ProjectionError(f"--conditions names {key} twice")
        out[key] = val == "true"
    return check_flags(out)


# ---------------------------------------------------------------------------------------------- byte-exact reads


def _decode(raw: bytes, label: str) -> str:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReadsetError(f"{label} is not valid UTF-8: {exc}") from exc
    if "\r" in text:
        raise ReadsetError(f"{label} contains CR bytes; the measurer counts raw bytes and does not normalise line endings")
    return text


def _git_bytes(root, *args: str) -> bytes:
    res = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if res.returncode != 0:
        raise ReadsetError(f"git {' '.join(args[:2])} failed: {res.stderr.decode('utf-8', 'replace').strip()}")
    return res.stdout


def read_exact(plugin_root, rel: str, ref: Optional[str] = None) -> str:
    """Router file text from the working tree or `ref`: read as bytes, decoded as UTF-8 (not the locale), no newline translation."""
    label = f"{rel} at {ref or 'working tree'}"
    if ref:
        return _decode(_git_bytes(plugin_root, "show", f"{ref}:./{rel}"), label)
    try:
        return _decode((Path(plugin_root) / rel).read_bytes(), label)
    except OSError as exc:
        raise ReadsetError(f"cannot read {label}: {exc}") from exc


def ls_tree(plugin_root, ref: str, prefix: str) -> List[str]:
    out = _git_bytes(plugin_root, "ls-tree", "-r", "--name-only", "-z", ref, "--", prefix)
    return sorted(n for n in out.decode("utf-8").split("\0") if n)


# ---------------------------------------------------------------------------------------------- unanchored rows and bias


def _unit(r: dict) -> str:
    return "chars" if r.get("op") == "descriptions" else "bytes"


def unanchored_report(rows: Iterable[dict], descriptions: Dict[str, str]) -> List[dict]:
    """One entry per `needs_anchor` row: direction (add|cut) and an estimated size, so the projection says which way it is biased.
    A description row is sized from its cap (a cut of the current length down to `max_chars`); every other row must carry
    `direction`, a positive integer `est_bytes` and optionally `affects` (workflow names, default all four)."""
    out = []
    for r in sorted((x for x in rows if x.get("status") == "needs_anchor"), key=lambda x: x["id"]):
        m = re.fullmatch(r"skills/([^/]+)/SKILL\.md", r.get("file") or "")
        if r.get("op") == "descriptions" and m:
            direction, est = "cut", max(0, len(descriptions.get(m.group(1), "")) - int(r.get("max_chars") or 0))
            affects = []
        else:
            direction, est, affects = r.get("direction"), r.get("est_bytes"), r.get("affects", list(WORKFLOWS))
            if direction not in ("add", "cut") or not isinstance(est, int) or isinstance(est, bool) or est < 0:
                raise ProjectionError(f"{r['id']}: a needs_anchor row must state direction add|cut and a non-negative integer est_bytes "
                                      "(its bytes are missing from every tier, so the size of the miss has to be disclosed; 0 means "
                                      "the row was evaluated and has no bytes to give, with the reason in its note)")
            if not isinstance(affects, list) or set(affects) - set(WORKFLOWS):
                raise ProjectionError(f"{r['id']}: affects must list workflows from {list(WORKFLOWS)}")
        out.append({"id": r["id"], "phase": r["phase"], "direction": direction, "est_bytes": est, "unit": _unit(r),
                    "affects": list(affects), "file": r.get("file"), "contingency": r.get("op") == "contingency", "note": r.get("note", "")})
    return out


def bias_summary(report: List[dict]) -> dict:
    """Net signed bytes the unapplied rows would add (+, the projection understates) or cut (-, it overstates).
    BUILD/PLAN/DEBUG/REVIEW: start-set effect in every tier; contingency rows only count under `with_contingencies_extra`
    (they only exist in that tier); kernel: rows whose file is the kernel; descriptions_chars: description caps not applied."""
    sign = lambda e: e["est_bytes"] if e["direction"] == "add" else -e["est_bytes"]  # noqa: E731
    plain = [e for e in report if e["unit"] == "bytes" and not e["contingency"]]
    cont = [e for e in report if e["unit"] == "bytes" and e["contingency"]]
    out = {"reading": "positive: the projection understates the real tree (unapplied rows add bytes); negative: it overstates",
           "kernel": sum(sign(e) for e in plain if e["file"] == KERNEL)}
    out.update({w: sum(sign(e) for e in plain if w in e["affects"]) for w in WORKFLOWS})
    out["with_contingencies_extra"] = {w: sum(sign(e) for e in cont if w in e["affects"]) for w in WORKFLOWS}
    out["descriptions_chars"] = sum(sign(e) for e in report if e["unit"] == "chars")
    out["rows"] = len(report)
    return out
