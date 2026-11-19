#!/usr/bin/env python3
"""Tests for craftflow_routing_law.py and the routing corpus (H2).

Run: python3 tests/fixtures/test_craftflow_routing_law.py

The corpus (routing-cases.json) is the reference model for the primary-deliverable
routing law. Every case is model:true: route() must produce it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = PLUGIN_ROOT.parents[3]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_routing_law as law  # noqa: E402

CASES = json.loads((Path(__file__).resolve().parent / "routing-cases.json").read_text(encoding="utf-8"))["cases"]
ADR_MATCHES = sorted((REPO_ROOT / "docs" / "ai" / "decisions").glob("0075-*.md"))
ADR = ADR_MATCHES[0] if ADR_MATCHES else None

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
    ok(name) if cond else fail(name, reason)


def test_a_protocol_table_parity() -> None:
    text = (PLUGIN_ROOT / "skills" / "_shared" / "router-protocol.md").read_text(encoding="utf-8")
    parsed = law.parse_routing_table(text)
    check("(a) protocol Intent Routing table has 8 rows equal to ROUTING_TABLE",
          len(parsed) == 8 and parsed == law.ROUTING_TABLE, f"parsed={parsed!r}")


def test_b_cases_match_route() -> None:
    check("(b) corpus holds at least 50 cases", len(CASES) >= 50, f"n={len(CASES)}")
    ids = [c["id"] for c in CASES]
    check("(b) case ids are unique", len(ids) == len(set(ids)), f"ids={ids!r}")
    check("(b) every case is model:true", all(c.get("model") is True for c in CASES))
    bad = []
    for c in CASES:
        got = law.route(c["prompt"], c.get("hint"))["workflow"]
        if got != c["expected"]:
            bad.append((c["id"], c["prompt"], c["expected"], got))
    check("(b) route(prompt, hint) equals expected for every case", not bad, f"mismatches={bad!r}")
    check("(b) all eight workflows are covered by expected values",
          {c["expected"] for c in CASES} == {row[3] for row in law.ROUTING_TABLE})


def test_c_empty_is_build() -> None:
    r = law.route("")
    check("(c) empty request routes BUILD", r["workflow"] == "BUILD" and r["matched"] == [], f"r={r!r}")


def test_d_determinism() -> None:
    unstable = [
        c["id"] for c in CASES
        if len({json.dumps(law.route(c["prompt"], c.get("hint")), sort_keys=True) for _ in range(100)}) != 1
    ]
    check("(d) 100 identical runs give identical results for every case", not unstable, f"unstable={unstable!r}")


def test_e_matched_present_in_text() -> None:
    bad = []
    for c in CASES:
        lowered = c["prompt"].lower()
        for kw in law.route(c["prompt"], c.get("hint"))["matched"]:
            if law.find_keyword(lowered, kw) < 0:
                bad.append((c["id"], kw))
    check("(e) matched lists only keywords present in the text", not bad, f"bad={bad!r}")
    r = law.route("explain how the error handler works")
    check("(e) matched is the winning row's keywords", r["matched"] == ["explain"], f"r={r!r}")


def test_f_legacy_model() -> None:
    bad = []
    for c in CASES:
        legacy = law.legacy_route_of(c["prompt"])
        if legacy != c["legacy_route"]:
            bad.append((c["id"], "legacy_route", c["legacy_route"], legacy))
        if (legacy != c["expected"]) != bool(c.get("legacy_flip")):
            bad.append((c["id"], "flip flag", legacy, c["expected"], c.get("legacy_flip")))
    check("(f) legacy_route matches the pre-harvest model and legacy_flip is set exactly on differences",
          not bad, f"bad={bad!r}")
    legacy_cases = [c for c in CASES if c["id"].startswith("L")]
    by_route = {r: sum(1 for c in legacy_cases if c["legacy_route"] == r) for r in ("BUILD", "DEBUG", "PLAN", "REVIEW")}
    check("(f) at least 15 legacy prompts (5 BUILD, 4 DEBUG, 3 PLAN, 3 REVIEW)",
          by_route == {"BUILD": 5, "DEBUG": 4, "PLAN": 3, "REVIEW": 3}, f"by_route={by_route!r}")


def adr_flip_prompts() -> set:
    """The quoted prompts of the 'Routing flips' list in ADR-0075 ## Impact."""
    if ADR is None:
        return set()
    text = re.sub(r"\s+", " ", ADR.read_text(encoding="utf-8"))
    start = text.find("**Routing flips")
    end = text.find("**Deferred.**", start)
    section = text[start:end] if start != -1 and end != -1 else ""
    first = section.find("- Group (")
    if first == -1:
        return set()
    return set(re.findall(r'"([^"]+)"', section[first:]))


def test_g_flips_match_adr() -> None:
    flips = {c["prompt"] for c in CASES if c.get("legacy_flip")}
    adr = adr_flip_prompts()
    check("(g) legacy_flip prompts equal the ADR-0075 Routing flips list", flips == adr,
          f"only_in_corpus={sorted(flips - adr)!r} only_in_adr={sorted(adr - flips)!r}")


def test_h_lead_clause() -> None:
    cases = (
        ("update ISSUE-12.md and more", "update ISSUE-12.md"),
        ("fix test_cli.py then run it", "fix test_cli.py"),
        ("update ISSUE-12.md. fix the crash", "update ISSUE-12.md"),
        ("update ISSUE-12.md.", "update ISSUE-12.md"),
        ("add x, then y", "add x"),
        ("add x; fix y", "add x"),
        ("add test_cli.py", "add test_cli.py"),
        ("add x! fix y", "add x"),
        ("add x? fix y", "add x"),
        ("add x\nfix y", "add x\nfix y"),
        ("add x!", "add x"),
        ("add a?b", "add a?b"),
    )
    bad = [(t, law.lead_clause(t), want) for t, want in cases if law.lead_clause(t) != want]
    check("(h) '.' inside a file name does not end the lead clause; '. ' and end of text do", not bad, f"bad={bad!r}")
    check("(h) a sentence-end dot hides a later ERROR word from the lead clause",
          law.route("update notes.md. fix the crash")["workflow"] == "BUILD")
    check("(h) the same prompt without the sentence end keeps ERROR in the lead clause",
          law.route("update test_cli.py to fix the crash")["workflow"] == "DEBUG")


def test_matcher_boundaries() -> None:
    check("matcher: failing does not match fail", law.find_keyword("the failing test", "fail") < 0)
    check("matcher: issues does not match issue", law.find_keyword("incoming issues", "issue") < 0)
    check("matcher: 'look at #' matches before digits", law.find_keyword("look at #12", "look at #") == 0)
    check("matcher: how does X work is a pattern", law.find_keyword("how does the router work", "how does X work") == 0)


def main() -> int:
    print("test_craftflow_routing_law: running")
    test_a_protocol_table_parity()
    test_b_cases_match_route()
    test_c_empty_is_build()
    test_d_determinism()
    test_e_matched_present_in_text()
    test_f_legacy_model()
    test_g_flips_match_adr()
    test_h_lead_clause()
    test_matcher_boundaries()
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
