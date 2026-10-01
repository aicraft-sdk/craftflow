#!/usr/bin/env python3
"""Throwaway progress probe for LA-5 (SPEC-0020 / ADR-0057). Never part of hooks.json.

Usage: advance_cursor.py <project-dir>

Marks the workflow artifact's current cursor phase completed, moves the cursor to the next phase (so the stop
gate sees progress and H16 does not fire) and prints the exact completion line the model must reply with.
Reads and writes only <project-dir>/.craftflow/state/workflows/*.json. Exit 1 on any problem, no traceback.
"""
import glob
import json
import os
import sys


def main(argv):
    if len(argv) != 2:
        return 1
    try:
        paths = sorted(glob.glob(os.path.join(argv[1], ".craftflow", "state", "workflows", "wf-*.json")))
        if len(paths) != 1:
            return 1
        with open(paths[0], "r", encoding="utf-8") as handle:
            art = json.load(handle)
        ids = [p["phase_id"] for p in art["normalized_phases"]]
        cur = art["phase_cursor"]
        idx = ids.index(cur)
        art["phase_status"][cur] = "completed"
        nxt = ids[idx + 1] if idx + 1 < len(ids) else None
        if nxt is not None:
            art["phase_cursor"] = nxt
        tmp = paths[0] + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(art, handle)
        os.replace(tmp, paths[0])
        wf = os.path.basename(paths[0])[:-len(".json")]
        tail = "Shall I continue to Phase %s?" % nxt if nxt else "All phases are done."
        print("Phase %s of craftflow workflow %s is done and checks pass. %s" % (cur, wf, tail))
        return 0
    except (OSError, ValueError, KeyError, TypeError):
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
