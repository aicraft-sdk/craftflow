# Code Review Heuristics

## Table of Contents
- [Maintainability Scan](#maintainability-scan)
- [Performance Scan](#performance-scan)
- [Hidden Failure Scan](#hidden-failure-scan)
- [Edge-Case Taxonomy](#edge-case-taxonomy)
- [Sloppy Pattern Scan](#sloppy-pattern-scan)
- [UI Quick Scan](#ui-quick-scan)
- [Type Design Red Flags](#type-design-red-flags)
- [Structural Simplification Scan](#structural-simplification-scan)

## Maintainability Scan

Look for:
- unclear naming
- multiple responsibilities in one function
- dense nested branching
- duplication with minor variations
- hardcoded values that should be centralized

Flag only when the problem materially increases bug risk or future change cost.

## Performance Scan

Look for:
- N+1 database or network calls
- expensive work inside loops or render paths
- missing early returns on large collections
- repeated parsing, sorting, or serialization
- missing caching where the code clearly expects reuse

Performance concerns without concrete impact are soft findings, not automatic
blockers.

## Hidden Failure Scan

Watch for patterns that suppress truth:
- optional chaining that swallows missing state without logging
- fallback defaults masking null or undefined source errors
- catch-log-continue flows that never surface failure to the caller
- retries that end silently
- background jobs that drop errors into logs only

Hidden failures are high value because tests often miss them.

## Edge-Case Taxonomy

Use only the categories relevant to the change:
- missing else/default branch
- unguarded input
- off-by-one bounds
- arithmetic edge case
- implicit type coercion
- race condition or shared mutable state
- timeout, retry, or cancellation gap

Do not run the full taxonomy mechanically. Pick the few categories the code
shape makes plausible.

## Sloppy Pattern Scan

After the main review, do a quick debt scan:
- dead imports
- leftover debug prints
- commented-out code blocks
- inconsistent naming in the same file
- copy-paste blocks with tiny variations

These usually produce LOW or MEDIUM findings unless they touch trust-critical
paths.

## UI Quick Scan

If the diff includes UI:
- loading, error, empty, and success states exist
- interactive elements use semantic controls
- focus and keyboard behavior are preserved
- labels and names exist for controls

For deep UI review, pair this with `frontend-patterns` references.

## Type Design Red Flags

Typed code deserves an extra pass for:
- mutable internals exposed to callers
- invariants documented but not enforced
- constructors or factories that allow invalid objects
- anemic models where domain rules live in unrelated helpers

Flag these when they make the current change unsafe or misleading, not as a
generic architecture lecture.

## Structural Simplification Scan

Run after the main review. Ask "what could disappear?" before "what could be cleaner?"

**Code-judo check** — look for reframings that delete whole categories of complexity:
- Can a branch, helper, mode, or layer be eliminated by restructuring the approach?
- Does the state model have a simpler form where some conditionals become unnecessary?
- Is there an ownership change that makes the feature a natural extension of an existing abstraction?
- Would renaming, reordering, or inverting the control flow make the logic feel inevitable?

**File-size check:**
- Did this PR push a file from below 1k lines to above 1k lines? Flag as HIGH.
- If so, ask whether helpers, subcomponents, or focused modules should be extracted first.
- Waive only when there is a compelling structural reason and the file remains clearly organized.

**Anti-spaghetti check:**
- New ad-hoc conditionals bolted onto unrelated existing flows → design problem, not a style nit
- Special-case branches scattered across shared code to solve a feature-local problem
- One-off booleans or nullable modes that complicate existing control flow
- "Temporary" branching that is likely to become permanent debt
Prefer: push the logic into a dedicated abstraction, helper, state machine, or separate module.

**Abstraction quality check:**
- Thin wrappers or identity abstractions that add indirection without simplifying the API
- Bespoke near-duplicate helpers when a canonical utility already exists
- Generic mechanisms hiding simple data-shape assumptions
- Abstractions justified only by hypothetical future requirements

**Boundary and layer check:**
- Feature-specific logic leaking into general-purpose or shared modules
- Logic placed in the wrong package, service, or module when a clear canonical home exists
- Implementation details exposed through a layer boundary that should hide them

**Orchestration check:**
- Independent async work serialized for no good reason (could stay parallel?)
- Related updates that can leave state half-applied (could be made more atomic?)

**Preferred remedies (structural):**
- Delete a layer of indirection rather than polish it
- Reframe the state model so conditionals disappear
- Extract a helper or pure function; split a large file into focused modules
- Replace condition chains with a typed model or explicit dispatcher
- Move logic to the package/module that already owns the concept
- Reuse the canonical helper instead of introducing a near-duplicate
- Separate orchestration from business logic

Flag structural issues only when they materially worsen maintainability or represent a missed
opportunity for a dramatic simplification. Do not surface cosmetic structure nits as regressions.
