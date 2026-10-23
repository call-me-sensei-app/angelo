# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Asset Browser library's disk (``studio_assets_space``) and its controls (``studio_library_ops``).

Free up space deletes only the files Angelo named itself in its own folder (``<id>.blend``, a writer's
``<id>.blend.<suffix>.tmp`` and the manifest: never a link's or a junction's target, a file the person put there, the
catalogue file with their catalogues, a sub-folder or ``registration.json``), never while the library is on and never
while a writer holds the folder, on a worker thread; the size counts the same files and a free makes an older measure
stale. A library entry that came back after Turn off is on again. Every control that deletes asks first (source-level: ``bpy`` is a stand-in here). The real files, a
real Turn off and the size Media Studio shows are ``test_blender_studio_assets``'s.
"""

import ast
import os
import sys
import uuid
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import types  # noqa: E402
from unittest import mock  # noqa: E402

from test_native_studio_assets import MODULES as BASE  # noqa: E402  (its stand-in bpy, already imported with the add-on)

MODULES = dict(BASE)   # Plus what the operator module imports.
MODULES["bpy.props"] = types.SimpleNamespace(BoolProperty=lambda **options: None, StringProperty=lambda **options: None)
MODULES["bpy.types"] = types.SimpleNamespace(Operator=object)

with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import studio_assets, studio_assets_space as space  # noqa: E402

ADDON = ROOT / "scripts/addons_core/angelo_agent"


def tree(root):
    """An asset folder as the writer leaves it, plus what is not Angelo's."""
    directory = Path(root) / "library" / "blender-assets"
    (directory / "models").mkdir(parents=True)
    (directory / "images").mkdir()
    ids = [str(uuid.UUID(int=n)) for n in (1, 2, 3)]
    ours = {directory / "models" / (ids[0] + ".blend"): 3000, directory / "models" / (ids[1] + ".blend.1f2e.tmp"): 70,
            directory / "images" / (ids[2] + ".blend"): 500, directory / studio_assets.MANIFEST: 40}
    for path, length in ours.items():
        path.write_bytes(b"x" * length)
    outside = Path(root) / "precious.blend"
    outside.write_bytes(b"p" * 900)
    # Review 5 (B1, S1): the person's own files beside Angelo's, and the catalogue file with their catalogues.
    theirs = [directory / studio_assets.REGISTRATION, directory / "models" / "notes.txt",
              directory / "readme.txt", directory / "models" / "kept" / "inner.blend",
              directory / "models" / "my_tree_edit.blend", directory / "models" / "scene_backup.tmp",
              directory / "images" / "a.blend", directory / studio_assets.CATS_FILE,
              directory / (studio_assets.CATS_FILE + "~")]
    (directory / "models" / "kept").mkdir()
    for path in theirs:
        path.write_bytes(b"t")
    studio_assets.write_registration(directory, removed=True)   # Free up space runs only while the library is off.
    os.symlink(outside, directory / "models" / "link.blend")
    return directory, ours, theirs, outside


class FreeTests(unittest.TestCase):
    def test_only_angelos_files_go_and_the_size_counts_them(self):
        with tempfile.TemporaryDirectory() as root:
            directory, ours, theirs, outside = tree(root)
            self.assertEqual(space.measure(directory), sum(ours.values()))
            freed, count = space.free(directory, active=False)
            self.assertEqual((freed, count), (sum(ours.values()), len(ours)))
            self.assertFalse(any(path.exists() for path in ours))
            self.assertTrue(all(path.exists() for path in theirs), "only what Angelo wrote")
            self.assertTrue((directory / "models" / "link.blend").is_symlink())
            self.assertEqual(outside.read_bytes(), b"p" * 900, "a link's target is never touched")
            self.assertTrue((directory / "images").exists(), "a folder holding the person's file stays")
            self.assertTrue((directory / "models").exists(), "a folder holding anything else stays")
            self.assertEqual(space.measure(directory), 0)

    def test_never_while_on_or_while_a_writer_holds_the_folder(self):
        with tempfile.TemporaryDirectory() as root:
            directory, ours, _theirs, _outside = tree(root)
            with self.assertRaises(ValueError):
                space.free(directory, active=True)
            held, release = threading.Event(), threading.Event()

            def writer():
                with studio_assets.sync_lock(directory) as got:
                    self.assertTrue(got)
                    held.set()
                    release.wait(5)
            thread = threading.Thread(target=writer)
            thread.start()
            held.wait(5)
            try:
                self.assertIsNone(space.free(directory, active=False))
            finally:
                release.set()
                thread.join()
            self.assertTrue(all(path.exists() for path in ours))
            self.assertIsNotNone(space.free(directory, active=False))

    def test_a_linked_folder_is_not_walked(self):
        with tempfile.TemporaryDirectory() as root:
            elsewhere = Path(root) / "elsewhere"
            elsewhere.mkdir()
            (elsewhere / "someone.blend").write_bytes(b"s")
            directory = Path(root) / "blender-assets"
            directory.mkdir()
            os.symlink(elsewhere, directory / "models")
            self.assertEqual(space.angelo_files(directory), [])
            space.free(directory, active=False)
            self.assertTrue((elsewhere / "someone.blend").exists())

    def test_angelos_names_only(self):
        name = "8f0c2a4e-9b1d-4c3e-a5f6-0d7e8f9a1b2c"
        for ok in (name + ".blend", name + ".blend.1f2e.tmp"):
            self.assertTrue(space.angelo_name(ok), ok)
        for other in ("a.blend", "my_tree_edit.blend", "scene_backup.tmp", name + ".blend1", name + ".blend.tmp",
                      name.upper() + ".blend", name + ".txt"):
            self.assertFalse(space.angelo_name(other), other)

    def test_blenders_save_temporaries_of_angelos_files_only(self):
        """A writer stopped mid-save leaves Blender's own temporary, ``<file>@``: Angelo's when the file it was saving
        is (``<id>.blend@``, ``<id>.blend.<suffix>.tmp@``); one ``@`` only, and never a person's file or backup."""
        name = "8f0c2a4e-9b1d-4c3e-a5f6-0d7e8f9a1b2c"
        for ok in (name + ".blend@", name + ".blend.1f2e.tmp@"):
            self.assertTrue(space.angelo_name(ok), ok)
        for other in (name + ".blend@@", name + ".blend.1f2e.tmp@@", name + ".blend1@", name + ".blend.tmp@",
                      name + ".blend.@", name + "@.blend", "my_tree_edit.blend@", "my_tree_edit.blend",
                      name.upper() + ".blend@", "@"):
            self.assertFalse(space.angelo_name(other), other)
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "blender-assets"
            models = directory / "models"
            models.mkdir(parents=True)
            ours = [models / (name + ".blend@"), models / (name + ".blend.1f2e.tmp@")]
            theirs = [models / "my_tree_edit.blend@", models / (name + ".blend@@")]
            for path in ours + theirs:
                path.write_bytes(b"x")
            self.assertEqual(sorted(space.angelo_files(directory)), sorted(ours))

    def test_a_junction_or_linked_folder_is_never_entered(self):
        """Review 5 (M2): ``is_symlink`` misses Windows junctions; the folder itself counts too."""
        with tempfile.TemporaryDirectory() as root:
            directory, ours, _theirs, _outside = tree(root)
            real = Path.is_junction if hasattr(Path, "is_junction") else None
            with mock.patch.object(Path, "is_junction", lambda path: path == directory, create=True):
                self.assertEqual(space.angelo_files(directory), [])
            with mock.patch.object(Path, "is_junction", lambda path: path == directory / "models", create=True):
                self.assertEqual(sorted(space.angelo_files(directory)),
                                 sorted(p for p in ours if p.parent != directory / "models"))
            self.assertIs(getattr(Path, "is_junction", None), real)

    def test_the_free_runs_on_a_worker_and_waits_for_the_writer(self):
        """Review 5 (M1): the main thread never waits; a writer finishing within the wait is waited for."""
        with tempfile.TemporaryDirectory() as root:
            directory, ours, _theirs, _outside = tree(root)
            held, release, results = threading.Event(), threading.Event(), []

            def writer():
                with studio_assets.sync_lock(directory):
                    held.set()
                    release.wait(5)
            thread = threading.Thread(target=writer)
            thread.start()
            held.wait(5)
            worker = space.free_later(directory, False, results.append, wait=3.0)
            self.assertTrue(worker.is_alive(), "returned at once, the worker waits")
            release.set()
            thread.join()
            worker.join(5)
            self.assertEqual(results[0][1], len(ours))
            with self.assertRaises(ValueError):
                space.free_later(directory, True, results.append)
            held.clear()
            release.clear()
            thread = threading.Thread(target=writer)
            thread.start()
            held.wait(5)
            space.free_later(directory, False, results.append, wait=0.3).join(5)
            release.set()
            thread.join()
            self.assertIsNone(results[1], "still busy after the wait: None, reported as busy")

    def test_a_free_makes_an_older_size_stale(self):
        """Review 5 (S5): a measure that started before a free never shows its old total afterwards."""
        with tempfile.TemporaryDirectory() as root:
            directory, _ours, _theirs, _outside = tree(root)
            go = threading.Event()
            measure = space.measure

            def slow(path):
                go.wait(5)
                return measure(path)
            space.forget_size()
            with mock.patch.object(space, "measure", slow):
                self.assertIsNone(space.size(directory))
                worker = space._size["worker"]
                space.free(directory, active=False)
                go.set()
                worker.join(5)
            self.assertIsNone(space._size["bytes"], "the older total was dropped")

    def test_turned_on_again_before_it_holds_the_folder_frees_nothing(self):
        """Review 6 (4): the registration is read again under the writer's lock."""
        with tempfile.TemporaryDirectory() as root:
            directory, ours, _theirs, _outside = tree(root)
            studio_assets.write_registration(directory, removed=False)   # Turned on (here, or by another Angelo).
            self.assertEqual(space.free(directory, active=False), space.ON)
            self.assertTrue(all(path.exists() for path in ours))

    def test_readable_sizes(self):
        self.assertEqual(space.readable(1_982_500_000), ("{value} GB", "1.8"))
        self.assertEqual(space.readable(110_700_000), ("{value} MB", "106"))
        self.assertEqual(space.readable(2048), ("{value} KB", "2.0"))
        self.assertEqual(space.readable(12), ("{value} bytes", "12"))


class TurnOffTests(unittest.TestCase):
    """Turn off is a removal (recorded, the writer stops at once) that never asks twice; a removal the person made in
    Preferences asks whether to free the space; Turn on resumes."""

    def setUp(self):
        with mock.patch.dict(sys.modules, MODULES):
            from angelo_agent import studio_assets_host
        self.host = studio_assets_host

    def libraries(self, directory):
        class Libraries(list):
            def new(self, name, directory):
                entry = SimpleNamespace(name=name, path=directory, import_method='PACK')
                self.append(entry)
                return entry
        return Libraries([SimpleNamespace(name="Angelo Media Studio", path=str(directory))])

    def test_turn_off_records_stops_and_does_not_ask(self):
        host, bpy = self.host, MODULES["bpy"]
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "blender-assets"
            directory.mkdir()
            libraries = self.libraries(directory)
            bpy.timers.clear()
            with mock.patch.dict(host._state, registered=True, active=True), \
                    mock.patch.object(host, "directories", return_value=(Path(root), directory)), \
                    mock.patch.object(host, "_libraries", return_value=libraries), \
                    mock.patch.dict(sys.modules, MODULES):
                host.turn_off()
                self.assertEqual((host._state["registered"], host._state["active"]), (False, False))
                self.assertEqual(list(libraries), [])
                self.assertTrue(studio_assets.read_registration(directory)["removed"])
                self.assertFalse(host.observe())
                self.assertEqual(bpy.timers, [], "Media Studio's own Turn off asked in its dialog already")
                host.turn_on()
                self.assertTrue(host._state["registered"])
                self.assertEqual(len(libraries), 1)
                self.assertFalse(studio_assets.read_registration(directory)["removed"])

    def test_a_removal_in_preferences_asks_whether_to_free_the_space(self):
        host, bpy = self.host, MODULES["bpy"]
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "blender-assets"
            directory.mkdir()
            bpy.timers.clear()
            with mock.patch.dict(host._state, registered=True, reread=False), \
                    mock.patch.object(host, "directories", return_value=(Path(root), directory)), \
                    mock.patch.object(host, "_libraries", return_value=[]), \
                    mock.patch.dict(sys.modules, MODULES):
                self.assertFalse(host.observe())
            from angelo_agent import studio_library_ops
            self.assertEqual([t[0] for t in bpy.timers], [studio_library_ops.ask_free])


class ComesBackTests(unittest.TestCase):
    def test_an_entry_back_after_turn_off_is_on_again(self):
        """Review 5 (S4): Turn off, then a crash or another Angelo saving its preferences brings the entry back: it is
        on, so the writer writes again (never "On" but idle)."""
        with mock.patch.dict(sys.modules, MODULES):
            from angelo_agent import studio_assets_host as host
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "blender-assets"
            directory.mkdir()
            studio_assets.write_registration(directory, removed=True)
            libraries = [SimpleNamespace(name="Angelo Media Studio", path=str(directory))]
            with mock.patch.object(host, "directories", return_value=(Path(root), directory)), \
                    mock.patch.object(host, "_libraries", return_value=libraries):
                self.assertTrue(host.ensure_registered())
            self.assertFalse(studio_assets.read_registration(directory)["removed"])


class ConfirmationTests(unittest.TestCase):
    """Every control that deletes asks first, in a centred Angelo card (``studio_library_ops.ask``, 1.0.5 review L1:
    no stock popup); Media Studio's buttons invoke them."""

    def operator(self, name):
        source = ast.parse((ADDON / "studio_library_ops.py").read_text())
        return next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == name)

    def invoke_calls(self, cls):
        invoke = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "invoke")
        return {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(invoke) if isinstance(n, ast.Call)}

    def test_deleting_controls_confirm(self):
        for name in ("ANGELO_OT_studio_library_off", "ANGELO_OT_studio_library_free"):
            self.assertIn("ask", self.invoke_calls(self.operator(name)), name)   # The card asks; execute deletes.
            execute = next(n for n in self.operator(name).body if isinstance(n, ast.FunctionDef) and n.name == "execute")
            self.assertNotIn("ask(", ast.unparse(execute), "confirmed only through the card, never skipped")

    def test_nothing_is_deleted_without_the_cards_confirm(self):
        """Review 5 (S2), review 6 (3): only a one-shot token the card's confirm issues deletes; an EXEC call alone,
        from a script or an agent, frees nothing."""
        off = ast.unparse(next(n for n in self.operator("ANGELO_OT_studio_library_off").body
                               if isinstance(n, ast.FunctionDef) and n.name == "execute"))
        self.assertIn("if self.free and consume_token(self.token):", off)
        free = ast.unparse(next(n for n in self.operator("ANGELO_OT_studio_library_free").body
                                if isinstance(n, ast.FunctionDef) and n.name == "execute"))
        self.assertIn("if not consume_token(self.token):", free)
        self.assertLess(free.index("if not consume_token(self.token):"), free.index("start_free()"))
        ops = (ADDON / "studio_library_ops.py").read_text()
        act = ops.split("    def act(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("free=self.free, token=issue_token()", act)
        self.assertIn("studio_library_free('EXEC_DEFAULT', token=issue_token())", act)
        self.assertEqual(ops.count("=issue_token()"), 2, "nothing else issues one")
        self.assertNotIn("confirmed", ops, "no flag a script could set")

    def test_linked_models_untick_also_delete(self):
        """Review 5 (S3): when this file links from the library or it imports by linking, Also delete starts off."""
        ops = (ADDON / "studio_library_ops.py").read_text()
        self.assertIn("self.free = what, area_pointer, not self.linked", ops)
        links = ops.split("def links_from_library(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("bpy.data.libraries", links)
        self.assertIn("import_method", links)
        self.assertNotIn("time.sleep", ops, "never waits on the main thread")

    def test_free_is_unavailable_while_the_library_is_on(self):
        poll = next(n for n in self.operator("ANGELO_OT_studio_library_free").body
                    if isinstance(n, ast.FunctionDef) and n.name == "poll")
        self.assertIn("active", ast.unparse(poll))

    def test_media_studio_and_the_folder_picker_invoke_them(self):
        studio = (ADDON / "studio.py").read_text()
        studio_input = (ADDON / "studio_input.py").read_text()
        self.assertIn("LIBRARY_ACTIONS[action])('INVOKE_DEFAULT')", studio)
        self.assertIn("LIBRARY_ACTIONS[choice])('INVOKE_DEFAULT')", studio_input)
        landing = (ADDON / "studio_assets_host.py").read_text()
        self.assertIn("studio_library_ops.ask_free", landing, "a removal in Preferences asks too")
        ops = (ADDON / "studio_library_ops.py").read_text()
        self.assertIn("bpy.ops.angelo.studio_library_free('INVOKE_DEFAULT')", ops)


if __name__ == "__main__":
    unittest.main()
