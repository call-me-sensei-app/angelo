# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where the World's sun is and how bright the World shows, beside ``world_surface.shows``. Facts, no verdicts.

Recorded one-shots (21 remarks in 14 bridge trials, on both sides) guessed where the Sky
texture's ``sun_rotation`` points, set the sky's sun "by estimate" apart from the Sun light, could not confirm
that haze settings took, and could not tell how bright a sky renders. ``world_surface.shows`` therefore adds:

* to ``sky``: the sun as ``sun_bearing_deg`` beside ``sun_elevation_deg``, and the node's other settings read
  back (``sky``);
* ``sun_lights``: each Sun light that renders, with its bearing, elevation and strength and ``from_sky_deg``,
  the angle between it and the sky's sun (``sun_lights``);
* ``brightness``: the World's scene-linear colour where it can be read, and ``shown``, what the scene's view
  transform makes of it on screen (``brightness``).

Directions follow one convention, stated in the result as ``directions``: toward the sun, the bearing in degrees
clockwise from +Y seen from above (+X is 90) and the elevation in degrees above the horizon. For the scattering
skies ``sun_rotation`` is that bearing: Cycles (``svm/sky.h``) and EEVEE (``node_shader_tex_sky.cc``) put the sun
along (cos e sin r, cos e cos r, sin e), so an elevation past 90 degrees folds over the zenith. The analytic skies
(Preetham, Hosek-Wilkie) read ``sun_direction``. A Sun light shines along its local -Z, so its sun lies along +Z.
A Sky node's own texture mapping or a linked Vector input turns the sky away from that reading: ``sun_turned``.

Readings of bpy values are duck-typed and need no Blender import, as in ``world_surface``; only ``shown`` uses
Blender, through ``view_save``'s native save (the scene's own view transform, look, exposure, gamma and curves),
and is left out where that helper is missing (host tests).
"""

import math
import struct
from pathlib import Path

from . import private_roots
from . import world_light_table

DIRECTIONS = "toward the sun: bearing in degrees clockwise from +Y seen from above (+X is 90), elevation above the horizon"
ANALYTIC = ("PREETHAM", "HOSEK_WILKIE")
# Read back as set: a value a script's try/except failed to write shows here as it is.
SCATTERING_SETTINGS = ("sun_disc", "sun_intensity", "altitude", "air_density", "aerosol_density", "ozone_density")
ANALYTIC_SETTINGS = ("turbidity", "ground_albedo")
MAX_SUNS = 4
# The picture ``shown`` is saved as: uncompressed, 8 bits a channel, in the scene's view transform (display=True).
SHOWN_FORMAT = {"media_type": "IMAGE", "file_format": "BMP", "color_mode": "RGB"}


def angles(vector):
    """``(bearing, elevation)`` in degrees of a direction (module docstring), or None for a zero vector."""
    x, y, z = (float(value) for value in list(vector)[:3])
    length = math.sqrt(x * x + y * y + z * z)
    if length == 0.0:
        return None
    bearing = round(math.degrees(math.atan2(x, y)) % 360.0, 2)
    return (0.0 if bearing >= 360.0 else bearing + 0.0,  # 359.999 rounds to 360: the same bearing as 0.
            round(math.degrees(math.asin(max(-1.0, min(1.0, z / length)))), 2) + 0.0)


def sky_direction(node):
    """The unit-free direction toward a Sky texture's sun, before its own mapping."""
    if node.sky_type in ANALYTIC:
        return tuple(float(value) for value in node.sun_direction)
    elevation, rotation = float(node.sun_elevation), float(node.sun_rotation)
    return (math.cos(elevation) * math.sin(rotation), math.cos(elevation) * math.cos(rotation), math.sin(elevation))


def _turned(node):
    mapping = getattr(node, "texture_mapping", None)
    if mapping is not None:
        rotation = [float(value) for value in getattr(mapping, "rotation", (0.0, 0.0, 0.0))]
        scale = [float(value) for value in getattr(mapping, "scale", (1.0, 1.0, 1.0))]
        axes = [str(getattr(mapping, "mapping_" + axis, axis.upper())) for axis in "xyz"]
        if any(abs(value) > 1e-6 for value in rotation) or any(abs(value - 1.0) > 1e-6 for value in scale) \
                or axes != ["X", "Y", "Z"]:
            return "texture_mapping"
    vector = next((socket for socket in node.inputs if socket.identifier == "Vector"), None)
    if vector is not None and getattr(vector, "is_linked", False) and not getattr(vector, "is_unavailable", False):
        return "vector_input"
    return None


def sky(node):
    """``(facts, direction)``: the Sky texture's sun and settings, read back from the node."""
    direction = sky_direction(node)
    facts = {"type": str(node.sky_type)}
    found = angles(direction)
    if found is not None:
        facts["sun_bearing_deg"], facts["sun_elevation_deg"] = found
    if node.sky_type not in ANALYTIC:
        facts["sun_size_deg"] = round(math.degrees(float(getattr(node, "sun_size", 0.0))), 3)
    for name in ANALYTIC_SETTINGS if node.sky_type in ANALYTIC else SCATTERING_SETTINGS:
        value = getattr(node, name, None)
        if isinstance(value, bool):
            facts[name] = value
        elif isinstance(value, (int, float)):
            facts[name] = round(float(value), 4)
    turned = _turned(node)
    if turned is not None:
        facts["sun_turned"] = turned
    return facts, direction


def _apart(first, second):
    a, b = [float(v) for v in list(first)[:3]], [float(v) for v in list(second)[:3]]
    lengths = math.sqrt(sum(v * v for v in a)) * math.sqrt(sum(v * v for v in b))
    if lengths == 0.0:
        return None
    return round(math.degrees(math.acos(max(-1.0, min(1.0, sum(x * y for x, y in zip(a, b)) / lengths)))), 2)


def rendering(scene, view_layer=None):
    """Pointers of the collections whose objects render: reached in a rendered view layer through collections that
    layer neither excludes nor hides for render (the chain's ``hide_render``), or None when the layers cannot be read
    (then only an object's own ``hide_render`` counts). The rendered layers are the render's own
    (``job_render_samples.layers``): ``view_layer`` alone when the scene renders a single layer and it is given,
    else every used layer. Collections only, never every object: a check costs the lights alone (``renders``)."""
    try:
        if view_layer is not None and getattr(scene.render, "use_single_layer", False):
            layers = [view_layer]
        else:
            layers = [layer for layer in scene.view_layers if getattr(layer, "use", True)]
        found, stack = set(), [layer.layer_collection for layer in layers]
        while stack:
            layer_collection = stack.pop()
            if layer_collection.exclude or getattr(layer_collection.collection, "hide_render", False):
                continue
            found.add(layer_collection.collection.as_pointer())
            stack.extend(layer_collection.children)
        return found
    except (AttributeError, TypeError, ReferenceError, RuntimeError):
        return None


def renders(obj, rendered):
    """Whether ``obj`` renders, given ``rendering``'s collections (None: its own flag alone)."""
    if getattr(obj, "hide_render", False):
        return False
    return rendered is None or any(collection.as_pointer() in rendered for collection in obj.users_collection)


def sun_lights(scene, toward=None, rendered=False):
    """``(rows, more)``: up to ``MAX_SUNS`` Sun lights that render (``rendering``), and how many more there are."""
    rows, more = [], 0
    rendered = rendering(scene) if rendered is False else rendered
    for obj in getattr(scene, "objects", ()):
        if obj.type != "LIGHT" or getattr(obj.data, "type", "") != "SUN" or not renders(obj, rendered):
            continue
        if len(rows) == MAX_SUNS:
            more += 1
            continue
        matrix = obj.matrix_world
        axis = (matrix[0][2], matrix[1][2], matrix[2][2])  # Its +Z: where the light comes from.
        found = angles(axis)
        if found is None:
            continue
        row = {"name": str(obj.name)[:64], "bearing_deg": found[0], "elevation_deg": found[1],
               "strength": round(float(getattr(obj.data, "energy", 0.0)), 4)}
        if toward is not None:
            row["from_sky_deg"] = _apart(axis, toward)
        rows.append(row)
    return rows, more


def view(scene):
    """The scene's display transform, as ``view_picture.colour`` states it for a rendered picture."""
    settings = getattr(scene, "view_settings", None)
    if settings is None:
        return {}
    facts = {"view_transform": str(settings.view_transform)}
    if settings.look != "None":
        facts["look"] = str(settings.look)
    if abs(settings.exposure) > 1e-6:
        facts["exposure"] = round(float(settings.exposure), 3)
    if abs(settings.gamma - 1.0) > 1e-6:
        facts["gamma"] = round(float(settings.gamma), 3)
    return facts


def sky_samples(node, strength):
    """``(samples, reason)``: the measured radiance of a Sky the Background shows directly, times its strength."""
    elevation = angles(sky_direction(node))
    if elevation is None:
        return None, "the sky's sun has no direction"
    atmosphere = {name: getattr(node, name) for name in world_light_table.ATMOSPHERE.get(node.sky_type, ())
                  if hasattr(node, name)}
    rows, reason = world_light_table.reading(str(node.sky_type), elevation[1], atmosphere)
    if rows is None:
        return None, reason
    return [(where, [round(value * strength, 5) for value in rgb]) for where, rgb in rows], None


def brightness(scene, samples, source):
    """``{"view", "source", "samples": [{"where", "linear", "shown"}]}`` for scene-linear ``samples``."""
    shown = displayed(scene, [rgb for _, rgb in samples])
    rows = []
    for index, (where, rgb) in enumerate(samples):
        row = {"where": where, "linear": rgb}
        if shown is not None:
            row["shown"] = shown[index]
        rows.append(row)
    return {"view": view(scene), "source": source, "samples": rows}


def radiance_hdr(values):
    """A one-row Radiance HDR (RGBE, flat scanline) holding scene-linear ``values``."""
    pixels = bytearray()
    for rgb in values:
        r, g, b = (max(0.0, float(value)) for value in rgb)
        top = max(r, g, b)
        if top < 1e-32:
            pixels += b"\0\0\0\0"
            continue
        mantissa, exponent = math.frexp(top)
        scale = mantissa * 256.0 / top
        pixels += bytes((min(255, int(r * scale)), min(255, int(g * scale)), min(255, int(b * scale)), exponent + 128))
    return b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe\n\n-Y 1 +X %d\n" % len(values) + bytes(pixels)


def read_bmp(data, count):
    """The first ``count`` pixels of an uncompressed 24- or 32-bit BMP's first stored row, as 0-1 RGB, or None."""
    offset = struct.unpack_from("<I", data, 10)[0]
    step = struct.unpack_from("<H", data, 28)[0] // 8
    if step not in (3, 4) or struct.unpack_from("<I", data, 30)[0] not in (0, 3):
        return None  # Not a layout this reads: no display reading rather than a wrong one.
    pixels = []
    for index in range(count):
        blue, green, red = data[offset + index * step:offset + index * step + 3]
        pixels.append([round(red / 255, 3), round(green / 255, 3), round(blue / 255, 3)])
    return pixels


def displayed(scene, values):
    """What the scene's view transform shows of scene-linear ``values`` (0-1 RGB each), or None without the helper."""
    try:
        from .view_save import native
        picture = native()
    except ImportError:
        return None
    if picture is None or not values:
        return None
    try:  # The folder's creation and cleanup too: a full or unwritable temporary folder only loses the reading.
        with private_roots.TemporaryDirectory("angelo-world-light-") as folder:
            source, target = Path(folder, "linear.hdr"), Path(folder, "shown.bmp")
            source.write_bytes(radiance_hdr(values))
            picture.save_copy(scene, str(target), source=str(source), settings=dict(SHOWN_FORMAT), display=True)
            return read_bmp(target.read_bytes(), len(values))
    except (RuntimeError, ValueError, TypeError, OSError, struct.error):
        return None  # Optional evidence: the linear values stand without it.
