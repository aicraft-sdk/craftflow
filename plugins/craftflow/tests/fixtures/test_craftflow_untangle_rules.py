#!/usr/bin/env python3
"""Content contract for the untangle-codebase skill and the tangle rules in the generation and review skills.

Run: python3 tests/fixtures/test_craftflow_untangle_rules.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Optional

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SKILLS = PLUGIN_ROOT / "skills"
TERMS = ("hidden link", "needless link", "surplus")
PREVENT_HEADING = "## Prevent Tangle"
REVIEW_HEADING = "## Tangle Checks (Stage 2)"
PREVENT_SKILLS = ("code-generation", "architecture-patterns", "planning-patterns")
MAX_BLOCK_LINES = 10
GATE_PHRASE = "**Diagnosis is read-only. This skill never edits code: every fix goes to PLAN, then BUILD.**"
READ_ONLY_TOOLS = {"Read", "Grep", "Glob", "Bash"}
ROUTE_STUB = SKILLS / "_shared" / "route-codebase-health.md"
ROUTE_INVOKE = 'Skill(skill="craftflow:untangle-codebase")'
ROUTE_CURSOR_PATH = "tools/craftflow-plugin/plugins/craftflow/skills/untangle-codebase/SKILL.md"
ROUTE_OVERRIDE = ("overrides the pending fallback below and the one in "
                  "`skills/craftflow-router/references/codebase-health-workflow.md`")
# Today's whole stub sentence (route-codebase-health.md line 3), kept byte-identical for every non-untangle request.
ROUTE_FALLBACK = ("Not yet available in this release. Announce `-> CODEBASE-HEALTH workflow (signals: …; pending)`, then route the "
                  "request by the pre-harvest order ERROR → DEBUG, PLAN → PLAN, REVIEW → REVIEW, otherwise BUILD, and continue "
                  "under that route's law.")

_passes = 0
_errors: List[str] = []


def check(name: str, cond: bool, reason: str) -> None:
    global _passes
    if cond:
        _passes += 1
        print(f"  PASS: {name}")
    else:
        _errors.append(f"FAIL [{name}]: {reason}")
        print(f"  FAIL: {name}: {reason}")


def split_frontmatter(text: str) -> "tuple[str, str]":
    lines = text.splitlines()
    idx = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if len(idx) < 2 or idx[0] != 0:
        return "", text
    return "\n".join(lines[1:idx[1]]), "\n".join(lines[idx[1] + 1:])


def block(body: str, heading: str) -> Optional[str]:
    """Text under `heading` (exact line) up to the next '## ' line; None when the heading is absent."""
    lines = body.splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.rstrip() == heading]
    if not starts:
        return None
    out = []
    for ln in lines[starts[0] + 1:]:
        if ln.startswith("## "):
            break
        out.append(ln)
    return "\n".join(out)


def block_problems(label: str, text: str, heading: str, extra: tuple, cap: Optional[int]) -> List[str]:
    _, body = split_frontmatter(text)
    count = sum(1 for ln in body.splitlines() if ln.rstrip() == heading)
    if count != 1:
        return [f"{label}: expected exactly one '{heading}' heading, found {count}"]
    b = block(body, heading) or ""
    low = b.lower()
    probs = [f"{label}: '{heading}' block lacks the '{t}' check" for t in TERMS + extra if t not in low]
    n = sum(1 for ln in b.splitlines() if ln.strip())
    if cap is not None and n > cap:
        probs.append(f"{label}: '{heading}' block has {n} non-blank lines (cap {cap})")
    return probs


def untangle_problems(label: str, text: str) -> List[str]:
    fm, body = split_frontmatter(text)
    probs = []
    if not re.search(r"^name: untangle-codebase\s*$", fm, re.M):
        probs.append(f"{label}: frontmatter lacks 'name: untangle-codebase'")
    m = re.search(r'^description: "(.*)"\s*$', fm, re.M)
    desc = m.group(1) if m else ""
    if not desc.startswith("Use when") or len(desc) > 160:
        probs.append(f"{label}: description must be one quoted line starting 'Use when', <= 160 chars (got {desc!r})")
    if re.search(r"^user-invocable:\s*false", fm, re.M) or "disable-model-invocation" in fm:
        probs.append(f"{label}: skill must stay user-invocable (no user-invocable: false / disable-model-invocation)")
    t = re.search(r"^allowed-tools:\s*(.*)$", fm, re.M)
    tools = set(re.split(r"[\s,]+", t.group(1).strip())) if t else set()
    if not tools or not tools <= READ_ONLY_TOOLS:
        probs.append(f"{label}: allowed-tools must be a subset of {sorted(READ_ONLY_TOOLS)} (got {sorted(tools)})")
    if GATE_PHRASE not in body:
        probs.append(f"{label}: gated-handoff statement missing: {GATE_PHRASE}")
    low = body.lower()
    for t2 in TERMS + ("special case", "unmet requirement"):
        if t2 not in low:
            probs.append(f"{label}: body lacks the '{t2}' check")
    if "github.com/caldoai/skills" not in body:
        probs.append(f"{label}: one-line upstream attribution (github.com/caldoai/skills) missing")
    if "codebase-deepening" not in body or "Deletion Test" not in body:
        probs.append(f"{label}: surplus must point to the Deletion Test in codebase-deepening")
    return probs


def route_problems(label: str, text: str) -> List[str]:
    """Plain-language path (OD-3): the CODEBASE-HEALTH stub sends untangle requests to the skill and keeps its fallback."""
    probs = []
    if "untangle" not in text.lower() or ROUTE_INVOKE not in text:
        probs.append(f"{label}: untangle requests must invoke {ROUTE_INVOKE}")
    if ROUTE_CURSOR_PATH not in text:
        probs.append(f"{label}: Cursor fallback Read() of {ROUTE_CURSOR_PATH} missing")
    if ROUTE_OVERRIDE not in text:
        probs.append(f"{label}: untangle branch must state it {ROUTE_OVERRIDE} (OD-4)")
    if ROUTE_FALLBACK not in text:
        probs.append(f"{label}: pre-harvest fallback sentence not byte-identical to today's line 3")
    return probs


GOOD_ROUTE = ("### route-codebase-health (pending)\n\nUntangle requests: when the request asks to untangle the codebase, this rule "
              + ROUTE_OVERRIDE + ": invoke `" + ROUTE_INVOKE + "` (Cursor: `Read()` `" + ROUTE_CURSOR_PATH + "`).\n\n"
              + ROUTE_FALLBACK + "\n")
GOOD_PREVENT = ("---\nname: x\n---\n## Prevent Tangle\n- **Hidden link:** a\n- **Needless link:** b\n- **Surplus:** c\n"
                "- **Special case:** d\n## Next\n")
GOOD_UNTANGLE = ('---\nname: untangle-codebase\ndescription: "Use when x"\nallowed-tools: Read Grep Glob Bash\n---\n'
                 + GATE_PHRASE + "\nhidden link needless link surplus special case unmet requirement "
                 "github.com/caldoai/skills codebase-deepening Deletion Test\n")


def test_probes() -> None:
    print("\n[detector probes]")
    ok = block_problems("p", GOOD_PREVENT, PREVENT_HEADING, ("special case",), MAX_BLOCK_LINES)
    check("probe_prevent_good_passes", ok == [], f"{ok}")
    miss = block_problems("p", GOOD_PREVENT.replace("Needless link", "Other"), PREVENT_HEADING, ("special case",), MAX_BLOCK_LINES)
    check("probe_prevent_missing_term", len(miss) == 1 and "'needless link'" in miss[0] and miss[0].startswith("p:"), f"{miss}")
    long = GOOD_PREVENT.replace("## Next", "\n".join(f"- x{i}" for i in range(7)) + "\n## Next")
    check("probe_prevent_over_cap", any("non-blank lines" in p for p in
          block_problems("p", long, PREVENT_HEADING, ("special case",), MAX_BLOCK_LINES)), "11-line block not caught")
    dup = GOOD_PREVENT + GOOD_PREVENT.split("---\n", 2)[2]
    check("probe_prevent_duplicate_heading", any("exactly one" in p for p in
          block_problems("p", dup, PREVENT_HEADING, ("special case",), MAX_BLOCK_LINES)), "duplicate heading not caught")
    check("probe_untangle_good_passes", untangle_problems("u", GOOD_UNTANGLE) == [], f"{untangle_problems('u', GOOD_UNTANGLE)}")
    for name, bad in (("gate", GOOD_UNTANGLE.replace("never edits code", "may edit code")),
                      ("invocable", GOOD_UNTANGLE.replace("allowed-tools", "user-invocable: false\nallowed-tools")),
                      ("write_tool", GOOD_UNTANGLE.replace("Read Grep", "Read Write Grep")),
                      ("desc", GOOD_UNTANGLE.replace('"Use when x"', '"Helps untangle"')),
                      ("attribution", GOOD_UNTANGLE.replace("github.com/caldoai/skills", ""))):
        check(f"probe_untangle_{name}", len(untangle_problems("u", bad)) >= 1, f"mutation {name} not caught")
    check("probe_route_good_passes", route_problems("r", GOOD_ROUTE) == [], f"{route_problems('r', GOOD_ROUTE)}")
    no_invoke = route_problems("r", GOOD_ROUTE.replace(ROUTE_INVOKE, "the skill"))
    check("probe_route_missing_invoke", len(no_invoke) == 1 and "must invoke" in no_invoke[0], f"{no_invoke}")
    no_override = route_problems("r", GOOD_ROUTE.replace(ROUTE_OVERRIDE, "applies first"))
    check("probe_route_missing_override", len(no_override) == 1 and "(OD-4)" in no_override[0], f"{no_override}")
    reworded = route_problems("r", GOOD_ROUTE.replace(", then route the request by", ". Otherwise route the request by"))
    check("probe_route_missing_fallback", len(reworded) == 1 and "byte-identical" in reworded[0], f"{reworded}")


def test_prevent_blocks() -> None:
    print("\n[prevent tangle blocks]")
    for s in PREVENT_SKILLS:
        path = SKILLS / s / "SKILL.md"
        probs = block_problems(str(path.relative_to(PLUGIN_ROOT)), path.read_text(encoding="utf-8"),
                               PREVENT_HEADING, ("special case",), MAX_BLOCK_LINES)
        check(f"prevent_{s}", probs == [], "; ".join(probs))


def test_review_checks() -> None:
    print("\n[review tangle checks]")
    path = SKILLS / "code-review-patterns" / "SKILL.md"
    probs = block_problems(str(path.relative_to(PLUGIN_ROOT)), path.read_text(encoding="utf-8"),
                           REVIEW_HEADING, ("missing requirement",), None)
    check("review_code-review-patterns", probs == [], "; ".join(probs))


def test_untangle_skill() -> None:
    print("\n[untangle-codebase skill]")
    path = SKILLS / "untangle-codebase" / "SKILL.md"
    if not path.is_file():
        check("untangle_skill_exists", False, f"missing {path.relative_to(PLUGIN_ROOT)}")
        return
    probs = untangle_problems(str(path.relative_to(PLUGIN_ROOT)), path.read_text(encoding="utf-8"))
    check("untangle_contract", probs == [], "; ".join(probs))


def test_route_hook() -> None:
    print("\n[plain-language route hook]")
    probs = route_problems(str(ROUTE_STUB.relative_to(PLUGIN_ROOT)), ROUTE_STUB.read_text(encoding="utf-8"))
    check("route_codebase-health", probs == [], "; ".join(probs))


def main() -> int:
    test_probes()
    test_prevent_blocks()
    test_review_checks()
    test_untangle_skill()
    test_route_hook()
    print()
    for e in _errors:
        print(e)
    print(f"Results: {_passes} passed, {len(_errors)} failed")
    return 1 if _errors else 0


if __name__ == "__main__":
    sys.exit(main())
