# Diagram Design — Style Guide

**Single source of truth for diagram colors and typography.** `SKILL.md` and every diagram
output must reference roles by name below — never hardcode a hex value or font name inline.

## Semantic Color Tokens

| Token | Role | Default Value |
|-------|------|----------------|
| `paper` | Background / canvas | `#0b1120` |
| `ink` | Primary text, primary stroke, default node border | `#e2e8f0` |
| `muted` | Secondary text, default arrow stroke, de-emphasized elements | `#64748b` |
| `accent` | Focal element(s) — 1-2 max per diagram | `#38bdf8` |
| `accent-tint` | Fill for accent-bordered boxes (low-opacity accent) | `rgba(56, 189, 248, 0.14)` |
| `link` | External/API-call arrows, cross-boundary edges | `#f59e0b` |

These defaults form one coherent editorial dark palette. Swap them per project only by editing
this file — never by hardcoding a substitute hex value in `SKILL.md` or in generated output.

## Typography

| Use case | Font family | Reasoning |
|----------|-------------|-----------|
| Names, labels, titles, prose annotations | A sans/humanist font (e.g. system-ui, Inter, "Segoe UI") | Names are not code — a humanist face reads faster and looks intentional, not templated |
| Technical content only: ports, URLs, commands, field types, status codes | A monospace font (e.g. "JetBrains Mono", ui-monospace) | Monospace signals "this is literal, copy-pasteable" — reserve it for content that actually is |

**Anti-pattern:** applying one blanket monospace font to every piece of text regardless of
content type. Monospace is a signal, not a default aesthetic — using it everywhere erases the
signal and produces the generic "AI-generated technical diagram" look. See
`anti-patterns.md` for the full checklist.

## The 1-2 Accent Max Rule

`accent` marks the 1-2 elements in a diagram that matter most for the point being made — the
node under discussion, the new component, the failure point. Everything else uses `ink` or
`muted`.

Using `accent` on many elements erases the signal it exists to carry: if everything is
highlighted, nothing is. When in doubt, default a node to `ink`/`muted` and only promote it to
`accent` if the diagram's whole point is to draw the eye there.

## Usage Contract

- Reference tokens by name (`accent`, `ink`, `muted`, `paper`, `accent-tint`, `link`) in prose
  and in generated SVG/CSS comments.
- Resolve token names to actual values only at the point of rendering, using the values in the
  tables above.
- Never write a bare hex value or a bare font-family string directly into `SKILL.md` guidance —
  always route through a token name defined here.
