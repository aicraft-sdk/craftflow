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
import re
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
REACH_AMENDMENTS = (None, "D17-B")
REL_ADR = "docs/ai/decisions/0073-craftflow-policy-first-router-kernel.md"
DEFAULT_TIERS = ("guaranteed", "conditional", "with_contingencies")
SHAPE = {"baseline_ref", "measured_at_sha", "last_phase", "merged_phases", "conditions", "reach_amendment", "ceilings", "ceiling_raises", "targets",
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
    if "reach_option" in d and d["reach_option"] not in ("REACH-A", "REACH-B"):
        problems.append(f"reach_option {d['reach_option']!r} not in REACH-A/REACH-B")
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
        if isinstance(r.get("decided_by"), str) and r["decided_by"].strip() and r["decided_by"] != "David":
            problems.append(f"revision {r.get('phase')}: decided_by must be David (advisory 4a), not {r['decided_by']!r}")
    return problems


def check_revision_authors(budget: dict, origin: Optional[dict]) -> List[str]:
    """K4: every projection_revisions entry this change adds against origin/main (all of them without an origin) names its cause and is decided
    by David, also when the cut plan did not change (check_cutplan_revision only looks at entries that go with a cut-plan change)."""
    revs = budget.get("projection_revisions", [])
    new = revs[len((origin or {}).get("projection_revisions", [])):] if origin is not None else revs
    problems = []
    for r in new:
        if not isinstance(r.get("cause"), str) or not r["cause"].strip():
            problems.append(f"revision {r.get('phase')}: cause missing")
        if r.get("decided_by") != "David":
            problems.append(f"revision {r.get('phase')}: decided_by must be David (advisory 4a), not {r.get('decided_by')!r}")
    return problems


def check_reach_amendment_record(budget: dict, adr: str) -> List[str]:
    """K4: a non-null `reach_amendment` is David's P2C decision and needs its record. Before the caps exist: a projection_revisions entry whose
    cause names the amendment, decided by David. With caps: `enforced_caps_decision.reach_amendment` equal to the budget's value, decided by
    David. Either way a line of ADR-0073's `## Budget decision (P2C)` section holds `reach_amendment` and the value. A decision record that
    names an amendment the budget does not carry is a problem too."""
    value = budget.get("reach_amendment")
    d = budget.get("enforced_caps_decision") or {}
    if value is None:
        return [] if d.get("reach_amendment") is None else [f"enforced_caps_decision records reach_amendment {d['reach_amendment']!r}, the budget carries null"]
    problems = []
    if budget.get("enforced_caps") is not None:
        if d.get("reach_amendment") != value:
            problems.append(f"reach_amendment {value!r} has no matching enforced_caps_decision.reach_amendment (got {d.get('reach_amendment')!r})")
        if d.get("decided_by") != "David":
            problems.append("enforced_caps_decision.decided_by must be David for a reach_amendment")
    elif not any("reach_amendment" in str(r.get("cause", "")) and value in str(r.get("cause", "")) and r.get("decided_by") == "David"
                 for r in budget.get("projection_revisions", [])):
        problems.append(f"reach_amendment {value!r} needs a projection_revisions entry whose cause names reach_amendment and {value} and whose "
                        "decided_by is David")
    section = adr.partition("## Budget decision (P2C)")[2]
    if not any("reach_amendment" in ln and value in ln for ln in section.splitlines()):
        problems.append(f"ADR-0073 has no line in '## Budget decision (P2C)' that holds reach_amendment and {value}")
    return problems


def sha_exists_in_repo(sha: str) -> bool:
    return isinstance(sha, str) and bool(sha) and subprocess.run(["git", "-C", str(REPO_ROOT), "cat-file", "-e", f"{sha}^{{commit}}"],
                                                                  capture_output=True).returncode == 0


def is_descendant(sha: str, ancestor: str) -> bool:
    """True when `sha` is a commit strictly after `ancestor` (git merge-base --is-ancestor; the same commit is not a descendant)."""
    rev = lambda s: subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--verify", "-q", f"{s}^{{commit}}"],  # noqa: E731
                                   capture_output=True, text=True).stdout.strip()
    a, d = rev(sha) if isinstance(sha, str) and sha else "", rev(ancestor) if isinstance(ancestor, str) and ancestor else ""
    return bool(a) and bool(d) and a != d and subprocess.run(["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", d, a],
                                                             capture_output=True).returncode == 0


def origin_adr_text() -> Optional[str]:
    """ADR-0073 as origin/main has it: None without a remote, "" while the ADR is not on origin/main yet (every line is then new)."""
    if not origin_available():
        return None
    raw = git_show("origin/main", REL_ADR)
    return "" if raw is None else raw.decode("utf-8")


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


def adr_line_cites(a: dict, adr: str, origin_adr: Optional[str], same_change: bool = False) -> bool:
    """G4 + K5: an ADR-0073 line cites the amendment when it holds the key AND the new value (thousands separators allowed) AND at_sha,
    whether the line is new against origin/main's ADR or old (REM-FIX 3 let any new line that merely held the sha pass). When `same_change`
    (at_sha is the HEAD of this very change) the citing line must also be new against origin/main's ADR, a visible edit of the PR; with no
    origin ADR newness cannot be judged and that part is skipped. Residual: an amendment and its decision recorded in one PR remain a
    human-diff control (CODEOWNERS @volkz is also the agent identity)."""
    old = None if origin_adr is None else set(origin_adr.splitlines())
    values = (str(a["to"]), f"{a['to']:,}") if isinstance(a["to"], int) and not isinstance(a["to"], bool) else ()
    lines = [ln for ln in adr.splitlines() if a["at_sha"] in ln and a["key"] in ln and any(v in ln for v in values)]
    if same_change and old is not None:
        return any(ln not in old for ln in lines)
    return bool(lines)


def check_amendments(budget: dict, sha_exists: Callable[[str], bool], adr: str, descends: Callable[[str, str], bool],
                     origin_adr: Optional[str] = None, head: Optional[str] = None) -> List[str]:
    """F4 + G4 + K5: an amendment is {key, from, to, cause, decided_by: David, at_sha, adr: ADR-0073}; key is a cap, `from` is the cap in
    force at that point of the chain, at_sha is a commit that descends strictly from the decision's decided_at_sha (and is not this change's
    own `head` unless the citing line is new), and an ADR-0073 line cites it with the key, the new value and the sha (adr_line_cites).
    Append-only vs origin: append_only_problems."""
    decision = budget.get("enforced_caps_decision") or {}
    ams = decision.get("amendments") or []
    if not ams:
        return []
    if budget.get("enforced_caps") is None:
        return ["amendments recorded while enforced_caps is null"]
    decided = decision.get("decided_at_sha")
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
        elif not isinstance(decided, str) or not decided.strip() or not descends(sha, decided):
            problems.append(f"{w}: at_sha {sha} is not a commit after the decision's decided_at_sha {decided!r}")
        elif not adr_line_cites(a, adr, origin_adr, same_change=bool(head) and sha == head):
            problems.append(f"{w}: no ADR-0073 line holds {a['key']}, {a['to']} and at_sha {sha}"
                            f"{' (at_sha is this change HEAD, so the line must be new against origin/main ADR)' if head and sha == head else ''}")
        if isinstance(a["to"], int) and not isinstance(a["to"], bool):
            set_key(caps, a["key"], a["to"])
    return problems


def computed_fits(budget: dict, projector: Callable[[List[str]], Dict[str, int]]) -> Optional[bool]:
    """F5a + G3: does the projection of the whole committed cut plan (every phase, recorded conditions and reach_amendment: the revised
    full projection) fit the effective caps? None: no caps yet. `measured_within_phase_projection` stays per merged phase."""
    if budget.get("enforced_caps") is None:
        return None
    caps, now = effective_caps(budget), projector(list(prj.ALL_PHASES))
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
        out = prj.project(cutplan, state0, phases, flags, reach_amendment=budget.get("reach_amendment"))["effective"]
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
        and all(isinstance(v, bool) for v in b["conditions"].values()) and isinstance(b["enforce"], bool) \
        and b["reach_amendment"] in REACH_AMENDMENTS


def test_budget_shape() -> None:
    b = load_budget()
    stringy = copy.deepcopy(b)
    stringy["conditions"]["difference_17"] = "false"
    renamed = copy.deepcopy(b)
    renamed["conditions"] = {"difference_17": True, "live_verified": False}
    bad_amend = [dict(copy.deepcopy(b), reach_amendment=v) for v in ("D17-A", True, "", "d17-b")]
    good_amend = shape_ok(dict(copy.deepcopy(b), reach_amendment="D17-B")) and shape_ok(dict(copy.deepcopy(b), reach_amendment=None))
    no_key = {k: v for k, v in b.items() if k != "reach_amendment"}
    check("budget_shape", shape_ok(b) and not shape_ok(stringy) and not shape_ok(renamed) and good_amend
          and not any(shape_ok(x) for x in bad_amend) and not shape_ok(no_key),
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


def test_decision_record_reach_option_is_validated() -> None:
    """P2C: `reach_option` names the chosen reach option (REACH-A or REACH-B) and is validated when present; the live record carries it."""
    base = {"enforced_caps": {k: 1 for k in KEYS}, "enforced_caps_decision": {"option": "A", "decided_by": "David", "decided_at_sha": "abc",
            "adr": "ADR-0073", "projection_sha256": hashlib.sha256(b"projection").hexdigest()}}
    show = lambda sha, rel: b"projection" if sha == "abc" else None  # noqa: E731
    with_opt = lambda v: {**base, "enforced_caps_decision": {**base["enforced_caps_decision"], "reach_option": v}}  # noqa: E731
    controls = {"b_ok": check_decision_record(with_opt("REACH-B"), show) == [], "a_ok": check_decision_record(with_opt("REACH-A"), show) == [],
                "bad": bool(check_decision_record(with_opt("REACH-C"), show)), "empty": bool(check_decision_record(with_opt(""), show)),
                "none": bool(check_decision_record(with_opt(None), show))}
    live = (load_budget().get("enforced_caps_decision") or {}).get("reach_option")
    check("decision_record_reach_option_is_validated", all(controls.values()) and live == "REACH-B", f"controls={controls} live={live!r}")


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


def test_p2c_in_merged_phases() -> None:
    """P2C exit criterion 3: the merged P2C phase is recorded after P2, and the projection is still met with both merged."""
    b = load_budget()
    check("p2c_in_merged_phases", b["merged_phases"][:2] == ["P2", "P2C"],
          f"merged_phases={b['merged_phases']}, expected to start with ['P2', 'P2C'] (later phases append after them)")


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
            "at_sha": "bcd2345", "adr": "ADR-0073"}
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
    """F4 + G4: each amendment names an existing cap, the current effective value, a cause, David, a commit that descends from the
    decision's decided_at_sha and an ADR-0073 line that cites it with the key and the new value (or is new against origin/main's ADR)."""
    sha_exists = lambda s: s in ("abc1234", "bcd2345", "cde3456", "base000")  # noqa: E731
    descends = lambda at, decided: decided == "abc1234" and at in ("bcd2345", "cde3456")  # noqa: E731
    adr = "# ADR\n- kernel_bytes cap 35000 -> 36000 at bcd2345\n- kernel_bytes cap 36000 -> 37,000 at cde3456\n"
    run = lambda *ams, text=adr, origin=None: check_amendments(amended_budget(*ams), sha_exists, text, descends, origin)  # noqa: E731
    chain2 = amendment(**{"from": 36000, "to": 37000, "at_sha": "cde3456"})
    e6c = amendment(**{"to": 10 ** 9, "at_sha": "base000", "cause": "x"})  # the doubt-verifier's E6c: a sha the ADR already cites
    cites_only = "# ADR\n- decision pinned at base000\n"
    controls = {
        "good": run(amendment()) == [],
        "chain": run(amendment(), chain2) == [],
        "bare": bool(run({"key": "kernel_bytes", "to": 10 ** 9})),
        "wrong_from": bool(run(amendment(**{"from": 34000}))),
        "missing_cause": bool(run({k: v for k, v in amendment().items() if k != "cause"})),
        "empty_cause": bool(run(amendment(cause=" "))),
        "decided_by": bool(run(amendment(decided_by="agent"))),
        "unknown_sha": bool(run(amendment(at_sha="deadbee"), text=adr.replace("bcd2345", "deadbee"))),
        "not_in_adr": bool(run(amendment(), text="# ADR\nnothing about the sha\n")),
        "unknown_key": bool(run(amendment(key="nope"))),
        "wrong_adr": bool(run(amendment(adr="ADR-0001"))),
        "bool_to": bool(run(amendment(to=True))),
        "stale_from_after_chain": bool(run(amendment(), amendment(**{"from": 35000, "to": 40000}))),
        # G4: lineage. The sha must exist AND descend from decided_at_sha (equal or unrelated shas do not).
        "not_a_descendant": bool(run(e6c, text=cites_only + "- kernel_bytes 35000 -> 1000000000 at base000\n")),
        "equal_to_decided": bool(run(amendment(at_sha="abc1234"), text=adr.replace("bcd2345", "abc1234"))),
        # G4: the citing line must carry the key and the value, so an old line that merely cites the sha proves nothing
        "line_without_key_and_to": bool(run(amendment(), text="# ADR\n- decided at bcd2345\n", origin="# ADR\n- decided at bcd2345\n")),
        "line_without_to": bool(run(amendment(), text="# ADR\n- kernel_bytes at bcd2345\n", origin="# ADR\n- kernel_bytes at bcd2345\n")),
        "line_without_key": bool(run(amendment(), text="# ADR\n- 36000 at bcd2345\n", origin="# ADR\n- 36000 at bcd2345\n")),
        "wrong_to_in_line": bool(run(amendment(**{"to": 38000}), text=adr)),
        "formatted_to_ok": run(amendment(**{"from": 35000, "to": 37000, "at_sha": "cde3456"}), text="# ADR\n- kernel_bytes 37,000 at cde3456\n") == [],
        # K5: the citing line carries the key AND the new value AND the sha, whether it is new against origin/main's ADR or old;
        # a new line that merely mentions the sha (or an unrelated one) is not a citation any more
        "new_line_with_sha_alone": bool(run(amendment(), text="# ADR\n- decided at bcd2345\n", origin="# ADR\n")),
        "new_unrelated_line": bool(run(amendment(), text="# ADR\n- projection regenerated at bcd2345.\n", origin="# ADR\n")),
        "new_line_with_key_to_sha_ok": run(amendment(), text="# ADR\n- kernel_bytes 35000 -> 36000 at bcd2345\n", origin="# ADR\n") == [],
        "old_line_with_key_to_sha_ok": run(amendment(), origin=adr) == [],
        # K5: an at_sha that is the HEAD of this very change needs a citing line that is new against origin/main's ADR (a visible edit);
        # without a remote the newness cannot be judged and the rule does not apply (every origin comparison skips then)
        "head_with_old_line": bool(check_amendments(amended_budget(amendment()), sha_exists, adr, descends, adr, head="bcd2345")),
        "head_with_new_line_ok": check_amendments(amended_budget(amendment()), sha_exists, adr, descends, "# ADR\n", head="bcd2345") == [],
        "head_without_origin": check_amendments(amended_budget(amendment()), sha_exists, adr, descends, None, head="bcd2345") == [],
        "other_head_old_line_ok": check_amendments(amended_budget(amendment()), sha_exists, adr, descends, adr, head="cde3456") == [],
        "no_decided_sha": bool(check_amendments(dict(amended_budget(amendment()), enforced_caps_decision={"decided_by": "David", "amendments": [amendment()]}),
                                                sha_exists, adr, descends)),
    }
    head = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    live = check_amendments(load_budget(), sha_exists_in_repo, adr_text(), is_descendant, origin_adr_text(), head=head)
    # the same lineage rule against the real repository: HEAD descends from the baseline commit, the baseline does not descend from HEAD
    base = json.loads((FIXTURES / "router-baseline.lock.json").read_text(encoding="utf-8"))["baseline_sha"]
    git_ok = is_descendant(head, base) and not is_descendant(base, head) and not is_descendant(head, head) and not is_descendant("nope", head)
    check("amendments_are_validated", all(controls.values()) and not live and git_ok, f"controls={controls} live={live} git_ok={git_ok}")


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


def capped_budget(build_cap: int, amendment_key=None) -> dict:
    """A P2C-shaped budget: every cap generous except the BUILD read set; `amendment_key` is router-budget.json `reach_amendment`."""
    b = load_budget()
    caps = copy.deepcopy(b["ceilings"])
    caps["readset"]["BUILD"] = build_cap
    for k in KEYS:
        if k != "readset.BUILD":
            set_key(caps, k, 10 ** 7)
    b.update(enforce=True, enforced_caps=caps, reach_amendment=amendment_key,
             enforced_caps_decision={"option": "B", "decided_by": "David", "adr": "ADR-0073", "decided_at_sha": "abc1234", "amendments": []})
    return b


def test_computed_fits_projects_the_full_plan_with_the_recorded_amendment() -> None:
    """G3: computed_fits projects every phase (the revised full projection), not merged_phases, with the recorded conditions and the
    recorded reach_amendment; the tagged D17 rows count only once the budget records D17-B."""
    out = {}
    for cap, amend in ((90000, None), (90000, "D17-B"), (100000, None), (100000, "D17-B")):
        b = capped_budget(cap, amend)
        out[(cap, amend)] = computed_fits(b, effective_values(b))
    b = capped_budget(100000)
    stale = effective_values(b)(["P2"])["readset.BUILD"]  # what the merged-phases-only projection used to compare (P2 alone; P11 pins P2, not the live merged list)
    full = effective_values(b)(list(prj.ALL_PHASES))["readset.BUILD"]
    seen = []
    computed_fits(b, lambda phases: seen.append(list(phases)) or {k: 0 for k in KEYS})
    check("computed_fits_projects_the_full_plan_with_the_recorded_amendment",
          out == {(90000, None): False, (90000, "D17-B"): True, (100000, None): True, (100000, "D17-B"): True}
          and stale > 100000 >= full > 90000 and seen == [list(prj.ALL_PHASES)],
          f"fits={out} merged_only_BUILD={stale} full_BUILD={full} projected_phases={seen}")


def test_effective_projection_leaves_out_infeasible_rows_unless_amended() -> None:
    """G2 on the budget side: the measured-within-projection and fits projector follow the recorded reach_amendment."""
    base = effective_values(dict(load_budget(), reach_amendment=None))(list(prj.ALL_PHASES))
    amended = effective_values(dict(load_budget(), reach_amendment="D17-B"))(list(prj.ALL_PHASES))
    check("effective_projection_leaves_out_infeasible_rows_unless_amended",
          amended["readset.BUILD"] < base["readset.BUILD"] and amended["kernel_bytes"] > base["kernel_bytes"],  # D17-B: -12,606 B, kernel +101
          f"default={base['readset.BUILD']} amended={amended['readset.BUILD']}")


def feasibility_problems(proj: dict, origin: Optional[dict]) -> List[str]:
    """Ratchet on the residual limits of the reach report: the default tiers carry no `unreachable` removal (a new untagged row P0 would
    refuse shows up there), and `single_token_units` (units no @reach quote can ever cover) never grows against origin/main's projection."""
    tiers = proj["reach_feasibility"]["tiers"]
    problems = [f"{t}: unreachable is {tiers[t]['unreachable']}, expected 0" for t in DEFAULT_TIERS if tiers[t]["unreachable"] != 0]
    if origin is not None:
        old = origin["reach_feasibility"]["tiers"]
        problems += [f"{t}: single_token_units grew {old[t]['single_token_units']} -> {tiers[t]['single_token_units']}"
                     for t in DEFAULT_TIERS if tiers[t]["single_token_units"] > old[t]["single_token_units"]]
    return problems


def test_feasibility_ratchet_vs_origin_main() -> None:
    mk = lambda unreachable, single: {"reach_feasibility": {"tiers": {t: {"unreachable": unreachable, "single_token_units": single}  # noqa: E731
                                                                      for t in DEFAULT_TIERS}}}
    controls = {
        "clean": feasibility_problems(mk(0, 1), mk(0, 1)) == [],
        "shrinks": feasibility_problems(mk(0, 0), mk(0, 1)) == [],
        "unreachable_in_default_tier": bool(feasibility_problems(mk(1, 1), mk(0, 1))),
        "single_token_grows": bool(feasibility_problems(mk(0, 2), mk(0, 1))),
        "no_origin": feasibility_problems(mk(0, 5), None) == [],
    }
    proj = json.loads(PROJECTION.read_text(encoding="utf-8"))
    raw = git_show("origin/main", REL_PROJECTION) if origin_available() else None
    if not origin_available():
        skip("feasibility_ratchet_vs_origin_main:live", "no origin/main ref (CI must not skip)")
        live = feasibility_problems(proj, None)
    elif raw is None or "reach_feasibility" not in json.loads(raw.decode("utf-8")):
        ok("feasibility_ratchet_vs_origin_main:first_introduction")  # origin/main has no reach_feasibility block yet: only the absolute rule
        live = feasibility_problems(proj, None)
    else:
        live = feasibility_problems(proj, json.loads(raw.decode("utf-8")))
    check("feasibility_ratchet_vs_origin_main", all(controls.values()) and not live, f"controls={controls} live={live}")


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
        "agent_decider": bool(run(mk(revision("o" * 64, "h" * 64, decided_by="agent")))),  # G4: advisory 4a, only David decides
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


def test_adr_discloses_the_rem_fix_3_findings() -> None:
    """G1/G5: ADR-0073 carries the corrected coverage figure, the REACH-A kernel penalty and the reach_amendment key; the stale 309 is gone."""
    text = adr_text()
    proj = json.loads(PROJECTION.read_text(encoding="utf-8"))["reach_feasibility"]["tiers"]["guaranteed"]
    need = ("## P2 REM-FIX 3 notes", "reach_a_kernel_penalty_bytes", "3,913 B", "reach_amendment", "167 / 88 / 160", "rewrite rows inside a moved section")
    split = [proj["reach_loss_split"][c]["units"] for c in ("pure_2_to_1_covered", "drops_to_0", "drops_workflow_or_variant")]
    check("adr_discloses_the_rem_fix_3_findings",
          # 167 / 88 / 160 at REM-FIX 3 (the ADR keeps that figure as history); P9's drafted rows (the section 13 move, the pointer, xref and dedupe rows)
          # are read by the projection's policy-map diff and take one unit out of each of the first two buckets: 166 / 87 / 160.
          # P10 remediation (final text and rows; the two fence rows are `pointer` rows now): 159 / 86 / 151 becomes 159 / 79 / 149
          # P10 (its drafted rows are read the same way, and the relocation is real in the live tree): 159 / 86 / 151, and the 3,913 B kernel penalty
          # (the one single-token unit the P2 analysis disclosed) is 0 because the one-word units stay in the kernel as stand-ins (ADR P10 notes)
          # P11 (its drafted rows are read the same way; the split sends one more unit to a covered bucket): 159 / 79 / 149 becomes 159 / 79 / 147
          # P11 remediation (the qualified bake-off references, the gate fences repeated in verify-dispatch.md, the Future Third Router rationale row): 162 / 75 / 144
          all(n in text for n in need) and "309 of them into happy-path files" not in text and split == [162, 75, 144]
          and proj["reach_a_kernel_penalty_bytes"] == 0,
          f"missing={[n for n in need if n not in text]} split={split}")


# ---------------------------------------------------------------------------------------------- P2 REM-FIX 4 tests (K4, K5, K6)

DECISION_LINE = "- Reach model: `reach_amendment` is `D17-B` (David, P2C): a happy-path file counts as reach 2.\n"


def adr_with(line: str = "", section: str = "## Budget decision (P2C)") -> str:
    return f"# ADR\n\n- `reach_amendment: \"D17-B\"` is only described here, in the notes.\n\n{section}\n\n{line}"


def test_reach_amendment_needs_a_decision_record() -> None:
    """K4: `reach_amendment: "D17-B"` switches the effective projection, the fits flag and the signing burden, yet only an incidental
    live-state readset assertion noticed a flipped key (budget 31/31 stayed green with `decided_by: agent` and no ADR line). It now needs the
    decision record: before the caps exist a projection_revisions entry naming D17-B decided by David, after they exist
    `enforced_caps_decision.reach_amendment` with the same value decided by David, and in both cases a line in ADR-0073's Budget decision
    (P2C) section that holds `reach_amendment` and `D17-B`."""
    base = dict(load_budget(), reach_amendment="D17-B", enforce=False, enforced_caps=None, enforced_caps_decision=None)
    rev = lambda cause="P2C decision: reach_amendment D17-B", by="David": [{"phase": "P2C", "from_projection_sha256": "a", "to_projection_sha256": "b",  # noqa: E731
                                                                          "fits_enforced_caps": None, "cause": cause, "decided_by": by}]
    pre = dict(base, projection_revisions=rev())
    caps = copy.deepcopy(load_budget()["ceilings"])
    post = dict(base, enforce=True, enforced_caps=caps, projection_revisions=[],
                enforced_caps_decision={"option": "B", "decided_by": "David", "adr": "ADR-0073", "decided_at_sha": "abc1234", "amendments": [],
                                        "reach_amendment": "D17-B"})
    adr = adr_with(DECISION_LINE)
    flipped_alone = dict(load_budget(), reach_amendment="D17-B")  # the doubt-verifier's clone: only the key flipped
    controls = {
        "null_needs_nothing": check_reach_amendment_record(load_budget(), "# ADR\n") == [],
        "flipped_key_alone": bool(check_reach_amendment_record(flipped_alone, adr_text())),
        "flipped_key_with_an_adr_line_but_no_revision": bool(check_reach_amendment_record(flipped_alone, adr)),
        "flipped_key_with_a_revision_but_no_adr_line": bool(check_reach_amendment_record(pre, adr_with(""))),
        "pre_caps_cause_names_only_the_value": bool(check_reach_amendment_record(dict(pre, projection_revisions=rev(cause="reads D17-B")), adr)),
        "pre_caps_good": check_reach_amendment_record(pre, adr) == [],
        "pre_caps_cause_without_d17b": bool(check_reach_amendment_record(dict(pre, projection_revisions=rev(cause="regenerated")), adr)),
        "pre_caps_agent": bool(check_reach_amendment_record(dict(pre, projection_revisions=rev(by="agent")), adr)),
        "post_caps_good": check_reach_amendment_record(post, adr) == [],
        "post_caps_no_key": bool(check_reach_amendment_record(dict(post, enforced_caps_decision={k: v for k, v in post["enforced_caps_decision"].items()
                                                                                                 if k != "reach_amendment"}), adr)),
        "post_caps_other_value": bool(check_reach_amendment_record(dict(post, enforced_caps_decision=dict(post["enforced_caps_decision"], reach_amendment=None)), adr)),
        "post_caps_agent": bool(check_reach_amendment_record(dict(post, enforced_caps_decision=dict(post["enforced_caps_decision"], decided_by="agent")), adr)),
        "decision_without_budget_key": bool(check_reach_amendment_record(dict(post, reach_amendment=None), adr)),
        "no_adr_line": bool(check_reach_amendment_record(pre, adr_with(""))),
        "line_only_in_the_notes": bool(check_reach_amendment_record(pre, adr_with("", section="## Other notes") + DECISION_LINE)),
        "line_without_d17b": bool(check_reach_amendment_record(pre, adr_with("- `reach_amendment` decided.\n"))),
    }
    live = check_reach_amendment_record(load_budget(), adr_text())
    check("reach_amendment_needs_a_decision_record", all(controls.values()) and not live, f"controls={controls} live={live}")


def test_revision_entries_are_decided_by_david() -> None:
    """K4: `projection_revisions[].decided_by == "David"` was enforced only next to a cut-plan change; a budget-only revision (a new entry,
    no cut-plan change) now needs it too, with a cause."""
    origin = {"projection_revisions": [revision("a" * 64, "b" * 64)]}
    mk = lambda *new: {"projection_revisions": origin["projection_revisions"] + list(new)}  # noqa: E731
    controls = {
        "no_new_entry": check_revision_authors(mk(), origin) == [],
        "david": check_revision_authors(mk(revision("b" * 64, "c" * 64)), origin) == [],
        "agent": bool(check_revision_authors(mk(revision("b" * 64, "c" * 64, decided_by="agent")), origin)),
        "missing": bool(check_revision_authors(mk(revision("b" * 64, "c" * 64, decided_by=None)), origin)),
        "no_cause": bool(check_revision_authors(mk(revision("b" * 64, "c" * 64, cause=" ")), origin)),
        "old_entries_not_judged": check_revision_authors({"projection_revisions": [revision("a" * 64, "b" * 64, decided_by="agent")]},
                                                         {"projection_revisions": [revision("a" * 64, "b" * 64, decided_by="agent")]}) == [],
        "no_origin_judges_all": bool(check_revision_authors(mk(revision("b" * 64, "c" * 64, decided_by="agent")), None)),
    }
    status, live_origin = origin_budget()
    live = check_revision_authors(load_budget(), live_origin) if status in ("ok", "new") else []
    if status == "skip":
        skip("revision_entries_are_decided_by_david:live", "no origin/main ref (CI must not skip)")
    check("revision_entries_are_decided_by_david", all(controls.values()) and not live, f"controls={controls} live={live}")


def test_adr_discloses_the_rem_fix_4_findings() -> None:
    """K1-K6: ADR-0073 carries the REM-FIX 4 notes with the corrected figures, and the stale 576 / 787 are fixed."""
    text = adr_text()
    sb = json.loads(PROJECTION.read_text(encoding="utf-8"))["reach_feasibility"]["tiers"]["guaranteed"]["signing_burden"]
    need = ("## P2 REM-FIX 4 notes", "disjoint", "definitional", "covered-first", "variant_split", "decision record", "R-M2", "32,881 B", "32,762 B",
            "key AND", "rows_upper_bound", "591", "424", "41-49")
    check("adr_discloses_the_rem_fix_4_findings",
          all(n in text for n in need) and "`unmapped_removed 576`" not in text and "`unmapped_removed 787`" not in text
          and "unmapped_removed 581" in text and "unmapped_removed 794" in text
          # 591 / 424 at REM-FIX 4 (the ADR keeps those figures as history); P6's six literal-to-key Rewrites rows take one vanishing id away,
          # and P8's thirteen listed rows (one of them a widening row for worktree.md) take one widening file away: 590 / 423 to 589 / 422
          # and P9's drafted rows (the section 13 move, the pointer, xref and dedupe rows and the two widening rows for the new stage files)
          # change the REACH-A upper bound once more: 589 / 422 to 588 / 422; P10's drafted rows and the real relocation make it 579 / 420;
          # the P10 remediation (pointer rows for the two fences, fifteen xref rows) makes it 584 / 425
          # P11's drafted rows (the protocol, build and plan splits) make it 580 / 421
          # P11 remediation (re-derived): 580 / 421 becomes 580 / 418
          # P11 remediation 2 (re-derived): the two fast-path.md xref rows add two vanishing ids: 580 / 418 becomes 582 / 420
          # P11 remediation 3 (re-derived): the closing-sentence xref row of fast-path.md adds one vanishing id: 582 / 420 becomes 583 / 421
          # P12 (re-derived): the five Shared-with-Cursor dedupe rows and the router description trigger row add two vanishing ids: 583 / 421 becomes 585 / 423
          and (sb["REACH_A"]["rows_upper_bound"], sb["REACH_B"]["rows_upper_bound"]) == (585, 423),
          f"missing={[n for n in need if n not in text]}")


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


# ---------------------------------------------------------------------------------------------- P3: skill descriptions

DESC_MAX = 160
# Explicit, closed exception list for the 160-char rule. craftflow-router keeps its original 499-char description:
# deferred to P10/P11, where a signed kernel unit can carry the trigger list. Until then the plan's one-line draft is blocked
# by the rule-inventory 60% token-retention check and the guarded word 'not' ("do not stop at describing capabilities"),
# which no policy-map row can satisfy. The list must equal exactly this set; adding a skill here is a reviewed change.
DESC_LONG_EXCEPTIONS = frozenset({"craftflow-router"})
ROUTER_DESC_MAX = 499  # the exception has no individual cap otherwise: it may never grow past today's text
# Plan P3 task 3 drafts that do not open with "Use " (read-only inspection skills, the update skill and the Cursor stub).
DESC_OTHER_START_OK = frozenset({"status", "failure-digest", "retro", "update", "cursor-router"})
INTERNAL_STUBS = frozenset({"architecture-patterns", "brainstorming", "code-generation", "code-review-patterns", "intent-interview",
                            "planning-patterns", "research", "session-memory", "test-driven-development"})
_SKILL_CALL = re.compile(r"""Skill\(\s*skill\s*=\s*["']craftflow:([a-z0-9-]+)["']""")
_FM_FLAG = re.compile(r"^disable-model-invocation:", re.M)


def norm(text: str) -> str:
    """BOM and CRLF tolerant view of a file: detectors read this, the format guard reports the raw defect."""
    return text.lstrip("﻿").replace("\r\n", "\n")


def skill_texts() -> Dict[str, str]:
    return {p.parent.name: p.read_bytes().decode("utf-8") for p in sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))}


def frontmatter_block(text: str) -> str:
    m = re.match(r"^---\n(.*?)\n---\n", norm(text), re.S)
    return m.group(1) if m else ""


def desc_of(text: str) -> str:
    return rs.core.effective_description(norm(text))


def over_limit(texts: Dict[str, str], limit: int = DESC_MAX, exceptions=frozenset()) -> Dict[str, int]:
    out = {}
    for name, text in texts.items():
        if name in exceptions:
            continue
        d = desc_of(text)
        if len(d) > limit or "\n" in d:
            out[name] = len(d)
    return out


def format_problems(texts: Dict[str, str], exceptions=frozenset(), other_start_ok=frozenset()) -> Dict[str, str]:
    """Per skill: raw BOM or CRLF, empty frontmatter, empty description, or a description that does not open with
    'Use ' or 'Internal skill' (the exception and the named plan drafts excluded)."""
    out = {}
    for name, text in texts.items():
        if text.startswith("﻿"):
            out[name] = "BOM"
        elif "\r" in text:
            out[name] = "CRLF"
        elif not frontmatter_block(text).strip():
            out[name] = "empty frontmatter"
        elif not desc_of(text).strip():
            out[name] = "empty description"
        elif name not in exceptions and name not in other_start_ok and not desc_of(text).startswith(("Use ", "Internal skill")):
            out[name] = "description does not start with 'Use ' or 'Internal skill'"
    return out


def flagged(texts: Dict[str, str]) -> List[str]:
    return sorted(n for n, t in texts.items() if _FM_FLAG.search(frontmatter_block(t)))


def invoked_or_preloaded(skill_texts_: Dict[str, str], agent_texts: Dict[str, str]) -> set:
    names = set()
    for text in list(skill_texts_.values()) + list(agent_texts.values()):
        names.update(_SKILL_CALL.findall(text))
    for text in agent_texts.values():
        names.update(re.findall(r"^\s+-\s+craftflow:([a-z0-9-]+)\s*$", frontmatter_block(text), re.M))
    return names


def agent_texts() -> Dict[str, str]:
    return {p.stem: p.read_bytes().decode("utf-8") for p in sorted((PLUGIN_ROOT / "agents").glob("*.md"))}


def test_each_description_le_160() -> None:
    texts = skill_texts()
    long_desc = "a" * 161
    probe = {"x": "---\nname: x\ndescription: |\n  " + "a" * 100 + "\n  b\n---\n", "y": "---\nname: y\ndescription: \"" + long_desc + "\"\n---\n",
             "crlf": "---\r\nname: c\r\ndescription: \"" + long_desc + "\"\r\n---\r\n",
             "bom": "﻿---\nname: b\ndescription: \"" + long_desc + "\"\n---\n"}
    detects = set(over_limit(probe)) == {"x", "y", "crlf", "bom"}
    # negative control: an exception for one name never excuses another over-long skill
    control = set(over_limit(probe, exceptions=frozenset({"x"}))) == {"y", "crlf", "bom"}
    closed = DESC_LONG_EXCEPTIONS == frozenset({"craftflow-router"})
    over = over_limit(texts, exceptions=DESC_LONG_EXCEPTIONS)
    router_len = len(desc_of(texts["craftflow-router"]))
    tight = {n: len(desc_of(texts[n])) for n in ("cursor-router", "research") if len(desc_of(texts[n])) > 100}
    check("each_description_le_160", detects and control and closed and not over and not tight and router_len <= ROUTER_DESC_MAX,
          f"detector_ok={detects} control_ok={control} exceptions_closed={closed}; one-line and <= {DESC_MAX} violated by {over}; "
          f"cursor-router/research over 100: {tight}; router description {router_len} (cap {ROUTER_DESC_MAX})")


def test_every_skill_has_frontmatter_and_a_use_description() -> None:
    texts = skill_texts()
    probes = {"crlf": "---\r\nname: c\r\ndescription: \"Use when x\"\r\n---\r\n", "bom": "﻿---\nname: b\ndescription: \"Use when x\"\n---\n",
              "nodesc": "---\nname: n\nallowed-tools: Read\n---\n", "nofm": "# no frontmatter\n", "bad": "---\nname: z\ndescription: \"Hello\"\n---\n",
              "good": "---\nname: g\ndescription: \"Use when x\"\n---\n", "stub": "---\nname: s\ndescription: \"Internal skill. x\"\n---\n"}
    got = format_problems(probes)
    guard = set(got) == {"crlf", "bom", "nodesc", "nofm", "bad"} and got["crlf"] == "CRLF" and got["bom"] == "BOM"
    tolerant = flagged({"c": "---\r\nname: c\r\ndisable-model-invocation: true\r\n---\r\n", "b": "﻿---\nname: b\ndisable-model-invocation: true\n---\n"}) == ["b", "c"]
    spare = DESC_OTHER_START_OK == frozenset({"status", "failure-digest", "retro", "update", "cursor-router"})
    bad = format_problems(texts, DESC_LONG_EXCEPTIONS, DESC_OTHER_START_OK)
    dirs = sorted(p.name for p in (PLUGIN_ROOT / "skills").iterdir() if p.is_dir() and not p.name.startswith("_"))
    missing = [d for d in dirs if d not in texts]
    check("every_skill_has_frontmatter_and_a_use_description", guard and tolerant and spare and not bad and not missing,
          f"guard_ok={guard} crlf_bom_tolerant={tolerant} start_exceptions_closed={spare}; problems={bad}; dirs without SKILL.md={missing}")


def test_no_skill_has_disable_model_invocation() -> None:
    probe = {"x": "---\nname: x\ndisable-model-invocation: true\n---\nbody\n", "y": "---\nname: y\n---\ndisable-model-invocation: true\n"}
    detects = flagged(probe) == ["x"]
    bad = flagged(skill_texts())
    check("no_skill_has_disable_model_invocation", detects and not bad,
          f"detector_ok={detects}; flagged={bad}. This round ships the flag nowhere; adding it is a deferred follow-up "
          "that needs live proof (HV-3) that Skill-tool invocation still works")


def test_no_disable_model_invocation_on_invoked_or_preloaded_skills() -> None:
    texts, agents = skill_texts(), agent_texts()
    used = invoked_or_preloaded(texts, agents)
    probe_skills = {"alpha": "---\nname: alpha\ndisable-model-invocation: true\n---\n"}
    probe_agents = {"a": "---\nname: a\nskills:\n  - craftflow:alpha\n---\n"}
    detects = invoked_or_preloaded({}, probe_agents) == {"alpha"} and invoked_or_preloaded({"r": 'Skill(skill="craftflow:alpha")'}, {}) == {"alpha"}
    bad = sorted(set(flagged(texts)) & used)
    check("no_disable_model_invocation_on_invoked_or_preloaded_skills", detects and len(used) >= 10 and not bad,
          f"detector_ok={detects}; invoked_or_preloaded={len(used)}; flagged-and-used={bad}; probe={flagged(probe_skills)}")


def test_internal_stubs_keep_prefix() -> None:
    texts = skill_texts()
    derived = frozenset(n for n, t in texts.items() if desc_of(t).startswith("Internal skill"))
    check("internal_stubs_keep_prefix", derived == INTERNAL_STUBS and len(derived) == 9,
          f"skills with the 'Internal skill' prefix must be exactly the {len(INTERNAL_STUBS)} stubs; "
          f"lost the prefix={sorted(INTERNAL_STUBS - derived)} gained it={sorted(derived - INTERNAL_STUBS)}")


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
