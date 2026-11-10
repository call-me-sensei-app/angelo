# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-step web export: Blender's glTF exporter with web settings, safe merging, KTX2 where possible, a report.

``check`` describes what an export would lose (``web_compat``) and estimates its budget without writing;
``export`` writes one self-contained .glb and measures it (``web_budget``). Both read the scene only: merging
happens in the exporter's own glTF tree (``WebExportExtension``, ``web_merge``), so no Blender data changes and no
depsgraph update follows an export. Main thread only. doc/angelo/web_export.rst has the contract.

Choices, each reported in ``settings`` so a result never hides what it did:

* meshes AUTO: meshopt when this build has it (one small decoder in Three.js), else Draco, else uncompressed.
* lights: unitless (the exporter's COMPAT mode), the convention Three.js scenes use: physical units would hand a
  sun of 3 W/m² to Three.js as about 2,000 lux, which its renderer shows blown out without an exposure model.
* textures AUTO: KTX2 (Basis) when a gltfpack with texture compression is set up (the glTF add-on's preference,
  or on PATH), else WebP. Blender's bundled tools have no KTX2 encoder of their own. KEEP writes PNG and JPEG.
* look: the scene's view transform, look and exposure as three.js settings, and its World as a colour or an
  equirectangular picture (``web_look``, ``web_world``, ``web_environment``), in the glTF scene's ``extras``
  (``angelo_look``). An explicit export carries the World's picture: while it is still being made, ``export``
  raises ``EnvironmentPending`` before writing and ``pending_export`` waits for it without blocking the main
  thread (``web_export_wait``). The live preview leaves a picture still being made out (``require_environment``)
  and exports again when it lands.
* baked_materials (on unless the request turns it off): an object with a current baked material from a texture-set
  bake exports with it, in the file only (``export_baked``); ``baked`` lists them and the stale ones.
"""

import os
import shutil
import subprocess
import tempfile
import time

import bpy

from . import export_baked, web_budget, web_compat, web_environment, web_facts, web_glb, web_look, web_world_key
from . import shared_replace
from .web_export_wait import EnvironmentPending, PendingExport
from .ui_text import n_

EXPORT_ID = "angelo_web"
LINK_EXPORT_ID = "angelo_link_web"   # web_link_assets.LIVE_EXPORT_ID: the web link's live frames.
GLTFPACK_SECONDS = 300
_ACTIVE = {}   # The running web export's merge request and result, read by WebExportExtension.

UNAVAILABLE = {
    "meshes": (n_("{kind} mesh compression isn't in this build; meshes are written uncompressed"),
               n_("Choose AUTO, or use a build with Draco and meshoptimizer")),
    "ktx2": (n_("KTX2 textures need gltfpack, which isn't set up; textures are WebP"),
             n_("Install gltfpack and set its path in the glTF add-on's preferences")),
    "ktx2_failed": (n_("gltfpack couldn't write KTX2 textures; textures are WebP"),
                    n_("Use a gltfpack build with texture compression (-tc)")),
}


class WebExportError(RuntimeError):
    """The export could not be written; nothing was left at the output."""


class WebExportExtension:
    """The glTF exporter's user extension (``angelo_agent.glTF2ExportUserExtension``): only Angelo's own exports
    (each hook checks ``gltf_export_id``)."""

    def gather_material_hook(self, gltf2_material, blender_material, export_settings):
        if export_settings.get("gltf_export_id") != EXPORT_ID or blender_material is None:
            return
        from . import web_shader
        shader = web_shader.extras(blender_material)   # Read by the preview page; other tools ignore extras.
        if shader is not None:
            gltf2_material.extras = dict(gltf2_material.extras or {}, **shader)

    def gather_tree_filter_tag_hook(self, vtree, export_settings):
        """The web link's live delta writes only the objects that changed (web_link_live.filter_tree)."""
        if export_settings.get("gltf_export_id") == LINK_EXPORT_ID:
            from . import web_link_live
            web_link_live.filter_tree(vtree)

    def gather_image_hook(self, gltf2_image, blender_image, blender_shader_sockets, export_settings):
        """The web link names each image by its bytes and sends one the page has as a stub (web_link_live.image)."""
        if export_settings.get("gltf_export_id") == LINK_EXPORT_ID:
            from . import web_link_live
            web_link_live.image(gltf2_image)

    def gather_mesh_materials_hook(self, materials, blender_object, export_settings):
        """Angelo's web and engine-link exports write an object with its current baked material (``export_baked``)."""
        export_baked.substitute(materials, blender_object, export_settings)

    def gather_scene_hook(self, gltf2_scene, blender_scene, export_settings):
        if export_settings.get("gltf_export_id") == LINK_EXPORT_ID:   # Each node's key and role in a live frame.
            from . import web_link_live
            web_link_live.mark_nodes(export_settings["vtree"])
            return
        if export_settings.get("gltf_export_id") != EXPORT_ID:
            return
        if _ACTIVE.get("extras"):   # The look; the World's picture becomes a bufferView like the exporter's own data.
            from io_scene_gltf2.io.exp.binary_data import BinaryData
            gltf2_scene.extras = dict(gltf2_scene.extras or {}, **web_glb.for_exporter(_ACTIVE["extras"], BinaryData))
        if not _ACTIVE.get("merge"):
            return
        from . import web_merge
        try:
            _ACTIVE["merged"] = web_merge.merge_scene(gltf2_scene, _ACTIVE["static"])
        except Exception as error:  # A merge that fails leaves the tree as the exporter made it (merge_scene).
            _ACTIVE["merged"] = {"objects": 0, "groups": 0, "error": type(error).__name__}


def _addon():
    """The glTF add-on module, enabled for this session if needed (never saved in preferences)."""
    if not hasattr(bpy.types, "EXPORT_SCENE_OT_gltf"):
        import addon_utils
        addon_utils.enable("io_scene_gltf2", default_set=False, persistent=False)
    import io_scene_gltf2
    return io_scene_gltf2


def run_exporter(window, settings):
    """``export_scene.gltf`` with ``settings`` that leaves the person in the mode they are in.

    Blender's exporter switches the active object to Object Mode before it reads the scene and never switches back.
    Sculpt, Weight, Vertex and Texture Paint keep their data in the mesh, so the exporter runs with no active object,
    the condition under which it switches nothing. Edit Mode keeps its changes outside the mesh, where the exporter
    can't read them: the exporter switches as it always does and the objects go back into Edit Mode afterwards (the
    live previews never export then: ``scene_changes.busy``). ``window`` None: the plain context."""
    override = {} if window is None else {"window": window}
    with bpy.context.temp_override(**override):
        editing = [obj for obj in bpy.context.objects_in_mode or () if obj.mode == "EDIT"]
        if not editing:
            with bpy.context.temp_override(active_object=None, object=None):
                return bpy.ops.export_scene.gltf(**settings)
        view_layer = bpy.context.view_layer
        active = view_layer.objects.active
        try:
            return bpy.ops.export_scene.gltf(**settings)
        finally:
            if active is not None and active.name in view_layer.objects and active.mode != "EDIT":
                view_layer.objects.active = active
                for obj in editing:
                    obj.select_set(True)
                bpy.ops.object.mode_set(mode="EDIT")


def public_baked(found):
    """``export_baked.plan`` as a report shows it: ``{"used": [{"object", "material"}], "stale": [object],
    "packing": [{"object", "packing"}]}``."""
    return {"used": [{"object": name, "material": material} for name, material in found["used"]],
            "stale": list(found["stale"]),
            "packing": [{"object": name, "packing": packing} for name, packing in found["packing"]]}


def gltfpack_path():
    """A usable gltfpack: the glTF add-on's preference, else one on PATH, else None."""
    addon = bpy.context.preferences.addons.get("io_scene_gltf2")
    configured = getattr(getattr(addon, "preferences", None), "gltfpack_path_ui", "").strip() if addon else ""
    for candidate in (configured, shutil.which("gltfpack") or ""):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def resolve(request):
    """(settings actually used, findings for anything requested that this machine can't do)."""
    addon = _addon()
    meshopt, draco = addon.is_meshopt_available(), addon.is_draco_available()
    found = []
    meshes = request["meshes"]
    if meshes == "AUTO":
        meshes = "MESHOPT" if meshopt else "DRACO" if draco else "NONE"
    elif meshes == "MESHOPT" and not meshopt or meshes == "DRACO" and not draco:
        found.append(_note("meshes", kind=meshes.title()))
        meshes = "NONE"
    textures = request["textures"]
    pack = gltfpack_path()
    if textures == "AUTO":
        textures = "KTX2" if pack else "WEBP"
    elif textures == "KTX2" and not pack:
        found.append(_note("ktx2"))
        textures = "WEBP"
    return {"meshes": meshes, "textures": textures, "merge": request["merge"],
            "selection_only": request["selection_only"],
            "baked_materials": request.get("baked_materials", True)}, found


def _note(code, **params):
    message, fix = UNAVAILABLE[code]
    return {"code": code, "severity": web_compat.WARN if code != "ktx2" else web_compat.INFO, "subject": "",
            "message": message, "fix": fix, "params": dict(params, subject="")}


def static_names(objects):
    """Names of objects with no animation, drivers or constraints on them or any parent (safe to merge), and no game
    physics on them or a parent: a merged node would leave its body or shape behind (doc/angelo/game_physics.rst)."""
    from io_scene_gltf2.angelo_physics.props import marked

    def animated(block):
        data = getattr(block, "animation_data", None)
        return bool(data is not None and (data.action is not None or len(data.drivers) or len(data.nla_tracks)))

    names = set()
    for obj in objects:
        node, moving = obj, False
        while node is not None and not moving:
            moving = animated(node) or bool(node.constraints) or node.parent_type != "OBJECT" or marked(node)
            node = node.parent
        if not moving and not animated(getattr(obj, "data", None)):
            names.add(obj.name)
    return names


def _operator_settings(path, settings, textures):
    return dict(
        filepath=path, check_existing=False, export_format="GLB", gltf_export_id=EXPORT_ID, will_save_settings=False,
        use_active_scene=True, use_visible=True, use_selection=settings["selection_only"], export_apply=True,
        export_yup=True, export_cameras=True, export_lights=True, export_extras=False, export_animations=True,
        export_gpu_instances=True, export_import_convert_lighting_mode="COMPAT",
        export_image_format={"WEBP": "WEBP", "KEEP": "AUTO", "KTX2": "AUTO"}[textures],
        export_draco_mesh_compression_enable=settings["meshes"] == "DRACO" and textures != "KTX2",
        export_meshopt_compression_enable=settings["meshes"] == "MESHOPT" and textures != "KTX2")


def _run_gltfpack(binary, source, target, settings):
    """Compress ``source``'s textures to KTX2 (and its meshes with meshopt) into ``target``; True on success."""
    command = [binary, "-i", source, "-o", target, "-tc"]
    if settings["meshes"] != "NONE":
        command.append("-cc")     # gltfpack writes meshopt; it has no Draco encoder.
    if not settings["merge"]:
        command.append("-kn")     # Keep every named node, as merge=false promises.
    try:
        subprocess.run(command, check=True, timeout=GLTFPACK_SECONDS, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return os.path.isfile(target) and os.path.getsize(target) > 0


def _write(window, scene, view_layer, path, settings, baked, extras=None):
    """Run the exporter into ``path`` with ``baked`` (``export_baked.plan``) and the scene ``extras``; returns the
    merge result. Raises WebExportError."""
    objects = web_facts.exported_objects(scene, view_layer, settings["selection_only"])
    _ACTIVE.clear()
    _ACTIVE.update(merge=settings["merge"], static=static_names(objects).__contains__,
                   merged={"objects": 0, "groups": 0}, extras=extras)
    try:
        textures = settings["textures"]
        target = path if textures != "KTX2" else path + ".unpacked.glb"
        with export_baked.active(baked):
            result = run_exporter(window, _operator_settings(target, settings, textures))
        if "FINISHED" not in result or not os.path.isfile(target):
            raise WebExportError(n_("Blender's glTF exporter didn't write the file; see the Info log"))
        if textures == "KTX2":
            packed = _run_gltfpack(gltfpack_path(), target, path, settings)
            if packed:
                os.remove(target)
                if extras:   # gltfpack keeps no extras and rewrites every buffer: set them again.
                    with open(path, "rb") as handle:
                        data = web_glb.add_scene_extras(handle.read(), extras)
                    with open(path, "wb") as handle:
                        handle.write(data)
            else:   # KTX2 failed: export again with WebP, so the file is still web-ready.
                os.remove(target)
                settings.update(textures="WEBP", ktx2_failed=True)
                with export_baked.active(baked):
                    run_exporter(window, _operator_settings(path, settings, "WEBP"))
        return dict(_ACTIVE["merged"])
    finally:
        _ACTIVE.clear()


def _draw_call_estimate(objects):
    return sum(max(1, len([slot for slot in obj.material_slots if slot.material is not None]))
               for obj in objects if obj.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"})


def _inspect(window, scene, view_layer, request):
    """(settings, findings, facts, objects, baked) of an export of ``scene`` with ``request``: ``baked`` is the
    ``export_baked.plan`` the export uses. Writes nothing (a plan's later scheme record aside: ``export_baked``)."""
    settings, notes = resolve(request)
    with bpy.context.temp_override(window=window):
        depsgraph = bpy.context.evaluated_depsgraph_get()
    objects = web_facts.exported_objects(scene, view_layer, settings["selection_only"])
    baked = export_baked.plan(objects, depsgraph) if settings["baked_materials"] else export_baked.NONE
    facts = web_facts.gather(scene, view_layer, settings["selection_only"], depsgraph, baked)
    return settings, notes + web_compat.findings(facts), facts, objects, baked


def _look_settings(look):
    """The three.js settings the export writes (``web_look.extras``), as the report shows them."""
    from . import web_tone
    return {"tone_mapping": look["tone_mapping"], "exposure": web_tone.exposure(look["exposure"]),
            "output": look["output"], "view_transform": look["view_transform"]}


def check(window, scene, view_layer, request):
    """What an export of ``scene`` would lose and its estimated budget; writes nothing."""
    settings, findings, facts, objects, baked = _inspect(window, scene, view_layer, request)
    settings = dict(settings, look=_look_settings(facts["look"]), environment={"kind": facts["world"]["kind"]})
    return {"settings": settings, "findings": findings, "baked": public_baked(baked),
            "estimate": {"objects": len(objects), "triangles": facts["triangles"],
                         "materials": len(facts["materials"]), "textures": len(facts["images"]),
                         "draw_calls_before_merge": _draw_call_estimate(objects)}}


def _environment(scene, world, require, asked=None):
    """(picture or None, its report, a finding or None) for ``world`` (``web_world.plan``). While the picture is still
    being made: ``require`` True raises EnvironmentPending, False (the live preview) marks it pending, None (an
    explicit export whose wait ran out) leaves it out, "failed" (``PendingExport`` adds the finding). ``asked``: as
    ``web_environment.ensure``."""
    if world["kind"] not in ("IMAGE", "BAKE"):
        return None, {"kind": world["kind"]}, None
    job = web_environment.ensure(scene, world, web_look._config_path(), asked)
    if job.status == "pending":
        if require:
            raise EnvironmentPending(job)
        return None, {"kind": world["kind"], "status": "pending" if require is False else "failed"}, None
    if job.status == "failed":
        found = web_compat.finding("world_failed", world["name"], reason=job.error or "unknown")
        return None, {"kind": world["kind"], "status": "failed"}, found
    report = {"kind": world["kind"], "status": "ready", "width": job.width, "height": job.height,
              "bytes": len(job.data), "made_ms": round(job.ms, 1)}
    return {"data": job.data, "width": job.width, "height": job.height}, report, None


def export(window, scene, view_layer, path, request, require_environment=True, preflight=False, shown=None,
           asked=None):
    """Write a web-ready .glb at ``path`` (a file that doesn't exist yet) and report it.

    ``require_environment`` (an explicit export): a World picture still being made raises EnvironmentPending and
    nothing is written; ``pending_export`` waits for it and calls again. False (the live preview): it is left out
    and marked pending; the preview exports again once it lands (``web_environment.ready_since``). None (an explicit
    export whose wait ran out): it is left out of the file for good (``web_export_wait``). ``asked``: when an explicit
    export was asked; a bake that ran out of time before then is made again (``web_environment.ensure``).

    ``shown`` (the live preview's own dict): ``shown["environment"]`` is the environment its page shows, which a
    picture still being made is written as (``web_look.extras``'s ``standin``), so a page (re)loaded during a bake
    gets the same lighting the open page keeps; every other export updates it to what it wrote.

    ``preflight``: also the engine preflight's WEB rules (under ``preflight``) and the scene's description for the
    read-back the caller starts in the background once the file is published (``_readback``: popped by ``web_host``,
    ``web_readback``); the live preview leaves it off, since it re-exports after every change."""
    started = time.perf_counter()
    settings, findings, facts, _objects, baked = _inspect(window, scene, view_layer, request)
    settings = dict(settings)
    picture, environment, failed = _environment(scene, facts["world"], require_environment, asked)
    if failed is not None:
        findings.insert(0, failed)
    pending = environment.get("status") == "pending"
    extras = web_look.extras(facts["look"], dict(facts["world"], status=environment.get("status")), picture,
                             standin=shown.get("environment") if shown is not None and pending else None)
    source = None
    if preflight:   # After the wait above: only the attempt that writes reads the rules.
        from . import engine_preflight
        rules = engine_preflight.check(window, "WEB", "GLB", selection_only=settings["selection_only"])
        source = engine_preflight.snapshot_for(
            window, web_facts.exported_objects(scene, view_layer, settings["selection_only"]), baked["used"])
    folder = os.path.dirname(path) or "."
    descriptor, scratch = tempfile.mkstemp(prefix=".angelo-web-", suffix=".glb", dir=folder)
    os.close(descriptor)
    try:
        with web_world_key.quiet():   # The exporter's own frame steps are no edit of the World.
            merged = _write(window, scene, view_layer, scratch, settings, baked, extras)
        budget = web_budget.measure(scratch)
        shared_replace.replace(scratch, path)   # An engine or viewer holding the person's file: retried, else raised.
        if shown is not None and not pending:   # Published now: what the page shows until another lands.
            shown["environment"] = extras["angelo_look"]["environment"]
    except BaseException:
        for leftover in (scratch, scratch + ".unpacked.glb"):
            if os.path.exists(leftover):
                os.remove(leftover)
        raise
    if settings.pop("ktx2_failed", False):
        findings.insert(0, _note("ktx2_failed"))
    settings.update(look=_look_settings(facts["look"]), environment=environment)
    report = {"settings": settings, "findings": findings, "baked": public_baked(baked), "budget": budget,
              "merged": merged, "export_ms": round((time.perf_counter() - started) * 1000.0, 1)}
    if source is not None:   # Report-only: nothing in the scene changes.
        report["preflight"] = {"findings": rules["findings"], "check_ms": rules["check_ms"]}
        report["_readback"] = {"source": source, "merged": bool(settings["merge"] and merged.get("objects"))}
    return report


def pending_export(window, scene, view_layer, path, request, preflight=False):
    """An explicit export of ``scene`` that waits for its World's picture without blocking (``web_export_wait``):
    call ``step`` on a timer. Each step finds the scene, its view layer and the window again; one gone, or a window
    that shows another scene now, ends the export with WebExportError. ``preflight``: as ``export``."""
    window_id = window.as_pointer() if window is not None else None
    scene_id, layer_name = scene.session_uid, view_layer.name
    asked = time.monotonic()

    def write(require):
        found = next((item for item in bpy.data.scenes if item.session_uid == scene_id), None)
        layer = found.view_layers.get(layer_name) if found is not None else None
        if layer is None:
            raise WebExportError(n_("The scene was removed while its World's picture was being made"))
        shown = None
        if window_id is not None:
            from . import live_windows
            shown = live_windows.find(bpy.context.window_manager, window_id, live=False)
            if shown is None or shown.scene != found:
                raise WebExportError(n_("The window was closed or shows another scene; export again"))
        return export(shown, found, layer, path, request, require_environment=require, preflight=preflight,
                      asked=asked)

    return PendingExport(write, scene.world.name if scene.world is not None else "")


def for_agent(report):
    """The report in English with no local paths, as a tool result carries it."""
    answer = dict(report)
    answer["findings"] = [web_compat.english(item) for item in report["findings"]]
    answer["note"] = ("The web result is Three.js rasterization: lighting, shadows and reflections differ from "
                      "Blender's render; the findings list what does not carry over. The file's scene extras "
                      "(angelo_look) hold the nearest three.js tone mapping, exposure and the World as an environment.")
    return answer
