# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The bundled Human Base Meshes outside Blender: the table, landmarks, provenance and the files.

The in-app check that placing each file gives the table's objects is ``test_blender_chosen_tools``.
"""

import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))

from angelo_agent import human_base_meshes as hbm  # noqa: E402

LFS_POINTER = b"version https://git-lfs"


class TableTests(unittest.TestCase):
    def test_names_match_whatever_their_spacing_or_case(self):
        # The bundle's own names carry a double space ("Hand  - Realistic") and trailing spaces.
        hand = next(a for a in hbm.table()["assets"] if a["name"] == "Hand  - Realistic")
        self.assertTrue(hbm._matches(hand, "hand - realistic"))
        head = next(a for a in hbm.table()["assets"] if a["file"] == "head_sculpting_realistic.blend")
        self.assertTrue(hbm._matches(head, "head_sculpting_realistic"))

    def test_heads_state_their_landmarks(self):
        heads = [a for a in hbm.table()["assets"] if a["kind"] == "head"]
        self.assertEqual(len(heads), 5)
        for head in heads:
            with self.subTest(head=head["name"]):
                marks = head["landmarks"]
                self.assertLess(marks["nose_tip"][1], 0)   # Faces -Y.
                self.assertAlmostEqual(marks["neck_bottom_z"], -head["size"][2] / 2, places=3)
                if "eye_L" in marks:
                    self.assertGreater(marks["eye_L"][0], 0)   # +X is its left.
                    self.assertLess(marks["eye_R"][0], 0)

    def test_whole_refuses_a_pointer_or_a_missing_file(self):
        asset = hbm.table()["assets"][0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / asset["file"]
            self.assertFalse(hbm.whole(str(path), asset))
            path.write_bytes(LFS_POINTER + b" spec/v1\n")
            self.assertFalse(hbm.whole(str(path), asset))
            path.write_bytes(b"x" * asset["bytes"])
            self.assertTrue(hbm.whole(str(path), asset))
        self.assertFalse(hbm.whole(None, asset))


class ProvenanceTests(unittest.TestCase):
    def test_source_is_the_pinned_official_bundle_under_cc0(self):
        source = hbm.table()["source"]
        self.assertTrue(source["url"].startswith("https://download.blender.org/"))
        self.assertRegex(source["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(source["license"], "CC0-1.0")
        self.assertTrue((hbm.FOLDER / "LICENSE.txt").is_file())

    def test_the_folder_holds_exactly_the_tables_files(self):
        listed = {asset["file"] for asset in hbm.table()["assets"]}
        on_disk = {path.name for path in hbm.FOLDER.glob("*.blend")}
        self.assertEqual(listed, on_disk)

    def test_files_match_their_digests(self):
        """The file, or the Git LFS pointer a checkout holds instead (its oid is the file's SHA-256), so a host run
        without LFS objects still checks every file."""
        for asset in hbm.table()["assets"]:
            data = (hbm.FOLDER / asset["file"]).read_bytes()
            with self.subTest(asset=asset["file"]):
                if data.startswith(LFS_POINTER):
                    fields = dict(line.split(" ", 1) for line in data.decode().splitlines() if " " in line)
                    self.assertEqual((int(fields["size"]), fields["oid"]), (asset["bytes"], "sha256:" + asset["sha256"]))
                else:
                    self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (asset["bytes"], asset["sha256"]))

    def test_eye_landmarks_are_eyeball_centres(self):
        """Never a lid's or an iris's: an eyelid named "eyelid" read as an eye 2.5-3.6 mm off."""
        for asset in hbm.table()["assets"]:
            balls = {row["role"][-1]: row for row in asset["objects"] if row["role"] in ("eye L", "eye R",
                                                                                       "sclera L", "sclera R")}
            for side in ("L", "R"):
                if "eye_" + side in asset["landmarks"]:
                    with self.subTest(asset=asset["name"], side=side):
                        row = balls[side]
                        centre = [(a + b) / 2 for a, b in zip(row["box_min"], row["box_max"])]
                        for got, want in zip(asset["landmarks"]["eye_" + side], centre):
                            self.assertAlmostEqual(got, want, delta=0.0002)

    def test_table_names_no_machine(self):
        text = hbm.TABLE.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"/Users/|/home/|[A-Za-z]:\\\\Users", text))


if __name__ == "__main__":
    unittest.main()
