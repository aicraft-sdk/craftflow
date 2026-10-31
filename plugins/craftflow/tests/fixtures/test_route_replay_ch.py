#!/usr/bin/env python3
"""H6: the CODEBASE-HEALTH replay checks go RED on a hollow or reverted law (controls for craftflow_route_replay_ch.py).

Run: python3 tests/fixtures/test_route_replay_ch.py   (non-zero exit on any FAIL)
Literal controls for the validation rule, the JUST_GO pick and the transcript rules; one in-memory mutation per doc pin; then
mutated copies of the plugin tree: each mutation must turn the named replay check RED while an untouched copy passes.
"""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

import craftflow_route_replay_ch as ch  # noqa: E402
import craftflow_route_replay_handoff as rh  # noqa: E402

FIXTURE = PLUGIN_ROOT / "tests" / "fixtures" / "codebase-health-candidate-choice.json"
ROUTE, GRAPH, KERNEL, PROTOCOL, ROUTES = ch.ROUTE, ch.GRAPH, ch.KERNEL, ch.PROTOCOL, ch.CURSOR_ROUTES
_passes = 0
_errors: list = []


def check(name: str, cond: bool, reason: str = "") -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


def scan(**kw: Any) -> dict:
    return {"op": "scanner", "status": "CANDIDATES_FOUND", "candidate_count": 2, "strengths": ["Strong", "Speculative"], "top": 1,
            "report_exists": True, "outcome": "ok", **kw}


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def scenario(name: str) -> dict:
    return copy.deepcopy(next(sc for sc in fixture()["scenarios"] if sc["name"].startswith(name)))


def literal_controls() -> None:
    out = ch.contract_outcome
    check("rule: a valid CANDIDATES_FOUND is ok", out(scan()) == "ok", out(scan()))
    check("rule: a missing report file is a scanner failure", out(scan(report_exists=False)) == ch.FAILED, "")
    check("rule: a count that disagrees with the candidates is a scanner failure", out(scan(candidate_count=3)) == ch.FAILED, "")
    check("rule: zero candidates under CANDIDATES_FOUND is a scanner failure",
          out(scan(candidate_count=0, strengths=[], top=None)) == ch.FAILED, "")
    check("rule: a TOP_RECOMMENDATION past the count is a scanner failure", out(scan(top=3)) == ch.FAILED, "")
    check("rule: TOP_RECOMMENDATION 0 is a scanner failure", out(scan(top=0)) == ch.FAILED, "")
    check("rule: NO_CANDIDATES with a count is a scanner failure", out(scan(status="NO_CANDIDATES")) == ch.FAILED, "")
    check("rule: a valid NO_CANDIDATES is ok", out(scan(status="NO_CANDIDATES", candidate_count=0, strengths=[], top=None)) == "ok", "")
    check("rule: an unknown status is a scanner failure", out(scan(status="PASS")) == ch.FAILED, "")
    check("pick: TOP_RECOMMENDATION wins", ch.expected_pick(scan(top=2)) == 2, "")
    check("pick: else the first Strong", ch.expected_pick(scan(top=None, strengths=["Worth exploring", "Strong", "Strong"])) == 2, "")
    check("pick: else the first", ch.expected_pick(scan(top=None, strengths=["Speculative", "Worth exploring"])) == 1, "")

    def broken(name: str, mutate: Callable[[dict], None]) -> bool:
        sc = scenario(name)
        before = ch.sequence_problems(sc)
        mutate(sc)
        return before == [] and ch.sequence_problems(sc) != []

    def step(sc: dict, op: str, nth: int = 0) -> dict:
        return [s for s in sc["steps"] if s["op"] == op][nth]

    base = "candidates-found-choice"
    check("transcript: a wrong announce is caught", broken(base, lambda sc: step(sc, "announce").update(text="-> BUILD workflow (signals: x)")), "")
    check("transcript: a wrong mint phase is caught", broken(base, lambda sc: step(sc, "mint").update(phase="build")), "")
    check("transcript: the report opened on linux is caught", broken(base, lambda sc: step(sc, "report_shown").update(platform="linux")), "")
    check("transcript: a question missing None is caught", broken(base, lambda sc: step(sc, "question").update(options=["1", "2"])), "")
    check("transcript: a grill that leaves the gate is caught", broken(base, lambda sc: step(sc, "grill").update(pending_gate="")), "")
    check("transcript: a grill on another candidate is caught", broken(base, lambda sc: step(sc, "grill").update(candidate=2)), "")
    check("transcript: a design outside docs/plans is caught", broken(base, lambda sc: step(sc, "design_saved").update(path="design.md")), "")
    check("transcript: a design captured elsewhere is caught",
          broken(base, lambda sc: step(sc, "design_saved").update(captured_in="design_file")), "")
    check("transcript: a handoff announce with another path is caught",
          broken(base, lambda sc: step(sc, "handoff").update(announce="-> handing off to PLAN (design: x.md)")), "")
    check("transcript: a BUILD handoff is caught", broken(base, lambda sc: step(sc, "handoff").update(target="BUILD")), "")
    check("transcript: a handoff before memory_finalized is caught",
          broken(base, lambda sc: sc["steps"].insert(len(sc["steps"]) - 3, sc["steps"].pop())), "")
    check("transcript: memory while the grill runs is caught",
          broken(base, lambda sc: sc["steps"].insert(sc["steps"].index(step(sc, "grill")), {"op": "memory_task_created"})), "")
    check("transcript: a terminal status while the grill runs is caught",
          broken(base, lambda sc: sc["steps"].insert(sc["steps"].index(step(sc, "design_saved")), {"op": "status", "value": "completed"})), "")
    check("transcript: a handoff after a decline is caught",
          broken("none-declines", lambda sc: sc["steps"].append({"op": "handoff", "target": "PLAN", "input_kind": "design", "announce": "x"})), "")
    check("transcript: a wrong JUST_GO pick is caught", broken("just-go-auto-picks", lambda sc: step(sc, "auto_pick").update(candidate=1)), "")
    check("transcript: a JUST_GO pick of the first when a Strong exists is caught",
          broken("just-go-without-a-top", lambda sc: step(sc, "auto_pick").update(candidate=1)), "")
    check("transcript: a JUST_GO question is caught",
          broken("just-go-auto-picks", lambda sc: sc["steps"].insert(4, {"op": "question", "options": ["1", "2", "3", "None"]})), "")
    jg = "just-go-auto-picks"
    ended = lambda sc: any("JUST_GO ended at the report" in p for p in ch.sequence_problems(sc))  # noqa: E731
    sc = scenario(jg)
    check("JUST_GO: report_shown directly followed by auto_pick, no question step, is clean (attempt 5 control)",
          [s["op"] for s in sc["steps"]][3:5] == ["report_shown", "auto_pick"] and ch.sequence_problems(sc) == [], str(ch.sequence_problems(sc)))
    sc["steps"].insert(4, {"op": "question", "text": "Which of 1, 2, 3 or None?", "options": ["1", "2", "3", "None"]})
    check("JUST_GO: a question after the report is RED 'JUST_GO ended at the report'", ended(sc), str(ch.sequence_problems(sc)))
    sc = scenario(jg)
    sc["steps"].insert(4, {"op": "reply"})
    check("JUST_GO: a reply after the report is RED 'JUST_GO ended at the report'", ended(sc), str(ch.sequence_problems(sc)))
    sc = scenario(jg)
    sc["steps"] = sc["steps"][:4]
    sc["expected"].update(path="completed_no_handoff", child_minted=False)
    del sc["expected"]["candidate_picked"]
    check("JUST_GO: a transcript that stops right after the report is RED (the turn ended at the report)", ended(sc), str(ch.sequence_problems(sc)))
    check("question: a text that omits a candidate number is caught (RR2)",
          broken(base, lambda sc: step(sc, "question").update(text="Which candidate: 1 or None?")), "")
    check("transcript: an accepted skip at the failure gate is caught",
          broken("scanner-failure", lambda sc: step(sc, "answer").update(outcome="ended")), "")
    check("transcript: a fixture outcome that contradicts the validation rule is caught",
          broken("a-report-missing", lambda sc: step(sc, "scanner").update(outcome="ok")), "")
    check("transcript: a gate after a NO_CANDIDATES scan is caught",
          broken("no-candidates", lambda sc: sc["steps"].insert(4, {"op": "gate", "pending_gate": "candidate_choice"})), "")
    check("transcript: memory before the terminal status is caught",
          broken("no-candidates", lambda sc: sc["steps"].insert(3, {"op": "memory_task_created"})), "")
    sc = scenario(base)
    sc["steps"] = [s for s in sc["steps"] if s["op"] != "handoff"]
    sc["expected"].update(path="completed_no_handoff", child_minted=False)
    check("transcript: a saved design that is never handed off is caught (attempt 3 of the live run ended this way)",
          any("never handed off" in p for p in ch.sequence_problems(sc)), str(ch.sequence_problems(sc)))
    check("transcript: a design saved with no grill step is caught (M4)",
          broken(base, lambda sc: sc["steps"].remove(step(sc, "grill"))), "")
    check("transcript: a JUST_GO design saved with no grill step is caught (M4)",
          broken("just-go-auto-picks", lambda sc: sc["steps"].remove(step(sc, "grill"))), "")
    check("transcript: a valid scan with no report_shown is caught (M4)",
          broken(base, lambda sc: sc["steps"].remove(step(sc, "report_shown"))), "")
    check("transcript: the report never shown after a retried scan is caught (M4)",
          broken("a-count-that", lambda sc: sc["steps"].remove(step(sc, "report_shown"))), "")
    check("transcript: a NO_CANDIDATES scan with no report_shown is caught (M4)",
          broken("no-candidates", lambda sc: sc["steps"].remove(step(sc, "report_shown"))), "")
    check("transcript: a report shown before any valid scan is caught",
          broken(base, lambda sc: sc["steps"].insert(2, {"op": "report_shown", "how": "open", "platform": "darwin"})), "")
    five = "five-candidates"
    check("question: five candidates ask options 1, 2, 3, None (AskUserQuestion allows 4), not five numbers (F1)",
          broken(five, lambda sc: step(sc, "question").update(options=["1", "2", "3", "4", "5", "None"])), "")
    check("question: five candidates with 4 numbers and no None is caught (F1)",
          broken(five, lambda sc: step(sc, "question").update(options=["1", "2", "3", "4"])), "")
    check("answer: a pick of candidate 5, typed as free text, is accepted (F1)",
          ch.sequence_problems(scenario(five)) == [] and step(scenario(five), "answer")["text"] == "5", "")
    check("answer: a pick of candidate 6 of 5 is caught (F1)", broken(five, lambda sc: step(sc, "answer").update(text="6")), "")
    check("transcript: a wrong expected path is caught", broken(base, lambda sc: sc["expected"].update(path="failed")), "")
    check("transcript: a wrong expected pick is caught", broken(base, lambda sc: sc["expected"].update(candidate_picked=2)), "")


def pin_controls() -> None:
    texts = {rel: (PLUGIN_ROOT / rel).read_text(encoding="utf-8") for rel in {rel for rel, _ in ch.DOC_PINS}}
    check("all pins hold on the live files", ch.pin_problems(lambda rel: texts[rel]) == [], str(ch.pin_problems(lambda rel: texts[rel]))[:300])
    for rel, pin in ch.DOC_PINS:
        mutated = dict(texts, **{rel: texts[rel].replace(pin, "", 1)})
        got = ch.pin_problems(lambda r, m=mutated: m[r])
        others = [p for p in ch.DOC_PINS if p != (rel, pin) and p[0] == rel and p[1] in pin]
        check(f"pin proven by removal: {rel.rsplit('/', 1)[-1]} {pin[:60]!r}",
              any(repr(pin) in p for p in got) and not others, f"problems={got[:2]} subsumed-by={others}")


def replay_on_copy(mutations: Dict[str, Callable[[str], str]]) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "craftflow"
        for sub in ("skills", "config", "scripts"):
            shutil.copytree(PLUGIN_ROOT / sub, root / sub, ignore=shutil.ignore_patterns("__pycache__"))
        (root / "tests" / "fixtures").mkdir(parents=True)
        for name in ("orient-inline.json", *rh.CHECKS, *ch.CHECKS):
            shutil.copy(PLUGIN_ROOT / "tests" / "fixtures" / name, root / "tests" / "fixtures" / name)
        for rel, fn in mutations.items():
            path = root / rel
            text = path.read_text(encoding="utf-8")
            new = fn(text)
            assert new != text, f"mutation of {rel} changed nothing"
            path.write_text(new, encoding="utf-8")
        return subprocess.run([sys.executable, str(PLUGIN_ROOT / "scripts" / "craftflow_route_replay_check.py"),
                               "--fixture", str(root / "tests" / "fixtures" / "codebase-health-candidate-choice.json"),
                               "--plugin-root", str(root)], capture_output=True, text=True)


def red(name: str, mutations: Dict[str, Callable[[str], str]], failing: str) -> None:
    res = replay_on_copy(mutations)
    check(name, res.returncode == 1 and f"FAIL: {failing}" in res.stdout,
          f"rc={res.returncode} want 'FAIL: {failing}' in {res.stdout[-700:]}{res.stderr[-300:]}")


def mutated_copies() -> None:
    res = replay_on_copy({})
    check("an untouched plugin copy passes the CODEBASE-HEALTH fixture", res.returncode == 0 and "0 failed" in res.stdout,
          f"rc={res.returncode} {res.stdout[-600:]}{res.stderr[-300:]}")
    fx = "tests/fixtures/codebase-health-candidate-choice.json"
    pins, reg, cur, live = "doc pins", "registry, stub removal, phases and dispatcher row", "Cursor chain and candidate_choice gate row", \
        "live CLI: CODEBASE-HEALTH mint and PLAN handoff"
    red("a hollow route-codebase-health.md fails the doc pins", {ROUTE: lambda t: "### CODEBASE-HEALTH preparation\n\nNothing here.\n"}, pins)
    red("a route file back to the stub fails the registry check",
        {ROUTE: lambda t: "### route-codebase-health (pending)\n\nNot yet available in this release.\n"}, reg)
    red("a graph file back to the stub fails the registry check",
        {GRAPH: lambda t: "### codebase-health-workflow (pending)\n\nNot yet available in this release.\n"}, reg)
    red("a hollow graph file fails the doc pins", {GRAPH: lambda t: "### codebase-health-workflow\n\nNothing here.\n"}, pins)
    red("a reverted kernel pointer fails the doc pins", {KERNEL: lambda t: t.replace("### CODEBASE-HEALTH pointer", "### Pointer")}, pins)
    red("a kernel pointer without the announce demand fails the doc pins",
        {KERNEL: lambda t: t.replace("first print `-> CODEBASE-HEALTH workflow (signals: ...)`", "first answer")}, pins)
    red("a removed dispatcher row fails the registry check",
        {PROTOCOL: lambda t: t.replace("| `codebase-health` | `craftflow:architecture-scanner` |\n", "")}, reg)
    red("a dispatcher row to a write agent fails the registry check",
        {PROTOCOL: lambda t: t.replace("`craftflow:architecture-scanner`", "`craftflow:component-builder`", 1)}, reg)
    red("a missing Cursor CODEBASE-HEALTH section fails the Cursor check",
        {ROUTES: lambda t: t.replace("### CODEBASE-HEALTH\n", "### Chain\n", 1)}, cur)
    red("a missing candidate_choice gate row fails the Cursor check",
        {ROUTES: lambda t: "\n".join(l for l in t.splitlines() if not l.startswith("| `candidate_choice`")) + "\n"}, cur)
    red("a candidate_choice row that offers skip fails the Cursor check",
        {ROUTES: lambda t: t.replace("| `candidate_choice` | anything else |", "| `candidate_choice` | \"skip\" |", 1)}, cur)
    for reply in ch.CHOICE_REPLIES:
        red(f"a removed candidate_choice row ({reply[:28]}...) fails the Cursor check",
            {ROUTES: lambda t, r=reply: "\n".join(l for l in t.splitlines() if not (l.startswith("| `candidate_choice` |") and f"| {r} |" in l)) + "\n"}, cur)
    red("a candidate_choice None row that no longer says no handoff fails the Cursor check",
        {ROUTES: lambda t: t.replace("clear `pending_gate` |\n| `candidate_choice` | anything else", "clear `pending_gate` |\n| `candidate_choice` | anything else", 1).replace("append `workflow_completed`, no handoff;", "append `workflow_completed`;", 1)}, cur)
    red("a duplicated candidate_choice row fails the Cursor check",
        {ROUTES: lambda t: t.replace("| `candidate_choice` | anything else |", "| `candidate_choice` | anything else |\n| `candidate_choice` | anything else |", 1)}, cur)
    red("a JUST_GO pick edited in the fixture fails the transcript check",
        {fx: lambda t: t.replace('{"op": "auto_pick", "candidate": 2}', '{"op": "auto_pick", "candidate": 3}', 1)},
        "scanner contract, choice, grill, terminal and handoff transcript")
    red("a handoff mint that skips the design link fails the live check",
        {"scripts/craftflow_workflow_handoff.py": lambda t: t.replace('"input_kind"', '"input_kind_x"')}, live)


def shape_controls() -> None:
    def shape_after(mut: Callable[[dict], None]) -> list:
        fx = fixture()
        mut(fx)
        return ch.shape(fx)

    def drop(*prefixes: str) -> Callable[[dict], None]:
        return lambda fx: fx.__setitem__("scenarios", [s for s in fx["scenarios"] if not s["name"].startswith(prefixes)])

    check("the live fixture has a clean shape", ch.shape(fixture()) == [], str(ch.shape(fixture())))
    for label, prefixes in (("the PLAN handoff", ("candidates-found-choice", "five-candidates", "just-go")), ("NO_CANDIDATES", ("no-candidates", "a-count-that")),
                            ("the declined path", ("none-declines",)), ("the JUST_GO top pick", ("just-go-auto-picks",)),
                            ("the scanner failure", ("scanner-failure", "a-report-missing", "a-count-that"))):
        check(f"a fixture without {label} fails the coverage shape", any("lacks" in p for p in shape_after(drop(*prefixes))), str(prefixes))
    check("a fixture without the more-than-3-candidates question fails the coverage shape (F1)",
          any("lacks" in p for p in shape_after(drop("five-candidates"))), "")
    for prefix in ("just-go-without-a-top", "just-go-with-neither", "a-report-missing", "an-unrelated", "no-candidates", "scanner-failure"):
        check(f"dropping {prefix} alone leaves the coverage intact (it is not the only witness)", shape_after(drop(prefix)) == [], prefix)
    check("a step without a known op is a shape failure",
          any("no known `op`" in p for p in shape_after(lambda fx: fx["scenarios"][0]["steps"][0].update(op="nope"))), "")
    check("a step missing a required key is a shape failure",
          any("lacks `candidate_count`" in p for p in shape_after(lambda fx: fx["scenarios"][0]["steps"][2].pop("candidate_count"))), "")
    check("a mistyped key is a shape failure (`top` as a string)",
          any("`top`" in p for p in shape_after(lambda fx: fx["scenarios"][0]["steps"][2].update(top="1"))), "")
    check("a bool is not an int (`candidate_count` true)",
          any("candidate_count" in p for p in shape_after(lambda fx: fx["scenarios"][0]["steps"][2].update(candidate_count=True))), "")
    check("an unknown expected.path is a shape failure",
          any("expected.path" in p for p in shape_after(lambda fx: fx["scenarios"][0]["expected"].update(path="x"))), "")
    check("a non-object fixture is a shape failure", ch.shape([]) != [], "")
    res = replay_on_copy({"tests/fixtures/codebase-health-candidate-choice.json": lambda t: t.replace('"op": "gate"', '"kind": "gate"', 1)})
    check("a mis-shaped fixture stops the replay at the shape phase",
          res.returncode == 1 and "route replay: stopped, fixture shape is invalid" in res.stdout and "FAIL: doc pins" not in res.stdout,
          f"rc={res.returncode} {res.stdout[-400:]}")
    check("a request that routes elsewhere fails the routing check",
          ch.routing_problems({"scenarios": [{"name": "x", "request": "fix the failing login test"}]}) != [], "")
    check("a non-existent file in the lab turns into a problem, not a traceback",
          any("could not run" in p for p in rh.live(lambda r: ch.live_problems(r), PLUGIN_ROOT / "nope")), "")


def main() -> int:
    literal_controls()
    pin_controls()
    shape_controls()
    mutated_copies()
    print(f"\n{_passes} passed, {len(_errors)} failed")
    for err in _errors:
        print(err)
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
