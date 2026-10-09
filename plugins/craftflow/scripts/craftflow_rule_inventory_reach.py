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
import subprocess
from collections import Counter
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Set, Tuple

import craftflow_router_corpus as cor
from craftflow_rule_inventory_readmap import RANK
from craftflow_rule_inventory_rows import REVIEWER, tokens_of

Triple = Tuple[str, str, str]
OVERLAP = 0.6  # a rewritten rule keeps at least this share of its tokens (D6)
NEGATIONS = frozenset({"never", "not", "no", "only", "must", "always", "cannot", "unless", "without", "nor", "neither"})
# modal and obligation words and sequencing words: a swap such as required -> optional or before -> after is a different
# rule, so they are counted like negations (two-way). Numerals, comparison operators and `fail closed`/`fail open` too.
GUARDED_WORDS = frozenset({"must", "shall", "required", "mandatory", "optional", "may", "should", "always", "only", "before",
                           "after", "until", "unless", "skip", "run", "allow", "deny", "stop", "advance"})
OPERATOR = re.compile(r">=|<=|==|!=|(?<![-=])>|<")
FAIL_MODE = re.compile(r"\bfail (closed|open)\b")
BIGRAM_KINDS = ("dedupe", "split", "literal-to-key")
BIGRAM_KEEP = 0.5  # a dedupe, split or literal-to-key keeps at least this share of the old text's adjacent word pairs


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
    for f, said, wide, why, who, *_evidence in rows:
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


def token_list(text: str) -> List[str]:
    """Lower-cased word tokens; an apostrophe form (`don't`) stays one token so `n't` negations are visible."""
    return re.findall(r"\w+(?:'\w+)?", text.lower().replace("’", "'"))


def tokens(text: str) -> Set[str]:
    return set(token_list(text))


def _guard(token: str) -> str:
    """The guarded base form of a token (`skips` -> `skip`), a numeral, a negation or '' when the token is not guarded."""
    if token in NEGATIONS or token in GUARDED_WORDS or token.isdigit() or token.endswith("n't"):
        return token
    for cut in (1, 2):
        if token.endswith("s" if cut == 1 else "es") and token[:-cut] in GUARDED_WORDS:
            return token[:-cut]
    return ""


def negs(text: str) -> Counter:
    """Guarded tokens of a text with their counts: negations, modal/obligation/sequencing words, numerals, comparison
    operators and `fail closed`/`fail open`. A token inside a code span counts like any other token."""
    low = text.lower().replace("\u2019", "'")
    found = Counter(g for g in map(_guard, token_list(low)) if g)
    found.update(OPERATOR.findall(low))
    found.update("fail " + m for m in FAIL_MODE.findall(low))
    return found


def bigrams(text: str) -> Set[Tuple[str, str]]:
    words = token_list(text)
    return set(zip(words, words[1:]))


def bigrams_kept(old_text: str, new_texts: List[str]) -> bool:
    """At least half of the old text's adjacent word pairs survive in the new units (a subject/object swap keeps none)."""
    old = bigrams(old_text)
    if not old:
        return True
    have = set().union(*(bigrams(t) for t in new_texts)) if new_texts else set()
    return len(old & have) / len(old) >= BIGRAM_KEEP


def heading_related(old: str, new: str) -> bool:
    """A heading row keeps >= 60% of the old heading's words and its guarded tokens match both ways."""
    old_t = tokens(old)
    return (not old_t or len(old_t & tokens(new)) / len(old_t) >= OVERLAP) and negation_kept(negs(old), [negs(new)])


def negation_kept(old: Counter, news: List[Counter]) -> bool:
    """Two-way: the new side has exactly the old negation tokens (a gained or lost one is a different rule), the new
    units together keep every occurrence and no single new unit carries more of a token than the old text did."""
    if set(old) != set().union(*news):
        return False
    return all(sum(n[t] for n in news) >= c and all(n[t] <= c for n in news) for t, c in old.items())


def _names(term: str, text: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text) is not None


def _section_holds(old_text: str, section: str) -> bool:
    """The named section carries the rule: >= 60% of its tokens (and every negation token) or one unit `related` accepts."""
    old, have = tokens(old_text), tokens(section)
    if not old or (set(negs(old_text)) <= set(negs(section)) and len(old & have) / len(old) >= OVERLAP):
        return True
    lines = (re.sub(r"^[\s>*+|#-]+", "", ln) for ln in section.split("\n"))
    return any(related(old_text, [ln], "dedupe", []) for ln in lines if ln.strip())


def pointer_names(old_text: str, new_texts: List[str], headings: List[str], dests: Dict[str, Dict[str, str]]) -> bool:
    """A pointer unit names the old unit's heading, a corpus file AND a heading of that file whose section holds the rule."""
    for t in new_texts:
        low = t.lower()
        if any(_names(h, low) for h in headings if h):
            if any(_names(f, low) and _names(h, low) and _section_holds(old_text, body)
                   for f, hs in dests.items() for h, body in hs.items() if h):
                return True
    return False


def related(old_text: str, new_texts: List[str], kind: str, headings: List[str], dests=None) -> bool:
    """A rewrite row relates its ids when the new text keeps >= 60% of the old tokens, every new unit is >= 60% old
    tokens and the guarded tokens (negations, modal/obligation words, numerals, comparison operators; see negs()) match
    both ways: one lost or gained is no relation (H-M1, REM-FIX 5/6). A dedupe, split or literal-to-key also keeps >= 50%
    of the old text's adjacent word pairs (bigrams_kept). Callers pass table rows as their body
    only (H-H1). A `pointer` row may instead have a new unit that names the old unit's heading, the file the rule now
    lives in and a heading of that file whose section holds the rule; `dests` maps file name -> heading -> section text."""
    if kind == "pointer" and dests and pointer_names(old_text, new_texts, headings, dests):
        return True
    old, news = tokens(old_text), [tokens(t) for t in new_texts]
    union = set().union(*news) if news else set()
    if news and not negation_kept(negs(old_text), [negs(t) for t in new_texts]):
        return False
    keeps = not old or len(old & union) / len(old) >= OVERLAP
    if kind in BIGRAM_KINDS and not bigrams_kept(old_text, new_texts):
        return False
    return keeps and all(len(n & old) / len(n) >= OVERLAP for n in news if n)


def group_relations(old: Dict[str, str], new: Dict[str, str]) -> Tuple[Dict[str, List[str]], List[str], List[str]]:
    """Many-to-many relation of a `group` row: (old id -> related new ids, ids that relate to nothing, weak ids).

    A pair is an edge when the shared tokens are >= 60% of either unit (a merge, a split or a reword). Edges are only
    candidates: each old id must have >= 60% of its tokens in the union of its related new units, each new unit must be
    >= 60% drawn from the union of its related old units, and negation tokens must match both ways (REM-FIX 5)."""
    ot, nt = {k: tokens(v) for k, v in old.items()}, {k: tokens(v) for k, v in new.items()}

    def edge(a: Set[str], b: Set[str]) -> bool:
        if not a or not b:
            return not a and not b
        shared = len(a & b)
        return shared / len(b) >= OVERLAP or shared / len(a) >= OVERLAP

    rel = {o: [n for n in nt if edge(ot[o], nt[n])] for o in ot}
    back = {n: [o for o in ot if n in rel[o]] for n in nt}
    lone = [o for o in ot if not rel[o]] + [n for n in nt if not back[n]]
    weak = []
    for o, ns in rel.items():
        got = set().union(*(nt[n] for n in ns)) if ns else set()
        if ns and ((ot[o] and len(ot[o] & got) / len(ot[o]) < OVERLAP) or not negation_kept(negs(old[o]), [negs(new[n]) for n in ns])
                   or not bigrams_kept(old[o], [new[n] for n in ns])):
            weak.append(o)
    for n, os_ in back.items():
        got = set().union(*(ot[o] for o in os_)) if os_ else set()
        have = sum((negs(old[o]) for o in os_), Counter())
        if os_ and ((nt[n] and len(nt[n] & got) / len(nt[n]) < OVERLAP) or any(c > have[t] for t, c in negs(new[n]).items())):
            weak.append(n)
    return rel, lone, weak


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
    try:  # the pinned commit must exist in this clone: a shallow checkout cannot prove the baseline describes it (H-M3)
        git = subprocess.run(["git", "-C", str(cor.PLUGIN_ROOT), "cat-file", "-e", lock["baseline_sha"] + "^{commit}"],
                             capture_output=True, text=True)
    except OSError as exc:
        raise ValueError(f"baseline lock: cannot run git to check the pinned commit: {exc}")
    if git.returncode != 0:
        raise ValueError(f"baseline lock: commit {lock['baseline_sha']} is not in this clone (fetch-depth 0 required; "
                         f"git cat-file -e exit {git.returncode})")
    for key, path in (("baseline_sha256", baseline_path), ("readmap_baseline_sha256", readmap_baseline_path)):
        if sha256_of(path) != lock[key]:
            raise ValueError(f"baseline lock mismatch: {path} does not match the lock's {key}; "
                             "rebaseline deliberately (router-policy-map.md, Rebaseline) or restore the file")
    base = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    if base.get("ref") != lock["baseline_sha"]:
        raise ValueError(f"baseline lock mismatch: baseline ref {base.get('ref')!r} is not the pinned {lock['baseline_sha']}")
    return base
