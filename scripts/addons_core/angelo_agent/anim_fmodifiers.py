# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``fcurve_modifier``: F-Curve modifiers of one channel on a private staged Action, and their revision.

Modifiers are added by type, then addressed by their exact name on the
channel; settings are RNA paths on the modifier (``rna_rules`` via
``generic_settings``), read back after writing. Envelope modifiers are not
added (their control points are a collection no settings path creates) and
existing Envelopes are not configured. Evaluation is bounded through each
setting that multiplies its per-frame cost: Noise octaves (``depth``), the
Smooth filter width and the Generator polynomial order (``MAX_COST``).

``revision`` digests every modifier's writable settings (and an Envelope's
control points), so a manual modifier edit makes an observed revision stale.
"""

from .anim_channels import AnimationError, edit_curves

MAX_MODIFIERS = 8
# Per-frame evaluation cost bounds: settings that multiply a modifier's work.
MAX_COST = {"depth": 16, "filter_width": 64, "poly_order": 16}


def _named(curve, name):
    modifier = next((item for item in curve.modifiers if item.name == name), None)
    if modifier is None:
        names = ", ".join(repr(item.name[:64]) for item in curve.modifiers)
        raise AnimationError("FMODIFIER_NOT_FOUND", "channel %s[%d] has no modifier named %r; its modifiers: %s" % (
            curve.data_path[:128], curve.array_index, name[:64], names or "none"))
    return modifier


def _write(context, obj, modifier, settings):
    from . import generic_settings
    if modifier.type == "ENVELOPE":
        raise AnimationError("FMODIFIER_UNSUPPORTED", "Envelope control points need reviewed Python")
    coefficients = settings.get("coefficients")
    rest = {path: value for path, value in settings.items() if path != "coefficients"}
    generic_settings.apply(context, obj, modifier, rest, "F-Curve modifier")
    if coefficients is not None:
        if modifier.type != "GENERATOR" or len(coefficients) != modifier.poly_order + 1 or \
                not all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in coefficients):
            raise AnimationError("RNA_ARRAY_LENGTH", "a Generator takes poly_order + 1 numeric coefficients")
        modifier.coefficients = coefficients
        if any(abs(a - b) > 1e-5 * max(1, abs(b)) for a, b in zip(modifier.coefficients, coefficients)):
            raise AnimationError("VALUE_NOT_RETAINED", "the Generator coefficients were not retained")
    for path, limit in MAX_COST.items():
        if getattr(modifier, path, 0) > limit:
            raise AnimationError("FMODIFIER_COST", "%s above %d multiplies per-frame evaluation work" % (path, limit))


def apply(context, obj, operation):
    """fcurve_modifier: add, configure or remove one modifier on each selected channel."""
    from .action_context import _readback
    params = operation["parameters"]
    _owner, curves = edit_curves(context, obj, operation["target"], params)
    rows = []
    for curve in curves:
        if params["mode"] == "ADD":
            if params["name"] is not None and any(item.name == params["name"] for item in curve.modifiers):
                raise AnimationError("NAME_TAKEN", "the channel already has a modifier named %r" % params["name"][:64])
            if len(curve.modifiers) >= MAX_MODIFIERS:
                raise AnimationError("TOO_MANY_FMODIFIERS", "at most %d modifiers per channel" % MAX_MODIFIERS)
            modifier = curve.modifiers.new(params["type"])
            modifier.show_expanded = False
            if params["name"] is not None:
                modifier.name = params["name"]
                if modifier.name != params["name"]:
                    raise AnimationError("NAME_TAKEN", "the modifier name was changed to %r" % modifier.name[:64],
                                         refusal_code="INTERNAL_ERROR")
            _write(context, obj, modifier, params["settings"])
        elif params["mode"] == "CONFIGURE":
            modifier = _named(curve, params["name"])
            _write(context, obj, modifier, params["settings"])
        else:
            modifier = _named(curve, params["name"])
            curve.modifiers.remove(modifier)
            modifier = None
        curve.update()
        rows.append([curve.data_path[:128], curve.array_index, [[item.name[:64], item.type] for item in curve.modifiers]])
    return _readback(obj, operation, channels=rows[:16])


def revision(curve, reference_of):
    """Exact JSON state of a channel's modifiers: type, name and every writable setting, in order."""
    from .generic_settings import values
    result = []
    for modifier in curve.modifiers:
        row = {"type": modifier.type, "name": modifier.name, "settings": values(modifier, reference_of)}
        if modifier.type == "GENERATOR":
            row["coefficients"] = [round(float(value), 7) for value in modifier.coefficients]
        if modifier.type == "ENVELOPE":
            row["control_points"] = [[point.frame, point.min, point.max] for point in modifier.control_points]
        result.append(row)
    return result
