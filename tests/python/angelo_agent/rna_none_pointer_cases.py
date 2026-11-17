# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The cases of test_blender_rna_none_pointer_params, run inside Blender: each builds what its function needs, valid
everywhere except the one parameter given None, and hands the call to ``attempt(call, parameter)``."""

import bpy

D = bpy.data


def _cube():
    return D.objects["Cube"]


def _camera():
    return D.objects["Camera"]


def _armature_object():
    armature = D.armatures.new("Guard Armature")
    obj = D.objects.new("Guard Armature", armature)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    armature.edit_bones.new("Bone").tail = (0, 0, 1)
    bpy.ops.object.mode_set(mode='OBJECT')
    return obj


def _pose_action():
    """An action with one slot keying the armature's bone: what a pose asset holds."""
    action = D.actions.new("Guard Pose")
    slot = action.slots.new('OBJECT', "Guard")
    strip = action.layers.new("Layer").strips.new(type='KEYFRAME')
    curve = strip.channelbags.new(slot).fcurves.new('pose.bones["Bone"].location', index=0)
    curve.keyframe_points.insert(1, 0.5)
    return action


def _keymap_items():
    keymap = bpy.context.window_manager.keyconfigs.default.keymaps.new("Guard Keymap")
    keymap.keymap_items.new("wm.read_homefile", 'A', 'PRESS')
    return keymap, keymap.keymap_items


def _geometry_tree():
    return D.node_groups.new("Guard Nodes", "GeometryNodeTree")


def _shader_tree():
    return D.node_groups.new("Guard Shader", "ShaderNodeTree")


def _custom_node():
    """A node of a Python node tree type: the only kind whose sockets a script may remove."""
    class GuardTree(bpy.types.NodeTree):
        bl_idname, bl_label, bl_icon = "GuardTree", "Guard Tree", 'NODETREE'

    class GuardNode(bpy.types.Node):
        bl_idname, bl_label = "GuardNode", "Guard Node"
    bpy.utils.register_class(GuardTree)
    bpy.utils.register_class(GuardNode)
    node = D.node_groups.new("Guard Custom", "GuardTree").nodes.new("GuardNode")
    node.inputs.new("NodeSocketFloat", "In")
    node.outputs.new("NodeSocketFloat", "Out")
    return node


def _render_engine_case(attempt, method, parameter, value=lambda engine: None):
    """Render once with a Python engine whose render() makes the call on itself (an engine exists only then)."""
    class GuardEngine(bpy.types.RenderEngine):
        bl_idname, bl_label = "GUARD_ENGINE", "Guard Engine"

        def render(self, depsgraph):
            attempt(lambda: getattr(self, method)(value(self)), parameter)
    bpy.utils.register_class(GuardEngine)
    scene = bpy.context.scene
    scene.render.engine = "GUARD_ENGINE"
    scene.render.resolution_x = scene.render.resolution_y = 8
    scene.render.use_multiview = True   # Stereo: the camera calls read the camera on this path too.
    scene.render.views_format = 'STEREO_3D'
    bpy.ops.render.render()


def _every_node():
    """An instance of every node type each built-in tree takes: a node function's callee depends on the
    instance's type (``NodeInternal.poll`` calls its type's poll), so the class alone proves nothing."""
    for tree_type in ("ShaderNodeTree", "GeometryNodeTree", "CompositorNodeTree", "TextureNodeTree"):
        tree = D.node_groups.new("Guard " + tree_type, tree_type)
        for name in dir(bpy.types):
            cls = getattr(bpy.types, name)
            if isinstance(cls, type) and issubclass(cls, bpy.types.Node):
                try:
                    yield tree.nodes.new(name)
                except RuntimeError:
                    pass   # Not a type this tree takes, or an abstract base.


def _on_every_node(method, parameter, value):
    """``method(value(node))`` on every node type: returns when every one returned; raises a TypeError naming
    ``parameter`` when every one raised that; otherwise a RuntimeError naming the types that ended otherwise."""
    ends = {}
    for node in _every_node():
        try:
            getattr(node, method)(value(node))
            ended = "returned"
        except TypeError as error:
            ended = "TypeError" if parameter in str(error) else "TypeError (%s)" % error
        except Exception as error:   # noqa: BLE001 - reported below as an ending of its own.
            ended = type(error).__name__
        ends.setdefault(ended, []).append(node.bl_idname)
    if set(ends) == {"TypeError"}:
        raise TypeError("%s: None refused on all %d node types" % (parameter, len(ends["TypeError"])))
    if set(ends) != {"returned"}:
        raise RuntimeError("mixed endings: %s" % {k: v[:5] for k, v in ends.items()})


def _clip():
    """A one-frame movie clip with one track: what the tracking collections' ``active`` setters act on."""
    import os
    import struct
    import tempfile
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    path = os.path.join(tempfile.mkdtemp(), "guard0001.png")
    rows = b"".join(b"\x00" + b"\x80\x80\x80\xff" * 4 for _ in range(4))
    with open(path, "wb") as file:
        file.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
    clip = D.movieclips.load(path)
    clip.tracking.tracks.new(name="Guard Track", frame=1)
    return clip


def _set_active(collection, value):
    """``collection.active = value`` must hold afterwards (None: nothing active)."""
    collection.active = value
    if collection.active != value:
        raise AssertionError("active is %r after assigning %r" % (collection.active, value))


def _gp_groups(active_layer):
    """A Grease Pencil with a group made active, or (``active_layer``) a layer active that None must leave."""
    gp = D.grease_pencils.new("Guard")
    group = gp.layer_groups.new("Group")
    layer = gp.layers.new("Layer")
    gp.layer_groups.active = group
    if active_layer:
        gp.layers.active = layer
    return gp, group, layer


def property_case(case, attempt):
    """An editable pointer property assigned None where None means "nothing active" (the review of 57412c1708e,
    SHOULD 3): it returns and clears; the ``_value`` twins assign a real value, which must still take."""
    def tracking(owner, name, value=lambda clip: None):
        clip = _clip()
        collection = getattr(clip.tracking.objects[0] if owner == "object" else clip.tracking, name)
        _set_active(collection, value(clip))

    def groups(active_layer=False, real=False):
        gp, group, layer = _gp_groups(active_layer)
        if real:   # A layer active, so no group is; the group then takes.
            gp.layers.active = layer
            _set_active(gp.layer_groups, group)
            return
        gp.layer_groups.active = None
        if gp.layer_groups.active is not None or (active_layer and gp.layers.active != layer):
            raise AssertionError("None left %r active and layer %r" % (gp.layer_groups.active, gp.layers.active))
    first = lambda clip: clip.tracking.tracks[0]
    calls = {
        "tracking_tracks_active": lambda: tracking("tracking", "tracks"),
        "tracking_plane_tracks_active": lambda: tracking("tracking", "plane_tracks"),
        "tracking_object_tracks_active": lambda: tracking("object", "tracks"),
        "tracking_object_plane_tracks_active": lambda: tracking("object", "plane_tracks"),
        "tracking_tracks_active_value": lambda: tracking("tracking", "tracks", first),
        "tracking_object_tracks_active_value": lambda: tracking("object", "tracks", first),
        "grease_pencil_layer_groups_active": groups,
        "grease_pencil_layer_groups_active_keeps_layer": lambda: groups(active_layer=True),
        "grease_pencil_layer_groups_active_value": lambda: groups(real=True),
    }
    attempt(calls[case], "active")


def background_case(case, attempt):
    wm, scene = bpy.context.window_manager, bpy.context.scene
    calls = {
        "wm_fileselect_add": (lambda: wm.fileselect_add(None), "operator"),
        "wm_modal_handler_add": (lambda: wm.modal_handler_add(None), "operator"),
        "wm_invoke_props_popup": (lambda: wm.invoke_props_popup(None, None), "operator"),
        "wm_invoke_props_dialog": (lambda: wm.invoke_props_dialog(None), "operator"),
        "wm_invoke_search_popup": (lambda: wm.invoke_search_popup(None), "operator"),
        "wm_invoke_popup": (lambda: wm.invoke_popup(None), "operator"),
        "wm_invoke_confirm": (lambda: wm.invoke_confirm(None, None), "operator"),
        "blend_data_pack_linked_ids_hierarchy": (lambda: D.pack_linked_ids_hierarchy(None), "root_id"),
        "collection_objects_unlink": (lambda: scene.collection.objects.unlink(None), "object"),
        "collection_children_unlink": (lambda: scene.collection.children.unlink(None), "child"),
        "collection_exporters_remove": (lambda: D.collections.new("Guard").exporters.remove(None), "exporter"),
        "object_calc_matrix_camera": (lambda: _camera().calc_matrix_camera(None), "depsgraph"),
        "object_camera_fit_coords": (lambda: _camera().camera_fit_coords(None, (0, 0, 0)), "depsgraph"),
        "object_crazyspace_eval": (lambda: _cube().crazyspace_eval(None, scene), "depsgraph"),
        "mesh_unit_test_compare": (lambda: _cube().data.unit_test_compare(mesh=None), "mesh"),
        "mesh_unit_test_compare_omitted": (lambda: _cube().data.unit_test_compare(), "mesh"),
        "curves_unit_test_compare": (lambda: D.hair_curves.new("Guard").unit_test_compare(curves=None), "curves"),
        "lattice_unit_test_compare": (lambda: D.lattices.new("Guard").unit_test_compare(lattice=None), "lattice"),
        "grease_pencil_unit_test_compare": (
            lambda: D.grease_pencils.new("Guard").unit_test_compare(grease_pencil=None), "grease_pencil"),
        "node_inputs_remove": (lambda: _custom_node().inputs.remove(None), "socket"),
        "node_outputs_remove": (lambda: _custom_node().outputs.remove(None), "socket"),
        "node_poll_instance": (
            lambda: _shader_tree().nodes.new("ShaderNodeMath").poll_instance(None), "node_tree"),
        "bone_collections_remove": (lambda: D.armatures.new("Guard").collections.remove(None), "bone_collection"),
        "pose_apply_pose_from_action": (lambda: _armature_object().pose.apply_pose_from_action(None), "action"),
        "pose_blend_pose_from_action": (
            lambda: _armature_object().pose.blend_pose_from_action(None, blend_factor=0.5), "action"),
        "pose_apply_pose_from_slotless_action": (
            lambda: _armature_object().pose.apply_pose_from_action(D.actions.new("Guard")), "action"),
        "pose_blend_pose_from_slotless_action": (
            lambda: _armature_object().pose.blend_pose_from_action(D.actions.new("Guard"), blend_factor=0.5),
            "action"),
        "node_poll_every_type": (lambda: _on_every_node("poll", "node_tree", lambda node: None), "node_tree"),
        "node_poll_instance_every_type": (
            lambda: _on_every_node("poll_instance", "node_tree", lambda node: None), "node_tree"),
    }
    for name, node_type in (("repeat", "GeometryNodeRepeatInput"), ("simulation", "GeometryNodeSimulationInput"),
                            ("foreach", "GeometryNodeForeachGeometryElementInput"),
                            ("closure", "NodeClosureInput")):
        calls[name + "_input_pair_with_output"] = (
            lambda node_type=node_type: _geometry_tree().nodes.new(node_type).pair_with_output(None), "output_node")
    if case.startswith("keymap"):
        keymap, items = _keymap_items()
        calls.update({
            "keymap_item_compare": (lambda: items[0].compare(None), "item"),
            "keymap_items_find_match": (lambda: items.find_match(None, items[0]), "keymap"),
            "keymap_items_match_event": (lambda: items.match_event(None), "event"),
            "keymaps_find_match": (lambda: wm.keyconfigs.default.keymaps.find_match(None), "keymap"),
        })
    engine = {"engine_camera_shift_x": ("camera_shift_x", "camera"),
              "engine_camera_model_matrix": ("camera_model_matrix", "camera"),
              "engine_use_spherical_stereo": ("use_spherical_stereo", "camera"),
              "engine_get_preview_pixel_size": ("get_preview_pixel_size", "scene")}
    if case in engine:
        _render_engine_case(attempt, *engine[case])
        return
    call, parameter = calls[case]
    attempt(call, parameter)


def window_case(case, attempt):
    """A UILayout case draws a popup whose draw function makes the call; the node editor case turns an area into a
    node editor first."""
    wm = bpy.context.window_manager
    window = wm.windows[0]
    tree = _shader_tree()
    node = tree.nodes.new("ShaderNodeMath")
    if case.startswith("wm_piemenu_begin"):   # Never-null but optional: omitted, the C read an empty pointer.
        with bpy.context.temp_override(window=window):
            if case == "wm_piemenu_begin_event_omitted":
                attempt(lambda: wm.piemenu_begin__internal("Guard"), "event")
            else:
                attempt(lambda: wm.piemenu_begin__internal("Guard", event=None), "event")
        return
    if case == "node_editor_path_append":
        area = max(window.screen.areas, key=lambda a: a.width * a.height)
        area.type = 'NODE_EDITOR'
        attempt(lambda: area.spaces.active.path.append(None), "node_tree")
        return
    layout_calls = {
        "ui_template_component_menu": (lambda layout: layout.template_component_menu(None, "location"), "data"),
        "ui_template_node_link_ntree": (lambda layout: layout.template_node_link(None, node, node.inputs[0]),
                                        "ntree"),
        "ui_template_node_link_node": (lambda layout: layout.template_node_link(tree, None, node.inputs[0]), "node"),
        "ui_template_node_link_socket": (lambda layout: layout.template_node_link(tree, node, None), "socket"),
        "ui_template_node_view_node": (lambda layout: layout.template_node_view(tree, None, None), "node"),
        "ui_template_file_select_path": (lambda layout: layout.template_file_select_path(None), "params"),
        "ui_template_layers_used_data": (
            lambda layout: layout.template_layers(_cube(), "lock_location", None, "lock_location", 0), "data"),
    }
    call, parameter = layout_calls[case]

    def draw(menu, _context):
        attempt(lambda: call(menu.layout), parameter)
    with bpy.context.temp_override(window=window):
        wm.popup_menu(draw, title="Guard")
