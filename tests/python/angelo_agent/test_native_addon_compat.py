# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which of the person's add-ons are known to work on an update's Blender version (``addon_compat``,
``angelo_addon_loads``; doc/angelo/updates_and_reports.rst, "Your add-ons and the update"). No Blender.

Covered: every verdict comes from a real signal (Angelo's load results, the add-on's own declared range, the
extension platform's listing for the new version), never from a name; an open declared range is not taken as proof
for a Blender series it predates; a failure in this session is named with its cause; the platform's newer version is
offered first; the listing query carries only the platform and versions; the load record survives a round trip and is
read from the new version's own folder.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/modules"))
from angelo_agent import addon_compat as compat  # noqa: E402
import angelo_addon_loads as loads  # noqa: E402

V53, V531, V54 = (5, 3, 0), (5, 3, 1), (5, 4, 0)


def addon(module="tool", name="Tool", **changes):
    values = dict(version=(1, 0, 0), extension=False, declared_min=(4, 2, 0), loaded=True)
    values.update(changes)
    return compat.Addon(module, name, **values)


def ext(package="tool", **changes):
    values = dict(extension=True, package=package, remote=True)
    values.update(changes)
    return addon("bl_ext.blender_org." + package, package.title(), **values)


class VersionTests(unittest.TestCase):
    def test_versions_parse_as_blender_does(self):
        self.assertEqual(compat.version_tuple("4.2"), (4, 2, 0))
        self.assertEqual(compat.version_tuple("5.3.1"), (5, 3, 1))
        self.assertEqual(compat.version_tuple((4, 2)), (4, 2, 0))
        for bad in ("", "x.1", "1.2.3.4", None, "-1.0", 4.2):
            with self.subTest(bad=bad):
                self.assertIsNone(compat.version_tuple(bad))


class JudgeTests(unittest.TestCase):
    def judge(self, item, target=V54, current=V53, **kw):
        return compat.judge(item, target, current, platform="macos-arm64", **kw)

    def test_same_series_update_trusts_what_loads_now(self):
        verdict = self.judge(addon(declared_min=None), target=V531)
        self.assertEqual((verdict.kind, verdict.why), (compat.WORKS, "same_series"))

    def test_same_series_but_not_loaded_is_not_known(self):
        self.assertEqual(self.judge(addon(loaded=False), target=V531).kind, compat.UNKNOWN)

    def test_a_failure_now_is_named_with_its_cause(self):
        verdict = self.judge(addon(cause="AttributeError: x"), target=V531)
        self.assertEqual((verdict.kind, verdict.why), (compat.FAILING, "AttributeError: x"))

    def test_an_open_range_is_no_proof_for_a_newer_series(self):
        verdict = self.judge(ext())
        self.assertEqual((verdict.kind, verdict.why, verdict.detail), (compat.UNKNOWN, "open_range", "4.2.0"))
        self.assertEqual(self.judge(addon(declared_min=None)).why, "no_range")

    def test_a_closed_range_past_the_target_is_known(self):
        verdict = self.judge(ext(declared_max=(5, 6, 0)))
        self.assertEqual((verdict.kind, verdict.why), (compat.WORKS, "declared"))

    def test_a_range_that_ends_at_the_target_excludes_it(self):
        verdict = self.judge(ext(declared_max=(5, 4, 0)))
        self.assertEqual((verdict.kind, verdict.why, verdict.detail), (compat.UNSUPPORTED, "too_new", "5.4.0"))

    def test_a_patch_level_bound_counts_even_within_a_series(self):
        verdict = self.judge(ext(declared_max=(5, 3, 1)), target=V531)
        self.assertEqual(verdict.kind, compat.UNSUPPORTED)

    def test_a_minimum_above_the_target(self):
        verdict = self.judge(addon(declared_min=(5, 5, 0)))
        self.assertEqual((verdict.kind, verdict.why), (compat.UNSUPPORTED, "too_old"))

    def test_an_extension_for_other_platforms(self):
        verdict = self.judge(ext(platforms=("windows-x64",)), target=V531)
        self.assertEqual((verdict.kind, verdict.why), (compat.UNSUPPORTED, "platform"))

    def test_a_newer_version_on_the_platform_comes_first(self):
        listing = {"tool": [{"version": (1, 1, 0), "min": (4, 2, 0), "max": None, "platforms": []}]}
        verdict = self.judge(ext(declared_max=(5, 4, 0)), listing=listing)
        self.assertEqual((verdict.kind, verdict.why, verdict.detail), (compat.UPDATE, "needs_update", "1.1.0"))
        verdict = self.judge(ext(), listing=listing)
        self.assertEqual((verdict.kind, verdict.why), (compat.UPDATE, "newer"))

    def test_a_listing_entry_outside_its_own_range_is_skipped(self):
        listing = {"tool": [{"version": (2, 0, 0), "min": (5, 5, 0), "max": None, "platforms": []},
                            {"version": (1, 0, 0), "min": (4, 2, 0), "max": None, "platforms": []}]}
        self.assertEqual(self.judge(ext(), listing=listing).kind, compat.UNKNOWN)

    def test_a_legacy_addon_is_never_matched_to_a_package(self):
        listing = {"tool": [{"version": (9, 0, 0), "min": (4, 2, 0), "max": None, "platforms": []}]}
        self.assertEqual(self.judge(addon(), listing=listing).kind, compat.UNKNOWN)

    def test_angelos_load_on_the_target_series_counts_for_that_version_only(self):
        item = ext()
        there = {(item.module, "1.0.0")}
        self.assertEqual(self.judge(item, loaded_there=there).why, "loaded_there")
        self.assertEqual(self.judge(ext(version=(1, 0, 1)), loaded_there=there).kind, compat.UNKNOWN)

    def test_an_unknown_base_names_everything_as_not_yet_known(self):
        self.assertEqual(self.judge(addon(), target=None).why, "base_unknown")

    def test_nothing_depends_on_an_addons_name(self):
        for module, name in (("hair_tool", "Hair Tool"), ("x", "X"), ("node_wrangler", "Node Wrangler")):
            with self.subTest(name=name):
                verdict = self.judge(compat.Addon(module, name, (1, 0, 0), False, (4, 2, 0), loaded=True))
                self.assertEqual((verdict.kind, verdict.why), (compat.UNKNOWN, "open_range"))


class SummaryTests(unittest.TestCase):
    def test_the_card_names_the_most_pressing_first_then_counts(self):
        addons = [addon("a", "Alpha"), ext("zed", declared_max=(5, 4, 0)), addon("b", "Beta", cause="Error"),
                  ext("ok", declared_max=(6, 0, 0)), addon("c", "Gamma")]
        summary = compat.summarize(addons, V54, V53, platform="macos-arm64")
        self.assertEqual([v.addon.name for v in summary.attention], ["Beta", "Zed", "Alpha", "Gamma"])
        self.assertEqual(summary.known, 1)
        self.assertEqual(summary.named(), (["Beta", "Zed", "Alpha"], 1))


class ListingTests(unittest.TestCase):
    def test_a_listing_parses_and_skips_what_is_not_well_formed(self):
        document = {"version": "v1", "blocklist": [], "data": [
            {"id": "a", "version": "1.2.0", "type": "add-on", "blender_version_min": "4.2.0"},
            {"id": "b", "version": "2.0.0", "type": "add-on", "blender_version_min": "4.2.0",
             "blender_version_max": "5.5.999", "platforms": ["macos-arm64"]},
            {"id": "t", "version": "1.0.0", "type": "theme", "blender_version_min": "4.2.0"},
            {"id": "c", "version": "x", "type": "add-on", "blender_version_min": "4.2.0"},
            {"id": "d", "version": "1.0.0", "type": "add-on", "blender_version_min": "4.2.0",
             "blender_version_max": "bad"},
            "junk"]}
        listing = compat.parse_listing(document)
        self.assertEqual(sorted(listing), ["a", "b"])
        self.assertEqual(listing["b"][0]["max"], (5, 5, 999))
        self.assertIsNone(compat.parse_listing({"version": "v2", "data": []}))
        self.assertIsNone(compat.parse_listing([]))

    def test_the_query_carries_only_the_platform_and_versions(self):
        url = compat.listing_query("https://extensions.blender.org/api/v1/extensions/?blender_version=5.3.0",
                                   V54, (3, 13, 9), "macos-arm64")
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path),
                         ("https", "extensions.blender.org", "/api/v1/extensions/"))
        self.assertEqual(parse_qs(parts.query), {"platform": ["macos-arm64"], "blender_version": ["5.4.0"],
                                                 "python_version": ["3.13.9"]})


class LoadRecordTests(unittest.TestCase):
    def test_a_record_round_trips_and_is_written_only_when_it_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Angelo" / "5.3" / "config" / "angelo-start"
            self.assertTrue(loads.write(folder, (5, 3, 0), {"tool": "1.0.0", "bl_ext.user_default.x": "2.1.0"}))
            self.assertFalse(loads.write(folder, (5, 3, 0), {"bl_ext.user_default.x": "2.1.0", "tool": "1.0.0"}))
            self.assertEqual(loads.read(folder / loads.FILE),
                             ((5, 3), {("tool", "1.0.0"), ("bl_ext.user_default.x", "2.1.0")}))
            self.assertTrue(loads.write(folder, (5, 3, 0), {"tool": "1.0.1"}))
            self.assertEqual(loads.read(folder / loads.FILE), ((5, 3), {("tool", "1.0.1")}))

    def test_versions_are_kept_as_the_check_reads_them(self):
        self.assertEqual(loads.version_text((1, 2)), compat.version_text(compat.version_tuple((1, 2))))
        self.assertEqual(loads.version_text((1, 2, 3)), "1.2.3")
        for odd in ((), (1, 2, 3, 4), ("a",), None, "1.2"):
            with self.subTest(odd=odd):
                self.assertEqual(loads.version_text(odd), "")

    def test_a_damaged_or_missing_record_reads_as_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / loads.FILE
            self.assertEqual(loads.read(path), (None, set()))
            for text in ("{", "[]", json.dumps({"format": 1, "blender": "x", "addons": {}}),
                         json.dumps({"format": 1, "blender": "5.3.0", "addons": {"a": 1}})):
                path.write_text(text, encoding="utf-8")
                with self.subTest(text=text):
                    self.assertEqual(loads.read(path)[1], set())

    def test_the_new_versions_folder_is_found_beside_this_one(self):
        config = Path("/u/Angelo/5.3/config")
        self.assertEqual(loads.record_for_series(config, (5, 4)),
                         Path("/u/Angelo/5.4/config") / loads.FOLDER / loads.FILE)
        self.assertIsNone(loads.record_for_series(Path("/custom/config"), (5, 4)))   # BLENDER_USER_CONFIG.


if __name__ == "__main__":
    unittest.main()
