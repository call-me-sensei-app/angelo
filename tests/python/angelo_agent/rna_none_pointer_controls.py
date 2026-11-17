# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The valid-value controls of test_blender_rna_none_pointer_params (the review of 57412c1708e, NIT): each makes
one of the cases' calls with a real value where the case passes None, and must return. A control proves the case
reaches the function it means: its TypeError comes from the None, not from a call that could never work. One per
family the fix touched (operator invokes, a pie's event, collections, node zones and every node type's poll,
sockets, objects, poses, keymaps, render engines, unit-test compares, layout templates)."""

import bpy

import rna_none_pointer_cases as cases

D = bpy.data
WINDOW = {"control_wm_fileselect_add", "control_wm_piemenu_begin", "control_ui_template_node_link",
          "control_ui_template_node_view"}


def background_control(case, attempt):
    def pair():
        tree = cases._geometry_tree()
        tree.nodes.new("GeometryNodeRepeatInput").pair_with_output(tree.nodes.new("GeometryNodeRepeatOutput"))

    def remove_input():
        node = cases._custom_node()
        node.inputs.remove(node.inputs[0])

    def unlink():
        obj = D.objects.new("Guard Linked", None)
        bpy.context.scene.collection.objects.link(obj)
        bpy.context.scene.collection.objects.unlink(obj)

    def compare_item():
        _keymap, items = cases._keymap_items()
        items[0].compare(items[0])
    calls = {
        "control_collection_objects_unlink": unlink,
        "control_node_poll_every_type": lambda: cases._on_every_node("poll", "node_tree", lambda n: n.id_data),
        "control_node_poll_instance_every_type": (
            lambda: cases._on_every_node("poll_instance", "node_tree", lambda n: n.id_data)),
        "control_repeat_input_pair_with_output": pair,
        "control_node_inputs_remove": remove_input,
        "control_mesh_unit_test_compare": lambda: cases._cube().data.unit_test_compare(mesh=cases._cube().data),
        "control_pose_apply_pose_from_action": (
            lambda: cases._armature_object().pose.apply_pose_from_action(cases._pose_action())),
        "control_object_calc_matrix_camera": (
            lambda: cases._camera().calc_matrix_camera(bpy.context.evaluated_depsgraph_get())),
        "control_keymap_item_compare": compare_item,
    }
    if case == "control_engine_camera_model_matrix":
        cases._render_engine_case(attempt, "camera_model_matrix", "camera", lambda engine: cases._camera())
        return
    attempt(calls[case], "")


def window_control(case, attempt):
    """An operator's invoke hands itself (``fileselect_add``) or its event (a pie) as a script's operator does;
    the layout templates draw a real node in a popup."""
    wm = bpy.context.window_manager
    window = wm.windows[0]
    if case in ("control_wm_fileselect_add", "control_wm_piemenu_begin"):
        class GuardInvoke(bpy.types.Operator):
            bl_idname, bl_label = "guard.invoke", "Guard Invoke"
            filepath: bpy.props.StringProperty(subtype='FILE_PATH')

            def invoke(self, context, event):
                if case == "control_wm_fileselect_add":   # The file browser holds the operator: it runs on.
                    attempt(lambda: context.window_manager.fileselect_add(self), "")
                    return {'RUNNING_MODAL'}
                attempt(lambda: context.window_manager.popup_menu_pie(event, lambda menu, ctx: None, title="Guard"),
                        "")
                return {'FINISHED'}

            def execute(self, context):
                return {'FINISHED'}
        bpy.utils.register_class(GuardInvoke)
        with bpy.context.temp_override(window=window):
            bpy.ops.guard.invoke('INVOKE_DEFAULT')
        return
    tree = cases._shader_tree()
    node = tree.nodes.new("ShaderNodeMath")
    call = {"control_ui_template_node_link": lambda layout: layout.template_node_link(tree, node, node.inputs[0]),
            "control_ui_template_node_view": lambda layout: layout.template_node_view(tree, node, node.inputs[0]),
            }[case]

    def draw(menu, _context):
        attempt(lambda: call(menu.layout), "")
    with bpy.context.temp_override(window=window):
        wm.popup_menu(draw, title="Guard")
