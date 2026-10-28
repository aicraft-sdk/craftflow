#!/usr/bin/env python3
"""Signed behavior change rows for craftflow_rule_inventory.py (AD-7, AD-10; split out for its cap).

A signed behavior change row accounts for one baseline unit whose rewrite changes a guarded word, number or token, which
an ordinary `## Rewrites` row refuses. It lives in the optional policy-map section `## Signed behavior changes`
(never in `SECTIONS`, so a map without it parses exactly as before). The row carries the old and new text verbatim,
names an existing ADR and is signed only by a name in `SBC_SIGNERS`. An accepted row feeds the same `rewrites` map the
Rewrites rows feed, so the reach rule still applies. A `Supersedes` cell replaces one signed Rewrites row of the same
baseline id whose replacement target a later edit turned into something else (AD-10): INV skips that row and lists it.
Python 3.9 compatible; pure helpers, no I/O except the default ADR lookup.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import craftflow_router_corpus as cor
from craftflow_rule_inventory_rows import REVIEWER, SEPARATOR

SBC_HEADER = "| Baseline ID | New ID | Supersedes | Old text | New text | ADR | Signed by | Evidence |"
SBC_TITLE = "signed behavior changes"
SBC_SIGNERS = ("David Gracia",)
HEX = re.compile(r"\b[0-9a-f]{12}\b")
ADR_CELL = re.compile(r"^ADR-(\d{4})$")
SUPERSEDES = re.compile(r"^Rewrites row ([0-9a-f]{12})$")
UNSUPERSEDABLE = ("heading", "section-move", "group")
DEAD_ADR = ("rejected", "superseded")
PENDING = "PENDING (David)"

Accepted = Dict[str, Tuple[str, List[str]]]


def escape_cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|")


def split_cells(line: str) -> List[str]:
    """Cells of one table line, split only on a `|` that is not escaped; `\\|` unescapes to `|` and `\\\\` to `\\`."""
    s, cells, cur, i = line.strip(), [], [], 0
    s = s[1:] if s.startswith("|") else s
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in "\\|":
            cur.append(s[i + 1])
            i += 2
            continue
        if s[i] == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(s[i])
        i += 1
    return cells + (["".join(cur).strip()] if "".join(cur).strip() else [])


def _squash(text: str) -> str:
    return " ".join(text.split())


def parse_sbc_rows(doc: str, errors: Optional[List[str]] = None) -> List[List[str]]:
    """Rows (8 cells; Evidence optional) of `## Signed behavior changes`; a missing section means zero rows.

    Lines inside fenced blocks are examples, not rows. A differing header or missing separator is an error."""
    errors = [] if errors is None else errors
    lines = doc.split("\n")
    mask, unclosed = cor.fence_scan(lines)
    heads = cor.headings(doc)
    idx = next((n for n, h in enumerate(heads) if cor.norm_text(h[2]).lstrip("§ ").strip() == SBC_TITLE), None)
    if unclosed or idx is None:  # an open fence is already reported by parse_tables
        return []
    end = heads[idx + 1][0] if idx + 1 < len(heads) else len(lines)
    table = [lines[n] for n in range(heads[idx][0] + 1, end) if lines[n].strip().startswith("|") and not mask[n]]
    want = [_squash(c).lower() for c in split_cells(SBC_HEADER)]
    if not table or [_squash(c).lower() for c in split_cells(table[0])] != want:
        errors.append("policy map: section 'signed behavior changes' header differs from the required header")
        return []
    if len(table) < 2 or not SEPARATOR.match(table[1].strip()) or "-" not in table[1]:
        errors.append("policy map: section 'signed behavior changes' has no separator row after the header")
        return []
    rows: List[List[str]] = []
    for ln in table[2:]:
        cells = split_cells(ln)
        cells = cells + [""] if len(cells) == len(want) - 1 else cells
        if len(cells) != len(want):
            errors.append(f"policy map: 'signed behavior changes' row has {len(cells)} cells, expected {len(want)}")
        else:
            rows.append(cells)
    return rows


def superseded_targets(rows: List[List[str]], rewrite_rows: List[List[str]], errors: List[str]) -> Set[str]:
    """Baseline ids whose signed Rewrites row a behavior change row replaces (AD-10). An unresolved cell is an error
    and the Rewrites row is processed as usual."""
    out: Set[str] = set()
    for c in rows:
        cell = c[2].strip()
        if cell in ("", "—", "-"):
            continue
        own, m = HEX.findall(c[0]), SUPERSEDES.match(cell)
        if not m:
            errors.append(f"signed behavior change row: Supersedes cell must read 'Rewrites row <its own baseline id>': {cell}")
            continue
        rid = m.group(1)
        found = [r for r in rewrite_rows if HEX.findall(r[0]) == [rid]]
        bad: List[str] = []
        if len(found) != 1:
            bad.append(f"supersedes no single Rewrites row: {rid}")
        elif found[0][2].strip().lower() in UNSUPERSEDABLE:
            bad.append(f"supersedes a {found[0][2].strip().lower()} Rewrites row: {rid}")
        elif not found[0][4] or not REVIEWER.match(found[0][5]):
            bad.append(f"supersedes an unsigned Rewrites row: {rid}")
        if own != [rid]:
            bad.append(f"Supersedes cell must read 'Rewrites row {own[0] if len(own) == 1 else '<its own baseline id>'}': {cell}")
        errors.extend(f"signed behavior change row {c[0]}: {b}" for b in bad)
        if not bad:
            out.add(rid)
    return out


def adr_status(adr: str, root=None) -> Optional[str]:
    """Lower-case `status:` of docs/ai/decisions/<nnnn>-*.md in the repo that holds the running script (never
    --plugin-root: a scratch root has no docs/); None when no such ADR exists."""
    m = ADR_CELL.match(adr.strip())
    if not m:
        return None
    folder = Path(root) if root else cor.PLUGIN_ROOT.parents[3] / "docs" / "ai" / "decisions"
    for f in sorted(folder.glob(m.group(1) + "-*.md")):
        for line in f.read_text(encoding="utf-8").splitlines()[:30]:
            if line.startswith("status:"):
                return line.split(":", 1)[1].strip().strip("\"'").lower()
        return ""
    return None


def check_sbc_rows(rows: List[List[str]], base: Dict[str, dict], after_by_id: Dict[str, dict], body: Callable[[dict], str],
                   rewrite_rows: List[List[str]], policy_ids: Set[str], superseded: Set[str],
                   adr_lookup: Callable[[str], Optional[str]]) -> Tuple[Accepted, int, List[str]]:
    """(accepted {old id: ("behavior-change", [new id])}, rows without a signer, row errors). Acceptance rules (a)-(d)."""
    accepted: Accepted = {}
    errors: List[str] = []
    unsigned = 0
    rewrite_ids = {i for r in rewrite_rows for i in HEX.findall(r[0])}
    for c in rows:
        ids, news, adr = HEX.findall(c[0]), HEX.findall(c[1]), c[5].strip()
        if not (REVIEWER.match(c[6]) and c[6].rsplit(" (", 1)[0] in SBC_SIGNERS):
            unsigned += 1
        bad: List[str] = []
        if len(ids) != 1:
            bad.append("needs exactly one baseline id")
        elif ids[0] not in base:
            bad.append(f"baseline id {ids[0]} is not in the frozen inventory")
        if len(news) != 1:
            bad.append("needs exactly one new id")
        elif news[0] not in after_by_id:
            bad.append(f"new id {news[0]} is not in the live corpus")
        if not bad:
            old = ids[0]
            if _squash(c[3]) != _squash(body(base[old])):
                bad.append(f"old text differs from the baseline unit {old}")
            if _squash(c[4]) != _squash(body(after_by_id[news[0]])):
                bad.append(f"new text differs from the new unit {news[0]}")
            if old in policy_ids:
                bad.append(f"baseline id {old} is named by a Policy row")
            if old in rewrite_ids and old not in superseded:
                bad.append(f"baseline id {old} is also named by a Rewrites row (name it in the Supersedes cell)")
            if old in accepted:
                bad.append(f"more than one signed behavior change row for baseline id {old}")
        if not ADR_CELL.match(adr):
            bad.append(f"ADR cell must read ADR-NNNN (got '{adr}')")
        elif adr_lookup(adr) is None:
            bad.append(f"{adr} does not exist under docs/ai/decisions")
        elif adr_lookup(adr) in DEAD_ADR:
            bad.append(f"{adr} is {adr_lookup(adr)}")
        errors.extend(f"signed behavior change row {c[0]}: {b}" for b in bad)
        if not bad:
            accepted[ids[0]] = ("behavior-change", [news[0]])
    return accepted, unsigned, errors


def merge_accepted(accepted: Accepted, rewrites: dict, ctx_renames: Dict[str, str], label_renames: Dict[str, str],
                   base: Dict[str, dict], after_by_id: Dict[str, dict], text: Callable[[dict], str], errors: List[str]) -> None:
    """Feed accepted rows to the Rewrites maps; a 1:1 row keeps its dependents in place, as a Rewrites row does."""
    for old, (kind, news) in accepted.items():
        if old in rewrites:
            errors.append(f"duplicate rewrite row for id {old}")
            continue
        rewrites[old] = (kind, news)
        ctx_renames[news[0]] = old
        if text(base[old]).endswith(":"):
            label_renames[text(after_by_id[news[0]])] = text(base[old])


def render_sbc_row(base_entry: dict, new_entry: dict, body: Callable[[dict], str], adr: str, supersedes: str = "") -> str:
    """One correctly escaped row with `PENDING (David)` in Signed by. Agents never hand-type the text cells."""
    cells = [base_entry["id"], new_entry["id"], f"Rewrites row {base_entry['id']}" if supersedes else "—",
             _squash(body(base_entry)), _squash(body(new_entry)), adr, PENDING, ""]
    return "| " + " | ".join(escape_cell(c) for c in cells) + " |"


def generate_row(base_entries: List[dict], live_entries: List[dict], old_id: str, new_id: str, adr: str, supersedes: bool,
                 body: Callable[[dict], str]) -> str:
    old = next((e for e in base_entries if e["id"] == old_id), None)
    new = next((e for e in live_entries if e["id"] == new_id), None)
    if old is None or new is None:
        raise ValueError(f"--old-id {old_id} must be in the baseline and --new-id {new_id} in the live corpus")
    return render_sbc_row(old, new, body, adr, "yes" if supersedes else "")
