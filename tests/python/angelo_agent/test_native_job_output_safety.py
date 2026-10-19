# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Published-picture confinement through discovery, stable copy and actual pixel consumption.

Every outside file here is a planted marker inside the fixture's own temporary
root. Native decoding is covered separately; this fixture uses the ordinary
finished-picture harness to read the PNG bytes delivered to the model.
"""

import io
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import checkpoint_filesystem as cf, job_store  # noqa: E402
from test_native_view_job_output import OutputCase  # noqa: E402
from test_native_view_finished import GREY, RED, picture  # noqa: E402


class PublishedSafetyTests(OutputCase):
    def test_ancestor_output_nested_and_leaf_links_are_refused_before_decoding(self):
        for number, component in enumerate(("ancestor", "output", "nested", "leaf")):
            with self.subTest(component=component):
                name = "nested/image.png"
                folder = "case%d/shot" % number
                self.published(folder, {name: GREY}, [{"file": name}])
                source = self.folder / folder / name
                outside = self.folder / ("outside%d" % number)
                outside.mkdir()
                tail = {"ancestor": "shot/nested/image.png", "output": "nested/image.png",
                        "nested": "image.png", "leaf": "image.png"}[component]
                self.file("outside%d/%s" % (number, tail), picture(64, 36, RED))
                chosen = {"ancestor": source.parents[2], "output": source.parents[1],
                          "nested": source.parent, "leaf": source}[component]
                chosen.rename(chosen.with_name(chosen.name + "-kept"))
                chosen.symlink_to(outside / "image.png" if component == "leaf" else outside,
                                  target_is_directory=component != "leaf")
                self.assertIn("moved or deleted", self.refusal(file=name))
                self.assertEqual(self.reference_files(), [])

    def test_legacy_still_and_animation_do_not_follow_ancestor_links(self):
        for kind in ("RENDER_STILL", "RENDER_ANIMATION"):
            with self.subTest(kind=kind):
                prefix = kind.lower()
                original = self.file(prefix + "/frame_0001.png", picture(64, 36, GREY))
                self.job(original if kind == "RENDER_STILL" else original.parent, kind=kind)
                original.parent.rename(original.parent.with_name(prefix + "-kept"))
                planted = self.file(prefix + "-outside/frame_0001.png", picture(64, 36, RED))
                original.parent.symlink_to(planted.parent, target_is_directory=True)
                self.assertIn("moved or deleted", self.refusal())
                self.assertEqual(self.reference_files(), [])

    def test_frame_listing_does_not_follow_directory_links(self):
        name = "nested/frame_####.png"
        self.published("frames", {"nested/frame_0001.png": GREY}, [{"file": name}],
                       kind="RENDER_ANIMATION", detail={"frames": [1, 2]})
        nested = self.folder / "frames/nested"
        nested.rename(nested.with_name("kept"))
        planted = self.file("outside/frame_0001.png", picture(64, 36, RED))
        nested.symlink_to(planted.parent, target_is_directory=True)
        self.assertEqual(job_store.published_frames(self.folder / "frames", self.records["j3"], name), [])
        self.assertIn("kept no frames", self.refusal(file=name))

    def test_a_hard_link_cannot_import_an_unpublished_image(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        source = self.folder / "shot/image.png"
        source.unlink()
        planted = self.file("unpublished.png", picture(64, 36, RED))
        os.link(planted, source)
        self.assertIn("moved or deleted", self.refusal(file="image.png"))

    def test_an_ordinary_output_edited_before_capture_remains_viewable_and_is_labelled_current(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        self.file("shot/image.png", picture(64, 36, RED))
        result = self.show(file="image.png")
        self.assertEqual(self.colours(result), {RED})
        self.assertEqual(result["rendered"]["source"], "CURRENT_OUTPUT_FILE")
        self.assertIn("recorded engine and samples describe the original job", result["note"])

    def test_a_leaf_swapped_after_lookup_is_not_opened(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        source = self.folder / "shot/image.png"
        planted = self.file("unpublished.png", picture(64, 36, RED))
        real = job_store.copy_output

        def swap(path, destination, maximum):
            source.rename(source.with_name("kept.png"))
            source.symlink_to(planted)
            return real(path, destination, maximum)

        with patch.object(job_store, "copy_output", side_effect=swap):
            self.assertIn("could not be read safely", self.refusal(file="image.png"))
        self.assertEqual(self.reference_files(), [])

    def test_an_ancestor_swapped_during_the_copy_is_refused(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        source = self.folder / "shot/image.png"
        planted = self.file("unpublished/image.png", picture(64, 36, RED))
        real = cf.copy_stable_file

        def swap(folder, name, destination, maximum=None):
            opened = real(folder, name, destination, maximum=maximum)
            source.parent.rename(source.parent.with_name("kept"))
            source.parent.symlink_to(planted.parent, target_is_directory=True)
            return opened

        with patch.object(cf, "copy_stable_file", side_effect=swap):
            self.assertIn("could not be read safely", self.refusal(file="image.png"))
        self.assertEqual(self.reference_files(), [])

    def test_regular_file_swap_and_swap_back_inside_copy_is_refused(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        source = self.folder / "shot/image.png"
        planted = self.file("unpublished.png", picture(64, 36, RED))
        original = source.with_name("kept.png")
        real = cf.copy_stable_file

        def swap(folder, name, destination, maximum=None):
            source.rename(original)
            planted.rename(source)
            try:
                return real(folder, name, destination, maximum=maximum)
            finally:
                source.rename(planted)
                original.rename(source)

        with patch.object(cf, "copy_stable_file", side_effect=swap):
            self.assertIn("could not be read safely", self.refusal(file="image.png"))
        self.assertEqual(self.reference_files(), [])

    def test_decoder_uses_the_admitted_copy_after_original_is_swapped(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        source = self.folder / "shot/image.png"
        planted = self.file("unpublished.png", picture(64, 36, RED))
        real = self.blender.imbuf.load
        consumed = []

        def swap(path):
            consumed.append(Path(path))
            source.rename(source.with_name("kept.png"))
            source.symlink_to(planted)
            return real(path)

        with patch.object(self.blender.imbuf, "load", side_effect=swap):
            result = self.show(file="image.png")
        self.assertEqual(self.colours(result), {GREY})
        self.assertNotEqual(consumed[0], source)
        self.assertFalse(consumed[0].exists(), "The private input copy is discarded after decoding")
        self.assertEqual(len(self.reference_files()), 1, "Only the delivered picture remains")

    def test_changed_source_bytes_fail_the_stable_copy_and_are_cleaned_up(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        source = self.folder / "shot/image.png"
        real = job_store.copy_output

        class ChangingDestination(io.BytesIO):
            def write(self, data):
                source.write_bytes(b"changed")
                return super().write(data)

        with self.assertRaises(ValueError):
            real(source, ChangingDestination(), 1_000_000)

    def test_oversized_input_is_refused_before_copy_and_decode(self):
        self.published("shot", {"image.png": GREY}, [{"file": "image.png"}])
        with patch("angelo_agent.view_picture.MAX_BYTES", 16):
            self.assertIn("could not be read safely", self.refusal(file="image.png"))
        self.assertEqual(self.reference_files(), [])


if __name__ == "__main__":
    unittest.main()
