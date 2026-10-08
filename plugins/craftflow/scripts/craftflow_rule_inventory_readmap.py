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


def coverage(readmap: dict) -> Dict[str, Set[Tuple[str, str]]]:
    """file -> set of (workflow, variant) pairs that read it. kernel covers all; maintainer covers nothing.

    Fails closed on a read map with no entries, an unknown workflow or an unknown variant (H-H2): a silent `[]`
    applicability would make every reachability check vacuous."""
    variants = readmap.get("variants", VARIANTS)
    if not isinstance(readmap.get("files"), list) or not readmap["files"]:
        raise ValueError("read map has no 'files' entries")
    out: Dict[str, Set[Tuple[str, str]]] = {}
    for e in readmap["files"]:
        cls, wfs = e.get("class"), e.get("workflows", [])
        if cls not in CLASSES or not e.get("file") or not isinstance(wfs, list):
            raise ValueError(f"malformed read map entry: {e}")
        pairs = out.setdefault(e["file"], set())
        if cls == "maintainer":
            continue
        if cls != "kernel" and not wfs:
            raise ValueError(f"read map entry has no workflows: {e}")
        for wf in (list(variants) if cls == "kernel" or "ALL" in wfs else wfs):
            if wf not in variants:
                raise ValueError(f"unknown workflow {wf!r} in read map")
            v = e.get("variant")
            chosen = [v] if isinstance(v, str) else (v or variants[wf])
            bad = [x for x in chosen if x not in variants[wf]]
            if bad:
                raise ValueError(f"unknown variant {bad[0]!r} for {wf} in read map entry {e['file']}")
            pairs.update((wf, var) for var in chosen)
    return out


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
