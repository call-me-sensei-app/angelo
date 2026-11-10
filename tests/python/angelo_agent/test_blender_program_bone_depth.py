# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed programs on deep hierarchies: humanoid rigs with fingers, long bone chains, nested bone collections and
nested node frames bind, commit and undo in one step; a change at the deepest element between bind and commit is
caught, the innermost nested bone collection and node frame included.

The depth-first dependency walks counted an armature's parent/child bone chains as nesting (every bone is also in
``Armature.bones``), so a chain of 11 bones refused every program (doc/angelo/native_coverage.rst, "Dependency walk
depth"). Development fixtures: synthetic chains. Held out (chosen before the fix): a UE5 Mannequin-named humanoid
(skeleton_engine_e2e/make_rigs.py), Mixamo's, VRM 1.0 (VRoid naming) and an Auto-Rig Pro-style hierarchy rebuilt from
their published naming, Rigify's human metarig, 16 nested bone collections and 16 nested node frames. The links each
walk finds must equal the old depth-first walk's with its depth bound lifted.

Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import statistics
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-bone-depth-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import native_programs as native, scene_queries as queries  # noqa: E402
from angelo_agent import exact_fence, program_dependencies as dependencies  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
from program_qa_helpers import no_stages, commit_operator  # noqa: E402
from rig_layouts_qa import arp, chain, depth, layout_rig, mixamo, ue5, vrm  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "version": bpy.app.version_string,
          "rigs": {}, "fingerprint_ms": {}}
window = bpy.context.window_manager.windows[0]


def check(name, condition):
    report["checks"][name] = bool(condition)
    assert condition, name


def settle(job):
    for _ in range(2000):
        result = job.poll()
        if result.get("final") or result.get("ready_to_commit"):
            return result
    raise AssertionError("cooperative native program did not settle")


def commit(job):
    result = bpy.ops.angelo.program_qa_commit("EXEC_DEFAULT", True, program_id=job.id)
    return "FINISHED" in result and job.result.get("status") == "committed"


def program(obj, operations):
    binding = {"handle": queries.object_handle(obj), "revision": queries.object_revision(window, obj)}
    return validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [binding],
                                                  "operations": operations})


def bone_set(obj, bone, **parameters):
    return {"kind": "bone_set", "target": queries.object_handle(obj), "parameters": dict(bone=bone, **parameters)}


def unbounded_links(root, visited=None):
    """The depth-first walk as it was before breadth first, without its depth bound and every collection iterated,
    structs identified as the walks now identify them (pointer and RNA type): the oracle of WHICH links."""
    visited = set() if visited is None else visited
    if (root.as_pointer(), root.bl_rna.identifier) in visited:
        return
    visited.add((root.as_pointer(), root.bl_rna.identifier))
    mesh = isinstance(root, bpy.types.Mesh)
    for name, kind, prop in dependencies.link_fields(root.bl_rna):
        if kind != "POINTER" and mesh:
            continue
        item = getattr(root, name, None)
        if kind == "POINTER":
            if item is not None:
                if isinstance(item, bpy.types.ID):
                    yield root, prop, item
                elif hasattr(item, "bl_rna"):
                    yield from unbounded_links(item, visited)
        elif kind == "COLLECTION":
            for child in item:
                if hasattr(child, "bl_rna"):
                    yield from unbounded_links(child, visited)
        else:
            for child in item:
                if child is not None:
                    yield root, prop, child


def link_set(links):
    return sorted((holder.bl_rna.identifier, holder.as_pointer(), prop.identifier, target.as_pointer())
                  for holder, prop, target in links)


def links_equal_oracle(name, block):
    """Outside a fence every collection is walked; inside one, link-free collections are skipped: both find exactly
    the oracle's links."""
    expected = link_set(unbounded_links(block))
    check(name + "_links_equal_oracle_" + block.bl_rna.identifier, link_set(dependencies._embedded_links(block)) == expected)
    with exact_fence.fence():
        check(name + "_fenced_links_equal_oracle_" + block.bl_rna.identifier,
              link_set(dependencies._walk_links(block)) == expected)


def refuses_after(name, obj, operations, change, restore):
    """A program binds and stages; ``change`` between bind and commit makes its commit refuse. ``operations(obj)``
    builds the operations after the baseline (an undo push renews handles)."""
    bpy.ops.ed.undo_push(message="refusal baseline " + name)
    queries.invalidate()
    job = native.begin(window, "angelo_scene_program", program(obj, operations(obj)))
    check(name + "_stages", settle(job).get("ready_to_commit"))
    change()
    check(name + "_change_refuses_commit", not commit(job))
    native.cleanup_all()
    restore()


def prove(name, obj, deepest):
    """Links equal the oracle's; a deepest-bone edit commits, one Undo restores it, one Redo replays it; a live change
    of the deepest bone between bind and commit refuses the commit."""
    for block in (obj, obj.data):
        links_equal_oracle(name, block)
    bpy.ops.ed.undo_push(message="bone depth baseline " + name)
    queries.invalidate()
    obj = window.scene.objects[name]
    before = dependencies._fingerprint(obj)
    samples = []   # The bind-time fingerprint's cost per rig (a signal: median of 5, read with the machine's load).
    for _ in range(5):
        started = time.perf_counter()
        dependencies._fingerprint(obj)
        samples.append(time.perf_counter() - started)
    report["fingerprint_ms"][name] = round(statistics.median(samples) * 1000, 2)
    segments = obj.data.bones[deepest].bbone_segments
    job = native.begin(window, "angelo_scene_program", program(obj, [bone_set(obj, deepest,
                                                                             settings={"bbone_segments": segments + 2})]))
    staged = settle(job)
    report["rigs"][name] = {"staged": staged.get("status"), "error": staged.get("error"), "bones": len(obj.data.bones),
                            "max_depth": max(depth(bone) for bone in obj.data.bones)}
    check(name + "_binds_and_stages", staged.get("ready_to_commit"))
    check(name + "_commits", commit(job) and window.scene.objects[name].data.bones[deepest].bbone_segments == segments + 2)
    bpy.ops.ed.undo()
    queries.invalidate()
    check(name + "_one_undo", dependencies._fingerprint(window.scene.objects[name]) == before and no_stages())
    bpy.ops.ed.redo()
    queries.invalidate()
    check(name + "_one_redo", window.scene.objects[name].data.bones[deepest].bbone_segments == segments + 2)
    bpy.ops.ed.undo()
    queries.invalidate()
    obj = window.scene.objects[name]
    job = native.begin(window, "angelo_scene_program", program(obj, [bone_set(obj, deepest, settings={"use_deform": False})]))
    check(name + "_restaged", settle(job).get("ready_to_commit"))
    obj.data.bones[deepest].bbone_x = obj.data.bones[deepest].bbone_x + 0.25   # Between bind and commit, deepest bone.
    check(name + "_deepest_change_refuses_commit", not commit(job) and obj.data.bones[deepest].use_deform)
    native.cleanup_all()
    obj.data.bones[deepest].bbone_x = obj.data.bones[deepest].bbone_x - 0.25


def nested_bone_collections(levels=16):
    obj = layout_rig("Collections", chain(2))
    parent = None
    for index in range(levels):
        collection = obj.data.collections.new("Group%d" % index, parent=parent)
        parent = collection
    parent.assign(obj.data.bones["C1"])
    return obj


def nested_node_frames(levels=16):
    material = bpy.data.materials.new("Frames")
    tree = material.node_tree if material.node_tree is not None else None
    if tree is None:
        material.use_nodes = True
        tree = material.node_tree
    parent = None
    for index in range(levels):
        frame = tree.nodes.new("NodeFrame")
        frame.name = "Frame%d" % index
        if parent is not None:
            frame.parent = parent
        parent = frame
    value = tree.nodes.new("ShaderNodeValue")
    value.parent = parent
    mesh = bpy.data.meshes.new("Framed")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    mesh.materials.append(material)
    obj = bpy.data.objects.new("Framed", mesh)
    window.scene.collection.objects.link(obj)
    window.view_layer.update()
    return obj


def run_checks():
    bpy.context.preferences.edit.use_global_undo = True
    native.register()
    bpy.utils.register_class(commit_operator(report))
    for length in (10, 11, 16, 32, 64):
        obj = layout_rig("Chain%d" % length, chain(length))
        obj.name = "Chain%d" % length
        prove(obj.name, obj, "C%d" % (length - 1))
    for name, layout in (("UE5", ue5), ("Mixamo", mixamo), ("VRM", vrm), ("ARPStyle", arp)):
        obj = layout_rig(name, layout())
        deepest = max(obj.data.bones, key=depth).name
        prove(name, obj, deepest)
    bpy.ops.preferences.addon_enable(module="rigify")
    bpy.ops.object.armature_human_metarig_add()
    metarig = bpy.context.view_layer.objects.active
    metarig.name = "Metarig"
    prove("Metarig", metarig, max(metarig.data.bones, key=depth).name)
    collections = nested_bone_collections()
    prove("Collections", collections, "C1")
    collections = window.scene.objects["Collections"]
    innermost = collections.data.collections_all["Group15"]
    refuses_after("innermost_collection", collections, lambda obj: [bone_set(obj, "C1", settings={"use_deform": False})],
                  lambda: setattr(innermost, "is_visible", False), lambda: setattr(innermost, "is_visible", True))
    framed = nested_node_frames()
    for block in (framed, framed.data, framed.data.materials[0]):
        links_equal_oracle("frames", block)
    bpy.ops.ed.undo_push(message="frames baseline")
    queries.invalidate()
    framed = window.scene.objects["Framed"]
    binding = {"handle": queries.object_handle(framed), "revision": queries.object_revision(window, framed)}
    arguments = validate_call("angelo_scene_program", {"mode": "APPLY", "read_set": [], "write_set": [binding],
                                                       "operations": [{"kind": "transform",
                                                                       "target": binding["handle"],
                                                                       "parameters": {"location": [1, 2, 3]}}]})
    job = native.begin(window, "angelo_scene_program", arguments)
    report["frames_staged"] = settle(job).get("error")
    check("frames_object_commits", commit(job) and tuple(window.scene.objects["Framed"].location) == (1, 2, 3))
    framed = window.scene.objects["Framed"]
    frame = framed.data.materials[0].node_tree.nodes["Frame15"]
    refuses_after("innermost_frame", framed, lambda obj: [{"kind": "transform", "target": queries.object_handle(obj),
                                                           "parameters": {"location": [3, 2, 1]}}],
                  lambda: setattr(frame, "label", "moved"), lambda: setattr(frame, "label", ""))
    check("no_stage_residue", no_stages() and not native.runtime_is_staging())
    report["success"] = True


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    native.cleanup_all()
(OUT / "bone-depth.json").write_text(json.dumps(report, indent=2, default=str))
print("ANGELO_BONE_DEPTH", json.dumps({key: report.get(key) for key in ("success", "paid_requests")}), flush=True)
assert report.get("success"), report.get("error")
