#!/usr/bin/env python3
"""LV-CH judge for the live driver `harvest_routes_roundtrip.py` (H6): what a `claude -p "run a codebase health check on calc"` run
left behind, and the verdict on it. Imported by the driver (never run directly); no import of the driver, so there is no cycle:
the driver hands in its own `git` function and the two stream facts (announced, non-empty).

PASS needs, all at once:
  - the `-> CODEBASE-HEALTH workflow` announce and exactly one CODEBASE-HEALTH artifact;
  - events in order `workflow_started` < `memory_finalized` < `handoff_started` in the parent's log;
  - `.craftflow/codebase-health/{parent}/report.html` that is non-empty and holds `<html` and `<svg`;
  - exactly one PLAN child linked by `parent_workflow_uuid`, whose `handoff` block says `source_route == CODEBASE-HEALTH`,
    `input_kind == design` and `input_path` = a saved `docs/plans/*-design.md` that exists in the project;
  - exactly one `open` shim call, equal to the parent's project-relative report path (the router opened the report once);
  - no product change under calc/ and tests/ against the seed commit (committed edits, working-tree edits and new files).
`rejudge(scr, git_fn)` applies the same judge to a kept scratch dir (transcripts/LV-CH.jsonl + LV-CH/ + open-calls.log).
Python 3.9 compatible.
"""
from __future__ import annotations

import json
from pathlib import Path

CH_ANNOUNCE = "-> CODEBASE-HEALTH workflow"
CH_PROMPT = "run a codebase health check on calc"
PRODUCT_DIRS = ("calc", "tests")
ORDER = ("workflow_started", "memory_finalized", "handoff_started")


def report_rel(parent):
    """The project-relative path the router must open: the same string the route law pins for REPORT_PATH."""
    return ".craftflow/codebase-health/%s/report.html" % parent


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def ch_state(project):
    """What a run left behind: its artifact(s), the parent's ordered event names, the report, designs and the PLAN children."""
    root = Path(project) / ".craftflow"
    wdir = root / "state" / "workflows"
    arts = {}
    for f in sorted(wdir.glob("wf-*.json")) if wdir.is_dir() else []:
        art = _json(f)
        if isinstance(art, dict):
            arts[f.stem] = art
    parents = [w for w, a in arts.items() if a.get("workflow_type") == "CODEBASE-HEALTH"]
    parent = parents[0] if len(parents) == 1 else None
    events = []
    if parent:
        try:
            lines = (wdir / (parent + ".events.jsonl")).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                events.append(json.loads(line).get("event"))
            except (ValueError, AttributeError):
                continue
    report = root / "codebase-health" / str(parent) / "report.html"
    text = report.read_text(encoding="utf-8", errors="replace") if parent and report.is_file() else ""
    children = [w for w, a in arts.items() if parent and a.get("workflow_type") == "PLAN" and a.get("parent_workflow_uuid") == parent]
    plans = Path(project) / "docs" / "plans"
    return {"parents": parents, "parent": parent, "events": events,
            "expected_report": report_rel(parent) if parent else None,
            "report_exists": bool(text.strip()), "report_symlink": report.is_symlink(), "report_html": "<html" in text.lower(), "report_svg": "<svg" in text.lower(),
            "designs": sorted(p.relative_to(project).as_posix() for p in plans.glob("*-design.md")) if plans.is_dir() else [],
            "children": children,
            "handoffs": {w: arts[w].get("handoff") for w in children}}


def product_changes(git_fn, project, seed_sha):
    """Changes under calc/ and tests/ against the seed commit: `name-status` lines for tracked paths (committed or not) plus new
    untracked files; `__pycache__` paths never count. '' when the product code is untouched."""
    diff = git_fn(project, "diff", "--name-status", seed_sha, "--", *PRODUCT_DIRS)
    new = git_fn(project, "ls-files", "--others", "--exclude-standard", "--", *PRODUCT_DIRS)
    hidden = git_fn(project, "ls-files", "--others", "--ignored", "--exclude-standard", "--", *PRODUCT_DIRS)  # an ignore rule hides nothing
    lines = [l for l in diff.splitlines() if l.strip()] + ["??\t" + p for p in (new + hidden).splitlines() if p.strip()]
    return "\n".join(l for l in lines if "__pycache__" not in l.split("\t")[-1].split("/"))


def read_open_calls(scr):
    log = Path(scr) / "open-calls.log"
    return [l.strip() for l in log.read_text(encoding="utf-8").splitlines() if l.strip()] if log.is_file() else []


def order_problem(events):
    idx = []
    for name in ORDER:
        if name not in events:
            return "events_missing(%s)" % name
        idx.append(events.index(name))
    return "" if idx == sorted(idx) and len(set(idx)) == 3 else "event_order(%s)" % ",".join(
        "%s@%d" % (n, i) for n, i in zip(ORDER, idx))


def handoff_problem(state):
    """'' when the single PLAN child's handoff block links the saved design; else the reason."""
    if len(state["children"]) != 1:
        return "plan_children=%s" % state["children"]
    child = state["children"][0]
    link = state["handoffs"].get(child)
    if not isinstance(link, dict):
        return "child %s has no handoff block" % child
    if (link.get("source_route"), link.get("input_kind")) != ("CODEBASE-HEALTH", "design"):
        return "child link is source_route=%r input_kind=%r" % (link.get("source_route"), link.get("input_kind"))
    if link.get("input_path") not in state["designs"]:
        return "input_path %r is not a saved design in %s" % (link.get("input_path"), state["designs"])
    return ""


def judge_ch(announced, has_events, state, changes, open_calls):
    """('PASS' | 'FAIL:<reason>', detail dict). Pure. A run stopped early (child exists) is fine: no exit-code check, only a non-empty
    stream. `changes` is `product_changes()` output; `open_calls` the lines the shim `open` recorded."""
    detail = {"announced": announced, "state": state, "product_changes": changes, "open_calls": open_calls}
    if not has_events:
        return "FAIL:claude_session_failed(empty stream)", detail
    if not announced:
        return "FAIL:no_ch_announce", detail
    if state["parent"] is None or len(state["parents"]) != 1:
        return "FAIL:no_ch_artifact(parents=%s)" % state["parents"], detail
    problem = order_problem(state["events"])
    if problem:
        return "FAIL:" + problem, detail
    if not state["report_exists"]:
        return "FAIL:no_report(%s)" % state["expected_report"], detail
    if state.get("report_symlink") or not (state["report_html"] and state["report_svg"]):
        return "FAIL:report_not_a_page(html=%s,svg=%s,symlink=%s)" % (state["report_html"], state["report_svg"], state.get("report_symlink")), detail
    if not state["designs"]:
        return "FAIL:no_design_file(docs/plans/*-design.md)", detail
    if not state["children"]:
        return "FAIL:no_plan_child(parent=%s)" % state["parent"], detail
    problem = handoff_problem(state)
    if problem:
        return "FAIL:handoff_link(%s)" % problem, detail
    if open_calls != [state["expected_report"]]:
        return "FAIL:open_calls(want exactly [%r], got %r)" % (state["expected_report"], open_calls), detail
    if changes.strip():
        return "FAIL:product_code_changed(%r)" % changes[:120], detail
    return "PASS", detail


def collect(project, scr, git_fn, seed_sha):
    """(state, product changes, open calls) for one scenario project, ready for `judge_ch`."""
    return ch_state(project), product_changes(git_fn, project, seed_sha), read_open_calls(scr)


def rejudge(scr, git_fn, stream_facts):
    """Judge a kept scratch dir offline. `stream_facts(text)` -> (announced, has_events). The seed is the root commit of the project."""
    scr = Path(scr)
    project = scr / "LV-CH"
    transcript = scr / "transcripts" / "LV-CH.jsonl"
    if not project.is_dir() or not transcript.is_file():
        return "FAIL:not_a_kept_ch_dir(%s)" % scr, {}
    announced, has_events = stream_facts(transcript.read_text(encoding="utf-8", errors="replace"))
    try:
        seed_sha = git_fn(project, "rev-list", "--max-parents=0", "HEAD").split()[0]
        state, changes, calls = collect(project, scr, git_fn, seed_sha)
    except (RuntimeError, IndexError, OSError) as exc:  # not a repo / no commits / unreadable: a named FAIL, never a traceback
        return "FAIL:not_a_kept_ch_dir(%s: %s)" % (scr, str(exc)[:120]), {}
    verdict, detail = judge_ch(announced, has_events, state, changes, calls)
    detail["seed_sha"] = seed_sha
    return verdict, detail
