.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

#########
Shape Cut
#########

Draw-to-cut hard-surface modeling: draw a shape on a surface and cut, add, intersect, slice, inset or panel-line it,
with the result live and editable until you apply it. Stock Blender has the pieces (the Boolean modifier with its
Exact, Fast and Manifold solvers, Bevel, Weighted Normal, Smooth by Angle, Knife Project, and the Add Cube tool's
surface-aligned placement) but no tool that draws a cutting shape on a surface. Upstream's 5.3 Carver is a Grease
Pencil tool. Planned for Angelo 2.1.0.

One description of a cut
========================

The hand tool, the agent's ``shape_cut`` operation and the tests describe a cut with the same dictionary, checked by
``shape_cut_spec.validate`` (no Blender) and made by ``shape_cut.cut``:

============== ======================================================================================================
Key            Meaning
============== ======================================================================================================
``shape``      ``BOX`` (``size``: [width, height]), ``CIRCLE`` (``radius``, ``segments`` 3-256, default 32) or
               ``POLYGON`` (``points``: [u, v] corners around ``center``; at most 512, no crossing)
``center``     Where the shape sits in the surface frame, metres (default [0, 0])
``rotation``   Degrees, in the surface's plane
``operation``  ``CUT`` (difference), ``UNION``, ``INTERSECT``, ``SLICE`` (the target keeps the difference, a new
               object "<target> Slice" gets the intersection), ``INSET`` (a pocket whose walls are ``width`` thick,
               so a thin shell stays closed) or ``PANEL`` (a groove ``width`` wide along the outline)
``depth``      Metres below the surface; for ``UNION``, the height above it. ``THROUGH`` (``CUT``, ``INTERSECT``,
               ``SLICE``; the default for ``SLICE``) reaches past the far side of the target's bounds
``width``      ``INSET`` and ``PANEL`` only
``array``      ``{columns, rows, spacing: [u, v]}``, copies centred on ``center``; at most 400; copies may not touch
============== ======================================================================================================

Refusals name the first thing that is wrong (a crossing outline, a width too large for the shape, copies that overlap,
a unit mistake over 1 km). Nothing is made when a cut is refused.

The surface frame
=================

``u`` and ``v`` span the face's plane and ``n`` is its outward normal. Aligned to the **edge** (default), ``u`` runs
along the face's edge nearest the pointer (the longest edge when there is no pointer, as for the agent) and the
origin is that edge's first corner, so the grid lines up with the panel. Aligned to the **world**, ``u`` is the world
axis least aligned with the normal and the grid is the scene's. The grid step is a tenth of the viewport's floor grid.

What a cut makes
================

* One **cutter** per solid: a closed mesh, consistently oriented by construction, in the "Angelo Cutters" collection
  (not rendered), drawn as wire, parented to the target, tagged ``angelo_shape_cut`` with its description. An array is
  two Array modifiers (Columns, Rows) on the cutter, so count and spacing stay editable. Flat-sided walls are marked
  sharp; a circle's facets (under 30°) stay smooth.
* One **Boolean** modifier per cutter on the target (the slice's piece for its intersection), inserted before the
  finishing modifiers. The solver is **Manifold** when the target's result so far is closed (every edge between two
  faces), otherwise **Exact**; the report says which and why.
* A cutter's top sits 5% of its size above the surface and an added solid sinks 0.1% into it, so no Boolean face is
  coplanar with the surface; an inset's cup grows 0.05% past its opening so it overlaps the shell it closes.

Editing and finishing
=====================

* Move, rotate or scale a cutter to move its cut; change its Array counts; delete it (``remove_cutter``) and its
  Booleans go with it, leaving the target's own mesh as it was.
* ``finish`` (clean shading) puts Blender's Smooth by Angle, an optional angle-limited Bevel with hardened normals,
  and Weighted Normal (keep sharp, face area) last on the stack; running it again updates them, a width of 0 removes
  the Bevel.
* ``apply_and_clean`` applies the stack up to the last cut (as Blender's Apply does, in order: a Mirror before the
  cuts is applied too; finishing and later modifiers stay live) on the target and any slice piece sharing its
  cutters, removes the cutters, merges coincident vertices, dissolves degenerate faces and coplanar splits (within
  material, seam, sharp and UV borders) and marks edges sharper than 30° sharp. It reports faces before and after,
  n-gons, edges not between two faces and sharp edges. A target with shape keys is refused.

The agent
=========

The agent makes the same cut with the program operation ``shape_cut``, declared once through Angelo's shared
typed-operation pattern (``wrapped_ops``; listed in ``wrapped_op_modules.MODULES``, so ``wrapped_ops.registry``
checks the declaration and ``admit`` applies the shared turn budget). Until the shared runner that stages it is built,
the agent calls ``angelo.shape_cut`` from a script, as the feature guide's example shows. Its parameters map one to
one onto ``angelo.shape_cut``'s properties and
the operator runs without drawing (``EXEC_DEFAULT``) on the program's stage, so approval, preview, the one Undo and
the receipt are the program's. Run that way, the operator cuts the active mesh in Object Mode: ``face`` (an index on
the evaluated mesh) or ``towards`` (the largest face within 30° of a direction), ``shape`` ``BOX`` (``size``) or
``CIRCLE`` (``radius``, ``segments``), ``offset`` from the face's centre (``u`` along its longest edge, or the world
grid with ``align`` ``WORLD``), ``rotation``, ``operation``, ``depth`` or ``through``, ``width`` (``INSET``,
``PANEL``) and ``columns``, ``rows``, ``spacing``. Polygon and freehand outlines are drawn by hand only. "Cut a 2 cm
vent grid into this panel" is one operation: a 2 x 0.5 cm box, 1 cm deep, 3 columns by 6 rows at 3 x 1 cm.

Bool Tool
=========

Blender's Bool Tool add-on ships bundled with Angelo. Shape Cut adds no key binding and no menu entry outside the
chat bar's + menu and F3, and its keys exist only while it is drawing, so the two never compete. Bool Tool's own job,
cutting with an object you already have, is planned for Shape Cut (an existing mesh as the cutter, kept live in the
same way), after which Shape Cut can replace it. The two differ on applying: Bool Tool's auto operators delete the
cutters and, with its default preference, apply every modifier on the object; Shape Cut keeps cuts live and its
Apply bakes only up to the last cut, leaving later modifiers in place.

The hand tool
=============

``angelo.shape_cut`` (Object and Edit Mode, 3D viewport). Hover a surface to see its frame and grid; drag a box or
circle, click corners for a polygon (click the first corner or press Enter to close), or hold and draw freehand
(simplified to the grid's scale); then move the mouse to set the depth (the depth is where the view ray passes
closest to the line through the shape's centre along the normal) and click. Shift squares, Alt grows from the
centre, Ctrl turns grid snap off and on, T cuts through, 1-6 choose the operation, B, C, P and F the shape. Esc or
right click cancels and leaves nothing. The Boolean shows live while the depth changes; on a target over 200,000
faces the cutter moves and the Boolean updates on the click. One Undo step per cut; the tool has no redo panel, because a drawn outline is not one of its properties.

Validation
==========

* ``tests/python/angelo_agent/test_native_shape_cut.py`` (host): frames on tilted faces, snapping, outlines,
  simplification, inset and outset, closed solids with analytic volumes, validation messages, each operation's solids,
  the tool's mouse math.
* ``tests/python/angelo_agent/test_blender_shape_cut.py`` (Blender, background): each operation's volume change, a
  thin shell, an open mesh, a curved face, a rotated and scaled object, live editing and arrays, the finishing order,
  apply-and-clean; cut and evaluation times recorded.
