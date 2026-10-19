# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only modifiers, material/node graphs, actions, rigs and constraints."""

from array import array
from itertools import islice
import bpy
from .scene_read_fields import (MODIFIER_FIELDS, REVISION_MODIFIERS, CONSTRAINT_FIELDS,
                                CONSTRAINT_COMMON_FIELDS, CONSTRAINT_TYPE_FIELDS, NODE_FIELDS,
                                KNOWN_NODES, TEXTURE_MAPPING_FIELDS, COLOR_MAPPING_FIELDS,
                                CAMERA_FIELDS, CAMERA_DOF_FIELDS, CAMERA_STEREO_FIELDS, LIGHT_FIELDS)
from .scene_read_values import BudgetExceeded, label, finite_number, vector, session_uid
from .action_camera_light_spec import is_default_light_graph


class MetadataReader:
    """Read closed metadata; the owner supplies document-scoped resource IDs.

    Retains only a resource formatter and a bound, never Blender datablocks.
    """
    def __init__(self, resource, max_list):
        self.resource = resource
        self.max_list = max_list

    def safe_value(self, value):
        if value is None or type(value) is bool or type(value) is int:
            return value
        if type(value) is float:
            return finite_number(value)
        if type(value) is str:
            return label(value)
        if hasattr(value, "session_uid"):
            return self.resource(value, "OBJECT" if isinstance(value, bpy.types.Object) else None)
        if isinstance(value, (list, tuple, array)) or type(value).__name__ in ("Vector", "Color", "Euler", "Quaternion", "bpy_prop_array"):
            return [self.safe_value(v) for v in islice(value, 16)]
        if type(value).__name__ == "Matrix":
            return [vector(row) for row in value]
        return None

    def settings(self, value, fields):
        result = {}
        for field in fields:
            if hasattr(value, field):
                result[field] = self.safe_value(getattr(value, field))
        return result

    def constraint(self, constraint, work):
        fields = CONSTRAINT_COMMON_FIELDS + CONSTRAINT_TYPE_FIELDS.get(constraint.type, CONSTRAINT_FIELDS)
        work.take(items=1 + len(fields))
        result = {"name": label(constraint.name), "type": constraint.type,
                  "settings": self.settings(constraint, fields)}
        if constraint.type not in CONSTRAINT_TYPE_FIELDS:
            # A generic type: every natively writable setting (generic_settings), bounded.
            from .generic_settings import values
            work.take(items=8)
            result["generic"] = values(constraint, lambda obj: label(obj.name))
        if hasattr(constraint, "targets"):
            if len(constraint.targets) > self.max_list:
                raise BudgetExceeded()
            result["targets"] = []
            for target in constraint.targets:
                work.take(items=1)
                result["targets"].append(self.settings(target, ("target", "subtarget", "weight")))
        return result

    def color_ramp(self, ramp, work):
        work.take(items=1 + len(ramp.elements))
        return {"interpolation": ramp.interpolation, "color_mode": ramp.color_mode,
                "hue_interpolation": ramp.hue_interpolation,
                "elements": [{"position": finite_number(e.position), "color": vector(e.color)} for e in ramp.elements]}

    def nodes(self, tree, work, seen=None):
        if tree is None:
            return None
        if len(tree.nodes) > self.max_list or len(tree.links) > self.max_list:
            raise BudgetExceeded("Node graph exceeds observation budget")
        seen = set() if seen is None else set(seen)
        result = {"resource": self.resource(tree), "nodes": [], "links": [], "revision_limitations": []}
        if session_uid(tree) in seen or len(seen) >= 4:
            result["revision_limitations"].append("node_group_cycle_or_depth")
            return result
        seen.add(session_uid(tree))
        result["animation"] = self.animation(tree, work)
        for node in tree.nodes:
            work.take(items=1)
            inputs = []
            if len(node.inputs) > self.max_list or len(node.outputs) > self.max_list:
                raise BudgetExceeded()
            for socket in node.inputs:
                work.take(items=1)
                item = {"name": label(socket.name), "identifier": label(socket.identifier),
                        "type": socket.type, "linked": bool(socket.is_linked)}
                # Never emit a script, arbitrary string default, image/file path or
                # IDProperty. Numeric/vector/bool sockets are explicit semantic data.
                if socket.type in {"VALUE", "INT", "BOOLEAN", "VECTOR", "RGBA", "ROTATION"} and hasattr(socket, "default_value"):
                    item["default"] = self.safe_value(socket.default_value)
                elif socket.type in {"OBJECT", "MATERIAL", "COLLECTION", "IMAGE"} and hasattr(socket, "default_value"):
                    item["resource"] = self.resource(socket.default_value, socket.type)
                    if socket.default_value is not None:
                        result["revision_limitations"].append("node_resource_socket_dependency")
                elif socket.type == "STRING":
                    result["revision_limitations"].append("unread_string_socket")
                inputs.append(item)
            outputs = []
            for socket in node.outputs:
                work.take(items=1)
                item = {"name": label(socket.name), "identifier": label(socket.identifier), "type": socket.type}
                if socket.type in {"VALUE", "INT", "BOOLEAN", "VECTOR", "RGBA", "ROTATION"} and hasattr(socket, "default_value"):
                    item["default"] = self.safe_value(socket.default_value)
                outputs.append(item)
            item = {"name": label(node.name), "label": label(node.label), "type": node.bl_idname,
                    "mute": bool(node.mute), "settings": self.settings(node, NODE_FIELDS), "inputs": inputs,
                    "outputs": outputs}
            if any(isinstance(v, dict) and "id" in v for v in item["settings"].values()):
                result["revision_limitations"].append("node_resource_setting_dependency")
            if node.bl_idname not in KNOWN_NODES and node.bl_idname != "NodeFrame":  # Frames: node_graph_check.
                # A generic node type (node_generic) is fingerprinted by its writable settings.
                from .node_generic import NodeRuleError, check_type
                from .generic_settings import foreign_ids, values
                try:
                    check_type(node.bl_idname, "SHADER" if getattr(tree, "bl_idname", "") == "ShaderNodeTree" else "GEOMETRY")
                    if foreign_ids(node) and node.bl_idname not in {"ShaderNodeTexImage", "ShaderNodeTexEnvironment"}:
                        raise NodeRuleError("datablock")
                    work.take(items=8)
                    item["generic"] = values(node, lambda value: label(value.name))
                except NodeRuleError:
                    result["revision_limitations"].append("unfingerprinted_node:" + node.bl_idname)
            if node.bl_idname == "ShaderNodeTexNoise":
                # Embedded mappings are mutable evaluation inputs, not ID links.
                # Read their closed numeric/enum controls before admitting Noise.
                work.take(items=len(TEXTURE_MAPPING_FIELDS) + len(COLOR_MAPPING_FIELDS))
                item["texture_mapping"] = self.settings(node.texture_mapping, TEXTURE_MAPPING_FIELDS)
                item["color_mapping"] = self.settings(node.color_mapping, COLOR_MAPPING_FIELDS)
                item["color_mapping"]["color_ramp"] = self.color_ramp(node.color_mapping.color_ramp, work)
            if hasattr(node, "image"):
                item["image"] = self.resource(node.image, "IMAGE")
                if node.image:
                    result["revision_limitations"].append("image_pixel_dependency")
            if hasattr(node, "node_tree"):
                item["group"] = self.resource(node.node_tree, "NODETREE")
                item["group_graph"] = self.nodes(node.node_tree, work, seen)
                if item["group_graph"]:
                    result["revision_limitations"].extend(item["group_graph"]["revision_limitations"])
            if hasattr(node, "color_ramp"):
                item["color_ramp"] = self.color_ramp(node.color_ramp, work)
            result["nodes"].append(item)
        for link in tree.links:
            work.take(items=1)
            result["links"].append({"from_node": label(link.from_node.name), "from_socket": label(link.from_socket.identifier),
                                    "to_node": label(link.to_node.name), "to_socket": label(link.to_socket.identifier),
                                    "valid": bool(link.is_valid), "muted": bool(getattr(link, "is_muted", False))})
        result["revision_limitations"] = sorted(set(result["revision_limitations"]))
        return result

    def materials(self, obj, work):
        if len(obj.material_slots) > self.max_list:
            raise BudgetExceeded()
        result = []
        for index, slot in enumerate(obj.material_slots):
            work.take(items=1)
            material = slot.material
            item = {"slot": index, "link": slot.link, "material": self.resource(material, "MATERIAL")}
            if material:
                item["settings"] = self.settings(material, ("diffuse_color", "metallic", "roughness", "use_nodes", "surface_render_method", "displacement_method",
                                                        "use_backface_culling", "use_backface_culling_shadow", "use_backface_culling_lightprobe_volume",
                                                        "use_transparent_shadow", "use_transparency_overlap", "use_raytrace_refraction", "thickness_mode",
                                                        "use_thickness_from_shadow", "volume_intersection_method", "max_vertex_displacement", "pass_index"))
                if getattr(material, "is_grease_pencil", False):
                    # Grease Pencil stroke and fill settings: every writable value; images stay a limitation.
                    from .generic_settings import foreign_ids, values
                    style = material.grease_pencil
                    if foreign_ids(style):
                        item["revision_limitations"] = ["grease_pencil_material_image"]
                    else:
                        work.take(items=8)
                        item["grease_pencil"] = values(style, lambda value: label(value.name))
                item["node_tree"] = self.shared_nodes(material.node_tree if material.use_nodes else None, work)
            result.append(item)
        return result

    def shared_nodes(self, tree, work):
        """``nodes(tree)``, read once per pass however many objects share the tree (scene_shared_reads)."""
        if tree is None:
            return None
        from .scene_shared_reads import shared_read

        def read(budget, _unsupported, resource):
            formatter, self.resource = self.resource, resource  # Records the identity-map registrations.
            try:
                return self.nodes(tree, budget)
            finally:
                self.resource = formatter
        return shared_read(work, ("nodes", tree.as_pointer()) if hasattr(tree, "as_pointer") else None, read,
                           resource=self.resource)

    def modifiers(self, obj, work):
        if len(obj.modifiers) > self.max_list:
            raise BudgetExceeded()
        result, unsupported = [], []
        for modifier in obj.modifiers:
            work.take(items=1)
            fields = MODIFIER_FIELDS.get(modifier.type, ())
            item = {"name": label(modifier.name), "type": modifier.type,
                    "show_viewport": bool(modifier.show_viewport), "show_render": bool(modifier.show_render),
                    "show_in_editmode": bool(modifier.show_in_editmode), "show_on_cage": bool(modifier.show_on_cage),
                    "settings": self.settings(modifier, fields)}
            if modifier.type not in REVISION_MODIFIERS and modifier.type != "NODES":
                # A generic type is fingerprinted by every natively writable setting (generic_settings).
                from .generic_settings import DENIED_MODIFIERS, foreign_ids, values
                if modifier.type in DENIED_MODIFIERS or foreign_ids(modifier):
                    unsupported.append("modifier:" + modifier.type)
                else:
                    work.take(items=8)
                    item["settings"] = values(modifier, lambda obj: label(obj.name))
            if modifier.type == "PARTICLE_SYSTEM":  # Its ParticleSettings, which particle_set writes.
                from .particle_rules import summary
                item["particles"] = summary(modifier.particle_system.settings, lambda target: label(target.name))
            if modifier.type == "BEVEL" and getattr(modifier, "profile_type", None) == "CUSTOM":
                unsupported.append("bevel_custom_profile")
            if modifier.type == "DISPLACE" and getattr(modifier, "texture", None) is not None:
                unsupported.append("displace_texture_dependency")
            if modifier.type in {"MESH_DEFORM", "SURFACE_DEFORM"} and getattr(modifier, "is_bound", False):
                unsupported.append("modifier_bind_cache")
            if modifier.type == "MULTIRES" and modifier.total_levels:
                unsupported.append("multires_displacement_requires_exact_sculpt_observation")
            if modifier.type == "NODES":
                from .scene_catalog_graphs import CatalogGraphUnsupported, read_catalog_geometry
                try:
                    item["node_tree"] = read_catalog_geometry(modifier, self, work)
                except (CatalogGraphUnsupported, AttributeError, TypeError, RuntimeError) as error:
                    item["node_tree"] = self.nodes(modifier.node_group, work)
                    reason = str(error) if isinstance(error, CatalogGraphUnsupported) else "catalog_geometry_native_schema"
                    unsupported.append("geometry_nodes:" + reason)
            result.append(item)
        return result, unsupported

    def action_curves(self, action):
        # Native layered Actions and older legacy Actions, read without slot changes.
        if hasattr(action, "layers") and len(action.layers):
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in getattr(strip, "channelbags", ()):
                        for curve in bag.fcurves:
                            yield curve
        else:
            for curve in getattr(action, "fcurves", ()):
                yield curve

    def action(self, action, work):
        if action is None:
            return None
        curves = []
        limitations = []
        for curve in self.action_curves(action):
            work.take(items=1+len(curve.keyframe_points)+len(curve.sampled_points),
                      elements=len(curve.keyframe_points) * 6 + len(curve.sampled_points) * 2)
            if len(curves) >= self.max_list:
                raise BudgetExceeded()
            if len(curve.data_path) > 256:
                limitations.append("bounded_animation_channel_name")
            work.take(items=len(curve.modifiers) * 8)
            keys = [{"co": vector(p.co), "left": vector(p.handle_left), "right": vector(p.handle_right),
                     "interpolation": p.interpolation, "easing": p.easing, "type": p.type,
                     "left_type": p.handle_left_type, "right_type": p.handle_right_type,
                     "amplitude": finite_number(p.amplitude), "back": finite_number(p.back), "period": finite_number(p.period)}
                    for p in curve.keyframe_points]
            from .anim_fmodifiers import revision as modifier_revision  # Every writable setting of each modifier.
            curves.append({"channel": label(curve.data_path, 256), "index": curve.array_index,
                           "mute": bool(curve.mute), "extrapolation": curve.extrapolation,
                           "keyframes": keys, "samples": [vector(p.co) for p in curve.sampled_points],
                           "modifiers": modifier_revision(curve, lambda value: label(value.name))})
        return {"resource": self.resource(action, "ACTION"), "frame_range": vector(action.frame_range),
                "users": int(action.users), "slots": [label(slot.identifier) for slot in list(action.slots)[:32]],
                "settings": self.settings(action, ("use_frame_range", "frame_start", "frame_end", "use_cyclic")),
                "curves": curves, "curve_count": len(curves), "keyframe_count": sum(len(c["keyframes"]) for c in curves),
                "revision_limitations": sorted(set(limitations))}

    def animation(self, value, work):
        animation = getattr(value, "animation_data", None)
        if animation is None:
            return {"action": None, "nla_tracks": [], "drivers": []}
        result = {"action": self.action(animation.action, work), "nla_tracks": [], "drivers": [],
                  "settings": self.settings(animation, ("action_slot_handle", "action_blend_type", "action_extrapolation", "action_influence", "use_nla", "use_tweak_mode"))}
        for track in animation.nla_tracks:
            work.take(items=1)
            strips = []
            for strip in track.strips:
                work.take(items=1)
                slot = strip.action_slot
                strips.append({"name": label(strip.name), "type": strip.type, "slot": label(slot.identifier) if slot else None,
                               "settings": self.settings(strip, ("frame_start", "frame_end", "action_frame_start", "action_frame_end", "scale", "repeat", "blend_type", "extrapolation", "influence", "mute", "use_animated_influence", "use_animated_time",
                                                                 "blend_in", "blend_out", "use_auto_blend", "use_reverse", "use_sync_length", "select")),
                               "action": self.action(strip.action, work)})
            result["nla_tracks"].append({"name": label(track.name), "mute": bool(track.mute), "solo": bool(track.is_solo),
                                         "lock": bool(track.lock), "select": bool(track.select), "strips": strips})
        from .driver_rules import describe
        for curve in animation.drivers:
            work.take(items=1 + len(curve.driver.variables))
            # Every variable and target (a changed driver fails the binding); never an expression's code.
            result["drivers"].append(describe(curve, label))
        return result

    def camera_light(self, obj, work):
        """Closed camera/light data settings; None for every other object type."""
        data = obj.data
        # One item per closed settings group, like material settings; the
        # light's node graph is charged per node/socket by nodes().
        if obj.type == "CAMERA":
            work.take(items=3)
            limitations = []
            if data.type == "CUSTOM":
                limitations.append("camera_custom_shader")
            if len(data.background_images):
                limitations.append("camera_background_image_dependency")
            return {"settings": self.settings(data, CAMERA_FIELDS), "dof": self.settings(data.dof, CAMERA_DOF_FIELDS),
                    "stereo": self.settings(data.stereo, CAMERA_STEREO_FIELDS),
                    "background_images": len(data.background_images), "revision_limitations": limitations}
        if obj.type == "LIGHT":
            work.take(items=1)
            # Every light owns a node tree. Its graph joins the revision; only
            # Blender's default Emission graph is a supported configuration.
            default = is_default_light_graph(data.node_tree)
            return {"settings": self.settings(data, LIGHT_FIELDS), "node_tree": self.nodes(data.node_tree, work),
                    "default_node_graph": default, "revision_limitations": [] if default else ["light_node_tree"]}
        return None

    def rig(self, obj, work):
        result = {"constraints": [], "vertex_groups": [], "bones": [], "pose_bones": []}
        for constraint in obj.constraints:
            result["constraints"].append(self.constraint(constraint, work))
        for group in obj.vertex_groups:
            work.take(items=1)
            result["vertex_groups"].append({"index": group.index, "name": label(group.name), "locked": bool(group.lock_weight)})
        if obj.type != "ARMATURE":
            return result
        data = obj.data
        bones = data.edit_bones if obj.mode == "EDIT" else data.bones
        for bone in bones:
            work.take(items=1)
            pose_bone = obj.pose.bones.get(bone.name) if obj.pose else None
            result["bones"].append({"name": label(bone.name), "parent": label(bone.parent.name) if bone.parent else None,
                                    "head": vector(bone.head if obj.mode == "EDIT" else bone.head_local),
                                    "tail": vector(bone.tail if obj.mode == "EDIT" else bone.tail_local),
                                    "deform": bool(bone.use_deform), "selected": bool(getattr(bone, "select", pose_bone.select if pose_bone else False)),
                                    "hide": bool(bone.hide), "inherit_scale": bone.inherit_scale,
                                    "settings": self.settings(bone, ("roll", "matrix_local", "use_connect", "use_inherit_rotation", "use_local_location",
                                                                  "envelope_distance", "envelope_weight", "head_radius", "tail_radius", "bbone_segments"))})
        if obj.pose:
            for bone in obj.pose.bones:
                work.take(items=1)
                result["pose_bones"].append({"name": label(bone.name), "matrix_basis": [vector(row) for row in bone.matrix_basis],
                                            "rotation_mode": bone.rotation_mode,
                                            "selected": bool(getattr(bone, "select", False)), "hidden": bool(getattr(bone, "hide", False)),
                                            "settings": self.settings(bone, ("lock_location", "lock_rotation", "lock_scale", "lock_ik_x", "lock_ik_y", "lock_ik_z",
                                                                          "use_ik_limit_x", "use_ik_limit_y", "use_ik_limit_z", "ik_min_x", "ik_max_x",
                                                                          "ik_min_y", "ik_max_y", "ik_min_z", "ik_max_z", "ik_stiffness_x", "ik_stiffness_y", "ik_stiffness_z", "ik_stretch")),
                                            "constraints": [self.constraint(c, work) for c in bone.constraints]})
        return result
