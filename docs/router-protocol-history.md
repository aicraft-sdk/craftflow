# Router Protocol History (Changelog)

Historical notes removed verbatim from `skills/_shared/router-protocol.md` to keep that
runtime-loaded file lean. Nothing here is normative; the shared doc is authoritative.

## Migration status blockquotes (Phase 3 / Phase 4)

> **Migration status (Phase 3, Claude side):** Phase 3 extracted the plan's two most
> clearly-delineated, cleanly-separable `shared`-classified sections — Intent Routing and
> the dispatch prompt scaffold. Phase 3b added `## 0. Resolve Project Root` (below, as
> "Resolve Project Root") — the resolution algorithm itself is identical in substance
> across hosts (Cursor's own text already said so directly before this extraction:
> "Resolve exactly as Claude Code's `craftflow-router/SKILL.md` does"). Phase 3c added
> the Skill-Distill Approval Flow. Phase 3d added the Explicit Dispatcher
> (Phase-to-Agent) table, as pure data. Phase 3e attempted `## 12. Chain Execution Loop`
> and made a deliberate no-op call — its policy logic is threaded sentence-by-sentence
> through literal `TaskList()` mechanics in the real text, not cleanly separable without
> either violating the purity boundary or the verbatim-move rule; it remains inline in
> `craftflow-router/SKILL.md` pending a future, deliberately-paraphrased treatment. Phase
> 3f added "Previous Agent Findings Handoff" — pure prose/template content with no
> host-specific tool syntax. Phase 3g added "Verifier Findings Handoff" — same kind of pure
> prose, explicitly cross-referenced by Cursor's own text as sharing the same
> fast-path-omission rule. Phase 3h investigated `### Parent workflow creation` and made a
> deliberate no-op call: the mapping table classified its JSON artifact schema as "shared"
> on the strength of Cursor's own prose claim ("same format... required for hook
> compatibility"), but direct comparison shows the actual schemas are NOT field-identical —
> Cursor's real schema is a much smaller subset (confirmed via exhaustive grep for ~10
> Claude-only field names, zero hits in cursor-router/SKILL.md). Moving Claude's full schema
> here would misrepresent what Cursor's file actually contains; flagged as a mapping-table
> correction candidate instead of extracted. Phase 3i (this pass) applied the same
> byte-comparison discipline to `## 2.`'s "shared" classification and found a partial match:
> the Memory File Required Sections table below IS byte-identical across hosts (moved here),
> but the JUST_GO rule in the same mapping-table row is NOT (Cursor's is a single terse
> sentence; Claude's has an AskUserQuestion-gate exception and a separate "v10 trust rule"
> paragraph with no Cursor equivalent) — JUST_GO stays inline in `craftflow-router/SKILL.md`.
> Phase 3j investigated `## 14. Hard Rules` and found zero bullets pass the
> byte-match-or-cross-reference test — a full no-op, no shared doc changes. Phase 3k (this
> pass) investigated `## 13. Memory Finalization` (full no-op — Cursor's own text
> self-labels it "a simplified subset... deferred to v2", an explicit non-equivalence
> disclaimer, not a same-rule cross-reference) and `## 8. Post-Agent Validation` (partial
> match — the read-only-agent fallback heading strings below ARE byte-identical, confirmed
> via direct grep, because both hosts dispatch the same underlying agent files; the
> extraction algorithm and full per-agent field tables are not). Several other sections the
> mapping table classifies
> `shared` (`### Parent workflow creation`'s artifact schema, `## 13. Memory
> Finalization`'s two-tier concept, `### Worktree Isolation`'s project-root-reuse text,
> `JUST_GO:`) remain deliberately left inline in `craftflow-router/SKILL.md` — see that
> file's own inline notes at each section, and the Phase 3/3b completion reports, for why:
> `craftflow_hook_unit_tests.py` anchors dense, exact-position, and in one case
> exact-match-paragraph assertions directly inside those sections, and a clean
> shared/host-specific split was not achievable without disproportionate regression risk
> relative to a follow-up, more carefully scoped sub-phase. This file will grow as those
> follow-ups land.
>
> **Migration status (Phase 4, Cursor side):** Phase 4a made `cursor-router/SKILL.md`
> aware of this file for the first time — a mandatory `Read()` note plus an explicit
> carve-out on the pre-existing "NEVER consult `craftflow-router/SKILL.md`" Hard Rule
> (Finding 1 from `docs/plans/2026-08-19-router-protocol-mapping.md`) — with zero content
> moved out of `cursor-router/SKILL.md` yet. Phase 4b replaced four low-risk items with
> pointers here: the Memory File Required Sections table, the Resolve Project Root
> multi-repo cross-reference (previously pointing at `craftflow-router/SKILL.md`, now
> pointing here directly since the algorithm no longer lives inline there either), the
> Previous Agent Findings Handoff template block, and the fast-path-omission
> cross-reference (previously citing `craftflow-router/SKILL.md` §12, now citing this
> file's own "Verifier Findings Handoff" section) plus the Skill-Distill Gate's intro
> cross-reference. Phase 4c added a cross-reference note to the Verdict-by-agent table
> (§ "Agent Verdict Headings" above is byte-identical for the 3 read-only-agent rows) —
> the table's actual pass-condition cell values stay inline verbatim, since they're
> operationally load-bearing, not documentation. Phase 4c also investigated the Dispatch
> Prompt Scaffold's literal fenced runtime template in `cursor-router/SKILL.md` § 5 and
> made a deliberate no-op call: see this section's own "Correction (Phase 4c)" note above
> for why — Cursor's real template is missing whole sections this shared scaffold has,
> not just a host-specific preamble. Still not touched: `### Phase chains by workflow
> type` (§5) — the phase→agent routing intent already lives in the Explicit Dispatcher
> table above; that section's own remaining content is Cursor-specific chain-execution
> mechanics with no clean shared/host-specific split attempted yet.

## Host note: Resolve Project Root (original text)

(Host-specific note: both hosts run this exact algorithm; `cursor-router/SKILL.md`
previously stated "Resolve exactly as Claude Code's `craftflow-router/SKILL.md` does" —
see its own binding doc for exactly when in its execution order this step runs, which
differs by host (Claude Code: once at session start, before `## 1.`; Cursor: inline at the
start of `## 4a. Worktree Isolation`), a sequencing detail, not a content difference.)

## Correction (Phase 4c), original text

**Correction (Phase 4c):** this scaffold's field list is NOT actually shared across hosts, despite the mapping table's original "field names and order match" claim — direct comparison found Cursor's real dispatch template omits `## Domain Context` and `## SKILL_HINTS` entirely, in addition to the already-known `Effort Directive` field gap. `cursor-router/SKILL.md` § 5's "Dispatch prompt template" therefore stays fully inline and literal — not pointer-ized — and self-documents this gap in its own "Simplified execution model" list. See `docs/plans/2026-08-19-router-protocol-mapping.md`'s Phase 3 corrections section for the mapping-table row fix.)
