# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine-contract preflight: which exporter profile a target uses, the check, and the re-import after an
export. Main thread.

* ``check(window, target, fmt, ...)``: the rules (``engine_rules``) on the facts (``engine_facts``) of the scene as
  that target's profile would export it. Read-only; nothing is written. The agent's ``angelo_engine_check``, the
  engine-link agent tool and the fix (``engine_check_host``) call it.
* ``verify(path, fmt, objects, window)``: the re-import comparison (``engine_reimport``) of a file just written.
  ``angelo_web_export`` runs it on its .glb; the engine link's write-back child runs it on the asset it wrote
  (``after_writeback``), and the result is kept beside the asset's working file (``link_report``).

Every result is plain data in English with object, image and action names and never a local path.
doc/angelo/engine_preflight.rst is the contract.
"""

import json
import os
import secrets
import time
from pathlib import Path

from . import engine_rules

TARGET_FOR_ENGINE = {"godot": "GODOT", "unity": "UNITY", "unreal": "UNREAL"}
DEFAULT_FORMAT = {"GODOT": "GLB", "UNITY": "FBX", "UNREAL": "GLB", "WEB": "GLB"}
# Angelo's web export (web_export._operator_settings) as the rules read it.
WEB_PROFILE = {"use_visible": True, "export_apply": True, "export_gpu_instances": True, "export_extras": False}
# Blender's FBX exporter defaults that the rules read (io_scene_fbx 5.2), for an FBX that no adapter writes.
FBX_DEFAULTS = {"use_mesh_modifiers": True, "use_custom_props": False, "add_leaf_bones": True,
                "bake_anim_use_all_actions": True}
REPORT_SUFFIX = ".preflight.json"


def resolve_format(target, fmt="AUTO"):
    fmt = DEFAULT_FORMAT[target] if fmt in (None, "AUTO") else fmt
    if fmt not in engine_rules.TARGET_FORMATS[target]:
        raise ValueError("%s takes %s" % (engine_rules.DISPLAY[target],
                                          " or ".join(engine_rules.TARGET_FORMATS[target])))
    return fmt


def profile(target, fmt, params=None):
    """The exporter options the target's import uses: the engine adapter's, which mirror the engine's own Blender
    import (Godot's import settings ``params``, Unity's FBX script), the web export's, or Blender's FBX defaults."""
    from .engine_link_assets import GLTF_DURABLE
    if target == "WEB":
        return dict(WEB_PROFILE)
    if target == "GODOT":
        from .godot_link_assets import godot_options
        return godot_options(params or {})
    if target == "UNITY" and fmt == "FBX":
        from .unity_link_assets import UNITY_FBX
        return dict(UNITY_FBX)
    return dict(FBX_DEFAULTS) if fmt == "FBX" else dict(GLTF_DURABLE)


def _counts(findings):
    counts = {severity: 0 for severity in engine_rules.SEVERITIES}
    for finding in findings:
        counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
    counts["fixable"] = sum(1 for finding in findings if finding.get("fixable"))
    return counts


def gather(window, scene, view_layer, fmt, options, selection_only=False):
    import bpy
    from . import engine_facts
    override = {} if window is None else {"window": window}
    with bpy.context.temp_override(**override):
        depsgraph = bpy.context.evaluated_depsgraph_get()
    return engine_facts.gather(scene, view_layer, fmt, options, selection_only, depsgraph)


def run(window, target, fmt="AUTO", engine_version=None, selection_only=False, params=None, options=None):
    """(report, facts, exporter options) of the preflight; ``check`` without the facts. ``options``: the exporter
    options the export will use, when the caller knows them (the write-back child), else the target's profile."""
    import bpy
    started = time.perf_counter()
    fmt = resolve_format(target, fmt)
    options = dict(options) if options is not None else profile(target, fmt, params)
    scene = window.scene if window is not None else bpy.context.scene
    view_layer = window.view_layer if window is not None else bpy.context.view_layer
    facts = gather(window, scene, view_layer, fmt, options, selection_only)
    findings = engine_rules.evaluate(facts, target, fmt, options, engine_version)
    report = {"target": target, "format": fmt, "engine_version": engine_version, "findings": findings,
              "counts": _counts(findings), "exported_objects": len(facts["exported"])}
    if engine_version and not engine_rules.in_range(target, engine_version):
        low, high = engine_rules.VERSIONS[target]
        report["version_note"] = ("These rules were read from %s %s to %s; %s may differ" % (
            engine_rules.DISPLAY[target], low, high, engine_version))
    report["check_ms"] = round((time.perf_counter() - started) * 1000.0, 1)
    return report, facts, options


def check(window, target, fmt="AUTO", engine_version=None, selection_only=False, params=None):
    """The preflight of the window's scene (``window`` None: the context's) for ``target``; read-only.

    Returns ``{"target", "format", "engine_version", "findings", "counts", "exported_objects", "check_ms"}``, plus
    ``version_note`` when ``engine_version`` is outside the versions the rules were read from (they still run)."""
    return run(window, target, fmt, engine_version, selection_only, params)[0]


def snapshot_for(window, objects, baked=()):
    """The re-import comparison's description of ``objects`` (call before the export writes), each with what the
    export writes for it: ``baked``'s ``[(object, material)]`` replaces its slots (``export_baked.plan``'s "used")."""
    import bpy
    from . import engine_reimport
    override = {} if window is None else {"window": window}
    with bpy.context.temp_override(**override):
        source = engine_reimport.snapshot(objects, bpy.context.scene, bpy.context.evaluated_depsgraph_get())
    return engine_reimport.with_baked(source, baked)


def verify(path, fmt, source, window=None, merged=False):
    """The re-import comparison of the file at ``path`` against ``source`` (``snapshot_for``); ``merged`` when the
    export joined objects (``engine_reimport.compare``)."""
    from . import engine_reimport
    return engine_reimport.check(path, fmt, source, window, merged)


def report_path(asset):
    """Where an engine-link asset's last save report lives: ``<project>/.angelo/<relative path>.preflight.json``."""
    from .engine_link_assets import WORKING_FOLDER
    parts = asset.relative.split("/")
    return str(Path(asset.project, WORKING_FOLDER, *parts[:-1], parts[-1] + REPORT_SUFFIX))


def write_report(path, report):
    """Write ``report`` as JSON at ``path`` through a new private file, never through a link."""
    if os.path.islink(path):
        raise OSError("The preflight report is a link")
    temporary = "%s.%s.tmp" % (path, secrets.token_hex(4))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(temporary, flags, 0o644)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=1, sort_keys=True)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.remove(temporary)
        raise


def link_report(asset):
    """The last engine-link save's preflight of ``asset`` (an ``engine_link_assets.Asset``), or None before one."""
    path = report_path(asset)
    if os.path.islink(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            report = json.load(handle)
    except (OSError, ValueError):
        return None
    return report if isinstance(report, dict) else None


def after_writeback(target_file, exporter, options, engine, report_file, scene_version=None, digest=False,
                    blend_identity=None, save_key=None, baked=()):
    """In the preflight child (``preflight_child``), on the saved file: the check of its scene and the re-import of
    ``target_file``, written to ``report_file`` as the report of the save at ``scene_version``; ``digest``: with the
    SHA-256 of ``target_file`` (a copy of the asset the write-back wrote) as ``target_sha256``; ``blend_identity``: the
    saved .blend's ``export_snapshot.identity`` when the save was made; ``save_key``: the caller's key for the save,
    written as given; ``baked``: what the file carries for those objects (``snapshot_for``). Report-only: the scene
    and the asset are never changed."""
    import bpy
    target = TARGET_FOR_ENGINE[engine]
    fmt = "FBX" if exporter == "fbx" else "GLB"
    started = time.perf_counter()
    report = run(None, target, fmt, options=options)[0]
    from . import engine_facts
    scene, view_layer = bpy.context.scene, bpy.context.view_layer
    objects = engine_facts.exported_objects(scene, view_layer, fmt, options)
    report["reimport"] = verify(target_file, fmt, snapshot_for(None, objects, baked))
    if digest:
        import hashlib
        with open(target_file, "rb") as handle:
            report["target_sha256"] = hashlib.file_digest(handle, "sha256").hexdigest()
    report.update(status="done", scene_version=scene_version, blend_identity=blend_identity, save_key=save_key,
                  saved_at=time.time(), preflight_ms=round((time.perf_counter() - started) * 1000.0, 1))
    write_report(report_file, report)
    return report


def blend_import_profile(engine, params):
    """(exporter, options) an engine itself uses to import a .blend (its Blender import), or None: Godot runs the glTF
    exporter with its import settings, Unity runs Unity-BlenderToFBX.py."""
    if engine == "godot":
        from .godot_link_assets import godot_options
        return "gltf", godot_options(params or {})
    if engine == "unity":
        from .unity_link_assets import UNITY_FBX
        return "fbx", dict(UNITY_FBX)
    return None


def _export_to(path, exporter, options):
    import addon_utils
    import bpy
    options = dict(options)
    if exporter == "fbx":
        addon_utils.enable("io_scene_fbx", default_set=False, persistent=False)
        if "object_types" in options:
            options["object_types"] = set(options["object_types"])
        return bpy.ops.export_scene.fbx(filepath=path, check_existing=False, use_selection=False, **options)
    addon_utils.enable("io_scene_gltf2", default_set=False, persistent=False)
    return bpy.ops.export_scene.gltf(filepath=path, check_existing=False, export_format="GLB",
                                     will_save_settings=False, use_active_scene=True, use_selection=False, **options)


def preflight_child(spec):
    """In the background Angelo ``engine_preflight_link`` starts on a saved file: read back ``spec["file"]`` (or, for
    a .blend asset, a private export with ``spec``'s exporter) and write the report to ``spec["report"]``."""
    import shutil
    import bpy
    from . import export_snapshot, private_roots
    expected = spec.get("blend_identity")
    if expected is not None and export_snapshot.identity(bpy.data.filepath) != expected:
        return None   # Saved again since: that save's preflight follows and reports it.
    version = spec.get("scene_version")
    if spec.get("file"):
        return after_writeback(spec["file"], spec["exporter"], spec["options"], spec["engine"], spec["report"],
                               version, digest=True, blend_identity=expected, save_key=spec.get("save_key"),
                               baked=spec.get("baked", ()))
    folder = str(private_roots.allocate("angelo-preflight-"))
    try:
        written = os.path.join(folder, "asset" + (".fbx" if spec["exporter"] == "fbx" else ".glb"))
        if "FINISHED" not in _export_to(written, spec["exporter"], spec["options"]):
            raise RuntimeError("the export for the engine didn't finish")
        return after_writeback(written, spec["exporter"], spec["options"], spec["engine"], spec["report"], version,
                               blend_identity=expected, save_key=spec.get("save_key"))
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def readback_child(spec):
    """In the background Angelo ``web_readback`` starts: read ``spec["path"]`` back in an empty scene, compare it with
    ``spec["source"]`` (the scene's description before the export) and write the result to ``spec["result"]``."""
    import bpy
    from . import engine_reimport, export_snapshot
    bpy.ops.wm.read_factory_settings(use_empty=True)
    expected = spec.get("identity")
    if expected is not None and export_snapshot.identity(spec["path"]) != expected:
        result = {"error": "the export's copy changed before its read-back"}
    else:
        result = engine_reimport.check(spec["path"], "GLB", spec["source"], None, spec.get("merged", False),
                                       spec.get("needles") or {})
        if expected is not None and export_snapshot.identity(spec["path"]) != expected:
            result = {"error": "the export's copy changed during its read-back"}
    write_report(spec["result"], result)
    return result


NOTE = ("Findings grouped by rule with examples; fixable names the typed fix angelo_engine_fix applies; more counts "
        "the ones not shown, which angelo_engine_check with code lists.")


def for_agent(report, code=None):
    """``report`` as a tool result (English with names only): its findings grouped by rule
    (``engine_findings.summarize``), or with ``code`` that rule's findings in full (``engine_findings.page``)."""
    from . import engine_findings
    answer = {key: value for key, value in report.items() if key != "findings"}
    if code is not None:
        answer.update(engine_findings.page(report["findings"], code))
        return answer
    answer.update(engine_findings.summarize(report["findings"]))
    if report["findings"]:
        answer["note"] = NOTE
    return answer
