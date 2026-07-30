# Anti-Slop Rubric

Work through these 4 rules **in order**. Rules 1 and 2 are pre-checks you
confirm are already satisfied by construction (no re-derivation needed). Rules
3 and 4 are the real, answerable checks you perform yourself. Any failure at
rule 3 or 4 stops the process at `STATUS: SKIPPED`.

## Rule 1 — Occurrences already counted correctly (pre-satisfied, do not re-check)

`craftflow_skill_ledger.py`'s `upsert_candidates()` already counts
`distinct_workflows` as unique workflow identities, never raw event/signal
counts. A single workflow that retries the same failure 5 times contributes
exactly 1 occurrence, not 5. By the time a candidate reaches `skill-author`,
this is already true of the `distinct_workflows` field on the ledger entry.

**What to do here:** nothing. Read `distinct_workflows` from the candidate as
given. Do not recount evidence entries yourself.

## Rule 2 — Gate threshold already satisfied (pre-satisfied, do not re-check)

`gate_eligible()` requires `distinct_workflows >= 2`. A candidate only reaches
`skill-author` because it already passed this gate (the router only dispatches
`skill-distill` for gate-eligible candidates, or the candidate id was passed
explicitly for a candidate that already satisfies the gate).

**What to do here:** nothing beyond a sanity confirmation — if `distinct_workflows < 2`
somehow appears on the candidate you were handed, that is a caller error, not a
rubric failure; treat it the same as "no candidate provided" in the Trigger
Classifier (`STATUS: SKIPPED`) rather than silently promoting a sub-threshold candidate.

## Rule 3 — Reject if already documented (REAL CHECK)

**Question:** is this lesson already stated in `CLAUDE.md`, `AGENTS.md`, or
`.craftflow/state/project/patterns.md` under `## Common Gotchas`?

**How to check:**

1. `Read` all three files that exist in this repo:
   - `CLAUDE.md` (repo root)
   - `AGENTS.md` (repo root)
   - `.craftflow/state/project/patterns.md` — read only the `## Common Gotchas`
     section if the file exists; treat a missing file, or a file with no
     `## Common Gotchas` section, as "nothing documented there" (not an error).
2. `Grep` each file's content for keywords drawn from the candidate's `surface`
   and `signature` fields (file/directory names, function/variable names,
   distinctive phrases).
3. Read any matching lines in context (a keyword hit alone is not proof —
   confirm the surrounding sentence actually describes the same lesson).

**What counts as "already stated":**
- An exact or near-exact restatement of the candidate's signature.
- A close paraphrase that describes the same underlying lesson using different
  words (e.g. candidate says "retry loop double-counts singleton occurrences",
  existing gotcha says "don't let internal retries inflate occurrence counts" —
  same lesson, different phrasing, still counts as already-documented).
- A narrower or broader statement that clearly subsumes the candidate's lesson
  (e.g. an existing gotcha already covers "always run `nvm use 22.14.0` before
  any `nx` command" and the candidate is specifically about `nx test` needing
  Node 22 — the general gotcha already covers the specific case).

**What does NOT count as "already stated":**
- A superficially similar keyword appearing in an unrelated context.
- A different lesson about the same file/surface (e.g. an existing gotcha about
  a *different* bug in the same file does not cover a *new* bug in that file).

**On failure (already documented):** `STATUS: SKIPPED`,
`SKIP_REASON: "Rule 3 (already documented): <quote the existing line/paragraph> in <file> already states this lesson"`.

## Rule 4 — Reject if no executable artifact (REAL CHECK)

**Question:** does the candidate's `evidence[]` array (from the ledger schema)
contain anything resembling a command with a known/expected output, a
`file:line` reference, or a concrete, named code pattern? Or is every entry
pure prose ("be careful with X", "watch out for Y")?

**How to check:**

1. Read every entry in the candidate's `evidence[]` array (each has `wf`, `ts`,
   `source`, `text`).
2. For each entry's `text`, classify it against these three acceptable shapes:
   - **Command shape:** contains a runnable command and a stated or clearly
     implied expected output/exit behavior (e.g. `results.verifier` `SCENARIOS`
     entries — the highest-value source, since those are commands that already
     proved something).
   - **`file:line` shape:** contains a path-like reference, ideally with a line
     number (`path/to/file.ts:42`), or at minimum a real, checkable file path in
     this repo (confirm the path exists via `Glob`/`Bash(command="test -f ...")`
     before crediting it).
   - **Concrete code pattern shape:** names specific functions, variables,
     types, or a specific structural pattern (e.g. "static Map + lookupProfile +
     registerProfile registry with a warn-once dedup Set") that a future agent
     could search for and verify with `Grep`, even without an explicit
     `file:line`.
3. If **at least one** evidence entry matches any of the three shapes above,
   the candidate has an executable artifact — rule 4 passes.
4. If **every** evidence entry is pure prose advice with none of the three
   shapes (no command, no file reference, no named code construct), rule 4
   fails.

**On failure (no executable artifact):** `STATUS: SKIPPED`,
`SKIP_REASON: "Rule 4 (no executable artifact): all N evidence entries are prose-only advice with no command, file:line, or named code pattern"`.

## After Both Real Checks Pass

Proceed to write the staged proposal (`skill-distillation/SKILL.md`'s Staging
Write section). The `## Verified Commands` section of the produced `SKILL.md`
must be populated from whichever evidence entries satisfied rule 4 — quote the
actual command/pattern found, not a paraphrase.

## Three Reference Rejection Cases

These three shapes are the canonical negative fixtures used to keep this
rubric honest (see `craftflow_hook_unit_tests.py`'s structural rubric tests):

1. **Already-in-gotchas:** a candidate whose signature is a near-paraphrase of
   an existing `patterns.md ## Common Gotchas` line → rejected under Rule 3.
2. **Prose-only, no executable artifact:** a candidate whose every evidence
   entry is advice text with no command, file reference, or named code
   construct → rejected under Rule 4.
3. **Duplicate of an existing skill:** the Dedup Check (in `SKILL.md`, run
   before the rubric) finds an existing `SKILL.md` with materially overlapping
   `description:` frontmatter → not a rubric rejection, but redirects the
   candidate to an UPDATE proposal instead of a new-skill proposal.
