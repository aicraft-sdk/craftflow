#!/usr/bin/env python3
"""Signing burden of a projected router tree and the residue notes of the projection (plan P2 REM-FIX 4, doubt-verify findings K1, K2, K6).

`burden(records, ...)` turns per-occurrence records into the rows a relocation phase would have to sign, for REACH-A and REACH-B
(ADR-0073, P2C), with and without the D17-B waiver, and states the formula next to the numbers. Rows are ids and files, not
occurrences: one row names a unit id (it covers every occurrence of that id) and one widening row names a file. Occurrences are
reported next to them so a reader can see both. Python 3.9 compatible, no I/O.

A record is `(id, bucket, cls)`: bucket `vanishing` (the id is gone from the projected tree), `in_place` (the unit stays at its place but
is read later or by fewer workflows) or `moved` (it was moved and is read later); cls is `cov` (every source pair still reads it on the
happy path, a 2 -> 1 drop), `d0` (some pair falls to a conditional file) or `wf` (some workflow or variant no longer reads it), None for
vanishing. The buckets are disjoint: each baseline occurrence is in exactly one of them, or in `moved_without_loss`, which the P0 counters
give as a number (no unit id: such a unit is covered by the section-move row of its section).
"""
from __future__ import annotations

from typing import Dict, Iterable, List

COV, D0, WF = "cov", "d0", "wf"
FORMULA = {
    "rows": "a row names a unit id (all its occurrences) or a file; rows_upper_bound counts distinct ids and files, never occurrences",
    "REACH_A": "ids(reach-lost: in place or moved) + ids(vanishing) + section_move_rows.rows_upper_bound + widening files",
    "REACH_B": "ids(in place, or moved and not pure 2->1) + ids(vanishing) + section_move_rows.rows_upper_bound + widening files; "
               "a section-move row covers the moved pure 2->1 units, nothing covers an in-place narrowing",
    "definitional_D17B": "D17-B as ADR-0073 defines it (a file read on the happy path of every workflow it belongs to counts as reach 2): "
                         "the pure 2->1 occurrences, in place or moved, need no row, so REACH_A == REACH_B = ids(not pure 2->1) + "
                         "ids(vanishing) + section_move_rows.rows_upper_bound + widening files. The un-suffixed REACH_A and REACH_B are the "
                         "narrow reading: the amendment only lets the three D17 rows through and waives nothing",
    "occurrences": "A = reach-lost + vanishing + moved_without_loss + widening pairs; B = section-move ops + per-unit occurrences + "
                   "vanishing + widening pairs (the moved-without-loss occurrences share the section-move rows, so B does not add them)",
    "moved_without_loss": "unmapped_removed - in_place - moved reach-lost - vanishing: occurrences that moved with their section and lost "
                          "nothing; they need the section-move rows only",
}


def move_summary(log: List[dict]) -> dict:
    """The section-move rows of one tree. One `move` op is one row at least; a span holding several top-level sections can need one row each
    (`rows_high_headed_spans` counts that for the spans that start at a heading, `rows_upper_bound` for every span, e.g. the bake-off body with
    its five). A move whose span and destination both lack a heading has no section to name: it needs a destination heading."""
    ops = len(log)
    extra = lambda ms: sum(m["top_sections"] - 1 for m in ms if m["top_sections"] > 1)  # noqa: E731
    return {"ops": ops, "rows_low": ops, "rows_high_headed_spans": ops + extra([m for m in log if m["starts_at_heading"]]),
            "rows_upper_bound": ops + extra(log), "partial_spans_without_heading": sum(1 for m in log if not m["starts_at_heading"]),
            "multi_section_spans": sum(1 for m in log if m["top_sections"] > 1),
            "needs_destination_heading": sorted(m["id"] for m in log if not m["starts_at_heading"] and not m["dst_heading"])}


def _ids(recs: Iterable[tuple]) -> int:
    return len({r[0] for r in recs})


def burden(records: List[tuple], moved_without_loss: int, widening_pairs: int, widening_files: int, moves: dict) -> dict:
    lost = [r for r in records if r[1] != "vanishing"]
    van = [r for r in records if r[1] == "vanishing"]
    non_cov = [r for r in lost if r[2] != COV]
    per_unit_b = [r for r in lost if r[2] != COV or r[1] == "in_place"]  # nothing covers an in-place narrowing without the waiver
    ops, sec = moves["ops"], moves["rows_upper_bound"]
    common = {"vanishing_ids": _ids(van), "section_move_rows": sec, "widening_files": widening_files}

    def rows(units: List[tuple]) -> dict:
        return {"rows_upper_bound": _ids(units) + _ids(van) + sec + widening_files, "rows": {"per_unit_ids": _ids(units), **common}}

    def occ(per_unit: int, extra: str, n: int) -> dict:
        return {"total": per_unit + len(van) + n + widening_pairs,
                "components": {"per_unit": per_unit, "vanishing": len(van), extra: n, "widening_pairs": widening_pairs}}

    a, b = rows(lost), rows(per_unit_b)
    a["occurrences"] = occ(len(lost), "moved_without_loss", moved_without_loss)
    b["occurrences"] = occ(len(per_unit_b), "section_move_ops", ops)
    da, db = rows(non_cov), rows(non_cov)
    da["occurrences"] = occ(len(non_cov), "moved_without_loss", moved_without_loss)
    db["occurrences"] = occ(len(non_cov), "section_move_ops", ops)

    def bucket(bk: str, cls=None) -> dict:
        sel = [r for r in records if r[1] == bk and (cls is None or r[2] == cls)]
        return {"occurrences": len(sel), "ids": _ids(sel)}

    buckets = {"in_place_narrowed": bucket("in_place"), "moved_pure_2_to_1": bucket("moved", COV), "moved_drops_to_0": bucket("moved", D0),
               "moved_drops_workflow_or_variant": bucket("moved", WF), "vanishing": bucket("vanishing"),
               "moved_without_loss": {"occurrences": moved_without_loss}, "widening": {"pairs": widening_pairs, "files": widening_files}}
    return {"formula": FORMULA, "buckets": buckets, "section_move_rows": moves, "REACH_A": a, "REACH_B": b,
            "definitional_D17B": {"REACH_A": da, "REACH_B": db}}


def bias_notes(split: Dict[str, dict], hsc_bytes: int) -> List[str]:
    """Residue the byte projection does not carry (K6): R-M2 and the bound of what returns to the start sets if the rows cannot be signed."""
    to0, wf = split["drops_to_0"]["bytes"], split["drops_workflow_or_variant"]["bytes"]
    return [
        f"R-M2: happy_path_total.PLAN omits references/harness-self-checks.md ({hsc_bytes:,} B). The read map has the file as a BUILD stage "
        "row only, yet SKILL.md reads it at PLAN memory-finalize. Adding PLAN widens the live map, so it needs a signed Read-map widening "
        "row (P11/P13); the projection leaves it out, so PLAN happy-path is understated by that much.",
        f"Signing-risk bound: the reach-lost units that REACH-A or REACH-B cannot sign return to the start sets. Up to {to0:,} B of drops-to-0 "
        f"plus {wf:,} B of workflow or variant drops (guaranteed tree, tier reach_feasibility.tiers.guaranteed) would stay in the kernel or "
        "start files; the byte tiers assume every one of them is signed.",
    ]
