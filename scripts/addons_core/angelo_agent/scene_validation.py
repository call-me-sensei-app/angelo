# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, read-only assertions against explicitly resolved scene targets.

``verify(context, checks)`` accepts an execution context with ``targets`` (or
``objects``) mapping aliases to Blender objects and optionally ``scene_objects``.
An object exposing ``resolve_target(alias)`` can supply its own guarded resolver.
Public tools must bind handles/revisions before building this context; this
module deliberately does not resolve arbitrary object names. A staged program
must supply its staged scene-object overlay when asserting object counts.

No operation, depsgraph update, frame switch, mesh evaluation, file access or
script evaluation takes place here. Mesh metrics describe BASE data, not the
potentially unbounded output of modifiers. Animation assertions describe direct
action-channel samples; NLA, drivers and transform constraints are inconclusive.
Only aggregate metrics leave this module, never vertex/face/UV coordinate lists.
Bounded measurements live in validation_reader; closed predicates live in
validation_checks. This module owns binding, observation and JSON reporting.
"""

import math
from dataclasses import dataclass, field

from .validation_spec import (
    CHECKS_SCHEMA, CHECK_SCHEMA, CHECK_KINDS, MODIFIER_PROPERTIES, MAX_OBJECTS,
    MAX_VERTICES, MAX_MESH_ELEMENTS, MAX_TOTAL_ELEMENTS, MAX_MODIFIERS,
    MAX_MATERIALS, MAX_UV_LAYERS, MAX_BONES, MAX_ANIMATION_CURVES,
    MAX_KEYFRAMES, validate_checks,
)

from .validation_checks import evaluate_check as _evaluate
from .validation_reader import (
    Inconclusive, Unobservable, ValidationReader as _Reader, context_value as _get,
    number_vector as _numbers, resolve_target as _target,
)


@dataclass
class VerificationContext:
    """Internal resolved context. Binding freshness is owned by its caller."""
    targets: dict
    scene_objects: object = None
    bindings: dict = field(default_factory=dict)
    # Scene handle -> the image a staged World will use (staged Worlds hold none).
    environment_images: dict = field(default_factory=dict)
    # The bound view layer; visibility holds a stage's intended hide states (None when live).
    view_layer: object = None
    visibility: object = None
    # Target -> the name commit gives a staged copy of an existing object (a stage keeps private names).
    logical_names: dict = field(default_factory=dict)
    # The depsgraph that evaluates the targets: an object it skips is measured from base data (validation_reader).
    depsgraph: object = None
    # ID -> the name a check reads for it: the name commit gives a staged copy (never its private name, which
    # would not fit a check's 64-character name fields). None: an ID's own name (validation_reader.name_of).
    shown_name: object = None

    def resolve_target(self, target):
        return self.targets.get(target)


def _binding(context, alias):
    bindings = _get(context, "bindings", {})
    value = bindings.get(alias, {}) if isinstance(bindings, dict) else {}
    return {k: value[k] for k in ("handle", "revision") if isinstance(value.get(k), str) and len(value[k]) <= 128}


def _safe(values):
    if isinstance(values, dict):
        return {str(k)[:128]: _safe(v) for k, v in values.items()}
    if isinstance(values, (list, tuple)):
        return [_safe(v) for v in values]
    if isinstance(values, float) and not math.isfinite(values):
        return None
    return values


def verify(context, checks):
    """Read-only JSON report; success means every requested assertion was proven."""
    checked = validate_checks(checks)
    reader, results = _Reader(context, max_elements=MAX_TOTAL_ELEMENTS), []
    for index, check in enumerate(checked):
        item = {"index": index, "kind": check["kind"]}
        if "target" in check:
            item.update(target=check["target"], **_binding(context, check["target"]))
        try:
            passed, observed = _evaluate(reader, check)
            item.update(status="pass" if passed else "fail", passed=bool(passed), observed=_safe(observed))
        except Unobservable as error:  # Certifies nothing; the run is partial (program_jobs).
            item.update(status="unobservable", passed=False, reason=str(error)[:240])
        except (Inconclusive, AttributeError, TypeError, ValueError, ReferenceError, RuntimeError) as error:
            item.update(status="inconclusive", passed=False, reason=str(error)[:240] or "Unsupported read-only observation")
        results.append(item)
    statuses = {r["status"] for r in results}
    status = ("fail" if "fail" in statuses else "inconclusive" if "inconclusive" in statuses
              else "unobservable" if "unobservable" in statuses else "pass")
    return {"success": status == "pass", "passed": status == "pass", "status": status, "checks": results,
            "truncated": "inconclusive" in statuses, "read_only": True, "scope": "closed_base_data_predicates"}


def observe(context, target):
    """Bounded typed target summary for an already handle/revision-bound context."""
    if type(target) is not str or not 1 <= len(target) <= 128 or any(ord(c) < 32 for c in target):
        raise ValueError("Choose an explicit bounded target alias/handle")
    obj = _target(context, target)
    if obj is None:
        return {"success": False, "status": "fail", "target": target, "exists": False, "read_only": True}
    result = {"success": True, "status": "pass", "target": target, **_binding(context, target),
              "name": obj.name[:128], "type": obj.type, "read_only": True,
              "transforms": {key: _safe(_numbers(getattr(obj, key), 3)) for key in ("location", "rotation_euler", "scale")},
              "parent_name": obj.parent.name[:128] if getattr(obj, "parent", None) else None,
              "constraints_count": len(getattr(obj, "constraints", ())),
              "scope": "BASE_DATA_NOT_EVALUATED_GEOMETRY", "inconclusive_fields": []}
    reader = _Reader(context, max_elements=MAX_TOTAL_ELEMENTS)
    for field_name, callback in (
            ("effective_linear_transform", lambda: reader.linear_transform(obj)),
            ("mesh", lambda: _mesh_observation(reader, obj)),
            ("materials", lambda: [m.name[:64] if m else None for m in reader.materials(obj)]),
            ("modifiers", lambda: _modifier_observation(obj))):
        if field_name == "mesh" and obj.type != "MESH":
            continue
        try:
            result[field_name] = _safe(callback())
        except (Inconclusive, AttributeError, TypeError, ValueError, ReferenceError, RuntimeError) as error:
            result["inconclusive_fields"].append({"field": field_name, "reason": str(error)[:240]})
    if result["inconclusive_fields"]:
        result.update(success=False, status="inconclusive")
    return result


def _mesh_observation(reader, obj):
    mesh = reader.mesh(obj)
    layers = obj.data.uv_layers
    if len(layers) > MAX_UV_LAYERS:
        raise Inconclusive("UV layer count exceeds observation limits")
    return {"vertices": mesh["vertices"], "edges": mesh["edge_count"], "faces": mesh["face_count"],
            "finite": mesh["finite"], "invalid_indices": mesh["invalid_indices"],
            "dimensions_world": reader.dimensions(obj, "WORLD"),
            "uv_layers": [layer.name[:64] for layer in layers], "topology": reader.topology(mesh)}


def _modifier_observation(obj):
    if len(obj.modifiers) > MAX_MODIFIERS:
        raise Inconclusive("Modifier count exceeds observation limits")
    result = []
    for modifier in obj.modifiers:
        values = {}
        for key, schema in MODIFIER_PROPERTIES.get(modifier.type, {}).items():
            if hasattr(modifier, key):
                value = getattr(modifier, key)
                values[key] = list(value) if schema.get("type") == "array" else value
        result.append({"name": modifier.name[:64], "type": modifier.type, "values": values,
                       "supported_properties": modifier.type in MODIFIER_PROPERTIES})
    return result


def execute(window, name, arguments):
    """Public read-only tool adapter; exact document handles bind every target."""
    from .validation_spec import validate_validation
    from .scene_queries import SceneQueryError, object_revision, resolve_object
    from .scene_settings import scene_handle, settings_revision
    arguments = validate_validation(name, arguments)
    targets, bindings = {}, {}
    scene_uid = getattr(getattr(window, "scene", None), "session_uid", None)

    def is_scene(handle):
        # The bound scene's own handle (same form as object handles).
        return scene_uid is not None and handle.rsplit(":", 1)[1] == str(scene_uid) and handle == scene_handle(window.scene)

    def revision_of(handle, target):
        # The bound scene binds by its settings revision, objects by theirs.
        return settings_revision(target) if is_scene(handle) else object_revision(window, target)

    for entry in arguments["read_set"]:
        handle = entry["handle"]
        target = window.scene if is_scene(handle) else resolve_object(window, handle)
        try:
            revision = revision_of(handle, target)
        except SceneQueryError as error:
            return _binding_inconclusive(arguments["checks"], str(error))
        if revision != entry["revision"]:
            raise ValueError("Verification target changed since observation; inspect its fresh revision")
        targets[handle] = target
        bindings[handle] = dict(entry)
    context = VerificationContext(targets, scene_objects=window.scene.objects, bindings=bindings,
                                  view_layer=getattr(window, "view_layer", None),
                                  depsgraph=getattr(getattr(window, "view_layer", None), "depsgraph", None))
    result = verify(context, arguments["checks"])
    # No cached observation is trusted as a replacement for a fresh binding.
    try:
        changed = any(revision_of(handle, target) != bindings[handle]["revision"] for handle, target in targets.items())
    except SceneQueryError as error:
        return _binding_inconclusive(arguments["checks"], str(error))
    if changed:
        raise ValueError("Verification target changed while it was being read; inspect again")
    return result


def _binding_inconclusive(checks, reason):
    """Without exact bounded freshness, even a plausible metric cannot pass."""
    results = [{"index": index, "kind": check["kind"], "status": "inconclusive", "passed": False,
                "reason": ("Exact bound revision unavailable: " + reason)[:240],
                **({"target": check["target"]} if "target" in check else {})}
               for index, check in enumerate(checks)]
    return {"success": False, "passed": False, "status": "inconclusive", "checks": results,
            "truncated": True, "read_only": True, "binding_verified": False,
            "scope": "closed_base_data_predicates"}
