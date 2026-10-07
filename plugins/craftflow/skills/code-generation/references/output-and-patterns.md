<!-- Moved verbatim from the code-generation SKILL.md preload (PR 2 split). Contents: Output format, common patterns, final rule. -->

## Output Format

```markdown
## Code Implementation

### Functionality
[What this code does]

### Universal Questions Answered
1. **Functionality**: [answer]
2. **Users**: [answer]
3. **Inputs**: [answer]
4. **Outputs**: [answer]
5. **Edge cases**: [answer]
6. **Existing patterns**: [answer]

### Implementation

```typescript
// Code here
```

### Key Decisions
- [Decision 1 and why]
- [Decision 2 and why]

### Assumptions
- [Assumption 1]
- [Assumption 2]
```

## Common Patterns

### Functions
```typescript
// Clear name, typed parameters and return
function calculateOrderTotal(items: OrderItem[]): Money {
  if (!items.length) {
    return Money.zero();
  }
  return items.reduce(
    (total, item) => total.add(item.price.multiply(item.quantity)),
    Money.zero()
  );
}
```

### Components (React example)
```typescript
interface UserCardProps {
  user: User;
  onSelect?: (user: User) => void;
}

export function UserCard({ user, onSelect }: UserCardProps) {
  if (!user) {
    return null;
  }

  return (
    <div
      className="user-card"
      onClick={() => onSelect?.(user)}
      role="button"
      tabIndex={0}
    >
      <span>{user.name}</span>
    </div>
  );
}
```

### Error Handling
```typescript
// Match project error patterns
async function fetchUser(id: string): Promise<Result<User>> {
  try {
    const response = await api.get(`/users/${id}`);
    return Result.ok(response.data);
  } catch (error) {
    logger.error('Failed to fetch user', { id, error });
    return Result.err(new UserNotFoundError(id));
  }
}
```

## Final Rule

```
Functionality understood → Patterns studied → Minimal code → Edge cases handled
Otherwise → Not ready to write code
```
