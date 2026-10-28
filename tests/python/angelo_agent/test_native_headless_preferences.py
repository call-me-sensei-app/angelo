# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The headless child loads the person's preferences read-only, or factory settings and says why.

Without Blender: where the launcher finds the person's ``userpref.blend``, the private copy it stages, the command
and environment with and without it, the factory restart when the child never says ready or cannot read the copy,
the result's words, and Angelo's own add-on staying unregistered in the child. The real child is
``test_blender_headless_preferences.py``. Contract: doc/angelo/external_mcp.rst, "Preferences".
"""

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
import angelo_agent  # noqa: E402
from angelo_agent import headless_preferences as preferences  # noqa: E402
from angelo_agent import headless_main  # noqa: E402
from angelo_agent import headless_files  # noqa: E402
from angelo_agent import headless_working_copy as working_copy  # noqa: E402
from angelo_agent.headless_session import HeadlessSession, child_command, child_environment  # noqa: E402


class FolderTests(unittest.TestCase):
    def test_the_window_default_folder_from_the_account_never_an_apps_variable(self):
        environ = {"HOME": "/h", "CFFIXED_USER_HOME": "/fixed", "BLENDER_USER_CONFIG": "/evil",
                   "BLENDER_USER_RESOURCES": "/evil", "XDG_CONFIG_HOME": "/evil"}
        self.assertEqual(preferences.person_config_directory(environ, "darwin"),
                         Path("/fixed/Library/Application Support/Angelo/5.2/config"))
        self.assertEqual(preferences.person_config_directory(environ, "linux"), Path("/h/.config/angelo/5.2/config"))
        import pwd
        account = pwd.getpwuid(os.getuid()).pw_dir
        self.assertEqual(preferences.person_config_directory({"HOME": "/elsewhere"}, "darwin"),
                         Path(account) / "Library/Application Support/Angelo/5.2/config",
                         "macOS: Blender's lookup ignores HOME, so the launcher does too")


class StageTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hpref-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.config, self.private = self.root / "config", self.root / "private"
        self.config.mkdir()
        self.private.mkdir(mode=0o700)

    def test_a_copy_owner_only_and_the_persons_file_untouched(self):
        source = self.config / preferences.USERPREF
        source.write_bytes(b"BLENDER17-01v0502 prefs")
        os.utime(source, (1_700_000_000, 1_700_000_000))
        before = os.stat(source)
        self.assertEqual(preferences.stage(self.config, self.private), preferences.YOURS)
        copy = self.private / preferences.CONFIG / preferences.USERPREF
        self.assertEqual(copy.read_bytes(), source.read_bytes())
        self.assertEqual(stat.S_IMODE(copy.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.private / preferences.CONFIG).stat().st_mode), 0o700)
        after = os.stat(source)
        self.assertEqual((before.st_mtime_ns, before.st_size, before.st_ino), (after.st_mtime_ns, after.st_size,
                                                                               after.st_ino))

    def test_a_link_to_the_file_is_followed_as_blender_follows_it(self):
        real = self.root / "dotfiles.blend"
        real.write_bytes(b"BLENDER prefs")
        (self.config / preferences.USERPREF).symlink_to(real)
        self.assertEqual(preferences.stage(self.config, self.private), preferences.YOURS)

    def test_missing_odd_or_huge_files_mean_factory_settings(self):
        self.assertEqual(preferences.stage(self.config, self.private), "missing")
        (self.config / preferences.USERPREF).mkdir()
        self.assertEqual(preferences.stage(self.config, self.private), "unreadable")
        (self.config / preferences.USERPREF).rmdir()
        os.mkfifo(self.config / preferences.USERPREF)   # Never blocks on a pipe planted there.
        self.assertEqual(preferences.stage(self.config, self.private), "unreadable")
        os.unlink(self.config / preferences.USERPREF)
        (self.config / preferences.USERPREF).write_bytes(b"x" * 64)
        with patch.object(preferences, "MAX_BYTES", 16):
            self.assertEqual(preferences.stage(self.config, self.private), "unreadable")
        self.assertFalse((self.private / preferences.CONFIG / preferences.USERPREF).exists())


class WordsTests(unittest.TestCase):
    def test_which_preferences_in_words(self):
        self.assertEqual(preferences.describe(preferences.YOURS, 7)["note"],
                         "Preferences: yours (7 add-ons on, Blender's defaults included)")
        record = preferences.describe(preferences.YOURS, 6, ["broken_addon"])
        self.assertEqual(record["note"],
                         "Preferences: yours (6 add-ons on, Blender's defaults included; did not load: broken_addon)")
        self.assertEqual(record["addons_failed"], ["broken_addon"])
        for code, why in (("missing", "no saved preferences were found"),
                          ("not_blend", "yours could not be read: Blender could not read the file"),
                          ("start_failed", "Angelo did not start with yours"), ("other", "yours could not be read")):
            self.assertEqual(preferences.describe(code), {"used": "factory",
                                                          "note": "Preferences: factory settings (%s)" % why})


class CommandTests(unittest.TestCase):
    def test_yours_drops_factory_startup_only(self):
        mine = child_command("/A", "/s.py", "/f.blend", "c", "codex", ("read",), preferences_code=preferences.YOURS)
        factory = child_command("/A", "/s.py", "/f.blend", "c", "codex", ("read",), preferences_code="missing")
        self.assertNotIn("--factory-startup", mine)
        self.assertIn("--factory-startup", factory)
        for command in (mine, factory):
            for flag in ("--background", "--disable-autoexec", "--offline-mode"):
                self.assertIn(flag, command)
            self.assertLess(command.index("--background"), command.index("--python"))
        self.assertEqual(mine[mine.index("--preferences") + 1], "yours")

    def test_the_config_folder_is_the_private_copy_never_the_apps(self):
        environ = {"PATH": "/bin", "HOME": "/h", "BLENDER_USER_CONFIG": "/evil", "BLENDER_USER_SCRIPTS": "/evil",
                   "BLENDER_USER_EXTENSIONS": "/evil", "BLENDER_USER_RESOURCES": "/evil"}
        private = Path("/tmp/p")
        mine = child_environment(environ, private, preferences_used=True)
        self.assertEqual(mine["BLENDER_USER_CONFIG"], "/tmp/p/config")
        self.assertNotIn("BLENDER_USER_CONFIG", child_environment(environ, private))
        for environment in (mine, child_environment(environ, private)):
            for name in ("BLENDER_USER_SCRIPTS", "BLENDER_USER_EXTENSIONS", "BLENDER_USER_RESOURCES"):
                self.assertNotIn(name, environment)
            self.assertEqual(environment["ANGELO_HEADLESS"], "1")


class FakeChild:
    def __init__(self, ready, hangs=False):
        self.ready, self.closed, self.hangs = ready, False, hangs

    def wait_ready(self, timeout=None):
        return self.ready

    def close(self):
        self.closed = True

    def alive(self):
        return not self.closed and (self.ready is not None or self.hangs)

    def last_output(self):
        return []


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hpref-s-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.config = self.root / "config"
        self.config.mkdir()
        (self.config / preferences.USERPREF).write_bytes(b"BLENDER prefs")
        self.source = self.root / "scene.blend"
        self.source.write_bytes(b"BLENDER-v502 scene")
        self.started, self.copies = [], []

    def leftovers(self):
        return sorted(path.name for path in self.root.iterdir() if path.name.startswith("scene.angelo-"))

    def session(self, *readies):
        readies = list(readies)

        def child(command, environ, stderr=None):
            code = command[command.index("--preferences") + 1]
            copy = Path(command[command.index("--private") + 1]) / preferences.CONFIG / preferences.USERPREF
            self.started.append((code, "--factory-startup" in command, environ.get("BLENDER_USER_CONFIG"),
                                 copy.exists()))
            working = Path(command[command.index("--working") + 1])
            self.copies.append(working)
            assert working.read_bytes() == self.source.read_bytes(), "the launcher's copy of the source"
            return FakeChild(readies.pop(0))
        return HeadlessSession("claude-code", "claude_code", str(self.root / "state"), binary=Path(sys.executable),
                               script="/s/x.py", child_type=child, config_directory=self.config)

    def test_a_child_that_never_says_ready_starts_again_with_factory_settings(self):
        record = preferences.describe("start_failed")
        session = self.session(None, {"ok": True, "file": str(self.source), "preferences": record})
        session.open(str(self.source), ("read",))
        self.assertEqual([(code, factory) for code, factory, _config, _copy in self.started],
                         [("yours", False), ("start_failed", True)])
        self.assertTrue(self.started[0][2] and self.started[0][3], "the first child reads the private copy")
        self.assertEqual(self.started[1][2:], (None, False), "the factory child has no copy and no config folder")
        self.assertEqual(session.status()["preferences"],
                         "Preferences: factory settings (Angelo did not start with yours)")

    def test_a_copy_blender_cannot_read_starts_again_with_factory_settings(self):
        session = self.session({"ok": False, "code": preferences.NOT_READABLE, "error": "x"},
                               {"ok": True, "file": str(self.source), "preferences": preferences.describe("not_blend")})
        session.open(str(self.source), ("read",))
        self.assertEqual([code for code, *_rest in self.started], ["yours", "not_blend"])

    def test_yours_starts_once_and_a_file_problem_never_retries(self):
        session = self.session({"ok": True, "file": str(self.source), "preferences": preferences.describe("yours", 3)})
        session.open(str(self.source), ("read",))
        self.assertEqual(len(self.started), 1)
        self.assertEqual(session.status()["preferences"],
                         "Preferences: yours (3 add-ons on, Blender's defaults included)")
        self.assertIsNone(session.status()["working_on_source"])
        session.close()
        self.started.clear()
        session = self.session({"ok": False, "code": "invalid", "error": "the file is not a .blend"})
        with self.assertRaises(Exception):
            session.open(str(self.source), ("read",))
        self.assertEqual(len(self.started), 1, "only a preferences failure starts again")

    def test_a_failed_start_is_remembered_until_the_preferences_change(self):
        """Break-it F4: a hanging add-on cost a full start every time; later starts go straight to factory settings
        while that preferences file is unchanged, and every failed start's copy is removed."""
        session = self.session(None, {"ok": True, "file": str(self.source), "preferences": preferences.describe("x")},
                               {"ok": True, "file": str(self.source), "preferences": preferences.describe("x")},
                               {"ok": True, "file": str(self.source), "preferences": preferences.describe("x")})
        session.open(str(self.source), ("read",))
        session.close()
        session.open(str(self.source), ("read",))
        self.assertEqual([(code, factory) for code, factory, *_rest in self.started],
                         [("yours", False), ("start_failed", True), ("start_failed", True)])
        session.close()
        self.assertEqual(self.leftovers(), [], "every copy is gone: the failed starts' and the closed sessions'")
        (self.config / preferences.USERPREF).write_bytes(b"BLENDER prefs, changed by the person")
        session.open(str(self.source), ("read",))
        self.assertEqual(self.started[-1][0], "yours", "changed preferences are tried again")

    def test_a_hang_is_bounded(self):
        child = FakeChild(None, hangs=True)
        with patch.object(preferences, "STARTUP_BUDGET", 0.2), patch.object(preferences, "READY_BUDGET", 0.4):
            started = __import__("time").monotonic()
            self.assertIsNone(preferences.wait_ready(child, self.root, preferences.YOURS))
            self.assertLess(__import__("time").monotonic() - started, 2)

    def test_an_error_that_is_not_the_files_starts_again_with_factory_settings(self):
        session = self.session({"ok": False, "code": "unavailable", "error": "an add-on broke the start"},
                               {"ok": True, "file": str(self.source), "preferences": preferences.describe("x")})
        session.open(str(self.source), ("read",))
        self.assertEqual([code for code, *_rest in self.started], ["yours", "start_failed"])
        self.assertEqual(len(self.leftovers()), 1, "only the running child's copy")

    def test_a_crashed_childs_unedited_copy_is_removed_by_the_launcher(self):
        session = self.session({"ok": True, "file": str(self.source), "preferences": preferences.describe("yours", 1)})
        session.open(str(self.source), ("read",))
        made = self.copies[-1]
        self.assertTrue(made.exists())
        session.close()
        self.assertFalse(made.exists(), "the copy the launcher made, unedited, goes")
        self.assertTrue(self.source.exists())

    def test_the_launcher_removes_only_the_copy_it_made_never_one_the_child_names(self):
        """Break-it L7: the person's code runs in the child, so a path the child reports is never trusted for a
        removal; only its identity of the launcher's own copy (after a save) is."""
        other = self.root / "scene.angelo-20261023-000000.blend"
        other.write_bytes(b"BLENDER the person's own output")
        session = self.session({"ok": True, "file": str(self.source), "preferences": preferences.describe("yours", 1),
                                "working": str(other), "working_identity": list(headless_files.identity(other))})
        session.open(str(self.source), ("read",))
        made = self.copies[-1]
        self.assertEqual(Path(session.working[0]), made)
        session.close()
        self.assertTrue(other.exists(), "a file the child named is never removed")
        self.assertEqual(other.read_bytes(), b"BLENDER the person's own output")
        self.assertFalse(made.exists())

    def test_no_saved_preferences_start_with_factory_settings_at_once(self):
        os.unlink(self.config / preferences.USERPREF)
        session = self.session({"ok": True, "file": str(self.source), "preferences": preferences.describe("missing")})
        session.open(str(self.source), ("read",))
        self.assertEqual([(code, factory) for code, factory, *_rest in self.started], [("missing", True)])


class WorkingCopyTests(unittest.TestCase):
    """Break-it review (2026-10-23): a person's add-on can save the open file at any moment (load_post, save_post,
    depsgraph_update_post, a timer); Blender never has the source open, only a copy next to it."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="hpref-w-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.source = self.root / "scene.blend"
        self.source.write_bytes(b"BLENDER-v502 the person's scene")
        os.utime(self.source, (1_700_000_000, 1_700_000_000))
        self.opened = []
        self.bpy = SimpleNamespace(ops=SimpleNamespace(wm=SimpleNamespace(open_mainfile=self.open)))

    def open(self, filepath, load_ui, use_scripts):
        assert load_ui is False and use_scripts is False
        self.opened.append(Path(filepath))
        return {"FINISHED"}

    def test_blender_opens_a_copy_next_to_the_source_never_the_source(self):
        before = headless_files.identity(self.source)
        working = working_copy.open_copy(self.bpy, self.source)
        self.assertEqual(self.opened, [working])
        self.assertNotEqual(working, self.source)
        self.assertEqual(working.parent, self.source.parent, "the same folder: // paths stay valid")
        self.assertTrue(working.name.startswith("scene" + headless_files.OUTPUT_TAG))
        self.assertEqual(working.read_bytes(), self.source.read_bytes())
        self.assertEqual(headless_files.identity(self.source), before, "the source is only read")

    def test_a_read_only_folder_opens_the_source_itself(self):
        os.chmod(self.root, 0o555)
        self.addCleanup(os.chmod, self.root, 0o755)
        if os.access(self.root, os.W_OK):
            self.skipTest("running as a user who can write anywhere")
        self.assertEqual(working_copy.open_copy(self.bpy, self.source), self.source)

    def test_a_copy_blender_cannot_open_is_removed(self):
        self.bpy.ops.wm.open_mainfile = lambda **_: {"CANCELLED"}
        with self.assertRaises(RuntimeError):
            working_copy.open_copy(self.bpy, self.source)
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["scene.blend"])

    def test_an_unedited_copy_is_removed_only_while_it_is_the_sessions(self):
        working = working_copy.open_copy(self.bpy, self.source)
        identity = headless_files.identity(working)
        Path(str(working) + "1").write_bytes(b"backup an add-on's save left")
        self.assertFalse(working_copy.discard(self.source, self.source, headless_files.identity(self.source)))
        working.write_bytes(b"someone else changed it")
        self.assertFalse(working_copy.discard(working, self.source, identity), "changed: kept")
        self.assertTrue(working_copy.discard(working, self.source, headless_files.identity(working)))
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["scene.blend"])
        self.assertEqual(self.source.read_bytes(), b"BLENDER-v502 the person's scene")

    def test_the_childs_source_baseline_is_the_launchers(self):
        command = child_command("/A", "/s.py", "/f.blend", "c", "codex", ("read",), source_identity=(1, 2, 3, 4))
        argv = command[command.index("--") + 1:]
        argv = [{"{in}": "3", "{out}": "4"}.get(part, part) for part in argv]
        self.assertEqual(headless_main.parse(argv).source_identity, (1, 2, 3, 4))
        self.assertIsNone(headless_main._identity("not json"))
        self.assertIsNone(headless_main._identity('["a"]'))


class LauncherPolicyTests(unittest.TestCase):
    """Break-it F3 (2026-10-23): the launcher, which runs none of the person's code, refuses what policy refuses,
    in the child's own words, before the child sees the call."""

    def setUp(self):
        import io
        from angelo_agent.external_clients import AppRegistry, write_access, write_organization
        from angelo_agent.external_mcp import Server
        self.root = Path(tempfile.mkdtemp(prefix="hpref-p-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        name = AppRegistry(self.root).connect("claude_code")
        write_access(self.root, True)
        write_organization(self.root, None)
        self.audited = []
        test = self

        class Opened:
            opened = True

            def audit(self, tool, arguments, code):
                test.audited.append((tool, code))

            def request(self, *arguments, **options):
                raise AssertionError("the child saw a call policy refuses")
        self.server = Server(name, self.root, stdout=io.StringIO(), headless=Opened())

    def refusal(self, tool, approval=None, **policy):
        from angelo_agent import external_mcp, managed_policy
        current = managed_policy.current()
        changed = SimpleNamespace(**dict(vars(current), **policy))
        with patch.object(managed_policy, "current", lambda: changed), \
                patch.object(external_mcp, "window_approval", lambda state, client: approval):
            return self.server.headless_policy_refusal(tool)

    def test_the_childs_words_for_each_policy(self):
        from angelo_agent.headless_host import SCRIPT_FILE_REFUSALS, WAITS_FOR_PERSON
        script, edit = "angelo_run_script_file", "angelo_create_cube"
        open_policy = {"allow_auto_approval": True, "require_ask_for_edits": False}
        self.assertIsNone(self.refusal(script, **open_policy))
        self.assertIsNone(self.refusal(edit, **open_policy))
        self.assertIsNone(self.refusal("angelo_inspect_scene", "READ_ONLY", allow_auto_approval=False,
                                       require_ask_for_edits=True), "reads are never refused here")
        self.assertEqual(self.refusal(script, allow_auto_approval=False, require_ask_for_edits=False),
                         SCRIPT_FILE_REFUSALS["policy"])
        self.assertIsNone(self.refusal(edit, allow_auto_approval=False, require_ask_for_edits=False))
        self.assertEqual(self.refusal(edit, allow_auto_approval=True, require_ask_for_edits=True), WAITS_FOR_PERSON)
        self.assertEqual(self.refusal(script, allow_auto_approval=True, require_ask_for_edits=True),
                         SCRIPT_FILE_REFUSALS["mode"])
        for approval in ("ASK", "READ_ONLY"):
            self.assertEqual(self.refusal(edit, approval, **open_policy), WAITS_FOR_PERSON)
            self.assertEqual(self.refusal(script, approval, **open_policy), SCRIPT_FILE_REFUSALS["mode"])

    def test_a_machine_policy_change_applies_at_the_next_call_here(self):
        """Break-it final pass (g2-flip-patch): the check reads this computer's policy again itself, so the first call
        after an admin turns AllowAutoApproval off is refused here, not only in the child (where a person's add-on
        can patch the check)."""
        from angelo_agent import managed_policy
        from angelo_agent.headless_host import SCRIPT_FILE_REFUSALS
        from angelo_agent.managed_policy import Policy
        policy = [Policy()]

        def machine():   # What managed_policy.reload does in the launcher (main passes it as Server.machine).
            managed_policy.reset(policy[0])
            return policy[0]
        self.addCleanup(managed_policy.reset)
        self.server.machine = machine
        with patch.object(managed_policy, "_organization_source", None):
            self.assertIsNone(self.server.headless_policy_refusal("angelo_run_script_file"))
            policy[0] = Policy(allow_auto_approval=False)   # The admin's change; nothing else has read it yet.
            self.assertEqual(self.server.headless_policy_refusal("angelo_run_script_file"),
                             SCRIPT_FILE_REFUSALS["policy"])

    def test_an_unreadable_policy_refuses(self):
        from angelo_agent import managed_policy
        from angelo_agent.headless_host import SCRIPT_FILE_REFUSALS

        def broken():
            raise OSError("unreadable")
        with patch.object(managed_policy, "current", broken):
            self.assertEqual(self.server.headless_policy_refusal("angelo_run_script_file"),
                             SCRIPT_FILE_REFUSALS["policy"])

    def test_the_launcher_refuses_before_the_child_sees_the_call(self):
        from angelo_agent import managed_policy
        changed = SimpleNamespace(**dict(vars(managed_policy.current()), allow_auto_approval=False))
        with patch.object(managed_policy, "current", lambda: changed):
            result = self.server.call(1, {"name": "angelo_run_script_file", "arguments": {"file": "/p/build.py"}})
        self.assertTrue(result["isError"])
        self.assertIn("AllowAutoApproval", json.dumps(result))
        self.assertEqual(self.audited, [("angelo_run_script_file", "needs_gui")])


class Recorder:
    """Every module ``register()``/``unregister()`` reaches, recording each call (``name.function``)."""

    def __init__(self, calls, name):
        self._calls, self._name = calls, name

    def __getattr__(self, attribute):
        if attribute.startswith("__"):
            raise AttributeError(attribute)
        return lambda *arguments, **options: self._calls.append(self._name + "." + attribute)


class OwnAddonTests(unittest.TestCase):
    """Break-it HIGH (2026-10-23): a person's preferences list ``angelo_agent``, so Blender enables it at the child's
    startup; the full add-on must not run there. The early return is inert in a window: pinned for every branch."""

    MODULES = ("tls_distrust", "data_migration", "product_env", "state_migration", "console", "studio", "ui",
               "about_card", "app_notices", "font_watch", "job_ui", "prefs_reload", "pycache_retention",
               "settings_import_ui", "studio_assets_host", "asset_shelf_add", "chat_launcher", "chat_toggle",
               "media_shelf", "studio_toggle", "shelf_empty", "web_export_ui", "web_shader_ui", "codex_runtime",
               "session_rail_input", "timer_jobs", "layout_hold")

    def run_add_on(self, background, environ):
        calls = []
        fake = SimpleNamespace(app=SimpleNamespace(background=background),
                               utils=SimpleNamespace(user_resource=lambda kind: "/config"))
        stubs = {name: Recorder(calls, name) for name in self.MODULES}
        stubs["product_env"].deprecated_in_use = lambda: []
        saved = dict(os.environ)
        with patch.dict(sys.modules, {"bpy": fake, "_console_angelo": None, "_console_angelo_genai": None,
                                      **{"angelo_agent." + name: stub for name, stub in stubs.items()}}), \
                patch.dict(os.environ, environ, clear=True):
            missing = object()
            before = {name: getattr(angelo_agent, name, missing) for name in stubs}
            for name, stub in stubs.items():
                setattr(angelo_agent, name, stub)
            try:
                angelo_agent.register()
                registered = list(calls)
                del calls[:]
                angelo_agent.unregister()
            finally:
                for name, value in before.items():
                    if value is missing:
                        delattr(angelo_agent, name)
                    else:
                        setattr(angelo_agent, name, value)
        self.assertEqual(dict(os.environ), saved)
        return registered, list(calls)

    def test_a_window_registers_everything_whatever_the_variable_says(self):
        for environ in ({}, {"ANGELO_HEADLESS": "1"}):
            registered, unregistered = self.run_add_on(False, environ)
            for expected in ("tls_distrust.install", "timer_jobs.register", "layout_hold.register", "ui.register",
                             "app_notices.register",
                             "job_ui.register", "pycache_retention.schedule", "studio_assets_host.register",
                             "shelf_empty.register", "web_shader_ui.register", "web_export_ui.register"):
                self.assertIn(expected, registered, environ)
            for expected in ("shelf_empty.unregister", "web_export_ui.unregister", "ui.unregister",
                             "codex_runtime.close_all", "layout_hold.unregister", "timer_jobs.unregister"):
                self.assertIn(expected, unregistered, environ)

    def test_a_background_process_without_the_variable_registers_normally(self):
        registered, unregistered = self.run_add_on(True, {"ANGELO_HEADLESS": "0"})
        self.assertIn("ui.register", registered)
        self.assertIn("ui.unregister", unregistered)

    def test_the_headless_child_registers_nothing(self):
        self.assertEqual(self.run_add_on(True, {"ANGELO_HEADLESS": "1"}), ([], []))


if __name__ == "__main__":
    unittest.main()
