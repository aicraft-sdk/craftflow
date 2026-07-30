#!/usr/bin/env python3
"""
craftflow_skill_promote.py

Deterministic promotion of a STAGED skill proposal (written by the
`skill-author` agent under `.craftflow/state/project/skill-proposals/<id>/`)
into a project's canonical skill locations. Phase 2 of the
post-implementation project-skill distillation feature
(docs/plans/2026-07-29-craftflow-skill-distillation-plan.md).

This script ONLY runs when explicitly invoked with `--approve <candidate-id>`.
It is never wired to run automatically. It is the sole authorized writer of
`<project-root>/.claude/skills/<name>/SKILL.md` for this feature.

Subcommand:
    --approve <candidate-id>
        [--proposals-dir PATH]  (default: .craftflow/state/project/skill-proposals)
        [--project-root PATH]   (default: .)
        [--ledger PATH]         (default: .craftflow/state/project/skill-candidates.json)

Behavior:
    1. Locate the proposal directory for <candidate-id>. It must contain
       exactly one of SKILL.md (new skill) or SKILL.patch (update to an
       existing skill, target path taken from the patch's own `+++` header).
    2. Validate frontmatter (`name` present, `description` >= 40 chars per
       harness-audit's SKL-04) BEFORE any write. Refuses to promote (exit 1,
       no writes at all) on validation failure.
    3. Replace the `PENDING_APPROVAL` placeholders for
       `craftflow-promoted-at` / `craftflow-review-after` with real ISO
       timestamps (review-after = promoted-at + 90 days).
    4. Write the canonical file atomically to
       <project-root>/.claude/skills/<name>/SKILL.md.
    5. Sync <project-root>/.cursor/skills/<name> to the canonical directory:
       symlink first (pwd -P canonicalization, `.stale-backup-<ts>-<pid>`
       conflict handling for anything already at that path that isn't already
       correctly linked), falling back to a dereferenced (real content) copy
       when symlink creation is not viable in the current environment.
    6. Always requires the candidate's CURRENT ledger status to be
       `candidate` or `proposed` BEFORE any write (fails closed, no write at
       all, if it's already `rejected`/`promoted`, absent from the ledger,
       or the `--ledger` path itself doesn't exist yet -- a missing ledger
       file loads as an empty ledger, so the candidate is simply "not
       found"), then marks the entry `promoted`, all inside one lock-held
       critical section shared with `--reject`'s own guard. There is no
       best-effort bypass: `--approve` is only ever router-invoked for a
       candidate queried out of an existing ledger, so there is no
       legitimate flow where the ledger should be skipped.

Exit 0 on success. Exit 1 on any validation or usage failure (no writes).
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import craftflow_skill_ledger as ledger_mod  # noqa: E402

DEFAULT_PROPOSALS_DIR = ".craftflow/state/project/skill-proposals"
DEFAULT_LEDGER_PATH = ledger_mod.DEFAULT_LEDGER_PATH
DEFAULT_PROJECT_ROOT = "."
MIN_DESCRIPTION_LEN = 40
REVIEW_AFTER_DAYS = 90

# Filesystem-safe token allowlists (mirrors craftflow_skill_ledger.is_safe_wf_id's
# fail-closed shape): no "/", no leading dot, no ".." component. Applied to
# BOTH the candidate id (comes from CLI args) and the skill `name` extracted
# from proposal frontmatter (comes from file content someone wrote) before
# either is used in a path join -- a malicious or malformed `name:` value
# must never be able to escape the canonical skills directory.
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

BLOCK_SCALAR_RE = re.compile(r"^([|>])([+-]?)$")


def is_safe_token(value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if value in (".", ".."):
        return False
    if value.startswith(".") or "/" in value or "\\" in value:
        return False
    return bool(_SAFE_TOKEN_RE.match(value))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def review_after_iso(promoted_at_iso: str) -> str:
    dt = datetime.strptime(promoted_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return (dt + timedelta(days=REVIEW_AFTER_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Frontmatter parsing -- ported (contract-identical subset) from
# packages/harness-audit/src/util.ts's parseFrontmatter, so this script's
# validation logic agrees with what harness-audit itself will later check
# (SKL-02/SKL-04) rather than silently drifting from it.
# ---------------------------------------------------------------------------

def _leading_whitespace(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _read_block_scalar(lines: list, start_index: int, key_indent: int, style: str) -> tuple:
    collected = []
    block_indent = None
    i = start_index
    while i < len(lines):
        line = lines[i]
        if line.strip() == "":
            collected.append("")
            i += 1
            continue
        indent = _leading_whitespace(line)
        if indent <= key_indent:
            break
        if block_indent is None:
            block_indent = indent
        collected.append(line[block_indent:])
        i += 1
    if style == "|":
        value = "\n".join(collected).strip()
    else:
        paragraphs = re.split(r"\n{2,}", "\n".join(collected))
        value = "\n".join(p.replace("\n", " ").strip() for p in paragraphs).strip()
    return value, i - 1


def parse_frontmatter(content: str):
    normalized = content.lstrip("﻿")
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---(\r?\n|$)", normalized)
    if not match:
        return None
    lines = re.split(r"\r?\n", match.group(1))
    result: dict = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        kv = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not kv:
            i += 1
            continue
        key = kv.group(1)
        raw_value = kv.group(2).strip()
        block_match = BLOCK_SCALAR_RE.match(raw_value)
        if block_match:
            style = block_match.group(1)
            key_indent = _leading_whitespace(line)
            value, last_index = _read_block_scalar(lines, i + 1, key_indent, style)
            result[key] = value
            i = last_index + 1
        else:
            result[key] = re.sub(r'^["\']|["\']$', "", raw_value)
            i += 1
    return result


def validate_frontmatter(fm) -> str | None:
    """Returns an error message string if invalid, or None if valid."""
    if not isinstance(fm, dict):
        return "no frontmatter block found"
    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        return "frontmatter missing required 'name' field"
    description = fm.get("description")
    if not isinstance(description, str) or len(description) < MIN_DESCRIPTION_LEN:
        return (
            f"frontmatter 'description' must be >= {MIN_DESCRIPTION_LEN} characters "
            f"(SKL-04); got {len(description) if isinstance(description, str) else 0}"
        )
    if not is_safe_token(name.strip()):
        return f"frontmatter 'name' is not a filesystem-safe token: {name!r}"
    return None


# ---------------------------------------------------------------------------
# Atomic file write (mirrors craftflow_skill_ledger.save_ledger_atomic's
# temp-file-then-os.replace pattern, generalized to arbitrary text content).
# ---------------------------------------------------------------------------

def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".skill-promote-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Cursor sync -- ported behavior (not literal shell) of install-cursor.sh's
# symlink-with-pwd-P-canonicalization + `.stale-backup-<ts>` pattern, plus a
# `dereference: true`-equivalent copy fallback (mirrors
# packages/repo-conductor/src/craftflow/provision.ts's fs.cp(..., { dereference: true }))
# for environments where symlink creation is not viable.
# ---------------------------------------------------------------------------

def _same_resolved_path(a: Path, b: Path) -> bool:
    """Case-fold-aware path equality (REM-FIX round 6, symmetric to
    cmd_approve's `promoted_skill` collision-guard fix -- same root cause).

    `Path.resolve()` only resolves symlinks; it does NOT normalize case to
    match whatever casing actually exists on disk. On a case-insensitive
    filesystem (macOS APFS, Windows NTFS), two resolved paths differing only
    by case name the SAME physical file/directory, but a naive `==` string
    compare would wrongly report them as different. Used by
    `sync_cursor_skill`'s "already correctly linked" checks so a
    differently-cased `canonical_dir`/`link_path` pair pointing at the same
    real location is recognized as already in sync rather than triggering an
    unnecessary (and potentially unsafe) stale-backup-and-relink cycle."""
    return str(a).strip().lower() == str(b).strip().lower()


def sync_cursor_skill(canonical_dir: Path, link_path: Path) -> str:
    """Returns one of: 'already-linked', 'symlinked', 'copied-fallback'."""
    canonical_real = canonical_dir.resolve()
    link_path.parent.mkdir(parents=True, exist_ok=True)

    if link_path.is_symlink():
        try:
            existing_real = link_path.resolve()
        except OSError:
            existing_real = None
        if existing_real is not None and _same_resolved_path(existing_real, canonical_real):
            return "already-linked"

    if link_path.exists() or link_path.is_symlink():
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        backup = link_path.with_name(f"{link_path.name}.stale-backup-{ts}-{os.getpid()}")
        os.rename(str(link_path), str(backup))

    try:
        os.symlink(str(canonical_real), str(link_path), target_is_directory=True)
        return "symlinked"
    except OSError:
        # HIGH 3(a) (REM-FIX): a concurrent `--approve` invocation for the
        # SAME candidate racing this one may have created the symlink in the
        # window between this process's own is_symlink()/exists() checks
        # above and this os.symlink() call -- re-check for that exact
        # "someone already linked it correctly" outcome before falling back
        # to a full copytree (which would otherwise crash with an unhandled
        # FileExistsError against a symlink that already exists and is
        # already correct).
        if link_path.is_symlink():
            try:
                existing_real = link_path.resolve()
            except OSError:
                existing_real = None
            if existing_real is not None and _same_resolved_path(existing_real, canonical_real):
                return "already-linked"
        shutil.copytree(str(canonical_real), str(link_path))
        return "copied-fallback"


# ---------------------------------------------------------------------------
# Promotion lock (HIGH 3(b), REM-FIX) -- serializes concurrent `--approve`
# invocations for the SAME candidate/skill name so the canonical write, the
# cursor sync, and the ledger update all happen as one atomic-ish unit rather
# than racing. Mirrors craftflow_skill_ledger.py's own
# `_ledger_file_lock`/fcntl.flock pattern (same blocking-acquire, same
# never-cleaned-up lock file rationale -- this is a low-frequency, fast
# operation with no need for timeout/retry machinery).
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _skill_promote_lock(canonical_dir: Path):
    """Exclusive advisory lock scoped to one candidate's canonical skill
    directory (`<project-root>/.claude/skills/<name>/`), held for the
    duration of the write + cursor-sync + ledger-mark sequence in
    cmd_approve. The lock FILE lives alongside (not inside) the canonical
    directory, since the directory itself may not exist yet on a fresh
    promotion."""
    canonical_dir.parent.mkdir(parents=True, exist_ok=True)
    lock_path = canonical_dir.parent / f".{canonical_dir.name}.promote.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------------------
# Ledger status precondition + promotion (REM-FIX round 2, symmetric to
# craftflow_skill_ledger.py's own cmd_reject guard, HIGH 3):
#
# --approve previously wrote the canonical skill file and marked the ledger
# "promoted" unconditionally -- it never checked whether the candidate's
# CURRENT ledger status still allowed promotion. That let an already-
# "rejected" candidate (a human explicitly tombstoned it) get silently
# re-promoted by a stale or concurrent --approve invocation on the same
# staged proposal, flipping the ledger status back to "promoted" and leaving
# contradictory rejected_reason/rejected_at_distinct_workflows fields in
# place. This bypasses the "reject is a permanent tombstone unless
# distinct_workflows doubles" invariant entirely.
#
# Fix: the status check AND the eventual "mark promoted" write always happen
# inside the SAME `_ledger_file_lock` critical section that cmd_reject itself
# acquires -- closing the TOCTOU gap where a concurrent --reject could
# tombstone the candidate between an unguarded check and the write.
#
# ROUND 3 (REM-FIX): the round-2 fix above was itself gated behind
# `if ledger_path.exists(): ... else: <unguarded best-effort success>`. A
# `--ledger` path that simply doesn't exist yet (a typo, stale automation,
# wrong cwd -- a CONFIG difference, not a code difference) took the
# unguarded else branch and wrote the canonical file with ZERO ledger
# validation, regardless of whether the SAME candidate id is "rejected" in
# the real ledger elsewhere. `ledger_mod.load_ledger` and
# `ledger_mod._ledger_file_lock` already tolerate a missing file (empty
# ledger, lock file created on demand), so `cmd_approve` now ALWAYS validates
# against whatever `--ledger` points at -- byte-for-byte symmetric with
# `cmd_reject`, which has never had a "no ledger file" carve-out. There is no
# legitimate real flow where `--approve` is invoked without the candidate
# already present in the ledger it queried it from (skill-distillation's own
# fast-path/SKILL.md), so removing the bypass is a simplification, not a
# regression against real usage.
# ---------------------------------------------------------------------------
#
# The precondition check itself is inlined directly in `cmd_approve` (not
# factored into a helper) so its shape stays textually visible alongside
# `cmd_reject`'s own equivalent check for the structural regression test
# that greps both function bodies -- a single call site does not earn an
# extraction (rule of three).


# ---------------------------------------------------------------------------
# Patch (update) application -- shells out to the system `patch` binary
# against a scratch copy of the target file, only committing the result if
# the patch applies cleanly AND the patched frontmatter still validates.
# ---------------------------------------------------------------------------

# HIGH 4 (REM-FIX): the ONLY locations a SKILL.patch update may legitimately
# target -- an already-promoted skill's own canonical (or synced cursor) file.
# An arbitrary project file, even one with valid-shaped frontmatter, must
# never be reachable via this path (that would silently "promote" a random
# file as a side effect of an update patch).
_SKILL_TARGET_RE = re.compile(r"^(\.claude|\.cursor)/skills/([^/]+)/SKILL\.md$")


def _extract_patch_target(patch_text: str) -> str | None:
    """Extract the target relative path from a unified diff's `+++` header."""
    for line in patch_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            target = re.split(r"\t", target)[0]
            for prefix in ("b/", "a/"):
                if target.startswith(prefix):
                    target = target[len(prefix):]
            return target
    return None


def apply_update_patch(project_root: Path, patch_text: str) -> tuple:
    """Returns (error_or_none, patched_content_or_none, target_path_or_none)."""
    target_rel = _extract_patch_target(patch_text)
    if not target_rel:
        return "SKILL.patch has no '+++' target header", None, None
    target_path = (project_root / target_rel).resolve()
    try:
        target_path.relative_to(project_root.resolve())
    except ValueError:
        return f"patch target escapes project root: {target_rel!r}", None, None

    # HIGH 4 (REM-FIX): reject any patch target that isn't an ALREADY-PROMOTED
    # skill location, before ever reading/copying the target's content.
    target_match = _SKILL_TARGET_RE.match(target_rel)
    if not target_match:
        return (
            "patch target is not a promoted skill location "
            f"(.claude/skills/<name>/SKILL.md or .cursor/skills/<name>/SKILL.md): {target_rel!r}"
        ), None, None
    parsed_name = target_match.group(2)

    if not target_path.exists():
        return f"patch target does not exist: {target_rel}", None, None

    # HIGH 4 (REM-FIX): the pre-patch target must ALREADY be a real promoted
    # skill whose own frontmatter 'name' agrees with its path segment -- not
    # merely a file that happens to sit at that path. This closes the gap
    # where an arbitrary project file could be "promoted" via a patch
    # targeting it, as long as the patch's own diff hunks happened to shape
    # valid-looking frontmatter into it.
    pre_patch_content = target_path.read_text(encoding="utf-8")
    pre_patch_fm = parse_frontmatter(pre_patch_content)
    pre_patch_name = pre_patch_fm.get("name") if isinstance(pre_patch_fm, dict) else None
    if not isinstance(pre_patch_name, str) or pre_patch_name.strip() != parsed_name:
        return (
            f"patch target's existing frontmatter 'name' ({pre_patch_name!r}) does not match "
            f"its own path segment ({parsed_name!r}); refusing to patch a file that is not "
            "already a self-consistent promoted skill"
        ), None, None

    with tempfile.TemporaryDirectory(prefix="skill-promote-patch-") as tmp:
        scratch = Path(tmp) / "scratch"
        shutil.copy(str(target_path), str(scratch))
        result = subprocess.run(
            ["patch", "--forward", str(scratch)],
            input=patch_text,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return f"patch did not apply cleanly: {result.stderr.strip() or result.stdout.strip()}", None, None
        patched_content = scratch.read_text(encoding="utf-8")
    return None, patched_content, target_rel


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_approve(args) -> int:
    candidate_id = args.approve
    if not is_safe_token(candidate_id):
        print(json.dumps({"error": f"invalid or unsafe candidate id: {candidate_id!r}"}), file=sys.stderr)
        return 1

    proposals_dir = Path(args.proposals_dir)
    project_root = Path(args.project_root)
    ledger_path = Path(args.ledger)

    # HIGH 2 (REM-FIX): `--project-root` must already exist as a directory
    # before any write is attempted. Without this, a nonexistent path
    # silently `mkdir -p`'s the whole tree via atomic_write_text()'s own
    # `path.parent.mkdir(parents=True, exist_ok=True)`, and exits 0. This is
    # a pure existence+directory check -- no `.git` requirement, no other
    # side effect.
    if not project_root.is_dir():
        print(
            json.dumps({"error": f"--project-root does not exist or is not a directory: {project_root}"}),
            file=sys.stderr,
        )
        return 1

    proposal_dir = proposals_dir / candidate_id
    if not proposal_dir.is_dir():
        print(json.dumps({"error": f"no proposal found for candidate {candidate_id!r} at {proposal_dir}"}), file=sys.stderr)
        return 1

    skill_md_path = proposal_dir / "SKILL.md"
    skill_patch_path = proposal_dir / "SKILL.patch"
    has_md = skill_md_path.is_file()
    has_patch = skill_patch_path.is_file()

    if has_md and has_patch:
        print(json.dumps({"error": "proposal has both SKILL.md and SKILL.patch; exactly one is required"}), file=sys.stderr)
        return 1
    if not has_md and not has_patch:
        print(json.dumps({"error": "proposal has neither SKILL.md nor SKILL.patch"}), file=sys.stderr)
        return 1

    target_rel: str | None = None
    if has_md:
        content = skill_md_path.read_text(encoding="utf-8")
    else:
        patch_text = skill_patch_path.read_text(encoding="utf-8")
        error, patched_content, target_rel = apply_update_patch(project_root, patch_text)
        if error:
            print(json.dumps({"error": error}), file=sys.stderr)
            return 1
        content = patched_content

    fm = parse_frontmatter(content)
    validation_error = validate_frontmatter(fm)
    if validation_error:
        print(json.dumps({"error": f"frontmatter validation failed: {validation_error}"}), file=sys.stderr)
        return 1

    name = fm["name"].strip()

    # CRITICAL 1 (REM-FIX): for the update-patch path, `target_rel` is the
    # path apply_update_patch() actually validated the patch against (from
    # the patch's own `+++` header). The canonical write below is always
    # derived from `name` (the patched frontmatter's own field) -- if the two
    # diverge, writing would silently land on a DIFFERENT file than the one
    # just validated, leaving the actually-referenced file untouched. Abort
    # before any write if they disagree.
    if target_rel is not None:
        target_match = _SKILL_TARGET_RE.match(target_rel)
        # target_match is guaranteed non-None here: apply_update_patch()
        # already rejected any target_rel that doesn't match this shape
        # (HIGH 4) before ever returning a target_rel at all.
        family = target_match.group(1)
        canonical_target_rel = f"{family}/skills/{name}/SKILL.md"
        if canonical_target_rel != target_rel:
            print(json.dumps({
                "error": (
                    f"patch target {target_rel!r} does not match the canonical path derived "
                    f"from the patched frontmatter's name {name!r} ({canonical_target_rel!r}); "
                    "refusing to write to a different file than the one the patch was validated against"
                )
            }), file=sys.stderr)
            return 1

    promoted_at = now_iso()
    review_after = review_after_iso(promoted_at)
    final_content = content.replace(
        "craftflow-promoted-at: PENDING_APPROVAL", f"craftflow-promoted-at: {promoted_at}"
    ).replace(
        "craftflow-review-after: PENDING_APPROVAL", f"craftflow-review-after: {review_after}"
    )

    canonical_dir = project_root / ".claude" / "skills" / name
    canonical_path = canonical_dir / "SKILL.md"
    link_path = project_root / ".cursor" / "skills" / name

    # HIGH 3(b) (REM-FIX): serialize the write + cursor-sync + ledger-mark
    # sequence against a concurrent `--approve` invocation for the SAME
    # candidate/skill name, mirroring craftflow_skill_ledger.py's own
    # `_ledger_file_lock` pattern.
    #
    # CRITICAL (REM-FIX round 2, made unconditional in round 3): the
    # status-precondition check and the eventual write always happen inside
    # `ledger_mod._ledger_file_lock` -- the SAME lock `cmd_reject` itself
    # acquires -- so no write occurs unless the candidate is still in a
    # promotable state, and no concurrent --reject can slip in between the
    # check and the promote. This now applies unconditionally, regardless of
    # whether `--ledger` points at an existing file (see ROUND 3 note below).
    with _skill_promote_lock(canonical_dir):
        # ROUND 3 (REM-FIX): the previous `if ledger_path.exists(): <guarded>
        # else: <unguarded best-effort>` branching let a `--ledger` path that
        # simply didn't exist (a typo, stale automation, wrong cwd -- a
        # CONFIG difference, not a code difference) silently take the
        # unguarded else and write the canonical file with ZERO ledger
        # validation, even when the SAME candidate id is recorded "rejected"
        # in the real ledger elsewhere. `load_ledger` and `_ledger_file_lock`
        # both already tolerate a missing file (empty ledger, lock file
        # created on demand), so there is no reason to special-case
        # non-existence here at all -- always validate, exactly like
        # cmd_reject (craftflow_skill_ledger.py) has always done.
        with ledger_mod._ledger_file_lock(ledger_path):
            ledger = ledger_mod.load_ledger(ledger_path)
            # MEDIUM (REM-FIX round 5): a ledger corrupted to contain a
            # non-dict candidate entry (e.g. hand-edited or partially
            # migrated JSON) must never raise an uncaught AttributeError out
            # of this generator -- degrade to this script's documented
            # {"error": ...} JSON shape instead (still fails closed either
            # way; this is an error-contract fix, not a security bypass).
            entry = next(
                (
                    c for c in ledger.get("candidates", [])
                    if isinstance(c, dict) and c.get("id") == candidate_id
                ),
                None,
            )
            if entry is None:
                print(
                    json.dumps({"error": f"candidate id not found in ledger: {candidate_id!r}"}),
                    file=sys.stderr,
                )
                return 1
            # Mirrors cmd_reject's own precondition check verbatim
            # (craftflow_skill_ledger.py) -- both commands must agree on
            # which statuses are still mutable before any write.
            current_status = entry.get("status")
            if current_status not in ("candidate", "proposed"):
                print(
                    json.dumps({
                        "error": (
                            f"cannot approve candidate {candidate_id!r}: current status is "
                            f"{current_status!r}, expected 'candidate' or 'proposed' -- a concurrent "
                            "workflow may have already rejected or promoted it"
                        )
                    }),
                    file=sys.stderr,
                )
                return 1

            # CRITICAL (REM-FIX round 5): the check above only guards THIS
            # candidate's own current status -- it says nothing about whether
            # a DIFFERENT, already-promoted candidate owns the canonical path
            # this fresh SKILL.md is about to write to. `canonical_path` is
            # derived purely from the proposal's own `name:` frontmatter
            # field, never from `candidate_id`, so two entirely unrelated
            # candidates can legitimately declare the same `name`. Without
            # this check, approving candB here would silently overwrite
            # candA's already-promoted canonical file (and its synced
            # .cursor/skills symlink target), exit 0, and leave the ledger
            # recording BOTH candidates as promoted with the same
            # promoted_skill -- silent data corruption of an already-shipped
            # skill. Only applies to the fresh-SKILL.md (has_md) path: the
            # SKILL.patch (update) path already requires its target to
            # already be a real, self-consistent promoted skill (HIGH 4,
            # above) before it may be touched at all, so it cannot reach a
            # DIFFERENT candidate's canonical file under a mismatched name.
            if has_md and canonical_path.exists():
                # CRITICAL (REM-FIX round 6): `canonical_path` is a REAL
                # filesystem path, and this host's filesystem class (macOS
                # APFS, Windows NTFS) is case-insensitive-but-case-preserving
                # -- `canonical_path.exists()` above already correctly
                # detects "foo-skill" and "Foo-Skill" as the SAME physical
                # directory. But the conflicting-entry lookup below used to
                # compare `c.get("promoted_skill") == name` with exact,
                # case-SENSITIVE string equality, so a differently-cased
                # `promoted_skill` value (from an earlier, legitimately
                # different-cased promotion) would never match, the
                # conflicting-entry search would come back empty, and this
                # candidate's write would silently proceed onto the SAME
                # physical directory/file another candidate already owns.
                # Case-fold (strip + lower) both sides before comparing, so
                # the ledger-level check agrees with what the filesystem
                # itself already knows.
                name_cf = name.strip().lower()
                conflicting = next(
                    (
                        c for c in ledger.get("candidates", [])
                        if isinstance(c, dict)
                        and c.get("id") != candidate_id
                        and c.get("status") == "promoted"
                        and isinstance(c.get("promoted_skill"), str)
                        and c["promoted_skill"].strip().lower() == name_cf
                    ),
                    None,
                )
                if conflicting is not None:
                    print(
                        json.dumps({
                            "error": (
                                f"refusing to promote candidate {candidate_id!r}: canonical skill "
                                f"{name!r} is already promoted by a DIFFERENT candidate "
                                f"{conflicting.get('id')!r}; a fresh SKILL.md may never overwrite "
                                "another candidate's already-promoted skill"
                            )
                        }),
                        file=sys.stderr,
                    )
                    return 1

            atomic_write_text(canonical_path, final_content)
            sync_result = sync_cursor_skill(canonical_dir, link_path)

            entry["status"] = "promoted"
            entry["promoted_skill"] = name
            ledger_mod.save_ledger_atomic(ledger_path, ledger)
            ledger_marked = True

    print(json.dumps({
        "promoted": name,
        "candidate_id": candidate_id,
        "canonical_path": str(canonical_path),
        "cursor_sync": sync_result,
        "ledger_marked_promoted": ledger_marked,
        "craftflow_promoted_at": promoted_at,
        "craftflow_review_after": review_after,
    }, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Promote a staged Craftflow skill proposal to its canonical .claude/skills/ "
        "(and synced .cursor/skills/) location. Runs only on explicit --approve."
    )
    parser.add_argument("--approve", metavar="CANDIDATE_ID", required=True, help="Candidate id of the staged proposal to promote.")
    parser.add_argument("--proposals-dir", default=DEFAULT_PROPOSALS_DIR, help=f"Path to the skill-proposals directory (default: {DEFAULT_PROPOSALS_DIR})")
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT, help="Project root to write canonical skills into (default: .)")
    parser.add_argument("--ledger", default=DEFAULT_LEDGER_PATH, help=f"Path to the ledger file (default: {DEFAULT_LEDGER_PATH})")
    args = parser.parse_args()
    try:
        return cmd_approve(args)
    except (OSError, UnicodeDecodeError, AttributeError, TypeError) as exc:
        # MEDIUM 6 (REM-FIX): any unexpected OS-level failure (e.g.
        # `--project-root` pointing at an existing FILE rather than a
        # directory, tripping an unguarded `mkdir` deep inside
        # atomic_write_text) must degrade to this script's own clean JSON
        # error shape, never a raw Python traceback.
        # MEDIUM (REM-FIX round 5): `skill_md_path.read_text(encoding="utf-8")`/
        # `skill_patch_path.read_text(encoding="utf-8")` above raise
        # `UnicodeDecodeError` on non-UTF-8 proposal content -- a `ValueError`
        # subclass, NOT caught by a bare `except OSError`. Before this fix, a
        # non-UTF-8 staged proposal crashed with a raw Python traceback
        # instead of this script's documented `{"error": ...}` JSON shape.
        # `AttributeError`/`TypeError` are belt-and-suspenders here: the
        # candidate-lookup guard above already prevents a malformed non-dict
        # ledger entry from raising these, but this wrapper stays defensive
        # in case any other future code path hits an equivalent shape
        # mismatch against untrusted ledger/proposal content.
        print(json.dumps({"error": f"unexpected error during promotion: {exc}"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
