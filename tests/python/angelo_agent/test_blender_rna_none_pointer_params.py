# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``None`` for a pointer parameter whose C function dereferences it raises a TypeError, never crashes (2.0.0).

Blender's Python API passes ``None`` for a pointer parameter as a null pointer unless the parameter is
``PROP_NEVER_NULL``. ``bpy.context.window_manager.fileselect_add(None)`` crashed the app, and so did every function
in ``CASES``: each callee dereferences the parameter with no null check (the sweep: every RNA function's pointer
parameters that accept None, listed by RNA introspection, each called with None in its own process, then the C
callee read for every one the calls could not reach; doc in the commit). Agent scripts reach all of them.

Run outside Blender (``run_native_suites.py --gui`` does this)::

  python3 tests/python/angelo_agent/test_blender_rna_none_pointer_params.py --binary /abs/Angelo --out /fresh/dir

Each case runs in its own Blender process (``--background``, or a window for the UILayout, pie and node editor
cases) and records how the call ended. The host checks every process exited normally and every case raised a
TypeError naming the parameter, except ``ACCEPTED`` (None is valid there and draws nothing), and every
``control_`` case, which makes a case's call with a real value and must return (rna_none_pointer_controls). A
function whose callee depends on the instance's type runs on an instance of every node type, never the class: the
class resolves to a Python override or a type-less copy (the review of 57412c1708e, MUST 1).
"""

import os, sys  # noqa: E401
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
from pathlib import Path

WINDOW = {"ui_template_component_menu", "ui_template_node_link_ntree", "ui_template_node_link_node",
          "ui_template_node_link_socket", "ui_template_node_view_node", "ui_template_file_select_path",
          "ui_template_layers_used_data", "node_editor_path_append", "wm_piemenu_begin_event_omitted",
          "wm_piemenu_begin_event_none"}
# None is valid there and must simply draw: no used-layers data; a material with no tree or no output node. A
# slotless action is a real one with nothing in it: a pose from it applies nothing (as Pose.backup_create).
ACCEPTED = {"ui_template_layers_used_data", "ui_template_node_view_node", "pose_apply_pose_from_slotless_action",
            "pose_blend_pose_from_slotless_action"}
# Editable pointer properties where None means nothing active: they must return and clear (``_value``: a real
# value still takes). Plane tracks have no Python constructor, so only their None case runs.
PROPERTIES = ["tracking_tracks_active", "tracking_plane_tracks_active", "tracking_object_tracks_active",
              "tracking_object_plane_tracks_active", "tracking_tracks_active_value",
              "tracking_object_tracks_active_value", "grease_pencil_layer_groups_active",
              "grease_pencil_layer_groups_active_keeps_layer", "grease_pencil_layer_groups_active_value"]
CONTROL_WINDOW = {"control_wm_fileselect_add", "control_wm_piemenu_begin", "control_ui_template_node_link",
                  "control_ui_template_node_view"}   # rna_none_pointer_controls.WINDOW
CONTROLS = sorted(CONTROL_WINDOW | {
    "control_collection_objects_unlink", "control_node_poll_every_type", "control_node_poll_instance_every_type",
    "control_repeat_input_pair_with_output", "control_node_inputs_remove", "control_mesh_unit_test_compare",
    "control_pose_apply_pose_from_action", "control_object_calc_matrix_camera", "control_keymap_item_compare",
    "control_engine_camera_model_matrix"})
CASES = [
    "wm_fileselect_add", "wm_modal_handler_add", "wm_invoke_props_popup", "wm_invoke_props_dialog",
    "wm_invoke_search_popup", "wm_invoke_popup", "wm_invoke_confirm",
    "keymap_item_compare", "keymap_items_find_match", "keymap_items_match_event", "keymaps_find_match",
    "blend_data_pack_linked_ids_hierarchy", "collection_objects_unlink", "collection_children_unlink",
    "collection_exporters_remove", "bone_collections_remove",
    "repeat_input_pair_with_output", "simulation_input_pair_with_output", "foreach_input_pair_with_output",
    "closure_input_pair_with_output", "node_inputs_remove", "node_outputs_remove", "node_poll_instance",
    "object_calc_matrix_camera", "object_camera_fit_coords", "object_crazyspace_eval",
    "pose_apply_pose_from_action", "pose_blend_pose_from_action",
    "engine_camera_shift_x", "engine_camera_model_matrix", "engine_use_spherical_stereo",
    "engine_get_preview_pixel_size",
    "mesh_unit_test_compare", "curves_unit_test_compare", "lattice_unit_test_compare",
    "grease_pencil_unit_test_compare", "mesh_unit_test_compare_omitted",
    "node_poll_every_type", "node_poll_instance_every_type", "pose_apply_pose_from_slotless_action",
    "pose_blend_pose_from_slotless_action",
] + sorted(WINDOW) + CONTROLS + PROPERTIES


def run_host():
    import argparse
    import subprocess
    import gui_launch
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--case", action="append", help="run only these cases")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {"success": False, "phase": "cases", "checks": {}, "cases": {}}
    for case in args.case or CASES:
        window = case in WINDOW or case in CONTROL_WINDOW
        result_path = out / (case + ".json")
        command = [str(Path(args.binary).resolve(strict=True)), "--factory-startup", "--disable-autoexec"]
        command += ([*gui_launch.window_flags(), "--window-geometry", "0", "0", "1280", "800"] if window
                    else ["--background"])
        command += ["--python", str(Path(__file__).resolve()), "--", "--rna-case", case, "--result", str(result_path)]
        with (out / (case + ".log")).open("w") as log:
            try:
                code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=120).returncode
            except subprocess.TimeoutExpired:
                code = "timeout"
        outcome = json.loads(result_path.read_text()) if result_path.exists() else {"ended": "no result"}
        outcome["exit_code"] = code
        expected = "returned" if case in ACCEPTED or case in PROPERTIES or case.startswith("control_") else "TypeError"
        report["cases"][case] = outcome
        report["checks"][case] = code == 0 and outcome.get("ended") == expected and (
            expected == "returned" or outcome.get("parameter", "") in outcome.get("message", ""))
        print("%-42s %-8s exit %s  %s" % (case, "ok" if report["checks"][case] else "FAILED", code,
                                         outcome.get("message", outcome.get("ended"))), flush=True)
    report["success"] = bool(report["checks"]) and all(report["checks"].values())
    report["phase"] = "complete"
    (out / "rna-none-pointer-params.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    return 0 if report["success"] else 1


if "--rna-case" not in sys.argv:
    if __name__ == "__main__":
        raise SystemExit(run_host())
    raise ImportError("test_blender_rna_none_pointer_params runs from host Python (see the module docstring)")

import bpy  # noqa: E402
import rna_none_pointer_cases as cases  # noqa: E402
import rna_none_pointer_controls as controls  # noqa: E402

ARGS = sys.argv[sys.argv.index("--") + 1:]
CASE, RESULT = ARGS[ARGS.index("--rna-case") + 1], Path(ARGS[ARGS.index("--result") + 1])


def record(ended, parameter="", message=""):
    RESULT.write_text(json.dumps({"ended": ended, "parameter": parameter, "message": message}))


def attempt(call, parameter):
    """Call ``call``; record a TypeError (the fix), any other exception, or a normal return."""
    try:
        call()
    except TypeError as error:
        record("TypeError", parameter, str(error))
    except Exception as error:   # noqa: BLE001 - recorded: the check expects a TypeError.
        record(type(error).__name__, parameter, str(error))
    else:
        record("returned", parameter)


if CASE in WINDOW or CASE in CONTROL_WINDOW:
    def later():
        (controls.window_control if CASE in CONTROL_WINDOW else cases.window_case)(CASE, attempt)
        bpy.app.timers.register(lambda: bpy.ops.wm.quit_blender() and None, first_interval=0.5)
        return None
    bpy.app.timers.register(later, first_interval=1.0)
elif CASE in PROPERTIES:
    cases.property_case(CASE, attempt)
elif CASE.startswith("control_"):
    controls.background_control(CASE, attempt)
else:
    cases.background_case(CASE, attempt)
