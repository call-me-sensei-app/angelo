# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a material input shows over the surface that uses it, read from its node graph (``shader_graph``).

``read(material, node, identifier, source)`` evaluates one input (a Principled
BSDF's Base Color, Roughness or Metallic, an Emission colour) and returns
``Reading``: a list of ``(value, weight)`` over the faces that show the
material, weighted by area. A value is definite, a ``Spread`` of the values
it can take, or ``UNKNOWN`` (``shader_graph``).

* An input that depends on nothing per point (a constant, an RGB node, a ramp
  or mix of them, a texture at a fixed coordinate) is read once: one value of
  weight 1.
* An input that reads the surface is evaluated at ``MAX_SAMPLES`` points
  spread over the faces that show the material in proportion to their area:
  stratified over the triangles' cumulative area, each point placed inside its
  triangle by a low-discrepancy sequence, all of equal weight. At each point
  the data is interpolated as the renderer interpolates it: attributes on
  points and corners barycentrically (face attributes are constant), the
  object-space position (Texture Coordinate Object), its world position
  (Geometry Position), the shading normal from the corner normals (Geometry
  Normal in world space, Texture Coordinate Normal in object space), the
  face's normal (True Normal), the active render UV map (Texture Coordinate
  UV) and the texture-space coordinate (Generated: the position mapped by the
  mesh's texture space, as Cycles maps a mesh without deforming modifiers).
  Noise, Voronoi, Brick and Checker textures are then computed exactly at
  every point at once (``texture_kernels``).
* Anything the context does not have (camera coordinates, an instancer's
  attributes, a missing attribute) is UNKNOWN.

``SurfaceSource`` names the mesh and faces: an object's evaluated mesh, its
world matrix and the material's slots on it. Without a source (instanced
geometry, a non-mesh object) per-point inputs are UNKNOWN. Read-only.
"""

import shader_graph
from shader_graph import UNKNOWN

MAX_SAMPLES = 1024        # Points read per material: an area share within about 1.5 % (1 sigma), stratified.
FACE_SAMPLES = 256        # Points read on the few faces a ray met (``SurfaceSource.faces``).
MAX_GROUP_DEPTH = 16      # Node groups looked into, one inside another, for what they read per point.
PLASTIC = 1.32471795724474602596   # The R2 sequence's constant: evenly spread points inside each triangle.
CONTEXT_FIELDS = {("NEW_GEOMETRY", "Position"): "position", ("NEW_GEOMETRY", "Normal"): "normal",
                  ("NEW_GEOMETRY", "True Normal"): "true_normal", ("TEX_COORD", "Object"): "local",
                  ("TEX_COORD", "Generated"): "generated", ("TEX_COORD", "UV"): "uv",
                  ("TEX_COORD", "Normal"): "local_normal"}


class Reading(list):
    """``[(value, weight)]`` with the nodes the grader could not evaluate and how many points were read."""

    def __init__(self, items=(), unknown_nodes=(), samples=1):
        super().__init__(items)
        self.unknown_nodes = sorted(set(unknown_nodes))
        self.samples = samples


class SurfaceSource:
    """The faces an object shows a material on: ``mesh`` (evaluated), ``matrix`` (world, 4 x 4 rows) and the
    material slot indices that hold it (``slots``); ``faces`` narrows it to some polygons."""

    def __init__(self, mesh, matrix, slots, faces=None):
        self.mesh, self.matrix, self.slots, self.faces = mesh, matrix, set(slots), faces


def object_source(obj, depsgraph, material, faces=None):
    """The ``SurfaceSource`` of a material on a mesh object's evaluated geometry, or None."""
    if getattr(obj, "type", None) != "MESH":
        return None
    try:
        evaluated = obj.evaluated_get(depsgraph) if depsgraph is not None else obj
        mesh = evaluated.data
        names = [slot.material.name if slot.material is not None else None for slot in evaluated.material_slots]
        matrix = [list(row) for row in evaluated.matrix_world]
    except (AttributeError, RuntimeError, TypeError):
        return None
    name = getattr(getattr(material, "original", None) or material, "name", None)
    slots = [index for index, slot in enumerate(names) if slot == name]
    return SurfaceSource(mesh, matrix, slots or ([0] if not names else []), faces)


class SurfaceContext(shader_graph.Context):
    """One surface point's values: ``fields`` maps ``("attr", name)`` and the names in ``CONTEXT_FIELDS``."""

    def __init__(self, fields, kinds):
        self.fields, self.kinds = fields, kinds

    def value(self, node, identifier):
        if node.type == "ATTRIBUTE" and getattr(node, "attribute_type", "GEOMETRY") == "GEOMETRY":
            return _attribute_output(self.fields.get(("attr", node.attribute_name), UNKNOWN),
                                     self.kinds.get(node.attribute_name), identifier)
        if node.type == "VERTEX_COLOR":
            name = self.kinds.get(("layer", node.layer_name))
            if name is None:
                return UNKNOWN
            return _attribute_output(self.fields.get(("attr", name), UNKNOWN), self.kinds.get(name),
                                     "Color" if identifier == "Color" else "Alpha")
        key = CONTEXT_FIELDS.get((node.type, identifier))
        if key is not None and not (node.type == "TEX_COORD" and getattr(node, "object", None) is not None):
            return self.fields.get(key, UNKNOWN)
        return super().value(node, identifier)

    def generated(self):
        return self.fields.get("generated", UNKNOWN)


def _attribute_output(raw, kind, identifier):
    """An Attribute node's output for one element's raw attribute value (Cycles' rules)."""
    if raw is UNKNOWN or kind is None:
        return UNKNOWN
    if kind in {"FLOAT_COLOR", "BYTE_COLOR"}:
        rgb = tuple(raw[:3])
        return {"Color": rgb, "Vector": rgb, "Fac": sum(rgb) / 3.0, "Alpha": raw[3]}.get(identifier, UNKNOWN)
    if kind in {"FLOAT_VECTOR", "FLOAT2"}:
        vector = tuple(raw) + (0.0,) * (3 - len(raw))
        return {"Color": vector, "Vector": vector, "Fac": sum(vector) / 3.0, "Alpha": 1.0}.get(identifier, UNKNOWN)
    value = float(raw[0])
    return {"Color": (value,) * 3, "Vector": (value,) * 3, "Fac": value, "Alpha": 1.0}.get(identifier, UNKNOWN)


_LAYOUT = {"FLOAT_COLOR": ("color", 4, "f"), "BYTE_COLOR": ("color", 4, "f"), "FLOAT": ("value", 1, "f"),
           "INT": ("value", 1, "i"), "INT8": ("value", 1, "i"), "BOOLEAN": ("value", 1, "?"),
           "FLOAT_VECTOR": ("vector", 3, "f"), "FLOAT2": ("vector", 2, "f")}


def _array(collection, attribute, width, kind="f"):
    """One property of every item of a collection as ``(n, width)`` floats (``foreach_get`` when it works)."""
    import numpy
    dtype = {"f": numpy.float32, "i": numpy.int32, "?": numpy.bool_}[kind]
    buffer = numpy.zeros(len(collection) * width, dtype=dtype)
    try:
        collection.foreach_get(attribute, buffer)
    except (AttributeError, TypeError, RuntimeError):   # A stand-in mesh in a host test, or a picky property.
        values = []
        for item in collection:
            value = getattr(item, attribute)
            values.extend(list(value) if hasattr(value, "__len__") else [value])
        buffer = numpy.array(values, dtype=dtype)
    return buffer.astype(numpy.float64).reshape(-1, width)


def _needed(feeds, node, socket, depth=0):
    """``(fields the input reads per point, nodes the grader cannot evaluate, nodes those fields reach)``.

    A node group's own graph is looked into (``_group_needs``): what it reads per point makes the group node
    vary per point too.
    """
    nodes, links = shader_graph.upstream(feeds, node.name, socket, shader_graph.evaluable)
    fields, unknown, sources = set(), [], []
    for link in links:
        source, output = link.from_node, link.from_socket.identifier
        if source.type == "ATTRIBUTE" and getattr(source, "attribute_type", "GEOMETRY") == "GEOMETRY":
            fields.add(("attr", source.attribute_name))
        elif source.type == "VERTEX_COLOR":
            fields.add(("layer", source.layer_name))
        elif (source.type, output) in CONTEXT_FIELDS:
            fields.add(CONTEXT_FIELDS[(source.type, output)])
        else:
            continue
        sources.append(source.name)
    for inner in nodes:
        if inner.type in shader_graph.TEXTURE_NODES and (inner.name, "Vector") not in feeds:
            fields.add("generated")   # An unlinked Vector reads the Generated coordinate.
            sources.append(inner.name)
        if inner.type == "GROUP" and not getattr(inner, "mute", False):
            inside, unread = _group_needs(inner, depth + 1)
            fields |= inside
            unknown += unread
            if inside:
                sources.append(inner.name)
        if not shader_graph.evaluable(inner) and inner.type not in shader_graph.CONTEXT_NODES:
            unknown.append("%s %r" % (inner.type, inner.name))
        elif inner.type in shader_graph.CONTEXT_NODES and inner.type not in {"ATTRIBUTE", "VERTEX_COLOR",
                                                                               "NEW_GEOMETRY", "TEX_COORD",
                                                                               "LIGHT_PATH"}:
            unknown.append("%s %r" % (inner.type, inner.name))
    return fields, unknown, shader_graph.downstream(links, sources)


def _group_needs(group, depth):
    """``(fields, unreadable nodes)`` of what a node group's outputs read inside it (``_needed``)."""
    tree = getattr(group, "node_tree", None)
    if tree is None or depth > MAX_GROUP_DEPTH:
        return set(), ([] if tree is None else ["GROUP %r (nested too deep)" % group.name])
    feeds, fields, unknown = shader_graph.incoming(tree), set(), []
    for output in (inner for inner in tree.nodes if inner.type == "GROUP_OUTPUT"):
        for socket in output.inputs:
            inside, unread, _ = _needed(feeds, output, socket, depth)
            fields |= inside
            unknown += unread
    return fields, unknown


def _default_colour_name(mesh):
    attributes = getattr(mesh, "color_attributes", None)
    if not attributes:
        return None
    index = getattr(attributes, "render_color_index", 0)
    try:
        return attributes[index].name
    except (IndexError, KeyError, TypeError):
        return None


def _normalised(vectors):
    import numpy
    length = numpy.linalg.norm(vectors, axis=1, keepdims=True)
    return numpy.where(length > 0.0, vectors / numpy.where(length > 0.0, length, 1.0), 0.0)


def _points(mesh, source, count):
    """``(triangles, barycentric weights)`` of ``count`` points spread over the source's faces by area, or
    None when no face shows the material."""
    import numpy
    triangles = mesh.loop_triangles
    polygon = _array(triangles, "polygon_index", 1, "i")[:, 0].astype(numpy.int64)
    area = _array(triangles, "area", 1)[:, 0]
    index = _array(mesh.polygons, "material_index", 1, "i")[:, 0].astype(numpy.int64)
    keep = numpy.isin(index[polygon], sorted(source.slots)) if source.slots else numpy.ones(len(polygon), bool)
    if source.faces is not None:
        keep &= numpy.isin(polygon, sorted(source.faces))
    kept = numpy.nonzero(keep & (area > 0.0))[0]
    if not len(kept):
        return None
    cumulative = numpy.cumsum(area[kept])
    targets = (numpy.arange(count) + 0.5) / count * cumulative[-1]
    chosen = kept[numpy.minimum(numpy.searchsorted(cumulative, targets), len(kept) - 1)]
    order = numpy.arange(count)
    first, second = (0.5 + order / PLASTIC) % 1.0, (0.5 + order / PLASTIC ** 2) % 1.0
    root = numpy.sqrt(first)
    weights = numpy.stack([1.0 - root, root * (1.0 - second), root * second], axis=1)
    return chosen, weights


def _fields(mesh, source, fields, count, points=None):
    """``(per-point field values, kinds of the attributes read, unread names)`` at ``count`` points spread by
    area, or at ``points`` (``(triangle indices, barycentric weights)``) when given."""
    import numpy
    found = points if points is not None else _points(mesh, source, count)
    if found is None:
        return None
    chosen, weights = found
    count = len(chosen)
    triangles = mesh.loop_triangles
    vertices = _array(triangles, "vertices", 3, "i").astype(numpy.int64)[chosen]
    loops = _array(triangles, "loops", 3, "i").astype(numpy.int64)[chosen]
    polygons = _array(triangles, "polygon_index", 1, "i")[:, 0].astype(numpy.int64)[chosen]

    def blend(values, corners):
        return numpy.einsum("nk,nkc->nc", weights, values[corners])
    matrix = numpy.array(source.matrix if source.matrix is not None else numpy.eye(4), dtype=numpy.float64)
    rotation = matrix[:3, :3]
    columns, kinds, unread = {}, {}, []
    local = blend(_array(mesh.vertices, "co", 3), vertices)
    columns["local"] = local
    columns["position"] = local @ rotation.T + matrix[:3, 3]
    if fields & {"normal", "local_normal"}:
        corner = getattr(mesh, "corner_normals", None)
        normals = _array(corner, "vector", 3) if corner is not None and len(corner) else None
        if normals is not None:
            local_normal = _normalised(blend(normals, loops))
            columns["local_normal"] = local_normal
            columns["normal"] = _normalised(local_normal @ numpy.linalg.inv(rotation))
    if "true_normal" in fields:
        face_normals = _array(mesh.polygons, "normal", 3)[polygons]
        columns["true_normal"] = _normalised(face_normals @ numpy.linalg.inv(rotation))
    if "uv" in fields:
        layers = [layer for layer in getattr(mesh, "uv_layers", ()) if getattr(layer, "active_render", False)]
        layers = layers or list(getattr(mesh, "uv_layers", ()))[:1]
        uv = blend(_array(layers[0].data, "uv", 2), loops) if layers else numpy.zeros((count, 2))
        columns["uv"] = numpy.concatenate([uv, numpy.zeros((count, 1))], axis=1)   # No UV map reads 0, 0, 0.
    if "generated" in fields:
        location = numpy.array(list(getattr(mesh, "texspace_location", (0.0, 0.0, 0.0))), dtype=numpy.float64)
        size = numpy.array(list(getattr(mesh, "texspace_size", (1.0, 1.0, 1.0))), dtype=numpy.float64)
        factor = numpy.where(size != 0.0, 0.5 / numpy.where(size != 0.0, size, 1.0), 0.0)
        columns["generated"] = local * factor - (location * factor - 0.5)
    for field in sorted((f for f in fields if isinstance(f, tuple)), key=str):
        if field[0] == "layer":
            name = field[1] or _default_colour_name(mesh)
            kinds[("layer", field[1])] = name
            field = ("attr", name)
        if field[0] != "attr" or field in columns:
            continue
        attribute = mesh.attributes.get(field[1]) if field[1] else None
        layout = _LAYOUT.get(getattr(attribute, "data_type", None))
        if attribute is None or layout is None or attribute.domain not in {"POINT", "CORNER", "FACE"}:
            unread.append("attribute %r" % (field[1],))
            continue
        values = _array(attribute.data, *layout)
        if attribute.domain == "FACE":
            columns[field] = values[polygons]
        else:
            columns[field] = blend(values, vertices if attribute.domain == "POINT" else loops)
        kinds[field[1]] = attribute.data_type
    return columns, kinds, unread


def read(material, node, identifier, source=None):
    """What ``node.inputs[identifier]`` shows over the material's faces (module docstring)."""
    try:
        return _read(material, node, identifier, source)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError) as error:   # Mesh data it cannot read.
        return Reading([(UNKNOWN, 1.0)], ["the surface data (%s)" % type(error).__name__])


def _read(material, node, identifier, source):
    tree = getattr(material, "node_tree", None)
    socket = shader_graph.socket_of(node.inputs, identifier)
    feeds = shader_graph.incoming(tree) if tree is not None else {}
    fields, unknown, varying = _needed(feeds, node, socket)
    shared, batch = {}, shader_graph.Batch()

    def evaluate(graph):
        try:
            return graph.input(socket, node.name)
        except shader_graph.Unreadable:
            return UNKNOWN

    mesh = getattr(source, "mesh", None)
    if not fields or mesh is None or not len(getattr(mesh, "polygons", ())):
        graph = shader_graph.Graph(feeds, SurfaceContext({}, {}), batch=batch)
        return Reading([(evaluate(graph), 1.0)], unknown + (["per-point data without a mesh"] if fields else []))
    count = FACE_SAMPLES if source.faces is not None else MAX_SAMPLES
    found = _fields(mesh, source, fields, count)
    if found is None:
        return Reading([(UNKNOWN, 1.0)], unknown + ["no face shows the material"])
    columns, kinds, unread = found
    graphs = [shader_graph.Graph(feeds, SurfaceContext({key: tuple(float(v) for v in values[row])
                                                        for key, values in columns.items()}, kinds),
                                 shared=shared, varying=varying, batch=batch) for row in range(count)]
    return Reading([(evaluate(graph), 1.0 / count) for graph in graphs], unknown + unread, count)


def mean_rgb(reading):
    """The area-weighted mean of every value a reading can show (members of a spread equally), or None."""
    total, weight = [0.0, 0.0, 0.0], 0.0
    for value, share in reading:
        found = shader_graph.members(value)
        if not found:
            return None
        for member in found:
            rgb = member.rgb if isinstance(member, shader_graph.Shader) else member
            rgb = rgb if isinstance(rgb, tuple) else (rgb,) * 3
            for axis in range(3):
                total[axis] += rgb[axis] * share / len(found)
        weight += share
    return [round(v / weight, 4) for v in total] if weight else None


def mean_value(reading):
    """The weighted mean of every value a reading can show, or None when any part is unknown."""
    total, weight = 0.0, 0.0
    for value, share in reading:
        found = shader_graph.members(value)
        if not found:
            return None
        for member in found:
            total += (sum(member) / len(member) if isinstance(member, tuple) else float(member)) * share / len(found)
        weight += share
    return round(total / weight, 4) if weight else None


def decide(reading, test, share=0.5):
    """True when at least ``share`` of the surface surely passes ``test``, False when so much surely fails that
    it cannot, None otherwise (an unreadable part could go either way)."""
    sure, against = 0.0, 0.0
    for value, weight in reading:
        verdict = shader_graph.judge(value, test)
        if verdict is True:
            sure += weight
        elif verdict is False:
            against += weight
    total = sum(weight for _, weight in reading) or 1.0
    if sure / total >= share - 1e-9:
        return True
    if against / total > 1.0 - share + 1e-9:
        return False
    return None

