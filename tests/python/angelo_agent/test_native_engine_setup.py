# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Setting up an installed render engine from the switcher, and its vendor's sign-in (``engine_setup``,
``engine_status``; doc/angelo/render_engines.rst, "Setting up an installed engine"). No Blender.

Covered: an add-on's ``bl_info`` is read as text, never run; the Render add-ons not on are listed from Angelo's own
add-ons and from the folder a vendor's installer filled for Blender (no Blender needed), never Angelo's shipped ones;
setting one up links it in, enables it and returns the engines it added, and leaves nothing behind when it fails or
adds no engine; the switcher lists them after the engines that are on and sets one up only by a module it found; the
status card follows a vendor's licence (starting, sign in, ready with its edition) and calls the add-on's own
operators. Engine and vendor names are stand-ins: the rules hold for any engine add-on.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import machine_clock

sys.path.insert(0, str(Path(__file__).resolve().parent))
# Importing the stand-in modules sets them as attributes of an ``angelo_agent`` package another test file already
# imported; put its attributes back after, or later files' ``from angelo_agent import x`` get the stand-ins.
_PACKAGE = sys.modules.get("angelo_agent")
_ATTRIBUTES = dict(vars(_PACKAGE)) if _PACKAGE is not None else None
from test_native_render_engines import ADDON, BPY, LOADED, MODULES, RenderEngine, engine_module, loaded, scene  # noqa: E402

with mock.patch.dict(sys.modules, dict(MODULES, **LOADED)):
    from angelo_agent import engine_setup, engine_status, render_engines, render_extensions  # noqa: E402
    LOADED.update({name: module for name, module in sys.modules.items() if name.split(".")[0] == "angelo_agent"})
if _PACKAGE is not None:
    for _name in set(vars(_PACKAGE)) - set(_ATTRIBUTES):
        delattr(_PACKAGE, _name)
    for _name, _value in _ATTRIBUTES.items():
        setattr(_PACKAGE, _name, _value)

RENDER_INFO = 'bl_info = {"name": "%s", "category": "Render", "blender": (5, 0)}\n'


def addon(folder, module, name, category="Render", body=""):
    path = Path(folder) / module
    path.mkdir(parents=True)
    info = RENDER_INFO % name if category == "Render" else 'bl_info = {"name": "%s", "category": "%s"}\n' % (
        name, category)
    (path / "__init__.py").write_text(info + body, encoding="utf-8")
    return path


def no_links():
    """Neither a symbolic link nor, on Windows, a directory junction can be made: ``link`` falls back to a copy."""
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(engine_setup.os, "symlink", side_effect=OSError("no links")))
    if sys.platform == "win32":
        import _winapi
        stack.enter_context(mock.patch.object(_winapi, "CreateJunction", side_effect=OSError("no junctions")))
    return stack


class Folder(unittest.TestCase):
    def setUp(self):
        self.enterContext(loaded())
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        engine_setup._scanned.clear()


class DiscoveryTests(Folder):
    def test_bl_info_is_read_as_text_never_run(self):
        path = addon(self.root, "lux", "Lux", body="raise SystemExit('ran')\n") / "__init__.py"
        self.assertEqual(engine_setup.bl_info(path)["name"], "Lux")
        computed = self.root / "computed.py"
        computed.write_text("bl_info = dict(name=open('x').read())\n", encoding="utf-8")
        self.assertIsNone(engine_setup.bl_info(computed), "anything but a literal is never evaluated")
        broken = self.root / "broken.py"
        broken.write_text("bl_info = {", encoding="utf-8")
        self.assertIsNone(engine_setup.bl_info(broken))
        with mock.patch.object(engine_setup, "MAX_BYTES", 8):
            self.assertIsNone(engine_setup.bl_info(path), "a file too big for a bl_info is not read")

    def test_only_render_add_ons_are_listed_by_their_own_names(self):
        addon(self.root, "lux_engine", "Lux Render")
        addon(self.root, "tools", "Some Tools", category="Object")
        (self.root / "single.py").write_text(RENDER_INFO % "Single", encoding="utf-8")
        addon(self.root, ".hidden", "Hidden")
        rows = engine_setup.scan(self.root)
        self.assertEqual([(row["module"], row["name"]) for row in rows], [("lux_engine", "Lux Render"),
                                                                         ("single", "Single")])
        self.assertEqual(rows[0]["source"], str(self.root / "lux_engine"))
        self.assertEqual([row["module"] for row in engine_setup.scan(self.root, skip={"single"})], ["lux_engine"])
        self.assertEqual(engine_setup.scan(self.root / "missing"), [])

    def test_a_new_install_shows_without_a_restart(self):
        self.assertEqual(engine_setup.scan(self.root), [])
        time.sleep(0.01)
        addon(self.root, "lux", "Lux")
        os.utime(self.root)   # The folder's own time changes when an installer adds an entry.
        self.assertEqual([row["module"] for row in engine_setup.scan(self.root)], ["lux"])

    def test_blenders_add_ons_folder_needs_no_blender_install(self):
        cases = {"darwin": ({"CFFIXED_USER_HOME": "/U/a"}, "/U/a/Library/Application Support/Blender/5.2/scripts/addons"),
                 "win32": ({"APPDATA": "C:/R"}, "C:/R/Blender Foundation/Blender/5.2/scripts/addons"),
                 "linux": ({"XDG_CONFIG_HOME": "/c"}, "/c/blender/5.2/scripts/addons")}
        for platform, (environ, expected) in cases.items():
            with self.subTest(platform=platform):
                found = engine_setup.blender_addons(platform, None, environ, (5, 2))   # Home from the environment.
                self.assertEqual(found.as_posix(), expected)

    def test_found_lists_angelos_off_add_ons_then_blenders_never_shipped_or_on_ones(self):
        own = addon(self.root / "angelo", "lux", "Lux")
        on = addon(self.root / "angelo", "luxon", "Lux On")
        blender = self.root / "blender"
        addon(blender, "vendor_engine", "Vendor Engine")
        addon(blender, "lux", "Lux (Blender's copy)")
        addon(blender, "gone", "Gone From Angelo")   # On in Angelo's preferences, but only Blender's folder has it.
        modules = [SimpleNamespace(__name__="lux", __file__=str(own / "__init__.py")),
                   SimpleNamespace(__name__="luxon", __file__=str(on / "__init__.py")),
                   SimpleNamespace(__name__="cycles", __file__=str(ADDON.parent / "cycles" / "__init__.py"))]
        infos = {"lux": {"name": "Lux", "category": "Render"}, "luxon": {"name": "Lux On", "category": "Render"},
                 "cycles": {"name": "Cycles", "category": "Render"}}
        fake = types.ModuleType("addon_utils")
        fake.modules = lambda refresh=False: modules
        fake.module_bl_info = lambda module: infos[module.__name__]
        fake.check = lambda name: (False, name != "gone")   # "gone": on in the preferences, its files removed.
        with mock.patch.dict(sys.modules, {"addon_utils": fake}), \
                mock.patch.object(BPY, "context", SimpleNamespace(preferences=SimpleNamespace(
                    addons={"luxon": None, "gone": None})),
                                  create=True), \
                mock.patch.object(engine_setup, "blender_addons", lambda: blender):
            rows = engine_setup.found()
        self.assertEqual([(row["module"], row["source"]) for row in rows],
                         [("lux", ""), ("gone", str(blender / "gone")),
                          ("vendor_engine", str(blender / "vendor_engine"))])


class SetUpTests(Folder):
    def stand_in(self, registers=("LUX_RENDER",), enable_fails=False):
        calls, engines = [], {}
        fake = types.ModuleType("addon_utils")
        fake.modules_refresh = lambda: calls.append("refresh")

        def enable(module, default_set, persistent, handle_error):
            calls.append(("enable", module, default_set, persistent))
            if enable_fails:
                handle_error(RuntimeError("boom"))
                return None
            engines.update({identifier: "Lux (lux)" for identifier in registers})
            return types.ModuleType(module)
        fake.enable = enable
        fake.disable = lambda module, default_set: calls.append(("disable", module))
        addons = self.root / "angelo_addons"
        addons.mkdir()
        utils = SimpleNamespace(**vars(BPY.utils), user_resource=lambda kind, path="", create=False: str(addons),
                                refresh_script_paths=lambda: calls.append("paths"))
        patches = [mock.patch.object(BPY, "utils", utils),
                   mock.patch.object(BPY, "context", SimpleNamespace(preferences=SimpleNamespace(
                       use_preferences_save=True)), create=True),
                   mock.patch.object(BPY, "ops", SimpleNamespace(wm=SimpleNamespace(
                       save_userpref=lambda: calls.append("saved"))), create=True),
                   mock.patch.dict(sys.modules, {"addon_utils": fake}),
                   mock.patch.object(render_extensions, "addon_engines", lambda roots=None: dict(engines))]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        return calls, addons

    def test_a_blender_folder_add_on_is_linked_enabled_kept_on_and_its_engine_returned(self):
        source = addon(self.root / "blender", "lux", "Lux")
        calls, addons = self.stand_in()
        self.assertEqual(engine_setup.set_up("lux", "Lux", str(source)), ["LUX_RENDER"])
        self.assertTrue((addons / "lux").is_symlink())
        self.assertEqual((addons / "lux").resolve(), source.resolve())
        self.assertIn(("enable", "lux", True, True), calls)
        self.assertLess(calls.index("paths"), calls.index(("enable", "lux", True, True)),
                        "a first add-on's folder is importable before it is enabled")
        self.assertEqual(calls[-1], "saved")

    def test_an_add_on_with_no_engine_is_turned_off_and_its_link_removed(self):
        source = addon(self.root / "blender", "tools", "Tools")
        calls, addons = self.stand_in(registers=())
        with self.assertRaises(engine_setup.SetupError) as caught:
            engine_setup.set_up("tools", "Tools", str(source))
        self.assertEqual(caught.exception.template, engine_setup.NO_ENGINE)
        self.assertIn(("disable", "tools"), calls)
        self.assertFalse(os.path.lexists(addons / "tools"))

    def test_an_add_on_that_fails_to_enable_leaves_nothing_behind(self):
        source = addon(self.root / "blender", "lux", "Lux")
        _calls, addons = self.stand_in(enable_fails=True)
        with self.assertRaises(engine_setup.SetupError) as caught:
            engine_setup.set_up("lux", "Lux", str(source))
        self.assertEqual((caught.exception.template, caught.exception.fields["error"]), (engine_setup.NOT_ON, "boom"))
        self.assertFalse(os.path.lexists(addons / "lux"))

    def test_an_add_on_with_no_engine_is_not_offered_again_until_its_files_change(self):
        source = addon(self.root / "blender", "tools", "Tools")
        self.stand_in(registers=())
        with self.assertRaises(engine_setup.SetupError):
            engine_setup.set_up("tools", "Tools", str(source))
        row = {"module": "tools", "name": "Tools", "source": str(source)}
        self.assertEqual(engine_setup._no_engine["tools"], engine_setup._stamp(row))
        os.utime(source / "__init__.py", ns=(1, 1))   # Updated by its vendor: offered again.
        self.assertNotEqual(engine_setup._no_engine["tools"], engine_setup._stamp(row))
        engine_setup._no_engine.clear()

    def test_a_copy_that_fails_partway_leaves_nothing(self):
        source = addon(self.root / "blender", "lux", "Lux")
        (source / "more.py").write_text("x = 1\n", encoding="utf-8")

        def partial(src, dst):
            Path(dst).mkdir()
            (Path(dst) / "half").write_text("", encoding="utf-8")
            raise OSError("disk full")
        with no_links(), mock.patch("shutil.copytree", partial), self.assertRaises(OSError):
            engine_setup.link(source, self.root / "copy")
        self.assertFalse((self.root / "copy").exists())

    def test_without_links_a_small_add_on_is_copied_and_a_large_one_refused(self):
        source = addon(self.root / "blender", "lux", "Lux")
        with no_links():
            self.assertEqual(engine_setup.link(source, self.root / "copy"), "copy")
            self.assertTrue((self.root / "copy" / "__init__.py").is_file())
            with mock.patch.object(engine_setup, "MAX_COPY_BYTES", 4), self.assertRaises(OSError):
                engine_setup.link(source, self.root / "big")


    def test_a_copy_from_a_read_only_install_is_the_persons_to_update_and_remove(self):
        """A vendor's engine installed read-only (another Blender's bundle): its copy kept read-only modes, so it
        could not be replaced or removed (the removal ignored errors and left it)."""
        if getattr(os, "geteuid", lambda: 1)() == 0:
            self.skipTest("[environment] root ignores permissions")
        source = addon(self.root / "blender", "lux", "Lux")
        (source / "data").mkdir()
        (source / "data" / "table.txt").write_text("x\n", encoding="utf-8")
        for path in [*source.rglob("*"), source]:
            os.chmod(path, os.stat(path).st_mode & 0o7555)
        self.addCleanup(lambda: [os.chmod(path, os.stat(path).st_mode | 0o700) for path in [source, *source.rglob("*")]])
        target = self.root / "copy"
        with no_links():
            self.assertEqual(engine_setup.link(source, target), "copy")
        for path in [target, *target.rglob("*")]:
            self.assertTrue(os.access(path, os.W_OK), str(path))
        engine_setup._remove(target, "copy")
        self.assertFalse(target.exists(), "the copy could not be removed")


class Vendor:
    def __init__(self, licensed=False, edition="paid", fails=False, answers=True):
        self.facts, self.fails, self.answers = [licensed, edition], fails, answers

    def read(self):
        if self.fails:
            raise AttributeError("internals changed")
        return tuple(self.facts)


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(loaded())
        self.vendor, self.restarts = Vendor(), []
        self.addCleanup(engine_status.VENDORS.pop, "lux_addon", None)
        engine_status.VENDORS["lux_addon"] = engine_status.Vendor(
            "Lux Inc", self.vendor.read, "lux.account", "lux.free", lambda: self.vendor.answers,
            lambda: self.restarts.append(True))
        engine_status._answered.clear()
        self.addCleanup(engine_status._answered.clear)
        self.addCleanup(engine_status.settle)

    def checked(self):
        """The vendor's service checked again, on its worker thread, and that check finished."""
        engine_status.service_answers("lux_addon", fresh=True)
        engine_status.settle()

    def state(self, now, on=True):
        self.checked()
        return engine_status.state("lux_addon", 100.0, on=on, now=now)

    def test_an_engine_without_a_vendor_licence_is_ready_at_once(self):
        self.assertEqual(engine_status.state("other_addon", 0.0), (engine_status.READY, None))

    def test_the_vendors_service_comes_first_then_starting_sign_in_and_ready(self):
        self.vendor.answers = False
        self.assertEqual(self.state(500.0), (engine_status.SERVICE, None), "no licence question without it")
        self.vendor.answers = True
        self.assertEqual(self.state(105.0)[0], engine_status.STARTING)
        self.assertEqual(self.state(100.0 + engine_status.GRACE)[0], engine_status.SIGN_IN)
        self.assertEqual(self.state(10_000.0)[0], engine_status.SIGN_IN, "a server that never starts still asks")
        self.assertEqual(self.state(105.0, on=False)[0], engine_status.STARTING, "not on: nothing of it is read")
        self.vendor.facts[:] = [True, "free"]
        self.assertEqual(self.state(105.0), (engine_status.READY, "free"))
        self.vendor.fails = True
        self.assertEqual(self.state(105.0), (None, None), "unreadable: never a guessed edition")

    def test_the_service_is_read_at_most_every_few_seconds_unless_fresh(self):
        calls = []
        engine_status.VENDORS["lux_addon"].service = lambda: calls.append(1) or True
        self.assertIsNone(engine_status.service_answers("lux_addon"), "no answer before a first check")
        engine_status.settle()
        for _ in range(5):
            self.assertTrue(engine_status.service_answers("lux_addon"))
        engine_status.settle()
        engine_status.service_answers("lux_addon", fresh=True)
        engine_status.settle()
        self.assertEqual(len(calls), 2)

    def test_a_slow_service_never_holds_the_caller_up(self):
        """Audit P2 (#103): a menu draw, the card or an operator never waits on the vendor's socket."""
        threads = []

        def slow():
            threads.append(threading.current_thread())
            time.sleep(0.6)
            return False
        engine_status.VENDORS["lux_addon"].service = slow
        begun = machine_clock.start(self)
        self.assertIsNone(engine_status.row_note("lux_addon", on=False), "not checked yet: no row note")
        self.assertEqual(engine_status.state("lux_addon", 0.0)[0], engine_status.STARTING)
        begun.assert_within(0.3, "the check runs while the caller goes on")
        engine_status.settle()
        self.assertEqual(threads and threads[0] is not threading.main_thread(), True, "on a worker thread")
        self.assertEqual(len(threads), 1, "one check in flight at a time")
        self.assertEqual(engine_status.row_note("lux_addon", on=False)[0], engine_status.ROW_SERVICE)

    def test_a_restart_runs_on_a_worker_and_the_engine_reads_as_starting_meanwhile(self):
        """Audit P2 (#103): the card's Restart never waits on ``launchctl``."""
        threads = []

        def slow_restart():
            threads.append(threading.current_thread())
            time.sleep(0.6)
        engine_status.VENDORS["lux_addon"].restart = slow_restart
        self.vendor.answers = False
        self.checked()
        called = []
        card = engine_status.EngineStatusCard("lux_addon", "Lux")
        card.refresh(self.host(called))
        self.assertEqual(card.shown[0], engine_status.SERVICE)
        begun = machine_clock.start(self)
        card.act("restart", None, self.host(called))
        begun.assert_within(0.3, "the restart runs while the card goes on")
        self.assertEqual(card.shown[0], engine_status.STARTING, "restarting: no answer to show yet")
        self.vendor.answers = True
        engine_status.settle()
        self.assertTrue(threads and threads[0] is not threading.main_thread())
        self.assertTrue(engine_status.service_answers("lux_addon"), "checked again after the restart")

    def test_a_check_a_restart_superseded_never_writes_its_old_answer(self):
        release, first = threading.Event(), []   # The first check would say "not answering", after the restart.
        engine_status.VENDORS["lux_addon"].service = lambda: (release.wait(5),
                                                              threading.current_thread() not in first)[1]
        engine_status.service_answers("lux_addon")
        first.append(engine_status._checking["lux_addon"])
        self.assertTrue(engine_status.restart_service("lux_addon"))
        release.set()
        engine_status.settle()
        engine_status.settle()
        self.assertTrue(engine_status.service_answers("lux_addon"))

    def test_the_switcher_rows_say_what_is_missing(self):
        self.vendor.answers = False
        self.checked()
        self.assertEqual(engine_status.row_note("lux_addon", on=False)[0], engine_status.ROW_SERVICE)
        self.vendor.answers = True
        self.checked()
        self.assertEqual(engine_status.row_note("lux_addon")[0], engine_status.ROW_SIGN_IN)
        self.vendor.facts[0] = True
        self.assertIsNone(engine_status.row_note("lux_addon"))
        self.assertIsNone(engine_status.row_note("other_addon"))

    def host(self, called):
        window = SimpleNamespace(screen=SimpleNamespace(areas=[]), scene=None, as_pointer=lambda: 1)
        return SimpleNamespace(redraw=lambda: None, close=lambda how: called.append(("closed", how)),
                               window=lambda: window)

    def ops(self, called):
        ops = SimpleNamespace(lux=SimpleNamespace(account=lambda how: called.append(("account", how)),
                                                  free=lambda how: called.append(("free", how))))
        context = SimpleNamespace(temp_override=lambda **kwargs: mock.MagicMock(), window=None, scene=None)
        for patcher in (mock.patch.object(BPY, "ops", ops, create=True),
                        mock.patch.object(BPY, "context", context, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_card_calls_the_add_ons_own_operators_and_its_confirmation_for_the_free_edition(self):
        called = []
        self.ops(called)
        self.checked()
        card = engine_status.EngineStatusCard("lux_addon", "Lux")
        card.started -= engine_status.GRACE
        card.refresh()
        self.assertEqual(card.shown, (engine_status.SIGN_IN, "paid"))
        host = self.host(called)
        card.act("sign_in", None, host)
        card.act("free", None, host)
        self.assertEqual(called, [("account", "EXEC_DEFAULT"), ("free", "INVOKE_DEFAULT")])
        self.assertEqual(card.shown[0], engine_status.STARTING, "the free edition restarts its server")
        card.started -= engine_status.GRACE
        self.vendor.facts[1] = "free"
        card.refresh()
        card.act("free", None, host)
        self.assertEqual(called[-1], ("free", "INVOKE_DEFAULT"), "already free: its toggle is never called again")
        self.assertEqual(len(called), 2)

    def test_the_free_edition_is_mentioned_only_where_the_add_on_has_one(self):
        source = (ADDON / "engine_status.py").read_text(encoding="utf-8")
        self.assertIn("SIGN_IN_WHY if self.vendor.free else SIGN_IN_ONLY", source)

    def test_an_unreadable_licence_closes_the_card(self):
        called = []
        self.checked()
        card = engine_status.EngineStatusCard("lux_addon", "Lux")
        self.vendor.fails = True
        card.refresh(self.host(called))
        self.assertEqual(called, [("closed", "unreadable")])

    def test_a_set_up_is_held_while_the_service_does_not_answer_then_runs(self):
        called, set_up = [], []
        self.ops(called)
        self.vendor.answers = False
        row = {"module": "lux_addon", "name": "Lux", "source": "/v/lux_addon"}
        shown = []
        with mock.patch.object(engine_status, "show", lambda module, engine, pending=None: shown.append(pending) or True):
            self.assertTrue(engine_status.hold_set_up(row), "not checked yet: held, never turned on unchecked")
            engine_status.settle()
            self.assertTrue(engine_status.hold_set_up(row), "not answering: held")
        self.assertEqual(shown, [row, row])
        card = engine_status.EngineStatusCard("lux_addon", "Lux", pending=row)
        card.refresh(self.host(called))
        self.assertEqual(card.shown[0], engine_status.SERVICE)
        card.act("restart", None, self.host(called))
        engine_status.settle()
        self.assertEqual(self.restarts, [True])
        self.vendor.answers = True
        with mock.patch.object(engine_setup, "set_up", lambda *args: set_up.append(args) or ["LUX_RENDER"]), \
                mock.patch.object(render_engines, "apply", lambda scene, engine: None), \
                mock.patch.object(render_engines, "current_label", lambda scene: "Lux"):
            card.act("retry", None, self.host(called))
            engine_status.settle()
            card.refresh(self.host(called))
        self.assertEqual(set_up, [("lux_addon", "Lux", "/v/lux_addon")])
        self.assertIsNone(card.pending)
        self.assertFalse(engine_status.hold_set_up(row), "answered just now: the set-up goes straight on")


class SwitcherTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(loaded())

    def test_installed_engines_not_on_come_last_and_set_up_by_module(self):
        rows = [{"module": "lux", "name": "Lux Render", "source": "/somewhere/lux"}]
        with mock.patch.object(engine_setup, "found", lambda: rows), \
                mock.patch.object(render_engines, "engine_ids", lambda: {"BLENDER_EEVEE"}), \
                mock.patch.object(render_engines, "others", lambda: []), \
                mock.patch.object(render_engines, "has_cycles", lambda scene: False):
            choices = render_engines.choices(scene())
        last = choices[-1]
        self.assertEqual((last["value"], last["label"], last["translate"], last["operator"], last["properties"]),
                         ("setup:lux", "Lux Render", False, "angelo.render_engine_setup", (("module", "lux"),)))
        self.assertEqual(last["subtitle"], engine_setup.SETUP_WHY)

    def test_a_set_up_names_only_a_module_and_takes_its_folder_from_what_was_found(self):
        source = (ADDON / "render_engines.py").read_text(encoding="utf-8")
        start = source.index("class ANGELO_OT_render_engine_setup")
        body = source[start:source.index("\nNOT_FOUND", start)]
        self.assertIn("module: StringProperty", body)
        self.assertNotIn("source: StringProperty", body)
        self.assertIn("engine_setup.found()", body)
        self.assertIn('self.layout.operator(row["operator"]', source, "the Render menu sets an engine up too")


if __name__ == "__main__":
    unittest.main()
