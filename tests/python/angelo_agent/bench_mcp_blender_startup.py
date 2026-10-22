# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Stock-Blender startup for one MCP-side trial of ``bench_mcp_vs_angelo.py``.

Started only by ``bench_mcp_trial.py`` with ``--factory-startup --online-mode``
and isolated ``BLENDER_USER_*`` directories. It enables the trial's private COPY
of Blender Lab's MCP add-on (``bl_ext.user_default.mcp``), points the add-on's
own preferences at the trial port, starts the add-on's own interactive socket
server through its ``blmcp.server_start`` operator, and writes a ready file.
On the GPU test VMs it first sets the Cycles GPU preference, exactly as the
Angelo side does (``bench_gpu``); the ready file carries its device line.

Nothing else is configured: polling intervals, sandbox and execution path are
the add-on's defaults (0.25 s active polling, 1 s once idle for 5 s), which is
what a user of the add-on gets. The scene is the factory default-cube scene.
"""

import importlib.util
import json
import os
import sys

import addon_utils
import bpy

assert "--factory-startup" in sys.argv and not bpy.app.background, "Started only by bench_mcp_trial.py"
PORT = int(os.environ["ANGELO_BENCH_MCP_PORT"])
READY = os.environ["ANGELO_BENCH_MCP_READY"]
MODULE = "bl_ext.user_default.mcp"
assert PORT != 9876, "Port 9876 is the Blender MCP add-on's default, left to an interactive pair"

sys.dont_write_bytecode = True
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.filepaths.temporary_directory = os.environ["TMPDIR"]


def write_ready(**values):
    with open(READY + ".tmp", "w", encoding="utf-8") as handle:
        json.dump(values, handle)
    os.replace(READY + ".tmp", READY)


try:
    addon_utils.enable(MODULE, default_set=True, persistent=False, handle_error=None)
    preferences = bpy.context.preferences.addons[MODULE].preferences
    preferences.host, preferences.port = "127.0.0.1", PORT
    # The GPU test VMs' Cycles preference, set as on the Angelo side. Loaded by path: the harness folder never joins
    # this Blender's module path, where it could shadow a module the add-on imports.
    spec = importlib.util.spec_from_file_location("angelo_bench_gpu", os.path.join(os.path.dirname(__file__),
                                                                                  "bench_gpu.py"))
    bench_gpu = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench_gpu)
    gpu = bench_gpu.run_if_required(bpy)   # No GPU raises: reported below, and the trial stops before billing.
    outcome = bpy.ops.blmcp.server_start()
    manifest = os.path.join(bpy.utils.user_resource("EXTENSIONS"), "user_default", "mcp", "blender_manifest.toml")
    addon_version = next((line.split("=", 1)[1].strip().strip('"') for line in open(manifest, encoding="utf-8")
                          if line.startswith("version")), None)
    write_ready(ok=outcome == {"FINISHED"}, port=PORT, pid=os.getpid(), outcome=sorted(outcome),
                blender_version=bpy.app.version_string, build_hash=bpy.app.build_hash.decode(),
                online_access=bpy.app.online_access, addon_module=MODULE, addon_version=addon_version,
                addon_path=os.path.dirname(manifest), gpu_preference=gpu)
except Exception as error:  # Reported to the driver, which stops before anything is billed.
    write_ready(ok=False, error="%s: %s" % (type(error).__name__, error))
