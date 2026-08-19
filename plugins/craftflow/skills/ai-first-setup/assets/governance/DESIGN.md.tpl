# Design reference

<!-- For UI projects (react/next/vite/vue/svelte): fill in Figma, Storybook, and token sections below. -->
<!-- For TUI/CLI projects: replace Figma section with ASCII layout reference; keep Storybook section empty or remove. -->

## Source of truth

<!-- UI projects: -->
- **Figma** defines visual design, components, and tokens for product UI. Link: {{FIGMA_URL}}
- **Code** consumes design tokens and shared components from the designated component library.

<!-- TUI / CLI projects: replace the block above with: -->
<!-- **ASCII layout reference** defines the visual structure of the interface. Describe key screens below. -->

## Tokens

- Token files live under the designated shared component library.
- Do not hard-code hex colors or ad-hoc spacing scales in feature code; use tokens or theme utilities provided by the shared library.
- For TUI/CLI projects: terminal colors are defined in the project's theme module; do not use raw ANSI codes outside that module.

## Storybook

<!-- UI projects only: -->
- Shared UI components should have **Storybook stories** where the project already uses Storybook.
- Stories document states (default, loading, error, empty) relevant to acceptance criteria.
- Run locally: `{{STORYBOOK_CMD}}`

<!-- TUI/CLI projects: remove Storybook section or leave with a note that it does not apply. -->

## Figma → code workflow

<!-- UI projects: -->
- Use the approved design-to-code scaffold path for this project (see team documentation).
- Treat scaffold output as **draft**: normalize imports, naming, tokens, accessibility, and tests before opening a PR.

<!-- TUI/CLI projects: replace with: -->
<!-- New screens are described first as ASCII art or table layouts in this document, reviewed, then implemented. -->

## ASCII layout reference (TUI / CLI projects)

<!-- Fill in for each key view. Example format: -->
<!--
### Main screen

```
┌─────────────────────────────────┐
│ {{PROJECT_NAME}} v1.0           │
├─────────────────────────────────┤
│ > Option 1                      │
│   Option 2                      │
│   Option 3                      │
└─────────────────────────────────┘
```
-->

{{DESIGN_NOTES}}

## Per-project notes

- Component notes and overrides specific to this project: {{COMPONENT_NOTES}}
