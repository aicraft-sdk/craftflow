#!/usr/bin/env python3
"""Policy-map table parsing and row checks for craftflow_rule_inventory.py (split out to keep that file under its cap).

Pure helpers: header constants, fence-aware table parsing, symbol lookup (AST for Python), narrowed-coverage tokens
and quoted-scope-word matching. Python 3.9 compatible.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import craftflow_router_corpus as cor

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
SEPARATOR = re.compile(r"^\|?[\s:|-]+\|?$")
EXCERPT_LEN = 100  # inventory entries keep this many normalized characters of each unit


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_tables(doc: str) -> Tuple[Dict[str, List[List[str]]], List[str]]:
    """Rows of each policy-map table; fail closed on a missing section, a differing header or a missing separator row.

    Lines inside fenced blocks are examples, not rows."""
    rows: Dict[str, List[List[str]]] = {k: [] for k in HEADERS}
    errors: List[str] = []
    lines = doc.split("\n")
    mask = cor.fence_mask(lines)
    heads = cor.headings(doc)
    for key, title in SECTIONS.items():
        idx = next((n for n, h in enumerate(heads) if cor.norm_text(h[2]).lstrip("§ ").strip() == title), None)
        if idx is None:
            errors.append(f"policy map: missing section '{title}'")
            continue
        end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
        table = [lines[n] for n in range(heads[idx][0] + 1, end) if lines[n].strip().startswith("|") and not mask[n]]
        want = [" ".join(c.split()).lower() for c in _cells(HEADERS[key])]
        if not table or [" ".join(c.split()).lower() for c in _cells(table[0])] != want:
            errors.append(f"policy map: section '{title}' header differs from the required header")
            continue
        if len(table) < 2 or not SEPARATOR.match(table[1].strip()) or "-" not in table[1]:
            errors.append(f"policy map: section '{title}' has no separator row after the header")
            continue
        for ln in table[2:]:
            cells = _cells(ln)
            if len(cells) != len(want):
                errors.append(f"policy map: '{title}' row has {len(cells)} cells, expected {len(want)}")
            else:
                rows[key].append(cells)
    return rows, errors


def _defines(path: Path, name: str) -> bool:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return False
        return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name
                   for n in ast.walk(tree))
    pat = r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function\*?|class|def|const|let|var)\s+" + re.escape(name) + r"\b"
    return re.search(pat, text) is not None


def symbol_ok(root, cell: str, prefixes: Tuple[str, ...]) -> bool:
    """`path::name` names a file under one of the prefixes that really defines `name` (a comment does not count)."""
    path, _, name = cell.partition("::")
    for pre in prefixes:
        f = Path(root) / pre / path.strip()
        if f.is_file():
            return not name.strip() or _defines(f, name.strip())
    return False


def pairs_of(cell: str, errors: List[str]) -> Set[Tuple[str, str]]:
    out: Set[Tuple[str, str]] = set()
    for tok in re.split(r"[,;]", cell):
        wf, _, var = tok.strip().partition(":")
        if wf not in VARIANTS or (var and var not in VARIANTS[wf]):
            errors.append(f"bad narrowed coverage token '{tok.strip()}'")
        else:
            out.update((wf, v) for v in ([var] if var else VARIANTS[wf]))
    return out


def _squash(text: str) -> str:
    return " ".join(re.sub(r"[`\"“”]|\*\*|__", "", text).split()).lower()


def quote_in_excerpt(quote_cell: str, excerpt: str) -> bool:
    """The quoted scope words appear in the rule's inventory excerpt (its first EXCERPT_LEN normalized characters)."""
    q, ex = _squash(quote_cell), _squash(excerpt)
    if not q:
        return False
    return q in ex or (len(excerpt) >= EXCERPT_LEN and q.startswith(ex))
