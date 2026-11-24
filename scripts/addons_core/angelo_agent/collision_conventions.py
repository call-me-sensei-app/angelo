# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Collision in the form each engine reads, applied to the copy an export writes (doc/angelo/game_physics.rst).

Game Physics colliders (``angelo_physics``: a shape and Collision Only) export as OMI or Godot name suffixes, which
Godot reads and Unreal and Unity don't. ``apply(scene, engine)`` rearranges a scene's collision-only meshes for one
engine. It changes the scene it is given, so it runs only on a copy: the engine link's write-back child, which loads
the saved file and exports it (``engine_link_writeback``), or any export that loads its own copy. Never on the
person's scene; the live frames of the engine link carry no collision (they are previews).

* **Unreal** reads convex collision by mesh name (``UCX_<mesh>_NN``) next to the mesh it belongs to. Each convex
  collision-only mesh is named after its nearest rendered ancestor (else its collision-only owner) and made a
  sibling of it, keeping its world placement. Unreal reads no concave collision by name, so a concave collision-only
  mesh (Mesh shape) is left out of the copy; its convex hulls (``collision_fit``) carry it, and the report says so.
* **Unity**: Angelo Link's importer rule reads ``<owner>_ConvexCollider_NN`` and ``<owner>_MeshCollider`` and turns
  them into MeshColliders (convex or not) on the owner, hiding their renderers.
* **Godot**: nothing to do; the glTF export writes OMI or Godot suffixes by ``Scene.angelo_physics_format``.

Rendered meshes keep their names. The report: ``renamed``, ``left_out`` (names), ``notes``.
"""

CONVEX, CONCAVE = 'CONVEX', 'TRIMESH'


def _collision(obj):
    physics = getattr(obj, "angelo_physics", None)
    return (obj.type == 'MESH' and physics is not None and physics.collision_only
            and physics.shape in (CONVEX, CONCAVE))


def _owner(obj):
    """The nearest rendered mesh above ``obj``, else the topmost collision-only mesh above it, else itself."""
    found, node = None, obj.parent
    while node is not None:
        if node.type == 'MESH' and not _collision(node):
            return node
        if _collision(node):
            found = node
        node = node.parent
    return found or obj


def apply(scene, engine):
    """Rearrange ``scene``'s collision for ``engine`` ("unreal", "unity", "godot", other: nothing); returns a report."""
    import bpy
    report = {"engine": engine, "renamed": 0, "left_out": [], "notes": []}
    if engine not in ("unreal", "unity"):
        return report
    collision = [obj for obj in scene.objects if _collision(obj)]
    plan, counters = [], {}
    for obj in sorted(collision, key=lambda o: o.name):
        owner = _owner(obj)
        kind = obj.angelo_physics.shape
        if engine == "unreal" and kind == CONCAVE:
            report["left_out"].append(obj.name)
            continue
        base = owner.name if owner is not obj else obj.name
        if kind == CONVEX:
            index = counters.get(base, 0)
            counters[base] = index + 1
            name = ("UCX_%s_%02d" if engine == "unreal" else "%s_ConvexCollider_%02d") % (base, index)
        else:
            name = "%s_MeshCollider" % base
        plan.append((obj, owner, name))
    # Names first to temporary ones, so a planned name never collides with one about to be freed.
    for index, (obj, _owner_obj, _name) in enumerate(plan):
        obj.name = "angelo_collision_tmp_%06d" % index
    for obj, owner, name in plan:
        obj.name = name
        if engine == "unreal" and obj.parent is not None:
            world = obj.matrix_world.copy()
            obj.parent = owner.parent if owner is not obj else None
            obj.matrix_world = world
        report["renamed"] += 1
    for name in report["left_out"]:
        obj = bpy.data.objects[name]
        for child in list(obj.children):   # Its hulls stay where they are in the world.
            world = child.matrix_world.copy()
            child.parent = obj.parent
            child.matrix_world = world
        bpy.data.objects.remove(obj)
    if report["left_out"]:
        report["notes"].append("Unreal reads no concave collision by name: %s left out; its convex hulls carry it"
                               % ", ".join(report["left_out"]))
    return report
