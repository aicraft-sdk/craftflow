#!/usr/bin/env python3
"""Occurrence-aware router rule inventory (DD-4, DD-5, BC-8, BC-9).

Splits the runtime router corpus into whole units (list items with continuation lines, table headers and body rows,
paragraphs, fenced blocks, [EASY TO MISS ...] brackets and skill frontmatter descriptions; deliberately no sentence
unit), gives each unit a stable id, records every occurrence with its heading path, list label and the workflow x
variant x reach triples that read its file, and accounts for every removed occurrence against the policy map.

  --emit --readmap-baseline RB [--ref SHA] [--allowlist A] --out PATH [--force]   write the baseline inventory
  --diff --baseline F --policy-map M [--rationale R] [--readmap RM] [--readmap-baseline RB] [--lock L]
                                                                                  BC-9 accounting, exit 1 on any gap
  --report-nonnormative --files F... [--list] [--format json|md]                  whole non-normative unit bytes (L3)

Flags that a mode does not use are an argparse error (exit 2). Python 3.9 compatible. Output is one JSON line (or the
written inventory); deterministic bytes (BC-8). Errors are one JSON line on stderr, exit 1. Unit extraction is in
craftflow_rule_inventory_units.py, policy-map table parsing in _rows.py, read-map coverage in _readmap.py and the
reach, read-map pinning, baseline lock and rewrite-relation checks in _reach.py.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

import craftflow_router_corpus as cor
from craftflow_rule_inventory_reach import (covers, group_relations, narrowed_cover, near_rule_count, related,  # noqa: F401
                                            verify_lock, widening)
from craftflow_rule_inventory_readmap import (CLASSES, RANK, check_readmap_files, coverage,  # noqa: F401
                                               load_readmap)
from craftflow_rule_inventory_rows import (EXCERPT_LEN, HEADERS, KINDS, REVIEWER, SECTIONS, STATUSES,  # noqa: F401
                                           VARIANTS, file_is, pairs_of, parse_tables, quote_in_excerpt, split_loc,
                                           symbol_ok, tokens_of)
from craftflow_rule_inventory_units import (ROW_SEP, classify_normative, extract_units, load_allowlist,  # noqa: F401
                                            make_id, normalize, normalize_fence)

PLUGIN_ROOT = cor.PLUGIN_ROOT
FIXTURES = PLUGIN_ROOT / "tests" / "fixtures"
DEFAULT_ALLOWLIST = FIXTURES / "router-normative-allowlist.json"
DEFAULT_READMAP_BASELINE = FIXTURES / "router-readmap.baseline.json"
DEFAULT_LOCK = FIXTURES / "router-baseline.lock.json"
BASELINE_VERSION = 3
HEX_ID = re.compile(r"\b[0-9a-f]{12}\b")
RANGE = re.compile(r"([\w./-]+\.md):(\d+)-(\d+)@([0-9a-f]{7,40})")


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
            if len(u["norm"]) > EXCERPT_LEN:  # the full text is only kept when the excerpt is not already all of it
                e["text"] = u["norm"]
            e["occurrences"].append({"file": rel, "heading_ctx": u["heading_ctx"], "heading_path": u["heading_path"],
                                     "label_ctx": u["label_ctx"], "applicability": applic})
    entries = [ents[k] for k in sorted(ents)]
    for e in entries:
        e["occurrences"].sort(key=lambda o: json.dumps(o, sort_keys=True))
    return {"version": BASELINE_VERSION, "ref": ref, "entries": entries, "allowlist_unmatched": sorted(allow - norms)}


def dump_inventory(inv: dict) -> bytes:
    body = ",\n".join(json.dumps(e, sort_keys=True, ensure_ascii=False) for e in inv["entries"])
    return ('{"entries":[\n%s\n],"ref":%s,"version":%d}\n' % (body, json.dumps(inv["ref"]), inv["version"])).encode("utf-8")


def _check_baseline(base) -> None:
    try:
        ok = base["version"] == BASELINE_VERSION and isinstance(base["entries"], list) and bool(base["entries"])
        if len({e["id"] for e in base["entries"]}) != len(base["entries"]):
            raise ValueError("malformed baseline inventory: duplicate unit ids")
        for e in base["entries"]:
            ok = ok and bool(e["id"]) and isinstance(e["normative"], bool) and bool(e["occurrences"])
            for o in e["occurrences"]:
                ok = ok and isinstance(o["file"], str) and isinstance(o["heading_ctx"], str) \
                    and isinstance(o["label_ctx"], str) and isinstance(o["heading_path"], list) \
                    and isinstance(o["applicability"], list) and all(len(t) == 3 for t in o["applicability"])
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise ValueError(f"malformed baseline inventory (expected version {BASELINE_VERSION} with non-empty "
                         "entries/occurrences, heading paths, label_ctx and (workflow, variant, reach) applicability)")


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
        k = next((n for n, c in enumerate(avail) if covers(c, req)), None)
        if k is None:
            missed.append(req)
        else:
            avail.pop(k)
            hit += 1
    return hit, missed, len(avail)


def _text(entry: dict) -> str:
    return entry.get("text", entry["excerpt"])


def _body(entry: dict) -> str:
    """The text a rewrite row is compared on: a table row's body without the header folded into its id (H-H1)."""
    return _text(entry).partition(ROW_SEP)[2] if entry["kind"] == "table_row" else _text(entry)


def _sections(text: str) -> Dict[str, str]:
    """Normalized heading -> its section text (up to the next heading of the same or a higher level) for a pointer check."""
    lines, out = text.split("\n"), {}
    heads = cor.headings(text)
    for k, (start, level, title) in enumerate(heads):
        end = next((h[0] for h in heads[k + 1:] if h[1] <= level), len(lines))
        key = cor.norm_text(title)
        out[key] = out.get(key, "") + "\n".join(lines[start + 1:end]) + "\n"
    return out


def _with_derived(ids: List[str], pool: Dict[str, dict]) -> List[str]:
    """`[EASY TO MISS ...]` spans are units of their own: they move with the unit that contains them (R-H3c)."""
    parents = [_text(pool[i]) for i in ids if pool[i]["kind"] != "easy_to_miss"]
    extra = [k for k, e in pool.items() if e["kind"] == "easy_to_miss" and k not in ids and any(_text(e) in p for p in parents)]
    return ids + sorted(extra)


def diff_inventory(baseline: dict, after_corpus: Dict[str, str], readmap: dict, policy_text: str,
                   rationale_text: str = "", plugin_root=None, allowlist=frozenset(), *, baseline_readmap) -> dict:
    """`baseline_readmap` is the read map frozen at the baseline (the CLI passes the lock-pinned file); it is required
    so a caller can never compare the live map with itself (H-L1)."""
    _check_baseline(baseline)
    if not after_corpus:
        raise ValueError("empty after corpus: refusing to report '0 lost' against nothing")
    after = build_inventory(after_corpus, readmap, allowlist)
    start_files = {e["file"] for e in readmap["files"] if e["class"] in ("kernel", "start")}
    after_ids = {e["id"] for e in after["entries"]}
    after_by_id = {e["id"]: e for e in after["entries"]}
    file_heads = {f: {cor.norm_text(h[2]) for h in cor.headings(t)} for f, t in after_corpus.items()}
    dests = {Path(f).name.lower(): _sections(t) for f, t in after_corpus.items()}  # file -> heading -> section text
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
        missing_review += 0 if REVIEWER.match(c[6]) else 1
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

    rewrites: Dict[str, Tuple[str, List[str]]] = {}  # old id -> (kind, the new ids that must carry its reach)
    renames: Dict[Tuple[str, str], str] = {}  # (file, new heading) -> old heading, per file (H-L4)
    moves: List[Tuple[List[str], List[str], str]] = []  # section-move: (source path, destination path, destination file)
    label_renames: Dict[str, str] = {}  # a reworded label paragraph: its new text -> the baseline text
    base_occ = [(e["id"], o) for e in baseline["entries"] for o in e["occurrences"]]
    after_occ = [(e["id"], o) for e in after["entries"] for o in e["occurrences"]]
    for c in rows["rewrites"]:
        kind = c[2].strip().lower()
        if kind not in KINDS:
            errors.append(f"rewrite row has unknown kind '{c[2]}'")
            continue
        if not c[4] or not REVIEWER.match(c[5]):
            missing_review += 1
        if kind in ("heading", "section-move"):
            (f0, p0), (f1, p1) = split_loc(c[0]), split_loc(c[1])
            one = kind == "heading"
            if not f0 or not f1 or not p0 or not p1 or (one and (f0 != f1 or len(p0) != 1 or len(p1) != 1)):
                errors.append(f"{kind} row needs file#heading cells" + (" naming the same file" if one else "") + f": {c[0]} -> {c[1]}")
                continue
            has = (lambda o, p: p[0] in o["heading_path"]) if one else (lambda o, p: o["heading_path"][:len(p)] == p)
            if not any(file_is(f0, o["file"]) and has(o, p0) for _, o in base_occ):
                errors.append(f"{kind} row: source '{c[0]}' names no baseline section")
            elif one and not any(file_is(f1, f) and p1[0] in hs for f, hs in file_heads.items()):
                errors.append(f"heading row: '{p1[0]}' is not a heading of {f1}")
            elif not one and not any(file_is(f1, o["file"]) and has(o, p1) for _, o in after_occ):
                errors.append(f"section-move row: destination '{c[1]}' holds no unit in the current corpus")
            elif one:
                renames[(f1, p1[0])] = p0[0]
            else:
                moves.append((p0, p1, f1))
            continue
        new_ids = HEX_ID.findall(c[1])
        if not new_ids or any(n not in after_ids for n in new_ids):
            errors.append(f"rewrite row names replacement ids that do not exist: {c[1]}")
            continue
        ids = known(c[0])
        if kind == "group":
            ids, new_ids = _with_derived(ids, base), _with_derived(new_ids, after_by_id)
            rel, lone, weak = group_relations({i: _body(base[i]) for i in ids}, {n: _body(after_by_id[n]) for n in new_ids})
            if lone:
                errors.append(f"group row has ids that relate to nothing on the other side ({', '.join(lone)}): {c[0]} -> {c[1]}")
                continue
            if weak:
                errors.append(f"group row ids keep under 60% retention or change a negation token ({', '.join(weak)}): {c[0]} -> {c[1]}")
                continue
            errors.extend(f"duplicate rewrite row for id {i}" for i in ids if i in rewrites)
            rewrites.update({i: ("group", rel[i]) for i in ids})
            continue
        news = [_body(after_by_id[n]) for n in new_ids]
        unrelated = [i for i in ids if not related(_body(base[i]), news, kind,
                                                   sorted({o["heading_ctx"] for o in base[i]["occurrences"]}), dests)]
        if unrelated:  # the new ids exist, but a row may not point a rule at text that does not carry it (A8r)
            errors.append(f"rewrite row relates ids with under 60% token overlap: {c[0]} -> {c[1]}")
            continue
        errors.extend(f"duplicate rewrite row for id {i}" for i in ids if i in rewrites)
        rewrites.update({i: (kind, new_ids) for i in ids})
        if len(ids) == len(new_ids) == 1 and _text(base[ids[0]]).endswith(":"):
            label_renames[_text(after_by_id[new_ids[0]])] = _text(base[ids[0]])

    # an occurrence is identified by its unit id, last heading, file, full heading path and list label (D4, A4, A10)
    occ_cov = {(e["id"], o["heading_ctx"], o["file"], tuple(o["heading_path"]), o["label_ctx"]):
               frozenset(map(tuple, o["applicability"])) for e in baseline["entries"] for o in e["occurrences"]}
    occs = list(occ_cov)
    narrowed: Dict[tuple, FrozenSet] = {}  # per occurrence, never per unit id (H-H3)
    for c in rows["applicability"]:
        if not c[3].strip(' "`'):
            errors.append(f"applicability row has no quoted scope words: {c[0]}")
            continue
        if not REVIEWER.match(c[4]):
            missing_review += 1
        toks = tokens_of(c[2], errors)
        pairs = set(toks)
        if not pairs:
            continue
        sect = re.match(r"^([\w./-]+)#([^@]+)(?:@\S+)?$", c[0].strip())
        if sect:
            want = cor.norm_text(sect.group(2))
            targets = [t for t in occs if (t[2] == sect.group(1) or t[2].endswith("/" + sect.group(1))) and t[1] == want]
        else:
            named = set(known(c[0]))
            targets = [t for t in occs if t[0] in named]
        real = lambda t: {(w, v) for w, v, _ in occ_cov[t]}  # noqa: E731
        if not targets:
            errors.append(f"applicability row names no baseline section or id: {c[0]}")
        elif not any(quote_in_excerpt(c[3], base[t[0]]["excerpt"]) for t in targets):
            errors.append(f"applicability row quotes words that are not in the named rule: {c[3]}")
        elif any(t in narrowed for t in targets):
            errors.append(f"duplicate applicability row for an occurrence of {c[0]}")
        elif any(real(t) != pairs_of(c[1], errors) for t in targets):  # the cell must state the real baseline coverage
            errors.append(f"applicability row's baseline coverage '{c[1]}' differs from the occurrence's real coverage: {c[0]}")
        elif any(not pairs <= real(t) for t in targets):
            errors.append(f"applicability row narrowed coverage is outside the baseline coverage:{c[2]}")
        elif any(toks[(w, v)] and RANK[toks[(w, v)]] > RANK[r] for t in targets for w, v, r in occ_cov[t] if (w, v) in toks):
            errors.append(f"applicability row narrows to a reach earlier than the baseline reach:{c[2]}")
        else:
            narrowed.update({t: narrowed_cover(occ_cov[t], toks) for t in targets})

    rationale: Set[str] = set()
    off_start: Set[str] = set()
    for c in rows["rationale"]:
        missing_review += 0 if REVIEWER.match(c[3]) else 1
        for i in known(c[0]):
            if i not in rat_ids:
                errors.append(f"rationale row {i} is not verbatim in the rationale file")
                continue
            rationale.add(i)
            if any(o["file"] not in start_files for o in base[i]["occurrences"]):
                off_start.add(i)  # the L3 budget counts start files only, so only they may shed prose to the rationale file
                errors.append(f"rationale row {i} has an occurrence outside the start-class files")

    widened, unsigned = widening(coverage(readmap), coverage(baseline_readmap), rows["widening"], errors)
    missing_review += unsigned
    ren = lambda o, h: next((old for (rf, new), old in renames.items() if new == h and file_is(rf, o["file"])), h)  # noqa: E731

    def where(o: dict) -> tuple:
        """An after occurrence mapped back to the baseline name of its place: a renamed heading takes its old name and
        a unit under a section-move destination takes the source path (the unit's own file is checked by the row)."""
        path = list(o["heading_path"])
        for src, dst, f in moves:
            if file_is(f, o["file"]) and path[:len(dst)] == dst:
                path = src + [ren(o, h) for h in path[len(dst):]]
                break
        else:
            path = [ren(o, h) for h in path]
        return tuple(path), label_renames.get(o["label_ctx"], o["label_ctx"])

    groups: Dict[tuple, List[FrozenSet]] = {}
    by_id: Dict[str, List[FrozenSet]] = {}
    for e in after["entries"]:
        for o in e["occurrences"]:
            cover = frozenset(map(tuple, o["applicability"]))
            groups.setdefault((e["id"], *where(o)), []).append(cover)
            by_id.setdefault(e["id"], []).append(cover)
    res = {"baseline_units": len(base), "baseline_occurrences": 0, "present": 0, "policy_mapped": 0, "reworded": 0,
           "rationale": 0, "unreachable": 0, "occurrence_deficit": 0, "unmapped_removed": 0}
    unmapped, bad_rationale = set(), set()
    wanted: Dict[tuple, List[FrozenSet]] = {}
    for e in baseline["entries"]:
        for o in e["occurrences"]:
            key = (e["id"], o["heading_ctx"], o["file"], tuple(o["heading_path"]), o["label_ctx"])
            wanted.setdefault((e["id"], tuple(o["heading_path"]), o["label_ctx"]), []).append(narrowed.get(key, occ_cov[key]))
    for (gid, *place), reqs in sorted(wanted.items()):
        supply = groups.get((gid, *place), [])
        hit, missed, leftover = _match(reqs, supply)
        res["baseline_occurrences"] += len(reqs)
        res["present"] += hit
        res["occurrence_deficit"] += len(missed)
        remaining = len(missed)
        if gid in policy:
            res["policy_mapped"] += remaining
            remaining = 0
        elif gid in rewrites:
            # every non-heading kind needs each replacement id to be read wherever the removed occurrence was, at least
            # as early (E63d, R-H1); a replacement may sit under any heading in any file. Losing reach takes an
            # Applicability row with @reach, never a Rewrites row.
            _, targets = rewrites[gid]
            done = sum(1 for m in missed if all(any(covers(s, m) for s in by_id.get(n, [])) for n in targets))
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
    res.update({"unmapped_ids": sorted(unmapped), "normative_in_rationale": len(bad_rationale), "readmap_widened": widened,
                "added_normative_near_rule": near_rule_count(baseline["entries"], after["entries"], {new: old for (_, new), old in renames.items()}),
                "rows_missing_review": missing_review, "row_errors": sorted(set(errors))})
    return res


def exit_code(res: dict) -> int:
    bad = ("unmapped_removed", "unreachable", "normative_in_rationale", "rows_missing_review", "readmap_widened")
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
              "diff": {"baseline", "policy_map", "rationale", "readmap", "readmap_baseline", "lock"},
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


def _refuse_baseline_clobber(out: str) -> None:
    """The report mode must never replace a baseline-shaped inventory (version + ref keys)."""
    if Path(out).is_file():
        try:
            have = cor.load_json(out)
        except ValueError:
            return
        if isinstance(have, dict) and {"version", "ref"} <= set(have):
            raise ValueError(f"{out} holds a baseline inventory; --report-nonnormative will not overwrite it")


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
    ap.add_argument("--lock", default=None, help="--diff: baseline lock file (default tests/fixtures/router-baseline.lock.json)")
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
            if a.force and not sha:  # a forced overwrite with no commit would write a baseline that vouches for itself (D3)
                return _fail("--emit --force requires an explicit --ref: a baseline with ref null cannot be locked")
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
            rmb = a.readmap_baseline or DEFAULT_READMAP_BASELINE
            base = verify_lock(a.baseline, rmb, a.lock or DEFAULT_LOCK)  # the baseline and its read map are pinned (D3)
            rm = load_readmap(a.readmap or root / "config" / "router-readmap.json")
            corpus = cor.read_corpus(root, sha, rm)
            check_readmap_files(rm, corpus)
            rat = Path(a.rationale) if a.rationale else Path(a.policy_map).with_name("router-rationale.md")
            res = diff_inventory(base, corpus, rm, Path(a.policy_map).read_text(encoding="utf-8"),
                                 rat.read_text(encoding="utf-8") if rat.is_file() else "", root,
                                 allowlist=_allowlist(None), baseline_readmap=load_readmap(rmb))
            print(json.dumps(res, sort_keys=True))
            return exit_code(res)
        if not a.files:
            return _fail("--report-nonnormative requires --files")
        files = [str(Path(f).resolve().relative_to(root.resolve())) if Path(f).is_absolute() else f for f in a.files]
        files = list(dict.fromkeys(files))  # a path named twice must not be counted twice (H-L4)
        res = report_nonnormative({f: cor.read_text(root, f, sha) for f in files}, files, _allowlist(a.allowlist), a.list)
        text = render_markdown(res, sha) if a.format == "md" else json.dumps(res, sort_keys=True)
        if a.out:
            _refuse_baseline_clobber(a.out)  # write the full listing to a file; stdout stays a one-line JSON summary
            cor.atomic_write(a.out, (text + "\n").encode("utf-8"))
            text = json.dumps({"total": res["total"], "units": sum(v["units"] for v in res["files"].values()),
                               "out": a.out}, sort_keys=True)
        print(text)
        return 0
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return _fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
