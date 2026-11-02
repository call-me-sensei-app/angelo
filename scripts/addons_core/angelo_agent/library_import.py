# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread import of a checked Angelo Library item into the scene (doc/angelo/library.rst, "Import").

The files passed ``library_fetch`` (size, checksum) and ``library_files`` (format and bounds). Here, with Blender's
own importers and nodes:

* **Model** (GLB or multi-file glTF): imported into a new collection "Library · <title>" under the active one, its
  images packed, then shaped like a Media Studio model (``studio_assets_mesh.shape``) and stood on ``point`` (the
  surface under the pointer, else the 3D cursor). A model listed with PNG maps (ToonLab's rocks) gets them as its
  material.
* **Texture set**: one Principled material from the maps (base colour in sRGB, the rest as data; ``arm`` split into
  roughness and metallic when they have no map of their own; OpenGL normals through a Normal Map node; displacement
  as bump), a Mapping node first so the tiling stays editable, put on each target object's active slot.
* **HDRI**: a new World with an Environment Texture (and a Mapping node for rotation), made the scene's world.
* **Sprite** (VFX): an upright plane at the sprite's aspect with the picture as colour and alpha.

As with the team library, what the importer created is scrubbed (``team_library_import._scrub``: unpacked images,
Text datablocks, scripted drivers), the scene's frame rate, range and units are restored, a failure removes
everything it made, and the whole import is one Undo step. Everything made carries ``angelo_library`` (item id,
title, source, licence, attribution): ``credits`` lists it for a file's attribution.

bpy is imported by this module; it runs only on the main thread.
"""

import bpy
from mathutils import Vector

from . import team_library_import as base
from .team_library_files import ImportFailed

PROPERTY = "angelo_library"
DATA_ROLES = ("roughness", "metallic", "ao", "arm", "displacement", "opacity", "normal_gl")
BUMP_DISTANCE = 0.02


def provenance(item):
    return {"item": item["id"], "title": item["title"][:120], "source": item["source"], "license": item["license"],
            "attribution": item["attribution"][:400], "attribution_required": bool(item["attribution_required"]),
            "source_url": item.get("source_url") or ""}


def _tag(ids, item):
    record = provenance(item)
    for datablock in ids:
        try:
            datablock[PROPERTY] = record
        except (TypeError, AttributeError, ReferenceError):
            pass


def _image(path, name, data):
    image = bpy.data.images.load(str(path), check_existing=False)
    image.name = name[:60]
    if data:
        image.colorspace_settings.name = 'Non-Color'
    image.pack()
    return image


def _nodes(owner):
    if owner.node_tree is None:
        owner.use_nodes = True
    return owner.node_tree


def material_from_maps(item, maps):
    """A Principled material from ``maps`` (``{role: Path}``); its images are packed."""
    material = bpy.data.materials.new(item["title"][:60])
    tree = _nodes(material)
    shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    output = next(n for n in tree.nodes if n.type == 'OUTPUT_MATERIAL')
    coordinates = tree.nodes.new("ShaderNodeTexCoord")
    mapping = tree.nodes.new("ShaderNodeMapping")
    coordinates.location, mapping.location = (shader.location.x - 980, shader.location.y), (shader.location.x - 800, shader.location.y)
    tree.links.new(coordinates.outputs["UV"], mapping.inputs["Vector"])
    textures = {}
    for row, role in enumerate(sorted(maps, key=lambda r: (r != "base_color", r))):
        node = tree.nodes.new("ShaderNodeTexImage")
        node.image = _image(maps[role], "%s %s" % (item["title"][:40], role), role in DATA_ROLES)
        node.location = (shader.location.x - 560, shader.location.y - 280 * row)
        tree.links.new(mapping.outputs["Vector"], node.inputs["Vector"])
        textures[role] = node
    link = tree.links.new
    link(textures["base_color"].outputs["Color"], shader.inputs["Base Color"])
    for role, socket in (("roughness", "Roughness"), ("metallic", "Metallic"), ("opacity", "Alpha")):
        if role in textures:
            link(textures[role].outputs["Color"], shader.inputs[socket])
    if "arm" in textures and not ("roughness" in textures and "metallic" in textures):
        split = tree.nodes.new("ShaderNodeSeparateColor")
        split.location = (shader.location.x - 260, shader.location.y - 120)
        link(textures["arm"].outputs["Color"], split.inputs["Color"])
        if "roughness" not in textures:
            link(split.outputs[1], shader.inputs["Roughness"])
        if "metallic" not in textures:
            link(split.outputs[2], shader.inputs["Metallic"])
    if "emission" in textures:
        link(textures["emission"].outputs["Color"], shader.inputs["Emission Color"])
        shader.inputs["Emission Strength"].default_value = 1.0
    if "normal_gl" in textures:
        normal = tree.nodes.new("ShaderNodeNormalMap")
        normal.location = (shader.location.x - 240, shader.location.y - 420)
        link(textures["normal_gl"].outputs["Color"], normal.inputs["Color"])
        link(normal.outputs["Normal"], shader.inputs["Normal"])
    if "displacement" in textures:
        displacement = tree.nodes.new("ShaderNodeDisplacement")
        displacement.location = (output.location.x - 200, output.location.y - 260)
        displacement.inputs["Scale"].default_value = BUMP_DISTANCE
        link(textures["displacement"].outputs["Color"], displacement.inputs["Height"])
        link(displacement.outputs["Displacement"], output.inputs["Displacement"])
    from . import material_display_sync as display   # Solid view: the map's mean colour, as it renders.
    display.sync(material, display.CREATED, images=display.LOAD)
    return material


def assign(objects, material):
    """``material`` on each object's active slot (one made when it has none); returns how many took it."""
    applied = 0
    for obj in objects:
        if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'CURVES'} or obj.library is not None \
                or obj.data is None or getattr(obj.data, "library", None) is not None:
            continue
        if not obj.material_slots:
            obj.data.materials.append(material)
        else:
            obj.material_slots[obj.active_material_index].material = material
        applied += 1
    return applied


def world_from_hdri(item, path):
    world = bpy.data.worlds.new("Library · " + item["title"][:50])
    tree = _nodes(world)
    background = next((n for n in tree.nodes if n.type == 'BACKGROUND'), None) or tree.nodes.new("ShaderNodeBackground")
    output = next((n for n in tree.nodes if n.type == 'OUTPUT_WORLD'), None) or tree.nodes.new("ShaderNodeOutputWorld")
    output.location = (background.location.x + 260, background.location.y)
    coordinates, mapping = tree.nodes.new("ShaderNodeTexCoord"), tree.nodes.new("ShaderNodeMapping")
    environment = tree.nodes.new("ShaderNodeTexEnvironment")
    environment.image = _image(path, item["title"][:50], False)
    coordinates.location, mapping.location = (background.location.x - 760, background.location.y), (background.location.x - 580, background.location.y)
    environment.location = (background.location.x - 320, background.location.y)
    tree.links.new(coordinates.outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], environment.inputs["Vector"])
    tree.links.new(environment.outputs["Color"], background.inputs["Color"])
    tree.links.new(background.outputs["Background"], output.inputs["Surface"])
    return world


def sprite_plane(item, path, collection, point):
    image = _image(path, item["title"][:50], False)
    width, height = image.size
    aspect = width / height if height else 1.0
    mesh = bpy.data.meshes.new(item["title"][:60])
    half_w = 0.5 * aspect if aspect <= 1 else 0.5
    half_h = 0.5 if aspect <= 1 else 0.5 / aspect
    mesh.from_pydata([(-half_w, 0, -half_h), (half_w, 0, -half_h), (half_w, 0, half_h), (-half_w, 0, half_h)], [],
                     [(0, 1, 2, 3)])
    layer = mesh.uv_layers.new()
    for loop, uv in zip(layer.data, ((0, 0), (1, 0), (1, 1), (0, 1))):
        loop.uv = uv
    material = bpy.data.materials.new(item["title"][:60])
    tree = _nodes(material)
    shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.location = (shader.location.x - 320, shader.location.y)
    tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = 'BLENDED'
    mesh.materials.append(material)
    obj = bpy.data.objects.new(item["title"][:60], mesh)
    obj.location = point + Vector((0.0, 0.0, half_h))   # Upright, its lower edge on the point.
    collection.objects.link(obj)
    return obj


def _stand(objects, point):
    """Stand the model's base centre on ``point`` (one object is baked to identity first)."""
    from . import studio_assets_mesh
    meshes = [o for o in objects if o.type == 'MESH' and o.data.vertices]
    if not meshes:
        return
    one, low_centre, _low, _high = studio_assets_mesh.shape(objects)
    if one is not None:
        one.location = point
    else:
        for top in (o for o in objects if o.parent is None):
            top.location += point - low_centre


def import_item(context, item, facts, *, point=None, targets=(), set_view=None):
    """Import a checked item; returns the read-back. ``point``: where a model or sprite stands (default: the 3D
    cursor). ``targets``: objects a texture set goes on. ``set_view``: a 3D view whose shading should show a new
    world (a drop in the interface); the agent never changes the person's view."""
    scene, layer = context.scene, context.view_layer
    if context.mode != 'OBJECT':
        raise base.NotImported("Leave the active editing or sculpt mode before importing.")
    kind = item["kind"]
    point = Vector(point) if point is not None else scene.cursor.location.copy()
    if kind == "texture" and not [o for o in targets if o.type == 'MESH']:
        raise base.NotImported("Drop a texture set on an object, or select the objects to give it.")
    layer.update()
    before, state = base._ids(), base._scene_state(scene)
    previous_active = layer.active_layer_collection
    collection, made, keep = None, [], []
    try:
        if kind in ("model", "vfx"):
            collection = bpy.data.collections.new("Library · " + item["title"][:50])
            previous_active.collection.children.link(collection)
            layer.active_layer_collection = next(c for c in previous_active.children if c.collection == collection)
        if kind == "model":
            from .gltf_morph import import_options
            result = bpy.ops.import_scene.gltf(filepath=str(facts["model"]), import_pack_images=True,
                                               import_scene_extras=False, **import_options(str(facts["model"])))
            if result != {'FINISHED'}:
                raise RuntimeError("Blender didn't finish importing this model.")
            objects = list(collection.all_objects)
            if not objects:
                raise RuntimeError("The model contained no scene objects.")
            if facts.get("maps") and "base_color" in facts["maps"]:
                material = material_from_maps(item, facts["maps"])
                for obj in objects:
                    if obj.type == 'MESH':
                        obj.data.materials.clear()
                        obj.data.materials.append(material)
            _stand(objects, point)
            made = objects
        elif kind == "vfx":
            made = [sprite_plane(item, facts["image"], collection, point)]
        elif kind == "texture":
            material = material_from_maps(item, facts["maps"])
            assign([o for o in targets if o.type == 'MESH'], material)
            made = [material]
        else:
            world = world_from_hdri(item, facts["hdri"])
            made = [world]
        created = base._new_ids(before)
        keep = [i for i in created["images"] if i.packed_file is not None]
        removed = base._scrub(created, keep)
        restored = base._restore_scene(scene, state)
        if kind == "hdri":
            scene.world = made[0]
            if set_view is not None:
                _show_world(set_view)
        _tag([d for name in ("objects", "materials", "worlds", "images", "meshes", "collections")
              for d in base._new_ids(before)[name]], item)
    except Exception as error:
        layer.active_layer_collection = previous_active
        base._rollback(base._new_ids(before))
        base._restore_scene(scene, state)
        unchanged = not any(base._new_ids(before).values()) and base._scene_state(scene) == state
        raise ImportFailed(base._failure_text(error, unchanged), scene_unchanged=unchanged) from error
    layer.active_layer_collection = previous_active
    layer.update()
    bpy.ops.ed.undo_push(message="Add “%s” from Angelo Library" % item["title"][:60])
    return _readback(item, kind, made, collection, removed, restored)


def _show_world(space):
    """A drop of an HDRI shows it: Solid becomes Material Preview, and either preview uses the scene's world."""
    shading = space.shading
    if shading.type == 'SOLID' or shading.type == 'WIREFRAME':
        shading.type = 'MATERIAL'
    if shading.type == 'MATERIAL':
        shading.use_scene_world = True
    elif shading.type == 'RENDERED':
        shading.use_scene_world_render = True


def _readback(item, kind, made, collection, removed, restored):
    result = {"success": True, "item_id": item["id"], "kind": kind, "title": item["title"],
              "license": item["license"], "blender_undoable": True}
    if item["attribution_required"]:
        result["attribution"] = item["attribution"]
    if kind in ("model", "vfx"):
        from .scene_queries import object_handle
        result["collection"] = collection.name
        result["objects"] = [{"name": o.name, "type": o.type, "handle": object_handle(o),
                              "dimensions": [round(v, 4) for v in o.dimensions]} for o in made[:base.MAX_LISTED]]
        result["object_count"] = len(made)
    elif kind == "texture":
        result["material"] = made[0].name
    else:
        result["world"] = made[0].name
    if any(removed.values()):
        result["removed_for_safety"] = {k: v for k, v in removed.items() if v}
    if restored:
        result["scene_settings_kept"] = restored
    return result


def credits():
    """Every library item this file uses: ``[{item, title, source, license, attribution, attribution_required}]``."""
    found = {}
    for collection in (bpy.data.objects, bpy.data.materials, bpy.data.worlds):
        for datablock in collection:
            record = datablock.get(PROPERTY)
            if record is not None and hasattr(record, "to_dict"):
                record = record.to_dict()
            if isinstance(record, dict) and isinstance(record.get("item"), str):
                found.setdefault(record["item"], record)
    return sorted(found.values(), key=lambda r: (not r.get("attribution_required"), str(r.get("title", "")).lower()))


def credits_text(records):
    lines = ["Angelo Library, powered by ToonLab: credits"]
    for record in records:
        lines.append("- %s (%s, %s)%s" % (record.get("title"), record.get("source"), record.get("license"),
                                          ": " + record["attribution"] if record.get("attribution") else ""))
    return "\n".join(lines)
