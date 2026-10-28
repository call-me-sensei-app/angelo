# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Starts a capture job (``scenario_job.py`` or ``oneshot_job.py``) with the split ray-cast capture installed.

    Blender -b ... --python scenario_capture_boot.py -- JOB_SCRIPT [job arguments...]

``scenario_references.run_job`` launches the capture jobs through this file.
It points ``scenario_capture.capture`` at ``scenario_capture_split`` and then
runs the job script unchanged as ``__main__`` with its own arguments. The
job's output is byte-identical; only the time differs. Scratch folders go
under the job's ``--out``.
"""

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True

import scenario_capture_split as split  # noqa: E402


script, sys.argv = split.job_arguments(sys.argv)
split.install(split.scratch_of(sys.argv[sys.argv.index("--") + 1:]))
runpy.run_path(script, run_name="__main__")
