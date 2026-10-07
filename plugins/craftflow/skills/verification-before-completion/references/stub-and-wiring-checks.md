<!-- Moved verbatim from the verification-before-completion SKILL.md preload (PR 2 split). Contents: Goal-backward template, stub, wiring, export and auth checks. -->

## Goal-Backward Lens (GSD-Inspired)

After standard verification passes, apply this additional check:

### Three Questions
1. **Truths:** What must be TRUE? (observable user or business outcomes)
2. **Artifacts:** What must EXIST? (files, endpoints, tests, records)
3. **Wiring:** What must be WIRED? (component → API → database)

### Why This Catches Stubs
A component can:
- Exist ✓
- Pass lint ✓
- Have tests ✓
- But NOT be wired to the system ✗

### Quick Check Template
```
GOAL: [What user wants to achieve]

TRUTHS (observable):
- [ ] [User-facing behavior 1]
- [ ] [User-facing behavior 2]

ARTIFACTS (exist):
- [ ] [Required file/endpoint 1]
- [ ] [Required file/endpoint 2]

WIRING (connected):
- [ ] [Component] → [calls] → [API]
- [ ] [API] → [queries] → [Database]

Standard verification: exit code 0 ✓
Goal check: All boxes checked?
```

### When to Apply
- After integration-verifier runs
- After any "feature complete" claim
- Before marking BUILD workflow as done

**Iron Law unchanged:** Exit code 0 still required. This is an additional verification lens, not a replacement.

## Stub Detection Patterns

After Goal-Backward Lens passes, scan for these stub indicators:

### Universal Stubs
```bash
# Check for TODO/placeholder markers
grep -rE "TODO|FIXME|placeholder|not implemented|coming soon" --include="*.ts" --include="*.tsx" --include="*.js"

# Check for empty returns
grep -rE "return null|return undefined|return \{\}|return \[\]" --include="*.ts" --include="*.tsx"
```

### React Component Stubs
| Pattern | Why It's a Stub |
|---------|-----------------|
| `return <div>Placeholder</div>` | Renders nothing useful |
| `onClick={() => {}}` | Click does nothing |
| `onSubmit={(e) => e.preventDefault()}` | Only prevents default, no action |
| `useState` with no setter calls | State never changes |

### API Route Stubs
| Pattern | Why It's a Stub |
|---------|-----------------|
| `return Response.json({ message: "Not implemented" })` | Explicit stub |
| `return Response.json([])` without DB query | Returns empty, no real data |
| `return NextResponse.json({})` with no logic | Empty response |

### Function Stubs
| Pattern | Why It's a Stub |
|---------|-----------------|
| `throw new Error("Not implemented")` | Will crash at runtime |
| `console.log("TODO")` | Debug artifact |
| `// TODO: implement` | Marked incomplete |

### Quick Stub Check
```bash
# Run before claiming completion
grep -rE "(TODO|FIXME|placeholder|not implemented)" src/
grep -rE "onClick=\{?\(\) => \{\}\}?" src/
grep -rE "return (null|undefined|\{\}|\[\])" src/
```

**If any stub patterns found:** DO NOT claim completion. Fix or document why it's intentional.

### Wiring Verification (Component → API → Database)

Artifacts can exist, pass lint, and have tests but NOT be wired to the system.

**Component → API Check:**
```bash
# Does component actually call the API?
grep -E "fetch\(['\"].*api|axios\.(get|post)" src/components/
# Is response actually used?
grep -A 5 "fetch\|axios" src/components/ | grep -E "await|\.then|setData|setState"
```

**API → Database Check:**
```bash
# Does API actually query database?
grep -E "prisma\.|db\.|mongoose\." src/app/api/
# Is result actually returned?
grep -E "return.*json.*data|Response\.json" src/app/api/
```

**Red Flags:**
| Pattern | Problem |
|---------|---------|
| `fetch('/api/x')` with no `await` | Call ignored |
| `await prisma.findMany()` → `return { ok: true }` | Query result discarded |
| Handler only has `e.preventDefault()` | Form does nothing |

**Line Count Minimums:**
| File Type | Minimum Lines | Below = Likely Stub |
|-----------|---------------|---------------------|
| Component | 15 | Too thin |
| API route | 10 | Too thin |
| Hook/util | 10 | Too thin |

### Export/Import Verification

Exports can exist but never be consumed. Check that key exports are actually used:

```bash
# Check if export is imported AND used (not just imported)
check_export_used() {
  local export_name="$1"
  grep -r "import.*$export_name" src/ --include="*.ts" --include="*.tsx" | wc -l
  grep -r "$export_name" src/ --include="*.ts" --include="*.tsx" | grep -v "import\|export" | wc -l
}

# Example: Check auth exports are consumed
check_export_used "getCurrentUser"
check_export_used "useAuth"
```

**Export Status:**
| Status | Meaning | Action |
|--------|---------|--------|
| CONNECTED | Imported AND used | ✓ Good |
| IMPORTED_NOT_USED | Import exists but never called | Remove dead import or implement |
| ORPHANED | Export exists, never imported | Dead code or missing integration |

### Auth Protection Verification

Sensitive routes must check authentication:

```bash
# Find routes that should be protected
protected_patterns="dashboard|settings|profile|account|admin"
grep -r -l "$protected_patterns" src/app/ --include="*.tsx"

# For each, verify auth usage
check_auth_protection() {
  local file="$1"
  grep -E "useAuth|useSession|getCurrentUser|isAuthenticated" "$file"
  grep -E "redirect.*login|router.push.*login" "$file"
}
```

**If sensitive route lacks auth check:** Add protection before claiming completion.

## The Bottom Line

**No shortcuts for verification.**

Run the command. Read the output. THEN claim the result.

This is non-negotiable.
