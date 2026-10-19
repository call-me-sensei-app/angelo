# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Evaluate a shader node graph the way the renderer does, for one sample, without rendering.

The grader reads a World's sky (``world_background``) and a material's colour,
roughness or emission (``surface_reading``) from what their node graphs
compute, never from which nodes exist. ``Graph(feeds, context)`` evaluates
any input or output socket; ``context`` supplies the nodes whose values come
from where the graph is used (coordinates, geometry, attributes, the ray):
the view direction for a world, a surface element's attributes and position
for a material.

Noise, Voronoi, Brick and Checker textures are computed exactly
(``texture_kernels``), for all of a reading's samples at once (``Batch``).

Three kinds of result:

* a value: a float, an ``(r, g, b)`` / ``(x, y, z)`` tuple or a ``Shader``;
* ``UNKNOWN``: something the grader cannot compute (an image texture, a
  texture variant ``texture_kernels`` does not port, a coordinate the context
  does not have). It can be anything;
* a ``Spread``: the set of values a node can produce whatever its unknown
  input is. Only nodes that bound their output make one: a Color Ramp shows
  one of its colours (its factor is clamped), a Mix with a clamped factor
  lies between its two inputs, a clamped Map Range, Math or Clamp lies in its
  range. Other nodes apply themselves to every member of a Spread (two Spreads
  combine as if independent, which can only widen the set) and pass ``UNKNOWN``
  on. No range is ever assumed for a texture.

``judge(value, test)`` is then True or False when every possible value agrees
and None (inconclusive) when they differ or the value is unknown, so an
unreadable part can never make a check pass, or fail, by guesswork.

The socket conventions are Blender 5.2's (identifiers such as ``A_Color``);
Cycles and EEVEE agree with every coordinate rule ``world_background`` states.
"""

import colorsys
import math

MAX_DEPTH = 64
MAX_SPREAD = 4096           # Larger sets of possible values are treated as unknown.
RAMP_SAMPLES = 64           # A ramp's curve is sampled at 65 factors plus each stop's own position.
SEGMENT_SAMPLES = 32        # A clamped factor or range is sampled at 33 evenly spaced points.
LUMINANCE = (0.2126, 0.7152, 0.0722)   # Linear Rec.709, Blender's default colour space.
# Nodes whose values come from where the graph is used; the context answers for them (UNKNOWN if it cannot).
CONTEXT_NODES = frozenset({"TEX_COORD", "NEW_GEOMETRY", "LIGHT_PATH", "ATTRIBUTE", "VERTEX_COLOR", "UVMAP",
                           "OBJECT_INFO", "CAMERA", "LAYER_WEIGHT", "FRESNEL", "TANGENT", "WIREFRAME",
                           "AMBIENT_OCCLUSION", "BEVEL", "PARTICLE_INFO", "POINT_INFO", "CURVES_INFO",
                           "VOLUME_INFO"})


class Context:
    """What a graph's context nodes read for one sample. Subclasses answer ``value``; anything they do not
    know is UNKNOWN. ``generated`` is the coordinate a texture node with no Vector link uses."""

    def value(self, node, identifier):
        if node.type == "LIGHT_PATH":   # A camera ray meets the surface or the background.
            if identifier == "Is Camera Ray":
                return 1.0
            return UNKNOWN if identifier == "Ray Length" else 0.0
        return UNKNOWN

    def generated(self):
        return UNKNOWN


class Unreadable(Exception):
    """The graph itself cannot be evaluated (a cycle, a runaway depth); the message says why."""


class Unseen(Exception):
    """A sample the context cannot show (a direction behind the camera): it is skipped, not judged."""


class _Unknown:
    __slots__ = ()

    def __repr__(self):
        return "UNKNOWN"


UNKNOWN = _Unknown()


class Shader:
    """The radiance a closure adds (a world's background), in linear RGB."""

    __slots__ = ("rgb",)

    def __init__(self, rgb):
        self.rgb = tuple(float(value) for value in rgb)

    def __eq__(self, other):
        return isinstance(other, Shader) and other.rgb == self.rgb

    def __hash__(self):
        return hash(self.rgb)


class Spread:
    """The values a node can produce whatever its unknown input is (see the module docstring)."""

    __slots__ = ("values",)

    def __init__(self, values):
        unique = []
        seen = set()
        for value in values:
            key = _key(value)
            if key not in seen:
                seen.add(key)
                unique.append(value)
        self.values = tuple(unique)

    def __repr__(self):
        return "Spread(%d)" % len(self.values)


def _key(value):
    if isinstance(value, Shader):
        return ("s",) + tuple(round(v, 6) for v in value.rgb)
    if isinstance(value, tuple):
        return tuple(round(v, 6) for v in value)
    return round(float(value), 6)


def spread_or_value(values):
    """One value when every member is the same, else a Spread (UNKNOWN beyond ``MAX_SPREAD``)."""
    spread = Spread(values)
    if len(spread.values) == 1:
        return spread.values[0]
    return spread if len(spread.values) <= MAX_SPREAD else UNKNOWN


def judge(value, test):
    """True or False when every value it can be agrees on ``test``; None when they differ or it is unknown."""
    if value is UNKNOWN:
        return None
    if isinstance(value, Spread):
        results = {bool(test(member)) for member in value.values}
        return results.pop() if len(results) == 1 else None
    return bool(test(value))


def members(value):
    """The definite values a result stands for (none for UNKNOWN), for reporting."""
    if value is UNKNOWN:
        return ()
    return value.values if isinstance(value, Spread) else (value,)


# ---------------------------------------------------------------- the graph the renderer uses

def active_output(tree, engine=None, kind="OUTPUT_WORLD"):
    """The output node the renderer evaluates: Blender's ``ntreeShaderOutputNode`` rule.

    An output targeting the scene's engine wins over one targeting all engines;
    among equals the active output wins. ``engine`` is ``scene.render.engine``.
    """
    target = {"CYCLES": "CYCLES", "BLENDER_EEVEE": "EEVEE", "BLENDER_EEVEE_NEXT": "EEVEE"}.get(engine)
    outputs = [node for node in tree.nodes if node.type == kind]
    exact = [node for node in outputs if target is not None and getattr(node, "target", "ALL") == target]
    pool = exact or [node for node in outputs if getattr(node, "target", "ALL") == "ALL"]
    if not pool and target is None:
        pool = outputs
    pool.sort(key=lambda node: not getattr(node, "is_active_output", False))
    return pool[0] if pool else None


def available(socket):
    return not getattr(socket, "is_unavailable", False)


def incoming(tree):
    """The link feeding each input, keyed by (node name, socket identifier).

    A muted or invalid link, and a link into a socket the node does not show
    (an unavailable socket), never feed anything: the renderer ignores them.
    """
    feeds = {}
    for link in tree.links:
        if getattr(link, "is_muted", False) or not getattr(link, "is_valid", True) or not available(link.to_socket):
            continue
        feeds.setdefault((link.to_node.name, link.to_socket.identifier), link)
    return feeds


def passed_inputs(node):
    """The inputs whose values can reach a node's outputs: all of them, or a muted node's pass-through ones."""
    if getattr(node, "mute", False):
        return [link.from_socket for link in getattr(node, "internal_links", ())]
    return list(node.inputs)


def upstream(feeds, owner, socket, expand=None):
    """``(nodes, links)`` the input reaches through links the renderer follows, nearest first.

    ``expand(node)`` says whether to follow a node's own inputs (all by default): what feeds a node whose
    output is unknown anyway cannot change it.
    """
    nodes, links, seen = [], [], set()
    frontier = [(owner, socket)]
    while frontier:
        name, current = frontier.pop(0)
        link = feeds.get((name, current.identifier))
        if link is None:
            continue
        links.append(link)
        node = link.from_node
        if node.name in seen:
            continue
        seen.add(node.name)
        nodes.append(node)
        if expand is None or expand(node):
            frontier.extend((node.name, inner) for inner in passed_inputs(node))
    return nodes, links


def downstream(links, sources):
    """Names of the nodes the ``sources`` reach along ``links`` (themselves included)."""
    reached, frontier = set(sources), list(sources)
    while frontier:
        name = frontier.pop()
        for link in links:
            if link.from_node.name == name and link.to_node.name not in reached:
                reached.add(link.to_node.name)
                frontier.append(link.to_node.name)
    return reached


TEXTURE_NODES = frozenset({"TEX_NOISE", "TEX_VORONOI", "TEX_BRICK", "TEX_CHECKER"})   # texture_kernels


def evaluable(node):
    """Whether the evaluator computes the node's outputs from its inputs (so they matter)."""
    return (getattr(node, "mute", False) or node.type in NODES or node.type in TEXTURE_NODES
            or node.type in {"GROUP", "GROUP_INPUT"})


# ---------------------------------------------------------------- evaluation

class _Split(Exception):
    def __init__(self, key, value):
        super().__init__(key)
        self.key, self.value = key, value


class _AllUnknown(Exception):
    pass


class _Getter:
    """A handler's view of its node's inputs: ``get(key)`` is one definite value (a Spread or UNKNOWN input
    makes the graph run the handler once per member, or give up), ``get.raw(key)`` may be either."""

    def __init__(self, graph, node, pins):
        self.graph, self.node, self.pins = graph, node, pins

    def raw(self, key):
        socket = socket_of(self.node.inputs, key)
        if socket.identifier in self.pins:
            return self.pins[socket.identifier]
        return self.graph.input(socket, self.node.name)

    def __call__(self, key):
        value = self.raw(key)
        if value is UNKNOWN or isinstance(value, Spread):
            raise _Split(socket_of(self.node.inputs, key).identifier, value)
        return value

    def linked(self, key):
        return (self.node.name, socket_of(self.node.inputs, key).identifier) in self.graph.feeds


class Batch:
    """The graphs of every sample of one reading, so that a texture node is computed for all of them at once.

    ``texture_kernels`` works on columns: when a sample first asks for a texture node's output, the node's
    inputs are evaluated on every sample's graph (each graph keeps what it computed), the kernel runs once
    over the samples whose inputs are definite, and each sample reads its row. A texture inside a node group
    is computed the same way, in each sample's graph of that group (``Graph.chain``). A sample whose inputs
    are unknown, and a variant the kernels do not compute, read UNKNOWN.
    """

    def __init__(self):
        self.graphs, self.results = [], {}

    def add(self, graph):
        graph.index = len(self.graphs)
        self.graphs.append(graph)
        return graph

    def texture(self, graph, node, socket):
        key = tuple(group.name for group in graph.chain) + (node.name,)
        if key not in self.results:
            self.results[key] = self._compute(graph.chain, node)
        rows = self.results[key]
        row = rows[graph.root().index] if isinstance(rows, list) else None
        return UNKNOWN if row is None else row.get(socket.identifier, UNKNOWN)

    def _compute(self, chain, node):
        import numpy
        import texture_kernels
        sockets = [socket for socket in node.inputs if available(socket) or socket.identifier == "Exponent"]
        rows, definite = [], []
        for root in self.graphs:
            graph = root
            for group in chain:   # The same node group's graph in this sample.
                graph = graph.group(group)
            values = {}
            for socket in sockets:
                try:
                    if socket.identifier == "Vector" and (node.name, "Vector") not in graph.feeds:
                        value = graph.context.generated()     # An unlinked Vector is Generated.
                    else:
                        value = graph.input(socket, node.name)
                except Unseen:
                    value = UNKNOWN
                if value is UNKNOWN or isinstance(value, Spread) or isinstance(value, Shader):
                    values = None
                    break
                values[socket.identifier] = value
            rows.append(values)
            if values is not None:
                definite.append(root.index)
        if not definite:
            return None
        columns = {key: numpy.asarray([rows[index][key] for index in definite], dtype=numpy.float32)
                   for key in rows[definite[0]]}
        try:
            computed = texture_kernels.evaluate(node, columns)
        except texture_kernels.Unsupported:
            return None
        results = [None] * len(self.graphs)
        for position, index in enumerate(definite):
            results[index] = {key: (tuple(float(v) for v in column[position]) if column.ndim == 2
                                    else float(column[position])) for key, column in computed.items()}
        return results


class Graph:
    """One evaluation of a node tree for one sample (``context``), caching each node's outputs.

    A node group is evaluated as its own graph whose Group Input reads the group node's inputs here. Graphs
    of one reading share a ``Batch`` (texture nodes) and, for nodes per-sample context does not reach
    (outside ``varying``), the ``shared`` outputs.
    """

    def __init__(self, feeds, context, parent=None, shared=None, varying=None, batch=None):
        self.feeds = feeds
        self.context = context
        self.parent = parent          # (outer graph, group node) inside a node group.
        self.shared, self.varying = shared, varying
        self.cache = {}
        self.groups = {}
        self.depth = parent[0].depth if parent else 0
        self.index = 0
        self.chain = parent[0].chain + (parent[1],) if parent else ()   # The group nodes down to this graph.
        if parent is not None:
            self.batch = parent[0].batch
        else:
            self.batch = batch if batch is not None else Batch()
            self.batch.add(self)

    def input(self, socket, owner):
        """An input socket's value, converted to the socket's type (from its link, or its own value)."""
        link = self.feeds.get((owner, socket.identifier))
        if link is None:
            return own_value(socket)
        value = self.output(link.from_node, link.from_socket)
        return convert(value, link.from_socket.type, socket.type)

    def output(self, node, socket):
        key = (node.name, socket.identifier)
        if self.shared is not None and node.name not in self.varying:
            if key not in self.shared:
                self.shared[key] = self._cached(node, socket, key)
            return self.shared[key]
        return self._cached(node, socket, key)

    def _cached(self, node, socket, key):
        if key not in self.cache:
            self.depth += 1
            if self.depth > MAX_DEPTH:
                raise Unreadable("the node graph is deeper than %d nodes (or loops)" % MAX_DEPTH)
            try:
                self.cache[key] = self._evaluate(node, socket)
            finally:
                self.depth -= 1
        return self.cache[key]

    def _evaluate(self, node, socket):
        if getattr(node, "mute", False):
            for link in getattr(node, "internal_links", ()):
                if link.to_socket.identifier == socket.identifier:
                    return convert(self.input(link.from_socket, node.name), link.from_socket.type, socket.type)
            return zero(socket.type)
        if node.type in CONTEXT_NODES:
            return self.context.value(node, socket.identifier)
        if node.type == "GROUP":
            return self._group_output(node, socket)
        if node.type in TEXTURE_NODES:
            return self.batch.texture(self, node, socket)
        if node.type == "GROUP_INPUT":
            if self.parent is None:
                return UNKNOWN
            outer, group = self.parent
            return outer.input(socket_of(group.inputs, socket.identifier), group.name)
        handler = NODES.get(node.type)
        if handler is None:
            return UNKNOWN
        try:
            results = self._lifted(handler, node, {})
        except _AllUnknown:
            return UNKNOWN
        values = [outputs.get(socket.identifier, UNKNOWN) for outputs in results]
        if any(value is UNKNOWN for value in values):
            return UNKNOWN
        flat = []
        for value in values:
            flat.extend(value.values if isinstance(value, Spread) else (value,))
        return spread_or_value(flat)

    def _group_output(self, node, socket):
        tree = getattr(node, "node_tree", None)
        outputs = [inner for inner in getattr(tree, "nodes", ()) if inner.type == "GROUP_OUTPUT"]
        outputs.sort(key=lambda inner: not getattr(inner, "is_active_output", True))
        if not outputs:
            return UNKNOWN
        inner = self.group(node)
        try:
            target = socket_of(outputs[0].inputs, socket.identifier)
        except KeyError:
            return UNKNOWN
        return inner.input(target, outputs[0].name)

    def group(self, node):
        """The graph of the node group ``node`` runs, inside this one (made once)."""
        if node.name not in self.groups:
            self.groups[node.name] = Graph(incoming(node.node_tree), self.context, parent=(self, node))
        inner = self.groups[node.name]
        inner.depth = self.depth
        return inner

    def root(self):
        """The sample's outermost graph (the one its batch row belongs to)."""
        graph = self
        while graph.parent is not None:
            graph = graph.parent[0]
        return graph

    def _lifted(self, handler, node, pins):
        """The handler's outputs for every combination of its Spread inputs' members."""
        try:
            return [handler(self, node, _Getter(self, node, pins))]
        except _Split as split:
            if split.value is UNKNOWN:
                raise _AllUnknown()
            results = []
            for member in split.value.values:
                results += self._lifted(handler, node, dict(pins, **{split.key: member}))
                if len(results) > MAX_SPREAD:
                    raise _AllUnknown()
            return results


def socket_of(sockets, key):
    for socket in sockets:
        if socket.identifier == key:
            return socket
    for socket in sockets:
        if socket.name == key and available(socket):
            return socket
    raise KeyError(key)


def own_value(socket):
    if socket.type == "SHADER":
        return Shader((0.0, 0.0, 0.0))
    value = socket.default_value
    if socket.type in {"VALUE", "INT", "BOOLEAN"}:
        return float(value)
    return tuple(float(v) for v in list(value)[:3])


def zero(kind):
    return Shader((0.0, 0.0, 0.0)) if kind == "SHADER" else 0.0 if kind in {"VALUE", "INT", "BOOLEAN"} else (0.0,) * 3


def convert(value, from_kind, to_kind):
    """Blender's implicit socket conversions (Cycles' ConvertNode; EEVEE agrees), member by member."""
    if value is UNKNOWN:
        return UNKNOWN
    if isinstance(value, Spread):
        return spread_or_value([convert(member, from_kind, to_kind) for member in value.values])
    if to_kind == "SHADER":
        if isinstance(value, Shader):
            return value
        return Shader(value if isinstance(value, tuple) else (value,) * 3)   # A colour into a closure emits it.
    if isinstance(value, Shader):
        return UNKNOWN   # Blender does not convert a closure to a value.
    if to_kind in {"VALUE", "INT", "BOOLEAN"}:
        if not isinstance(value, tuple):
            return value
        if from_kind == "RGBA":
            return sum(weight * channel for weight, channel in zip(LUMINANCE, value))
        return sum(value) / 3.0
    return value if isinstance(value, tuple) else (value,) * 3


def _samples(low, high, count=SEGMENT_SAMPLES):
    return [low + (high - low) * index / count for index in range(count + 1)]


# ---------------------------------------------------------------- arithmetic shared by nodes

def _lerp(a, b, t):
    return a + (b - a) * t


def _clamp(value, low=0.0, high=1.0):
    return min(max(value, low), high)


def _safe_divide(a, b):
    return a / b if b != 0.0 else 0.0


def _wrap(value, high, low):
    span = high - low
    return value - span * math.floor((value - low) / span) if span != 0.0 else low


def _smooth_min(a, b, c):
    if c != 0.0:
        h = max(c - abs(a - b), 0.0) / c
        return min(a, b) - h * h * h * c * (1.0 / 6.0)
    return min(a, b)


def _power(a, b):
    if a < 0.0 and b != float(int(b)):
        return 0.0
    try:
        return math.pow(a, b)
    except (OverflowError, ValueError, ZeroDivisionError):
        raise _AllUnknown() from None


MATH = {
    "ADD": lambda a, b, c: a + b, "SUBTRACT": lambda a, b, c: a - b, "MULTIPLY": lambda a, b, c: a * b,
    "DIVIDE": lambda a, b, c: _safe_divide(a, b), "MULTIPLY_ADD": lambda a, b, c: a * b + c,
    "POWER": lambda a, b, c: _power(a, b),
    "LOGARITHM": lambda a, b, c: _safe_divide(math.log(a), math.log(b)) if a > 0.0 and b > 0.0 else 0.0,
    "SQRT": lambda a, b, c: math.sqrt(a) if a > 0.0 else 0.0,
    "INVERSE_SQRT": lambda a, b, c: 1.0 / math.sqrt(a) if a > 0.0 else 0.0,
    "ABSOLUTE": lambda a, b, c: abs(a), "EXPONENT": lambda a, b, c: math.exp(min(a, 700.0)),
    "MINIMUM": lambda a, b, c: min(a, b), "MAXIMUM": lambda a, b, c: max(a, b),
    "LESS_THAN": lambda a, b, c: float(a < b), "GREATER_THAN": lambda a, b, c: float(a > b),
    "SIGN": lambda a, b, c: float((a > 0.0) - (a < 0.0)),
    "COMPARE": lambda a, b, c: float(abs(a - b) <= max(c, 1e-5)),
    "SMOOTH_MIN": _smooth_min, "SMOOTH_MAX": lambda a, b, c: -_smooth_min(-a, -b, c),
    "ROUND": lambda a, b, c: math.floor(a + 0.5), "FLOOR": lambda a, b, c: math.floor(a),
    "CEIL": lambda a, b, c: math.ceil(a), "TRUNC": lambda a, b, c: float(math.trunc(a)),
    "FRACT": lambda a, b, c: a - math.floor(a),
    "MODULO": lambda a, b, c: math.fmod(a, b) if b != 0.0 else 0.0,
    "FLOORED_MODULO": lambda a, b, c: a - math.floor(a / b) * b if b != 0.0 else 0.0,
    "WRAP": _wrap, "SNAP": lambda a, b, c: math.floor(_safe_divide(a, b)) * b,
    "PINGPONG": lambda a, b, c: abs((((a - b) / (b * 2.0)) % 1.0) * b * 2.0 - b) if b != 0.0 else 0.0,
    "SINE": lambda a, b, c: math.sin(a), "COSINE": lambda a, b, c: math.cos(a),
    "TANGENT": lambda a, b, c: math.tan(a), "ARCSINE": lambda a, b, c: math.asin(_clamp(a, -1.0, 1.0)),
    "ARCCOSINE": lambda a, b, c: math.acos(_clamp(a, -1.0, 1.0)), "ARCTANGENT": lambda a, b, c: math.atan(a),
    "ARCTAN2": lambda a, b, c: math.atan2(a, b), "SINH": lambda a, b, c: math.sinh(_clamp(a, -700.0, 700.0)),
    "COSH": lambda a, b, c: math.cosh(_clamp(a, -700.0, 700.0)), "TANH": lambda a, b, c: math.tanh(a),
    "RADIANS": lambda a, b, c: math.radians(a), "DEGREES": lambda a, b, c: math.degrees(a),
}


def hsv(rgb):
    return colorsys.rgb_to_hsv(*(max(0.0, v) for v in rgb))


def _rgb_from_hsv(value):
    return colorsys.hsv_to_rgb(value[0] % 1.0, value[1], value[2])


def _blend(kind, t, a, b):
    """Blender's colour blend (``ramp_blend``/Cycles ``svm_mix``) of A and B by factor ``t``."""
    m = 1.0 - t
    per = lambda f: tuple(f(x, y) for x, y in zip(a, b))   # noqa: E731
    if kind == "MIX":
        return per(lambda x, y: m * x + t * y)
    if kind == "ADD":
        return per(lambda x, y: x + t * y)
    if kind == "MULTIPLY":
        return per(lambda x, y: x * (m + t * y))
    if kind == "SUBTRACT":
        return per(lambda x, y: x - t * y)
    if kind == "SCREEN":
        return per(lambda x, y: 1.0 - (m + t * (1.0 - y)) * (1.0 - x))
    if kind == "DIVIDE":
        return per(lambda x, y: m * x + t * x / y if y != 0.0 else x)
    if kind == "DIFFERENCE":
        return per(lambda x, y: _lerp(x, abs(x - y), t))
    if kind == "EXCLUSION":
        return per(lambda x, y: max(_lerp(x, x + y - 2.0 * x * y, t), 0.0))
    if kind == "DARKEN":
        return per(lambda x, y: _lerp(x, min(x, y), t))
    if kind == "LIGHTEN":
        return per(lambda x, y: _lerp(x, max(x, y), t))
    if kind == "OVERLAY":
        return per(lambda x, y: x * (m + 2.0 * t * y) if x < 0.5 else 1.0 - (m + 2.0 * t * (1.0 - y)) * (1.0 - x))
    if kind == "SOFT_LIGHT":
        return per(lambda x, y: m * x + t * ((1.0 - x) * y * x + x * (1.0 - (1.0 - y) * (1.0 - x))))
    if kind == "LINEAR_LIGHT":
        return per(lambda x, y: x + t * (2.0 * y - 1.0))
    if kind == "DODGE":
        return per(lambda x, y: x if x == 0.0 else 1.0 if 1.0 - t * y <= 0.0 else min(x / (1.0 - t * y), 1.0))
    if kind == "BURN":
        return per(lambda x, y: 0.0 if m + t * y <= 0.0 else _clamp(1.0 - (1.0 - x) / (m + t * y)))
    if kind in {"HUE", "COLOR"}:
        other = hsv(b)
        if other[1] == 0.0:
            return a
        own = list(hsv(a))
        own[0] = other[0]
        if kind == "COLOR":
            own[1] = other[1]
        shifted = _rgb_from_hsv(own)
        return tuple(_lerp(x, y, t) for x, y in zip(a, shifted))
    if kind == "SATURATION":
        own = list(hsv(a))
        if own[1] == 0.0:
            return a
        own[1] = m * own[1] + t * hsv(b)[1]
        return _rgb_from_hsv(own)
    if kind == "VALUE":
        own = list(hsv(a))
        own[2] = m * own[2] + t * hsv(b)[2]
        return _rgb_from_hsv(own)
    raise _AllUnknown()


def _factor_members(raw, clamp):
    """The factors a clamped factor input can take: itself, its members, or all of 0..1 when unknown."""
    if raw is UNKNOWN:
        if not clamp:
            raise _AllUnknown()
        return _samples(0.0, 1.0)
    values = raw.values if isinstance(raw, Spread) else (raw,)
    return [_clamp(v) if clamp else v for v in values]


# ---------------------------------------------------------------- nodes

def _math(graph, node, get):
    operation = MATH.get(node.operation)
    if operation is None:
        raise _AllUnknown()
    clamp = bool(getattr(node, "use_clamp", False))
    try:
        result = operation(get("Value"), get("Value_001"), get("Value_002"))
    except _Split as split:
        if split.value is UNKNOWN and clamp:   # A clamped result stays in 0..1 whatever the input.
            return {"Value": Spread(_samples(0.0, 1.0))}
        raise
    return {"Value": _clamp(result) if clamp else float(result)}


def _vector_math(graph, node, get):
    a, b = get("Vector"), get("Vector_001")
    per = lambda f: tuple(f(x, y) for x, y in zip(a, b))   # noqa: E731
    dot = sum(x * y for x, y in zip(a, b))
    length = math.sqrt(sum(x * x for x in a))
    kind = node.operation
    vectors = {
        "ADD": lambda: per(lambda x, y: x + y), "SUBTRACT": lambda: per(lambda x, y: x - y),
        "MULTIPLY": lambda: per(lambda x, y: x * y), "DIVIDE": lambda: per(_safe_divide),
        "MULTIPLY_ADD": lambda: tuple(x * y + z for x, y, z in zip(a, b, get("Vector_002"))),
        "CROSS_PRODUCT": lambda: (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]),
        "SCALE": lambda: tuple(x * get("Scale") for x in a),
        "NORMALIZE": lambda: tuple(x / length for x in a) if length else (0.0, 0.0, 0.0),
        "ABSOLUTE": lambda: tuple(abs(x) for x in a), "MINIMUM": lambda: per(min), "MAXIMUM": lambda: per(max),
        "FLOOR": lambda: tuple(float(math.floor(x)) for x in a), "CEIL": lambda: tuple(float(math.ceil(x)) for x in a),
        "FRACTION": lambda: tuple(x - math.floor(x) for x in a),
        "MODULO": lambda: per(lambda x, y: math.fmod(x, y) if y != 0.0 else 0.0),
        "WRAP": lambda: tuple(_wrap(x, y, z) for x, y, z in zip(a, b, get("Vector_002"))),
        "SNAP": lambda: per(lambda x, y: math.floor(_safe_divide(x, y)) * y),
        "SINE": lambda: tuple(math.sin(x) for x in a), "COSINE": lambda: tuple(math.cos(x) for x in a),
        "TANGENT": lambda: tuple(math.tan(x) for x in a),
        "POWER": lambda: per(_power), "SIGN": lambda: tuple(float((x > 0.0) - (x < 0.0)) for x in a),
    }
    values = {"DOT_PRODUCT": lambda: dot, "LENGTH": lambda: length,
              "DISTANCE": lambda: math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))}
    if kind in vectors:
        return {"Vector": vectors[kind](), "Value": 0.0}
    if kind in values:
        return {"Vector": (0.0, 0.0, 0.0), "Value": values[kind]()}
    raise _AllUnknown()


def _map_one(value, low, high, to_low, to_high, steps, kind, clamp):
    if kind in {"LINEAR", "STEPPED"}:
        factor = _safe_divide(value - low, high - low)
        if kind == "STEPPED":
            factor = math.floor(factor * (steps + 1.0)) / steps if steps > 0.0 else 0.0
        result = to_low + factor * (to_high - to_low)
        if clamp:
            result = _clamp(result, min(to_low, to_high), max(to_low, to_high))
        return result
    if kind in {"SMOOTHSTEP", "SMOOTHERSTEP"}:
        def smooth(edge0, edge1, x):
            if x <= edge0:
                return 0.0
            if x >= edge1:
                return 1.0
            t = (x - edge0) / (edge1 - edge0)
            return t * t * (3.0 - 2.0 * t) if kind == "SMOOTHSTEP" else t * t * t * (t * (t * 6.0 - 15.0) + 10.0)
        factor = 1.0 - smooth(high, low, value) if low > high else smooth(low, high, value)
        return to_low + factor * (to_high - to_low)
    raise _AllUnknown()


def _map_range(graph, node, get):
    kind, clamp = node.interpolation_type, bool(getattr(node, "clamp", True))
    # Its output stays inside To Min..To Max whatever the value when it clamps or eases (stepped: on its steps).
    bounded = (clamp and kind in {"LINEAR", "STEPPED"}) or kind in {"SMOOTHSTEP", "SMOOTHERSTEP"}
    if getattr(node, "data_type", "FLOAT") == "FLOAT":
        to_low, to_high = get("To Min"), get("To Max")
        value = get.raw("Value")
        if value is UNKNOWN and bounded:
            if kind == "STEPPED":
                steps = get("Steps")
                points = [index / steps for index in range(int(steps) + 1)] if steps >= 1 else [0.0]
                return {"Result": Spread(_lerp(to_low, to_high, f) for f in points), "Vector": (0.0, 0.0, 0.0)}
            return {"Result": Spread(_samples(to_low, to_high)), "Vector": (0.0, 0.0, 0.0)}
        steps = get("Steps") if kind == "STEPPED" else 0.0
        return {"Result": _map_one(get("Value"), get("From Min"), get("From Max"), to_low, to_high, steps, kind,
                                   clamp), "Vector": (0.0, 0.0, 0.0)}
    steps = get("Steps_FLOAT3") if kind == "STEPPED" else (0.0, 0.0, 0.0)
    columns = zip(get("Vector"), get("From_Min_FLOAT3"), get("From_Max_FLOAT3"), get("To_Min_FLOAT3"),
                  get("To_Max_FLOAT3"), steps)
    return {"Vector": tuple(_map_one(*column, kind, clamp) for column in columns), "Result": 0.0}


def _clamp_node(graph, node, get):
    low, high = get("Min"), get("Max")
    if getattr(node, "clamp_type", "MINMAX") == "RANGE" and low > high:
        low, high = high, low
    value = get.raw("Value")
    if value is UNKNOWN:
        return {"Result": Spread(_samples(low, high)) if low <= high else high}
    return {"Result": min(max(get("Value"), low), high)}


def _mix(graph, node, get):
    kind, clamp = node.data_type, bool(getattr(node, "clamp_factor", True))
    if kind == "RGBA":
        a, b = get("A_Color"), get("B_Color")
        results = [_blend(node.blend_type, t, a, b) for t in _factor_members(get.raw("Factor_Float"), clamp)]
        if getattr(node, "clamp_result", False):
            results = [tuple(_clamp(v) for v in result) for result in results]
        return {"Result_Color": spread_or_value([tuple(result) for result in results])}
    if kind == "FLOAT":
        a, b = get("A_Float"), get("B_Float")
        return {"Result_Float": spread_or_value([_lerp(a, b, t) for t in _factor_members(get.raw("Factor_Float"),
                                                                                         clamp)])}
    if kind == "VECTOR":
        a, b = get("A_Vector"), get("B_Vector")
        if getattr(node, "factor_mode", "UNIFORM") == "UNIFORM":
            return {"Result_Vector": spread_or_value([tuple(_lerp(x, y, t) for x, y in zip(a, b))
                                                      for t in _factor_members(get.raw("Factor_Float"), clamp)])}
        factors = get("Factor_Vector")
        factors = tuple(_clamp(v) if clamp else v for v in factors)
        return {"Result_Vector": tuple(_lerp(x, y, f) for x, y, f in zip(a, b, factors))}
    raise _AllUnknown()


def _mix_rgb(graph, node, get):
    a, b = get("Color1"), get("Color2")
    results = [_blend(node.blend_type, t, a, b) for t in _factor_members(get.raw("Fac"), True)]
    if getattr(node, "use_clamp", False):
        results = [tuple(_clamp(v) for v in result) for result in results]
    return {"Color": spread_or_value([tuple(result) for result in results])}


def _ramp_at(node, position):
    rgba = list(node.color_ramp.evaluate(_clamp(position)))
    return tuple(float(v) for v in rgba[:3]), (float(rgba[3]) if len(rgba) > 3 else 1.0)


def _ramp(graph, node, get):
    raw = get.raw("Fac")
    if raw is UNKNOWN:   # Any factor: one of the ramp's colours.
        positions = _samples(0.0, 1.0, RAMP_SAMPLES) + [_clamp(element.position)
                                                        for element in node.color_ramp.elements]
    else:
        positions = raw.values if isinstance(raw, Spread) else (raw,)
    samples = [_ramp_at(node, position) for position in positions]
    return {"Color": spread_or_value([colour for colour, _ in samples]),
            "Alpha": spread_or_value([alpha for _, alpha in samples])}


def _separate_xyz(graph, node, get):
    x, y, z = get("Vector")
    return {"X": x, "Y": y, "Z": z}


def _combine_xyz(graph, node, get):
    return {"Vector": (get("X"), get("Y"), get("Z"))}


def _hls_as_hsl(rgb):
    hue, lightness, saturation = colorsys.rgb_to_hls(*(max(0.0, v) for v in rgb))
    return hue, saturation, lightness


def _separate_color(graph, node, get):
    rgb = get("Color")
    mode = getattr(node, "mode", "RGB")
    parts = rgb if mode == "RGB" else hsv(rgb) if mode == "HSV" else _hls_as_hsl(rgb) if mode == "HSL" else None
    if parts is None:
        raise _AllUnknown()
    return dict(zip(("Red", "Green", "Blue"), (float(v) for v in parts)))


def _combine_color(graph, node, get):
    a, b, c = get("Red"), get("Green"), get("Blue")
    mode = getattr(node, "mode", "RGB")
    if mode == "RGB":
        return {"Color": (a, b, c)}
    if mode == "HSV":
        return {"Color": tuple(colorsys.hsv_to_rgb(a % 1.0, _clamp(b), c))}
    if mode == "HSL":
        return {"Color": tuple(colorsys.hls_to_rgb(a % 1.0, c, _clamp(b)))}
    raise _AllUnknown()


def _hue_saturation(graph, node, get):
    colour, fac = get("Color"), get("Fac")
    hue, saturation, value = hsv(colour)
    shifted = _rgb_from_hsv(((hue + get("Hue") + 0.5) % 1.0, _clamp(saturation * get("Saturation")),
                             value * get("Value")))
    return {"Color": tuple(max(_lerp(x, y, fac), 0.0) for x, y in zip(colour, shifted))}


def _invert(graph, node, get):
    colour, fac = get("Color"), get("Fac")
    return {"Color": tuple(_lerp(x, 1.0 - x, fac) for x in colour)}


def _gamma(graph, node, get):
    gamma = get("Gamma")
    if gamma == 0.0:
        return {"Color": (1.0, 1.0, 1.0)}
    return {"Color": tuple(_power(x, gamma) if x > 0.0 else x for x in get("Color"))}


def _bright_contrast(graph, node, get):
    contrast = get("Contrast")
    a, b = 1.0 + contrast, get("Bright") - contrast * 0.5
    return {"Color": tuple(max(a * x + b, 0.0) for x in get("Color"))}


def _rgb_to_bw(graph, node, get):
    return {"Val": sum(w * c for w, c in zip(LUMINANCE, get("Color")))}


def _identity_mapping(node):
    mapping = getattr(node, "texture_mapping", None)
    if mapping is None:
        return True
    return (all(abs(v) < 1e-9 for v in mapping.translation) and all(abs(v) < 1e-9 for v in mapping.rotation)
            and all(abs(v - 1.0) < 1e-9 for v in mapping.scale))


def _gradient(graph, node, get):
    if not _identity_mapping(node):
        raise _AllUnknown()
    # An unlinked Vector is the Generated coordinate.
    if get.linked("Vector"):
        x, y, z = get("Vector")
    else:
        generated = graph.context.generated()
        if generated is UNKNOWN or isinstance(generated, Spread):
            raise _AllUnknown()
        x, y, z = generated
    kind = node.gradient_type
    if kind == "LINEAR":
        f = x
    elif kind == "QUADRATIC":
        f = max(x, 0.0) ** 2
    elif kind == "EASING":
        r = _clamp(x)
        f = 3.0 * r * r - 2.0 * r * r * r
    elif kind == "DIAGONAL":
        f = (x + y) * 0.5
    elif kind == "RADIAL":
        f = math.atan2(y, x) / (2.0 * math.pi) + 0.5
    elif kind == "QUADRATIC_SPHERE":
        f = max(0.999999 - math.sqrt(x * x + y * y + z * z), 0.0) ** 2
    elif kind == "SPHERICAL":
        f = max(0.999999 - math.sqrt(x * x + y * y + z * z), 0.0)
    else:
        raise _AllUnknown()
    f = _clamp(f)
    return {"Fac": f, "Color": (f, f, f)}


def _euler_matrix(rotation):
    """Rows of Blender's XYZ Euler rotation (Rz . Ry . Rx), applied as ``matrix @ vector``."""
    cx, cy, cz = (math.cos(v) for v in rotation)
    sx, sy, sz = (math.sin(v) for v in rotation)
    return ((cy * cz, sy * sx * cz - cx * sz, sy * cx * cz + sx * sz),
            (cy * sz, sy * sx * sz + cx * cz, sy * cx * sz - sx * cz),
            (-sy, cy * sx, cy * cx))


def _mapping(graph, node, get):
    vector, rotation, scale = get("Vector"), get("Rotation"), get("Scale")
    matrix = _euler_matrix(rotation)
    apply = lambda m, v: tuple(sum(m[r][c] * v[c] for c in range(3)) for r in range(3))   # noqa: E731
    kind = node.vector_type
    if kind == "POINT":
        moved = apply(matrix, tuple(v * s for v, s in zip(vector, scale)))
        return {"Vector": tuple(m + l for m, l in zip(moved, get("Location")))}
    if kind == "TEXTURE":
        transposed = tuple(tuple(matrix[c][r] for c in range(3)) for r in range(3))
        moved = apply(transposed, tuple(v - l for v, l in zip(vector, get("Location"))))
        return {"Vector": tuple(_safe_divide(m, s) for m, s in zip(moved, scale))}
    if kind == "VECTOR":
        return {"Vector": apply(matrix, tuple(v * s for v, s in zip(vector, scale)))}
    if kind == "NORMAL":
        moved = apply(matrix, tuple(_safe_divide(v, s) for v, s in zip(vector, scale)))
        length = math.sqrt(sum(m * m for m in moved))
        return {"Vector": tuple(m / length for m in moved) if length else (0.0, 0.0, 0.0)}
    raise _AllUnknown()


def _emitter(graph, node, get):
    strength = get("Strength")
    return {node.outputs[0].identifier: Shader(tuple(channel * strength for channel in get("Color")))}


def _mix_shader(graph, node, get):
    first, second = get("Shader"), get("Shader_001")
    return {"Shader": spread_or_value([Shader(_lerp(x, y, t) for x, y in zip(first.rgb, second.rgb))
                                       for t in _factor_members(get.raw("Fac"), True)])}


def _add_shader(graph, node, get):
    return {"Shader": Shader(x + y for x, y in zip(get("Shader").rgb, get("Shader_001").rgb))}


def _reroute(graph, node, get):
    return {node.outputs[0].identifier: graph.input(node.inputs[0], node.name)}


def _constant(graph, node, get):
    socket = node.outputs[0]
    value = socket.default_value
    return {socket.identifier: float(value) if node.type == "VALUE" else tuple(float(v) for v in list(value)[:3])}


NODES = {
    "MATH": _math, "VECT_MATH": _vector_math, "MAP_RANGE": _map_range, "CLAMP": _clamp_node, "MIX": _mix,
    "MIX_RGB": _mix_rgb, "VALTORGB": _ramp, "SEPXYZ": _separate_xyz, "COMBXYZ": _combine_xyz,
    "SEPARATE_COLOR": _separate_color, "COMBINE_COLOR": _combine_color, "HUE_SAT": _hue_saturation,
    "INVERT": _invert, "GAMMA": _gamma, "BRIGHTCONTRAST": _bright_contrast, "RGBTOBW": _rgb_to_bw,
    "TEX_GRADIENT": _gradient, "MAPPING": _mapping, "BACKGROUND": _emitter, "EMISSION": _emitter,
    "MIX_SHADER": _mix_shader, "ADD_SHADER": _add_shader, "REROUTE": _reroute, "RGB": _constant,
    "VALUE": _constant,
}
