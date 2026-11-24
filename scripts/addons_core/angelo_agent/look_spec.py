# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The lens and film look: its closed forms, its stages and its presets. Pure.

Contract: doc/angelo/camera_look.rst. A look is the camera's own settings (bokeh blades and ratio, focus breathing,
f-stop) plus one compositor node group (``look_nodes``) whose stages run in physical order (``ORDER``): lens
(distortion and lateral chromatic aberration, the Lens Distortion node), vignette (natural cos^4 falloff from the
real focal length and sensor, plus a mechanical amount), glare (bloom, streaks, ghosts), halation (highlights
scattered back through the film base, warm), film (exposure, white balance through colour management, contrast,
saturation, faded blacks) and grain (a share of the frame, so it does not change with the resolution). It works on
every render engine and device and in the viewport compositor; Cycles' Custom (OSL) camera is not used.

Presets are written from public photographic behaviour; they are named by look, never by a product, lens or film
stock. Model-facing refusals are English.
"""

import math

from . import read_only
from .refusal_codes import RefusalError

FOCUS_VARIABLE = "d"   # The breathing driver's variable: the camera's focus distance in scene units.
ORDER = ("lens", "vignette", "glare", "halation", "film", "grain")
# Each stage's values: (lowest, highest, default). A stage a look leaves out is not built.
STAGES = {
    "lens": {"distortion": (-0.3, 0.3, 0.0), "dispersion": (0.0, 0.1, 0.0)},
    "vignette": {"amount": (0.0, 1.0, 1.0), "mechanical": (0.0, 1.0, 0.0)},
    "glare": {"bloom": (0.0, 1.0, 0.0), "streaks": (0.0, 1.0, 0.0), "ghosts": (0.0, 1.0, 0.0),
              "threshold": (0.0, 10.0, 1.0)},
    "halation": {"amount": (0.0, 1.0, 0.0), "radius": (0.0, 0.05, 0.01), "threshold": (0.0, 10.0, 1.0)},
    "film": {"exposure": (-5.0, 5.0, 0.0), "temperature": (1800.0, 16000.0, 6500.0), "tint": (-150.0, 150.0, 10.0),
             "contrast": (-1.0, 1.0, 0.0), "saturation": (0.0, 2.0, 1.0), "fade": (0.0, 0.3, 0.0)},
    "grain": {"amount": (0.0, 0.3, 0.0), "size": (0.0002, 0.005, 0.001)},
}
# Camera values a look may set: (lowest, highest).
CAMERA = {"aperture_blades": (0, 16), "aperture_rotation": (-math.pi, math.pi), "aperture_ratio": (0.25, 4.0),
          "breathing": (-0.5, 0.5)}
PRESETS = {
    "CLEAN": {"stages": {}},
    "VINTAGE": {"camera": {"aperture_blades": 6, "breathing": 0.15},
                "stages": {"lens": {"distortion": -0.03, "dispersion": 0.015}, "vignette": {"mechanical": 0.3},
                           "glare": {"bloom": 0.35, "threshold": 0.8}, "halation": {"amount": 0.25},
                           "film": {"contrast": -0.1, "saturation": 0.85, "fade": 0.05, "temperature": 5600.0},
                           "grain": {"amount": 0.06}}},
    "ANAMORPHIC": {"camera": {"aperture_ratio": 2.0, "aperture_blades": 0, "breathing": 0.1},
                   "stages": {"lens": {"distortion": -0.05, "dispersion": 0.02}, "vignette": {"mechanical": 0.2},
                              "glare": {"streaks": 0.6, "bloom": 0.2, "threshold": 1.2},
                              "film": {"contrast": 0.1, "temperature": 6000.0}, "grain": {"amount": 0.03}}},
    "DOCUMENTARY": {"camera": {"breathing": 0.2},
                    "stages": {"vignette": {"mechanical": 0.15}, "halation": {"amount": 0.1},
                               "film": {"contrast": 0.15, "saturation": 0.9},
                               "grain": {"amount": 0.12, "size": 0.0018}}},
    "WARM_PORTRAIT": {"camera": {"aperture_blades": 9},
                      "stages": {"vignette": {"mechanical": 0.15}, "glare": {"bloom": 0.2, "threshold": 1.0},
                                 "halation": {"amount": 0.15},
                                 "film": {"temperature": 5200.0, "contrast": -0.05, "saturation": 0.95,
                                          "fade": 0.03},
                                 "grain": {"amount": 0.04, "size": 0.0008}}},
    "BLEACH_BYPASS": {"stages": {"vignette": {"mechanical": 0.2},
                                 "film": {"contrast": 0.45, "saturation": 0.45}, "grain": {"amount": 0.05}}},
    "NOIR": {"stages": {"vignette": {"mechanical": 0.4}, "glare": {"bloom": 0.25, "threshold": 1.0},
                        "film": {"contrast": 0.4, "saturation": 0.0}, "grain": {"amount": 0.08}}},
}


class LookError(RefusalError):
    partial_changes_possible = False


def _error(message):
    return LookError(message, code="VALIDATION")


def natural_vignette(x, y, focal_mm, sensor_width_mm, aspect, amount=1.0):
    """The light reaching a point of the frame (``x``, ``y`` from -1 to 1, the sensor fitted horizontally), relative
    to the centre: cos^4 of its angle from the optical axis, blended toward 1 by ``1 - amount``."""
    half_width = sensor_width_mm * 0.5
    off_axis = math.hypot(x * half_width, y * half_width / aspect)
    tangent_squared = (off_axis / focal_mm) ** 2
    falloff = 1.0 / (1.0 + tangent_squared) ** 2
    return 1.0 - amount * (1.0 - falloff)


def breathing_lens(focal_mm, breathing, focus_mm):
    """The focal length a lens that breathes by ``breathing`` shows when focused at ``focus_mm``: the thin-lens
    magnification f / (d - f) times the coefficient, added to 1 (positive: the view narrows as focus comes close)."""
    return focal_mm * (1.0 + breathing * focal_mm / max(focus_mm - focal_mm, 1.0))


def breathing_expression(focal_mm, breathing, mm_per_unit):
    """``breathing_lens`` as a driver expression Blender evaluates without Python (simple expressions: arithmetic
    and its built-in functions), with the focus distance in scene units as ``FOCUS_VARIABLE``."""
    f, k, unit = _decimal(focal_mm), _decimal(breathing), _decimal(mm_per_unit)
    return "%s * (1 + %s * %s / clamp(%s * %s - %s, 1, 1000000000000))" % (f, k, f, FOCUS_VARIABLE, unit, f)


def _decimal(value):
    """A number as plain decimal digits (no exponent, which an expression would read as a name)."""
    text = ("%.12f" % float(value)).rstrip("0").rstrip(".")
    return "(%s)" % text if text.startswith("-") else (text or "0")


def stops(reference, setting):
    """Exposure of ``setting`` relative to ``reference`` in stops (each {f_stop, shutter seconds, iso}): positive is
    brighter."""
    def value(item):
        return item["f_stop"] ** 2 / item["shutter"]
    return math.log2(value(reference) / value(setting)) + math.log2(setting["iso"] / reference["iso"])


# A camera setting for exposure in stops: (lowest, highest). Shutter in seconds.
EXPOSURE = {"f_stop": (0.5, 128.0), "shutter": (1.0 / 64000.0, 3600.0), "iso": (1.0, 1000000.0)}


def _setting(value, what, camera_f_stop=None):
    if not isinstance(value, dict):
        raise _error("exposure.%s is an object of f_stop, shutter (seconds) and iso" % what)
    unknown = set(value) - set(EXPOSURE)
    if unknown:
        raise _error("exposure.%s takes f_stop, shutter and iso (not %s)" % (what, ", ".join(sorted(unknown))))
    out = dict(value)
    if camera_f_stop is not None and "f_stop" not in out:
        out["f_stop"] = camera_f_stop   # The camera's own: exposure agrees with its depth of field.
    for key, (low, high) in EXPOSURE.items():
        if key not in out:
            raise _error("exposure.%s needs %s" % (what, key))
        number = out[key]
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not low <= number <= high:
            source = " (the camera's)" if key == "f_stop" and "f_stop" not in value else ""
            raise _error("exposure.%s.%s%s must be a number from %g to %g" % (what, key, source, low, high))
        out[key] = float(number)
    return out


def exposure_stops(exposure, camera_f_stop):
    """The stops ``exposure`` asks for: {"reference": {f_stop, shutter, iso}, "setting": {shutter, iso, f_stop?}},
    the reference being the settings the scene's light is right for (film exposure 0) and the setting the camera's,
    with the camera's own f-stop (``camera_f_stop``) unless it names one. Positive is brighter (``stops``)."""
    if not isinstance(exposure, dict):
        raise _error("exposure is an object: reference and setting")
    unknown = set(exposure) - {"reference", "setting"}
    if unknown:
        raise _error("exposure takes reference and setting (not %s)" % ", ".join(sorted(unknown)))
    for key in ("reference", "setting"):
        if key not in exposure:
            raise _error("exposure needs %s (f_stop, shutter in seconds, iso)" % key)
    return stops(_setting(exposure["reference"], "reference"), _setting(exposure["setting"], "setting", camera_f_stop))


def grain_pixels(size, height_px):
    """A grain size given as a share of the frame's height, in pixels of a render ``height_px`` tall."""
    return size * height_px


def resolve(preset, overrides):
    """The stages a look builds: ``preset``'s, each filled with its defaults, with ``overrides`` ({stage: {value}})
    checked and applied on top."""
    if preset not in PRESETS:
        raise _error("preset must be one of: " + ", ".join(PRESETS))
    if not isinstance(overrides, dict):
        raise _error("stages is an object of stages")
    unknown = set(overrides) - set(STAGES)
    if unknown:
        raise _error("stages are: " + ", ".join(ORDER) + " (not " + ", ".join(sorted(unknown)) + ")")
    out = {}
    chosen = read_only.thawed(PRESETS[preset]["stages"])
    for stage, values in overrides.items():
        if not isinstance(values, dict) or set(values) - set(STAGES[stage]):
            raise _error("%s takes: %s" % (stage, ", ".join(STAGES[stage])))
        chosen.setdefault(stage, {}).update(values)
    for stage in ORDER:
        if stage not in chosen:
            continue
        values = {key: default for key, (_low, _high, default) in STAGES[stage].items()}
        for key, value in chosen[stage].items():
            low, high, _default = STAGES[stage][key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
                raise _error("%s.%s must be a number from %g to %g" % (stage, key, low, high))
            values[key] = float(value)
        out[stage] = values
    return out


# The validators above read these as registered, read only (read_only.seal).
read_only.seal(globals(), 'STAGES', 'CAMERA', 'PRESETS', 'EXPOSURE')
