# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where Media Studio's items land: one place for every drop target and every menu action (``media_drop``).

An item is a Media Studio record and its asset file in Angelo's Asset Browser library (``studio_assets``): a model's
.blend holds an Object or a Collection, an image's one Material per picture. Models come in as the library's import
method says (Append, or Link), standing at the given point at the GLB's size; several stand side by side across the
view. Images become a reference image in the scene, a material on an object, a picture in the Image Editor, a chat
attachment or a Media Studio reference (node editors are space_node's own drop: ``flow_drop``). Every picture that lands in the person's file is packed there (``studio_assets_landing``), as a drop
from the Asset Browser packs it.
"""

from pathlib import Path

import bpy
from mathutils import Vector

from . import studio_assets
from .ui_text import n_

REFERENCE_SIZE = 5.0   # Blender's own image reference size (Add > Image > Reference).


class Item:
    """A Media Studio item as its asset file holds it."""

    def __init__(self, kind, identity, blend, name="", datablock="", picture=None, files=(), thumbnail=""):
        self.kind, self.id, self.blend, self.name, self.datablock = kind, identity, blend, name, datablock
        self.picture, self.files, self.thumbnail = picture, list(files), thumbnail


def directories(library_name=""):
    """``(Studio library folder, asset folder)``: the drop's own Asset Browser library when it names one, else the
    current Media Studio library's."""
    from .studio_assets_host import _libraries, directories as found
    library_dir, directory = found()
    entry = next((lib for lib in _libraries() if lib.name == library_name), None) if library_name else None
    if entry is not None and Path(entry.path).name == directory.name:
        directory = Path(entry.path)
        library_dir = directory.parent
    return library_dir, directory


def _material_names(entry):
    """The materials ``studio_assets_convert.image_assets`` writes for an image item, one per picture, in order."""
    return [entry["name"] if index == 0 else f"{entry['name']} {index + 1}" for index in range(len(entry["files"]))]


def _entry(identity, library_name=""):
    from .studio_state import StudioLibrary
    library_dir, _directory = directories(library_name)
    studio = StudioLibrary(library_dir)
    record = studio.assets.get(identity)
    return studio_assets.item(library_dir, record, studio.folders) if record else None


def from_identifiers(text, library_name=""):
    """Items from the relative asset identifiers a drop names (``models/<id>.blend/Object/<name>``), one per line, in
    the drop's library."""
    _library_dir, directory = directories(library_name)
    items = []
    for line in text.splitlines():
        folder, _, rest = line.partition("/")
        identity, separator, rest = rest.partition(".blend/")
        datablock, _, name = rest.partition("/")
        if not separator or folder not in {"models", "images"}:
            continue
        entry = _entry(identity, library_name)
        kind = "3D" if folder == "models" else "IMAGE"
        picture = None
        if kind == "IMAGE" and entry:
            names = _material_names(entry)
            picture = entry["files"][names.index(name)] if name in names else entry["files"][0]
        items.append(Item(kind, identity, str(directory / folder / f"{identity}.blend"), name, datablock, picture,
                          entry["files"] if entry else (), entry["thumbnail"] if entry else ""))
    return items


def from_ids(identities):
    """Items from Media Studio ids (a menu action, an agent): every picture of an image item, a model whole."""
    _library_dir, directory = directories()
    items = []
    for identity in identities:
        entry = _entry(identity)
        if entry is None:
            continue
        if entry["kind"] == "IMAGE":
            for name, path in zip(_material_names(entry), entry["files"]):
                items.append(Item("IMAGE", identity, str(directory / "images" / f"{identity}.blend"), name, "Material",
                                  path, entry["files"]))
        else:
            items.append(Item("3D", identity, str(directory / "models" / f"{identity}.blend"), entry["name"], "",
                              None, entry["files"], entry["thumbnail"]))
    return items


def _import_method():
    from . import studio_assets_host
    _library_dir, directory = directories()
    entry = studio_assets.find_library(studio_assets_host._libraries(), directory)
    return getattr(entry, "import_method", 'APPEND') if entry else 'APPEND'


def _target_collection(context, name="", into=""):
    """The collection named, a scene's own (``into == "SCENE"``, the scene named, else the current one), else the
    active collection."""
    if into == "SCENE":
        scene = bpy.data.scenes.get(name) if name else None
        return (scene or context.scene).collection
    collection = bpy.data.collections.get(name) if name else None
    if collection is not None and collection.library is None:
        return collection
    return context.collection or context.scene.collection


def _load_model(item, link):
    """The model's datablock from its asset file: (object or None, collection or None)."""
    if not Path(item.blend).is_file():
        raise ValueError(n_("This Media Studio item is not in the Asset Browser library yet"))
    with bpy.data.libraries.load(item.blend, link=link, assets_only=True) as (source, target):
        if item.datablock == "Collection" or (not item.datablock and source.collections and not source.objects):
            target.collections = list(source.collections[:1])
        else:
            target.objects = list(source.objects[:1])
    objects = [o for o in getattr(target, "objects", []) or [] if o is not None]
    collections = [c for c in getattr(target, "collections", []) or [] if c is not None]
    return (objects[0] if objects else None), (collections[0] if collections else None)


def _width(objects):
    corners = [obj.matrix_world @ Vector(c) for obj in objects if obj.type == 'MESH' for c in obj.bound_box]
    if not corners:
        return 1.0
    return max(max(c.x for c in corners) - min(c.x for c in corners),
               max(c.y for c in corners) - min(c.y for c in corners), 0.1)


def check_files(items):
    """Refuse before anything changes when an item's file is gone (a drop is all or nothing)."""
    for item in items:
        if not Path(item.blend).is_file() or (item.kind == "IMAGE" and not (item.picture and Path(item.picture).is_file())):
            raise ValueError(n_("This Media Studio item is not in the Asset Browser library yet"))


def add_models(context, items, point, across=Vector((1.0, 0.0, 0.0)), collection_name="", into="", select=True):
    """Each model standing at ``point``; several side by side along ``across``. Returns the objects added.

    A linked object stays where Blender links it: a linked object's place is the library's, so a move would not be
    saved (Blender's own link drop does the same); a linked collection is placed through an instance."""
    link = _import_method() == 'LINK'
    target = _target_collection(context, collection_name, into)
    placed = []   # (moved objects, width)
    for item in items:
        obj, collection = _load_model(item, link)
        if obj is not None:
            target.objects.link(obj)
            placed.append(([obj], obj, None))
        elif collection is not None:
            if link:   # A linked collection is placed through an instance, as Blender's own link drop does.
                holder = bpy.data.objects.new(collection.name, None)
                holder.instance_type, holder.instance_collection = 'COLLECTION', collection
                target.objects.link(holder)
                placed.append(([holder], holder, None))
            else:
                target.children.link(collection)
                tops = [o for o in collection.all_objects if o.parent is None]
                placed.append((tops, None, Vector(collection.instance_offset)))
    context.view_layer.update()
    widths = [_width(_all_objects(objects)) for objects, _obj, _base in placed]
    gap = 0.1 * max(widths, default=1.0)
    total = sum(widths) + gap * max(0, len(widths) - 1)
    offset = -total / 2
    added = []
    for (objects, obj, base), width in zip(placed, widths):
        spot = point + across * (offset + width / 2)
        offset += width + gap
        if obj is not None:
            if obj.library is None:
                obj.location = spot
        else:   # An appended collection keeps its objects' places: move them so its base stands at the spot.
            for top in objects:
                top.location += spot - base
        added.extend(objects)
    if select:
        _select(context, added)
    return added


def _all_objects(objects):
    result = []
    for obj in objects:
        result.append(obj)
        if obj.instance_type == 'COLLECTION' and obj.instance_collection:
            result.extend(obj.instance_collection.all_objects)
    return result


def _select(context, objects):
    if not objects:
        return   # Nothing landed: the person's selection stays.
    for obj in context.view_layer.objects:
        if obj.select_get():
            obj.select_set(False)
    for obj in objects:
        if obj.name in context.view_layer.objects:
            obj.select_set(True)
    if objects and objects[0].name in context.view_layer.objects:
        context.view_layer.objects.active = objects[0]


def _picture(item, path=None):
    """Studio's picture as an image in the person's file, packed: one copy per picture, however often it lands (a
    reference, a material, the Image Editor), through ``studio_assets_landing.land`` as a material's picture lands.
    Loaded fresh, never one of Media Studio's grid previews (unpacked copies of the same files, freed when they
    scroll away)."""
    from .studio_assets_landing import STUDIO_IMAGE, land
    image = bpy.data.images.load(str(Path(path or item.picture).resolve()), check_existing=False)
    image[STUDIO_IMAGE] = item.id
    return land(image)


def add_references(context, items, point, rotation=None, across=Vector((1.0, 0.0, 0.0)), collection_name="",
                   select=True):
    """Images as reference images (Blender's Add > Image > Reference) at ``point``, side by side."""
    target = _target_collection(context, collection_name)
    added = []
    pictures = [item for item in items if item.picture]
    step = REFERENCE_SIZE * 1.1
    for index, item in enumerate(pictures):
        image = _picture(item)
        empty = bpy.data.objects.new(item.name or image.name, None)
        empty.empty_display_type, empty.empty_display_size, empty.data = 'IMAGE', REFERENCE_SIZE, image
        empty.location = point + across * (index - (len(pictures) - 1) / 2) * step
        if rotation is not None:
            empty.rotation_euler = rotation
        target.objects.link(empty)
        added.append(empty)
    if select:
        _select(context, added)
    return added


def materials(items):
    """The images' materials, appended (their pictures packed)."""
    from .studio_assets_landing import pack_landed_images
    found = []
    for item in items:
        if not item.picture or not Path(item.blend).is_file():
            continue
        with bpy.data.libraries.load(item.blend, link=False, assets_only=True) as (source, target):
            target.materials = [name for name in source.materials if name == item.name] or list(source.materials[:1])
        found.extend(m for m in target.materials if m is not None)
    pack_landed_images()
    return found


def apply_materials(objects, items):
    """The first image's material on each object's active slot (one made if it has none); the rest as more slots."""
    found = materials(items)
    if not found:
        return 0
    applied = 0
    for obj in objects:
        if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'GPENCIL', 'GREASEPENCIL', 'CURVES',
                            'POINTCLOUD', 'VOLUME'} or obj.library is not None or obj.data is None:
            continue
        if not obj.material_slots:
            obj.data.materials.append(found[0])
        else:
            obj.material_slots[obj.active_material_index].material = found[0]
        for extra in found[1:]:
            obj.data.materials.append(extra)
        applied += 1
    return applied


def apply_picture_materials(objects, items):
    """With the library off: each image's material made from its picture, as the library's writer makes it."""
    found = []
    for item in items:
        if not item.picture:
            continue
        material = bpy.data.materials.new(item.name or Path(item.picture).stem)
        if material.node_tree is None:
            material.use_nodes = True
        tree = material.node_tree
        shader = next(n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED')
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = _picture(item)
        texture.location = (shader.location.x - 340, shader.location.y)
        tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
        tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
        from . import material_display_sync as display   # Solid view: the picture's mean colour, as it renders.
        display.sync(material, display.CREATED, images=display.LOAD)
        found.append(material)
    applied = 0
    for obj in objects:
        if obj.type != 'MESH' or obj.library is not None:
            continue
        if not obj.material_slots:
            obj.data.materials.append(found[0])
        else:
            obj.material_slots[obj.active_material_index].material = found[0]
        for extra in found[1:]:
            obj.data.materials.append(extra)
        applied += 1
    return applied if found else 0


def import_glbs(context, items, point):
    """With the library off: Blender's glTF import of each model, shaped as the library's writer shapes it (its base
    standing at ``point``). Returns the objects added."""
    from . import studio_assets_mesh
    added = []
    for item in items:
        glb = next((f for f in item.files if f.endswith(".glb")), None)
        if glb is None:
            continue
        objects = studio_assets_mesh.import_glb(glb).objects
        one, base, _low, _high = studio_assets_mesh.shape(objects)
        if one is not None:
            one.location = point
        else:
            for top in (o for o in objects if o.parent is None):
                top.location += point - base
        added.extend(objects)
    _select(context, added)
    return added


def view3d_point(context, event):
    """Where a drop lands in a 3D view: the surface under the pointer, else the ground plane, else the 3D cursor;
    and the view's right axis on the ground, for a row."""
    from bpy_extras import view3d_utils
    region, rv3d = context.region, context.region_data
    cursor = context.scene.cursor.location.copy()
    if event is None or rv3d is None or region is None:
        return cursor, Vector((1.0, 0.0, 0.0)), None
    coord = (event.mouse_region_x, event.mouse_region_y)
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    hit, location, *_rest = context.scene.ray_cast(context.evaluated_depsgraph_get(), origin, direction)
    if not hit and abs(direction.z) > 1e-6 and -origin.z / direction.z > 0:
        location, hit = origin + direction * (-origin.z / direction.z), True
    across = rv3d.view_rotation @ Vector((1.0, 0.0, 0.0))
    across.z = 0.0
    across = across.normalized() if across.length > 1e-6 else Vector((1.0, 0.0, 0.0))
    return (location if hit else cursor), across, rv3d.view_rotation.to_euler()


def open_images(context, items):
    """The pictures in the Image Editor dropped on, the last one shown; packed."""
    images = [_picture(item) for item in items if item.picture]
    if images and context.space_data and context.space_data.type == 'IMAGE_EDITOR':
        context.space_data.image = images[-1]
    return images


def attach_to_chat(items):
    """The pictures, or a model's GLB, as attachments of the chat's next message."""
    from . import ui
    controller = ui.get_controller()
    if controller.busy:
        raise ValueError(n_("Wait for this turn to finish before dropping attachments"))
    paths = [item.picture if item.kind == "IMAGE" else (item.files[0] if item.files else None) for item in items]
    paths = [p for p in dict.fromkeys(paths) if p]
    controller.attachments.extend(controller.references.import_images(paths, controller.attachments))
    controller.version += 1
    return len(paths)


def add_studio_references(area, items, event=None):
    """Images as references of the Media Studio editor dropped on. Its own grid's drag adds them over the Create
    column (the references' place); dropped back anywhere else on it (a click that moved a little) it adds nothing, as
    an asset dropped back on the Asset Browser. A drag from anywhere else adds them wherever it lands."""
    from . import studio, studio_drag
    from .studio import attach
    from .studio_input import state_for
    if studio_drag.came_back(area, dict.fromkeys(item.id for item in items)):
        create = (studio.view(area).get("geometry") or {}).get("create", (0, 0, 0, 0))
        inside = event is not None and create[2] > 0 and (create[0] <= event.mouse_region_x < create[0] + create[2]
                                                           and create[1] <= event.mouse_region_y < create[1] + create[3])
        if not inside:
            return 0
    state = state_for(area.as_pointer())
    pictures = [item.picture for item in items if item.picture]
    attach(state, pictures)
    return len(pictures)
