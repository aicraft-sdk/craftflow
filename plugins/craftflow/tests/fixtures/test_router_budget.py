#!/usr/bin/env python3
"""Ratcheting budget test for the router read set (plan P2 task 4, DD-6, PP-8, PP-10, Advisory G).

Run: python3 tests/fixtures/test_router_budget.py
Reads tests/fixtures/router-budget.json and compares it with the measured tree (craftflow_router_readset), with the
committed cut-plan projection and, where a remote is available, with origin/main. Every `origin/main` comparison is
skipped with a counting `skip(...)` line ("... CI must not skip") when `git show` fails; run_fixture_suite.sh fails a CI run
(CI=true) that prints one, because CI checks out with fetch-depth 0 and must never skip. The bound checks are pure functions that take the
projection as a callback, so the negative tests run them on fixture budgets and cut plans.
"""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_router_corpus as cor  # noqa: E402
import craftflow_router_readset as rs  # noqa: E402
import craftflow_router_readset_project as prj  # noqa: E402

FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
BUDGET = FIXTURES / "router-budget.json"
PROJECTION = FIXTURES / "router-projection.json"
CUTPLAN = FIXTURES / "router-cut-plan.json"
REL_BUDGET = "tools/craftflow-plugin/plugins/craftflow/tests/fixtures/router-budget.json"
REL_PROJECTION = "tools/craftflow-plugin/plugins/craftflow/tests/fixtures/router-projection.json"
REL_CUTPLAN = "tools/craftflow-plugin/plugins/craftflow/tests/fixtures/router-cut-plan.json"
ADR = PLUGIN_ROOT.parents[3] / "docs" / "ai" / "decisions" / "0073-craftflow-policy-first-router-kernel.md"
AMENDMENT_FIELDS = {"key", "from", "to", "cause", "decided_by", "at_sha", "adr"}
_UNSET = object()

TARGETS = {"kernel_bytes": 25000, "protocol_bytes": 15000, "reference_max_bytes": 20000, "description_chars": 4000,
           "readset": {"BUILD": 60000, "PLAN": 55000, "DEBUG": 55000, "REVIEW": 55000}}
KEYS = ["kernel_bytes", "protocol_bytes", "reference_max_bytes", "description_chars",
        "readset.BUILD", "readset.PLAN", "readset.DEBUG", "readset.REVIEW"]
SHAPE = {"baseline_ref", "measured_at_sha", "last_phase", "merged_phases", "conditions", "ceilings", "ceiling_raises", "targets",
         "enforced_caps", "enforced_caps_decision", "projection_sha256", "projection_revisions", "enforce", "p2_sha", "p2_measured"}
LISTS = ("merged_phases", "ceiling_raises", "projection_revisions")

_passes = 0
_skips = 0
_errors: list = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def skip(name: str, reason: str) -> None:
    global _skips
    _skips += 1
    print(f"  SKIP: {name}: {reason}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


def check(name: str, cond: bool, reason: str = "") -> None:
    if cond:
        ok(name)
    else:
        fail(name, reason or "assertion false")


def load_budget() -> dict:
    return json.loads(BUDGET.read_text(encoding="utf-8"))


def get_key(d: dict, key: str):
    cur = d
    for part in key.split("."):
        cur = cur[part]
    return cur


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------------------------- measurement


def measured() -> Dict[str, int]:
    """Every gated value of the live tree, keyed like `ceilings`."""
    out = {}
    rm = rs.load_map(PLUGIN_ROOT / "config" / "router-readmap.json")
    for wf in ("BUILD", "PLAN", "DEBUG", "REVIEW"):
        out[f"readset.{wf}"] = rs.compute(wf, PLUGIN_ROOT, readmap=rm, budget_path=Path("/nonexistent"))["start_total"]
    corpus = cor.read_corpus(PLUGIN_ROOT)
    size = {k: len(v.encode("utf-8")) for k, v in corpus.items()}
    out["kernel_bytes"] = size["skills/craftflow-router/SKILL.md"]
    out["protocol_bytes"] = size["skills/_shared/router-protocol.md"]
    out["reference_max_bytes"] = max(v for k, v in size.items() if k.startswith("skills/craftflow-router/references/")
                                     or (k.startswith("skills/_shared/") and k != "skills/_shared/router-protocol.md"))
    out["description_chars"] = rs.descriptions(PLUGIN_ROOT)["sum"]
    return out


MEASURED = measured()


def ceiling(budget: dict, key: str) -> int:
    return get_key(budget["ceilings"], key)


# ---------------------------------------------------------------------------------------------- git helpers


def origin_available() -> bool:
    return subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "-q", "origin/main"], capture_output=True).returncode == 0


def git_show(ref: str, rel: str) -> Optional[bytes]:
    res = subprocess.run(["git", "-C", str(REPO_ROOT), "show", f"{ref}:{rel}"], capture_output=True)
    return res.stdout if res.returncode == 0 else None


def origin_budget() -> "tuple[str, Optional[dict]]":
    """('skip', None) without a remote, ('new', None) when origin/main has no budget file yet, else ('ok', parsed)."""
    if not origin_available():
        return "skip", None
    raw = git_show("origin/main", REL_BUDGET)
    return ("new", None) if raw is None else ("ok", json.loads(raw.decode("utf-8")))


# ---------------------------------------------------------------------------------------------- pure bound checks


def is_prefix(old: list, new: list) -> bool:
    return len(old) <= len(new) and new[:len(old)] == old


def check_raise_bounds(budget: dict, origin: Optional[dict], projector: Callable[[List[str]], Dict[str, int]]) -> List[str]:
    """PP-8 / DD-6: a ceiling that rose against `origin` needs a ceiling_raises entry for this phase and key with
    projected_delta > 0, raise <= projected_delta + 500 and new ceiling <= project(merged_phases) + 1000.
    `projector(phases)` returns {key: projected value} for a list of phase ids (effective tier, recorded conditions)."""
    if origin is None:
        return []  # first introduction of the ratchet: nothing to compare with
    problems = []
    phase = budget["last_phase"]
    now = projector(budget["merged_phases"])
    prev = projector([p for p in budget["merged_phases"] if p != phase])
    for k in KEYS:
        old, new = ceiling(origin, k), ceiling(budget, k)
        if new <= old:
            continue
        entries = [e for e in budget["ceiling_raises"] if e.get("phase") == phase and e.get("key") == k]
        if not entries:
            problems.append(f"{k}: ceiling rose {old} -> {new} with no ceiling_raises entry for {phase}")
            continue
        delta = now[k] - prev[k]
        if delta <= 0:
            problems.append(f"{k}: raised although the projected delta of {phase} is {delta} (not an additive key)")
        elif new - old > delta + 500:
            problems.append(f"{k}: raise {new - old} exceeds projected delta {delta} + 500")
        if new > now[k] + 1000:
            problems.append(f"{k}: new ceiling {new} exceeds projection {now[k]} + 1000")
    return problems


def check_decision_record(budget: dict, show: Callable[[str, str], Optional[bytes]]) -> List[str]:
    """Advisory G: enforced caps need David's decision record, pinned to the projection committed at decided_at_sha."""
    if budget.get("enforced_caps") is None:
        return [] if budget.get("enforced_caps_decision") is None else ["enforced_caps_decision set while enforced_caps is null"]
    d = budget.get("enforced_caps_decision") or {}
    problems = []
    if d.get("option") not in ("A", "B", "C"):
        problems.append(f"option {d.get('option')!r} not in A/B/C")
    if d.get("decided_by") != "David":
        problems.append("decided_by must be David")
    if d.get("adr") != "ADR-0073":
        problems.append("adr must be ADR-0073")
    if not isinstance(d.get("decided_at_sha"), str) or not d.get("decided_at_sha"):
        problems.append("decided_at_sha missing")
        return problems
    raw = show(d["decided_at_sha"], REL_PROJECTION)
    if raw is None:
        problems.append(f"router-projection.json not readable at {d['decided_at_sha']}")
    elif hashlib.sha256(raw).hexdigest() != d.get("projection_sha256"):
        problems.append("projection_sha256 differs from router-projection.json as committed at decided_at_sha")
    return problems


def check_revision_chain(budget: dict, file_sha: str, computed=_UNSET) -> List[str]:
    """Chain continuity plus the fits flag. `computed` is computed_fits(): the newest entry must carry exactly that value (the flag
    is never trusted); older entries carry True once caps exist (they were true when recorded) and None before."""
    revs = budget.get("projection_revisions", [])
    decision = budget.get("enforced_caps_decision")
    pinned = decision.get("projection_sha256") if decision else None
    top = budget.get("projection_sha256")
    problems = []
    if top != file_sha:
        problems.append("projection_sha256 differs from the sha256 of the committed router-projection.json")
    if not revs:
        if pinned is not None and pinned != top:
            problems.append("no revisions, yet the top-level projection_sha256 differs from the decision's")
        return problems
    if pinned is not None and revs[0].get("from_projection_sha256") != pinned:
        problems.append("revisions[0].from_projection_sha256 differs from the decision's")
    for a, b in zip(revs, revs[1:]):
        if b.get("from_projection_sha256") != a.get("to_projection_sha256"):
            problems.append(f"revision chain broken between {a.get('phase')} and {b.get('phase')}")
    if revs[-1].get("to_projection_sha256") != top:
        problems.append("last revision's to_projection_sha256 differs from the top-level projection_sha256")
    capped = budget.get("enforced_caps") is not None
    for i, r in enumerate(revs):
        want = computed if (i == len(revs) - 1 and computed is not _UNSET) else (True if capped else None)
        if capped and want is not True:
            problems.append(f"revision {r.get('phase')}: the projected cut plan does not fit enforced_caps (computed {want})")
        elif r.get("fits_enforced_caps") is not want:
            problems.append(f"revision {r.get('phase')} records fits_enforced_caps={r.get('fits_enforced_caps')!r}, computed {want!r}")
    return problems


def check_cutplan_revision(budget: dict, origin: Optional[dict], origin_projection_sha: Optional[str], origin_plan: Optional[bytes],
                           head_plan: bytes) -> List[str]:
    """F5b/c: a router-cut-plan.json that differs from origin/main's needs a NEW projection_revisions entry (cause, decided_by)
    chaining origin's projection sha256 to HEAD's. Before P2C too, so the sha the P2C decision pins cannot drift silently."""
    if origin is None or origin_plan is None or origin_plan == head_plan:
        return []
    new = budget.get("projection_revisions", [])[len(origin.get("projection_revisions", [])):]
    if not new:
        return ["router-cut-plan.json differs from origin/main but projection_revisions has no new entry"]
    problems = []
    if new[0].get("from_projection_sha256") != origin_projection_sha:
        problems.append("the first new revision's from_projection_sha256 is not the sha256 of origin/main's router-projection.json")
    if new[-1].get("to_projection_sha256") != budget.get("projection_sha256"):
        problems.append("the last new revision's to_projection_sha256 is not HEAD's projection_sha256")
    for r in new:
        for k in ("cause", "decided_by"):
            if not isinstance(r.get(k), str) or not r[k].strip():
                problems.append(f"revision {r.get('phase')}: {k} missing")
    return problems


def sha_exists_in_repo(sha: str) -> bool:
    return isinstance(sha, str) and bool(sha) and subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
                                                                  capture_output=True).returncode == 0


def adr_text() -> str:
    return ADR.read_text(encoding="utf-8")


def set_key(d: dict, key: str, value) -> None:
    parts = key.split(".")
    for p in parts[:-1]:
        d = d[p]
    d[parts[-1]] = value


def effective_caps(budget: dict) -> dict:
    """enforced_caps with the recorded amendments applied in order (their validity is check_amendments's job)."""
    caps = copy.deepcopy(budget["enforced_caps"])
    for a in (budget.get("enforced_caps_decision") or {}).get("amendments", []):
        set_key(caps, a["key"], a["to"])
    return caps


def check_amendments(budget: dict, sha_exists: Callable[[str], bool], adr: str) -> List[str]:
    """F4: an amendment is {key, from, to, cause, decided_by: David, at_sha, adr: ADR-0073}; key is a cap, `from` is the cap in force
    at that point of the chain, at_sha is a commit and ADR-0073 has a line citing it. Append-only vs origin: append_only_problems."""
    ams = (budget.get("enforced_caps_decision") or {}).get("amendments") or []
    if not ams:
        return []
    if budget.get("enforced_caps") is None:
        return ["amendments recorded while enforced_caps is null"]
    caps, problems = copy.deepcopy(budget["enforced_caps"]), []
    for i, a in enumerate(ams):
        w = f"amendment {i}"
        if not isinstance(a, dict) or set(a) != AMENDMENT_FIELDS:
            problems.append(f"{w}: fields must be exactly {sorted(AMENDMENT_FIELDS)}")
            continue
        if a["key"] not in KEYS:
            problems.append(f"{w}: key {a['key']!r} is not a capped value")
            continue
        if a["from"] != get_key(caps, a["key"]):
            problems.append(f"{w}: from {a['from']!r} is not the cap in force ({get_key(caps, a['key'])})")
        if not isinstance(a["to"], int) or isinstance(a["to"], bool) or a["to"] <= 0:
            problems.append(f"{w}: to must be a positive integer")
        if not isinstance(a["cause"], str) or not a["cause"].strip():
            problems.append(f"{w}: cause missing")
        if a["decided_by"] != "David":
            problems.append(f"{w}: decided_by must be David")
        if a["adr"] != "ADR-0073":
            problems.append(f"{w}: adr must be ADR-0073")
        sha = a["at_sha"]
        if not isinstance(sha, str) or not sha.strip() or not sha_exists(sha):
            problems.append(f"{w}: at_sha {sha!r} is not a commit of this repository")
        elif not any(sha in line for line in adr.splitlines()):
            problems.append(f"{w}: ADR-0073 has no line citing at_sha {sha}")
        if isinstance(a["to"], int) and not isinstance(a["to"], bool):
            set_key(caps, a["key"], a["to"])
    return problems


def computed_fits(budget: dict, projector: Callable[[List[str]], Dict[str, int]]) -> Optional[bool]:
    """F5a: does the projection of the committed cut plan (recorded conditions, merged phases) fit the effective caps? None: no caps yet."""
    if budget.get("enforced_caps") is None:
        return None
    caps, now = effective_caps(budget), projector(budget["merged_phases"])
    return all(now[k] <= get_key(caps, k) for k in KEYS)


def check_within_projection(values: Dict[str, int], projected: Dict[str, int]) -> List[str]:
    return [f"{k}: measured {values[k]} > projection {projected[k]} + 1000" for k in KEYS if values[k] > projected[k] + 1000]


def effective_values(budget: dict) -> Callable[[List[str]], Dict[str, int]]:
    """Projector over the committed cut plan: the effective tier (recorded conditions) for a list of phases."""
    cutplan = json.loads(CUTPLAN.read_text(encoding="utf-8"))
    lock = json.loads((FIXTURES / "router-baseline.lock.json").read_text(encoding="utf-8"))
    state0 = prj.load_baseline_state(PLUGIN_ROOT, lock["baseline_sha"], FIXTURES / "router-readmap.baseline.json")
    flags = budget.get("conditions", {})

    def project(phases: List[str]) -> Dict[str, int]:
        out = prj.project(cutplan, state0, phases, flags)["effective"]
        t, pf = out["tier"], out["per_file"]
        return {"kernel_bytes": pf["kernel_bytes"], "protocol_bytes": pf["protocol_bytes"], "reference_max_bytes": pf["reference_max_bytes"],
                "description_chars": out["descriptions"], "readset.BUILD": t["BUILD"]["max"], "readset.PLAN": t["PLAN"],
                "readset.DEBUG": t["DEBUG"], "readset.REVIEW": t["REVIEW"]}

    return project


# ---------------------------------------------------------------------------------------------- tests on the live budget


def shape_ok(b: dict) -> bool:
    """The budget file's shape; the condition flags must be real booleans (a "false" string is truthy)."""
    c = b["ceilings"]
    return set(b) == SHAPE and set(c) == {"kernel_bytes", "protocol_bytes", "reference_max_bytes", "description_chars", "readset"} \
        and set(c["readset"]) == {"BUILD", "PLAN", "DEBUG", "REVIEW"} and all(isinstance(get_key(c, k), int) for k in KEYS) \
        and bool(b["merged_phases"]) and b["merged_phases"][-1] == b["last_phase"] and len(set(b["merged_phases"])) == len(b["merged_phases"]) \
        and set(b["conditions"]) == {"difference_17", "live_verified_enterplanmode"} \
        and all(isinstance(v, bool) for v in b["conditions"].values()) and isinstance(b["enforce"], bool)


def test_budget_shape() -> None:
    b = load_budget()
    stringy = copy.deepcopy(b)
    stringy["conditions"]["difference_17"] = "false"
    renamed = copy.deepcopy(b)
    renamed["conditions"] = {"difference_17": True, "live_verified": False}
    check("budget_shape", shape_ok(b) and not shape_ok(stringy) and not shape_ok(renamed),
          f"keys={sorted(set(b) ^ SHAPE)} merged={b.get('merged_phases')} last={b.get('last_phase')} string_flag_rejected={not shape_ok(stringy)}")


def test_kernel_le_ceiling() -> None:
    check("kernel_le_ceiling", MEASURED["kernel_bytes"] <= ceiling(load_budget(), "kernel_bytes"),
          f"SKILL.md is {MEASURED['kernel_bytes']} B, ceiling {ceiling(load_budget(), 'kernel_bytes')}")


def test_protocol_le_ceiling() -> None:
    check("protocol_le_ceiling", MEASURED["protocol_bytes"] <= ceiling(load_budget(), "protocol_bytes"),
          f"router-protocol.md is {MEASURED['protocol_bytes']} B, ceiling {ceiling(load_budget(), 'protocol_bytes')}")


def test_each_reference_le_ceiling() -> None:
    cap = ceiling(load_budget(), "reference_max_bytes")
    corpus = cor.read_corpus(PLUGIN_ROOT)
    over = {k: len(v.encode()) for k, v in corpus.items()
            if (k.startswith("skills/craftflow-router/references/") or (k.startswith("skills/_shared/") and k != "skills/_shared/router-protocol.md"))
            and len(v.encode()) > cap}
    check("each_reference_le_ceiling", not over, f"over {cap}: {over}")


def test_descriptions_le_ceiling() -> None:
    check("descriptions_le_ceiling", MEASURED["description_chars"] <= ceiling(load_budget(), "description_chars"),
          f"{MEASURED['description_chars']} chars, ceiling {ceiling(load_budget(), 'description_chars')}")


def test_readsets_le_ceilings() -> None:
    b = load_budget()
    over = {k: (MEASURED[k], ceiling(b, k)) for k in KEYS if k.startswith("readset.") and MEASURED[k] > ceiling(b, k)}
    check("readsets_le_ceilings", not over, f"start_total above ceiling: {over}")


def test_targets_are_design_values() -> None:
    check("targets_are_design_values", load_budget()["targets"] == TARGETS, f"targets edited: {load_budget()['targets']}")


def test_ceiling_changes_bounded_vs_origin_main() -> None:
    status, origin = origin_budget()
    if status == "skip":
        skip("ceiling_changes_bounded_vs_origin_main", "no origin/main ref (CI must not skip)")
        return
    if status == "new":
        ok("ceiling_changes_bounded_vs_origin_main:first_introduction")
        return
    b = load_budget()
    problems = check_raise_bounds(b, origin, effective_values(b))
    check("ceiling_changes_bounded_vs_origin_main", not problems, "; ".join(problems))


def append_only_problems(origin: dict, b: dict, identical: bool) -> List[str]:
    """The lists only grow (always); a phase PR whose budget file differs from origin/main's adds exactly its own id to
    merged_phases, and a PR that leaves the file byte-equal to origin/main's (any non-phase PR, or main itself) adds none."""
    problems = [f"{k} is not an append-only extension of origin/main's" for k in LISTS if not is_prefix(origin[k], b[k])]
    old = ((origin.get("enforced_caps_decision") or {}).get("amendments")) or []
    new = ((b.get("enforced_caps_decision") or {}).get("amendments")) or []
    if not is_prefix(old, new):
        problems.append("enforced_caps_decision.amendments is not an append-only extension of origin/main's")
    added = len(b["merged_phases"]) - len(origin["merged_phases"])
    want = 0 if identical else 1
    if added != want:
        problems.append(f"merged_phases added {added}, expected {want} "
                        f"({'the file equals origin/main, so nothing' if identical else 'a phase PR adds exactly its own id'})")
    return problems


def test_budget_lists_append_only() -> None:
    status, origin = origin_budget()
    b = load_budget()
    pure = is_prefix(["P2"], ["P2", "P2C"]) and not is_prefix(["P2", "P2C"], ["P2"]) and not is_prefix(["P2"], ["P3", "P2"])
    own = len(set(b["merged_phases"])) == len(b["merged_phases"])
    check("budget_lists_append_only:helpers", pure and own, "prefix helper or own-list sanity broken")
    if status == "skip":
        skip("budget_lists_append_only", "no origin/main ref (CI must not skip)")
        return
    if status == "new":
        ok("budget_lists_append_only:first_introduction")
        return
    identical = git_show("origin/main", REL_BUDGET) == BUDGET.read_bytes()
    problems = append_only_problems(origin, b, identical)
    check("budget_lists_append_only", not problems, "; ".join(problems))


def test_append_only_both_branches() -> None:
    """H2: byte-equal to origin/main -> added 0 passes; a changed budget needs exactly one new id."""
    origin = {"merged_phases": ["P2"], "ceiling_raises": [], "projection_revisions": []}
    same = copy.deepcopy(origin)
    phase = dict(copy.deepcopy(origin), merged_phases=["P2", "P3"])
    two = dict(copy.deepcopy(origin), merged_phases=["P2", "P3", "P4"])
    reorder = dict(copy.deepcopy(origin), merged_phases=["P3", "P2"])
    shrink = dict(copy.deepcopy(origin), merged_phases=[])
    check("append_only_both_branches",
          append_only_problems(origin, same, True) == [] and append_only_problems(origin, phase, False) == []
          and append_only_problems(origin, same, False) != [] and append_only_problems(origin, two, False) != []
          and append_only_problems(origin, phase, True) != [] and append_only_problems(origin, reorder, False) != []
          and append_only_problems(origin, shrink, False) != [],
          f"identical/0={append_only_problems(origin, same, True)} changed/0={append_only_problems(origin, same, False)} "
          f"changed/2={append_only_problems(origin, two, False)}")


def test_enforced_caps_have_decision_record() -> None:
    b = load_budget()
    good = {"enforced_caps": {k: 1 for k in KEYS}, "enforced_caps_decision": {"option": "B", "decided_by": "David", "decided_at_sha": "abc",
            "adr": "ADR-0073", "projection_sha256": hashlib.sha256(b"projection").hexdigest()}}
    show = lambda sha, rel: b"projection" if sha == "abc" else None  # noqa: E731
    wrong = copy.deepcopy(good)
    wrong["enforced_caps_decision"]["projection_sha256"] = "0" * 64
    anon = copy.deepcopy(good)
    anon["enforced_caps_decision"]["decided_by"] = "agent"
    badopt = copy.deepcopy(good)
    badopt["enforced_caps_decision"]["option"] = "D"
    nosha = copy.deepcopy(good)
    nosha["enforced_caps_decision"].pop("decided_at_sha")
    fixture_ok = (not check_decision_record(good, show) and check_decision_record(wrong, show) and check_decision_record(anon, show)
                  and check_decision_record(badopt, show) and check_decision_record(nosha, show)
                  and check_decision_record({"enforced_caps": {"x": 1}, "enforced_caps_decision": None}, show))
    live = check_decision_record(b, lambda sha, rel: git_show(sha, rel))
    check("enforced_caps_have_decision_record", fixture_ok and not live, f"fixture_ok={fixture_ok} live={live}")


def test_decision_record_immutable() -> None:
    status, origin = origin_budget()
    if status == "skip":
        skip("decision_record_immutable", "no origin/main ref (CI must not skip)")
        return
    b = load_budget()
    if status == "new" or origin.get("enforced_caps") is None:
        ok("decision_record_immutable:no_record_on_origin")
        return

    def frozen(d):
        d = copy.deepcopy(d)
        if d:
            d.pop("amendments", None)
        return d
    same = origin["enforced_caps"] == b["enforced_caps"] and frozen(origin["enforced_caps_decision"]) == frozen(b["enforced_caps_decision"])
    check("decision_record_immutable", same, "enforced_caps or its decision record changed after origin/main had them")


def test_projection_revision_chain() -> None:
    b = load_budget()
    h = "f" * 64
    chain_ok = {"projection_sha256": "c", "enforced_caps": {"kernel_bytes": 1}, "enforced_caps_decision": {"projection_sha256": "a"},
                "projection_revisions": [{"phase": "P4", "from_projection_sha256": "a", "to_projection_sha256": "b", "fits_enforced_caps": True},
                                         {"phase": "P6", "from_projection_sha256": "b", "to_projection_sha256": "c", "fits_enforced_caps": True}]}
    broken = copy.deepcopy(chain_ok)
    broken["projection_revisions"][1]["from_projection_sha256"] = "x"
    nofit = copy.deepcopy(chain_ok)
    nofit["projection_revisions"][0]["fits_enforced_caps"] = False
    tail = copy.deepcopy(chain_ok)
    tail["projection_revisions"][1]["to_projection_sha256"] = "z"
    plain_ok = {"projection_sha256": h, "enforced_caps_decision": {"projection_sha256": h}, "projection_revisions": []}
    plain_bad = {"projection_sha256": h, "enforced_caps_decision": {"projection_sha256": "e" * 64}, "projection_revisions": []}
    fixtures = (not check_revision_chain(chain_ok, "c") and check_revision_chain(broken, "c") and check_revision_chain(nofit, "c")
                and check_revision_chain(tail, "c") and not check_revision_chain(plain_ok, h) and check_revision_chain(plain_bad, h)
                and check_revision_chain(plain_ok, "0" * 64))
    live = check_revision_chain(b, sha256_file(PROJECTION), computed_fits(b, effective_values(b)))
    check("projection_revision_chain", fixtures and not live, f"fixtures={fixtures} live={live}")


def test_projection_file_current() -> None:
    res = subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_router_readset.py"), "--project", str(CUTPLAN), "--json"],
                         capture_output=True)
    check("projection_file_current", res.returncode == 0 and res.stdout == PROJECTION.read_bytes(),
          f"router-projection.json is stale: regenerate with --project (exit {res.returncode})")


def test_measured_within_phase_projection() -> None:
    b = load_budget()
    project = effective_values(b)
    projected = project(b["merged_phases"])
    problems = check_within_projection(MEASURED, projected)
    inflated = dict(MEASURED, **{"readset.BUILD": projected["readset.BUILD"] + 1001})
    control = check_within_projection(inflated, projected)
    check("measured_within_phase_projection", not problems and len(control) == 1,
          f"{problems}; control={control} (apply the projection-drift stop rule, never raise a ceiling silently)")


def test_enforce_requires_caps() -> None:
    b = load_budget()
    rule = lambda d: (not d["enforce"]) or d["enforced_caps"] is not None  # noqa: E731
    check("enforce_requires_caps", rule(b) and not rule({"enforce": True, "enforced_caps": None}) and rule({"enforce": True, "enforced_caps": {}})
          and rule({"enforce": False, "enforced_caps": None}), "enforce: true with null enforced_caps")


def test_ceilings_le_enforced_caps_when_enforced() -> None:
    b = load_budget()
    if not b["enforce"]:
        check("ceilings_le_enforced_caps_when_enforced", True)
        return
    caps = effective_caps(b)
    over = [k for k in KEYS if ceiling(b, k) > get_key(caps, k)]
    check("ceilings_le_enforced_caps_when_enforced", not over, f"ceilings above enforced caps: {over}")


# ---------------------------------------------------------------------------------------------- P2 REM-FIX 2 tests (F4, F5)


def amendment(**kw) -> dict:
    good = {"key": "kernel_bytes", "from": 35000, "to": 36000, "cause": "reach rows add 1,000 B", "decided_by": "David",
            "at_sha": "abc1234", "adr": "ADR-0073"}
    good.update(kw)
    return good


def amended_budget(*ams: dict) -> dict:
    b = load_budget()
    caps = copy.deepcopy(b["ceilings"])
    caps["kernel_bytes"] = 35000
    b.update(enforce=True, enforced_caps=caps, enforced_caps_decision={"option": "B", "decided_by": "David", "adr": "ADR-0073",
                                                                      "decided_at_sha": "abc1234", "amendments": list(ams)})
    return b


def test_amendments_are_validated() -> None:
    """F4: each amendment names an existing cap, the current effective value, a cause, David, an existing sha that ADR-0073 cites."""
    sha_exists = lambda s: s == "abc1234"  # noqa: E731
    adr = "# ADR\n- kernel_bytes cap 35000 -> 36000 at abc1234\n"
    run = lambda *ams, text=adr: check_amendments(amended_budget(*ams), sha_exists, text)  # noqa: E731
    bare = {"key": "kernel_bytes", "to": 10 ** 9}
    controls = {
        "good": run(amendment()) == [],
        "chain": run(amendment(), amendment(**{"from": 36000, "to": 37000, "at_sha": "abc1234"})) == [],
        "bare": bool(run(bare)),
        "wrong_from": bool(run(amendment(**{"from": 34000}))),
        "missing_cause": bool(run({k: v for k, v in amendment().items() if k != "cause"})),
        "empty_cause": bool(run(amendment(cause=" "))),
        "decided_by": bool(run(amendment(decided_by="agent"))),
        "unknown_sha": bool(run(amendment(at_sha="deadbee"), text=adr.replace("abc1234", "deadbee"))),
        "not_in_adr": bool(run(amendment(), text="# ADR\nnothing about the sha\n")),
        "unknown_key": bool(run(amendment(key="nope"))),
        "wrong_adr": bool(run(amendment(adr="ADR-0001"))),
        "bool_to": bool(run(amendment(to=True))),
        "stale_from_after_chain": bool(run(amendment(), amendment(**{"from": 35000, "to": 40000}))),
    }
    live = check_amendments(load_budget(), lambda s: sha_exists_in_repo(s), adr_text())
    check("amendments_are_validated", all(controls.values()) and not live, f"controls={controls} live={live}")


def test_amendments_append_only_vs_origin() -> None:
    origin = {"merged_phases": ["P2"], "ceiling_raises": [], "projection_revisions": [],
              "enforced_caps_decision": {"amendments": [amendment()]}}
    grown = copy.deepcopy(origin)
    grown["enforced_caps_decision"]["amendments"].append(amendment(**{"from": 36000, "to": 37000}))
    edited = copy.deepcopy(origin)
    edited["enforced_caps_decision"]["amendments"][0]["to"] = 10 ** 9
    dropped = copy.deepcopy(origin)
    dropped["enforced_caps_decision"]["amendments"] = []
    check("amendments_append_only_vs_origin",
          append_only_problems(origin, grown, True) == [] and append_only_problems(origin, edited, True) != []
          and append_only_problems(origin, dropped, True) != [], "amendment list is not append-only")


def test_fits_enforced_caps_is_computed() -> None:
    """F5a: the recorded flag must equal the projection of the committed cut plan against the effective caps."""
    over = amended_budget()
    over["projection_sha256"] = "c"
    over["projection_revisions"] = [{"phase": "P4", "from_projection_sha256": "a", "to_projection_sha256": "c", "fits_enforced_caps": True,
                                     "cause": "x", "decided_by": "David"}]
    over["enforced_caps_decision"]["projection_sha256"] = "a"
    fits = lambda value: {k: value for k in KEYS}  # noqa: E731
    proj_fit, proj_over = (lambda phases: fits(1)), (lambda phases: fits(10 ** 9))
    ok_fit = computed_fits(over, proj_fit) is True and computed_fits(over, proj_over) is False
    liar = check_revision_chain(over, "c", computed_fits(over, proj_over))
    honest = check_revision_chain(over, "c", computed_fits(over, proj_fit))
    pre = dict(load_budget(), enforce=False, enforced_caps=None, enforced_caps_decision=None)  # a budget before the P2C decision
    pre_rev = dict(pre, projection_revisions=[{"phase": "P4", "from_projection_sha256": "a", "to_projection_sha256": pre["projection_sha256"],
                                               "fits_enforced_caps": True, "cause": "x", "decided_by": "David"}])
    pre_true = check_revision_chain(pre_rev, pre["projection_sha256"], computed_fits(pre_rev, proj_fit))
    pre_null = check_revision_chain(dict(pre_rev, projection_revisions=[dict(pre_rev["projection_revisions"][0], fits_enforced_caps=None)]),
                                    pre["projection_sha256"], computed_fits(pre_rev, proj_fit))
    live = check_revision_chain(load_budget(), sha256_file(PROJECTION), computed_fits(load_budget(), effective_values(load_budget())))
    check("fits_enforced_caps_is_computed", ok_fit and liar and not honest and pre_true and not pre_null and not live,
          f"ok_fit={ok_fit} liar={liar} honest={honest} pre_true={pre_true} pre_null={pre_null} live={live}")


def revision(frm: str, to: str, **kw) -> dict:
    return dict({"phase": "P3", "from_projection_sha256": frm, "to_projection_sha256": to, "fits_enforced_caps": None,
                 "cause": "new rows", "decided_by": "David"}, **kw)


def test_cutplan_change_needs_revision_entry() -> None:
    """F5b/c: a cut-plan change against origin/main needs a new projection_revisions entry that chains origin's sha to HEAD's."""
    origin = {"projection_revisions": [], "projection_sha256": "o" * 64}
    mk = lambda *revs: {"projection_revisions": list(revs), "projection_sha256": "h" * 64}  # noqa: E731
    run = lambda b, o_plan=b"A", h_plan=b"B": check_cutplan_revision(b, origin, "o" * 64, o_plan, h_plan)  # noqa: E731
    controls = {
        "unchanged": run(mk(), b"A", b"A") == [],
        "changed_no_entry": bool(run(mk())),
        "good": run(mk(revision("o" * 64, "h" * 64))) == [],
        "wrong_from": bool(run(mk(revision("x" * 64, "h" * 64)))),
        "wrong_to": bool(run(mk(revision("o" * 64, "x" * 64)))),
        "no_cause": bool(run(mk(revision("o" * 64, "h" * 64, cause="")))),
        "no_decider": bool(run(mk(revision("o" * 64, "h" * 64, decided_by=None)))),
        "first_introduction": check_cutplan_revision(mk(), None, None, None, b"B") == [],
    }
    status, live_origin = origin_budget()
    if status == "ok":
        raw = git_show("origin/main", REL_PROJECTION)
        o_plan = git_show("origin/main", REL_CUTPLAN)
        live = check_cutplan_revision(load_budget(), live_origin, hashlib.sha256(raw).hexdigest() if raw else None, o_plan, CUTPLAN.read_bytes())
    elif status == "new":
        live = []
    else:
        skip("cutplan_change_needs_revision_entry:live", "no origin/main ref (CI must not skip)")
        live = []
    check("cutplan_change_needs_revision_entry", all(controls.values()) and not live, f"controls={controls} live={live}")


def test_adr_records_codeowner_identity() -> None:
    text = adr_text()
    check("adr_records_codeowner_identity", "## P2 REM-FIX 2 notes" in text and "@volkz" in text and "second reviewer" in text,
          "ADR-0073 must note that CODEOWNERS @volkz is also the agent identity")


# ---------------------------------------------------------------------------------------------- negative tests (fixtures)


def fixture_projector(table: Dict[str, Dict[str, int]]) -> Callable[[List[str]], Dict[str, int]]:
    return lambda phases: table["+".join(phases)]


def raise_world() -> tuple:
    zero = {k: 1000 for k in KEYS}
    after = dict(zero, **{"readset.BUILD": 1300})
    table = {"P4": after, "": zero}
    origin = {"ceilings": {"kernel_bytes": 1000, "protocol_bytes": 1000, "reference_max_bytes": 1000, "description_chars": 1000,
                           "readset": {"BUILD": 1000, "PLAN": 1000, "DEBUG": 1000, "REVIEW": 1000}}}
    budget = {"last_phase": "P4", "merged_phases": ["P4"], "ceiling_raises": [], "ceilings": copy.deepcopy(origin["ceilings"])}
    return budget, origin, table


def test_raise_without_record_fails() -> None:
    budget, origin, table = raise_world()
    budget["ceilings"]["readset"]["BUILD"] = 1300
    problems = check_raise_bounds(budget, origin, fixture_projector(table))
    budget["ceiling_raises"] = [{"phase": "P4", "key": "readset.BUILD", "from": 1000, "to": 1300, "projected_delta": 300}]
    recorded = check_raise_bounds(budget, origin, fixture_projector(table))
    check("raise_without_record_fails", len(problems) == 1 and "no ceiling_raises entry" in problems[0] and not recorded, f"{problems} {recorded}")


def test_raise_on_non_additive_key_fails() -> None:
    budget, origin, table = raise_world()
    budget["ceilings"]["readset"]["PLAN"] = 1100  # the projection of PLAN does not grow in P4
    budget["ceiling_raises"] = [{"phase": "P4", "key": "readset.PLAN", "from": 1000, "to": 1100, "projected_delta": 100}]
    problems = check_raise_bounds(budget, origin, fixture_projector(table))
    check("raise_on_non_additive_key_fails", len(problems) >= 1 and "not an additive key" in problems[0], str(problems))


def test_raise_over_projected_delta_plus_500_fails() -> None:
    budget, origin, table = raise_world()
    table["P4"]["readset.BUILD"] = 1100  # projected delta 100
    budget["ceilings"]["readset"]["BUILD"] = 1700  # raise 700 > 100 + 500
    budget["ceiling_raises"] = [{"phase": "P4", "key": "readset.BUILD", "from": 1000, "to": 1700, "projected_delta": 100}]
    problems = check_raise_bounds(budget, origin, fixture_projector(table))
    budget["ceilings"]["readset"]["BUILD"] = 1600  # raise 600 = 100 + 500 and new ceiling <= projection + 1000
    budget["ceiling_raises"][0]["to"] = 1600
    within = check_raise_bounds(budget, origin, fixture_projector(table))
    check("raise_over_projected_delta_plus_500_fails", any("+ 500" in p for p in problems) and not within, f"{problems} | {within}")


def test_raise_over_projection_plus_1000_fails() -> None:
    budget, origin, table = raise_world()
    origin["ceilings"]["readset"]["BUILD"] = 3000  # a slack origin ceiling: the raise below stays within delta + 500
    table["P4"]["readset.BUILD"] = 1900
    table[""]["readset.BUILD"] = 1500  # projected delta 400
    budget["ceilings"]["readset"]["BUILD"] = 3400  # raise 400 <= 400 + 500 but 3400 > projection 1900 + 1000
    budget["ceiling_raises"] = [{"phase": "P4", "key": "readset.BUILD", "from": 3000, "to": 3400, "projected_delta": 400}]
    problems = check_raise_bounds(budget, origin, fixture_projector(table))
    check("raise_over_projection_plus_1000_fails", any("+ 1000" in p for p in problems), str(problems))


def main() -> int:
    for name in sorted(n for n in globals() if n.startswith("test_")):
        try:
            globals()[name]()
        except Exception as exc:  # noqa: BLE001 - a crashing test is a failure of that test, reported by name
            fail(name, f"{type(exc).__name__}: {exc}")
    print()
    for e in _errors:
        print(e)
    print(f"Results: {_passes} passed, {len(_errors)} failed, {_skips} skipped")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
