# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The MCP visibility readers on real ``bpy``: what the fake-window host tests can't prove (Blender's own attribute
names and values). Runs in background Blender, no window needed::

    blender -b --factory-startup --python-exit-code 1 --python tests/python/angelo_agent/test_blender_external_visibility.py

* Every ``external_state`` section reads the default scene's Layout screen with nothing ``unavailable``, and an
  absent Undo stack (background Blender has none) is an empty history.
* Edit Mode counts the default cube's selected vertices, edges and faces.
* A scene camera becomes a free camera (``external_visibility.camera_item``) and an orbit framing on the cube
  (``external_visibility.sphere``), and every look built from them passes ``view_spec.validate_view``.

Contract: doc/angelo/external_mcp.rst, "Looks" and "Window state".
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import math
import traceback
from types import SimpleNamespace as NS

import bpy

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "scripts",
                                "addons_core"))
from angelo_agent import external_look_args, external_state, external_visibility, view_spec  # noqa: E402


def check(condition, what):
    if not condition:
        raise AssertionError(what)


def main():
    window = NS(scene=bpy.context.scene, view_layer=bpy.context.view_layer,
                workspace=bpy.data.workspaces["Layout"], screen=bpy.data.screens["Layout"])
    state = external_state.read(bpy, window, None)
    missing = {name: value for name, value in state.items() if isinstance(value, dict) and "unavailable" in value}
    check(not missing, "every section reads on real bpy: %s" % missing)
    check(state["selection"]["bounds"] == {"min": [-1.0, -1.0, -1.0], "max": [1.0, 1.0, 1.0]}, state["selection"])
    check(state["scene"]["camera"] == "Camera" and state["render"]["resolution"] == [1920, 1080], state["scene"])
    check("blend" not in json.dumps(state), "no file path")
    cube = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = cube
    cube.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.object.mode_set(mode="OBJECT")   # Edit Mode's selection reaches the mesh's counts on the way out.
    bpy.ops.object.mode_set(mode="EDIT")
    edit = external_state.read(bpy, window, None)
    check(edit["mode"]["mode"] == "EDIT" and edit["tool"]["context_mode"] == "EDIT_MESH", edit["mode"])
    check(edit["selection"]["edit"]["vertices"] == 8 and edit["selection"]["edit"]["faces"] == 6, edit["selection"])
    bpy.ops.object.mode_set(mode="OBJECT")
    camera = external_visibility.camera_item(bpy.data.objects["Camera"])
    check(camera["lens"] == 50.0 and math.dist(camera["location"], camera["look_at"]) > 9.99, camera)
    bounds = external_visibility.sphere([cube])
    check(bounds[0] == (0.0, 0.0, 0.0) and abs(bounds[1] - math.sqrt(3)) < 1e-6, bounds)
    facts = {"handles": {"Cube": "scn:" + "a" * 32 + ":1"}, "bounds": bounds, "aspect": 16 / 9,
             "cameras": {"Camera": camera}}
    for request in ({"views": ["front", "perspective", {"camera": "Camera"}], "objects": ["Cube"]},
                    {"views": [{"orbit": [180, -20]}], "shading": "LIT", "resolution": 1024}):
        arguments = external_look_args.build(external_look_args.validate(request), facts)
        view_spec.validate_view(view_spec.TOOL_NAME, arguments)
    print("EXTERNAL VISIBILITY ON BPY: OK")


try:
    main()
except Exception:  # noqa: BLE001 - the run's exit code is the test's verdict.
    traceback.print_exc()
    sys.exit(1)
