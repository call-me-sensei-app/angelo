# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Engine detection, the one-time suggestion and the agent's note (doc/angelo/engine_link.rst, "Engine detection and
the suggestion"). No Blender: each operating system's process table is faked at its system call (libproc on macOS,
Toolhelp32 on Windows), the file system by ``exists``, and the card runs on ``notices_fakes``' stand-in ``bpy``.

Covered: which executable names count as each engine's editor (and which don't: the Unity Hub, Unreal's windowless
commandlet); the macOS and Windows readers return every name and survive a failing call; installed engines by the
places their installers use; the suggestion is offered once per engine, never while the link is on or after the
person used it, and a damaged memory suggests nothing; the agent's note holds engine names only and is sent only
when it changed; the watcher reads scans on the main thread and asks for the card; the card's buttons connect
nothing except Link a project…, which opens that engine's folder picker.
"""

import ctypes
import importlib
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import angelo_agent  # noqa: E402
from angelo_agent import engine_detect, engine_suggest  # noqa: E402

MAC_NAMES = ["launchd", "Finder", "Godot", "Unity Hub", "UnrealEditor-Cmd", "zsh", "Angelo"]
WINDOWS_NAMES = ["System", "explorer.exe", "Godot_v4.3-stable_mono_win64.exe", "Unity Hub.exe", "Unity.exe",
                 "UnrealEditor.exe", "UnrealEditor-Cmd.exe"]


class NamesTests(unittest.TestCase):
    def test_each_engines_editor_and_nothing_else(self):
        cases = {"Godot": "godot", "godot.exe": "godot", "Godot_v4.3-stable_win64.exe": "godot",
                 "Godot_v4.4-stable_mono_win64_console.exe": "godot", "Unity": "unity", "Unity.exe": "unity",
                 "UnrealEditor": "unreal", "UnrealEditor.exe": "unreal", "UE4Editor.exe": "unreal",
                 "UnrealEditor-Win64-DebugGame.exe": "unreal",
                 "/Applications/Unity/Hub/Editor/6000.0.1f1/Unity.app/Contents/MacOS/Unity": "unity",
                 "C:\\Program Files\\Epic Games\\UE_5.4\\Engine\\Binaries\\Win64\\UnrealEditor.exe": "unreal"}
        for name, engine in cases.items():
            with self.subTest(name=name):
                self.assertEqual(engine_detect.engine_of(name), engine)
        for name in ("Unity Hub", "Unity Hub.exe", "UnrealEditor-Cmd.exe", "UnrealEditor-Cmd", "Finder",
                     "EpicGamesLauncher.exe", "Blender", "", "  ", "unityhub"):
            with self.subTest(name=name):
                self.assertIsNone(engine_detect.engine_of(name))

    def test_running_judges_a_given_table(self):
        self.assertEqual(engine_detect.running(MAC_NAMES), {"godot"})
        self.assertEqual(engine_detect.running(WINDOWS_NAMES), {"godot", "unity", "unreal"})
        self.assertEqual(engine_detect.running([]), frozenset())

    def test_an_unsupported_or_failing_system_finds_nothing(self):
        self.assertEqual(engine_detect.process_names("linux"), [])
        with mock.patch.object(engine_detect, "_mac_names", side_effect=OSError("no libproc")):
            self.assertEqual(engine_detect.running(platform="darwin"), frozenset())
        with mock.patch.object(engine_detect, "_windows_names", side_effect=AttributeError("no kernel32")):
            self.assertEqual(engine_detect.running(platform="win32"), frozenset())


class FakeLibproc:
    """libproc's two calls over ``names`` (pid i+1 is names[i]); pid 4 has gone between the two calls."""

    def __init__(self, names):
        self.names = names

    def proc_listallpids(self, buffer, size):
        if buffer is None:
            return len(self.names)
        for index in range(len(self.names)):
            buffer[index] = index + 1
        return len(self.names)

    def proc_name(self, pid, buffer, size):
        if pid == 4:
            return 0
        buffer.value = self.names[pid - 1].encode()
        return len(self.names[pid - 1])


class FakeKernel32:
    """Toolhelp32 over ``names``: Process32FirstW/NextW fill the entry ``byref`` points at."""

    def __init__(self, names, snapshot=7):
        self.names, self.snapshot, self.closed, self.index = names, snapshot, [], 0
        self.CreateToolhelp32Snapshot = SimpleNamespace()   # restype/argtypes are set on it like on a ctypes function.
        self.Process32FirstW = SimpleNamespace()
        self.Process32NextW = SimpleNamespace()
        self.CloseHandle = SimpleNamespace()
        functions = {"CreateToolhelp32Snapshot": lambda flags, pid: self.snapshot,
                     "Process32FirstW": lambda handle, entry: self._entry(entry, first=True),
                     "Process32NextW": lambda handle, entry: self._entry(entry),
                     "CloseHandle": self.closed.append}
        for name, function in functions.items():
            setattr(self, name, _Callable(function))

    def _entry(self, reference, first=False):
        if first:
            self.index = 0
        if self.index >= len(self.names):
            return 0
        reference._obj.szExeFile = self.names[self.index]
        self.index += 1
        return 1


class _Callable:
    def __init__(self, function):
        self.function = function

    def __call__(self, *args):
        return self.function(*args)


class ReaderTests(unittest.TestCase):
    def test_macos_reads_every_process_name(self):
        library = FakeLibproc(MAC_NAMES)
        with mock.patch.object(ctypes, "CDLL", return_value=library) as cdll:
            names = engine_detect.process_names("darwin")
        cdll.assert_called_once_with("/usr/lib/libproc.dylib", use_errno=True)
        self.assertEqual(names, [name for index, name in enumerate(MAC_NAMES) if index != 3])
        with mock.patch.object(ctypes, "CDLL", return_value=library):
            self.assertEqual(engine_detect.running(platform="darwin"), {"godot"})

    def test_windows_reads_every_process_name_and_closes_the_snapshot(self):
        kernel32 = FakeKernel32(WINDOWS_NAMES)
        with mock.patch.object(ctypes, "WinDLL", return_value=kernel32, create=True):
            names = engine_detect.process_names("win32")
            self.assertEqual(engine_detect.running(platform="win32"), {"godot", "unity", "unreal"})
        self.assertEqual(names, WINDOWS_NAMES)
        self.assertEqual(kernel32.closed, [7, 7])

    def test_windows_without_a_snapshot_finds_nothing(self):
        kernel32 = FakeKernel32(WINDOWS_NAMES, snapshot=0)
        with mock.patch.object(ctypes, "WinDLL", return_value=kernel32, create=True):
            self.assertEqual(engine_detect.process_names("win32"), [])


class InstalledTests(unittest.TestCase):
    def test_installers_places_per_system(self):
        def exists_among(*present):
            return lambda pattern: pattern in present

        mac = engine_detect.installed("darwin", {"HOME": "/Users/a"}, exists_among(
            "/Users/a/Applications/Godot*.app", "/Users/Shared/Epic Games/UE_5.*"))
        self.assertEqual(mac, {"godot", "unreal"})
        windows = engine_detect.installed("win32", {"ProgramFiles": "C:\\Program Files", "LOCALAPPDATA": "C:\\L"},
                                          exists_among("C:\\Program Files/Unity/Hub/Editor/*/Editor/Unity.exe"))
        self.assertEqual(windows, {"unity"})
        # An unset variable is a place that doesn't exist here, never a path relative to the working folder.
        seen = []
        engine_detect.installed("win32", {}, lambda pattern: seen.append(pattern) or False)
        self.assertEqual(seen, [])
        self.assertEqual(engine_detect.installed("linux", {}, lambda pattern: True), frozenset())


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = os.path.join(self.folder.name, "state", engine_suggest.FILE_NAME)

    def test_once_per_engine_in_a_fixed_order(self):
        memory = engine_suggest.Memory(self.path)
        both = frozenset({"unity", "godot"})
        self.assertEqual(memory.next_suggestion(both, False, False), "godot")
        memory.mark("godot")
        self.assertEqual(memory.next_suggestion(both, False, False), "unity")
        memory.mark("unity")
        self.assertIsNone(memory.next_suggestion(both, False, False))
        again = engine_suggest.Memory(self.path)   # A later launch remembers.
        self.assertIsNone(again.next_suggestion(both, False, False))
        self.assertEqual(again.next_suggestion({"unreal"}, False, False), "unreal")
        again.mark("unreal")
        self.assertTrue(again.done())
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"suggested": ["godot", "unity", "unreal"]})

    def test_never_while_the_link_is_on_or_once_the_person_used_it(self):
        memory = engine_suggest.Memory(self.path)
        self.assertIsNone(memory.next_suggestion({"unity"}, True, False))
        self.assertIsNone(memory.next_suggestion({"unity"}, False, True))
        self.assertIsNone(memory.next_suggestion(frozenset(), False, False))

    def test_a_damaged_memory_suggests_nothing(self):
        os.makedirs(os.path.dirname(self.path))
        for text in ("{not json", "[1, 2]"):
            with self.subTest(text=text):
                with open(self.path, "w", encoding="utf-8") as handle:
                    handle.write(text)
                memory = engine_suggest.Memory(self.path)
                self.assertTrue(memory.done())
                self.assertIsNone(memory.next_suggestion({"godot"}, False, False))


class NoteTests(unittest.TestCase):
    def test_names_only_and_sent_only_when_changed(self):
        reported = engine_suggest.Reported()
        self.assertIsNone(reported.take(engine_suggest.note(frozenset(), False)))   # Nothing to say yet: no bytes.
        unity = engine_suggest.note(frozenset({"unity"}), False)
        self.assertEqual(reported.take(unity), unity)
        self.assertEqual(unity["running"], ["Unity"])
        self.assertIn("FEATURES", unity["hint"])
        self.assertIsNone(reported.take(engine_suggest.note(frozenset({"unity"}), False)))
        on = engine_suggest.note(frozenset({"unity"}), True)
        self.assertNotIn("hint", on)
        self.assertEqual(reported.take(on), on)
        closed = engine_suggest.note(frozenset(), True)
        self.assertEqual(reported.take(closed), closed)   # The engine closed: told once.
        text = json.dumps([unity, on, closed])
        self.assertNotIn("/", text.replace("angelo_agent_capabilities", ""))
        self.assertNotIn("\\\\", text)


def _stand_in(name, **attributes):
    module = types.ModuleType("angelo_agent." + name)
    module.__dict__.update(attributes)
    return module


class WatcherAndCardTests(unittest.TestCase):
    """``engine_suggest_ui`` on the notices' stand-in Blender."""

    def setUp(self):
        from notices_fakes import Notices
        self.link = SimpleNamespace(on=False)
        self.opened = []
        self.notices = Notices(
            engine_link=_stand_in("engine_link", running=lambda: self.link.on),
            engine_link_ui=_stand_in("engine_link_ui", remembered=lambda: False),
            splash_setup=_stand_in("splash_setup", after_close=lambda window, function: function()))
        self.n = self.notices.__enter__()
        self.addCleanup(self.notices.__exit__, None, None, None)
        saved = sys.modules.pop("angelo_agent.engine_suggest_ui", None)
        attribute = getattr(angelo_agent, "engine_suggest_ui", None)
        self.ui = importlib.import_module("angelo_agent.engine_suggest_ui")

        def restore():
            sys.modules.pop("angelo_agent.engine_suggest_ui", None)
            if saved is not None:
                sys.modules["angelo_agent.engine_suggest_ui"] = saved
            if attribute is not None:
                angelo_agent.engine_suggest_ui = attribute
            elif hasattr(angelo_agent, "engine_suggest_ui"):
                del angelo_agent.engine_suggest_ui
        self.addCleanup(restore)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.ui._memory = engine_suggest.Memory(os.path.join(folder.name, engine_suggest.FILE_NAME))
        self.n.bpy.ops.angelo.godot_link_install = lambda *args, **kwargs: self.opened.append(kwargs) or {'FINISHED'}
        patches = [mock.patch.object(self.ui, "_start_scan"), mock.patch.object(self.n.card_host, "pump"),
                   mock.patch.object(self.n.app_notices, "automation_off", return_value=False)]
        self.start_scan = patches[0].start()
        for patch in patches[1:]:
            patch.start()
        for patch in patches:
            self.addCleanup(patch.stop)

    def _scan(self, *engines):
        self.ui._inbox.append((frozenset(engines), frozenset()))

    def _show(self, card):
        """Show ``card`` as card_host does (``opened``), with a host that records closes."""
        host = SimpleNamespace(closed=[], window=lambda: "window")
        host.close = host.closed.append
        host.after = lambda seconds, function: function()
        card.opened(host)
        return host

    def test_a_finished_scan_is_read_at_once_and_scans_keep_their_interval(self):
        self.assertEqual(self.ui.tick(), self.ui.COLLECT)   # A scan starts and its result is read right after.
        self.start_scan.assert_called_once_with()
        remaining = self.ui.tick()   # Read; the next scan waits its interval.
        self.assertTrue(self.ui.INTERVAL - 1 < remaining <= self.ui.INTERVAL, remaining)
        self.assertEqual(self.start_scan.call_count, 1)
        self.ui._scan = SimpleNamespace(is_alive=lambda: True)
        self.assertEqual(self.ui.tick(), self.ui.COLLECT)   # A scan still running is read as soon as it ends.

    def test_a_running_engine_gets_one_card_and_the_watcher_slows_when_none_is_left(self):
        self.ui.tick()
        self.assertEqual(self.n.card_host.queued("engine_suggest"), [])   # No scan finished yet: nothing to show.
        self._scan("unity")
        self.ui.tick()
        cards = self.n.card_host.queued("engine_suggest")
        self.assertEqual([card.engine for card in cards], ["unity"])
        self.assertEqual(cards[0].title, "Unity is open")
        self.assertEqual([(action, style) for action, _label, style in cards[0].buttons],
                         [("later", "ghost"), ("link", "primary")])
        self._show(cards[0])
        self.n.card_host.withdraw("engine_suggest")   # Shown and answered.
        self._scan("unity")
        self.ui.tick()   # The same engine again: no second card.
        self.assertEqual(self.n.card_host.queued("engine_suggest"), [])
        for engine in ("godot", "unreal"):
            self._scan(engine)
            self.ui.tick()
            self._show(self.n.card_host.queued("engine_suggest")[0])
            self.n.card_host.withdraw("engine_suggest")
        self.ui._next_scan = 0.0
        self.ui.tick()   # Every engine suggested: the next scan waits the quiet interval (the agent's facts only).
        self.assertTrue(self.ui._next_scan - time.monotonic() > self.ui.INTERVAL)

    def test_an_engine_installed_but_not_running_gets_no_card(self):
        # "Is open" is a running editor process, the same check for every engine: an installed editor, or one that
        # ran before (its settings folder), is never open (1.0.8 re-cut: a card over a suite was asked about).
        for engine in engine_detect.ENGINES:
            with self.subTest(engine=engine):
                self.ui._inbox.append((frozenset(), frozenset(engine_detect.ENGINES)))
                self.ui.tick()
                self.assertEqual(self.n.card_host.queued("engine_suggest"), [])
                # What a Mac runs with every editor installed and none open (process names, as proc_name gives).
                self.assertEqual(engine_detect.running(["Finder", "Unity Hub", "UnrealEditor-Cmd", "EpicGamesLauncher",
                                                        "zsh", "Angelo"]), frozenset())
        self.assertEqual(self.ui.memory().suggested, set())

    def test_cards_that_never_show_use_up_no_suggestion(self):
        """Audit 2026-11-01: with the window unable to show cards and three engines running, the queue replaced
        Godot's card with Unity's and Unity's with Unreal's, and all three counted as suggested."""
        for _ in range(3):
            self._scan("godot", "unity", "unreal")
            self.ui.tick()
        waiting = self.n.card_host.queued("engine_suggest")
        self.assertEqual([card.engine for card in waiting], ["godot"])   # One waits; it is never replaced.
        self.assertEqual(self.ui.memory().suggested, set())
        self._show(waiting[0])
        self.assertEqual(self.ui.memory().suggested, {"godot"})   # Counted once it showed.
        self.n.card_host.withdraw("engine_suggest")
        self._scan("godot", "unity", "unreal")
        self.ui.tick()
        self.assertEqual([card.engine for card in self.n.card_host.queued("engine_suggest")], ["unity"])

    def test_a_waiting_card_that_no_longer_applies_is_dropped_or_closes_as_it_shows(self):
        self._scan("godot")
        self.ui.tick()
        self._scan()   # Godot closed while its card waited.
        self.ui.tick()
        self.assertEqual(self.n.card_host.queued("engine_suggest"), [])
        self._scan("godot")
        self.ui.tick()
        card = self.n.card_host.queued("engine_suggest")[0]
        self.link.on = True   # The person turned the link on before the card could show.
        host = self._show(card)
        self.assertEqual(host.closed, ["stale"])
        self.assertEqual(self.ui.memory().suggested, set())   # Not used up: it never showed as a suggestion.

    def test_no_card_while_the_link_is_on_and_none_in_a_background_session(self):
        self.link.on = True
        self._scan("godot")
        self.ui.tick()
        self.assertEqual(self.n.card_host.queued("engine_suggest"), [])
        self.n.bpy.app.background = True
        self.assertIsNone(self.ui.tick())   # The watcher stops.

    def test_only_link_a_project_opens_anything(self):
        host = SimpleNamespace(closed=[], window=lambda: "window")
        host.close = host.closed.append
        card = self.ui.SuggestionCard("unreal")
        card.act("later", None, host)
        self.assertEqual((host.closed, self.opened), (["later"], []))
        card.act("close", None, host)
        self.assertEqual(self.opened, [])
        card.act("link", None, host)
        self.assertEqual(self.opened, [{"engine": "UNREAL"}])

    def test_the_agent_hears_of_a_change_once_per_chat(self):
        self._scan("godot")
        self.ui.tick()
        first, second = SimpleNamespace(), SimpleNamespace()
        note = self.ui.for_request(first)
        self.assertEqual(note["running"], ["Godot"])
        self.assertIsNone(self.ui.for_request(first))
        self.assertEqual(self.ui.for_request(second), note)   # Another chat's agent hasn't been told.
        self.assertEqual(self.ui.facts(), {"running": ["Godot"], "installed": [], "live_link_on": False})

    def test_a_scan_keeps_engine_ids_only(self):
        with mock.patch.object(engine_detect, "running", return_value=frozenset({"unity"})), \
                mock.patch.object(engine_detect, "installed", return_value=frozenset({"godot"})):
            self.ui._scan_once(True)
        self.assertEqual(self.ui._inbox[-1], (frozenset({"unity"}), frozenset({"godot"})))
        with mock.patch.object(engine_detect, "running", side_effect=RuntimeError("boom")):
            self.ui._scan_once(False)   # A failing scan is "nothing found", never an error.
        self.assertEqual(self.ui._inbox[-1], (frozenset(), None))


if __name__ == "__main__":
    unittest.main()
