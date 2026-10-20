# Router rationale

Non-normative explanatory prose relocated out of the runtime router files by the context cut (SPEC-0034).
This file is not loaded at runtime. Each unit below is verbatim from `BASELINE_SHA` and has a row in
`router-policy-map.md` under Rationale. Normative text is never moved here.

## Future Third Router (Note, Not a Current Abstraction)

[NOTE — documentation only, no code implied by this section]: today there are exactly 2
routers (`craftflow-router/SKILL.md` for Claude Code, `cursor-router/SKILL.md` for Cursor),
and this file plus each binding doc's own host-specific sections already avoid scattering
per-host conditionals across call sites. Building a shared "runtime-adapter factory"
abstraction now would be speculative — there is no third runtime to abstract for. If a third
runtime/router is ever added, the recommended shape is a single factory function that picks
the correct tool name/example syntax per runtime (one function, one switch point) rather than
`if (runtime === ...)` conditionals scattered across binding docs — see
`docs/research/2026-08-29-agent-teams-ai-borrowable-ideas.md` idea #5 (pattern source:
agent-teams-ai's `memberMessagingProtocol.js`) for the full rationale. Revisit this note, not
the router split itself, when that day comes.
