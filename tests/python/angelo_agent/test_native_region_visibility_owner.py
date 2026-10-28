# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile the actual RNA callbacks against a small context/region test double.

This tests native branch and context ownership decisions, not Blender's runtime
handler lifecycle. The GUI suite and the unchanged shared sculpt fixture cover
that separately. Requires a host C++ compiler; does not build or launch Blender.
"""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
CALLBACKS = ("rna_Space_bool_from_region_flag_get_by_type", "rna_Space_bool_from_region_flag_set_by_type",
             "rna_Space_region_visibility_update", "rna_Space_bool_from_region_flag_update_by_type",
             "rna_Space_show_region_header_get", "rna_Space_show_region_header_set",
             "rna_Space_show_region_header_update", "rna_Space_show_region_tool_header_get",
             "rna_Space_show_region_tool_header_set", "rna_Space_show_region_tool_header_update")


def callback(source, name):
    """The complete current C++ function, without translating its decisions to Python."""
    # Search declarations by line so a call cannot be mistaken for a definition.
    match = re.search(r"^static [^\n]*\b" + re.escape(name) + r"\(", source, re.MULTILINE)
    if match is None:
        return ""  # The pre-fix source has no owner helper; its original callback still compiles.
    start = match.start()
    opening = source.index("{", start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]


class RegionVisibilityOwnerTests(unittest.TestCase):
    def test_actual_callbacks_keep_flags_and_owner_context(self):
        compiler = shutil.which(os.environ.get("CXX", "c++"))
        self.assertIsNotNone(compiler, "A C++ compiler is required for the extracted native callback test")
        source_path = Path(os.environ.get("ANGELO_QA_RNA_SPACE_SOURCE") or
                           ROOT / "source/blender/makesrna/intern/rna_space.cc")
        source = source_path.read_text()
        fixture = Path(__file__).with_name("region_visibility_owner_host.cc").read_text()
        combined = fixture.replace("// ACTUAL_RNA_CALLBACKS", "\n\n".join(callback(source, name) for name in CALLBACKS))
        with tempfile.TemporaryDirectory(prefix="angelo-region-owner-") as directory:
            cpp, executable = Path(directory, "test.cc"), Path(directory, "test")
            cpp.write_text(combined)
            for python_enabled in (False, True):
                with self.subTest(with_python=python_enabled):
                    flags = ["-DWITH_PYTHON"] if python_enabled else []
                    compiled = subprocess.run([compiler, "-std=c++17", "-Wall", "-Wextra", *flags,
                                               str(cpp), "-o", str(executable)],
                                              text=True, capture_output=True, timeout=30)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    result = subprocess.run([str(executable)], text=True, capture_output=True, timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("all region visibility owner cases passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
