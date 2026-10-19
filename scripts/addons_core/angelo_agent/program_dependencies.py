# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned staged data types and complete bounded mutable-dependency fingerprints.

No evaluated geometry is retained or traversed. Pointer identities apply only
to ID ownership; semantic RNA storage uses structural references, so Blender
copy-on-write representation changes do not masquerade as artist edits."""

import hashlib
import json
import math
from dataclasses import dataclass, field

from . import exact_fence
from .program_jobs import ProgramError
from .refusal_codes import refusal_code, refusal_details
from .program_fingerprint_packing import packed_attribute_values, packed_bytes_value
from .scene_geometry import mesh_array
# Re-exported: the walkers here, admission, commit and transfers read these declarations.
from .rna_fields import _RNA_SKIP, ELEMENT_VIEWS, link_fields  # noqa: F401

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


def _uid(value):
    return int(value.session_uid) if hasattr(value, "session_uid") else int(value.as_pointer())


def _data_collections():
    data = _bpy().data
    return [getattr(data, prop.identifier) for prop in data.bl_rna.properties
            if prop.type == "COLLECTION" and hasattr(getattr(data, prop.identifier, None), "remove")]


def _main_ids():
    return {block.as_pointer(): block for collection in _data_collections() for block in collection}


def _rna_values(value, *, depth=0, budget=None, seen=None):
    """Complete bounded RNA settings, not a truncating observation digest.

    ID pointers are identities; mutable dependency contents are separately
    visited. Unsupported/deep settings fail preflight instead of claiming exact.
    The resulting digest is private and never discloses source paths or scripts.
    """
    if depth == 0 and seen is None and exact_fence.current() is None:
        with exact_fence.fence():  # One table of each type's declarations for the whole walk.
            return _rna_values(value, budget=budget, seen={})  # Off the main thread there is no fence.
    if budget is None:
        budget = fingerprint_budget()
    if seen is None:
        seen = {}
    budget[0] -= 1
    if budget[0] < 0 or depth > 16:
        raise ProgramError("Exact program preconditions exceed the bounded fingerprint budget", code='LIMIT_EXCEEDED')
    if not hasattr(value, "bl_rna"):
        if isinstance(value, (str, bool, int)) or value is None:
            return value
        if isinstance(value, bytes):
            # Angelo's native mesh positions/topology are already exact packed bytes.
            # Preserve every byte and the old per-byte work charge without
            # expanding half a million integer JSON/RNA recursion entries.
            return packed_bytes_value(value, budget=budget)
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ProgramError("Non-finite source values cannot be staged", code='VALIDATION')
            return value
        try:
            items = []
            for item in value:  # An array's elements: plain values are charged and checked as a call would.
                items.append(_plain(item, depth + 1, budget) if type(item) in _PLAIN else
                             _rna_values(item, depth=depth + 1, budget=budget, seen=seen))
            return items
        except TypeError:
            return str(value)
    bpy = _bpy()
    pointer = value.as_pointer()
    rna = value.bl_rna
    if pointer in seen:
        # Attribute COW can relocate an unchanged RNA value array while making
        # a Mesh copy. Structural ordinals preserve alias/cycle information
        # without mistaking internal storage relocation for a manual edit.
        return {"rna_reference": [rna.identifier, seen[pointer]]}
    seen[pointer] = len(seen)
    result = {}
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
                    result[name] = _rna_values(item, depth=depth + 1, budget=budget, seen=seen)
            elif kind == "COLLECTION":
                # Mesh packed attributes contain all coordinates/topology/UVs.
                if mesh and name in _MESH_PACKED:
                    continue
                # Hair curves' point, curve and position views repeat their packed attributes and offsets;
                # normals derive from them.
                if curves and name in _CURVES_PACKED:
                    continue
                if attribute and name == "data":
                    packed = packed_attribute_values(value, item, budget=budget, skip_names=_RNA_SKIP,
                                                     child_depth=depth + 1)
                    if packed is not None:
                        result[name] = packed
                        continue
                result[name] = [_rna_values(child, depth=depth + 1, budget=budget, seen=seen) for child in item]
            elif kind in _SCALAR_TYPES:
                if type(item) in _PLAIN:  # _plain inline: most values are plain, and this loop is the hot path.
                    budget[0] -= 1
                    if budget[0] < 0 or depth >= 16:
                        raise ProgramError("Exact program preconditions exceed the bounded fingerprint budget",
                                           code='LIMIT_EXCEEDED')
                    if type(item) is float and not math.isfinite(item):
                        raise ProgramError("Non-finite source values cannot be staged", code='VALIDATION')
                    result[name] = item
                else:
                    result[name] = _rna_values(item, depth=depth + 1, budget=budget, seen=seen)
        except (AttributeError, ReferenceError) as error:
            raise ProgramError("A complete exact source precondition could not be read",
                               code=refusal_code(error), details=refusal_details(error)) from error
    if isinstance(value, bpy.types.ID):
        result["custom_properties"] = _custom_properties(value, budget=budget, depth=depth + 1)
    return result


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


def _custom_properties(block, *, budget=None, depth=0):
    """Exact custom values, bounded before ID-property group/array expansion."""
    budget = [MAX_FINGERPRINT_ITEMS] if budget is None else budget

    def charge(amount, level):
        if level > 16 or amount > budget[0]:
            raise ProgramError("Exact custom-property preconditions exceed the bounded fingerprint budget", code='LIMIT_EXCEEDED')
        budget[0] -= amount

    def clean(value, level):
        charge(1, level)
        if isinstance(value, dict) or hasattr(value, "to_dict"):
            # IDPropertyGroup supports bounded mapping access. Do not call
            # to_dict(), which materializes all descendants before admission.
            count = len(value)
            if count > budget[0]:
                raise ProgramError("Exact custom-property group exceeds the bounded fingerprint budget", code='LIMIT_EXCEEDED')
            result = {}
            for key in value.keys():
                charge(1, level + 1)
                result[str(key)] = clean(value[key], level + 1)
            return result
        if isinstance(value, (list, tuple)) or hasattr(value, "to_list"):
            count = len(value)
            if count > budget[0]:
                raise ProgramError("Exact custom-property array exceeds the bounded fingerprint budget", code='LIMIT_EXCEEDED')
            return [clean(item, level + 1) for item in value]
        if isinstance(value, str):
            charge(len(value), level)
            return value
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ProgramError("Non-finite custom-property source values cannot be staged", code='VALIDATION')
            return value
        if isinstance(value, (bool, int)) or value is None:
            return value
        if hasattr(value, "as_pointer"):
            return [value.bl_rna.identifier, _uid(value)]
        raise ProgramError("Unsupported custom property in an exact program input", code='UNSUPPORTED')

    keys = block.keys()
    if len(keys) > budget[0]:
        raise ProgramError("Exact custom-property keys exceed the bounded fingerprint budget", code='LIMIT_EXCEEDED')
    result = {}
    for key in keys:
        charge(1, depth)
        result[str(key)] = clean(block[key], depth)
    return result


def _embedded_links(root, depth=0, visited=None):
    """RNA object links in modifiers, constraints, node sockets and drivers: [(holder, property, ID)].

    Inside an exact fence a datablock's links are walked once and the same
    list serves every later walk of the fence (exact_fence); otherwise, and for
    nested structs, this is the walk itself.
    """
    memo = exact_fence.current() if depth == 0 and visited is None else None
    if memo is None:
        return _walk_links(root, depth, visited)
    key = root.as_pointer()
    found = memo.links.get(key)
    if found is None:
        found = memo.links[key] = list(_walk_links(root))
    return iter(found)


def _walk_links(root, depth=0, visited=None):
    bpy = _bpy()
    if visited is None:
        visited = set()
    pointer = root.as_pointer() if hasattr(root, "as_pointer") else id(root)
    if pointer in visited:
        return
    visited.add(pointer)
    if depth > 14:
        raise ProgramError("An input dependency graph is too deep to isolate completely", code='LIMIT_EXCEEDED')
    mesh = isinstance(root, bpy.types.Mesh)  # Mesh collections are checked by their existing owners.
    for name, kind, prop in exact_fence.table("links", root.bl_rna, link_fields):
        if kind != "POINTER" and mesh:
            continue
        item = getattr(root, name, None)
        if kind == "POINTER":  # The hot path: most fields are pointers, most of them empty.
            if item is not None:
                if isinstance(item, bpy.types.ID):
                    yield root, prop, item
                elif hasattr(item, "bl_rna"):
                    yield from _walk_links(item, depth + 1, visited)
        elif kind == "COLLECTION":
            for child in item:
                if hasattr(child, "bl_rna"):
                    yield from _walk_links(child, depth + 1, visited)
        else:
            # IDS: an ID a collection holds (a curve's materials) is a link, as through a pointer. Walked as
            # an embedded struct, a staged curve reached into a borrowed live material's tree. (A plain loop:
            # a generator expression here would make root and prop closure cells for every field.)
            for child in item:
                if child is not None:
                    yield root, prop, child


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


@dataclass
class Stage:
    scene: object
    collection: object
    view_layer: object
    binding: dict
    context: object = None
    objects: dict = field(default_factory=dict)
    originals: dict = field(default_factory=dict)
    owned_ids: list = field(default_factory=list)
    changes: dict = field(default_factory=dict)
    logical_names: dict = field(default_factory=dict)
    bundle: str = ""
    extras: dict = field(default_factory=dict)
    # Live wrappers of the materials this resident stage borrows (stage_borrowing); never persisted.
    borrowing: object = None

    @property
    def created_ids(self):
        return self.context.created_ids if self.context else self.owned_ids


@dataclass
class SuspendedStage:
    bundle: str
    scene_name: str
    binding: dict
    object_names: dict
    aliases: dict
    changes: dict
    logical_names: dict
    topology_versions: dict = field(default_factory=dict)
    node_bounds: dict = field(default_factory=dict)
    node_metadata: dict = field(default_factory=dict)
    extras: dict = field(default_factory=dict)
    # One active job may own a closed graph in a separate native Main. Retained
    # previews and inspection snapshots continue to own a file bundle instead.
    native_owner: object = None
    resident: object = None


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
