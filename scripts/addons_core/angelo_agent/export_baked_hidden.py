# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a render shows, hidden in the viewport or not: ``export_baked_reads.Reader``'s view of the scene.

Contract: doc/angelo/long_jobs.rst, "Baked materials at export". A bake renders every object a render shows,
whatever the viewport hides (measured), so the digest reads each one as the viewport evaluates a shown object. Only
visibility that changes a render counts: a render's (Disable in Renders, a collection excluded or disabled in
renders, Show Instancer for renders, a child its parent instances on vertices or faces); hiding with the eye or
disabling in viewports leaves the digest as it was.

``Depsgraph.object_instances`` lists what the viewport shows. Of an object hidden with the eye the viewport still
evaluates the geometry (measured) but lists nothing, so the instances it makes (geometry nodes, a collection
instance, particles) were unseen; one disabled in viewports is not evaluated there at all, so its own mesh was read
without its modifiers' effect (an armature's pose, a shrinkwrap's target), and showing it read as a change.
``_bpy._angelo_digest.instances`` hands over the geometry of those (``each``), evaluating the disabled ones in a
temporary depsgraph of their own. Main thread.
"""

try:
    import _bpy
    NATIVE = getattr(_bpy, "_angelo_digest", None)
except ImportError:   # Outside Blender (a host test's stand-in bpy).
    NATIVE = None

DUPLI_CHILDREN = frozenset({"VERTS", "FACES"})   # A parent's instancing that hides its children from renders.


def visibility(view_layer):
    """``(rendered, evaluated)``: session uids of the view layer's objects a render shows (Disable in Renders off, in
    no excluded collection or one disabled in renders) and of those the viewport evaluates (in no collection
    disabled in viewports, Disable in Viewports off), hidden with the eye or not. As Blender's bases: an object in
    several collections counts when any of them shows it."""
    rendered, evaluated = set(), set()
    todo = [(view_layer.layer_collection, True, True)]
    while todo:
        layer, renders, evaluates = todo.pop()
        if layer.exclude:
            continue
        renders = renders and not layer.collection.hide_render
        evaluates = evaluates and not layer.collection.hide_viewport
        for obj in layer.collection.objects:
            if renders and not obj.hide_render:
                rendered.add(obj.session_uid)
            if evaluates and not obj.hide_viewport:
                evaluated.add(obj.session_uid)
        todo += [(child, renders, evaluates) for child in layer.children]
    return rendered, evaluated


def shows_self(obj, instancer):
    """Whether a render shows ``obj``'s own geometry (``instancer``: it makes instances or particles)."""
    if instancer and not obj.show_instancer_for_render:
        return False
    return obj.parent is None or obj.parent.instance_type not in DUPLI_CHILDREN


def each(depsgraph, objects, visit):
    """Call ``visit(geometry, original, matrix, is_instance)`` for the geometry each of ``objects`` shows in a render
    and each instance it makes, as the viewport evaluates them when shown. ``geometry`` (an evaluated object or the
    data an instance replaced its object's with) is valid during the call only."""
    if NATIVE is not None:
        NATIVE.instances(depsgraph, list(objects), visit)
        return
    # Without the native reader (another build): each one's own geometry as this depsgraph holds it, no instances.
    for obj in objects:
        visit(obj.evaluated_get(depsgraph), obj, obj.matrix_world, False)


def own(depsgraph, obj, visit):
    """``visit(geometry, matrix)`` for ``obj``'s own geometry as the viewport evaluates it when shown (``each``),
    once; False when a render shows none of it."""
    found = []

    def keep(geometry, original, matrix, is_instance):
        if not is_instance and original == obj and not found:
            found.append(True)
            visit(geometry, matrix)

    each(depsgraph, [obj], keep)
    return bool(found)
