# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parenting that keeps where things are, for kit.parent (script_kit).

The recorded miss (doc/angelo/performance.rst, "Colours a request states", the parenting paragraph): a build made a
car's parts at their world places, set the body's location, and then parented each part with
``matrix_parent_inverse = body.matrix_world.inverted()`` in the same script. ``matrix_world`` is only brought up to
date by an evaluation, so it was still the identity: the inverse undid nothing and every part moved by the body's own
offset (7 m off the deck). ``parent_keep_world`` evaluates first, then parents each child and sets its inverse from
the parent's fresh ``matrix_world``, then puts the child back at the world matrix it had, so a child shows where it
was and moves with the parent from then on. A parent of None unparents in place. Like kit.cut it refuses, raising
before it changes anything, an object Blender does not evaluate in this scene (its matrix would be the old one), a
parent that is the child itself or one of its children, and a parent whose matrix has no inverse (a zero scale).

A child with a constraint that offsets it (Copy Location with an offset, say) moves by that offset, as with Blender's
own Keep Transform parenting: its constrained world matrix is what is kept.
"""


def parent_keep_world(bpy, scene, children, parent):
    """Parent each of ``children`` to ``parent`` (None: unparent) without moving it in the world; returns them."""
    bpy.context.view_layer.update()   # A location set just before reaches every matrix_world.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in ([] if parent is None else [parent]) + list(children):
        if scene not in obj.users_scene or obj.evaluated_get(depsgraph).as_pointer() == obj.as_pointer():
            raise ValueError("kit.parent: %r is not evaluated in this scene (disabled in viewports, in an excluded or "
                             "only instanced collection, or another scene): enable it first" % obj.name)
    inverse = None
    if parent is not None:
        for child in children:
            if child == parent or parent in child.children_recursive:
                raise ValueError("kit.parent: %r cannot be parented to %r, its own child or itself"
                                 % (child.name, parent.name))
        try:   # Once, before any child changes: a parent without an inverse leaves every child as it was.
            inverse = parent.matrix_world.inverted()
        except ValueError:
            raise ValueError("kit.parent: %r has no inverse (a zero scale): give it a scale first"
                             % parent.name) from None
    places = [child.matrix_world.copy() for child in children]
    for child, place in zip(children, places):
        child.parent = parent
        if inverse is not None:
            child.matrix_parent_inverse = inverse.copy()
        else:
            child.matrix_parent_inverse.identity()   # Unparented: no stale inverse left for a later parent.
        child.matrix_world = place
    bpy.context.view_layer.update()
    return children
