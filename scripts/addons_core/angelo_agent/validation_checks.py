# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed predicate implementations for already-resolved scene validation.

These functions consume a bounded ValidationReader and canonical checks from
validation_spec. They return (passed, aggregate_metrics); unsupported scope
raises the shared Inconclusive exception. They never mutate or authorize edits.
"""

import math

from .validation_spec import MAX_MESH_ELEMENTS, MAX_MODIFIERS, MAX_UV_LAYERS, MAX_BONES, MAX_KEYFRAMES
from .validation_reader import (Inconclusive, context_value, deformed, finite_values, name_of, number_vector,
                                resolve_target, values_near)
from .validation_camera_light import CAMERA_LIGHT_CHECKS, evaluate_camera_light
from .validation_properties import evaluate_property, evaluate_world
from .validation_animation import ANIMATION_CHECKS, evaluate_animation


def _material_values(material):
    if not getattr(material, "use_nodes", False):
        raise Inconclusive("Material-value assertions require a simple Principled BSDF node material")
    tree = material.node_tree
    if tree is None or len(tree.nodes) > 128 or len(tree.links) > 256:
        raise Inconclusive("Material graph exceeds the bounded simple-graph evaluator")
    nodes = [n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"]
    outputs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL" and getattr(n, "is_active_output", True)]
    if len(nodes) != 1 or len(outputs) != 1:
        raise Inconclusive("Only one active direct Principled BSDF material output is supported")
    node, output = nodes[0], outputs[0]
    surface = output.inputs.get("Surface")
    if surface is None or len(surface.links) != 1 or surface.links[0].from_node != node:
        raise Inconclusive("Material output is not directly connected to the supported shader")
    values = {}
    for key, name in (("base_color", "Base Color"), ("roughness", "Roughness"), ("metallic", "Metallic")):
        socket = node.inputs.get(name)
        if socket is None or socket.is_linked:
            raise Inconclusive("Linked or missing shader inputs cannot be asserted from scalar defaults")
        values[key] = number_vector(socket.default_value, 4) if key == "base_color" else float(socket.default_value)
    return values


def evaluate_check(reader, check):
    """Return a proof result and bounded metrics for one canonical predicate."""
    kind = check["kind"]
    if kind == "object_count":
        objects = reader.scene_objects()
        types = check["types"]
        count = sum(not types or o.type in types for o in objects)
        return check["minimum"] <= count <= check["maximum"], {"count": count, "scope": "complete_bound_scene_overlay"}
    obj = resolve_target(reader.context, check["target"])
    if kind == "object_exists":
        return (obj is not None) == check["expected"], {"exists": obj is not None}
    if obj is None:
        return False, {"exists": False}
    if kind == "property":
        return evaluate_property(reader, obj, check)
    if kind == "world_settings":
        return evaluate_world(reader, obj, check)
    if kind in ANIMATION_CHECKS:
        return evaluate_animation(reader, obj, check)
    if kind == "visibility":
        from .action_visibility import evaluate
        try:
            return evaluate(context_value(reader.context, "visibility"), context_value(reader.context, "view_layer"),
                            obj, check)
        except RuntimeError:  # hide_get on an object without a Base in the view layer.
            return False, {"hidden": None, "note": "not in the bound view layer"}
    if kind == "collection_membership":
        staged = context_value(reader.context, "memberships", None) or {}
        if check["target"] in staged:
            names = list(staged[check["target"]])
        else:
            # Never the stage's private collection, which still holds a new object while commit checks it.
            names = [collection.name for collection in obj.users_collection if not collection.name.startswith("__angelo_")]
        missing = [name for name in check["collections"] if name not in names]
        present = [name for name in check["absent"] if name in names]
        observed = {"collections": [name[:64] for name in names[:32]]}
        if missing or present:
            observed.update(missing=missing, still_present=present)
        return not missing and not present, observed
    if kind == "modifier_stack":
        if len(obj.modifiers) > MAX_MODIFIERS:
            raise Inconclusive("Modifier count exceeds observation limits")
        names = [modifier.name for modifier in obj.modifiers]
        misplaced = [item["name"] for item in check["positions"]
                     if item["index"] >= len(names) or names[item["index"]] != item["name"]]
        present = [name for name in check["absent"] if name in names]
        observed = {"stack": [name[:64] for name in names]}
        if misplaced or present:
            observed.update(misplaced=misplaced, still_present=present)
        return not misplaced and not present, observed
    if kind in CAMERA_LIGHT_CHECKS:
        return evaluate_camera_light(reader, obj, check)
    if kind == "object_type":
        return obj.type == check["expected"], {"type": obj.type}
    if kind == "object_name":
        name = name_of(reader.context)(obj)
        return name == check["expected"], {"name": name[:128]}
    if kind == "dimensions":
        values = reader.dimensions(obj, check["space"])
        scope = "EVALUATED_BOUNDS" if deformed(obj) else "BASE_MESH"
        return values_near(values, check["expected"], check.get("tolerance")), {"dimensions": values, "space": check["space"], "scope": scope}
    if kind == "transform":
        values = number_vector(getattr(obj, check["property"]), 3)
        return values_near(values, check["expected"], check.get("tolerance")), {check["property"]: values, "scope": "local_RNA"}
    if kind == "finite_coordinates":
        matrix = [number_vector(row) for row in obj.matrix_world]
        transforms = [*matrix, number_vector(obj.location), number_vector(obj.scale), number_vector(obj.rotation_euler)]
        valid = all(finite_values(values) for values in transforms)
        if obj.type == "MESH":
            mesh = reader.mesh(obj)
            valid = valid and mesh["finite"] and not mesh["invalid_indices"]
            return valid, {"finite": valid, "vertices_checked": mesh["vertices"], "scope": "BASE_MESH_AND_TRANSFORMS"}
        if obj.type == "EMPTY":
            return valid, {"finite": valid, "scope": "TRANSFORMS_ONLY"}
        raise Inconclusive("Finite geometry verification is supported only for MESH and transform-only EMPTY objects")
    if kind == "mesh_degenerate":
        mesh = reader.mesh(obj)
        if not mesh["finite"] or mesh["invalid_indices"]:
            return False, {"finite": mesh["finite"], "invalid_indices": mesh["invalid_indices"]}
        metrics = mesh.setdefault("degeneracy", {})
        epsilon = check["epsilon"]
        if epsilon not in metrics:
            reader.spend(len(mesh["areas"]) + len(mesh["lengths"]))
            metrics[epsilon] = (sum(a <= epsilon for a in mesh["areas"]), sum(e <= epsilon for e in mesh["lengths"]))
        faces, edges = metrics[epsilon]
        return faces <= check["max_faces"] and edges <= check["max_edges"], {"degenerate_faces": faces, "zero_length_edges": edges, "scope": "BASE_MESH_LOCAL"}
    if kind == "curve_counts":
        from .validation_curves import evaluate_curve_counts
        return evaluate_curve_counts(reader, obj, check)
    if kind == "mesh_counts":
        mesh = reader.mesh(obj)
        counts = {"vertices": mesh["vertices"], "edges": mesh["edge_count"], "faces": mesh["face_count"]}
        return all(check[key]["minimum"] <= value <= check[key]["maximum"] for key, value in counts.items()), {**counts, "scope": "BASE_MESH"}
    if kind == "mesh_manifold":
        mesh = reader.mesh(obj)
        metrics = reader.topology(mesh)
        clean = mesh["face_count"] > 0 and not any(metrics[k] for k in ("invalid_geometry", "nonmanifold_edges", "nonmanifold_vertices", "wire_edges", "loose_vertices", "missing_edges"))
        wanted = metrics.get("boundary_edges", 0) == 0 if check["expected"] == "CLOSED" else metrics.get("boundary_edges", 0) > 0
        return clean and wanted, {**metrics, "scope": "BASE_MESH"}
    if kind in ("material_assignment", "material_values"):
        materials = reader.materials(obj)
        named = name_of(reader.context)
        matching = [m for m in materials if m and named(m) == check["material"]]
        if not matching:
            return False, {"material_present": False}
        if kind == "material_values":
            values = _material_values(matching[0])
            return all(values_near(values[key], check[key], check.get("tolerance")) for key in values if key in check), values
        if not check["all_faces"]:
            # Assigned means rendered: a slot no face uses leaves the object looking unchanged. Where the base
            # faces do not decide what it renders (material_faces.faces_decide) only the slots can be read.
            from .material_faces import faces_decide, rendered_faces
            try:   # One material index per face, charged to the verification budget like every other read.
                faces = len(obj.data.polygons) if faces_decide(obj) else 0
                if faces > MAX_MESH_ELEMENTS:
                    raise Inconclusive("Base mesh exceeds deterministic verification limits")
                reader.spend(faces)
                rendered = rendered_faces(obj, [index for index, material in enumerate(materials) if material in matching])
            except Inconclusive as error:   # Read no further: what the slots show, and why only that.
                return True, {"material_present": True, "scope": "object_material_slots", "faces_unread": str(error)[:120]}
            if rendered is None:
                return True, {"material_present": True, "scope": "object_material_slots"}
            return rendered > 0, {"material_present": True, "rendered_faces": rendered, "scope": "object_faces"}
        mesh = reader.mesh(obj)
        assignments = reader.material_assignments(obj)
        assigned = sum(count for index, count in assignments.items() if 0 <= index < len(materials) and materials[index] in matching)
        return assigned == mesh["face_count"] and assigned > 0, {"assigned_faces": assigned, "total_faces": mesh["face_count"]}
    if kind == "uv_layer":
        if obj.type != "MESH":
            raise Inconclusive("UV checks support base MESH data only")
        if getattr(obj, "mode", "OBJECT") == "EDIT" or getattr(obj.data, "is_editmode", False):
            raise Inconclusive("Live Edit-mode UV data is not available through the read-only base-mesh reader")
        layers = obj.data.uv_layers
        if len(layers) > MAX_UV_LAYERS:
            raise Inconclusive("UV layer count exceeds verification limits")
        layer = layers.get(check["name"]) if "name" in check else layers.active
        if layer is None:
            return False, {"present": False}
        uv = getattr(layer, "uv", None)
        if uv is None:
            uv = layer.data
        count = len(uv)
        if count > MAX_MESH_ELEMENTS:
            raise Inconclusive("UV corner count exceeds verification limits")
        valid = not check["require_nonempty"] or count > 0
        coverage = count == len(obj.data.loops)
        if check["require_nonempty"] or check["require_finite"]:
            valid = valid and coverage
        if check["require_finite"]:
            reader.spend(count)
            valid = valid and all(finite_values(number_vector(getattr(item, "vector", getattr(item, "uv", ())), 2)) for item in uv)
        return valid, {"present": True, "name": layer.name[:64], "corners_checked": count,
                       "finite_checked": check["require_finite"], "coverage_complete": coverage}
    if kind == "smooth_shading_graph":
        if len(obj.modifiers) > MAX_MODIFIERS:
            raise Inconclusive("Modifier count exceeds verification limits")
        modifier = obj.modifiers.get(check["modifier"])
        if modifier is None or modifier.type != "NODES":
            return False, {"present": False}
        tree = modifier.node_group
        if tree is None or len(tree.nodes) > 32 or len(tree.links) > 64:
            raise Inconclusive("Geometry graph exceeds the bounded smooth-shading evaluator")
        types = {kind: [n for n in tree.nodes if n.bl_idname == kind] for kind in
                 ("NodeGroupInput", "GeometryNodeSetShadeSmooth", "NodeGroupOutput")}
        if len(tree.nodes) != 3 or any(len(nodes) != 1 for nodes in types.values()) or len(tree.links) != 2:
            raise Inconclusive("Only the exact direct input/smooth/output geometry graph is supported")
        first, smooth, last = (types[kind][0] for kind in ("NodeGroupInput", "GeometryNodeSetShadeSmooth", "NodeGroupOutput"))
        # RNA wrappers obtained through NodeLink may have a different Python
        # hash from wrappers obtained through nodes; compare native identities.
        identity = lambda node: int(node.as_pointer()) if hasattr(node, "as_pointer") else id(node)
        # In newer Blender the FACE smooth node displays its closed Geometry
        # sockets as "Mesh". Resolve those typed sockets rather than comparing
        # translated/domain-dependent display labels.
        smooth_input = smooth.inputs.get("Geometry") or smooth.inputs.get("Mesh")
        smooth_output = smooth.outputs.get("Geometry") or smooth.outputs.get("Mesh")
        group_input, group_output = first.outputs.get("Geometry"), last.inputs.get("Geometry")
        if any(socket is None for socket in (smooth_input, smooth_output, group_input, group_output)):
            raise Inconclusive("This Blender build lacks the supported direct geometry sockets")
        edges = {(identity(link.from_node), identity(link.from_socket), identity(link.to_node), identity(link.to_socket)) for link in tree.links}
        expected_edges = {(identity(first), identity(group_input), identity(smooth), identity(smooth_input)),
                          (identity(smooth), identity(smooth_output), identity(last), identity(group_output))}
        selection, shade = smooth.inputs.get("Selection"), smooth.inputs.get("Shade Smooth")
        valid = (edges == expected_edges and smooth.domain == "FACE" and selection is not None and shade is not None
                 and not selection.is_linked and not shade.is_linked and bool(selection.default_value)
                 and bool(shade.default_value) == check["expected"] and modifier.show_viewport and modifier.show_render
                 and not any(getattr(node, "mute", False) for node in tree.nodes)
                 and all(getattr(link, "is_valid", True) for link in tree.links))
        return valid, {"present": True, "shade_smooth": bool(shade.default_value) if shade else None,
                       "links_match": edges == expected_edges, "domain": smooth.domain,
                       "links": [{"from_type": link.from_node.bl_idname, "from_socket": link.from_socket.name[:64],
                                  "to_type": link.to_node.bl_idname, "to_socket": link.to_socket.name[:64]} for link in tree.links],
                       "selection_enabled": bool(selection.default_value) if selection else None,
                       "inputs_unlinked": selection is not None and shade is not None and not selection.is_linked and not shade.is_linked,
                       "viewport_enabled": modifier.show_viewport, "render_enabled": modifier.show_render,
                       "scope": "DIRECT_GRAPH_CONFIGURATION_NOT_EVALUATED_MESH"}
    if kind == "bone":
        from .bone_state import evaluate
        return evaluate(reader, obj, check)
    if kind == "constraint_values":
        constraint = obj.constraints.get(check["name"]) if len(obj.constraints) <= MAX_MODIFIERS else None
        if constraint is None:
            return False, {"present": False}
        from .generic_settings import evaluate
        return evaluate(constraint, check["values"], check.get("tolerance"),
                        lambda reference: resolve_target(reader.context, reference))
    if kind == "copies":  # A batch's copies of this source.
        from .batch_spec import evaluate_copies
        return evaluate_copies(reader, obj, check)
    if kind == "node_graph":  # An object's material or Geometry Nodes tree, or a scene's World (node_graph_check).
        from .node_graph_check import evaluate as evaluate_graph
        return evaluate_graph(obj, check, name_of(reader.context))
    if kind == "particle_values":
        if len(obj.modifiers) > MAX_MODIFIERS:
            raise Inconclusive("Modifier count exceeds verification limits")
        from .action_particles import evaluate_check as evaluate_particles
        return evaluate_particles(obj, check, lambda reference: resolve_target(reader.context, reference))
    if kind == "modifier_values":
        modifier = obj.modifiers.get(check["name"]) if len(obj.modifiers) <= MAX_MODIFIERS else None
        if modifier is None or modifier.type != check["type"]:
            return False, {"present": modifier is not None, "type": getattr(modifier, "type", None)}
        from .generic_settings import evaluate
        return evaluate(modifier, check["values"], check.get("tolerance"),
                        lambda reference: resolve_target(reader.context, reference))
    if kind == "modifier":
        modifiers = obj.modifiers
        if len(modifiers) > MAX_MODIFIERS:
            raise Inconclusive("Modifier count exceeds verification limits")
        modifier = modifiers.get(check["name"])
        if modifier is None:
            return False, {"present": False}
        if modifier.type != check["type"]:
            return False, {"present": True, "type": modifier.type}
        values = {}
        for key, expected in check["values"].items():
            if not hasattr(modifier, key):
                raise Inconclusive("This Blender build does not expose an allowlisted modifier property")
            value = getattr(modifier, key)
            values[key] = list(value) if isinstance(expected, list) else value
        return all(values_near(values[k], expected, check.get("tolerance")) for k, expected in check["values"].items()), {"present": True, "type": modifier.type, "values": values}
    if kind == "parent":
        expected = None if check["expected"] is None else resolve_target(reader.context, check["expected"])
        if check["expected"] is not None and expected is None:
            return False, {"expected_parent_exists": False}
        valid = obj.parent == expected
        if "parent_type" in check:
            valid = valid and obj.parent_type == check["parent_type"]
        if "bone" in check:
            valid = valid and obj.parent_bone == check["bone"]
        return valid, {"parent_name": obj.parent.name[:128] if obj.parent else None,
                       "parent_type": obj.parent_type, "bone": getattr(obj, "parent_bone", "")[:64]}
    if kind == "rig":
        if obj.type != "ARMATURE":
            return False, {"type": obj.type}
        bones = obj.data.bones
        if len(bones) > MAX_BONES:
            raise Inconclusive("Bone count exceeds verification limits")
        reader.spend(len(bones))
        count = len(bones)
        missing = [name for name in check["expected_bones"] if bones.get(name) is None]
        valid = check["minimum_bones"] <= count <= check["maximum_bones"] and not missing
        finite = all(finite_values(number_vector(b.head_local, 3) + number_vector(b.tail_local, 3)) and math.dist(b.head_local, b.tail_local) > 1e-9 for b in bones) if check["require_finite"] else None
        return valid and (finite is not False), {"bone_count": count, "missing_expected_bones": missing, "finite_nondegenerate_bones": finite, "scope": "REST_BONES"}
    if kind == "armature_binding":
        mesh = reader.mesh(obj)
        armature = resolve_target(reader.context, check["armature"])
        if armature is None or armature.type != "ARMATURE":
            return False, {"armature_present": False}
        if len(obj.modifiers) > MAX_MODIFIERS or len(obj.vertex_groups) > MAX_BONES:
            raise Inconclusive("Rig binding metadata exceeds verification limits")
        modifier = obj.modifiers.get(check["modifier"])
        wired = modifier is not None and modifier.type == "ARMATURE" and modifier.object == armature and modifier.use_vertex_groups
        if len(armature.data.bones) > MAX_BONES:
            raise Inconclusive("Bone count exceeds verification limits")
        bone_names = {bone.name for bone in armature.data.bones}
        weighted, invalid, entries = 0, 0, 0
        for vertex in mesh["data"].vertices:
            entries += len(vertex.groups)
            if entries > MAX_KEYFRAMES:
                raise Inconclusive("Explicit rig weight entry count exceeds verification limits")
            total = 0.0
            for group in vertex.groups:
                if not 0 <= group.group < len(obj.vertex_groups):
                    invalid += 1
                    continue
                if obj.vertex_groups[group.group].name in bone_names:
                    weight = float(group.weight)
                    if not math.isfinite(weight) or weight < 0 or weight > 1:
                        invalid += 1
                    else:
                        total += weight
            if total > check["tolerance"]:
                weighted += 1
                if abs(total - 1.0) > check["tolerance"]:
                    invalid += 1
        reader.spend(entries)
        valid = wired and not invalid and (not check["every_vertex_weighted"] or weighted == mesh["vertices"] and weighted > 0)
        return valid, {"modifier_wired": wired, "weighted_vertices": weighted, "vertices": mesh["vertices"],
                       "invalid_or_unnormalized_weights": invalid, "scope": "RIG_WIRING_AND_REST_WEIGHTS_NOT_DEFORMATION"}
    if kind == "animation":
        if check["property"] == "rotation_euler" and getattr(obj, "rotation_mode", "XYZ") in ("QUATERNION", "AXIS_ANGLE"):
            raise Inconclusive("Euler action samples do not prove quaternion/axis-angle animation output")
        curves = reader.curves(obj)
        selected = [c for c in curves if c.data_path == check["property"]]
        if len(selected) != 3 or {c.array_index for c in selected} != {0, 1, 2}:
            return False, {"channel_count": len(selected), "required_channels": 3}
        if any(getattr(c, "modifiers", ()) or getattr(c, "mute", False) or not getattr(c, "is_valid", True) for c in selected):
            raise Inconclusive("Muted, invalid or modifier-driven animation curves are unsupported")
        # require_keyframes: every channel is keyframed (finite keys), so samples between keys read
        # their interpolation. It never demanded a key AT each sample: a recorded check sampled a
        # LINEAR spin between its keys and failed twice on a correct result.
        keyed = all(len(c.keyframe_points) and all(finite_values(number_vector(p.co, 2)) for p in c.keyframe_points)
                    for c in selected)
        values, valid = [], keyed or not check["require_keyframes"]
        for sample in check["samples"]:
            frame = sample["frame"]
            actual = [float(next(c for c in selected if c.array_index == i).evaluate(frame)) for i in range(3)]
            valid = valid and values_near(actual, sample["expected"], check.get("tolerance"))
            values.append({"frame": frame, "actual": actual})
        return valid, {"samples": values, "keyframed": bool(keyed), "scope": "DIRECT_ACTION_CHANNELS_NOT_WORLD_EVALUATION"}
    raise Inconclusive("Unsupported predicate")
