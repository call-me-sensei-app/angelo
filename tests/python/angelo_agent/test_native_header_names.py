# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No two C/C++ headers in the source tree have names that differ only by case.

A quoted ``#include`` looks in the including file's own folder first. On a file system that ignores case (NTFS,
macOS's default APFS) ``#include "WM_x.hh"`` from ``intern/`` finds ``intern/wm_x.hh`` when one exists, and the
public header never arrives; on Linux the same code builds. The 2.0.0 GPU start-up fallback first shipped
``windowmanager/WM_angelo_gpu_startup.hh`` beside ``windowmanager/intern/wm_angelo_gpu_startup.hh``: three files
got the internal header and the Windows build failed with four undeclared functions (the macOS build would have,
too).

The whole tree is checked, not only one folder: which folders one compilation searches depends on every include
path it is given. Blender itself has nine such groups, all between libraries that never share a search path
(Cycles, Freestyle, libmv, dualcon and the bundled quadriflow, mantaflow and audaspace); they are listed here, so a
new one fails until someone looks at it. A sparse checkout holds fewer files and so finds fewer groups; what it
finds is still checked.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import os
import unittest
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FOLDERS = ("source", "intern", "extern")
HEADERS = (".h", ".hh", ".hpp", ".hxx", ".inl")
# Blender's own groups: lower-case name -> the folders (below the root) its spellings may live in.
UPSTREAM = {
    "bezier.h": ("extern/quadriflow/", "source/blender/freestyle/"),
    "curve.h": ("intern/cycles/", "source/blender/freestyle/"),
    "grid.h": ("extern/mantaflow/", "source/blender/freestyle/"),
    "image.h": ("intern/cycles/", "intern/libmv/", "source/blender/freestyle/"),
    "node.h": ("intern/cycles/", "source/blender/freestyle/"),
    "noise.h": ("intern/cycles/", "source/blender/freestyle/"),
    "queue.h": ("intern/cycles/", "intern/dualcon/"),
    "triangle.h": ("extern/audaspace/", "intern/cycles/"),
    "volume.h": ("extern/audaspace/", "intern/cycles/"),
}


def collisions(root, folders=FOLDERS):
    """Lower-case header name -> the paths (below ``root``) of headers with that name, for every name that is
    spelled in more than one way."""
    found = defaultdict(set)
    for folder in folders:
        for directory, _, names in os.walk(Path(root) / folder):
            for name in names:
                if name.endswith(HEADERS):
                    found[name.casefold()].add(Path(directory, name).relative_to(root).as_posix())
    return {name: sorted(paths) for name, paths in found.items()
            if len({path.rsplit("/", 1)[-1] for path in paths}) > 1}


def unexplained(groups, upstream=UPSTREAM):
    """The groups that are not Blender's own listed ones: a name that is not listed, or a listed name with a
    spelling in a folder that is not."""
    return {name: paths for name, paths in groups.items()
            if name not in upstream or not all(path.startswith(tuple(upstream[name])) for path in paths)}


class HeaderNameTests(unittest.TestCase):
    def test_no_two_headers_differ_only_by_case(self):
        found = unexplained(collisions(ROOT))
        self.assertEqual(found, {}, "headers whose names differ only by case: on Windows and macOS an include of "
                                    "one can find the other")

    def test_a_public_header_and_an_internal_one_that_differ_by_case_are_found(self):
        # The pair that broke the Windows build, in a tree of its own.
        import tempfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for path in ("source/blender/windowmanager/WM_angelo_x.hh",
                         "source/blender/windowmanager/intern/wm_angelo_x.hh",
                         "source/blender/windowmanager/intern/wm_other.hh",
                         "intern/cycles/kernel/image.h", "source/blender/freestyle/intern/image/Image.h",
                         "intern/cycles/util/queue.h", "source/blender/editors/Queue.h"):
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text("")
            groups = collisions(root)
            self.assertEqual(set(groups), {"wm_angelo_x.hh", "image.h", "queue.h"})
            # Blender's own group passes; a new name fails, and so does a listed name in a new folder.
            self.assertEqual(set(unexplained(groups)), {"wm_angelo_x.hh", "queue.h"})


if __name__ == "__main__":
    unittest.main()
