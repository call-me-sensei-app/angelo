# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""From baked arrays to a texture set's files: weights, alpha, emission and height ranges, and packing. numpy only.

A bake leaves one float array per channel (``bake_maps_graph`` sums, see there).
``finish`` turns the sums into the channels a texture set holds:

* base colour, metallic and roughness are divided by the BSDF weight where it is
  not one everywhere (a Mix Shader with a Transparent BSDF, an Add Shader of two
  BSDFs); a texel with no BSDF at all gets the channel's default;
* alpha is one minus the transparent weight;
* emission above 1 in an 8- or 16-bit file is stored divided by its largest value,
  which becomes the baked material's Emission Strength (``facts["emission_strength"]``);
* height (scene units) is stored from 0 to 1 over its own range in a PNG, with the
  Displacement node's midlevel and scale that give the original values back.

``compose`` packs the channels into one file's pixels per ``bake_maps_spec.files``.
"""

import numpy

from .bake_maps_spec import DEFAULTS, WIDTH

EPSILON = 1e-6


def _array(value, shape, width):
    """A channel's array: a baked one as it is, a constant (an RGB tuple or a float) filled."""
    if isinstance(value, numpy.ndarray):
        return value
    rgb = value if isinstance(value, (tuple, list)) else (value, value, value)
    filled = numpy.empty((*shape, width), dtype=numpy.float32)
    filled[...] = numpy.asarray(rgb[:width], dtype=numpy.float32)
    return filled


def finish(sums, shape, image_format):
    """``(channels, facts)``: the set's channels from the baked or folded sums. A baked sum is an ``(h, w, k)``
    array, a folded one an RGB tuple; a channel stays a tuple when everything it is made from is one. The sums are
    taken out of ``sums`` as they are used, so an array the caller no longer holds is freed once its channel is
    made; each channel is one new array, its arithmetic done in place."""
    channels, facts = {}, {}
    weight = sums.pop("WEIGHT", (1.0, 1.0, 1.0))
    if isinstance(weight, numpy.ndarray):
        weight = weight[..., :1]
        if float(numpy.abs(weight - 1.0).max(initial=0.0)) <= 1e-4:
            weight = (1.0, 1.0, 1.0)
    for channel in ("BASE_COLOR", "METALLIC", "ROUGHNESS"):
        if channel in sums:
            channels[channel] = _divide(sums.pop(channel), weight, channel, shape)
    del weight
    if "TRANSPARENCY" in sums:
        transparency = sums.pop("TRANSPARENCY")
        if isinstance(transparency, numpy.ndarray):
            alpha = numpy.subtract(1.0, transparency[..., :1], dtype=numpy.float32)
            channels["ALPHA"] = numpy.clip(alpha, 0.0, 1.0, out=alpha)
        else:
            channels["ALPHA"] = (min(1.0, max(0.0, 1.0 - transparency[0])),) * 3
    if "EMISSION" in sums:
        emission = sums.pop("EMISSION")
        peak = float(numpy.max(emission)) if isinstance(emission, numpy.ndarray) else max(emission)
        if image_format != "OPEN_EXR" and peak > 1.0:
            facts["emission_strength"] = round(peak, 6)
            emission = numpy.divide(emission, peak, dtype=numpy.float32) if isinstance(
                emission, numpy.ndarray) else tuple(v / peak for v in emission)
        channels["EMISSION"] = emission
    if "HEIGHT" in sums:
        channels["HEIGHT"], facts["height"] = _height(sums.pop("HEIGHT"), image_format)
    for channel in ("NORMAL", "AO"):
        if channel in sums:
            channels[channel] = sums.pop(channel)
    return {channel: _own(value) for channel, value in channels.items()}, facts


def _own(value):
    """A channel that is part of a packed bake's array (a view of one component) as an array of its own, so the
    packed array is freed once every channel of it is made."""
    if isinstance(value, numpy.ndarray) and isinstance(value.base, numpy.ndarray) and value.base.nbytes > value.nbytes:
        return value.copy()
    return value


def _divide(value, weight, channel, shape):
    """A weighted sum divided by the BSDF weight; where no BSDF covers a texel, the channel's default."""
    if isinstance(weight, tuple):
        w = weight[0]
        if abs(w - 1.0) <= 1e-6:
            return value
        if w <= EPSILON:
            return (DEFAULTS[channel],) * 3
        return numpy.divide(value, w, dtype=numpy.float32) if isinstance(value, numpy.ndarray) else tuple(
            v / w for v in value)
    covered = weight > EPSILON
    divided = numpy.divide(_array(value, shape, WIDTH[channel]), numpy.where(covered, weight, numpy.float32(1.0)),
                           dtype=numpy.float32)
    numpy.copyto(divided, numpy.float32(DEFAULTS[channel]), where=~covered)
    return divided


def _height(value, image_format):
    """Height in scene units as stored, and the Displacement settings (midlevel, scale) that give it back:
    ``(stored - midlevel) * scale``. OpenEXR keeps the values; a PNG spans 0 to 1 over their range. A height that
    is one value everywhere (a constant, or a flat array) keeps it: stored 1 with midlevel 0 and that value as the
    scale, so a constant offset is never lost (0 stays 0.5 with no scale)."""
    if isinstance(value, numpy.ndarray):
        low, high = float(value.min()), float(value.max())
        if high - low > EPSILON:
            if image_format == "OPEN_EXR":
                return value, {"midlevel": 0.0, "scale": 1.0}
            span = high - low
            stored = numpy.subtract(value, low, dtype=numpy.float32)
            stored /= span
            return stored, {"midlevel": round(-low / span, 6), "scale": round(span, 6)}
        constant = (low + high) / 2.0
    else:
        constant = float(value[0])
    if image_format == "OPEN_EXR":
        return (constant,) * 3, {"midlevel": 0.0, "scale": 1.0}
    if abs(constant) <= EPSILON:
        return (0.5, 0.5, 0.5), {"midlevel": 0.5, "scale": 0.0}
    return (1.0, 1.0, 1.0), {"midlevel": 0.0, "scale": round(constant, 6)}


def _component(channels, slot, rows):
    """One component of a file (``bake_maps_spec`` slot) over ``rows`` (lo, hi): an ``(h, w)`` view or a float."""
    if not isinstance(slot, tuple):
        return float(slot)
    channel, index, _transform = slot
    value = channels.get(channel, DEFAULTS[channel])
    if isinstance(value, numpy.ndarray):
        return value[rows[0]:rows[1], :, min(index, value.shape[-1] - 1)]
    rgb = value if isinstance(value, (tuple, list)) else (value, value, value)
    return float(rgb[min(index, len(rgb) - 1)])


def compose(channels, slots, shape, rows=None):
    """The ``(h, w, len(slots))`` float32 pixels of one file, or of its rows ``rows`` (lo, hi), row 0 at the bottom:
    written straight into one array, so a band costs only its own pixels."""
    lo, hi = rows or (0, shape[0])
    pixels = numpy.empty((hi - lo, shape[1], len(slots)), dtype=numpy.float32)
    for position, slot in enumerate(slots):
        plane = pixels[..., position]
        plane[...] = _component(channels, slot, (lo, hi))
        if isinstance(slot, tuple) and slot[2] in {"invert", "flip"}:
            numpy.subtract(1.0, plane, out=plane)
    return pixels


def constant_of(values):
    """The one constant all ``values`` share (an RGB tuple), or None when they differ or any is not constant."""
    first = None
    for value in values:
        if not isinstance(value, tuple):
            return None
        if first is None:
            first = value
        elif any(abs(a - b) > 1e-6 for a, b in zip(first, value)):
            return None
    return first
