#!/usr/bin/env python3
"""Router corpus loader and cross-reference resolver (DD-14, BC-10, A2).

Loads the runtime router corpus (working tree or a git ref), finds sections without ambiguity, ports the
skill-description parser and checks that every heading cross-reference resolves. Python 3.9 compatible.
CLI: `craftflow_router_corpus.py [--ref SHA]` prints one JSON line listing the unresolved references.
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

from craftflow_router_util import atomic_write, fence_mask, fence_scan, load_json, parse_frontmatter  # noqa: F401

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL = "skills/craftflow-router/SKILL.md"
CURSOR = "skills/cursor-router/SKILL.md"
REPO_PREFIX = "tools/craftflow-plugin/plugins/craftflow/"
ROUTER_DIRS = ("skills/craftflow-router", "skills/_shared")


class AmbiguousHeading(Exception):
    """A heading occurs more than once where exactly one match was required (A2)."""


class SectionNotFound(KeyError):
    """No heading matched."""


def _git(root, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _is_runtime(rel: str) -> bool:
    depth = rel.count("/")
    return rel == SKILL or (rel.endswith(".md") and (
        (rel.startswith("skills/craftflow-router/references/") and depth == 3)
        or (rel.startswith("skills/_shared/") and depth == 2)))


def runtime_files(plugin_root=None, ref: Optional[str] = None, readmap: Optional[dict] = None) -> List[str]:
    """Sorted runtime router files (plugin-relative). Files the read map marks `maintainer` are excluded."""
    root = Path(plugin_root or PLUGIN_ROOT)
    if ref:
        res = _git(root, "ls-tree", "-r", "--name-only", ref, "--", *ROUTER_DIRS)
        if res.returncode != 0:
            raise RuntimeError(f"git ls-tree {ref} failed: {res.stderr.strip()}")
        names = res.stdout.split("\n")
    else:
        names = [p.relative_to(root).as_posix() for d in ROUTER_DIRS for p in (root / d).rglob("*.md")]
    maintainer = {e["file"] for e in (readmap or {}).get("files", []) if e.get("class") == "maintainer"}
    found = sorted(n for n in names if n and _is_runtime(n) and n not in maintainer)
    if not found:  # an empty corpus would read as "0 rules lost" downstream (H-H1)
        raise RuntimeError(f"no router files found under {root} at {ref or 'working tree'}")
    return found


def read_text(plugin_root, rel: str, ref: Optional[str] = None) -> str:
    if ref:
        res = _git(plugin_root, "show", f"{ref}:./{rel}")
        if res.returncode != 0:
            raise RuntimeError(f"git show {ref}:{rel} failed: {res.stderr.strip()}")
        return res.stdout.replace("\r\n", "\n")
    return (Path(plugin_root) / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def read_corpus(plugin_root=None, ref: Optional[str] = None, readmap: Optional[dict] = None) -> Dict[str, str]:
    root = plugin_root or PLUGIN_ROOT
    return {rel: read_text(root, rel, ref) for rel in runtime_files(root, ref, readmap)}


def corpus_text(corpus: Dict[str, str]) -> str:
    return "".join(f"\n<!-- file: {k} -->\n{corpus[k]}" for k in sorted(corpus))


def plugin_loader(plugin_root=None) -> Callable[[str], Optional[str]]:
    """Loader for files outside the runtime corpus (agents/, repo root) used by `resolve`."""
    root = Path(plugin_root or PLUGIN_ROOT)
    repo = root.parents[3] if len(root.parents) > 3 else root

    def load(rel: str) -> Optional[str]:
        for base in (root, repo):
            p = base / rel
            if p.is_file():
                return p.read_text(encoding="utf-8").replace("\r\n", "\n")
        return None

    return load


# --------------------------------------------------------------------------------------- markdown


def headings(text: str) -> List[Tuple[int, int, str]]:
    """(0-based line, level, heading text) for each ATX heading outside fences."""
    lines = text.split("\n")
    mask = fence_mask(lines)
    out = []
    for i, line in enumerate(lines):
        m = None if mask[i] else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            out.append((i, len(m.group(1)), m.group(2)))
    return out


def norm_text(s: str) -> str:
    s = re.sub(r"^\s*#+\s*", "", s)
    s = re.sub(r"[`\"'“”‘’]", "", s)
    return " ".join(s.split()).lower()


def _split_hash(heading: str) -> Tuple[Optional[int], str]:
    m = re.match(r"^\s*(#+)\s+(.*)$", heading)
    return (len(m.group(1)), m.group(2)) if m else (None, heading)


def _matches(text: str, heading: str) -> List[Tuple[int, int, str]]:
    level, want = _split_hash(heading)
    return [h for h in headings(text) if norm_text(h[2]) == norm_text(want) and (level is None or h[1] == level)]


def find_section(text: str, heading: str) -> str:
    """Text of the section whose heading matches; AmbiguousHeading when it occurs more than once (A2)."""
    hits = _matches(text, heading)
    if not hits:
        raise SectionNotFound(heading)
    if len(hits) > 1:
        raise AmbiguousHeading(f"{heading!r} occurs {len(hits)} times")
    start, level, _ = hits[0]
    lines = text.split("\n")
    end = next((h[0] for h in headings(text) if h[0] > start and h[1] <= level), len(lines))
    return "\n".join(lines[start:end]).rstrip("\n") + "\n"


def section_any(heading: str, file_hint: Optional[str] = None, corpus: Optional[Dict[str, str]] = None,
                plugin_root=None) -> Tuple[str, str]:
    """Find a section across the corpus. AmbiguousHeading when it exists in several files and no hint is given."""
    corpus = corpus if corpus is not None else read_corpus(plugin_root)
    names = sorted(corpus)
    if file_hint:
        names = [n for n in names if n == file_hint or n.endswith("/" + file_hint)]
    files = [n for n in names if _matches(corpus[n], heading)]
    if not files:
        raise SectionNotFound(heading)
    if len(files) > 1:
        raise AmbiguousHeading(f"{heading!r} exists in {files}; pass file_hint")
    return files[0], find_section(corpus[files[0]], heading)


def skill_descriptions(plugin_root=None) -> Dict[str, str]:
    """name -> description for every skills/*/SKILL.md, parsed exactly like gen-craftflow-graph.mjs."""
    skills = Path(plugin_root or PLUGIN_ROOT) / "skills"
    out = {}
    for d in sorted(p for p in skills.iterdir() if p.is_dir()):
        f = d / "SKILL.md"
        if f.is_file():
            fm = parse_frontmatter(f.read_text(encoding="utf-8"))
            desc = fm.get("description", "")
            out[str(fm["name"]) if "name" in fm else d.name] = desc if isinstance(desc, str) else ""
    return out


# --------------------------------------------------------------------------------------- xrefs


class Xref(NamedTuple):
    file: str
    line: int
    target_file: str
    heading: str
    raw: str
    sub: str = ""
    num: str = ""
    loose: bool = False


PATH = r"[\w./~-]+\.md"
ARROW = re.compile(r"(?P<path>" + PATH + r")(?P<close>`?)\s*(?:→|->)\s*")
SEC_Q = re.compile(r"§\s*(?:\"(?P<q>[^\"]+)\"|`(?P<b>[^`]+)`)")
SEC_U = re.compile(r"(?P<path>" + PATH + r")`?\s*§\s*(?=[A-Z])")
SPAN = re.compile(r"`#{2,6}\s*([^`]+)`")
STEP = re.compile(r"\"(#{2,6}\s[^\"]+)\"\s+step\s+\d")
CUE = re.compile(r"\b(in that file|apply its|blocks? there)\b")
ADJ_PATH = re.compile(r"(" + PATH + r")(?:'s|’s)?[`)'’\s]*$")  # a path directly before the section sign
ADJ_ALIAS = re.compile(r"shared doc(?:'s|’s)?[`)\s]*$")
POSS = re.compile(r"(" + PATH + r")`?(?:'s|’s)\s*$")
LIST_MARK = re.compile(r"^([-*+]|\d+[.)])\s+")
ALIAS_TARGET = "skills/_shared/router-protocol.md"


def logical_lines(text: str) -> List[Tuple[str, List[int], List[int]]]:
    """Join paragraphs, list items and table rows into logical lines outside fences (BC-10).

    Returns (joined text, start offsets of each physical piece, their 1-based line numbers)."""
    lines = text.split("\n")
    mask = fence_mask(lines, prose_text_fences=True)
    out: List[list] = []
    cur: Optional[list] = None
    for i, line in enumerate(lines):
        s = line.strip()
        skip = mask[i] or not s or s.startswith("#")
        row = s.startswith("|")
        if cur and (skip or row or LIST_MARK.match(s)):
            out.append(cur)
            cur = None
        if skip:
            continue
        cur = cur or ["", [], []]
        cur[0] += " " if cur[0] else ""
        cur[1].append(len(cur[0]))
        cur[2].append(i + 1)
        cur[0] += s
        if row:  # a table row is always its own logical line
            out.append(cur)
            cur = None
    if cur:
        out.append(cur)
    return [(c[0], c[1], c[2]) for c in out]


def _last_path(text: str, end: int) -> Optional[str]:
    hits = re.findall(PATH, text[:end])
    return hits[-1] if hits else None


def _closing(text: str, start: int, delim: str) -> str:
    return text[start:].split(delim, 1)[0]


def find_xrefs(text: str, file: str) -> List[Xref]:
    found: List[Tuple[int, int, Xref]] = []
    prev = ""
    for logical, offs, lns in logical_lines(text):
        def at(pos: int) -> int:
            return lns[max(bisect.bisect_right(offs, pos) - 1, 0)]

        def add(pos: int, target: str, heading: str, raw: str, **kw) -> None:
            h = norm_text(heading)
            if h:
                found.append((at(pos), pos, Xref(file, at(pos), target or file, h, " ".join(raw.split()), **kw)))

        for m in ARROW.finditer(logical):
            rest = logical[m.end():]
            if re.match(r"[\w.-]+/[\w./-]*", rest):
                continue  # `a.md -> dir/b.md` maps a file to a path, not to a heading
            if m.group("close") == "`":
                if rest[:1] == '"':
                    head, loose = _closing(rest, 1, '"'), False
                elif rest[:1] == "`":
                    head, loose = _closing(rest, 1, "`"), False
                else:
                    head, loose = rest[:100], True
            else:
                head, loose = _closing(rest, 0, "`"), False
            head = re.split(r"→|->", head)[0].strip()
            add(m.start(), m.group("path"), head, logical[m.start():m.end()] + head, loose=loose)
        for m in SEC_Q.finditer(logical):
            head = m.group("q") or m.group("b")
            before = logical[:m.start()]
            pm = ADJ_PATH.search(before)
            target = pm.group(1) if pm else (ALIAS_TARGET if ADJ_ALIAS.search(before) else "")
            add(m.start(), target, head, "§ " + head)
        for m in SEC_U.finditer(logical):
            head = logical[m.end():m.end() + 100]
            add(m.start(), m.group("path"), head, logical[m.start():m.end()] + head, loose=True)
        for m in STEP.finditer(logical):
            if re.search(r"(?:→|->)\s*$", logical[:m.start()]):
                continue  # already parsed as the quoted form of an arrow reference
            add(m.start(), _last_path(logical, m.start()) or "", m.group(1), m.group(0))
        for m in SPAN.finditer(logical):
            body = m.group(1)
            if ".md" in body:
                continue
            parts = [p.strip() for p in re.split(r"→|->", body)]
            nm = re.match(r"^(\d+[a-z]?)\.?\s*(.*)$", parts[0])
            sub = re.sub(r"^#+\s*", "", parts[1]) if len(parts) > 1 else ""
            cue = bool(CUE.search(logical))
            if nm and not (nm.group(2) or sub):
                continue
            if not nm and not cue:
                continue
            poss = POSS.search(logical[:m.start()])
            cued = (_last_path(logical, m.start()) or _last_path(prev, len(prev))) if cue else None
            target = poss.group(1) if poss else cued
            num = nm.group(1) if nm else ""
            head = f"{num}. {nm.group(2)}".strip() if nm and nm.group(2) else (f"{num}." if nm else parts[0])
            add(m.start(), target or "", head, m.group(0), sub=norm_text(sub), num=num)
        prev = logical
    found.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in found]


def _lookup(target: str, corpus: Dict[str, str], loader) -> Optional[str]:
    t = target.replace("\\", "/")
    if t.startswith(REPO_PREFIX):
        t = t[len(REPO_PREFIX):]
    for cand in ("skills/craftflow-router/" + t, "skills/craftflow-router/references/" + t, "skills/_shared/" + t, t,
                 "skills/" + t):
        if cand in corpus:
            return corpus[cand]
        text = loader(cand) if loader else None
        if text is not None:
            return text
    return None


def _head_ok(ref: str, found: str, loose: bool) -> bool:
    if loose:
        return bool(found) and re.match(re.escape(found) + r"(?![\w-])", ref) is not None
    return found == ref or found.startswith(ref + " (")


def resolution(xref: Xref, corpus: Dict[str, str], loader=None) -> str:
    """`ok`, `missing`, or `ambiguous` (the named heading occurs more than once in the target file, H-M2)."""
    text = _lookup(xref.target_file, corpus, loader)
    if text is None:
        return "missing"
    hs = headings(text)
    if not xref.num:
        hits = [norm_text(h[2]) for h in hs if _head_ok(xref.heading, norm_text(h[2]), xref.loose)]
        dup = len(hits) > 1 if not xref.loose else len(set(hits)) < len(hits)  # `Bar (x)` + `Bar (y)` are two candidates
        return "missing" if not hits else ("ambiguous" if dup else "ok")
    want = xref.heading
    cands = [i for i, (_, _, raw) in enumerate(hs)
             if (norm_text(raw).startswith(xref.num + ".") if want == xref.num + "." else _head_ok(want, norm_text(raw), False))]
    if len(cands) > 1:
        return "ambiguous"
    for idx in cands:
        ln, level, _ = hs[idx]
        end = next((x[0] for x in hs[idx + 1:] if x[1] <= level), 10 ** 9)
        if not xref.sub or any(ln < x[0] < end and _head_ok(xref.sub, norm_text(x[2]), False) for x in hs):
            return "ok"
    return "missing"


def resolve(xref: Xref, corpus: Dict[str, str], loader=None) -> bool:
    """True when the reference's heading exists exactly once in the named file (and, for `## N` forms, its sub-heading)."""
    return resolution(xref, corpus, loader) == "ok"


def all_xrefs(plugin_root=None, ref: Optional[str] = None) -> List[Xref]:
    """Cross-references in the runtime corpus plus cursor-router's references into `_shared/`."""
    root = plugin_root or PLUGIN_ROOT
    out: List[Xref] = []
    for rel, text in read_corpus(root, ref).items():
        out.extend(find_xrefs(text, rel))
    out.extend(x for x in find_xrefs(read_text(root, CURSOR, ref), CURSOR) if "_shared/" in x.target_file)
    return out


def _key(file: str, raw: str, target: str) -> Tuple[str, str, str]:
    return (file, " ".join(raw.split()), target)


def _budget(entries: List[dict]) -> Counter:
    for e in entries:
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) and e.get(k) for k in ("file", "raw", "target")):
            raise ValueError(f"xref allowlist entry needs string file, raw and target: {e!r}")
    return Counter(_key(e["file"], e["raw"], e["target"]) for e in entries)


def diff_allowlist(xrefs: List[Xref], entries: List[dict]) -> List[Xref]:
    """Xrefs not covered by the allowlist (multiset match on file + raw text + target, so line drift is tolerated)."""
    budget, out = _budget(entries), []
    for x in xrefs:
        k = _key(x.file, x.raw, x.target_file)
        if budget[k] > 0:
            budget[k] -= 1
        else:
            out.append(x)
    return out


def stale_allowlist(xrefs: List[Xref], entries: List[dict]) -> List[dict]:
    """Allowlist entries that no unresolved xref consumes (a fixed or renamed dangler must leave the list, H-M3)."""
    budget, out = _budget(entries), []
    for x in xrefs:
        k = _key(x.file, x.raw, x.target_file)
        if budget[k] > 0:
            budget[k] -= 1
    for e in entries:
        k = _key(e["file"], e["raw"], e["target"])
        if budget[k] > 0:
            budget[k] -= 1
            out.append(e)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Print the unresolved router cross-references as one JSON line")
    ap.add_argument("--ref", default=None)
    ap.add_argument("--plugin-root", default=None)
    ap.add_argument("--allowlist", default=None, help="xref allowlist JSON; unresolved refs it lists are expected")
    ap.add_argument("--strict", action="store_true", help="exit 1 on an unresolved ref not allowlisted or a stale entry")
    args = ap.parse_args(argv)
    root = args.plugin_root or PLUGIN_ROOT
    try:
        corpus, loader = read_corpus(root, args.ref), plugin_loader(root)
        items = [x for x in all_xrefs(root, args.ref) if not resolve(x, corpus, loader)]
        entries = load_json(args.allowlist)["entries"] if args.allowlist else []
        new, stale = diff_allowlist(items, entries), stale_allowlist(items, entries)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, sort_keys=True), file=sys.stderr)
        return 1
    print(json.dumps({"unresolved": [x._asdict() for x in items], "count": len(items),
                      "not_allowlisted": len(new), "stale_allowlist": stale}, sort_keys=True))
    return 1 if args.strict and (new or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
