# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Individual compositor files must validate view suffixes even with a combined main image."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import job_render_outputs as outputs  # noqa: E402


def node(*, multilayer=False, linked=True, views_format="INDIVIDUAL"):
    extension = ".exr" if multilayer else ".png"
    return {"folder": "Passes", "name": "Passes", "multilayer": multilayer, "extension": extension,
            "views_format": views_format,
            "items": [{"name": "Beauty", "extension": extension, "views_format": views_format, "linked": linked}]}


class SuffixTests(unittest.TestCase):
    def test_every_individual_output_refuses_unsafe_or_duplicate_suffixes(self):
        for main in ("INDIVIDUAL", "MULTIVIEW", "STEREO_3D"):
            for multilayer in (False, True):
                for suffixes in (("_same", "_same"), ("/../../../escaped", "_R"), ("_#", "_R"),
                                 ("_{blend_name}", "_R"), ("\\escaped", "_R"), ("\n", "_R")):
                    with self.subTest(main=main, multilayer=multilayer, suffixes=suffixes):
                        views = list(zip(("left", "right"), suffixes))
                        with self.assertRaisesRegex(outputs.PlanError, "own file suffix"):
                            outputs.plan(".exr", views, main, [node(multilayer=multilayer)])

    def test_valid_individual_outputs_are_kept_with_combined_main(self):
        for multilayer in (False, True):
            with self.subTest(multilayer=multilayer):
                plan = outputs.plan(".exr", [("left", "_L"), ("right", "_R")], "MULTIVIEW",
                                    [node(multilayer=multilayer)])
                stem, extension = ("layers", ".exr") if multilayer else ("Beauty", ".png")
                self.assertEqual([outputs.file_name(output, 1) for output in plan],
                                 ["frame_0001.exr", "Passes/" + stem + "0001_L" + extension,
                                  "Passes/" + stem + "0001_R" + extension])

    def test_unused_suffixes_are_allowed_for_combined_or_unlinked_outputs(self):
        views = [("left", "/unused"), ("right", "/unused")]
        for multilayer in (False, True):
            for linked in (False, True):
                with self.subTest(multilayer=multilayer, linked=linked):
                    plan = outputs.plan(".exr", views, "MULTIVIEW",
                                        [node(multilayer=multilayer, linked=linked,
                                              views_format="MULTIVIEW" if linked else "INDIVIDUAL")])
                    self.assertEqual(len(plan), 2 if linked else 1)
                    self.assertTrue(all(output["suffix"] == "" for output in plan))

    def test_per_item_override_is_checked_independently_of_its_node(self):
        item_override = node(views_format="MULTIVIEW")
        item_override["items"][0]["views_format"] = "INDIVIDUAL"
        with self.assertRaisesRegex(outputs.PlanError, "own file suffix"):
            outputs.plan(".exr", [("left", "_V"), ("right", "_V")], "MULTIVIEW", [item_override])

    def test_output_names_cannot_alias_by_case_in_any_emitting_format(self):
        for suffixes in (("_L", "_l"), ("_Straße", "_STRASSE"), ("_Σ", "_ς"), ("_é", "_e\u0301"),
                         ("_Å", "_A\u030a")):
            for main in ("INDIVIDUAL", "MULTIVIEW", "STEREO_3D"):
                for multilayer in (False, True):
                    with self.subTest(suffixes=suffixes, main=main, multilayer=multilayer):
                        with self.assertRaisesRegex(outputs.PlanError, "distinct name ignoring case") as caught:
                            outputs.plan(".exr", list(zip(("left", "right"), suffixes)), main,
                                         [node(multilayer=multilayer)])
                        for suffix in suffixes:
                            self.assertIn(suffix, str(caught.exception))

    def test_whole_plan_checks_exact_and_casefold_aliases_between_items_and_nodes(self):
        for names in (("Beauty", "Beauty"), ("Beauty", "beauty"), ("Straße", "STRASSE"), ("Éclat", "E\u0301clat")):
            first, second = node(), node()
            first["items"][0]["name"], second["items"][0]["name"] = names
            with self.subTest(names=names), self.assertRaisesRegex(outputs.PlanError, "name the same file ignoring case"):
                outputs.plan(".png", [], "INDIVIDUAL", [first, second])
        items = node()
        items["items"].append(dict(items["items"][0], name="beauty"))
        with self.assertRaisesRegex(outputs.PlanError, "Passes/Beauty####.png and Passes/beauty####.png"):
            outputs.plan(".png", [], "INDIVIDUAL", [items])

    def test_distinct_unicode_and_casefold_names_preserve_all_outputs(self):
        first, second = node(), node(multilayer=True)
        first["items"][0]["name"] = "Éclat"
        second["folder"] = "Layers"
        plan = outputs.plan(".exr", [("left", "_左"), ("right", "_右")], "INDIVIDUAL", [first, second])
        names = [outputs.pattern(output) for output in plan]
        self.assertEqual(len(names), 6)
        self.assertEqual(len(set(name.casefold() for name in names)), 6)
        self.assertIn("Passes/Éclat####_左.png", names)


if __name__ == "__main__":
    unittest.main()
