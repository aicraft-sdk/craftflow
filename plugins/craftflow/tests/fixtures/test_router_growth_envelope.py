#!/usr/bin/env python3
"""ADR-0074 router growth envelope test law (plan H2 tasks 16-17, AD-3).

Run: python3 tests/fixtures/test_router_growth_envelope.py
The logic lives in router_envelope.py (shared with test_router_budget.py). The mutation controls run on literal fixtures; the live check
reads router-growth-envelope.json, router-budget.json and the measured tree, and stays red until David signs and the amendments are
recorded at the signature commit (C6/C7): decided_at_sha is "PENDING-SIGNATURE" before that, and the ceilings sit at the pre-envelope values.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import router_envelope as renv  # noqa: E402
import test_router_budget as trb  # noqa: E402
import craftflow_router_readset as rs  # noqa: E402

ENVELOPE = trb.FIXTURES / "router-growth-envelope.json"
REL_ENVELOPE = "tools/craftflow-plugin/plugins/craftflow/tests/fixtures/router-growth-envelope.json"
_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason or 'assertion false'}")
        print(f"  FAIL: {name}: {reason or 'assertion false'}")


def is_ancestor_of_head(sha: str) -> bool:
    if not isinstance(sha, str) or not sha:
        return False
    return subprocess.run(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", sha, "HEAD"], capture_output=True).returncode == 0


# ---------------------------------------------------------------------------------------------- fixtures


def world() -> dict:
    keys = trb.KEYS
    return {"env": {"adr": "ADR-0074", "decided_by": "David", "decided_at_sha": "good",
                    "base": {k: 1000 for k in keys}, "allowances": {k: 500 for k in keys},
                    "new_route_ceilings": {r: 2000 for r in renv.NEW_ROUTES}, "consumed": []},
            "ceilings": {k: 1200 for k in keys}, "measured": {k: 1100 for k in keys}, "caps": {k: 1600 for k in keys},
            "totals": {r: 1900 for r in renv.NEW_ROUTES}, "origin": None, "raises": []}


def problems_of(w: dict) -> list:
    return renv.problems(w["env"], w["origin"], w["ceilings"], w["measured"], w["caps"], w["totals"], w["raises"], lambda s: s == "good")


def test_shape_and_good_world() -> None:
    w = world()
    check("good_fixture_has_no_problems", problems_of(w) == [], str(problems_of(w)))
    short = world()
    del short["env"]["consumed"]
    check("shape_missing_field_is_one_problem", len(problems_of(short)) == 1 and "keys differ" in problems_of(short)[0], str(problems_of(short)))
    badentry = world()
    badentry["env"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1, "to": 2}]
    check("shape_consumed_entry_needs_all_fields", len(problems_of(badentry)) == 1, str(problems_of(badentry)))


def test_mutation_controls_each_give_exactly_one_problem() -> None:
    over = world()
    over["ceilings"]["kernel_bytes"] = 1501  # base 1000 + allowance 500 = 1500
    over["caps"]["kernel_bytes"] = 2000
    notag = world()
    notag["raises"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1200, "at_sha": "x"}]
    edited = world()
    edited["origin"] = copy.deepcopy(edited["env"])
    edited["origin"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1200, "at_sha": "x"}]
    edited["env"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1300, "at_sha": "x"}]
    agent = world()
    agent["env"]["decided_by"] = "agent"
    detached = world()
    detached["env"]["decided_at_sha"] = "PENDING-SIGNATURE"
    capped = world()
    capped["caps"]["readset.BUILD"] = 1499  # base + allowance above the effective cap
    slow = world()
    slow["measured"]["protocol_bytes"] = 1201  # measured above the ceiling
    route = world()
    route["totals"]["QA"] = 2001
    cases = {"allowance_exceeded": over, "missing_tag": notag, "consumed_edited": edited, "decided_by_agent": agent,
             "sha_not_ancestor": detached, "allowance_above_cap": capped, "measured_above_ceiling": slow, "route_above_signed_ceiling": route}
    for name, w in cases.items():
        got = problems_of(w)
        check(f"mutation_{name}_one_problem", len(got) == 1, str(got))
    appended = world()
    appended["origin"] = copy.deepcopy(appended["env"])
    appended["env"]["consumed"] = [{"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1200, "at_sha": "x"}]
    check("consumed_append_is_allowed", problems_of(appended) == [], str(problems_of(appended)))


def test_check_raise_bounds_envelope_branch() -> None:
    def run(entries, to=1300, extra_keys=()):
        budget, origin, table = trb.raise_world()
        w = world()
        budget["ceilings"]["readset"]["BUILD"] = to
        budget["ceiling_raises"] = entries
        envelope = renv.checker(w["env"], {k: 1100 for k in trb.KEYS})
        return trb.check_raise_bounds(budget, origin, trb.fixture_projector(table), envelope=envelope)

    good = {"phase": "H2", "key": "readset.BUILD", "from": 1000, "to": 1300, "envelope": "ADR-0074", "at_sha": "abc"}
    check("envelope_raise_within_allowance_passes", run([good]) == [], str(run([good])))
    check("envelope_raise_above_allowance_one_problem", len(run([dict(good, to=1501)], to=1501)) == 1, str(run([dict(good, to=1501)], to=1501)))
    check("envelope_raise_with_p13_phase_one_problem", len(run([dict(good, phase="P13")])) == 1, str(run([dict(good, phase="P13")])))
    check("envelope_raise_from_not_origin_one_problem", len(run([dict(good, **{"from": 900})])) == 1, str(run([dict(good, **{"from": 900})])))
    mixed = [good, {"phase": "P4", "key": "readset.BUILD", "from": 1300, "to": 1300, "projected_delta": 300}]
    check("envelope_mixed_with_projection_raise_one_problem", len(run(mixed)) == 1, str(run(mixed)))
    check("raise_without_entry_still_fails_as_before", any("no ceiling_raises entry" in p for p in run([])), str(run([])))


def test_slack_and_append_only() -> None:
    w = world()
    budget = {"ceilings": {"kernel_bytes": 1300, "readset": {"BUILD": 1000}}, "ceiling_raises": [
        {"phase": "H2", "key": "kernel_bytes", "from": 1000, "to": 1300, "envelope": "ADR-0074", "at_sha": "abc"}]}
    w["env"]["base"] = {"kernel_bytes": 1000, "readset.BUILD": 1000}
    check("slack_only_for_keys_with_an_envelope_raise", renv.slack(budget, w["env"]) == {"kernel_bytes": 300}, str(renv.slack(budget, w["env"])))
    origin = {"merged_phases": ["P13"], "ceiling_raises": [], "projection_revisions": [], "ceilings": {"kernel_bytes": 1000}, "measured_at_sha": "a",
              "enforced_caps_decision": {"amendments": []}}
    head = copy.deepcopy(origin)
    head["ceilings"]["kernel_bytes"] = 1300
    head["measured_at_sha"] = "b"
    head["ceiling_raises"] = [budget["ceiling_raises"][0]]
    head["enforced_caps_decision"]["amendments"] = [{"key": "kernel_bytes"}]
    check("envelope_only_change_accepts_the_allowed_fields", renv.envelope_only_change(origin, head) and trb.append_only_problems(origin, head, False) == [],
          str(trb.append_only_problems(origin, head, False)))
    other = copy.deepcopy(head)
    other["conditions"] = {"difference_17": False}
    origin["conditions"] = {"difference_17": True}
    check("envelope_only_change_rejects_other_fields", not renv.envelope_only_change(origin, other), "a conditions change passed as envelope-only")
    check("identical_budget_is_not_an_envelope_change", not renv.envelope_only_change(origin, copy.deepcopy(origin)), "no change counted as one")


# ---------------------------------------------------------------------------------------------- live check


def test_live_envelope() -> None:
    e = json.loads(ENVELOPE.read_text(encoding="utf-8"))
    budget = trb.load_budget()
    caps = trb.effective_caps(budget) if budget.get("enforced_caps") else None
    cap_of = {k: trb.get_key(caps, k) for k in trb.KEYS} if caps else {k: 0 for k in trb.KEYS}
    rm = rs.load_map(PLUGIN_ROOT / "config" / "router-readmap.json")
    totals = {r: rs.compute(r, PLUGIN_ROOT, readmap=rm, budget_path=Path("/nonexistent"))["start_total"] for r in renv.NEW_ROUTES}
    ceilings = {k: trb.ceiling(budget, k) for k in trb.KEYS}
    status, origin_budget = trb.origin_budget()
    raw = trb.git_show("origin/main", REL_ENVELOPE) if trb.origin_available() else None
    origin_env = json.loads(raw.decode("utf-8")) if raw else None
    added = budget["ceiling_raises"][len((origin_budget or {}).get("ceiling_raises", [])):]
    got = renv.problems(e, origin_env, ceilings, trb.MEASURED, cap_of, totals, added, is_ancestor_of_head)
    check("live_envelope_signed_and_within_bounds", not got, "; ".join(got) + " (expected red until the H2 signature commits C6/C7)")


def main() -> int:
    for name in sorted(n for n in globals() if n.startswith("test_")):
        try:
            globals()[name]()
        except Exception as exc:  # noqa: BLE001 - a crashing test is a failure of that test, reported by name
            check(name, False, f"{type(exc).__name__}: {exc}")
    print()
    for err in _errors:
        print(err)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
