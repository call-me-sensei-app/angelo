# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the same feature/Undo matrix in a fresh process using file-backed stages.

The delegated suite writes results.json in ANGELO_QA_DIR and quits the app.
"""

import os
from pathlib import Path
import runpy
import sys

import bpy

assert '--factory-startup' in sys.argv and not bpy.app.background
os.environ['ANGELO_QA_STAGE_STORAGE'] = 'file'
runpy.run_path(str(Path(__file__).with_name('test_blender_program_commit_effects.py')), run_name='__main__')
