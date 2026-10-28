# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The plain description of a scene that ``web_compat`` checks: what a web export would carry. Main thread, read-only.

Only what the export would include is described: objects visible in the view layer (selected ones when
``selection_only``), the materials on them, the images those materials use, the lights, the world and the scene's
render effects. Nothing is written to Blender data.
"""

import bpy

PRINCIPLED = "ShaderNodeBsdfPrincipled"


def exported_objects(scene, view_layer, selection_only=False):
    """The objects a web export of ``scene`` includes, in a stable order."""
    objects = [obj for obj in view_layer.objects if obj.visible_get(view_layer=view_layer)
               and (not selection_only or obj.select_get(view_layer=view_layer))]
    return sorted(objects, key=lambda obj: obj.name)


def _output(tree):
    """The active material output of ``tree`` (the one Blender renders), or None."""
    outputs = [node for node in tree.nodes if node.bl_idname == "ShaderNodeOutputMaterial"]
    for node in outputs:
        if getattr(node, "is_active_output", False):
            return node
    return outputs[0] if outputs else None


def _upstream(socket, seen):
    """Every node feeding ``socket``, through groups' outer links (not into them), once each."""
    stack = [link.from_node for link in socket.links if link.is_valid and not link.is_muted]
    while stack:
        node = stack.pop()
        if node.as_pointer() in seen:
            continue
        seen[node.as_pointer()] = node
        if node.mute:
            continue
        for item in node.inputs:
            stack.extend(link.from_node for link in item.links if link.is_valid and not link.is_muted)


def _label(node):
    return node.label or node.bl_label or node.name


def _web_shader(material):
    from . import web_shader
    settings = web_shader.settings_of(material)
    if settings is None:
        return None
    problem = web_shader.file_problem(settings, material) if settings.kind == "FILE" else None
    return {"kind": settings.kind, "problem": problem}


def describe_material(material):
    facts = {"name": material.name, "uses_nodes": bool(material.node_tree), "nodes": [], "surface": False,
             "web_shader": _web_shader(material)}
    tree = material.node_tree
    output = _output(tree) if tree is not None else None
    if output is None:
        return facts
    seen = {}
    surface = output.inputs.get("Surface")
    if surface is not None:
        _upstream(surface, seen)
        facts["surface"] = bool(surface.is_linked)
    nodes = list(seen.values())
    facts["nodes"] = [{"type": node.bl_idname, "label": _label(node),
                       "group": node.node_tree.name if node.bl_idname == "ShaderNodeGroup" and node.node_tree else None}
                      for node in nodes]
    for name in ("Displacement", "Volume"):
        socket = output.inputs.get(name)
        facts[name.lower()] = bool(socket is not None and socket.is_linked)
    for node in nodes:
        if node.bl_idname == PRINCIPLED:
            weight = node.inputs.get("Subsurface Weight")
            if weight is not None and (weight.is_linked or weight.default_value > 0.0):
                facts["subsurface"] = True
    return facts


def _images(material):
    tree = material.node_tree
    if tree is None:
        return ()
    return [node.image for node in tree.nodes if node.bl_idname == "ShaderNodeTexImage" and node.image is not None]


def describe_image(image):
    width, height = (int(value) for value in image.size[:2])
    missing = False
    if image.source == "FILE" and not image.packed_file:
        path = bpy.path.abspath(image.filepath, library=image.library)
        import os
        missing = not path or not os.path.isfile(path)
    return {"name": image.name, "source": image.source, "width": width, "height": height, "missing": missing}


def describe_object(obj):
    particles = [{"name": system.name, "kind": system.settings.type} for system in getattr(obj, "particle_systems", ())
                 if system.settings.render_type != "NONE"]
    return {"name": obj.name, "type": obj.type, "modifiers": [modifier.type for modifier in obj.modifiers
                                                             if modifier.show_viewport],
            "particles": particles}


def _world(scene):
    world = scene.world
    if world is None:
        return None
    lit = False
    if world.node_tree is not None:
        lit = any(node.bl_idname in {"ShaderNodeTexEnvironment", "ShaderNodeTexSky"} for node in world.node_tree.nodes)
    return {"name": world.name, "lit": lit or tuple(world.color[:3]) != (0.0, 0.0, 0.0)}


# Compositor nodes that only pass the render through (Blender 5's group output included): no effect of their own.
PASSTHROUGH = frozenset({"CompositorNodeRLayers", "CompositorNodeComposite", "CompositorNodeViewer", "NodeGroupInput",
                         "NodeGroupOutput", "NodeReroute", "NodeFrame"})


def _effects(scene, objects):
    tree = getattr(scene, "compositing_node_group", None) or getattr(scene, "node_tree", None)
    use_compositor = bool(tree is not None and scene.render.use_compositing and any(
        node.bl_idname not in PASSTHROUGH and not node.mute for node in tree.nodes))
    return {"motion_blur": bool(scene.render.use_motion_blur),
            "depth_of_field": sorted(obj.name for obj in objects if obj.type == "CAMERA" and obj.data.dof.use_dof),
            "compositor": use_compositor}


def gather(scene, view_layer, selection_only=False, depsgraph=None):
    """``web_compat``'s facts for a web export of ``scene`` as ``view_layer`` shows it."""
    objects = exported_objects(scene, view_layer, selection_only)
    materials, images, triangles = {}, {}, 0
    for obj in objects:
        for slot in obj.material_slots:
            if slot.material is not None:
                materials.setdefault(slot.material.name, slot.material)
        if depsgraph is not None and obj.type in {"MESH", "CURVE", "SURFACE", "FONT"}:
            evaluated = obj.evaluated_get(depsgraph)
            data = evaluated.data if obj.type == "MESH" else None
            if data is not None:
                triangles += len(data.loop_triangles)
    for material in materials.values():
        for image in _images(material):
            images.setdefault(image.name, image)
    return {
        "objects": [describe_object(obj) for obj in objects],
        "materials": [describe_material(materials[name]) for name in sorted(materials)],
        "images": [describe_image(images[name]) for name in sorted(images)],
        "lights": [{"name": obj.name, "type": obj.data.type, "soft": obj.data.type != "SUN"
                    and getattr(obj.data, "shadow_soft_size", 0.0) > 0.05 and obj.data.use_shadow}
                   for obj in objects if obj.type == "LIGHT"],
        "world": _world(scene),
        "effects": _effects(scene, objects),
        "triangles": triangles,
    }
