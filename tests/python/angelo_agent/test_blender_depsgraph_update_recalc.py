# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``DepsgraphUpdate.angelo_recalc`` in the real binary, from updates Blender itself reports.

The GTest (``rna_depsgraph_update_test.cc``) proves the getter on set bits. This suite proves what a
``depsgraph_update_post`` handler sees for real edits: a ``select_set`` reports SELECT, a location edit reports
TRANSFORM on the object, a material setting reports SHADING on the material, and no reported update ever has an
empty set. The set also holds bits the update flush adds (the same bits the ``is_updated_*`` siblings see), so an
object-mode click is the Scene with SELECT and at most its copy tag SYNC_TO_EVAL. Feature detection is
``"angelo_recalc" in bpy.types.DepsgraphUpdate.bl_rna.properties``.

Background, no provider, account or network. Writes results.json under ANGELO_QA_DIR.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-update-recalc-qa"))
OUT.mkdir(parents=True, exist_ok=True)
state = {"success": False, "checks": {}, "updates": {}}
seen = []


def check(name, passed, detail=None):
    state["checks"][name] = bool(passed)
    if detail is not None:
        state["updates"][name] = detail


def on_update(scene, depsgraph):
    for update in depsgraph.updates:
        seen.append((update.id.original.session_uid, type(update.id).__name__, set(update.angelo_recalc)))


def updates_after(edit):
    view_layer = bpy.context.view_layer
    view_layer.update()
    seen.clear()
    edit()
    view_layer.update()
    return list(seen)


def kinds_of(found, id_data):
    result = set()
    for uid, _, kinds in found:
        if uid == id_data.session_uid:
            result |= kinds
    return result


try:
    check("feature detection finds the property", "angelo_recalc" in bpy.types.DepsgraphUpdate.bl_rna.properties)
    scene = bpy.context.scene
    mesh = bpy.data.meshes.new("Recalc mesh")
    cube = bpy.data.objects.new("Recalc cube", mesh)
    scene.collection.objects.link(cube)
    material = bpy.data.materials.new("Recalc material")
    mesh.materials.append(material)
    bpy.app.handlers.depsgraph_update_post.append(on_update)
    cube.select_set(False)

    found = updates_after(lambda: cube.select_set(True))
    # The Scene's selection tag also tags its copy (LAYER_COLLECTIONS asks for one), so the flush adds SYNC_TO_EVAL.
    check("a selection click reports the Scene with SELECT and only its copy tag besides",
          "SELECT" in kinds_of(found, scene) and kinds_of(found, scene) <= {"SELECT", "SYNC_TO_EVAL"},
          [[name, sorted(kinds)] for _, name, kinds in found])

    def move():
        cube.location.x += 1.0
    found = updates_after(move)
    check("a location edit reports TRANSFORM on the object", "TRANSFORM" in kinds_of(found, cube),
          sorted(kinds_of(found, cube)))

    def roughen():
        material.roughness = 0.25
    found = updates_after(roughen)
    check("a material setting reports SHADING on the material", "SHADING" in kinds_of(found, material),
          sorted(kinds_of(found, material)))

    every = []
    for edit in (lambda: cube.select_set(False), move, roughen):
        every += updates_after(edit)
    check("no reported update has an empty set", every and all(kinds for _, _, kinds in every),
          [[name, sorted(kinds)] for _, name, kinds in every])
    bpy.app.handlers.depsgraph_update_post.remove(on_update)
    state["success"] = all(state["checks"].values())
except Exception:
    state["error"] = traceback.format_exc()
(OUT / "results.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
print("ANGELO_DEPSGRAPH_UPDATE_RECALC", json.dumps(state), flush=True)
