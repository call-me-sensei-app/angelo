.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

###################
Viewport UV checker
###################

One of Blender's most requested ideas: Right-Click Select's "Better UV visualization … Checker Pattern Overlay"
(470 votes, 5th of 17,379 ideas), with "UV distortion heat map in 3D view" (119), "Color coding for flipped UVs" (84)
and "Default UV Checker in Viewport Overlays" (70). Upstream has an open design, #162730 "UV Grid Overlay in the
viewport" (August 2026). Checking an unwrap in stock Blender means making a checker image and wiring it into every
material, then undoing that. Angelo draws the checker in the viewport instead: one toggle, every visible mesh, no
material, image or node touched. First released in Angelo 1.0.8.

Contract
========

* ``SpaceView3D.uv_checker`` (``View3DUVChecker``, a member of ``View3D`` in ``DNA_view3d_types.h``): a display
  setting of one 3D viewport, saved with the viewport in the .blend like its overlays, never animated. Nothing else is
  saved: no material, image, node, texture or slot is made, assigned or changed, in the scene or in the file.

  ===================== ========================================================= =================
  Property              Meaning                                                   Default
  ===================== ========================================================= =================
  ``show``              The toggle                                                off
  ``pattern``           ``COLOR_GRID`` (Blender's generated Color Grid image),    ``COLOR_GRID``
                        ``CHECKER`` (its UV Grid image), ``TEXEL_DENSITY``,
                        ``STRETCH``
  ``scale``             Pattern repeats per UV unit (one UDIM tile)               1
  ``uv_map``            ``ACTIVE`` (the map being edited), ``RENDER``, ``NAMED``  ``ACTIVE``
  ``uv_map_name``       The map ``NAMED`` reads                                   empty
  ``use_selected_only`` Only selected objects                                     off
  ``show_flipped``      Hatch faces whose UVs are mirrored                        off
  ``opacity``           Checker over the shaded surface                           1
  ``texture_size``      Texture width (px) the density is measured for            1024
  ``target_density``    Texels per meter shown as on target                       512
  ===================== ========================================================= =================

* Older files (stock Blender's, Angelo's from before) read the struct as zeros; versioning gives every 3D view the
  defaults when the file lacks the member (``version_angelo_uv_checker_defaults`` in ``versioning_520.cc``, keyed on
  the member, not a version number, since Angelo never bumps Blender's). Stock Blender ignores the member.
* Drawn in Solid and Material Preview; not in Wireframe, Rendered or X-ray (as Face Orientation). It is its own
  toggle: it draws with the overlays hidden too. Face Orientation draws over it when both are on.
* Meshes only (``OB_MESH``, display Solid or Textured), instances included. The object in Sculpt Mode (paint BVH
  drawing) keeps its shading. A mesh without the chosen map (no UVs at all, or no map of that name) is hatched magenta
  and dark grey, so a missing map is obvious, never blank or an error; the card says how many visible meshes lack
  it and names the first ones (``uv_checker.meshes_without_map``): counted when the card opens, after each change made
  in it, and while it is open after scene changes (a depsgraph update only marks it stale; a half-second timer counts
  once). Nothing counts per redraw or while the card is closed.
* Edit Mode: drawn from the edit mesh, so a UV edit shows at the next redraw (the UV editor's tools tag the mesh).
* UDIM: the pattern repeats every UV unit, so each tile shows the same pattern at the same density and nothing outside
  0-1 is lost.

Drawing
=======

``overlay_uv_checker.hh`` (the overlay engine, beside ``overlay_facing.hh``):

* Off (or in a mode where it is not drawn) its ``begin_sync`` releases its pass and returns; ``object_sync``,
  ``pre_draw`` and ``draw`` return at once. Nothing is requested from the batch cache, so a viewport with it off
  costs what it did before.
* On, each mesh's surface is drawn again over the shaded surface with the depth test of Face Orientation (equal
  over Workbench, less-equal over EEVEE), lit from the eye so its form stays readable, pre-multiplied by
  ``opacity`` into the overlay buffer.
* The UV map is resolved per mesh (``DRW_mesh_batch_cache_get_surface_uv_checker``): active, render or by name, and
  requested only if the mesh has that float2 corner attribute (Edit Mode extraction reads the BMesh by name). The UV
  buffer gives each map a checker reads one more alias in a slot of its own, ``cu0`` to ``cu3``
  (``cd_used.uv_checker``, ``UV_CHECKER_SLOTS``), and the pass draws a mesh with the shader variant of that slot. UVs
  are extracted once and again only when the mesh changes or a map is new to it, never per frame; viewports showing
  different maps of one mesh share one extraction. A fifth map at once starts the slots over (one extraction).
* The Color Grid and UV Grid images come from Blender's own generators (``BKE_image_buf_fill_checker_color``,
  ``BKE_image_buf_fill_checker``) at 1024 x 1024 into GPU textures with mipmaps, 4x anisotropic filtering and
  repeat, no Image data-block (``overlay_uv_checker_patterns.cc``). Each is made at most once per session, on a
  worker thread, the first time a view shows it, and shared by every viewport; switching patterns makes nothing new.
  Texel density and stretch sample no image (a 1 x 1 placeholder is bound).
* Turning it on never stalls an interactive view: until the shaders (compiled asynchronously) and the image are
  ready, the view draws without the checker and asks for another redraw. Offscreen and render draws, which have no
  next redraw, finish both in place.
* Texel density, stretch and flipped UVs come from screen-space derivatives in the fragment shader. Within a triangle
  the world position and the UV are both affine, so the map from the surface to UV space is the same at every pixel,
  and the screen's own Jacobian cancels from each ratio: exact per triangle, no CPU pass, live in Edit Mode.

  * Texel density: ``texture_size * sqrt(UV area / surface area)``, per meter through the scene's unit scale (per unit
    without a unit system), against ``target_density`` on a log2 ramp: blue a quarter or less, green on target, red
    four times or more. Collapsed UVs read as no density (blue).
  * Stretch: the ratio of the surface-to-UV map's two singular values (1 for a map without angle distortion), blue at
    1 to red at 4 or more. Collapsed UVs are the most stretched.
  * Only those two patterns compute the metric; the flip test runs only with Mark Flipped on.
  * Flipped: the UV tangent frame against the surface normal (``cross(dP/dx, dP/dy) . N`` times the UV determinant),
    which does not depend on the screen's y direction. Mirrored islands (often intentional) are hatched pink.

Drawing cost
============

Measured with ``bench_uv_checker.py`` (driver) and ``bench_uv_checker_draw.py`` (in-app). Each frame is an offscreen
1920 x 1080 draw of the 3D view with a GPU read-back, so GPU work counts. The scene is 400 grids with UVs
(1,280,000 triangles), and Edit Mode uses one 180,000-triangle mesh. *Navigating* frames turn the view a little each
time, so every engine draws again. *Static* frames repeat the view, so Workbench and EEVEE show what they accumulated
and only overlays draw. Mac 1 (M4 Pro), quiet, under the billed lock, 2026-10-31 18:38; builds: main 7338359b5b8 and
branch a61b12b45ef, release configuration. Medians over 5 rounds x 12 frames (p75 in parentheses).

* **Off costs nothing.** Checker off, navigating, three processes per build alternated (ABAB): main 16.684, 16.708
  and 16.615 ms; branch 16.771, 16.641 and 16.741 ms. The means differ by 0.049 ms, inside the run-to-run range of
  0.13 ms. Off, the pass is never set up and requests no batch.
* **On, against stock Blender's nearest route** (one Color Grid image texture in a material on every object, in
  Material Preview):

  ======================= ================ ================= ======================== ===============================
  Navigating              Solid, off       Solid, checker    Material Preview, checker Material Preview, stock material
  ======================= ================ ================= ======================== ===============================
  Heavy scene             16.77 (16.95)    17.89 (18.07)     80.60 (80.89)            84.09 (84.55)
  Edit Mode               8.41 (8.52)      8.66 (8.75)       --                       56.14 (56.37)
  ======================= ================ ================= ======================== ===============================

  Static frames: Solid off 2.07, Solid checker 4.17, Material Preview checker 4.80, stock material 2.96 ms; in Edit
  Mode 2.82, 2.92 and 2.87 ms. That is the overlay's own redraw. Stock Blender's route has nothing to redraw then, so
  it is the case where the checker costs most: about 1.2 ms on 1.28 million triangles.
* **First use** in a process (offscreen draws finish it in place): 74 ms on Mac 1 with a cold shader cache, then about
  4.6 ms per frame. An interactive view does this work off the drawing thread and draws without the checker until it
  is ready.
* **Audit points**, measured on Mac 2 (M5 Max, load about 4 to 10, three processes per build): pattern switch, median
  (max), 2.98 (4.45) ms before the fixes against 1.24 (1.33) ms after; cold first offscreen frame 5.5 to 5.9 ms
  against 4.7 to 4.9 ms; warm re-activation and two maps drawn in turn at steady-frame cost.
* **Pattern images, made against uploaded** (``--log draw.uv_checker --log-level debug``, Mac 2, three processes):
  the Color Grid is made in 5.2 to 6.0 ms on the worker (its labels' font loads on first use, about 3 ms more once per
  session), the UV Grid in 0.2 to 0.35 ms; the drawing thread then uploads either in 0.35 to 0.45 ms and queues its
  mipmaps in 0.03 to 1.4 ms.
* **Color grid labels and threads:** the Color Grid's labels draw with a font object of their own, one grid at a time
  (``image_gen.cc``), never the render stamp's ``blf_mono_font_render``. Before, grids made at once crashed
  (``test_blender_color_grid_threads.py``: twelve generated grids loaded by Cycles' image threads, stamping on, crashed
  in ``blf_font_draw_buffer`` 5 times in 5 on the earlier build; the fixed build passes, every grid byte for byte
  equal to one made alone). It was a latent Blender 5.2 race the checker's worker made likelier; the sequencer and the
  compositor already keep fonts of their own.
* **That font's life:** it is made private (``BLF_PRIVATE``, ``BLF_load_mono_default_private``) before it is
  published, under the load lock that ``BLF_unload_all`` and ``BLF_reset_fonts`` now take, so an interface font reload
  never frees it, even while a session's first grid is being made, and no lookup by path or name shares it.
  Instrumented on Mac 2 (a 50 ms pause after the font is made, never committed), the old publish-then-protect order
  lost the font to a reload in 4 of 5 runs; the private load kept it in 5 of 5.
  ``test_blender_color_grid_font_reload.py`` makes a session's first grids on Cycles' threads while another thread
  reloads the interface fonts, and checks every grid has its labels and equals one made alone.
* **Where the remaining cost is:** a sample of the checker drawing showed the main thread mostly waiting on the GPU,
  with the checker's own per-object CPU work about 6% of a frame. A further cut would draw the pattern inside
  Workbench's own pass instead of a second one, so Solid would pay nothing for the extra geometry; that is a candidate
  for a later release.

Controls
========

``uv_checker.py`` and ``uv_checker_card.py`` (add-on):

* ``angelo.uv_checker_toggle`` turns it on or off for the 3D view it runs in (else the window's largest, Director's
  view). ``angelo.uv_checker`` turns it on and opens the **UV checker card**.
* The card is a corner card (``card_host``, non-modal), so orbiting, editing and unwrapping go on while every change
  shows live: on/off, Pattern (Grid, Checker, Density, Stretch) with a line saying what it shows and, for density and
  stretch, the color legend; Repeats per UV tile (1x-16x) or, for density, Texture size (512-8K) and Target texels
  per meter (128-2048); Objects (All visible, Selected); UV map (Active, Render, or a visible mesh's map by name);
  Mark flipped UVs. It writes only that view's settings, finds the view again on each use, and closes if the view is
  gone. Other values (any scale or target) are set through the properties.
* Reached from the chat bar's + menu ("UV checker", section "Check", ``chat_destinations``), Director's top bar after
  Blender's menus (its glyph button, pressed while on, and the arrow that opens the card), the 3D view header in
  Manual mode (the same two buttons, appended to ``VIEW3D_HT_header`` in Solid and Material Preview or while on),
  the 3D view's View menu (Toggle UV Checker, UV Checker…), and F3, whose menu search reads that menu in both
  modes. No default key.
* All text is in Angelo's catalogues (21 languages). A UV map's name is data and is shown as it is.

Agents
======

``angelo_view`` has a ``UV_CHECKER`` look (``view_looks``): flat grey Workbench with the checker on each mesh's
active UV map (Color Grid, mirrored UVs hatched), so an agent can judge an unwrap it made. Like every look it writes
the drawing viewport's settings only for its own draw and puts them back, and every other look turns the checker off
for its draw (``view_looks.UV_CHECKER_OFF``), so a person's own checker never colours an agent's SOLID or MATERIAL
view. Agents do not turn the person's checker on or off: it is a display preference of their viewport, like Select
Through.

Prompt cost (maintainer ruling, 2026-10-31: in 1.0.8 with its bytes; the candidate's gate covers it), measured with
``coverage_prefix.py`` against main 7338359b5b8: the Claude plan schema 56,540 -> 56,553 B with parallel edit on
(56,470 -> 56,483 off, +13 B), Codex's declared tools 105,631 -> 105,741 B and code mode 90,878 -> 90,988 B
(+110 B, about 57 tokens), instructions unchanged; all of it in the cached prefix. ``plan_budgets.py`` records the
raised budgets.

Validation
==========

* Host: ``test_native_uv_checker.py`` (DNA layout and versioning, RNA, the ``cu`` alias and its re-extraction, zero
  cost while off, no data-block code, shader registration, the controls, the card's layout and actions on a fake
  view, the agent's look and other looks turning it off, the catalogues).
* Background, needs a GPU: ``test_blender_uv_checker_draw.py`` (pixels and data counts; see its docstring) and the
  heavy-scene draw time off/on.
* GUI in Director, replayable: ``test_blender_uv_checker_director_gui.py`` (window events; screenshots of each step).
