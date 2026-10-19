# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread preparation of a team-library upload: the file, its type, a thumbnail and provenance.

Sources (``angelo_team_library_save``):

* ``STUDIO_ASSET``: a ready Media Studio output (image, or GLB with its preview
  as the thumbnail); provenance ``generated`` with the prompt, model, provider
  and options Studio recorded.
* ``OBJECTS``: the given scene objects exported as one GLB (modifiers applied,
  the selection restored afterwards) and a 512-pixel Material Preview image of
  them as the thumbnail; provenance ``derived`` from the team items they were
  imported from (``angelo_team_item``), else ``upload``.
* ``VIEW``: an ``angelo_view`` image from this chat (PNG).
* ``ATTACHMENT``: an image attached to this chat.

The console requires a WebP thumbnail for models (its presigned upload is
``image/webp``); Blender writes it. Every file is written into the caller's
private temporary folder or read from an owner's checked path; no model-supplied
path is ever opened. The upload itself runs on a worker (``team_library_host``).

bpy is imported by this module; it runs only on the main thread.
"""

from pathlib import Path

import bpy

from .provider_registry import SPECS
from .team_library_files import IMAGE_FORMATS, image_size, sniff
from .team_library_spec import ITEM_PROPERTY

MIME = {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}
MAX_UPLOAD = {"image": 64 * 1024 * 1024, "model": 1024 * 1024 * 1024}  # The console's own caps.
THUMBNAIL = 512
EXPORTABLE = frozenset({"MESH", "CURVE", "SURFACE", "FONT", "META", "EMPTY", "ARMATURE", "CAMERA", "LIGHT",
                        "CURVES", "POINTCLOUD"})


def _image(path, name):
    with open(path, "rb") as handle:
        head = handle.read(4 * 1024 * 1024)
    fmt = sniff(head[:65536]) if head else ""
    if fmt not in IMAGE_FORMATS:
        raise ValueError("%s isn't a PNG, JPEG or WebP image." % name)
    width, height = image_size(head) or (None, None)
    return {"path": Path(path), "mime": MIME[fmt], "kind": "image", "extension": IMAGE_FORMATS[fmt],
            "width": width, "height": height}


def webp_thumbnail(source, destination):
    """Write a WebP of at most ``THUMBNAIL`` pixels a side from an image file."""
    image = bpy.data.images.load(str(source), check_existing=False)
    try:
        width, height = image.size
        if not width or not height:
            raise ValueError("The preview image is empty.")
        scale = min(1.0, THUMBNAIL / max(width, height))
        if scale < 1.0:
            image.scale(max(1, round(width * scale)), max(1, round(height * scale)))
        image.filepath_raw, image.file_format = str(destination), 'WEBP'
        image.save()
    finally:
        bpy.data.images.remove(image)
    return Path(destination)


def _studio_asset(controller, window, asset_id, folder):
    from .studio_host import service_for
    service = service_for(controller, window)
    asset = service.asset(asset_id, ready=True)
    path = service.library.checked_file(asset, asset["files"][0])
    provenance = {"source": "generated", "prompt": (asset.get("prompt") or "")[:16000],
                  "model": str(asset.get("model_id") or asset.get("model") or "")[:200],
                  "provider": str(SPECS.get(asset.get("mode"), {}).get("provider", "")).lower()[:60]}
    if isinstance(asset.get("options"), dict) and asset["options"]:
        provenance["params"] = dict(list(asset["options"].items())[:30])
    if asset["kind"] == "IMAGE":
        return dict(_image(path, "This Studio output"), provenance=provenance)
    if path.suffix.lower() != ".glb":
        raise ValueError("Only GLB Media Studio models can be saved to the team library.")
    prepared = {"path": path, "mime": "model/gltf-binary", "kind": "model", "extension": ".glb",
                "provenance": provenance}
    if asset.get("thumbnail"):
        try:
            preview = service.library.checked_file(asset, asset["thumbnail"])
            prepared["thumbnail"] = webp_thumbnail(preview, folder / "thumb.webp")
        except (ValueError, OSError, RuntimeError):
            pass  # The console shows a typed placeholder; the model is still saved.
    return prepared


def resolve_objects(window, handles):
    from .scene_queries import resolve_object
    objects = []
    for handle in handles:
        obj = resolve_object(window, handle)
        if obj.type not in EXPORTABLE:
            raise ValueError("“%s” is a %s, which can't be saved as a model." % (obj.name[:64], obj.type.lower()))
        objects.append(obj)
    return objects


def _objects(controller, window, handles, folder):
    objects = resolve_objects(window, handles)
    layer = window.view_layer
    area = next((a for a in window.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        raise ValueError("Keep a 3D Viewport (or the Director preview) open to save scene objects.")
    region = next(r for r in area.regions if r.type == 'WINDOW')
    selected = [o for o in window.scene.objects if o.select_get(view_layer=layer)]
    active = layer.objects.active
    path = folder / "model.glb"
    with bpy.context.temp_override(window=window, area=area, region=region):
        if bpy.context.mode != 'OBJECT':
            raise ValueError("Leave the active editing or sculpt mode before saving objects.")
        try:
            for obj in selected:
                obj.select_set(False, view_layer=layer)
            for obj in objects:
                obj.select_set(True, view_layer=layer)
            result = bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=True,
                                               export_apply=True)
        finally:
            for obj in objects:
                obj.select_set(False, view_layer=layer)
            for obj in selected:
                obj.select_set(True, view_layer=layer)
            layer.objects.active = active
    if result != {'FINISHED'} or not path.is_file():
        raise RuntimeError("Blender couldn't export these objects as GLB; nothing was uploaded.")
    parents = sorted({str(o[ITEM_PROPERTY]) for o in objects if isinstance(o.get(ITEM_PROPERTY), str)})[:30]
    provenance = {"source": "derived" if parents else "upload"}
    if parents:
        provenance["parentItemIds"] = parents
    prepared = {"path": path, "mime": "model/gltf-binary", "kind": "model", "extension": ".glb",
                "provenance": provenance}
    try:
        prepared["thumbnail"] = _object_thumbnail(controller, window, handles, folder)
    except (ValueError, RuntimeError, OSError):
        pass  # A thumbnail is optional; the console shows a typed placeholder.
    return prepared


def _object_thumbnail(controller, window, handles, folder):
    from . import view_capture
    references = controller.references
    capture = view_capture.execute(window, {"targets": list(handles)[:16], "views": ["ISO"], "shading": "MATERIAL",
                                            "resolution": THUMBNAIL}, references)
    view = capture["views"][0]
    try:
        return webp_thumbnail(references.checked_path(view), folder / "thumb.webp")
    finally:
        references.discard_observation(view)


def _attachment(controller, identity):
    records = controller.turn_attachments + [r for m in controller.state["messages"] for r in m.get("references", [])]
    reference = next((r for r in records if Path(r["path"]).stem == identity), None)
    if not reference or reference.get("kind") != "image":
        raise ValueError("Choose an image attached to THIS conversation.")
    return controller.references.checked_path(reference)


def _view(controller, view_id):
    references = controller.references
    record = {"id": view_id, "path": str(references.directory / (view_id + ".png"))}
    try:
        return references.checked_path(record)
    except (ValueError, OSError):
        raise ValueError("That view is no longer available; take a new angelo_view and save that.") from None


def check_source(controller, window, args):
    """Main-thread precondition: the source exists now. Returns a signature that changes with it."""
    source = args["source"]
    if source == "OBJECTS":
        objects = resolve_objects(window, args["objects"])
        return tuple((o.session_uid, o.name) for o in objects)
    if source == "STUDIO_ASSET":
        from .studio_host import service_for
        service = service_for(controller, window)
        asset = service.asset(args["asset_id"], ready=True)
        path = service.library.checked_file(asset, asset["files"][0])
        return (asset["id"], str(path), path.stat().st_size, path.stat().st_mtime_ns)
    path = _view(controller, args["view_id"]) if source == "VIEW" else _attachment(controller, args["reference_id"])
    return (str(path), path.stat().st_size, path.stat().st_mtime_ns)


def prepare(controller, window, args, folder):
    """The file to upload and what the console is told about it."""
    folder = Path(folder)
    source = args["source"]
    if source == "STUDIO_ASSET":
        prepared = _studio_asset(controller, window, args["asset_id"], folder)
    elif source == "OBJECTS":
        prepared = _objects(controller, window, args["objects"], folder)
    elif source == "VIEW":
        prepared = dict(_image(_view(controller, args["view_id"]), "This view"), provenance={"source": "upload"})
    else:
        prepared = dict(_image(_attachment(controller, args["reference_id"]), "This attachment"),
                        provenance={"source": "upload"})
    size = prepared["path"].stat().st_size
    if not 0 < size <= MAX_UPLOAD[prepared["kind"]]:
        raise ValueError("The team library takes %s files up to %d MB; this one is %.0f MB."
                         % (prepared["kind"], MAX_UPLOAD[prepared["kind"]] // 1048576, size / 1048576))
    provenance = prepared["provenance"]
    provenance["app"] = {"name": "Angelo", "version": bpy.app.version_string[:60]}
    provenance["scene"] = window.scene.name[:300]
    if args.get("ip_entities"):
        provenance["ipEntities"] = [{"entityId": e["entity_id"], "version": e["version"]} for e in args["ip_entities"]]
    if args.get("reference_item_ids"):
        provenance["referenceItemIds"] = list(args["reference_item_ids"])
    prepared["size"] = size
    return prepared

