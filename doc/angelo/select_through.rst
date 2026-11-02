.. SPDX-FileCopyrightText: 2026 Angelo Contributors
.. SPDX-License-Identifier: GPL-2.0-or-later

##############
Select Through
##############

Blender's most-commented open request (`#73479 <https://projects.blender.org/blender/blender/issues/73479>`_):
in solid display, box, lasso and circle selection reach only what is visible, and the only way to
reach hidden elements is to turn on X-ray, which changes the display. Angelo adds a per-viewport
**Select Through** toggle (3D viewport header, beside X-ray): with it on, those three selections
take the path they take in X-ray while the display stays solid. First released in Angelo 1.0.8.

Contract
========

* ``SpaceView3D.use_select_through`` (``View3D::flag2`` bit ``V3D_SELECT_THROUGH``, ``1 << 30``).
  Off by default, saved with the viewport in the .blend, not animatable. A high bit, away from
  the bits upstream adds next; stock Blender ignores it when it opens the file.
* ``SELECT_THROUGH_ENABLED(v3d)`` (``ED_view3d.hh``) is read only by region selection:

  ================================== ============================================================
  Mode                               Effect of Select Through
  ================================== ============================================================
  Edit Mode, mesh (verts/edges/faces) box, lasso, circle: ``use_zbuf`` off, as in X-ray
                                     (``view3d_select.cc``)
  Weight/Vertex Paint, vertex mask   box, lasso, circle: as in X-ray
  Particle Edit                      box, lasso, circle: keys skip the depth test
                                     (``PE_set_view3d_data_ex``, ``particle_edit.cc``)
  ================================== ============================================================

* Unchanged on purpose: click selection (``view3d.select``), the nearest-element lookups tools
  share (loop cut, knife, shortest path, the pre-selection gizmo, snapping) and particle brushes.
  Clicking a spot keeps picking what is under the cursor; only drag selections reach through.
* What is reached is exactly what X-ray reaches, including how faces are tested (by their
  centre, as in X-ray). Selecting faces by covered area is a separate request and not part of it.
* Already through without the toggle, so the header does not offer it there: Object Mode (box
  selection writes object IDs whatever their depth; lasso and circle test origins), armature
  edit and pose box selection, curves (edit and Sculpt Mode), point clouds, Grease Pencil,
  lattices and metaballs.
* Not supported: the face selection mask of the paint modes. Its box, lasso and circle always
  use the select buffer, even in X-ray, so there is no X-ray path to take.
* Keymaps: no default binding (Blender users keep their keys). The property can be bound like
  any other, for example ``wm.context_toggle`` with ``space_data.use_select_through``, or
  right-click the header button > Assign Shortcut.
* The header button (``_select_through_applies`` in ``space_view3d.py``) is drawn inactive where
  X-ray already selects through, by each mode's own test (``_select_through_redundant``): in mesh
  Edit Mode whenever X-ray is on (``XRAY_FLAG_ENABLED``), in the paint vertex mask and Particle Edit
  only while X-ray is see-through (``XRAY_ENABLED``, opacity below 1). It uses the built-in icon ``ANGELO_SELECT_THROUGH``
  (``release/datafiles/icons_svg/angelo_select_through.svg``), added after every existing SVG icon
  so those keep their IDs; the non-SVG icons after that block shift by one, as they do whenever
  upstream adds an icon.
* Director hides the viewport header, so its top bar offers the same toggle after Blender's menus
  (``director_select_through.py``, drawn from ``director.draw_topbar``), in the same modes and
  greyed by the same rule. It is the Director view's own ``use_select_through``: the header, the
  pie and the top bar show and change one state. Z's shading pie also offers it, as its last item so the stock
  items keep their directions, in both modes: north-west in the default pie
  (``VIEW3D_MT_shading_pie``, the four shading types), south-west beside X-ray in the pie the
  keymap's "Extra Shading Pie Menu Items" opens (``VIEW3D_MT_shading_ex_pie``). No key is added.
* Hidden vertices (H) of the paint vertex mask are read from the original mesh: the evaluated mesh
  passes original indices (ORIGINDEX), and a modifier that renumbers vertices (Weld) made the old
  lookup on the evaluated mesh test the wrong vertex (``meshobject_foreachScreenVert``). Evaluated
  vertices with no original are skipped. This also fixes X-ray selection there.
* Seeing what was selected: in mesh Edit Mode, with the toggle on and X-ray off, the selected
  vertices, edges and face dots behind the surface are drawn over it at X-ray's opacity for hidden
  edit elements (0.5), and only those: unselected hidden geometry and everything visible draw as
  in stock Blender. ``Meshes::edit_mesh_hidden_selection_ps_`` (``overlay_mesh.hh``), submitted in
  the overlay's color-only stage (no depth attachment, so it reads the scene depth); shader
  variants ``overlay_edit_mesh_{vert,edge,facedot}_hidden_selection`` (``SELECTED_HIDDEN_ONLY``)
  leave the regular edit-mesh shaders as they are. It reuses the batches the regular passes
  request (face dots are requested in face select mode). In Front objects (the retopology setup) are
  covered too: Workbench merges their depth into the scene depth the pass reads (``merge_ps_``).
  The pass is not drawn for GPU selection. With the
  toggle off the pass is never set up or submitted. Blender's selection counts (status bar, statistics) count what was reached.
  The paint vertex mask and Particle Edit have no such drawing yet (their reached elements show when
  the view turns); a candidate for a later release.
* Agents: no impact. The agent's tools never run viewport region selection (selection is set
  through data), and the toggle is a display-side preference of a person's viewport.

Design notes
============

Upstream never settled a design (284 comments on #73479, no developer sign-off by 5.3). Most user
requests asked for drag selections only, clicks unchanged; open PR #109319 (tool settings flags, a
popover beside X-ray) takes the same native route, skipping the depth test. The UI team's
objection was "you select what you see": an option that only adds selecting through overlaps
X-ray, which already does it.

Angelo's maintainer chose the per-viewport header toggle beside X-ray (decision of 2026-10-24),
with this rationale: "If it's available in 3ds Max, Maya and Houdini, then I think it also makes
sense for us to have it." Artists move between those applications and Blender; selecting through
a solid view is something they already rely on there (see "Coming from other applications").
Campbell Barton's note abandoning D6322 (a tool setting for box, lasso and circle, 2019-2020) gave
five objections; how each is handled:

1. Not obvious when it is enabled. The toggle is a header button beside X-ray, shown wherever it
   changes selection and greyed while X-ray is on.
2. You do not see the geometry you select, so later tools may act on an unexpected selection. The
   selected elements behind the surface are drawn faintly over it (the hidden-selection pass
   above), and the selection counts include them. Measured: see "Drawing cost".
3. It interfered with edit-mesh drawing (it removed face dots, so face select mode was unclear).
   Edit-mesh drawing is untouched: with nothing hidden selected, the toggle changes no pixel in
   vertex, edge or face select mode; with X-ray on it changes no pixel; with a hidden selection it
   changes pixels only where that selection projects (``test_blender_select_through_draw.py``).
4. It showed as a tool option but selection is mostly used from key bindings. It is a viewport
   state that every box, lasso and circle path reads alike, from a tool or a key.
5. Alt-Z (X-ray) already does this. X-ray couples selection with display: to reach hidden
   elements the whole view turns transparent. Select Through decouples them: select through,
   keep looking at a solid model.

Coming from other applications
==============================

How the three applications artists most often come from handle the same choice (their current
documentation, October 2026):

============ ======================================= ================ ============================
Application  Control (sense)                         Default          Shows a hidden selection
============ ======================================= ================ ============================
Maya         Camera Based Selection, On/Off/Auto     Off: drag        X-Ray Active Components
             (on = visible only); global preference  selects through  (selected components drawn
                                                                      over shaded objects)
3ds Max      Ignore Backfacing, and Optional Culling Off: selects     backfacing elements highlight
             Backface/Occluded (on = visible only);  through          while previewing; See-Through
             per object or modifier                                   (Alt+X) makes the object
                                                                      translucent
Houdini      Select Visible Geometry Only (on =      On: visible only Draw as X-Ray (wireframe of
             visible only); global preference                         hidden geometry)
============ ======================================= ================ ============================

* None of the three binds a default key to it; Angelo keeps Blender's keymap unchanged too.
* Angelo names the opposite sense, Select Through, because Blender's default, like Houdini's, is
  visible only. Off it behaves as Blender always has; on it behaves as Maya and 3ds Max do by
  default: drag selections reach what is hidden, the display stays shaded.
* Seeing what was selected follows Maya's X-Ray Active Components: selected elements behind the
  surface are drawn over it, nothing else changes. Unlike Maya's setting it is not forgotten on
  restart: it is saved with the viewport.
* Clicks stay visible-only (Maya documents its option for drag selection; Blender's click picks
  what is under the cursor). 3ds Max's per-object scope does not apply: the toggle is the
  viewport's, so every object in Edit Mode behaves alike.
* To make it the default for new files, as in Maya and 3ds Max: turn it on, then File > Defaults >
  Save Startup File (the startup file keeps viewport settings).

Drawing cost
============

Worst case, everything selected on a dense edit mesh (87,848 vertices), offscreen 320 x 240 draw
plus read-back, 40 interleaved frames each way (``test_blender_select_through_draw.py``):
median 2.84 ms off and 4.27 ms on, p75 3.31 ms and 4.56 ms. Measured 2026-10-30 on Mac 2 (M5 Max)
while it ran other work, so contended and not a published claim; with the toggle off the pass is
never set up, so stock drawing pays nothing.

Blender 5.3
===========

The 5.3 branch changes none of the selection occlusion logic in the touched files (only includes
and an unrelated ``RV3D_FLIP_X`` / ``camroll`` in ``DNA_view3d_types.h``), and nothing upstream
implements Select Through by 5.3. Porting the selection side is a replay; check that ``1 << 30`` of
``flag2`` is still free. The drawing side needs one adaptation: 5.3 renames the mesh overlay's
color-only stage to ``draw_line_only`` (a line-only target), so the hidden-selection pass moves
there and its edge fragment output follows 5.3's ``pack_line_data_no_aa``.

Validation
==========

* Host: ``test_native_select_through.py`` (which functions read the toggle and which never do, the
  free DNA bit, RNA, icon, header modes, no default binding).
* Background: ``test_blender_select_through.py`` (default, per viewport, save/reload, header modes)
  and ``test_blender_select_through_draw.py`` (offscreen pixels and draw time, needs a GPU).
* GUI: ``test_blender_select_through_gui.py`` (two cubes, one hidden: box, lasso and circle in vertex,
  edge and face modes, the paint vertex mask and Particle Edit reach what X-ray reaches with the
  toggle on and skip the hidden elements with it off; a click on a hidden corner picks nothing,
  while the same click in X-ray picks it; the statistics count what was reached; a user-assigned key
  toggles it; H-hidden vertices stay out of a paint vertex mask selection on a welded mesh; header
  (hidden selection showing through) and angle screenshots, checked by eye).
* Director, the default view, replayable: ``test_blender_select_through_director_gui.py`` (real
  startup, window events: Tab, 1, Z's pie (numpad direction) to turn the toggle on, a dragged box
  reaching all of A and B, Ctrl+Z and Ctrl+Shift+Z undoing and redoing the selection
  with the toggle kept, the pie again to turn it off; Director's top bar offers it only in Edit Mode
  and shows the shared state after each change; screenshots of the top bar, the pie and both boxes).
  The toggle-off baseline (A's front only) runs the box operator in Director's window: simulated
  drags do not drive Blender's unchanged select-buffer path (dragged 0, operator 4, in three Mac 1
  runs), so the dragged result is recorded, not checked; a real-mouse check of it is a dogfood item.
* Draw: the In Front case (``in_front_hidden_selection_is_shown``) in
  ``test_blender_select_through_draw.py``.
