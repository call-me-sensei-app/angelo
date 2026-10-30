# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Unreal link's live layer, without Unreal (doc/angelo/unreal_link.rst, "The live layer").

Covered:

* The Angelo Link Live plugin is an editor-only C++ module, MIT, with no Epic code and no path of the machine that
  wrote it; the content plugin never lists it, so the durable layer runs where no binary exists.
* The plugin's Python (``angelo_link.editor`` and ``live``) on a stand-in ``unreal``: without the native library the
  handshake says ``durable`` only; with it, ``live`` too. A live scene is written to an owner-only file in one step
  and shown, and Angelo hears ``previewed`` with the version and how many instances show it; a frame the native
  side can't read keeps the earlier preview and says ``preview_failed``. A save's re-import ends the preview, unless
  a newer live scene arrived after the save; ``ended`` and a broken link end it and restore the throttle.
"""

import importlib
import json
import os
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PLUGINS = ROOT / "scripts/addons_core/angelo_agent/unreal_plugin"
LIVE_PLUGIN = PLUGINS / "AngeloLinkLive"
sys.path.insert(0, str(PLUGINS / "AngeloLink/Content/Python"))


class FakeNative:
    """unreal.AngeloLinkLiveLibrary, as the C++ module exposes it to Python."""

    def __init__(self, version="0.1.0", shown=1):
        self.version, self.shown, self.calls, self.live, self.count = version, shown, [], False, 0

    def live_version(self):
        return self.version

    def show_preview(self, path, package):
        with open(path, "rb") as handle:
            self.calls.append(("show", package, handle.read(), os.stat(os.path.dirname(path)).st_mode & 0o777))
        if self.shown < 0:
            return -1, "Couldn't read Angelo's live scene"
        self.count = self.shown
        return self.shown, ""

    def clear_preview(self):
        self.calls.append(("clear",))
        self.count = 0
        return 1

    def preview_count(self):
        return self.count

    def set_session_live(self, on):
        self.live = on


class FakeClient:
    def __init__(self):
        self.sent, self.ready, self.last_error = [], True, ""

    def is_ready(self):
        return self.ready

    def request(self, header):
        self.sent.append(header)

    def poll(self):
        pass


def fake_unreal(native=None):
    module = types.ModuleType("unreal")
    module.logs = []
    module.log = lambda text: module.logs.append(("log", text))
    module.log_warning = lambda text: module.logs.append(("warning", text))
    module.uclass = lambda: (lambda cls: cls)
    module.ufunction = lambda **_: (lambda function: function)
    module.ToolMenuEntryScript = object
    module.shutdown_callbacks = []
    module.register_python_shutdown_callback = module.shutdown_callbacks.append
    if native is not None:
        module.AngeloLinkLiveLibrary = native
    return module


class LiveGlueTests(unittest.TestCase):
    def load(self, native=None):
        sys.modules["unreal"] = fake_unreal(native)
        package = importlib.import_module("angelo_link")
        for name in ("live", "editor"):   # Fresh modules on this stand-in (``from . import`` reuses attributes).
            sys.modules.pop("angelo_link." + name, None)
            package.__dict__.pop(name, None)
        editor = importlib.import_module("angelo_link.editor")
        self.addCleanup(lambda: [sys.modules.pop(name, None) for name in ("unreal", "angelo_link.editor",
                                                                          "angelo_link.live")])
        self.client = FakeClient()
        editor._state.update(client=self.client, linked="", scene=0, saved_scene=0, saving=None)
        self.addCleanup(editor.live.shutdown)
        return editor

    def open(self, editor, package="/Game/Props/SM_Crate"):
        editor._on_message({"type": "opened", "asset": package}, b"")
        return package

    def test_no_native_library_is_durable_only(self):
        editor = self.load()
        self.assertFalse(editor.live.available())
        self.assertEqual(editor.capabilities(), ["durable"])
        package = self.open(editor)
        editor._on_message({"type": "scene", "asset": package, "version": 1}, b"glb")
        self.assertEqual([item["code"] for item in self.client.sent], ["preview_failed"])

    def test_an_older_native_library_is_not_used(self):
        self.assertFalse(self.load(FakeNative(version="0.0.9")).live.available())

    def test_live_scene_is_shown_and_reported(self):
        native = FakeNative(shown=2)
        editor = self.load(native)
        self.assertTrue(editor.live.available())
        self.assertEqual(editor.capabilities(), ["durable", "live"])
        package = self.open(editor)
        self.assertTrue(native.live)
        editor._on_message({"type": "scene", "asset": package, "version": 3}, b"\x00glb-bytes")
        kind, shown_package, data, mode = native.calls[-1]
        self.assertEqual((kind, shown_package, data, mode), ("show", package, b"\x00glb-bytes", 0o700))
        self.assertEqual(self.client.sent[-1], {"type": "status", "asset": package, "code": "previewed",
                                                "version": 3, "instances": 2})
        folder = editor.live._state["folder"]
        self.assertEqual(os.listdir(folder), ["live.glb"])   # No half-written file is left beside it.
        for callback in sys.modules["unreal"].shutdown_callbacks:   # The editor quits without a Stop.
            callback()
        self.assertFalse(os.path.exists(folder))

    def test_a_scene_for_another_asset_is_ignored(self):
        native = FakeNative()
        editor = self.load(native)
        self.open(editor)
        editor._on_message({"type": "scene", "asset": "/Game/Other/SM_X", "version": 1}, b"glb")
        self.assertEqual(native.calls, [])

    def test_unreadable_frame_keeps_the_preview_and_says_so(self):
        native = FakeNative(shown=-1)
        editor = self.load(native)
        package = self.open(editor)
        editor._on_message({"type": "scene", "asset": package, "version": 1}, b"bad")
        self.assertEqual(self.client.sent[-1]["code"], "preview_failed")
        self.assertNotIn(("clear",), native.calls)

    def finish_save(self, editor):
        """Run the save-from-tick path as if Interchange were idle and the package saved."""
        subsystem = types.SimpleNamespace(load_asset=lambda _: types.SimpleNamespace(get_name=lambda: "SM_Crate"),
                                          save_loaded_asset=lambda *_: True)
        manager = types.SimpleNamespace(is_interchange_active=lambda: False, is_object_being_imported=lambda _: False)
        unreal = sys.modules["unreal"]
        unreal.get_editor_subsystem = lambda _: subsystem
        unreal.EditorAssetSubsystem = object
        unreal.InterchangeManager = types.SimpleNamespace(get_interchange_manager_scripted=lambda: manager)
        editor._dirty = lambda _: False
        editor._save_when_built()

    def save(self, editor, package, newer_scene=False, covers=None):
        editor.reimport = lambda asset: editor._state.update(saving={"package": asset, "tries": 0}) or True
        header = {"type": "saved", "asset": package}
        if covers is not None:
            header["scene_version"] = covers
        editor._on_message(header, b"")
        if newer_scene:
            editor._on_message({"type": "scene", "asset": package, "version": 9}, b"newer")
        self.finish_save(editor)

    def test_save_reimport_ends_the_preview(self):
        native = FakeNative()
        editor = self.load(native)
        package = self.open(editor)
        editor._on_message({"type": "scene", "asset": package, "version": 1}, b"glb")
        self.save(editor, package)
        self.assertEqual(native.calls[-1], ("clear",))
        self.assertEqual(self.client.sent[-1]["code"], "reimported")

    def test_a_newer_scene_after_the_save_keeps_its_preview(self):
        native = FakeNative()
        editor = self.load(native)
        package = self.open(editor)
        editor._on_message({"type": "scene", "asset": package, "version": 1}, b"glb")
        self.save(editor, package, newer_scene=True)
        self.assertEqual(native.calls[-1][0], "show")
        self.assertEqual(native.count, 1)

    def test_a_scene_sent_while_the_save_was_written_keeps_its_preview(self):
        """Angelo writes a save in the background: edits made meanwhile arrive as scenes before its ``saved``."""
        for covers, cleared in ((1, False), (2, True)):
            with self.subTest(covers=covers):
                native = FakeNative()
                editor = self.load(native)
                package = self.open(editor)
                editor._on_message({"type": "scene", "asset": package, "version": 1}, b"saved state")
                editor._on_message({"type": "scene", "asset": package, "version": 2}, b"edited after the save")
                self.save(editor, package, covers=covers)
                self.assertEqual(native.calls[-1] == ("clear",), cleared)

    def test_ended_and_a_broken_link_end_everything_live(self):
        for how in ("ended", "broken"):
            with self.subTest(how=how):
                native = FakeNative()
                editor = self.load(native)
                package = self.open(editor)
                editor._on_message({"type": "scene", "asset": package, "version": 1}, b"glb")
                if how == "ended":
                    editor._on_message({"type": "ended", "asset": package, "reason": "angelo"}, b"")
                else:
                    self.client.ready = False
                    editor._tick(0.0)
                self.assertEqual((native.count, native.live, editor._state["linked"]), (0, False, ""))


class LivePluginTests(unittest.TestCase):
    def test_descriptor_is_one_editor_module(self):
        descriptor = json.loads((LIVE_PLUGIN / "AngeloLinkLive.uplugin").read_text(encoding="utf-8"))
        self.assertEqual(descriptor["Modules"], [{"Name": "AngeloLinkLive", "Type": "Editor",
                                                  "LoadingPhase": "PostEngineInit"}])
        self.assertFalse(descriptor["EnabledByDefault"])
        content = json.loads((PLUGINS / "AngeloLink/AngeloLink.uplugin").read_text(encoding="utf-8"))
        self.assertNotIn("Modules", content)   # A missing binary never stops the durable layer loading.

    def test_sources_are_mit_and_ours(self):
        files = sorted(path for path in LIVE_PLUGIN.rglob("*") if path.suffix in {".h", ".cpp", ".cs"})
        self.assertGreaterEqual(len(files), 7)
        self.assertIn("MIT License", (LIVE_PLUGIN / "LICENSE.md").read_text(encoding="utf-8"))
        for path in files:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("SPDX-License-Identifier: MIT", text)
                self.assertNotIn("Epic Games, Inc", text)
                self.assertIsNone(re.search(r"/Users/|C:\\\\Users|/home/", text))
                self.assertLessEqual(len(text.splitlines()), 500)

    def test_no_build_output_is_kept(self):
        for folder in ("Binaries", "Intermediate"):
            self.assertFalse((LIVE_PLUGIN / folder).exists())


if __name__ == "__main__":
    unittest.main()
