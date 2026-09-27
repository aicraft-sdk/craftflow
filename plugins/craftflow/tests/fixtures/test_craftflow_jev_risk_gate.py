#!/usr/bin/env python3
"""Tests for craftflow_jev_risk_gate.py.

Run: python3 tests/fixtures/test_craftflow_jev_risk_gate.py
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_risk_gate import (  # noqa: E402
    matches_allowlist,
    _db_drop_match,
    redact_action,
    _relativize_path,
    build_state,
    build_questions,
    decide,
    telemetry_row,
    _append_event,
    _ACTION_TEXT_DOS_BACKSTOP_CHARS,
)

_passes = 0
_errors: list[str] = []


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


# ---------------------------------------------------------------------------
# Allowlist matcher -- positive + negative per category
# ---------------------------------------------------------------------------

RM_RF_POSITIVE = [
    "rm -rf /tmp/scratch",
    "rm -fr /tmp/scratch",
    "rm -r -f /tmp/scratch",
    "rm -f -r /tmp/scratch",
    "rm --recursive --force /tmp/scratch",
    "rm -r --force /tmp/scratch",
    "cd /tmp && rm -rf scratch",          # 2nd subcommand still matches
]
RM_RF_NEGATIVE = [
    "rm -r /tmp/scratch",                  # recursive only
    "rm -f /tmp/scratch",                  # force only
    "rm /tmp/scratch",                     # neither
    "rmdir /tmp/scratch",                  # different command
    "ls -rf /tmp/scratch",                 # not rm at all
]


def test_rm_rf_allowlist_positive_and_negative() -> None:
    positive_ok = all(matches_allowlist("Bash", {"command": c}) == "rm_rf" for c in RM_RF_POSITIVE)
    negative_ok = all(matches_allowlist("Bash", {"command": c}) is None for c in RM_RF_NEGATIVE)
    if positive_ok and negative_ok:
        ok("rm -rf allowlist matches every documented variant, no false positives on partial flags")
    else:
        fail(
            "rm-rf-allowlist",
            f"positive={[matches_allowlist('Bash', {'command': c}) for c in RM_RF_POSITIVE]!r} "
            f"negative={[matches_allowlist('Bash', {'command': c}) for c in RM_RF_NEGATIVE]!r}",
        )


FORCE_PUSH_POSITIVE = [
    "git push --force",
    "git push -f",
    "git push --force-with-lease",
    "git push origin main -f",
    "git push --force-with-lease=origin/main",
]
FORCE_PUSH_NEGATIVE = [
    "git push",
    "git push origin main",
    "git pull --force",             # not push
    "git push --follow-tags",       # unrelated flag
]


def test_force_push_allowlist_positive_and_negative() -> None:
    positive_ok = all(matches_allowlist("Bash", {"command": c}) == "force_push" for c in FORCE_PUSH_POSITIVE)
    negative_ok = all(matches_allowlist("Bash", {"command": c}) is None for c in FORCE_PUSH_NEGATIVE)
    if positive_ok and negative_ok:
        ok("git push --force allowlist matches every documented variant, no false positives")
    else:
        fail(
            "force-push-allowlist",
            f"positive={[matches_allowlist('Bash', {'command': c}) for c in FORCE_PUSH_POSITIVE]!r} "
            f"negative={[matches_allowlist('Bash', {'command': c}) for c in FORCE_PUSH_NEGATIVE]!r}",
        )


DB_DROP_POSITIVE = [
    "psql -c 'DROP TABLE users;'",
    "psql -c 'drop database mydb'",
    "rails db:migrate:down",  # contains "migrate down"-shaped text? see note below
    "npm run db:rollback",
]
DB_DROP_NEGATIVE = [
    "psql -c 'SELECT * FROM users'",
    "echo 'do not drop the ball'",
]


def test_db_drop_allowlist_positive_and_negative() -> None:
    # NOTE: "rails db:migrate:down" is deliberately handled by the "migrate down"-shaped
    # phrase requirement in the design -- if the literal phrase match is too narrow to catch
    # this real-world shape during implementation, adjust the pattern (not the test intent)
    # to also cover `migrate:down`/`migrate down` colon-or-space variants; keep DB_DROP_POSITIVE
    # green either way, and add the extra shape as its own explicit case rather than loosening
    # the assertion.
    positive_ok = all(matches_allowlist("Bash", {"command": c}) == "db_drop" for c in DB_DROP_POSITIVE)
    negative_ok = all(matches_allowlist("Bash", {"command": c}) is None for c in DB_DROP_NEGATIVE)
    if positive_ok and negative_ok:
        ok("DB-drop/migration-down allowlist matches documented shapes, no false positives")
    else:
        fail(
            "db-drop-allowlist",
            f"positive={[matches_allowlist('Bash', {'command': c}) for c in DB_DROP_POSITIVE]!r} "
            f"negative={[matches_allowlist('Bash', {'command': c}) for c in DB_DROP_NEGATIVE]!r}",
        )


SECRET_PATH_POSITIVE = [".env", ".env.local", "config/id_rsa", "certs/server.pem", "src/secret-config.json", "app/credentials.yaml"]
SECRET_PATH_NEGATIVE = ["README.md", "src/index.ts", "docs/env-setup.md"]


def test_secret_path_allowlist_positive_and_negative_for_write_and_edit() -> None:
    for tool_name in ("Write", "Edit"):
        positive_ok = all(matches_allowlist(tool_name, {"file_path": p}) == "secret_path" for p in SECRET_PATH_POSITIVE)
        negative_ok = all(matches_allowlist(tool_name, {"file_path": p}) is None for p in SECRET_PATH_NEGATIVE)
        if positive_ok and negative_ok:
            ok(f"secret-path allowlist matches documented shapes for {tool_name}, no false positives")
        else:
            fail(
                f"secret-path-allowlist-{tool_name}",
                f"positive={[matches_allowlist(tool_name, {'file_path': p}) for p in SECRET_PATH_POSITIVE]!r} "
                f"negative={[matches_allowlist(tool_name, {'file_path': p}) for p in SECRET_PATH_NEGATIVE]!r}",
            )


def test_read_and_other_tool_names_never_match() -> None:
    if (
        matches_allowlist("Read", {"file_path": ".env"}) is None
        and matches_allowlist("WebFetch", {"url": "https://example.com"}) is None
        and matches_allowlist("Bash", {"command": "cat .env"}) is None
    ):
        ok("only Bash/Write/Edit are ever eligible; cat/Read/WebFetch never match")
    else:
        fail("other-tools-never-match", "a non-Bash/Write/Edit call or a benign read matched")


def test_content_and_new_string_never_inspected() -> None:
    # Property P1: even if content/new_string/old_string CONTAIN allowlist-shaped text (e.g. a
    # secret value that happens to say "rm -rf"), matches_allowlist must decide purely from
    # command/file_path -- never from content/new_string/old_string.
    canary = "CANARY_SECRET_VALUE_rm_-rf_force_push_DROP_TABLE"
    write_result = matches_allowlist("Write", {"file_path": "README.md", "content": canary})
    edit_result = matches_allowlist("Edit", {"file_path": "README.md", "old_string": canary, "new_string": canary})
    if write_result is None and edit_result is None:
        ok("matches_allowlist never inspects content/old_string/new_string, only file_path")
    else:
        fail("content-never-inspected", f"write_result={write_result!r} edit_result={edit_result!r}")


def test_malformed_tool_input_never_raises() -> None:
    cases = [
        ("Bash", {}), ("Bash", {"command": None}), ("Bash", {"command": 123}),
        ("Write", {}), ("Write", {"file_path": None}), ("Edit", {"file_path": 123}),
        (None, {}), ("Bash", None),
    ]
    try:
        results = [matches_allowlist(tool_name, tool_input) for tool_name, tool_input in cases]
    except Exception as exc:
        fail("malformed-tool-input-never-raises", f"raised {type(exc).__name__}: {exc}")
        return
    if all(r is None for r in results):
        ok("matches_allowlist never raises on malformed/missing tool_input, always returns None")
    else:
        fail("malformed-tool-input-never-raises", f"results={results!r}")


# ---------------------------------------------------------------------------
# Pure builders -- build_state/build_questions/decide/telemetry_row/_append_event
# ---------------------------------------------------------------------------

def test_build_state_caps_action_text_and_preserves_category() -> None:
    state = build_state("Bash", "rm_rf", "rm -rf /very/long/path" * 10, api_key="", max_chars=20)
    if state["category"] == "rm_rf" and state["tool_name"] == "Bash" and len(state["action_text"]) == 20 and state["truncated"] is True:
        ok("build_state caps action_text and preserves category/tool_name/truncated flag")
    else:
        fail("build-state-caps", f"state={state!r}")


def test_build_state_no_truncation_flag_when_under_budget() -> None:
    state = build_state("Bash", "force_push", "git push -f", api_key="", max_chars=4000)
    if state["truncated"] is False and state["action_text"] == "git push -f":
        ok("build_state does not flag truncation when text fits under max_chars")
    else:
        fail("build-state-no-truncate", f"state={state!r}")


# ---------------------------------------------------------------------------
# redact_action() -- credential masking before egress (bakeoff synthesis
# addition, DD-8). The db_drop category's real-world shape routinely carries
# an inline credential inside the command string itself (connection strings,
# PGPASSWORD=..., --password=...) -- this is the one field this feature sends
# to a third party, so it must never leave the machine unredacted.
# ---------------------------------------------------------------------------

def test_redact_action_masks_url_userinfo_env_assignment_and_flag_value() -> None:
    command = 'PGPASSWORD=hunter2 psql postgres://u:pw@h/db -c "DROP TABLE t" --password=zz KEYSENTINEL'
    redacted, was_redacted = redact_action(command, "KEYSENTINEL")
    checks = (
        was_redacted is True,
        "hunter2" not in redacted,
        ":pw@" not in redacted,
        "=zz" not in redacted,
        "KEYSENTINEL" not in redacted,
        "DROP TABLE t" in redacted,        # non-secret content survives
        "PGPASSWORD=***" in redacted,
        "postgres://u:***@" in redacted,
    )
    if all(checks):
        ok("redact_action masks URL userinfo, credential-shaped env assignment, --password flag, and the literal API key")
    else:
        fail("redact-action-masks", f"redacted={redacted!r} checks={checks!r}")


def test_redact_action_leaves_non_sensitive_commands_unchanged() -> None:
    redacted, was_redacted = redact_action("rm -rf /tmp/scratch", "k")
    if redacted == "rm -rf /tmp/scratch" and was_redacted is False:
        ok("redact_action is a no-op (and reports no-redaction) for commands with nothing to mask")
    else:
        fail("redact-action-noop", f"redacted={redacted!r} was_redacted={was_redacted!r}")


def test_redact_action_blank_api_key_never_replaces_empty_string() -> None:
    redacted, _was_redacted = redact_action("FOO=bar rm -rf x", "")
    if redacted.startswith("FOO=bar") or "FOO=***" in redacted:
        # FOO is not a credential-shaped name -- assignment redaction must not fire on it either.
        if "FOO=bar" in redacted:
            ok("redact_action does not touch non-credential-shaped assignments, and a blank api_key never matches")
        else:
            fail("redact-action-blank-key", f"redacted={redacted!r}")
    else:
        fail("redact-action-blank-key", f"redacted={redacted!r}")


def test_redact_action_masks_url_userinfo_with_embedded_at_and_slash_in_password() -> None:
    # REM-FIX (code-reviewer CRITICAL #1): the password char class used to exclude '@' and '/',
    # so a password containing either character leaked (partially or entirely uncensored).
    partial_leak = "psql postgres://dbuser:p@ss@dbhost:5432/prod --command 'drop table users'"
    total_bypass = "psql postgres://dbuser:pa/ss@dbhost:5432/prod -c 'drop table users'"
    redacted1, was_redacted1 = redact_action(partial_leak, "")
    redacted2, was_redacted2 = redact_action(total_bypass, "")
    checks = (
        was_redacted1 is True,
        was_redacted2 is True,
        "postgres://dbuser:***@dbhost:5432/prod" in redacted1,
        "postgres://dbuser:***@dbhost:5432/prod" in redacted2,
        "p@ss" not in redacted1,
        "pa/ss" not in redacted2,
        "ss@dbhost" not in redacted1,
        "ss@dbhost" not in redacted2,
    )
    if all(checks):
        ok("redact_action masks passwords containing embedded @ and / (partial-leak and total-bypass PoCs both closed)")
    else:
        fail(
            "redact-action-embedded-at-slash",
            f"redacted1={redacted1!r} was_redacted1={was_redacted1!r} "
            f"redacted2={redacted2!r} was_redacted2={was_redacted2!r} checks={checks!r}",
        )


def test_redact_action_masks_broadened_credential_name_shapes_and_concatenated_flags() -> None:
    # REM-FIX (silent-failure-hunter CRITICAL #2): the closed name allowlist missed common real
    # credential shapes (DB_PASS, *_KEY, *_PAT), and the single-dash concatenated mysql/psql
    # `-p<value>` flag form was not recognized by the flag matcher at all.
    cases = [
        ("DB_PASS=hunter2secret rm -rf /tmp", "hunter2secret"),
        ("OPENAI_KEY=sk-livesecretvalue rm -rf /tmp", "sk-livesecretvalue"),
        ("AWS_ACCESS_KEY_ID=AKIAFAKEEXAMPLE rm -rf /tmp", "AKIAFAKEEXAMPLE"),
        ('GH_PAT=ghp_faketoken123 git push --force', "ghp_faketoken123"),
        ('mysql -uroot -pS3cretPass1 -e "DROP DATABASE prod"', "S3cretPass1"),
    ]
    details = []
    all_ok = True
    for command, secret in cases:
        redacted, was_redacted = redact_action(command, "")
        passed = was_redacted is True and secret not in redacted
        all_ok = all_ok and passed
        details.append((command, redacted, was_redacted, passed))
    if all_ok:
        ok("redact_action masks DB_PASS/OPENAI_KEY/AWS_ACCESS_KEY_ID/GH_PAT names and mysql -p<value> concatenated flag")
    else:
        fail("redact-action-broadened-names-and-flags", f"details={details!r}")


def test_matches_allowlist_applies_hard_input_cap_before_pattern_matching() -> None:
    # REM-FIX (silent-failure-hunter MEDIUM #1, matches_allowlist side): unlike
    # test_matcher_and_redactor_are_bounded_time_on_pathological_input (which pre-slices samples
    # to command[:20000] before calling the pure functions), this test calls matches_allowlist
    # DIRECTLY with full, unsliced adversarial input. A db_drop-shaped phrase placed only beyond
    # the cap boundary must not be detected -- proving the cap is enforced by matches_allowlist
    # itself, not merely assumed from bounded regex quantifiers.
    command_with_late_db_drop = ("a" * 25_000) + " DROP DATABASE prod"
    category = matches_allowlist("Bash", {"command": command_with_late_db_drop})
    if category is None:
        ok("matches_allowlist caps raw command input -- a pattern occurring only beyond the cap boundary is not matched")
    else:
        fail(
            "matches-allowlist-hard-input-cap",
            f"category={category!r} (expected None -- db_drop phrase occurs beyond the 20000-char cap)",
        )


# ---------------------------------------------------------------------------
# REM-FIX cycle 6 (architectural redesign; origin: code-reviewer + silent-failure-hunter, 6
# rounds of findings). Cycles 1-5 each fixed a leak at one fixed cut point only to reopen the
# same bug class at a new one (see craftflow_jev_risk_gate.py's build_state() docstring for the
# full audit trail). The tests below that pinned the now-removed cap/margin/window boundary
# values (`_MAX_REDACT_ACTION_INPUT_CHARS`, `_REDACT_ACTION_INPUT_MARGIN_CHARS`,
# `_REDACT_ACTION_INPUT_WINDOW_CHARS`, `_REDACT_ACTION_SAFETY_CAP`) are replaced by the tests in
# this section, which validate the new, simpler, position-independent invariant instead: a
# credential ANYWHERE in a realistic-to-large-but-still-far-below-the-backstop-sized input is
# always fully redacted, because there is no positional cut point left for it to straddle.
# ---------------------------------------------------------------------------


def _url_userinfo_command(offset: int, marker_suffix: str) -> tuple[str, str]:
    password = "supersecretpassw0rd-1234567890"
    command = ("a" * offset) + "https://admin:" + password + "@example.com/api AFTER_" + marker_suffix
    return command, password


def _assignment_command(offset: int, marker_suffix: str) -> tuple[str, str]:
    secret = "S" * 40
    # A space (not another word char) separates the "a" padding from the credential-shaped
    # assignment -- _ASSIGNMENT's `\b` requires an actual word boundary before NAME, which a
    # direct a-to-letter concatenation (no separator) would not provide.
    command = ("a" * offset) + f' MY_TOKEN="{secret}" --done AFTER_{marker_suffix}'
    return command, secret


def _secret_flag_command(offset: int, marker_suffix: str) -> tuple[str, str]:
    secret = "S" * 40
    command = ("a" * offset) + f'--password "{secret}" AFTER_{marker_suffix}'
    return command, secret


def test_build_state_redacts_credentials_position_independently_across_offsets() -> None:
    # Replaces cycles 2-5's boundary-pinning tests (straddling the pre-redaction cap, the 100k
    # cap, and the margin-extended window edge). Under the new architecture there is no fixed
    # cut point before redact_action() runs (see build_state()'s docstring) -- so a credential of
    # any of the three recognized shapes must be fully redacted regardless of where in the
    # command it starts, as long as the whole command stays under the DoS-only backstop cap.
    # Offsets deliberately include the exact positions where cycles 2 ("20,000"), 3 ("100,000"),
    # and 5 ("104,500" margin edge) each drew their now-removed fixed cut points, plus much larger
    # offsets (500,000; 2,000,000) that are still comfortably below the new backstop cap.
    offsets = [0, 19_990, 99_995, 104_495, 500_000, 2_000_000]
    builders = (
        ("url_userinfo", _url_userinfo_command, "admin:***@"),
        ("assignment", _assignment_command, "MY_TOKEN=***"),
        ("secret_flag", _secret_flag_command, "--password ***"),
    )
    failures = []
    for label, builder, masked_marker in builders:
        for offset in offsets:
            marker_suffix = f"{label}_{offset}"
            command, secret = builder(offset, marker_suffix)
            state = build_state("Bash", "db_drop", command, api_key="", max_chars=6_000_000)
            text = state["action_text"]
            if secret in text or masked_marker not in text or f"AFTER_{marker_suffix}" not in text:
                failures.append((label, offset, secret in text, masked_marker in text, f"AFTER_{marker_suffix}" in text))
    if not failures:
        ok(
            "build_state fully redacts url_userinfo/assignment/secret_flag credentials at every "
            "tested offset (0 through 2,000,000) -- no positional cut point exists to straddle"
        )
    else:
        fail("build-state-position-independent-redaction", f"failures={failures!r}")


def test_build_state_credential_at_large_arbitrary_offset_is_fully_redacted() -> None:
    # Explicit proof of the class this redesign is meant to make trivially safe: a
    # credential-shaped value positioned at an arbitrary large offset (500,000 chars) into the
    # command is still fully redacted.
    offset = 500_000
    password = "supersecretpassw0rd-arbitrary-offset"
    command = ("x" * offset) + "https://svc:" + password + "@internal.example.com/api --done"
    state = build_state("Bash", "db_drop", command, api_key="", max_chars=6_000_000)
    checks = (
        password not in state["action_text"],
        "svc:***@" in state["action_text"],
        state["redacted"] is True,
        "--done" in state["action_text"],
    )
    if all(checks):
        ok("a credential positioned 500,000 chars into the command is still fully redacted")
    else:
        fail("credential-at-large-offset", f"checks={checks!r}")


def test_build_state_backstop_cap_is_dos_only_sized_far_beyond_any_credential() -> None:
    # Pins the design intent of the replacement constant: a low-single-digit-megabyte DoS-only
    # backstop, not a "big enough to contain any credential" cap (that framing is exactly what
    # made cycles 3-5's caps/margins straddle-able).
    largest_plausible_credential_value = 4096  # each pattern's own bounded value-capture quantifier
    if (
        2_000_000 <= _ACTION_TEXT_DOS_BACKSTOP_CHARS <= 5_000_000
        and _ACTION_TEXT_DOS_BACKSTOP_CHARS >= largest_plausible_credential_value * 1000
    ):
        ok(
            "the DoS-only backstop cap is in the low-single-digit-megabyte range and orders of "
            "magnitude larger than any plausible credential value"
        )
    else:
        fail(
            "backstop-cap-sizing",
            f"_ACTION_TEXT_DOS_BACKSTOP_CHARS={_ACTION_TEXT_DOS_BACKSTOP_CHARS!r}",
        )


def test_build_state_content_beyond_backstop_cap_is_intentionally_dropped() -> None:
    # The backstop cap still exists (it must, to bound compute time on a genuinely pathological
    # multi-MB input) -- content genuinely beyond it is dropped. That drop is an accepted,
    # intentional compute-cost bound, not a re-introduction of the straddling-cut-point bug: no
    # real-world command places a credential 5,000,000+ characters in specifically to land on
    # this boundary (see _ACTION_TEXT_DOS_BACKSTOP_CHARS's own sizing rationale).
    marker = "MARKER_BEYOND_BACKSTOP_CAP"
    action_text = ("a" * (_ACTION_TEXT_DOS_BACKSTOP_CHARS + 500)) + marker
    state = build_state("Bash", "rm_rf", action_text, api_key="", max_chars=10_000_000)
    if marker not in state["action_text"] and state["truncated"] is True:
        ok(
            "content genuinely beyond the DoS-only backstop cap is dropped and flagged "
            "truncated -- the intended compute-cost bound, not a bug"
        )
    else:
        fail(
            "content-beyond-backstop-cap",
            f"marker_present={marker in state['action_text']!r} truncated={state['truncated']!r}",
        )


# ---------------------------------------------------------------------------
# REM-FIX cycle 5 (doubt-verifier CRITICAL, corroborated 3x independently by code-reviewer +
# silent-failure-hunter + doubt-verifier). Root cause: _SECRET_FLAG's separator group was
# `(?:=|\s+)` -- the `\s+` alternative was UNBOUNDED. Fixed by bounding `_SECRET_FLAG`'s separator
# to `\s{1,32}` -- this fix is orthogonal to the cycle 3-5 cap/margin/window architecture (which
# cycle 6 removes entirely): it closes a real hole in the regex itself, independent of any
# positional cut point. The test below pins that fix on its own terms, without constructing its
# target offset relative to any now-removed window/margin constant.
# ---------------------------------------------------------------------------


def test_redact_action_large_separator_gap_never_produces_false_safe_leak() -> None:
    # A large, arbitrary whitespace gap between "--password" and its quoted value must never let
    # _SECRET_FLAG bridge the two (separator bounded to `\s{1,32}`) in a way that produces a
    # "false-safe" leak -- build_state() must never claim `redacted=True` while the actual secret
    # is still present in cleartext. Unlike cycle 5's original regression test, this gap and flag
    # position are arbitrary literals, not derived from any removed cap/margin/window constant --
    # the property being pinned (bounded separator => no bridging => no false-safe leak) does not
    # depend on where any cut point used to be.
    secret = "S" * 40
    gap = " " * 50_000
    command = ("A" * 200_000) + "--password" + gap + '"' + secret + '"' + " --done"
    state = build_state("Bash", "db_drop", command, api_key=None, max_chars=2_000_000)
    false_safe_leak = state["redacted"] is True and secret in state["action_text"]
    if not false_safe_leak:
        ok(
            "redact_action never claims redacted=True while a secret still leaks, even with a "
            "large (50,000-space) arbitrary gap between a flag and its value -- the bounded "
            "separator (\\s{1,32}) prevents the bridge regardless of any cap/margin/window"
        )
    else:
        fail(
            "secret-flag-large-separator-gap",
            f"redacted={state['redacted']!r} secret_present={secret in state['action_text']!r}",
        )


def test_relativize_path_never_leaks_absolute_prefix() -> None:
    checks = (
        _relativize_path("/p/src/.env", "/p") == "src/.env",
        _relativize_path("/etc/ssl/k.pem", "/p") == "k.pem",   # outside cwd -> basename only
        _relativize_path("/p/.env", None) == ".env",            # missing cwd -> basename
        _relativize_path("", "/p") == "",
    )
    if all(checks):
        ok("_relativize_path returns a cwd-relative path or a bare basename, never an absolute path")
    else:
        fail("relativize-path", f"checks={checks!r}")


def test_build_state_calls_redact_action_before_capping() -> None:
    command = "PGPASSWORD=hunter2 psql postgres://u:pw@h/db -c 'DROP TABLE t'"
    state = build_state("Bash", "db_drop", command, api_key="", max_chars=4000)
    if "hunter2" not in state["action_text"] and ":pw@" not in state["action_text"] and state["redacted"] is True:
        ok("build_state redacts credential-shaped command text before it ever reaches action_text")
    else:
        fail("build-state-redacts", f"state={state!r}")


def test_matcher_and_redactor_are_bounded_time_on_pathological_input() -> None:
    # Bakeoff synthesis addition: the DB-drop matcher and redact_action() regexes both run on
    # EVERY Bash command that reaches build_state (allowlist-matched calls only, but still on
    # attacker-influenced input) -- a pathological input must not trigger catastrophic regex
    # backtracking. Bounded quantifiers were used throughout precisely to make this true; this
    # test pins that property instead of assuming it.
    #
    # NOTE: every sample below is pre-sliced to `command[:20000]` before calling
    # `_db_drop_match`/`redact_action` directly -- this test proves the regexes themselves have
    # no catastrophic-backtracking blowup at realistic sizes. See
    # test_build_state_bounds_compute_time_on_unsliced_multi_mb_command below for the un-sliced,
    # build_state()-direct property (bounded by `_ACTION_TEXT_DOS_BACKSTOP_CHARS`) this test's own
    # pre-slice cannot demonstrate.
    pathological_inputs = [
        "a" * 200_000,
        " -" * 50_000,
        "=" * 20_000,
        "x://" + "a" * 20_000 + ":" + "b" * 20_000 + "@",
        # silent-failure-hunter re-hunt LOW: a -p/-u-shaped adversarial case, pinning
        # _SHORT_FLAG_CONCAT's bounded-time property (previously only verified manually).
        "-p" + "a" * 200_000,
    ]
    started = time.monotonic()
    for command in pathological_inputs:
        _db_drop_match(command[:20000])
        redact_action(command[:20000], "k")
    elapsed = time.monotonic() - started
    if elapsed < 2.0:
        ok(f"matcher + redact_action finish in {elapsed:.3f}s total on 4 pathological inputs (< 2.0s budget)")
    else:
        fail("matcher-redactor-bounded-time", f"elapsed={elapsed:.3f}s (budget 2.0s)")


def test_build_state_bounds_compute_time_on_unsliced_multi_mb_command() -> None:
    # A multi-MB Bash command (heredoc, base64 blob) must not make this PreToolUse hook's regex
    # passes run over genuinely unbounded, attacker-influenceable text -- that would stall this
    # synchronous PreToolUse call. `_ACTION_TEXT_DOS_BACKSTOP_CHARS` (applied to the raw input
    # inside build_state(), BEFORE redact_action() runs) bounds this: at ~0.01s/MB, a 5,000,000
    # char cap costs ~0.05s worst case, comfortably inside this test's 1.0s budget even for a
    # 20MB genuinely un-sliced input.
    #
    # Unlike test_matcher_and_redactor_are_bounded_time_on_pathological_input above (which
    # pre-slices every sample to command[:20000] before calling the pure functions directly --
    # a citation that is misleading as proof of THIS property, since it never exercises the real
    # unbounded build_state() call path), this test calls build_state() DIRECTLY with a genuinely
    # un-sliced 20MB command and pins a tight time budget that only the backstop cap ahead of
    # redact_action() can satisfy.
    huge_command = "rm -rf /tmp/x " + ("a" * (20 * 1024 * 1024))
    started = time.monotonic()
    state = build_state("Bash", "rm_rf", huge_command, api_key="", max_chars=4000)
    elapsed = time.monotonic() - started
    if elapsed < 1.0 and isinstance(state["action_text"], str):
        ok(
            f"build_state bounds compute time to {elapsed:.3f}s on a genuinely un-sliced 20MB "
            "command (< 1.0s budget) -- redact_action()'s input, not just its output, is bounded"
        )
    else:
        fail(
            "build-state-bounded-time-unsliced-multi-mb",
            f"elapsed={elapsed:.3f}s (budget 1.0s) action_text_type={type(state.get('action_text'))!r}",
        )


def test_build_questions_shape() -> None:
    q = build_questions()
    if q["risk"]["type"] == "choice" and set(q["risk"]["criteria"]) == {"risky", "not_risky"}:
        ok("build_questions returns a single risk choice question")
    else:
        fail("build-questions-shape", f"q={q!r}")


def test_decide_logs_well_formed_answer_regardless_of_mode() -> None:
    audit = decide("audit", {"choice": "not_risky", "confidence": 0.4})
    advise = decide("advise", {"choice": "risky", "confidence": 0.99})
    if audit == ("logged", "not_risky", 0.4) and advise == ("logged", "risky", 0.99):
        ok("decide() logs a well-formed answer identically for audit and advise -- no apply state exists")
    else:
        fail("decide-logs-both-modes", f"audit={audit!r} advise={advise!r}")


def test_decide_rejects_malformed_answer_as_no_decision() -> None:
    cases = [
        decide("audit", None),
        decide("audit", {}),
        decide("audit", {"choice": "maybe", "confidence": 0.9}),
        decide("audit", {"choice": "risky", "confidence": "high"}),
        decide("audit", {"choice": "risky", "confidence": True}),
        decide("audit", {"choice": "risky", "confidence": 1.5}),
        decide("audit", {"choice": "risky", "confidence": float("nan")}),
    ]
    if all(c == ("no_decision", None, None) for c in cases):
        ok("decide() rejects every malformed answer shape as no_decision")
    else:
        fail("decide-rejects-malformed", f"cases={cases!r}")


def test_telemetry_row_shape_never_carries_raw_command_or_path() -> None:
    row = telemetry_row(
        decision="logged", choice="risky", confidence=0.8,
        result={"model": "jev-latest", "latency_ms": 90, "cache_hit": False, "usage": {"input_tokens": 3}},
        mode="audit", model="jev-latest", workflow_uuid="wf-test-1",
        tool_name="Bash", category="rm_rf",
    )
    checks = (
        row["feature"] == "risk_gate",
        row["heuristic_result"] == "risky",
        row["agree"] is True,
        row["answers"] == {"choice": "risky", "confidence": 0.8},
        row["decision"] == "logged",
        row["workflow_uuid"] == "wf-test-1",
        row["tool_name"] == "Bash",
        row["category"] == "rm_rf",
        row["advise_supported"] is False,
        "action_text" not in json.dumps(row),
        "command" not in row,
        "file_path" not in row,
    )
    if all(checks):
        ok("telemetry_row has the right shape, agrees with heuristic, never carries raw command/path")
    else:
        fail("telemetry-row-shape", f"row={row!r} checks={checks!r}")


def test_telemetry_row_not_risky_disagrees_with_heuristic() -> None:
    row = telemetry_row(
        decision="logged", choice="not_risky", confidence=0.9, result=None,
        mode="audit", model="jev-latest", workflow_uuid=None, tool_name="Write", category="secret_path",
    )
    if row["agree"] is False:
        ok("a not_risky answer disagrees with the constant risky heuristic baseline")
    else:
        fail("telemetry-row-disagree", f"row={row!r}")


def test_telemetry_row_no_decision_disagrees_with_heuristic() -> None:
    row = telemetry_row(
        decision="no_decision", choice=None, confidence=None, result=None,
        mode="audit", model="jev-latest", workflow_uuid=None, tool_name="Bash", category="db_drop",
    )
    if row["agree"] is False and row["answers"] == {"choice": None, "confidence": None}:
        ok("no_decision rows never count as agreement")
    else:
        fail("telemetry-row-no-decision", f"row={row!r}")


def test_append_event_returns_true_on_success_and_false_on_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ok_path = Path(tmp) / "events.jsonl"
        succeeded = _append_event(ok_path, {"a": 1})
        blocked_parent = Path(tmp) / "not_a_dir"
        blocked_parent.write_text("x")
        failed = _append_event(blocked_parent / "events.jsonl", {"a": 1})
        if succeeded is True and ok_path.read_text().strip() and failed is False:
            ok("_append_event returns True on success, False on failure, never raises")
        else:
            fail("append-event-bool", f"succeeded={succeeded!r} failed={failed!r}")


def main_tests() -> int:
    test_rm_rf_allowlist_positive_and_negative()
    test_force_push_allowlist_positive_and_negative()
    test_db_drop_allowlist_positive_and_negative()
    test_secret_path_allowlist_positive_and_negative_for_write_and_edit()
    test_read_and_other_tool_names_never_match()
    test_content_and_new_string_never_inspected()
    test_malformed_tool_input_never_raises()
    test_build_state_caps_action_text_and_preserves_category()
    test_build_state_no_truncation_flag_when_under_budget()
    test_redact_action_masks_url_userinfo_env_assignment_and_flag_value()
    test_redact_action_leaves_non_sensitive_commands_unchanged()
    test_redact_action_blank_api_key_never_replaces_empty_string()
    test_redact_action_masks_url_userinfo_with_embedded_at_and_slash_in_password()
    test_redact_action_masks_broadened_credential_name_shapes_and_concatenated_flags()
    test_build_state_redacts_credentials_position_independently_across_offsets()
    test_build_state_credential_at_large_arbitrary_offset_is_fully_redacted()
    test_build_state_backstop_cap_is_dos_only_sized_far_beyond_any_credential()
    test_build_state_content_beyond_backstop_cap_is_intentionally_dropped()
    test_redact_action_large_separator_gap_never_produces_false_safe_leak()
    test_matches_allowlist_applies_hard_input_cap_before_pattern_matching()
    test_relativize_path_never_leaks_absolute_prefix()
    test_build_state_calls_redact_action_before_capping()
    test_matcher_and_redactor_are_bounded_time_on_pathological_input()
    test_build_state_bounds_compute_time_on_unsliced_multi_mb_command()
    test_build_questions_shape()
    test_decide_logs_well_formed_answer_regardless_of_mode()
    test_decide_rejects_malformed_answer_as_no_decision()
    test_telemetry_row_shape_never_carries_raw_command_or_path()
    test_telemetry_row_not_risky_disagrees_with_heuristic()
    test_telemetry_row_no_decision_disagrees_with_heuristic()
    test_append_event_returns_true_on_success_and_false_on_failure()

    print(f"\n{_passes} passed, {len(_errors)} failed")
    if _errors:
        for e in _errors:
            print(e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main_tests())
