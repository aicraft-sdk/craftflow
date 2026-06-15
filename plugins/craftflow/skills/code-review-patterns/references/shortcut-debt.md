# Shortcut Debt Convention

## The `cf:shortcut:` comment

When a deliberate simplification is made — a known ceiling accepted to keep a diff small — mark it in-code so it is not mistaken for ignorance and can be tracked over time.

**Format:**
```
// cf:shortcut: <what was simplified>; <ceiling or upgrade trigger>
```

**Examples:**
```typescript
// cf:shortcut: linear scan; build an index when list grows past ~1k items
const found = items.find(i => i.id === targetId);

// cf:shortcut: in-process cache; move to Redis when multi-instance deployment needed
const cache = new Map<string, Result>();

// cf:shortcut: polling every 5s; replace with WebSocket when UI latency becomes a complaint
setInterval(fetchStatus, 5000);
```

**Rules:**
- The comment names the ceiling and the upgrade trigger, not just "TODO refactor"
- A `cf:shortcut:` with no upgrade trigger is rot-risk — the reviewer will flag it
- Once the upgrade trigger is met, the shortcut should be revisited and the comment removed

## Debt Harvest (Review Step)

During Pass 5 (Structural Simplification), the reviewer scans changed files for `cf:shortcut:` markers:

```bash
grep -rn "cf:shortcut:" <changed-files>
```

Each marker produces one of:
- **Clean:** has a clear ceiling + trigger → noted in Memory Notes as tracked debt
- **Rot-risk:** missing upgrade trigger → flagged as `shrink:` finding (MEDIUM)
- **Resolved:** the trigger condition was met and the shortcut was addressed → noted as resolved

## Repo-wide Harvest

To produce a full ledger of all tracked shortcuts across the codebase:

```bash
grep -rnE "(#|//) ?cf:shortcut:" . \
  --include="*.ts" --include="*.tsx" --include="*.js" --include="*.py" \
  --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=dist
```

Output format for the ledger:
```
<file>:<line> — <what was simplified>; ceiling: <limit>. upgrade: <trigger>.
```

Flag any entry with no semicolon-separated trigger with `no-trigger` — these are the ones that rot silently.

End the ledger with: `N markers, M with no trigger.`
