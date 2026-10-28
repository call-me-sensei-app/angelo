.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

One-Shot Tests
**************

A one-shot test is one user message, sent once: a brief with one or more
reference images or, for the `mini tests`_, a single short request. Then
nothing: no second message, no notes, no relayed errors, no person. The
harness waits until the agent ends its turn (or a limit is reached), grades
the result with closed checks, and records how the agent got there. It runs
on Angelo and on stock Blender with an MCP server, with the same model on
both sides.

One-shot tests are task sets of ``bench_mcp_vs_angelo.py``
(``--task-set oneshot-<id>``) and of ``eval_live_agent.py``. They reuse the
scenario machinery (`scenario_benchmarks.rst <scenario_benchmarks.rst>`_):
references delivered identically to each runtime, closed checks run by the
driver and never by the model, the scene digest, the end-of-session save and
the capture in stock Blender. The scenario sets, the default five-task set
and the long set keep their tasks, prompts, checks, approval rules, time cap
and launch commands; ``test_native_oneshot_wiring.py`` and
``test_native_scenario_registry.py`` pin that.

The contract
============

#. **One message per side.** The driver sends the brief with its references
   and never types again; it never sends "continue". If the agent stops
   before the brief is met (a limit, an error that ends the turn, a refusal),
   the test records why and ends. The rule is structural: the chat's
   ``send`` (Angelo) and the client stream's ``send`` (MCP side) are wrapped
   so that a second call raises, whoever makes it. The one exception is a
   diagnostic run's ``--ask-why-on-miss`` question, sent after the trial was
   graded and its scene saved for scoring, through the unwrapped ``send``,
   and never graded (`debug mode <debug_mode.rst>`_).
#. **Approvals.** Nothing waits for a person. On Angelo the chat keeps the
   product's default approval, Auto bounded edits, so the typed scene tools
   (sculpting and preview acceptance included) run without asking; the tester
   approves what still asks and only edits the scene, as a person building a
   scene would: reviewed Python (two clicks, as in the app) and the render
   job. Every approval is counted and the request receipt times the wait.
   A comparison with the MCP side, whose scripts run without a prompt, turns
   on the product's own Always allow scripts setting
   (``--angelo-always-allow-scripts``, never the chat-level Trusted scripts
   test switch), and every report states "scripts allowed"
   (``bench_integrity.scripts_label``).
   Still declined, and reported when it happens: file writes and deletes,
   generation and Generation Flows, delegation, memory notes, workspace
   changes and the team library. A decline is the harness's rule, so the
   report names each declined tool apart from the product's own errors and
   says so. The MCP side has no approval gate.
#. **The same brief and the same images on both sides.** The brief is
   written for a capable 3D artist's assistant in general: it never mentions
   tools, Python, approvals, what the model can or cannot see, or how to
   work. ``test_native_oneshot_registry.py`` checks every brief for that.
#. **A brief, not a script.** Subject, scale in metres, the ingredients that
   must be present and how they are named, materials, light and time of day,
   camera and framing, the output (a final still at a stated size and engine,
   rendered by the agent as part of the job), and the instruction to look at
   the result next to the references and fix what is wrong. Specific enough
   to check, free enough that the model designs.
#. **Grading by closed checks only** (`Grading`_). No score judged by a model
   counts towards pass or fail.
#. **Path metrics** per side (`Path metrics`_).
#. **Limits** per test (`Limits`_): a time cap for the turn and a cost cap,
   after which the harness stops the side and records it.

Fairness
========

* Both sides receive the same brief text and byte-identical reference files
  (SHA-256 checked before the send): Angelo as chat attachments, Claude Code
  as base64 image blocks, Codex as local images.
* Both sides start from Blender's factory scene and use the same model and
  effort.
* The MCP side's client is allowed what a person doing this work would
  allow:

  * every tool of the Blender MCP server, model-written Python included,
    which is unrestricted on that side in every task set;
  * Claude Code: its Read tool on image files anywhere, and its read-only
    file tools in its working folder and in the trial folder, which is stock
    Blender's working and temporary folder, so it can open the images it
    rendered. Anything else that would prompt (shell, edits, the web) is
    denied and counted, because nobody answers prompts in an unattended run;
  * Codex: its read-only sandbox, in which shell commands run read-only and
    local images can be opened; MCP tools are approved automatically and
    nothing asks a person;
  * the reference images are also files in the client's working folder, as a
    person's project would hold them. That folder is empty otherwise and no
    project context is visible from it (``final_runs.rst``, "What runs";
    ``bench_workdirs.py``): a folder inside a checkout would give the MCP
    side the checkout's ``CLAUDE.md`` and git status.

  Without the image rule, a Claude Code profile whose permission mode asks
  before reading outside the working folder (``--permission-mode default``)
  would have every read of a still rendered elsewhere denied.
* The brief asks for a render, so neither side is told not to render.
* Limits and stop rules are the same on both sides.
* Each trial records what it was sent and graded with: every task record, on
  both sides, keeps the SHA-256 of the prompt text it sent and the test code's
  version (the checkout's commit and the SHA-256 of the harness and grader
  sources, ``bench_integrity.test_code``). The report prints the brief's
  SHA-256 and names any trial that recorded another or none.
* ``--reuse-mcp-results`` pairs new Angelo trials with an earlier run's MCP
  trials only when that run had the same task set, runtime, model, effort and
  reference digest, every task the same prompt SHA-256, every trial the same
  test code (the harness and the product modules its graders run, such as
  ``scene_validation.py``) and was no diagnostic run. Anything else, including
  a trial without these stamps, is refused before the first trial and
  again by ``--summarize-only``; rerun both sides instead. In practice reuse
  needs the same harness commit. ``--cross-model-reuse`` allows another model
  or effort (never another runtime) for a tier measurement
  (``model_routing.rst``): such a run says so in every report, its reused
  trials are priced by their own model, and the gate tool refuses it
  (``measured_results.py`` reads only its Angelo side and still accepts it).
* ``--summarize-only`` rebuilds a run as it was recorded: its options must
  match the recorded plan's order, task set, runtime, model and effort, the
  planned n comes from ``RUN-PLAN.json``, and every trial folder present is
  read, so no trial is dropped silently.
* A trial is invalid, never failed, when the provider throttled it, by the
  same rule on both sides and runtimes (``eval_provider_limits``): a limit
  error, or a throttled request (HTTP 429) that was waited out and retried,
  whether Claude Code or Codex retried it or Angelo resent it. Angelo's
  receipts count its runtime's own retries (``claude_stream.retry_kind``,
  ``codex_retry.note``) as the MCP side's stream shows them. A retry for any
  other reason (5xx, an overloaded provider) never makes a trial invalid.
  Invalid trials are counted and left out of every rate. Two consequences:
  the rule is length-biased, since a trial with more requests is more likely
  to meet a throttle, so the side that makes more requests loses more trials;
  and one throttle stops the run, and in a final run blocks the next steps on
  that provider connection for a cooldown (``final_runs.COOLDOWN_S``).
  Reports state the invalid trials per side. A trial without a test-code
  stamp has no record of Angelo's own retries, so a re-summary applies the
  waited-out part of the rule to neither side of it and the report lists
  those trials.
* Every trial is a fresh launch on both sides, so its one message is a cold
  start, classified by launch position and never by a cache hit, with any
  warm-up of that launch in it (``final_runs.rst``, "Cold start by launch
  position").
* The planned n per side is fixed before the first trial (the run plan's
  ``order``; a final run's ``planned_n``), and every report states the planned
  and the valid n per side and flags a side whose valid n differs.

What differs is each product as shipped: Angelo attaches a bounded scene
observation to the message and asks before scene changes; the MCP side must
call tools to see the scene and runs model-written Python without a gate.
Stock Blender and the Angelo build are different binaries; references,
captures and the harness's final still all use stock Blender.

The tests
=========

======================  ==========================================  ======================  ==========================
Task set                Subject                                     References              Final still
======================  ==========================================  ======================  ==========================
``oneshot-bridge``      The bridge scenario's whole scene (valley,  front, side, top, hero  1920 x 1080, EEVEE, frame
                        arch bridge, railings, lamps, river and     (rendered from the      60 from ``Deck Camera``,
                        rocks, golden-hour light, deck camera,      ground truth)           ``bridge-final.png``
                        keyframed car, render setup)
``oneshot-footbridge``  A timber footbridge over a stream, with     hero, front, top,       2560 x 1440, EEVEE with
                        railings and stones, late afternoon         railing close-up        ray tracing, from ``Hero
                                                                    (rendered from the      Camera``,
                                                                    ground truth)           ``footbridge-final.png``
``oneshot-tree``        One broadleaf tree on a grassy rise, a      one picture: the tree   2560 x 1440, EEVEE with
                        bright summer morning                       cut from a reference    ray tracing, from ``Hero
                                                                    painting                Camera``,
                                                                                            ``tree-final.png``
``oneshot-cottage``     A small stone cottage with a terracotta     one picture: the        2560 x 1440, EEVEE with
                        roof, a door and a wall lantern, golden     cottage cut from the    ray tracing, from ``Hero
                        hour                                        same painting           Camera``,
                                                                                            ``cottage-final.png``
``oneshot-castle``      A whole fairytale castle on a meadow: six   hero (rendered from     2560 x 1440, EEVEE with
                        round towers with slate cones,              the ground truth)       ray tracing, from ``Hero
                        battlements, an arched gatehouse, a keep,                           Camera``,
                        windows, late afternoon                                             ``castle-final.png``
======================  ==========================================  ======================  ==========================

``oneshot-bridge`` keeps the bridge scenario's ground truth and every one of
its 46 closed checks unchanged and adds a tidy-file check and the saved
still (48 checks). ``oneshot-footbridge`` has 25 scene checks and the saved
still (26).

``oneshot-tree`` (34 scene checks and the still) and ``oneshot-cottage`` (36
and the still) attach a picture instead of renders of a ground truth: a part
of a reference painting, cut out and enlarged. Their own ``build`` is a
stand-in (``GROUND_TRUTH = "stand-in"``): the plainest scene that follows the
brief, which proves that the checks can be met and is not a look to match.
Reports say so, and show no similarity score against it.

* The tree brief asks for "a few thousand leaves or leaf clusters" once and
  names no way to make them: the route is what is measured. Its checks count
  separate pieces however they are made (one mesh of leaf cards, instances
  of a leaf, or leaf objects; a leaf placed as an instance carries the
  foliage's material), find the trunk and the clearance around it with rays
  from four sides at three heights, and count the limbs where they cross a
  cylinder 0.45 m around the trunk's axis: close enough that a limb's own
  side branches are not counted as limbs, so a tree whose main limbs share a
  base further out than that counts them as one. Two limbs that cross the
  cylinder at overlapping angles merge for the rays, so separate pieces of
  wood that grow out of the trunk (they start within 0.25 m of the axis, the
  largest trunk radius the brief allows, and spread past the cylinder) count
  too, and the larger count is the tree's.
* The cottage brief departs from its picture on purpose and says so: the
  painted roof is slate and the brief asks for terracotta, and it adds a
  door and a wall lantern the painting does not show. A side that copies the
  picture instead of reading the brief fails those checks.
  Door dimensions measure the exact named leaf, including Blender's numbered
  siblings, rather than its separately named frame or doorstep. Placement and
  visibility are one ray each through the middle of the door at 1.1 m, like
  every other probe: a seam the build leaves there is part of the build.

The footbridge's two briefs
---------------------------

``oneshot-footbridge`` names its parts with one rule that every part can
meet: "every part's name starts with 'Footbridge', except the railings,
whose part names start with 'Railing'". The railing checks find the railings
by 'Railing', and the checks that select by 'Footbridge' read the deck,
beams and sills, never a railing part, so both sides are graded on one
meaning of the brief.

``oneshot-footbridge-conflict`` is the same test with a brief whose two
naming rules no railing part can meet together: "every part's name starts
with 'Footbridge'" for the bridge, and "Every railing part's name starts
with 'Railing'" for its railings. It has the footbridge's pictures (byte for
byte: it reads the footbridge's reference folder), limits and still, and the
footbridge's checks with railing parts found under either of its two rules
('Railing ...' or 'Footbridge Railing ...'): a check tests what its brief
states, and this brief states both. It measures how an agent resolves a
brief that contradicts itself, as a regression check of Angelo against its
own behaviour rather than a comparison with the MCP side, and the final runs
(`final_runs.rst <final_runs.rst>`_) do not include it.

The bridge's two briefs
-----------------------

The bridge's railing posts stand every 1.5 m from x = -12 to 12 m on a deck
that ends at x = -12 and 12 m. Posts 0.1 m thick centred at the ends reach
5 cm past the deck, so ``oneshot-bridge`` says where the end posts go ("the
end posts are centred on the deck's ends at x = -12 and 12 m, even though
half of each then reaches past the deck end"), as the ground truth builds
them. A build that follows this wording passes every railing check, the
railings' span included (``test_native_oneshot_bridge_endpost.py`` runs them
with the grader's own code).

``oneshot-bridge-conflict`` is the same test with the brief's posts only
"every 1.5 m from x = -12 to 12 m". An agent that keeps them on the deck
moves the end posts inwards (to 11.95 m), and the check that probes the end
post at exactly x = 12 then meets only the moved post's edge and misses. It
has the bridge's pictures (byte for byte: it reads the bridge's reference
folder), checks, limits and still: a regression check of Angelo against its
own behaviour, not a comparison with the MCP side, and not part of the final
runs. The multi-turn ``scenario-bridge`` keeps that shorter wording in its
railings turn; the final runs do not include it either.

The castle
----------

``oneshot-castle`` is the heavy one-shot: a generic romantic castle in the
spirit of the Bavarian revival castles (the brief names Neuschwanstein, a real
public building, as the inspiration; no brand's castle is named, imitated or
pictured). It stresses what big builds need, so its output is long:
repetition (six round towers, dozens of merlons, a dozen windows), an arch, a
closed ring of walls and well over a hundred objects. The brief fixes the
layout in metres (courtyard, tower centres and radii, gatehouse, keep) and
leaves the design to the model. It also states the naming groups the checks
read, the same for both sides: the curtain wall and its battlements start
with 'Castle Wall', each tower and its roof with 'Castle Tower', the
gatehouse with its battlements and arch with 'Castle Gatehouse', the keep
and its roof with 'Castle Keep', every window with 'Castle Window', any other
castle detail with 'Castle' and a word of its own, and nothing outside the
castle with 'Castle'. The groups are stated because the checks measure by
them: battlements named after the wall would be measured as curtain wall
wherever they stood. Its one picture is the hero shot of the stand-in (``castle_kit``), rendered like the footbridge's references
(``GROUND_TRUTH = "reference"``), and it has 32 scene checks and the still
(``LIMITS``: 3,600 s, 900 s for the render, 40 USD). Besides the usual kinds,
``castle_checks`` measures, with rays that pass through other objects. A ray
that meets another object's face still reports a matching face within 1 mm of
it: two parts built on one outline share that surface (a curtain wall run on
through the gatehouse and cut by the same arch hides the gatehouse's arch
underside behind its own, and without the matching face the arch check
would read that round arch as flat):

* a round tower at each of the six stated centres: 16 horizontal rays (a
  gate tower's bearings into the gatehouse block not asked; its window
  counts as the tower) all meet it at nearly one distance from its axis
  (round, not square), and rays from
  above meet a roof that falls from apex to eaves (pointed, not flat) in a
  blue-slate colour;
* the battlements: rays straight down along the wall-walk lines, across the
  outer half of the wall ("along the whole outer edge"); runs of samples
  clearly above the walk are merlons (at least thirty);
* the enclosure: 72 rays out of the courtyard above the gate's crown all meet
  the wall, a tower, the gatehouse or a window set in them;
* the arch: under the gatehouse the underside is higher mid-passage than at
  its sides, and a ray through the passage at head height meets nothing;
* nothing floats: every connected piece of the castle reaches the ground
  (the top of 'Ground', else height 0) through pieces whose bounds touch,
  within 1 mm of float error; towers and keep stand apart in plan;
* the camera frames the castle: its vertices (every n-th, at most 20,000)
  project inside the frame;
* the keep is taller than every other part of the castle.

Its tidy-file check, like every scene test's, counts unused data as a save
writes it (`Grading`_): none ("no unused leftovers").

``oneshot_variants.py -- oneshot_castle`` grades 18 builds in stock Blender:
four other ways to follow the brief (eight-sided towers, merlons merged into
the walls, every merlon its own object, the south wall run on through the
gatehouse and cut by the same arch) pass every check, and twelve defects
(square or flat-roofed or stone-roofed towers, a solid or closed gateway, a
gap in the wall, ten merlons, a floating roof, a tower inside the keep, a low
keep, a camera aimed away, dark windows) fail exactly the checks for them;
forty zero-user data-blocks left alone pass, and thirteen materials a save
keeps unused fail the tidy-file check.

Face and hair tests
-------------------

Five tests measure how precisely an agent can sculpt a face or groom hair
to pictures (``--task-set oneshot-face-hair`` runs them in turn). Each brief
attaches renders of a ground truth and states the orthographic pictures'
scale and centre, as a reference sheet would, so every feature can be placed
to the millimetre, and states the precision the checks grade.

==========================  ===================================================  ==================  ==================
Task set                    The brief                                            References          Starts from
==========================  ===================================================  ==================  ==================
``oneshot-head-stylised``   A cartoon head (large cranium and eyes, button       front, side, hero   the factory scene
                            nose, round chin), features within 2 mm
``oneshot-head-portrait``   Sculpt a neutral head into a realistic person,       front, side, hero   ``Head``: a neutral
                            eyes moved into place, features within 2 mm                              head with its eyes
``oneshot-hair-short``      A side-parted short crop of 10,000+ real strands;    front, side, back,  the person's head
                            outline 4 mm, hairline 3 mm, length 1 cm             hero                with its eyes
``oneshot-hair-long``       Long straight hair parted in the middle, the same    front, side, back,  the person's head
                            precision                                            hero                with its eyes
``oneshot-head-anime``      An anime head (very large eyes, small nose and       front, side, hero   the factory scene
                            mouth, pointed chin), features within 2 mm
==========================  ===================================================  ==================  ==================

The heads are analytic: signed distance fields of ellipsoids, round cones and
spheres joined by smooth unions and carved by smooth subtractions
(``head_designs``, ``head_sdf``), meshed by surface nets for the renders and
the start files (``head_kit``). The hairstyles are strands grown on the
portrait's scalp, following the scalp and gravity and kept outside the head
(``hair_kit``). Both are seeded and deterministic. The pictures are renders
(``GROUND_TRUTH = "reference"``); the face pictures are 0.512 m across (0.4 mm
a pixel), the hair pictures 0.704 m (0.55 mm a pixel). The portrait and hair
tests append a start file into both sides' factory scene before the message
(``START``, made by ``scenario_start_job.py`` before the references and kept
with them), so both sides sculpt or groom the same bytes.

**How a head is read** (``face_casters``, ``face_measure``). A side's head is
read in the pictures' own orthographic views by ray casts through its
evaluated scene, so any construction reads the same (a sculpted, remeshed,
Multires, subdivided or Geometry Nodes mesh); rays pass through anything
that is not the head. The ground truth is read the same way from its design
alone, by sphere tracing its distance fields, never from a stored picture or
mesh. Both use one lattice and the same windows and rows, placed from the
design's expected landmark heights (``face_truth.placements``), so only the
caster differs:

* the **outline** of each view: every point where a lattice row or column
  passes from a miss to a hit, bisected to 0.05 mm; graded by the distance
  from each edge point to the other outline's nearest (95th percentile);
* the **profile landmarks** (side view): glabella, nasion, nose tip,
  subnasale, both lips and the mouth line, the fold under the lip and the
  chin, each the most forward or most set-back point of the profile in its
  window, its height the centre of the stretch within 0.5 mm of that extreme.
  A test grades those its truth has well defined (inside the window, standing
  out by 0.4 mm): nine for the portrait, three for the stylised head, one for
  the anime head, whose lips and chin are smooth in profile;
* the **eyes**: each eye object's centre;
* the **chin's bottom and the jaw line** (front view): going down from below
  the lips, where the surface turns more than 70 degrees away from the viewer
  or jumps back to the neck, found again 0.05 mm apart around the first find;
* the **mouth corners** (front view): where the mouth line's groove fades to
  a quarter of its depth at the middle;
* the **relief** (front view): per region (each eye, the nose, the mouth, the
  chin, each cheek, the forehead), how far each head's visible surface lies
  from the other's surface, both ways (the side's points from the truth's
  distance fields, the truth's points from a BVH of the side's meshes),
  90th percentile;
* the **hair** (front, side, back and top): a pixel shows hair where a hair
  mesh is hit first or a strand passes nearer than whatever is hit; strands
  are read from hair curves objects (their own Geometry Nodes included),
  from hair particle systems with their children, on any object, and from
  the splines of curve objects (the brief asks for strands, not a data type),
  sampled every 0.4 mm, and read as the mass they make: gaps up to 6 mm
  closed, strays thinner than 4 mm dropped. So the same style reads alike in
  10,000 strands or in 16,000, and one loose strand sets no edge (read strand
  by strand instead, the same short crop in 3,500 strands reads its hairline
  4 mm off and in 1,750 its overlap 0.76; ``face_variants``). Graded: the hair's overlap with the truth's and the
  outline of head and hair together per view, the hairline over the forehead
  column by column, and the lowest hair at the back and at the sides.
  ``hair_strands`` asks for at least the ten thousand strands the brief states
  (a short crop's hairline is its roots: in a few thousand strands it is
  genuinely thinner than the picture's), rooted within 3 mm of the scalp and
  never inside the head; two outline checks at 0.5 mm ask that the head itself
  is left alone.

Every check's details give each feature's error in millimetres and in pixels
of the reference picture, so a failed check says how far off each feature
was (``head_checks``, ``face_compare``). ``face_report.py`` turns a run's
saved checks (the stock-Blender pass on each side's saved scene) into one
table of every feature's error for both sides.

Limits: like every scenario grader, these read the viewport evaluation, so a
Multires head whose viewport level is set below the level it was sculpted at
reads at that coarser level (both sides alike; the brief asks for no level).
A truth's readings are computed once, by sphere tracing, and then read from
``<references root>/face-truth/``.

**The graders' own uncertainty** (``face_variants.py``, stock Blender;
``--calibrate OUT.json`` writes every reading). The same head or hair built
other ways must pass every check, and what it reads is the noise floor: the
ground truth meshed with 1.6 mm and 0.8 mm cells, triangulated, voxel-remeshed
by a modifier, a coarse mesh under a Subdivision Surface, the hair as a
particle system or as legacy curve splines. Builds with one known defect must fail exactly the checks for
it, reading close to the change that was made: the nose tip 4 mm forward, the
eyes 3 mm higher, the jaw 4 mm wider, the mouth 5 mm wider, the head 3 %
larger, the hairline 6 mm lower, the hair 2 cm shorter, no hair, hair as solid
tubes. The measured floor and calibration are in `Grader uncertainty`_.

Grader uncertainty
------------------

Measured with ``face_variants.py --calibrate`` in stock Blender 5.2.0
(``face_report.py --calibration`` prints the tables; the readings are ray
casts of seeded, deterministic builds). Worst
reading over six other builds of each head that must read as the truth
(meshed with 1.6 and 0.8 mm cells, triangulated, voxel-remeshed by a
modifier, a 2 mm mesh under a Subdivision Surface, the build itself); every
check's tolerance is 2 mm:

======================================  ===========  ===========  ===========
Feature                                 Portrait     Stylised     Anime
======================================  ===========  ===========  ===========
Profile landmarks (worst)               0.27 mm      0.16 mm      0.32 mm
Outline front / side, 95th percentile   0.26 / 0.16  0.34 / 0.13  0.16 / 0.09
Eyes                                    0.00         0.00         0.00
Chin bottom / jaw line (mean)           0.10 / 0.11  0.05 / 0.17  0.15 / 0.11
Mouth corners / middle                  0.56 / 0.50  0.90 / 0.50  1.03 / 0.25
Relief, worst region (90th percentile)  0.17         0.16         0.12
======================================  ===========  ===========  ===========

The mouth corners are the least certain reading (the anime head's mouth is
19 mm wide); every other reading is under 0.35 mm. Known defects read what
the changed design itself reads from its distance field: the nose tip moved
4 mm reads 3.25 mm on the portrait (the design 3.26; its blends move the
visible tip less than the primitive) and 3.99 (4.00) on the stylised head;
the jaw 4 mm wider reads a front outline of 2.56 mm (2.56); the mouth 25 %
wider reads 5.9 to 6.0 mm at its corners (5.94); the eyes 3 mm higher read
3.00 mm. Relief measures shape, so a feature moved whole along the face (the
eyes) reads 1.2 to 1.6 mm of relief; each feature has its own check.

Hair, worst reading over the truth and the same style in 10,000 strands (the
brief's least): overlap at least 0.98 per view (checked at 0.85), outline 95th
percentile 1.41 mm (checked at 4 mm), hairline 1.39 mm (checked at 3 mm) and
length 1 mm (checked at 10 mm). Particle hair with interpolated children on a
moved and turned head reads every strand, each starting within 0.11 mm of the
head's surface in world space. The hair 2 cm shorter reads 20 mm, the hairline
6 mm lower reads 6 mm, and hair as solid mesh tubes fails the strands check.
Every build of the five tests reads as expected (``face_variants.py``: 21 of 21
for the two stylised and realistic heads, 22 of 22 for the anime head and the
two hairstyles).

Long candidates
---------------

Large multi-part scenes written to take one chat 20 minutes or more, to
measure whether parallel edit saves time on a long job
(``scenario_specs.ONESHOT_LONG``; neither is in the parallel set or any group,
and each runs alone as ``--task-set oneshot-<id>``):

* ``oneshot-apartment``: a furnished apartment of six rooms off one hallway,
  about forty distinct pieces plus their dressing. Stand-in
  ``long_kit_apartment.py``.
* ``oneshot-high-street``: eight distinct buildings on a street, a square with
  a fountain, trees, benches and a kiosk, the street's furniture and two cars.
  Stand-in ``long_kit_street.py``.
* ``oneshot-town-centre``: the high street enlarged after its pilot finished
  in 6 minutes: two side streets crossing it, 23 distinct buildings, a church
  with a tower, a covered market hall with stalls, a square, a park with a
  bandstand, street furniture and five vehicles. Stand-in ``long_kit_town.py``;
  its brief's building sentences are written from the stand-in's table, so the
  brief, the stand-in and the checks share every number. Its probe proof runs
  through ``long_probe_check.py``, which takes any long candidate.
* ``oneshot-grand-apartment``: a super-detailed apartment of twelve rooms, an
  entrance hall and a balcony with about 240 pieces, each its own design with
  its construction and materials described (no two chairs, tables or lamps
  alike). One table (``long_kit_flat_north.py``, ``long_kit_flat_south.py``)
  writes the brief's piece phrases, builds the stand-in (``long_kit_flat.py``)
  and derives the checks. Besides positions and seat or top heights, its
  checks measure distinctness and detail: each key piece's own footprint and
  overall height, open space under seats and tops (a level ray between the
  legs meets nothing of the piece), knobs and pulls standing proud of drawer
  and door fronts (its box reaches past the stated depth), frames and mirrors
  on their
  walls, hollow basins and baths, windows in the walls and colour families. A
  hard top's height is its own box's top, so nothing standing on it can hide
  it; a seat's or bed's is a ray that also counts the bedding or throw the
  brief lays on it. No check counts pieces of mesh, so a model that fuses its
  legs or knobs into one object is measured the same as one that does not.
* ``oneshot-tempest``: a ship in a bottle on a period desk in a dark study, at
  real size: the study (walls, wainscot, window, curtains, a bookcase of
  atlases), a pedestal desk with its instruments and two lanterns, a carved
  walnut stand with cradles and an engraved plaque, and the bottle with its
  cork, rope, knot and hanging compass and lantern; inside it a stormy sea,
  volumetric storm clouds, a moon, a fully rigged frigate (planked hull, gun
  ports and cannons, three masts, nine sails, two jibs, standing and running
  rigging, a helm) with a gun deck, a captain's cabin and a cargo hold, and a
  blue whale under the surface. One table of numbers
  (``long_kit_tempest_values.py``) writes the brief
  (``long_kit_tempest_brief.py``), builds the stand-in (``long_kit_tempest.py``,
  ``long_kit_tempest_ship.py``) and sets the checks, and a test proves the brief
  states every number a check measures. Rays find a piece by its own name and
  pass through everything else, so the glass, the clouds and the sea never
  block a ray aimed at what is inside or under them. Its parts are millimetres
  across, so each probe's margin is 5 cm or a fifth of its target's smallest
  stated extent across the ray, whichever is smaller (``probe_margin``), proved
  on the stand-in by ``long_probe_scaled.py``; box tolerances are 5% of the
  piece's largest stated size, 4 mm to 10 cm, at most 0.3 times its smallest.
  Floors and tops with things standing on them are measured by their own box's
  top, never by a ray that a coincident surface could skip.

Both share ``long_kits.py`` and, like the parallel tests, ask for no final
still. Their checks are the closed kinds every scene test uses: rays down at
each stated piece's place for its seat, top or roof height, rays along the
hallway for open doorways and walls, and boxes for each room's or building's
named parts and each named prop. Every ray is aimed at a face with margin,
never along a symmetry axis, through a pole or on an edge; a tip or top only a
ray on a pole could meet is a box's height instead. ``long_probe_margins.py``
proves it on each stand-in after ``scenario_references.py``: every probe moved
5 cm sideways in eight directions still passes, recorded per probe under
``probe_margins`` in the folder's ``selfcheck.json``. Calibration pilots run
with ``--task-timeout 7200`` (the candidates' ``LIMITS["wall_s"]``); a pilot
that reaches it counts as at least 7,200 s, never a measured time.

Mini tests
----------

The smallest useful one-shot tests: one short everyday request on Blender's
factory scene, no reference image, a few closed checks, expected to take
well under two minutes. They probe how the agent goes about ordinary work
(a typed tool or a script for each change, how much it looks up first, what
it costs, how the turn ends); the path metrics are the point, the checks
only say whether the request was met.

==================================  =========================================================  ======
Task set                            The whole message                                          Checks
==================================  =========================================================  ======
``oneshot-mini-footbridge``         Add a stone footbridge at the origin: a deck 4 m long and  5
                                    1.2 m wide, four square posts and two rails, in a grey
                                    stone material.
``oneshot-mini-footbridge-arched``  The same request with "a slightly arched stone             5
                                    footbridge".
``oneshot-mini-cube``               Bevel the cube's edges by 2 cm with 3 segments, add a      5
                                    Subdivision modifier at level 2, and give it a
                                    brushed-copper material.
``oneshot-mini-camera``             Add a camera at (6, -8, 4) aimed at the cube with a 50 mm  4
                                    lens, make it the scene camera, then delete the old
                                    camera.
``oneshot-mini-lighting``           Light the cube with a key, a fill and a rim area light,    5
                                    set the world to a physical sky with the sun 5 degrees
                                    up, and switch to Cycles at 128 samples with denoising.
==================================  =========================================================  ======

What differs from the scene tests:

* **The factory scene is kept.** A mini's message does not ask for the
  default scene to be cleared, so no check demands it. The cube test
  requires the cube to be still there (its message is about that cube); the
  camera and lighting tests do not, since their messages never ask to keep
  it; the footbridge tests look past an
  untouched default cube and check neither its staying nor its going.
* **Nothing is named**, so checks find things by shape and type
  (``oneshot_mini_checks.py``): the plan of what was built, a top surface of
  the stated size that is flat or rises towards mid-span, connected pieces
  shaped like square posts and like rails, a bevel read from a modifier or
  from the geometry, a modifier's values, a light rig read from the lights
  (three area lights aimed at the cube, one behind it as the scene camera
  sees it, the key stronger than the fill). Behind means beyond the cube in
  depth and, in plan, more than about 107 degrees round from the camera's
  bearing: a raised rim counts until it is above the cube more than behind
  it, a fill out to the side does not (``oneshot_mini_checks.behind``; until
  2026-10-21 a 3D angle that failed rims raised past about 46 degrees). What
  was built is everything
  except the factory cube exactly as it was; a cube reshaped into the deck
  counts.
* **No still is asked for**, so there is no saved-still check and no pixel
  check. The harness still renders the result from the factory camera's
  position for people to look at.

``--task-set oneshot-minis`` runs all five in turn with one command, each as
its own one-message run in ``<output>/<task set>/``, and writes one report
over them (``ONESHOT-GROUP.md``, ``oneshot-group.json``). A group run stops at
the first member that does not end cleanly, so a broken sign-in does not send
one message per member.

One trial is a sample of one: the same test on the same model can take
another route, and another time, on the next trial. The group report therefore
opens with one row
per test and side over its trials (``--trials``): how many passed, then the
median with the least and the most in brackets for the seconds, the output
tokens, the typed share of mutations and the lookups before the first
change, the cost, and how the turns ended. A number a trial did not record
is left out of the median and counted beside it, never taken as zero. Below
that, every trial keeps its own row: verdict, checks, how the turn ended,
typed against script calls, each mutation in order, lookups, output tokens,
seconds and cost. A single test run with several trials gets the same
over-the-trials table in its own report.

References and their sources
----------------------------

References are never downloaded and never part of this repository. They are
made, with no provider, before the first message and kept with the scenario
references (``~/.angelo-bench/scenarios/references/oneshot_<id>/<digest>/``).
``manifest.json`` says where every image came from, with its SHA-256:

* ``render``: rendered in stock Blender from the test's own ground truth
  (``scenario_oneshot_<id>.py``), which the reference job first self-checks:
  the brief's checks must fail on the factory scene and pass on the ground
  truth, or no reference is written;
* ``crop``: cut from a source picture a test names by file name and SHA-256
  (``SOURCE_IMAGES``). The picture is looked for in
  ``~/.angelo-bench/oneshot/sources`` (or ``ANGELO_ONESHOT_SOURCES``) and
  refused unless its hash matches; the manifest records the pixel box and the
  enlargement. Source pictures are not part of this repository and are never
  downloaded from a public place: the tree and the cottage read one source
  picture, named in their ``SOURCE_IMAGES``, which must be copied into that
  folder once on every machine that makes their references.

To make a test's references on their own (no provider)::

    python3 tests/python/angelo_agent/scenario_references.py --scenario oneshot_footbridge

Rendered references are not bit-reproducible: two EEVEE renders of the same
ground truth differ in a few pixels (crops are reproducible byte for byte). A
reference folder is therefore made once per digest and kept, and ``--force``
must not be used on a test that has recorded runs: later runs would be given
different image bytes of the same scene. The digest covers the test's module, ``oneshot_job.py``,
``oneshot_images.py``, the shared builder sources and what the test's own
ground truth reads (``oneshot_kit.py`` for the tree and the cottage), so
editing any of them re-renders the references of every test that reads it;
``test_native_oneshot_registry.py`` pins the digests of ``oneshot-bridge``
and ``oneshot-footbridge``, so their reference folders stay those of their
recorded runs. A test
that attaches only crops (the tree, the cottage) is given the same bytes
whatever its digest. Every report states the digest and each image's
SHA-256.

The brief decides no picture, so a brief-only edit can keep a folder:
``scenario_specs.KEPT_DIGESTS`` records the folder's digest with the digest
of the same sources without the module's ``PROMPT`` statement, and the kept
digest stands only while that second digest still matches; any other edit
makes new references. The footbridge's and the bridge's briefs
(`The footbridge's two briefs`_, `The bridge's two briefs`_) keep their
reference folders this way. A test that is another test's scene with
another brief reads that test's folder and digest (``SHARED_REFERENCES``:
``oneshot-footbridge-conflict`` and ``oneshot-bridge-conflict`` read their
tests' folders), and the folder is made by that test.

Grading
=======

A side **passes** only when all of these hold; everything else is a fail
with its reasons listed:

* it ended its one turn by itself (a stopped turn is graded and reported,
  never passed);
* every closed check of the brief passed, read from the scene by the driver
  after the turn;
* the still the brief asks for exists: found in the side's own folders
  (Angelo: the chat's workspace and the trial's temporary folders; MCP side:
  stock Blender's working and temporary folders and the client's working
  folder), where the scene's render path points, or at a path the side
  itself stated in its reply or scripts; written after the message was
  sent, so a file of an earlier run never counts; at least the stated size.
  The file judged is the one the agent names as its final render ("Which
  file is the final still", below);
* every pixel check passed.

A check tests what its brief states, no more and no less, the same on every
side, and judges the scene as built: no rule forgives a gap, seam or hole a
build leaves (no sampling of several rays for one check, no slack for a
piece that hovers). Tolerances absorb measurement error and a brief's own
"about" only.

Names are read as the briefs state them. Where a brief says "name them
exactly as written here and give anything else you add a meaningful name of
its own", a quoted name ('Cottage Walls', 'Lantern', 'Bridge Deck') is that
object and Blender's numbered copies of it ('Cottage Walls.001'); a part
with any other name (a mortar core, a roof underlay, a door frame, a child
object) is something else, so a check of the walls never counts it. Where a
brief says names "start with" a word ('Cottage Window', 'Castle Wall'),
any such name counts; the castle's prefixes must end a word ("'Castle'
followed by a word of its own"). No probe sits where its brief allows
something else: a wall-face probe counts a window or the door set in the
wall, a roof-slope probe a roof window; the castle's round-tower and
enclosure rays count windows, its gate towers skip bearings into the
gatehouse block their stated centres overlap, its battlements are counted
on the outer half of the wall, and its gateway ray covers the passage only.
"No unused leftovers" allows none (counted as a save writes them). The
tree's trunk is measured on 'Tree Trunk' alone, and its "clear of branches
and leaves for the first 1.8 m" by rays that must miss every 'Tree Branch'
and 'Tree Foliage'; the footbridge's beams must clear the stated water
level, each handrail is a railing piece at its stated height (posts may rise
above it), posts are counted per side, and the walkway rays cover the
stated deck; the bridge counts eight rocks a side and finds its deck, arch,
piers, abutments and river by their exact names.

Surfaces a brief states as solid are also checked for see-through gaps
(``opaque``, ``oneshot_opacity.py``): a grid of parallel rays crosses each
stated area and every ray must meet a part the brief allows to close it
(any part of the cottage or castle, so windows, doors and a core count).
The cottage's walls and roof; the bridge's deck slab, solid arch ring and
solid valley block; the castle's curtain wall ("no gap anywhere") and the
keep's and towers' roofs above their eaves. Not checked: the footbridge deck
(its brief asks for gaps between planks), the minis (the deck's ``slab``
check already requires every ray to meet it), the tree. No ray may cross,
so a build whose parts close the stated area passes and one that leaves a
joint open fails. The check runs after the turn like every other and is
never shown to the model.

Which file is the final still
-----------------------------

Angelo's jobs never overwrite: a render to a taken name writes the next
free one (``tree-final-j3.png``), and the job's receipt and the agent's reply
name that file. The grader therefore judges, on every side and runtime, the
file the agent names as its final output (``final_render.py``), decided in
this order:

#. **reply**: the turn's final reply (whole, not the 1,500 characters a
   record's ``result`` keeps) names a file of this run that answers to the
   requested name; when it names several, the one written last;
#. **job receipt**: the last render the side ran for the requested name
   wrote another file. Angelo's receipt says which; on the Blender MCP side a
   render for a name writes that name, because stock Blender overwrites;
#. **requested path**: nothing names another file, so the file of the
   requested name is judged.

A file answers to the requested name when it is that name, or, only when a
file of that name is beside it (the name was taken), the requested stem with
a suffix and the same extension. A reply naming a file the brief did not ask
for (``render.png``, or ``tree-final-2.png`` with no ``tree-final.png``
beside it) names nothing, so the rule never accepts a different name than
the brief asked for. The output check's ``details["final"]`` records the
file, the source that decided it, the names the reply gave, and whether it
is the newest file of this run that answers to the requested name (when it
is not, which one is). The report lists that for each side under its Result
table (``final_still_judged`` in ``oneshot-scorecard.json``). The agent's
own-still pixel checks measure the judged file. No check compares that still
with the saved scene, so a named file that is not the newest is recorded,
not failed.

The rule reads only what a trial records (its reply, receipts and file
list) and the files themselves, so a recorded trial can be judged by it
again. The scan for paths a side stated runs in linear time, so a client
stream that holds base64 images is read whole without slowing the grade.

A closed check the grader cannot decide is **inconclusive**: what decides it
is something the grader cannot read (a colour an image texture chooses, a
mesh too large to count). It is never passed, and it is listed apart from a
measured fail (``check inconclusive: ...``); a side whose only reasons are
such checks gets the verdict ``inconclusive``, which no total counts as a
pass. It is a third verdict beside passed and failed in every table the
harness writes: the Result table's closed checks read passed / failed /
inconclusive of all, the line under it gives every side's passed, failed and
inconclusive trials, and the trials tables of a test and of a group have a
column for each.

When the still is missing, the report says which of two different failures
it was. Every run records the image files it wrote after the message was
sent, whatever their names, with their pixel sizes (``oneshot_produced.py``),
and on Angelo the render jobs it started (id, kind, status, the output name
asked for and the one written).
"Why a side did not pass" then reads either "is missing, but the side did
render" with those files and jobs (a review still under another name, a job
whose result it never saw), or "is missing and the side rendered nothing".
Where the add-on marks it, each job also says whether its picture was shown
to the agent, its result was only told, or it was never shown.
Files are looked for in the folders only that side writes to, where the
scene's render path points, and at any path the side named in its reply,
scripts or job requests. A folder other programs write to as well (the
system's temporary folder, the home folder) is never walked, so another
program's picture is never taken for the side's work; the harness's own
renders, the attached references and its copy of the still are left out.

Closed checks are the scenario kinds (``scenario_benchmarks.rst``) plus four
for briefs that leave the design open (``oneshot_checks.py``):

* ``surface``: like ``material``, for materials a model designs itself:
  colour, roughness and metallic as the node graph computes them over the
  faces that show the material ("How material colours are read").
* ``visible``: the first thing a ray meets is the named object.
* ``detail``: evaluated vertices of the object's own meshes plus the
  instances it makes.
* ``hygiene``: no object of the default scene, no default-named object, a
  material on every mesh that renders, and at most a stated number of unused
  data-blocks (the factory file's own are not counted), counted as a save
  writes them (``scenario_hygiene.saved_orphans``). Data nothing uses is
  dropped by a save and never counts, so a side that leaves it alone, as
  Angelo's commits tell the model it may, is not failed for it; data a save
  writes and a reopened file shows unused (a material whose only user was the
  mesh of a deleted object) still counts. The count was checked against a
  real save and reopen in a fresh Blender 5.2. Every scene test with this
  check (bridge, footbridge, tree, cottage, castle) counts this way; the mini
  tests have no such check. ``"unused_counted": "session"`` counts the
  session's zero-user data instead.

and four for a form the brief leaves to the model
(``oneshot_form_checks.py``):

* ``pieces``: the connected mesh pieces of the matching objects' own meshes
  plus the instances they make. A few smooth blobs do not count as foliage,
  however many vertices they have.
* ``ring``: rays run upwards side by side on a cylinder around a vertical
  axis. A limb that spreads away from the axis has to cross it, at whatever
  height it leaves: neighbouring rays that meet a matching object are one
  limb, a gap separates two. With ``core``, separate pieces of the wood (mesh
  pieces and instances) that start within ``core`` of the axis and reach past
  the cylinder are counted too, and the larger count holds: the rays merge
  limbs that cross at overlapping angles, the pieces merge limbs built as one
  mesh with the trunk, and neither counts one limb twice.
* ``groups``: mesh pieces whose bounds touch are one group, so a window is
  its frame, bars and glass together whether that is one object or twenty.
* ``light_from``: the side a light shines from, as a bearing in the ground
  plane.

A mesh too large to count fails as inconclusive rather than passing unread.

How the sky is read
-------------------

The ``world`` check grades what the world shows a camera, never which nodes
exist (``world_background.py``, ``scenario_checks.world_facts``):

* Only the World Output the renderer uses counts: one targeting the scene's
  engine before one for all engines, the active one first. A node counts
  only when that output's Surface reaches it through links the renderer
  follows; a muted link, a link into a hidden socket and a muted node's
  other inputs are cut. A Sky texture that is not linked shows nothing and is
  not a sky.
* A Sky texture the output reaches is the sky, judged by its sun's elevation.
* Otherwise the graph is evaluated per direction, as a camera ray meeting the
  background sees it: Background and Emission colour times strength, Mix and
  Add shaders, Light Path as a camera ray, and the coordinate, math,
  map-range, mix, Color Ramp (by its stops' positions), colour and Gradient
  texture nodes. Texture Coordinate's Camera and Window outputs use the
  scene's camera, in the directions it sees. Nothing linked to the Surface is
  black; the viewport colour never stands in for the graph.
* Two bands are sampled, each five 3-degree rings of eight directions: the
  lowest 15 degrees above the horizon (what a level camera sees behind the
  scene) and the top 15 overhead. A band shows a colour family when at least
  half its directions show it. ``sky_or_color`` and ``color`` read the
  horizon; ``zenith_color`` reads overhead. A brief's colour stays what it
  was: the bridge's golden sky still needs a warm horizon, so flat blue fails
  there.
* Noise, Voronoi, Brick and Checker textures are computed in each direction
  as Cycles computes them, the direction being their Generated coordinate
  ("How material colours are read"). What the grader cannot evaluate (an
  image, a Wave or Magic texture, a variant the ports do not cover) is
  unknown, and a Color Ramp or Mix after it shows the colours it can show
  for any value; a band the unknown decides leaves the check inconclusive,
  naming the node.

The evaluation was checked against renders, and showed no difference in
colour family on any pixel: Cycles and EEVEE agree with it on every
coordinate output; Cycles panoramas (3 degrees a pixel) of 244 worlds built
in one-shot trials that have no Sky texture in their colour path match it
within 0.05 % (Cycles' Color Ramp table); world-only renders through the
scene camera match the camera-relative skies within 1.3 % in both engines.
``test_native_world_background.py`` pins the evaluator on stand-in graphs
with the sockets and defaults of Blender 5.2's nodes.

How material colours are read
-----------------------------

The ``surface`` and ``material`` checks, and the castle's roof colour, read a
material the same way (``surface_reading.py``, on the shader evaluator
``shader_graph.py`` the sky uses):

* Only the BSDF the active Material Output's Surface reaches counts.
* Base colour, roughness, metallic and emission colour are evaluated over the
  faces that show the material: once when the input depends on nothing per
  point, otherwise at 1,024 points spread over those faces in proportion to
  their area (stratified over the triangles' cumulative area, each point
  placed inside its triangle by a low-discrepancy sequence; 256 on the few
  faces a castle ray met). At each point the data is interpolated as Cycles
  interpolates it: a Color Attribute or Attribute node (a colour, float or
  vector on points, corners or faces, from the evaluated mesh), the Geometry
  node's Position, Normal and True Normal, and Texture Coordinate's Object,
  Generated (the mesh's texture space), UV (the active render map) and Normal.
  Ramps are read by their stops' positions, mixes by their factors, node
  groups through their outputs.
* Noise (3D fBM, with detail, roughness, lacunarity, distortion and
  normalisation), Voronoi (3D F1, F2 and distance to edge, every distance
  metric, with detail) and Brick and Checker textures are computed at every
  point as Cycles computes them (``texture_kernels.py``: numpy ports of
  Blender 5.2's SVM kernels in single precision, hashes included), inside
  node groups too. A Brick texture's mortar therefore counts by the share of
  the surface its joints cover, and a ramp after a Noise texture by how much
  of the surface the noise puts on each side of its stops.
* What the grader cannot evaluate (an image, a Wave or Magic texture, a 1D,
  2D or 4D Noise or Voronoi, another fractal type, a texture mapping set on
  the texture node itself) is unknown; no range is assumed for it. A node
  that bounds its output turns an unknown input into the set of values it
  can produce: a Color Ramp shows one of its colours, a Mix with a clamped
  factor lies between its inputs, a clamped Map Range, Math or Clamp lies in
  its range.
* A predicate holds when at least half the surface surely satisfies it for
  every value the unknown parts can take, fails when it surely cannot, and
  is inconclusive otherwise, naming the unknown node. A ramp of browns after
  a Wave texture is brown whatever the wave does; a ramp from brown to green
  after an image is inconclusive, because the image decides how much is
  green.

The ports were checked against Cycles renders. Every texture configuration
the scenes built in one-shot trials use (264: 3D fBM Noise, 3D F1 and
distance-to-edge Voronoi, Brick; every output they read, every unlinked
input value) was rendered as a world panorama under two coordinate mappings
and compared at every pixel, 9.7 million samples: Noise within 7.8e-5,
Voronoi exactly, Brick within 2.5e-3 (31 pixels above 1e-3, all on a joint's
edge, where single-precision rounding moves it). Thirty-three configurations
no recorded scene uses (Noise distortion and no normalisation, F2 and every
distance metric, fractal Voronoi, brick offset and squash, Checker) agree
within 2.4e-4. The values a texture reads were checked at Cycles' own
surface points (its Position pass) on scenes built in one-shot trials,
154,000 points: in
every object 99 % of the positions, normals, UV and Generated coordinates
agree within 4.1e-4, and attributes within 8e-6. The exception is one trunk
with overlapping faces, where the validation's own nearest-face lookup lands
on another sheet for up to 7 % of its points. End to end, on every check
whose verdict depends on these readings, Cycles' render of the material's base colour and the
grader's reading at the same 239,000 surface points agree within 1.4e-3 at
99 % of them, no point is judged differently by the brief's colour test, and
the share of the surface that passes it is the same.

The reference job proves the checks on one build, the test's own stand-in.
``oneshot_variants.py`` grades more of them in stock Blender, with no
provider: each
variant starts from the stand-in and either makes one thing another way (it
must still pass every check) or breaks one thing the brief asks for (it
must fail exactly the checks for it)::

    <stock Blender> --background --factory-startup \
      --python tests/python/angelo_agent/oneshot_variants.py -- oneshot_tree oneshot_cottage

For the tree: leaves as instances of one leaf on points, as 1,600 objects
and as one mesh of 60,000 cards; limbs as bevelled curves; the fork as part
of the trunk object; and, broken, smooth blobs for foliage and three limbs.
For the cottage: walls as one solid; all window frames in one object and
all glass in another; a lantern in named parts with shutters, a sill, a door
frame and a doorstep; and, broken, the picture's slate roof, no lantern,
three windows and a door buried in the wall. Textured variants colour the
roof with a Brick texture and the walls with a noise mix of two greys (both
read from the graph), the roof with a Voronoi-driven mix and with a mix
chosen through its UV map (both measured; the UV one passes only when the
measured points carry the UVs), and, broken, a blue Voronoi roof that is
measured and fails the terracotta check. Door variants also cover a wide
doorstep and decorative overlays; a leaf of planks with a seam on its centre
line fails placement and visibility (the ray passes the seam). Negative variants remove the door, widen its leaf, move
it off centre or to the back, reduce it to two edge strips, or obscure most
of it with stone. Run it before a test with new checks is given to a model,
and again when its checks change: a check can wrongly fail a valid
construction (leaves placed as instances, whose placing object has no faces
and no material), and only another build shows it.

The pixel checks use two stills. After the session
``oneshot_job.py --job final`` opens the side's saved scene in stock Blender
and renders it from the brief's own camera position with the scene's own
engine, samples, lights, world and colour management; only what keeps the job
bounded is overridden and recorded (the longest side, 1,920 pixels; a Cycles
time limit of 600 s; the file format). That still and the one the agent
saved itself are measured: the mean and spread of the luminance, the share of
near-black pixels, and a small thumbnail compared with renders of the factory
scene from its own camera and from the brief's. Each must be not empty, not
black and not the default scene. A still that cannot be read fails; nothing
is assumed.

Both stills, the references and the fixed-view review renders are copied
into ``report-assets/`` and shown side by side in ``oneshot-report.html`` for
a person to judge. The report has an "Opinion" section reserved for a
model-judged comparison; none is implemented, and if one is added it stays
out of every total.

A check that already holds on the factory scene cannot show that the turn
did anything; the reference job lists them (``passing_before`` in
``selfcheck.json``) and requires the brief's checks to fail there as a whole.

Path metrics
============

Recorded per test and per side, each from a record and never from the
model's own account (``oneshot_metrics.py``): Angelo's request receipt and
chat rows, Claude Code's stream, Codex's notifications.

* messages typed (must be 1);
* the stop reason: ``finished``, ``time_cap``, ``cost_cap``, ``round_cap``,
  ``refusal``, ``approval_declined``, ``error``, ``provider_limit``,
  ``runtime_exit``, ``setup_error`` or ``unknown``, with the recorded detail;
* model requests and rounds;
* tool calls by tool;
* mutations by typed tools against by scripts (calls, and the typed share),
  each mutation in order as "typed <tool> (n operations)" or "script
  (categories)", with each script's host-derived category and, on Angelo,
  the model's own description of it;
* lookups before the first change to the scene, by tool; on Angelo also the
  capability lookups, lookup rounds and rejected plans of the turn;
* approvals asked, approved and declined, the seconds waited, and each tool
  the harness's approval policy declined (shown apart, as the harness's rule
  and not a product failure);
* errors, and for each whether the side did something that succeeded
  afterwards in the same turn (recovered) or not (it ended the turn);
* whether the side looked at an image of its own result, in which view mode,
  whether the pixels reached the model, whether it looked after its last
  edit, whether it rendered, and whether it saw its final render;
* why each script was written, from the request's own steps, and how many
  scripts raised (Angelo, where the add-on records them);
* time to first visible activity and wall time. On the MCP side the first
  activity is Claude Code's first streamed text or tool call; on Angelo it is
  whichever came first of the first reply text, the first native action, the
  first tool call's row in the transcript (a Codex ``exec`` lookup) and the
  moment the chat's live row first named the work (a typed plan's
  provisional steps or names, or a reasoning summary's title). The live-eval
  session reads the last two from what the chat draws, every 20 ms from the
  send (``eval_receipts.ProgressWatch``: ``first_step_ms`` and
  ``first_progress_ms``, with ``first_plan_ms`` when a plan began,
  ``first_rows_ms`` when its first step was drawn and ``first_thinking_ms``
  when a thinking row first held summary text), and the report prints the
  live row's own moment in its own row ("Live row first named the work"). A
  trial recorded without these watches counts only the reply and the first
  action, so its first activity can be later than what the person saw (a
  typed plan's live row can name its first object well before the reply's
  first text);
* output tokens, all tokens and the cost: API-equivalent at the list prices of
  ``model_profiles.py`` (``cost_list_usd``) and the runtime's own estimate
  (``cost_reported_usd``); the report prints one of them for every side, the
  comparison's ``cost_basis`` (``final_runs.rst``, "What is recorded"). Each
  cache write is priced at the TTL the provider reported for it (Claude's
  ``usage.cache_creation``; on a subscription Claude Code writes 1-hour
  entries, as Angelo's interactive sessions there do, and on an API-key or
  team route both write 5-minute ones), read from the side's raw stream when
  its record predates the split. Writes without a reported TTL are priced at
  Angelo's session TTL (the one its receipt's route records, which the cost
  cap asks the chat for; its receipts carry no split) or at the 5-minute rate
  for Claude Code, and ``cost_list_basis`` says which (``final_runs.rst``,
  "What is recorded"). A price step is per request: the MCP side's stream gives each
  request's usage; Angelo's receipt sums them, so a model with a step whose
  sum exceeds it has an unknown list price. The cost cap prices the usage it
  watches the same way.

The report prints them as one table per test with the sides next to each
other (``oneshot_tables.py``), with the image files each side wrote and the
render jobs it started, and for Angelo one line: one message, error-ended
turns, the share of mutations by typed tools, checks passed. With several
trials it adds one row per side over them: passed of trials, then the
median, least and most.

A number no record gives is printed as "unknown", never guessed, and each
report lists only what its own run left unknown. One harness grades several
add-on versions, so every such fact is read when the add-on under test
records it and stays unknown when it predates the record:

* **Pictures that reached the model**, and whether the side looked at its
  result after its last edit: from the receipt's ``path`` section
  (``images``: per provider turn, the edits done by then and the pictures by
  kind; the person's own attachments are counted apart). Unknown on an
  add-on whose receipts have no ``path``. Delivery has three states:
  confirmed, unknown and none. Pictures sent to a runtime whose protocol
  cannot prove delivery (``unconfirmed_images``, Codex code-mode results)
  are reported as sent with delivery unknown, never as none, and so are
  "looked after its last edit" and "saw its final render" when only such a
  picture could make them true; a proven attachment says nothing about the
  agent's own pictures. On the MCP side the same question
  is answered from the order of the client's own calls, counting only a call
  whose own result put a picture in the model's context (a screenshot that
  failed or a read of a missing file is no look); the MCP side saw its final
  render only when a read of that file returned the picture.
* **Whether Angelo's agent saw the still it rendered**: from the ``shown``
  mark on the job's record (when its result was told to the agent, and when
  its picture was shown). Without the mark only this is known: a still
  rendered as a job was not seen inside the turn.
* **Why a script was used instead of a typed tool**: from the receipt's
  ``path`` steps, in order: a script after a typed call that was refused or
  failed is a fallback from it, one with nothing typed before it went
  straight to a script. This is the host's record, never the model's own
  account; the report also gives the scripts that raised. Unknown without
  ``path``.
* **Model requests** on a Codex connection: from the receipt's usage when it
  counts them, else from the token-usage updates seen while the turn ran.
  Model rounds inside a Codex turn stay unknown: Codex does not report them.
* **The usage so far, for the cost cap**: asked of the add-on's telemetry
  when it offers a reader for a request in progress, else read from the
  request's receipt in progress.
* The viewport shading of a screenshot on the MCP side stays unknown: its
  screenshot tools do not report it (the shading modes its scripts set are
  listed instead).

Limits
======

======================  =============  =====================  ==================
Task set                Turn time cap  Wait for a render job  Cost cap, list USD
======================  =============  =====================  ==================
``oneshot-bridge``      3,600 s        900 s                  50
``oneshot-footbridge``  2,400 s        900 s                  25
``oneshot-tree``        2,400 s        900 s                  25
``oneshot-cottage``     3,000 s        900 s                  30
``oneshot-castle``      3,600 s        900 s                  40
``oneshot-head-*``      2,400 s        600 s                  20
                        (portrait
                        3,000 s)                              (portrait 25)
``oneshot-hair-*``      3,000 s        600 s                  25
each mini test          300 s          none                   3
======================  =============  =====================  ==================

* The time cap is the task timeout: at the cap the driver presses Stop (or
  interrupts the client), grades what is there and records ``time_cap``.
  ``--task-timeout`` overrides it. For one-shot task sets the harness accepts
  up to 21,600 s; every other task set keeps its 1,800 s limit.
* On Angelo a render started as a background job may finish within the job
  wait after the turn; the scene is graded when it has ended.
* The cost cap prices the usage seen so far at list prices: on Angelo once a
  second from the add-on's own reading of the request in progress (on an
  add-on without one: the receipt in progress, per model round on Claude
  connections, or the running thread total on Codex connections); on the
  MCP side from the client's stream, per model response. Past the cap the
  side is stopped and ``cost_cap`` is recorded. ``--cost-cap`` overrides it.
  A model without a list price cannot be capped by cost; its cost is
  recorded as unknown. On Angelo the running usage is summed over the
  request's model calls, so for a model with a price step whose sum exceeds
  it the cap prices every token at the higher rates: an upper bound, kept
  apart as ``cap_cost_usd`` and marked ``bound`` on the stop, so the turn
  may stop early but never runs uncapped.

Files
=====

All under ``tests/python/angelo_agent/``.

``scenario_oneshot_<id>.py``
   One test: the brief, its references, checks, limits, fixed cameras and
   ground truth. A scenario module with exactly one turn.
``oneshot_minis.py``, ``scenario_oneshot_mini_<name>.py``
   The mini tests: what they share (limits, views, stand-in builders) and
   one small module each.
``oneshot_mini_checks.py``
   The check kinds of the mini tests (things found by shape, not by name).
``oneshot_group.py``
   One command for a set of tests (``oneshot-minis``) and its table.
``scenario_specs.py``
   The registry (``ONESHOT_IDS``), limits and the reference digest.
``oneshot_checks.py``
   The added check kinds, the still's grading and the pixel checks.
``oneshot_form_checks.py``
   The kinds that count separate pieces, limbs and openings, and the side a
   light comes from.
``oneshot_kit.py``
   Stand-in builders shared by tests: a broadleaf tree and a stone cottage.
``oneshot_variants.py``
   Other ways to build a test's scene, and builds that break one thing,
   graded by the test's own checks in stock Blender.
``oneshot_images.py``, ``oneshot_job.py``
   Image statistics and crops; the background jobs (references, captures,
   the final still).
``oneshot_references.py``
   References and their manifest.
``oneshot_session.py``
   Angelo's driver: one message, approvals, the cost cap, the still, the
   chat rows as facts.
``oneshot_mcp.py``
   The MCP side: the client's allowances, one message, the cost cap, the
   still.
``oneshot_metrics.py``
   Path metrics. Usage and list prices (``list_prices.py``) are re-exported
   from it.
``oneshot_tables.py``
   Their tables: the sides of one test next to each other, and several
   trials of a side as one row (pass count, median, least and most).
``oneshot_produced.py``
   The image files a run wrote and the render jobs it started: what tells
   "rendered something else" from "rendered nothing" when the still is
   missing.
``oneshot_report.py``
   The verdict, ``oneshot-scorecard.json``, ``ONESHOT-REPORT.md`` and
   ``oneshot-report.html``.
``oneshot_fake_models.py``
   The dry run's scripted models.
``scenario_oneshot_head_*.py``, ``scenario_oneshot_hair_*.py``, ``face_tests.py``
   The face and hair tests and what they share: cameras, checks, the ground
   truth's scene and the start file.
``head_sdf.py``, ``head_designs.py``, ``head_kit.py``, ``hair_kit.py``
   The heads as signed distance fields and their mesher, the four designs,
   their Blender objects, and the hairstyles' strands.
``face_casters.py``, ``face_measure.py``, ``face_truth.py``, ``face_compare.py``, ``head_checks.py``
   How a head and its hair are read (ray casts of the scene, sphere tracing
   of the truth), the readings, the truth's readings and their cache, the
   errors, and the check kinds.
``face_variants.py``, ``face_report.py``
   The graders' noise floor and calibration (stock Blender), and the
   per-feature error table of a run or of a calibration (host).
``test_native_oneshot_*.py``
   Host tests (no Blender): the registry and briefs (``registry``), the
   wiring into the launchers and drivers (``wiring``), the one-message rule,
   approvals, caps and the still (``session``), the check kinds, pixel
   checks and image statistics (``checks``), the path metrics from recorded
   receipts and streams (``metrics``), the report and its pixel checks
   (``report``), the dry run's scripted models (``dry_run``), the mini
   tests with their check kinds and group command (``minis``), several
   trials at a glance with what a run wrote when its still is missing
   (``trials``), the tree and cottage briefs, their stand-ins and the
   counting kinds (``scenes``), the variant job's table (``variants``),
   what an add-on's own records add to the path metrics (``receipt``) and
   the footbridge's and the bridge's two briefs with the folder each pair
   shares (``footbridge_naming``, ``bridge_endpost``).

Commands
========

From the repository root, host Python with NumPy; ``export
GIT_LFS_SKIP_SMUDGE=1`` first. One command runs a test unattended. On macOS
the stock Blender, the Blender MCP server and its add-on default to their
installed locations; elsewhere give ``--stock-blender``, ``--mcp-server-dir``
and ``--mcp-addon-dir``.

The plan, without starting anything::

    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --task-set oneshot-footbridge --plan-only

Dry run
-------

No account and no provider: every part of a real run is real except the
model (``scenario_benchmarks.rst``, "Dry run"). Each scripted model builds the
ground truth, renders the still, looks at its own result (Claude Code opens
the PNG it rendered with its Read tool; Codex takes a screenshot through the
MCP server; Angelo's scripted runtime asks for a view through the scene
camera) and replies, all within the one message. It starts GUI processes::

    nice -n 10 python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py \
      --task-set oneshot-footbridge --dry-run-scenario --trials 1 \
      --angelo-binary <Angelo executable> \
      --output <new folder>

``dry-run.json`` lists every check; the exit code is 0 only when all pass:
the scenario dry run's own five per side, and per side that exactly one
message was typed, that the side passed, that its still was rendered and
found, that it looked at an image of its result, that its path metrics were
recorded, on Angelo that approvals were counted and timed, and on Claude
Code that the client could read the image it rendered.

All five mini tests with one command (a mini asks for no still, so its
scripted models only make the change and reply; each member has its own
``dry-run.json``)::

    nice -n 10 python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py \
      --task-set oneshot-minis --dry-run-scenario --trials 1 \
      --angelo-binary <Angelo executable> \
      --output <new folder>

Running a test
--------------

Each trial is one message per side, sent to the model on the chosen
connection's account, so the bench refuses to start without
``--authorize-billed-runs`` and an explicit ``--connection-id``. References
are made, with no provider, before the first message::

    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --authorize-billed-runs \
      --task-set oneshot-footbridge --trials 1 \
      --connection-id <connection id> --source-connections-dir <connection store> \
      --angelo-binary <Angelo executable> \
      --angelo-observation turn-context --angelo-always-allow-scripts \
      --model <model id> --effort medium --output <new folder>

``--angelo-always-allow-scripts`` is the product's own setting a comparison
uses, so the reports say "scripts allowed"; leave it out only to measure the
reviewed-scripts path, which the reports then name.

``--runtime codex`` runs Codex on both sides and needs ``--codex-home``, a
dedicated signed-in Codex profile used by both sides. One side only: add
``--sides angelo`` (or ``--sides mcp``). Another test: change ``--task-set``
and the output folder. ``--task-set oneshot-minis`` runs the five mini tests
in turn (five messages per side); one mini alone is, for example,
``--task-set oneshot-mini-cube``. ``--summarize-only <folder>`` with
``--task-set oneshot-minis`` rebuilds every member's report and the group's
table.

Outputs, next to the bench's usual ``SUMMARY.md`` and
``bench-mcp-vs-angelo.json``: ``oneshot-scorecard.json`` (verdict, checks,
pixel statistics and path metrics per side), ``ONESHOT-REPORT.md`` and
``oneshot-report.html``. ``--summarize-only <folder>`` rebuilds them from the
raw trial folders without sending anything.

Pinned run worktrees
--------------------

The Angelo side imports the checkout's add-on, not the app's: the live-eval
session puts ``<checkout>/scripts/addons_core`` first on ``sys.path`` inside the
app. A run from a checkout that changes while it runs (new commits, a merge,
edited files) therefore changes host code under its trials, while the app and
the model-facing text stay the same. So:

* **Measured runs come from pinned, detached run worktrees** that nothing
  changes during a run, one per arm, checked out at exactly the commit its
  app was built from (``GIT_LFS_SKIP_SMUDGE=1`` keeps the checkout free of
  LFS downloads)::

      GIT_LFS_SKIP_SMUDGE=1 git worktree add --detach .worktrees/run-<build> <commit the app was built from>
      GIT_LFS_SKIP_SMUDGE=1 git -C .worktrees/run-<build> checkout --detach <next build's commit>   # a new build

  Development happens in other checkouts. A run worktree moves only to a new
  build's commit, and only between runs.
* **The bench refuses a mismatch.** Before anything runs, and each
  ``eval_live_agent.py`` run before its first trial and again before each
  trial (``bench_addon_pin``), the checkout's add-on and startup modules are
  compared with the app's bundled ones (``native_addon_guard``, the native
  suites' check). A mismatch is refused, naming both folders, their digests
  and the differing files; a checkout that changes after the run began ends
  the run before its next trial. ``--allow-addon-mismatch`` runs anyway, for a
  deliberate case, and prints a warning first.
* **Every trial records what it ran**: the Angelo trial's ``addon`` (the
  imported add-on's digest, the app's bundled digest, whether they match, the
  commit and whether the add-on files had uncommitted changes), in its
  ``eval-live-agent.json`` trial, its launch record and the bench's
  ``trial_meta``; an MCP trial's ``mcp-trial-NN-source.json`` (the same stamp
  of the checkout that ran it).

Warm-up before a measured run
-----------------------------

Every measured test starts with a warm prompt cache on both sides, warmed
the same way for each side. ``--warm-up-only``, with the run's own flags and
``--output``, sends one short, untimed, ungraded message ("Reply with OK.")
per selected side through that side's trial launch and exits:

* MCP: the trial's Claude Code command with the run's MCP server, client
  profile and working directory (whose path the client's system prompt
  names); stock Blender is not started, since the client lists the server's
  tools without it and the message asks for none;
* Angelo: the trial's live-eval command with ``--warm-up-only``; the app boots
  and configures the chat as for a trial, sends the message, declines
  anything that would change the scene and quits without grading.

It writes ``warm-up/<side>-NN/`` and ``warm-up/WARM-UP-NN.json`` (usage and list
price), never a ``*-trial-NN`` folder or the run's plan, so trial counting,
``--resume``, the summaries, scorecards and result tools ignore it, and the next
``--resume`` call adds trial 1. On Codex the MCP side's warm-up runs the
trial's ``codex app-server`` launch with an ephemeral thread (stock Codex keys
its cache by thread, so it warms only what threads share), and Angelo's runs the
trial's own chat launch, so its ``cacheAffinityKey`` opening is the trial's.
The dry run, which no provider bills, takes the flag (``--dry-run-scenario --warm-up-only``) and checks that the
warm-up's system and tools are the trial's first request's on the MCP side and
its Claude launch the trial's on Angelo's (``eval_warm_up``, ``bench_warm_up``,
``test_native_bench_warm_up.py``)::

    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py <the run's flags> \
      --output <the run's folder> --warm-up-only

Adding a one-shot test
======================

#. Write ``scenario_oneshot_<id>.py`` like a scenario module with one turn
   (``id`` ``brief``), plus ``LIMITS``, ``FINAL_STILL``, ``FINAL_VIEW`` (the
   fixed camera the brief's still is taken from), ``GROUND_TRUTH``
   (``reference`` when the references are renders of it, ``stand-in`` when it
   only proves the checks), ``APPROVABLE`` and ``JOB_WAIT_S``. The turn names
   its ``outputs`` (the still) and the ``job`` a perfect agent would start.
#. Write the brief as one brief (`The contract`_). Quote every name a check
   looks for; derive check values from the module's constants.
#. Prefer checks that hold however the scene is built, and that cannot be
   failed by chance (no ray through a plank gap).
#. Add the id at the end of ``scenario_specs.ONESHOT_IDS`` (tests pin the
   order of the first entries; the files its ground truth reads go to
   ``ONESHOT_SOURCES``) and the module to
   ``native_suite_manifest.HELPERS``.
#. Make the references: the self-check must pass. Look at every image.
#. Add variants of the scene to ``oneshot_variants.py`` (other ways to make
   what the brief leaves open, and builds that break one thing) and run it.
#. Run the dry run, then the host tests.

Limits of the format
====================

* One trial per side is a sample of one: it shows where a side struggles,
  not a rate.
* A brief gives names, sizes and positions so that checks can be closed;
  what it leaves open (the look) is judged by people from the report.
* Closed checks ask for what any acceptable build must have. A build can
  pass them and still be a poor picture, and an unusual construction can
  fail one (a welded railing has no separate posts to count).
* The typed share counts calls, not the work inside them.
* Whether Angelo's agent saw an image is known only from an add-on whose
  receipts record it (`Path metrics`_); the MCP side's stream shows it.
* Costs are API-equivalent estimates at list prices, not what a provider
  bills.
