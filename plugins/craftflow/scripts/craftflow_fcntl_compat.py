#!/usr/bin/env python3
"""Portable `fcntl.flock` for the hook scripts: `import craftflow_fcntl_compat as fcntl`.

Several hook scripts serialize read-modify-write cycles with `fcntl.flock` on a lock file (or on an append-only
event log). `fcntl` does not exist on Windows, so a bare `import fcntl` raised ModuleNotFoundError at import time
and took down every hook that imports one of those modules (all of `craftflow_hooklib`'s importers included).

POSIX: a pure re-export of `fcntl.flock` and its LOCK_* flags, so behavior there is unchanged.

Windows: the same call shape over `msvcrt.locking`, which takes a byte-range lock from the file's current position.
`flock` accepts an int fd or any object with `fileno()`, and maps the flags like this:
- LOCK_UN: unlock; a handle that holds no lock is a no-op, as with flock(2).
- LOCK_NB (with LOCK_EX or LOCK_SH): a single attempt; raises BlockingIOError when another handle holds the lock.
- LOCK_EX / LOCK_SH without LOCK_NB: msvcrt's blocking mode (it retries for about 10 s per call), repeated up to
  _BLOCKING_CEILING_SECONDS and then OSError, so a stuck holder can never hang a hook forever.
Differences from flock(2) that no call site depends on: LOCK_SH is exclusive (msvcrt has no shared lock), and a
second LOCK_EX through the handle that already holds the lock is refused instead of being a no-op.

Every lock covers the same single byte at _LOCK_OFFSET, far past the data of any lock file or event log, and the
caller's file position is restored afterwards. Windows byte-range locks are mandatory, so locking real data bytes
would make concurrent readers of an append-only log fail; and a lock taken at the current position could not be
released once a write under the lock had moved that position.
"""
from __future__ import annotations

try:
    from fcntl import LOCK_EX, LOCK_NB, LOCK_SH, LOCK_UN, flock  # POSIX: re-exported unchanged
except ImportError:  # Windows
    import errno
    import msvcrt
    import os
    import time
    from typing import Any

    LOCK_SH, LOCK_EX, LOCK_NB, LOCK_UN = 1, 2, 4, 8
    _BLOCKING_CEILING_SECONDS = 30.0
    _LOCK_OFFSET = 0x7FFFFFFE  # one byte below 2 GiB: past any real data, inside every filesystem's offset range
    # The only errnos msvcrt.locking raises for "another handle holds it / this handle does not hold it": EACCES for
    # LK_NBLCK and LK_UNLCK, EDEADLOCK once LK_LOCK's own ~10 s of internal retries ran out. Anything else (EBADF on a
    # closed fd, EINVAL on a filesystem that rejects byte-range locks) is a real error that retrying can never fix, so
    # it propagates unchanged instead of being retried to the ceiling or relabelled as contention.
    _CONTENTION_ERRNOS = (errno.EACCES, errno.EDEADLOCK)

    def _fileno(fd: Any) -> int:
        if isinstance(fd, int):
            return fd
        if hasattr(fd, "fileno"):
            return fd.fileno()
        raise TypeError("argument must be an int, or have a fileno() method.")

    def _lock_byte(fd: int, mode: int) -> None:
        """msvcrt.locking on the one byte at _LOCK_OFFSET; the caller's file position is restored either way."""
        position = os.lseek(fd, 0, os.SEEK_CUR)
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, mode, 1)
        finally:
            os.lseek(fd, position, os.SEEK_SET)

    def flock(fd: Any, operation: int) -> None:  # type: ignore[no-redef]
        fd = _fileno(fd)
        if operation & LOCK_UN:
            try:
                _lock_byte(fd, msvcrt.LK_UNLCK)
            except OSError as exc:
                if exc.errno not in _CONTENTION_ERRNOS:
                    raise
                # EACCES: nothing held through this handle (a no-op, as with flock(2))
            return
        if not operation & (LOCK_EX | LOCK_SH):
            raise OSError(errno.EINVAL, "flock operation needs LOCK_EX, LOCK_SH or LOCK_UN")
        if operation & LOCK_NB:
            try:
                _lock_byte(fd, msvcrt.LK_NBLCK)
            except OSError as exc:
                if exc.errno not in _CONTENTION_ERRNOS:
                    raise
                raise BlockingIOError(exc.errno, f"lock is held by another handle: {exc}") from exc
            return
        deadline = time.monotonic() + _BLOCKING_CEILING_SECONDS
        while True:
            try:
                _lock_byte(fd, msvcrt.LK_LOCK)
                return
            except OSError as exc:
                if exc.errno not in _CONTENTION_ERRNOS:
                    raise
                if time.monotonic() >= deadline:
                    detail = f"lock not acquired within {_BLOCKING_CEILING_SECONDS:.0f}s: {exc}"
                    raise OSError(exc.errno, detail) from exc
