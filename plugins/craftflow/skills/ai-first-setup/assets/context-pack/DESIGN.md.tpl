# {{PROJECT_NAME}} — Design

<!-- Per-project DESIGN.md for UI projects only. -->
<!-- For TUI/CLI projects: use docs/ai/DESIGN.md instead. -->

## Figma

- Project file: {{FIGMA_URL}}
- Component page: {{FIGMA_COMPONENTS_URL}}

## Storybook

- URL: {{STORYBOOK_URL}}
- Run locally: `{{STORYBOOK_CMD}}`

## Token source

- Design tokens: `{{TOKEN_PACKAGE}}`
- Do not hard-code hex colors, spacing, or typography; use tokens only.

## Component notes

<!-- Project-specific component conventions or overrides. -->

{{COMPONENT_NOTES}}

## Figma → code workflow

1. Export or copy from Figma using the approved scaffold tool.
2. Treat output as draft: normalize imports, naming, tokens, accessibility, and tests.
3. Open PR with design reference link in the description.
