.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

#########
UV Layout
#########

Unwrap and pack meshes for one texture set, and an optional Search in Pack Islands. Blender 5.3's packer is good (concave
shapes, rotation, UDIM targets, pinned and merged islands) but runs one deterministic pass and has no texel-density
target, stacking, grouping or mip-aware padding. Planned for Angelo 2.1.0.

Pack Islands' Search
====================

``UV_OT_pack_islands`` gains **Search** (``search``, 0-1024, default 0: unchanged) and
``UVPackIsland_Params.search_trials``. Trial *t* packs the islands with the standard order rule, each island's size
changed by up to a quarter by a hash of *t* (odd trials order rotatable islands by area instead of their longest
side), so islands of similar size trade places. Trials run in parallel, each with its own layout; the tightest wins,
the standard layout first on ties, then the lowest trial, so the result is the same on every run and machine. With a
Fraction margin, Blender's line search for the scale runs first; the trials are then packed once each at the scale it
found, and only the tightest, when tighter than the standard layout there, gets a line search of its own from that
scale. Its scale replaces the standard one only when larger, so Search is never looser than one pass and costs about
one more line search plus one pack per trial (packing every trial at every line-search step made it far slower). The
fixed-scale path packs every trial once. Trials report no progress of their own and stop on cancel. The change is
upstream-shaped and opt-in, a candidate for contribution.

UV Layout
=========

``uv_layout.run(objects, spec)`` (meshes in Edit Mode together) returns a receipt. It is the step the game-ready job
calls, what the agent's ``uv_layout`` operation and ``angelo.uv_layout`` (chat bar **+** > **Model** > **UV
Layout**) runs by hand. UV selection follows mesh selection while it runs; the person's setting is put back.

================ ================================================================================================
Key              Meaning (default)
================ ================================================================================================
``texture_size`` Pixels, 16-32768 (2048)
``density``      Pixels per metre to set on every island, or None to even them out and fill the tiles (None)
``mips``         Padding that keeps islands apart down to this mip level (4; unset, see Too little room)
``unwrap``       ``KEEP``, ``SEAMS`` (angle-based on marked seams) or ``SMART`` (Smart UV Project, 66°) (KEEP)
``stack``        Congruent parts share the first one's UVs (False); ``mirrored``: mirror copies share too (False)
``group_by``     ``NONE``, ``MATERIAL`` or ``OBJECT``: each group in its own UDIM tile (NONE)
``search``       Pack Islands' Search for each tile, 0-256 (0)
``rotate``       Islands may rotate (True)
``packer``       ``QUALITY`` (Blender's Concave shape) or ``FAST`` (the AABB shape) (QUALITY)
================ ================================================================================================

* **Padding.** Blender adds the margin on every side, so islands end ``2 x margin`` apart. A mip-*m* texel covers
  2^m texels and filtering reads one more, so the gap must be at least 2^(m+1) texels: the margin is
  ``2^m / texture_size`` (Pack Islands' Fraction margin). ``test_native_uv_layout`` proves it by box-filtering a
  rasterised gap at the worst alignment.
* **Density.** px/m = ``texture_size * sqrt(uv_area / surface_area)`` per island (surface in world units); islands
  are scaled about their centre to the target and packed without scaling. Without a target, Blender's Average
  Islands Scale evens them first and the pack scales to fill.
* **Stacking.** Stacking works on parts (a mesh's connected pieces), not on islands, so congruent parts share UVs
  however their own unwrap split them. Parts are grouped by a signature (vertex and face counts, principal spreads);
  a part matches the first of its group when an alignment of principal axes puts every vertex within 0.1 mm of one of
  the first's (rotation and translation; a mirror only with ``mirrored``), and then takes its UVs face corner by face
  corner, seams included. A round part, whose two spreads across its axis are equal, is turned about that axis until
  a vertex lands (at most 256 ways). The matched vertex normals must face the same way: a flat or mirror-symmetric
  part's mirror image also matches it turned over, but its surface faces the other way and shared UVs would show the
  texture mirrored, so that counts as a mirror. Shapes with no distinct axis at all (a cube's corners, a sphere),
  parts over 4,096 vertices and parts whose faces don't correspond are never stacked, so a stack is never placed
  wrong. The match compares every vertex pair a block at a time, so its memory stays bounded (under 64 MiB at 4,096
  vertices). A copy's islands then lie on the first part's and pack with them (merge overlapping).
* **Tiles.** Each group starts on a new tile. With a density, a group plans ``ceil(area / 0.72)`` tiles, islands go
  largest first to the tile with most room, and each tile is packed once. Largest first, islands stay while the
  tile holds at most 0.72 of its area; the rest, and every island its pack leaves outside, move together to the
  group's next tile not yet packed (a new tile only after the last), while the islands inside keep their places (at
  most 100 tiles, UDIM 1001-1100). Every tile is packed at UDIM 1001 and then moved to its own: Pack Islands'
  closest-UDIM placement holds for 1001 but packed islands centred on another tile outside it.
  An island whose smallest turned rectangle is wider than a tile less both margins cannot keep a fixed density in
  one tile, so the layout is refused, naming how wide the widest is.
* **Too little room.** Filling the tiles (no density), the padding a mip level needs is fixed in pixels, so thousands
  of islands at 4 mip levels on 2048 px can leave them almost nothing (the held-out soup of 3,000 islands kept 1.3%,
  a scan of 10,731 kept 0%). Before packing, ``uv_layout_math.room`` predicts the most of each group's tile the
  islands can cover with their padding: from their boxes and true areas, the scale at which the padded boxes fill the
  tile, then their area at that scale (a ceiling a real pack stays under: 0.33 against 0.30 measured on a 449-island
  kit, 0.017 against 0.013 on the soup). Under ``ROOM_FLOOR`` (1/16 of the tile: less than a texture half the size on
  each side would give them whole) the layout still runs but never finishes silently: the person reads "Padding leaves
  the N islands only P% of the texture: lower Mip Levels Kept, use a larger texture or set a Texel Density" (a
  warning, which Director shows in its top bar), and the receipt's ``room`` gives the agent the share, the floor and
  the concrete fixes: ``fits_with_mips``, the most mip levels, and ``fits_with_texture_size``, the smallest texture,
  that would clear it (None when none does). That is for a margin someone set (the person in the dialog, the
  agent, a job's layout or a script's spec naming ``mips``): it is never lowered. A margin nobody set keeps 4 mip
  levels where the islands have room and otherwise drops to the most that leave them the floor
  (``uv_layout_math.fit_margin``; the coordinator's decision, 2026-11-18), never silently: the person reads "Margin
  lowered to M mip levels so N islands fit; raise the texture size to keep 4" and the receipt's ``margin_lowered``
  holds ``from``, ``to`` and ``keep_with_texture_size``. On the held-out set at 2048 px that is 3 for the
  triangle soup and 2 for the scan; the others keep 4 (``test_blender_uv_layout``). The dialog's Mip Levels Kept is
  not remembered between runs, so only a value the person sets counts as set. A fixed density spills to more tiles
  instead, so it is not judged by room (``room`` and ``margin_lowered`` are None).
* **Refusals change nothing.** A refused layout puts back each mesh's UV layers, the active layer's UVs and the
  face selection as they were before it began.
* **A mesh changed meanwhile.** Every re-read of the meshes (each pack, and the put-back) first checks that each
  still has the faces (every face's corners by vertex) and active UV map it had when its islands were fixed. One
  changed between two packs (by the person, a timer or a script) stops the layout, naming it, rather than writing UVs
  by face numbers that now name other faces; the put-back skips it and names it, as it does a mesh renamed or
  removed. Neither a refused pack nor a failed put-back leaves the layout half way: it ends failed with its cleanup
  done, by hand and as the job.
* **Packer.** Quality packs islands by their shapes and is the default: on generated plates it reached 0.61 of the
  tile where AABB reached 0.42, at thousands of times the time (stock Blender's Concave packer took 2.6-72 s a pass
  on 30-150 island kits on the 2.1.0 pool VM; to be measured on Mac 1-class hardware). Fast packs islands by their
  boxes: of Convex and AABB it gave by far the better efficiency per second (AABB 89 per second, Convex 0.04, median
  over five generated kits), and it equals Quality on box and cylinder kits, whose islands are rectangles. The agent
  keeps Quality unless the person asks for speed.
* **By hand, without freezing.** ``angelo.uv_layout``'s dialog hands the layout to ``angelo.uv_layout_run``
  (``uv_layout_job``): ``uv_layout.steps`` yields each pack, and each runs as Pack Islands' own window-manager job
  (``UV_PACK``, whose progress and stop ``_bpy._angelo_jobs`` offers), so Blender keeps drawing, the status bar shows
  the tile and the job's progress, viewport navigation works and Esc stops the job at its next island and puts the
  meshes back. A finished layout is one Undo step; the packs push none of their own. Run from its properties
  (``EXEC_DEFAULT``: scripts and the agent), every pack runs at once.
* **The interface lock.** A pack job locks the interface, as a render does, so the runner sets ``bl_options``
  ``LOCK_BYPASS`` (exposed to Python operators in ``rna_wm.cc``) to hear Esc. While any job holds the lock it only
  reads the job's progress and asks it to stop; it touches a mesh only once no job locks the interface, and it does
  not start while one does. A confined agent script may not register a ``LOCK_BYPASS`` operator or macro, and no
  caller can add the option to a registered one (:doc:`script_confinement`); Blender, add-ons and Angelo's own code
  may register one. Pack Islands' ``confirm=False`` only skips its settings popup: a script that starts a pack job
  with it starts an interface-locking job as a script-started render does, which Angelo's job manager does not run.
* **Receipt.** ``objects``, ``islands``, ``stacked``, ``tiles`` (UDIM numbers), ``efficiency`` (island area over the
  tiles used), ``density_px_per_m`` (low, high and area-weighted mean), ``margin_px``, ``gap_px``, ``mips_kept``,
  ``texture_size``, ``search``, ``packer``, ``room`` and ``margin_lowered`` (above), ``milliseconds``.

The agent
=========

The program operation ``uv_layout``, declared through Angelo's shared typed-operation pattern (``wrapped_ops``),
maps its parameters one to one onto ``angelo.uv_layout``'s properties (``texture_size``, ``use_density``,
``density``, ``mips``, ``unwrap``, ``stack``, ``mirrored``, ``group_by``, ``search``, ``packer``) and runs it
without the dialog (``EXEC_DEFAULT``) on the program's stage, the active mesh included whether selected or not;
approval, preview, the one Undo and the receipt are the program's. Search reaches the agent only here: the existing
``uv_pack`` operation keeps Blender's Pack Islands fields (an existing kind stays native). It is listed in
``wrapped_op_modules.MODULES``, so ``wrapped_ops.registry`` checks the declaration and ``admit`` applies the shared
turn budget; until the shared runner that stages it is built, the agent calls ``angelo.uv_layout`` from a script, as
the feature guide's example shows.

Only ``FAST`` layouts run in the turn, up to a measured input bound. A ``QUALITY`` one never does: the Concave
packer's time does not grow with the input (4-75 s on development kits of 4 to 160 parts on the pool VM), so no
bound keeps it inside a turn. The declaration says so (``job_kind="UV_LAYOUT"``, ``job_if=("packer", {"QUALITY"})``)
and the shared code (``wrapped_op_budget.admit``) refuses such a call before staging, at any size, with the
correctable ``LIMIT_EXCEEDED`` (``limit: "turn_budget"``, ``job_if: {"packer": "QUALITY"}``; a call that names no
packer counts as Quality), naming the ``UV_LAYOUT`` background job (``angelo_job_start``; doc/angelo/long_jobs.rst),
whose ``layout`` takes the same parameters (``uv_layout_ops.to_layout`` is the one mapping for the operator and the job;
the job's checks and the mapping's defaults read read-only copies taken as they load, so a script that changes the
operation's schema in place changes neither):

* **Admission** refuses an object that is not a mesh, is linked, hidden, not in Object Mode or has no faces, and
  ``unwrap: KEEP`` on a mesh with no UV map. Scene edits and reads wait while it runs (it locks the scene).
* **Run** (``job_uv_layout.UvLayoutRunner``): the target enters Edit Mode alone (other selected meshes would come
  with it; the person's selection comes back after), and each poll advances ``uv_layout_job.Layout``: one Pack
  Islands job per tile, its progress the job's, a cancel stopping it at its next island.
* **End.** Done: back in Object Mode with the receipt (``objects``, ``islands``, ``stacked``, ``tiles`` (at most 32
  listed, ``tile_count`` beyond), ``efficiency``, ``density_px_per_m``, ``margin_px``, ``gap_px``, ``mips_kept``,
  ``texture_size``, ``search``, ``packer``, ``room``, ``margin_lowered``, ``seconds``); one Undo takes the layout back. Cancelled or failed: every
  UV as it was. A fault while it runs fails it the same way once no pack runs; a mesh removed meanwhile ends it at
  once.

Benchmark against Blender's packer
==================================

Fixed before any tuning. Both sides get the same islands (the same unwrap), the same margin (Fraction, same pixels),
rotation Any, shape Concave; the only difference is Search 0 (Blender 5.3's packer as shipped) or Search N. Measured:
efficiency (island area in the unit tile after packing) and wall time, ABAB, every trial reported with its spread and
the build. A held-out set chosen now and never used for development: a hard-surface kit with hundreds of islands, a
smart-projected organic mesh, a generated triangle-soup asset, an architectural set and a 10,000-island scan. The
development set (``test_blender_uv_layout``'s box kits) is separate. Claims cite only the held-out set.

Result (2026-11-18, ``tests/python/angelo_agent/bench_uv_pack_heldout.py`` on the 2.1.0 pool VM, the batch-E tree,
rule and set committed before any pack ran; ABAB, 6 trials a side, no failures; Search 16 against Search 0):

================ ======= ================================ ===========================
Held-out mesh    Islands Efficiency (Search 0 = 16)       Time, Search 16 against 0
================ ======= ================================ ===========================
Hard-surface kit 449     0.2955                           +44% (1.92 s against 1.34)
Organic          1,042   0.1357                           +23%
Triangle soup    3,000   0.0128                           +15%
Architecture     390     0.3448                           +16%
Scan             10,731  0.0000                           +25%
================ ======= ================================ ===========================

Search found no tighter layout than Blender's packer on any held-out mesh and cost 15-44% more time, so its default
is 0: it stays for whoever asks for it, and nothing claims it packs tighter. The low efficiencies of the soup and the
scan are the padding's (4 mip levels at 2048 px around thousands of islands), not the packer's: see "Too little
room".

Validation
==========

* ``source/blender/geometry/tests/GEO_uv_pack_test.cc`` (GTest): the searched layout is the same twice, never looser
  than one pass, inside the unit square and free of overlaps (raster check).
* ``tests/python/angelo_agent/test_native_uv_layout.py`` (host): mip padding, density, tiles, stacking matches.
* ``tests/python/angelo_agent/test_blender_uv_layout.py`` (Blender, background): Search on box kits (ABAB, recorded),
  density on target, spilling to tiles, the gap, stacking, a tile per group, ``angelo.uv_layout`` run from its properties,
  a mesh changed between two packs (stopped and named) and the read-only tables.
* ``tests/python/angelo_agent/test_blender_uv_layout_gui.py`` (GUI): by hand, each pack is Pack Islands' job while the
  interface keeps going (no stall of 1 s), a layout runs to the end inside its tile, Esc stops one mid-pack within 5 s
  and every UV is as it was.
* ``tests/python/angelo_agent/test_native_uv_layout_hardening.py`` (host): the tables later layouts read are
  read-only, and every ending path (a refused pack, a fault, a failed put-back, a lock) finishes its cleanup.
* ``tests/python/angelo_agent/test_native_uv_layout_job_kind.py`` (host): the ``UV_LAYOUT`` kind's fields, bounds,
  defaults and refusals, the shared mapping, its receipt, its dispatch and the operation's routing to it.
* ``tests/python/angelo_agent/test_blender_uv_layout_job.py`` (GUI): the job through the real executor: admission
  refusals, progress, the scene guard, a done receipt with mode, selection and settings back, one Undo, a cancel
  within 5 s that puts every UV back.
