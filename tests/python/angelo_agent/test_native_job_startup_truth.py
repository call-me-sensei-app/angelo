# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mutable job startup cannot borrow no-change authority from a refusal code."""

import ast
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import job_host, plan_recovery
from angelo_agent.job_manager import JobManager, JobStartError, NOTHING_RAN
from angelo_agent.job_spec import JobRequestError, validate_job
from angelo_agent.job_store import JobStore

START = {"tool": "angelo_job_start", "arguments": {"kind": "RENDER_STILL", "output": "renders/shot.png"}}


class StartupTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = JobManager(JobStore(Path(self.temp.name) / "jobs.json"))
        ui = types.ModuleType("angelo_agent.job_ui")
        ui.manager = lambda: self.manager
        modules = patch.dict(sys.modules, {"angelo_agent.job_ui": ui})
        modules.start()
        self.addCleanup(modules.stop)
        self.controller = types.SimpleNamespace(state={"id": "chat"}, files=None, executing_call=None)

    def runner(self, *, error=None, restore=(), cleanup_error=None, admission=None, exists=False,
               stage_error=None, abandon_error=None):
        # The production class, including start/_undo_start/abandon, executes
        # unchanged over owned scene/settings/output stand-ins. No bpy import.
        source = ROOT / "scripts/addons_core/angelo_agent/job_runners.py"
        tree = ast.parse(source.read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Runner")
        scene = types.SimpleNamespace(session_uid=1, setting=0, callback_effect=0)
        events = []

        class Overrides:
            def __init__(self, scene, identity):
                self.scene = scene
            def apply(self):
                events.append("apply")
                scene.setting = 1
            def restore(self):
                events.append("restore")
                if cleanup_error:
                    raise cleanup_error
                if not restore:
                    scene.setting = 0
                return list(restore)

        class Staging:
            def __init__(self, *args):
                pass
            def create(self):
                events.append("create")
                if stage_error:
                    scene.callback_effect = 1
                    raise stage_error
                return self
            def verify(self):
                events.append("verify")
            def discard(self):
                events.append("discard")
                if abandon_error:
                    raise abandon_error

        output = types.SimpleNamespace(exists=lambda: exists, is_symlink=lambda: False)
        namespace = {"JobRequestError": JobRequestError, "JobStartError": JobStartError,
                     "native": lambda: None, "Overrides": Overrides,
                     "render_extensions": types.SimpleNamespace(recheck=lambda runner: None),
                     "job_outputs": types.SimpleNamespace(admit_output=lambda *args: output, Staging=Staging)}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(source), "exec"), namespace)

        class FixtureRunner(namespace["Runner"]):
            def admit(self):
                if admission:
                    raise admission
            def _start(self):
                self.overrides.apply()
                scene.callback_effect = 1  # Settings restore cannot prove arbitrary callback effects gone.
                raise error

        request = validate_job("angelo_job_start", START["arguments"])
        runner = FixtureRunner(types.SimpleNamespace(scene=scene), request, types.SimpleNamespace(root=Path(self.temp.name)))
        return runner, scene, events

    def submit(self, runner):
        request = validate_job("angelo_job_start", START["arguments"])
        with patch.object(job_host, "_admit", return_value=(request, runner)):
            answer = job_host.execute(self.controller, object(), "angelo_job_start", START["arguments"])
        return answer, self.manager.record(answer["job"])["receipt"]

    def uncertain(self, answer, receipt):
        for result in (answer, receipt):
            self.assertTrue(result.get("partial_changes_possible"), result)
            self.assertNotIn("scene_changed", result)
            self.assertNotEqual(result.get("note"), NOTHING_RAN)
        self.assertIsNone(plan_recovery.unchanged_failure([START], [answer]))
        self.assertIsNone(self.manager.active())

    def test_classified_error_after_actual_start_is_uncertain_even_with_settings_restored(self):
        for error_type in (JobRequestError, JobStartError):
            for restore in ((), ("render.resolution_x",)):
                with self.subTest(error=error_type.__name__, restore=restore):
                    runner, scene, events = self.runner(error=error_type("closed-looking failure", code="VALIDATION"),
                                                        restore=restore)
                    answer, receipt = self.submit(runner)
                    self.uncertain(answer, receipt)
                    self.assertEqual(answer["error_code"], "VALIDATION")
                    self.assertEqual(scene.callback_effect, 1)
                    self.assertEqual(scene.setting, int(bool(restore)))
                    self.assertEqual(events, ["create", "verify", "apply", "restore", "discard"])
                    if restore:
                        self.assertEqual(receipt["unrestored"], list(restore))

    def test_cleanup_exception_cannot_restore_no_change_authority_and_still_discards(self):
        for where in ("cleanup_error", "abandon_error"):
            with self.subTest(where=where):
                runner, scene, events = self.runner(error=JobRequestError("startup", code="VALIDATION"),
                    **{where: JobStartError("cleanup callback", code="VALIDATION")})
                answer, receipt = self.submit(runner)
                self.uncertain(answer, receipt)
                self.assertEqual(scene.callback_effect, 1)
                self.assertIn("discard", events)

    def test_staging_failure_after_owned_effect_is_also_uncertain(self):
        runner, scene, events = self.runner(stage_error=JobRequestError("staging callback", code="VALIDATION"))
        answer, receipt = self.submit(runner)
        self.uncertain(answer, receipt)
        self.assertEqual(scene.callback_effect, 1)
        self.assertIn("restore", events)
        self.assertNotIn("apply", events)

    def test_real_admission_refusals_remain_correctable(self):
        runner, scene, events = self.runner(admission=JobRequestError("No camera", code="VALIDATION"))
        answer, receipt = self.submit(runner)
        self.assertIs(answer["scene_changed"], False)
        self.assertIs(receipt["scene_changed"], False)
        self.assertEqual(receipt["note"], NOTHING_RAN)
        self.assertIsNotNone(plan_recovery.unchanged_failure([START], [answer]))
        self.assertEqual((scene.setting, scene.callback_effect, events), (0, 0, []))

    def test_a_taken_output_is_never_refused_at_start(self):
        # 1.0: a job never moves or replaces a file, and a taken name is no refusal: it writes under the next free
        # name (job_output_names), so the start goes on to stage its output.
        runner, _scene, events = self.runner(exists=True, error=JobStartError("No camera", code="VALIDATION"))
        answer, _receipt = self.submit(runner)
        self.assertIn("create", events)
        self.assertNotIn("exists", answer.get("error", ""))

    def test_runner_without_phase_evidence_cannot_claim_unchanged(self):
        class UnobservedRunner:
            output_name = None
            def describe(self):
                return {}
            def start(self):
                raise JobStartError("No camera", code="VALIDATION")
        answer, receipt = self.submit(UnobservedRunner())
        self.uncertain(answer, receipt)


if __name__ == "__main__":
    unittest.main()
