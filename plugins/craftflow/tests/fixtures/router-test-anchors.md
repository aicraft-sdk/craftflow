# Router doc test anchors

Created in P1 of the router context cut. Every test that reads the runtime router text (`skills/craftflow-router/SKILL.md`, `skills/_shared/router-protocol.md`, `skills/craftflow-router/references/*.md`) is classified here, so a later phase that moves prose knows which assertions it has to keep true or retarget.

Kinds:

- **C (content presence).** Reads the joined router corpus (`craftflow_router_corpus`), so the text may live in any runtime router file. Retargeted in P1; a phase that moves text needs no test edit. Owning phase `none`.
- **K (kernel structure or order).** Pins where a section sits, which file holds a pointer, or an order between two sections. Stays on the files it names. The owning phase is the phase that moves one of those sections and must retarget or extend the test in that same phase. `never-move` means no planned phase moves the pinned text (the test is expected to keep passing unchanged).
- **F (file-specific counts or presence).** Asserts a property of one named file (a per-file count, a table in one reference). A corpus-wide read would make it vacuous, so it stays file-specific. Same owning-phase rule as K.

`TBD-by-mover` means the plan does not say where the asserted text ends up; the phase that moves it assigns the retarget.

Excluded tests. `tests/fixtures/router_test_enumerate.py` flags a test when its source (or a same-file helper it uses) names a router path, a corpus helper or any `SKILL.md`, so it also flags tests that mention a path without asserting runtime router prose. Each such test is excluded by exactly one line of the form `Not listed: <label>::<test> — <reason>` below. The label is `hook` or `fx::<file>`, the reason is mandatory, and a bare test name classifies nothing, so an exclusion cannot match a same-named test in another file. The P0 tooling fixtures (`test_router_xrefs.py` other than the rows below and the exclusions, `test_rule_inventory*.py`) build synthetic corpora from string constants and are not enumerated.

- Not listed: hook::test_intent_interview_skill_present — asserts that another skill's `SKILL.md` exists, not router prose.
- Not listed: hook::test_pretooluse_guard_allows_unrelated_hand_authored_skill_write_no_ledger — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_edit_write_to_claude_skills_skill_md — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_edit_write_to_cursor_skills_skill_md — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_bash_redirect_to_claude_skills_skill_md — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_python_oneliner_write_to_inflight_skill — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_python_os_system_write_to_inflight_skill — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_python_heredoc_write_to_inflight_skill — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_tamper_then_write_via_ledger_write — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_write_to_skill_proposal_file — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_write_to_not_yet_existing_proposal_path — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_bash_redirect_to_not_yet_existing_proposal_path — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_python_oneliner_write_to_not_yet_existing_proposal_path — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_bash_redirect_to_skill_ledger — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_write_when_ledger_json_is_malformed — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_any_skill_write_when_ledger_malformed_no_matching_candidate — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_fails_closed_when_candidate_entry_missing_status — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_allows_bash_permit_write_multiline_command — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_allows_benign_redirect_to_dev_null — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_allows_benign_stderr_redirect_to_dev_null — a router path appears only in a comment.
- Not listed: hook::test_pretooluse_guard_denies_bash_cp_write_to_skill_proposal_file — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_bash_mv_write_to_skill_proposal_file — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_bash_mv_write_to_inflight_skill_promotion_path — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_denies_bash_cp_target_directory_form_write_to_skill_proposals — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_skill_propose_non_utf8_skill_md_file_returns_json_error — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_skill_promote_non_utf8_skill_md_returns_json_error — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_bash_guard_permit_write_end_to_end_skill_md_literal_project_root_prefixed_spelling — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_bash_guard_allows_benign_redirect_to_dev_null — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_bash_guard_allows_benign_stderr_redirect_to_dev_null — a router path appears only in a comment.
- Not listed: hook::test_hooklib_matches_permit_shape_true_regardless_of_target_spelling — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_ledger_prune_promoted_healthy_no_needs_review — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_promoted_missing_referenced_path_flags_stale_path — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_promoted_elapsed_review_after_flags_review_after_elapsed — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_promoted_missing_name_flags_missing_promoted_skill_name — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_promoted_unparseable_review_after_flags_unparseable — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_promoted_referenced_path_escapes_project_root_flags_stale_path — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_promoted_name_escapes_project_root_flags_stale_path — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_multi_entry_malformed_promoted_entry_does_not_affect_healthy_entry — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_ledger_prune_promoted_missing_skill_md_flags_stale_path_no_crash — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_short_description — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_missing_name — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_both_skill_md_and_patch_present — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_neither_present — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_unsafe_skill_name — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_writes_canonical_and_syncs_cursor_symlink — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_stale_backup_on_conflicting_cursor_entry — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_dereference_fallback_when_symlink_unavailable — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_idempotent_when_already_correctly_linked — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_marks_ledger_entry_promoted — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_applies_update_patch_to_existing_skill — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_critical1_rejects_mismatched_patch_target_and_name — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_high2_rejects_nonexistent_project_root — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_high3_concurrent_approve_no_traceback — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_medium6_project_root_is_file_returns_json_not_traceback — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_already_rejected_candidate — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_candidate_not_in_ledger — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_fails_closed_when_ledger_flag_points_to_never_created_sibling_path — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_cross_candidate_name_collision — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_reapproving_same_already_promoted_candidate_still_refused — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_refuses_case_fold_collision_across_candidates — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_promote_malformed_non_dict_ledger_entry_returns_json_error — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_propose_stages_valid_candidate_and_updates_ledger_status — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_propose_allows_overwrite_with_flag — skill ledger or promotion script behavior on temp skill directories; no runtime router text is read.
- Not listed: hook::test_skill_distillation_skill_present — asserts that another skill's `SKILL.md` exists, not router prose.
- Not listed: hook::test_cursor_router_shared_protocol_read_and_hard_rule_carveout — reads `cursor-router/SKILL.md`, which no phase moves text out of except through the P11 pointer rows already covered by `fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked`.
- Not listed: hook::test_cursor_router_verdict_table_cross_ref_and_scaffold_no_op_documented — reads `cursor-router/SKILL.md`, which no phase moves text out of except through the P11 pointer rows already covered by `fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked`.
- Not listed: hook::test_cursor_bootstrap_mdc_heading_references_resolve — reads `cursor-router/SKILL.md`, which no phase moves text out of except through the P11 pointer rows already covered by `fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked`.
- Not listed: hook::test_install_cursor_sh_raw_github_urls_include_plugin_subpath — install script URLs; the router appears only as a path in the script.
- Not listed: hook::test_cursor_router_memory_finalize_parity_mentions_apply — reads `cursor-router/SKILL.md`, which no phase moves text out of except through the P11 pointer rows already covered by `fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked`.
- Not listed: hook::test_cursor_router_skill_md_wires_completed_cap — reads `cursor-router/SKILL.md`, which no phase moves text out of except through the P11 pointer rows already covered by `fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked`.
- Not listed: hook::test_cursor_router_wires_skill_distill_gate — reads `cursor-router/SKILL.md`, which no phase moves text out of except through the P11 pointer rows already covered by `fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked`.
- Not listed: hook::test_systematic_debugging_documents_sandbox_vs_real_failure_protocol — reads the `systematic-debugging` skill, not the router.
- Not listed: hook::test_planning_patterns_adr_section_defers_to_project_convention — reads the `planning-patterns` skill, not the router.
- Not listed: hook::test_pretooluse_guard_skill_ledger_diverges_cwd_and_claude_project_dir — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_skill_promotion_diverges_cwd_and_claude_project_dir — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_skill_promotion_ledger_corrupt_case_fold_bypass — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_skill_promotion_cwd_is_ancestor_of_project — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_skill_promotion_case_sensitive_ancestor_fallback — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_pretooluse_guard_skill_promotion_ancestor_root_has_own_ledger — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_ai_first_setup_item10_collects_workspace_members — reads the `ai-first-setup` skill, not the router.
- Not listed: hook::test_ai_first_setup_membership_snippet_survives_quote_in_workspace_root — reads the `ai-first-setup` skill, not the router.
- Not listed: hook::test_ai_first_setup_step5_workspace_allowlist_snippet_survives_quote_in_workspace_root — reads the `ai-first-setup` skill, not the router.
- Not listed: hook::test_pretooluse_guard_bash_unrelated_unresolvable_target_does_not_hide_skill_promotion_violation — skill-write guard behavior on synthetic paths and ledgers; `SKILL.md` appears only in test inputs, not as router prose.
- Not listed: hook::test_split_skill_cores_stay_within_budget_and_references_resolve — byte budgets of the verification, code-generation and TDD skill cores, not the router.
- Not listed: fx::test_craftflow_conductor_inspection.py::test_text — reads `cursor-router/SKILL.md` or the router-exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_conductor_inspection.py::test_oneliner — reads `cursor-router/SKILL.md` or the router-exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_conductor_inspection.py::test_nested_cwd — reads `cursor-router/SKILL.md` or the router-exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_conductor_inspection.py::test_quoted_expression_safe_in_hostile_path — reads `cursor-router/SKILL.md` or the router-exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_conductor_inspection.py::test_router — reads `cursor-router/SKILL.md` or the router-exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_cursor_exempt_wiring.py::test_step1_resolution — reads `cursor-router/SKILL.md` and the exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_cursor_exempt_wiring.py::test_symlink_realpath — reads `cursor-router/SKILL.md` and the exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_cursor_exempt_wiring.py::test_symlink_run_readonly — reads `cursor-router/SKILL.md` and the exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_cursor_exempt_wiring.py::test_exempt_block — reads `cursor-router/SKILL.md` and the exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_cursor_exempt_wiring.py::test_carve_out — reads `cursor-router/SKILL.md` and the exempt inspection skills, which no phase moves.
- Not listed: fx::test_craftflow_jev_ab_report.py::test_manifest_by_call_id_is_reused_from_shared_lib_not_duplicated — module import identity; the router is not read.
- Not listed: fx::test_craftflow_jev_prompt_hint.py::test_roster_excludes_internal_and_host_skills_and_appends_none — builds a skill roster; the router appears only as an excluded skill name.
- Not listed: fx::test_craftflow_jev_prompt_hint.py::test_roster_rejects_hostile_skill_id_with_newline_or_tag_content — builds a skill roster; the router appears only as an excluded skill name.
- Not listed: fx::test_craftflow_jev_prompt_hint.py::test_roster_sources_read_project_skills_plugin_skills_and_patterns_hints — builds a skill roster; the router appears only as an excluded skill name.
- Not listed: fx::test_craftflow_jev_prompt_hint.py::test_real_plugin_roster_size_matches_exclusion_rule — builds a skill roster; the router appears only as an excluded skill name.
- Not listed: fx::test_craftflow_jev_session_check.py::test_jev_setup_skill_documents_auto_detect_coexistence — reads the `jev-setup` skill, not the router.
- Not listed: fx::test_craftflow_jev_setup.py::test_jev_setup_skill_frontmatter_and_steps — reads the `jev-setup` skill, not the router.
- Not listed: fx::test_craftflow_retro_v2.py::test_skill_recurrence_text — reads the retro skill, not the router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_skill_exists — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_frontmatter — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_boundary_statement — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_signal_ids_in_rubric — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_rubric_no_mappable_line — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_skill_resolution_order — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_steps_present — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_cursor_router_exemption_block — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_cursor_router_hard_rules_carve_out — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_readme_counts_and_install — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_symlink_realpath_resolution — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_craftflow_retro_wiring.py::test_pruning_row_no_speculative_causation — reads the retro skill or `cursor-router/SKILL.md`, not the runtime router.
- Not listed: fx::test_router_xrefs.py::test_runtime_files_sorted_and_excludes_maintainer — corpus loader behavior (file ordering and maintainer exclusion), no router prose asserted.
- Not listed: fx::test_router_xrefs.py::test_read_corpus_from_ref_matches_tree — corpus loader behavior (git ref versus tree), no router prose asserted.
- Not listed: fx::test_router_xrefs.py::test_h_h1_empty_corpus_and_cli_errors — corpus loader behavior (empty corpus, CLI errors), no router prose asserted.


Failure messages of the corpus-wide tests still say "SKILL.md missing ..." because rewording `fail(name, ...)` lines would break the P1 assertion-count proof; the Kind column, not the message, says what a test reads.

Reproduce the enumeration with `python3 tests/fixtures/router_test_enumerate.py [--names]`; `tests/fixtures/test_router_test_anchors.py` fails when a router-reading test is neither a row below nor a qualified, reasoned `Not listed` line, when a row or exclusion names a test that no longer exists (stale), or when the row count line is stale.

Limits of the guard (a convention check for the in-repo style, not a proof). The enumerator reads each test's own source and the same-file helpers and constants it uses (`=`, annotated and augmented assignments, class `test_` methods). It does not follow a helper imported from another module, a router file reached by a glob or a computed path with no marker in the test, or a nested or dynamically built test. `fx::test_agent_structure.py` runs its checks at module level without `def test_`, so its rows below are hand-classified, are verified only by their `[section]` label still existing in the file, and a new check added there is not detected; `tests/live/*` is outside the enumeration. The enumeration skips only the exact filenames in `EXCLUDED_FILES` (the guard itself and the `test_rule_inventory*.py` fixtures), so a new fixture is enumerated unless it is added to that list. A SyntaxError in any `tests/fixtures/test_*.py` aborts the guard with a traceback (exit 1, no `Results:` line). `_router_section_or_none` failures report an ambiguous heading as not found or not bounded; the message wording is left as is because rewording `fail(name, ...)` lines would break the P1 assertion-count proof.

Rows: 98 (C=35, K=39, F=24).

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
| fx::test_router_xrefs.py::test_section_any_ambiguous_without_hint_raises | K | SKILL.md#BUILD preparation and references/build-workflow.md#BUILD preparation (the heading must stay ambiguous without a file hint) | P10 (Fast Path Detection and Worktree steps move into build-workflow.md; the kernel keeps the pointer heading) |
| fx::test_router_xrefs.py::test_known_sites_are_parsed | K | the `KNOWN` list: cross-reference sites in SKILL.md, harness-self-checks.md, cursor-router/SKILL.md and their target files | TBD-by-mover (P9, P10 and P11 each update the minimum counts for the sites they move) |
| fx::test_router_xrefs.py::test_cursor_router_shared_refs_checked | K | cursor-router/SKILL.md references into _shared/router-protocol.md (at least 5, all resolving) | P11 (Cursor pointers retarget to the new `_shared/` files) |
