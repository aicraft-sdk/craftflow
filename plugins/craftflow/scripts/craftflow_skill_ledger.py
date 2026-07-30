#!/usr/bin/env python3
"""
craftflow_skill_ledger.py

Deterministic ledger for candidate skill-worthy signatures mined from
craftflow workflow event logs and artifacts. Phase 1 of the
post-implementation project-skill distillation feature
(docs/plans/2026-07-29-craftflow-skill-distillation-plan.md).

Reuses `normalize_reason()` and the `.events.jsonl` parsing helper
(`_parse_events_file`) from the sibling `craftflow_learn_scan.py` rather than
reimplementing them.

Subcommands:
    --observe <wf-id> [--state-dir PATH] [--ledger PATH]
        Read the workflow artifact <state-dir>/workflows/<wf-id>.json and its
        <wf-id>.events.jsonl, extract candidate signals, and upsert them into
        the ledger file (atomic write).

    --query [--ledger PATH]
        Print the current ledger as JSON.

    --prune [--ledger PATH] [--state-dir PATH] [--project-root PATH]
        Apply anti-rot transitions to the ledger (atomic write). Stale (>90d,
        no new evidence) `candidate` entries are removed. `rejected` entries
        are left untouched. `promoted` entries are NEVER removed and NEVER
        have their `status` changed -- instead they are checked for rot: is
        `promoted_skill` present and non-blank, does the promoted skill's
        canonical `.claude/skills/<name>/SKILL.md` still exist, do all paths
        in its `craftflow-referenced-paths` frontmatter still exist
        (resolved against `--project-root`, and contained under it -- no
        absolute path or ".." traversal), and (if present) is its
        `craftflow-review-after` date parseable and not yet elapsed?
        Rot found sets `needs_review: true` / `needs_review_reason`
        ('missing_promoted_skill_name', 'stale_path', 'review_after_elapsed',
        or 'unparseable_review_after') on the entry in place. Refuses to
        write back over a corrupt-but-existing ledger file (see
        `LedgerCorruptError`) -- exits non-zero with a JSON error instead.
        If `--ledger` is left at its default and `--state-dir` is
        overridden, the default ledger path is derived from `--state-dir`
        (consistent with `--observe`/`--backtest`).

    --backtest [--state-dir PATH]
        Run the observe-equivalent logic over every workflow artifact under
        <state-dir>/workflows/*.json WITHOUT mutating the real ledger file.
        Builds the candidate set in memory and prints it as JSON, including
        a gate-eligibility subset.

    --reject <candidate-id> [--reason TEXT] [--ledger PATH]
        Mark a ledger candidate as rejected (permanent tombstone; revived
        only by --observe's own upsert logic once distinct_workflows at
        least doubles from the value recorded at rejection time). Phase 3
        addition: the router's skill-distill approval flow's "Reject"
        option calls this directly -- no agent or subagent is involved.

Ledger file default: .craftflow/state/project/skill-candidates.json

Exit 0 on success. Exit 1 on usage error.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import craftflow_learn_scan as learn_scan  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
DEFAULT_LEDGER_PATH = ".craftflow/state/project/skill-candidates.json"
DEFAULT_STATE_DIR = ".craftflow/state"

MAX_CANDIDATES = 200
MAX_EVIDENCE_PER_CANDIDATE = 10
STALE_DAYS = 90

_SEVERITY_RANK = {"unknown": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

# This project's real review taxonomy is wider than critical/high/medium/low:
# planning_review_findings[].severity and free-text reviewer/remediation
# notes also use "BLOCKING" (equivalent to a hard-blocking/critical finding)
# and "ADVISORY"/"MINOR" (equivalent to a low-priority note). Observed
# verbatim in this repo's own .craftflow/state/workflows/*.json history.
_SEVERITY_SYNONYMS = {"blocking": "critical", "advisory": "low", "minor": "low"}

_SEVERITY_WORD_ALT = r"(?:critical|high|medium|low|blocking|advisory|minor)"

# Negation guard: a severity word immediately preceded by "non-"/"non"/
# "not "/"no " is a negated mention ("3 medium non-blocking" == the medium
# findings are NOT blocking), never that severity's presence. Each
# lookbehind alternative is individually fixed-width, which Python's `re`
# requires (chained rather than alternated within one lookbehind).
_NEG_GUARD = r"(?<!non-)(?<!non)(?<!not\s)(?<!no\s)"

# Three real-world mention shapes, tried in this order at each position:
#   1. SUFFIX count: "word[_ ]?issues?['\"]?\s*[:=]\s*N" -- e.g. "critical=0",
#      "Critical Issues: 2", or a camelCase-concatenated metric name like
#      "totalCritical=5" (no left word-boundary required, so the merged
#      prefix is allowed; the trailing colon/equals/count is what anchors
#      the match to an intentional severity-count mention).
#   2. PREFIX count: "N word" -- e.g. "2 CRITICAL", "0 critical issues".
#   3. BARE mention: the word alone, e.g. "found a CRITICAL bug".
# A mention with an explicit count of exactly 0 (either shape) means the
# ABSENCE of that severity (a clean/PASS result) and must be excluded from
# consideration, not read as a severity marker.
_SEVERITY_TOKEN_RE = re.compile(
    _NEG_GUARD + r"(?P<word_suffix>" + _SEVERITY_WORD_ALT + r")(?:[_ ]?issues?)?[\"']?\s*[:=]\s*(?P<count_suffix>\d+)"
    r"|\b(?P<count_prefix>\d+)\s+" + _NEG_GUARD + r"(?P<word_prefix>" + _SEVERITY_WORD_ALT + r")\b"
    r"|" + _NEG_GUARD + r"\b(?P<word_bare>" + _SEVERITY_WORD_ALT + r")\b",
    re.IGNORECASE,
)

# Bare positive/neutral verdict tokens: these are the ABSENCE of a finding
# (a routine "no issues" outcome), never a recurring failure/finding
# signature. Only skipped when they are the ENTIRE normalized text, so real
# content elsewhere in a longer string is never suppressed.
_BARE_VERDICT_TOKENS = frozenset({
    "approve", "approved", "clean", "pass", "passed", "ok", "okay",
    "done", "n/a", "none", "no issues", "no issues found",
})

# Matches a relative-looking path with at least one directory segment and a
# file extension, e.g. "tools/craftflow-plugin/scripts/foo.py" or
# "src/daemon.ts" (optionally followed by ":123" line info, which the regex
# simply stops before).
_PATH_RE = re.compile(r"\b(?:[A-Za-z0-9_.\-]+/){1,}[A-Za-z0-9_.\-]+\.[A-Za-z0-9]+\b")

# Strict allowlist for workflow ids used in filesystem path joins under
# <state-dir>/workflows/. No "/" is permitted at all (blocks both absolute
# paths and any relative traversal, since a traversal needs at least one
# "/" to move up a directory level).
_WF_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_severity(value) -> str:
    """Best-effort severity normalization to one of: critical/high/medium/low/unknown.

    Recognizes this project's full real-world severity vocabulary
    (critical/high/medium/low plus the synonyms blocking/advisory/minor),
    and scans free text for ALL severity mentions rather than the first
    word encountered -- picking the highest-ranked mention that carries a
    nonzero (or absent/bare) count. A mention with an explicit count of 0
    (e.g. "0 critical issues", "critical=0") is the ABSENCE of that
    severity and is excluded, even when a different, genuinely nonzero
    severity is mentioned elsewhere in the same text.
    """
    if not value:
        return "unknown"
    text = str(value).strip()
    lowered = text.lower()
    if lowered in _SEVERITY_RANK:
        return lowered
    if lowered in _SEVERITY_SYNONYMS:
        return _SEVERITY_SYNONYMS[lowered]

    best = "unknown"
    for m in _SEVERITY_TOKEN_RE.finditer(text):
        word = m.group("word_suffix") or m.group("word_prefix") or m.group("word_bare")
        if not word:
            continue
        count = m.group("count_suffix") or m.group("count_prefix")
        if count is not None and int(count) == 0:
            continue
        mapped = _SEVERITY_SYNONYMS.get(word.lower(), word.lower())
        if _SEVERITY_RANK.get(mapped, 0) > _SEVERITY_RANK.get(best, 0):
            best = mapped
    return best


def derive_surface(text) -> str:
    """Derive a directory-prefix bucket from a path-like substring in `text`.
    Falls back to 'unscoped' when no path evidence is present."""
    if not isinstance(text, str) or not text:
        return "unscoped"
    m = _PATH_RE.search(text)
    if not m:
        return "unscoped"
    path = m.group(0)
    dirname = os.path.dirname(path)
    if not dirname:
        return "unscoped"
    parts = [p for p in dirname.split("/") if p]
    if not parts:
        return "unscoped"
    return "/".join(parts[:2])


def candidate_id(surface: str, signature: str) -> str:
    digest = hashlib.sha1(f"{surface}\x00{signature}".encode("utf-8")).hexdigest()
    return digest[:8]


def is_safe_wf_id(wf_id, workflows_dir: Path | None = None) -> bool:
    """Fail-closed validation for a wf_id used in a filesystem path join.

    Rejects anything that is not a plain filename-safe token: no "/"
    (blocks both absolute paths like "/etc/passwd" and relative traversal
    like "../../x", both of which require at least one path separator),
    no leading dot, and no literal ".." component. As a second,
    belt-and-suspenders layer (in case workflows_dir is provided), also
    resolves the would-be artifact path and confirms it stays inside
    workflows_dir.
    """
    if not isinstance(wf_id, str) or not wf_id:
        return False
    if wf_id in (".", ".."):
        return False
    if wf_id.startswith(".") or wf_id.startswith("/"):
        return False
    if not _WF_ID_RE.match(wf_id):
        return False
    if workflows_dir is not None:
        try:
            candidate_path = (workflows_dir / f"{wf_id}.json").resolve()
            candidate_path.relative_to(workflows_dir.resolve())
        except (OSError, ValueError):
            return False
    return True


# ---------------------------------------------------------------------------
# Signal extraction (defensive: heterogeneous, LLM-written artifact shapes)
# ---------------------------------------------------------------------------

def extract_event_signals(events: list) -> list:
    """Signal source 1: existing failure events (same detection as learn_scan)."""
    signals = []
    if not isinstance(events, list):
        return signals
    for event in events:
        if not isinstance(event, dict):
            continue
        if not learn_scan.is_failure_event(event):
            continue
        reason = event.get("reason", "")
        if not isinstance(reason, str) or not reason.strip():
            continue
        ts = event.get("ts", event.get("timestamp", ""))
        signals.append({
            "text": reason,
            "source": "events.jsonl",
            "severity": "unknown",
            "surface": derive_surface(reason),
            "ts": ts if isinstance(ts, str) else "",
        })
    return signals


def _entry_severity(entry: dict, text: str) -> str:
    sev = normalize_severity(text)
    if sev != "unknown":
        return sev
    for key, sev_name in (("critical", "critical"), ("high", "high"), ("medium", "medium"), ("low", "low")):
        v = entry.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
            return sev_name
    return "unknown"


def extract_remediation_signals(artifact: dict) -> list:
    """Signal source 2: artifact remediation_history[] entries."""
    signals = []
    if not isinstance(artifact, dict):
        return signals
    rh = artifact.get("remediation_history")
    if not isinstance(rh, list):
        return signals
    for entry in rh:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("ts", "")
        texts = []
        reason = entry.get("reason")
        if isinstance(reason, str) and reason.strip():
            texts.append(reason)
        items = entry.get("items")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, str) and item.strip():
                    texts.append(item)
        if not texts:
            note = entry.get("note")
            if isinstance(note, str) and note.strip():
                texts.append(note)
        for text in texts:
            signals.append({
                "text": text,
                "source": "remediation_history",
                "severity": _entry_severity(entry, text),
                "surface": derive_surface(text),
                "ts": ts if isinstance(ts, str) else "",
            })
    return signals


def extract_finding_signals(results: dict, source_name: str) -> list:
    """Signal source 3: results.reviewer / results.hunter (string, dict-with-findings,
    or dict-with-note shapes have all been observed in real artifacts)."""
    signals = []
    if not isinstance(results, dict):
        return signals
    value = results.get(source_name)
    if value is None:
        return signals

    if isinstance(value, str):
        text = value.strip()
        if text and learn_scan.normalize_reason(text) not in _BARE_VERDICT_TOKENS:
            signals.append({
                "text": text,
                "source": f"results.{source_name}",
                "severity": normalize_severity(text),
                "surface": derive_surface(text),
                "ts": "",
            })
        return signals

    if not isinstance(value, dict):
        return signals

    findings = value.get("findings")
    if isinstance(findings, list) and findings:
        for finding in findings:
            if isinstance(finding, dict):
                text = (
                    finding.get("summary")
                    or finding.get("note")
                    or finding.get("reason")
                    or finding.get("description")
                )
                if not isinstance(text, str) or not text.strip():
                    continue
                location = finding.get("location")
                surface = derive_surface(location) if isinstance(location, str) else derive_surface(text)
                signals.append({
                    "text": text,
                    "source": f"results.{source_name}",
                    "severity": normalize_severity(finding.get("severity")),
                    "surface": surface,
                    "ts": "",
                })
            elif isinstance(finding, str) and finding.strip():
                signals.append({
                    "text": finding,
                    "source": f"results.{source_name}",
                    "severity": normalize_severity(finding),
                    "surface": derive_surface(finding),
                    "ts": "",
                })
        return signals

    # No findings list -- fall back to a single note-based signal, using any
    # *_issues counts on the object to approximate severity.
    note = value.get("note")
    if isinstance(note, str) and note.strip():
        severity = "unknown"
        for key, sev_name in (
            ("critical_issues", "critical"),
            ("high_issues", "high"),
            ("medium_issues", "medium"),
            ("low_issues", "low"),
        ):
            v = value.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0:
                severity = sev_name
                break
        if severity == "unknown":
            severity = normalize_severity(note)
        signals.append({
            "text": note,
            "source": f"results.{source_name}",
            "severity": severity,
            "surface": derive_surface(note),
            "ts": "",
        })
    return signals


def extract_verifier_scenario_signals(results: dict) -> list:
    """Signal source 4: results.verifier SCENARIOS (proven, executable checks) --
    highest-value source when present. Rare/absent in older artifacts; handled
    defensively since the shape is not guaranteed."""
    signals = []
    if not isinstance(results, dict):
        return signals
    verifier = results.get("verifier")
    if not isinstance(verifier, dict):
        return signals
    scenarios = verifier.get("SCENARIOS")
    if not isinstance(scenarios, list):
        scenarios = verifier.get("scenarios")
    if not isinstance(scenarios, list):
        return signals
    for sc in scenarios:
        if not isinstance(sc, dict):
            continue
        text = sc.get("name")
        if not isinstance(text, str) or not text.strip():
            continue
        command = sc.get("command") if isinstance(sc.get("command"), str) else ""
        signals.append({
            "text": text,
            "source": "results.verifier",
            "severity": "unknown",
            "surface": derive_surface(command or text),
            "ts": "",
        })
    return signals


def extract_memory_note_signals(artifact: dict) -> list:
    """Signal source 5: memory_notes[].patterns (new structured contract) or a
    flat list of strings (the shape actually used by every real artifact in
    this repo's history)."""
    signals = []
    if not isinstance(artifact, dict):
        return signals
    mn = artifact.get("memory_notes")
    if not isinstance(mn, list):
        return signals
    for item in mn:
        if isinstance(item, str):
            if item.strip():
                signals.append({
                    "text": item,
                    "source": "memory_notes",
                    "severity": "unknown",
                    "surface": derive_surface(item),
                    "ts": "",
                })
        elif isinstance(item, dict):
            patterns = item.get("patterns")
            if not isinstance(patterns, list):
                continue
            for p in patterns:
                text = None
                if isinstance(p, str):
                    text = p
                elif isinstance(p, dict):
                    t = p.get("text")
                    if isinstance(t, str):
                        text = t
                if isinstance(text, str) and text.strip():
                    signals.append({
                        "text": text,
                        "source": "memory_notes",
                        "severity": "unknown",
                        "surface": derive_surface(text),
                        "ts": "",
                    })
    return signals


def collect_signals(artifact: dict, events: list) -> list:
    """Collect all candidate signals for one workflow. Each source is wrapped
    independently so a malformed field in one source never prevents the
    others from contributing (and never crashes the caller)."""
    signals = []
    if not isinstance(artifact, dict):
        artifact = {}
    if not isinstance(events, list):
        events = []

    for extractor, arg in (
        (extract_event_signals, events),
        (extract_remediation_signals, artifact),
    ):
        try:
            signals.extend(extractor(arg))
        except Exception:
            continue

    results = artifact.get("results")
    if isinstance(results, dict):
        for extractor in (
            lambda r: extract_finding_signals(r, "reviewer"),
            lambda r: extract_finding_signals(r, "hunter"),
            extract_verifier_scenario_signals,
        ):
            try:
                signals.extend(extractor(results))
            except Exception:
                continue

    try:
        signals.extend(extract_memory_note_signals(artifact))
    except Exception:
        pass

    return signals


# ---------------------------------------------------------------------------
# Ledger load / save (atomic writes)
# ---------------------------------------------------------------------------

def _empty_ledger() -> dict:
    return {"schema_version": SCHEMA_VERSION, "candidates": []}


class LedgerCorruptError(Exception):
    """Raised by `load_ledger()` when the ledger file EXISTS but its content
    cannot be trusted (parse failure or invalid top-level schema shape) --
    distinguished from the benign "file does not exist" case (which returns
    an empty ledger with no error).

    CRITICAL (REM-FIX, task #59 item 1): `load_ledger()` used to catch
    `(OSError, ValueError)` on a truncated/corrupt-but-EXISTING file and
    silently degrade to an empty ledger -- indistinguishable from "no ledger
    file at all." Every mutating subcommand (`--observe`, `--prune`,
    `--reject`) then wrote that empty ledger straight back over the corrupt
    file via `save_ledger_atomic`, destroying it at exit 0. This is now
    triggered on EVERY workflow via the router's unconditional `--prune`
    wiring. Letting this exception propagate to `main()`'s existing
    OSError/UnicodeDecodeError/AttributeError/TypeError wrapper (which this
    error is added to) makes every mutating subcommand fail closed instead:
    non-zero exit, a clean JSON error, and the corrupt file left untouched
    on disk."""


def load_ledger(path: Path) -> dict:
    if not path.exists():
        return _empty_ledger()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise LedgerCorruptError(f"ledger file at {path} exists but failed to parse: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise LedgerCorruptError(f"ledger file at {path} exists but has an invalid schema shape")
    return data


@contextlib.contextmanager
def _ledger_file_lock(ledger_path: Path):
    """Exclusive advisory lock guarding the load-modify-save cycle against
    two concurrent invocations (e.g. two --observe calls racing) silently
    clobbering each other via last-writer-wins on os.replace(). A blocking
    acquire is fine here: this is a low-frequency, fast operation, so no
    timeout/retry machinery is needed. Lock file lives alongside the ledger
    file itself and is never cleaned up (cheap, reused across calls)."""
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ledger_path.with_suffix(ledger_path.suffix + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def read_events_file_safely(path: Path) -> tuple:
    """Read an .events.jsonl file, tolerating file-level read/decode
    failures (e.g. a torn write leaving invalid UTF-8 bytes partway
    through the file). Per-line JSON parsing already tolerates individual
    malformed lines inside `learn_scan._parse_events_file` -- this wraps
    the file-level iteration itself, since a UnicodeDecodeError raised
    while iterating lines is NOT an OSError and would otherwise propagate
    as an uncaught exception.

    Returns (events, error): `error` is None on success, or a short
    diagnostic string on failure (events is [] in that case).

    IMPORTANT: used by BOTH --observe (cmd_observe) and --backtest
    (run_backtest) so the two code paths keep equivalent resilience against
    this failure mode -- keep them in parity if either is touched again.
    """
    try:
        return learn_scan._parse_events_file(path), None
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [], f"failed to read/parse {path.name}: {exc}"


def save_ledger_atomic(path: Path, data: dict) -> None:
    """Write `data` to `path` atomically: write to a temp file in the same
    directory, then os.replace() into place. On any failure the original
    file (if any) is left untouched and the temp file is cleaned up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".skill-candidates-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Upsert (the anti-slop occurrence-counting core)
# ---------------------------------------------------------------------------

def upsert_candidates(ledger: dict, workflow_identity: str, signals: list) -> dict:
    """Upsert signals from ONE workflow into the ledger.

    `workflow_identity` MUST be a stable identifier for the physical
    workflow across its entire lifecycle -- callers pass the filename-
    derived wf_id, never the artifact's self-reported `workflow_uuid`
    field (which may be absent on an early observation and present on a
    later one, which would otherwise register as two different identities
    for one physical workflow).

    Occurrence counting rule: `distinct_workflows` counts unique
    `workflow_identity` values that have contributed evidence, never the
    raw number of matching signals/events. Calling this once per workflow
    with N matching signals for the same (surface, signature) key
    increments distinct_workflows by at most 1 for that workflow.
    """
    if not isinstance(ledger, dict) or not isinstance(ledger.get("candidates"), list):
        ledger = _empty_ledger()

    # HIGH (REM-FIX round 6): direct-indexing every loaded ledger entry's
    # "surface"/"signature" here used to raise an uncaught KeyError on a
    # well-formed dict entry that is simply missing those keys (hand-edited,
    # partially migrated, or a future schema change) -- as opposed to a
    # non-dict entry, which was already guarded. Since --observe is invoked
    # on EVERY workflow completion, one such malformed entry permanently
    # broke ledger mining thereafter. Skip (never index) any candidate that
    # isn't a dict or lacks a string "surface"/"signature" pair; it simply
    # cannot participate in key-based upsert matching, but it is NOT
    # dropped from the ledger (still present in ledger["candidates"]).
    candidates_by_key = {}
    for c in ledger["candidates"]:
        if not isinstance(c, dict):
            continue
        surface = c.get("surface")
        signature = c.get("signature")
        if not isinstance(surface, str) or not isinstance(signature, str):
            continue
        candidates_by_key[(surface, signature)] = c
    ts = now_iso()

    grouped: dict = {}
    for sig in signals:
        text = sig.get("text", "")
        signature = learn_scan.normalize_reason(text) if isinstance(text, str) else ""
        if not signature:
            continue
        key = (sig.get("surface") or "unscoped", signature)
        grouped.setdefault(key, []).append(sig)

    for key, sigs in grouped.items():
        surface, signature = key
        entry = candidates_by_key.get(key)
        max_sev_new = "unknown"
        for s in sigs:
            sev = s.get("severity", "unknown")
            if _SEVERITY_RANK.get(sev, 0) > _SEVERITY_RANK.get(max_sev_new, 0):
                max_sev_new = sev

        if entry is None:
            entry = {
                "id": candidate_id(surface, signature),
                "surface": surface,
                "signature": signature,
                "workflows": [],
                "distinct_workflows": 0,
                "max_severity": "unknown",
                "evidence": [],
                "first_seen": ts,
                "last_seen": ts,
                "status": "candidate",
                "promoted_skill": None,
                "rejected_reason": None,
                "rejected_at_distinct_workflows": None,
            }
            ledger["candidates"].append(entry)
            candidates_by_key[key] = entry
        else:
            # HIGH (REM-FIX round 6): an EXISTING entry located via
            # candidates_by_key is guaranteed to be a dict with string
            # surface/signature (that guard lives in the candidates_by_key
            # build above), but it may still be a well-formed dict that is
            # simply missing workflows/distinct_workflows/max_severity/
            # evidence -- a hand-edited or partially migrated schema. Repair
            # in place with setdefault() rather than direct-indexing, so
            # mutating a malformed-but-matching entry degrades cleanly
            # instead of raising KeyError.
            entry.setdefault("workflows", [])
            entry.setdefault("distinct_workflows", 0)
            entry.setdefault("max_severity", "unknown")
            entry.setdefault("evidence", [])

        if workflow_identity not in entry["workflows"]:
            entry["workflows"].append(workflow_identity)
        entry["distinct_workflows"] = len(set(entry["workflows"]))

        if _SEVERITY_RANK.get(max_sev_new, 0) > _SEVERITY_RANK.get(entry.get("max_severity", "unknown"), 0):
            entry["max_severity"] = max_sev_new

        # Revival check: a rejected candidate is only revived here (new
        # evidence arriving), never by --prune, and only once distinct
        # workflows has at least doubled since rejection.
        if entry.get("status") == "rejected":
            rejected_at = entry.get("rejected_at_distinct_workflows")
            if isinstance(rejected_at, (int, float)) and rejected_at > 0:
                if entry["distinct_workflows"] >= 2 * rejected_at:
                    entry["status"] = "candidate"
                    entry["rejected_reason"] = None
                    entry["rejected_at_distinct_workflows"] = None

        # Skip re-appending evidence rows already recorded for this
        # candidate (same wf + source + text). Without this, repeat
        # re-observation of one workflow re-appends the identical row every
        # call, which can push out genuinely distinct evidence from OTHER
        # workflows once MAX_EVIDENCE_PER_CANDIDATE trims the list.
        existing_evidence_keys = {
            (e.get("wf"), e.get("source"), e.get("text")) for e in entry["evidence"]
        }
        for sig in sigs:
            row_key = (workflow_identity, sig.get("source", "unknown"), sig.get("text", ""))
            if row_key in existing_evidence_keys:
                continue
            row_ts = sig.get("ts") or ts
            entry["evidence"].append({
                "wf": workflow_identity,
                "ts": row_ts,
                "source": sig.get("source", "unknown"),
                "text": sig.get("text", ""),
            })
            existing_evidence_keys.add(row_key)
        if len(entry["evidence"]) > MAX_EVIDENCE_PER_CANDIDATE:
            keep_head = MAX_EVIDENCE_PER_CANDIDATE // 2
            keep_tail = MAX_EVIDENCE_PER_CANDIDATE - keep_head
            entry["evidence"] = entry["evidence"][:keep_head] + entry["evidence"][-keep_tail:]
            entry["evidence_truncated"] = True

        entry["last_seen"] = ts

    if len(ledger["candidates"]) > MAX_CANDIDATES:
        overflow = len(ledger["candidates"]) - MAX_CANDIDATES
        # Size-cap eviction must only ever target "candidate"-status entries.
        # "rejected" and "promoted" entries are permanent tombstones/records
        # (mirrors the same status exemption prune_ledger() already applies
        # below) -- evicting them here would silently destroy that history
        # once the ledger fills past MAX_CANDIDATES.
        evictable = [c for c in ledger["candidates"] if c.get("status") == "candidate"]
        evictable.sort(key=lambda c: c.get("last_seen", ""))
        to_evict = {id(c) for c in evictable[:overflow]}
        if to_evict:
            ledger["candidates"] = [c for c in ledger["candidates"] if id(c) not in to_evict]

    return ledger


# ---------------------------------------------------------------------------
# Prune (anti-rot transitions)
# ---------------------------------------------------------------------------

def _is_stale(last_seen: str, now: datetime, days: int = STALE_DAYS) -> bool:
    if not last_seen:
        return False
    try:
        dt = datetime.strptime(last_seen, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return (now - dt).days > days


def _is_safe_project_relative_path(rel: str, base: Path) -> bool:
    """Fail-closed containment guard for a project-relative path string (a
    `craftflow-referenced-paths` entry, or a `promoted_skill` name) before it
    is joined onto `base` and touched via `.exists()`/`.read_text()`.

    MEDIUM (REM-FIX, task #59 item 4): neither the `craftflow-referenced-
    paths` join in `_referenced_paths_missing` nor the `promoted_skill` name
    join in `_read_promoted_skill_frontmatter` had any path-containment
    check before this fix -- an absolute path or a ".."-traversal value
    silently escaped `project_root`, either falsely reporting a referenced
    path as present (masking real rot) or reading/following an arbitrary
    SKILL.md outside the project entirely.

    Styled after `is_safe_wf_id` above (~line 208), but permits legitimate
    "/" subdirectory components -- unlike a workflow id, both a referenced
    path and a nested skill name may legitimately contain them. Rejects an
    empty/non-string value, anything absolute (leading "/") or home-relative
    (leading "~"), any literal ".." path component, then -- belt-and-
    suspenders, mirroring `is_safe_wf_id`'s own second layer -- resolves the
    joined path and confirms it still lives under `base`."""
    if not isinstance(rel, str) or not rel.strip():
        return False
    rel = rel.strip()
    if rel.startswith("/") or rel.startswith("~"):
        return False
    parts = [p for p in rel.split("/") if p != ""]
    if not parts or any(p == ".." for p in parts):
        return False
    try:
        candidate_path = (base / rel).resolve()
        candidate_path.relative_to(base.resolve())
    except (OSError, ValueError):
        return False
    return True


def _read_promoted_skill_frontmatter(project_root: Path, name: str):
    """Read and parse the frontmatter of a promoted skill's canonical
    SKILL.md. Returns the parsed frontmatter dict, or None when the file is
    missing, unreadable, has no frontmatter block at all, or `name` fails the
    `_is_safe_project_relative_path` containment guard -- all of which the
    caller treats identically as "the SKILL.md itself is gone".

    Reuses `craftflow_skill_promote.parse_frontmatter` rather than
    reimplementing a parser (deferred/lazy import: `craftflow_skill_promote`
    itself imports this module at ITS top level, so importing it back at
    THIS module's top level would create an import-order cycle; importing it
    lazily here, at first call time, avoids that entirely)."""
    import craftflow_skill_promote as promote_mod  # noqa: WPS433 (intentional lazy import)

    skills_dir = project_root / ".claude" / "skills"
    if not _is_safe_project_relative_path(name, skills_dir):
        return None
    skill_md_path = skills_dir / name / "SKILL.md"
    try:
        content = skill_md_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    fm = promote_mod.parse_frontmatter(content)
    if not isinstance(fm, dict):
        return None
    return fm


def _referenced_paths_missing(project_root: Path, fm: dict) -> bool:
    """True if any comma-separated entry in `craftflow-referenced-paths`
    (relative to `project_root`) no longer exists on disk, OR fails the
    `_is_safe_project_relative_path` containment guard (an absolute path or
    a ".."-traversal entry is treated as rot -- it can never be verified
    safely, so it degrades to the same outcome as "missing"). An absent or
    empty field is not itself rot (a skill may legitimately reference no
    specific paths)."""
    raw = fm.get("craftflow-referenced-paths")
    if not isinstance(raw, str) or not raw.strip():
        return False
    for part in raw.split(","):
        rel = part.strip()
        if not rel:
            continue
        if not _is_safe_project_relative_path(rel, project_root):
            return True
        if not (project_root / rel).exists():
            return True
    return False


def _review_after_status(fm: dict, now: datetime):
    """Returns None if `craftflow-review-after` is absent, blank, the
    `PENDING_APPROVAL` placeholder (which only appears on not-yet-promoted
    proposals and should never reach here), or a real ISO timestamp that has
    not yet elapsed. Returns `'review_after_elapsed'` if a real ISO timestamp
    has passed, or `'unparseable_review_after'` if the value is present and
    non-placeholder but is NOT a parseable ISO timestamp.

    HIGH (REM-FIX, task #59 item 3): the previous `_review_after_elapsed`
    caught `ValueError` from `strptime` and returned `False` (treated as "not
    elapsed" / healthy) -- indistinguishable from a legitimate future date.
    An unparseable value is itself a data-quality problem that deserves
    human review, not silent healthy status."""
    raw = fm.get("craftflow-review-after")
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw or raw == "PENDING_APPROVAL":
        return None
    try:
        dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return "unparseable_review_after"
    return "review_after_elapsed" if now > dt else None


def _check_promoted_rot(entry: dict, project_root: Path, now: datetime):
    """Returns a `needs_review_reason` string ('missing_promoted_skill_name',
    'stale_path', 'review_after_elapsed', or 'unparseable_review_after') for
    one `status: promoted` ledger entry, or None if it shows no rot.

    Never raises: this is the single try/except that now wraps EVERY
    downstream sub-call (frontmatter read, referenced-paths check, and
    review-after check alike) -- MEDIUM (REM-FIX, task #59 item 6): before
    this fix, only the `_read_promoted_skill_frontmatter` call was wrapped,
    so an unexpected exception from `_referenced_paths_missing` or
    `_review_after_elapsed` (now `_review_after_status`) would have
    propagated uncaught, contradicting this function's own "never raises"
    docstring claim. Any exception from any of the three degrades to
    'stale_path'.

    HIGH (REM-FIX, task #59 item 2): a missing/blank `promoted_skill` name
    used to return None (treated as healthy, since "there is nothing on disk
    to check against") -- but a promoted entry with no recorded skill name
    can never be reconciled against a real SKILL.md again, and is itself a
    rot condition that must be surfaced for human review."""
    name = entry.get("promoted_skill")
    if not isinstance(name, str) or not name.strip():
        return "missing_promoted_skill_name"
    name = name.strip()
    try:
        fm = _read_promoted_skill_frontmatter(project_root, name)
        if fm is None:
            return "stale_path"
        if _referenced_paths_missing(project_root, fm):
            return "stale_path"
        return _review_after_status(fm, now)
    except Exception:
        return "stale_path"


def prune_ledger(ledger: dict, project_root="." ) -> dict:
    """Apply anti-rot transitions:
    - candidate, stale (>90d, no new evidence) -> removed.
    - rejected -> left untouched (permanent tombstone; revival happens only
      in --observe's upsert, never here).
    - promoted -> NEVER removed and NEVER has its `status` changed. Instead,
      checked for rot: is `promoted_skill` present and non-blank, does the
      promoted skill's canonical SKILL.md still exist, do all paths listed
      in its `craftflow-referenced-paths` frontmatter still exist on disk
      (and stay contained under `project_root`), and (if present) is its
      `craftflow-review-after` date parseable and not yet elapsed? Any rot
      found sets `needs_review: true` plus `needs_review_reason`
      (`missing_promoted_skill_name`, `stale_path`, `review_after_elapsed`,
      or `unparseable_review_after`) on the entry in place. A promoted entry
      that currently shows no rot has `needs_review` explicitly cleared back
      to False (idempotent: recomputed fresh every prune call, so a
      previously-flagged entry whose files came back is not stuck flagged
      forever).
    """
    if not isinstance(ledger, dict) or not isinstance(ledger.get("candidates"), list):
        return _empty_ledger()
    root = Path(project_root)
    now = datetime.now(timezone.utc)
    kept = []
    for c in ledger["candidates"]:
        if not isinstance(c, dict):
            continue
        if c.get("status") == "candidate" and _is_stale(c.get("last_seen", ""), now):
            continue
        if c.get("status") == "promoted":
            reason = _check_promoted_rot(c, root, now)
            if reason:
                c["needs_review"] = True
                c["needs_review_reason"] = reason
            else:
                c["needs_review"] = False
                c["needs_review_reason"] = None
        kept.append(c)
    ledger["candidates"] = kept
    return ledger


# ---------------------------------------------------------------------------
# Backtest (read-only over the whole corpus; never touches the real ledger)
# ---------------------------------------------------------------------------

def gate_eligible(c: dict) -> bool:
    """Recurrence gate: distinct_workflows>=2 is sufficient, regardless of
    severity.

    Calibrated against this project's real workflow corpus (REM-FIX round
    3): a backtest over 141 real workflow logs mined 200 candidates, of
    which 196 were singletons (distinct_workflows=1) -- a structural
    property of this project's low-recurrence workflow cadence, not a code
    defect. The original >=3-distinct-workflows tier (with a >=2-plus-
    critical-severity escalation) left `gate_eligible_count` at 1 on that
    real corpus, effectively never firing. >=2 distinct workflows is the
    calibrated bar: still requires genuine recurrence across independent
    workflows (a single workflow's repeated internal retries never counts,
    per `upsert_candidates`'s distinct-workflow-identity dedup), but no
    longer additionally requires a critical-severity finding to reach that
    bar at N=2.
    """
    dw = c.get("distinct_workflows", 0)
    return dw >= 2


def run_backtest(state_dir: Path) -> dict:
    ledger = _empty_ledger()
    workflows_dir = state_dir / "workflows"
    if not workflows_dir.exists():
        return ledger
    try:
        json_files = sorted(workflows_dir.glob("*.json"))
    except OSError:
        return ledger

    for jf in json_files:
        try:
            wf_id = jf.stem
            # Defensive: wf_id here is derived from an actual file already
            # discovered via workflows_dir.glob(), so it is not attacker
            # input the way --observe's wf_id is -- but the same allowlist
            # guard is applied for consistency, so both code paths agree on
            # what a valid wf_id looks like.
            if not is_safe_wf_id(wf_id, workflows_dir):
                continue
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    artifact = json.load(f)
            except (OSError, ValueError):
                continue
            if not isinstance(artifact, dict):
                continue
            # Identity for dedup is pinned to wf_id (stable, filename-derived,
            # invariant across the workflow's entire lifecycle) -- NOT the
            # artifact's self-reported workflow_uuid, which may be absent on
            # an early observation and present on a later one, which would
            # otherwise register as two different identities for one
            # physical workflow and inflate distinct_workflows.
            events_path = workflows_dir / f"{wf_id}.events.jsonl"
            events, _events_error = read_events_file_safely(events_path) if events_path.exists() else ([], None)
            signals = collect_signals(artifact, events)
            upsert_candidates(ledger, wf_id, signals)
        except Exception:
            # A single malformed workflow must never abort the backtest.
            continue

    return ledger


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_observe(args) -> int:
    wf_id = args.observe
    state_dir = Path(args.state_dir)
    ledger_path = Path(args.ledger)
    workflows_dir = state_dir / "workflows"

    if not is_safe_wf_id(wf_id, workflows_dir):
        print(
            json.dumps({"error": f"invalid or unsafe wf_id: {wf_id!r}"}),
            file=sys.stderr,
        )
        return 1

    artifact: dict = {}
    artifact_path = workflows_dir / f"{wf_id}.json"
    if artifact_path.exists():
        try:
            with open(artifact_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                artifact = loaded
        except (OSError, ValueError):
            artifact = {}

    events_path = workflows_dir / f"{wf_id}.events.jsonl"
    events, events_error = read_events_file_safely(events_path) if events_path.exists() else ([], None)

    # workflow_uuid is recorded as metadata only -- it is NEVER the dedup
    # identity (see the comment on the same pinning in run_backtest above).
    workflow_uuid = artifact.get("workflow_uuid") if isinstance(artifact.get("workflow_uuid"), str) else None

    with _ledger_file_lock(ledger_path):
        ledger = load_ledger(ledger_path)
        signals = collect_signals(artifact, events)
        ledger = upsert_candidates(ledger, wf_id, signals)
        save_ledger_atomic(ledger_path, ledger)

    output = {
        "observed": wf_id,
        "workflow_uuid": workflow_uuid,
        "signals_collected": len(signals),
        "total_candidates": len(ledger["candidates"]),
    }
    if events_error:
        output["events_parse_warning"] = events_error
    print(json.dumps(output, indent=2))
    return 0


def cmd_query(args) -> int:
    ledger_path = Path(args.ledger)
    ledger = load_ledger(ledger_path)
    print(json.dumps(ledger, indent=2))
    return 0


def cmd_prune(args) -> int:
    # LOW (REM-FIX, task #59 item 5): --state-dir was declared and accepted
    # by argparse for this subcommand but never read anywhere below --
    # unlike --observe/--backtest, which both honor a custom --state-dir to
    # locate workflow artifacts. When --ledger is left at its built-in
    # default and --state-dir is overridden, follow --state-dir for the
    # ledger path too (<state-dir>/project/skill-candidates.json is exactly
    # what DEFAULT_LEDGER_PATH already resolves to under DEFAULT_STATE_DIR).
    # An explicit --ledger always wins outright.
    if args.ledger == DEFAULT_LEDGER_PATH and args.state_dir != DEFAULT_STATE_DIR:
        ledger_path = Path(args.state_dir) / "project" / "skill-candidates.json"
    else:
        ledger_path = Path(args.ledger)
    project_root = Path(args.project_root)
    with _ledger_file_lock(ledger_path):
        ledger = load_ledger(ledger_path)
        ledger = prune_ledger(ledger, project_root)
        save_ledger_atomic(ledger_path, ledger)
    print(json.dumps({"remaining_candidates": len(ledger["candidates"])}, indent=2))
    return 0


def cmd_reject(args) -> int:
    ledger_path = Path(args.ledger)
    candidate_id_arg = args.reject
    with _ledger_file_lock(ledger_path):
        ledger = load_ledger(ledger_path)
        # MEDIUM (REM-FIX round 5): symmetric to craftflow_skill_promote.py's
        # own cmd_approve guard -- a ledger corrupted to contain a non-dict
        # candidate entry must never raise an uncaught AttributeError out of
        # this generator; degrade to this script's documented
        # {"error": ...} JSON shape instead (still fails closed either way).
        entry = next(
            (
                c for c in ledger["candidates"]
                if isinstance(c, dict) and c.get("id") == candidate_id_arg
            ),
            None,
        )
        if entry is None:
            print(
                json.dumps({"error": f"candidate id not found in ledger: {candidate_id_arg!r}"}),
                file=sys.stderr,
            )
            return 1
        # HIGH 3 (REM-FIX): a real race exists between the router's gate check
        # and the user's eventual AskUserQuestion answer, during which a
        # DIFFERENT concurrent workflow could already have run
        # craftflow_skill_promote.py --approve on this same candidate. Refuse
        # (exit 1, no write) rather than silently overwriting a status that
        # is no longer in a rejectable state -- mirrors promote.py's own
        # defensive posture (e.g. its --project-root existence check).
        current_status = entry.get("status")
        if current_status not in ("candidate", "proposed"):
            print(
                json.dumps({
                    "error": (
                        f"cannot reject candidate {candidate_id_arg!r}: current status is "
                        f"{current_status!r}, expected 'candidate' or 'proposed' -- a concurrent "
                        "workflow may have already promoted or rejected it"
                    )
                }),
                file=sys.stderr,
            )
            return 1
        entry["status"] = "rejected"
        entry["rejected_reason"] = args.reason or "rejected via router approval flow"
        entry["rejected_at_distinct_workflows"] = entry.get("distinct_workflows", 0)
        save_ledger_atomic(ledger_path, ledger)

    print(json.dumps({
        "rejected": candidate_id_arg,
        "rejected_reason": entry["rejected_reason"],
        "rejected_at_distinct_workflows": entry["rejected_at_distinct_workflows"],
    }, indent=2))
    return 0


def cmd_backtest(args) -> int:
    state_dir = Path(args.state_dir)
    ledger = run_backtest(state_dir)
    candidates = ledger["candidates"]
    eligible = [c for c in candidates if gate_eligible(c)]
    print(json.dumps({
        "schema_version": SCHEMA_VERSION,
        "total_candidates": len(candidates),
        "gate_eligible_count": len(eligible),
        "candidates": candidates,
        "gate_eligible_candidates": eligible,
    }, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Craftflow skill-candidate ledger: mine recurring failure/finding "
        "signatures from workflow logs and artifacts."
    )
    parser.add_argument("--observe", metavar="WF_ID", help="Observe one workflow and upsert candidates into the ledger.")
    parser.add_argument("--query", action="store_true", help="Print the current ledger as JSON.")
    parser.add_argument("--prune", action="store_true", help="Apply anti-rot transitions to the ledger.")
    parser.add_argument(
        "--backtest", action="store_true",
        help="Run observe-equivalent logic over every workflow artifact without mutating the real ledger.",
    )
    parser.add_argument("--reject", metavar="CANDIDATE_ID", help="Mark a ledger candidate as rejected (permanent tombstone).")
    parser.add_argument("--reason", default=None, help="Optional rejection reason to record (used with --reject).")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help=f"Path to the craftflow state directory (default: {DEFAULT_STATE_DIR})")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER_PATH, help=f"Path to the ledger file (default: {DEFAULT_LEDGER_PATH})")
    parser.add_argument(
        "--project-root", default=".",
        help="Project root used by --prune to resolve promoted skills' canonical SKILL.md and "
        "craftflow-referenced-paths for the rot check (default: .)",
    )
    args = parser.parse_args()

    if args.backtest:
        return cmd_backtest(args)

    # MEDIUM (REM-FIX): mirror craftflow_skill_promote.py's main()-level
    # OSError/UnicodeDecodeError wrapping for the mutating/reading
    # subcommands, so an unexpected OS-level failure (e.g. a ledger path
    # whose parent path component is a regular file, not a directory, or a
    # non-UTF-8 file read) degrades to this script's own clean JSON error
    # shape instead of a raw Python traceback. --backtest is intentionally
    # excluded (read-only over an entire corpus of workflow artifacts; it
    # already tolerates per-file failures internally and never crashes).
    try:
        if args.query:
            return cmd_query(args)
        if args.prune:
            return cmd_prune(args)
        if args.reject:
            return cmd_reject(args)
        if args.observe:
            return cmd_observe(args)
    except (OSError, UnicodeDecodeError, AttributeError, TypeError, LedgerCorruptError) as exc:
        # MEDIUM (REM-FIX round 5): mirror craftflow_skill_promote.py's own
        # main()-level wrapper -- a malformed non-dict ledger candidate entry
        # is already guarded against directly in cmd_reject above, but this
        # wrapper stays defensive against any other future shape mismatch
        # against untrusted ledger content.
        #
        # CRITICAL (REM-FIX, task #59 item 1): LedgerCorruptError added here
        # so a corrupt-but-existing ledger file fails closed (non-zero exit,
        # clean JSON error, no write-back) for every mutating subcommand,
        # instead of load_ledger() silently treating it as an absent ledger.
        print(json.dumps({"error": f"unexpected error: {exc}"}), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
