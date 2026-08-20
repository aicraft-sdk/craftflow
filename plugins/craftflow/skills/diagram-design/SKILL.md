---
name: diagram-design
description: "Use when the user asks for an architecture, flowchart, sequence, state machine, ER, timeline, swimlane, or quadrant diagram — craftflow's own token-driven, multi-type, anti-pattern-gated diagram skill (distinct from the global single-type ~/.claude/skills/architecture-diagram skill)."
allowed-tools: Read Write Grep Glob
user-invocable: false
---

# Diagram Design

## Overview

A diagram is a claim about structure. It earns its place only when a reader learns the
structure faster from the picture than from prose. This skill covers structural/relational
diagram types — architecture, flowchart, sequence, state machine, ER, timeline, swimlane,
quadrant, and similar — with a shared token system, complexity budget, and anti-pattern gate.

## Reference Files

- `references/style-guide.md` — the single source of truth for colors and typography. Every
  token name used below (`paper`, `ink`, `muted`, `accent`, `accent-tint`, `link`) is defined
  there. Diagrams must never hardcode a hex value or font name inline — always resolve through
  this file.
- `references/anti-patterns.md` — the shipping checklist. Every diagram must be checked against
  it before being shipped.

## Philosophy: Complexity Budget

Every node must earn its place. Prefer deletion over addition — if a node, label, or connector
doesn't change what the reader understands, cut it.

- **Soft ceiling: ~9-12 nodes.** Past that, a single diagram usually stops communicating and
  starts overwhelming.
- If a diagram needs more than ~9-12 nodes, either:
  - split it into two diagrams along a natural seam (e.g. request path vs. data path), or
  - reconsider whether prose or a table would communicate the relationship better than a
    picture at all.
- Density is not sophistication. A diagram with fewer, well-chosen nodes beats one that tries
  to show everything.

## When To Use

Use this skill for diagrams where a reader learns more from a visual than from prose because
the value is in the *relationships*, not just the parts:

- System/software architecture
- Flowcharts (decision/process flow)
- Sequence diagrams (call/message order)
- State machines
- Entity-relationship (ER) diagrams
- Timelines
- Swimlanes
- Quadrant charts
- Other structural/relational diagrams with the same shape of problem

## When Not To Use

- A simple list — just write the list.
- A single before/after comparison — a two-column table or a sentence communicates it as well
  or better, with far less production cost.
- A one-shape "diagram" (a single box, a single arrow) — that's a sentence, not a diagram. Write
  the sentence.

## Design System

All colors and typography route through named semantic tokens defined in
`references/style-guide.md` — never inline a hex value or a font name directly in a diagram or
in this file. The token set: `paper` (background), `ink` (primary text/stroke), `muted`
(secondary text/default arrow stroke), `accent` (1-2 focal elements per diagram, max),
`accent-tint` (fill for accent-bordered boxes), `link` (external/API-call arrows). See that file
for default values, the sans-vs-mono typography rule, and the 1-2 accent max rule.

## Anti-Pattern Gate

Before shipping any diagram, check it against every item in
`references/anti-patterns.md`. This is a hard gate, not a suggestion — a diagram that fails any
item on that checklist is not done.

## Output Format

Produce a single self-contained `.html` file with inline SVG for the diagram and inline/embedded
CSS for styling. No external JS dependency, no build step — the file must open directly in a
browser. This mirrors the output contract already used by the existing
`~/.claude/skills/architecture-diagram` skill; keep new diagrams consistent with that contract
so all diagram output in this environment behaves the same way for the user.

## Relationship To Existing Skills

The user's existing global `~/.claude/skills/architecture-diagram` skill (not craftflow-owned,
out of scope here, not modified by this skill) covers one diagram type — dark-themed
architecture diagrams — with a single hardcoded color palette and font.

**Known gap, noted for the record:** that skill hardcodes JetBrains Mono as a blanket font for
all text (component names, sublabels, annotations alike). `references/anti-patterns.md` in this
skill explicitly calls that pattern out as anti-pattern #1 — mono should be reserved for
genuinely technical content, not applied to every text element. This skill's semantic token
system (see `references/style-guide.md`) separates sans/humanist type for names and labels from
monospace reserved for technical content, avoiding that mistake by construction rather than by
after-the-fact cleanup.

This skill is invoked via router dispatch (Deterministic Skill Hints) when a diagram/
architecture/flowchart/sequence-diagram-shaped request is in scope for the current workflow, not
invoked directly by the user.
