# AGENTS.md Validation Rules

Reference for the rules enforced by `tools/scripts/agents-md-lint.js`.

---

## Root AGENTS.md requirements

| Rule | Detail |
|---|---|
| **Must exist** | `AGENTS.md` at the repo root is mandatory |
| **Size limit** | ≤ 8 192 bytes (8 KB) UTF-8 byte length |
| **Required sections** | Five sections must be present as markdown headers |
| **Forbidden patterns** | Must not contain `/src/`, `/lib/`, `/components/` |

### Required section matching (flexible)

The linter matches section headers case-insensitively and allows an optional numeric prefix (e.g., `## 1. Project / Scope`):

| Section name | Accepted header variants |
|---|---|
| Project / Scope | `Project / Scope`, `Project / Scope Identification` |
| Non-Negotiable | `Non-Negotiable`, `Non-Negotiable Constraints` |
| Build | `Build`, `Build, Run & Test` |
| Security | `Security`, `Security & Safety` |
| Agent Operating | `Agent Operating`, `Agent Operating Rules` |

Any markdown heading level (`#`, `##`, `###`) is accepted.

---

## Subfolder AGENTS.md requirements

| Rule | Detail |
|---|---|
| **Overrides only** | Must NOT redefine root section titles |
| **Forbidden patterns** | Same as root: no `/src/`, `/lib/`, `/components/` |
| **Size limit** | Same 8 KB cap applies |

The linter checks these exact root section header strings in subfolder files (and rejects if found):
- `Project / Scope Identification`
- `Non-Negotiable Constraints`
- `Build, Run & Test`
- `Security & Safety`
- `Agent Operating Rules`

Structure subfolder AGENTS.md with a custom heading like `## Local Overrides` or `## {{Language}} Notes`.

---

## Forbidden patterns

The linter uses these RegExp patterns:

```
/src/        →  matches any occurrence of "src/"
/lib/        →  matches any occurrence of "lib/"
/\/components\//  →  matches "/components/"
```

**Compliant alternatives:**

| Forbidden | Use instead |
|---|---|
| `look in /src/` | `look in the source directory` |
| `shared /lib/ utilities` | `shared utility libraries` |
| `import from /components/` | `import from the shared component library` |
| `lives in src/providers/` | `lives in the providers module` |

---

## Exit code behavior

```
Exit 0  →  All checks passed ("AGENTS.md lint passed" printed to stdout)
Exit 1  →  One or more violations (error message printed to stderr, immediate exit on first failure)
```

The linter exits immediately on the first violation found (fail-fast). Fix violations one at a time and re-run.

---

## Directories skipped during traversal

The linter skips these directories automatically:
- `.git`
- `node_modules`
- `.next`
- `dist`
- `build`
- `.cursor`

It also calls `git check-ignore` for each path and skips gitignored entries.

---

## AGENTS.md content guidelines (not enforced by linter)

These are not enforced by the linter but are part of the playbook contract:

- Do not hard-code ephemeral file paths (use prose descriptions instead)
- Link to `AI_FIRST.md` for the charter reference in §3 Non-Negotiable
- Link to `AI.md` and `TESTS.md` in §8 Agent Operating Rules (AI Context Pack)
- Link to all `docs/ai/*` files in §7 Progressive Documentation Index
- Keep §4 Build commands exact and runnable (copy-paste ready)
- Use `{{PLACEHOLDER}}` tokens for project-specific values the user must fill in

---

## Validator configuration (`.agents-md-validator.json`)

```json
{
  "maxSizeBytes": 8192,
  "requiredSections": {
    "root": ["Project / Scope", "Non-Negotiable", "Build", "Security", "Agent Operating"]
  },
  "forbiddenPatterns": ["/src/", "/lib/", "/components/"],
  "exemptions": [],
  "aiContractPack": {
    "severity": "warn",
    "scanRoots": ["{{SCAN_ROOTS}}"],
    "designPathPatterns": [],
    "exemptions": []
  }
}
```

Note: `requiredSections` in the JSON config is used for documentation/tooling only. The linter's required section patterns are hardcoded in the script and match the variants listed in the table above. The JSON config primarily controls `aiContractPack` behavior.
