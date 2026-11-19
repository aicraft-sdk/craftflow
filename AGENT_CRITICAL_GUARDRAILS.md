# Craftflow Plugin — Critical Guardrails

Hard rules only. One bullet, no exceptions. For context, see `README.md`. The rationale and
decision docs live in the source monorepo this repository is published from; they are not in this repo.

- Never bypass `craftflow-router`. It is the only entry point — no direct Edit/Write/Bash
  for code changes without routing through it first.
- TDD is mandatory: RED (failing test) → GREEN (minimal passing code) → REFACTOR. No
  production code without a failing test first.
- File size: prefer < 400 lines per file, max 500; functions ~30–40 lines. Enforced by
  `python3 plugins/craftflow/scripts/craftflow_file_size_ratchet.py` (oversized files may only shrink).
- Never hand-edit `CHANGELOG.md`'s released sections or any version field in this plugin
  (`plugins/craftflow/.claude-plugin/plugin.json`, `plugins/craftflow/.cursor-plugin/plugin.json`
  and the marketplace manifests). Releases are cut by the source monorepo's pipeline, not by
  anything in this repo — manual edits are overwritten or fail its consistency gate.
- Never skip TDD, review, or verifier phases "just this once" — no fresh evidence, no
  completion claim.
- Never edit `.craftflow/state/*.md` memory files directly from a write agent — emit
  structured `MEMORY_NOTES`; only the router/workflow finalizer persists memory.
- No secrets, tokens, or private keys committed — use environment variables.
- No `# noqa` / `# type: ignore` without a rule code and a reason (suppression ratchet, clean-state check).
- Never raise a ratchet baseline, add a ruff per-file-ignore, or weaken `hook-mode.json` to make a check
  pass: CI rejects it (`craftflow_baseline_integrity.py`). Ask the user.
- Spec before code — no implementation without a linked feature spec or approved plan.
