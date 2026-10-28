# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A connected app's script files and "Always allow this file while it's unchanged" (``script_files``).

Maintainer decision (2026-10-23): an opt-in standing approval for an external app's script file, keyed by the app,
the project, the file's path and its content hash; any change asks again. Contract: doc/angelo/external_mcp.rst,
"Script files".

Dependency-free. ``read_script``: absolute paths only, the real path, a regular UTF-8 file of at most one script's
length, read once, never a FIFO, a directory or a final symbolic link swapped in after resolving. The store: saved
before it takes effect, owner-only, one approval per app, project and path, removed by app or file, a damaged file
holding none. Then the real ``ExternalHost`` and MCP ``Server`` with Blender stand-ins: ``angelo_run_script_file``
is offered and accepted only with the python level; the first run waits for the person, who may always allow the
file once its text was opened; the same file then runs unasked with consent ``script_file``, while a changed file,
another project, an unsaved file, another app, Ask, ``AllowAutoApproval`` off, a forgotten approval, a script that
names preferences or Angelo's add-on, and the same code sent as ``angelo_execute_python`` all ask again. What runs is
exactly what Angelo read, even when the file changes before dispatch. The card and Preferences show it.
"""

import ast
import hashlib
import json
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import managed_policy, script_files  # noqa: E402
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.external_clients import PYTHON, READ, SCRIPT_FILE_TOOL, AppRegistry  # noqa: E402
from angelo_agent.external_mcp_tools import app_tools  # noqa: E402
from angelo_agent.managed_policy import Policy, with_organization  # noqa: E402
from angelo_agent.team_view import org_policy  # noqa: E402
import test_native_external_mcp as mcp_harness  # noqa: E402  (a module: its tests are not collected again here)
from test_native_external_cli_host import Executor  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
PROJECT = os.path.realpath("/tmp/angelo-project/scene.blend")
BUILD = "result = 7\n"


class ReadScriptTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="angelo-script-files-"))
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write(self, name, data):
        path = self.folder / name
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        return path

    def test_a_script_is_read_once_with_its_real_path_and_hash(self):
        path = self.write("build.py", "import bpy\nresult = 1\n")
        link = self.folder / "link.py"
        link.symlink_to(path)
        for given in (path, link):
            script = script_files.read_script(str(given))
            self.assertEqual(script.path, os.path.realpath(path))
            self.assertEqual(script.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(script.text, "import bpy\nresult = 1\n")
            self.assertEqual(script_files.text_digest(script.text), script.sha256)

    def test_what_is_not_one_script_file_is_refused_with_why(self):
        os.mkfifo(self.folder / "pipe")
        (self.folder / "folder").mkdir()
        cases = {
            "relative": ("build.py", "absolute"),
            "missing": (str(self.folder / "missing.py"), "can't open"),
            "a directory": (str(self.folder / "folder"), "not a regular file"),
            "a FIFO, without waiting on it": (str(self.folder / "pipe"), "not a regular file"),
            "not UTF-8": (str(self.write("latin.py", b"name = '\xe9'\n")), "not UTF-8"),
            "empty": (str(self.write("empty.py", "  \n")), "empty"),
            "too long": (str(self.write("long.py", "#" * (script_files.MAX_CHARACTERS + 1))), "larger"),
            "no path": ("", "absolute path"),
        }
        for label, (path, words) in cases.items():
            with self.subTest(label), self.assertRaises(script_files.ScriptFileError) as raised:
                script_files.read_script(path)
            self.assertIn(words, str(raised.exception), label)
        exactly = self.write("exact.py", "é" * script_files.MAX_CHARACTERS)
        self.assertEqual(len(script_files.read_script(str(exactly)).text), script_files.MAX_CHARACTERS)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is POSIX")
    def test_a_link_swapped_in_after_resolving_is_never_followed(self):
        target = self.write("real.py", "result = 1\n")
        link = self.folder / "swapped.py"
        link.symlink_to(target)
        with patch.object(script_files.os.path, "realpath", lambda path: str(link)):
            with self.assertRaises(script_files.ScriptFileError):
                script_files.read_script(str(link))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="angelo-script-store-")
        self.addCleanup(shutil.rmtree, self.state, True)
        script_files.attach(self.state)
        self.addCleanup(script_files.detach)
        self.script = script_files.ScriptFile("/work/build.py", "a" * 64, "result = 1")

    def test_an_approval_is_saved_owner_only_before_it_applies_and_read_again(self):
        self.assertFalse(script_files.allowed("claude-code", PROJECT, "/work/build.py", "a" * 64))
        script_files.allow("claude-code", PROJECT, self.script)
        path = Path(self.state) / script_files.FILE
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        saved = json.loads(path.read_text())
        self.assertEqual(saved["version"], 1)
        self.assertEqual([(e["app"], e["project"], e["path"], e["sha256"]) for e in saved["files"]],
                         [("claude-code", PROJECT, "/work/build.py", "a" * 64)])
        script_files.detach()
        script_files.attach(self.state)
        self.assertTrue(script_files.allowed("claude-code", PROJECT, "/work/build.py", "a" * 64))
        for other in (("codex", PROJECT, "/work/build.py", "a" * 64), ("claude-code", "/other.blend", "/work/build.py",
                      "a" * 64), ("claude-code", PROJECT, "/work/other.py", "a" * 64),
                      ("claude-code", PROJECT, "/work/build.py", "b" * 64), ("claude-code", "", "/work/build.py", "a" * 64)):
            self.assertFalse(script_files.allowed(*other), other)

    def test_a_changed_file_replaces_its_approval_and_forget_removes_by_app_or_file(self):
        script_files.allow("claude-code", PROJECT, self.script)
        changed = script_files.ScriptFile("/work/build.py", "b" * 64, "result = 2")
        self.assertEqual(script_files.previous("claude-code", PROJECT, "/work/build.py")["sha256"], "a" * 64)
        script_files.allow("claude-code", PROJECT, changed)
        self.assertEqual([e["sha256"] for e in script_files.entries("claude-code")], ["b" * 64])
        script_files.allow("codex", PROJECT, self.script)
        self.assertEqual(script_files.forget(app="claude-code"), 1)
        self.assertEqual([e["app"] for e in script_files.entries()], ["codex"])
        self.assertEqual(script_files.forget(app="codex", path="/work/build.py"), 1)
        self.assertEqual(json.loads((Path(self.state) / script_files.FILE).read_text())["files"], [])

    def test_a_save_that_fails_changes_nothing_and_a_removal_still_ends_for_the_session(self):
        script_files.allow("claude-code", PROJECT, self.script)
        with patch.object(script_files.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                script_files.allow("codex", PROJECT, self.script)
            self.assertEqual([e["app"] for e in script_files.entries()], ["claude-code"])
            with self.assertRaises(OSError):
                script_files.forget(app="claude-code")
        self.assertEqual(script_files.entries(), [])

    def test_a_fifo_or_link_at_the_approvals_file_never_blocks_and_holds_none(self):
        """Break-it review L3: a FIFO there hung every check (the window's main thread)."""
        path = Path(self.state) / script_files.FILE
        os.mkfifo(path)
        self.assertEqual(script_files.entries(), [])
        path.unlink()
        target = Path(self.state) / "elsewhere.json"
        target.write_text(json.dumps({"version": 1, "files": [{"app": "a", "project": "p", "path": "/x",
                                                                "sha256": "a" * 64}]}))
        path.symlink_to(target)
        self.assertEqual(script_files.entries(), [], "a link at the name is not read")

    def test_an_app_and_a_saved_project_are_required_and_a_damaged_file_holds_none(self):
        for app, project in (("", PROJECT), ("claude-code", "")):
            with self.assertRaises(ValueError):
                script_files.allow(app, project, self.script)
        (Path(self.state) / script_files.FILE).write_text('{"version": 1, "files": [{"app": "x"}, 3]}')
        script_files.attach(self.state)
        self.assertEqual(script_files.entries(), [])
        (Path(self.state) / script_files.FILE).write_text("not json")
        script_files.attach(self.state)
        self.assertEqual(script_files.entries(), [])


class SharedStoreTests(unittest.TestCase):
    """Break-it review F1/F2 (2026-10-23): the file is the source of truth across Angelo instances."""

    def setUp(self):
        self.state = tempfile.mkdtemp(prefix="angelo-script-shared-")
        self.addCleanup(shutil.rmtree, self.state, True)
        script_files.attach(self.state)
        self.addCleanup(script_files.detach)
        self.first = script_files.ScriptFile("/work/build.py", "a" * 64, "result = 1")
        self.second = script_files.ScriptFile("/work/other.py", "b" * 64, "result = 2")

    def another_instance(self, code):
        """Run ``code`` against the same state folder in a separate interpreter (another Angelo instance)."""
        import subprocess
        program = ("import sys; sys.path.insert(0, %r); from angelo_agent import script_files as s; "
                   "s.attach(%r); %s" % (str(ADDON.parent), self.state, code))
        subprocess.run([sys.executable, "-I", "-c", program], check=True, timeout=60)

    def test_a_removal_in_another_instance_applies_here_and_is_never_written_back(self):
        script_files.allow("claude-code", PROJECT, self.first)
        self.assertTrue(script_files.allowed("claude-code", PROJECT, self.first.path, self.first.sha256))
        self.another_instance("s.forget(app='claude-code')")
        self.assertFalse(script_files.allowed("claude-code", PROJECT, self.first.path, self.first.sha256),
                         "a removal made elsewhere applies at the next check")
        script_files.allow("claude-code", PROJECT, self.second)   # This instance's next save keeps the removal.
        saved = json.loads((Path(self.state) / script_files.FILE).read_text())["files"]
        self.assertEqual([entry["path"] for entry in saved], [self.second.path])

    def test_an_approval_made_elsewhere_is_kept_by_this_instances_save(self):
        self.another_instance("s.allow('codex', %r, s.ScriptFile('/x/a.py', 'c' * 64, 'r = 1'))" % PROJECT)
        script_files.allow("claude-code", PROJECT, self.first)
        self.assertEqual(sorted(e["app"] for e in script_files.entries()), ["claude-code", "codex"])

    def test_every_write_uses_its_own_temporary_file_under_a_lock(self):
        names = []
        real_open = script_files.os.open

        def record(path, flags, *mode):
            if str(path).endswith(".tmp"):
                names.append((os.path.basename(path), bool(flags & os.O_EXCL)))
            return real_open(path, flags, *mode)
        with patch.object(script_files.os, "open", record):
            script_files.allow("claude-code", PROJECT, self.first)
            script_files.allow("claude-code", PROJECT, self.second)
        self.assertEqual(len({name for name, _ in names}), 2, "a fresh name per write")
        self.assertTrue(all(exclusive for _, exclusive in names))
        self.assertTrue((Path(self.state) / (script_files.FILE + ".lock")).exists())
        self.assertEqual([p.name for p in Path(self.state).glob("*.tmp")], [])

    def test_disconnect_and_losing_the_python_level_forget_the_apps_files(self):
        registry = AppRegistry(self.state)
        name = registry.connect("claude_code")
        script_files.allow(name, PROJECT, self.first)
        script_files.allow("codex", PROJECT, self.second)
        registry.set_levels(name, (READ,))
        self.assertEqual([e["app"] for e in script_files.entries()], ["codex"])
        script_files.allow(name, PROJECT, self.first)
        registry.disconnect(name)
        self.assertEqual([e["app"] for e in script_files.entries()], ["codex"])

    def test_a_new_connection_starts_with_none_even_after_a_removal_that_failed(self):
        """Fresh break-it review FR-L1: a Disconnect whose removal could not be saved left the app's approvals on
        disk, and reconnecting brought them back; the Forget button said nothing."""
        registry = AppRegistry(self.state)
        name = registry.connect("claude_code")
        script_files.allow(name, PROJECT, self.first)
        with patch.object(script_files, "forget_at", side_effect=OSError("disk full")):
            registry.disconnect(name)
        self.assertEqual(len(script_files.entries()), 1, "the failed removal left it on disk")
        registry.connect("claude_code")
        self.assertEqual(script_files.entries(), [], "a new connection starts with none")
        registry.connect("claude_code")   # Connecting again while connected keeps what the person allowed since.
        script_files.allow(name, PROJECT, self.first)
        registry.connect("claude_code")
        self.assertEqual(len(script_files.entries()), 1)
        page = (ADDON / "external_ui.py").read_text(encoding="utf-8")
        self.assertIn("if _forget_script_files(DEFAULT_CLIENT[self.app]) is None:", page,
                      "the Forget button reports a removal it could not save")

    def test_the_apps_record_is_read_without_waiting_on_a_fifo(self):
        """Fresh break-it review FR-L2: AppRegistry.load runs on Angelo's main thread at every draw."""
        registry = AppRegistry(self.state)
        registry.root.mkdir(parents=True, exist_ok=True)
        os.mkfifo(registry.path)
        self.assertEqual(registry.load(), {}, "unreadable: no app is connected")


class CatalogTests(unittest.TestCase):
    def test_offered_with_the_python_level_only_and_never_an_angelo_agent_tool(self):
        names = {tool["name"] for tool in app_tools((READ, PYTHON))["tools"]}
        self.assertIn(SCRIPT_FILE_TOOL, names)
        self.assertNotIn(SCRIPT_FILE_TOOL, {tool["name"] for tool in app_tools((READ,))["tools"]})
        from angelo_agent.tool_specs import TOOL_MAP
        self.assertNotIn(SCRIPT_FILE_TOOL, TOOL_MAP, "Angelo's own agents never see it")
        tool = next(t for t in app_tools((PYTHON,))["tools"] if t["name"] == SCRIPT_FILE_TOOL)
        self.assertEqual(tool["inputSchema"]["required"], ["file"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])


class RecordingExecutor(Executor):
    def __init__(self, state):
        super().__init__(state)
        self.codes = []
        self.files = []      # The script file each run was given (Controller sets executor.script_file), or None.
        self.on_run = None   # What a run also does (a test's stand-in for a script's side effects).

    def execute(self, name, arguments):
        if name == "angelo_execute_python":
            self.codes.append(arguments["code"])
            script = getattr(self, "script_file", None)
            self.files.append(script.path if script is not None else None)
            if self.on_run is not None:
                self.on_run()
            return {"success": True, "result": 7}
        return super().execute(name, arguments)


@unittest.skipIf(sys.platform == "win32", "Needs the Unix-socket listener (Windows named pipe: a later phase)")
class ScriptFileAppTests(unittest.TestCase):
    """End to end through the app's MCP server and Angelo's External conversation (test_native_external_mcp)."""
    tick, server, rpc, call, conversation = mcp_harness.AppTests.tick, mcp_harness.AppTests.server, mcp_harness.AppTests.rpc, mcp_harness.AppTests.call, mcp_harness.AppTests.conversation

    def setUp(self):
        mcp_harness.AppTests.setUp(self)
        self.executor = RecordingExecutor(self.state)
        script_files.attach(self.state)
        self.addCleanup(script_files.detach)
        self.project = PROJECT
        patcher = patch.object(Controller, "script_project", lambda controller: self.project)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.file = self.root / "work" / "build.py"
        self.file.parent.mkdir()
        self.file.write_text(BUILD)
        self.asked = []
        self.allow = False
        self.consents = []   # The consent the host recorded on each script's result (TrustedScripts.script_result).
        original = Controller.script_result

        def recorded(controller, call, result):
            result = original(controller, call, result)
            if call.name == "angelo_execute_python" and isinstance(result, dict) and "consent" in result:
                self.consents.append(result["consent"])
            return result
        consent_patch = patch.object(Controller, "script_result", recorded)
        consent_patch.start()
        self.addCleanup(consent_patch.stop)

        def person(controller, call):
            """The person: opens the exact text; Always allow when the test says so, else approves this run."""
            self.asked.append(call.arguments["code"])
            controller.review_script(call)
            if self.allow:
                self.assertTrue(controller.offers_script_file_allow(call))
                self.assertTrue(controller.allow_script_file(call))
            else:
                controller.execute_tool(call, self.executor, controller.document)
        self.approve = person

    def run_file(self, **extra):
        return self.call(SCRIPT_FILE_TOOL, {"file": str(self.file), **extra})

    def test_the_first_run_asks_and_runs_exactly_the_files_text(self):
        result = self.run_file()
        self.assertFalse(result["isError"], result)
        self.assertEqual((self.asked, self.executor.codes), ([BUILD], [BUILD]))
        self.assertEqual(self.consents, ["exact_code_review"])

    def test_a_script_file_runs_as_that_file_and_inline_code_does_not(self):
        """Break-it review FR-M1: a file's main guard and ``__file__`` work as in Blender (blender_tools sets
        ``__name__`` "__main__" and ``__file__`` from executor.script_file); inline code is no file."""
        self.assertFalse(self.run_file()["isError"])
        self.assertEqual(self.executor.files, [os.path.realpath(self.file)])
        from angelo_agent import script_prepare
        bpy = SimpleNamespace(context="context")
        names = script_prepare.namespace(bpy, "scene", "kit", script_files.ScriptFile("/w/build.py", "a" * 64, "x"))
        self.assertEqual((names["__name__"], names["__file__"]), ("__main__", "/w/build.py"))
        inline = script_prepare.namespace(bpy, "scene", "kit")
        self.assertNotEqual(inline["__name__"], "__main__")
        self.assertNotIn("__file__", inline)
        source = (ADDON / "blender_tools.py").read_text(encoding="utf-8")
        self.assertIn('script_prepare.namespace(bpy, scene, kit, getattr(self, "script_file", None))', source)

    def test_always_allow_runs_the_unchanged_file_unasked_and_any_change_asks_again(self):
        self.allow = True
        first = self.run_file()
        self.assertFalse(first["isError"], first)
        self.assertEqual(len(self.asked), 1)
        self.assertEqual([e["path"] for e in script_files.entries()], [os.path.realpath(self.file)])
        self.allow = False
        second = self.run_file()
        self.assertFalse(second["isError"], second)
        self.assertEqual(len(self.asked), 1, "the unchanged file ran without the person")
        self.assertEqual(self.consents, ["script_file", "script_file"], "the button covers this run too")
        self.assertEqual(self.executor.codes, [BUILD, BUILD])
        self.file.write_text(BUILD + "result = 8\n")
        changed = {}

        def person(controller, call):
            changed["note"] = controller.script_file_changed(call)
            self.asked.append(call.arguments["code"])
            controller.review_script(call)
            controller.execute_tool(call, self.executor, controller.document)
        self.approve = person
        self.assertFalse(self.run_file()["isError"])
        self.assertEqual(len(self.asked), 2, "a changed file asks again")
        self.assertIsNotNone(changed["note"])

    def asks_again(self, why):
        self.allow = True
        self.assertFalse(self.run_file()["isError"])
        self.allow = False
        before = len(self.asked)
        why()
        self.assertFalse(self.run_file()["isError"])
        return len(self.asked) > before

    def test_another_project_an_unsaved_file_ask_and_policy_each_ask_again(self):
        def another():
            self.project = os.path.realpath("/tmp/angelo-project/other.blend")
        self.assertTrue(self.asks_again(another), "another project")
        script_files.forget()
        self.project = PROJECT

        def unsaved():
            self.project = ""
        self.assertTrue(self.asks_again(unsaved), "an unsaved file")
        script_files.forget()
        self.project = PROJECT

        def ask():
            self.conversation().set_options(approval="ASK")
        self.assertTrue(self.asks_again(ask), "Ask asks")
        self.conversation().set_options(approval="AUTO_BOUNDED")
        script_files.forget()

        def policy():
            managed_policy.reset(with_organization(Policy(), org_policy({"allowAutoApproval": False})))
        self.assertTrue(self.asks_again(policy), "AllowAutoApproval off")

    def test_a_forgotten_approval_and_another_app_ask_again(self):
        self.assertTrue(self.asks_again(lambda: script_files.forget(app=self.name)), "forgotten")
        script_files.forget()

        def another_app():
            entry = script_files.entries()[0]
            script_files.forget()
            script_files.allow("codex", entry["project"], script_files.read_script(str(self.file)))
        self.assertTrue(self.asks_again(another_app), "another app's approval")

    def test_the_same_code_sent_as_execute_python_still_asks(self):
        self.allow = True
        self.assertFalse(self.run_file()["isError"])
        self.allow = False
        before = len(self.asked)
        self.assertFalse(self.call("angelo_execute_python", {"description": "same text", "code": BUILD})["isError"])
        self.assertEqual(len(self.asked), before + 1)

    def test_a_script_naming_preferences_or_angelo_is_never_offered(self):
        for text in ("import angelo_agent\nresult = 1\n", "bpy.context.preferences.view.ui_scale = 2\nresult = 1\n"):
            self.file.write_text(text)
            offered = []

            def person(controller, call):
                controller.review_script(call)
                offered.append(controller.offers_script_file_allow(call))
                controller.execute_tool(call, self.executor, controller.document)
            self.approve = person
            self.assertFalse(self.run_file()["isError"])
            self.assertEqual(offered, [False], text)

    def test_what_runs_is_what_angelo_read_even_if_the_file_changes_before_dispatch(self):
        def person(controller, call):
            self.file.write_text("result = 'swapped'\n")
            controller.review_script(call)
            controller.execute_tool(call, self.executor, controller.document)
        self.approve = person
        self.assertFalse(self.run_file()["isError"])
        self.assertEqual(self.executor.codes, [BUILD])

    def test_bad_paths_say_why_and_nothing_runs(self):
        for arguments in ({"file": "relative/build.py"}, {"file": str(self.root / "missing.py")},
                          {"file": str(self.file), "extra": 1}, {"file": str(self.file), "description": " "}):
            result = self.call(SCRIPT_FILE_TOOL, arguments)
            self.assertTrue(result["isError"], arguments)
            self.assertIn("nothing ran", json.dumps(result), arguments)
        self.assertEqual(self.executor.codes, [])

    def test_an_app_that_lost_the_python_level_never_runs_an_allowed_file(self):
        """Break-it F3: an approval applies only while the app still has the python level (read at every run)."""
        self.allow = True
        self.assertFalse(self.run_file()["isError"])
        self.allow = False
        controller = self.conversation()
        call = SimpleNamespace(name="angelo_execute_python", script_file=script_files.read_script(str(self.file)),
                               arguments={"code": BUILD}, approval=True, executing=False, recovery_review=False, id="x")
        self.assertTrue(controller._script_file_applies(call))
        with patch.object(AppRegistry, "load", lambda registry: {}):
            self.assertFalse(controller._script_file_applies(call), "a disconnected app")

    def test_only_an_app_with_the_python_level_may_call_it(self):
        AppRegistry(self.state).set_levels(self.name, (READ,))
        result = self.run_file()
        self.assertTrue(result["isError"])
        self.assertIn("not allowed this app to run Python scripts", json.dumps(result))
        self.assertEqual((self.asked, self.executor.codes), ([], []))


class CardAndPreferencesTests(unittest.TestCase):
    def controller(self, offers=False, changed=None, always=False):
        return SimpleNamespace(offers_always_allow=lambda call: always, offers_script_file_allow=lambda call: offers,
                               script_file_changed=lambda call: changed)

    def test_the_card_shows_the_note_the_change_and_the_files_own_button(self):
        from angelo_agent import chat_cards
        call = SimpleNamespace(script_file=script_files.ScriptFile("/work/build.py", "a" * 64, "x"))
        wrap = lambda text, width, size, font: [(text,)]
        self.assertEqual(chat_cards.always_allow_rows(self.controller(), call, wrap, 300, 12, None), ())
        self.assertIsNone(chat_cards.always_allow_button(self.controller(), call))
        rows = chat_cards.always_allow_rows(self.controller(offers=True, changed={"allowed_at": "2026-10-23T01:02:03Z"}),
                                            call, wrap, 300, 12, None)
        self.assertIn("/work/build.py changed since you allowed it on 2026-10-23", rows[0][0], "the full path (F4)")
        self.assertIn("/work/build.py", rows[1][0])
        self.assertIn("Files it reads or imports aren't covered", rows[1][0], "what the hash does not cover (F5)")
        self.assertEqual(rows[2][0], chat_cards.HEADLESS_PREFERENCES, "headless runs use the person's preferences "
                         "(FR-M2): their add-ons run too")
        self.assertEqual(len(rows), 3, "the card's rows before FR-M2, plus exactly that one line")
        only_allow = chat_cards.always_allow_rows(self.controller(offers=True), call, wrap, 300, 12, None)
        self.assertEqual([row[0] for row in only_allow][1:], [chat_cards.HEADLESS_PREFERENCES])
        auto = chat_cards.always_allow_rows(self.controller(always=True), call, wrap, 300, 12, None)
        self.assertEqual(len(auto), 1, "Always allow scripts in an Auto chat is unchanged")
        self.assertNotIn(chat_cards.HEADLESS_PREFERENCES, auto[0][0])
        self.assertEqual(chat_cards.always_allow_button(self.controller(offers=True), call)[0], "script_file_allow")
        self.assertEqual(chat_cards.always_allow_button(self.controller(always=True), call)[0], "script_always_allow")

    def test_the_card_handler_refuses_while_an_agent_script_runs_and_disconnect_forgets(self):
        tree = ast.parse((ADDON / "trusted_scripts_ui.py").read_text())
        handler = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "script_file_allow_from_card")
        self.assertIn("agent_script_running()", ast.unparse(handler))
        disconnect = (ADDON / "external_ui.py").read_text()
        self.assertIn("AppRegistry(_state()).disconnect(DEFAULT_CLIENT[self.app])   # Forgets its script files too.",
                      disconnect)   # AppRegistry.disconnect forgets them (SharedStoreTests).
        page = (ADDON / "providers_page_external.py").read_text()
        self.assertIn('"mcp_forget_scripts": "external_app_forget_scripts"', page)


if __name__ == "__main__":
    unittest.main()
