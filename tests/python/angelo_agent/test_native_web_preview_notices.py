# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every file the live web preview bundles comes with its license in the app.

Dependency-free. ``release/license/web-preview`` (installed with ``release/license`` into the app's text folder)
holds three.js's license and the licenses (and NOTICE, where one is published) of the five libraries three.js
0.185.1 ships prebuilt and the preview loads: Draco, Basis Universal, meshoptimizer, KTX-Parse and zstddec. Its
README lists every file of ``web_preview/vendor/three`` under its component, so a file added there without a
component, or a component without its license, fails here. ``copyright.txt`` points at the folder.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FOLDER = ROOT / "release/license/web-preview"
VENDOR = ROOT / "scripts/addons_core/angelo_agent/web_preview/vendor/three"
HEADING = re.compile(r"^(\S[^()]*) \(([\w.-]+)/?\): (.+)$")   # "Draco (draco/): Apache-2.0"; "(none)": Angelo's own
FILE = re.compile(r"^    (\S+)$")
# What each license's text must say (the SPDX identifiers the README names).
MARKS = {"MIT": "Permission is hereby granted, free of charge",
         "Apache-2.0": "Version 2.0, January 2004",
         "BSD-3-Clause": "Neither the name"}


def components():
    """``[(name, folder or None, license expression, [vendored paths])]`` from the README's file list."""
    text = (FOLDER / "README.txt").read_text(encoding="utf-8")
    found = []
    for line in text.split("\nFiles, by component", 1)[1].splitlines():
        heading, path = HEADING.match(line), FILE.match(line)
        if heading:
            folder = heading.group(2)
            found.append((heading.group(1), None if folder == "none" else folder, heading.group(3), []))
        elif path and found:
            found[-1][3].append(path.group(1))
    return found


class WebPreviewNoticeTests(unittest.TestCase):
    def test_every_bundled_file_is_listed_once(self):
        listed = [path for *_rest, paths in components() for path in paths]
        self.assertEqual(len(listed), len(set(listed)), "a file listed twice")
        bundled = sorted(path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob("*") if path.is_file())
        self.assertEqual(sorted(listed), bundled, "every file in web_preview/vendor/three, under its component")

    def test_each_component_ships_its_license(self):
        named = [entry for entry in components() if entry[1]]
        self.assertEqual([name for name, *_rest in named],
                         ["three.js", "Draco", "Basis Universal", "meshoptimizer", "KTX-Parse", "zstddec"])
        for name, folder, expression, paths in named:
            with self.subTest(component=name):
                self.assertTrue(paths, "a component with no files")
                text = (FOLDER / folder / "LICENSE.txt").read_text(encoding="utf-8")
                for identifier in re.findall(r"[A-Za-z0-9.-]+", expression.replace(" AND ", " ")):
                    self.assertIn(MARKS[identifier], text, "%s's license text: %s" % (name, identifier))

    def test_apache_components_pass_on_their_notice(self):
        # Basis Universal publishes a NOTICE (Apache-2.0 4(d)); Draco publishes none (its README says so).
        notice = (FOLDER / "basis_universal/NOTICE.txt").read_text(encoding="utf-8")
        self.assertIn("Binomial LLC", notice)
        self.assertFalse((FOLDER / "draco/NOTICE.txt").exists())
        self.assertIn("no NOTICE file", " ".join((FOLDER / "README.txt").read_text(encoding="utf-8").split()))

    def test_the_three_js_license_is_the_one_it_ships_with(self):
        self.assertEqual((FOLDER / "three.js/LICENSE.txt").read_bytes(), (VENDOR / "LICENSE").read_bytes())

    def test_the_folder_holds_only_what_the_readme_names(self):
        allowed = {"README.txt"} | {"%s/%s" % (folder, name) for _name, folder, *_rest in components() if folder
                                    for name in ("LICENSE.txt", "NOTICE.txt")}
        present = {path.relative_to(FOLDER).as_posix() for path in FOLDER.rglob("*") if path.is_file()}
        self.assertLessEqual(present, allowed)

    def test_the_folder_ships_and_the_copyright_text_points_at_it(self):
        cmake = (ROOT / "source/creator/CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn('install(\n    DIRECTORY ${CMAKE_SOURCE_DIR}/release/license\n    DESTINATION "${TARGETDIR_TEXT}"', cmake)
        copyright_text = " ".join((ROOT / "release/text/copyright.txt").read_text(encoding="utf-8").split())
        self.assertIn("license/web-preview/", copyright_text)

    def test_no_gltfpack_is_bundled(self):
        # Web export runs a gltfpack only when the person installed one (web_export.gltfpack_path).
        self.assertFalse([path for path in (ROOT / "scripts").rglob("gltfpack*")])
        self.assertIn("Angelo bundles no gltfpack", " ".join((FOLDER / "README.txt").read_text(encoding="utf-8").split()))


if __name__ == "__main__":
    unittest.main()
