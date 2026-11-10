# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned staged data types and complete bounded mutable-dependency fingerprints.

No evaluated geometry is retained or traversed. Pointer identities apply only
to ID ownership; semantic RNA storage uses structural references, so Blender
copy-on-write representation changes do not masquerade as artist edits."""

import collections
import hashlib
import json
import math

from . import exact_fence
from .program_jobs import ProgramError
from .refusal_codes import refusal_code, refusal_details
from .program_fingerprint_packing import packed_attribute_values, packed_bytes_value
# Re-exported: these moved out to keep this module within its size; callers import them from here.
from .program_custom_values import _custom_properties, _uid  # noqa: F401
from .program_stages import Stage, SuspendedStage  # noqa: F401
from .scene_geometry import mesh_array
# Re-exported: the walkers here, admission, commit and transfers read these declarations.
from .rna_fields import _RNA_SKIP, ELEMENT_VIEWS, link_fields, link_free_classifier, python_subtyped  # noqa: F401

MAX_TOTAL_VERTICES = 1000000
MAX_STAGE_IDS = 2048
MAX_FINGERPRINT_ITEMS = 2000000
# Bytes hashed from single C copies (packed attributes, native buffers) per bind.
MAX_FINGERPRINT_PACKED_BYTES = 256000000


def fingerprint_budget():
    """[recursive RNA items, packed bytes]: separate bounds for slow and C-speed work."""
    return [MAX_FINGERPRINT_ITEMS, MAX_FINGERPRINT_PACKED_BYTES]


def _bpy():
    import bpy
    return bpy


def link_free_types():
    """rna_fields.link_free_classifier over this process's RNA types, asking Python's subclasses (which follow
    registration): one per exact fence, or one per walk outside a fence (its memo then lives as long as the walk)."""
    types = _bpy().types
    build = lambda: link_free_classifier(types, python_subtyped(types, _PRIMED))
    return build() if exact_fence.current() is None else exact_fence.check("link_free_types", (), build)


_PRIMED = [False]  # bpy.types has built every class once (rna_fields.python_subtyped).


def _data_collections():
    data = _bpy().data
    return [getattr(data, prop.identifier) for prop in data.bl_rna.properties
            if prop.type == "COLLECTION" and hasattr(getattr(data, prop.identifier, None), "remove")]


def _main_ids():
    return {block.as_pointer(): block for collection in _data_collections() for block in collection}


def _rna_values(value, *, budget=None):
    """Complete bounded RNA settings, not a truncating observation digest.

    ID pointers are identities; mutable dependency contents are separately
    visited. Unsupported settings fail preflight instead of claiming exact.
    The resulting digest is private and never discloses source paths or scripts.

    A flat table, breadth first: every non-ID struct is written where it is reached as ``{"rna_reference":
    [identifier, ordinal]}``, its ordinal numbering the structs in the order they are first reached (the root 0); the
    root's own settings are the result, and every other struct's settings are ``result["#structs"][ordinal - 1]``.
    A struct is one (pointer, RNA type) pair: Blender returns several views over one pointer (``Object.display`` and
    ``Object.light_linking`` are the Object itself, ``Object.collision`` shares ``Object.field``'s data,
    ``World.mist_settings`` is the World), and each view's settings are its own. Every struct that holds ID properties
    (a pose bone's or bone's custom properties, not only an ID's) records them under ``"#custom"``. ``#`` keys cannot
    be RNA identifiers, so they never collide with a setting.
    So the labelled struct graph is recoverable, and the result nests no deeper than one field's arrays however
    deeply the structs nest: an armature's bone chains, bone collections nested under each other and reached through
    a pose bone, node frames. Neither a struct's nesting nor a recursive consumer of the result (``json.dumps``,
    ``hash_value``) bounds the walk any more; the item and packed budgets do (doc/angelo/native_coverage.rst,
    "Dependency walk depth"). The queue holds a pending field (a struct or a collection, as fetched), never one entry
    per element; each field is read once, when its holder is expanded, and a collection is iterated once, when
    dequeued.
    """
    if exact_fence.current() is None:
        with exact_fence.fence():  # One table of each type's declarations for the whole walk.
            return _rna_values(value, budget=budget)  # Off the main thread there is no fence.
    walk = _Walk(fingerprint_budget() if budget is None else budget, {})
    holder = [None]
    try:
        _visit(value, 0, walk, holder, 0)
        while walk.queue:
            target, key, item = walk.queue.popleft()
            if key is not None:  # A struct field, fetched when its holder was expanded.
                _visit(item, 0, walk, target, key)
                continue
            for child in item:  # A collection: its elements are reached now, in order.
                target.append(None)
                _visit(child, 0, walk, target, len(target) - 1)
    except (AttributeError, ReferenceError) as error:  # A freed or unreadable struct, as a field read would be.
        raise ProgramError("A complete exact source precondition could not be read",
                           code=refusal_code(error), details=refusal_details(error)) from error
    if not walk.nodes:
        return holder[0]
    root = walk.nodes[0]
    if len(walk.nodes) > 1:
        root[_STRUCTS] = walk.nodes[1:]
    return root


_STRUCTS, _CUSTOM = "#structs", "#custom"  # Not RNA identifiers: never a setting's name.
_NO_ID_PROPERTIES = set()  # RNA types that cannot hold ID properties (a per-type flag; their keys() raises).


class _Walk:
    __slots__ = ("budget", "seen", "queue", "nodes")

    def __init__(self, budget, seen):
        self.budget, self.seen, self.queue, self.nodes = budget, seen, collections.deque(), []


def _visit(value, depth, walk, target, key):
    """Write ``value``'s fingerprint to ``target[key]``: plain values inline, a struct as its ordinal (expanded into
    the table when first reached, its struct and collection fields queued). ``depth`` is the nesting of arrays inside
    one field's value, the only recursion left."""
    budget = walk.budget
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ProgramError("Exact program preconditions exceed the bounded fingerprint budget", code='LIMIT_EXCEEDED')
    if not hasattr(value, "bl_rna"):
        if isinstance(value, (str, bool, int)) or value is None:
            target[key] = value
            return
        if isinstance(value, bytes):
            # Angelo's native mesh positions/topology are already exact packed bytes.
            # Preserve every byte and the old per-byte work charge without
            # expanding half a million integer JSON/RNA recursion entries.
            target[key] = packed_bytes_value(value, budget=budget)
            return
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ProgramError("Non-finite source values cannot be staged", code='VALIDATION')
            target[key] = value
            return
        try:
            items = []
            for item in value:  # An array's elements: plain values are charged and checked as a call would.
                if type(item) in _PLAIN:
                    items.append(_plain(item, depth + 1, budget))
                else:
                    items.append(None)
                    _visit(item, depth + 1, walk, items, len(items) - 1)
            target[key] = items
        except TypeError:
            target[key] = str(value)
        return
    bpy = _bpy()
    rna = value.bl_rna
    pointer = (value.as_pointer(), rna.identifier)   # A view over another view's data is its own struct.
    seen = walk.seen
    if pointer in seen:
        # Attribute COW can relocate an unchanged RNA value array while making
        # a Mesh copy. Structural ordinals preserve alias/cycle information
        # without mistaking internal storage relocation for a manual edit.
        target[key] = {"rna_reference": [rna.identifier, seen[pointer]]}
        return
    ordinal = seen[pointer] = len(seen)
    target[key] = {"rna_reference": [rna.identifier, ordinal]}
    result = {}
    walk.nodes.append(result)
    queue = walk.queue
    mesh, curves = isinstance(value, bpy.types.Mesh), isinstance(value, bpy.types.Curves)
    attribute = isinstance(value, bpy.types.Attribute)
    for name, kind in exact_fence.table("values", rna, _value_fields):
        try:
            item = getattr(value, name)
            if kind == "POINTER":
                if item is None:
                    result[name] = None
                elif isinstance(item, bpy.types.ID):
                    result[name] = [item.bl_rna.identifier, _uid(item)]
                else:
                    result[name] = None
                    queue.append((result, name, item))
            elif kind == "COLLECTION":
                # Mesh packed attributes contain all coordinates/topology/UVs.
                if mesh and name in _MESH_PACKED:
                    continue
                # Hair curves' point, curve and position views repeat their packed attributes and offsets;
                # normals derive from them.
                if curves and name in _CURVES_PACKED:
                    continue
                if attribute and name == "data":
                    packed = packed_attribute_values(value, item, budget=budget, skip_names=_RNA_SKIP, child_depth=1)
                    if packed is not None:
                        result[name] = packed
                        continue
                result[name] = []
                queue.append((result[name], None, item))
            elif kind in _SCALAR_TYPES:
                if type(item) in _PLAIN:  # _plain inline: most values are plain, and this loop is the hot path.
                    budget[0] -= 1
                    if budget[0] < 0:
                        raise ProgramError("Exact program preconditions exceed the bounded fingerprint budget",
                                           code='LIMIT_EXCEEDED')
                    if type(item) is float and not math.isfinite(item):
                        raise ProgramError("Non-finite source values cannot be staged", code='VALIDATION')
                    result[name] = item
                else:
                    result[name] = None
                    _visit(item, 1, walk, result, name)
        except (AttributeError, ReferenceError) as error:
            raise ProgramError("A complete exact source precondition could not be read",
                               code=refusal_code(error), details=refusal_details(error)) from error
    custom = _id_properties(value, rna, budget)
    if custom is not None:
        result[_CUSTOM] = custom


def _id_properties(value, rna, budget):
    """The struct's ID properties (_custom_properties), or None for a type that cannot hold any."""
    identifier = rna.identifier
    if identifier in _NO_ID_PROPERTIES:
        return None
    try:
        keys = value.keys()
    except TypeError:  # "This type doesn't support IDProperties": the same for every struct of the type.
        _NO_ID_PROPERTIES.add(identifier)
        return None
    except AttributeError:  # No mapping protocol at all (host stand-ins).
        return None
    if not len(keys):
        return {}   # The common case, a node or socket without any: no second keys() call.
    return _custom_properties(value, budget=budget, depth=1)


_SCALAR_TYPES = frozenset({"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"})
_PLAIN = frozenset({str, bool, int, float, type(None)})


def _plain(value, depth, budget):
    """_rna_values of a plain str, bool, int, float or None at ``depth``: the same charge, limits and checks."""
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ProgramError("Exact program preconditions exceed the bounded fingerprint budget", code='LIMIT_EXCEEDED')
    if type(value) is float and not math.isfinite(value):
        raise ProgramError("Non-finite source values cannot be staged", code='VALIDATION')
    return value


_MESH_PACKED = frozenset({"vertices", "edges", "loops", "polygons", "vertex_normals", "polygon_normals",
                          "corner_normals", "loop_triangles", "loop_triangle_polygons"})
_CURVES_PACKED = frozenset({"points", "curves", "position_data", "normals"})


def _value_fields(rna):
    """(identifier, type) of every declaration _rna_values reads, in RNA order."""
    return tuple((prop.identifier, prop.type) for prop in rna.properties
                 if not (prop.identifier in _RNA_SKIP or (prop.is_readonly and prop.identifier.startswith("is_"))))


def _embedded_links(root):
    """RNA object links in modifiers, constraints, node sockets and drivers: [(holder, property, ID)].

    Inside an exact fence a datablock's links are walked once and the same
    list serves every later walk of the fence (exact_fence); otherwise, and for
    nested structs, this is the walk itself.
    """
    memo = exact_fence.current()
    if memo is None:
        return _walk_links(root)
    key = root.as_pointer()
    found = memo.links.get(key)
    if found is None:
        found = memo.links[key] = list(_walk_links(root))
    return iter(found)


def _walk_links(root, budget=None):
    """Every ID link of ``root`` and the structs it embeds, breadth first (see ``_rna_values``): a struct, one
    (pointer, RNA type) pair, is expanded once and charged one item of ``budget`` (``[MAX_FINGERPRINT_ITEMS]`` per
    walk, so the aggregate is that bound per datablock times MAX_STAGE_IDS, scaling with resident data). The queue
    holds pending struct and collection fields as fetched; a collection is iterated once, when dequeued, unless its
    item type can hold no link (rna_fields.link_free_classifier: a particle's hair keys). Nesting is not
    bounded: the walk is iterative, the visited set ends it, the budget bounds its work, and its links are flat. An
    exhausted budget refuses; the walk is never truncated."""
    bpy = _bpy()
    visited = set()
    budget = [MAX_FINGERPRINT_ITEMS] if budget is None else budget
    queue = collections.deque()
    free = link_free_types()

    def reach(holder):
        key = (holder.as_pointer(), holder.bl_rna.identifier) if hasattr(holder, "as_pointer") else id(holder)
        if key in visited:
            return False
        visited.add(key)
        budget[0] -= 1
        if budget[0] < 0:
            raise ProgramError("An input embeds more data than its bounded dependency walk reads", code='LIMIT_EXCEEDED')
        return True

    def expand(holder):
        mesh = isinstance(holder, bpy.types.Mesh)  # Mesh collections are checked by their existing owners.
        for name, kind, prop in exact_fence.table("links", holder.bl_rna, link_fields):
            if kind != "POINTER" and mesh:
                continue
            item = getattr(holder, name, None)
            if kind == "POINTER":  # The hot path: most fields are pointers, most of them empty.
                if item is not None:
                    if isinstance(item, bpy.types.ID):
                        yield holder, prop, item
                    elif hasattr(item, "bl_rna"):
                        queue.append((False, item))
            elif kind == "COLLECTION":
                if not free(prop):
                    queue.append((True, item))
            else:
                # IDS: an ID a collection holds (a curve's materials) is a link, as through a pointer. Walked as
                # an embedded struct, a staged curve reached into a borrowed live material's tree. (A plain loop:
                # a generator expression here would make holder and prop closure cells for every field.)
                for child in item:
                    if child is not None:
                        yield holder, prop, child

    if reach(root):
        yield from expand(root)
    while queue:
        collection, item = queue.popleft()
        if not collection:
            if reach(item):
                yield from expand(item)
            continue
        for child in item:
            if hasattr(child, "bl_rna") and reach(child):
                yield from expand(child)


def linked_once():
    """``_embedded_links`` remembered per datablock for one pass over many objects.

    Linked copies share their data, materials and node trees; a pass that
    visits every staged object would otherwise walk those again for each one.
    The result of a walk depends only on the datablock, so it is the same pass.
    """
    cache = {}

    def links(block):
        key = block.as_pointer()
        if key not in cache:
            cache[key] = list(_embedded_links(block))
        return cache[key]

    return links


def _mutable_dependencies(obj, links=_embedded_links, borrowed=frozenset()):
    """Closed dependency families kernels can change; recurse nested node trees.

    ``links``: ``linked_once()`` when the caller visits many objects in one pass.
    ``borrowed``: pointers of live materials a stage reads in place (stage_borrowing);
    a staged object's walk neither returns nor enters them.
    Inside an exact fence an object's closure is walked once (exact_fence).
    """
    with exact_fence.fence():  # Reads only: the walk shares one table of each type's declarations.
        memo = exact_fence.current() if links is _embedded_links and not borrowed else None
        if memo is None:
            return _walk_dependencies(obj, links, borrowed)
        key = obj.as_pointer()
        found = memo.closures.get(key)
        if found is None:
            found = memo.closures[key] = _walk_dependencies(obj, links, borrowed)
        return list(found)


def object_data_types(bpy):
    """Every ID type an object's ``data`` can be (``Curve``: surfaces and text too) but an image Empty's (unstaged)."""
    types = bpy.types
    return (types.Mesh, types.Curve, types.MetaBall, types.GreasePencil, types.Curves, types.PointCloud,
            types.Volume, types.Lattice, types.Armature, types.Camera, types.Light, types.LightProbe, types.Speaker)


def _walk_dependencies(obj, links, borrowed):
    bpy = _bpy()
    queue, result, seen = [obj], [], set(borrowed)
    # Everything staging copies (copy_object): the object's data of any type, shape keys, Actions, materials,
    # node groups and particle settings, for the fingerprint, the undeclared-dependency search and borrowing.
    kinds = (bpy.types.Material, bpy.types.NodeTree, bpy.types.Action, bpy.types.Key, bpy.types.ParticleSettings,
             *object_data_types(bpy))
    while queue:
        block = queue.pop()
        pointer = block.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        result.append(block)
        if len(result) > MAX_STAGE_IDS:
            raise ProgramError("An input has too many mutable dependencies to isolate", code='LIMIT_EXCEEDED')
        for _, _, dependency in links(block):
            if isinstance(dependency, kinds):
                queue.append(dependency)
        # Material pointers live in a Mesh RNA collection, excluded above for
        # efficient topology processing.
        if hasattr(block, "materials"):
            queue.extend(material for material in block.materials if material is not None)
    return result


def _fingerprint(obj, budget=None):
    """The exact digest of an object's mutable closure, charged to ``budget`` ([items, packed bytes]).

    Inside an exact fence an object is hashed once; a repeat charges the
    caller's budget exactly what hashing it again would (exact_fence.check).
    """
    budget = fingerprint_budget() if budget is None else budget

    def compute():
        before = list(budget)
        with exact_fence.fence():  # The closure walks and links below share one memo.
            digest = _hash_closure(obj, budget)
        return digest, tuple(start - left for start, left in zip(before, budget))

    def replay(result):
        if any(charge > left for charge, left in zip(result[1], budget)):
            return False  # Hashing again exhausts this budget, and refuses exactly where it always did.
        for slot, charge in enumerate(result[1]):
            budget[slot] -= charge
        return True

    # A one-slot (legacy) budget charges packed bytes to its item slot: reuse only with as many slots.
    return exact_fence.check("fingerprint", (obj.as_pointer(), len(budget)), compute, replay=replay)[0]


def _hash_closure(obj, budget):
    digest = hashlib.blake2b(digest_size=32)
    for block in sorted(_mutable_dependencies(obj), key=lambda item: (item.bl_rna.identifier, _uid(item))):
        digest.update(json.dumps([block.bl_rna.identifier, _uid(block), _rna_values(block, budget=budget)],
                                 sort_keys=True, allow_nan=False, separators=(",", ":")).encode())
        if block.bl_rna.identifier == "Mesh":
            for domain, collection, prop, kind, width in (("POINT", block.vertices, "co", "f", 3),
                                                          ("EDGE", block.edges, "vertices", "i", 2),
                                                          ("CORNER", block.loops, "vertex_index", "i", 1),
                                                          ("FACE", block.polygons, "loop_total", "i", 1)):
                # Same bytes as the element read, from the contiguous attribute.
                digest.update(mesh_array(block, domain, collection, prop, kind, width)[0].tobytes())
            native = getattr(block, "angelo_vertex_group_weights", None)
            if isinstance(native, bytes):
                # Native (vertex, group, weight) records: same charge per assignment.
                if len(native) % 12:
                    raise ProgramError("Exact vertex-group preconditions exceed the bounded budget", code='INTERNAL_ERROR')
                budget[0] -= len(native) // 12
                if budget[0] < 0:
                    raise ProgramError("Exact vertex-group preconditions exceed the bounded budget", code='LIMIT_EXCEEDED')
                digest.update(b"native_vertex_group_weights_v1")
                digest.update(native)
            else:
                groups = [[(group.group, float(group.weight)) for group in vertex.groups] for vertex in block.vertices]
                budget[0] -= sum(len(items) for items in groups)
                if budget[0] < 0:
                    raise ProgramError("Exact vertex-group preconditions exceed the bounded budget", code='LIMIT_EXCEEDED')
                digest.update(json.dumps(groups, allow_nan=False, separators=(",", ":")).encode())
    return digest.hexdigest()


def _bpy_vector(value):
    from mathutils import Vector
    return Vector(value)


def _dependency_closure(roots):
    with exact_fence.fence():  # Reads only (commit calls it between its writes).
        return _walk_closure(roots)


def _walk_closure(roots):
    bpy = _bpy()
    queue, seen, result = list(roots), set(), []
    while queue:
        block = queue.pop()
        if block is None or block.as_pointer() in seen:
            continue
        seen.add(block.as_pointer())
        result.append(block)
        for _, _, dependency in _embedded_links(block):
            if isinstance(dependency, bpy.types.ID) and not isinstance(dependency, (bpy.types.Scene, bpy.types.Collection)):
                queue.append(dependency)
        if hasattr(block, "materials"):
            queue.extend(material for material in block.materials if material is not None)
    return result
