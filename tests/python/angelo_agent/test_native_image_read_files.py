# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which file Blender reads for an image's tile, frame or view, and whether a tiled image's files are there.

``blend_file_paths`` names each file as Blender's own source does (``BKE_image_user_frame_get``,
``BLI_path_sequence_decode``/``_encode``, ``BKE_image_get_tile_strformat``, ``BKE_scene_multiview_view_prefix_get``,
``BKE_bpath_missing_files_check``); these cases follow that source. ``test_blender_image_read_files.py`` checks the
same names against Blender's ``Image.filepath_from_user`` in the built app (1.0.10 audit, P3-2 and P3-4).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
from angelo_agent import blend_file, blend_file_paths as paths  # noqa: E402
from angelo_agent.read_spec import validate_read  # noqa: E402
from host_test_policy import environment_skip  # noqa: E402
from test_native_blend_file import block, open_file  # noqa: E402

POSIX = unittest.skipUnless(os.name == "posix", environment_skip(
    "POSIX path spelling: these paths are macOS and Linux ones, and Windows reads either separator as written"))


class FrameNumberTests(unittest.TestCase):
    def test_the_frame_an_image_user_reads(self):
        number = paths.frame_number
        # Held within 0..duration, counted from the start frame as 1.
        self.assertEqual([number(frame, 3, 1, 0, False) for frame in (-5, 0, 1, 2, 3, 4, 50)], [0, 0, 1, 2, 3, 3, 3])
        self.assertEqual([number(frame, 4, 10, 0, False) for frame in (9, 10, 13, 14)], [0, 1, 4, 4])
        # Cyclic: wrapped into 1..duration, below the start too (C's remainder, then made positive).
        self.assertEqual([number(frame, 3, 1, 0, True) for frame in (-1, 0, 1, 3, 4, 7)], [2, 3, 1, 3, 1, 1])
        self.assertEqual([number(frame, 3, 5, 0, True) for frame in (1, 4, 5, 8)], [3, 3, 1, 1])
        # The offset shifts the result, after the wrap or the hold.
        self.assertEqual([number(frame, 3, 1, 10, False) for frame in (0, 2, 9)], [10, 12, 13])
        self.assertEqual(number(4, 3, 1, -1, True), 0)
        # No duration: frame 0, whatever the offset.
        self.assertEqual(number(7, 0, 1, 25, True), 0)


class FrameFileTests(unittest.TestCase):
    def test_the_last_digit_run_of_the_name_is_the_frame(self):
        name = paths.frame_file_path
        self.assertEqual(name("/p/seq.0001.png", 12), "/p/seq.0012.png")
        self.assertEqual(name("/p/seq.0001.png", 12345), "/p/seq.12345.png")   # Wider than the run: not cut.
        self.assertEqual(name("/p/seq.0001.png", 0), "/p/seq.0000.png")
        self.assertEqual(name("/p/seq.0001.png", -4), "/p/seq.0000.png")       # Never negative.
        self.assertEqual(name("/p/seq.1.png", 120), "/p/seq.120.png")
        self.assertEqual(name("/p/shot2_take3_0007.exr", 9), "/p/shot2_take3_0009.exr")
        self.assertEqual(name("/p/render-007-final.png", 8), "/p/render-008-final.png")   # Text after the digits.
        self.assertEqual(name("/p/a1.b2", 5), "/p/a5.b2")                     # The extension's digits never count.
        self.assertEqual(name("/p/seq_01", 7), "/p/seq_07")                     # No extension.
        self.assertEqual(name("/p/.0001", 3), "/p/.0003")                       # A leading dot is not an extension.
        self.assertEqual(name("/p/a..png", 3), "/p/a.3.png")
        self.assertEqual(name("//seq.0001.png", 2), "//seq.0002.png")           # Relative to the .blend.
        self.assertEqual(name("C:\\r\\s.001.png", 2), "C:\\r\\s.002.png")    # Either separator.
        self.assertEqual(name("/p/v12/seq.png", 5), "/p/v12/seq5.png")          # Folders never count.
        self.assertEqual(name("/p/v12\\seq.png", 5), "/p/v12\\seq5.png")
        self.assertEqual(name("/p/v12/seq.png", 0), "/p/v12/seq.png")           # Frame 0 without digits: nothing.
        self.assertEqual(name("/p/seq.####.png", 5), "/p/seq.####5.png")        # '#' is no frame mark here.
        # A run that overflows a 32-bit int is no frame: the frame goes before the extension.
        self.assertEqual(name("/p/seq.99999999999.png", 3), "/p/seq.999999999993.png")
        self.assertEqual(name("/p/seq.2147483647.png", 3), "/p/seq.0000000003.png")
        self.assertEqual(name("/p/seq.2147483648.png", 3), "/p/seq.21474836483.png")
        self.assertEqual(name("/p/s\u00e9q\u0661.0001.png", 2), "/p/s\u00e9q\u0661.0002.png")   # ASCII digits only.
        self.assertEqual(name("/p/\u0661\u0662.png", 2), "/p/\u0661\u06622.png")


def render(*views, multiview=True, setup="MULTIVIEW"):
    return SimpleNamespace(use_multiview=multiview, views_format=setup,
                           views=[SimpleNamespace(name=name, file_suffix=suffix, use=use)
                                  for name, suffix, use in views])


class ViewFileTests(unittest.TestCase):
    STEREO = (("left", "_L", True), ("right", "_R", True))

    def test_the_views_whose_files_blender_reads(self):
        image = SimpleNamespace(use_multiview=True, views_format="INDIVIDUAL")
        self.assertEqual(paths.view_suffixes(image, render(*self.STEREO)), ("_L", "_R"))
        self.assertEqual(paths.view_suffixes(image, render(*self.STEREO, multiview=False)), ())
        self.assertEqual(paths.view_suffixes(SimpleNamespace(use_multiview=False, views_format="INDIVIDUAL"),
                                             render(*self.STEREO)), ())
        self.assertEqual(paths.view_suffixes(SimpleNamespace(use_multiview=True, views_format="STEREO_3D"),
                                             render(*self.STEREO)), ())   # Both views in one file.
        views = self.STEREO + (("top", "_T", True), ("off", "_O", False))
        self.assertEqual(paths.view_suffixes(image, render(*views)), ("_L", "_R", "_T"))
        # The Stereo 3D setup reads only the left and right views.
        self.assertEqual(paths.view_suffixes(image, render(*views, setup="STEREO_3D")), ("_L", "_R"))

    def test_each_views_file(self):
        files = paths.view_file_paths
        self.assertEqual(files("/p/shot_L.png", ("_L", "_R")), ["/p/shot_L.png", "/p/shot_R.png"])
        self.assertEqual(files("/p/shot_R.png", ("_L", "_R")), ["/p/shot_L.png", "/p/shot_R.png"])
        self.assertEqual(files("/p/shot.png", ("_L", "_R")), [])          # No view suffix: one file.
        self.assertEqual(files("/p/shot_L", ("_L", "_R")), [])            # No extension.
        self.assertEqual(files("_L.png", ("_L", "_R")), [])               # Nothing before the suffix.
        self.assertEqual(files("/p/_L.png", ("_L", "_R")), ["/p/_L.png", "/p/_R.png"])   # The folder is the prefix.
        self.assertEqual(files("/p/shot_L.png", ()), [])
        # The first suffix (in the scene's order) that the name ends with decides, an empty one included.
        self.assertEqual(files("/p/shotL.png", ("L", "")), ["/p/shotL.png", "/p/shot.png"])
        self.assertEqual(files("/p/shot.png", ("", "_R")), ["/p/shot.png", "/p/shot_R.png"])
        # The last dot anywhere in the path splits it (BLI_str_rpartition).
        self.assertEqual(files("/p_L.v/shot", ("_L", "_R")), ["/p_L.v/shot", "/p_R.v/shot"])
        self.assertEqual(files("/p.v_L/shot", ("_L", "_R")), [])

    def test_read_files_combines_views_with_frames_and_tiles(self):
        read = paths.read_files
        self.assertEqual(read("/p/a.png", "FILE"), ["/p/a.png"])
        self.assertEqual(read("/p/a.mov", "MOVIE", frame=40), ["/p/a.mov"])
        self.assertEqual(read("//s.0001.png", "SEQUENCE", frame=7), ["//s.0007.png"])
        self.assertEqual(read("/p/t.<UDIM>.png", "TILED", tiles=(1002, 1011)), ["/p/t.1002.png", "/p/t.1011.png"])
        self.assertEqual(read("/p/t.png", "TILED", tiles=(1001, 1002)), ["/p/t.png"])   # No token: one file.
        self.assertEqual(read("/p/s.0001_L.png", "SEQUENCE", frame=3, suffixes=("_L", "_R")),
                         ["/p/s.0003_L.png", "/p/s.0003_R.png"])
        # A view suffix with digits is the name's last digit run, as Blender decodes the view's path.
        self.assertEqual(read("/p/s.0001_v1.png", "SEQUENCE", frame=3, suffixes=("_v1", "_v2")),
                         ["/p/s.0001_v3.png"])
        self.assertEqual(read("/p/t.<UDIM>_L.png", "TILED", tiles=(1002,), suffixes=("_L", "_R")),
                         ["/p/t.1002_L.png", "/p/t.1002_R.png"])


class TileAndSpellingTests(unittest.TestCase):
    def test_a_tile_name_is_a_printf_format_in_blender_and_plain_in_cycles(self):
        # BKE: the path with its token replaced is a printf format, so %% reads as %.
        self.assertEqual(paths.tile_file_path("/p/100%%.<UDIM>.png", 1002), "/p/100%.1002.png")
        self.assertEqual(paths.tile_file_path("/p/a.<UVTILE>%%.png", 1012), "/p/a.u2_v2%.png")
        # Any other % is undefined in C: named with the token replaced, as Cycles names it.
        self.assertEqual(paths.tile_file_path("/p/50%_<UDIM>.png", 1002), "/p/50%_1002.png")
        self.assertEqual(paths.tile_file_path("/p/a%d.<UDIM>.png", 1002), "/p/a%d.1002.png")
        # Cycles, reading a tile's file itself, replaces both tokens; Blender only the one that wins.
        self.assertEqual(paths.cycles_tile_file_path("/p/t.<UDIM>_<UVTILE>.png", 1002), "/p/t.1002_u2_v1.png")
        self.assertEqual(paths.tile_file_path("/p/t.<UDIM>_<UVTILE>.png", 1002), "/p/t.1002_<UVTILE>.png")
        self.assertEqual(paths.cycles_tile_file_path("/p/100%%.<UDIM>.png", 1002), "/p/100%%.1002.png")
        self.assertIsNone(paths.cycles_tile_file_path("/p/t.png", 1002))
        # A bake may read either: both are named, once each.
        self.assertEqual(paths.read_files("/p/t.<UDIM>_<UVTILE>.png", "TILED", tiles=(1002,)),
                         ["/p/t.1002_<UVTILE>.png", "/p/t.1002_u2_v1.png"])
        self.assertEqual(paths.read_files("/p/t.<UVTILE>.png", "TILED", tiles=(1002,)), ["/p/t.u2_v1.png"])

    @POSIX
    def test_a_path_saved_on_windows_is_spelled_as_blender_reads_it_here(self):
        spell = paths.native_spelling
        self.assertEqual(spell("//textures\\bricks.0001.png"), "//textures/bricks.0001.png")
        self.assertEqual(spell("C:\\Art\\a.png"), "/c/Art/a.png")
        self.assertEqual(spell("d:/Art/a.png"), "/d/Art/a.png")
        self.assertEqual(spell("\\\\server\\share\\a.png"), "\\\\server/share/a.png")
        self.assertEqual(spell("\\\\?\\C:\\a.png"), "\\\\?\\C:/a.png")
        self.assertEqual(spell("/p/plain.png"), "/p/plain.png")
        self.assertEqual(spell("C:a.png"), "C:a.png")   # A drive without a slash is no drive path.

    @POSIX
    def test_a_linked_images_path_is_the_respelled_librarys_folder(self):
        def abspath(path, start=None):   # bpy.path.abspath: // is the open file's folder unless start says.
            return os.path.join("/proj" if start is None else start, path[2:]) if path.startswith("//") else path
        library = SimpleNamespace(filepath="//libs\\mat.blend")   # Both saved on Windows.
        self.assertEqual(paths.located("//tex\\a.png", library, abspath), "/proj/libs/tex/a.png")
        self.assertEqual(paths.located("//tex\\a.png", None, abspath), "/proj/tex/a.png")
        self.assertEqual(paths.located("C:\\Art\\a.png", library, abspath), "/c/Art/a.png")
        self.assertEqual(paths.located("//tex/a.png", SimpleNamespace(filepath="/lib/m.blend"), abspath),
                         "/lib/tex/a.png")


class FirstTileTests(unittest.TestCase):
    @POSIX
    def test_a_tile_set_is_there_when_its_first_tile_is(self):
        asked = []

        def exists(path):
            asked.append(path)
            return path.endswith(("tile.1002.png", "tile.u2_v1.png"))
        self.assertEqual(paths.state("//tile.<UDIM>.png", "/proj", exists=exists, tile=1002), paths.PRESENT)
        self.assertEqual(paths.state("//tile.<UVTILE>.png", "/proj", exists=exists, tile=1002), paths.PRESENT)
        self.assertEqual(paths.state("//tile.<UDIM>.png", "/proj", exists=exists, tile=1001), paths.MISSING)
        # Not a tiled image: the path as written, a token included (Blender resolves tokens of tiled images only).
        self.assertEqual(paths.state("//tile.<UDIM>.png", "/proj", exists=exists), paths.MISSING)
        self.assertEqual(asked, ["/proj/tile.1002.png", "/proj/tile.u2_v1.png", "/proj/tile.1001.png",
                                 "/proj/tile.<UDIM>.png"])

    def test_first_tile_is_the_tiled_images_first(self):
        tiles = [SimpleNamespace(number=number) for number in (1002, 1003, 1005)]
        self.assertEqual(paths.first_tile(SimpleNamespace(source="TILED", tiles=tiles)), 1002)
        self.assertIsNone(paths.first_tile(SimpleNamespace(source="FILE", tiles=tiles)))
        self.assertIsNone(paths.first_tile(SimpleNamespace(source="TILED", tiles=[])))
        self.assertIsNone(paths.first_tile(SimpleNamespace(name="a sound")))

    @POSIX
    def test_the_open_file_lists_a_tile_set_without_1001_as_present(self):
        tiles = [SimpleNamespace(number=number) for number in (1002, 1003, 1004, 1005)]
        image = block("images", "Bricks", packed_file=None, packed_files=[], source="TILED", tiles=tiles)
        bpy = open_file(images=[image], paths={image: {"//textures/bricks.<UDIM>.png"}})
        on_disk = {"/Users/alice/Client X/island/textures/bricks.%d.png" % tile.number for tile in tiles}
        with patch("angelo_agent.native_programs.runtime_is_staging", lambda: False), \
                patch.object(os.path, "exists", lambda path: path in on_disk):
            result = blend_file.read(bpy, validate_read("angelo_blend_file", {}),
                                     SimpleNamespace(root="/Users/alice/Client X/island"))
        json.dumps(result, allow_nan=False)
        self.assertEqual(result["external_files"], {"total": 1, "present": 1, "items": [
            {"type": "images", "name": "Bricks", "file": "textures/bricks.<UDIM>.png", "status": "present"}]})


class ImageFilesTests(unittest.TestCase):
    """``image_files``: the files a reader of an image opens, as its node's image user reads it at a scene frame,
    shared by the baked-material digest and the World picture's key."""

    def image(self, source, path, tiles=()):
        return SimpleNamespace(source=source, filepath_raw=path, filepath=path, library=None,
                               tiles=[SimpleNamespace(number=number, is_generated_tile=generated)
                                      for number, generated in tiles])

    @POSIX
    def test_the_frame_tile_or_file_a_reader_opens(self):
        absolute = lambda path, library=None: path   # noqa: E731
        user = SimpleNamespace(frame_duration=3, frame_start=1, frame_offset=0, use_cyclic=False)
        sequence = self.image("SEQUENCE", "/sky/sky.0001.png")
        self.assertEqual(paths.image_files(sequence, user, 2, absolute), ["/sky/sky.0002.png"])
        self.assertEqual(paths.image_files(sequence, user, 9, absolute), ["/sky/sky.0003.png"], "held at its last")
        self.assertEqual(paths.image_files(sequence, None, 2, absolute), ["/sky/sky.0001.png"], "no image user")
        tiled = self.image("TILED", "/t/paint.<UDIM>.png", [(1001, False), (1002, True), (1011, False)])
        self.assertEqual(paths.image_files(tiled, user, 1, absolute), ["/t/paint.1001.png", "/t/paint.1011.png"],
                         "a generated tile has no file")
        self.assertEqual(paths.image_files(tiled, user, 1, absolute, tiles=(1002,)), ["/t/paint.1002.png"])
        self.assertEqual(paths.image_files(self.image("MOVIE", "/m/clip.mov"), user, 2, absolute), ["/m/clip.mov"])
        stereo = self.image("FILE", "/v/shot_L.png")
        self.assertEqual(paths.image_files(stereo, user, 1, absolute, suffixes=("_L", "_R")),
                         ["/v/shot_L.png", "/v/shot_R.png"])


if __name__ == "__main__":
    unittest.main()
