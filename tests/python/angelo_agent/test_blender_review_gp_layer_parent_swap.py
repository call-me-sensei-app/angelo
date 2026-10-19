# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Grease Pencil edit that swaps the data of an object with a parented layer (repro).

A pre-existing failure, present with and without material borrowing.

``finish_stage`` points the staged GreasePencil data's ``layers[].parent`` at
the staged parent copy with ``setattr``. When an operator_run edit
(``grease_pencil.stroke_subdivide``) swaps the data, commit repointed every
owned block's Object links to the live objects (``program_fields.repoint``),
took any holder with ``parent`` and ``matrix_parent_inverse`` for an Object
and called ``remap_parent``, which reads ``holder.location``; a
GreasePencilLayer has none. The commit failed as UNKNOWN ("Native commit
failed; 'GreasePencilLayer' object has no attribute 'location'"), rolled back.

Repointing the layer instead cannot keep it either: Blender's only way to set a
layer's parent (``grease_pencil_layer_parent_set``) recomputes the layer's
read-only inverse from where the parent is now, so this layer's inverse
(identity, set before the parent was first evaluated) would become a
translation and the strokes would move; it also leaks the bone name it
replaces. Since the fix the edit is refused before any write (UNSUPPORTED,
stage validation, ``program_fields.layer_parent_swaps``) and nothing changes.
Committing it with the inverse kept needs a native way to set a layer's parent
without recomputing its inverse.
"""

import json
import os
from pathlib import Path
import sys
import traceback

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from program_qa_helpers import no_stages  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.filepaths.file_preview_type = "NONE"
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
W = bpy.context.window

try:
    material = bpy.data.materials.new("Layer parent swap")
    bpy.data.materials.create_gpencil_data(material)
    data = bpy.data.grease_pencils.new("Layer parent swap")
    layer = data.layers.new("Layer")
    layer.frames.new(1).drawing.add_strokes([4])
    data.materials.append(material)
    obj = bpy.data.objects.new("Layer parent swap", data)
    parent = bpy.data.objects.new("Layer parent", None)
    for item in (obj, parent):
        W.scene.collection.objects.link(item)
    parent.location = (1., 0., 0.)
    layer.parent = parent
    W.view_layer.update()
    inverse = [list(row) for row in layer.matrix_parent_inverse]
    entry = lambda item: {"handle": queries.object_handle(item), "revision": queries.object_revision(W, item)}
    job = native.begin(W, "angelo_scene_program", {
        "mode": "APPLY", "read_set": [entry(parent)], "write_set": [entry(obj)], "expected_checks": [],
        "operations": [{"kind": "operator_run", "target": queries.object_handle(obj),
                        "parameters": {"operator": "grease_pencil.stroke_subdivide", "properties": {"number_cuts": 1}}}]})
    result = job.poll()
    while not (result.get("final") or result.get("ready_to_commit")):
        result = job.poll()
    outcome = job.commit() if result.get("ready_to_commit") else result
    report["observed"] = {key: outcome.get(key) for key in ("status", "error_code", "error", "rollback_verified",
                                                             "partial_changes_possible")}
    live = obj.data.layers["Layer"]
    report["checks"]["refused_before_writing"] = (outcome.get("error_code") == "UNSUPPORTED"
                                                  and not outcome.get("partial_changes_possible")
                                                  and "parent inverse" in (outcome.get("error") or ""))
    report["checks"]["data_and_layer_parent_unchanged"] = obj.data == data and live.parent == parent
    report["checks"]["parent_inverse_kept"] = [list(row) for row in live.matrix_parent_inverse] == inverse
    report["checks"]["no_stage_left"] = no_stages()
    report["success"] = all(report["checks"].values())
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "results.json").write_text(json.dumps(report, indent=2))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("GP_LAYER_PARENT_SWAP failed checks:", json.dumps(failed))
assert report["success"], failed
