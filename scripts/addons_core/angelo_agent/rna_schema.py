# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender's RNA properties per compiled struct type, read once, for walks that follow pointers (no bpy import at
load; ``object_dependencies`` is its user).

Iterating ``bl_rna.properties`` from Python dominated the dependency walk: ``revision_time.still`` over 1,000 plain
meshes went from 5 to 88 microseconds each when it began walking their data (2026-11-05). A compiled struct type's
RNA lives as long as Blender, so its property list is kept, by RNA address. Properties Python defines on a compiled type
(add-on settings) are left out, whenever they are added: Blender relates no ID-property pointer to its owner's
evaluation, and a driver that reads one is judged by its own rules. Structs Blender defines at run time (below) are
read whole.

The walk also skips nested values that cannot lead to an Object or Collection pointer. A break-it review
(2026-11-05) showed two ways an earlier version of that pruning failed open, and both are now part of the rule:
- Which types refine a declared type was read from ``__subclasses__``, which depends on the classes the process had
  happened to create. Now every struct type ``bpy.types`` names is read once (``_type_facts``), and a type that has
  subtypes, can be registered from Python, is a PropertyGroup, or has no properties of its own (a placeholder RNA
  refines) is open: its values are always read.
- Structs Blender defines at run time (a Geometry Nodes modifier's interface inputs ``Socket_0``... share names across
  modifiers with different properties, and their memory is reused when an interface changes) are read from RNA every
  time, never cached and never pruned, and a value's actual type, not its declared one, decides (``descends``).
``test_blender_revision_deformers`` compares this walk with an unpruned one over every modifier, constraint and data
type and Geometry Nodes inputs and sockets. Test fakes (any ``bl_rna`` that is not ``bpy.types``) are read whole.
"""


class _Property:
    """What the walk reads of one RNA property: its name, its type and, for a pointer or collection, the type of
    struct it holds."""

    __slots__ = ("identifier", "type", "fixed_type")

    def __init__(self, prop):
        self.identifier, self.type = prop.identifier, prop.type
        fixed = getattr(prop, "fixed_type", None) if prop.type in ("POINTER", "COLLECTION") else None
        self.fixed_type = _Fixed(getattr(fixed, "identifier", None)) if fixed is not None else None


class _Fixed:
    __slots__ = ("identifier",)

    def __init__(self, identifier):
        self.identifier = identifier


_SCHEMAS = {}
STRUCT_DEPTH = 4  # How deep ``object_dependencies._structs`` reads nested structs.
_OBJECT_TYPES = ("Object", "Collection")
_TYPES = None   # Built once: what bpy.types says about every struct type (``_type_facts``).
_REACH = {}
_DESCENDS = {}


def _type_facts():
    """``(compiled, open, children)`` from every struct ``bpy.types`` names, read once (about 20 ms).

    ``compiled`` maps each identifier to its RNA address; ``children`` each type to the types deriving from it, so
    a type's whole family is known whatever classes the process happens to have created. ``open`` holds the types
    whose values the walk always reads, because RNA may refine them to a type no table lists: one Python can
    register (nodes, sockets), a PropertyGroup, or a struct with no properties of its own (a Geometry Nodes
    modifier's ``properties``, refined to the interface Blender defines at run time)."""
    global _TYPES
    if _TYPES is None:
        import bpy
        compiled, open_types, bases, children = {}, set(), {}, {}
        for name in dir(bpy.types):
            rna = getattr(getattr(bpy.types, name, None), "bl_rna", None)
            if rna is None or not hasattr(rna, "functions"):
                continue
            compiled[rna.identifier] = rna.as_pointer()
            base = rna.base
            if base is not None:
                bases[rna.identifier] = base.identifier
                children.setdefault(base.identifier, []).append(rna.identifier)
            if any(function.is_registered for function in rna.functions) or not any(
                    prop.identifier != "rna_type" for prop in rna.properties):
                open_types.add(rna.identifier)
        for identifier in compiled:  # Everything deriving from PropertyGroup holds run-time properties.
            chain = identifier
            while chain is not None and chain != "PropertyGroup":
                chain = bases.get(chain)
            if chain == "PropertyGroup":
                open_types.add(identifier)
        open_types.add("PropertyGroup")
        _TYPES = (compiled, frozenset(open_types), {key: tuple(value) for key, value in children.items()})
    return _TYPES


def _compiled(rna):
    """Whether ``rna`` is the struct type ``bpy.types`` names under its identifier (not one defined at run time)."""
    return _type_facts()[0].get(rna.identifier) == rna.as_pointer()


def _family(identifier):
    """A compiled type and every compiled type deriving from it (the types RNA may refine it to)."""
    children = _type_facts()[2]
    found, stack = [], [identifier]
    while stack:
        item = stack.pop()
        found.append(item)
        stack.extend(children.get(item, ()))
    return found


def _is_open(identifier):
    """A value of this declared type may be of a type no table lists, so it is always read."""
    compiled, open_types, _children = _type_facts()
    return identifier not in compiled or any(item in open_types for item in _family(identifier))


def _is_id(identifier):
    import bpy
    cls = getattr(bpy.types, identifier, None)
    return cls is not None and issubclass(cls, bpy.types.ID)


def _leads_to_objects(prop, depth):
    """Whether following ``prop`` ``depth`` more levels can reach an Object or Collection pointer."""
    kind = prop.fixed_type.identifier if prop.fixed_type is not None else None
    if prop.type == "POINTER" and kind in _OBJECT_TYPES:
        return True
    if kind is None or depth <= 0 or _is_id(kind):
        return False
    return _is_open(kind) or any(reaches(member, depth - 1) for member in _family(kind))


def reaches(identifier, depth):
    """Whether one compiled struct type has an Object or Collection pointer within ``depth`` more levels."""
    key = (identifier, depth)
    if key not in _REACH:
        import bpy
        rna = getattr(getattr(bpy.types, identifier, None), "bl_rna", None)
        _REACH[key] = rna is None or any(
            _leads_to_objects(_Property(prop), depth) for prop in rna.properties
            if prop.type in ("POINTER", "COLLECTION") and prop.identifier != "rna_type" and not prop.is_runtime)
    return _REACH[key]


def _object_properties(pointers):
    """``(identifier, "Object" | "Collection")`` for each pointer property typed Object or Collection."""
    return tuple((prop.identifier, prop.fixed_type.identifier) for prop in pointers
                 if prop.type == "POINTER" and prop.fixed_type is not None
                 and prop.fixed_type.identifier in _OBJECT_TYPES)


def _schema(struct):
    """``(all, pointers, walked, objects)`` for ``struct``'s RNA, cached for a compiled type; None for a fake.
    ``walked``: the pointers the walk reads further (``_leads_to_objects`` from ``STRUCT_DEPTH``); ``objects``: the
    Object and Collection pointers (``_object_properties``). A struct defined at run time is read from RNA each time
    and walked whole."""
    rna = getattr(struct, "bl_rna", None)
    if rna is None or type(rna).__module__ != "bpy.types":
        return None
    pointer = rna.as_pointer()
    found = _SCHEMAS.get(pointer)
    if found is not None:
        return found
    if not _compiled(rna):  # Defined at run time (a modifier's interface inputs): every property, read whole.
        everything = tuple(_Property(prop) for prop in rna.properties)
        pointers = tuple(prop for prop in everything if prop.type in ("POINTER", "COLLECTION") and prop.identifier != "rna_type")
        return everything, pointers, pointers, _object_properties(pointers)
    # A compiled type: Python-defined properties (add-on settings, ``is_runtime``) are left out. Blender relates no
    # ID-property pointer to its owner's evaluation (deg_builder_relations.cc, build_idproperties), so they feed
    # nothing an object evaluates to; Geometry Nodes interface inputs live on run-time structs, read above.
    everything = tuple(_Property(prop) for prop in rna.properties if not prop.is_runtime)
    pointers = tuple(prop for prop in everything if prop.type in ("POINTER", "COLLECTION") and prop.identifier != "rna_type")
    found = _SCHEMAS[pointer] = (everything, pointers, tuple(
        prop for prop in pointers if _leads_to_objects(prop, STRUCT_DEPTH)), _object_properties(pointers))
    return found


def descends(item, depth):
    """Whether the walk must read ``item`` (a nested struct value) with ``depth`` levels left: always for a value
    of a type defined at run time or an open type; for a closed compiled type, only if it can reach an Object.
    Remembered per RNA type and depth."""
    rna = getattr(item, "bl_rna", None)
    if rna is None or type(rna).__module__ != "bpy.types":
        return True
    key = (rna.as_pointer(), depth)
    found = _DESCENDS.get(key)
    if found is None:
        found = not _compiled(rna) or _is_open(rna.identifier) or reaches(rna.identifier, depth)
        if _compiled(rna):
            _DESCENDS[key] = found
    return found


def properties(struct):
    """``struct``'s RNA properties (cached for a compiled type, ``_schema``)."""
    schema = _schema(struct)
    return schema[0] if schema is not None else getattr(getattr(struct, "bl_rna", None), "properties", ())


def pointer_properties(struct, walked=False):
    """The pointer and collection properties (``rna_type`` excluded); ``walked``: only those the walk reads further."""
    schema = _schema(struct)
    if schema is not None:
        return schema[2] if walked else schema[1]
    return [prop for prop in getattr(getattr(struct, "bl_rna", None), "properties", ())
            if prop.type in ("POINTER", "COLLECTION") and prop.identifier != "rna_type"]


def object_properties(struct):
    """``(identifier, kind)`` of ``struct``'s pointer properties typed Object or Collection (cached, ``_schema``)."""
    schema = _schema(struct)
    if schema is not None:
        return schema[3]
    return tuple((prop.identifier, prop.fixed_type.identifier)
                 for prop in getattr(getattr(struct, "bl_rna", None), "properties", ())
                 if prop.type == "POINTER" and getattr(getattr(prop, "fixed_type", None), "identifier", None)
                 in _OBJECT_TYPES)
