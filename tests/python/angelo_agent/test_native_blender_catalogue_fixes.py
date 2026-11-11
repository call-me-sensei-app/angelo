# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's own catalogues (``locale/po``, compiled to ``blender.mo``) never name a GPU or compute API as another one.

Preferences > System > Backend, the Cycles device list and their tooltips name APIs (OpenGL, Vulkan, Metal, CUDA,
OptiX, HIP, oneAPI, OpenCL). A translation that keeps one name or none is fine; a translation that swaps in a different
API tells the user the wrong thing. Blender 5.2's Italian and Vietnamese catalogues (and 5.3's Italian) rendered
the backend item "OpenGL" as "OpenCL"; Angelo corrects them in its copy (to be offered upstream). An upstream
catalogue refresh must keep the corrections, so the corrected entries are pinned too. No Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/angelo"))
from i18n import languages, po  # noqa: E402

APIS = ("OpenGL", "Vulkan", "Metal", "OpenCL", "CUDA", "OptiX", "HIP", "oneAPI")


def entries(name):
    _, found = po.read(str(ROOT / "locale/po" / f"{name}.po"))
    return [entry for entry in found if entry.id and entry.str and not entry.obsolete]


class BlenderCatalogueApiNameTests(unittest.TestCase):
    def test_no_shipped_catalogue_swaps_one_api_name_for_another(self):
        swapped = []
        for language in languages.LANGUAGES:
            for entry in entries(language.po):
                for name in APIS:
                    if name in entry.id and name not in entry.str:
                        others = [other for other in APIS if other in entry.str and other not in entry.id]
                        if others:
                            swapped.append(f"{language.code}: {entry.id!r} -> {entry.str!r}")
        self.assertEqual([], swapped, "Correct the msgstr in locale/po/<language>.po; the app shows it as is")

    def test_corrected_opengl_backend_entries_keep_their_name(self):
        for language in ("it", "vi"):   # Corrected in Angelo's copy; an upstream refresh must not undo them.
            with self.subTest(language=language):
                backend = [entry.str for entry in entries(language) if entry.ctxt == "" and entry.id == "OpenGL"]
                self.assertEqual(["OpenGL"], backend)


if __name__ == "__main__":
    unittest.main()
