#!/usr/bin/env python3
"""Windows-portability tests split out of craftflow_hook_unit_tests.py.

Holds the concurrent self-check sweep tests, the Windows-interpreter test and the
fcntl-compat tests so the 30k-line runner stays under its file-size baseline. The
runner's main() registers and calls these, so every test name and count is unchanged.

The SessionStart self-check imports every craftflow_*.py in this directory under a
short sweep budget, so this module imports only the stdlib at module level; the
runner (slow to compile) is fetched lazily by each test via _runtime().
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path


def _runtime():
    """The runner module holding ok/fail and the shared self-check helpers.

    When the runner is executed as a script it is `__main__`, and its ok/fail
    counters live there; importing it by name would count into a second copy.
    """
    main = sys.modules.get("__main__")
    if Path(getattr(main, "__file__", "") or "").name == "craftflow_hook_unit_tests.py":
        return main
    import craftflow_hook_unit_tests as rt

    return rt


def test_selfcheck_concurrent_sweep_fits_budget_that_truncates_sequentially(tmp_dir: Path) -> None:
    rt = _runtime()
    # Windows regression: one interpreter spawn costs ~50-80 ms there, so the
    # sequential ~98-script sweep overran SELFCHECK_TIME_BUDGET_SECONDS on every
    # session and truthfully reported "Sweep truncated ... after checking ~45 of
    # 98". Scaled down: 12 slow-but-valid siblings (0.5 s each) under a 3.5 s
    # budget. Sequentially that is ~6 s, so SWEEP_WORKERS = 1 must truncate (the
    # control: without it a silent 4-wide run would prove nothing); 4 workers run
    # 3 waves (~1.6 s) and must finish silently with every sibling checked.
    name = "hook-selfcheck/concurrent-sweep-fits-budget-that-truncates-sequentially"
    outputs: dict[int, subprocess.CompletedProcess] = {}
    for workers in (1, 4):
        scratch_dir = tmp_dir / f"workers-{workers}"
        checker = rt._selfcheck_scratch_checker_with_constants(scratch_dir, 5, 3.5, sweep_workers=workers)
        for i in range(12):
            rt._selfcheck_write_slow_script(scratch_dir / f"craftflow_scratch_slow_{i:02d}.py", f"craftflow_scratch_slow_{i:02d}", 0.5)
        outputs[workers] = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True, timeout=60)
    sequential, concurrent = outputs[1], outputs[4]
    if sequential.returncode != 0 or "truncat" not in sequential.stdout.lower():
        rt.fail(name, f"control: SWEEP_WORKERS = 1 must truncate this sweep; rc={sequential.returncode} stdout={sequential.stdout!r}")
        return
    if concurrent.returncode != 0 or concurrent.stdout.strip():
        rt.fail(name, f"SWEEP_WORKERS = 4 must check all 12 siblings inside the budget, silently; rc={concurrent.returncode} stdout={concurrent.stdout!r}")
        return
    rt.ok(name)


def test_selfcheck_concurrent_sweep_reports_broken_siblings_in_sibling_order(tmp_dir: Path) -> None:
    rt = _runtime()
    # The concurrent path must keep the sequential contract: every failure is
    # reported with its reason, and in sibling (sorted) order even though the
    # slow "a_" sibling completes after the instant "z_" one.
    name = "hook-selfcheck/concurrent-sweep-reports-broken-in-sibling-order"
    checker = rt._selfcheck_scratch_checker_with_constants(tmp_dir, 5, 10, sweep_workers=4)
    for i in range(rt._SELFCHECK_SCRATCH_CLEAN_COUNT):
        rt._selfcheck_write_clean_script(tmp_dir / f"craftflow_scratch_clean_{i}.py", f"craftflow_scratch_clean_{i}")
    (tmp_dir / "craftflow_scratch_a_broken_slow.py").write_text(
        'import time\ntime.sleep(1.0)\nHelperArg = "str" | None\n', encoding="utf-8"
    )
    rt._selfcheck_write_broken_script(tmp_dir / "craftflow_scratch_z_broken.py")
    result = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True, timeout=60)
    out = result.stdout
    if result.returncode != 0 or "2 hook script(s) failed" not in out or "truncat" in out.lower():
        rt.fail(name, f"expected exactly 2 reported failures and no truncation; rc={result.returncode} stdout={out!r}")
        return
    slow_at, fast_at = out.find("craftflow_scratch_a_broken_slow.py (TypeError"), out.find("craftflow_scratch_z_broken.py (TypeError")
    if slow_at < 0 or fast_at < 0 or slow_at > fast_at:
        rt.fail(name, f"expected both broken siblings with their TypeError reason, in sibling order (a_ before z_); got: {out!r}")
        return
    if any(f"craftflow_scratch_clean_{i}.py" in out for i in range(rt._SELFCHECK_SCRATCH_CLEAN_COUNT)):
        rt.fail(name, f"a clean sibling was reported as failing: {out!r}")
        return
    rt.ok(name)


def test_selfcheck_concurrent_sweep_starts_no_check_after_deadline(tmp_dir: Path) -> None:
    rt = _runtime()
    # The 12 s worst case (DISCOVERY + BUDGET + PER_SCRIPT) under the 15 s hook
    # timeout needs every check still running at the deadline to have STARTED
    # before it. 24 siblings of 1 s each, 4 workers, 1 s budget: the first wave
    # finishes after the deadline, so each worker must stop after it (4 of 24
    # checked, ~1.1 s). A pool that kept draining its queue would check all 24 in
    # ~6 s, past budget + PER_SCRIPT_TIMEOUT_SECONDS.
    name = "hook-selfcheck/concurrent-sweep-starts-no-check-after-deadline"
    per_script_timeout, time_budget = 2, 1
    checker = rt._selfcheck_scratch_checker_with_constants(tmp_dir, per_script_timeout, time_budget, sweep_workers=4)
    for i in range(24):
        rt._selfcheck_write_slow_script(tmp_dir / f"craftflow_scratch_slow_{i:02d}.py", f"craftflow_scratch_slow_{i:02d}", 1.0)
    started = time.monotonic()
    result = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True, timeout=60)
    elapsed = time.monotonic() - started
    counts = re.search(r"after checking (\d+) of (\d+)", result.stdout)
    if result.returncode != 0 or not counts or counts.groups() != ("4", "24"):
        rt.fail(name, f"expected the sweep to stop after the 4 in-flight checks (4 of 24); rc={result.returncode} stdout={result.stdout!r}")
        return
    if elapsed >= time_budget + per_script_timeout + 1.5:
        rt.fail(name, f"sweep took {elapsed:.1f}s, past budget {time_budget}s + one per-script timeout {per_script_timeout}s")
        return
    rt.ok(name)


def _subprocess_run_arg_lists(content: str) -> list[str]:
    """Return the literal text of every argv list/tuple passed as
    subprocess.run's first positional argument -- either inline
    (subprocess.run(["python3", ...])) or via a bare-name variable that was
    itself assigned a list/tuple literal earlier in the file
    (CMD = ["python3", ...]; subprocess.run(CMD, ...)). Capture is
    best-effort (not a real parser) but only the FIRST element of each
    returned string is ever inspected by the caller, so imprecise capture of
    later elements (e.g. across a nested call like str(x)) doesn't matter.
    """
    inline = re.findall(r"subprocess\.run\(\s*(\[[^\]]*\]|\([^)]*\))", content)
    var_names = re.findall(r"subprocess\.run\(\s*(\w+)\s*[,)]", content)
    via_var = []
    for var in var_names:
        m = re.search(rf"\b{re.escape(var)}\s*=\s*(\[[^\]]*\]|\([^)]*\))", content)
        if m:
            via_var.append(m.group(1))
    return inline + via_var


def _first_argv_element_matches(content: str, argv_lists: list[str], value_pattern: str) -> bool:
    """True if any captured argv list/tuple's FIRST element matches
    value_pattern -- either directly (a literal expression) or indirectly via
    a bare identifier that was itself assigned a value matching value_pattern
    elsewhere in the file (e.g. INTERPRETER = "python3")."""
    for argv in argv_lists:
        # NOTE: no trailing \b after value_pattern -- both patterns this is
        # called with end in a non-word character (a closing quote for
        # "python3", a dot-separated identifier for sys.executable's own
        # \b already anchoring it internally), so a trailing \b would sit
        # between two non-word characters and never match. The quotes (or
        # the dotted-attribute pattern itself) already delimit the literal
        # precisely enough without it.
        if re.match(rf"[\[\(]\s*{value_pattern}", argv):
            return True
        ident_match = re.match(r"[\[\(]\s*(\w+)\s*[,\)\]]", argv)
        if ident_match:
            ident = ident_match.group(1)
            if re.search(rf"\b{re.escape(ident)}\s*=\s*{value_pattern}", content):
                return True
    return False


def test_selfcheck_resolves_bare_python3_not_sys_executable() -> None:
    rt = _runtime()
    name = "hook-selfcheck/resolves-bare-python3"
    path = rt.SCRIPTS / "craftflow_hook_selfcheck.py"
    if not path.exists():
        rt.fail(name, f"craftflow_hook_selfcheck.py not found at {path}")
        return
    content = path.read_text(encoding="utf-8")
    # Both the accept and reject checks require actual call-site usage as the
    # first element of a real subprocess.run() argv list/tuple -- not just
    # presence anywhere in the file. A bare content-substring check would
    # false-FAIL on legitimate prose (e.g. this checker's own module
    # docstring explaining "never sys.executable") and false-PASS on a stray
    # comment or unused variable. Accepts either a direct literal or a
    # same-file constant/variable assigned that value and then used as the
    # argv list's first element (covers both `INTERPRETER = "python3"` and
    # `CMD = ["python3", ...]; subprocess.run(CMD, ...)` idioms, plus the
    # tuple form `subprocess.run(("python3", ...))`).
    #
    # The one sanctioned sys.executable use is Windows-only: there CreateProcess
    # resolves a bare "python3" to the Microsoft Store alias stub, not the
    # ~/bin/python3 shim hooks.json runs under Git Bash, so every sibling would
    # falsely "fail to import". That exact guarded conditional must exist once,
    # and is reduced to its POSIX branch before the checks below, so any other
    # (unguarded or differently guarded) sys.executable use still fails.
    guarded = _SELFCHECK_WINDOWS_INTERPRETER_RE.findall(content)
    if len(guarded) != 1:
        rt.fail(
            name,
            "expected exactly one `INTERPRETER = sys.executable if os.name == \"nt\" else \"python3\"` "
            f"assignment (Windows-only sys.executable); found {len(guarded)}",
        )
        return
    posix_view = _SELFCHECK_WINDOWS_INTERPRETER_RE.sub(r"\1\2", content)
    argv_lists = _subprocess_run_arg_lists(posix_view)
    if _first_argv_element_matches(posix_view, argv_lists, r"sys\.executable"):
        rt.fail(name, "checker must resolve the interpreter as bare python3 on PATH, not sys.executable")
        return
    if not _first_argv_element_matches(posix_view, argv_lists, r'"python3"'):
        rt.fail(
            name,
            "checker's subprocess.run call does not appear to pass a literal "
            "'python3' (or a constant/variable assigned to it) as its first argument",
        )
        return
    expected = sys.executable if os.name == "nt" else "python3"
    resolved = rt._load_selfcheck_module().INTERPRETER
    if resolved != expected:
        rt.fail(name, f"on os.name={os.name!r} the checker resolves {resolved!r}; expected {expected!r}")
        return
    rt.ok(name)


# `<NAME> = sys.executable if os.name == "nt" else "python3"`; group 1 is `<NAME> = `, group 2 the POSIX literal.
_SELFCHECK_WINDOWS_INTERPRETER_RE = re.compile(
    r"""(\b\w+\s*=\s*)sys\.executable\s+if\s+os\.name\s*==\s*["']nt["']\s+else\s+("python3")"""
)


# Holds two flocks from a separate process, as when two hooks race (re-locking through the holder's own handle is a
# no-op on POSIX but refused on Windows, so the contender must be another holder): a dedicated lock file via an int
# fd, and an append-mode log via fileno() that is written between LOCK_EX and LOCK_UN the way the event-log writers
# do. argv: scripts dir, lock path, log path, marker dir.
_FCNTL_COMPAT_HOLDER = textwrap.dedent(
    """
    import os, sys, time
    sys.path.insert(0, sys.argv[1])
    import craftflow_fcntl_compat as fcntl

    def marker(name):
        return os.path.join(sys.argv[4], name)

    def wait_for(name):
        deadline = time.monotonic() + 30
        while not os.path.exists(marker(name)):
            if time.monotonic() > deadline:
                sys.exit(3)
            time.sleep(0.02)

    fd = os.open(sys.argv[2], os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    log = open(sys.argv[3], "a", encoding="utf-8")
    fcntl.flock(log.fileno(), fcntl.LOCK_EX)
    log.write("held\\n")
    log.flush()
    open(marker("ready"), "w").close()
    wait_for("release")
    fcntl.flock(fd, fcntl.LOCK_UN)
    fcntl.flock(log.fileno(), fcntl.LOCK_UN)
    open(marker("released"), "w").close()
    wait_for("done")  # both files stay open: the reacquire must succeed because of LOCK_UN, not close
    log.close()
    os.close(fd)
    """
)


def _wait_for_marker(path: Path, proc: subprocess.Popen, seconds: float) -> str | None:
    """None once `path` exists; else why not (the holder exited early, or the wait timed out)."""
    deadline = time.monotonic() + seconds
    while not path.exists():
        if proc.poll() is not None:
            return f"holder exited {proc.returncode} before {path.name}: {proc.stderr.read()[-400:]!r}"
        if time.monotonic() > deadline:
            return f"timed out after {seconds}s waiting for {path.name}"
        time.sleep(0.02)
    return None


def test_fcntl_compat_platform_branch(tmp_dir: Path) -> None:
    rt = _runtime()
    import craftflow_fcntl_compat as fcntl  # fcntl on POSIX; an msvcrt-backed flock on Windows

    name = "fcntl-compat/posix-reexport-or-windows-msvcrt-flock"
    exports = ("flock", "LOCK_EX", "LOCK_SH", "LOCK_NB", "LOCK_UN")
    try:
        import fcntl as real_fcntl
    except ImportError:
        real_fcntl = None
    if real_fcntl is not None:
        # POSIX: a pure re-export, so every existing call site behaves byte-identically.
        changed = [n for n in exports if getattr(fcntl, n) is not getattr(real_fcntl, n)]
        if changed:
            rt.fail(name, f"POSIX branch must re-export fcntl unchanged; differs for {changed}")
            return
        rt.ok(name)
        return
    flags = [getattr(fcntl, n) for n in exports[1:]]
    if fcntl.flock.__module__ != "craftflow_fcntl_compat" or len({*flags}) != 4 or any(f & (f - 1) for f in flags):
        rt.fail(name, f"Windows branch must define its own flock and four distinct single-bit flags; got {flags}")
        return
    tmp_dir.mkdir(parents=True, exist_ok=True)
    path = tmp_dir / "events.jsonl"
    path.write_text("seed\n", encoding="utf-8")
    with open(path, "a", encoding="utf-8") as fh:
        before = os.lseek(fh.fileno(), 0, os.SEEK_CUR)
        try:
            fcntl.flock(fh, fcntl.LOCK_UN)  # LOCK_UN with nothing held is a no-op, as with flock(2)
            fcntl.flock(fh, fcntl.LOCK_SH)  # LOCK_SH is taken as an exclusive lock on Windows
            fcntl.flock(fh, fcntl.LOCK_UN)
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            during = os.lseek(fh.fileno(), 0, os.SEEK_CUR)
            fh.write("x\n")
            fh.flush()
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # released above, so not refused
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError as exc:
            rt.fail(name, f"lock/unlock sequence on one handle raised {type(exc).__name__}: {exc}")
            return
    if during != before:
        rt.fail(name, f"flock moved the caller's file position from {before} to {during}")
        return
    if path.read_text(encoding="utf-8") != "seed\nx\n":
        rt.fail(name, f"append under the lock wrote to the wrong place: {path.read_text(encoding='utf-8')!r}")
        return
    rt.ok(name)


def test_fcntl_compat_cross_process_contention_then_release(tmp_dir: Path) -> None:
    rt = _runtime()
    import craftflow_fcntl_compat as fcntl  # fcntl on POSIX; an msvcrt-backed flock on Windows

    name = "fcntl-compat/cross-process-nonblocking-contention-then-release"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    lock_path, log_path, markers = tmp_dir / "probe.lock", tmp_dir / "events.jsonl", tmp_dir / "markers"
    markers.mkdir()
    log_path.write_text("seed\n", encoding="utf-8")
    holder = subprocess.Popen(
        [sys.executable, "-c", _FCNTL_COMPAT_HOLDER, str(rt.SCRIPTS), str(lock_path), str(log_path), str(markers)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    fd, log = None, None
    try:
        problem = _wait_for_marker(markers / "ready", holder, 15)
        if problem:
            rt.fail(name, problem)
            return
        fd = os.open(str(lock_path), os.O_RDWR)
        log = open(log_path, "a", encoding="utf-8")
        probes = (("lock file via int fd", fd), ("append-mode log via file object", log))
        for label, target in probes:
            try:
                fcntl.flock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            except OSError as exc:
                rt.fail(name, f"{label}: expected BlockingIOError while held elsewhere, got {type(exc).__name__}: {exc}")
                return
            rt.fail(name, f"{label}: LOCK_EX | LOCK_NB succeeded while another process holds the lock")
            return
        try:  # advisory, like flock(2): a held lock never blocks plain reads of the locked file
            seen = log_path.read_text(encoding="utf-8")
        except OSError as exc:
            rt.fail(name, f"reading the locked log failed: {type(exc).__name__}: {exc}")
            return
        if seen != "seed\nheld\n":
            rt.fail(name, f"unexpected log content while held: {seen!r}")
            return
        (markers / "release").touch()
        problem = _wait_for_marker(markers / "released", holder, 15)
        if problem:
            rt.fail(name, problem)
            return
        for label, target in probes:
            try:
                fcntl.flock(target, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(target, fcntl.LOCK_UN)
                fcntl.flock(target, fcntl.LOCK_EX)  # the blocking mode acquires a free lock at once
                fcntl.flock(target, fcntl.LOCK_UN)
            except OSError as exc:
                rt.fail(name, f"{label}: not reacquirable after the holder's LOCK_UN: {type(exc).__name__}: {exc}")
                return
        (markers / "done").touch()
        holder.wait(timeout=15)
        if holder.returncode != 0:
            rt.fail(name, f"holder exited {holder.returncode}: {holder.stderr.read()[-400:]!r}")
            return
    finally:
        if log is not None:
            log.close()
        if fd is not None:
            os.close(fd)
        if holder.poll() is None:
            holder.kill()
            holder.wait()
        holder.stderr.close()
    rt.ok(name)


def test_fcntl_compat_bad_fd_fails_fast_with_ebadf(tmp_dir: Path) -> None:
    rt = _runtime()
    import craftflow_fcntl_compat as fcntl  # fcntl on POSIX; an msvcrt-backed flock on Windows

    name = "fcntl-compat/closed-fd-raises-ebadf-immediately-in-every-mode"
    import errno

    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(tmp_dir / "closed.lock"), os.O_RDWR | os.O_CREAT)
    os.close(fd)
    # A regression used to retry every OSError until the 30 s ceiling; cap the ceiling so that regression shows up
    # as a failed assertion in ~3 s instead of a half-minute hang. POSIX has no such attribute (real flock(2)).
    saved = getattr(fcntl, "_BLOCKING_CEILING_SECONDS", None)
    if saved is not None:
        fcntl._BLOCKING_CEILING_SECONDS = 3.0
    try:
        for label, operation, tolerated_seconds in (
            ("LOCK_EX", fcntl.LOCK_EX, 1.0),
            ("LOCK_EX | LOCK_NB", fcntl.LOCK_EX | fcntl.LOCK_NB, 1.0),
            ("LOCK_UN", fcntl.LOCK_UN, 1.0),
        ):
            started = time.monotonic()
            try:
                fcntl.flock(fd, operation)
            except BlockingIOError as exc:
                rt.fail(name, f"{label}: a closed fd was reported as lock contention: {exc!r}")
                return
            except OSError as exc:
                elapsed = time.monotonic() - started
                if exc.errno != errno.EBADF or elapsed >= tolerated_seconds:
                    rt.fail(name, f"{label}: expected EBADF within {tolerated_seconds}s, got errno={exc.errno} after {elapsed:.2f}s: {exc}")
                    return
                continue
            rt.fail(name, f"{label}: flock on a closed fd returned without raising")
            return
    finally:
        if saved is not None:
            fcntl._BLOCKING_CEILING_SECONDS = saved
    rt.ok(name)
