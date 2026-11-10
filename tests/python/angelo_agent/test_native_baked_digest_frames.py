# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The baked-material digest reads the file Blender reads at the frame and in the views a bake renders.

``export_baked_reads.Reader`` runs here against a stand-in texture node and image. A bake reads an image sequence's
file of the frame its node's image user reads at the scene frame, a movie at that frame, and each view's file of an
image whose views are separate files; a frame or view file saved again with other pixels must make the bake stale
(1.0.10 audit, P3-2). ``test_blender_image_read_files.py`` repeats this in the built app.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import hashlib
import importlib
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_test_policy import environment_skip  # noqa: E402


class ID:
    _next = [1]

    def __init__(self):
        self.session_uid = ID._next[0]
        ID._next[0] += 1


class Image(ID):
    def __init__(self, path, source):
        super().__init__()
        self.filepath_raw, self.source, self.library, self.packed_file, self.packed_files = path, source, None, None, []
        self.tiles, self.is_dirty, self.has_data = [], False, True
        self.use_multiview, self.views_format = False, "INDIVIDUAL"
        self.views = []   # The view files Blender set up for it (``Image.is_multiview`` when there are any).
        self.colorspace_settings, self.alpha_mode = SimpleNamespace(name="sRGB"), "STRAIGHT"

    @property
    def is_multiview(self):
        return bool(self.views)

    def filepath_from_user(self, image_user):   # Blender's name for the image user's view (the first here).
        return self.views[image_user.multilayer_view]


def rna(**kinds):
    return SimpleNamespace(properties=[SimpleNamespace(identifier=name, type=kind, is_readonly=False, subtype="NONE")
                                       for name, kind in kinds.items()])


def texture(image, duration=3, start=1, offset=0, cyclic=False):
    """An Image Texture node and its image user, as ``Reader._value`` reads them."""
    kinds = rna(frame_duration="INT", frame_start="INT", frame_offset="INT", use_cyclic="BOOLEAN")
    user = SimpleNamespace(bl_rna=kinds, frame_duration=duration, frame_start=start, frame_offset=offset,
                           use_cyclic=cyclic, multilayer_view=0, frame_current=0)
    return SimpleNamespace(bl_rna=rna(image="POINTER", image_user="POINTER"), image=image, image_user=user)


def stand_in_bpy():
    bpy = ModuleType("bpy")
    bpy.types = SimpleNamespace(ID=ID, Image=Image, Object=type("Object", (ID,), {}),
                                NodeTree=type("NodeTree", (ID,), {}), Text=type("Text", (ID,), {}))
    bpy.path = SimpleNamespace(abspath=lambda path, library=None: path)
    return bpy


def fresh_export_modules():
    """Drop the add-on's export modules so the next import runs them against this test's stand-ins: from sys.modules
    and as attributes of the add-on's package too, since ``from . import export_baked_hidden`` reads a package
    attribute left by an earlier import before it looks in sys.modules. Returns the undo, for a class cleanup, that
    puts the package's attributes back as they were."""
    package = sys.modules.get("angelo_agent")
    saved = {name: value for name, value in vars(package).items() if name.startswith("export_baked")} if package else {}
    for name in [name for name in sys.modules if name.startswith("angelo_agent.export_baked")]:
        del sys.modules[name]
    for name in saved:
        delattr(package, name)

    def undo():
        for name in [name for name in vars(package) if name.startswith("export_baked")]:
            delattr(package, name)
        for name, value in saved.items():
            setattr(package, name, value)
    return undo if package is not None else (lambda: None)


def stereo(multiview=True):
    return SimpleNamespace(use_multiview=multiview, views_format="STEREO_3D",
                           views=[SimpleNamespace(name="left", file_suffix="_L", use=True),
                                  SimpleNamespace(name="right", file_suffix="_R", use=True)])


class BakedDigestFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.dict(sys.modules, {"bpy": stand_in_bpy(), "_bpy": ModuleType("_bpy")}):
            cls.addClassCleanup(fresh_export_modules())
            cls.reads = importlib.import_module("angelo_agent.export_baked_reads")
            cls.bpy = sys.modules["bpy"]

    def setUp(self):
        self.modules = patch.dict(sys.modules, {"bpy": self.bpy})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.render = stereo(multiview=False)

    def write(self, name, data=b"pixels"):
        path = os.path.join(self.folder, name)
        Path(path).write_bytes(data)
        return path

    def digest(self, node, frame):
        """The node's computing value as a fresh reader (one export) reads it at scene frame ``frame``."""
        depsgraph = SimpleNamespace(scene=SimpleNamespace(frame_current=frame, render=self.render))
        return self.reads.Reader(depsgraph)._value(node, 3, self.reads.Notes())

    def test_a_sequence_frames_file_changed_on_disk_makes_the_bake_stale(self):
        files = [self.write("seq.%04d.png" % frame) for frame in (1, 2, 3)]
        for cyclic in (False, True):
            with self.subTest(cyclic=cyclic):
                node = texture(Image(files[0], "SEQUENCE"), cyclic=cyclic)
                before = self.digest(node, 2)
                self.assertEqual(self.digest(node, 2), before)
                self.write("seq.0002.png", b"pixels, painted over %s" % str(cyclic).encode())   # The frame read.
                self.assertNotEqual(self.digest(node, 2), before)
                at_two = self.digest(node, 2)
                self.write("seq.0003.png", b"another frame %s" % str(cyclic).encode())   # Not read at frame 2.
                self.assertEqual(self.digest(node, 2), at_two)
                os.remove(files[1])   # The frame's file going missing is a change too.
                self.assertNotEqual(self.digest(node, 2), at_two)
                self.write("seq.0002.png")

    def test_the_image_users_offset_and_wrap_choose_the_file(self):
        self.write("seq.0011.png")
        node = texture(Image(self.write("seq.0010.png"), "SEQUENCE"), duration=2, start=5, offset=9, cyclic=True)
        before = self.digest(node, 8)   # Frame 8 from start 5 is the 4th, wrapped to 2, offset by 9: file 11.
        self.write("seq.0010.png", b"other")
        self.assertEqual(self.digest(node, 8), before)
        self.write("seq.0011.png", b"other")
        self.assertNotEqual(self.digest(node, 8), before)

    def test_a_movie_is_read_at_the_image_users_frame(self):
        # Cycles copies a movie's frame from Blender's memory (export_baked_images.reads_memory): its pixels, read
        # here by a stand-in native reader that decodes the file, as Blender loads a frame it does not hold.
        movie = self.write("clip.mov")
        node = texture(Image(movie, "MOVIE"), duration=3)
        native = SimpleNamespace(pixels=lambda image, user, frame, tiles: [
            (0, 8, 8, 4, False, hashlib.blake2b(Path(image.filepath_raw).read_bytes(), digest_size=16).digest())])
        with patch.dict(self.reads.image_digest.__globals__, {"NATIVE": native}):   # export_baked_images
            before = self.digest(node, 3)
            self.assertEqual(self.digest(node, 9), before, "past its last frame a movie holds the last one")
            self.assertNotEqual(self.digest(node, 2), before)
            self.write("clip.mov", b"re-encoded")
            self.assertNotEqual(self.digest(node, 3), before)

    def test_each_views_file_of_a_multi_view_image(self):
        left, right = self.write("shot_L.png"), self.write("shot_R.png")
        image = Image(left, "FILE")
        image.use_multiview, image.views = True, [left, right]   # Set up by Blender in this stereo scene.
        node = texture(image)
        self.render = stereo()
        before = self.digest(node, 1)
        self.assertEqual(self.digest(node, 1), before)
        self.write("shot_R.png", b"the right eye, painted over")
        self.assertNotEqual(self.digest(node, 1), before)

    def test_views_blender_set_up_for_another_scene_are_unknown(self):
        right = self.write("shot_R.png")
        image = Image(right, "FILE")
        image.use_multiview, image.views = True, [self.write("shot_L.png"), right]
        node = texture(image)
        self.render = stereo(multiview=False)   # Stereoscopy turned off since: Blender still reads its saved views.
        self.assertNotEqual(self.digest(node, 1), self.digest(node, 1), "unknown is stale")
        image.views = []   # Views never set up (or freed): the stored file alone, whatever the scene.
        self.render = stereo()
        single = self.digest(node, 1)
        self.write("shot_L.png", b"not read")
        self.assertEqual(self.digest(node, 1), single)

    @unittest.skipUnless(os.name == "posix", environment_skip("Blender respells a Windows path only on macOS and Linux"))
    def test_a_path_saved_on_windows_names_the_file_blender_opens(self):
        os.makedirs(os.path.join(self.folder, "textures"))
        self.write(os.path.join("textures", "seq.0001.png"))
        node = texture(Image(self.folder + "\\textures\\seq.0001.png", "SEQUENCE"))
        before = self.digest(node, 1)
        self.write(os.path.join("textures", "seq.0001.png"), b"repainted")
        self.assertNotEqual(self.digest(node, 1), before)

    def test_a_still_files_digest_is_unchanged_from_before(self):
        # Bindings made by earlier builds stay current: a still image's digest is still its file's size and time.
        path = self.write("still.png")
        status = os.stat(path)
        expected = hashlib.blake2b(repr((path, status.st_size, status.st_mtime_ns)).encode(), digest_size=16)
        self.assertEqual(self.reads.Reader(SimpleNamespace(scene=SimpleNamespace(frame_current=4, render=self.render)))
                         .image(Image(path, "FILE")), expected.hexdigest())

    def test_without_an_image_user_the_stored_file_stands_for_the_image(self):
        # A Geometry Nodes Image input has no image user; its effect reaches a bake through the evaluated mesh.
        image = Image(self.write("seq.0001.png"), "SEQUENCE")
        depsgraph = SimpleNamespace(scene=SimpleNamespace(frame_current=2, render=self.render))
        before = self.reads.Reader(depsgraph).image(image)
        self.assertEqual(self.reads.Reader(depsgraph).image(image), before)
        self.write("seq.0001.png", b"repainted")
        self.assertNotEqual(self.reads.Reader(depsgraph).image(image), before)


if __name__ == "__main__":
    unittest.main()
