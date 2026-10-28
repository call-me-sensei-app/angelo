# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread binding of chat Studio tools to the existing app-owned services."""

import hashlib
import json
import re
import uuid
from pathlib import Path

import bpy

from . import live_windows
from .gltf_morph import import_options
from .studio_agent import StudioAgent
from .provider_registry import billing_label, is_coding


def _enhancer(arguments):
    from . import studio, ui
    from .studio_generation import enhance_gemini
    from .studio_enhance import enhance_native
    if arguments.get("connection_id"):
        record = ui.find_connection(arguments["connection_id"])
        if not record:
            raise ValueError("The requested enhancement account is unavailable; no billing fallback")
        config = {"connection": record["id"], "model": arguments["model"]}
        if is_coding(record):
            if not ui.connection_ready(record) or not any(m["id"] == config["model"] for m in ui.catalogs().entries.get(record["id"], [])):
                raise ValueError("Choose an available model on the exact coding enhancement account")
        elif not ui.gemini_text_ready(record):
            raise ValueError("Choose a connected Gemini text or coding enhancement account")
    else:
        record, config = studio.enhancement_binding()
    if not is_coding(record) and not re.fullmatch(r"gemini-[A-Za-z0-9_.-]+", config["model"]):
        raise ValueError("Choose a valid Gemini text model for enhancement")
    key = ui._memory_keys.get(record["id"], "")
    access = None if is_coding(record) else ui.gemini_text_binding(record)
    settings = ui.connection_settings(record["mode"], record=record) if is_coding(record) else {}
    models = list(ui.catalogs().entries.get(record["id"], [])) if is_coding(record) else []
    prompt, kind, model = arguments["prompt"], arguments["kind"], arguments["generation_model"]
    directory, transport = studio.library().directory, studio._jobs.transport
    identity = "chat-" + str(uuid.uuid4())
    def run(cancel):
        if cancel.is_set():
            raise ValueError("Enhancement cancelled before submission")
        return (enhance_native(directory, record, settings, key, config["model"], models, prompt, kind, model, cancel)
            if is_coding(record) else enhance_gemini(access, config["model"], prompt, kind, model, transport))
    billing = billing_label(record["mode"])
    scope = access.scope_digest() if access is not None else hashlib.sha256(key.encode()).hexdigest()
    return {"connection": record["id"], "label": record["name"] + " · " + config["model"] +
        (" · subscription usage" if billing == "Subscription" else " · " + billing),
        "signature": (json.dumps(record, sort_keys=True), json.dumps(config, sort_keys=True), scope),
        "run": run, "started": lambda: studio._enhancement_connections.update({identity: record["id"]}),
        "finished": lambda: studio._enhancement_connections.pop(identity, None)}


def write_fitted(path, destination, long_edge):
    """Write ``path``'s pixels to ``destination`` as a PNG no longer than ``long_edge`` on either side. Main thread.

    Full-size paid outputs stay untouched in the library; what a model is shown is this private copy, which fits the
    image-input bounds every attachment has (``references.MAX_BYTES``). Generated sheets are 3840 px PNGs of 4-6 MB.
    """
    from .studio_generation import MAX_IMAGE, validated_image
    from .studio_previews import release_preview
    if path.stat().st_size > MAX_IMAGE:
        raise ValueError("The image exceeds the supported image size")
    validated_image(path.read_bytes())  # Bound decoded dimensions before native loading.
    image = None
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
        scale = min(1, long_edge / max(image.size))
        if scale < 1:
            image.scale(max(1, round(image.size[0] * scale)), max(1, round(image.size[1] * scale)))
        image.filepath_raw, image.file_format = str(destination), 'PNG'
        image.save()
    finally:
        release_preview((image, None) if image else None, bpy.data.images)
    return Path(destination)


def _preview(controller, path):
    from .studio_generation import MAX_IMAGE, validated_image
    if path.stat().st_size > MAX_IMAGE:
        raise ValueError("Preview exceeds the supported image size")
    validated_image(path.read_bytes())  # Bound decoded dimensions before native loading.
    if path.stat().st_size <= controller.references.image_limit:
        return controller.references.import_images([str(path)])
    reference = controller.references.new_observation("Media Studio preview")
    try:
        write_fitted(path, reference["path"], 768)
        reference["bytes"] = controller.references.checked_path(reference).stat().st_size
        return [reference]
    except Exception:
        controller.references.discard_observation(reference)
        raise


def scene_view(window):
    """(window, 3D view) to import into ``window``'s scene: its own 3D view, else one in another window showing the
    same scene. Media Studio and Generation Flows open in windows of their own (1.0.5) that have no 3D view, and
    their Place buttons import into the scene the person is working in. (None, None) when no window shows it."""
    others = live_windows.windows(bpy.context.window_manager)
    windows = [window] + [w for w in others if w != window and w.scene == window.scene]
    for candidate in windows:
        area = next((a for a in live_windows.areas(candidate) if a.type == 'VIEW_3D'), None)
        if area is not None:
            return candidate, area
    return None, None


def _import_model(window, path):
    """Import one GLB into ``window``'s scene: the agent's result (``objects`` bounded at 200 names)."""
    added = import_glb(window, path)
    if not added:
        return {"success": False, "error": "The GLB contained no scene objects; nothing was imported. Do not automatically regenerate it.", "objects": [], "object_count": 0}
    return {"success": True, "objects": added[:200], "object_count": len(added), "blender_undoable": True}


def import_glb(window, path):
    """Import one checked GLB into ``window``'s scene through a window that shows it; every new object's name."""
    from .studio_generation import checked_glb, MAX_MODEL
    if path.stat().st_size > MAX_MODEL:
        raise ValueError("The local GLB exceeds the supported size")
    checked_glb(path.read_bytes())
    window, area = scene_view(window)
    if not area:
        raise ValueError("Keep a scene preview available before importing a model")
    before = {o.as_pointer() for o in window.scene.objects}
    region = next(r for r in area.regions if r.type == 'WINDOW')
    with bpy.context.temp_override(window=window, area=area, region=region):
        if bpy.context.mode != 'OBJECT':
            raise ValueError("Leave the active editing/sculpt mode before importing a model")
        try:
            result = bpy.ops.import_scene.gltf(filepath=str(path), **import_options(path))
        except RuntimeError:
            result = {'CANCELLED'}
    added = [o.name for o in window.scene.objects if o.as_pointer() not in before]
    if result != {'FINISHED'}:
        for name in added:   # What a failed import left half-made is removed, never kept unrecorded.
            bpy.data.objects.remove(bpy.data.objects[name])
        raise RuntimeError("Blender did not finish importing this GLB; inspect before retrying")
    window.view_layer.update()
    return added


def service_for(controller, window):
    if controller is None:
        raise RuntimeError("Studio tools must be bound to an active chat")
    from . import live_target, studio, ui
    service, live = getattr(controller, "media_service", None), live_target.window_ref(window)   # Kept on the chat:
    if service is None:   # its imports read the window live, never the window itself (live_target).
        library = studio.library()
        service = StudioAgent(library, studio._jobs,
            lambda: [m for kind in ("IMAGE", "3D") for m in studio.model_options(kind, ui.generation_connections(), ui.generation_health())],
            ui.generation_binding, controller, lambda path: _import_model(live(), path), _enhancer)
        controller.media_service = service
    service.enhancement_accounts = lambda: [
        {"connection_id": r["id"], "account": r["name"], "models":
            [{"id": m["id"], "name": m["name"]} for m in ui.catalogs().entries.get(r["id"], [])] if is_coding(r)
            else [{"id": studio.library().enhancer["model"] if studio.library().enhancer["model"].startswith("gemini-") else "gemini-3.8-flash", "name": "Gemini text enhancement model (access not independently verified)"}],
         "custom_gemini_text_model": not is_coding(r)}
        for r in ui.connection_records().values()
        if (is_coding(r) and ui.connection_ready(r)) or (not is_coding(r) and ui.gemini_text_ready(r))]
    service.preview = lambda path: _preview(controller, path)
    service.import_model = lambda path: _import_model(live(), path)
    return service
