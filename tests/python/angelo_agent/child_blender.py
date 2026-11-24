# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A child Angelo in the background, running a suite's code from its own file (``--python``), the escapes suites'
children. Run only inside Blender, from a suite."""

import os
import subprocess
import tempfile


def run(code, **options):
    """``subprocess.run`` of this build, ``--background --factory-startup``, on ``code`` written to a file of its own;
    ``options`` go to ``subprocess.run`` (``capture_output``, ``text``, ``timeout``, ``env``)."""
    import bpy
    with tempfile.TemporaryDirectory(prefix="angelo-child-") as folder:
        script = os.path.join(folder, "child.py")
        with open(script, "w", encoding="utf-8") as out:
            out.write(code)
        return subprocess.run([bpy.app.binary_path, "--background", "--factory-startup", "--python", script],
                              **options)
