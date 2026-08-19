# Defensive Patterns

A living list of hard-won bug classes, stated as rules. These are not debugging
techniques (see `root-cause-playbooks.md` for that) — they are patterns that
prevent a class of bug from being written in the first place. Most bite hardest
in exactly the territory `hooks/scripts/craftflow_*_guard.py` and the router's
own worktree/lock machinery already live in: subprocess spawning, file I/O,
lock files, and untrusted input.

Add to this list when a real incident reveals a rule that would have prevented
it. Keep each entry short: the rule, why it matters, and where in this repo it
already applies (or should).

## 1. Report orthogonal outcomes independently

Two operations that can each independently succeed or fail must not be
collapsed into one status. If a script both writes a file and clears a lock,
"done" is not one bit — a caller that only sees "done" cannot tell "wrote but
lock-clear failed" from "both failed" from "both succeeded."

Craftflow already gets this right in the router's worktree-merge cleanup: each
of `git worktree remove`, `git branch -d`, and the lock release captures its
own `_OUTPUT`/`_EXIT` pair rather than one merged "cleanup succeeded" flag.
Follow that shape whenever a guard or hook script performs more than one
side-effecting step.

## 2. Never leave a spawned command's exit code unchecked

A `subprocess.run(...)` or shell command whose exit code is silently ignored
turns a real failure into an invisible no-op — the next line proceeds as if it
succeeded. This is the single most repeated rule in the router protocol
itself (`MERGE_EXIT`, `COPY_FALLBACK_EXIT`, `WORKTREE_REMOVE_EXIT`,
`BRANCH_DELETE_EXIT`, `DIRTY_EXIT` are all deliberately captured, never
assumed). Apply the same discipline in guard scripts: capture `returncode`
explicitly, branch on it, and never treat "the call didn't raise" as "the call
succeeded."

## 3. Contain callback/dispatcher exceptions per-check, not per-run

A hook script that runs several independent checks in sequence (e.g. a
PreToolUse guard validating path scope, then secret patterns, then denial
history) must not let one check's uncaught exception abort the checks after
it. An exception escaping the dispatcher either crashes the hook (denying
everything, including safe operations) or — worse, depending on the hook's
fail-open/fail-closed default — silently skips every check that didn't get to
run. Wrap each check independently; log and continue rather than letting one
bad check take down the rest.

## 4. Never hand a spawned process the ambient environment or a predictable path

When a script spawns a subprocess with `env=os.environ.copy()` (as
`craftflow_cursor_adapter.py` does today), the child inherits every variable
in the parent's environment, including anything matching `*KEY*`, `*SECRET*`,
or `*TOKEN*` — regardless of whether the child needs it. If that subprocess's
output, logs, or crash reports are ever untrusted-output-adjacent (written to
a file another process reads, echoed back to a model, uploaded), those
credentials can leak through a path that was never audited for secret
handling. Scrub or allowlist the env passed to a child process rather than
copying it wholesale; the same applies to writing to a predictable temp path
(`/tmp/foo` vs. a `mkstemp`-style unique path) that another process or user
could pre-create.

## 5. Distinguish "positively denied" from "could not evaluate"

A guard that fails closed on ambiguity is correct — but only if it also
reports *why* it denied, distinctly from an ordinary policy denial. The
router's own worktree-lock staleness logic already draws this line:
`LOCK_READ_ERROR` and `GIT_WORKTREE_LIST_ERROR` are never folded into
`"unknown"` contention, because that would make a local filesystem/permission
problem look identical to "another workflow is genuinely running." Guard
scripts should follow the same split: a real policy violation and "the check
itself couldn't run" both fail closed, but they must never be reported with
the same message, or the operator will chase the wrong problem.

## 6. Re-check immediately before a destructive action, not just before deciding to take it

Any gap between "I read the state and decided to act" and "I actually acted"
is a TOCTOU window. If another process can mutate that state in between, a
decision based on the stale read can destroy something live. The router's
merge-lock reclaim logic closes this by re-reading `metadata.json`
immediately before `rm -rf "$LOCK_DIR"` and comparing it byte-for-byte against
what was read before the staleness decision — only deleting if unchanged.
Apply the same shape to any guard or script that reads-then-deletes,
reads-then-overwrites, or reads-then-merges based on a check that ran even a
few lines earlier.

## 7. Dispose/cleanup must reach quiescence, not just request it

`rm -rf`, `git worktree remove`, process termination — none of these are
guaranteed to have finished just because the call returned. A "busy" or
"still has an open handle" failure is a real, expected outcome, not an edge
case to ignore. Treat every cleanup step as requiring proof it completed
(exit code 0, or a follow-up existence check), and if it didn't, surface that
as a distinct pending state rather than assuming the resource is gone.

## 8. Never let a partial-apply failure be mistaken for total failure — or total success

A multi-step operation (e.g. applying several file changes from a worktree
diff) can fail partway through, after some steps already landed. Reporting
this as a flat pass/fail loses the information the next actor needs: which
steps already applied, and which are still pending. Document and check the
actual partial-apply guarantee of any such script (does it stop on first
error? does it continue and report what failed?) — don't assume atomicity
that was never implemented.
