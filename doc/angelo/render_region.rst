.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

####################################
Render a region over the last render
####################################

Blender renders a render region (Properties > Output > Render Region, Ctrl+B) with Crop off by filling everything
outside the region with black, and while it renders the image shows only the region. Look-development iterates on
one part of a frame, so people asked for the region to land on the previous render instead: "Retain previous render
while rendering with render border" (Right-Click Select, 438 votes), "New Render Window" (246) and "Region Render Over
Previous Render" (35); no Blender release through 5.2 does it (angelo.studio/blender-requests, ``render-region``).

Angelo renders a region **into the last render**: the region is rendered again, and every pass of every rendered layer
changes only inside it; outside it every pass stays bit-identical. Compositing then runs again on the whole frame, so
a node that spreads light or blurs (Glare, Blur) near the region can change the composite outside it, as the region's
new light would in a full render; the person's card, the + menu, the approval card and the agent are told so. The last
render stays on screen while the region renders. A cancelled or failed render leaves the last render exactly as it was. Revert Region puts the region's
previous pixels back, and applies the region render again. A plain render is unchanged.

.. contents::
   :local:

What the person does
====================

* **In the render window** (the Image Editor showing the Render Result), pills at the top-left of the image
  (``render_region_pills``; card-kit pills, as the Asset Shelf's): **Render region**, and after a region render
  **Revert region** or **Show region render**. They are hidden while a render runs.
* **Render region** starts the region tool (``angelo.render_region_draw``): a hint chip at the top of the view
  ("Drag over the render to choose the region to render again · Esc cancels"); the person may zoom and pan first;
  the drag draws a teal rectangle; on release the region renders at once. In a 3D view looking through the scene
  camera the same tool reads the drag against the camera's frame. Escape or a right-click leaves the tool. A region
  smaller than 4 pixels a side is refused in the hint ("That region is too small").
* **From the chat bar's + menu** (Create: **Render** ▸ **Render region**), the **Render menu** (Render Region Over Last Render)
  and F3: ``angelo.render_region`` draws where the last render shows (the render window, else a 3D view through
  the camera; it opens the render window when neither shows). With no render yet, a card asks for a full render first
  and offers it.
* **After the person's region render**, a corner card in the render window (``render_region_card``) says what
  happened: *Region rendered over the last render* (Revert region, Render again, New region), *The region shows the last
  render again* (Show region render, ...), or *The region did not render* with *The last render is kept exactly as it
  was* (Try again).
* The drawn region is kept on the scene (``Scene["angelo_render_region"]``, Blender's fractions) for Render again.
  The scene's own Render Region settings are never changed by any of this.

Render slots
------------

The region renders over what the Image Editor shows in the active render slot: that slot's own render when it has
one, else a copy of the last render, which keeps its own slot. So a before/after compare across slots works: render
the full frame in slot 1, switch to slot 2, render a region, and switch between them.

An agent's look renders into its own slot and puts the person's slot back, so the Render's last result is then the
look's. One rule (``angelo_region_slots.cc``) says which render a region goes over, and the operator, Revert's poll and
the RNA all use it: ``RenderSettings.angelo_last_render_size``, which the add-on and the ``RENDER_REGION`` job check
against the frame's size, is the size of the render the person sees, never the look's, and
``angelo_region_render`` reads ``NONE`` unless the person's slot is the one the region went into. A look is still a
render of the scene, so like any render it ends a region's Revert.

The agent
=========

``angelo_job_start`` kind ``RENDER_REGION`` with ``region`` ([left, top, right, bottom] fractions from the top-left,
as ``angelo_view``'s, checked by the same ``view_spec.check_region``) is a background job like every render
(``long_jobs.rst``): the person approves it (the card says which pixels render again, that every pass stays as it
was outside them, that compositing runs again on the whole frame and that nothing is written to disk), it runs as Blender's own render job with progress, Cancel and a time budget,
and its result says whether the region was composited. It writes no file; ``angelo_view`` image ``RENDER_RESULT``
shows the frame. It refuses when there is no render to render over, or when the scene now renders another size
(``job_render_region.RegionRenderRunner.admit``). The agent's route leaves the scene untouched (no kept region).

The kind is one more enum item and one field on the existing tool (+263 B of declarations, measured, recorded in
``plan_budgets.py``): no new tool and no guidance text. An earlier session's transcript stays valid (adding a kind
removes nothing).

The native pipeline
===================

The work is in the render pipeline, so every engine that supports a render border gets it: Cycles, EEVEE, Workbench
and add-on engines, which all read the scene's render region.

* ``render.render`` takes three hidden, unsaved properties: ``region_over_last``, ``region`` (left, bottom, right,
  top fractions; the scene's Render Region when unset) and ``region_padding`` (pixels, default 32).
  ``editors/render/render_region.cc``: before the render, it checks the render the Image Editor shows
  (``region_render_base_usable``: the frame's size, pixels in a pass), takes it from the render or its slot (a copy
  for an empty slot), arms the scene's ``Render`` with it, and sets the scene's Render Region to the region padded by
  ``region_padding`` with Crop off for the render's duration. The scene's ``R_BORDER``/``R_CROP`` bits and region come
  back when the render ends, however it ends (``render_endjob``, ``render_freejob`` for a job killed before it ran,
  and the blocking ``exec``).
* **The engine is kept between region renders.** A region render renders with Blender's Persistent Data on, so the
  engine keeps its device, synced scene and BVH, and the next region render only syncs what changed: the first
  region render after an ordinary one builds them, every later one starts in tens of milliseconds. The cost is the
  memory Persistent Data always holds, until the next ordinary render, which frees the kept engine before it starts
  (``render_region_begin``) unless the scene keeps Persistent Data itself, so an ordinary F12 starts exactly as it
  would without region renders. Engines that render in a GPU context (EEVEE, Workbench) never keep one. A region
  render's job is also polled every 0.05 s instead of 0.2 s, so its end and its tiles show sooner.
* ``render/intern/angelo_region_render.cc`` (contract in ``RE_angelo_region.hh``):

  - **Armed**: the previous frame is kept whole, and the region's previous pixels of every pass, and every view's
    composite, are copied aside (the *kept* pixels).
  - **While rendering**: ``RE_AcquireResultImage`` shows the previous frame's passes (never its composite, which the
    region render does not update), and every tile an engine merges
    (``RE_engine_update_result``, ``RE_engine_end_result``) also lands on it inside the region. The Image Editor's
    tile updates are offset into the full frame (``region_render_display_offset``). An engine that draws its own
    progress (Cycles) draws it over the previous frame: the draw manager runs the image engine before the external
    engine (they are otherwise never used together), and the external engine neither clears nor shifts by the
    border's offset (``DRW_engine_external_region_underlay``).
  - **Where a border render inserts its result into black** (``render_result_uncrop``), the region of the rendered
    result is copied into the previous frame instead, which becomes the result. Only the region is copied: the
    padding gives a denoiser and screen-space effects context past the edge, and is dropped. Passes match by layer,
    full pass name (with its view) and channel count. A pass the previous frame lacked is added, empty outside the
    region; a pass not rendered this time keeps its previous pixels; both are reported as warnings. The previous
    composite is dropped so the compositor makes it again from the new passes.
  - **Cancelled** (the render's break check at that point), failed, or stopped before the engine handed its result
    over (``region_render_finish``): the kept pixels go back, so the previous frame is exactly what it was, live tiles
    included.
  - **Revert** (``render.region_revert``): swaps the kept pixels and composites with the region's current ones; the
    next call swaps them back. ``RenderSettings.angelo_region_render`` reads ``NONE``, ``APPLIED`` or ``REVERTED``
    (under the result's read lock: the next render frees the state on its own thread). The kept pixels go when the
    next render of the scene starts, and with every render result (a file load). A failed region render keeps the
    engine's own error on the restored frame and reports it.

Limits
======

* A region renders one frame, never an animation, and never over the Sequencer's output (both refused).
* Refused before anything changes, because the outside could not stay exact: Burn Into Image (Output > Metadata) on,
  which every render draws into the whole frame; a compositor that renders another scene (a Render Layers or
  Cryptomatte node of another scene: that scene would take the region as its border and be black outside it); a render
  already running.
* The last render must have the size the scene renders now (resolution and percentage); otherwise the operator asks
  for a full render first.
* The compositor runs on the whole frame again: a node that spreads light (Glare, Blur) near the region also changes
  the composite outside it, as the new region's light would in a full render. The passes outside never change.
* Render results are not part of Blender's Undo: Revert Region is the region's undo, one level deep.
* While a region render runs, the scene's Render Region settings hold the padded region; an Undo step pushed during
  the render records them, and the render's end restores them on the scene either way.

Performance
===========

``test_blender_render_region_perf`` measures it in the running app (Director, real render jobs, a fresh profile per
run). It alternates two binaries built in one tree with the same flags: main, then this feature (A B A B). Each
measure has one untimed warm-up, then 5 timed runs per arm. Every number below is the median (min to max), n = 10 per
binary.

Conditions:

* Mac 1 (M4 Pro, 20-core GPU), 2026-10-31 17:46 to 17:51 JST.
* A quiet machine: the slot waits until no other host suite, build or app runs before each arm.
* 1280 x 720 product shot. Cycles on the GPU (Metal) at 64 samples with denoising; EEVEE at 32 samples.
* A region of 20% of the frame.

**Unused: nothing slower.**

* F12 Cycles: 1.871 s (1.861 to 1.888) on main, 1.870 s (1.843 to 1.880) with the feature.
* F12 EEVEE: 0.284 s and 0.281 s.
* Director redraws: 1.5 ms Solid and 1.7 to 1.8 ms Material Preview on both.
* Render window redraw: 0.2 ms on main, 0.3 ms with the feature (the pills' draw).

**The first ordinary F12 after region renders** frees the engine the region renders kept. That costs 33 ms
(24 to 40) of its start: Cycles 1.894 s (1.861 to 1.935); EEVEE keeps none, 0.275 s.

* The free alone measures 31 ms (28 to 36); the render's pipeline is unchanged.
* It stays on the critical path on purpose:
  * Freeing after or beside the new render would hold both engines' device memory at once.
  * Freeing on another thread would race Cycles' teardown against the starting render.
  * Freeing on an idle timer would lose the reuse whenever the person pauses between regions.
* Reusing the kept engine for that render would change ordinary renders, so it is left out.

**The region against a full re-render on main.**

* Cycles: 0.544 s (0.535 to 0.555), 3.4x faster.
* EEVEE: 0.168 s (0.163 to 0.178), 1.7x faster.

**Where a Cycles region render's time goes**, before and after keeping the engine:

* Total: 0.857 to 0.544 s.
* Operator to pipeline: 2 to 4 ms.
* Pipeline: 0.844 to 0.536 s. Of this, the fixed cost (scene sync, BVH, device and denoiser setup, measured as a
  4 x 4 pixel region) drops from 0.372 to 0.058 s. The rest is the region's own pixels.
* Display and the job's end: 10 to 4 ms.
* The feature's own work (debug log ``render.region``): keeping the previous pixels 0.8 ms, the live tiles 0.9 ms,
  the composite 0.6 ms.

EEVEE renders in a GPU context and cannot keep its engine. Its fixed cost, 0.12 s of a 0.17 s region render, is
EEVEE's own render start, the same as for a full frame.

**The agent's job** runs from its approval facts to its end in 0.565 s (0.554 to 0.572). That is 0.02 s over the
operator, down from 0.19 s once its end is watched like a look's (``look_notify``).

The "before" figures come from a run of the previous build in the same session (n = 5).

Validation
==========

* Host: ``test_native_render_region`` (the drag's fractions, the smallest region, the agent's region and job
  contract, and the native hooks read from their sources), with ``test_native_chat_destinations`` and
  ``test_native_translation``.
* Pixels, background, in Blender: ``test_blender_render_region_pixels`` renders three unrelated scenes (product,
  city with a second view layer, glass with transparent film) in Cycles (denoised and not) and EEVEE, with the Z,
  Normal, Mist, Diffuse Color, Emission and AO passes, saves every layer and pass to multilayer OpenEXR and compares
  bit for bit: outside the region unchanged after a region render with nothing changed and after a material change;
  revert and reapply exact everywhere; an empty slot rendered over the last render while the last render's slot is
  kept; after an agent's look in its own slot, the size the job checks is unchanged and a region renders over the
  person's slot; the scene's Render Region settings restored; a plain render after reads ``NONE``. It also measures
  the seam: the mean change on the region's 2-pixel inner edge against the rest of the region. It is
  ``needs_opengl`` (its EEVEE cases need a GPU; ``test_native_suite_opengl`` holds every suite that renders with
  EEVEE, Workbench or ``render.opengl`` to that), and it prints ``ANGELO_RR_STEP`` before every step; ``render_region_probe.py`` runs it per variant, with stock
  controls, to place a crash.
* Window, Director (the default view): ``test_blender_render_region`` (render window pill, drag, the last render on
  screen during the region render, the card, bit-identical outside, Revert, a stopped region render keeps the last render).
