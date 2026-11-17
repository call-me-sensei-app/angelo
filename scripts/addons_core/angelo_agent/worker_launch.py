# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a worker Blender is started (``worker_process``): its command line, the running Angelo's own program and the
worker's script beside this add-on, and the process start itself. ``worker_process`` imports these by name, so its
callers and its tests' stand-ins (``worker_process.start_process``, ``worker_process.default_binary``) are as before.
"""

import subprocess
from pathlib import Path


def worker_command(binary, script, part, channel, private):
    return [str(binary), "--background", "--factory-startup", "--disable-autoexec", "--offline-mode",
            "--python-exit-code", "1", "--python", str(script), "--", "--worker", "--channel", channel,
            "--file", str(part), "--private", str(private)]


def default_binary():
    """The running Angelo's own executable, from C (``BKE_appdir_program_path``: set once at startup, read-only in
    the app), read at the start on whatever thread starts the worker."""
    import bpy
    return Path(bpy.app.binary_path)


def default_script():
    """The worker's script: the one beside this add-on's own file."""
    return Path(__file__).resolve().parents[2] / "angelo" / "angelo_headless.py"


def start_process(command, options, platform):
    """Start a worker's process: Python's own start (Windows: a Job Object, so stopping ends its whole tree)."""
    if platform == "win32":
        from .child_process import popen
        return popen(command, **options)
    return subprocess.Popen(command, **options)
