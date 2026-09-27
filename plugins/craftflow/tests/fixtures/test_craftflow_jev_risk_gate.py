#!/usr/bin/env python3
"""Tests for craftflow_jev_risk_gate.py.

Run: python3 tests/fixtures/test_craftflow_jev_risk_gate.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_risk_gate import (  # noqa: E402
    matches_allowlist,
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


def main_tests() -> int:
    test_rm_rf_allowlist_positive_and_negative()
    test_force_push_allowlist_positive_and_negative()
    test_db_drop_allowlist_positive_and_negative()
    test_secret_path_allowlist_positive_and_negative_for_write_and_edit()
    test_read_and_other_tool_names_never_match()
    test_content_and_new_string_never_inspected()
    test_malformed_tool_input_never_raises()

    print(f"\n{_passes} passed, {len(_errors)} failed")
    if _errors:
        for e in _errors:
            print(e)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main_tests())
