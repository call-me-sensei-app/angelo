# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Asset Browser's empty and no-results states come from its real counts (1.0.5, ``asset_browser_states``).

Host side: which state a browser gets from ``SpaceFileBrowser.angelo_listed_count``, ``angelo_total_count`` and
``angelo_list_ready`` (stand-in spaces), and the native wiring that provides them: read-only RNA over
``ED_fileselect_angelo_list_counts``, which reports nothing as final while the list is read or unfiltered. The app's
proof, counts and pixels, is ``test_blender_asset_browser_states``.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.asset_browser_states import state  # noqa: E402


def browser(listed, total, ready=True, query="", mode='ASSETS'):
    return SimpleNamespace(browse_mode=mode, angelo_listed_count=listed, angelo_total_count=total,
                           angelo_list_ready=ready, params=SimpleNamespace(filter_search=query))


class StateTests(unittest.TestCase):
    def test_an_empty_library_is_empty(self):
        self.assertEqual(state(browser(0, 0)), ("empty", ""))

    def test_a_search_without_results_names_its_query(self):
        self.assertEqual(state(browser(0, 12, query="chair")), ("no_results", "chair"))

    def test_an_empty_catalog_without_a_search_is_empty(self):
        self.assertEqual(state(browser(0, 12))[0], "empty")

    def test_a_search_in_an_empty_library_is_still_empty(self):
        self.assertEqual(state(browser(0, 0, query="chair"))[0], "empty")

    def test_a_list_with_assets_draws_nothing(self):
        self.assertEqual(state(browser(3, 12, query="cha"))[0], None)

    def test_nothing_while_the_list_is_read_or_on_an_older_build(self):
        self.assertIsNone(state(browser(0, 0, ready=False))[0])
        self.assertIsNone(state(SimpleNamespace(browse_mode='ASSETS', params=None))[0], "no counts: no state")
        self.assertIsNone(state(browser(0, 0, mode='FILES'))[0], "a file browser is not an asset browser")


class TitleTests(unittest.TestCase):
    def test_media_studios_own_library_says_what_appears_there(self):
        from angelo_agent.asset_browser_states import EMPTY, STUDIO_EMPTY, empty_title
        space = SimpleNamespace(params=SimpleNamespace(asset_library_reference="Angelo Media Studio"))
        self.assertEqual(empty_title(space, "Angelo Media Studio"), STUDIO_EMPTY)
        renamed = SimpleNamespace(params=SimpleNamespace(asset_library_reference="My generations"))
        self.assertEqual(empty_title(renamed, "My generations"), STUDIO_EMPTY, "found by the Preferences' name")
        for other in ("LOCAL", "ALL", "ESSENTIALS", "Kitbash"):
            self.assertEqual(empty_title(SimpleNamespace(params=SimpleNamespace(asset_library_reference=other)),
                                         "Angelo Media Studio"), EMPTY, other)
        self.assertEqual(empty_title(space, None), EMPTY, "not registered: any library is just empty")


class NativeWiringTests(unittest.TestCase):
    def test_rna_exposes_read_only_counts(self):
        rna = (ROOT / "source/blender/makesrna/intern/rna_space.cc").read_text()
        definition = rna.split('srna = RNA_def_struct(brna, "SpaceFileBrowser", "Space");', 1)[1].split("\n}\n", 1)[0]
        for name, kind in (("angelo_listed_count", "PROP_INT"), ("angelo_total_count", "PROP_INT"),
                           ("angelo_list_ready", "PROP_BOOLEAN")):
            block = definition.split(f'RNA_def_property(srna, "{name}", {kind}', 1)[1].split("prop = ", 1)[0]
            self.assertIn("RNA_def_property_clear_flag(prop, PROP_EDITABLE);", block, name)
            self.assertIn(f"rna_SpaceFileBrowser_{name}_get", block, name)
            self.assertIn("ED_fileselect_angelo_list_counts", rna.split(f"rna_SpaceFileBrowser_{name}_get(PointerRNA", 1)[1]
                          .split("\n}\n", 1)[0], name)

    def test_counts_are_final_only_once_read_and_filtered(self):
        source = (ROOT / "source/blender/editors/space_file/filesel.cc").read_text()
        body = source.split("bool ED_fileselect_angelo_list_counts(", 1)[1].split("\n}\n", 1)[0]
        self.assertIn("filelist_files_num_entries_filtered(files)", body)
        self.assertIn("const bool read = filelist_is_ready(files) && !filelist_pending(files);", body)
        self.assertIn("return listed >= 0 && read;", body)
        # A read list with no entries is never filtered; it is final with nothing listed (the current file's empty
        # library: interim dbe022dddb9 probe, LOCAL stayed [0, 0, not ready] while ALL/ESSENTIALS were ready).
        self.assertIn("if (read && *r_total == 0 && listed < 0) {", body)
        self.assertIn("listed = 0;", body.split("if (read && *r_total == 0 && listed < 0) {", 1)[1].split("}", 1)[0])
        self.assertIn("bool ED_fileselect_angelo_list_counts(const SpaceFile *sfile, int *r_listed, int *r_total);",
                      (ROOT / "source/blender/editors/include/ED_fileselect.hh").read_text())
        filelist = (ROOT / "source/blender/editors/space_file/filelist/filelist.cc").read_text()
        self.assertIn("return filelist->filelist.entries_filtered_num;",
                      filelist.split("int filelist_files_num_entries_filtered(const FileList *filelist)", 1)[1][:80])


if __name__ == "__main__":
    unittest.main()
