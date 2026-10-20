#!/usr/bin/env python3
"""Unit extraction, normalization and the normative classifier for craftflow_rule_inventory.py (split out for its cap).

A unit is the smallest span a reviewer would call one rule: a list item with its continuation lines, a table header or
body row, a paragraph, a fenced block, an [EASY TO MISS ...] bracket or a skill's frontmatter description. A unit's id
hashes its normalized text; its place is the heading path plus, for list items, the label paragraph that introduces
the list (`label_ctx`) and the ids of the list's enclosing list items and preceding paragraph (`parent_ctx`, R1).
Python 3.9 compatible.
"""
from __future__ import annotations

import hashlib
import re
from typing import FrozenSet, List, Optional, Set, Tuple

import craftflow_router_corpus as cor
from craftflow_rule_inventory_frontmatter import keys_unit_text, parse_strict

# `documentation only, no code` scopes a note ("[NOTE - documentation only, no code implied]"); it is not the obligation word `only`
# (P11 rebaseline of the Future Third Router note, edf4681b29aa: the one baseline unit that carries the phrase). Only that exact scope
# phrase is exempt: a bare `documentation only` or `the documentation only when ...` still reads as the obligation word.
KEYWORDS = re.compile(r"(?i)\b(never|must|do not|always|(?:(?<!documentation )|(?<=documentation )(?!only, no code\b))only|required|requires|shall|fail closed)\b|MUST|MANDATORY|\[EASY TO MISS")
VERBS3 = re.compile(r"(?i)\b(skips?|stops?|blocks?|denies|fails?|advances?|halts?|aborts?|should|anything (?:other than|else)"
                    r"|authoritative)\b|\+=|→|->")
IMPERATIVES = frozenset(
    "read run set record append persist create skip stop treat use ask write dispatch mark include track apply "
    "before after if when do call parse release re-read delete keep verify check load resolve extract "
    "increment update count otherwise prefer".split())
LIST_MARK = re.compile(r"^([-*+]|\d+[.)])\s+")
BULLET_MARK = re.compile(r"^[-*+]\s+")
CODE_SPAN = re.compile(r"(`[^`\n]*`)")
FENCE_OPEN = re.compile(r"^(`{3,}|~{3,})")
ROW_SEP = " // "  # joins a table's header text to each body row so a header edit changes every row id


def normalize(text: str) -> str:
    """Whitespace-collapsed, lower-cased text. Unordered markers and `**`/`__` are dropped; an ordinal marker (`3.`)
    is part of the rule and stays; the text of a backticked code span keeps its case (A11)."""
    t = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    t = BULLET_MARK.sub("", t, count=1).replace("**", "").replace("__", "")
    parts = CODE_SPAN.split(" ".join(t.split()))
    return "".join(p if k % 2 else p.lower() for k, p in enumerate(parts))


def normalize_fence(text: str) -> str:
    """A fenced block keeps its line structure: only trailing whitespace per line and surrounding blank lines go."""
    return "\n".join(x.rstrip() for x in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip("\n")


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


def _frontmatter(lines: List[str], close: int, file: str) -> Tuple[str, str]:
    """(description, whole-frontmatter text) of a `---` block. A block outside the strict subset, or one without a
    non-empty string description, raises instead of being skipped (H-M2, F1-F6)."""
    where = file or "text"
    try:
        fields = parse_strict(lines[1:close])
    except ValueError as exc:
        raise ValueError(f"frontmatter of {where} is not valid: {exc}")
    desc = fields.get("description")
    if not isinstance(desc, str) or not desc.strip():
        raise ValueError(f"frontmatter of {where} does not parse to a non-empty string description")
    return desc, keys_unit_text(fields)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def extract_units(text: str, file: str = "") -> List[dict]:
    """Units in file order: {kind, raw, norm, id, line, numbered, heading_ctx, heading_path, label_ctx, parent_ctx}.

    Every unit kind carries its branch context (P0 REM-FIX 7): `label_ctx` is the nearest preceding label (a unit ending
    in a colon) at a lower indent, and `parent_ctx` is the ids of the enclosing list items (a list item also gets the
    preceding paragraph). A fence or paragraph additionally records `@<id>` of its nearest preceding sibling of another
    kind. Only a dedent pops a parent: a fence, table or continuation paragraph nested under a list item keeps its chain.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    i, front, keys = 0, None, ""
    if lines and lines[0].strip() == "---":
        close = next((k for k in range(1, len(lines)) if lines[k].strip() == "---"), None)
        if close is not None:
            i = close + 1
            front, keys = _frontmatter(lines, close, file)
    mask, unclosed = cor.fence_scan(lines)
    if unclosed:  # an unclosed fence would silently swallow the rest of the file as one block
        raise ValueError(f"unclosed fence opened at line {unclosed} of {file or 'text'}")
    units: List[dict] = []
    stack: List[Tuple[int, str]] = []
    labels: List[Tuple[int, str]] = []  # (indent, norm) of the open label units; a paragraph label sits one below its indent
    parents: List[Tuple[int, str]] = []  # (indent, id) of the open list items, whatever their trailing punctuation (R1)
    para: List[str] = []  # [id] of the top-level paragraph that directly precedes the current list, else []
    hist: List[Tuple[str, str, Tuple[str, ...]]] = []  # (kind, id, sibling chain) of the units since the last heading
    cur: Optional[dict] = None
    table: Optional[str] = None  # normalized header row of the table being read
    table_ctx: Tuple[str, Tuple[str, ...]] = ("", ())  # (label, parent chain) of that table, shared by its rows

    def anchor(kind: str, sib: Tuple[str, ...]) -> List[str]:
        found = next((uid for k, uid, s in reversed(hist) if s == sib and k != kind), "")
        return ["@" + found] if found else []

    def emit(kind: str, raw_lines: List[str], line: int, numbered: bool = False, label: str = "",
             norm: Optional[str] = None, parent: Optional[List[str]] = None, sib: Tuple[str, ...] = ()) -> str:
        raw = "\n".join(x.rstrip() for x in raw_lines).strip("\n")
        norm = norm if norm is not None else (normalize_fence(raw) if kind == "fence" else normalize(raw))
        path = [h for _, h in stack]
        if kind in ("fence", "paragraph"):
            parent = (parent or []) + anchor(kind, sib)
        units.append({"kind": kind, "raw": raw, "norm": norm, "id": make_id(norm), "line": line, "numbered": numbered,
                      "heading_ctx": path[-1] if path else "", "heading_path": path, "label_ctx": label,
                      "parent_ctx": parent or []})
        if kind != "easy_to_miss":
            hist.append((kind, make_id(norm), sib))
        if kind in ("list_item", "paragraph"):
            for span in _bracket_spans(raw):
                if normalize(span) != norm:
                    emit("easy_to_miss", [span], line)
        return norm

    def flush() -> None:
        nonlocal cur
        if cur:
            norm = emit(cur["kind"], cur["lines"], cur["line"], cur["numbered"], cur["label"], parent=cur["parent"],
                        sib=cur["sib"])
            if norm.endswith(":"):
                low = cur["indent"] - (1 if cur["kind"] == "paragraph" else 0)
                while labels and labels[-1][0] >= low:
                    labels.pop()
                labels.append((low, norm))
            if cur["kind"] == "list_item":
                parents.append((cur["indent"], make_id(norm)))
            elif cur["indent"] == 0:
                para[:] = [make_id(norm)]
        cur = None

    def context(indent: int) -> Tuple[str, Tuple[str, ...]]:
        """(label, enclosing list-item ids) for a unit at `indent`: a dedent pops, a nested unit inherits."""
        while labels and labels[-1][0] >= indent:
            labels.pop()
        while parents and parents[-1][0] >= indent:
            parents.pop()
        return (labels[-1][1] if labels else ""), tuple(pid for _, pid in parents)

    def begin(kind: str, line: str, no: int, numbered: bool) -> dict:
        indent = _indent(line)
        label, sib = context(indent)
        return {"kind": kind, "lines": [line], "line": no, "numbered": numbered, "indent": indent, "label": label,
                "sib": sib, "parent": (para + list(sib)) if kind == "list_item" else list(sib)}

    def reset() -> None:
        labels[:] = []
        parents[:] = []
        para[:] = []

    def leave_top_level() -> None:
        """After a fence or table outside any list item the old label and intro paragraph no longer introduce what follows."""
        if not parents:
            labels[:] = []
            para[:] = []

    if isinstance(front, str) and front.strip():  # the description is loaded into context on every turn (A6)
        emit("frontmatter", [front], 1)
    if keys:  # the whole block: a changed name or an added disable-model-invocation unloads every kernel rule (F2, F6)
        emit("frontmatter_keys", [keys], 1, norm=keys)
    while i < len(lines):
        line, s = lines[i], lines[i].strip()
        if mask[i]:
            flush()
            table = None
            label, sib = context(_indent(line))
            opener = FENCE_OPEN.match(s).group(1)
            j = i + 1
            while j < len(lines) and not (lines[j].strip().startswith(opener[0] * len(opener))
                                           and not lines[j].strip().strip(opener[0])):
                j += 1
            emit("fence", lines[i:j + 1], i + 1, label=label, parent=list(sib), sib=sib)
            leave_top_level()
            i = j + 1
            continue
        if "<!--" in line and "-->" not in line.split("<!--", 1)[1]:  # a comment spanning lines hides its units (A-3)
            end = next((k for k in range(i + 1, len(lines)) if "-->" in lines[k]), None)
            if end is None:
                raise ValueError(f"unclosed HTML comment opened at line {i + 1} of {file or 'text'}")
            flush()
            table = None
            i = end + 1
            continue
        h = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if h:
            flush()
            reset()
            table = None
            hist.clear()
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
            table = None
            if s:
                reset()
        elif s.startswith("|"):
            flush()
            if table is None:
                table_ctx = context(_indent(line))
                table = normalize(line)
                emit("table_header", [line], i + 1, label=table_ctx[0], parent=list(table_ctx[1]), sib=table_ctx[1])
                leave_top_level()
            elif not re.match(r"^\|[\s:|-]+\|?$", s):
                emit("table_row", [line], i + 1, norm=table + ROW_SEP + normalize(line), label=table_ctx[0],
                     parent=list(table_ctx[1]), sib=table_ctx[1])
        elif LIST_MARK.match(s):
            flush()
            table = None
            cur = begin("list_item", line, i + 1, s[0].isdigit())
        elif cur:
            cur["lines"].append(line)
        else:
            table = None
            cur = begin("paragraph", line, i + 1, False)
        i += 1
    flush()
    return units


def classify_normative(unit: dict, allowlist: FrozenSet[str] = frozenset(), keywords: bool = True) -> bool:
    """Keyword classifier OR membership in the hand-maintained allowlist. `keywords=False` disables the classifier."""
    if unit["norm"] in allowlist:
        return True
    if not keywords:
        return False
    if unit["kind"] in ("table_row", "table_header", "fence", "easy_to_miss", "frontmatter", "frontmatter_keys") or unit["numbered"]:
        return True
    first = unit["norm"].split(" ")[0].strip(":,.;`*_(").lower() if unit["norm"] else ""
    return bool(KEYWORDS.search(unit["norm"]) or VERBS3.search(unit["norm"]) or first in IMPERATIVES)


def load_allowlist(path) -> Set[str]:
    data = cor.load_json(path)
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("entries"), list):
        raise ValueError(f"malformed normative allowlist: {path}")
    texts = set()
    for e in data["entries"]:
        if not isinstance(e, dict) or not isinstance(e.get("text"), str):
            raise ValueError(f"allowlist entry needs a string 'text': {e!r}")
        t = e["text"]
        if not (normalize(t) == t or (FENCE_OPEN.match(t) and normalize_fence(t) == t)):
            raise ValueError(f"allowlist text is not in normal form (normalize() would change it): {t!r}")
        texts.add(t)
    return texts
