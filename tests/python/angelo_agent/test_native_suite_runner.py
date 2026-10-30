# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only regressions for the native suite manifest and runner; Blender is never launched.

Process tests use a tiny fake "binary" (a host Python script) to exercise real
launch, isolation, timeout and signal handling.
"""

import contextlib
import dataclasses
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import native_suite_checks as checks
import native_suite_manifest as manifest
import native_suite_paths
import run_native_suites as runner
from angelo_agent import product_env  # The runner put the add-on directory on sys.path.

SUITES = manifest.by_name()
SECRETS = {"OPENAI_API_KEY": "sk-fixture", "ANTHROPIC_API_KEY": "fixture", "CODEX_HOME": "/real/.codex",
           "ANGELO_ENGINE_SURFACE": "BASE", "ANGELO_QA_KEEP_OPEN": "1", "PYTHONPATH": "/real/site",
           "BLENDER_USER_CONFIG": "/real/blender/config", "ANGELO_QA_LIVE_CODEX": "1"}
REAL_HOME = "/Users/real-user-fixture"
# A Windows caller's system and profile variables (fixture values; os.environ spells them in upper case).
WINDOWS_ENV = {"SYSTEMROOT": "C:\\Windows", "WINDIR": "C:\\Windows", "COMSPEC": "C:\\Windows\\system32\\cmd.exe",
               "PATHEXT": ".COM;.EXE;.BAT;.CMD", "PROGRAMFILES(X86)": "C:\\Program Files (x86)",
               "PROGRAMDATA": "C:\\ProgramData", "PROCESSOR_ARCHITECTURE": "AMD64", "NUMBER_OF_PROCESSORS": "8",
               "USERNAME": "real-user-fixture", "USERPROFILE": "C:\\Users\\real-user-fixture",
               "APPDATA": "C:\\Users\\real-user-fixture\\AppData\\Roaming",
               "LOCALAPPDATA": "C:\\Users\\real-user-fixture\\AppData\\Local",
               "HOMEDRIVE": "C:", "HOMEPATH": "\\Users\\real-user-fixture"}
FAKE_BINARY = """#!{python}
import json, os, signal, sys, time
from pathlib import Path
behavior = Path(sys.argv[sys.argv.index("--python") + 1]).stem
qa = Path(os.environ["ANGELO_QA_DIR"])
(qa / "env.json").write_text(json.dumps(dict(os.environ)))
if behavior == "fake_sleep":
    time.sleep(60)
if behavior == "fake_signal":   # Windows has no signals: an unhandled exception's NTSTATUS exit instead.
    os._exit(-1073741819) if os.name == "nt" else os.kill(os.getpid(), signal.SIGTERM)
(qa / "report.json").write_text(json.dumps({{"success": behavior != "fake_quit_failed", "checks": {{"a": True}}}}))
if behavior == "fake_assert":
    print("BLI_assert failed: source/x.cc:1, f(), at 'false'", file=sys.stderr, flush=True)
print("FAKE_OK", flush=True)
"""


def fake_suite(name, **values):
    values.setdefault("report", manifest.Report("report.json"))
    return manifest.Suite(name + ".py", values.pop("kind", manifest.BACKGROUND), "fixture", **values)


class ManifestTests(unittest.TestCase):
    def test_every_native_suite_is_classified_exactly_once(self):
        scripts = [suite.script for suite in manifest.SUITES]
        self.assertEqual(len(scripts), len(set(scripts)))
        self.assertEqual(sorted(scripts), sorted(path.name for path in HERE.glob("test_blender_*.py")))

    def test_no_stale_entries(self):
        for name in [s.script for s in manifest.SUITES] + list(manifest.EXCLUDED) + list(manifest.HELPERS):
            with self.subTest(name=name):
                self.assertTrue((HERE / name).is_file())

    def test_every_other_python_entry_point_is_classified(self):
        suites, excluded, helpers = {s.script for s in manifest.SUITES}, set(manifest.EXCLUDED), manifest.HELPERS
        self.assertFalse(suites & excluded or suites & helpers or excluded & helpers)
        for path in HERE.glob("*.py"):
            if not path.name.startswith("test_native_"):
                with self.subTest(file=path.name):
                    self.assertIn(path.name, suites | excluded | helpers)
        for path in list(HERE.glob("benchmark_*.py")) + list(HERE.glob("stress_*.py")):
            self.assertIn(path.name, excluded)

    def test_flags_are_consistent_with_class(self):
        for suite in manifest.SUITES:
            with self.subTest(suite=suite.name):
                self.assertIn(suite.kind, manifest.CLASSES)
                self.assertTrue(suite.report is not None or suite.marker, "needs a receipt or marker")
                self.assertGreater(suite.timeout, 0)
                self.assertEqual(suite.window is None, suite.kind != manifest.LIVE)
                self.assertFalse(suite.event_simulate and suite.real_input, "simulated events need the isolation")
                if suite.kind == manifest.BACKGROUND:
                    self.assertFalse(suite.event_simulate or suite.default_chat or suite.first_launch
                                     or suite.real_input)
                    if suite.host_launcher:  # A background host driver starts only background app processes.
                        self.assertIn('"--background"', (HERE / suite.script).read_text(encoding="utf-8"))
                else:
                    self.assertFalse(suite.gui_superset)
                if suite.kind != manifest.LIVE:
                    self.assertFalse(suite.live_env)
                for argument in suite.host_args:
                    argument.format(binary="b", qa="q")
        self.assertEqual({s.name for s in manifest.SUITES if s.kind == manifest.LIVE},
                         {"test_blender_api_tls", "test_blender_chat_usage_live", "test_blender_cross_provider_live",
                          "test_blender_keychain_live",
                          "test_blender_shared_sessions"})

    def test_manifest_matches_each_suites_own_guards_and_receipts(self):
        for suite in manifest.SUITES:
            source = (HERE / suite.script).read_text(encoding="utf-8")
            guards = [line for line in source.splitlines() if line.lstrip().startswith("assert ")]
            with self.subTest(suite=suite.name):
                if any("not bpy.app.background" in line for line in guards):
                    self.assertTrue(suite.needs_window or suite.host_launcher)
                elif any("bpy.app.background" in line or '"--background" in' in line for line in guards):
                    self.assertFalse(suite.needs_window)
                if "event_simulate(" in source or any("--enable-event-simulate" in line for line in guards):
                    self.assertTrue(suite.event_simulate or suite.host_launcher)
                pops_default_chat = re.search(r"product_env\.clear\(['\"]DISABLE_DEFAULT_CHAT", source)
                self.assertEqual(bool(pops_default_chat), suite.default_chat)
                if suite.first_launch:   # It starts with Angelo's startup on: the first frames are the real ones.
                    self.assertTrue(suite.default_chat)
                    self.assertNotRegex(source, r"product_env\.clear\(['\"]DISABLE_INTRO")
                    self.assertNotIn("bpy.ops.wm.splash(", source, "Blender's startup splash is never reopened")
                requires_nested_state = re.search(r"is_relative_to\(OUT\b", source)
                self.assertEqual(bool(requires_nested_state), suite.state_in_qa)
                if suite.report is not None:
                    self.assertIn(suite.report.env or Path(suite.report.path).name, source)
                if suite.marker:
                    self.assertIn(suite.marker, source)
                for value in suite.live_env + suite.options_env + tuple(a for a in suite.extra_args if not a.startswith("--")):
                    self.assertIn(value, source)
                if 'system_resource("SCRIPTS")' in source:
                    self.assertEqual(suite.notes, manifest.INSTALLED_HOST)

    def test_every_receipt_writes_the_field_the_runner_reads(self):
        """release-105 fix round: two headless fixtures wrote "passed" and every check passed, yet the runner read
        "success" and failed them as success=None. Each suite whose receipt the runner reads by a field writes that
        field: a script that writes its own receipt (a json.dump or write_text(json.dumps( in it) in its own text or
        a script it runs (runpy);
        one that leaves the receipt to helpers it imports from this folder, in those helpers."""
        def sources(script):
            text = (HERE / script).read_text(encoding="utf-8")
            run = {name for name in re.findall(r"[\"'](\w+)\.py[\"']", text) if (HERE / (name + ".py")).is_file()}
            if not re.search(r"write_text\(\s*json\.dumps\(|\bjson\.dump\(", text):   # Receipt left to helpers.
                names = re.findall(r"^\s*(?:from\s+(\w+)\s+import|import\s+(\w+))", text, re.M)
                run |= {name for pair in names for name in pair if name and (HERE / (name + ".py")).is_file()}
            return [text] + [(HERE / (name + ".py")).read_text(encoding="utf-8") for name in sorted(run)]
        for suite in manifest.SUITES:
            if suite.report is None or suite.report.success_key is None:
                continue
            key = suite.report.success_key
            with self.subTest(suite=suite.name):
                self.assertTrue(any(re.search(r"[\"']%s[\"']\s*[:\]]|\b%s\s*=" % (key, key), text)
                                    for text in sources(suite.script)),
                                "%s's receipt never writes %r, the field the runner reads" % (suite.script, key))

    def test_a_receipt_without_the_field_says_so(self):
        spec = manifest.Report("report.json")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.json"
            path.write_text(json.dumps({"checks": {"a": True}, "passed": True}))
            reasons = checks.validate_report(spec, path, started_at=0)
        self.assertEqual(reasons, ["report has no 'success' field (it has checks, passed): the suite must write it"])

    def test_host_driver_output_matches_its_report_path(self):
        drivers = [suite for suite in manifest.SUITES if suite.host_launcher]
        self.assertEqual({s.name for s in drivers}, {"test_blender_crash_handler", "test_blender_memory_project_resume", "test_blender_external_cli", "test_blender_external_mcp",
                                                 "test_blender_headless_mcp", "test_blender_headless_script_files", "test_blender_headless_script_document", "test_blender_headless_preferences",
                                                 "test_blender_headless_native_commits",
                                                     "test_blender_keychain_live", "test_blender_legacy_migration",
                                                     "test_blender_app_identity", "test_blender_perf_ratchet",
                                                     "test_blender_settings_import", "test_blender_prefs_and_quit",
                                                     "test_blender_first_launch_frames", "test_blender_godot_link_e2e"})
        for suite in drivers:
            with self.subTest(suite=suite.name):
                out = suite.host_args[suite.host_args.index("--out") + 1]
                self.assertEqual(Path(out.format(qa="q")).name, Path(suite.report.path).parent.name)

    def test_platform_applicability_is_explained_and_matches_each_suites_code(self):
        for suite in manifest.SUITES:
            with self.subTest(suite=suite.name):
                self.assertEqual(bool(suite.platforms), bool(suite.platform_reason))
                self.assertLessEqual(set(suite.platforms), {"darwin", "linux", "win32"})
                source = (HERE / suite.script).read_text(encoding="utf-8")
                if "import fake_provider_cli" in source:  # POSIX shell-wrapper stand-ins.
                    self.assertFalse(suite.runs_on("win32"))
                # macOS bundle or counters, unless the suite has its own Windows branch.
                if ("Info.plist" in source or "proc_pid_rusage" in source) and "win32" not in source:
                    self.assertEqual(suite.platforms, ("darwin",))
        self.assertEqual({s.name for s in manifest.SUITES if s.platforms == ("darwin",)},
                         {"test_blender_angelo_startup", "test_blender_external_cli", "test_blender_external_mcp",
                      "test_blender_headless_mcp", "test_blender_headless_script_files", "test_blender_headless_script_document", "test_blender_headless_preferences",
                      "test_blender_headless_native_commits", "test_blender_keychain_live",
                          "test_blender_perf_ratchet", "test_blender_undo_unlinked_pointers",
                          "test_blender_godot_link_e2e", "test_blender_godot_link_gui"})
        self.assertEqual({s.name for s in manifest.SUITES if not s.runs_on("win32")} - {
            s.name for s in manifest.SUITES if s.platforms == ("darwin",)},
            {"test_blender_canvas_states", "test_blender_chat_cards", "test_blender_crash_handler", "test_blender_chat_start_states", "test_blender_chat_waiting",
             "test_blender_codex_turn_errors",
             "test_blender_connect_hero", "test_blender_connect_surfaces", "test_blender_director_navigation", "test_blender_everyday_turns", "test_blender_first_run",
             "test_blender_plan_progress", "test_blender_prefs_and_quit", "test_blender_reply_stream",
             "test_blender_script_stream", "test_blender_settings_import",
             "test_blender_team_chat", "test_blender_translation",
             "test_blender_turn_recovery"})

    def test_output_roots_resolve_through_the_short_temp_root(self):
        roots = {s.name: s.output_root for s in manifest.SUITES if s.output_root}
        self.assertEqual(roots, {"test_blender_external_cli": manifest.SHORT_TEMP_ROOT,
                                 "test_blender_external_mcp": manifest.SHORT_TEMP_ROOT,
                                 "test_blender_memory_project_resume": manifest.SHORT_TEMP_ROOT})
        for name in roots:
            self.assertEqual(runner.required_output_root(SUITES[name]), native_suite_paths.short_temp_root())
            source = (HERE / SUITES[name].script).read_text(encoding="utf-8")
            self.assertIn("native_suite_paths.fresh_fixture_root(args.out)", source)
        self.assertIsNone(runner.required_output_root(SUITES["test_blender_native"]))
        self.assertEqual(runner.default_output_root(), native_suite_paths.short_temp_root())

    def test_self_launched_windows_never_take_focus(self):
        # Host drivers start their own GUI processes, so the runner cannot add the flag: they take it from
        # gui_launch.window_flags, which test_native_gui_launch_focus.py requires of every launcher.
        for name in ("test_blender_memory_project_resume.py", "test_blender_external_cli.py", "eval_live_agent.py",
                     "test_blender_legacy_migration.py"):
            with self.subTest(script=name):
                self.assertIn("gui_launch.window_flags(", (HERE / name).read_text(encoding="utf-8"))


class SelectionTests(unittest.TestCase):
    def select(self, **options):
        options.setdefault("platform", "darwin")  # Every suite applies there; CI runs on Linux.
        selected, skipped = runner.select_suites(manifest.SUITES, **options)
        self.assertEqual(sorted(s.name for s in selected + [s for s, _ in skipped]), sorted(SUITES))
        return {s.name for s in selected}, dict((s.name, reason) for s, reason in skipped)

    def test_default_runs_background_only(self):
        selected, skipped = self.select()
        self.assertEqual(selected, {s.name for s in manifest.SUITES if s.kind == manifest.BACKGROUND})
        self.assertIn("--gui", skipped["test_blender_sculpt"])

    def test_opengl_suites_are_skipped_never_passed_where_the_machine_has_none(self):
        needing = {suite.name for suite in manifest.SUITES if suite.needs_opengl}
        self.assertTrue(needing)
        selected, skipped = runner.select_suites(manifest.SUITES, platform="win32", no_opengl=True)
        reasons = {suite.name: reason for suite, reason in skipped}
        self.assertFalse(needing & {suite.name for suite in selected})
        for name in needing:
            self.assertIn("needs a working OpenGL", reasons[name])
        selected, _ = runner.select_suites(manifest.SUITES, platform="win32")
        self.assertTrue(needing <= {suite.name for suite in selected})

    def test_gui_adds_window_suites_but_never_live(self):
        selected, _ = self.select(gui=True, includes=["*"])
        self.assertEqual(selected, {s.name for s in manifest.SUITES if s.kind != manifest.LIVE})

    def test_live_requires_allow_live_and_exact_include(self):
        live = "test_blender_cross_provider_live"
        for options in ({"includes": [live]}, {"allow_live": True}, {"allow_live": True, "includes": ["*"]},
                        {"allow_live": True, "includes": ["*live*"]}, {"allow_live": True, "gui": True}):
            with self.subTest(options=options):
                selected, skipped = self.select(**options)
                self.assertFalse(selected & {s.name for s in manifest.SUITES if s.kind == manifest.LIVE})
        for name in (live, live + ".py"):
            selected, _ = self.select(allow_live=True, includes=[name])
            self.assertEqual(selected, {live})

    def test_other_platforms_skip_suites_their_code_cannot_run(self):
        for platform in ("win32", "linux"):
            with self.subTest(platform=platform):
                selected, skipped = self.select(gui=True, platform=platform)
                for suite in manifest.SUITES:
                    if not suite.runs_on(platform):
                        self.assertNotIn(suite.name, selected)
                        self.assertIn("not applicable on " + platform, skipped[suite.name])
                        self.assertIn(suite.platform_reason, skipped[suite.name])
                self.assertEqual(selected, {s.name for s in manifest.SUITES
                                            if s.kind != manifest.LIVE and s.runs_on(platform)})
        _, skipped = self.select(platform="win32")
        self.assertIn("runs on darwin", skipped["test_blender_perf_ratchet"])
        self.assertIn("not applicable", skipped["test_blender_keychain_live"])  # Before the LIVE gate.

    def test_include_and_exclude_globs(self):
        selected, skipped = self.select(gui=True, includes=["*sculpt*"], excludes=["*fixes"])
        self.assertTrue(selected and all("sculpt" in n and not n.endswith("fixes") for n in selected))
        self.assertEqual(skipped["test_blender_sculpt_safety_fixes"], "excluded")
        self.assertEqual(skipped["test_blender_native"], "not included")


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-runner-test-")
        self.addCleanup(self.temp.cleanup)
        self.run_dir = Path(self.temp.name).resolve()
        self.binary = self.run_dir / "Angelo"
        self.base = dict(SECRETS, HOME=REAL_HOME, PATH="/usr/bin:/bin", TMPDIR="/real/tmp",
                         ANGELO_DISABLE_DEFAULT_CHAT="0", LANG="en_US.UTF-8",
                         SENSEI_CONNECTIONS_DIR="/real/accounts", SENSEI_DISABLE_DEFAULT_CHAT="1",
                         TOONLAB_STATE_DIR="/real/state", TOONLAB_QA_DIR="/real/qa", **WINDOWS_ENV)

    def plan(self, name, **options):
        return runner.plan_suite(SUITES[name], self.run_dir, self.binary, self.base, **options)

    def test_fixture_waits_get_the_runner_timeout_scale(self):
        scale_env = runner.qa_waits.SCALE_ENV
        self.base[scale_env] = "9"  # Only the runner's own --timeout-scale reaches a suite.
        self.assertEqual("1.0", self.plan("test_blender_sculpt_checkpoint_bundle")["env"][scale_env])
        plan = self.plan("test_blender_sculpt_checkpoint_external", timeout_scale=2.5)
        self.assertEqual(2.5, runner.qa_waits.timeout_scale(plan["env"]))
        self.assertEqual(SUITES["test_blender_sculpt_checkpoint_external"].timeout * 2.5, plan["timeout"])

    def test_supervisor_owner_marker_reaches_native_and_worker_without_widening_credentials(self):
        from angelo_agent.worker_process import worker_environment
        suite = "test_blender_concurrent_edit"
        directory = self.run_dir / "owner-environment"
        def environment():
            return runner.build_environment(SUITES[suite], directory, self.binary, self.base, directory / "suite.log")
        baseline = environment()
        for marker in ("a" * 32, "", "a" * 31, "a" * 33, "A" * 32, "g" * 32, "a" * 31 + "\n", 12):
            with self.subTest(marker=marker):
                self.base["ANGELO_QA_PROCESS_OWNER"] = marker
                self.base["ANTHROPIC_API_KEY"] = "not-a-real-key"
                self.base["CODEX_HOME"] = "must-not-pass"
                env = environment()
                child = worker_environment(env, self.run_dir / "worker-private")
                if marker == "a" * 32:
                    self.assertEqual(env["ANGELO_QA_PROCESS_OWNER"], marker)
                    self.assertEqual(child["ANGELO_QA_PROCESS_OWNER"], marker)
                else:
                    self.assertNotIn("ANGELO_QA_PROCESS_OWNER", env)
                    self.assertNotIn("ANGELO_QA_PROCESS_OWNER", child)
                    self.assertEqual(env, baseline)
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                self.assertNotIn("ANTHROPIC_API_KEY", child)
                self.assertNotIn("CODEX_HOME", env)
                self.assertEqual(env["ANGELO_AGENT_RUNTIME"], "off")
        self.base["ANGELO_QA_PROCESS_OWNER"] = "a" * 32
        self.assertEqual(self.plan(suite)["env"]["ANGELO_QA_PROCESS_OWNER"], "a" * 32)

    def test_only_a_suites_own_options_come_from_the_caller(self):
        with_options = [suite for suite in manifest.SUITES if suite.options_env]
        expected = {
            "test_blender_concurrent_edit": ("ANGELO_CONCURRENT_EDIT_MODE",),
            "test_blender_complete_checks": ("ANGELO_COMPLETE_PHASE", "ANGELO_QA_CONCURRENT_NATIVE_JOBS",
                                             "ANGELO_CAPTURE_LARGE_RESULT", "ANGELO_COMPLETE_TRIALS",
                                             "ANGELO_COMPLETE_JOURNAL"),
            "test_blender_translation": ("ANGELO_L10N_LOCALES", "ANGELO_L10N_PSEUDO"),
            "test_blender_sculpt_checkpoint_bundle": ("ANGELO_SCULPT_CHECKPOINT_VARIANT",),
            "test_blender_settings_import": ("ANGELO_QA_STOCK_BLENDER",),
            "test_blender_script_stream": ("ANGELO_SCRIPT_STREAM_TIMING",),
            "test_blender_job_desktop": ("ANGELO_WINDOWS_JOB_DESKTOP",),
            "test_blender_chat_usage": ("ANGELO_QA_USAGE_SKIP",),
            "test_blender_media_drop": ("ANGELO_QA_NO_OPENGL",),
            "test_blender_studio_assets": ("ANGELO_QA_NO_OPENGL",),
            "test_blender_studio_workflows": ("ANGELO_QA_CHARACTER", "ANGELO_QA_SHEET", "ANGELO_QA_PARTS",
                                              "ANGELO_QA_GROUNDING"),
            "test_blender_route_parity": ("ANGELO_ROUTE_PARITY_RECORD",),
            "test_blender_godot_link_e2e": ("ANGELO_QA_GODOT",),
            "test_blender_godot_link_gui": ("ANGELO_QA_GODOT",),
        }
        self.assertEqual(expected, {suite.name: suite.options_env for suite in with_options})
        for suite in with_options:
            values = dict(self.base, ANGELO_SCULPT_CHECKPOINT_VARIANT="dyntopo", ANGELO_L10N_LOCALES="en_US",
                          ANGELO_COMPLETE_PHASE="capture", ANGELO_UNDECLARED_OPTION="must not reach a child")
            env = runner.plan_suite(suite, self.run_dir, self.binary, values)["env"]
            self.assertNotIn("ANGELO_UNDECLARED_OPTION", env)
            self.assertEqual("ANGELO_SCULPT_CHECKPOINT_VARIANT" in env,
                             suite.name == "test_blender_sculpt_checkpoint_bundle")
            self.assertEqual("ANGELO_L10N_LOCALES" in env, suite.name == "test_blender_translation")
            self.assertEqual("ANGELO_COMPLETE_PHASE" in env, suite.name == "test_blender_complete_checks")

    def test_every_suite_environment_is_isolated(self):
        allowed = set(runner.INHERITED_ENV)
        for suite in manifest.SUITES:
            if suite.kind == manifest.LIVE:
                continue
            with self.subTest(suite=suite.name):
                plan = self.plan(suite.name)
                env, directory = plan["env"], plan["directory"]
                for key, value in SECRETS.items():
                    self.assertNotEqual(env.get(key), value, key)
                # One bytecode folder for the run's suites, beside the run directory: compiled sources only.
                self.assertEqual(env.pop("PYTHONPYCACHEPREFIX"), str(self.run_dir.parent / runner.PYCACHE_FOLDER))
                for key, value in env.items():
                    if key not in allowed and key not in ("ANGELO_DISABLE_DEFAULT_CHAT", "ANGELO_EXPECTED_BINARY",
                                                          "ANGELO_DISABLE_KEYCHAIN", "ANGELO_DISABLE_INTRO",
                                                          "ANGELO_DISABLE_HERO", "ANGELO_AGENT_RUNTIME",
                                                          "ANGELO_JOURNAL_VERIFY", "ANGELO_STRICT_CHANGE_REPORTS",
                                                          runner.qa_waits.SCALE_ENV,
                                                          runner.qa_waits.FACTOR_ENV):
                        self.assertTrue(Path(value).is_relative_to(directory)   # Or its profile (HOME, TMP).
                                        or Path(value).is_relative_to(plan["profile"]), (key, value))
                    self.assertNotIn(REAL_HOME, value)
                for key in ("HOME", "TMPDIR", "BLENDER_USER_RESOURCES", "BLENDER_USER_CONFIG", "ANGELO_STATE_DIR",
                            "ANGELO_CONNECTIONS_DIR", "ANGELO_LIBRARY_DIR", "ANGELO_QA_DIR"):
                    self.assertTrue(Path(env[key]).is_dir(), key)
                self.assertEqual(env["CFFIXED_USER_HOME"], env["HOME"])  # Cocoa's home too (crash prompts).
                self.assertEqual(env["ANGELO_QA_LOG"], str(plan["log"]))
                self.assertEqual(env.get("ANGELO_DISABLE_DEFAULT_CHAT"), None if suite.default_chat else "1")
                self.assertEqual(env.get("ANGELO_DISABLE_KEYCHAIN"), "1")
                self.assertEqual(env.get("ANGELO_DISABLE_INTRO"), None if suite.first_launch else "1")
                self.assertEqual(env.get("ANGELO_DISABLE_HERO"), "1")
                self.assertEqual(env.get("ANGELO_AGENT_RUNTIME"), "off", "Stand-in CLIs, never a bundled runtime")
                self.assertEqual(env.get("ANGELO_JOURNAL_VERIFY"), "1", "every save reads its journal back")
                self.assertEqual(env.get("ANGELO_STRICT_CHANGE_REPORTS"), "1", "a drifting change report fails")
                # A caller's deprecated spelling must not leak a real profile or setting in.
                self.assertEqual([key for key in env if key.startswith(product_env.LEGACY_PREFIXES)], [])
                if not suite.output_root:
                    self.assertEqual(plan["preconditions"], [])

    def test_windows_profile_is_private_and_system_variables_are_kept(self):
        env, home = self.plan("test_blender_native")["env"], None
        home = Path(env["HOME"])
        self.assertEqual(env["USERPROFILE"], str(home))
        for key in ("APPDATA", "LOCALAPPDATA"):
            self.assertTrue(Path(env[key]).is_dir() and Path(env[key]).is_relative_to(home), key)
        for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROGRAMFILES(X86)", "PROGRAMDATA",
                    "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS", "USERNAME"):
            self.assertEqual(env[key], WINDOWS_ENV[key], key)
        for key in ("HOMEDRIVE", "HOMEPATH"):
            self.assertNotIn(key, env)
        self.assertEqual((env["TMP"], env["TEMP"]), (env["TMPDIR"], env["TMPDIR"]))

    def test_state_in_qa_nests_state_accounts_and_user_resources(self):
        plan = self.plan("test_blender_eval_live_agent_dryrun")
        for key in ("ANGELO_STATE_DIR", "ANGELO_CONNECTIONS_DIR", "ANGELO_LIBRARY_DIR", "BLENDER_USER_CONFIG",
                    "BLENDER_USER_RESOURCES"):
            self.assertTrue(Path(plan["env"][key]).is_relative_to(plan["qa"]), key)
        self.assertFalse(Path(plan["env"]["HOME"]).is_relative_to(plan["qa"]))
        plan = self.plan("test_blender_native")
        self.assertFalse(Path(plan["env"]["ANGELO_STATE_DIR"]).is_relative_to(plan["qa"]))

    def test_report_path_overrides_point_into_the_qa_directory(self):
        plan = self.plan("test_blender_ordered_recovery")
        self.assertEqual(plan["env"]["ANGELO_ORDERED_RECOVERY_REPORT"], str(plan["qa"] / "ordered-recovery.json"))
        self.assertEqual(plan["env"]["ANGELO_RUNTIME_QA_DIR"], str(plan["qa"]))

    def test_live_suites_use_caller_profile_and_opt_in_only(self):
        self.base.update(ANGELO_QA_LIVE_BOTH="1", ANGELO_QA_CLAUDE_STATE_DIR="/real/claude",
                         ANGELO_QA_CLAUDE_CONNECTION_ID="00000000-0000-0000-0000-000000000001",
                         ANGELO_DISABLE_KEYCHAIN="0", SENSEI_DISABLE_KEYCHAIN="0", TOONLAB_DISABLE_KEYCHAIN="0")
        plan = self.plan("test_blender_cross_provider_live")
        self.assertEqual(plan["env"]["HOME"], REAL_HOME)
        self.assertNotIn("CFFIXED_USER_HOME", plan["env"])
        for key in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOMEDRIVE", "HOMEPATH"):
            self.assertEqual(plan["env"][key], WINDOWS_ENV[key], key)  # Windows CLI logins live there.
        # The caller's real HOME is used, but its login Keychain never is.
        self.assertEqual(plan["env"]["ANGELO_DISABLE_KEYCHAIN"], "1")
        self.assertNotIn("SENSEI_DISABLE_KEYCHAIN", plan["env"])
        self.assertNotIn("TOONLAB_DISABLE_KEYCHAIN", plan["env"])
        self.assertEqual(plan["env"]["ANGELO_QA_LIVE_BOTH"], "1")
        self.assertNotIn("OPENAI_API_KEY", plan["env"])
        self.assertNotIn("ANGELO_QA_LIVE_CODEX", plan["env"])
        self.assertNotIn("ANGELO_JOURNAL_VERIFY", plan["env"], "a LIVE suite saves as the product does")
        self.assertNotIn("ANGELO_STRICT_CHANGE_REPORTS", plan["env"], "a LIVE suite reads reports as shipped")
        self.assertTrue(Path(plan["env"]["ANGELO_CONNECTIONS_DIR"]).is_relative_to(plan["directory"]))
        self.assertEqual(plan["preconditions"], [])
        self.assertEqual(self.plan("test_blender_shared_sessions")["preconditions"], [])
        del self.base["ANGELO_QA_LIVE_CODEX"]
        self.run_dir = self.run_dir / "second"
        self.assertIn("ANGELO_QA_LIVE_CODEX", self.plan("test_blender_shared_sessions")["preconditions"][0])

    def test_live_suites_run_the_bundled_agent_runtime_or_the_callers(self):
        self.base.update(ANGELO_QA_LIVE_BOTH="1", ANGELO_QA_CLAUDE_STATE_DIR="/real/claude",
                         ANGELO_QA_CLAUDE_CONNECTION_ID="00000000-0000-0000-0000-000000000001")
        self.assertNotIn("ANGELO_AGENT_RUNTIME", self.plan("test_blender_cross_provider_live")["env"])
        self.base["SENSEI_AGENT_RUNTIME"] = "/opt/sensei/codex"  # Never a spelling of this newer variable.
        self.run_dir = self.run_dir / "legacy"
        self.assertNotIn("ANGELO_AGENT_RUNTIME", self.plan("test_blender_cross_provider_live")["env"])
        self.base["ANGELO_AGENT_RUNTIME"] = "/opt/agent-runtime/codex"
        self.run_dir = self.run_dir / "caller"
        env = self.plan("test_blender_cross_provider_live")["env"]
        self.assertEqual(env["ANGELO_AGENT_RUNTIME"], "/opt/agent-runtime/codex")
        self.assertNotIn("SENSEI_AGENT_RUNTIME", env)
        self.assertEqual(self.plan("test_blender_native")["env"]["ANGELO_AGENT_RUNTIME"], "off")

    def test_live_opt_in_accepts_a_deprecated_spelling_under_its_angelo_name(self):
        self.base.update(TOONLAB_QA_LIVE_BOTH="1", TOONLAB_QA_CLAUDE_STATE_DIR="/real/claude",
                         ANGELO_QA_CLAUDE_CONNECTION_ID="00000000-0000-0000-0000-000000000001")
        plan = self.plan("test_blender_cross_provider_live")
        self.assertEqual(plan["preconditions"], [])
        self.assertEqual(plan["env"]["ANGELO_QA_LIVE_BOTH"], "1")
        self.assertEqual(plan["env"]["ANGELO_QA_CLAUDE_STATE_DIR"], "/real/claude")
        self.assertNotIn("TOONLAB_QA_LIVE_BOTH", plan["env"])

    def test_background_command(self):
        command = self.plan("test_blender_native")["command"]
        self.assertEqual(command[:6], [str(self.binary), "--factory-startup", "--disable-autoexec",
                                       "--python-exit-code", "1", "--background"])
        self.assertNotIn("--no-window-focus", command)
        self.assertEqual(command[-2:], ["--python", str(HERE / "test_blender_native.py")])
        self.assertNotIn("--enable-event-simulate", command)

    def test_gui_commands(self):
        command = self.plan("test_blender_studio")["command"]
        self.assertNotIn("--background", command)
        self.assertIn("--enable-event-simulate", command)
        self.assertIn("--no-window-focus", command)
        command = self.plan("test_blender_sculpt_undo_accounting")["command"]
        self.assertEqual(command[-6:], ["--log", "undo,undo.sculpt", "--log-level", "debug", "--python",
                                        str(HERE / "test_blender_sculpt_undo_accounting.py")])

    def test_every_window_suite_is_isolated_from_outside_input_unless_it_needs_real_input(self):
        # Outside input once started playback and lost a live-eval dry run (event simulation drops the
        # machine's real keyboard and mouse events).
        for name, suite in SUITES.items():
            if suite.host_launcher or not suite.needs_window:
                continue
            with self.subTest(suite=name):
                self.assertIn("--enable-event-simulate", self.plan(name)["command"])
        self.assertIn("--enable-event-simulate", self.plan("test_blender_native_programs", gui=True)["command"])
        real = dataclasses.replace(SUITES["test_blender_sculpt_undo_accounting"], real_input=True)
        command = runner.build_command(real, self.binary, True, self.run_dir / "qa")
        self.assertNotIn("--enable-event-simulate", command)
        self.assertIn("--no-window-focus", command)

    def test_gui_superset_runs_in_a_window_only_with_gui(self):
        self.assertIn("--background", self.plan("test_blender_native_programs")["command"])
        self.run_dir = self.run_dir / "gui"
        plan = self.plan("test_blender_native_programs", gui=True)
        self.assertTrue(plan["window"])
        self.assertNotIn("--background", plan["command"])

    def test_host_driver_command_and_output_root(self):
        suite = SUITES["test_blender_memory_project_resume"]
        plan = runner.plan_suite(dataclasses.replace(suite, output_root=str(self.run_dir)), self.run_dir,
                                 self.binary, self.base)
        self.assertEqual(plan["command"], [sys.executable, str(HERE / suite.script),
                                           "--binary", str(self.binary), "--out", str(plan["qa"] / "fixture")])
        self.assertEqual(plan["report"], plan["qa"] / "fixture/memory-project-resume.json")
        self.assertEqual(plan["preconditions"], [])
        elsewhere = str(self.run_dir / "elsewhere")
        plan = runner.plan_suite(dataclasses.replace(suite, output_root=elsewhere), self.run_dir / "second",
                                 self.binary, self.base)
        self.assertEqual(plan["preconditions"], ["needs --output under " + elsewhere])
        # The manifest's own value resolves to this platform's short temp root at run time.
        with mock.patch.object(native_suite_paths, "short_temp_root", return_value=self.run_dir):
            plan = runner.plan_suite(suite, self.run_dir / "third", self.binary, self.base)
        self.assertEqual(plan["preconditions"], [])
        with mock.patch.object(native_suite_paths, "short_temp_root", return_value=Path(elsewhere)):
            plan = runner.plan_suite(suite, self.run_dir / "fourth", self.binary, self.base)
        self.assertEqual(plan["preconditions"], ["needs --output under " + elsewhere])


class TempRootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-paths-test-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name).resolve()

    def test_short_temp_root_is_private_tmp_on_macos_and_the_temp_folder_elsewhere(self):
        temp = Path(tempfile.gettempdir()).resolve()
        for platform in ("linux", "win32"):
            self.assertEqual(native_suite_paths.short_temp_root(platform), temp)
        with mock.patch.object(native_suite_paths, "MACOS_SHORT_ROOT", self.folder):
            self.assertEqual(native_suite_paths.short_temp_root("darwin"), self.folder)
        with mock.patch.object(native_suite_paths, "MACOS_SHORT_ROOT", self.folder / "missing"):
            self.assertEqual(native_suite_paths.short_temp_root("darwin"), temp)

    def test_fixture_roots_must_be_new_and_disposable(self):
        root = self.folder / "fixture"
        with mock.patch.object(native_suite_paths, "short_temp_root", return_value=self.folder):
            self.assertEqual(native_suite_paths.fresh_fixture_root(str(root), environ={}), root)
            for refused in (self.folder, self.folder.parent / "elsewhere-fixture"):
                with self.subTest(refused=refused), self.assertRaisesRegex(ValueError, "fresh disposable"):
                    native_suite_paths.fresh_fixture_root(str(refused), environ={})
        # Outside macOS the runner's private TMPDIR is not above its QA folder: ANGELO_QA_DIR is accepted.
        qa = self.folder / "run/suite/qa"
        qa.mkdir(parents=True)
        tmp = self.folder / "run/suite/tmp"
        with mock.patch.object(native_suite_paths, "short_temp_root", return_value=tmp):
            self.assertEqual(native_suite_paths.fresh_fixture_root(str(qa / "fixture"), {"ANGELO_QA_DIR": str(qa)}),
                             qa / "fixture")
            with self.assertRaises(ValueError):
                native_suite_paths.fresh_fixture_root(str(qa / "fixture"), {})
        # macOS keeps exactly /private/tmp: the runner's QA folder is always inside it.
        with mock.patch.object(native_suite_paths, "MACOS_SHORT_ROOT", tmp), \
                mock.patch.object(native_suite_paths, "short_temp_root", return_value=tmp), \
                self.assertRaises(ValueError):
            native_suite_paths.fresh_fixture_root(str(qa / "fixture"), {"ANGELO_QA_DIR": str(qa)})


class ProcessTreeTests(unittest.TestCase):
    def test_windows_kills_the_whole_tree_by_pid(self):
        process = mock.Mock(pid=4242)
        with mock.patch.object(runner.subprocess, "run") as run:
            runner._kill(process, platform="win32")
        self.assertEqual(run.call_args.args[0], ["taskkill", "/T", "/F", "/PID", "4242"])
        process.kill.assert_called_once_with()
        with mock.patch.object(runner.subprocess, "run", side_effect=FileNotFoundError("taskkill")):
            runner._kill(process, platform="win32")  # No taskkill: the suite's own process still dies.
        self.assertEqual(process.kill.call_count, 2)

    def test_suites_get_their_own_process_group(self):
        self.assertEqual(runner._session_options("darwin"), {"start_new_session": True})
        self.assertEqual(runner._session_options("linux"), {"start_new_session": True})
        self.assertEqual(runner._session_options("win32"), {"creationflags": 0x200})  # CREATE_NEW_PROCESS_GROUP


class LogScannerTests(unittest.TestCase):
    def kinds(self, text):
        return [finding["kind"] for finding in checks.scan_log(text)]

    def test_native_failure_markers(self):
        samples = {
            "BLI_assert failed: source/a.cc:10, f(), at 'x'": "native_assertion",
            "partial stdout lineBLI_assert failed: source/a.cc:10, f(), at 'x'": "native_assertion",
            "Code marked as unreachable has been executed. Please report this as a bug.": "native_assertion",
            "Assertion failed: (x), function f, file a.c, line 3.": "native_assertion",
            "Angelo: a.c:3: int f(): Assertion `x' failed.": "native_assertion",
            "Error: Not freed memory blocks: 3, total unfreed memory 0.001 MB": "allocator_leak",
            "Freeing memory after the leak detector has run. This can happen": "allocator_leak",
            "Memoryblock Foo: end corrupt": "allocator_error",
            "Malloc returns null: len=1 in x, total 2": "allocator_error",
            "Writing: /tmp/run/tmp/angelo.crash.txt": "crash",
            "Fatal Python error: Segmentation fault": "crash",
            "ERROR: Python context internal state bug. this should not happen!": "python_context_state",
        }
        for line, kind in samples.items():
            with self.subTest(line=line):
                self.assertEqual(self.kinds("Blender 5.2.2\n" + line + "\nBlender quit\n"), [kind])

    def test_clean_and_embedded_text_is_not_a_failure(self):
        clean = ('Blender 5.2.2 LTS\nANGELO_X {"error": "Traceback (most recent call last):\\n  File"}\n'
                 "No Assertion failed: here\nreport Memoryblock mention\nBlender quit\n")
        self.assertEqual(self.kinds(clean), [])

    def test_line_initial_tracebacks_are_failures(self):
        def block(exception):
            return ('Traceback (most recent call last):\n  File "x.py", line 1, in <module>\n    f()\n'
                    + exception + "\n")
        timer = block("RuntimeError: timer failed")
        self.assertEqual(checks.scan_log("Blender 5.2.2\n" + timer + "Blender quit\n"),
                         [{"kind": "python_traceback", "line": "RuntimeError: timer failed"}])
        chained = (block("KeyError: 'a'") + "\nDuring handling of the above exception, another exception occurred:\n\n"
                   + timer)
        self.assertEqual(checks.traceback_exceptions(chained), ["KeyError: 'a'", "RuntimeError: timer failed"])
        self.assertEqual(checks.traceback_exceptions("Traceback (most recent call last):\n  File"),
                         ["<truncated traceback>"])

    def test_deliberate_operator_reports_are_not_failures(self):
        self.assertEqual(self.kinds("Error: Injected transcript failure at pre_notice\nWarning: Unwrap failed\n"), [])

    def test_crash_logs_are_found_anywhere_in_the_suite_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            crash = Path(directory) / "qa/runtime-temp/angelo.crash.txt"
            crash.parent.mkdir(parents=True)
            self.assertEqual(checks.find_crash_logs(directory), [])
            crash.write_text("# backtrace")
            self.assertEqual(checks.find_crash_logs(directory), [str(crash)])

    def test_a_crash_logs_tail_reaches_the_suite_log_and_the_summary(self):
        # A remote run brings back only the suite's log (Windows build VM, media_drop on 19d51292ee1): the crash log's
        # end goes there and into the summary, the runner's home folder shown as "~".
        from native_suite_summary import human_summary
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(Path, "home", return_value=Path(directory)):
            crash = Path(directory) / "tmp/angelo.crash.txt"   # Under the home folder, as with a suite's own HOME.
            crash.parent.mkdir(parents=True)
            home = directory
            crash.write_text("\n".join(["line %d" % i for i in range(100)] + ["at %s/build/blender.exe" % home]))
            log = Path(directory) / "suite.log"
            log.write_text("the suite's own output\n")
            tails = {}
            reasons = checks.crash_logs(directory, log, 0, tails)
            written = log.read_text()
        shown = "~/tmp/angelo.crash.txt"
        tail = tails[shown].splitlines()
        self.assertEqual(len(tail), checks.CRASH_TAIL)
        self.assertEqual((tail[0], tail[-1]), ("line 41", "at ~/build/blender.exe"))
        self.assertNotIn(home, written)
        self.assertTrue(written.startswith("the suite's own output\n") and "line 99" in written
                        and "line 40" not in written)
        self.assertEqual(reasons, ["crash log written: %s (the suite's own process exited 0: a process it started "
                                   "crashed)" % shown])
        self.assertTrue(all(reason.startswith("crash") for reason in reasons), "still a crash for the status")
        result = {"status": "crashed", "duration_seconds": 1.0, "mode": "background", "name": "suite",
                  "reasons": reasons, "crash_logs": tails}
        text = human_summary({"suites": [result], "counts": {"error": 0, "timeout": 0, "crashed": 1, "failed": 0,
                                                             "passed": 0, "skipped": 0},
                              "success": False, "output": "out"})
        self.assertIn("           | at ~/build/blender.exe", text)

    def test_a_crash_of_the_suites_own_process_is_not_called_a_childs(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "angelo.crash.txt").write_text("# backtrace")
            self.assertEqual(checks.crash_logs(directory, None, 0xC0000005, {}),
                             ["crash log written: %s" % checks.scrub_home(str(Path(directory) / "angelo.crash.txt"))])

    def test_exit_status_classification(self):
        self.assertEqual(checks.describe_exit(0), (None, None))
        self.assertEqual(checks.describe_exit(1), ("failed", "exit status 1"))
        self.assertEqual(checks.describe_exit(-11), ("crashed", "terminated by SIGSEGV"))
        # Windows: an access violation ends the process with its NTSTATUS code, not a signal.
        self.assertEqual(checks.describe_exit(0xC0000005), ("crashed", "terminated by NTSTATUS 0xC0000005"))
        self.assertEqual(checks.describe_exit(255), ("failed", "exit status 255"))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-report-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "report.json"
        self.started = time.time()

    def reasons(self, value, spec=manifest.Report("report.json"), raw=None):
        self.path.write_text(raw if raw is not None else json.dumps(value))
        return checks.validate_report(spec, self.path, self.started)

    def test_passing_reports(self):
        self.assertEqual(self.reasons({"success": True, "checks": {"a": True}, "paid_requests": 0, "error": None}), [])
        self.assertEqual(self.reasons({"completed": True, "checks": {}}, manifest.Report("r", "completed")), [])
        self.assertEqual(self.reasons({"checks": [{"name": "a", "passed": True}]}, manifest.Report("r", None)), [])
        phased = manifest.Report("r", require_complete_phase=True)
        self.assertEqual(self.reasons({"success": True, "phase": "complete"}, phased), [])

    def test_failing_reports_fail_closed(self):
        failing = [{"success": False}, {}, {"success": "true"}, {"success": 1},
                   {"success": True, "checks": {"a": False}},
                   {"success": True, "checks": {"a": 1}}, {"success": True, "paid_requests": 1},
                   {"success": True, "paid_requests": False}, {"success": True, "billable_requests": 2},
                   {"success": True, "provider_started": True}, {"success": True, "failures": {"case": "trace"}},
                   {"success": True, "error": "Traceback\nValueError: x"}, []]
        for value in failing:
            with self.subTest(value=value):
                self.assertTrue(self.reasons(value))
        self.assertTrue(self.reasons(None, raw="{"))
        phased = manifest.Report("r", require_complete_phase=True)
        self.assertTrue(self.reasons({"success": True, "phase": "boot"}, phased))
        for value in ({"checks": {}}, {"passed": 3}, {"checks": [{"name": "a", "passed": False}]}):
            self.assertTrue(self.reasons(value, manifest.Report("r", None)))

    def test_missing_and_stale_reports(self):
        self.assertIn("missing", checks.validate_report(manifest.Report("r"), self.path, self.started)[0])
        self.reasons({"success": True})
        os.utime(self.path, (self.started - 60, self.started - 60))
        self.assertIn("older", checks.validate_report(manifest.Report("r"), self.path, self.started)[0])


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="native-process-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.binary = self.root / "Angelo"
        self.binary.write_text(FAKE_BINARY.format(python=sys.executable))
        self.binary.chmod(0o755)
        if os.name == "nt":   # No shebang: a batch file starts the fixture (the runner kills its tree with taskkill).
            script, self.binary = self.binary.with_suffix(".py"), self.root / "Angelo.cmd"
            (self.root / "Angelo").rename(script)
            self.binary.write_text('@"%s" "%s" %%*\r\n' % (sys.executable, script), newline="")
        self.base = dict(SECRETS, HOME=REAL_HOME, PATH=os.environ.get("PATH", "/usr/bin:/bin"))
        self.addCleanup(setattr, runner, "source_revision", runner.source_revision)
        runner.source_revision = lambda: "fixture"

    def main(self, suites, *extra):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            # A fixture executable bundles no add-on; the guard has its own tests (test_native_addon_guard).
            code = runner.main(["--binary", str(self.binary), "--output", str(self.root / "out")] + list(extra),
                               suites=suites, base_env=self.base, addon_check=lambda binary, root: {"matches": True})
        runs = list((self.root / "out").glob("angelo-native-suites-*"))
        summary = json.loads((runs[0] / "summary.json").read_text()) if runs else None
        return code, summary, output.getvalue()

    def test_outcomes_are_classified_from_real_processes(self):
        suites = (fake_suite("fake_pass", marker="FAKE_OK"), fake_suite("fake_assert"),
                  fake_suite("fake_quit_failed"), fake_suite("fake_signal"), fake_suite("fake_sleep", timeout=1),
                  fake_suite("fake_marker", report=None, marker="NEVER_PRINTED"))
        code, summary, output = self.main(suites, "--jobs", "3")
        status = {item["name"]: item for item in summary["suites"]}
        self.assertEqual(code, 1)
        self.assertFalse(summary["success"])
        self.assertEqual(status["fake_pass"]["status"], "passed")
        self.assertIn("native_assertion", status["fake_assert"]["reasons"][0])
        self.assertEqual(status["fake_quit_failed"]["status"], "failed")
        self.assertEqual(status["fake_signal"]["status"], "crashed")
        self.assertIn("NTSTATUS 0xC0000005" if os.name == "nt" else "SIGTERM", status["fake_signal"]["reasons"][0])
        self.assertEqual(status["fake_sleep"]["status"], "timeout")
        self.assertLess(status["fake_sleep"]["duration_seconds"], 30)
        self.assertIn("NEVER_PRINTED", status["fake_marker"]["reasons"][-1])
        self.assertIn("Durations measured with up to 3", summary["timing_note"])
        self.assertIn("FAILED", output)
        self.assertTrue((Path(summary["output"]) / "summary.txt").is_file())

    def test_child_receives_only_isolated_environment(self):
        code, summary, _ = self.main((fake_suite("fake_pass"),))
        self.assertEqual(code, 0)
        self.assertTrue(summary["success"])
        child = json.loads((Path(summary["suites"][0]["qa_dir"]) / "env.json").read_text())
        directory = Path(summary["output"]) / "fake_pass"
        self.assertTrue(Path(child["HOME"]).resolve().is_relative_to(runner.profile_folder(
            Path(summary["output"]), fake_suite("fake_pass"))))   # Beside it: never as deep as its name.
        self.assertTrue(Path(child["BLENDER_USER_CONFIG"]).resolve().is_relative_to(directory))
        for key, value in SECRETS.items():
            self.assertNotEqual(child.get(key), value, key)

    def test_usage_errors_exit_two(self):
        self.assertEqual(self.main((fake_suite("fake_pass"),), "--include", "nothing")[0], 2)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(runner.main(["--binary", "relative/Angelo"], suites=(), base_env={}), 2)


if __name__ == "__main__":
    unittest.main()
