#!/usr/bin/env python3
"""
craftflow_skill_propose.py

Deterministic staging of a skill-author DRAFT into the protected
`.craftflow/state/project/skill-proposals/<candidate-id>/` tree. REM-FIX
round 4 of the skill-distillation guardrail hardening
(docs/plans/2026-07-29-craftflow-skill-distillation-plan.md): after three
remediation rounds each found a deeper bypass in the file-existence-based
trust-boundary logic the guard used to protect this tree, the ledger and its
proposals are now protected the SAME way regardless of whether a path exists
yet -- which means `skill-author` can no longer write proposal files directly
via the `Write` tool. This script is the sole authorized writer of that tree,
mirroring `craftflow_skill_promote.py`'s/`craftflow_skill_ledger.py`'s
atomic-write pattern (tempfile.mkstemp + os.replace, same-directory temp
file) and `craftflow_skill_ledger.py`'s own `_ledger_file_lock` for the
load-validate-write-mark sequence.

Why this closes the precompute-squat hijack the prior three rounds could not:
`candidate_id()` is a deterministic `sha1(surface+signature)` hash --
precomputable offline with no observation needed -- and the ledger is freely
readable via `--query`. Any scheme that infers "this proposal path is
legitimate" from whether a file already exists at that path is exploitable:
an attacker can precompute the exact candidate-id a future legitimate
candidate will get and plant a file there first. Routing ALL proposal writes
through this script removes the need for the guard to infer trust from
existence at all -- the guard can protect the ENTIRE proposals tree
unconditionally (see `craftflow_pretooluse_guard.py`'s
`_is_protected_skill_ledger_or_proposal_path`), and this script is the one
place that decides whether a given candidate-id is legitimately eligible to
receive a proposal (status `"candidate"` in the ledger, not a terminal
`"rejected"`/`"promoted"` status) before ever touching disk.

CLI:
    --candidate-id <id>
    --skill-md-file <path to draft content>      (a scratch/temp location the
                                                    caller wrote its draft to
                                                    -- NEVER the protected
                                                    staging path itself)
    [--proposal-md-file <path to draft content>]
    --state-dir <path>              (default: .craftflow/state)
    [--project-root <path>]         (default: .)
    [--overwrite]                   (allow replacing an already-staged
                                      proposal for this candidate-id;
                                      idempotent re-propose is an explicit
                                      choice, never a silent default)

Behavior:
    1. Validate `--candidate-id` is a filesystem-safe token (reuses
       `craftflow_skill_promote.is_safe_token`).
    2. Read the draft `SKILL.md` content from `--skill-md-file` and validate
       its frontmatter (reuses `craftflow_skill_promote.parse_frontmatter` /
       `validate_frontmatter`: `name` present + safe token, `description`
       >= 40 chars) BEFORE any write.
    3. Load the ledger (`craftflow_skill_ledger.load_ledger`), holding
       `craftflow_skill_ledger._ledger_file_lock` for the ENTIRE
       validate-write-mark sequence below. Confirm the candidate-id exists
       with `status == "candidate"` -- `"rejected"`/`"promoted"` are
       terminal, staging a proposal for them makes no sense, and any other/
       missing status fails closed too. No write happens if this check
       fails.
    4. Refuse to overwrite an existing `SKILL.md` for this candidate-id
       unless `--overwrite` is passed.
    5. Atomically write `SKILL.md` (+ `PROPOSAL.md` if `--proposal-md-file`
       was given) under
       `<state-dir>/project/skill-proposals/<candidate-id>/`, then flip the
       ledger entry's `status` to `"proposed"` -- both inside the same
       locked operation, so a concurrent `--observe`/`--propose` cannot
       observe a half-done state.
    6. Emit `{"proposed": "<candidate-id>", "skill_md_path": "...",
       "proposal_md_path": "..."}` on success, or `{"error": "..."}` on any
       failure. Exit 0 on success, exit 1 on any validation/usage failure
       (no writes).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import craftflow_skill_ledger as ledger_mod  # noqa: E402
import craftflow_skill_promote as skill_promote  # noqa: E402

DEFAULT_STATE_DIR = ledger_mod.DEFAULT_STATE_DIR
DEFAULT_PROJECT_ROOT = "."

# Ledger statuses a candidate must be in to legitimately receive a proposal.
# Terminal statuses ("rejected", "promoted") are excluded on purpose --
# staging a proposal for a candidate that has already been rejected or
# promoted makes no sense and must fail closed, not silently succeed.
_PROPOSABLE_STATUS = "candidate"


def _error(message: str) -> int:
    print(json.dumps({"error": message}), file=sys.stderr)
    return 1


def cmd_propose(args) -> int:
    candidate_id = args.candidate_id
    if not skill_promote.is_safe_token(candidate_id):
        return _error(f"invalid or unsafe candidate id: {candidate_id!r}")

    skill_md_file = Path(args.skill_md_file)
    if not skill_md_file.is_file():
        return _error(f"--skill-md-file does not exist or is not a file: {skill_md_file}")

    proposal_md_file = Path(args.proposal_md_file) if args.proposal_md_file else None
    if proposal_md_file is not None and not proposal_md_file.is_file():
        return _error(f"--proposal-md-file does not exist or is not a file: {proposal_md_file}")

    project_root = Path(args.project_root)
    if not project_root.is_dir():
        return _error(f"--project-root does not exist or is not a directory: {project_root}")

    state_dir = Path(args.state_dir)
    ledger_path = state_dir / "project" / "skill-candidates.json"
    proposals_dir = state_dir / "project" / "skill-proposals"

    # Read + validate the draft BEFORE ever touching the ledger lock or the
    # protected staging path -- a bad draft must never partially stage.
    skill_md_content = skill_md_file.read_text(encoding="utf-8")
    fm = skill_promote.parse_frontmatter(skill_md_content)
    validation_error = skill_promote.validate_frontmatter(fm)
    if validation_error:
        return _error(f"frontmatter validation failed: {validation_error}")

    proposal_md_content = (
        proposal_md_file.read_text(encoding="utf-8") if proposal_md_file is not None else None
    )

    candidate_dir = proposals_dir / candidate_id
    skill_md_target = candidate_dir / "SKILL.md"
    proposal_md_target = candidate_dir / "PROPOSAL.md"

    with ledger_mod._ledger_file_lock(ledger_path):
        ledger = ledger_mod.load_ledger(ledger_path)
        entry = None
        for candidate in ledger.get("candidates", []):
            if isinstance(candidate, dict) and candidate.get("id") == candidate_id:
                entry = candidate
                break

        if entry is None:
            return _error(f"no ledger candidate found for id {candidate_id!r}")

        status = entry.get("status")
        if status != _PROPOSABLE_STATUS:
            return _error(
                f"candidate {candidate_id!r} has status {status!r}, expected "
                f"{_PROPOSABLE_STATUS!r} (terminal statuses cannot be re-staged)"
            )

        if skill_md_target.exists() and not args.overwrite:
            return _error(
                f"a proposal already exists for candidate {candidate_id!r} at "
                f"{skill_md_target}; pass --overwrite to replace it"
            )

        skill_promote.atomic_write_text(skill_md_target, skill_md_content)
        if proposal_md_content is not None:
            skill_promote.atomic_write_text(proposal_md_target, proposal_md_content)

        entry["status"] = "proposed"
        ledger_mod.save_ledger_atomic(ledger_path, ledger)

    print(json.dumps({
        "proposed": candidate_id,
        "skill_md_path": str(skill_md_target),
        "proposal_md_path": str(proposal_md_target) if proposal_md_content is not None else None,
    }, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Atomically stage a skill-author draft into the protected "
        ".craftflow/state/project/skill-proposals/<candidate-id>/ tree, and mark the "
        "matching ledger candidate 'proposed'. The sole authorized writer of that tree."
    )
    parser.add_argument("--candidate-id", required=True, help="Ledger candidate id to stage a proposal for.")
    parser.add_argument("--skill-md-file", required=True, help="Path to a scratch file holding the drafted SKILL.md content.")
    parser.add_argument("--proposal-md-file", default=None, help="Path to a scratch file holding the drafted PROPOSAL.md content.")
    parser.add_argument("--state-dir", default=DEFAULT_STATE_DIR, help=f"Path to the craftflow state directory (default: {DEFAULT_STATE_DIR})")
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT, help="Project root, used only to validate the invocation context (default: .)")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing an already-staged proposal for this candidate id.")
    args = parser.parse_args()
    try:
        return cmd_propose(args)
    except (OSError, UnicodeDecodeError) as exc:
        # MEDIUM (REM-FIX round 5): `.read_text(encoding="utf-8")` at the
        # `--skill-md-file`/`--proposal-md-file` read sites above raises
        # `UnicodeDecodeError` on non-UTF-8 draft content -- a `ValueError`
        # subclass, NOT caught by a bare `except OSError`. Before this fix, a
        # non-UTF-8 draft crashed with a raw Python traceback instead of this
        # script's documented `{"error": ...}` JSON shape.
        return _error(f"unexpected error reading draft content during proposal staging: {exc}")


if __name__ == "__main__":
    sys.exit(main())
