# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Headless Python: a script file the person always allows while it is unchanged runs without Angelo's window.

Maintainer decision (2026-10-23): headless Angelo runs Python under the script-file approval rule; with no window to
approve in, an unapproved or changed file is refused with a clear message saying how to approve it. Contract:
doc/angelo/external_mcp.rst, "Headless" and "Script files".

The launcher, its session and a headless child in the same process with a stand-in Blender (test_native_headless_mcp's
harness). Covered: an approved file runs headless and its result is saved to a new file beside the source, never over
it; an unapproved file, a changed file and an approval for another project or app each end as ``needs_gui`` naming the
file, the project and how to approve, and nothing runs; the approval is keyed by the opened source even once edits are
saved to the output; raw ``angelo_execute_python`` stays refused headless; an app without the python level gets
neither the tool nor the approvals folder; the child reads the person's approvals and can never change them.
"""

import json
import tempfile
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import script_files  # noqa: E402
from angelo_agent.external_clients import EDIT, PYTHON, READ, SCRIPT_FILE_TOOL, AppRegistry  # noqa: E402
from angelo_agent.headless_host import bind_script_files  # noqa: E402
from angelo_agent.headless_session import child_command  # noqa: E402
import test_native_headless_mcp as headless_harness  # noqa: E402  (a module: its tests are not collected again here)
from test_native_script_files import RecordingExecutor  # noqa: E402

BUILD = "result = 7\n"


@unittest.skipIf(sys.platform == "win32", "Headless sessions are macOS and Linux for now")
class HeadlessScriptFileTests(unittest.TestCase):
    tick, call, open = headless_harness.HeadlessTests.tick, headless_harness.HeadlessTests.call, headless_harness.HeadlessTests.open

    def setUp(self):
        headless_harness.HeadlessTests.setUp(self)
        self.executor = RecordingExecutor(str(self.root / "child-state"))
        self.addCleanup(script_files.detach)
        self.script = self.root / "work" / "build.py"
        self.script.parent.mkdir()
        self.script.write_text(BUILD)

    def start_child(self, options, read_fd, write_fd, process):
        """The harness's child, plus what headless_main and HeadlessEnvironment add for script files."""
        headless_harness.HeadlessTests.start_child(self, options, read_fd, write_fd, process)
        self.child_options = options
        if options.get("--approvals"):
            script_files.attach(options["--approvals"], read_only=True)
        made = self.child_env.make_controller
        self.child_env.make_controller = lambda identity: bind_script_files(
            made(identity), options["--file"], options["--levels"].split(","), session_open=self.host.session_file_open)

    def approve(self, project=None, app=None):
        """What the person's "Always allow this file while it's unchanged" in the window saved."""
        script_files.attach(self.state)
        script_files.allow(app or self.name, project or os.path.realpath(self.source),
                           script_files.read_script(str(self.script)))
        script_files.detach()

    def run_file(self):
        return self.call(SCRIPT_FILE_TOOL, {"file": str(self.script)}, timeout=20)

    def assert_refused_with_how_to_approve(self, result):
        self.assertTrue(result["isError"], result)
        text = json.dumps(result)
        self.assertIn("needs_gui:", text, result)
        self.assertIn(str(os.path.realpath(self.script)), text)
        self.assertIn("Always allow this file while it", text)
        self.assertEqual(self.executor.codes, [], "nothing ran")

    def test_an_approved_file_runs_headless_and_is_saved_to_a_new_file(self):
        self.approve()
        self.open()
        result = self.run_file()
        self.assertFalse(result["isError"], result)
        self.assertEqual(self.executor.codes, [BUILD])
        output = self.host.output
        self.assertIn("Saved as %s" % output, json.dumps(result))
        self.assertNotEqual(output.resolve(), self.source.resolve())
        self.assertEqual(self.source.read_bytes(), b"BLENDER-v502 source scene", "the source never changes")

    def test_an_unapproved_or_changed_file_is_refused_with_how_to_approve(self):
        self.open()
        self.assert_refused_with_how_to_approve(self.run_file())
        self.approve()
        self.script.write_text(BUILD + "result = 8\n")
        self.assert_refused_with_how_to_approve(self.run_file())

    def test_an_approval_for_another_project_or_app_is_not_this_one(self):
        self.approve(project=os.path.realpath(self.folder / "other.blend"))
        self.approve(app="codex")
        self.open()
        self.assert_refused_with_how_to_approve(self.run_file())

    def test_the_approval_stays_keyed_by_the_source_once_edits_are_saved_to_the_output(self):
        self.approve()
        self.open()
        self.assertFalse(self.call("angelo_create_cube", {"name": "Box", "size": 1, "location": [0, 0, 0]})["isError"])
        self.assertIsNotNone(self.host.output)
        result = self.run_file()
        self.assertFalse(result["isError"], result)
        self.assertEqual(self.executor.codes, [BUILD])

    def test_a_run_that_opens_another_file_saves_nothing_and_the_session_keeps_its_own(self):
        """Break-it review M2: the host owns the document; a script's open of another file never becomes the output."""
        self.approve()
        self.open()
        other = str(self.folder / "other.blend")
        self.executor.on_run = lambda: setattr(self.bpy.data, "filepath", other)
        result = self.run_file()
        self.assertIn("opened or saved as another file", json.dumps(result))
        self.assertEqual(self.bpy.saves, [], "nothing of the run is saved")
        reloaded = Path(self.bpy.loads[-1])
        self.assertEqual((reloaded.parent.resolve(), reloaded.name.startswith("scene.angelo-")),
                         (self.source.parent.resolve(), True), "the session's own file again: a fresh copy of the source, "
                         "never the source itself (FR-M2 review)")
        self.assertEqual(reloaded.read_bytes(), self.source.read_bytes())
        self.executor.on_run = None
        again = self.run_file()
        self.assertFalse(again["isError"], again)
        self.assertIn("Saved as", json.dumps(again))

    def test_approvals_apply_only_while_the_sessions_own_file_is_open(self):
        self.approve()
        self.open()
        controller = next(iter(self.child_env.controllers().values()), None) or self.child_env.make_controller("x")
        self.assertEqual(controller.script_project(), os.path.realpath(self.source))
        self.bpy.data.filepath = str(self.folder / "other.blend")
        self.assertEqual(controller.script_project(), "", "another file open: no approval applies")

    def test_a_failed_run_is_always_reloaded_and_nothing_ran_is_structured(self):
        """Review N2: a failed script may leave a change Undo could not restore, so the session's last saved state is
        loaded again; and "nothing ran" is read from the result's fields, never from words an error could echo."""
        from angelo_agent.headless_host import nothing_ran
        self.approve()
        self.open()

        def fail():
            raise RuntimeError("nothing ran, says the script, but it did")
        self.executor.on_run = fail
        loads = len(self.bpy.loads)
        result = self.run_file()
        self.assertTrue(result["isError"])
        self.assertGreater(len(self.bpy.loads), loads, "the last saved state is loaded again")
        self.assertEqual(self.bpy.saves, [], "nothing of the failed run is saved")
        refused = {"content": [{"type": "text", "text": "needs_gui: ... nothing ran."},
                               {"type": "text", "text": json.dumps({"error_code": "NEEDS_GUI", "scene_changed": False})}]}
        echoed = {"content": [{"type": "text", "text": "Object 'nothing ran' failed"},
                              {"type": "text", "text": json.dumps({"error": "x", "scene_changed": True})}]}
        self.assertTrue(nothing_ran(refused))
        self.assertFalse(nothing_ran(echoed))

    def test_a_run_that_writes_the_source_says_so(self):
        """Break-it review L1: never "unchanged" when the run itself wrote the source; never blaming only the script,
        since the person's add-ons can save too (FR-M2 review)."""
        self.approve()
        self.open()
        self.executor.on_run = lambda: self.source.write_bytes(b"BLENDER-v502 written by the script")
        result = self.run_file()
        self.assertIn("changed on disk during this call (an approved script or one of your add-ons saved it)",
                      json.dumps(result))
        self.assertNotIn("is unchanged", json.dumps(result))

    def test_a_refusal_says_the_actual_reason(self):
        """Break-it review L2: Ask, policy and a script that names Angelo's add-on are not "not approved"."""
        from angelo_agent import managed_policy
        from angelo_agent.managed_policy import Policy, with_organization
        from angelo_agent.team_view import org_policy
        self.approve()
        self.open()
        self.approve_mode = "ASK"
        self.assertIn("set to Ask or Read only", json.dumps(self.run_file()))
        self.approve_mode = "AUTO_BOUNDED"
        managed_policy.reset(with_organization(Policy(), org_policy({"allowAutoApproval": False})))
        self.assertIn("AllowAutoApproval", json.dumps(self.run_file()))
        managed_policy.reset(Policy())
        self.script.write_text("import angelo_agent\nresult = 1\n")
        self.approve()
        self.assertIn("refers to preferences or Angelo's own add-on", json.dumps(self.run_file()))
        self.assertEqual(self.executor.codes, [])

    def test_the_child_registers_the_windows_apply_operators(self):
        """Break-it review H1: the script operator was missing, so every approved run failed in a real child."""
        from types import SimpleNamespace
        from angelo_agent import headless_main
        classes = [SimpleNamespace(bl_idname=name) for name in ("angelo.other", *headless_main.APPLY_OPERATORS)]
        found = headless_main.apply_operators(SimpleNamespace(CLASSES=classes))
        self.assertEqual([cls.bl_idname for cls in found], list(headless_main.APPLY_OPERATORS))
        source = (Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent/headless_main.py").read_text()
        self.assertIn("(ui.ANGELO_Preferences, ui.ANGELO_OT_approve) + apply_operators(ui)", source)

    def test_paths_the_app_named_stay_as_named_under_a_broad_private_root(self):
        """CI on Linux (2026-10-24): with the project under the temporary folder (or home), the result said "Saved as
        <temp>/..." and the refusal hid the very script path the app must approve. What the app named (the source's
        folder, where the output goes, and its script files) stays as named, ahead of <temp> and <home>."""
        broad = str(self.root.parent)
        with patch.object(tempfile, "gettempdir", lambda: broad), \
                patch.object(Path, "home", classmethod(lambda cls: Path(broad))):
            self.approve()
            self.open()
            result = self.run_file()
            self.assertFalse(result["isError"], result)
            self.assertIn("Saved as %s" % self.host.output, json.dumps(result))
            self.assertNotIn("<home>", json.dumps(result))
            self.script.write_text(BUILD + "result = 8\n")   # Changed: refused, naming the file and the project.
            refused = self.run_file()
            self.assertIn("needs_gui", refused["content"][0]["text"])
            # The full reason is the structured error; the summary line is clipped for the model (claude_direct.why,
            # 300 characters), so where a long path falls past the cut depends on how long the paths are.
            error = json.loads(refused["content"][-1]["text"])["error"]
            self.assertTrue(error.startswith("needs_gui:"), error)
            self.assertIn(os.path.realpath(self.script), error)
            self.assertIn(os.path.realpath(self.source), error)
            self.assertNotIn("<temp>", error)

    def test_raw_python_stays_refused_headless(self):
        self.approve()
        self.open()
        result = self.call("angelo_execute_python", {"description": "same text", "code": BUILD}, timeout=10)
        self.assertTrue(result["isError"])
        self.assertEqual(result["structuredContent"]["code"], "needs_gui")
        self.assertEqual(self.executor.codes, [])

    def test_an_app_without_the_python_level_gets_neither_the_tool_nor_the_approvals(self):
        self.approve()
        AppRegistry(self.state).set_levels(self.name, (READ, EDIT))
        self.open()
        result = self.run_file()
        self.assertTrue(result["isError"])
        self.assertNotIn("--approvals", self.child_options)
        self.assertEqual(self.executor.codes, [])
        command = child_command("/A", "/s.py", "/f.blend", "c", "claude_code", (READ, PYTHON), approvals="/state")
        self.assertEqual(command[command.index("--approvals") + 1], "/state")
        self.assertNotIn("--approvals", child_command("/A", "/s.py", "/f.blend", "c", "claude_code", (READ,),
                                                      approvals="/state"))

    def test_the_child_reads_the_persons_approvals_and_never_changes_them(self):
        self.approve()
        script_files.attach(self.state, read_only=True)
        with self.assertRaises(PermissionError):
            script_files.forget(app=self.name)
        with self.assertRaises(PermissionError):
            script_files.allow(self.name, os.path.realpath(self.source), script_files.read_script(str(self.script)))
        self.assertEqual(len(script_files.entries()), 1)


if __name__ == "__main__":
    unittest.main()
