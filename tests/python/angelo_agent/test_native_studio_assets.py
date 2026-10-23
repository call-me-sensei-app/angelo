# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio as a Blender asset library: the plan, the registration rules and the startup path, on a stand-in ``bpy``.

``studio_assets`` decides which asset files are missing, stale or orphaned and never deletes a file it did not name;
``studio_assets_host`` does nothing at registration beyond a timer, starts no scan before ``SYNC_DELAY``, runs the
writer in batches with lowered priority and stops it with the add-on; a preference read is not a removal
(``prefs_reload.READ_LISTENERS``). The real writer and Preferences entry are ``test_blender_studio_assets``'s.
"""

import json
import os
import stat
import sys
import tempfile
import textwrap
import threading
import types
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    timers = []
    bpy.app = SimpleNamespace(background=False, binary_path="/nonexistent/Angelo", timers=SimpleNamespace(
        register=lambda f, first_interval=0, persistent=False: timers.append((f, first_interval)),
        is_registered=lambda f: any(t[0] is f for t in timers),
        unregister=lambda f: timers[:] and timers.remove(next(t for t in timers if t[0] is f))),
        handlers=SimpleNamespace(load_factory_preferences_post=[], depsgraph_update_post=[], save_pre=[],
                                 load_post=[], blend_import_pre=[], blend_import_post=[], persistent=lambda f: f),
        translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text))
    bpy.timers = timers
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda f: f
    return {"bpy": bpy, "bpy.app": bpy.app, "bpy.app.handlers": handlers}


MODULES = fake_bpy()
with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import prefs_reload, studio_assets, studio_assets_host, studio_assets_landing
    from angelo_agent.studio_state import StudioLibrary


class Library:
    """A Studio library on disk with ready records."""

    def __init__(self, root):
        self.root = Path(root) / "media-studio"
        self.studio = StudioLibrary(self.root)

    def model(self, prompt="A stone column", glb=b"glTF-fixture", thumbnail=None, status="ready"):
        record = self.studio.add("3D", prompt, "Tripo H3.1", status=status)
        (self.studio.result_directory(record["id"]) / "model.glb").write_bytes(glb)
        files = ["model.glb"]
        if thumbnail:
            (self.studio.result_directory(record["id"]) / "preview.jpg").write_bytes(thumbnail)
        record.update(files=files, thumbnail="preview.jpg" if thumbnail else "")
        self.studio.update(record)
        return record

    def image(self, prompt="A poster", count=1):
        record = self.studio.add("IMAGE", prompt, "Nano Banana 2.1", status="ready")
        names = [f"image-{n + 1}.jpg" for n in range(count)]
        for name in names:
            (self.studio.result_directory(record["id"]) / name).write_bytes(b"\xff\xd8 fixture")
        record.update(files=names, thumbnail=names[0])
        self.studio.update(record)
        return record

    def plan(self, manifest=None, session="s"):
        return studio_assets.plan(self.root, self.studio.assets.values(), self.studio.folders, manifest or {}, session)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.library = Library(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_ready_items_are_planned_with_their_catalogue_and_metadata(self):
        model, image = self.library.model(), self.library.image(count=2)
        convert, remove = self.library.plan()
        found = {item["id"]: item for item in convert}
        self.assertEqual(remove, [])
        self.assertEqual(found[model["id"]]["catalog_id"], studio_assets.CATALOGS["3D"][0])
        self.assertTrue(found[model["id"]]["blend"].endswith(f"blender-assets/models/{model['id']}.blend"))
        self.assertEqual(len(found[image["id"]]["files"]), 2)
        self.assertEqual(found[image["id"]]["description"], "A poster")
        self.assertIn("Nano Banana 2.1", found[image["id"]]["tags"])

    def test_items_with_nothing_to_list_are_skipped(self):
        self.library.model(status="generating")
        broken = self.library.model()
        (self.library.root / "outputs" / broken["id"] / "model.glb").unlink()
        linked = self.library.image()
        target = self.library.root / "outputs" / linked["id"] / "image-1.jpg"
        target.unlink()
        target.symlink_to(Path(self.temporary.name) / "elsewhere.jpg")
        (Path(self.temporary.name) / "elsewhere.jpg").write_bytes(b"x")
        self.assertEqual(self.library.plan(), ([], []))

    def test_unchanged_items_are_not_rewritten_and_changes_are(self):
        model = self.library.model()
        convert, _remove = self.library.plan()
        item = convert[0]
        Path(item["blend"]).parent.mkdir(parents=True)
        Path(item["blend"]).write_bytes(b"BLENDER")
        manifest = {}
        studio_assets.apply_results(manifest, convert, {model["id"]: {"ok": True}})
        self.assertEqual(self.library.plan(manifest), ([], []))
        folder = self.library.studio.folder("Props")
        model["folder"] = folder["id"]
        self.library.studio.update(model)
        self.assertEqual([i["id"] for i in self.library.plan(manifest)[0]], [model["id"]])
        model["folder"] = ""
        self.library.studio.update(model)
        (self.library.root / "outputs" / model["id"] / "model.glb").write_bytes(b"glTF-another-model")
        self.assertEqual(len(self.library.plan(manifest)[0]), 1, "a replaced file is a change")
        Path(item["blend"]).unlink()
        self.assertEqual(len(self.library.plan(manifest)[0]), 1, "a missing file is written again")

    def test_a_failure_waits_for_another_session_or_a_change(self):
        model = self.library.model()
        convert, _ = self.library.plan()
        manifest = {}
        studio_assets.apply_results(manifest, convert, {}, session="s")
        self.assertTrue(manifest[model["id"]]["failed"])
        self.assertEqual(self.library.plan(manifest, "s"), ([], []))
        self.assertEqual(len(self.library.plan(manifest, "next")[0]), 1)

    def test_a_removed_record_removes_only_the_file_this_folder_named(self):
        model = self.library.model()
        convert, _ = self.library.plan()
        blend = Path(convert[0]["blend"])
        blend.parent.mkdir(parents=True)
        blend.write_bytes(b"BLENDER")
        keep = blend.parent / "the-persons-own.blend"
        keep.write_bytes(b"BLENDER")
        outside = Path(self.temporary.name) / "outside.blend"
        outside.write_bytes(b"BLENDER")
        manifest = {model["id"]: {"fingerprint": convert[0]["fingerprint"]}, "../../outside": {}, "x": {}}
        (self.library.root / "assets" / (model["id"] + ".json")).unlink()
        self.library.studio.reload()
        _convert, remove = self.library.plan(manifest)
        self.assertEqual(sorted(remove), sorted([model["id"], "../../outside", "x"]))
        studio_assets.remove_orphans(self.library.root, manifest, remove)
        self.assertFalse(blend.exists())
        self.assertTrue(keep.exists() and outside.exists())
        self.assertNotIn(model["id"], manifest)

    def test_a_failed_rewrite_is_retried_next_session_even_with_the_old_file_there(self):
        """Review 4 (1): a failed v3 rewrite left the v2 file; the next session tries it again, this one does not."""
        model = self.library.model()
        convert, _ = self.library.plan()
        blend = Path(convert[0]["blend"])
        blend.parent.mkdir(parents=True)
        blend.write_bytes(b"BLENDER older converter")
        manifest = {}
        studio_assets.apply_results(manifest, convert, {}, session="earlier")
        self.assertTrue(manifest[model["id"]]["failed"])
        self.assertEqual(self.library.plan(manifest, "earlier"), ([], []), "Not again in its own session")
        self.assertEqual([i["id"] for i in self.library.plan(manifest, "next")[0]], [model["id"]])
        manifest[model["id"]] = {"fingerprint": convert[0]["fingerprint"], "blend": blend.name}
        self.assertEqual(self.library.plan(manifest, "next"), ([], []), "A success with its file there is skipped")

    def copy_of(self, record):
        """A written copy for ``record`` and the manifest that names it."""
        found = studio_assets.item(self.library.root, record, {})
        blend = Path(found["blend"])
        blend.parent.mkdir(parents=True, exist_ok=True)
        blend.write_bytes(b"BLENDER")
        return blend, {record["id"]: {"fingerprint": found["fingerprint"], "blend": blend.name}}

    def test_a_copy_goes_only_when_its_item_is_deleted(self):
        """Review 4 (4): an item left out for any reason but its deletion keeps its copy (a linked scene needs it)."""
        records = self.library.root / "assets"
        cases = {}
        for case in ("unreadable", "too_large", "not_ready", "glb_missing", "past_the_cap"):
            record = self.library.model(case)
            cases[case] = (record, *self.copy_of(record))
        (records / (cases["unreadable"][0]["id"] + ".json")).write_text("{ not json", encoding="utf-8")
        (records / (cases["too_large"][0]["id"] + ".json")).write_text(
            json.dumps(dict(cases["too_large"][0], prompt="x" * (1024 * 1024))), encoding="utf-8")
        not_ready = dict(cases["not_ready"][0], status="generating")
        (records / (not_ready["id"] + ".json")).write_text(json.dumps(not_ready), encoding="utf-8")
        (self.library.root / "outputs" / cases["glb_missing"][0]["id"] / "model.glb").unlink()
        self.library.studio.reload()
        manifest = {}
        for _record, _blend, named in cases.values():
            manifest.update(named)
        listed = [r for r in self.library.studio.assets.values() if r["id"] != cases["past_the_cap"][0]["id"]]
        _convert, remove = studio_assets.plan(self.library.root, listed, {}, manifest, "s")   # As past the cap.
        self.assertEqual(remove, [], "Nothing deleted while a record is unreadable or merely left out")
        for case in ("unreadable", "too_large"):   # Repaired: a left-out item still keeps its copy.
            (records / (cases[case][0]["id"] + ".json")).write_text(json.dumps(cases[case][0]), encoding="utf-8")
        self.library.studio.reload()
        _convert, remove = studio_assets.plan(self.library.root, listed, {}, manifest, "s")
        self.assertEqual(remove, [], "Not ready, model missing, past the cap: kept")
        (records / (cases["not_ready"][0]["id"] + ".json")).unlink()   # The person deleted it in Media Studio.
        self.library.studio.reload()
        _convert, remove = studio_assets.plan(self.library.root, self.library.studio.assets.values(), {}, manifest,
                                              "s")
        self.assertEqual(remove, [cases["not_ready"][0]["id"]])

    def test_a_record_that_blocks_removals_is_said_once(self):
        """Review 6 (5): a damaged record keeps every copy; the log says which, once."""
        record = self.library.model()
        _blend, manifest = self.copy_of(record)
        damaged = self.library.model("damaged")
        (self.library.root / "assets" / (damaged["id"] + ".json")).write_text("{ broken", encoding="utf-8")
        (self.library.root / "assets" / (record["id"] + ".json")).unlink()
        studio_assets._blocking_logged.discard(damaged["id"] + ".json")
        printed = []
        with mock.patch("builtins.print", lambda *a, **k: printed.append(" ".join(map(str, a)))):
            for _ in range(2):
                self.assertEqual(studio_assets.removable(self.library.root, [record["id"]]), [])
        self.assertEqual(len([p for p in printed if damaged["id"] in p]), 1)

    def test_nothing_is_deleted_while_the_records_folder_is_unreachable(self):
        """Review 4 (4): an unmounted or moved library reads as no records: that is no deletion."""
        record = self.library.model()
        _blend, manifest = self.copy_of(record)
        root = self.library.root / "assets"
        root.rename(self.library.root / "assets-away")
        self.assertEqual(studio_assets.plan(self.library.root, [], {}, manifest, "s"), ([], []))
        self.assertEqual(studio_assets.removable(self.library.root, ["not-an-id"]), ["not-an-id"],
                         "A key that is no item id is only forgotten (no file)")

    def test_a_large_manifest_reads_back_whole(self):
        manifest = {str(uuid.uuid4()): {"fingerprint": "f" * 64, "blend": "x.blend", "failed": True,
                                        "error": "e" * 200} for _ in range(5000)}
        with tempfile.TemporaryDirectory() as directory:
            studio_assets.write_manifest(directory, manifest)
            self.assertGreater((Path(directory) / studio_assets.MANIFEST).stat().st_size, 1024 * 1024)
            self.assertEqual(studio_assets.read_manifest(directory), manifest)

    def test_one_damaged_record_never_stops_the_rest(self):
        good = self.library.model("A good model")
        bad = dict(self.library.model("placeholder"), prompt="bad \ud800 prompt")   # As another writer could leave it.
        odd = dict(self.library.model("odd"), folder=["not", "a", "folder", "id"])
        convert, _ = studio_assets.plan(self.library.root, [good, bad, odd], self.library.studio.folders, {}, "s")
        self.assertIn(good["id"], [item["id"] for item in convert])
        self.assertIn(bad["id"], [item["id"] for item in convert], "any text has a fingerprint")

    def test_ids_that_are_no_item_are_forgotten_and_nothing_is_a_change(self):
        manifest = {"not-a-uuid": {}, str(uuid.uuid4()): {}}
        self.assertFalse(studio_assets.remove_orphans(self.library.root, manifest, list(manifest)))
        self.assertEqual(manifest, {})

    def test_names_are_the_prompt_shortened_never_translated(self):
        self.assertEqual(studio_assets.asset_name("  A  red\nchair  "), "A red chair")
        long = studio_assets.asset_name("x" * 200)
        self.assertEqual(len(long), 60)
        self.assertTrue(long.endswith("…"))


class CatalogueTests(unittest.TestCase):
    def test_names_follow_the_language_and_identities_stay(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(studio_assets.write_catalogs(directory, {"3D": "Models", "IMAGE": "Images"}))
            self.assertFalse(studio_assets.write_catalogs(directory, {"3D": "Models", "IMAGE": "Images"}))
            self.assertTrue(studio_assets.write_catalogs(directory, {"3D": "Modelle: 3D/neu", "IMAGE": "Bilder"}))
            text = (Path(directory) / studio_assets.CATS_FILE).read_text(encoding="utf-8")
        self.assertIn("VERSION 1", text)
        self.assertIn(f"{studio_assets.PARENT_CATALOG}:Angelo Media Studio:Angelo Media Studio", text)
        self.assertIn(f"{studio_assets.CATALOGS['3D'][0]}:Angelo Media Studio/Modelle 3D neu:Modelle 3D neu", text)
        self.assertIn(f"{studio_assets.CATALOGS['IMAGE'][0]}:Angelo Media Studio/Bilder:Bilder", text)

    def test_the_tab_path_is_fixed_and_kinds_are_found_in_any_language(self):
        self.assertEqual(studio_assets.TAB_PATH, "Angelo Media Studio")
        german = {"3D": "Modelle", "IMAGE": "Bilder"}
        self.assertEqual(studio_assets.catalog_kind("Angelo Media Studio/Modelle", german), "3d")
        self.assertEqual(studio_assets.catalog_kind("Angelo Media Studio/Bilder", german), "image")
        self.assertEqual(studio_assets.catalog_kind("Angelo Media Studio/Images", german), "image", "English stays")
        self.assertEqual(studio_assets.catalog_kind("Angelo Media Studio/Bilder/Himmel", german), "image",
                         "a catalogue the person made under one")
        self.assertEqual(studio_assets.catalog_kind("Angelo Media Studio/Models/Hero props", german), "3d")
        for other in ("Angelo Media Studio", "Models", "Props/Models", "Angelo Media Studio/Hero",
                      "Angelo Media Studio/Modelle2", "Angelo Media Studio/ImagesX/Skies"):
            self.assertIsNone(studio_assets.catalog_kind(other, german), other)

    def test_catalogues_the_person_adds_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            studio_assets.write_catalogs(directory, {})
            path = Path(directory) / studio_assets.CATS_FILE
            mine = "0b0b0b0b-0000-4000-8000-000000000001:Models/Hero props:Models-Hero props"
            path.write_text(path.read_text(encoding="utf-8") + mine + "\n", encoding="utf-8")
            self.assertFalse(studio_assets.write_catalogs(directory, {}))
            self.assertTrue(studio_assets.write_catalogs(directory, {"3D": "Modelle"}))
            text = path.read_text(encoding="utf-8")
        self.assertIn(mine, text)
        self.assertEqual(text.count("VERSION 1"), 1)


class RegistrationTests(unittest.TestCase):
    def test_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            ours = Path(directory) / "blender-assets"
            ours.mkdir()
            other = SimpleNamespace(name="Angelo Media Studio", path=directory)
            renamed = SimpleNamespace(name="My models", path=str(ours) + os.sep)
            decide = studio_assets.registration_decision
            self.assertEqual(decide([other], ours, {}), "add", "a same-named library elsewhere is the person's")
            self.assertEqual(decide([other, renamed], ours, {}), "present")
            self.assertEqual(decide([other], ours, {"removed": True, "directory": str(ours)}), "removed")
            self.assertEqual(decide([other], ours, {"removed": True, "directory": "/elsewhere"}), "add")

    def test_paths_are_compared_by_their_text_alone(self):
        with mock.patch.object(studio_assets.os.path, "realpath", side_effect=AssertionError("file system")), \
                mock.patch.object(Path, "stat", side_effect=AssertionError("file system")):
            self.assertTrue(studio_assets.same_directory("/Volumes/share/lib/", "/Volumes/share/lib"))
            self.assertTrue(studio_assets.same_directory("/a/b/../lib", "/a/lib"))
            self.assertFalse(studio_assets.same_directory("/a/lib", "/a/lib2"))
            entry = SimpleNamespace(name="Studio NAS", path="/Volumes/unreachable/lib")
            self.assertEqual(studio_assets.registration_decision([entry], "/Volumes/unreachable/lib", {}), "present")

    def test_a_preference_read_is_announced(self):
        calls = []
        listener = lambda: calls.append(1)   # noqa: E731
        failing = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.object(prefs_reload, "_installed", False), \
                mock.patch.object(prefs_reload, "_blender", (None, lambda **options: calls.append("read"))), \
                mock.patch.object(prefs_reload, "READ_LISTENERS", [failing, listener]):
            prefs_reload._reset_all()
        self.assertEqual(calls, ["read", 1])


class StartupPathTests(unittest.TestCase):
    def test_registration_adds_the_entry_and_a_timer_and_scans_nothing(self):
        bpy = MODULES["bpy"]
        bpy.timers.clear()
        check = mock.Mock(return_value=True)
        with mock.patch.dict(sys.modules, MODULES), \
                mock.patch.object(studio_assets_host, "ensure_registered", check), \
                mock.patch.object(studio_assets_host, "sync", side_effect=AssertionError("startup path")), \
                mock.patch.object(studio_assets_host.threading, "Thread", side_effect=AssertionError("startup path")):
            studio_assets_host.register()
            # The module register() imported (``from . import prefs_reload``): that import sets it on the package
            # it resolved from sys.modules, whether or not an earlier test left it in sys.modules too (in a full run
            # the submodule can come from the package attribute alone), so read it where register() did.
            listeners = getattr(sys.modules[studio_assets_host.__package__], "prefs_reload").READ_LISTENERS
            try:
                check.assert_called_once_with()   # Before any shelf draws: a library added later crashed one.
                self.assertTrue(studio_assets_host._state["registered"])
                self.assertEqual([(t[0].__name__, t[1]) for t in bpy.timers], [("_tick", studio_assets_host.FIRST)])
                self.assertIn(studio_assets_host.preferences_read, listeners)
                self.assertIn(studio_assets_host._imported, bpy.app.handlers.blend_import_post)
                self.assertIn(studio_assets_host._before_save, bpy.app.handlers.save_pre)
            finally:
                studio_assets_host.unregister()
            self.assertNotIn(studio_assets_host.preferences_read, listeners)
            self.assertEqual(bpy.app.handlers.blend_import_post, [])
            self.assertEqual(bpy.app.handlers.depsgraph_update_post, [], "no per-update cost")

    def test_a_failing_check_never_fails_the_add_on(self):
        bpy = MODULES["bpy"]
        bpy.timers.clear()
        with mock.patch.dict(sys.modules, MODULES), \
                mock.patch.object(studio_assets_host, "ensure_registered", side_effect=OSError("read-only")):
            studio_assets_host.register()
            try:
                self.assertIsNone(studio_assets_host._state["registered"], "the first tick tries again")
            finally:
                studio_assets_host.unregister()

    def test_no_scan_before_the_delay_and_none_in_the_background(self):
        state = studio_assets_host._state
        with mock.patch.dict(state, registered=True, since=studio_assets_host.time.monotonic(), worker=None,
                             changed=False, reread=False), \
                mock.patch.object(studio_assets_host, "observe", return_value=True), \
                mock.patch.object(studio_assets_host.threading, "Thread") as thread:
            self.assertEqual(studio_assets_host._tick(), studio_assets_host.TICK)
            thread.assert_not_called()
        bpy = MODULES["bpy"]
        bpy.timers.clear()
        with mock.patch.object(bpy.app, "background", True):
            studio_assets_host.register()
        self.assertEqual(bpy.timers, [])


class LandingTests(unittest.TestCase):
    """A picture that arrives with a Media Studio material is packed into the person's file, once; then it is theirs."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.source = self.folder / "image-1.png"
        self.source.write_bytes(b"\x89PNG Studio's picture")

    class Image:
        def __init__(self, test, name, identity=None, packed_id=None, packed=None, library=None, missing=False,
                     dirty=False):
            self.name, self.filepath, self.library, self.missing = name, str(test.source), library, missing
            self.packed_file = SimpleNamespace(data=packed) if packed is not None else None
            self.props = {}
            if identity:
                self.props["angelo_studio_asset"] = identity
            if packed_id:
                self.props["angelo_studio_packed"] = packed_id
            self.is_dirty, self.remapped_to, self.pointer = dirty, None, id(self)
            self.colorspace_settings, self.alpha_mode, self.source = SimpleNamespace(name="sRGB"), "STRAIGHT", "FILE"

        def get(self, key):
            return self.props.get(key)

        def __setitem__(self, key, value):
            self.props[key] = value

        def __delitem__(self, key):
            del self.props[key]

        def as_pointer(self):
            return self.pointer

        def pack(self):
            if self.missing:
                raise RuntimeError("missing")
            self.packed_file = SimpleNamespace(data=Path(self.filepath).read_bytes())

        def user_remap(self, other):
            self.remapped_to = other

    def run_pack(self, images, arrived=None):
        class Images(list):
            def remove(self, image):
                list.remove(self, image)
        bpy = MODULES["bpy"]
        bpy.data = SimpleNamespace(images=Images(images), objects=[])
        bpy.path = SimpleNamespace(abspath=lambda path: path)
        with mock.patch.dict(sys.modules, MODULES):
            packed = studio_assets_landing.pack_landed_images(arrived)
        return packed, bpy.data.images

    def test_landed_pictures_are_packed_once_and_others_left_alone(self):
        first, second = self.Image(self, "image-1.png", "a"), self.Image(self, "image-1.png.001", "a")
        mine = self.Image(self, "photo.png")
        linked = self.Image(self, "image-1.png", "b", library=object())
        gone = self.Image(self, "image-1.png", "c", missing=True)
        packed, images = self.run_pack([first, second, mine, linked, gone])
        self.assertEqual(packed, 1)
        self.assertIsNotNone(first.packed_file)
        self.assertIs(second.remapped_to, first, "The same unchanged picture: one copy")
        self.assertIn(second, studio_assets_landing._to_free, "freed later, never inside the import handler")
        self.assertIsNone(mine.packed_file)
        self.assertIsNone(linked.packed_file)
        self.assertIn(gone, images)
        self.assertEqual(gone.get("angelo_studio_asset"), "c", "Not packed: a save tries again")

    def test_packed_once_then_the_persons(self):
        """Review 4 (2): after packing, the marker goes; an Unpack the person made is never undone by a save."""
        image = self.Image(self, "image-1.png", "a")
        self.run_pack([image])
        self.assertIsNone(image.get("angelo_studio_asset"))
        self.assertEqual(image.get("angelo_studio_packed"), "a")
        image.packed_file = None   # The person unpacks (File > External Data > Unpack Resources).
        packed, _images = self.run_pack([image])   # What save_pre runs.
        self.assertEqual(packed, 0)
        self.assertIsNone(image.packed_file)

    def test_only_this_imports_pictures(self):
        """Review 4 (2): an import packs only the pictures it brought (blend_import_pre noted the rest)."""
        earlier, arrived = self.Image(self, "image-1.png", "a"), self.Image(self, "poster.png", "b")
        packed, _images = self.run_pack([earlier, arrived], arrived=lambda image: image is arrived)
        self.assertEqual(packed, 1)
        self.assertIsNone(earlier.packed_file)
        self.assertIsNotNone(arrived.packed_file)

    def test_a_drop_after_an_earlier_unchanged_copy_reuses_it(self):
        earlier = self.Image(self, "image-1.png", packed_id="a", packed=self.source.read_bytes())
        again = self.Image(self, "image-1.png.001", "a")
        packed, _images = self.run_pack([again, earlier])
        self.assertEqual(packed, 0)
        self.assertIs(again.remapped_to, earlier)
        self.assertIn(again, studio_assets_landing._to_free)

    def test_a_drop_never_takes_a_picture_the_person_changed(self):
        """Review 4 (3): a painted (or repacked) copy is not Studio's picture: the new drop packs its own."""
        for changed in ({"packed": b"red pixels"}, {"packed": self.source.read_bytes(), "dirty": True}):
            earlier = self.Image(self, "image-1.png", packed_id="a", **changed)
            again = self.Image(self, "image-1.png.001", "a")
            packed, _images = self.run_pack([again, earlier])
            self.assertEqual(packed, 1, changed)
            self.assertIsNone(again.remapped_to)
            self.assertEqual(again.packed_file.data, self.source.read_bytes())

    def test_a_drop_never_takes_a_picture_whose_settings_the_person_changed(self):
        """Review 6 (2): Non-Color, another alpha mode, another source or the person's own name: not Studio's."""
        changes = (("colorspace_settings", SimpleNamespace(name="Non-Color")), ("alpha_mode", "NONE"),
                   ("source", "GENERATED"), ("name", "my_mask"))
        for attribute, value in changes:
            earlier = self.Image(self, "image-1.png", packed_id="a", packed=self.source.read_bytes())
            setattr(earlier, attribute, value)
            again = self.Image(self, "image-1.png.001", "a")
            packed, _images = self.run_pack([again, earlier])
            self.assertEqual(packed, 1, attribute)
            self.assertIsNone(again.remapped_to, attribute)

    def run_land(self, images, fresh):
        bpy = MODULES["bpy"]
        bpy.data = SimpleNamespace(images=list(images) + [fresh], objects=[])
        bpy.path = SimpleNamespace(abspath=lambda path: path)
        with mock.patch.dict(sys.modules, MODULES):
            return studio_assets_landing.land(fresh)

    def test_a_reference_and_the_same_pictures_material_share_one_copy_either_way(self):
        """A placement (a reference, the Image Editor) lands its fresh picture through ``land`` as an append does,
        so whichever came first is the one copy (test_blender_media_drop on 6ceaef365ff packed two)."""
        reference = self.Image(self, "image-1.png", "a")
        self.assertIs(self.run_land([], reference), reference)
        self.assertEqual(reference.get("angelo_studio_packed"), "a")
        material_picture = self.Image(self, "image-1.png.001", "a")
        packed, _images = self.run_pack([reference, material_picture])
        self.assertEqual(packed, 0)
        self.assertIs(material_picture.remapped_to, reference)
        material_first = self.Image(self, "image-1.png", "b")
        self.run_pack([material_first])
        fresh = self.Image(self, "image-1.png.001", "b")
        self.assertIs(self.run_land([material_first], fresh), material_first)
        self.assertIs(fresh.remapped_to, material_first)
        self.assertIn(fresh, studio_assets_landing._to_free)

    def test_a_placement_never_takes_a_picture_the_person_changed(self):
        earlier = self.Image(self, "image-1.png", packed_id="a", packed=b"red pixels")
        fresh = self.Image(self, "image-1.png.001", "a")
        self.assertIs(self.run_land([earlier], fresh), fresh)
        self.assertEqual(fresh.packed_file.data, self.source.read_bytes())

    def test_only_the_landing_reads_the_markers_or_packs_a_studio_picture(self):
        """One owner: a module that packed and marked its own copy made the next material drop pack a second. Outside
        studio_assets_landing no module uses the packed marker, nor reads the waiting one (the writer and a
        placement set it on a fresh picture; re-exports for tests are imports, not uses)."""
        import ast
        folder = ROOT / "scripts/addons_core/angelo_agent"
        for path in sorted(folder.glob("*.py")):
            if path.name == "studio_assets_landing.py":
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                packed = (isinstance(node, ast.Name) and node.id == "STUDIO_PACKED") or (
                    isinstance(node, ast.Constant) and node.value == "angelo_studio_packed")
                read = (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                        and node.args and (getattr(node.args[0], "id", None) == "STUDIO_IMAGE"
                                           or getattr(node.args[0], "value", None) == "angelo_studio_asset"))
                self.assertFalse(packed or read, "%s:%s" % (path.name, getattr(node, "lineno", "?")))
        place = (folder / "media_place.py").read_text(encoding="utf-8")
        self.assertNotIn(".pack()", place, "media_place lands pictures through studio_assets_landing.land")
        self.assertIn("return land(image)", place.split("def _picture(", 1)[1])

    def test_never_packs_into_the_librarys_own_files(self):
        """Review 4 (7): saving an image asset's own .blend (Open Blend File) packs nothing into the library."""
        folder = studio_assets.asset_directory(self.folder)
        self.assertTrue(studio_assets_landing.library_file(str(folder / "images" / "x.blend"), folder))
        self.assertFalse(studio_assets_landing.library_file(str(self.folder / "scene.blend"), folder))
        self.assertFalse(studio_assets_landing.library_file("", folder))
        source = (ROOT / "scripts/addons_core/angelo_agent/studio_assets_landing.py").read_text(encoding="utf-8")
        self.assertIn("if not library_file(bpy.data.filepath):", source.split("def _before_save(", 1)[1])

    def test_duplicates_are_freed_later_only_when_unused_and_handlers_come_and_go(self):
        landing = studio_assets_landing
        bpy = MODULES["bpy"]
        removed = []
        bpy.data = SimpleNamespace(batch_remove=removed.extend)

        class Gone:
            @property
            def users(self):
                raise ReferenceError("freed by an undo")
        unused, used = SimpleNamespace(users=0), SimpleNamespace(users=1)
        landing._to_free[:] = [unused, used, Gone()]
        with mock.patch.dict(sys.modules, MODULES):
            self.assertIsNone(landing._free_unused())
        self.assertEqual(removed, [unused])
        self.assertEqual(landing._to_free, [])
        handlers = bpy.app.handlers
        with mock.patch.dict(sys.modules, MODULES):
            landing.add_landing_handlers()
            landing.add_landing_handlers()
            self.assertEqual((handlers.blend_import_pre, handlers.blend_import_post, handlers.save_pre),
                             ([landing._importing], [landing._imported], [landing._before_save]))
            landing.remove_landing_handlers()
        self.assertEqual((handlers.blend_import_pre, handlers.blend_import_post, handlers.save_pre), ([], [], []))


class RefreshTests(unittest.TestCase):
    def test_browsers_and_shelves_on_this_library_or_all_are_refreshed_once_per_list(self):
        refreshed = []

        def browser(name, ui_type='ASSETS', library=None):
            return SimpleNamespace(type='FILE_BROWSER', ui_type=ui_type, name=name, regions=[SimpleNamespace(type='WINDOW')],
                                   spaces=SimpleNamespace(active=SimpleNamespace(
                                       params=SimpleNamespace(asset_library_reference=library))))

        def view(name, library=None, kind='VIEW_3D'):
            regions = [SimpleNamespace(type='WINDOW')]
            if library is not None:
                regions.append(SimpleNamespace(type='ASSET_SHELF', library=library))
            return SimpleNamespace(type=kind, ui_type=kind, name=name, regions=regions)
        areas = [browser("browser mine", library="Mine renamed"), browser("browser all", library='ALL'),
                 browser("browser huge", library="Huge studio library"), browser("files", ui_type='FILES'),
                 view("view no shelf"), view("shelf mine", "Mine renamed"), view("shelf mine again", "Mine renamed"),
                 view("shelf all", 'ALL', 'IMAGE_EDITOR'), view("shelf huge", "Huge studio library")]
        current = {}

        class Context:
            window_manager = SimpleNamespace(windows=[SimpleNamespace(screen=SimpleNamespace(areas=areas))])

            @property
            def asset_shelf(self):
                region = current.get("region")
                if region is None or region.type != 'ASSET_SHELF':
                    return None
                return SimpleNamespace(asset_library_reference=region.library)

            def temp_override(self, **context):
                class Override:
                    def __enter__(self_):
                        current.update(context)

                    def __exit__(self_, *exc):
                        current.clear()
                return Override()
        bpy = MODULES["bpy"]
        bpy.context = Context()
        bpy.ops = SimpleNamespace(asset=SimpleNamespace(library_refresh=type("Refresh", (), {
            "poll": staticmethod(lambda: True),
            "__call__": lambda self: refreshed.append(current["area"].name)})()))
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(sys.modules, MODULES):
            directory = studio_assets.asset_directory(root)
            directory.mkdir(parents=True)
            entry = SimpleNamespace(name="Mine renamed", path=str(directory))
            with mock.patch.object(studio_assets_host, "directories", return_value=(Path(root), directory)), \
                    mock.patch.object(studio_assets_host, "_libraries", return_value=[entry]):
                studio_assets_host.refresh_asset_browsers()
        self.assertEqual(refreshed, ["browser mine", "browser all", "shelf mine", "shelf all"])

    def test_a_refused_refresh_waits_for_the_next_tick_but_not_forever(self):
        state = studio_assets_host._state
        calls = []
        with mock.patch.dict(state, registered=True, since=studio_assets_host.time.monotonic(), changed=True, waits=0,
                             worker=None, reread=False), \
                mock.patch.object(studio_assets_host, "observe", return_value=True), \
                mock.patch.object(studio_assets_host, "host_busy", return_value=False), \
                mock.patch.object(studio_assets_host, "_catalog_names", return_value={}), \
                mock.patch.object(studio_assets_host, "refresh_asset_browsers",
                                  side_effect=lambda: calls.append(1) or False):   # A drag holds the lists.
            studio_assets_host._tick()
            self.assertTrue(state["changed"], "kept for the next tick")
            for _ in range(studio_assets_host.REFRESH_TRIES + 5):
                studio_assets_host._tick()
            self.assertFalse(state["changed"], "never retried forever")
            self.assertEqual(len(calls), studio_assets_host.REFRESH_TRIES + 1)


class SyncTests(unittest.TestCase):
    def test_one_writer_per_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            with studio_assets.sync_lock(directory) as first, studio_assets.sync_lock(directory) as second:
                self.assertTrue(first)
                self.assertFalse(second)
            with studio_assets.sync_lock(directory) as again:
                self.assertTrue(again, "released on close")
            with studio_assets.sync_lock(directory) as held, \
                    mock.patch.object(studio_assets_host, "_sync_locked", side_effect=AssertionError("ran")):
                self.assertFalse(studio_assets_host.sync(Path(directory), Path(directory), {}, "b", "s",
                                                         threading.Event()))

    def test_the_manifest_is_read_before_the_records(self):
        order = []
        with tempfile.TemporaryDirectory() as root:
            library = Library(root)
            directory = studio_assets.asset_directory(library.root)
            real = studio_assets.read_manifest
            with mock.patch.object(studio_assets, "read_manifest", side_effect=lambda d: order.append("manifest") or real(d)), \
                    mock.patch("angelo_agent.studio_state.StudioLibrary.reload", autospec=True,
                               side_effect=lambda self: order.append("records") or setattr(self, "assets", {}) or
                               setattr(self, "folders", {})):
                studio_assets_host.sync(library.root, directory, {}, "b", "s", threading.Event(), lambda *a: {})
        self.assertEqual(order[:2], ["manifest", "records"])

    def test_stale_temporaries_are_removed_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as root:
            folder = studio_assets.asset_directory(root) / "models"
            folder.mkdir(parents=True)
            stale = folder / (str(uuid.uuid4()) + ".blend.ab12.tmp")
            fresh = folder / (str(uuid.uuid4()) + ".blend.cd34.tmp")
            other = folder / "notes.blend.ef56.tmp"
            # Blender's own save temporary of a writer's file, left when the writer was stopped mid-save.
            stale_save, fresh_save = (folder / (str(uuid.uuid4()) + ".blend.%s.tmp@" % s) for s in ("ab", "cd"))
            other_save = folder / "notes.blend.ef56.tmp@"
            for path in (stale, fresh, other, stale_save, fresh_save, other_save):
                path.write_bytes(b"x")
            for path in (stale, other, stale_save, other_save):
                os.utime(path, (0, 0))
            studio_assets.remove_stale_temporaries(root)
            self.assertEqual((stale.exists(), fresh.exists(), other.exists()), (False, True, True))
            self.assertEqual((stale_save.exists(), fresh_save.exists(), other_save.exists()), (False, True, True))

    def test_batches_and_stop(self):
        with tempfile.TemporaryDirectory() as root:
            library = Library(root)
            for n in range(studio_assets.BATCH + 3):
                library.model(f"Model {n}")
            batches = []
            stop = threading.Event()

            def run(binary, batch, stopping, *rest):
                batches.append(len(batch))
                stop.set()   # The add-on unloads during the first batch: the second never starts.
                return {item["id"]: {"ok": True} for item in batch}
            directory = studio_assets.asset_directory(library.root)
            changed = studio_assets_host.sync(library.root, directory, {}, "binary", "s", stop, run)
            self.assertTrue(changed)
            self.assertEqual(batches, [studio_assets.BATCH])
            self.assertEqual(len(studio_assets.read_manifest(directory)), studio_assets.BATCH)

    def test_a_crashing_item_fails_alone(self):
        with tempfile.TemporaryDirectory() as root:
            library = Library(root)
            for n in range(12):
                library.model(f"Model {n}")
            runs, poison = [], []

            def run(binary, batch, stopping, *rest):   # The writer dies on the fifth item it is given, as a crash would.
                runs.append([item["id"] for item in batch])
                if not poison:
                    poison.append(batch[4]["id"])   # Chosen from the batch: the plan's order is the folder's.
                results = {}
                for item in batch:
                    if item["id"] == poison[0]:
                        break
                    results[item["id"]] = {"ok": True}
                return results
            directory = studio_assets.asset_directory(library.root)
            studio_assets_host.sync(library.root, directory, {}, "b", "s", threading.Event(), run)
            manifest = studio_assets.read_manifest(directory)
            self.assertEqual([k for k, v in manifest.items() if v.get("failed")], poison)
            self.assertEqual(len(manifest), 12)
            self.assertEqual([len(r) for r in runs], [12, 7], "the items after the crash ran in a fresh writer")

    def test_a_writer_that_cannot_run_is_not_relaunched_per_item(self):
        with tempfile.TemporaryDirectory() as root:
            library = Library(root)
            for n in range(30):
                library.model(f"Model {n}")
            runs = []
            directory = studio_assets.asset_directory(library.root)
            studio_assets_host.sync(library.root, directory, {}, "b", "s", threading.Event(),
                                    lambda binary, batch, stop, *rest: runs.append(1) or {})
            self.assertEqual(len(runs), 2)

    def test_the_writer_steps_aside_for_the_persons_work(self):
        with tempfile.TemporaryDirectory() as root:
            library = Library(root)
            records = [library.model(f"Model {n}") for n in range(10)]
            busy = {"now": False}

            def run(binary, batch, stop, yield_to):   # A render starts after three items.
                results = {item["id"]: {"ok": True} for item in batch[:3]}
                busy["now"] = True
                self.assertTrue(yield_to())
                return results
            directory = studio_assets.asset_directory(library.root)
            sync = lambda runner: studio_assets_host.sync(library.root, directory, {}, "b", "s", threading.Event(),  # noqa: E731
                                                          runner, yield_to=lambda: busy["now"])
            self.assertTrue(sync(run))
            manifest = studio_assets.read_manifest(directory)
            self.assertEqual(len(manifest), 3)
            self.assertFalse(any(v.get("failed") for v in manifest.values()), "stepping aside is not a failure")
            never = mock.Mock(return_value={})
            sync(never)
            never.assert_not_called()
            busy["now"] = False
            rest = mock.Mock(side_effect=lambda binary, batch, stop, yield_to: {i["id"]: {"ok": True} for i in batch})
            sync(rest)
            self.assertEqual(len(studio_assets.read_manifest(directory)), len(records))

    def test_renders_bakes_jobs_and_agent_turns_count_as_busy(self):
        bpy = MODULES["bpy"]
        jobs = SimpleNamespace(running=None)
        controllers = {"a": SimpleNamespace(busy=False)}
        fake_ui = types.ModuleType("angelo_agent.ui")
        fake_ui._controllers = controllers
        fake_jobs = types.ModuleType("angelo_agent.job_ui")
        fake_jobs.manager = lambda create=True: jobs
        running = set()
        with mock.patch.dict(sys.modules, {"angelo_agent.ui": fake_ui, "angelo_agent.job_ui": fake_jobs}), \
                mock.patch.object(bpy.app, "is_job_running", lambda kind: kind in running, create=True), \
                mock.patch.object(sys.modules["angelo_agent"], "ui", fake_ui, create=True), \
                mock.patch.object(sys.modules["angelo_agent"], "job_ui", fake_jobs, create=True):
            self.assertFalse(studio_assets_host.host_busy())
            for kind in studio_assets_host.BUSY_JOBS:
                running.add(kind)
                self.assertTrue(studio_assets_host.host_busy(), kind)
                running.clear()
            jobs.running = "job-1"
            self.assertTrue(studio_assets_host.host_busy())
            jobs.running = None
            controllers["a"].busy = True
            self.assertTrue(studio_assets_host.host_busy())

    def test_a_turn_off_from_another_angelo_stops_the_writer_between_batches(self):
        """Review 6 (4): the registration is read under the lock and between batches."""
        with tempfile.TemporaryDirectory() as root:
            library = Library(root)
            library.model("one")
            library.model("two")
            directory = studio_assets.asset_directory(library.root)

            def run(binary, batch, stop, yield_to):
                studio_assets.write_registration(directory, removed=True)   # Another Angelo turns it off now.
                return {}
            calls = mock.Mock(side_effect=run)
            with mock.patch.object(studio_assets, "BATCH", 1):
                studio_assets_host.sync(library.root, directory, {}, "b", "s", threading.Event(), calls)
            self.assertEqual(calls.call_count, 1, "no batch after the turn-off")

    def test_nothing_is_written_once_removed(self):
        with tempfile.TemporaryDirectory() as root:
            library = Library(root)
            library.model()
            directory = studio_assets.asset_directory(library.root)
            studio_assets.write_registration(directory, removed=True)
            run = mock.Mock(return_value={})
            self.assertFalse(studio_assets_host.sync(library.root, directory, {}, "b", "s", threading.Event(), run))
            run.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "the stand-in writer is a POSIX script")
    def test_the_writer_runs_lowered_and_stops_with_the_add_on(self):
        with tempfile.TemporaryDirectory() as root:
            writer = Path(root) / "writer"
            writer.write_text(textwrap.dedent(f"""\
                #!{sys.executable}
                import json, os, sys, time
                args = sys.argv[sys.argv.index("--") + 1:]
                batch = json.load(open(args[0]))
                time.sleep(.2)   # After the launcher lowered this process.
                json.dump({{"nice": os.getpriority(os.PRIO_PROCESS, 0), "ids": [i["id"] for i in batch]}},
                          open(args[1], "w"))
                time.sleep(float(batch[0].get("sleep", 0)))
                """))
            writer.chmod(writer.stat().st_mode | stat.S_IEXEC)
            results = studio_assets_host.convert_batch(str(writer), [{"id": "a", "sleep": 0.6}], threading.Event())
            self.assertEqual(results["ids"], ["a"])
            self.assertGreaterEqual(results["nice"], 10)
            stop = threading.Event()
            threading.Timer(.3, stop.set).start()
            started = studio_assets_host.time.monotonic()
            studio_assets_host.convert_batch(str(writer), [{"id": "b", "sleep": 30}], stop)
            self.assertLess(studio_assets_host.time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
