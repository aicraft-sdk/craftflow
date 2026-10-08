#!/usr/bin/env python3
"""Occurrence-aware router rule inventory (DD-4, DD-5, BC-8, BC-9).

Splits the runtime router corpus into whole units (list items with continuation lines, table body rows,
paragraphs, fenced blocks and [EASY TO MISS ...] brackets; deliberately no sentence unit), gives each unit a
stable id, records every occurrence with its heading context and the workflow x variant pairs that read its
file, and accounts for every removed occurrence against the policy map.

  --emit --readmap-baseline RB [--ref SHA] [--allowlist A] --out PATH [--force]   write the baseline inventory
  --diff --baseline F --policy-map M [--rationale R] [--readmap RM]               BC-9 accounting, exit 1 on any gap
  --report-nonnormative --files F... [--list] [--format json|md]                  whole non-normative unit bytes (L3)

Flags that a mode does not use are an argparse error (exit 2). Python 3.9 compatible. Output is one JSON line (or the
written inventory); deterministic bytes (BC-8). Errors are one JSON line on stderr, exit 1. Policy-map table parsing
and row checks live in craftflow_rule_inventory_rows.py.
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
from craftflow_rule_inventory_readmap import (CLASSES, check_readmap_files, coverage,  # noqa: F401
                                               load_readmap)
from craftflow_rule_inventory_rows import (EXCERPT_LEN, HEADERS, KINDS, REVIEWER, SECTIONS, STATUSES,  # noqa: F401
                                           VARIANTS, pairs_of, parse_tables, quote_in_excerpt, symbol_ok)

PLUGIN_ROOT = cor.PLUGIN_ROOT
FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
DEFAULT_ALLOWLIST = FIXTURES / "router-normative-allowlist.json"
KEYWORDS = re.compile(r"(?i)\b(never|must|do not|always|only|required|requires|shall|fail closed)\b|MUST|MANDATORY|\[EASY TO MISS")
VERBS3 = re.compile(r"(?i)\b(skips?|stops?|blocks?|denies|fails?|advances?|halts?|aborts?|should|anything (?:other than|else)"
                    r"|authoritative)\b|\+=|→|->")
IMPERATIVES = frozenset(
    "read run set record append persist create skip stop treat use ask write dispatch mark include track apply "
    "before after if when do call parse release re-read delete keep verify check load resolve extract "
    "increment update count otherwise prefer".split())
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


def _bracket_close(lines: List[str], mask: List[bool], i: int, cur_lines: List[str], window: int = 12) -> Optional[int]:
    """Index of the line closing an [EASY TO MISS ...] bracket that blank line `i` leaves open, else None."""
    text = "\n".join(cur_lines)
    start, depth = text.rfind("[EASY TO MISS"), 0
    if start < 0:
        return None
    for ch in text[start:]:
        depth += (ch == "[") - (ch == "]")
        if depth == 0:
            return None
    for k in range(i + 1, min(i + 1 + window, len(lines))):
        s = lines[k].strip()
        if mask[k] or re.match(r"^#{1,6}\s", s) or s.startswith("|") or LIST_MARK.match(s):
            return None
        depth += s.count("[") - s.count("]")
        if depth <= 0:
            return k
    return None


def extract_units(text: str, file: str = "") -> List[dict]:
    """Whole units in file order: {kind, raw, norm, id, line, numbered, heading_ctx, heading_path}."""
    lines = text.replace("\r\n", "\n").split("\n")
    i = 0
    if lines and lines[0].strip() == "---":
        close = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
        i = 0 if close is None else close + 1
    mask, unclosed = cor.fence_scan(lines)
    if unclosed:  # an unclosed fence would silently swallow the rest of the file as one block
        raise ValueError(f"unclosed fence opened at line {unclosed} of {file or 'text'}")
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
        elif cur and cur["kind"] == "paragraph" and re.match(r"^(={2,}|-{2,})$", s):
            raise ValueError(f"setext heading at line {i + 1} of {file or 'text'} is unsupported; use an ATX '#' heading")
        elif not s and cur and (k := _bracket_close(lines, mask, i, cur["lines"])) is not None:
            cur["lines"].extend(lines[i:k + 1])  # [EASY TO MISS ...] spanning a blank line stays one unit
            i = k
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
    first = unit["norm"].split(" ")[0].strip(":,.;`*_(") if unit["norm"] else ""
    return bool(KEYWORDS.search(unit["norm"]) or VERBS3.search(unit["norm"]) or first in IMPERATIVES)


def load_allowlist(path) -> Set[str]:
    data = cor.load_json(path)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError(f"malformed normative allowlist: {path}")
    texts = set()
    for e in data["entries"]:
        if not isinstance(e, dict) or not isinstance(e.get("text"), str):
            raise ValueError(f"allowlist entry needs a string 'text': {e!r}")
        if normalize(e["text"]) != e["text"]:
            raise ValueError(f"allowlist text is not in normal form (normalize() would change it): {e['text']!r}")
        texts.add(e["text"])
    return texts


# --------------------------------------------------------------------------------------- inventory


def build_inventory(corpus: Dict[str, str], readmap: dict, allowlist, ref: Optional[str] = None) -> dict:
    if not corpus:
        raise ValueError("empty corpus: no router files to inventory")
    cov, allow, ents, norms = coverage(readmap), frozenset(allowlist), {}, set()
    unlisted = sorted(set(corpus) - set(cov))
    if unlisted:
        raise ValueError(f"corpus files missing from the read map: {unlisted}")
    for rel in sorted(corpus):
        applic = sorted([list(p) for p in cov[rel]])
        for u in extract_units(corpus[rel], rel):
            norms.add(u["norm"])
            e = ents.setdefault(u["id"], {"id": u["id"], "kind": u["kind"], "normative": False,
                                          "excerpt": u["norm"][:EXCERPT_LEN], "occurrences": []})
            e["normative"] = e["normative"] or classify_normative(u, allow)
            e["occurrences"].append({"file": rel, "heading_ctx": u["heading_ctx"], "heading_path": u["heading_path"],
                                     "applicability": applic})
    entries = [ents[k] for k in sorted(ents)]
    for e in entries:
        e["occurrences"].sort(key=lambda o: json.dumps(o, sort_keys=True))
    return {"version": 2, "ref": ref, "entries": entries, "allowlist_unmatched": sorted(allow - norms)}


def dump_inventory(inv: dict) -> bytes:
    body = ",\n".join(json.dumps(e, sort_keys=True, ensure_ascii=False) for e in inv["entries"])
    return ('{"entries":[\n%s\n],"ref":%s,"version":%d}\n' % (body, json.dumps(inv["ref"]), inv["version"])).encode("utf-8")


def _check_baseline(base) -> None:
    try:
        ok = base["version"] == 2 and isinstance(base["entries"], list) and bool(base["entries"])
        for e in base["entries"]:
            ok = ok and bool(e["id"]) and isinstance(e["normative"], bool) and bool(e["occurrences"])
            for o in e["occurrences"]:
                ok = ok and isinstance(o["file"], str) and isinstance(o["heading_ctx"], str) \
                    and isinstance(o["applicability"], list)
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise ValueError("malformed baseline inventory (expected version 2 with non-empty entries/occurrences)")


# --------------------------------------------------------------------------------------- policy map


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
    if not after_corpus:
        raise ValueError("empty after corpus: refusing to report '0 lost' against nothing")
    after = build_inventory(after_corpus, readmap, allowlist)
    start_files = {e["file"] for e in readmap["files"] if e["class"] in ("kernel", "start")}
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
        script_ok, test_ok = symbol_ok(root, c[2], ("", "scripts")), symbol_ok(root, c[3], ("", "tests/fixtures", "scripts"))
        live = c[5].strip().lower() == "true"
        if c[4] not in STATUSES:
            errors.append(f"policy row has unknown status '{c[4]}'")
        if not script_ok or not test_ok:
            errors.append(f"policy row names a missing script or test: {c[2]} / {c[3]}")
        if c[4] == "deleted" and not live:
            errors.append(f"policy row is 'deleted' but live_verified is not true: {c[0]}")
        if c[4] == "deleted" and script_ok and test_ok and live:
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

    occs = [(e["id"], o["heading_ctx"], o["file"]) for e in baseline["entries"] for o in e["occurrences"]]
    narrowed: Dict[Tuple[str, str, str], Set[Tuple[str, str]]] = {}  # per occurrence, never per unit id (H-H3)
    for c in rows["applicability"]:
        if not c[3].strip(' "`'):
            errors.append(f"applicability row has no quoted scope words: {c[0]}")
            continue
        if not REVIEWER.match(c[4]):
            missing_review += 1
        pairs = pairs_of(c[2], errors)
        if not pairs:
            continue
        sect = re.match(r"^([\w./-]+)#([^@]+)(?:@\S+)?$", c[0].strip())
        if sect:
            want = cor.norm_text(sect.group(2))
            targets = [t for t in occs if (t[2] == sect.group(1) or t[2].endswith("/" + sect.group(1))) and t[1] == want]
        else:
            named = set(known(c[0]))
            targets = [t for t in occs if t[0] in named]
        if not targets:
            errors.append(f"applicability row names no baseline section or id: {c[0]}")
        elif not any(quote_in_excerpt(c[3], base[t[0]]["excerpt"]) for t in targets):
            errors.append(f"applicability row quotes words that are not in the named rule: {c[3]}")
        elif any(t in narrowed for t in targets):
            errors.append(f"duplicate applicability row for an occurrence of {c[0]}")
        else:
            narrowed.update({t: pairs for t in targets})

    rationale: Set[str] = set()
    off_start: Set[str] = set()
    for c in rows["rationale"]:
        for i in known(c[0]):
            if i not in rat_ids:
                errors.append(f"rationale row {i} is not verbatim in the rationale file")
                continue
            rationale.add(i)
            if any(o["file"] not in start_files for o in base[i]["occurrences"]):
                off_start.add(i)  # the L3 budget counts start files only, so only they may shed prose to the rationale file
                errors.append(f"rationale row {i} has an occurrence outside the start-class files")

    groups: Dict[Tuple[str, str], List[FrozenSet]] = {}
    by_id: Dict[str, List[FrozenSet]] = {}
    for e in after["entries"]:
        for o in e["occurrences"]:
            cover = frozenset(map(tuple, o["applicability"]))
            groups.setdefault((e["id"], renames.get(o["heading_ctx"], o["heading_ctx"])), []).append(cover)
            by_id.setdefault(e["id"], []).append(cover)
    res = {"baseline_units": len(base), "baseline_occurrences": 0, "present": 0, "policy_mapped": 0, "reworded": 0,
           "rationale": 0, "unreachable": 0, "occurrence_deficit": 0, "unmapped_removed": 0}
    unmapped, bad_rationale = set(), set()
    wanted: Dict[Tuple[str, str], List[FrozenSet]] = {}
    for e in baseline["entries"]:
        for o in e["occurrences"]:
            req = frozenset(map(tuple, o["applicability"]))
            key = (e["id"], o["heading_ctx"], o["file"])
            if key in narrowed:
                req = req & frozenset(narrowed[key])
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
            # the kept copy may sit under any heading in any file (R-H1), so the cover is every after-copy of the id
            kept = by_id.get(gid, [])
            done = sum(1 for m in missed if any(m <= s for s in kept)) if rewrites[gid] == "dedupe" else remaining
            res["reworded"] += done
            remaining -= done
        elif gid in rationale:
            if base[gid]["normative"]:
                bad_rationale.add(gid)
            elif gid not in off_start:
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
    print(json.dumps({"error": msg}, sort_keys=True), file=sys.stderr)
    return 1


# flags each mode accepts besides --ref/--plugin-root; anything else given explicitly is a usage error (H-M7)
MODE_FLAGS = {"emit": {"readmap_baseline", "allowlist", "out", "force"},
              "diff": {"baseline", "policy_map", "rationale", "readmap"},
              "report_nonnormative": {"allowlist", "out", "files", "list", "format"}}


def _allowlist(path: Optional[str]) -> Set[str]:
    """An explicitly named allowlist must exist; only the defaulted path may be absent."""
    if path:
        if not Path(path).is_file():
            raise ValueError(f"--allowlist {path} does not exist")
        return load_allowlist(path)
    return load_allowlist(DEFAULT_ALLOWLIST) if DEFAULT_ALLOWLIST.is_file() else set()


def _refuse_other_ref(out: str, sha: Optional[str]) -> None:
    """--out must not silently replace a committed baseline that was emitted from a different ref (H-M5)."""
    if not Path(out).is_file():
        return
    try:
        have = cor.load_json(out).get("ref")
    except (ValueError, AttributeError):
        have = "<unreadable>"
    if have != sha:
        raise ValueError(f"{out} holds a baseline for ref {have!r}, not {sha!r}; pass --force to overwrite it")


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
    ap.add_argument("--allowlist", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--policy-map", default=None)
    ap.add_argument("--rationale", default=None)
    ap.add_argument("--files", nargs="+", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--format", choices=("json", "md"), default=None)
    ap.add_argument("--force", action="store_true", help="--emit: overwrite --out even when it holds another ref")
    a = ap.parse_args(argv)
    name = next(m for m in MODE_FLAGS if getattr(a, m))
    given = {k for k, v in vars(a).items() if v not in (None, False, [])} - {"plugin_root", "ref", *MODE_FLAGS}
    if given - MODE_FLAGS[name]:
        ap.error(f"--{name.replace('_', '-')} does not accept " + ", ".join("--" + k.replace("_", "-") for k in sorted(given - MODE_FLAGS[name])))
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
            corpus = cor.read_corpus(root, sha, rm)
            check_readmap_files(rm, corpus)
            inv = build_inventory(corpus, rm, _allowlist(a.allowlist), ref=sha)
            if inv["allowlist_unmatched"]:
                return _fail(f"allowlist_unmatched: {len(inv['allowlist_unmatched'])} allowlist text(s) match no unit, "
                             f"e.g. {inv['allowlist_unmatched'][0]!r}")
            if not a.force:
                _refuse_other_ref(a.out, sha)
            cor.atomic_write(a.out, dump_inventory(inv))
            units, occ = len(inv["entries"]), sum(len(e["occurrences"]) for e in inv["entries"])
            norm = sum(1 for e in inv["entries"] if e["normative"])
            print(json.dumps({"ref": sha, "units": units, "occurrences": occ, "normative_units": norm,
                              "non_normative_units": units - norm, "allowlist_unmatched": 0, "out": a.out}, sort_keys=True))
            return 0
        if a.diff:
            if not a.baseline or not a.policy_map:
                return _fail("--diff requires --baseline and --policy-map")
            rm = load_readmap(a.readmap or root / "config" / "router-readmap.json")
            corpus = cor.read_corpus(root, sha, rm)
            check_readmap_files(rm, corpus)
            rat = Path(a.rationale) if a.rationale else Path(a.policy_map).with_name("router-rationale.md")
            res = diff_inventory(cor.load_json(a.baseline), corpus, rm, Path(a.policy_map).read_text(encoding="utf-8"),
                                 rat.read_text(encoding="utf-8") if rat.is_file() else "", root)
            print(json.dumps(res, sort_keys=True))
            return exit_code(res)
        if not a.files:
            return _fail("--report-nonnormative requires --files")
        files = [str(Path(f).resolve().relative_to(root.resolve())) if Path(f).is_absolute() else f for f in a.files]
        files = list(dict.fromkeys(files))  # a path named twice must not be counted twice (H-L4)
        res = report_nonnormative({f: cor.read_text(root, f, sha) for f in files}, files, _allowlist(a.allowlist), a.list)
        text = render_markdown(res, sha) if a.format == "md" else json.dumps(res, sort_keys=True)
        if a.out:  # write the full listing to a file; stdout stays a one-line JSON summary
            cor.atomic_write(a.out, (text + "\n").encode("utf-8"))
            text = json.dumps({"total": res["total"], "units": sum(v["units"] for v in res["files"].values()),
                               "out": a.out}, sort_keys=True)
        print(text)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        return _fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
