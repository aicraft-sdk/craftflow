# Router doc test anchors

Created in P1 of the router context cut. Every test that reads the runtime router text (`skills/craftflow-router/SKILL.md`, `skills/_shared/router-protocol.md`, `skills/craftflow-router/references/*.md`) is classified here, so a later phase that moves prose knows which assertions it has to keep true or retarget.

Kinds:

- **C (content presence).** Reads the joined router corpus (`craftflow_router_corpus`), so the text may live in any runtime router file. Retargeted in P1; a phase that moves text needs no test edit. Owning phase `none`.
- **K (kernel structure or order).** Pins where a section sits, which file holds a pointer, or an order between two sections. Stays on the files it names. The owning phase is the phase that moves one of those sections and must retarget or extend the test in that same phase. `never-move` means no planned phase moves the pinned text (the test is expected to keep passing unchanged).
- **F (file-specific counts or presence).** Asserts a property of one named file (a per-file count, a table in one reference). A corpus-wide read would make it vacuous, so it stays file-specific. Same owning-phase rule as K.

`TBD-by-mover` means the plan does not say where the asserted text ends up; the phase that moves it assigns the retarget.

Not listed (matched by the P1 enumeration but they do not read the runtime router corpus): hook-suite `test_pretooluse_guard_allows_benign_stderr_redirect_to_dev_null` and `test_bash_guard_allows_benign_stderr_redirect_to_dev_null` (a path appears only in a comment), `test_cursor_router_shared_protocol_read_and_hard_rule_carveout`, `test_cursor_router_skill_md_wires_completed_cap` and `test_cursor_bootstrap_mdc_heading_references_resolve` (read `cursor-router/SKILL.md`, which no phase moves text out of except through the P11 pointer rows already covered by `test_cursor_router_shared_refs_checked`), `test_rubric_documents_three_rejection_cases`, `test_systematic_debugging_documents_sandbox_vs_real_failure_protocol` and `test_agents_only_read_references_of_their_own_skills` (other skills or agent files); and the P0 tooling fixtures (`test_router_xrefs.py` other than the three rows below, `test_rule_inventory*.py`), which build synthetic corpora from string constants. Also not listed, flagged by `tests/fixtures/router_test_enumerate.py` only because they mention a router path or helper without asserting router prose: hook-suite `test_install_cursor_sh_raw_github_urls_include_plugin_subpath` (install script URLs); `test_craftflow_cursor_exempt_wiring.py::test_exempt_block` (`cursor-router/SKILL.md`); `test_craftflow_jev_ab_report.py::test_manifest_by_call_id_is_reused_from_shared_lib_not_duplicated` (module import identity); `test_craftflow_jev_prompt_hint.py::test_roster_excludes_internal_and_host_skills_and_appends_none` and `::test_real_plugin_roster_size_matches_exclusion_rule` (skill roster, the router is only an excluded name); `test_craftflow_retro_v2.py::test_skill_recurrence_text`, `test_craftflow_retro_wiring.py::test_frontmatter` and `::test_boundary_statement` (the retro skill); `test_router_xrefs.py::test_runtime_files_sorted_and_excludes_maintainer`, `::test_read_corpus_from_ref_matches_tree` and `::test_h_h1_empty_corpus_and_cli_errors` (corpus loader behavior, no router prose).

Failure messages of the corpus-wide tests still say "SKILL.md missing ..." because rewording `fail(name, ...)` lines would break the P1 assertion-count proof; the Kind column, not the message, says what a test reads.

Reproduce the enumeration with `python3 tests/fixtures/router_test_enumerate.py [--names]`; `tests/fixtures/test_router_test_anchors.py` fails when a router-reading test is neither a row below nor named in the Not listed note, or when the row count line is stale.

Rows: 97 (C=35, K=38, F=24).

Paths are relative to the plugin root (`tools/craftflow-plugin/plugins/craftflow/`). Test paths use the short form `hook::` for `scripts/craftflow_hook_unit_tests.py` and `fx::` for `tests/fixtures/`.

| Test | Kind (C/K/F) | Anchored file#section | Owning phase |
|------|--------------|-----------------------|--------------|
| hook::test_corpus_helper_covers_skill_and_refs | C | corpus-wide smoke only: `## 14. Hard Rules` and `### BUILD preparation` anywhere in the corpus (the second heading exists in SKILL.md and build-workflow.md, so it pins no single file) | none |
| hook::test_router_dispatches_doubt_verify | C | corpus-wide token presence: `doubt-verify` and `doubt-verifier` anywhere in the corpus (`_shared/router-protocol.md` also carries them), so it does not pin SKILL.md#7. Dispatcher And Agent Prompt Contract | none |
| hook::test_router_records_reliability_gates_evidence_in_fix_verify_and_doubt_verify | C | corpus; SKILL.md#Doubt-Verify Dispatch Rule, #Fix-Verify Dispatch Rule (the Fix-Verify half reads `section_any`) | none |
| hook::test_router_documents_task_tool_capability_detection | C | SKILL.md#0a. Task Tool Capability Detection | none |
| hook::test_router_task_tool_probe_call_documented | C | SKILL.md#0a. Task Tool Capability Detection (`section_any`) | none |
| hook::test_router_task_tool_fallback_wired_through_resume_and_chain_loop | C | SKILL.md#4. Resume And Hydration, #12. Chain Execution Loop | P9 (adds the per-site assertions for `task-tool-fallback.md`; plan ln 1545) |
| hook::test_workflow_artifact_schema_documents_task_tools_available | C | references/workflow-artifact-and-hook-policy.md#`capabilities` bullet | none |
| hook::test_router_dispatches_intent_interview | C | SKILL.md#Intent Interview Gate (PLAN only) | none |
| hook::test_circuit_breaker_uses_persisted_non_telemetry_field_not_live_tasklist_count | K | references/remediation-and-research.md#Circuit breaker; references/workflow-artifact-and-hook-policy.md (bullet indentation of `circuit_breaker` vs `telemetry`) | P6 (loop-caps edits to remediation-and-research.md) and P11 (artifact-policy trim) |
| hook::test_section_0_precedes_memory_load | K | SKILL.md#0. Resolve Project Root before #2. Memory Load And Template Validation (order) | never-move (P10 keeps both in the kernel, in this order) |
| hook::test_memory_load_anchored_to_project_root | C | SKILL.md#2. Memory Load And Template Validation | none |
| hook::test_parent_workflow_creation_anchored_to_project_root | C | SKILL.md#Parent workflow creation | none |
| hook::test_parent_workflow_creation_fallback_reason_wired | C | SKILL.md#Parent workflow creation | none |
| hook::test_shared_preparation_anchored_to_project_root | C | SKILL.md#Shared preparation | none |
| hook::test_worktree_isolation_reuses_project_root_no_duplicate_resolution | K | SKILL.md (0 resolver references) and _shared/router-protocol.md (exactly 1); SKILL.md#Worktree Isolation (BUILD Default) | P10 (Worktree Isolation steps 1-3 move to build-workflow.md) |
| hook::test_memory_finalization_for_plan_anchored_to_project_tier | C | SKILL.md#13. Memory Finalization (`For PLAN:`) | none |
| hook::test_memory_finalization_for_debug_anchored_and_uses_workflow_uuid | C | SKILL.md#13. Memory Finalization (`For DEBUG:`) | none |
| hook::test_memory_finalization_prelude_anchored_to_project_root | C | SKILL.md#13. Memory Finalization | none |
| hook::test_just_go_and_scope_decision_resume_anchored_to_project_root | C | SKILL.md#2. Memory Load (`JUST_GO:`), #4. Resume And Hydration | none |
| hook::test_dispatcher_scaffold_workflow_artifact_anchored_to_project_root | K | SKILL.md#Prompt scaffold for every agent (pointer only) and _shared/router-protocol.md#Prompt scaffold for every agent (content) | never-move (scaffold stays in router-protocol.md; SKILL.md keeps the pointer) |
| hook::test_memory_finalize_permit_standalone_documented | C | SKILL.md#13. Memory Finalization (permit guidance) | none |
| hook::test_router_one_call_minting_documented | C | SKILL.md#Parent workflow creation | none |
| hook::test_router_snippets_avoid_llm_quoted_requests_and_eval | K | SKILL.md (`--request-file`, #Parent workflow creation) and _shared/router-protocol.md, each checked separately | P10 (Parent workflow creation moves to workflow-start.md) and P11 (protocol split) |
| hook::test_router_mint_command_passes_safe_shell_guard | C | SKILL.md#Parent workflow creation (bash blocks) | none |
| hook::test_state_digest_required_sections_match_router_protocol_table | C | _shared/router-protocol.md#Memory File Required Sections (table rows) | none |
| hook::test_router_memory_load_documents_digest_reread_rule | C | SKILL.md#2. Memory Load And Template Validation | none |
| hook::test_router_request_paths_are_write_guard_safe | K | SKILL.md (`.router-request-{session_id}.txt`, read through the corpus) and _shared/router-protocol.md#Resolve Project Root step 1a (scratchpad, no TMPDIR; read directly) | P11 (step 1a moves to `router-protocol-multirepo.md`) |
| hook::test_worktree_isolation_resolver_gated_on_toplevel_failure | K | _shared/router-protocol.md#Resolve Project Root (gate before resolver) and SKILL.md#0. Resolve Project Root (pointer before mkdir) | P11 (step 1a, which holds the resolver reference, moves to `router-protocol-multirepo.md`) |
| hook::test_worktree_isolation_step_4a_derives_from_worktree_path | C | SKILL.md#Worktree Isolation (BUILD Default) step 4a | none |
| hook::test_router_uses_workflow_id_helper | C | corpus-wide token presence: `craftflow_workflow_id.py`, `worktree_dir`, `worktree_branch` anywhere in the corpus (`worktree_dir` is also in references/workflow-artifact-and-hook-policy.md), so it does not pin SKILL.md#Parent workflow creation | none |
| hook::test_craftflow_router_shared_protocol_extraction_no_stale_reembed | K | SKILL.md (absent) and _shared/router-protocol.md (present) for 15 moved literals | P11 (each literal that moves to a new `_shared/` file) |
| hook::test_cursor_router_shared_protocol_extraction_batch1_no_stale_reembed | K | cursor-router/SKILL.md (absent) and _shared/router-protocol.md (present): memory table, handoff template block | P11 (Previous Agent Findings Handoff moves to `verifier-handoff.md`) |
| hook::test_memory_finalize_instruction_sites_use_state_query_full_mode | F | references/{plan,build,debug,review}-workflow.md Memory Update task description (count 1/2/1/1) | P9 |
| hook::test_memory_finalize_instruction_sites_wire_archive_rotation | F | same four files, marker `write archive_path FIRST` | P9 |
| hook::test_memory_finalize_instruction_sites_document_entries_unit | F | same four files, marker `unit mode, notes are prepended as new raw-text entries` | P9 |
| hook::test_memory_finalize_instruction_sites_prefer_apply | F | same four files via `_memory_finalize_doc_problems` (`--apply <destination_file_path>` count) | P9 |
| hook::test_memory_finalize_instruction_sites_forbid_raw_envelope | F | same four files via `_memory_finalize_doc_problems` (JSON-envelope marker) | P9 |
| hook::test_memory_finalize_instruction_sites_drop_dangling_phase3_reference | F | same four files via `_memory_finalize_doc_problems` | P9 |
| hook::test_memory_finalize_instruction_sites_have_no_inline_heredoc_instruction | F | same four files via `_memory_finalize_doc_problems` | P9 |
| hook::test_memory_finalize_instruction_sites_preserve_protected_marker_counts | F | same four files via `_memory_finalize_doc_problems` | P9 |
| hook::test_craftflow_router_documents_state_read_compaction_self_heal | C | corpus-wide token presence: `craftflow_state_query.py` and `state-read-compaction` anywhere in the corpus (5-6 router files carry them), so it does not pin the SKILL.md#2 self-heal paragraph | none |
| hook::test_skill_md_wires_project_tier_last_updated_and_completed_caps | C | SKILL.md#13. Memory Finalization | none |
| hook::test_router_phase_enum_registers_skill_distill_learn_distill_doubt_verify | C | SKILL.md#3. Task Metadata Contract (`phase:{...}` line) | none |
| hook::test_router_dispatcher_table_includes_skill_distill | K | SKILL.md#Explicit dispatcher (pointer) and _shared/router-protocol.md#Explicit Dispatcher (Phase-to-Agent Table) (row) | never-move (the dispatcher table stays in router-protocol.md) |
| hook::test_router_effort_dispatch_includes_skill_distill_low | C | SKILL.md#Effort Dispatch Rule | none |
| hook::test_router_contract_overrides_includes_skill_author | C | SKILL.md#Contract overrides (skill-author row) | none |
| hook::test_router_memory_finalization_calls_ledger_observe | C | SKILL.md#13. Memory Finalization | none |
| hook::test_router_memory_finalization_calls_ledger_prune | C | SKILL.md#13. Memory Finalization | none |
| hook::test_router_hard_rules_includes_skill_distill_skip | C | SKILL.md#14. Hard Rules | none |
| hook::test_router_documents_skill_distill_approval_flow | K | SKILL.md#Skill-Distill Approval Flow (pointer) and _shared/router-protocol.md#Skill-Distill Approval Flow (markers) | P11 (moves to `_shared/skill-distill-approval.md`) |
| hook::test_build_workflow_wires_learn_distill_taskcreate | F | references/build-workflow.md (`phase:learn-distill` TaskCreate); corpus-wide would be satisfied by debug-workflow.md | P11 (BUILD graphs move to `build-graph-standard.md`) |
| hook::test_build_workflow_wires_skill_distill_taskcreate | F | references/build-workflow.md (`phase:skill-distill`, `gate_eligible`) | P11 |
| hook::test_build_workflow_fast_path_graph_wires_learn_and_skill_distill | F | references/build-workflow.md#BUILD task graph - fast path | P11 (moves to `build-graph-fast.md`) |
| hook::test_debug_workflow_documents_skill_distill_reasoning | F | references/debug-workflow.md (`skill-distill`); corpus-wide would be vacuous | never-move (debug-workflow.md is not split) |
| hook::test_fast_path_agent_dispatch_table_includes_skill_distill | F | references/fast-path.md#Agent Dispatch Table (`skill-distill` row); the protocol has a same-shaped row | never-move (fast-path.md stays in place; only `fix_verify_keyword_scan` leaves, in P10) |
| hook::test_fast_path_escalated_gate_wiring_reconciled | F | references/fast-path.md#Agent Dispatch Table and #Gate Table | never-move |
| hook::test_fast_path_documents_skill_distill_gate | F | references/fast-path.md#Skill-Distill Gate | never-move |
| hook::test_workflow_artifact_policy_registers_skill_distill_events | F | references/workflow-artifact-and-hook-policy.md (event registrations) | P11 (artifact-policy trim; keep the event list in place) |
| hook::test_router_template_stamps_session_id | C | SKILL.md#Parent workflow creation, #4. Resume And Hydration; references/workflow-artifact-and-hook-policy.md (session_id rules) | none |
| hook::test_workspace_root_config_read_gated_inside_step_1a | K | _shared/router-protocol.md#Resolve Project Root, step 1a (slices between step 1a and the single-repo branch) | P11 (step 1a moves to `router-protocol-multirepo.md`) |
| hook::test_workflow_artifact_template_includes_workspace_writable_paths_field | C | SKILL.md#Parent workflow creation | none |
| hook::test_workspace_setup_and_routers_cross_reference_membership | K | craftflow-router/SKILL.md#2. Memory Load (workspace-tier walk, `NOT membership-gated`, read directly through `skill_dir`, not the corpus) and cursor-router/SKILL.md (same literal) | P10 (the step 5a workspace-walk details move to `memory-load-workspace.md`; retarget the craftflow-router half to the corpus or that file, keep the cursor-router half) |
| fx::test_craftflow_context_nudge.py::test_router_context_boundary_reference_contract | F | references/context-boundary.md | never-move |
| fx::test_craftflow_context_nudge.py::test_router_pointer_counts | K | references/build-workflow.md (`_POINTER` count == 2), references/remediation-and-research.md (count == 2; pass-1 and pass-2 heading order), references/plan-workflow.md (count 0) | P11 (pointer count; the build split moves the pointers) and P6 (pass-1/pass-2 heading order in remediation-and-research.md; BC-6: P6 keeps it true without editing the test) |
| fx::test_craftflow_context_nudge.py::test_router_pointers_avoid_memory_finalize_markers | K | references/context-boundary.md and the `_POINTER` lines of build-workflow.md and remediation-and-research.md | P11 (pointer lines move with the build split) |
| fx::test_craftflow_context_nudge.py::test_hook_inventory_docs_mention_context_nudge | F | references/workflow-artifact-and-hook-policy.md (hook policy bullets) | P11 (hook policy moves to `hooks/README.md`) |
| fx::test_craftflow_context_nudge.py::test_context_boundary_reference_ready_to_paste_line | F | references/context-boundary.md | never-move |
| fx::test_craftflow_context_nudge.py::test_docs_name_user_override_path | F | references/workflow-artifact-and-hook-policy.md (user override path) plus README files | P11 (hook policy moves to `hooks/README.md`) |
| fx::test_craftflow_jev_heuristic.py::test_parity_with_router_protocol_markdown | K | _shared/router-protocol.md#Intent Routing table (parsed and compared with `INTENT_TABLE`) | never-move |
| fx::test_craftflow_jev_heuristic.py::test_parity_with_fast_path_markdown (:54) | K | references/fast-path.md#risk_keyword_scan keyword table | never-move |
| fx::test_craftflow_jev_heuristic.py::test_skill_rule_source_lines_still_present (:94) | K | SKILL.md#Deterministic skill hints (two pinned rule lines) | never-move |
| fx::test_craftflow_jev_heuristic.py::test_remfix_scope_heuristic_matches_documented_recommendation (:107) | K | references/remediation-and-research.md (`Fix critical only (Recommended)`) | never-move |
| fx::test_craftflow_jev_prompt_hint.py::test_router_docs_carry_jev_precedence_rules | K | _shared/router-protocol.md (shared rule, Host-specific additions) and SKILL.md#1. Intent Routing (pointer sentence, no duplicate) | never-move (the Intent Routing table and Jev contract stay in router-protocol.md) |
| fx::test_craftflow_jev_prompt_hint.py::test_readme_and_hook_inventory_docs_document_jev | F | references/workflow-artifact-and-hook-policy.md (hook policy) plus README files | P11 (hook policy moves to `hooks/README.md`) |
| fx::test_craftflow_jev_session_check.py::test_router_docs_carry_jev_consent_request_contract | K | _shared/router-protocol.md (consent-request tag contract) and SKILL.md (pointer only) | never-move |
| fx::test_craftflow_jev_session_check.py::test_readme_and_hook_inventory_docs_document_session_check | F | references/workflow-artifact-and-hook-policy.md (SessionStart jev bullet) plus README files | P11 (hook policy moves to `hooks/README.md`) |
| fx::test_craftflow_jev_remfix_scope.py::test_remfix_scope_call_site_is_structurally_isolated_to_1a_scope | K | references/remediation-and-research.md#Scope resolution (call site), #Circuit breaker, #Verifier REVERT gate (line ranges) | P6 (adds gate-check call lines to remediation-and-research.md; must keep this true) |
| fx::test_craftflow_jev_remfix_scope.py::test_build_workflow_escalated_path_delegates_instead_of_duplicating | F | references/build-workflow.md (`1a-SCOPE`, `Scope resolution`) and references/remediation-and-research.md | P11 (build-workflow.md split) |
| fx::test_craftflow_jev_remfix_scope.py::test_skill_md_interrupted_jev_resume_checks_existence_before_create | C | SKILL.md#4. Resume And Hydration (interrupted jev-auto-decide rule) | none |
| fx::test_craftflow_jev_remfix_scope.py::test_remfix_scope_script_appears_in_exactly_one_router_doc_file | F | every `*.md` under skills/craftflow-router (exactly one file, `remediation-and-research.md`) | never-move |
| fx::test_agent_structure.py [test_skill_md_write_literal_declares_bakeoff_fields] | C | artifact written by `scripts/craftflow_workflow_id.py --init-artifact` (no router prose) | none (fixed in P1) |
| fx::test_agent_structure.py [test_dispatcher_table_lists_bakeoff_phases] | K | _shared/router-protocol.md#Explicit Dispatcher (Phase-to-Agent Table) | never-move |
| fx::test_agent_structure.py [test_optional_sections_lists_candidates] | K | _shared/router-protocol.md#Prompt assembly rule (optional sections list) | never-move |
| fx::test_agent_structure.py [test_dispatch_time_prompt_assembly_covers_bakeoff] | K | references/plan-workflow.md (dispatch-time prompt assembly) | P11 (plan-workflow.md split into `plan-bakeoff.md`; TBD-by-mover for the assembly block) |
| fx::test_agent_structure.py [test_bakeoff_fanout_block] | K | references/plan-workflow.md (bake-off fan-out) | P11 (moves to `plan-bakeoff.md`) |
| fx::test_agent_structure.py [test_judge_dispatch_and_post_judge_validation_blocks] | K | references/plan-workflow.md#PLAN judge dispatch, post-judge validation | P11 (moves to `plan-bakeoff.md`) |
| fx::test_agent_structure.py [test_skill_md_phase_enum_lists_bakeoff_phases] | K | SKILL.md#3. Task Metadata Contract (`phase:{...}` line) | P10 (§ 3 moves to `workflow-start.md`) |
| fx::test_agent_structure.py [test_skill_md_chain_loop_has_5b_bakeoff_step] | K | SKILL.md#12. Chain Execution Loop step 5b | P11 (step 5b moves to `plan-bakeoff.md`) |
| fx::test_agent_structure.py [test_skill_md_write_agent_table_has_judge_row] | K | SKILL.md#8. Post-Agent Validation (write-agent table) | P10 (§ 8 moves to `post-agent-validation.md`) |
| fx::test_agent_structure.py [test_skill_md_contract_overrides_has_judge_row] | K | SKILL.md#Contract overrides | P10 (moves to `post-agent-validation.md`) |
| fx::test_agent_structure.py [test_skill_md_phase_enum_lists_bakeoff_opus_exact_count] | K | SKILL.md#3. Task Metadata Contract (exact count of `plan-bakeoff-candidate-opus`) | P10 (§ 3 moves to `workflow-start.md`) |
| fx::test_agent_structure.py [test_router_protocol_dispatcher_has_judge_row_exact_count] | K | _shared/router-protocol.md#Explicit Dispatcher (Phase-to-Agent Table) | never-move |
| fx::test_agent_structure.py [test_router_protocol_optional_sections_lists_target_plan_file] | K | _shared/router-protocol.md#Prompt assembly rule | never-move |
| fx::test_agent_structure.py [test_plan_workflow_has_dispatch_time_prompt_assembly_heading] | K | references/plan-workflow.md (dispatch-time prompt assembly heading) | P11 (TBD-by-mover) |
| fx::test_router_xrefs.py::test_section_any_with_hint_resolves | K | SKILL.md#BUILD preparation and references/build-workflow.md#BUILD preparation (both must exist, differ) | P10 (Fast Path Detection and Worktree steps move into build-workflow.md; the kernel keeps the pointer heading) |
| fx::test_router_xrefs.py::test_known_sites_are_parsed | K | the `KNOWN` list: cross-reference sites in SKILL.md, harness-self-checks.md, cursor-router/SKILL.md and their target files | TBD-by-mover (P9, P10 and P11 each update the minimum counts for the sites they move) |
| fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked | K | cursor-router/SKILL.md references into _shared/router-protocol.md (at least 5, all resolving) | P11 (Cursor pointers retarget to the new `_shared/` files) |
