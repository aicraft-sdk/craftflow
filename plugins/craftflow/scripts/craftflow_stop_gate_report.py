#!/usr/bin/env python3
"""Stop-gate calibration report and offline replay (SPEC-0018 / ADR-0055, RD-3). Read-only, fail soft.

  craftflow_stop_gate_report.py [--events FILE] [--transcripts-root DIR] [--scope {all,jev}]
      --scope jev counts schema-2 rows only (the GO basis for arming) and adds the ``act`` section. Labels each shadow row by the user's next genuine reply in the same transcript, then prints ONE JSON
      object: rows, labeled, by_verdict, rule_hits, jev, heuristic, sweep, latency_ms, usage_total,
      go_criteria. ACT stays NO-GO unless the RD-3 criteria are met.
  craftflow_stop_gate_report.py --replay --transcripts-root DIR
      Offline replay over transcripts only: prints counts, never any message text.

The events file is only ever read. Message text is read to compute labels and is never printed.
Python 3.9 stdlib only.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import craftflow_stop_gate as gate  # noqa: E402
import craftflow_stop_gate_core as core  # noqa: E402

MAX_EVENTS_BYTES = 64 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024
STATE_SEGMENTS = (".craftflow", "state", "stop-gate", "events.jsonl")
NO_JEV_CALL = frozenset(("not_needed", "not_consented", "inactive", None))
VERDICTS = ("would_continue", "would_commit", "needs_human")


def iter_records(data):
    """Parsed JSON object per line of transcript/events bytes; malformed lines are skipped."""
    return gate._records(data)


def read_capped(path, cap):
    """(bytes, truncated) of a REGULAR file, else (b'', False). Never raises.

    A file larger than ``cap`` is read from its TAIL (the newest lines), the cut first line is dropped and
    ``truncated`` is True."""
    if not gate.is_regular_file(path):
        return b"", False
    try:
        with open(path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size <= cap:
                return handle.read(cap), False
            handle.seek(size - cap - 1)  # one byte earlier: tells whether the cut fell on a line boundary
            data = handle.read(cap + 1)
    except OSError:
        return b"", False
    if data[:1] == b"\n":
        return data[1:], True
    return (data.split(b"\n", 1)[1] if b"\n" in data else b""), True


def read_bytes(path, cap):
    """Bytes of a REGULAR file (the tail when larger than ``cap``), else b''. Never raises."""
    return read_capped(path, cap)[0]


def human_text(rec):
    """Text of a genuine human line, else None."""
    if not gate.is_genuine_human(rec):
        return None
    content = rec["message"]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(b["text"] for b in content if b.get("type") == "text" and isinstance(b.get("text"), str))


def join_next_user_reply(records, after_ts, before_epoch=None):
    """Text of the first genuine human line strictly after ``after_ts`` (ISO string), else None.

    With ``before_epoch`` (the next stop row of the same session) a reply at or after it belongs to that
    later stop, so it is not returned here."""
    after = gate.parse_ts(after_ts)
    if after is None:
        return None
    for rec in records:
        epoch = gate.parse_ts(rec.get("timestamp"))
        if epoch is None or epoch <= after:
            continue
        text = human_text(rec)
        if text is not None:
            return text if before_epoch is None or epoch < before_epoch else None
    return None


def metric_rows(rows):
    """Rows that count toward metrics: dict rows of row_kind stop (relay follow-ups and errors are excluded)."""
    return [r for r in rows if isinstance(r, dict) and r.get("row_kind") == "stop"]


def load_events_ex(path):
    """(rows, truncated): the newest MAX_EVENTS_BYTES of the events file."""
    data, truncated = read_capped(path, MAX_EVENTS_BYTES)
    return list(iter_records(data)), truncated


def load_events(path):
    return load_events_ex(path)[0]


def _find_transcript(row, root):
    names = []
    tpath, sid = row.get("transcript_path"), row.get("session_id")
    if isinstance(tpath, str) and tpath:
        names.append(os.path.basename(tpath))
    if isinstance(sid, str) and sid:
        names.append(sid + ".jsonl")
    candidates = [os.path.join(root, n) for n in names] if root else []
    if not root and isinstance(tpath, str) and tpath:
        candidates.append(tpath)
    for candidate in candidates:
        if gate.is_regular_file(candidate):
            return candidate
    return None


def _session_key(row):
    sid, tpath = row.get("session_id"), row.get("transcript_path")
    if isinstance(sid, str) and sid:
        return ("sid", sid)
    if isinstance(tpath, str) and tpath:
        return ("tp", tpath)
    return None


def _next_row_epochs(rows):
    """{row index: epoch of the next stop row of the same session}: a reply belongs to the LATEST stop row
    that precedes it, so it must come before the next row's timestamp."""
    groups = {}
    for index, row in enumerate(rows):
        key, epoch = _session_key(row), gate.parse_ts(row.get("ts"))
        if key is not None and epoch is not None:
            groups.setdefault(key, []).append((epoch, index))
    bounds = {}
    for items in groups.values():
        items.sort()
        for (_epoch, index), (later, _other) in zip(items, items[1:]):
            bounds[index] = later
    return bounds


def label_rows(rows, root, flags=None):
    """Copies of the rows with a ``label`` from the user's next reply; unlabeled when it cannot be joined.

    Each reply labels only the latest preceding stop row of its session. ``flags`` (a set) collects
    ``"transcript"`` when a transcript was read from its tail because of the size cap."""
    cache = {}
    bounds = _next_row_epochs(rows)
    out = []
    for index, row in enumerate(rows):
        labeled = dict(row)
        labeled["label"] = "unlabeled"
        path = _find_transcript(row, root)
        if path is not None:
            if path not in cache:
                data, cut = read_capped(path, MAX_TRANSCRIPT_BYTES)
                cache[path] = list(iter_records(data))
                if cut and flags is not None:
                    flags.add("transcript")
            # an auto-continued (acted) or follow-up stop is answered by the human line after the whole chain,
            # so its reply is not bounded by the next row of the session
            chained = row.get("acted") is True or row.get("stop_hook_active") is True
            labeled["label"] = core.label_reply(
                join_next_user_reply(cache[path], row.get("ts"), None if chained else bounds.get(index)))
        out.append(labeled)
    return out


def _percentile(values, pct):
    numbers = sorted(v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not numbers:
        return None
    return numbers[max(0, min(len(numbers) - 1, int(math.ceil(pct / 100.0 * len(numbers))) - 1))]


def _latency(values):
    numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return {"n": len(numbers), "p50": _percentile(numbers, 50), "p90": _percentile(numbers, 90),
            "max": max(numbers) if numbers else None}


def _count(values):
    counts = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _usage_total(rows):
    total = {}
    for row in rows:
        usage = row.get("jev_usage")
        if isinstance(usage, dict):
            for key, value in usage.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    total[key] = total.get(key, 0) + value
    return total


def _jev_summary(rows):
    calls = [r for r in rows if r.get("jev_status") not in NO_JEV_CALL]
    failures = [r for r in calls if r.get("jev_status") != "ok"]
    return {"calls": len(calls), "failures": len(failures),
            "failure_rate": round(len(failures) / len(calls), 4) if calls else None,
            "status_counts": _count(r.get("jev_status") for r in rows)}


SCOPES = ("all", "jev")
GO_ROW_SCHEMA = 2


def _act_summary(stops):
    """{acted, chains, chain_negatives}: auto-continued rows, chain starts, and acted rows the user answered
    negatively."""
    acted = [r for r in stops if r.get("acted") is True]
    return {"acted": len(acted),
            "chains": sum(1 for r in acted if not r.get("stop_hook_active")),
            "chain_negatives": sum(1 for r in acted if r["label"] == "negative")}


def build_report(rows, root, events_truncated=False, scope="all"):
    """Calibration report. ``scope`` ``jev`` counts schema-2 rows only (A11): older rows predate the act
    fields and the Jev-gated verdict, so they cannot back a GO."""
    if scope not in SCOPES:
        raise ValueError("scope")
    flags = set()
    selected = metric_rows(rows)
    if scope == "jev":
        selected = [r for r in selected if type(r.get("schema")) is int and r.get("schema") == GO_ROW_SCHEMA]
    stops = label_rows(selected, root, flags)
    labeled = [r for r in stops if r["label"] != "unlabeled"]
    # a follow-up stop (stop_hook_active) is a consequence of an earlier stop, not an independent verdict
    wc = [r for r in labeled if r.get("verdict") == "would_continue" and not r.get("stop_hook_active")]
    stamped = sum(1 for r in wc if r["label"] == "rubber_stamp")
    jev = _jev_summary(stops)
    hook_latency = _latency(r.get("hook_ms") for r in stops)
    stats = {
        "would_continue_labeled": len(wc),
        "sessions": len({r.get("session_id") for r in wc if r.get("session_id")}),
        "precision": round(stamped / len(wc), 4) if wc else None,
        "negatives": sum(1 for r in wc if r["label"] == "negative"),
        "jev_failure_rate": jev["failure_rate"],
        "jev_calls": jev["calls"],
        "hook_p90_ms": hook_latency["p90"],
        "label_coverage": round(len(labeled) / len(stops), 4) if stops else None,
    }
    swept = core.sweep(stops)
    return {
        "scope": scope,
        "act": _act_summary(stops),
        "rows": len(stops),
        "labeled": len(labeled),
        "unlabeled": len(stops) - len(labeled),
        "label_coverage": stats["label_coverage"],
        "truncated": bool(events_truncated or flags),
        "by_verdict": _count(r.get("verdict") for r in stops),
        "rule_hits": _count(code for r in stops for code in (r.get("rule_hits") or [])),
        "jev": jev,
        "heuristic": dict(swept["heuristic"], label_counts=_count(r["label"] for r in stops)),
        "sweep": swept["jev"],
        "latency_ms": {"hook": hook_latency, "jev": _latency(r.get("jev_latency_ms") for r in stops)},
        "usage_total": _usage_total(stops),
        "go_criteria": dict(core.go_criteria(stats), stats=stats),
    }


def replay(root):
    """Counts over every user turn that follows assistant text in the transcripts. Never returns text."""
    out = {"turns": 0, "rubber_stamp": 0, "question_end": 0, "question_end_rubber_stamp": 0,
           "heuristic_confusion": {v: {"rubber_stamp": 0, "other": 0} for v in VERDICTS}, "truncated": False}
    paths = sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)) if root else []
    for path in paths:
        last = None
        data, cut = read_capped(path, MAX_TRANSCRIPT_BYTES)
        out["truncated"] = out["truncated"] or cut
        for rec in iter_records(data):
            if rec.get("type") == "assistant":
                text = gate._assistant_text(rec)
                if text.strip():
                    last = text
                continue
            reply = human_text(rec)
            if reply is None or last is None:
                continue
            stamp = core.label_reply(reply) == "rubber_stamp"
            kind, _signals = core.heuristic_kind(last)
            verdict = core._verdict_for(kind, [])
            out["turns"] += 1
            out["rubber_stamp"] += stamp
            if last.rstrip().endswith("?"):
                out["question_end"] += 1
                out["question_end_rubber_stamp"] += stamp
            out["heuristic_confusion"][verdict]["rubber_stamp" if stamp else "other"] += 1
            last = None
    return out


def default_events_path():
    base = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(base, *STATE_SEGMENTS)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Stop-gate calibration report (read-only).")
    parser.add_argument("--events", default=None)
    parser.add_argument("--transcripts-root", default=None)
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--scope", choices=SCOPES, default="all")
    args = parser.parse_args(argv)
    try:
        if args.replay:
            result = replay(args.transcripts_root)
        else:
            rows, cut = load_events_ex(args.events or default_events_path())
            result = build_report(rows, args.transcripts_root, cut, args.scope)
    except Exception as exc:  # noqa: BLE001 - fail soft: report the error class only, never content
        result = {"error": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
