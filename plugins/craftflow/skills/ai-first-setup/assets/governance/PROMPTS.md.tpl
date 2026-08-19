# Prompts and AIDLC usage

## Skills and CLI

- Development workflows are driven by skills, rules, and commands installed via the AI resources CLI (see `project-config.json`).
- The **`aidlc`** set is declared in `project-config.json`. After clone, run (with registry auth for the `@bcai` scope if applicable):
  ```
  npx --yes @bcai/ai-resources-cli set install aidlc --no-open
  ```
  then run `{{INSTALL_CMD}}` (the postinstall wrapper runs project sync / ensure). Commit any updated lock artifacts the CLI produces.
- If `@bcai/ai-resources-cli` is not available (registry not configured), the `postinstall-ai-resources.sh` wrapper exits 0 and prints a hint.

## Router

- The AIDLC entry skill is the `cc10x-router` (or equivalent AI resources router skill installed in your editor/CLI).
- Use it for build / plan / debug / review routing.

## Practical flow in this repo

1. Create or update `docs/ai/specs/<id>.md`.
2. Let the router produce or reference `docs/plans/<slug>.md`.
3. Implement with TDD; run `{{TEST_CMD}}` (and other targets your plan lists).
4. Put **Spec:** and **Plan:** links plus verification output in the PR ([pull request template](../../.github/pull_request_template.md)).

## Commands from the `aidlc` set

Use whatever slash-commands or agent prompts the installed `aidlc` set provides; this file intentionally does not duplicate them (they ship with the set and may vary by version).
