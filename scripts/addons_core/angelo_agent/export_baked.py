# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's glTF exports with a target's baked material in place of its own, in the file only.

Contract: doc/angelo/long_jobs.rst, "Baked materials at export". A ``BAKE_MAPS`` job that makes its baked material
binds each target to it (``bind``): ``Object.angelo_baked`` holds the material, the source objects and cage the bake
rendered from, whether it baked AO, and a digest of everything the bake read (``export_baked_reads``: the geometry,
attributes and normals of every object it rendered from, the materials and images whose shading it read, the
placements it depended on and, for AO, the geometry within reach). Angelo's glTF exports (the web export and
preview, the engine link's live scene and its write-back) ask ``plan`` which exported objects have a current
binding. Those are written with their baked material in every slot; an object changed since its bake keeps its own
materials and the export says so (``stale``), so stale maps never ship silently, and so does a set whose packing
glTF can't carry (``packing``: Unity's mask map inverts smoothness and Unreal's normal is DirectX, which Blender's
glTF exporter can't write through the material's unpacking nodes and which a glTF reader would flip again). The
person's slots never change: in this session the glTF exporter's ``gather_mesh_materials_hook`` swaps the materials
it writes (``active``, ``substitute``); the engine link's write-back runs in a disposable background Angelo, which
assigns them to its own copy (``engine_link_writeback.CHILD``, from ``plan``'s ``used``).

``plan`` costs one property read per exported object when nothing is bound; the digest is computed only for
bound objects, with one ``Reader`` per export. Main thread only.
"""

from contextlib import contextmanager

import bpy
from bpy.props import BoolProperty, CollectionProperty, PointerProperty, StringProperty

from .export_baked_reads import Reader

GLTF_PACKINGS = frozenset({"GLTF", "ORM", "SEPARATE"})   # Packings a glTF file carries as baked (bake_maps_spec).
EXPORT_IDS = frozenset({"angelo_web", "angelo_link"})   # web_export.EXPORT_ID, engine_link_assets.EXPORT_ID.
NONE = {"substitutes": {}, "used": [], "stale": [], "nearby": [], "packing": []}   # A plan that changes nothing (the option turned off).
_SUBSTITUTES = {}   # session_uid -> baked material, while an Angelo export runs (``active``).


# One object a high-to-low bake rendered from (``ANGELO_PG_baked.sources``).
class ANGELO_PG_baked_source(bpy.types.PropertyGroup):
    object: PointerProperty(type=bpy.types.Object)


# A target's baked material, its packing preset, the digest of what its bake read and what else it read: its source
# objects and cage, and whether it baked AO (``Object.angelo_baked``). A comment, not a docstring: a property group's
# docstring becomes an interface description.
class ANGELO_PG_baked(bpy.types.PropertyGroup):
    material: PointerProperty(type=bpy.types.Material)
    digest: StringProperty()
    packing: StringProperty()
    own: StringProperty()   # The digest without the AO reach (``export_baked_reads.Reader.digests``).
    sources: CollectionProperty(type=ANGELO_PG_baked_source)
    cage: PointerProperty(type=bpy.types.Object)
    ao: BoolProperty()


def binding(obj):
    """The object's bound baked material, or None (also when this process never registered the property)."""
    settings = getattr(obj, "angelo_baked", None)
    return None if settings is None else settings.material


def bind(obj, material, digest_, packing, sources=(), cage=None, ao=False, own=""):
    """Bind ``obj`` to its newest baked ``material`` with the ``digest`` its bake read (and ``own``, the same without
    the AO reach), its ``packing`` preset and what the bake read besides the target (``digest``'s ``sources``,
    ``cage`` and ``ao``)."""
    settings = obj.angelo_baked
    settings.material, settings.digest, settings.packing, settings.own = material, digest_, packing, own
    settings.sources.clear()
    for source in sources:
        settings.sources.add().object = source
    settings.cage, settings.ao = cage, bool(ao)


def digest(obj, depsgraph, sources=(), cage=None, ao=False, reader=None):
    """What a bake of ``obj`` (from ``sources``, through ``cage``, with an AO map when ``ao``) read now, as a hex
    string (``export_baked_reads``). ``reader`` shares one export's reads between objects."""
    return digests(obj, depsgraph, sources, cage, ao, reader)[0]


def digests(obj, depsgraph, sources=(), cage=None, ao=False, reader=None):
    """``digest`` and the same without the AO reach: ``(all, own)``."""
    return (reader or Reader(depsgraph)).digests(obj, list(sources), cage, ao)


# -- export -------------------------------------------------------------------------------------------------------

def plan(objects, depsgraph):
    """Which of ``objects`` export with their baked material: ``{"substitutes": {session_uid: material},
    "used": [(object, material)], "stale": [object], "nearby": [object], "packing": [(object, packing)]}`` (names,
    in the order given). ``nearby`` are the stale objects whose own inputs are unchanged: what changed is geometry
    within their baked AO's reach."""
    substitutes, used, stale, nearby, packing = {}, [], [], [], []
    reader = None
    for obj in objects:
        material = binding(obj) if obj.type == "MESH" else None
        if material is None:
            continue
        if obj.material_slots and all(slot.material == material for slot in obj.material_slots):
            continue   # Already assigned (Use Baked Material): the export writes it as it is.
        if obj.angelo_baked.packing not in GLTF_PACKINGS:
            packing.append((obj.name, obj.angelo_baked.packing))
            continue
        settings = obj.angelo_baked
        reader = reader or Reader(depsgraph)
        try:
            now, own = digests(obj, depsgraph, [item.object for item in settings.sources], settings.cage,
                               settings.ao, reader)
        except (RuntimeError, TypeError, ValueError, AttributeError, ReferenceError) as error:
            print("Angelo: the baked material of %s could not be checked (%s); its own materials are written"
                  % (obj.name, type(error).__name__))
            now = own = None   # Unknown is stale: the export keeps the object's own materials and says so.
        if now != settings.digest:
            stale.append(obj.name)
            if settings.own and own == settings.own:
                nearby.append(obj.name)
            continue
        substitutes[obj.session_uid] = material
        used.append((obj.name, material.name))
    return {"substitutes": substitutes, "used": used, "stale": stale, "nearby": nearby, "packing": packing}


@contextmanager
def active(found):
    """While an Angelo glTF export runs, ``substitute`` swaps the materials of ``found["substitutes"]``."""
    _SUBSTITUTES.clear()
    _SUBSTITUTES.update(found["substitutes"])
    try:
        yield
    finally:
        _SUBSTITUTES.clear()


def substitute(materials, blender_object, export_settings):
    """The glTF exporter's ``gather_mesh_materials_hook`` for Angelo's exports: every slot of a planned object is
    written with its baked material (``materials`` is the exporter's list for this object's mesh)."""
    if not _SUBSTITUTES or export_settings.get("gltf_export_id") not in EXPORT_IDS:
        return
    material = _SUBSTITUTES.get(blender_object.session_uid)
    if material is not None:
        materials[:] = [material] * max(1, len(materials))


def register():
    bpy.utils.register_class(ANGELO_PG_baked_source)
    bpy.utils.register_class(ANGELO_PG_baked)
    bpy.types.Object.angelo_baked = PointerProperty(type=ANGELO_PG_baked)


def unregister():
    del bpy.types.Object.angelo_baked
    bpy.utils.unregister_class(ANGELO_PG_baked)
    bpy.utils.unregister_class(ANGELO_PG_baked_source)
