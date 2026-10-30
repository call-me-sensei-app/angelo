# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a material's node graph shows, as the values Blender's Solid view draws instead of the graph.

Solid view (Workbench) never evaluates shader nodes: it draws ``Material.diffuse_color`` (RGB and alpha),
``Material.metallic`` and ``Material.roughness``. A material whose graph says walnut but whose display values are
Blender's defaults looks pale grey there, an edit that looks failed (1.0.6 GUI test: a ``shader_nodes`` wood seat
stayed grey while the ``material_principled`` legs turned red). ``derive`` estimates those values from the graph;
``material_display_sync`` decides when Angelo writes them.

The rule, bounded and exact where the graph is exact:

* Start at the Material Output Material Preview renders (EEVEE's own before All's, the active one first, Cycles'
  last) and follow its Surface link, through
  reroutes, muted nodes' pass-through links and node groups (into the group's active Group Output, out through its
  Group Input to the group node's own inputs).
* Shaders: Principled BSDF gives Base Color, Metallic, Roughness and Alpha; Diffuse gives its colour, metallic 0 and
  roughness 1 (no highlight); Glossy and Metallic BSDFs metallic 1; Glass, Refraction and the other BSDFs their
  Color or Base Color input and Roughness where they have one; Emission its colour; Transparent alpha 0. A Mix
  Shader weighs its two shaders by its factor, an Add Shader evenly; a value only one side knows comes from that
  side alone.
* Colours and values: an unlinked socket is its own value. Linked: an RGB or Value node is its value; a Color Ramp
  is its colour at the factor when the factor is unlinked, else its mean over 0..1 (the stops and interpolation);
  Mix and MixRGB blend their inputs by blend type and factor (uncommon blend types as Mix); Invert, Gamma,
  Brightness/Contrast, Hue/Saturation/Value, RGB to BW, Combine/Separate Color, Math, Map Range and Clamp are
  evaluated; RGB Curves passes its colour through. A procedural texture's outputs are taken at their mean, 0.5 grey
  (Checker and Brick: the mean of their two colours), so what a ramp or mix makes of it decides the colour. An image
  texture's colour comes only from ``image_colour`` (a caller's bounded reader; None: unknown). A mix factor driven
  by anything unknown (Fresnel, Layer Weight, an attribute) is taken at 0.5.
* Anything else is unknown (None), and an unknown value is never invented: the caller leaves that display value.

Every walk is bounded (``MAX_VISITS`` sockets, ``MAX_DEPTH`` nesting) and a socket already on the walk's path is
unknown, so a cycle or a huge graph ends quickly. Blender-free: it reads only node, socket and link attributes, so
host tests drive it with stand-ins.
"""

import colorsys

FIELDS = ("color", "alpha", "metallic", "roughness")
MAX_VISITS = 512   # Sockets evaluated in one derivation.
MAX_DEPTH = 48     # Nested evaluations (links followed) along one path.
UNKNOWN_FACTOR = 0.5
TEXTURE_MEAN = 0.5
LUMINANCE = (0.2126, 0.7152, 0.0722)   # Rec. 709, scene linear: a colour read as a value.
TEXTURES = frozenset({"TEX_NOISE", "TEX_VORONOI", "TEX_WAVE", "TEX_MAGIC", "TEX_GRADIENT", "TEX_WHITE_NOISE",
                      "TEX_GABOR", "TEX_MUSGRAVE", "TEX_CHECKER", "TEX_BRICK"})
IMAGES = frozenset({"TEX_IMAGE", "TEX_ENVIRONMENT"})
# What each evaluator answers itself; the other one reads it through them (a colour as a value: its luminance).
SCALAR_NODES = frozenset({"VALUE", "MATH", "MAP_RANGE", "CLAMP", "RGBTOBW", "SEPARATE_COLOR"})
COLOUR_NODES = frozenset({"RGB", "VALTORGB", "MIX_RGB", "INVERT", "GAMMA", "BRIGHTCONTRAST", "HUE_SAT", "CURVE_RGB",
                          "COMBINE_COLOR"}) | IMAGES
# Shaders: (colour inputs in order of preference, metallic, roughness as a fixed value or None to read the input).
SHADERS = {
    "BSDF_DIFFUSE": (("Color",), 0.0, 1.0),
    "BSDF_GLOSSY": (("Color",), 1.0, None),
    "BSDF_ANISOTROPIC": (("Color",), 1.0, None),
    "BSDF_METALLIC": (("Base Color",), 1.0, None),
    "BSDF_GLASS": (("Color",), 0.0, None),
    "BSDF_REFRACTION": (("Color",), 0.0, None),
    "BSDF_TRANSLUCENT": (("Color",), 0.0, 1.0),
    "BSDF_SHEEN": (("Color",), 0.0, 1.0),
    "BSDF_VELVET": (("Color",), 0.0, 1.0),
    "BSDF_TOON": (("Color",), 0.0, 1.0),
    "BSDF_HAIR": (("Color",), 0.0, None),
    "BSDF_HAIR_PRINCIPLED": (("Color",), 0.0, None),
    "SUBSURFACE_SCATTERING": (("Color",), 0.0, 1.0),
    "EEVEE_SPECULAR": (("Base Color",), 0.0, None),
}


def _clamp(value, low=0.0, high=1.0):
    return min(max(value, low), high)


def _lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def _grey(value):
    return None if value is None else (value, value, value)


def _luminance(colour):
    return None if colour is None else sum(w * c for w, c in zip(LUMINANCE, colour))


def _as_colour(value):
    """A socket's own value as RGB: a colour's first three channels, a number as grey; None for anything else."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _grey(float(value))
    try:
        channels = [float(v) for v in value]
    except (TypeError, ValueError):
        return None
    return tuple(channels[:3]) if len(channels) in (3, 4) else None


def _as_scalar(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    colour = _as_colour(value)
    return _luminance(colour)


def identity(item):
    """A stable key for a Blender struct (its pointer: bpy makes a new Python wrapper on every access, so ``id`` of
    one names nothing), or ``id`` for a stand-in without one."""
    pointer = getattr(item, "as_pointer", None)
    return pointer() if callable(pointer) else id(item)


def _socket(sockets, *keys):
    """The first enabled socket whose identifier, then name, is one of ``keys``, or None."""
    items = [s for s in sockets if getattr(s, "enabled", True)]
    for key in keys:
        for attribute in ("identifier", "name"):
            found = next((s for s in items if getattr(s, attribute, None) == key), None)
            if found is not None:
                return found
    return None


def _ramp_stops(ramp):
    stops = sorted(((_clamp(float(e.position)), tuple(float(c) for c in e.color)) for e in ramp.elements),
                   key=lambda stop: stop[0])
    return stops or None


def ramp_at(ramp, position):
    """A Color Ramp's RGBA at ``position`` (linear between stops; Constant holds the stop at or below it)."""
    stops = _ramp_stops(ramp)
    if stops is None:
        return None
    position = _clamp(position)
    if position <= stops[0][0]:
        return stops[0][1]
    for (left, a), (right, b) in zip(stops, stops[1:]):
        if position <= right:
            if getattr(ramp, "interpolation", "LINEAR") == "CONSTANT" or right <= left:
                return a if position < right else b
            return _lerp(a, b, (position - left) / (right - left))
    return stops[-1][1]


def ramp_mean(ramp):
    """A Color Ramp's mean RGBA over factors 0..1: what a texture spread evenly over the ramp shows on average."""
    stops = _ramp_stops(ramp)
    if stops is None:
        return None
    constant = getattr(ramp, "interpolation", "LINEAR") == "CONSTANT"
    total = [c * stops[0][0] for c in stops[0][1]]
    for (left, a), (right, b) in zip(stops, stops[1:]):
        span = right - left
        segment = a if constant else tuple((x + y) / 2 for x, y in zip(a, b))
        total = [t + s * span for t, s in zip(total, segment)]
    total = [t + c * (1.0 - stops[-1][0]) for t, c in zip(total, stops[-1][1])]
    return tuple(total)


def blend(kind, factor, a, b):
    """Blender's colour blend of ``b`` over ``a`` by ``factor`` for the common blend types; others as Mix."""
    f = _clamp(factor)
    if kind == "ADD":
        return tuple(x + f * y for x, y in zip(a, b))
    if kind == "MULTIPLY":
        return tuple(x * (1 - f + f * y) for x, y in zip(a, b))
    if kind == "SUBTRACT":
        return tuple(x - f * y for x, y in zip(a, b))
    if kind == "SCREEN":
        return tuple(1 - (1 - f + f * (1 - y)) * (1 - x) for x, y in zip(a, b))
    if kind == "DIVIDE":
        return tuple(x * (1 - f) + f * x / y if y != 0 else x for x, y in zip(a, b))
    if kind == "DIFFERENCE":
        return _lerp(a, tuple(abs(x - y) for x, y in zip(a, b)), f)
    if kind == "DARKEN":
        return _lerp(a, tuple(min(x, y) for x, y in zip(a, b)), f)
    if kind == "LIGHTEN":
        return _lerp(a, tuple(max(x, y) for x, y in zip(a, b)), f)
    if kind == "OVERLAY":
        return tuple(x * (1 - f + 2 * f * y) if x < 0.5 else 1 - (1 - f + 2 * f * (1 - y)) * (1 - x)
                     for x, y in zip(a, b))
    return _lerp(a, b, f)


def _math(operation, a, b, c):
    simple = {"ADD": lambda: a + b, "SUBTRACT": lambda: a - b, "MULTIPLY": lambda: a * b,
              "DIVIDE": lambda: a / b if b != 0 else 0.0, "MULTIPLY_ADD": lambda: a * b + c,
              "POWER": lambda: a ** b if a > 0 or float(b).is_integer() else 0.0,
              "MINIMUM": lambda: min(a, b), "MAXIMUM": lambda: max(a, b), "ABSOLUTE": lambda: abs(a),
              "LESS_THAN": lambda: float(a < b), "GREATER_THAN": lambda: float(a > b)}
    try:
        return simple[operation]() if operation in simple else None
    except (OverflowError, ZeroDivisionError, TypeError):
        return None


def combine(parts):
    """``parts``: [(display or None, weight)]. Each field the weighted mean of the parts that know it."""
    out = {}
    for field in FIELDS:
        known = [(display[field], weight) for display, weight in parts
                 if display is not None and weight > 0 and display.get(field) is not None]
        total = sum(weight for _value, weight in known)
        if not known or total <= 0:
            out[field] = None
        elif field == "color":
            out[field] = tuple(sum(value[i] * weight for value, weight in known) / total for i in range(3))
        else:
            out[field] = sum(value * weight for value, weight in known) / total
    return out if any(value is not None for value in out.values()) else None


def active_output(tree):
    """The Material Output Material Preview (EEVEE) renders: one targeting EEVEE before one targeting all engines,
    the active one first (as Blender's ``ntreeShaderOutputNode``); a Cycles-only output last."""
    outputs = [n for n in getattr(tree, "nodes", ()) if getattr(n, "type", None) == "OUTPUT_MATERIAL"
               and not getattr(n, "mute", False)]
    for target, active in (("EEVEE", True), ("ALL", True), ("EEVEE", False), ("ALL", False), ("CYCLES", True)):
        for node in outputs:
            if getattr(node, "target", "ALL") == target and (not active or getattr(node, "is_active_output", True)):
                return node
    return outputs[0] if outputs else None


class _Walk:
    """One bounded derivation. ``scope``: the group nodes entered, innermost last."""

    def __init__(self, image_colour):
        self.image_colour, self.visits, self.path = image_colour, 0, set()

    def _resolve(self, socket, scope, depth=0):
        """Where an input's value comes from: ("out", node, socket, scope), ("in", socket, scope) for an input whose
        own value it is, or None when nothing is known."""
        if depth > MAX_DEPTH:
            return None
        for link in getattr(socket, "links", ()) or ():
            if getattr(link, "is_muted", False) or not getattr(link, "is_valid", True):
                continue
            node, out = link.from_node, link.from_socket
            if getattr(node, "type", None) == "REROUTE":
                inner = node.inputs[0] if len(node.inputs) else None
                if inner is None or not getattr(inner, "is_linked", bool(getattr(inner, "links", ()))):
                    return ("in", socket, scope)   # A dangling reroute: the socket keeps its own value.
                return self._resolve(inner, scope, depth + 1)
            if getattr(node, "mute", False):
                passed = next((il.from_socket for il in getattr(node, "internal_links", ()) if il.to_socket == out), None)
                if passed is None:
                    return None
                return self._resolve(passed, scope, depth + 1) if getattr(passed, "links", ()) else ("in", passed, scope)
            if getattr(node, "type", None) == "GROUP_INPUT":
                if not scope:
                    return None
                outer = _socket(scope[-1].inputs, getattr(out, "identifier", None))
                return None if outer is None else self._resolve(outer, scope[:-1], depth + 1)
            return ("out", node, out, scope)
        return ("in", socket, scope)

    def _value(self, socket, scope, kind):
        """``kind`` ("colour", "scalar", "shader") of the value reaching an input socket."""
        self.visits += 1
        if self.visits > MAX_VISITS:
            return None
        where = self._resolve(socket, scope)
        if where is None:
            return None
        if where[0] == "in":
            if kind == "shader":
                return None
            value = getattr(where[1], "default_value", None)
            return _as_colour(value) if kind == "colour" else _as_scalar(value)
        _out, node, out, inner_scope = where
        key = (identity(out), tuple(identity(n) for n in inner_scope), kind)
        if key in self.path or len(self.path) > MAX_DEPTH:
            return None   # A cycle, or a path too deep: unknown.
        self.path.add(key)
        try:
            if getattr(node, "type", None) == "GROUP":
                return self._group(node, out, inner_scope, kind)
            if kind == "shader":
                return self._shader(node, inner_scope)
            if kind == "colour":
                return self._colour(node, out, inner_scope)
            return self._scalar(node, out, inner_scope)
        finally:
            self.path.discard(key)

    def _group(self, node, out, scope, kind):
        tree = getattr(node, "node_tree", None)
        outputs = [n for n in getattr(tree, "nodes", ()) if getattr(n, "type", None) == "GROUP_OUTPUT"]
        output = next((n for n in outputs if getattr(n, "is_active_output", True)), outputs[0] if outputs else None)
        inner = None if output is None else _socket(output.inputs, getattr(out, "identifier", None))
        return None if inner is None else self._value(inner, scope + (node,), kind)

    def colour(self, node, scope, *keys):
        socket = _socket(node.inputs, *keys)
        return None if socket is None else self._value(socket, scope, "colour")

    def scalar(self, node, scope, *keys):
        socket = _socket(node.inputs, *keys)
        return None if socket is None else self._value(socket, scope, "scalar")

    def factor(self, node, scope, *keys):
        value = self.scalar(node, scope, *keys)
        return UNKNOWN_FACTOR if value is None else _clamp(value)

    def _shader(self, node, scope):
        kind = getattr(node, "type", None)
        if kind == "BSDF_PRINCIPLED":
            return {"color": self.colour(node, scope, "Base Color"), "alpha": self.scalar(node, scope, "Alpha"),
                    "metallic": self.scalar(node, scope, "Metallic"), "roughness": self.scalar(node, scope, "Roughness")}
        if kind == "MIX_SHADER":
            f = self.factor(node, scope, "Fac", "Factor")
            shaders = [s for s in node.inputs if getattr(s, "type", "SHADER") == "SHADER"][:2]
            if len(shaders) < 2:
                return None
            return combine([(self._value(shaders[0], scope, "shader"), 1 - f),
                            (self._value(shaders[1], scope, "shader"), f)])
        if kind == "ADD_SHADER":
            return combine([(self._value(s, scope, "shader"), 0.5) for s in node.inputs[:2]])
        if kind == "EMISSION":
            return {"color": self.colour(node, scope, "Color"), "alpha": None, "metallic": None, "roughness": None}
        if kind == "BSDF_TRANSPARENT":
            return {"color": None, "alpha": 0.0, "metallic": None, "roughness": None}
        if kind in SHADERS:
            colours, metallic, roughness = SHADERS[kind]
            return {"color": self.colour(node, scope, *colours), "alpha": None, "metallic": metallic,
                    "roughness": roughness if roughness is not None else self.scalar(node, scope, "Roughness")}
        return None   # Holdout, volumes and anything unknown.

    def _colour(self, node, out, scope):
        kind = getattr(node, "type", None)
        if kind == "RGB":
            return _as_colour(getattr(out, "default_value", None))
        if kind == "VALTORGB":
            return self._ramp(node, scope, out)
        if kind == "MIX" and getattr(node, "data_type", "RGBA") == "RGBA":
            a, b = self.colour(node, scope, "A_Color"), self.colour(node, scope, "B_Color")
            mixed = None if a is None or b is None else blend(getattr(node, "blend_type", "MIX"),
                                                              self.factor(node, scope, "Factor_Float"), a, b)
            return None if mixed is None else tuple(_clamp(c) for c in mixed) if getattr(node, "clamp_result", False) \
                else mixed
        if kind == "MIX_RGB":
            a, b = self.colour(node, scope, "Color1"), self.colour(node, scope, "Color2")
            mixed = None if a is None or b is None else blend(getattr(node, "blend_type", "MIX"),
                                                              self.factor(node, scope, "Fac"), a, b)
            return None if mixed is None else tuple(_clamp(c) for c in mixed) if getattr(node, "use_clamp", False) \
                else mixed
        if kind == "INVERT":
            colour = self.colour(node, scope, "Color")
            return None if colour is None else _lerp(colour, tuple(1 - c for c in colour), self.factor(node, scope, "Fac"))
        if kind == "GAMMA":
            colour, gamma = self.colour(node, scope, "Color"), self.scalar(node, scope, "Gamma")
            return None if colour is None or gamma is None else tuple(max(c, 0.0) ** gamma for c in colour)
        if kind == "BRIGHTCONTRAST":
            colour = self.colour(node, scope, "Color")
            bright, contrast = self.scalar(node, scope, "Bright"), self.scalar(node, scope, "Contrast")
            if colour is None or bright is None or contrast is None:
                return None
            return tuple(max(c * (1 + contrast) + bright - contrast * 0.5, 0.0) for c in colour)
        if kind == "HUE_SAT":
            return self._hue_sat(node, scope)
        if kind == "CURVE_RGB":
            return self.colour(node, scope, "Color")   # The curve itself is not evaluated: its colour passes.
        if kind == "COMBINE_COLOR":
            channels = [self._value(s, scope, "scalar") for s in node.inputs[:3]]
            if None in channels or len(channels) < 3:
                return None
            mode = getattr(node, "mode", "RGB")
            return (tuple(channels) if mode == "RGB" else colorsys.hsv_to_rgb(*channels) if mode == "HSV"
                    else colorsys.hls_to_rgb(channels[0], channels[2], channels[1]))
        if kind in TEXTURES and getattr(out, "type", "RGBA") == "RGBA":
            if kind in {"TEX_CHECKER", "TEX_BRICK"}:
                a, b = self.colour(node, scope, "Color1"), self.colour(node, scope, "Color2")
                return None if a is None or b is None else _lerp(a, b, 0.5)
            return _grey(TEXTURE_MEAN)
        if kind in IMAGES:
            if getattr(out, "identifier", "Color") != "Color" or self.image_colour is None:
                return None
            return self.image_colour(getattr(node, "image", None))
        if kind in SCALAR_NODES or (kind == "MIX" and getattr(node, "data_type", "RGBA") == "FLOAT"):
            return _grey(self._scalar(node, out, scope))
        return None

    def _ramp(self, node, scope, out):
        ramp = getattr(node, "color_ramp", None)
        if ramp is None:
            return None
        fac = _socket(node.inputs, "Fac")
        where = None if fac is None else self._resolve(fac, scope)
        if where is not None and where[0] == "in":   # A fixed factor: the ramp's colour there.
            value = self._value(fac, scope, "scalar")
            rgba = ramp_at(ramp, UNKNOWN_FACTOR if value is None else value)
        else:
            rgba = ramp_mean(ramp)
        if rgba is None:
            return None
        return _grey(rgba[3]) if getattr(out, "identifier", "Color") == "Alpha" and len(rgba) > 3 else tuple(rgba[:3])

    def _hue_sat(self, node, scope):
        colour = self.colour(node, scope, "Color")
        hue, saturation, value = (self.scalar(node, scope, key) for key in ("Hue", "Saturation", "Value"))
        if colour is None or None in (hue, saturation, value):
            return None
        h, s, v = colorsys.rgb_to_hsv(*(max(c, 0.0) for c in colour))
        shifted = colorsys.hsv_to_rgb((h + hue - 0.5) % 1.0, _clamp(s * saturation), max(v * value, 0.0))
        return _lerp(colour, shifted, self.factor(node, scope, "Fac"))

    def _scalar(self, node, out, scope):
        kind = getattr(node, "type", None)
        if kind == "VALUE":
            return _as_scalar(getattr(out, "default_value", None))
        if kind == "MATH":
            values = [self._value(s, scope, "scalar") for s in node.inputs[:3]]
            values += [0.0] * (3 - len(values))
            if None in values[:2]:
                return None
            result = _math(getattr(node, "operation", "ADD"), *(v if v is not None else 0.0 for v in values))
            return None if result is None else _clamp(result) if getattr(node, "use_clamp", False) else result
        if kind == "MAP_RANGE" and getattr(node, "data_type", "FLOAT") == "FLOAT":
            names = ("Value", "From Min", "From Max", "To Min", "To Max")
            value, low, high, to_low, to_high = (self.scalar(node, scope, name) for name in names)
            if None in (value, low, high, to_low, to_high) or high == low:
                return None
            t = (value - low) / (high - low)
            t = _clamp(t) if getattr(node, "clamp", True) else t
            return to_low + (to_high - to_low) * t
        if kind == "CLAMP":
            value, low, high = (self.scalar(node, scope, name) for name in ("Value", "Min", "Max"))
            return None if None in (value, low, high) else min(max(value, min(low, high)), max(low, high))
        if kind == "RGBTOBW":
            return _luminance(self.colour(node, scope, "Color"))
        if kind == "SEPARATE_COLOR" and getattr(node, "mode", "RGB") == "RGB":
            colour, index = self.colour(node, scope, "Color"), list(node.outputs).index(out)
            return None if colour is None or index > 2 else colour[index]
        if kind == "MIX" and getattr(node, "data_type", "RGBA") == "FLOAT":
            a, b = self.scalar(node, scope, "A_Float"), self.scalar(node, scope, "B_Float")
            return None if a is None or b is None else a + (b - a) * self.factor(node, scope, "Factor_Float")
        if kind == "VALTORGB" and getattr(out, "identifier", "Color") == "Alpha":
            colour = self._ramp(node, scope, out)
            return None if colour is None else colour[0]
        if kind in TEXTURES and getattr(out, "type", "VALUE") == "VALUE":
            return TEXTURE_MEAN
        if (kind in COLOUR_NODES or (kind == "MIX" and getattr(node, "data_type", "RGBA") == "RGBA")
                or (kind in TEXTURES and getattr(out, "type", "VALUE") == "RGBA")):
            return _luminance(self._colour(node, out, scope))   # Never back here: _colour answers these itself.
        return None

    def surface(self, tree):
        output = active_output(tree)
        surface = None if output is None else _socket(output.inputs, "Surface")
        return None if surface is None else self._value(surface, (), "shader")


def derive(tree, image_colour=None):
    """{"color": (r, g, b), "alpha", "metallic", "roughness"} of what ``tree`` (a material's node tree) shows, each
    None where it is unknown; every value None for no tree, no output or nothing linked to its Surface.
    ``image_colour(image)``: a bounded reader of an image's mean scene-linear RGB, or None (images unknown)."""
    display = _Walk(image_colour).surface(tree) if tree is not None else None
    display = dict.fromkeys(FIELDS) if display is None else dict(display)
    if display["color"] is not None:
        display["color"] = tuple(_clamp(c) for c in display["color"])
    for field in ("alpha", "metallic", "roughness"):
        if display[field] is not None:
            display[field] = _clamp(display[field])
    return display
