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
"""

import os
import shutil
import subprocess
import tempfile
import time

import bpy

from . import web_budget, web_compat, web_facts
from .ui_text import n_

EXPORT_ID = "angelo_web"
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
    """The glTF exporter's user extension (``angelo_agent.glTF2ExportUserExtension``): only Angelo's web exports."""

    def gather_material_hook(self, gltf2_material, blender_material, export_settings):
        if export_settings.get("gltf_export_id") != EXPORT_ID or blender_material is None:
            return
        from . import web_shader
        shader = web_shader.extras(blender_material)   # Read by the preview page; other tools ignore extras.
        if shader is not None:
            gltf2_material.extras = dict(gltf2_material.extras or {}, **shader)

    def gather_scene_hook(self, gltf2_scene, blender_scene, export_settings):
        if export_settings.get("gltf_export_id") != EXPORT_ID or not _ACTIVE.get("merge"):
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
            "selection_only": request["selection_only"]}, found


def _note(code, **params):
    message, fix = UNAVAILABLE[code]
    return {"code": code, "severity": web_compat.WARN if code != "ktx2" else web_compat.INFO, "subject": "",
            "message": message, "fix": fix, "params": dict(params, subject="")}


def static_names(objects):
    """Names of objects with no animation, drivers or constraints on them or any parent (safe to merge)."""
    def animated(block):
        data = getattr(block, "animation_data", None)
        return bool(data is not None and (data.action is not None or len(data.drivers) or len(data.nla_tracks)))

    names = set()
    for obj in objects:
        node, moving = obj, False
        while node is not None and not moving:
            moving = animated(node) or bool(node.constraints) or node.parent_type != "OBJECT"
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


def _write(window, scene, view_layer, path, settings):
    """Run the exporter into ``path``; returns the merge result. Raises WebExportError."""
    objects = web_facts.exported_objects(scene, view_layer, settings["selection_only"])
    _ACTIVE.clear()
    _ACTIVE.update(merge=settings["merge"], static=static_names(objects).__contains__, merged={"objects": 0, "groups": 0})
    try:
        textures = settings["textures"]
        target = path if textures != "KTX2" else path + ".unpacked.glb"
        with bpy.context.temp_override(window=window):
            result = bpy.ops.export_scene.gltf(**_operator_settings(target, settings, textures))
        if "FINISHED" not in result or not os.path.isfile(target):
            raise WebExportError(n_("Blender's glTF exporter didn't write the file; see the Info log"))
        if textures == "KTX2":
            packed = _run_gltfpack(gltfpack_path(), target, path, settings)
            if packed:
                os.remove(target)
            else:   # KTX2 failed: export again with WebP, so the file is still web-ready.
                os.remove(target)
                settings.update(textures="WEBP", ktx2_failed=True)
                with bpy.context.temp_override(window=window):
                    bpy.ops.export_scene.gltf(**_operator_settings(path, settings, "WEBP"))
        return dict(_ACTIVE["merged"])
    finally:
        _ACTIVE.clear()


def _draw_call_estimate(objects):
    return sum(max(1, len([slot for slot in obj.material_slots if slot.material is not None]))
               for obj in objects if obj.type in {"MESH", "CURVE", "SURFACE", "FONT", "META"})


def check(window, scene, view_layer, request):
    """What an export of ``scene`` would lose and its estimated budget; writes nothing."""
    settings, notes = resolve(request)
    with bpy.context.temp_override(window=window):
        depsgraph = bpy.context.evaluated_depsgraph_get()
    facts = web_facts.gather(scene, view_layer, settings["selection_only"], depsgraph)
    objects = web_facts.exported_objects(scene, view_layer, settings["selection_only"])
    return {"settings": settings, "findings": notes + web_compat.findings(facts),
            "estimate": {"objects": len(objects), "triangles": facts["triangles"], "materials": len(facts["materials"]),
                         "textures": len(facts["images"]), "draw_calls_before_merge": _draw_call_estimate(objects)}}


def export(window, scene, view_layer, path, request):
    """Write a web-ready .glb at ``path`` (a file that doesn't exist yet) and report it."""
    started = time.perf_counter()
    report = check(window, scene, view_layer, request)
    settings = dict(report["settings"])
    folder = os.path.dirname(path) or "."
    descriptor, scratch = tempfile.mkstemp(prefix=".angelo-web-", suffix=".glb", dir=folder)
    os.close(descriptor)
    try:
        merged = _write(window, scene, view_layer, scratch, settings)
        budget = web_budget.measure(scratch)
        os.replace(scratch, path)
    except BaseException:
        for leftover in (scratch, scratch + ".unpacked.glb"):
            if os.path.exists(leftover):
                os.remove(leftover)
        raise
    if settings.pop("ktx2_failed", False):
        report["findings"].insert(0, _note("ktx2_failed"))
    report.pop("estimate")
    report.update(settings=settings, budget=budget, merged=merged,
                  export_ms=round((time.perf_counter() - started) * 1000.0, 1))
    return report


def for_agent(report):
    """The report in English with no local paths, as a tool result carries it."""
    answer = dict(report)
    answer["findings"] = [web_compat.english(item) for item in report["findings"]]
    answer["note"] = ("The web result is Three.js rasterization: lighting, shadows and reflections differ from "
                      "Blender's render; the findings list what does not carry over.")
    return answer
