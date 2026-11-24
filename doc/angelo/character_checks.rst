.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

======================
The pre-review checks
======================

Facts about a model's surfaces that an artist would otherwise find by orbiting around it before a review: faces wound
against their neighbours, closed shells turned inside out, parts floating free, parts no bone moves, surfaces that
clip at close range and see-through holes in each view. They are facts with where they are, never a pass or fail,
and they work the same for a stylised character, a prop or a building. Angelo 2.1.0.

Owners: ``character_geometry.py`` (the measurements, NumPy only, no Blender). The host side (reading objects through
``game_ready_mesh.read``, close-range clipping through Blender's BVH tree, silhouettes through ``view_silhouette``),
the read-only agent tool ``angelo_character_check`` and its summary in ``angelo_engine_check`` follow.

The measurements
================

* **Shells**: triangles joined through shared corners, after corners at the same place are welded (split normals and
  UV seams don't split a shell).
* ``flipped_faces``: faces wound against their neighbours in a shell (two faces crossing a shared edge the same way).
  The minority is reported, with up to 20 face indices and 5 world points.
* ``inward_shell``: a closed shell (every edge on exactly two faces) whose consistent winding encloses negative volume:
  inside out. Open surfaces (cards, planes) are never judged inside out: which side faces out is the artist's choice.
* ``floating_part``: a shell farther than 1 % of the figure's height (``TOUCH_SHARE``; the height is given, or the
  model's largest extent) from every other shell, measured surface to surface, so a part resting flush on a face,
  or pushed through it, touches it. ``gap`` is the part's true nearest distance. When no two shells touch, the
  largest is the model the others float from.

Surface to surface (``triangle_distance.py``)
=============================================

Two triangles are at zero when an edge of either crosses the other; otherwise their distance is the smallest of the
six corner-to-triangle and nine edge-to-edge distances, which is where the closest points of two separate triangles
always lie. Faces that cross, edges that meet and coplanar overlaps all come out at zero; a zero-area triangle is
measured by its edges. Measuring corners against faces alone misses the first three (audit F7, 2026-11-14).

The pairs worth measuring come from a bounding-volume tree: triangles sorted along a Morton curve into leaves of 8,
boxes over them, and each node's range of shells. Node pairs are walked level by level and kept only while their
boxes are closer than the bound and they hold more than one shell, so the work follows the surfaces that are near
each other, not the number of triangles squared. A floating part's gap is searched with a bound that is the
nearest distance measured so far or, before any, the smallest farthest-point distance between two boxes still in
play (each holds a triangle), which tightens at every level, so nearest boxes are measured first. ``check`` returns the count of node pairs visited
and triangle pairs measured as ``work``.
* ``unweighted_part``: a shell no bone moves (every corner's largest weight is zero), for a rigged model.
* ``see_through``: holes in a view's silhouette (background enclosed by the model, not touching the picture's
  border), largest first, with their area and bounding box in pixels.
