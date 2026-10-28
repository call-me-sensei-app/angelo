# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread import of a checked team-library file into the bound scene or Media Studio.

The file has already passed ``team_library_files.inspect`` in a private
temporary folder that holds nothing else. Here, on Blender's main thread:

* **USD** layers are opened with ``pxr`` first (``check_usd``): every asset path
  must be relative and stay inside the file (a USDZ's own entries); a plain
  USD file may not compose other layers.
* **Import** with Blender's own importers into a new collection under the
  scene's active one, with the options that keep the file self-contained: GLB
  packs its images and never writes scene extras, FBX never searches folders for
  textures, USD packs textures and never sets the frame range, scene units or
  world. The scene's frame rate, frame range, units and world are restored
  afterwards regardless (the FBX importer sets the frame rate from the file).
* **Scrub** what the import created: images that aren't packed (a path to a
  file outside the download, or a missing placeholder) are removed, Text
  datablocks are removed, and scripted drivers are removed from every new
  datablock. Blender's script auto-run setting is never touched; nothing here
  executes code from the file.
* **Undo and read-back.** One Undo step ("Import … from team library"), new
  objects tagged ``angelo_team_item`` (their library item, for provenance when
  they are saved back), and a read-back of what was added. A failed import
  removes everything it created, then checks that nothing it created remains
  and that the scene reads as before; only then does it report that nothing
  was kept (``ImportFailed.scene_unchanged``).

bpy is imported by this module; it runs only on the main thread.
"""

import math
import posixpath
import re
import zipfile
from pathlib import Path

import bpy

from . import live_windows
from .gltf_morph import import_options
from .object_bounds import InstanceFacts, states_own_box
from .team_library_files import ImportFailed, NotImported, Refused
from .team_library_spec import ITEM_PROPERTY

MAX_LISTED = 50
_COLLECTIONS = ("objects", "meshes", "materials", "images", "textures", "node_groups", "actions", "armatures",
                "cameras", "lights", "curves", "shape_keys", "worlds", "collections", "texts", "grease_pencils",
                "lattices", "metaballs", "volumes", "pointclouds", "hair_curves", "speakers", "lightprobes")
_SCENE_SETTINGS = (("render", "fps"), ("render", "fps_base"), ("unit_settings", "system"),
                   ("unit_settings", "scale_length"), ("unit_settings", "length_unit"))


def _ids():
    return {name: {item.as_pointer(): item for item in getattr(bpy.data, name, ())} for name in _COLLECTIONS}


def _new_ids(before):
    after = _ids()
    return {name: [item for pointer, item in after[name].items() if pointer not in before[name]] for name in _COLLECTIONS}


def _asset_problem(asset, package):
    """Why a USD asset path is refused, or ""."""
    parts = re.split(r"[\\/]", asset)
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", asset) or asset.startswith(("/", "\\")) or ".." in parts:
        return "points outside the file (%s)" % asset[:80]
    if package is None and asset.lower().endswith((".usd", ".usda", ".usdc", ".usdz")):
        return "composes other USD files (%s)" % asset[:80]
    return ""


def check_usd(path, facts):
    """Refuse a USD file whose layers reference anything outside it (needs Blender's bundled ``pxr``)."""
    try:
        from pxr import UsdUtils
    except ImportError:
        raise Refused("USD import isn't available in this build; ask for a GLB instead.") from None
    package = None
    if facts["format"] == "usdz":
        with zipfile.ZipFile(path) as archive:  # Entry names were checked by team_library_files.
            package = {entry.filename for entry in archive.infolist()}
    pending, seen = [(str(path), "")], set()
    while pending:
        identifier, folder = pending.pop()
        if identifier in seen:
            continue
        seen.add(identifier)
        if len(seen) > 64:
            raise Refused("This USD file has too many layers to check; it wasn't imported.")
        try:
            sublayers, references, payloads = UsdUtils.ExtractExternalReferences(identifier)
        except Exception:  # noqa: BLE001 - pxr raises Tf errors for malformed layers.
            raise Refused("This USD file couldn't be read; it wasn't imported.") from None
        for asset in [*sublayers, *references, *payloads]:
            asset = str(asset).strip()
            if not asset:
                continue
            problem = _asset_problem(asset, package)
            if problem:
                raise Refused("This USD file %s; only self-contained USD files are imported." % problem)
            if package is not None:
                inner = posixpath.normpath(posixpath.join(folder, asset.replace("\\", "/")))
                if inner not in package:
                    raise Refused("This USDZ file refers to %s, which isn't inside it; it wasn't imported." % asset[:80])
                if inner.lower().endswith((".usd", ".usda", ".usdc")):
                    pending.append(("%s[%s]" % (path, inner), posixpath.dirname(inner)))


def _view3d(window):
    area = next((a for a in live_windows.areas(window) if a.type == 'VIEW_3D'), None)
    if area is None:
        raise NotImported("Keep a 3D Viewport (or the Director preview) open to import into the scene.")
    return area, next(r for r in area.regions if r.type == 'WINDOW')


_OPERATORS = {"glb": ("import_scene", "gltf"), "fbx": ("import_scene", "fbx"), "obj": ("wm", "obj_import"),
              "usdc": ("wm", "usd_import"), "usda": ("wm", "usd_import"), "usdz": ("wm", "usd_import")}


def importer_available(fmt):
    """Whether this build has the importer (a build without OBJ or USD support has no such operator)."""
    module, name = _OPERATORS[fmt]
    try:
        getattr(getattr(bpy.ops, module), name).get_rna_type()
    except (AttributeError, KeyError):
        return False
    return True


def _run_importer(fmt, path):
    path = str(path)
    if fmt == "glb":
        return bpy.ops.import_scene.gltf(filepath=path, import_pack_images=True, import_scene_extras=False,
                                         **import_options(path))
    if fmt == "fbx":
        return bpy.ops.import_scene.fbx(filepath=path, use_image_search=False)
    if fmt == "obj":
        return bpy.ops.wm.obj_import(filepath=path)
    options = bpy.ops.wm.usd_import.get_rna_type().properties.keys()
    wanted = {"import_textures_mode": "IMPORT_PACK", "set_frame_range": False, "create_world_material": False,
              "convert_scene_units": "NONE"}
    return bpy.ops.wm.usd_import(filepath=path, **{k: v for k, v in wanted.items() if k in options})


def _scrub(created, keep=()):
    """Remove what an untrusted import must not leave behind; returns the counts."""
    removed = {"unpacked_images": 0, "texts": 0, "scripted_drivers": 0}
    keep = {item.as_pointer() for item in keep}
    for image in list(created["images"]):
        if image.as_pointer() not in keep and image.packed_file is None and image.source in {'FILE', 'SEQUENCE', 'MOVIE', 'TILED'}:
            bpy.data.images.remove(image)
            removed["unpacked_images"] += 1
    for text in list(created["texts"]):
        bpy.data.texts.remove(text)
        removed["texts"] += 1
    for name in _COLLECTIONS:
        for item in created[name]:
            try:
                animation = getattr(item, "animation_data", None)
            except ReferenceError:
                continue
            if animation is None:
                continue
            for fcurve in [f for f in animation.drivers if f.driver.type == 'SCRIPTED']:
                animation.drivers.remove(fcurve)
                removed["scripted_drivers"] += 1
    return removed


def _rollback(created):
    ids = [item for name in _COLLECTIONS for item in created[name]]
    try:
        bpy.data.batch_remove(ids)
    except (ReferenceError, RuntimeError):
        pass


def _scene_state(scene):
    return ({key: getattr(getattr(scene, owner), key) for owner, key in _SCENE_SETTINGS},
            scene.frame_start, scene.frame_end, scene.world)


def _restore_scene(scene, state):
    settings, start, end, world = state
    changed = []
    for owner, key in _SCENE_SETTINGS:
        if getattr(getattr(scene, owner), key) != settings[key]:
            setattr(getattr(scene, owner), key, settings[key])
            changed.append(key)
    if (scene.frame_start, scene.frame_end) != (start, end):
        scene.frame_start, scene.frame_end = start, end
        changed.append("frame_range")
    if scene.world != world:
        scene.world = world
        changed.append("world")
    return changed


def _rolled_back(scene, before, signature, state):
    """Whether a rollback is verified: nothing the import created remains and the scene reads as before."""
    from .blender_tools import scene_signature
    try:
        return (not any(_new_ids(before).values()) and _scene_state(scene) == state
                and not signature["truncated"] and scene_signature(scene) == signature)
    except (ReferenceError, RuntimeError):
        return False


def _failure_text(error, unchanged):
    reason = str(error)[:300] if isinstance(error, (RuntimeError, ValueError)) and str(error) else ""
    reason = reason or "Blender couldn't import this file."
    return reason + (" Nothing from it was kept." if unchanged else
                     " Removing what it added could not be confirmed; check the scene before continuing.")


def _reference_image(path, title, collection, scene):
    image = bpy.data.images.load(str(path), check_existing=False)
    image.pack()
    image.name = title[:60]
    empty = bpy.data.objects.new(title[:60], None)
    empty.empty_display_type = 'IMAGE'
    empty.data = image
    empty.empty_display_size = 2.0
    empty.location = scene.cursor.location
    empty.rotation_euler = (math.pi / 2, 0.0, 0.0)  # Facing -Y: the Front view, like a modeling reference.
    collection.objects.link(empty)
    return image


def import_into_scene(window, path, facts, item):
    """Import a checked file; returns the read-back, or raises with nothing kept."""
    from .blender_tools import scene_diff, scene_signature
    from .scene_queries import object_handle
    area, region = _view3d(window)
    title = (item.get("title") or "Team item").strip()[:60] or "Team item"
    if facts["kind"] == "model" and not importer_available(facts["format"]):
        raise Refused("This build of Angelo can't import %s files; ask for the model as GLB or FBX."
                      % facts["format"].upper().replace("USDC", "USD").replace("USDA", "USD"))
    if facts["format"] in {"usdc", "usda", "usdz"}:
        check_usd(path, facts)
    scene, layer = window.scene, window.view_layer
    with bpy.context.temp_override(window=window, area=area, region=region):
        if bpy.context.mode != 'OBJECT':
            raise NotImported("Leave the active editing or sculpt mode before importing.")
        layer.update()  # Evaluated matrices: the rollback check and read-back then compare like with like.
        before_signature, before, state = scene_signature(scene), _ids(), _scene_state(scene)
        parent = layer.active_layer_collection
        collection = bpy.data.collections.new("Team · " + title)
        parent.collection.children.link(collection)
        previous_active = layer.active_layer_collection
        layer.active_layer_collection = next(c for c in parent.children if c.collection == collection)
        keep = ()
        try:
            if facts["kind"] == "image":
                keep = (_reference_image(path, title, collection, scene),)
            else:
                result = _run_importer(facts["format"], path)
                if result != {'FINISHED'}:
                    raise RuntimeError("Blender didn't finish importing this %s." % facts["format"].upper())
            created = _new_ids(before)
            removed = _scrub(created, keep)
            restored = _restore_scene(scene, state)
        except Exception as error:
            layer.active_layer_collection = previous_active
            _rollback(_new_ids(before))
            _restore_scene(scene, state)
            unchanged = _rolled_back(scene, before, before_signature, state)
            raise ImportFailed(_failure_text(error, unchanged), scene_unchanged=unchanged) from error
        layer.active_layer_collection = previous_active
        objects = [o for o in collection.all_objects]
        if not objects:
            _rollback(_new_ids(before))
            if _rolled_back(scene, before, before_signature, state):
                return {"success": False, "scene_changed": False, "objects": [], "object_count": 0,
                        "error_code": "UNSUPPORTED", "error": "The file contained no scene objects; nothing was imported."}
            return {"success": False, "partial_changes_possible": True, "objects": [], "object_count": 0,
                    "error": "The file contained no scene objects; removing what the import added could not be "
                             "confirmed. Check the scene before continuing."}
        for obj in objects:
            obj[ITEM_PROPERTY] = item["id"]
        layer.update()
        bpy.ops.ed.undo_push(message="Import “%s” from team library" % title)
    counts = {}
    for obj in objects:
        counts[obj.type] = counts.get(obj.type, 0) + 1
    created = _new_ids(before)
    instances = InstanceFacts(layer.depsgraph)
    result = {"success": True, "item_id": item["id"], "collection": collection.name, "object_count": len(objects),
              "types": counts, "objects": [_object_row(o, instances) for o in objects[:MAX_LISTED]],
              "materials": len(created["materials"]), "packed_images": len(created["images"]),
              "removed_for_safety": {k: v for k, v in removed.items() if v}, "blender_undoable": True,
              "changes": scene_diff(before_signature, scene_signature(scene))}
    if restored:
        result["scene_settings_kept"] = restored  # The file asked to change these; the scene's own values stay.
    if len(objects) > MAX_LISTED:
        result["objects_truncated"] = True
    return result


def import_into_studio(path, item):
    """Copy a checked image into the local Media Studio library (no scene change)."""
    from . import studio
    library = studio.library()
    asset = library.import_image(Path(path), "")
    asset["prompt"] = (item.get("title") or "Team library image")[:200]
    library.update(asset)
    return {"success": True, "item_id": item["id"], "studio_asset_id": asset["id"], "scene_changed": False,
            "note": "A Media Studio asset now; pass it to angelo_studio_generate as a reference with source ASSET."}


def _object_row(obj, instances):
    """An imported object as the result states it: its dimensions, and ``instances`` for one that makes them; no
    dimensions for one that shows only instances (they would be its base data's, 0 or a curve's padded cage)."""
    from .scene_queries import object_handle
    row = {"name": obj.name[:128], "type": obj.type, "handle": object_handle(obj)}
    fact = instances.of(obj)
    if states_own_box(fact) and instances.own_box_known(obj):
        row["dimensions"] = [round(v, 4) for v in obj.dimensions]
    if fact is not None:
        row["instances"] = fact
    return row
