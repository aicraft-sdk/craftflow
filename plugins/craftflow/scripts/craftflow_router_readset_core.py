#!/usr/bin/env python3
"""Pure core of the router read-set measurer: read-map pairs, per-workflow sizes, the kernel's Read Map table.

No file or git access here (craftflow_router_readset.py loads bytes, craftflow_router_readset_project.py applies the cut
plan); everything takes plain dicts so the tests can drive it with synthetic worlds. Python 3.9 compatible.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

MARK_BEGIN = "<!-- readmap:begin -->"
MARK_END = "<!-- readmap:end -->"
DEFAULT_VARIANTS = {"BUILD": ["fast_path", "standard"], "PLAN": ["standard"], "DEBUG": ["standard"], "REVIEW": ["standard"]}
WORKFLOWS = ("BUILD", "PLAN", "DEBUG", "REVIEW")
CLASSES = ("kernel", "start", "stage", "maintainer")
MENTION = re.compile(r"(?:references|_shared)/[A-Za-z0-9_-]+\.md")


class ReadsetError(Exception):
    """Malformed read map, missing file or ambiguous anchor: the CLI exits 2 (fail closed)."""


def variants_of(readmap: dict) -> Dict[str, List[str]]:
    v = readmap.get("variants", DEFAULT_VARIANTS)
    if not isinstance(v, dict) or not v or not all(isinstance(x, list) and x for x in v.values()):
        raise ReadsetError("read map 'variants' must map each workflow to a non-empty list")
    return v


def entry_pairs(readmap: dict, e: dict) -> List[Tuple[str, str]]:
    """(workflow, variant) pairs that read a read-map entry. kernel covers all; maintainer covers nothing."""
    cls = e.get("class")
    if cls not in CLASSES or not e.get("file"):
        raise ReadsetError(f"malformed read map entry: {e!r}")
    if cls == "maintainer":
        return []
    variants = variants_of(readmap)
    wfs = e.get("workflows", [])
    if not isinstance(wfs, list) or (cls != "kernel" and not wfs):
        raise ReadsetError(f"read map entry has no workflows: {e!r}")
    out: List[Tuple[str, str]] = []
    for wf in (list(variants) if cls == "kernel" or "ALL" in wfs else wfs):
        if wf not in variants:
            raise ReadsetError(f"unknown workflow {wf!r} in read map entry {e['file']}")
        v = e.get("variant")
        chosen = variants[wf] if v is None else ([v] if isinstance(v, str) else list(v))
        for var in chosen:
            if var not in variants[wf]:
                raise ReadsetError(f"unknown variant {var!r} for {wf} in read map entry {e['file']}")
            out.append((wf, var))
    return out


def compute_from_sizes(readmap: dict, sizes: Dict[str, int], workflow: str, variant: Optional[str] = None,
                       extra: Optional[Dict[str, int]] = None, budget: Optional[int] = None) -> dict:
    """The BC-style read-set record of one workflow from file sizes. `extra` holds start-set files outside the map
    (config/policy.json counts toward every start set). The binding variant is the largest, or the one asked for."""
    variants = variants_of(readmap)
    if workflow not in variants:
        raise ReadsetError(f"unknown workflow {workflow!r}")
    per: Dict[str, dict] = {}
    for var in variants[workflow]:
        start, stage, seen = [], [], set()
        for e in readmap["files"]:
            if (workflow, var) not in entry_pairs(readmap, e):
                continue
            if e["file"] not in sizes:
                raise ReadsetError(f"read map names {e['file']} but no bytes were measured for it")
            if e["class"] in ("kernel", "start"):
                if e["file"] not in seen:
                    seen.add(e["file"])
                    start.append({"path": e["file"], "bytes": sizes[e["file"]]})
            else:
                stage.append({"path": e["file"], "bytes": sizes[e["file"]], "trigger": e.get("trigger", ""),
                              "happy_path": bool(e.get("happy_path"))})
        for path, n in sorted((extra or {}).items()):
            start.append({"path": path, "bytes": n})
            seen.add(path)
        stage = [s for s in stage if s["path"] not in seen]
        total = sum(f["bytes"] for f in start)
        per[var] = {"start_files": start, "stage_files": stage, "start_total": total,
                    "happy_path_total": total + sum(s["bytes"] for s in stage if s["happy_path"])}
    if variant is not None and variant not in per:
        raise ReadsetError(f"unknown variant {variant!r} for {workflow}")
    pick = variant or max(sorted(per, key=lambda v: (v != "standard", v)), key=lambda v: per[v]["start_total"])
    chosen = per[pick]
    return {"workflow": workflow, "variant": pick, "start_files": chosen["start_files"], "start_total": chosen["start_total"],
            "budget": budget, "within_budget": None if budget is None else chosen["start_total"] <= budget,
            "stage_files": chosen["stage_files"], "happy_path_total": chosen["happy_path_total"],
            "variants": {v: {"start_total": d["start_total"], "happy_path_total": d["happy_path_total"]} for v, d in per.items()}}


# ---------------------------------------------------------------------------------------------- Read Map table

TABLE_HEAD = ("Class", "Read when", "File", "Workflows", "Variant", "Happy path")
INTRO = ("Generated from `config/router-readmap.json` by `scripts/craftflow_router_readset.py --print-table`; edit the map, "
         "not this table. It lists when each runtime file is read today. The sections below carry the rules.")


def short_path(file: str) -> str:
    for prefix in ("skills/craftflow-router/", "skills/"):
        if file.startswith(prefix):
            return file[len(prefix):]
    return file


def _cell(s: str) -> str:
    return " ".join(str(s).replace("|", "/").split())


def table_row(e: dict) -> str:
    wfs = e.get("workflows") or (["ALL"] if e.get("class") == "kernel" else [])
    v = e.get("variant")
    hp = e.get("happy_path")
    cells = (e.get("class", ""), e.get("trigger", ""), "`" + short_path(e["file"]) + "`", ", ".join(wfs) or "-",
             "-" if v is None else (v if isinstance(v, str) else ", ".join(v)), "-" if hp is None else ("yes" if hp else "no"))
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def render_table(readmap: dict) -> str:
    lines = ["| " + " | ".join(TABLE_HEAD) + " |", "|" + "|".join("---" for _ in TABLE_HEAD) + "|"]
    lines += [table_row(e) for e in readmap["files"] if e.get("class") != "maintainer"]  # maintainer files are never read at run time
    return "\n".join(lines) + "\n"


SECTION_HEADING = "## Reference Read Map\n\n"


def table_block(readmap: dict) -> str:
    """The marker-wrapped generated block exactly as it sits in the kernel (ends with a newline). The heading stays
    outside the markers so both markers sit under the new heading, not under the title's rule paragraph."""
    return f"{MARK_BEGIN}\n{INTRO}\n\n{render_table(readmap)}{MARK_END}\n"


def section_block(readmap: dict) -> str:
    return SECTION_HEADING + table_block(readmap)


def table_span(text: str) -> Optional[Tuple[int, int]]:
    """(start, end) offsets of the marker-wrapped block including the end marker's newline, or None."""
    a = text.find(MARK_BEGIN + "\n")
    b = text.find(MARK_END + "\n", a + 1) if a >= 0 else -1
    if a < 0 or b < 0:
        return None
    return a, b + len(MARK_END) + 1


def extract_table(text: str) -> Optional[str]:
    span = table_span(text)
    return text[span[0]:span[1]] if span else None


def rerender_table(text: str, readmap: dict) -> str:
    span = table_span(text)
    return text if span is None else text[:span[0]] + table_block(readmap) + text[span[1]:]


def unmapped_kernel_mentions(kernel_text: str, readmap: dict) -> List[str]:
    """Short paths of `references/x.md` / `_shared/x.md` mentions in the kernel that no read-map entry names (E65)."""
    mapped = {e["file"] for e in readmap["files"]}
    out = set()
    for m in MENTION.findall(kernel_text):
        full = ("skills/craftflow-router/" if m.startswith("references/") else "skills/") + m
        if full not in mapped:
            out.add(m)
    return sorted(out)


# ---------------------------------------------------------------------------------------------- descriptions


def _frontmatter_lines(text: str) -> List[str]:
    m = re.match(r"^---\n([\s\S]*?)\n---\n", text)
    return m.group(1).split("\n") if m else []


def effective_description(text: str) -> str:
    """The description a YAML reader (and the harness) sees: `|` keeps line breaks, `>`/`>-` fold them, quotes drop.
    gen-craftflow-graph.mjs reads `>-` as the literal text `>-`, so it undercounts those skills (E66)."""
    lines = _frontmatter_lines(text)
    for i, line in enumerate(lines):
        m = re.match(r"^description:\s*(.*)$", line)
        if not m:
            continue
        rest = m.group(1).strip()
        block: List[str] = []
        j = i + 1
        while j < len(lines) and (lines[j].startswith(" ") or lines[j].strip() == ""):
            block.append(lines[j])
            j += 1
        indent = min((len(b) - len(b.lstrip()) for b in block if b.strip()), default=0)
        body = [b[indent:] if b.strip() else "" for b in block]
        if re.fullmatch(r"[|>][+-]?", rest):
            if rest[0] == "|":
                return "\n".join(body).strip()
            paras, cur = [], []
            for b in body:
                if b == "":
                    if cur:
                        paras.append(" ".join(cur))
                        cur = []
                else:
                    cur.append(b)
            if cur:
                paras.append(" ".join(cur))
            return "\n".join(paras).strip()
        parts = [rest] + [b.strip() for b in body if b.strip()]
        return re.sub(r'^"(.*)"$', r"\1", " ".join(p for p in parts if p))
    return ""


def description_edit(text: str, new: str) -> str:
    """Replace the frontmatter `description` field (scalar or block) of a SKILL.md text by one line."""
    m = re.match(r"^(---\n)([\s\S]*?)(\n---\n)([\s\S]*)$", text)
    if not m:
        raise ReadsetError("SKILL.md has no frontmatter to edit")
    lines = m.group(2).split("\n")
    for i, line in enumerate(lines):
        if re.match(r"^description:", line):
            j = i + 1
            while j < len(lines) and (lines[j].startswith(" ") or lines[j].strip() == ""):
                j += 1
            lines[i:j] = ["description: " + new]
            return m.group(1) + "\n".join(lines) + m.group(3) + m.group(4)
    raise ReadsetError("SKILL.md frontmatter has no description")
