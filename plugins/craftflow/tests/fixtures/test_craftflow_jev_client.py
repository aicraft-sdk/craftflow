#!/usr/bin/env python3
"""Tests for craftflow_jev_client.py.

Run: python3 tests/fixtures/test_craftflow_jev_client.py

All network I/O is mocked via mock.patch("craftflow_jev_client.urllib.request.urlopen").
The client must never touch the real network in these tests.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import time
import urllib.error
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from craftflow_jev_client import call, ENDPOINT, RETRY_STATUSES  # noqa: E402

_passes = 0
_errors: list[str] = []

SENTINEL_KEY = "k-123"


def ok(name: str) -> None:
    global _passes
    _passes += 1
    print(f"  PASS: {name}")


def fail(name: str, reason: str) -> None:
    _errors.append(f"FAIL [{name}]: {reason}")
    print(f"  FAIL: {name}: {reason}")


class _FakeResponse:
    """Minimal context-manager stand-in for http.client.HTTPResponse."""

    def __init__(self, status: int, body_bytes: bytes) -> None:
        self.status = status
        self._body = body_bytes

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def fake_response(status: int, body_bytes: bytes) -> _FakeResponse:
    return _FakeResponse(status, body_bytes)


def _http_error(code: int, msg: str = "error") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(ENDPOINT, code, msg, None, None)


class _Clock:
    """Shared mutable monotonic clock so fake urlopen/sleep can advance time."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, dt: float) -> None:
        self.now += dt


def test_success_returns_answers_and_usage_and_sends_bearer_header() -> None:
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        body = json.dumps({"answers": {"workflow": "BUILD"}, "usage": {"tokens": 10}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen):
        result = call(
            {"prompt": "hi"},
            {"workflow": {"criteria": {}}},
            api_key=SENTINEL_KEY,
            model="jev-1.12",
            timeout=2.5,
            cache_dir=None,
        )

    req = captured.get("req")
    body = captured.get("body", {})
    if (
        result is not None
        and result["answers"] == {"workflow": "BUILD"}
        and result["usage"] == {"tokens": 10}
        and req is not None
        and req.get_header("Authorization") == f"Bearer {SENTINEL_KEY}"
        and req.full_url == ENDPOINT
        and set(body) == {"state", "model", "questions"}
    ):
        ok("success returns answers/usage and sends Bearer header")
    else:
        fail("success-bearer-header", f"result={result!r} req={req!r} body={body!r}")


def test_401_returns_none_without_retry_and_logs_status_only() -> None:
    calls: list = []
    logged: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise _http_error(401, "Unauthorized: bad key k-123")

    def fake_log(name, payload):
        logged.append((name, payload))

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen):
        result = call(
            {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log
        )

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and len(calls) == 1
        and logged
        and logged[0][1].get("status") == 401
        and SENTINEL_KEY not in payload_dump
    ):
        ok("401 returns None without retry and logs status only")
    else:
        fail("401-no-retry", f"result={result!r} calls={calls!r} logged={logged!r}")


def test_429_then_success_retries_once_with_backoff() -> None:
    calls: list = []
    slept: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            raise _http_error(429, "Too Many Requests")
        body = json.dumps({"answers": {"a": 1}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.sleep", side_effect=lambda s: slept.append(s)
    ):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result is not None and len(calls) == 2 and slept == [0.3]:
        ok("429 then success retries once with 0.3s backoff")
    else:
        fail("429-retry-once", f"result={result!r} calls={calls!r} slept={slept!r}")


def test_529_retries_but_500_does_not() -> None:
    calls_529: list = []

    def fake_urlopen_529(req, timeout=None):
        calls_529.append(timeout)
        if len(calls_529) == 1:
            raise _http_error(529, "Overloaded")
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen_529), mock.patch(
        "craftflow_jev_client.time.sleep", return_value=None
    ):
        result_529 = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    calls_500: list = []

    def fake_urlopen_500(req, timeout=None):
        calls_500.append(timeout)
        raise _http_error(500, "Server Error")

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen_500):
        result_500 = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result_529 is not None and len(calls_529) == 2 and result_500 is None and len(calls_500) == 1:
        ok("529 retries but 500 does not")
    else:
        fail(
            "529-retries-500-does-not",
            f"result_529={result_529!r} calls_529={calls_529!r} result_500={result_500!r} calls_500={calls_500!r}",
        )


def test_retry_skipped_when_budget_below_one_second() -> None:
    clock = _Clock()
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        clock.advance(3.2)
        raise _http_error(429, "Too Many Requests")

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result is None and len(calls) == 1:
        ok("retry skipped when remaining budget below 1.0s")
    else:
        fail("retry-skipped-low-budget", f"result={result!r} calls={calls!r}")


def test_second_attempt_timeout_never_exceeds_remaining_budget() -> None:
    # Finding 5: the first attempt must be a RETRYABLE failure, i.e. a slow HTTPError(429),
    # not socket.timeout.
    clock = _Clock()
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        if len(calls) == 1:
            clock.advance(2.5)
            raise _http_error(429, "Too Many Requests")
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    def fake_sleep(seconds):
        clock.advance(seconds)

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ), mock.patch("craftflow_jev_client.time.sleep", side_effect=fake_sleep):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    second_timeout = calls[1] if len(calls) > 1 else None
    if (
        result is not None
        and len(calls) == 2
        and second_timeout is not None
        and 1.0 <= second_timeout <= 1.2 + 1e-9
    ):
        ok("second attempt timeout never exceeds remaining budget (<=1.2, >=1.0)")
    else:
        fail("second-attempt-timeout-budget", f"result={result!r} calls={calls!r}")


def test_socket_timeout_is_not_retried() -> None:
    calls: list = []
    logged: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        raise socket.timeout("timed out")

    def fake_log(name, payload):
        logged.append((name, payload))

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)

    if (
        result is None
        and len(calls) == 1
        and logged
        and logged[0][1].get("status") is None
        and logged[0][1].get("error") == "timeout"
    ):
        ok("socket.timeout is not retried")
    else:
        fail("socket-timeout-not-retried", f"result={result!r} calls={calls!r} logged={logged!r}")


def test_timeout_seconds_4_leaves_no_retry_budget() -> None:
    clock = _Clock()
    calls: list = []
    logged: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        clock.advance(4.0)
        raise _http_error(429, "Too Many Requests")

    def fake_log(name, payload):
        logged.append((name, payload))

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen), mock.patch(
        "craftflow_jev_client.time.monotonic", side_effect=clock.monotonic
    ):
        result = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=4.0, cache_dir=None, log=fake_log)

    if (
        result is None
        and len(calls) == 1
        and logged
        and logged[0][1].get("attempt") == 1
    ):
        ok("timeoutSeconds=4.0 leaves no retry budget (no retry occurs)")
    else:
        fail("timeout-4-no-retry-budget", f"result={result!r} calls={calls!r} logged={logged!r}")


def test_malformed_json_and_missing_answers_return_none() -> None:
    def fake_urlopen_malformed(req, timeout=None):
        return fake_response(200, b"not-json{")

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen_malformed):
        result_malformed = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    def fake_urlopen_missing(req, timeout=None):
        body = json.dumps({"usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen_missing):
        result_missing = call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)

    if result_malformed is None and result_missing is None:
        ok("malformed JSON and missing 'answers' both return None")
    else:
        fail("malformed-missing-answers", f"result_malformed={result_malformed!r} result_missing={result_missing!r}")


def test_cache_hit_skips_network_and_marks_cache_hit() -> None:
    calls: list = []

    def fake_urlopen(req, timeout=None):
        calls.append(1)
        body = json.dumps({"answers": {"a": 1}, "usage": {"tokens": 5}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen):
            result1 = call(
                {"prompt": "same"}, {"workflow": {}}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache_dir
            )
            result2 = call(
                {"prompt": "same"}, {"workflow": {}}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache_dir
            )

    if (
        result1 is not None
        and result1["cache_hit"] is False
        and result2 is not None
        and result2["cache_hit"] is True
        and result2["answers"] == {"a": 1}
        and len(calls) == 1
    ):
        ok("cache hit skips network on second call with same inputs")
    else:
        fail("cache-hit-skips-network", f"result1={result1!r} result2={result2!r} calls={calls!r}")


def test_cache_entry_never_contains_state_or_key() -> None:
    def fake_urlopen(req, timeout=None):
        body = json.dumps({"answers": {"a": 1}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen):
            call(
                {"prompt": "secret-state-value"},
                {"workflow": {}},
                api_key=SENTINEL_KEY,
                model="jev-1.12",
                timeout=2.5,
                cache_dir=cache_dir,
            )
        cache_files = list(cache_dir.glob("*.json"))
        contents = [f.read_text(encoding="utf-8") for f in cache_files]

    joined = "\n".join(contents)
    if cache_files and "state" not in joined and SENTINEL_KEY not in joined and "secret-state-value" not in joined:
        ok("cache entry never contains state or key")
    else:
        fail("cache-entry-no-state-or-key", f"cache_files={cache_files!r} contents={contents!r}")


def test_key_never_appears_in_exception_or_log_payloads() -> None:
    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError(f"connection refused for key {SENTINEL_KEY}")

    with tempfile.TemporaryDirectory() as tmp:
        cache_dir = Path(tmp)
        with mock.patch("craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen):
            result = call(
                {}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=cache_dir, log=fake_log
            )
        state_root_text = "\n".join(
            p.read_text(encoding="utf-8") for p in cache_dir.rglob("*") if p.is_file()
        )

    payload_dump = json.dumps(logged, default=str)
    if (
        result is None
        and SENTINEL_KEY not in payload_dump
        and SENTINEL_KEY not in state_root_text
    ):
        ok("key never appears in exception or log payloads")
    else:
        fail("key-never-in-logs", f"result={result!r} logged={logged!r} state_root_text={state_root_text!r}")


def test_endpoint_override_honored_only_for_loopback() -> None:
    captured: dict = {}

    def fake_urlopen(req, timeout=None):
        captured["full_url"] = req.full_url
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch.dict("os.environ", {"CRAFTFLOW_JEV_ENDPOINT": "http://127.0.0.1:9/"}, clear=False), mock.patch(
        "craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen
    ):
        call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None)
    loopback_url = captured.get("full_url", "")

    logged: list = []

    def fake_log(name, payload):
        logged.append((name, payload))

    captured2: dict = {}

    def fake_urlopen2(req, timeout=None):
        captured2["full_url"] = req.full_url
        body = json.dumps({"answers": {}, "usage": {}, "model": "jev-1.12"}).encode()
        return fake_response(200, body)

    with mock.patch.dict("os.environ", {"CRAFTFLOW_JEV_ENDPOINT": "https://evil.example/"}, clear=False), mock.patch(
        "craftflow_jev_client.urllib.request.urlopen", side_effect=fake_urlopen2
    ):
        call({}, {}, api_key=SENTINEL_KEY, model="jev-1.12", timeout=2.5, cache_dir=None, log=fake_log)
    evil_url = captured2.get("full_url", "")

    decisions = [p.get("decision") for _n, p in logged]
    if (
        loopback_url.startswith("http://127.0.0.1:9/")
        and evil_url == ENDPOINT
        and "endpoint_override_ignored" in decisions
    ):
        ok("endpoint override honored only for loopback (DD-14)")
    else:
        fail(
            "endpoint-override-loopback-only",
            f"loopback_url={loopback_url!r} evil_url={evil_url!r} decisions={decisions!r}",
        )


def main() -> int:
    print("test_craftflow_jev_client: running")
    print(f"  (ENDPOINT = {ENDPOINT}, RETRY_STATUSES = {RETRY_STATUSES})")
    test_success_returns_answers_and_usage_and_sends_bearer_header()
    test_401_returns_none_without_retry_and_logs_status_only()
    test_429_then_success_retries_once_with_backoff()
    test_529_retries_but_500_does_not()
    test_retry_skipped_when_budget_below_one_second()
    test_second_attempt_timeout_never_exceeds_remaining_budget()
    test_socket_timeout_is_not_retried()
    test_timeout_seconds_4_leaves_no_retry_budget()
    test_malformed_json_and_missing_answers_return_none()
    test_cache_hit_skips_network_and_marks_cache_hit()
    test_cache_entry_never_contains_state_or_key()
    test_key_never_appears_in_exception_or_log_payloads()
    test_endpoint_override_honored_only_for_loopback()

    print()
    print("=" * 40)
    if _errors:
        for err in _errors:
            print(err, file=sys.stderr)
        print(f"\nResults: {_passes} passed, {len(_errors)} failed", file=sys.stderr)
        print("FAIL", file=sys.stderr)
        return 1
    print(f"Results: {_passes} passed, 0 failed")
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
