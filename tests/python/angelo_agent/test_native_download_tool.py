# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_download`` in the agent (doc/angelo/downloads.rst, "The agent"), on a stand-in ``bpy``.

Agent scripts have no network (the maintainer's ruling for 2.0.0: "Deny network to scripts"), so a file from the web
comes through this typed tool. Covered: it is a deferred, approved tool that is not a bounded edit, so its card shows
in Ask and in Auto, never in a read-only chat; outside apps reach it only with the window's card; the card names the
host, the size cap and the folder; the folder is ``assets/downloads`` in the saved file's folder, or in the unsaved
document's own folder in Blender's temporary folder (never the chat's folder, which agent scripts cannot read); the
download runs on a worker thread and its result carries the relative path,
size and SHA-256 and says the scene did not change; agent code cannot call the tool's own functions; the feature
guide sends agents to it instead of a script that fetches.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import angelo_guard_twin  # noqa: E402  C's authority, as the app's (strict: no scope but the one a test opens).

import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import addon_stand_in_import as stand_ins

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


class Scene(dict):
    library = override_library = None


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    bpy.props = types.ModuleType("bpy.props")
    handlers = SimpleNamespace(persistent=lambda function: function, depsgraph_update_post=[], load_post=[],
                               save_post=[], exit_pre=[], undo_post=[], redo_post=[])
    timers = SimpleNamespace(register=lambda *a, **k: None, unregister=lambda *a: None,
                             is_registered=lambda *a: False)
    bpy.app = SimpleNamespace(handlers=handlers, timers=timers, background=True, binary_path="/Apps/Angelo",
                              translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text,
                                                           locale="en_US"))
    bpy.data = SimpleNamespace(filepath="", is_dirty=False, scenes=[Scene()])
    bpy.app.tempdir = tempfile.mkdtemp(prefix="angelo-download-tool-") + "/blender_1/"
    bpy.context = SimpleNamespace(window_manager=SimpleNamespace(windows=[]))
    return bpy


BPY = fake_bpy()
_BEFORE = stand_ins.snapshot()
with mock.patch.dict(sys.modules, {"bpy": BPY, "bpy.types": BPY.types, "bpy.props": BPY.props}):
    from angelo_agent import download_approvals as approvals, download_fetch, download_tool, grant_guard  # noqa
    from angelo_agent import approval_families as routing  # noqa: E402
    from angelo_agent.download_spec import DOWNLOAD_GUIDANCE, MAX_BYTES, NAME  # noqa: E402
    _IMPORTED = stand_ins.imported_since(_BEFORE)
stand_ins.settle(_IMPORTED, BPY)


def controller(workspace):
    return SimpleNamespace(workspace=Path(workspace), state={})


class ContractTests(unittest.TestCase):
    def test_a_deferred_approved_tool_that_asks_in_ask_and_in_auto(self):
        from angelo_agent.controller import Controller
        from angelo_agent.plan_kinds import DEFERRED_TOOLS
        from angelo_agent.tool_specs import BOUNDED_MUTATIONS, TOOL_MAP, dynamic_tools
        self.assertIn(NAME, TOOL_MAP)
        self.assertFalse(TOOL_MAP[NAME]["annotations"]["readOnlyHint"])
        self.assertIn(NAME, DEFERRED_TOOLS)
        self.assertNotIn(NAME, {tool["name"] for tool in dynamic_tools()})
        self.assertNotIn(NAME, BOUNDED_MUTATIONS)
        call = SimpleNamespace(name=NAME, arguments={"url": "https://example.com/a.png", "kind": "image"})
        chat = SimpleNamespace(state={"id": "download-chat"})
        angelo_guard_twin.automatic(angelo_guard_twin.set, "approval_modes", "download-chat", "AUTO_BOUNDED")
        for mode in ("ASK", "AUTO_BOUNDED", "READ_ONLY"):
            with self.subTest(mode=mode):
                self.assertFalse(Controller._auto_bounded(chat, call, mode))
        from angelo_agent import auto_reasons
        self.assertEqual(auto_reasons.bounded_reason(chat, call, "AUTO_BOUNDED"), auto_reasons.OUTSIDE)

    def test_a_blender_without_the_window_never_runs_it(self):
        from angelo_agent import windowless_tools
        self.assertIn(NAME, windowless_tools.WINDOW_ONLY)   # Its card is the window's.
        self.assertNotIn(NAME, windowless_tools.TOOLS)

    def test_the_guide_sends_agents_here_instead_of_a_script_that_fetches(self):
        from angelo_agent import feature_guide
        entries = feature_guide.lookup(["download"])[0]
        text = str(entries)
        self.assertIn(NAME, text)
        self.assertIn("script", text.lower())
        self.assertIn("no network", DOWNLOAD_GUIDANCE.lower())


class CardTests(unittest.TestCase):
    def tearDown(self):
        BPY.data.filepath = ""

    def test_the_card_names_the_host_the_size_and_the_folder(self):
        BPY.data.filepath = "/projects/castle/castle.blend"
        card = approvals.message(NAME, {"url": "https://ambientcg.com/get?file=Bricks_2K.zip", "kind": "texture"})
        self.assertIn("ambientcg.com", card["title"])
        self.assertIn("%d MB" % (MAX_BYTES // (1024 * 1024)), card["body"])
        self.assertIn("assets/downloads", card["body"])
        self.assertIn("project", card["body"].lower())
        self.assertIn("https://ambientcg.com/get?file=Bricks_2K.zip", card["body"])
        for word in ("opened", "run", "imported"):
            self.assertIn(word, card["warning"])

    def test_an_unsaved_file_downloads_into_its_own_temporary_folder_never_the_chats(self):
        from angelo_agent import download_folders
        card = approvals.message(NAME, {"url": "https://example.com/a.wav", "kind": "audio"})
        self.assertIn("temporary folder", card["body"])
        self.assertNotIn("chat", card["body"].lower())
        with tempfile.TemporaryDirectory() as folder:
            root, where = download_tool.destination(controller(folder))
            self.assertEqual(where, "unsaved")
            self.assertEqual(root.parent, download_folders.unsaved_base(BPY).resolve())
            self.assertNotIn(Path(folder).resolve(), root.parents)
            BPY.data.filepath = str(Path(folder) / "saved" / "scene.blend")
            root, where = download_tool.destination(controller(folder))
            self.assertEqual((root, where), (Path(folder) / "saved", "project"))

    def test_the_card_says_when_the_proxy_may_find_the_address_instead_of_angelo(self):
        # The card comes before any network use, so it says when the route is possible; the result says which ran.
        args = {"url": "https://cdn.example.net/a.png", "kind": "image"}
        line = "If this computer can't look up cdn.example.net, your network's proxy finds its address instead, " \
               "and Angelo can't check where it leads."
        for possible in (True, False):
            with self.subTest(possible=possible), \
                    mock.patch.object(download_fetch, "proxy_lookup_possible", return_value=possible):
                card = approvals.message(NAME, args)
                self.assertEqual(line in card["body"], possible)
                with tempfile.TemporaryDirectory() as folder:
                    self.assertEqual(download_tool.precondition(controller(folder), None, NAME, args)["proxy_lookup"],
                                     possible)

    def test_the_cards_chip_says_download_never_scene_changes(self):
        # A download changes no scene; the chip comes from the family's approvals module, and a family without
        # one keeps the scene-change chip. The approvals modules load in Blender; here, on the stand-in bpy.
        with mock.patch.dict(sys.modules, {"bpy": BPY, "bpy.types": BPY.types, "bpy.props": BPY.props}):
            self.assertEqual(routing.approval_kind(NAME), "File download")
            for name in ("angelo_sculpt_program", "angelo_execute_python", "angelo_library_import"):
                self.assertIsNone(routing.approval_kind(name), name)

    def test_a_long_link_is_shortened_on_the_card(self):
        url = "https://example.com/" + "a" * 1500
        self.assertLess(len(approvals.message(NAME, {"url": url, "kind": "other"})["body"]), 600)


class RunTests(unittest.TestCase):
    def test_the_download_runs_on_a_worker_and_reports_its_file(self):
        seen = {}

        def fake(url, kind, root, name, cancel=None, **kwargs):
            import threading
            seen.update(url=url, kind=kind, root=root, name=name, thread=threading.current_thread().name)
            return {"path": "assets/downloads/a.png", "bytes": 10, "sha256": "ab" * 32, "format": "png",
                    "host": "example.com"}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(download_fetch, "download", grant_guard.entry(fake)):
            BPY.data.filepath = str(Path(folder) / "scene.blend")
            pending = download_tool.execute(controller(folder), None, NAME,
                                            {"url": "https://example.com/a.png", "kind": "image", "name": "a"})
            began = time.monotonic()
            result = None
            while result is None and time.monotonic() - began < 5:
                result = pending.poll()
                time.sleep(0.01)
        BPY.data.filepath = ""
        self.assertEqual(result["path"], "assets/downloads/a.png")   # Relative to the project's folder.
        self.assertEqual(result["file"], str(Path(folder) / "assets/downloads/a.png"))
        self.assertEqual(result["folder"], "project")
        self.assertEqual(result["sha256"], "ab" * 32)
        self.assertIs(result["scene_changed"], False)
        self.assertNotEqual(seen["thread"], "MainThread")
        self.assertEqual((seen["root"], seen["name"], seen["kind"]), (Path(folder), "a", "image"))

    def test_an_unsaved_documents_download_gives_its_full_path(self):
        def fake(url, kind, root, name, cancel=None, **kwargs):
            return {"path": "assets/downloads/b.wav", "bytes": 4, "sha256": "cd" * 32, "format": "wav",
                    "host": "example.com", "kind": kind}
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(download_fetch, "download", fake):
            pending = download_tool.execute(controller(folder), None, NAME,
                                            {"url": "https://example.com/b", "kind": "audio"})
            began, result = time.monotonic(), None
            while result is None and time.monotonic() - began < 5:
                result = pending.poll()
                time.sleep(0.01)
        self.assertEqual(result["folder"], "unsaved")
        self.assertTrue(Path(result["path"]).is_absolute())
        self.assertEqual(result["path"], result["file"])
        self.assertTrue(result["file"].endswith(os.path.join("assets", "downloads", "b.wav")))

    def test_a_refusal_is_a_coded_result_and_nothing_changed(self):
        def fake(url, kind, root, name, cancel=None, **kwargs):
            raise download_fetch.Refused("address_private", "VALIDATION", "private address")
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(download_fetch, "download", fake):
            pending = download_tool.execute(controller(folder), None, NAME,
                                            {"url": "https://x.example/a", "kind": "image"})
            began, result = time.monotonic(), None
            while result is None and time.monotonic() - began < 5:
                result = pending.poll()
                time.sleep(0.01)
        self.assertEqual(result["error_code"], "VALIDATION")
        self.assertEqual(result["reason"], "address_private")
        self.assertIs(result["scene_changed"], False)

    def test_agent_code_cannot_call_the_tool_itself(self):
        with tempfile.TemporaryDirectory() as folder:
            for call in (lambda: download_tool.execute(controller(folder), None, NAME,
                                                       {"url": "https://example.com/a.png", "kind": "image"}),
                         lambda: download_tool.precondition(controller(folder), None, NAME,
                                                            {"url": "https://example.com/a.png", "kind": "image"})):
                with grant_guard.agent_script(), self.assertRaises(PermissionError):
                    call()
        entries = {".".join(key.split(".")[-2:]) for key in grant_guard.ENTRIES}   # Short or package-qualified keys.
        for entry in ("download_tool.execute", "download_tool.precondition", "download_fetch.download"):
            self.assertIn(entry, entries)


if __name__ == "__main__":
    unittest.main()
