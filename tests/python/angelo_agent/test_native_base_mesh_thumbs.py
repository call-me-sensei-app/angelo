# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every base mesh in the + menu has its own picture (1.0.5 review SD3, ``base_mesh_thumbs``).

The pictures are the bundled base meshes' Essentials asset previews at 96 px. Each option resolves to an existing,
distinct PNG that the add-on's own reader decodes (no ``bpy.data`` image), and the base-mesh rows carry it.
A git-archive snapshot holds LFS pointers instead of PNGs; there only the files' presence and distinctness are checked.
"""

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
ADDON = ROOT / "scripts/addons_core/angelo_agent"


def _module(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location("angelo_agent." + name, ADDON / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BaseMeshThumbTests(unittest.TestCase):
    def setUp(self):
        import json
        self.assets = json.loads((ADDON / "human_base_meshes.json").read_text(encoding="utf-8"))["assets"]
        self.thumbs = _module("base_mesh_thumbs")

    def test_each_base_mesh_has_an_existing_distinct_picture(self):
        folder = ADDON / "assets" / "base_mesh_thumbs"
        digests = {}
        for asset in self.assets:
            path = folder / (Path(asset["file"]).stem + ".png")
            self.assertTrue(path.is_file(), "%s has no picture" % asset["name"])
            digests[asset["name"]] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(len(set(digests.values())), len(self.assets), "every base mesh its own picture")

    def test_each_picture_decodes_to_a_96_px_tile_with_a_subject(self):
        folder = ADDON / "assets" / "base_mesh_thumbs"
        for asset in self.assets:
            data = (folder / (Path(asset["file"]).stem + ".png")).read_bytes()
            if data.startswith(b"version https://git-lfs"):
                self.skipTest("LFS pointers in this checkout (git-archive snapshot)")
            width, height, rows = self.thumbs.decode_png(data)
            self.assertEqual((width, height), (96, 96), asset["name"])
            self.assertEqual(rows[0][3], 0, "a transparent corner: %s" % asset["name"])
            centre = rows[48][48 * 4:48 * 4 + 4]
            self.assertGreater(sum(rows[y][x * 4 + 3] for y in range(30, 66) for x in range(30, 66)), 0,
                               "something drawn in the middle: %s (%r)" % (asset["name"], centre))

    def test_the_base_mesh_rows_carry_their_picture(self):
        source = (ADDON / "chosen_tools.py").read_text(encoding="utf-8")
        self.assertIn('"thumbnail": row[0]', source.split("\ndef base_mesh_rows(", 1)[1].split("\ndef ", 1)[0])
        draw = (ADDON / "chat_add_menu.py").read_text(encoding="utf-8").split("\ndef draw_row(", 1)[1].split(
            "\ndef ", 1)[0]
        self.assertIn("base_mesh_thumbs.draw(view, thumbnail", draw)
        self.assertNotIn("bpy.data.images", (ADDON / "base_mesh_thumbs.py").read_text(encoding="utf-8"),
                         "a picture never becomes an image in the person's file")


if __name__ == "__main__":
    unittest.main()
