# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The files beside the native suites that are not suites: entry points the runner never launches, and helpers.

``native_suite_manifest`` re-exports both; ``test_native_suite_runner.py``
checks that every file of this folder is a suite, excluded or a helper. Update
this file whenever such a file is added or renamed.
"""

# Native entry points the runner never launches, with the reason.
EXCLUDED = {
    "bench_external_mcp_latency.py": "Per-call latency of Angelo's MCP server against stock Blender + MCP; run alone.",
    "check_external_mcp_clients.py": "LIVE, billed: Claude Code and Codex calling Angelo over MCP; run by hand.",
    'team_cache_key_probe.py': 'Unbilled wire-level probe of the team cache key (fake console and gateway); run by hand.',
    "prompt_route_probe.py": "Unbilled: records what routes no trial covers send (fake endpoints) for prompt_audit.py.",
    'qa_reference_workload.py': 'Machine-factor reference workload; started only by qa_machine.py (run_native_suites).',
    'scenario_plan_replay_session.py': 'In-app scripted model started only by scenario_plan_replay.py; refuses otherwise.',
    'scenario_plan_replay.py': 'Unbilled replay of scripted plans through the real host; started by hand with a binary.',
    "long_session_replay.py": "Unbilled long-session replay with a scripted runtime (GUI); started by hand with a binary.",
    'benchmark_blender_batch.py': 'Performance benchmark of batch staging, commit, Undo and Redo by copies; run alone.',
    "benchmark_blender_stage_storage.py": "Native stage storage benchmark; compare sequential isolated processes.",
    "benchmark_blender_native_graph_build.py": "Native graph build benchmark; run alone after builds/tests stop.",
    "benchmark_blender_native_depsgraph.py": "Native graph performance benchmark; run alone after builds/tests stop.",
    "benchmark_blender_agent_execution.py": "Performance benchmark; run alone after builds/tests stop.",
    "benchmark_blender_fingerprint.py": "Performance benchmark; run alone after native measurements stop.",
    "measure_sky_table.py": "Re-measures world_light_table.SKY_TABLE (Cycles renders) for a new Blender; run by hand.",
    "benchmark_blender_operator_run_work.py": "Growth of operator_run's phase-5 operators at n and 4n; run alone.",
    "benchmark_blender_read_tools.py": "Time and answer size of the live read tools on a given .blend; run alone.",
    "read_coverage_sweep.py": "Records what the live reader answers on a planted document, once per binary; run alone.",
    "read_coverage_compare.py": "Host CLI comparing two read_coverage_sweep.py records; no Blender.",
    "make_human_base_meshes.py": "Makes the bundled Human Base Meshes and their table from Blender's archive; stock Blender, by hand.",
    "face_report.py": "Host CLI: the face and hair one-shots' per-feature errors from a run's saved checks; no Blender.",
    "benchmark_blender_request_host_time.py": "Per-request host time on a production-size scene; run alone.",
    "benchmark_blender_sculpt.py": "Performance benchmark of sculpt batching.",
    "benchmark_blender_sculpt_audit.py": "Serial performance benchmark; run alone.",
    "benchmark_blender_snapshot_cost.py": "Performance benchmark of request snapshots; run alone.",
    "benchmark_blender_stage_compression.py": "Performance benchmark of stage-bundle compression; run alone.",
    "benchmark_blender_turn_context.py": "Performance benchmark of turn context and change feed; run alone.",
    "benchmark_blender_uv_unwrap_work.py": "Growth of the typed uv_unwrap action with mesh size; run alone.",
    "bench_native_reads.py": "Native reads against stock Blender's fastest routes on the heavy scene; run alone.",
    "benchmark_blender_native_reads.py": "Instruction ratchet of the native reads; run by test_blender_perf_ratchet.",
    "benchmark_blender_chat_waiting.py": "Instruction ratchet of the chat's marble wait; run by test_blender_perf_ratchet.",
    "heavy_variants.py": "Stock-Blender job grading right and wrong heavy-scene results; run by hand.",
    "scenario_start_job.py": "Background job making a scenario's start file; started by scenario_references.py.",
    "benchmark_blender_view_scene_check.py": "Cost of the scene check after every look and strip; run alone.",
    "benchmark_blender_long_session.py": "Per-edit cost over a 500-edit session and by material slots; run alone.",
    "benchmark_blender_session_journal.py": "Conversation saves over a 500-edit session and the snapshot worker's "
                                            "probe; run alone.",
    "benchmark_fixtures.py": "Shared fixtures of the execution, snapshot, compression, view-check and long-session benchmarks.",
    "bench_mcp_vs_angelo.py": "BILLED Blender MCP vs Angelo benchmark launcher; needs --authorize-billed-runs.",
    "bench_mcp_blender_startup.py": "Stock-Blender startup started only by bench_mcp_trial.py; refuses otherwise.",
    "eval_live_agent.py": "BILLED live-agent latency launcher; needs --authorize-billed-runs and a connection.",
    "live_flow_smoke.py": "BILLED one-shot live Generation Flow run (3 images + 1 model); needs ANGELO_QA_LIVE_GENAI=1 and --authorize-billed-generation.",
    "live_genai_smoke.py": "BILLED one-shot live Gemini image + Tripo model check; needs ANGELO_QA_LIVE_GENAI=1 and --authorize-billed-generation.",
    "eval_live_agent_session.py": "In-app driver started only by eval_live_agent.py; refuses to run otherwise.",
    "scenario_references.py": "Unbilled host CLI: renders and self-checks a scenario's references in stock Blender.",
    "scenario_job.py": "Stock-Blender background job (references, captures) started by scenario_references.py.",
    "blender_profile_fixture.py": "Builds a stock Blender user folder in a sandbox; started only by "
                                  "test_blender_settings_import.py.",
    "scenario_demo_job.py": "Stock-Blender background job encoding a demo recording; started by scenario_demo.encode.",
    "scenario_dry_run.py": "Unbilled scenario dry run started by bench_mcp_vs_angelo.py --dry-run-scenario.",
    "scenario_dry_run_session.py": "In-app scripted runtime started only by scenario_dry_run.py; refuses otherwise.",
    "stress_blender_multires_undo.py": "Concurrent correctness stress with its own host runner.",
    "stress_blender_world_redo.py": "Concurrent correctness stress (World commit, Undo, Redo) with its own host runner.",
    "gui_smoke.py": "Legacy standalone GUI fixture; its documented run makes a live subscription turn.",
    "native_coverage_metrics.py": "Read-only coverage measurement for native_coverage.rst; not a pass/fail suite.",
    "test_codex_external_local.py": "Host-Python test that drives the installed Codex CLI; explicit only.",
    "codex_prompt_probe.py": "Unbilled probe of the installed Codex CLI's request bytes (fake local endpoint); explicit only.",
    "codex_cache_fork_check.py": "Prompt-cache A/B of prediction-mode forks against fresh Codex threads; unbilled "
                                 "dry run by default, BILLED only with --authorize-billed-run; explicit only.",
    "codex_fork_probe.py": "Unbilled probe of prediction-mode forks across Codex app-server processes (fake local "
                           "endpoint); explicit only.",
    "codex_opening_check.py": "Unbilled timing of a launch's first Codex chat with and without its opening readied "
                              "at connect (fake local endpoint); explicit only.",
    "codex_runtime_check.py": "New Codex chats through Controller.send on a shared app-server against the old path; "
                              "unbilled by default, BILLED only with --authorize-billed-run; explicit only.",
    "codex_stale_call_probe.py": "Unbilled probe of whether codex app-server waits for an unanswered tool call "
                                 "(fake local endpoint); explicit only.",
    "claude_direct_probe.py": "Unbilled probe of the opt-in direct-tools route with the installed Claude Code CLI: both "
                              "routes against a fake local endpoint, their openings and what the model received",
    "claude_prompt_probe.py": "Unbilled probe of the installed Claude Code CLI's request bytes and cache TTLs per route (fake local endpoint, "
                              "dummy key, never a sign-in); explicit only.",
    "runtime_launch_check.py": "Unbilled launch check and first-message timing of Claude Code and Codex releases, "
                               "launched at Send and prewarmed (local mock, dummy key); explicit only.",
    "prefix_composition.py": "Host-Python byte report of a new chat's fixed opening by section, tool and kind; "
                             "a measurement, not a pass/fail suite.",
    "team_gateway_probe.py": "Unbilled probe of the gateway paths Codex and Claude Code call for team connections (local recorder); explicit only.",
    "live_team_sign_in.py": "LIVE (unbilled) team sign-in against a real console; a person clicks Allow in the browser; explicit only.",
    "live_team_library_check.py": "LIVE (unbilled, read-only) team library and IP reads against a real console; "
                                  "a person clicks Allow in the browser; explicit only.",
    "windows_agent_checks.py": "Windows only: the file backend on real NTFS and CLI discovery/spawning, run by the "
                               "Windows validation machine (doc/angelo/app_identity.rst); explicit only.",
    "live_team_library_write_check.py": "LIVE (unbilled) team library save, search, import and delete inside Angelo "
                                        "with an existing team sign-in; explicit only.",
    "bench_mcp_replay.py": "LIVE (billed) replay of a recorded Angelo session's messages on stock Blender + MCP; "
                           "explicit only, needs --authorize-billed-runs.",
    "oneshot_job.py": "Stock-Blender background job of the one-shot tests (references, captures, the final still); "
                      "started by oneshot_references.py and oneshot_report.py.",
    "final_runs.py": "BILLED final-runs campaign driver (doc/angelo/final_runs.rst); its run action needs "
                     "--authorize-billed-runs, every other action is unbilled; explicit only.",
    "cache_floor.py": "Unbilled CLI: the cache-floor check and the floors' regenerate tool (doc/angelo/final_runs.rst); "
                      "final_runs imports it; explicit only.",
    "final_runs_mcp.py": "Unbilled: bundles the pilot's MCP runtime on the Mac and installs it on the Windows VM "
                         "(doc/angelo/final_runs.rst); explicit only.",
    "final_runs_sources.py": "Unbilled: checks the one-shot tests' source pictures against their SHA-256 pins on "
                             "the Mac and the Windows VM (doc/angelo/final_runs.rst); explicit only.",
}

# Imported fixture/helper modules and host runners: not entry points.
HELPERS = frozenset({
    'program_completion_fixtures.py',
    "agent_runtime_fixtures.py", "animation_fakes.py", "external_app_env.py", "bench_mcp_codex.py", "bench_mcp_grading.py", "bench_mcp_report.py", "bench_mcp_trial.py",
    "bench_mcp_scenario.py", "bench_cold_warm.py", "bench_integrity.py",
    "coverage_prefix.py", "crash_fixtures.py", "settings_import_phases.py", "prefs_quit_phases.py",
    "first_launch_frames_phases.py", "first_launch_frames_probe.py", "splash_frames.py",
    "claude_code_errors.py", "claude_protocol_fixtures.py", "eval_agent_tasks.py", "eval_live_tasks.py",
    "eval_animation_tasks.py", "eval_node_tasks.py", "eval_operator_tasks.py", "eval_request_log.py",
    "eval_returning_user.py", "eval_approval.py", "eval_diagnostics.py", "eval_ask_why.py", "mcp_request_parameters.py",
    "eval_warm_up.py", "bench_warm_up.py", "bench_addon_pin.py",
    "eval_provider_limits.py", "eval_receipts.py", "eval_survey_tasks.py", "survey_report.py",
    # The grading contract (doc/angelo/native_coverage.rst, "Grading contract"): answers, result checks, fixtures.
    "eval_answers.py", "eval_grading.py", "eval_grading_live.py", "survey_checks.py", "survey_checks_motion.py",
    "survey_fixtures.py", "survey_reference_builds.py", "survey_truth.py",
    "fake_c_libraries.py", "fake_nt_files.py", "fake_provider_cli.py", "fake_team_console.py", "file_times.py",
    "fake_team_library.py", "fake_usage_reports.py", "gui_launch.py", "identity_fixtures.py", "long_jobs_fixtures.py",
    "long_session_cost_model.py", "long_session_timers.py", "loopback_server.py", "macos_window.py", "measured_results.py", "notices_fakes.py",
    "native_addon_guard.py", "native_suite_checks.py", "native_suite_manifest.py", "native_suite_other_files.py",
    "native_suite_paths.py",
    "native_suite_summary.py",
    "operator_run_qa.py",
    "perf_counters.py", "perf_ratchet.py", "picture_fakes.py", "plan_budgets.py", "plan_output_accounting.py",
    "production_jobs_fixtures.py",
    "production_jobs_harness.py", "program_production_helpers.py", "program_qa_helpers.py", "program_stress_plans.py",
    "program_teardown_qa.py",
    "qa_machine.py", "qa_waits.py",
    "format_curve_fixtures.py", "rendered_view_sample_checks.py",
    "read_tools_fakes.py", "read_tools_fixtures.py", "read_tools_stand_ins.py", "rendered_view_case.py",
    "rendered_view_fakes.py", "rendered_view_fixtures.py", "replay_fast_tier_cases.py", "replay_refused_plans.py",
    "replay_turn_context.py",
    "plan_progress_fixtures.py", "chat_waiting_fixtures.py", "chat_waiting_measure.py",
    "reply_stream_fixtures.py",
    "view_source_fakes.py",
    "run_host_tests.py",
    "script_pixel_fixtures.py", "script_review_fixtures.py", "sculpt_undo_admission_cases.py",
    "run_native_suites.py", "snapshot_fixtures.py", "strict_plan_vectors.py", "team_library_fixtures.py",
    "token_accounting.py", "turn_recovery_fixtures.py", "translation_cards.py", "update_crash_gui.py", "update_crash_scenes.py", "update_fixtures.py",
    "update_e2e_common.py", "update_e2e_driver.py", "update_e2e_macos.py", "update_e2e_windows.py",
    "ui_kit_gallery_card.py", "ui_kit_gallery_fields.py", "ui_kit_gallery_rows.py",
    "window_chrome.py", "windows_cli_checks.py", "windows_ntfs_checks.py",
    "ps_stderr_lint.py",   # The Windows VM scripts' native-stderr check (test_native_windows_bench_stage.py).
    # Scenario benchmarks (doc/angelo/scenario_benchmarks.rst): imported by the bench, its drivers and jobs.
    "scenario_bridge.py", "scenario_build_kit.py", "scenario_capture.py", "scenario_checks.py", "scenario_demo.py",
    "scenario_fake_models.py", "scenario_hygiene.py", "scenario_island.py", "scenario_report.py",
    "scenario_scorecard.py",
    "scenario_scoring.py", "scenario_session.py", "scenario_specs.py",
    "world_background.py",   # What a World shows a camera, for the world checks (scenario_checks.world_facts).
    "shader_graph.py", "surface_reading.py",   # Shader graphs evaluated as the renderer does, for the colour checks.
    "texture_kernels.py",   # Cycles' Noise, Voronoi, Brick and Checker kernels, ported for those graphs.
    "shader_fakes.py",   # Stand-in node trees and meshes for the host tests of those readings.
    # Gate answer forms and one-shot scenes: imported by the test drivers and reports.
    "answer_forms_report.py", "bench_mcp_answers.py", "bench_mcp_gate.py", "bench_mcp_method.py",
    "eval_answer_forms.py", "eval_gate.py",
    # Benchmark totals that keep unrecorded values unknown, and list prices per model request.
    "bench_totals.py", "list_prices.py", "bench_workdirs.py",
    # The final-runs campaign (doc/angelo/final_runs.rst): its plan and quiet-machine guard.
    "final_runs_freeze.py", "final_runs_load.py", "final_runs_plan.py", "final_runs_report.py",
    "oneshot_checks.py", "oneshot_cottage_variants.py", "oneshot_fake_models.py",
    "oneshot_form_checks.py", "oneshot_group.py", "oneshot_images.py", "oneshot_kit.py", "oneshot_mcp.py",
    "oneshot_metrics.py", "oneshot_mini_checks.py", "oneshot_minis.py", "oneshot_opacity.py",
    "oneshot_produced.py", "oneshot_references.py",
    "final_render.py",   # Which file is a trial's final render: the one the agent names (every side).
    "oneshot_report.py", "oneshot_session.py", "oneshot_tables.py", "oneshot_variants.py",
    "scenario_oneshot_bridge.py", "scenario_oneshot_cottage.py", "scenario_oneshot_footbridge.py",
    "scenario_oneshot_footbridge_conflict.py", "scenario_oneshot_bridge_conflict.py",
    "scenario_oneshot_mini_camera.py", "scenario_oneshot_mini_cube.py", "scenario_oneshot_mini_footbridge.py",
    "scenario_oneshot_mini_footbridge_arched.py", "scenario_oneshot_mini_lighting.py", "scenario_oneshot_tree.py",
    # The heavy cases: the castle one-shot and the dense castle scene's five edits, and the native-reads benchmark.
    "castle_checks.py", "castle_kit.py", "castle_variants.py", "scenario_oneshot_castle.py",
    "heavy_checks.py", "heavy_plan.py", "heavy_report.py", "heavy_scene.py", "heavy_scene_blend.py",
    "scenario_heavy.py",
    "view_outline_kit.py",   # The reference comparisons' GUI suites: decoding, read-only wrapping, attaching.
    "bench_native_reads_paths.py", "bench_native_reads_web.py",
    # The face and hair one-shots: heads as distance fields, hairstyles, their readings, checks and variants.
    "face_casters.py", "face_compare.py", "face_measure.py", "face_tests.py", "face_truth.py", "face_variants.py",
    "hair_kit.py", "head_checks.py", "head_designs.py", "head_kit.py", "head_sdf.py",
    "scenario_oneshot_hair_long.py", "scenario_oneshot_hair_short.py", "scenario_oneshot_head_anime.py",
    "scenario_oneshot_head_portrait.py", "scenario_oneshot_head_stylised.py",
})
