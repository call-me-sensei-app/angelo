# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which three.js tone mapping shows a Blender view transform best, chosen by measurement. NumPy only.

Blender turns scene-linear light into display values with its view transform (AgX, Filmic, Standard, Khronos PBR
Neutral, ACES, a studio's own OpenColorIO views) and look. Three.js 0.185.1, the build the preview bundles, offers
a fixed set of tone mappings. Rather than a table of names, both are measured on one test set and the nearest wins:

* the test set (``SAMPLES``) is a grey ramp from 6.5 stops under to 6.5 over middle grey and the 24 ColorChecker
  patches at six exposures: the range most renders occupy, neutrals and saturated colours alike;
* Blender's side is its own OpenColorIO processor for the view and look on the sRGB display (``measure``), so a
  custom configuration is measured the same way as the bundled one;
* three.js's side is its own shader code (``tonemapping_pars_fragment``), ported line for line below, then the sRGB
  (or, for a linear output, no) transfer the renderer applies;
* the difference is the mean absolute difference of the display values, in percent of the display range.

A difference of at most ``EXACT`` percent is a counterpart (Standard, Khronos PBR Neutral and Raw are, by
construction); anything more is the nearest, and ``web_compat`` says so with the measured difference. Exposure is
not part of the choice: Blender and every candidate apply it to scene-linear light before the curve, so it carries
over as ``2 ** exposure`` (``toneMappingExposure``).
"""

import hashlib
import os

import numpy as np

EXACT = 0.5     # Percent: below one 8-bit display step on average.
CLOSE = 4.0     # Percent: a difference a side-by-side shows, but not a different look.
SRGB, LINEAR_SRGB = "srgb", "srgb-linear"   # THREE.SRGBColorSpace, THREE.LinearSRGBColorSpace.
# (three.js tone mapping constant, output colour space): every candidate applies toneMappingExposure.
CANDIDATES = (("AgXToneMapping", SRGB), ("NeutralToneMapping", SRGB), ("ACESFilmicToneMapping", SRGB),
              ("LinearToneMapping", SRGB), ("LinearToneMapping", LINEAR_SRGB))
FALLBACK = ("AgXToneMapping", SRGB)   # Blender's default view when its own cannot be read.

# GLSL's mat3(a, b, c) takes columns; with colours as rows, ``colour @ M`` is ``M * colour`` there.
_ACES_IN = np.array([[0.59719, 0.07600, 0.02840], [0.35458, 0.90834, 0.13383], [0.04823, 0.01566, 0.83777]])
_ACES_OUT = np.array([[1.60475, -0.10208, -0.00327], [-0.53108, 1.10813, -0.07276], [-0.07367, -0.00605, 1.07602]])
_TO_2020 = np.array([[0.6274, 0.0691, 0.0164], [0.3293, 0.9195, 0.0880], [0.0433, 0.0113, 0.8956]])
_FROM_2020 = np.array([[1.6605, -0.1246, -0.0182], [-0.5876, 1.1329, -0.1006], [-0.0728, -0.0083, 1.1187]])
_AGX_INSET = np.array([[0.856627153315983, 0.137318972929847, 0.11189821299995],
                       [0.0951212405381588, 0.761241990602591, 0.0767994186031903],
                       [0.0482516061458583, 0.101439036467562, 0.811302368396859]])
_AGX_OUTSET = np.array([[1.1271005818144368, -0.1413297634984383, -0.14132976349843826],
                        [-0.11060664309660323, 1.157823702216272, -0.11060664309660294],
                        [-0.016493938717834573, -0.016493938717834257, 1.2519364065950405]])
_AGX_MIN_EV, _AGX_MAX_EV = -12.47393, 4.026069
# The 24 ColorChecker patches (sRGB, 8-bit, as published), decoded to linear below.
_CHECKER = np.array([
    [115, 82, 68], [194, 150, 130], [98, 122, 157], [87, 108, 67], [133, 128, 177], [103, 189, 170],
    [214, 126, 44], [80, 91, 166], [193, 90, 99], [94, 60, 108], [157, 188, 64], [224, 163, 46],
    [56, 61, 150], [70, 148, 73], [175, 54, 60], [231, 199, 31], [187, 86, 149], [8, 133, 161],
    [243, 243, 242], [200, 200, 200], [160, 160, 160], [122, 122, 121], [85, 85, 85], [52, 52, 52]]) / 255.0


def srgb_decode(values):
    values = np.asarray(values, dtype=np.float64)
    return np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)


def srgb_encode(values):
    values = np.clip(values, 0.0, 1.0)
    return np.where(values <= 0.0031308, values * 12.92, 1.055 * np.power(values, 1 / 2.4) - 0.055)


def _samples():
    greys = 0.18 * np.power(2.0, np.linspace(-6.5, 6.5, 53))
    checker = srgb_decode(_CHECKER)
    return np.concatenate([np.stack([greys] * 3, 1)] + [checker * 2.0 ** stop for stop in (-2, -1, 0, 1, 2, 3)])


SAMPLES = _samples()


def _linear(colour):
    return np.clip(colour, 0.0, 1.0)


def _aces_filmic(colour):
    colour = (colour / 0.6) @ _ACES_IN
    a = colour * (colour + 0.0245786) - 0.000090537
    b = colour * (0.983729 * colour + 0.4329510) + 0.238081
    return np.clip((a / b) @ _ACES_OUT, 0.0, 1.0)


def _agx(colour):
    colour = colour @ _TO_2020 @ _AGX_INSET
    colour = np.clip((np.log2(np.maximum(colour, 1e-10)) - _AGX_MIN_EV) / (_AGX_MAX_EV - _AGX_MIN_EV), 0.0, 1.0)
    x2 = colour * colour
    x4 = x2 * x2
    colour = 15.5 * x4 * x2 - 40.14 * x4 * colour + 31.96 * x4 - 6.868 * x2 * colour + 0.4298 * x2 \
        + 0.1191 * colour - 0.00232
    colour = np.power(np.maximum(colour @ _AGX_OUTSET, 0.0), 2.2) @ _FROM_2020
    return np.clip(colour, 0.0, 1.0)


def _neutral(colour):
    start, desaturation = 0.8 - 0.04, 0.15
    low = colour.min(axis=1, keepdims=True)
    colour = colour - np.where(low < 0.08, low - 6.25 * low * low, 0.04)
    peak = colour.max(axis=1, keepdims=True)
    d = 1.0 - start
    new_peak = 1.0 - d * d / (peak + d - start)
    compressed = colour * new_peak / np.maximum(peak, 1e-12)
    g = 1.0 - 1.0 / (desaturation * (peak - new_peak) + 1.0)
    compressed = compressed * (1.0 - g) + new_peak * g
    return np.where(peak < start, colour, compressed)


CURVES = {"LinearToneMapping": _linear, "ACESFilmicToneMapping": _aces_filmic, "AgXToneMapping": _agx,
          "NeutralToneMapping": _neutral}


def three_display(tone_mapping, output, linear):
    """What three.js writes to the canvas for scene-linear ``linear`` (rows of RGB) at exposure 1."""
    mapped = CURVES[tone_mapping](np.asarray(linear, dtype=np.float64))
    return srgb_encode(mapped) if output == SRGB else np.clip(mapped, 0.0, 1.0)


def nearest(reference):
    """{"tone_mapping", "output", "difference"} of the candidate nearest ``reference``: Blender's display values for
    ``SAMPLES`` (rows of RGB in 0..1). ``difference`` is in percent of the display range."""
    reference = np.clip(np.asarray(reference, dtype=np.float64), 0.0, 1.0)
    scored = [(float(np.mean(np.abs(three_display(tone, output, SAMPLES) - reference))) * 100.0, index)
              for index, (tone, output) in enumerate(CANDIDATES)]
    difference, index = min(scored)
    tone, output = CANDIDATES[index]
    return {"tone_mapping": tone, "output": output, "difference": round(difference, 1)}


def exposure(stops):
    """three.js ``toneMappingExposure`` for Blender's exposure in stops."""
    return round(2.0 ** float(stops), 6)


_measured = {}   # (configuration, view, look) -> nearest(); a few entries per session.
_digests = {}    # (configuration, modified, size) -> sha256 of the file.


def _known(config_path, view, look):
    """The measurement made ahead (``web_tone_known``) when ``config_path`` is Angelo's own configuration."""
    from . import web_tone_known as known
    try:
        stat = os.stat(config_path)
        key = (config_path, stat.st_mtime_ns, stat.st_size)
        if key not in _digests:
            with open(config_path, "rb") as handle:
                _digests[key] = hashlib.sha256(handle.read()).hexdigest()
    except OSError:
        return None
    found = known.MEASURED.get((view, look)) if _digests[key] == known.CONFIG_SHA256 else None
    return None if found is None else {"tone_mapping": found[0], "output": found[1], "difference": found[2]}


def measure(config_path, view, look):
    """``nearest`` for ``view`` and ``look`` (None or "None": no look) of the OpenColorIO configuration at
    ``config_path``, measured with Blender's bundled PyOpenColorIO; None when it cannot be read. Cached, and for
    Angelo's own configuration measured ahead (``web_tone_known``), so an export never waits for it."""
    look = None if look in (None, "", "None") else look
    key = (config_path, view, look)
    if key not in _measured:
        _measured[key] = (_known(config_path, view, look) if __package__ else None) or \
            _measure(config_path, view, look)
    found = _measured[key]
    return dict(found) if found is not None else None


def _measure(config_path, view, look):
    try:
        import PyOpenColorIO as ocio
        config = ocio.Config.CreateFromFile(config_path)
        source = config.getRoleColorSpace("scene_linear")
        transform = ocio.DisplayViewTransform(src=source, display="sRGB", view=view)
        if look:   # Blender applies the look in scene-linear light, before the view.
            transform = ocio.GroupTransform([ocio.LookTransform(src=source, dst=source, looks=look), transform])
        pixels = SAMPLES.astype(np.float32)
        config.getProcessor(transform).getDefaultCPUProcessor().applyRGB(pixels)
    except Exception:  # No PyOpenColorIO, an unknown view, display or look: the caller reports it unread.
        return None
    return nearest(pixels)
