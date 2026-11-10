# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The baked-material digest describes the pixels a bake reads, from wherever the bake reads them.

1.0.10 audit, re-check 2 (P2, since 1.0.9): the digest held an image's files and packed bytes while Cycles, for some
images, copies the pixels Blender holds in memory, and those can be older than the files (a file saved over while
the image holds the old pixels; packing after that). Such a bake read the old pixels, the binding recorded the new
source, and after a Reload or reopening the .blend the export called the bake current and shipped it.

Cycles decides per image (``image_is_builtin``, intern/cycles/blender/util.h): a packed, generated or movie image, one
with unsaved edits, and a UDIM image with a generated tile are copied from memory; every other image is opened from
its files on disk, whatever Blender holds (both measured in the built app, ``test_blender_baked_export_buffers``).

Here a stand-in Blender holds buffers per tile and loads them from the image's source, and a stand-in bake reads
what Cycles reads, by Cycles' own rule written out independently of the add-on. Each case moves the scene through
the transitions a person makes (a file saved over, Reload, reopening the file, packing, painting, a buffer evicted
under memory pressure) and checks the one property that matters: the digest is unchanged exactly when a bake now
reads the same pixels as the bake that was bound. The add-on's rule is also held to Cycles' source text.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import hashlib
import importlib
import os
from pathlib import Path
import re
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_baked_digest_frames import fresh_export_modules  # noqa: E402

CYCLES_UTIL = ROOT / "intern/cycles/blender/util.h"
CYCLES_SHADER = ROOT / "intern/cycles/blender/shader.cpp"
CYCLES_IMAGE = ROOT / "intern/cycles/blender/image.cpp"


class ID:
    _next = [1]

    def __init__(self):
        self.session_uid = ID._next[0]
        ID._next[0] += 1


class Tile(SimpleNamespace):
    pass


class Image(ID):
    """A stand-in image: its source (files, packed data or generator settings) and the buffers Blender holds, one per
    tile (``held``: tile -> (pixels, edited)). Buffers load from the source when read and stay until freed."""

    def __init__(self, source="FILE", path="", tiles=(0,)):
        super().__init__()
        self.source, self.filepath_raw, self.library, self.type = source, path, None, "IMAGE"
        self.packed = {}   # tile -> packed bytes
        self.tiles = [Tile(number=number, is_generated_tile=False, generated_type="BLANK",
                           generated_color=(0.5, 0.5, 0.5, 1.0), generated_width=8, generated_height=8,
                           use_generated_float=False) for number in tiles]
        self.generated_type, self.generated_color = "BLANK", (0.2, 0.2, 0.2, 1.0)
        self.generated_width = self.generated_height = 8
        self.use_generated_float = False
        self.held = {}
        self.decoder = None   # A movie's open decoder: what it opened, read until the image is freed or reloaded.

    # -- what Python reads ------------------------------------------------------------------------------
    @property
    def packed_files(self):
        return [SimpleNamespace(tile_number=tile, view=0, packed_file=SimpleNamespace(data=data))
                for tile, data in sorted(self.packed.items())]

    @property
    def packed_file(self):
        files = self.packed_files
        return files[0].packed_file if files else None

    @property
    def is_dirty(self):
        return any(edited for _pixels, edited in self.held.values())

    @property
    def has_data(self):
        return bool(self.held)

    # -- Blender ----------------------------------------------------------------------------------------
    def tile_path(self, tile):
        return self.filepath_raw.replace("<UDIM>", str(tile)) if self.source == "TILED" else self.filepath_raw

    decodes = 0   # Loads from the source, every image's.

    def decode(self, tile, frame=0):
        """What loading the tile's buffer reads now: generator settings, packed bytes, else the file."""
        Image.decodes += 1
        entry = next((each for each in self.tiles if each.number == tile), None)
        if self.source == "GENERATED":
            return ("generated", self.generated_color)
        if entry is not None and entry.is_generated_tile:
            return ("generated tile", entry.generated_color)
        if tile in self.packed:
            return ("decoded", self.packed[tile])
        if self.source == "MOVIE" and self.decoder is not None:
            return ("decoded", self.decoder, frame)   # Through the decoder opened before, whatever the file is now.
        try:
            data = Path(self.tile_path(tile)).read_bytes()
        except OSError:
            return None
        if self.source == "MOVIE":
            self.decoder = data
            return ("decoded", data, frame)
        return ("decoded", data)

    def acquire(self, tile, frame=0):
        key = (tile, frame) if self.source == "MOVIE" else tile
        if key not in self.held:
            pixels = self.decode(tile, frame)
            if pixels is None:
                return None
            self.held[key] = (pixels, False)
        return self.held[key][0]

    def view(self):
        """The viewport draws the image: every tile's buffer is loaded."""
        for tile in self.tiles:
            self.acquire(tile.number)

    def paint(self, tile, stroke):
        pixels = self.acquire(tile)
        self.held[tile] = (("painted", pixels, stroke), True)

    def reload(self):   # Image > Reload, or reopening the .blend: every buffer and edit is gone.
        self.held.clear()
        self.decoder = None

    def free(self):   # BKE_image_free_buffers_ex: buffers and decoders.
        self.held.clear()
        self.decoder = None

    def evict(self, tile):   # Blender's memory cache limiter frees an unedited buffer under memory pressure.
        if not self.held[tile][1]:
            del self.held[tile]

    def pack(self):   # Pack from the files on disk (what Pack does for an image without edits).
        for tile in self.tiles:
            self.packed[tile.number] = Path(self.tile_path(tile.number)).read_bytes()


class Native:
    """``_bpy._angelo_digest.pixels`` as bpy_angelo_digest.cc answers: Cycles' loader's reads, each buffer acquired
    (loaded from the source when not held), freed again when the image held nothing."""

    @staticmethod
    def pixels(image, user, scene_frame, tiles):
        held = bool(image.held)
        frame = scene_frame if image.source == "MOVIE" else 0
        found = []
        for tile in tiles:
            pixels = image.acquire(tile, frame)
            digest = None if pixels is None else hashlib.blake2b(repr(pixels).encode(), digest_size=16).digest()
            found.append((tile, 8, 8, 4, False, digest))
        if not held:
            image.free()
        return found

    @staticmethod
    def holds(image):
        return bool(image.held) or image.decoder is not None

    @staticmethod
    def packed(image):
        return [(tile, 0, len(data), hashlib.blake2b(data, digest_size=16).digest())
                for tile, data in sorted(image.packed.items())]

    @staticmethod
    def unsaved_pixels(image):
        """The edited buffers alone: the reader the digest used before (cbadc2a6433), kept so that digest can be run
        against these cases and shown to fail them."""
        return [(tile, 8, 8, 4, False, hashlib.blake2b(repr(pixels).encode(), digest_size=16).digest())
                for tile, (pixels, edited) in sorted(image.held.items()) if edited]


def cycles_copies_from_memory(image):
    """Cycles' ``image_is_builtin`` for a bake, written out here independently of the add-on."""
    generated_tile = image.source == "TILED" and any(tile.is_generated_tile for tile in image.tiles)
    return (generated_tile or bool(image.packed) or image.source in ("GENERATED", "MOVIE") or image.is_dirty)


def bake(image, frame=0):
    """What a Cycles bake reads of every tile: Blender's buffers, or the files on disk (OpenImageIO)."""
    tiles = [tile.number for tile in image.tiles]
    if cycles_copies_from_memory(image):
        held = bool(image.held)
        read = [image.acquire(tile, frame) for tile in tiles]
        if not held:   # Cycles' loader frees an image it loaded (buffers and decoders).
            image.free()
        return read
    return [image.decode(tile) for tile in tiles]


def stand_in_bpy():
    bpy = ModuleType("bpy")
    bpy.types = SimpleNamespace(ID=ID, Image=Image, Object=type("Object", (ID,), {}),
                                NodeTree=type("NodeTree", (ID,), {}), Text=type("Text", (ID,), {}))
    bpy.path = SimpleNamespace(abspath=lambda path, library=None: path)
    return bpy


USER = SimpleNamespace(frame_duration=10, frame_start=1, frame_offset=0, use_cyclic=False, multilayer_view=0,
                       frame_current=0)


class BakedDigestBufferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        native = ModuleType("_bpy")
        native._angelo_digest = Native()
        with patch.dict(sys.modules, {"bpy": stand_in_bpy(), "_bpy": native}):
            cls.addClassCleanup(fresh_export_modules())
            cls.reads = importlib.import_module("angelo_agent.export_baked_reads")
            cls.images = sys.modules["angelo_agent.export_baked_images"]
            cls.bpy = sys.modules["bpy"]

    def setUp(self):
        self.modules = patch.dict(sys.modules, {"bpy": self.bpy})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        self.stamp = 10 ** 18   # A distinct, settled time for every write.

    def write(self, name, data):
        path = os.path.join(self.folder, name)
        Path(path).write_bytes(data)
        self.stamp += 10 ** 9
        os.utime(path, ns=(self.stamp, self.stamp))
        return path

    def digest(self, image, frame=1):
        depsgraph = SimpleNamespace(scene=SimpleNamespace(frame_current=frame, render=None))
        return self.reads.Reader(depsgraph).image(image, USER)

    def bound(self, image, frame=1):
        """Bind as the job does (the digest first, then the bake reads)."""
        return self.digest(image, frame), bake(image, frame if image.source == "MOVIE" else 0)

    def assert_honest(self, image, binding, label, frame=1):
        """The digest is unchanged exactly when a bake now reads what the bound bake read."""
        digest, read = binding
        now = bake(image, frame if image.source == "MOVIE" else 0)
        self.assertEqual(self.digest(image, frame) == digest, now == read,
                         "%s: digest %s, pixels %s" % (label, "same" if self.digest(image, frame) == digest
                                                       else "changed", "same" if now == read else "changed"))
        return now == read

    # -- the audit's cases, by where Cycles reads ---------------------------------------------------------------
    def test_a_file_saved_over_while_blender_holds_the_old_pixels(self):
        # Cycles opens this image's file whatever Blender holds, so the file is what the bake read.
        image = Image("FILE", self.write("a.png", b"0.1"))
        image.view()
        self.write("a.png", b"0.9")
        binding = self.bound(image)
        self.assertEqual(binding[1], [("decoded", b"0.9")], "the bake read the file, not the held pixels")
        for label, step in (("not reloaded", lambda: None), ("reloaded", image.reload),
                            ("reopened", image.reload)):
            step()
            self.assertTrue(self.assert_honest(image, binding, label))

    def test_packed_after_the_file_changed(self):
        image = Image("FILE", self.write("p.png", b"0.1"))
        image.view()
        self.write("p.png", b"0.9")
        image.pack()   # Packs the new file; Blender still holds the old pixels, and the bake copies those.
        binding = self.bound(image)
        self.assertEqual(binding[1], [("decoded", b"0.1")])
        self.assertTrue(self.assert_honest(image, binding, "not reloaded"))
        image.reload()
        self.assertFalse(self.assert_honest(image, binding, "reloaded: the packed pixels"))

    def test_a_udim_image_with_a_generated_tile(self):
        image = Image("TILED", os.path.join(self.folder, "u.<UDIM>.png"), tiles=(1001, 1002))
        self.write("u.1001.png", b"0.1")
        image.tiles[1].is_generated_tile = True   # Add Tile, filled, never saved.
        image.view()
        self.write("u.1001.png", b"0.9")
        binding = self.bound(image)
        self.assertEqual(binding[1][0], ("decoded", b"0.1"), "every tile is copied from memory")
        self.assertTrue(self.assert_honest(image, binding, "not reloaded"))
        image.reload()
        self.assertFalse(self.assert_honest(image, binding, "reloaded"))

    def test_a_movie_saved_over(self):
        image = Image("MOVIE", self.write("m.mov", b"take 1"))
        image.acquire(0, 3)
        self.write("m.mov", b"take 2")
        binding = self.bound(image, 3)
        self.assertTrue(self.assert_honest(image, binding, "not reloaded", 3))
        image.reload()
        self.assertFalse(self.assert_honest(image, binding, "reloaded", 3))

    def test_a_movie_read_through_a_decoder_opened_before_its_file_changed(self):
        """Re-check of the break-it review: a decoder outlives its frames, so a read of an image holding no frame can
        still read an older file. Such a read is no decode of the source, and is never remembered for it."""
        image = Image("MOVIE", self.write("old.mov", b"take 1"))
        image.acquire(0, 3)   # Viewed: its decoder open on take 1, the frame held.
        os.replace(self.write("new.mov", b"take 2"), image.filepath_raw)   # Saved over by another program.
        binding = self.bound(image, 3)
        self.assertEqual(binding[1], [("decoded", b"take 1", 3)])
        del image.held[(0, 3)]   # The frame evicted under memory pressure; the decoder stays open.
        self.digest(image, 3)   # An export: reads take 1 through the open decoder, then frees it.
        self.assertIsNone(image.decoder)
        self.assertFalse(self.assert_honest(image, binding, "the next export, the decoder freed", 3))

    def test_unsaved_paint_with_another_tile_held_from_an_older_file(self):
        image = Image("TILED", os.path.join(self.folder, "d.<UDIM>.png"), tiles=(1001, 1002))
        self.write("d.1001.png", b"0.1")
        self.write("d.1002.png", b"0.5")
        image.view()
        self.write("d.1002.png", b"0.9")   # Saved over by another program, not reloaded.
        image.paint(1001, "stroke")
        binding = self.bound(image)
        self.assertTrue(self.assert_honest(image, binding, "unchanged"))
        image.evict(1002)   # Memory pressure frees the unedited tile; the next read loads the new file.
        self.assertFalse(self.assert_honest(image, binding, "tile 1002 evicted and read again"))
        image.paint(1001, "another stroke")
        self.assertFalse(self.assert_honest(image, binding, "painted again"))

    # -- the same, whatever is loaded --------------------------------------------------------------------------
    def test_loaded_or_not_reads_the_same_digest(self):
        for label, make in (("packed", self.packed_image), ("generated tile", self.udim_with_generated_tile),
                            ("movie", lambda: Image("MOVIE", self.write("n.mov", b"clip")))):
            with self.subTest(image=label):
                image = make()
                never = self.digest(image)
                self.assertFalse(image.held, "reading an image that held nothing keeps nothing in memory")
                image.view()
                image.acquire(0, 1)
                self.assertEqual(self.digest(image), never, "loaded from the same source")
                binding = self.bound(image)
                image.reload()
                self.assertTrue(self.assert_honest(image, binding, "reopened"))

    def packed_image(self):
        image = Image("FILE", self.write("q.png", b"pixels"))
        image.pack()
        return image

    def udim_with_generated_tile(self):
        image = Image("TILED", os.path.join(self.folder, "g.<UDIM>.png"), tiles=(1001, 1002))
        self.write("g.1001.png", b"pixels")
        image.tiles[1].is_generated_tile = True
        return image

    def test_a_generated_image_is_its_settings(self):
        image = Image("GENERATED")
        binding = self.bound(image)
        image.view()
        self.assertTrue(self.assert_honest(image, binding, "drawn"))
        image.reload()
        self.assertTrue(self.assert_honest(image, binding, "reopened"))
        image.generated_color = (1.0, 0.0, 0.0, 1.0)
        image.reload()   # Blender frees a generated image's pixels when a setting changes.
        self.assertFalse(self.assert_honest(image, binding, "a setting changed"))
        image.paint(0, "stroke")
        self.assertFalse(self.assert_honest(image, binding, "painted"))

    def test_a_still_file_never_loaded(self):
        image = Image("FILE", self.write("s.png", b"0.1"))
        binding = self.bound(image)
        self.assertTrue(self.assert_honest(image, binding, "unchanged"))
        self.write("s.png", b"0.9")
        self.assertFalse(self.assert_honest(image, binding, "saved over"))

    # -- Cycles' rule, condition for condition ---------------------------------------------------------------
    def test_the_rule_is_cycles_rule(self):
        cases = []
        for source in ("FILE", "SEQUENCE", "MOVIE", "TILED", "GENERATED"):
            for packed in (False, True):
                for dirty in (False, True):
                    for generated_tile in ((False, True) if source == "TILED" else (False,)):
                        image = Image(source, self.write("r.png", b"r"), tiles=(1001, 1002))
                        if packed:
                            image.packed = {1001: b"r"}
                        if dirty:
                            image.held = {1001: ("painted", True)}
                        image.tiles[1].is_generated_tile = generated_tile
                        cases.append((source, packed, dirty, generated_tile, image))
        for source, packed, dirty, generated_tile, image in cases:
            with self.subTest(source=source, packed=packed, dirty=dirty, generated_tile=generated_tile):
                self.assertEqual(self.images.reads_memory(image), cycles_copies_from_memory(image))

    def test_cycles_source_still_decides_as_the_add_on_reads(self):
        """``reads_memory`` mirrors ``image_is_builtin``: a change to Cycles' rule must be carried to the add-on."""
        text = CYCLES_UTIL.read_text(encoding="utf-8")
        body = re.search(r"static inline bool image_is_builtin\(([^)]*)\)\s*\{(.*?)\n\}", text, re.S)
        self.assertIsNotNone(body, "image_is_builtin is in intern/cycles/blender/util.h")
        words = " ".join(re.sub(r"/\*.*?\*/", "", body.group(2), flags=re.S).split())
        self.assertEqual(words, " ".join("""
            const blender::eImageSource image_source = blender::eImageSource(ima.source);
            if (image_source == blender::IMA_SRC_TILED) {
              for (blender::ImageTile &tile : ima.tiles) { if (tile.gen_flag & blender::IMA_GEN_TILE) { return true; } }
            }
            return BKE_image_has_packedfile(&ima) || image_source == blender::IMA_SRC_GENERATED ||
                   image_source == blender::IMA_SRC_MOVIE || BKE_image_is_dirty(&ima) ||
                   ((engine.flag & blender::RE_ENGINE_PREVIEW) != 0 && image_source != blender::IMA_SRC_SEQUENCE);
            """.split()))
        # A bake is never a preview render: the last condition never holds for it.

    def test_cycles_loader_still_reads_as_the_native_reader_does(self):
        """bpy_angelo_digest.cc's ``pixels`` repeats Cycles' loader: the node's image user at the frame computed from
        the scene frame, each UDIM tile through the image user's tile, and an image that held nothing freed once
        read. A change to any of these in Cycles must be carried to the reader."""
        shader = " ".join(CYCLES_SHADER.read_text(encoding="utf-8").split())
        for line in ("const int scene_frame = b_scene.r.cfra;",
                     "const int image_frame = image_user_frame_number(b_image_user, *b_image, scene_frame);",
                     "const bool is_builtin = image_is_builtin(*b_image, b_engine);"):
            self.assertIn(line, shader)
        loader = " ".join(CYCLES_IMAGE.read_text(encoding="utf-8").split())
        for line in ("free_cache(!is_preview_render && !BKE_image_has_loaded_ibuf(b_image)),",
                     "this->b_iuser.framenr = frame;", "this->b_iuser.tile = tile_number;",
                     "blender::ImBuf *ibuf = BKE_image_acquire_ibuf(b_image, &b_iuser, &lock);",
                     "is_float = ibuf->float_data() != nullptr;"):
            self.assertIn(line, loader)

    def test_an_image_held_nowhere_is_decoded_once_per_source(self):
        image = self.packed_image()
        before = Image.decodes
        first = self.digest(image)
        decoded = Image.decodes - before
        self.assertGreater(decoded, 0)
        for _ in range(3):   # Exports in a row, nothing held: the remembered decode.
            self.assertEqual(self.digest(image), first)
        self.assertEqual(Image.decodes - before, decoded, "decoded once")
        image.view()
        self.assertEqual(self.digest(image), first, "held from the same source")
        image.reload()
        image.packed[0] = b"other pixels"   # A new source is a new key: decoded again, and different.
        self.assertNotEqual(self.digest(image), first)
        self.assertGreater(Image.decodes - before, decoded)

    def test_the_decode_cache_keys_its_source_without_reading_it(self):
        # The merge of the bake-buffer work with change_keys (break-it review of e8dd8aefa34): ``_source`` is a cache's
        # key, so a file in its clock's step makes a key used once (decoded again, never remembered), never a read of
        # its bytes, which the detector's form costs a whole movie twice per export on a coarse volume.
        image = Image("MOVIE", self.write("cache.mov", b"take 1"))
        keys, clock = self.images.change_keys, self.images.change_keys.change_clock
        reads = []
        digest = keys._digest
        with patch.object(keys, "_digest", lambda *args: reads.append(args[0]) or digest(*args)):
            settled = [self.images._source(image, USER, 1, [0], None) for _ in range(2)]
            with patch.multiple(clock, settle=lambda *_a, **_k: (_ for _ in ()).throw(clock.Unsettled("tick")),
                                settled=lambda *_a, **_k: False):
                unsettled = [self.images._source(image, USER, 1, [0], None) for _ in range(2)]
        self.assertEqual(settled[0], settled[1], "a settled source is one key")
        self.assertNotEqual(unsettled[0], unsettled[1], "a source in its clock's step is never remembered")
        self.assertEqual(reads, [], "a cache's key reads no file's bytes")

    def test_without_the_native_reader_only_a_still_single_buffer_is_known(self):
        images = self.images
        still = self.packed_image()
        udim = self.udim_with_generated_tile()
        with patch.object(images, "NATIVE", None):
            pixels = [0.25] * 16
            still.pixels = type("Pixels", (list,), {"foreach_get": lambda self, out: out.__setitem__(
                slice(None), pixels)})(pixels)
            self.assertEqual(self.digest(still), self.digest(still))
            self.assertNotEqual(self.digest(udim), self.digest(udim), "unknown is stale")


if __name__ == "__main__":
    unittest.main()
