#!/usr/bin/env python3
"""
craftflow_learn_scan.py

Deterministic failure-pattern miner. Reads all workflow event logs under
.craftflow/state/workflows/*.events.jsonl and clusters failure signals
into recurring patterns.

Usage:
    python3 scripts/craftflow_learn_scan.py [--state-dir PATH]

Output JSON on stdout:
    [
      {
        "signature": "<normalized failure reason string>",
        "occurrences": 3,
        "example_reasons": ["reason text 1", "reason text 2"],
        "first_seen": "2026-06-01T10:00:00Z",
        "last_seen": "2026-07-01T11:00:00Z",
        "event_types": ["remediation_created", "workflow_failed"]
      }
    ]

Exit 0 on success. Exit 1 on usage error.
"""
import argparse
import json
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_reason(reason: str) -> str:
    """
    Normalize a failure reason string into a clustering signature.
    - Lowercase
    - Collapse internal whitespace to single space
    - Strip leading/trailing punctuation and whitespace
    """
    text = reason.lower()
    text = re.sub(r"\s+", " ", text).strip()
    # Strip leading/trailing punctuation characters
    text = text.strip("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
    text = text.strip()
    return text


# ---------------------------------------------------------------------------
# Failure signal detection
# ---------------------------------------------------------------------------

_FAILURE_EVENT_TYPES = frozenset({"remediation_created", "workflow_failed"})
_LOOP_DECISIONS = frozenset({"re_verify", "re_review", "re_hunt"})


def is_failure_event(event: dict) -> bool:
    """
    Return True if the event is a failure signal.

    Failure signals:
    - event type in {remediation_created, workflow_failed}
    - any event whose 'decision' field contains re_verify, re_review, or re_hunt
    """
    event_type = event.get("event", "")
    if event_type in _FAILURE_EVENT_TYPES:
        return True
    decision = event.get("decision", "")
    if decision in _LOOP_DECISIONS:
        return True
    return False


# ---------------------------------------------------------------------------
# Scanning and clustering
# ---------------------------------------------------------------------------

def _parse_events_file(path: Path) -> list:
    """Parse an .events.jsonl file. Skip malformed lines silently."""
    events = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        events.append(obj)
                except (json.JSONDecodeError, ValueError):
                    continue
    except OSError:
        pass
    return events


def scan_state_dir(state_dir: Path) -> list:
    """
    Scan all .events.jsonl files under state_dir/workflows/ and return
    a list of failure clusters sorted by occurrences descending.

    Each cluster:
    {
        "signature": str,
        "occurrences": int,
        "example_reasons": list[str],   # up to 3 unique
        "first_seen": str,              # ISO timestamp
        "last_seen": str,               # ISO timestamp
        "event_types": list[str],       # unique event types
    }
    """
    workflows_dir = state_dir / "workflows"
    if not workflows_dir.exists():
        return []

    event_files = sorted(workflows_dir.glob("*.events.jsonl"))
    if not event_files:
        return []

    # clusters: signature -> accumulator dict
    clusters: dict = {}

    for path in event_files:
        events = _parse_events_file(path)
        for event in events:
            if not is_failure_event(event):
                continue

            reason = event.get("reason", "")
            signature = normalize_reason(reason) if reason else ""
            timestamp = event.get("timestamp", "")
            event_type = event.get("event", "")

            if signature not in clusters:
                clusters[signature] = {
                    "signature": signature,
                    "occurrences": 0,
                    "example_reasons": [],
                    "_example_seen": set(),
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "event_types": [],
                    "_event_types_seen": set(),
                }

            c = clusters[signature]
            c["occurrences"] += 1

            # Collect up to 3 unique example_reasons (original text)
            if reason not in c["_example_seen"] and len(c["example_reasons"]) < 3:
                c["example_reasons"].append(reason)
                c["_example_seen"].add(reason)

            # Track first/last seen by timestamp (lexicographic ISO comparison)
            if timestamp:
                if not c["first_seen"] or timestamp < c["first_seen"]:
                    c["first_seen"] = timestamp
                if not c["last_seen"] or timestamp > c["last_seen"]:
                    c["last_seen"] = timestamp

            # Collect unique event_types
            if event_type and event_type not in c["_event_types_seen"]:
                c["event_types"].append(event_type)
                c["_event_types_seen"].add(event_type)

    # Clean up internal tracking sets and sort by occurrences descending
    result = []
    for c in clusters.values():
        result.append({
            "signature": c["signature"],
            "occurrences": c["occurrences"],
            "example_reasons": c["example_reasons"],
            "first_seen": c["first_seen"],
            "last_seen": c["last_seen"],
            "event_types": c["event_types"],
        })

    result.sort(key=lambda x: x["occurrences"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mine failure patterns from craftflow workflow event logs."
    )
    parser.add_argument(
        "--state-dir",
        default=".craftflow/state",
        help="Path to the craftflow state directory (default: .craftflow/state)",
    )
    args = parser.parse_args()

    state_dir = Path(args.state_dir)
    clusters = scan_state_dir(state_dir)
    print(json.dumps(clusters, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
