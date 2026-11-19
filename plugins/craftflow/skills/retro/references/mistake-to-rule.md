# From agent mistake to rule

How a repeated agent mistake becomes a check that fires next time. Read by humans and by agents (the retro skill, any
agent drafting a proposal). Only the arch-lint path below has tooling; the other rows say who acts and how.

## When a mistake qualifies

Trigger on the **second occurrence** of the same mistake, or the first if it is severe (data loss, secret leak, wrong
root writes). A first, non-severe occurrence gets nothing yet: note it and wait. A mistake becomes a **check** only if
all four hold:

1. A program can decide it from the file text alone (no judgment about intent or quality).
2. The fix is stated in one sentence and is easy to apply.
3. It has no legitimate exceptions, if it will ever block. A rule with exceptions stays advisory or becomes reviewer prose.
4. It will plausibly recur. A one-off cleanup is a task, not a rule.

If one fails, do not write a check; use the "judgment" row below.

## Pick the mechanism by the shape of the mistake

| Shape of the mistake | Mechanism | Who acts |
|---|---|---|
| Banned import or layer boundary (`from '<prefix>` / `require('<prefix>`) | arch-lint rule in `.craftflow/state/project/arch-rules.json` | agent drafts the JSON, human adds it |
| Another syntactic pattern in this repo's Python | ruff rule in `ruff.toml`, or a clean-state check class | human (agents never edit `ruff.toml`) |
| Behavioural bug | regression test written to fail first, then fix | agent, in the normal TDD flow |
| Process step skipped (ran X without Y) | hook in `hooks/hooks.json` | human reviews; hooks gate agent behaviour |
| Judgment call, preference, or anything with exceptions | reviewer prose in `skills/code-review-patterns/SKILL.md` | agent proposes text, human merges |
| First occurrence, not severe | nothing yet | record it |

Never add steering-file prose for something a check can decide, and never add a check for something only judgment can decide.

## Steps for an arch-lint rule

Registry rule fields: `id`, `scope_globs`, `forbidden_import_prefixes`, `what`, `why`, `fix`, optional `mode`
(`advisory` default, `blocking` exits 2), and, new, `origin`, `owner`, `added`, `examples`. The detector matches literal
import statements only (see the script docstring for what it cannot see).

1. **Write the rule JSON as one object** (the rule itself, not a registry). Write `what`, `why` and `fix` as the
   instruction the next agent will read when it fires: `what` names the violation, `why` the reason in one sentence,
   `fix` the concrete change to make.
2. **Record provenance**: `origin` (the incident or evidence: workflow id, retro signal, commit), `owner` (who decides to
   keep or delete it), `added` (`YYYY-MM-DD`). Both `origin` and `owner` are mandatory for `blocking`.
3. **Add examples**: `"examples": {"bad": [{"path": "<inside scope_globs>", "content": "<file text>"}], "good": [...]}`
   with a real violation and a real compliant file, saved in the rule so they can be re-run later.
4. **Prove it**: `python3 <plugin>/scripts/craftflow_arch_lint.py --check-proposal proposal.json`. Exit 0 means every bad
   example fires and every good one is silent; 2 means an example behaves wrongly or one kind is missing; 1 means the
   JSON is invalid. It never touches the registry and ends by printing the human's next step. If it cannot be run, say
   why in the proposal.
5. **Hand the JSON to the user.** The pre-tool-use guard denies agent writes to `arch-rules.json`; the human adds the
   object to the `rules` array.
6. **Start advisory.** Findings only reach the reviewer's prompt; the exit code stays 0. Check the registry later with
   `craftflow_arch_lint.py --self-test`, which re-runs every rule's examples without scanning the project.
7. **Promote to blocking** (a human edits `"mode": "blocking"`) only after the rule has run clean on real work, has
   `origin`, `owner` and both example kinds. Without them the tool refuses the registry (exit 1 or 2).
8. **Delete the prose line the check replaces** (a CLAUDE.md or skill sentence saying "never import X"), so the rule is
   the single source and the prose cannot drift.

## What an agent must never do

- Edit `arch-rules.json`, or lower a rule's `mode` from `blocking`.
- Add a `# noqa` / `# type: ignore` or a per-file ignore to make a rule or lint pass, or raise any ratchet baseline or
  weaken `config/hook-mode.json`. Fix the code, or report the rule as wrong.
- Present an advisory-only check as enforcement.
- Propose a blocking rule that has not run as advisory, or that has no failing bad example.

## Pruning

Hit counts per rule are not tracked yet, so pruning is a human review, not an automated report. Demote a rule
(`blocking` to `advisory`) or delete it when:

- it has produced no findings over a long period and the mistake it targeted no longer appears in retros;
- it keeps producing false positives, or people suppress or work around it (a rule needing exceptions fails test 3);
- its `why` no longer holds (the boundary moved, the dependency is gone);
- its `owner` is gone and nobody will take it.

Deleting a rule also deletes its examples, so remove its row from any docs that list it. Record the reason in the commit.
