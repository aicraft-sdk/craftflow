#!/usr/bin/env python3
"""Occurrence-aware router rule inventory (DD-4, DD-5, BC-8, BC-9).

Splits the runtime router corpus into whole units (list items with continuation lines, table body rows,
paragraphs, fenced blocks and [EASY TO MISS ...] brackets; deliberately no sentence unit), gives each unit a
stable id, records every occurrence with its heading context and the workflow x variant pairs that read its
file, and accounts for every removed occurrence against the policy map.

  --emit --readmap-baseline RB [--ref SHA] [--allowlist A] --out PATH     write the baseline inventory
  --diff --baseline F --policy-map M [--rationale R] [--readmap RM]       BC-9 accounting, exit 1 on any gap
  --report-nonnormative --files F... [--list] [--format json|md]          whole non-normative unit bytes (L3)

Python 3.9 compatible. Output is one JSON line (or the written inventory); deterministic bytes (BC-8).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import craftflow_router_corpus as cor

PLUGIN_ROOT = cor.PLUGIN_ROOT
FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
CLASSES = ("kernel", "start", "stage", "maintainer")
VARIANTS = {"BUILD": ["fast_path", "standard"], "PLAN": ["standard"], "DEBUG": ["standard"], "REVIEW": ["standard"]}
HEADERS = {
    "policy": "| Baseline ID or group (file:start-end@sha) | Policy key | Enforcing script::function | Test | Status | live_verified |",
    "rewrites": "| Baseline ID(s) or group | New IDs | Kind (pointer/dedupe/trigger/literal-to-key/xref/split/heading) | Justification | Equivalence note | Reviewed by |",
    "applicability": "| Baseline ID or section (file#heading@sha) | Baseline coverage | Narrowed coverage | Quoted scope words | Reviewed by |",
    "rationale": "| Baseline ID | Destination | Justification |",
}
SECTIONS = {"policy": "policy-mapped deletions", "rewrites": "rewrites", "applicability": "applicability",
            "rationale": "rationale"}
STATUSES = ("deleted", "pending-prose-deletion", "prose_retained", "document-only")
KINDS = ("pointer", "dedupe", "trigger", "literal-to-key", "xref", "split", "heading")
REVIEWER = re.compile(r"^(David|(doubt-verifier|plan-gap-reviewer)@wf:\S+)$")
KEYWORDS = re.compile(r"(?i)\b(never|must|do not|always|only|required|requires|shall|fail closed)\b|MUST|MANDATORY|\[EASY TO MISS")
VERBS3 = re.compile(r"(?i)\b(skips?|stops?|blocks?|denies|fails?|advances?|halts?|aborts?)\b")
IMPERATIVES = frozenset(
    "read run set record append persist create skip stop treat use ask write dispatch mark include track apply "
    "before after if when do call parse release re-read delete keep verify check load resolve extract".split())
LIST_MARK = re.compile(r"^([-*+]|\d+[.)])\s+")
HEX_ID = re.compile(r"\b[0-9a-f]{12}\b")
RANGE = re.compile(r"([\w./-]+\.md):(\d+)-(\d+)@([0-9a-f]{7,40})")


# --------------------------------------------------------------------------------------- units


def normalize(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    t = LIST_MARK.sub("", t, count=1).replace("**", "").replace("__", "")
    return " ".join(t.split()).lower()


def make_id(norm: str) -> str:
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


def _bracket_spans(raw: str) -> List[str]:
    spans, pos = [], 0
    while True:
        start = raw.find("[EASY TO MISS", pos)
        if start < 0:
            return spans
        depth, end = 0, len(raw)
        for k in range(start, len(raw)):
            depth += (raw[k] == "[") - (raw[k] == "]")
            if depth == 0:
                end = k + 1
                break
        spans.append(raw[start:end])
        pos = end


def extract_units(text: str, file: str = "") -> List[dict]:
    """Whole units in file order: {kind, raw, norm, id, line, numbered, heading_ctx, heading_path}."""
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    if lines and lines[0].strip() == "---":
        close = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
        i = 0 if close is None else close + 1
    mask = cor.fence_mask(lines)
    units: List[dict] = []
    stack: List[Tuple[int, str]] = []
    cur: Optional[dict] = None
    in_table = False

    def emit(kind: str, raw_lines: List[str], line: int, numbered: bool = False) -> None:
        raw = "\n".join(x.rstrip() for x in raw_lines).strip("\n")
        norm = normalize(raw)
        path = [h for _, h in stack]
        units.append({"kind": kind, "raw": raw, "norm": norm, "id": make_id(norm), "line": line, "numbered": numbered,
                      "heading_ctx": path[-1] if path else "", "heading_path": path})
        if kind in ("list_item", "paragraph"):
            for span in _bracket_spans(raw):
                if normalize(span) != norm:
                    emit("easy_to_miss", [span], line)

    def flush() -> None:
        nonlocal cur
        if cur:
            emit(cur["kind"], cur["lines"], cur["line"], cur["numbered"])
        cur = None

    while i < len(lines):
        line, s = lines[i], lines[i].strip()
        if mask[i]:
            flush()
            in_table = False
            opener = re.match(r"^(`{3,}|~{3,})", s).group(1)
            j = i + 1
            while j < len(lines) and not (lines[j].strip().startswith(opener[0] * len(opener))
                                           and not lines[j].strip().strip(opener[0])):
                j += 1
            emit("fence", lines[i:j + 1], i + 1)
            i = j + 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            flush()
            in_table = False
            while stack and stack[-1][0] >= len(h.group(1)):
                stack.pop()
            stack.append((len(h.group(1)), cor.norm_text(h.group(2))))
        elif not s or re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            flush()
            in_table = False
        elif s.startswith("|"):
            flush()
            if not in_table:
                in_table = True  # header row: not a unit
            elif not re.match(r"^\|[\s:|-]+\|?$", s):
                emit("table_row", [line], i + 1)
        elif LIST_MARK.match(s):
            flush()
            in_table = False
            cur = {"kind": "list_item", "lines": [line], "line": i + 1, "numbered": s[0].isdigit()}
        elif cur:
            cur["lines"].append(line)
        else:
            in_table = False
            cur = {"kind": "paragraph", "lines": [line], "line": i + 1, "numbered": False}
        i += 1
    flush()
    return units


def classify_normative(unit: dict, allowlist: FrozenSet[str] = frozenset(), keywords: bool = True) -> bool:
    """Keyword classifier OR membership in the hand-maintained allowlist. `keywords=False` disables the classifier."""
    if unit["norm"] in allowlist:
        return True
    if not keywords:
        return False
    if unit["kind"] in ("table_row", "fence", "easy_to_miss") or unit["numbered"]:
        return True
    first = unit["norm"].split(" ")[0].strip(":,.;") if unit["norm"] else ""
    return bool(KEYWORDS.search(unit["raw"]) or VERBS3.search(unit["raw"]) or first in IMPERATIVES)


def load_allowlist(path) -> Set[str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError(f"malformed normative allowlist: {path}")
    return {normalize(e["text"]) for e in data["entries"]}


# --------------------------------------------------------------------------------------- read map


def coverage(readmap: dict) -> Dict[str, Set[Tuple[str, str]]]:
    """file -> set of (workflow, variant) pairs that read it. kernel covers all; maintainer covers nothing."""
    variants = readmap.get("variants", VARIANTS)
    out: Dict[str, Set[Tuple[str, str]]] = {}
    for e in readmap.get("files", []):
        cls, wfs = e.get("class"), e.get("workflows", [])
        if cls not in CLASSES or not e.get("file"):
            raise ValueError(f"malformed read map entry: {e}")
        pairs = out.setdefault(e["file"], set())
        if cls == "maintainer":
            continue
        for wf in (list(variants) if cls == "kernel" or "ALL" in wfs else wfs):
            if wf not in variants:
                raise ValueError(f"unknown workflow {wf!r} in read map")
            v = e.get("variant")
            for var in ([v] if isinstance(v, str) else (v or variants[wf])):
                pairs.add((wf, var))
    return out


def load_readmap(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    coverage(data)
    return data


# --------------------------------------------------------------------------------------- inventory


def build_inventory(corpus: Dict[str, str], readmap: dict, allowlist, ref: Optional[str] = None) -> dict:
    cov, allow, ents = coverage(readmap), frozenset(allowlist), {}
    for rel in sorted(corpus):
        applic = sorted([list(p) for p in cov.get(rel, ())])
        for u in extract_units(corpus[rel], rel):
            e = ents.setdefault(u["id"], {"id": u["id"], "kind": u["kind"], "normative": False,
                                          "excerpt": u["norm"][:100], "occurrences": []})
            e["normative"] = e["normative"] or classify_normative(u, allow)
            e["occurrences"].append({"file": rel, "heading_ctx": u["heading_ctx"], "heading_path": u["heading_path"],
                                     "applicability": applic})
    entries = [ents[k] for k in sorted(ents)]
    for e in entries:
        e["occurrences"].sort(key=lambda o: json.dumps(o, sort_keys=True))
    return {"version": 2, "ref": ref, "entries": entries}


def dump_inventory(inv: dict) -> bytes:
    body = ",\n".join(json.dumps(e, sort_keys=True, ensure_ascii=False) for e in inv["entries"])
    return ('{"entries":[\n%s\n],"ref":%s,"version":%d}\n' % (body, json.dumps(inv["ref"]), inv["version"])).encode("utf-8")


def _check_baseline(base) -> None:
    try:
        ok = base["version"] == 2 and isinstance(base["entries"], list)
        for e in base["entries"]:
            ok = ok and bool(e["id"]) and isinstance(e["normative"], bool) and bool(e["occurrences"])
            for o in e["occurrences"]:
                ok = ok and isinstance(o["file"], str) and isinstance(o["heading_ctx"], str) \
                    and isinstance(o["applicability"], list)
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise ValueError("malformed baseline inventory (expected version 2 with entries/occurrences)")


# --------------------------------------------------------------------------------------- policy map


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_tables(doc: str) -> Tuple[Dict[str, List[List[str]]], List[str]]:
    """Rows of each policy-map table; fail closed on a missing section or a header that differs from the plan."""
    rows: Dict[str, List[List[str]]] = {k: [] for k in HEADERS}
    errors: List[str] = []
    lines = doc.split("\n")
    heads = cor.headings(doc)
    for key, title in SECTIONS.items():
        idx = next((n for n, h in enumerate(heads) if cor.norm_text(h[2]).lstrip("§ ").strip() == title), None)
        if idx is None:
            errors.append(f"policy map: missing section '{title}'")
            continue
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        table = [x for x in lines[heads[idx][0] + 1:end] if x.strip().startswith("|")]
        want = [" ".join(c.split()).lower() for c in _cells(HEADERS[key])]
        if not table or [" ".join(c.split()).lower() for c in _cells(table[0])] != want:
            errors.append(f"policy map: section '{title}' header differs from the required header")
            continue
        for ln in table[2:]:
            cells = _cells(ln)
            if len(cells) != len(want):
                errors.append(f"policy map: '{title}' row has {len(cells)} cells, expected {len(want)}")
            else:
                rows[key].append(cells)
    return rows, errors


def _ids(cell: str, root, errors: List[str]) -> List[str]:
    found = HEX_ID.findall(cell)
    for path, a, b, sha in RANGE.findall(cell):
        try:
            rel = next(p for p in (path, "skills/craftflow-router/" + path, "skills/craftflow-router/references/" + path,
                                   "skills/_shared/" + path) if cor.runtime_files(root, sha).count(p))
            found += [u["id"] for u in extract_units(cor.read_text(root, rel, sha), rel) if int(a) <= u["line"] <= int(b)]
        except (StopIteration, RuntimeError) as exc:
            errors.append(f"cannot resolve range '{path}:{a}-{b}@{sha}': {exc}")
    return found


def _symbol_ok(root, cell: str, prefixes: Tuple[str, ...]) -> bool:
    path, _, name = cell.partition("::")
    for pre in prefixes:
        f = Path(root) / pre / path.strip()
        if f.is_file():
            return not name.strip() or name.strip() in f.read_text(encoding="utf-8")
    return False


def _pairs(cell: str, errors: List[str]) -> Set[Tuple[str, str]]:
    out: Set[Tuple[str, str]] = set()
    for tok in re.split(r"[,;]", cell):
        wf, _, var = tok.strip().partition(":")
        if wf not in VARIANTS or (var and var not in VARIANTS[wf]):
            errors.append(f"bad narrowed coverage token '{tok.strip()}'")
        else:
            out.update((wf, v) for v in ([var] if var else VARIANTS[wf]))
    return out


# --------------------------------------------------------------------------------------- diff


def _match(reqs: List[FrozenSet], covs: List[FrozenSet]) -> Tuple[int, List[FrozenSet], int]:
    """Greedy: widest requirement first, smallest covering supply. Returns (matched, unmatched reqs, leftover supply)."""
    avail, missed, hit = sorted(covs, key=len), [], 0
    for req in sorted(reqs, key=len, reverse=True):
        k = next((n for n, c in enumerate(avail) if req <= c), None)
        if k is None:
            missed.append(req)
        else:
            avail.pop(k)
            hit += 1
    return hit, missed, len(avail)


def diff_inventory(baseline: dict, after_corpus: Dict[str, str], readmap: dict, policy_text: str,
                   rationale_text: str = "", plugin_root=None, allowlist=frozenset()) -> dict:
    _check_baseline(baseline)
    after = build_inventory(after_corpus, readmap, allowlist)
    after_ids = {e["id"] for e in after["entries"]}
    base = {e["id"]: e for e in baseline["entries"]}
    rows, errors = parse_tables(policy_text)
    rat_ids = {u["id"] for u in extract_units(rationale_text, "rationale")}
    root = plugin_root or PLUGIN_ROOT
    missing_review = 0

    def known(cell: str) -> List[str]:
        ids = _ids(cell, root, errors)
        errors.extend(f"row names unknown baseline id {i}" for i in ids if i not in base)
        return [i for i in ids if i in base]

    policy: Set[str] = set()
    for c in rows["policy"]:
        ids = known(c[0])
        script_ok, test_ok = _symbol_ok(root, c[2], ("", "scripts")), _symbol_ok(root, c[3], ("", "tests/fixtures", "scripts"))
        if c[4] not in STATUSES:
            errors.append(f"policy row has unknown status '{c[4]}'")
        if not script_ok or not test_ok:
            errors.append(f"policy row names a missing script or test: {c[2]} / {c[3]}")
        if c[4] == "deleted" and script_ok and test_ok:
            policy.update(ids)

    rewrites: Dict[str, str] = {}
    renames: Dict[str, str] = {}
    for c in rows["rewrites"]:
        kind = c[2].strip().lower()
        if kind not in KINDS:
            errors.append(f"rewrite row has unknown kind '{c[2]}'")
            continue
        if not c[4] or not REVIEWER.match(c[5]):
            missing_review += 1
        if kind == "heading":
            old, new = (cor.norm_text(re.sub(r"^.*#|@.*$", "", x)) for x in (c[0], c[1]))
            renames[new] = old
            continue
        new_ids = HEX_ID.findall(c[1])
        if not new_ids or any(n not in after_ids for n in new_ids):
            errors.append(f"rewrite row names replacement ids that do not exist: {c[1]}")
            continue
        rewrites.update({i: kind for i in known(c[0])})

    narrowed: Dict[str, Set[Tuple[str, str]]] = {}
    for c in rows["applicability"]:
        if not c[3].strip(' "`'):
            errors.append(f"applicability row has no quoted scope words: {c[0]}")
            continue
        if not REVIEWER.match(c[4]):
            missing_review += 1
        pairs = _pairs(c[2], errors)
        if not pairs:
            continue
        sect = re.match(r"^([\w./-]+)#([^@]+)(?:@\S+)?$", c[0].strip())
        ids = [e["id"] for e in baseline["entries"] for o in e["occurrences"]
               if sect and o["file"].endswith(sect.group(1)) and o["heading_ctx"] == cor.norm_text(sect.group(2))] \
            if sect else known(c[0])
        narrowed.update({i: pairs for i in ids})

    rationale: Set[str] = set()
    for c in rows["rationale"]:
        for i in known(c[0]):
            if i in rat_ids:
                rationale.add(i)
            else:
                errors.append(f"rationale row {i} is not verbatim in the rationale file")

    groups: Dict[Tuple[str, str], List[FrozenSet]] = {}
    for e in after["entries"]:
        for o in e["occurrences"]:
            groups.setdefault((e["id"], renames.get(o["heading_ctx"], o["heading_ctx"])), []).append(
                frozenset(map(tuple, o["applicability"])))
    res = {"baseline_units": len(base), "baseline_occurrences": 0, "present": 0, "policy_mapped": 0, "reworded": 0,
           "rationale": 0, "unreachable": 0, "occurrence_deficit": 0, "unmapped_removed": 0}
    unmapped, bad_rationale = set(), set()
    wanted: Dict[Tuple[str, str], List[FrozenSet]] = {}
    for e in baseline["entries"]:
        for o in e["occurrences"]:
            req = frozenset(map(tuple, o["applicability"]))
            if e["id"] in narrowed:
                req = req & frozenset(narrowed[e["id"]])
            wanted.setdefault((e["id"], o["heading_ctx"]), []).append(req)
    for (gid, ctx), reqs in sorted(wanted.items()):
        supply = groups.get((gid, ctx), [])
        hit, missed, leftover = _match(reqs, supply)
        res["baseline_occurrences"] += len(reqs)
        res["present"] += hit
        res["occurrence_deficit"] += len(missed)
        remaining = len(missed)
        if gid in policy:
            res["policy_mapped"] += remaining
            remaining = 0
        elif gid in rewrites:
            # a dedupe only covers an occurrence whose workflows x variants the kept copy also reaches (E63d)
            done = sum(1 for m in missed if any(m <= s for s in supply)) if rewrites[gid] == "dedupe" else remaining
            res["reworded"] += done
            remaining -= done
        elif gid in rationale:
            if base[gid]["normative"]:
                bad_rationale.add(gid)
            else:
                res["rationale"] += remaining
                remaining = 0
        if remaining:
            res["unreachable"] += min(remaining, leftover)
            res["unmapped_removed"] += remaining
            unmapped.add(gid)
    res.update({"unmapped_ids": sorted(unmapped), "normative_in_rationale": len(bad_rationale),
                "rows_missing_review": missing_review, "row_errors": sorted(set(errors))})
    return res


def exit_code(res: dict) -> int:
    bad = ("unmapped_removed", "unreachable", "normative_in_rationale", "rows_missing_review")
    return 1 if any(res[k] for k in bad) or res["row_errors"] else 0


# --------------------------------------------------------------------------------------- report


def report_nonnormative(corpus: Dict[str, str], files: List[str], allowlist, list_units: bool = False) -> dict:
    """Bytes of whole non-normative units only (L3 basis, A15): a normative unit contributes 0 bytes."""
    allow, out, listing, total = frozenset(allowlist), {}, [], 0
    for f in files:
        by_heading: Dict[str, int] = {}
        n = size = 0
        for u in extract_units(corpus[f], f):
            if classify_normative(u, allow):
                continue
            b = len(u["raw"].encode("utf-8"))
            n, size = n + 1, size + b
            by_heading[u["heading_ctx"] or "(no heading)"] = by_heading.get(u["heading_ctx"] or "(no heading)", 0) + b
            listing.append({"file": f, "line": u["line"], "heading": u["heading_ctx"], "bytes": b, "text": u["raw"]})
        out[f] = {"bytes": size, "units": n, "by_heading": dict(sorted(by_heading.items()))}
        total += size
    res = {"files": out, "total": total}
    if list_units:
        res["units"] = listing
    return res


def render_markdown(res: dict, ref: Optional[str]) -> str:
    out = [f"# Non-normative units at {ref or 'working tree'}", "",
           f"Total: {res['total']} bytes over {sum(v['units'] for v in res['files'].values())} whole units.", ""]
    for f, info in res["files"].items():
        out += [f"## {f} ({info['units']} units, {info['bytes']} bytes)", ""]
        out += [f"- L{u['line']} [{u['heading'] or '(no heading)'}] {u['bytes']} B: {' '.join(u['text'].split())}"
                for u in res.get("units", []) if u["file"] == f]
        out.append("")
    return "\n".join(out)


# --------------------------------------------------------------------------------------- cli


def _fail(msg: str) -> int:
    print(json.dumps({"error": msg}, sort_keys=True))
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Occurrence-aware router rule inventory")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--emit", action="store_true")
    mode.add_argument("--diff", action="store_true")
    mode.add_argument("--report-nonnormative", action="store_true")
    ap.add_argument("--plugin-root", default=str(PLUGIN_ROOT))
    ap.add_argument("--ref", default=None)
    ap.add_argument("--readmap-baseline", default=None)
    ap.add_argument("--readmap", default=None)
    ap.add_argument("--allowlist", default=str(FIXTURES / "router-normative-allowlist.json"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--policy-map", default=None)
    ap.add_argument("--rationale", default=None)
    ap.add_argument("--files", nargs="+", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--format", choices=("json", "md"), default="json")
    a = ap.parse_args(argv)
    root = Path(a.plugin_root)
    try:
        sha = None
        if a.ref:
            r = subprocess.run(["git", "-C", str(root), "rev-parse", "--verify", a.ref + "^{commit}"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return _fail(f"unknown ref {a.ref}")
            sha = r.stdout.strip()
        if a.emit:
            if not a.readmap_baseline or not a.out:
                return _fail("--emit requires --readmap-baseline and --out")
            rm = load_readmap(a.readmap_baseline)
            inv = build_inventory(cor.read_corpus(root, sha, rm), rm, load_allowlist(a.allowlist), ref=sha)
            cor.atomic_write(a.out, dump_inventory(inv))
            units, occ = len(inv["entries"]), sum(len(e["occurrences"]) for e in inv["entries"])
            norm = sum(1 for e in inv["entries"] if e["normative"])
            print(json.dumps({"ref": sha, "units": units, "occurrences": occ, "normative_units": norm,
                              "non_normative_units": units - norm, "out": a.out}, sort_keys=True))
            return 0
        if a.diff:
            if not a.baseline or not a.policy_map:
                return _fail("--diff requires --baseline and --policy-map")
            rm = load_readmap(a.readmap or root / "config" / "router-readmap.json")
            rat = Path(a.rationale) if a.rationale else Path(a.policy_map).with_name("router-rationale.md")
            res = diff_inventory(json.loads(Path(a.baseline).read_text(encoding="utf-8")), cor.read_corpus(root, sha, rm), rm,
                                 Path(a.policy_map).read_text(encoding="utf-8"),
                                 rat.read_text(encoding="utf-8") if rat.is_file() else "", root)
            print(json.dumps(res, sort_keys=True))
            return exit_code(res)
        if not a.files:
            return _fail("--report-nonnormative requires --files")
        files = [str(Path(f).resolve().relative_to(root.resolve())) if Path(f).is_absolute() else f for f in a.files]
        allow = load_allowlist(a.allowlist) if Path(a.allowlist).is_file() else set()
        res = report_nonnormative({f: cor.read_text(root, f, sha) for f in files}, files, allow, a.list)
        text = render_markdown(res, sha) if a.format == "md" else json.dumps(res, sort_keys=True)
        if a.out:  # write the full listing to a file; stdout stays a one-line JSON summary
            cor.atomic_write(a.out, (text + "\n").encode("utf-8"))
            text = json.dumps({"total": res["total"], "units": sum(v["units"] for v in res["files"].values()),
                               "out": a.out}, sort_keys=True)
        print(text)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        return _fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
