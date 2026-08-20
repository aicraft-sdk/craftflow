# Diagram Design — Anti-Pattern Checklist

Run this checklist against every diagram before shipping. Each row is a common "AI slop" tell —
a pattern that reads as generated rather than deliberately designed.

| # | Anti-pattern | Why it fails |
|---|--------------|---------------|
| 1 | Blanket monospace font for all text regardless of content type | Names/labels should be sans (humanist); mono is reserved for genuinely technical content (ports, URLs, commands, field types) — see `style-guide.md`. Using mono everywhere is the single most common tell of a templated diagram. |
| 2 | Unnecessary drop shadows on shapes | Borders and stroke weight communicate hierarchy more clearly than shadows; shadows add visual noise without adding information. |
| 3 | Identical box styling for every node regardless of role | Erases hierarchy — focal, backend, store, and external nodes should look visually distinct (stroke color, fill, or shape) so the reader can tell them apart at a glance. |
| 4 | Floating legend that overlaps or collides with diagram content | A legend that competes with the diagram for space undermines its own purpose; place it clearly outside all diagram boundaries with enough margin. |
| 5 | Arrow labels with no background/mask | An unmasked label lets the connector line visually bleed through the text, making both illegible; give arrow labels an opaque or matching-background mask. |
| 6 | Diagonal/slanted connectors between off-grid nodes | Prefer orthogonal (90-degree) routing; diagonal lines between arbitrarily placed nodes read as unplanned rather than deliberate. |
| 7 | Accent/focal color used on more than 1-2 elements | Erases the "what matters most" signal — see the 1-2 accent max rule in `style-guide.md`. |
| 8 | Generic Mermaid-style auto-layout look (uniform rounded boxes, uniform spacing, no deliberate hierarchy) | Reads as machine-generated rather than an editorial, considered layout — vary size, weight, and spacing to reflect actual structure. |

**Run this checklist before writing the output file; fix any failure before shipping.**
