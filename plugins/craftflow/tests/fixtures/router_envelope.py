"""ADR-0074 router growth envelope: pure checks shared by test_router_growth_envelope.py and test_router_budget.py (plan H2 task 17).

The envelope file (router-growth-envelope.json) is David's signed allowance per gated key, plus signed absolute ceilings for the new routes.
Nothing here reads git or the tree: callers inject measured values, caps and an ancestor test, so every rule has a literal-fixture control.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional

ADR = "ADR-0074"
FIELDS = {"adr", "decided_by", "decided_at_sha", "base", "allowances", "new_route_ceilings", "consumed"}
ENTRY = {"phase", "key", "from", "to", "at_sha"}
NEW_ROUTES = ("ORIENT", "TRIAGE", "CODEBASE-HEALTH", "QA")
_H_PHASE = re.compile(r"^H\d+$")


def load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _h_phase(entry: dict) -> bool:
    return bool(_H_PHASE.match(str(entry.get("phase", ""))))


def is_envelope_entry(entry: dict) -> bool:
    """A ceiling_raises entry the envelope path may judge: tagged with this ADR and an H<n> phase."""
    return entry.get("envelope") == ADR and _h_phase(entry)


def shape_problems(e: dict) -> List[str]:
    if not isinstance(e, dict) or set(e) != FIELDS:
        return [f"envelope keys differ: {sorted(set(e) ^ FIELDS) if isinstance(e, dict) else 'not an object'}"]
    out = []
    if e["adr"] != ADR:
        out.append(f"adr must be {ADR}")
    if not (isinstance(e["base"], dict) and set(e["base"]) == set(e["allowances"]) and all(_int(v) for v in e["base"].values())
            and all(_int(v) and v >= 0 for v in e["allowances"].values())):
        out.append("base and allowances must hold the same keys with integer values")
    if not (isinstance(e["new_route_ceilings"], dict) and set(e["new_route_ceilings"]) == set(NEW_ROUTES)
            and all(_int(v) for v in e["new_route_ceilings"].values())):
        out.append(f"new_route_ceilings must hold exactly {list(NEW_ROUTES)} with integer values")
    if not (isinstance(e["consumed"], list) and all(isinstance(c, dict) and set(c) == ENTRY for c in e["consumed"])):
        out.append(f"every consumed entry must hold exactly {sorted(ENTRY)}")
    return out


def identity_problems(e: dict, is_ancestor: Callable[[str], bool]) -> List[str]:
    out = [] if e["decided_by"] == "David" else [f"decided_by must be David, not {e['decided_by']!r}"]
    if not is_ancestor(e["decided_at_sha"]):
        out.append(f"decided_at_sha {e['decided_at_sha']!r} is not an ancestor of HEAD")
    return out


def bound_problems(e: dict, ceilings: Dict[str, int], measured: Dict[str, int], caps: Dict[str, int]) -> List[str]:
    """Per key: measured <= ceiling <= base + allowance <= effective cap."""
    out = []
    for k, base in e["base"].items():
        high = base + e["allowances"][k]
        if measured[k] > ceilings[k]:
            out.append(f"{k}: measured {measured[k]} > ceiling {ceilings[k]}")
        elif ceilings[k] > high:
            out.append(f"{k}: ceiling {ceilings[k]} > base {base} + allowance {e['allowances'][k]}")
        if high > caps[k]:
            out.append(f"{k}: base + allowance {high} > effective cap {caps[k]}")
    return out


def route_problems(e: dict, totals: Dict[str, int]) -> List[str]:
    return [f"{r}: start total {totals[r]} > signed ceiling {e['new_route_ceilings'][r]}" for r in NEW_ROUTES
            if totals[r] > e["new_route_ceilings"][r]]


def consumed_problems(e: dict, origin: Optional[dict]) -> List[str]:
    old = (origin or {}).get("consumed", [])
    return [] if e["consumed"][:len(old)] == old else ["consumed[] is not an append-only extension of origin/main's"]


def tag_problems(added: List[dict]) -> List[str]:
    """An envelope tag needs an H<n> phase and an H<n> phase needs the tag (a raise outside the cut plan must say which rule judges it)."""
    return [f"ceiling_raises {x.get('key')}: phase {x.get('phase')!r} and envelope {x.get('envelope')!r} do not form an {ADR} entry"
            for x in added if (x.get("envelope") == ADR) != _h_phase(x)]


def problems(e: dict, origin: Optional[dict], ceilings: Dict[str, int], measured: Dict[str, int], caps: Dict[str, int],
             totals: Dict[str, int], added_raises: List[dict], is_ancestor: Callable[[str], bool]) -> List[str]:
    bad = shape_problems(e)
    if bad:
        return bad
    return (identity_problems(e, is_ancestor) + bound_problems(e, ceilings, measured, caps) + route_problems(e, totals)
            + consumed_problems(e, origin) + tag_problems(added_raises))


def checker(e: dict, measured: Dict[str, int]) -> Callable[[str, List[dict], int, int], Optional[List[str]]]:
    """For check_raise_bounds: None = not an envelope raise (the projection rule applies); a list = the envelope verdict for that key."""
    def run(key: str, added: List[dict], old: int, new: int) -> Optional[List[str]]:
        if not any(x.get("envelope") == ADR or _h_phase(x) for x in added):
            return None
        bad = tag_problems(added)
        if bad:
            return bad
        if not all(is_envelope_entry(x) for x in added):
            return [f"{key}: envelope raises mixed with non-envelope raises"]
        out = []
        if added[0]["from"] != old or added[-1]["to"] != new or any(a["to"] != b["from"] for a, b in zip(added, added[1:])):
            out.append(f"{key}: envelope entries do not chain origin's {old} to {new}")
        if new > e["base"][key] + e["allowances"][key]:
            out.append(f"{key}: ceiling {new} > base {e['base'][key]} + allowance {e['allowances'][key]}")
        if new < measured[key]:
            out.append(f"{key}: ceiling {new} < measured {measured[key]}")
        if not all(isinstance(x.get("at_sha"), str) and x["at_sha"] for x in added):
            out.append(f"{key}: envelope entry without at_sha")
        return out
    return run


def slack(budget: dict, e: dict) -> Dict[str, int]:
    """ceiling - envelope base for keys that carry an accepted envelope raise (the projection check may add it to its +1000)."""
    def get(d: dict, key: str) -> int:
        for part in key.split("."):
            d = d[part]
        return d
    keyed = {x["key"] for x in budget["ceiling_raises"] if is_envelope_entry(x)}
    return {k: get(budget["ceilings"], k) - e["base"][k] for k in e["base"] if k in keyed}


def envelope_only_change(origin: dict, b: dict) -> bool:
    """True when b differs from origin and every difference is an envelope-only field: ceilings, measured_at_sha, envelope-tagged
    ceiling_raises entries and enforced_caps_decision.amendments (their append-only prefix is checked elsewhere)."""
    def mask(x: dict) -> dict:
        x = copy.deepcopy(x)
        x.pop("measured_at_sha", None)
        x.pop("ceilings", None)
        x["ceiling_raises"] = [r for r in x.get("ceiling_raises", []) if not is_envelope_entry(r)]
        if isinstance(x.get("enforced_caps_decision"), dict):
            x["enforced_caps_decision"].pop("amendments", None)
        return x
    return origin != b and mask(origin) == mask(b)
