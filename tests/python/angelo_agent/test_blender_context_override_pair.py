# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Python context override never pairs a scene with another scene's view layer.

A depsgraph exists for a scene and one of its own view layers. Stock Blender
5.2 crashes (SIGSEGV in ``BKE_object_eval_eval_base_flags``) when a script
evaluates a scene with another scene's view layer, which two ordinary lines
do: ``temp_override(view_layer=<another scene's layer>)``, or
``temp_override(window=w, scene=s2, view_layer=s2's layer)`` (setting the
window drops the ``scene`` given in the same call, ``CTX_wm_window_set``, and
keeps ``view_layer``), followed by any ``bpy.ops`` call or
``evaluated_depsgraph_get()``. Any full-Python script a person approves can
write them. Angelo's guard has two halves, both checked here:

* ``temp_override`` raises ``TypeError`` when a ``scene`` or ``view_layer`` it
  names cannot apply (another scene's layer, a wrongly typed value) and leaves
  the context as it was (``bpy_rna_context.cc``).
* An inherited view layer that no longer belongs to the context scene (a
  nested window or scene override, or an operator that switched windows) is
  resolved from that scene instead (``CTX_data_view_layer``). The window
  first, then the scene and view layer in a nested override, keeps working:
  Angelo's own staged programs rely on it.

With ``--gui`` it adds the case only a second window reproduces: entering
Edit mode refreshes the other windows' tool systems, which drops the scene
override mid-operator, and Blender's view-layer update after the call used to
crash. No add-on, provider, account or paid call.
"""

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup Blender instance"
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-context-override-pair"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "checks": {}, "errors": {}, "gui": not bpy.app.background, "paid_requests": 0,
          "binary": bpy.app.binary_path}


def check(name, value):
    REPORT["checks"][name] = bool(value)  # Every case runs; the suite fails at the end.


def window():
    return bpy.context.window_manager.windows[0]


def artist():
    return bpy.data.scenes["Scene"]


def build_fixture():
    """A second scene with an object of its own and two view layers; the artist's scene keeps its default."""
    other = bpy.data.scenes.new("Other")
    mesh = bpy.data.meshes.new("Other Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new("Other Object", mesh)
    other.collection.objects.link(obj)
    other.view_layers.new("Second")
    for layer in other.view_layers:
        layer.objects.active = obj
    return other


def selected(scene, layer):
    return sorted(obj.name for obj in scene.objects if obj.select_get(view_layer=layer))


def deselect_everywhere():
    for scene in bpy.data.scenes:
        for layer in scene.view_layers:
            for obj in scene.objects:
                obj.select_set(False, view_layer=layer)


def context_state():
    return (bpy.context.window, bpy.context.scene, bpy.context.view_layer)


def refused(name, make_override, body, *expected):
    """The override raises TypeError on enter, its body never runs and the context is as before."""
    before = context_state()
    ran = []
    try:
        with make_override():
            ran.append(True)
            body()
        error = None
    except TypeError as caught:
        error = str(caught)
    REPORT["errors"][name] = error
    check(name + "_raises_type_error", error is not None and all(text in error for text in expected))
    check(name + "_body_never_ran", not ran)
    check(name + "_context_restored", context_state() == before)


def select_all():
    bpy.ops.object.select_all(action="SELECT")


def evaluate():
    bpy.context.evaluated_depsgraph_get()


def refusals(other):
    layer = other.view_layers["ViewLayer"]
    for verb, body in (("operator", select_all), ("depsgraph", evaluate)):
        refused("view_layer_of_another_scene_" + verb, lambda: bpy.context.temp_override(view_layer=layer), body,
                'view_layer "ViewLayer" belongs to scene "Other", not to the context scene "Scene"')
    refused("window_scene_view_layer_in_one_call",
            lambda: bpy.context.temp_override(window=window(), scene=other, view_layer=layer), select_all,
            'belongs to scene "Other"', "nested temp_override")
    refused("no_window_scene_view_layer_in_one_call",
            lambda: bpy.context.temp_override(window=None, scene=other, view_layer=layer), select_all,
            'belongs to scene "Other"', "nested temp_override")
    refused("view_layer_of_another_scene_inside_its_window",
            lambda: bpy.context.temp_override(window=window(), view_layer=layer), evaluate, 'belongs to scene "Other"')
    refused("view_layer_not_a_view_layer",
            lambda: bpy.context.temp_override(view_layer=other.objects[0]), select_all,
            "view_layer expects a ViewLayer, not Object")
    refused("scene_not_a_scene",
            lambda: bpy.context.temp_override(scene=other.objects[0]), select_all, "scene expects a Scene, not Object")
    # The refusal inside an outer override restores that outer override, not the plain context.
    with bpy.context.temp_override(scene=other, view_layer=other.view_layers["Second"]):
        refused("nested_refusal", lambda: bpy.context.temp_override(view_layer=artist().view_layers[0]), select_all,
                'belongs to scene "Scene", not to the context scene "Other"')
        check("nested_refusal_keeps_the_outer_pair",
              bpy.context.scene == other and bpy.context.view_layer == other.view_layers["Second"])


def applied_pairs(other):
    second = other.view_layers["Second"]
    deselect_everywhere()
    # Angelo's staged programs: the window first, then the scene and view layer in a nested override.
    with bpy.context.temp_override(window=window()), bpy.context.temp_override(scene=other, view_layer=second):
        select_all()
        graph = bpy.context.evaluated_depsgraph_get()
        pair = (graph.scene, graph.view_layer.name)
    check("window_then_pair_runs_on_that_view_layer", selected(other, second) == ["Other Object"]
          and selected(other, other.view_layers["ViewLayer"]) == [] and selected(artist(), artist().view_layers[0]) == [])
    check("window_then_pair_evaluates_that_pair", pair == (other, "Second"))
    deselect_everywhere()
    with bpy.context.temp_override(scene=other, view_layer=second):
        select_all()
    check("pair_in_one_call_without_window", selected(other, second) == ["Other Object"])
    # A layer of the window's own scene, given with the window in one call, applies.
    with bpy.context.temp_override(window=window(), view_layer=artist().view_layers[0]):
        evaluate()
    check("window_with_its_own_scene_layer", True)


def inherited_pairs(other):
    layer = other.view_layers["ViewLayer"]
    deselect_everywhere()
    # A window override inside a scene/view layer pair resets the scene; the inherited layer follows it.
    with bpy.context.temp_override(scene=other, view_layer=layer):
        with bpy.context.temp_override(window=None):
            select_all()
            graph = bpy.context.evaluated_depsgraph_get()
            pair = (graph.scene, graph.view_layer.name)
    check("inherited_layer_follows_a_window_override", pair == (artist(), "ViewLayer")
          and selected(other, layer) == [] and "Cube" in selected(artist(), artist().view_layers[0]))
    deselect_everywhere()
    with bpy.context.temp_override(scene=artist(), view_layer=artist().view_layers[0]):
        with bpy.context.temp_override(scene=other):
            select_all()
            graph = bpy.context.evaluated_depsgraph_get()
            pair = (graph.scene, graph.view_layer.name)
    check("inherited_layer_follows_a_nested_scene", pair == (other, "ViewLayer")
          and selected(other, layer) == ["Other Object"] and selected(artist(), artist().view_layers[0]) == [])


def run_background_cases():
    other = build_fixture()
    refusals(other)
    applied_pairs(other)
    inherited_pairs(other)
    return other


def open_second_window():
    """A second main window on another workspace: an Edit-mode switch then refreshes its tool system."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.window_new()
    windows = bpy.context.window_manager.windows
    windows[1].workspace = next(space for space in bpy.data.workspaces if space != windows[0].workspace)


def mode_switch_with_second_window(other):
    """Edit mode entered in a window-then-pair override; the switch drops the scene mid-operator."""
    obj = other.objects["Other Object"]
    layer = other.view_layers["ViewLayer"]
    members = dict(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj])
    with bpy.context.temp_override(window=window()), \
            bpy.context.temp_override(scene=other, view_layer=layer, **members):
        result = bpy.ops.object.editmode_toggle()
        after = (bpy.context.scene, obj.mode)
    with bpy.context.temp_override(window=window()), \
            bpy.context.temp_override(scene=other, view_layer=layer, **members):
        bpy.ops.object.editmode_toggle()
    REPORT["mode_switch"] = {"result": sorted(result), "scene_after": after[0].name, "mode_after": after[1],
                             "windows": len(bpy.context.window_manager.windows)}
    check("mode_switch_with_second_window_survives", "FINISHED" in result and after[1] == "EDIT")
    check("mode_switch_left_edit_mode", obj.mode == "OBJECT")
    check("mode_switch_second_window_open", len(bpy.context.window_manager.windows) == 2)


def finish():
    REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values()) and "error" not in REPORT
    (OUT / "context-override-pair.json").write_text(json.dumps(REPORT, indent=2, default=str))
    print("ANGELO_CONTEXT_OVERRIDE_PAIR", json.dumps({"success": REPORT["success"], "gui": REPORT["gui"]}),
          flush=True)
    if not bpy.app.background:
        bpy.ops.wm.quit_blender()


def guarded(step):
    try:
        return step()
    except Exception:
        REPORT["error"] = traceback.format_exc()
        return None


def gui_cases(other):
    guarded(lambda: mode_switch_with_second_window(other))
    finish()
    return None


def gui_start():
    """The workspace of a new window changes on the next event-loop pass, so the window case runs later."""
    other = guarded(run_background_cases)
    if other is not None:
        guarded(open_second_window)
    if "error" in REPORT:
        finish()
        return None
    bpy.app.timers.register(lambda: gui_cases(other), first_interval=1)
    return None


if bpy.app.background:
    guarded(run_background_cases)
    finish()
else:
    bpy.app.timers.register(gui_start, first_interval=1)
