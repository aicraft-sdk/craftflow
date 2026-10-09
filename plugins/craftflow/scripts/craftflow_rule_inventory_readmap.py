#!/usr/bin/env python3
"""Read-map loading and validation for craftflow_rule_inventory.py (split out to keep that file under its cap).

The read map says which workflow x variant pairs read each runtime router file; every reachability check in the
inventory depends on it, so it fails closed on anything missing or unknown. Python 3.9 compatible.
"""
from __future__ import annotations

from typing import Dict, Set, Tuple

import craftflow_router_corpus as cor
from craftflow_rule_inventory_rows import VARIANTS

CLASSES = ("kernel", "start", "stage", "maintainer")
ENTRY_KEYS = frozenset({"file", "class", "workflows", "variant", "trigger", "happy_path"})


# how early a rule is read: kernel and start files at workflow start, a happy-path stage file when its trigger fires
# on every run, a conditional stage file only when its trigger fires (remediation, research). Higher reads earlier.
RANK = {"kernel": 2, "start": 2, "happy_path_stage": 1, "conditional_stage": 0}


def entry_reach(e: dict) -> str:
    if e["class"] in ("kernel", "start"):
        return e["class"]
    if not isinstance(e.get("happy_path"), bool):  # a missing flag must not silently read as a happy-path stage
        raise ValueError(f"read map stage entry needs a boolean happy_path: {e}")
    return "happy_path_stage" if e["happy_path"] else "conditional_stage"


def coverage(readmap: dict) -> Dict[str, Set[Tuple[str, str, str]]]:
    """file -> set of (workflow, variant, reach) triples that read it. kernel covers all; maintainer covers nothing.

    A file listed twice keeps the earliest reach per pair. Fails closed on a read map with no entries, an unknown
    workflow or an unknown variant (H-H2): a silent `[]` applicability would make every reachability check vacuous."""
    variants = readmap.get("variants", VARIANTS)
    if not isinstance(variants, dict) or not all(isinstance(v, list) and v for v in variants.values()):
        raise ValueError("read map 'variants' must map each workflow to a non-empty list")
    if not isinstance(readmap.get("files"), list) or not readmap["files"]:
        raise ValueError("read map has no 'files' entries")
    out: Dict[str, Dict[Tuple[str, str], str]] = {}
    seen: Set[Tuple[str, str]] = set()
    for e in readmap["files"]:
        if not isinstance(e, dict):
            raise ValueError(f"malformed read map entry: {e!r}")
        if set(e) - ENTRY_KEYS:  # a misspelled key would silently widen coverage
            raise ValueError(f"unknown key(s) {sorted(set(e) - ENTRY_KEYS)} in read map entry {e.get('file')!r}")
        cls, wfs = e.get("class"), e.get("workflows", [])
        if cls not in CLASSES or not e.get("file") or not isinstance(wfs, list):
            raise ValueError(f"malformed read map entry: {e}")
        if (e["file"], cls) in seen:
            raise ValueError(f"duplicate read map entry for {e['file']} (class {cls})")
        seen.add((e["file"], cls))
        pairs = out.setdefault(e["file"], {})
        if cls == "maintainer":
            continue
        reach = entry_reach(e)
        if cls != "kernel" and not wfs:
            raise ValueError(f"read map entry has no workflows: {e}")
        v = e.get("variant")
        if v is not None and not (isinstance(v, str) or (isinstance(v, list) and v and all(isinstance(x, str) for x in v))):
            raise ValueError(f"read map 'variant' must be a string or a non-empty list of strings: {e}")
        for wf in (list(variants) if cls == "kernel" or "ALL" in wfs else wfs):
            if wf not in variants:
                raise ValueError(f"unknown workflow {wf!r} in read map")
            chosen = variants[wf] if v is None else ([v] if isinstance(v, str) else v)
            bad = [x for x in chosen if x not in variants[wf]]
            if bad:
                raise ValueError(f"unknown variant {bad[0]!r} for {wf} in read map entry {e['file']}")
            for var in chosen:
                if (wf, var) not in pairs or RANK[reach] > RANK[pairs[(wf, var)]]:
                    pairs[(wf, var)] = reach
    return {f: {(w, v, r) for (w, v), r in d.items()} for f, d in out.items()}


def load_readmap(path) -> dict:
    data = cor.load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"read map must be a JSON object: {path}")
    coverage(data)
    return data


def check_readmap_files(readmap: dict, corpus_files) -> None:
    """Every non-maintainer read-map entry must name a file that is in the corpus (a typo would drop its coverage)."""
    have = set(corpus_files)
    missing = sorted({e["file"] for e in readmap["files"] if e["class"] != "maintainer"} - have)
    if missing:
        raise ValueError(f"read map names files that are not in the corpus: {missing}")
