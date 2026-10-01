#!/usr/bin/env python3
"""CRAFTFLOW stop gate: pure core (SPEC-0018 / ADR-0055), slice 1 (shadow).

Settings parsing, artifact facts and hard rules for the opt-in Stop hook. Stdlib only, no I/O at import
time, and no hook shell here: the functions below are total (they never raise on odd input). The only
functions that touch the filesystem are ``load_json_file`` and ``read_consent_file``; the rule engine
(``hard_rules``) is pure and never does.

Privacy: nothing returned by ``workflow_facts`` is ever written to a log row. ``title``/``objective``
free text is only matched locally against ``OUTWARD_RE``.
"""
from __future__ import annotations

import errno
import fnmatch
import json
import os
import re
import stat

import craftflow_context_nudge_compact as cc

# --- settings (DD-5 / DD-5a) -------------------------------------------------------------------------
USER_OVERRIDE_ENV = "CRAFTFLOW_STOP_GATE_USER_CONFIG"
# Built from segments only (harness-audit legacy-literal rule, ADR-0054 DD-21).
USER_OVERRIDE_SEGMENTS = (".claude", "craftflow", "stop-gate.json")
USER_OVERRIDE_MAX_BYTES = 65536

MODES = ("off", "audit", "on")
NOTIFY_MODES = ("off", "desktop", "push")
INT_CURSOR_MEANINGS = ("finished_count", "one_based_current")

DEFAULTS = {
    "mode": "off",
    "notify": "off",
    "notifyMinTurnSeconds": 300,
    "jevKindThreshold": 0.9,
    "jevNeedsHumanMax": 0.2,
    "jevTimeoutSeconds": 2.0,
    "tailChars": 1500,
    "intCursorMeaning": "finished_count",
    "maxAutoContinuesPerSession": 5,
}

# key -> (kind, low, high, allowed values). jevText is separate: consent-file only (DD-5a).
_SPECS = {
    "mode": ("enum", None, None, MODES),
    "notify": ("enum", None, None, NOTIFY_MODES),
    "notifyMinTurnSeconds": ("int", 0, 86400, None),
    "jevKindThreshold": ("float", 0.5, 1.0, None),
    "jevNeedsHumanMax": ("float", 0.0, 0.5, None),
    "jevTimeoutSeconds": ("float", 0.5, 2.5, None),
    "tailChars": ("int", 200, 4000, None),
    "intCursorMeaning": ("enum", None, None, INT_CURSOR_MEANINGS),
    "maxAutoContinuesPerSession": ("int", 0, 50, None),
}
_PLUGIN_ONLY_KEYS = frozenset(("intCursorMeaning",))
_SETTING_SOURCES = ("seam", "home", "passwd", "absent")


def _valid(key, value):
    kind, low, high, allowed = _SPECS[key]
    if kind == "enum":
        return isinstance(value, str) and value in allowed
    if kind == "int":
        return type(value) is int and low <= value <= high
    return type(value) in (int, float) and low <= value <= high


def _layer(settings, obj, tags, plugin):
    """Overlay the valid keys of ``obj`` onto ``settings``; invalid/unknown keys leave a tag."""
    for key, value in obj.items():
        if key == "jevText":
            continue  # consent-file only (DD-5a), decided in parse_settings
        if key not in _SPECS:
            tags.append("unknown_key:" + str(key)[:40])
        elif key in _PLUGIN_ONLY_KEYS and not plugin:
            tags.append("int_cursor_meaning_user_ignored")
        elif not _valid(key, value):
            tags.append("invalid:" + key)
        else:
            settings[key] = float(value) if _SPECS[key][0] == "float" else value


def jev_text_consent(settings_source, settings_obj, consent_obj):
    """(allowed, tag): text egress is allowed only by the consent file (DD-5a). Never raises."""
    try:
        if isinstance(consent_obj, dict) and consent_obj.get("jevText") is True:
            return True, None
        asked = isinstance(settings_obj, dict) and settings_obj.get("jevText") is True
        if asked and settings_source in ("seam", "home"):
            return False, "jev_text_seam_ignored"
        return False, None
    except Exception:  # noqa: BLE001 - totality
        return False, None


def _resolve_jev_text(plugin_obj, user_obj, user_source, consent_obj, tags):
    allowed, tag = jev_text_consent(user_source, user_obj, consent_obj)
    plugin_asked = isinstance(plugin_obj, dict) and plugin_obj.get("jevText") is True
    if plugin_asked:
        tags.append("jev_text_plugin_ignored")
    if tag:
        tags.append(tag)
    if allowed:
        return True, "consent_file"
    if tag:
        return False, "ignored_seam"
    return False, ("ignored_plugin" if plugin_asked else "off")


def parse_settings(plugin_obj, user_obj, user_source="absent", consent_obj=None):
    """(settings, tags) from the plugin file, the user file and the consent file (DD-5). Never raises.

    ``user_source`` is how the user file was reached: seam | home | passwd | absent. ``consent_obj`` is the
    securely read passwd-home file (or None). Only it can enable ``jevText`` or ``notify: "push"``.
    """
    try:
        settings = dict(DEFAULTS)
        tags = []
        if isinstance(plugin_obj, dict):
            _layer(settings, plugin_obj, tags, True)
        if isinstance(user_obj, dict):
            _layer(settings, user_obj, tags, False)
        if settings["mode"] == "on":
            settings["mode"] = "audit"
            tags.append("act_not_available")
        consent_push = isinstance(consent_obj, dict) and consent_obj.get("notify") == "push"
        if settings["notify"] == "push" and not consent_push:
            settings["notify"] = "desktop"
            tags.append("notify_push_seam_ignored")
        settings["jevText"], settings["jev_text_source"] = _resolve_jev_text(
            plugin_obj, user_obj, user_source, consent_obj, tags)
        return settings, tags
    except Exception:  # noqa: BLE001 - fail open to the shipped defaults (mode off)
        fallback = dict(DEFAULTS)
        fallback.update({"jevText": False, "jev_text_source": "off"})
        return fallback, ["settings_error"]


def user_override_path(environ):
    """Path of the user-level stop-gate.json, or None when HOME is unresolved. No I/O."""
    try:
        forced = environ.get(USER_OVERRIDE_ENV)
        if isinstance(forced, str) and forced.strip():
            return os.path.expanduser(forced)
        if "HOME" in environ:
            home = environ.get("HOME")
        else:
            home = passwd_home()
        if isinstance(home, str) and os.path.isabs(home):
            return os.path.join(home, *USER_OVERRIDE_SEGMENTS)
    except Exception:  # noqa: BLE001 - totality
        pass
    return None


def passwd_home():
    """Home directory from the passwd database; ignores the HOME env var. None when unavailable."""
    try:
        import pwd  # lazy: POSIX only
        return pwd.getpwuid(os.getuid()).pw_dir
    except Exception:  # noqa: BLE001 - fail open: unresolved home
        return None


def override_source(environ, pw_home):
    """How the user file path was reached: seam | home | passwd | absent (DD-5a)."""
    try:
        forced = environ.get(USER_OVERRIDE_ENV)
        if isinstance(forced, str) and forced.strip():
            return "seam"
        if "HOME" in environ:
            return "passwd" if environ.get("HOME") == pw_home else "home"
        return "passwd" if pw_home else "absent"
    except Exception:  # noqa: BLE001 - totality
        return "absent"


def load_json_file(path):
    """(obj, status, error): stat first, max 64 KiB, utf-8-sig, object only (ADR-0054 DD-4). Never raises."""
    if path is None:
        return None, "absent", "home_unresolved"
    try:
        try:
            st = os.stat(str(path))
        except FileNotFoundError:
            return None, "absent", None
        except OSError:
            return None, "error", "unreadable"
        if not stat.S_ISREG(st.st_mode):
            return None, "error", "unreadable"
        if st.st_size > USER_OVERRIDE_MAX_BYTES:
            return None, "error", "too_large"
        with open(str(path), "rb") as handle:
            raw = handle.read(USER_OVERRIDE_MAX_BYTES + 1)
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except ValueError:  # UnicodeDecodeError and JSONDecodeError are both ValueError
            return None, "error", "corrupt"
        if not isinstance(obj, dict):
            return None, "error", "not_object"
        return obj, "ok", None
    except Exception:  # noqa: BLE001 - fail open
        return None, "error", "unreadable"


def consent_stat_ok(st_mode, st_uid, st_size, uid):
    """(ok, tag) for the consent file's fstat result: regular, owned by ``uid``, <= 64 KiB (DD-5a)."""
    if not stat.S_ISREG(st_mode):
        return False, "consent_file_not_regular"
    if st_uid != uid:
        return False, "consent_file_foreign_owner"
    if st_size > USER_OVERRIDE_MAX_BYTES:
        return False, "consent_file_too_large"
    return True, None


def _consent_dirs_ok(home):
    """(ok, tag): both parent directories must be real directories, not symlinks. (False, None) = absent."""
    current = home
    for segment in USER_OVERRIDE_SEGMENTS[:-1]:
        current = os.path.join(current, segment)
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            return False, None
        if stat.S_ISLNK(st.st_mode):
            return False, "consent_file_symlink"
        if not stat.S_ISDIR(st.st_mode):
            return False, "consent_file_not_regular"
    return True, None


def read_consent_file(home):
    """(obj, tag): read the consent file below ``home`` without following symlinks (DD-5a).

    ``(None, None)`` means absent (not a violation); a tag names why the file was refused. Never raises.
    """
    try:
        if not (isinstance(home, str) and os.path.isabs(home)):
            return None, None
        ok, tag = _consent_dirs_ok(home)
        if not ok:
            return None, tag
        path = os.path.join(home, *USER_OVERRIDE_SEGMENTS)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None, None
        except OSError as exc:
            return None, ("consent_file_symlink" if exc.errno == errno.ELOOP else "consent_file_unreadable")
        with os.fdopen(fd, "rb") as handle:
            st = os.fstat(handle.fileno())
            ok, tag = consent_stat_ok(st.st_mode, st.st_uid, st.st_size, os.getuid())
            if not ok:
                return None, tag
            raw = handle.read(USER_OVERRIDE_MAX_BYTES + 1)
        try:
            obj = json.loads(raw.decode("utf-8-sig"))
        except ValueError:
            return None, "consent_file_corrupt"
        return (obj, None) if isinstance(obj, dict) else (None, "consent_file_corrupt")
    except Exception:  # noqa: BLE001 - fail open: no consent
        return None, "consent_file_unreadable"


# --- artifact facts (DD-7 / DD-7a / DD-8) ------------------------------------------------------------
WORKFLOW_FACT_KEYS = ("workflow_type", "plan_file", "phase_cursor", "phase_status", "phases",
                      "phases_malformed", "pending_gate", "open_decisions", "circuit_broken",
                      "last_event", "worktree_path", "worktree_mode", "terminal")

DONE_STATUSES = frozenset(("completed", "complete", "verified_passed", "verified_pass", "verified", "passed"))
PENDING_STATUSES = frozenset(("pending", "not_started", "queued"))
FAILURE_WORDS = ("fail", "block", "gap", "human", "revert", "remediat", "error", "abort")
_NUMERIC_ID_RE = re.compile(r"[0-9]+")
_FILE_KEYS = ("files", "files_surfaces", "files/surfaces")


def _str(value):
    return value if isinstance(value, str) else ""


def _str_list(value):
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def normalize_phase_status(value):
    """DONE | PENDING | FAILED | IN_PROGRESS for one raw status value (dict values use ``status``)."""
    if isinstance(value, dict):
        value = value.get("status")
    text = value.strip().lower() if isinstance(value, str) else ""
    if text in DONE_STATUSES:
        return "DONE"
    if text in PENDING_STATUSES:
        return "PENDING"
    if any(word in text for word in FAILURE_WORDS):
        return "FAILED"
    return "IN_PROGRESS"  # unknown vocabulary fails closed (H05)


def _phase_id(entry):
    if isinstance(entry, str):
        return entry or None
    if not isinstance(entry, dict):
        return None
    for key in ("phase_id", "id"):
        value = entry.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return str(value)
        if isinstance(value, str) and value:
            return value
    return None


def _phase_files(entry):
    for key in _FILE_KEYS:
        if key in entry:
            return _str_list(entry[key])
    return []


def _normalize_one(entry, pid):
    if isinstance(entry, str):
        return {"id": pid, "title": "", "objective": "", "checks": [], "files": []}
    return {"id": pid, "title": _str(entry.get("title")), "objective": _str(entry.get("objective")),
            "checks": _str_list(entry.get("checks")), "files": _phase_files(entry)}


def _normalize_phases(raw):
    """(phases, malformed): malformed when an entry has no id or an id is duplicated."""
    if not isinstance(raw, list):
        return [], False
    phases, seen, malformed = [], set(), False
    for entry in raw:
        pid = _phase_id(entry)
        if pid is None or pid in seen:
            malformed = True
            continue
        seen.add(pid)
        phases.append(_normalize_one(entry, pid))
    return phases, malformed


def normalize_phases(raw):
    """Normalised phases: [{id, title, objective, checks, files}] (DD-7a). Never raises."""
    try:
        return _normalize_phases(raw)[0]
    except Exception:  # noqa: BLE001 - totality
        return []


def _key_candidates(pid):
    exact = [pid]
    prefixes = [pid + "-"]
    if _NUMERIC_ID_RE.fullmatch(pid):
        exact.append("phase-" + pid)
        prefixes.append("phase-" + pid + "-")
    return exact, tuple(prefixes)


def phase_state(phase_status, pid):
    """DONE | PENDING | IN_PROGRESS | FAILED for phase ``pid`` over a ``{key: status}`` map (DD-7a)."""
    try:
        exact, prefixes = _key_candidates(pid)
        for key in exact:
            if key in phase_status:
                return normalize_phase_status(phase_status[key])
        subs = [normalize_phase_status(v) for k, v in phase_status.items()
                if isinstance(k, str) and k.startswith(prefixes)]
        if not subs:
            return "PENDING"  # absent
        if all(s == "DONE" for s in subs):
            return "DONE"
        return "FAILED" if "FAILED" in subs else "IN_PROGRESS"
    except Exception:  # noqa: BLE001 - totality, fail closed
        return "IN_PROGRESS"


def non_phase_failures(phase_status, phase_ids):
    """Keys that belong to no phase (neither an id nor an id sub-step) and carry a failure value."""
    claimed = set()
    prefixes = []
    for pid in phase_ids:
        exact, pre = _key_candidates(pid)
        claimed.update(exact)
        prefixes.extend(pre)
    prefixes = tuple(prefixes)
    return sorted(k for k, v in phase_status.items()
                  if isinstance(k, str) and k not in claimed and not k.startswith(prefixes)
                  and normalize_phase_status(v) == "FAILED")


def resolve_cursor(cursor, phases, int_cursor_meaning):
    """(index | None, resolution): id_match | int_finished_count | int_one_based | unresolved (DD-7a)."""
    try:
        ids = [p["id"] for p in phases]
        if isinstance(cursor, bool):
            return None, "unresolved"
        if isinstance(cursor, (str, int)) and str(cursor) in ids:
            return ids.index(str(cursor)), "id_match"
        if isinstance(cursor, int):
            if int_cursor_meaning == "finished_count":
                idx, how = cursor, "int_finished_count"
            elif int_cursor_meaning == "one_based_current":
                idx, how = cursor - 1, "int_one_based"
            else:
                return None, "unresolved"
            if 0 <= idx < len(phases):
                return idx, how
        return None, "unresolved"
    except Exception:  # noqa: BLE001 - totality
        return None, "unresolved"


def _status_map(raw):
    """Allowlisted view of phase_status: {key: status string} (dict values reduced to their status)."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, dict):
            value = value.get("status")
        out[key] = value.strip().lower() if isinstance(value, str) else ""
    return out


def _last_event(history):
    if isinstance(history, list) and history and isinstance(history[-1], dict):
        return _str(history[-1].get("event"))
    return ""


def workflow_facts(payload, isdir):
    """Allowlisted facts of a workflow artifact (DD-7). ``isdir`` is injected so the rules stay pure."""
    try:
        data = payload if isinstance(payload, dict) else {}
        phases, malformed = _normalize_phases(data.get("normalized_phases"))
        intent = data.get("intent") if isinstance(data.get("intent"), dict) else {}
        decisions = intent.get("open_decisions")
        breaker = data.get("circuit_breaker") if isinstance(data.get("circuit_breaker"), dict) else {}
        cursor = data.get("phase_cursor")
        return {
            "workflow_type": _str(data.get("workflow_type")).upper(),
            "plan_file": _str(data.get("plan_file")).strip(),
            "phase_cursor": cursor if isinstance(cursor, (str, int)) and not isinstance(cursor, bool) else None,
            "phase_status": _status_map(data.get("phase_status")),
            "phases": phases,
            "phases_malformed": malformed,
            "pending_gate": bool(cc._has_gate(data.get("pending_gate"))),
            "open_decisions": len(decisions) if isinstance(decisions, list) else 0,
            "circuit_broken": breaker.get("broken") is True,
            "last_event": _last_event(data.get("status_history")),
            "worktree_path": _str(data.get("worktree_path")),
            "worktree_mode": _str(data.get("worktree_mode")),
            "terminal": bool(cc.payload_terminal(payload, isdir=isdir)),
        }
    except Exception:  # noqa: BLE001 - totality: an unreadable artifact is terminal (fail closed)
        facts = dict.fromkeys(WORKFLOW_FACT_KEYS, "")
        facts.update({"phase_cursor": None, "phase_status": {}, "phases": [], "phases_malformed": True,
                      "pending_gate": False, "open_decisions": 0, "circuit_broken": False,
                      "terminal": True})
        return facts


# --- next phase, commit scope (DD-9 H09/H10, DD-9b) --------------------------------------------------
BOUND_REASONS = frozenset(("single_candidate", "mention_mtime_agree", "session_match"))
REVERT_WORDS = ("revert", "fail", "blocked", "abort", "rollback", "escalat")
SECRET_MESSAGE_CAP = 65536


def _next_at(phases, status, idx):
    """(phase, case): the phase about to run when the cursor sits on index idx (DD-9 H09 a/b)."""
    if not 0 <= idx < len(phases):
        return None, "none"
    state = phase_state(status, phases[idx]["id"])
    if state == "PENDING":
        return phases[idx], "at_next"
    if state == "DONE" and idx + 1 < len(phases):
        if phase_state(status, phases[idx + 1]["id"]) == "PENDING":
            return phases[idx + 1], "on_completed"
    return None, "none"


def next_phase(facts):
    """{phase, case, resolution, candidates}: the next phase and which cursor case applied (DD-9 H09).

    candidates holds every phase H10 must screen: when the cursor resolved through a bare integer
    the phase the other intCursorMeaning would pick is included as defence in depth.
    """
    empty = {"phase": None, "case": "none", "resolution": "unresolved", "candidates": []}
    try:
        work = facts.get("workflow")
        if not isinstance(work, dict):
            return empty
        phases, status = work["phases"], work["phase_status"]
        meaning = facts["settings"]["intCursorMeaning"]
        idx, how = resolve_cursor(work["phase_cursor"], phases, meaning)
        if idx is None:
            return dict(empty, resolution=how)
        phase, case = _next_at(phases, status, idx)
        candidates = [phase] if phase is not None else []
        if how.startswith("int_"):
            alt_idx = idx - 1 if how == "int_finished_count" else idx + 1
            alt = _next_at(phases, status, alt_idx)[0]
            if alt is not None and alt not in candidates:
                candidates.append(alt)
        if work["phases_malformed"]:
            phase, case = None, "none"
        return {"phase": phase, "case": case, "resolution": how, "candidates": candidates}
    except Exception:  # noqa: BLE001 - totality: no next phase => H09
        return empty


def _under(path, base):
    return path == base or path.startswith(base + "/")


def _clean_path(path):
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.rstrip("/")


def _plan_file_union(facts):
    work = facts["workflow"]
    union = set()
    for phase in work["phases"]:
        if phase_state(work["phase_status"], phase["id"]) == "DONE":
            union.update(_clean_path(f) for f in phase["files"])
    current = next_phase(facts)["phase"]
    if current is None:
        idx = resolve_cursor(work["phase_cursor"], work["phases"], facts["settings"]["intCursorMeaning"])[0]
        current = work["phases"][idx] if idx is not None else None
    if current is not None:
        union.update(_clean_path(f) for f in current["files"])
    union.discard("")
    return union


def commit_blockers(facts):
    """C1-C3 (DD-9b). Kept outside hard_rules; only commit eligibility consults them. Never raises."""
    try:
        dirty = [_clean_path(p) for p in facts["git"].get("dirty_paths", []) if isinstance(p, str)]
        union = _plan_file_union(facts) if isinstance(facts.get("workflow"), dict) else set()
        out = []
        if not dirty:
            out.append("C1_no_dirty_paths")
        if not union:
            out.append("C2_empty_plan_file_union")
        elif any(not any(_under(p, base) for base in union) for p in dirty):
            out.append("C3_dirty_outside_plan")
        return out
    except Exception:  # noqa: BLE001 - totality: fail closed on every blocker
        return ["C1_no_dirty_paths", "C2_empty_plan_file_union"]


# --- text rules (DD-9 H10, H12, H14, H15) ------------------------------------------------------------
OUTWARD_RE = re.compile(
    r"(?i)\b(push|pull request|open (?:a |the )?pr|gh pr|merge|deploy|publish|release|npm publish|"
    r"changeset publish|tag|force)\b")
OPTION_LINE_RE = re.compile(r"(?m)^\s*(?:[-*]\s*)?(?:\(?[A-Da-d1-4][\).:]|Option [A-D1-4]\b)")
CHOICE_RE = re.compile(
    r"(?i)\bwhich (?:one|option|approach|do you)\b|"
    r"\b(?:do you (?:prefer|want)|would you (?:rather|like)).{0,120}\bor\b")
SECRET_TEXT_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]+-----|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,}|xox[baprs]-"
    r"|(?i:secret|token|password|passwd|api[_-]?key|private[_-]?key)[A-Za-z0-9_.-]*\s*=\s*\S")
SECRET_PATH_GLOBS = (".env", ".env.*", "id_rsa*", "*.pem", "*.p12", "*.key")
_QUESTION_RE = re.compile(r"[^.!?\n]*\?")


def has_choice(text):
    """H14: the text asks the user to pick between options."""
    if not isinstance(text, str):
        return False
    return len(OPTION_LINE_RE.findall(text)) >= 2 or bool(CHOICE_RE.search(text))


def outward_question(text):
    """H15: an outward action (push, PR, merge, ...) appears inside a question sentence."""
    if not isinstance(text, str):
        return False
    return any(OUTWARD_RE.search(q) for q in _QUESTION_RE.findall(text))


def has_secret_text(text):
    """H12: credential-shaped text (checked on the last 64 KiB)."""
    return isinstance(text, str) and bool(SECRET_TEXT_RE.search(text[-SECRET_MESSAGE_CAP:]))


def secret_path_hit(path):
    """H12: the basename of a dirty path looks like a credential file."""
    name = os.path.basename(str(path)).lower()
    return any(fnmatch.fnmatchcase(name, glob) for glob in SECRET_PATH_GLOBS)


# --- facts constructor and hard rules (DD-9) ---------------------------------------------------------
_CLEAN_PAYLOAD = {
    "workflow_type": "BUILD", "plan_file": "docs/plans/x.md", "phase_cursor": "P2",
    "phase_status": {"P1": "completed", "P2": "pending"},
    "normalized_phases": [{"phase_id": "P1", "files": ["a.py"]}, {"phase_id": "P2", "files": ["b.py"]}],
}


def base_facts(**kw):
    """Facts constructor for tests and the shell; the defaults hit NO hard rule."""
    facts = {
        "message": "Done.",
        "binding_reason": "single_candidate",
        "permission_mode": "default",
        "stop_hook_active": False,
        "git": {"ok": True, "in_progress_op": None, "unmerged": False, "dirty_paths": [], "head": "0" * 40},
        "session": {"would_continue_since_human": 0, "last_human_ts": None, "relay_pending": False},
        "settings": parse_settings(None, None)[0],
        "workflow": workflow_facts(_CLEAN_PAYLOAD, lambda p: True),
    }
    facts.update(kw)
    return facts


def _msg(facts):
    msg = facts.get("message")
    return msg if isinstance(msg, str) else ""


def _tail(facts):
    return _msg(facts)[-facts["settings"]["tailChars"]:]


def _work(facts):
    work = facts.get("workflow")
    return work if isinstance(work, dict) else None


def _h05(facts, work):
    status = work["phase_status"]
    ids = [p["id"] for p in work["phases"]]
    if any(phase_state(status, pid) in ("IN_PROGRESS", "FAILED") for pid in ids):
        return True
    return bool(non_phase_failures(status, ids))


def _h10(facts):
    cands = next_phase(facts)["candidates"]
    return any(OUTWARD_RE.search(" ".join([c["title"], c["objective"]] + c["checks"])) for c in cands)


def _h11(facts):
    git = facts["git"]
    return not git.get("ok") or bool(git.get("in_progress_op")) or bool(git.get("unmerged"))


def _h12(facts):
    paths = facts["git"].get("dirty_paths", [])
    return has_secret_text(_msg(facts)) or any(secret_path_hit(p) for p in paths)


def _wf_rule(test):
    """Wrap a workflow rule: no workflow data means the rule cannot fire (H01 already covers it)."""
    def rule(facts):
        work = _work(facts)
        return work is not None and bool(test(facts, work))
    return rule


_RULES = (
    ("H00_message_missing", lambda f: not _msg(f).strip()),
    ("H01_no_bound_workflow", lambda f: f.get("binding_reason") not in BOUND_REASONS or _work(f) is None),
    ("H02_not_build_or_no_plan",
     _wf_rule(lambda f, w: w["workflow_type"] != "BUILD" or not w["plan_file"])),
    ("H03_pending_gate", _wf_rule(lambda f, w: w["pending_gate"])),
    ("H04_open_decisions", _wf_rule(lambda f, w: w["open_decisions"] > 0)),
    ("H05_phase_status_not_clean", _wf_rule(_h05)),
    ("H06_circuit_breaker", _wf_rule(lambda f, w: w["circuit_broken"])),
    ("H07_failure_or_revert_event",
     _wf_rule(lambda f, w: any(word in w["last_event"].lower() for word in REVERT_WORDS))),
    ("H08_workflow_terminal", _wf_rule(lambda f, w: w["terminal"])),
    ("H09_no_next_phase", _wf_rule(lambda f, w: next_phase(f)["phase"] is None)),
    ("H10_next_phase_outward", _wf_rule(lambda f, w: _h10(f))),
    ("H11_git_unsafe", _h11),
    ("H12_secret_signal", _h12),
    ("H13_permission_mode_plan", lambda f: f.get("permission_mode") == "plan"),
    ("H14_question_needs_choice", lambda f: has_choice(_tail(f))),
    ("H15_outward_request", lambda f: outward_question(_tail(f))),
)


def hard_rules(facts):
    """All hard-rule hits, in H00..H15 order (DD-9). Pure; commit blockers are NOT consulted.

    A rule that cannot be evaluated (internal error) counts as a hit: fail closed.
    """
    hits = []
    for code, rule in _RULES:
        try:
            hit = rule(facts)
        except Exception:  # noqa: BLE001 - fail closed: an unevaluable rule counts as a hit
            hit = True
        if hit:
            hits.append(code)
    return hits


# --- taxonomy heuristic and verdict combiner (DD-10) -------------------------------------------------
STOP_KINDS = ("phase_done_awaiting_continue", "awaiting_commit", "awaiting_push_or_pr", "asking_decision",
              "blocked_by_error", "work_complete", "other")
HEURISTIC_WINDOW = 600
HEURISTIC_PATTERNS = (
    ("asking_decision", re.compile(r"(?i)\b(?:should|shall) i\b[^?.\n]{0,120}\bor\b")),
    ("blocked_by_error", re.compile(
        r"(?i)\btests? (?:fail|are failing)\b|\b(?:failed|failing|blocked|cannot|unable to)\b|"
        r"\berrors? out\b|\b\w*Error:|\bTraceback\b")),
    ("awaiting_commit", re.compile(r"(?i)\bcommit\b[^.!?\n]*\?")),
    ("phase_done_awaiting_continue", re.compile(
        r"(?i)\b(?:continue|proceed|move on|go ahead|next phase|next step)\b[^.!?\n]*\?")),
    ("work_complete", re.compile(
        r"(?i)\ball done\b|\ball phases (?:are )?(?:complete|done)\b|\bwork is complete\b|\bsummary\b\s*:|"
        r"\bsummary of (?:the )?changes\b")),
)


def heuristic_kind(text):
    """(kind, signals): the stop kind from the last 600 chars; CHOICE/OUTWARD first, then first match (DD-10)."""
    if not isinstance(text, str) or not text.strip():
        return "other", []
    tail = text[-HEURISTIC_WINDOW:]
    if has_choice(tail):
        return "asking_decision", ["choice_re"]
    if outward_question(tail):
        return "awaiting_push_or_pr", ["outward_re"]
    for kind, pattern in HEURISTIC_PATTERNS:
        if pattern.search(tail):
            return kind, [kind + "_re"]
    return "other", []


def _jev_pass(jev, settings):
    """(kind, passed): the Jev kind and whether both thresholds hold; (None, None) when Jev did not answer."""
    if not isinstance(jev, dict) or jev.get("status") != "ok":
        return None, None
    try:
        conf, needs = float(jev["kind_conf"]), float(jev["needs_human"])
    except (KeyError, TypeError, ValueError):
        return None, None
    passed = conf >= settings["jevKindThreshold"] and needs <= settings["jevNeedsHumanMax"]
    return jev.get("kind"), passed


def _verdict_for(kind, blockers):
    if kind == "phase_done_awaiting_continue":
        return "would_continue"
    if kind == "awaiting_commit" and not blockers:
        return "would_commit"
    return "needs_human"


_WOULD_ACTION = {"would_continue": "continue", "would_commit": "commit", "needs_human": "none"}


def decide(rule_hits, commit_blockers, heuristic, jev, settings):
    """Combine rule hits, Jev and the heuristic into a verdict dict (DD-10). Pure; never returns "done".

    heuristic is the heuristic kind string; jev is None or {status, kind, kind_conf, needs_human}.
    act_eligible is a candidate flag only (verdict came from a Jev answer); nothing in Slice 1 consumes it.
    """
    hits = list(rule_hits or [])
    if hits:
        return {"verdict": "needs_human", "verdict_source": "hard_rule", "act_eligible": False,
                "would_action": "none", "reasons": hits}
    jev_kind, passed = _jev_pass(jev, settings)
    if passed is not None:
        verdict = _verdict_for(jev_kind, commit_blockers) if passed else "needs_human"
        source = "jev"
    else:
        verdict, source = _verdict_for(heuristic, commit_blockers), "heuristic"
    return {"verdict": verdict, "verdict_source": source,
            "act_eligible": source == "jev" and verdict != "needs_human",
            "would_action": _WOULD_ACTION[verdict], "reasons": []}


# --- notification text, push relay (DD-12) -----------------------------------------------------------
NOTIFY_MAX_CHARS = 180
PUSH_RELAY_TEMPLATE = (
    "craftflow stop-gate: the user asked to be notified when you need them. "
    "Call the PushNotification tool exactly once with this message: '%s'. "
    "Do nothing else, then end your turn.")
_VERDICT_LABELS = {"needs_human": "needs you", "would_continue": "would continue",
                   "would_commit": "would commit"}
_REASON_RE = re.compile(r"[A-Z][0-9]{1,2}_[a-z0-9_]{1,60}")


def _allowed(value, pattern):
    """The value when it is a string fully matching the allowlist pattern, else empty."""
    if isinstance(value, str) and value.isascii() and pattern.fullmatch(value):
        return value
    return ""


def notify_text(verdict, wf, phase, reasons):
    """One-line ASCII notification text, built ONLY from allowlisted, regex-validated fields (DD-12)."""
    wf_id = _allowed(wf, cc.WF_ID_RE)
    parts = [p for p in (wf_id[-8:], _allowed(phase, cc._PHASE_RE)) if p]
    first = _allowed(reasons[0], _REASON_RE) if isinstance(reasons, (list, tuple)) and reasons else ""
    text = "Craftflow: " + _VERDICT_LABELS.get(verdict, "needs you")
    if parts:
        text += " - " + " ".join(parts)
    if first:
        text += " (" + first + ")"
    return text[:NOTIFY_MAX_CHARS]


def relay_decision(settings, verdict, stop_hook_active, session_state, tail_sha):
    """True when a push relay block should be printed: once per tail, never inside a relay (DD-12, P5)."""
    state = session_state if isinstance(session_state, dict) else {}
    if stop_hook_active or not tail_sha or state.get("relay_pending"):
        return False
    if not isinstance(settings, dict) or settings.get("notify") != "push" or verdict != "needs_human":
        return False
    return state.get("last_notified_tail_sha") != tail_sha


# --- loop guards and session state (DD-9a, DD-13) ----------------------------------------------------
def _count_since_human(facts, session):
    """would_continue_since_human, counted as 0 when a new genuine human turn was seen (DD-13)."""
    if session.get("last_human_ts") != facts.get("last_human_ts"):
        return 0
    count = session.get("would_continue_since_human")
    return count if isinstance(count, int) and not isinstance(count, bool) else 0


def loop_guards(facts, session):
    """Loop-guard tags L1/L2 (DD-9a). Tags only in Slice 1: they never reach hard_rules or decide."""
    try:
        facts = facts if isinstance(facts, dict) else {}
        session = session if isinstance(session, dict) else {}
        count = _count_since_human(facts, session)
        tags = []
        git = facts.get("git") if isinstance(facts.get("git"), dict) else {}
        work = facts.get("workflow") if isinstance(facts.get("workflow"), dict) else {}
        recorded = count > 0 or bool(session.get("relay_pending"))
        unchanged = (git.get("head") == session.get("last_head")
                     and work.get("phase_cursor") == session.get("last_cursor"))
        if facts.get("stop_hook_active") and recorded and unchanged:
            tags.append("L1_no_progress")
        limit = (facts.get("settings") or DEFAULTS).get("maxAutoContinuesPerSession", 5)
        if count >= limit:
            tags.append("L2_budget")
        return tags
    except Exception:  # noqa: BLE001 - tags are advisory in Slice 1: fail open to no tags
        return []


def session_update(state, verdict, head, cursor, tail_sha, relayed, now, last_human_ts=None, notified=False):
    """New session record after a stop (DD-13). Pure: the input record is never mutated."""
    old = state if isinstance(state, dict) else {}
    count = old.get("would_continue_since_human")
    count = count if isinstance(count, int) and not isinstance(count, bool) else 0
    if last_human_ts != old.get("last_human_ts"):
        count = 0
    if verdict == "would_continue":
        count += 1
    sent = bool(relayed or notified)
    return {
        "would_continue_since_human": count,
        "last_human_ts": last_human_ts,
        "last_head": head,
        "last_cursor": cursor,
        "last_notified_tail_sha": tail_sha if sent else old.get("last_notified_tail_sha"),
        "relay_pending": bool(relayed),
        "updated_at": now,
    }


# --- shadow row (DD-14) ------------------------------------------------------------------------------
ROW_KEYS = (
    "schema", "row_kind", "ts", "session_id", "transcript_path", "mode", "mode_tag", "stop_hook_active",
    "permission_mode", "wf", "binding_reason", "phase_cursor", "cursor_case", "cursor_resolution", "rule_hits",
    "loop_guards", "commit_blockers", "jev_text_source", "last_human_ts", "heuristic_kind", "heuristic_verdict",
    "jev_status", "jev_kind", "jev_kind_conf", "jev_needs_human", "jev_latency_ms", "jev_usage", "verdict",
    "verdict_source", "act_eligible", "would_action", "notify", "notify_status", "turn_seconds",
    "turn_seconds_lower_bound", "message_chars", "tail_sha", "hook_ms", "settings_tags")
_ROW_LIST_KEYS = ("rule_hits", "loop_guards", "commit_blockers", "settings_tags")
ROW_VALUE_MAX_CHARS = 200
_ROW_MAX_ITEMS = 40


def _row_clean(value, depth=0):
    """Bound a row value: long strings are cut, containers are shallow and capped (never raw text)."""
    if isinstance(value, str):
        return value[:ROW_VALUE_MAX_CHARS]
    if isinstance(value, (list, tuple)) and depth < 2:
        return [_row_clean(v, depth + 1) for v in list(value)[:_ROW_MAX_ITEMS]]
    if isinstance(value, dict) and depth < 2:
        return {str(k)[:40]: _row_clean(v, depth + 1) for k, v in list(value.items())[:_ROW_MAX_ITEMS]}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return None


def build_row(**fields):
    """A shadow row with exactly ROW_KEYS. Unknown keys (e.g. message text) are dropped, values bounded."""
    row = dict.fromkeys(ROW_KEYS)
    row.update({"schema": 1, "row_kind": "stop", "act_eligible": False, "would_action": "none"})
    row.update({key: [] for key in _ROW_LIST_KEYS})
    for key in ROW_KEYS:
        if key in fields:
            row[key] = _row_clean(fields[key])
    return row
