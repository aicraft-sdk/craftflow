#!/usr/bin/env python3
"""Reach, read-map pinning, baseline lock and rewrite-relation checks for craftflow_rule_inventory.py (split for its cap).

The zero-rule-loss net trusts three things the loss counter cannot see for itself: WHEN a file is read (reach), WHICH
read map was frozen at the baseline (pinning) and WHICH baseline the PR gate compares against (lock). Python 3.9
compatible; stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Set, Tuple

import craftflow_router_corpus as cor
from craftflow_rule_inventory_readmap import RANK
from craftflow_rule_inventory_rows import REVIEWER, tokens_of

Triple = Tuple[str, str, str]
OVERLAP = 0.6  # a rewritten rule keeps at least this share of its tokens (D6)


def covers(supply: Iterable[Triple], req: Iterable[Triple]) -> bool:
    """Every required (workflow, variant, reach) is met by a supplied pair that is read at least as early."""
    have: Dict[Tuple[str, str], int] = {}
    for w, v, r in supply:
        have[(w, v)] = max(have.get((w, v), -1), RANK[r])
    return all(have.get((w, v), -1) >= RANK[r] for w, v, r in req)


def narrowed_cover(base: FrozenSet[Triple], tokens: Dict[Tuple[str, str], object]) -> FrozenSet[Triple]:
    """The baseline coverage restricted to the row's pairs, each lowered to the row's reach when it names one."""
    return frozenset((w, v, tokens[(w, v)] or r) for w, v, r in base if (w, v) in tokens)


def widening(live: Dict[str, Set[Triple]], base: Dict[str, Set[Triple]], rows: List[List[str]],
             errors: List[str]) -> Tuple[int, int]:
    """(unsigned widenings, rows missing a reviewer): live read-map coverage beyond the frozen baseline map (D2).

    Row: | file | baseline coverage | widened coverage | justification | Reviewed by |. A row allows the named pairs
    (up to the named reach, `@kernel` when unstated) of its file only, and must state the file's real baseline pairs."""
    allowed: Dict[str, Dict[Tuple[str, str], int]] = {}
    unsigned = 0
    for f, said, wide, why, who in rows:
        real = {(w, v) for w, v, _ in base.get(f, set())}
        stated = set() if said.strip().lower() == "none" else set(tokens_of(said, errors))
        if stated != real:
            errors.append(f"read-map widening row's baseline coverage '{said}' differs from the baseline map for {f}")
            continue
        if not why.strip():
            errors.append(f"read-map widening row has no justification: {f}")
            continue
        unsigned += 0 if REVIEWER.match(who) else 1
        for pair, reach in tokens_of(wide, errors).items():
            allowed.setdefault(f, {})[pair] = RANK[reach or "kernel"]
    count = 0
    for f, triples in sorted(live.items()):
        for w, v, r in sorted(triples):
            if covers(base.get(f, set()), [(w, v, r)]) or allowed.get(f, {}).get((w, v), -1) >= RANK[r]:
                continue
            count += 1
            errors.append(f"read map widens {f} to {w}:{v} ({r}) beyond the frozen baseline map without a signed row")
    return count, unsigned


def tokens(text: str) -> Set[str]:
    return set(re.findall(r"\w+", text.lower()))


def related(old_text: str, new_texts: List[str], kind: str, headings: List[str]) -> bool:
    """A rewrite row relates its ids when the new text keeps >= 60% of the old tokens and every new unit is >= 60% old
    tokens; a `pointer` row may instead name the old unit's heading in a new unit (the rule now lives behind it)."""
    if kind == "pointer" and any(re.search(r"(?<!\w)" + re.escape(h) + r"(?!\w)", t.lower())
                                 for t in new_texts for h in headings if h):
        return True
    old, news = tokens(old_text), [tokens(t) for t in new_texts]
    union = set().union(*news) if news else set()
    keeps = not old or len(old & union) / len(old) >= OVERLAP
    return keeps and all(len(n & old) / len(n) >= OVERLAP for n in news if n)


def near_rule_count(base_entries: List[dict], after_entries: List[dict], renames: Dict[str, str]) -> int:
    """New normative occurrences under a heading path that holds a baseline normative rule (A5, advisory)."""
    held = {tuple(o["heading_path"]) for e in base_entries if e["normative"] for o in e["occurrences"]}
    known = {e["id"] for e in base_entries}
    return sum(1 for e in after_entries if e["normative"] and e["id"] not in known for o in e["occurrences"]
               if tuple(renames.get(h, h) for h in o["heading_path"]) in held)


def sha256_of(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_lock(baseline_path, readmap_baseline_path, lock_path) -> dict:
    """The committed baseline and its read map must be byte-identical to what the lock pins (D3); returns the baseline.

    A baseline that was re-emitted from an edited tree (any ref) cannot match a lock written at the deliberate
    rebaseline, so a PR cannot pass by diffing a corpus against a baseline made from itself."""
    try:
        lock = cor.load_json(lock_path)
        keys = ("baseline_sha", "baseline_sha256", "readmap_baseline_sha256")
        if not isinstance(lock, dict) or not all(isinstance(lock.get(k), str) for k in keys) or set(lock) != set(keys):
            raise ValueError(f"malformed baseline lock (expected exactly {list(keys)})")
    except (OSError, ValueError) as exc:
        raise ValueError(f"baseline lock unusable: {exc}")
    if not re.fullmatch(r"[0-9a-f]{40}", lock["baseline_sha"]):
        raise ValueError("baseline lock: baseline_sha must be a full 40-hex commit id")
    for key, path in (("baseline_sha256", baseline_path), ("readmap_baseline_sha256", readmap_baseline_path)):
        if sha256_of(path) != lock[key]:
            raise ValueError(f"baseline lock mismatch: {path} does not match the lock's {key}; "
                             "rebaseline deliberately (router-policy-map.md, Rebaseline) or restore the file")
    base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    if base.get("ref") != lock["baseline_sha"]:
        raise ValueError(f"baseline lock mismatch: baseline ref {base.get('ref')!r} is not the pinned {lock['baseline_sha']}")
    return base
