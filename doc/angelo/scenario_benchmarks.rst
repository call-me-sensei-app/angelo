.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Scenario Benchmarks
*******************

A scenario benchmark is one longer build, scripted as several chat turns, run
on Angelo and on stock Blender with the Blender MCP server, with the same model
on both sides. Every turn touches a different feature area, is graded by closed
host checks, and both sides work from the same reference images. At the end
each side's scene is scored against the ground truth from fixed cameras, and a
report puts the renders side by side for human review.

The scenario sets are task sets of ``bench_mcp_vs_angelo.py``
(``--task-set scenario-<id>``) and of ``eval_live_agent.py``. Three scenarios
exist: ``scenario-bridge``, built to reference images with exact sizes
(`The bridge`_), ``scenario-island``, an art-directed Cycles still with
loose checks and a render job at the end (`The island`_), and
``scenario-heavy``, five everyday edits in a dense castle scene that each
need geometry read (`The heavy scene`_). They are separate
from the default and long task sets, whose tasks, prompts, checks, approval
rule and launch commands they leave unchanged;
``test_native_scenario_registry.py`` checks that those sets carry no scenario
fields.

How a run works
===============

#. **References.** Before the first message, ``scenario_references.make``
   builds the ground truth in a background stock Blender, self-checks it and
   renders every fixed camera. Nothing is re-rendered while the ground truth's
   sources are unchanged. Every background job (references, start files,
   captures) runs with its home, temporary and Blender user folders in its own
   scratch folder (``scenario_references.isolated_environment``); on Windows
   that home is ``USERPROFILE`` with ``APPDATA`` and ``LOCALAPPDATA`` inside
   it, since Python ignores ``HOME`` there.
#. **Turns.** Each side gets the same prompts, one per turn, in one continuing
   chat, from Blender's factory scene. A turn may attach reference images; the
   bridge's first turn attaches the four overview views, later turns a
   close-up. Before each send, the files' SHA-256 hashes are checked against
   the reference manifest, and each runtime gets the same bytes:

   * Angelo: chat attachments, through the chat's own attach path (private
     snapshots of the same files);
   * Claude Code: base64 image blocks after the text;
   * Codex: ``localImage`` inputs after the text.

#. **Grading.** After each turn the driver, never the model, runs the turn's
   closed checks (``scenario_checks.evaluate``) and records a digest of the
   scene's authored state. In Angelo the checks run in the trial process; on
   the MCP side they run in stock Blender over the add-on's socket. A turn
   that was stopped or timed out is still graded and digested. A turn that
   started a background job (the island's final render) is graded when the
   job has ended, and the file it must have written counts with its checks
   (`Background jobs and output files`_).
#. **End of session.** Each side reports scene hygiene, saves a copy of the
   scene (no thumbnail) and then runs the Undo probe, which changes the scene,
   so it is always last.
#. **Scoring and report.** On the host, stock Blender opens each saved scene,
   re-runs every turn's checks on it, renders review images and writes the
   ray-cast capture used for scoring. ``scenario-scorecard.json``,
   ``SCENARIO-REPORT.md`` and ``scenario-report.html`` are written next to the
   bench's usual ``SUMMARY.md``. ``--summarize-only`` rebuilds them from the
   raw trial folders.

Files
=====

All under ``tests/python/angelo_agent/``.

``scenario_specs.py``
   The registry: task-set names, reference locations and manifest checks,
   the task list of a scenario, the reference digest.
``scenario_bridge.py``
   The bridge: prompts, attached views and checks per turn, fixed cameras,
   the subject, and ``build(bpy, upto)``, the ground truth.
``scenario_island.py``
   The island: the eight directions, their loose checks, the final render
   and its job, and a plain stand-in ground truth.
``scenario_build_kit.py``
   Data-API helpers for ground-truth builders (no operators, no randomness).
``scenario_checks.py``
   The closed check kinds and ``evaluate``.
``scenario_hygiene.py``
   State digest, hygiene report, scoring copy and Undo probe.
``scenario_capture.py``, ``scenario_job.py``
   Ray-cast captures and renders; the background reference and capture jobs.
``scenario_capture_split.py``, ``scenario_capture_worker.py``, ``scenario_capture_boot.py``
   The ray-cast capture split across background Blender processes, byte-identical to one process.
``scenario_references.py``
   Renders and caches references (also a command line).
``scenario_scoring.py``
   Silhouette and depth scores (host NumPy).
``scenario_scorecard.py``, ``scenario_report.py``
   Per-side metrics and the report files.
``scenario_session.py``
   The Angelo driver's scenario steps and approval rule.
``bench_mcp_scenario.py``
   The MCP side's scenario steps, shared by Claude Code and Codex.
``bench_mcp_replay.py``
   Replays a recorded Angelo session's typed messages (notes and attachments
   included) on the Blender MCP side; sends model messages, explicit only
   (`The island`_).
``scenario_heavy.py``, ``heavy_plan.py``, ``heavy_scene.py``, ``heavy_scene_blend.py``
   The heavy scene's turns, its seeded plan, its geometry (NumPy) and its
   Blender side: build, start file, append (`The heavy scene`_).
``heavy_checks.py``, ``heavy_variants.py``
   Its check kinds, and right and wrong results graded in stock Blender.
``scenario_start_job.py``
   The background job that makes a scenario's start file before its
   references.
``scenario_demo.py``, ``scenario_demo_job.py``
   The trial-window recording and its encoder (`Demo recording`_).
``scenario_fake_models.py``, ``scenario_dry_run.py``, ``scenario_dry_run_session.py``
   The dry run, which needs no provider.
``test_native_scenario_*.py``
   Host tests (no Blender): checks, output files, hygiene and Undo probe on
   stand-ins (``checks``), scores and ray layout (``scoring``), the registry,
   the bridge and island specs and the harness wiring (``registry``), the
   scorecard and report (``report``) and the recording's host side (``demo``).
   ``test_native_bench_mcp_replay.py`` covers the replay's script checks and
   refusals.

The bridge
==========

A stone arch bridge over a river valley at golden hour, nine turns:

==================  =========================================  ===============
Turn                Feature area                               References
==================  =========================================  ===============
``valley``          Height-field terrain, solid diorama block  front, side,
                                                               top, hero
``bridge``          Deck, open-spandrel arch ring, piers,
                    abutments, mirrored parts
``railings``        Posts and rails along both edges           railing
                                                               close-up
``lamps``           Lamp array, emissive lanterns
``water_rocks``     Glossy water plane, 16 scattered rocks     river close-up
``golden_hour``     Low warm Sun named ``Sun``, physical sky
``deck_camera``     Camera placed, aimed and made active
``car_animation``   A multi-part car, keyframed crossing,
                    frame range                                car close-up
``render_setup``    1920 x 1080, 24 fps, EEVEE, samples, ray
                    tracing, motion blur, AgX, render camera
==================  =========================================  ===============

The turns carry 46 checks. The ground truth's sky is calibrated so that the
Sky texture's sun sits where the Sun light shines from (Blender 5.2: rotation
0 puts the texture's sun at azimuth 90 degrees, so rotation = 90 - azimuth).
References and scores use frame 60, the car mid-bridge.

The island
==========

A realistic floating island at first light, as one hero still: eroded
sandstone with real displacement, scattered grass, wind-bent pines, a pond and
a waterfall into mist, a cloud sea, a low sun, and a final 3840 x 2160 Cycles
frame. Eight turns, each one stage direction as an art director would type
it. The prompts name every object the checks look for and give no method,
except that the first message agrees how to work: one focused script per
step, no renders inside a script, and no verification checks of the agent's
own.

==================  ==========================================  ======
Turn                Feature area                                Checks
==================  ==========================================  ======
``blockout``        Camera, island mass, pond and notch, pine   10
                    stand-ins, physical sky and Sun, AgX,
                    Cycles on the GPU
``rock_form``       Non-destructive form: bedding, joints,      4
                    buttresses, a hanging underside, shards,
                    boulders
``rock_material``   Procedural rock with true displacement and  2
                    adaptive subdivision
``vegetation``      Geometry-nodes grass scatter, three pines   5
                    with needle shoots, roots
``water``           Pond, waterfall, a mist volume, wet rock    4
``atmosphere``      Volume cloud deck, far and low decks,       4
                    haze, wisps, two islets, birds
``lighting``        First light, an island-only boost, final    4
                    size and sampling
``final_frame``     Depth of field, then the frame as a         1 + the
                    background job (``renders/island-final      file
                    .png``)
==================  ==========================================  ======

The checks are deliberately loose: they ask whether each ingredient exists in
a usable form (a displaced material, a volume, a scatter, a named light, a
file of the right size), never how it looks. Looks are judged by people from
the stage renders. The ground truth in ``scenario_island.py`` is therefore a
plain stand-in that satisfies the checks, not a picture to match, and the
references are only the self-check's proof that every check fails before its
turn and passes after it (34 checks). The turns attach no reference images.

The island runs like the bridge (`Running a scenario`_). One trial is eight
messages per side, and the last turn waits for a 4K render (up to
``JOB_WAIT_S``, three hours), so two options matter for it:
``--stage-copies`` keeps a copy of the scene after every turn for stage
renders, and ``--render-device gpu`` lets Angelo's trial render Cycles on the
machine's GPU (`Background jobs and output files`_). A turn that ends on an
error is recorded as failed and the session goes on with the next turn on
whatever the scene then holds: an unattended run does not relay errors the way
a person does.

Replaying a recorded session
----------------------------

``bench_mcp_replay.py`` sends the messages a person typed into an Angelo
session, word for word and in order (notes and attachments included), to the
Blender MCP side: the same launch, the same headless Claude Code command line,
the same isolation and the same usage accounting as the MCP side of
``bench_mcp_vs_angelo.py``. The messages are a ``messages.json`` list (the
format is in ``bench_mcp_replay.py``); the scene is copied after each stage,
and ``replay.json`` records per message the wall time, model requests, tool
calls, tool errors, tokens and Claude Code's own cost estimate. The run stops
when the cost passes ``--cap-usd`` or the provider reports a limit. A message
without a recorded cost is counted in ``cost_unknown_messages``, never added
as zero, so ``cost_usd`` and the cap check cover the messages that recorded
one::

    python3 tests/python/angelo_agent/bench_mcp_replay.py --authorize-billed-runs \
      --run <new folder> --script messages.json --model <model id> --effort medium \
      --connection-id <connection id> --source-connections-dir <connection store> \
      --mcp-addon-dir <a copy of the Blender MCP add-on>

A replay compares "the same messages on the same model", not equal
direction:

* messages written while watching one side's results (notes on its renders,
  with those renders attached) describe that side's scene, not the other's;
* a prompt written around one product's working style (for example the
  island's "one focused script per step") may suit that product better;
* the replay denies Claude Code's built-in tools (file reading and writing,
  the shell, the web), because a headless run has nobody to approve them; a
  Claude Code user at a terminal would have them;
* one session per side is a sample of one.

The heavy scene
===============

``scenario-heavy`` asks whether an agent's time holds in a production-size
scene. The scene is built from a fixed seed (``heavy_plan``, ``heavy_scene``):
1,969 objects, 1,247 meshes and 2,662,867 vertices (2,672,973 counting shared
meshes once per object): a 832 x 832 terrain with a summit and three
look-alike hills within 1.4 m of it, a castle with stone walls and towers
sampled every 10 to 15 cm (walls, towers and a statue carry vertex groups),
300 trees, 900 rocks, a village of 40 houses, 400 fence posts, 180 props, a
flag, a lone watchtower, and two dense carvings. Placement uses a small
integer generator, never NumPy's, and every coordinate is rounded to 0.1 mm
before it becomes float32, so the same arrays come out on any machine.

The start file. ``scenario_start_job.py`` builds the scene, saves it as
``start.blend`` in the scenario's reference folder (uncompressed, about
250 MB; SHA-256 in the manifest) and checks that appending it into a fresh
factory scene works; the reference job then begins from that file. Before
the first message, each side of a trial appends the same file into its own
scene (``setup``: factory objects removed, collections, world and camera
appended) after checking its SHA-256; the time it took is recorded under
``scenario.setup``, outside every turn's time.

==============  ==================================================  =================================
Turn            What the result needs read                          Checks
==============  ==================================================  =================================
``flag``        The highest of 692,224 terrain vertices             ``summit``: the flag's pole within
                                                                    3 m of it, its foot within 0.3 m
``trees``       The terrain's height under each of 300 trees (75    ``grounded``: every foot within
                float, 45 are sunk)                                 0.15 m of the surface below, none
                                                                    moved sideways
``heaviest``    Polygon counts across 1,247 meshes: the statue      ``reduced``: 35-65 % of its faces
                (706,560 faces) leads a carving and the terrain     or triangles, its shape and place
                by 0.4 % and 2.3 %                                  kept
``normals``     Which faces of five dense walls are wound inwards   ``outward``: every edge walked
                (three walls have a 3 x 3 m patch)                  once each way, positive volume,
                                                                    no vertex moved
``tower``       Nothing: a plain move, the control                  ``moved``: tower and roof 10 m +X
==============  ==================================================  =================================

Every turn also checks ``untouched``: every object no turn names is exactly
as the plan made it (transform, parent, materials, no modifiers, CRC-32s of
its positions and corners), which also holds at the end of the session, so a
fix that reaches too far (normals recalculated on the terrain, a nudged
rock, a deleted house) fails. The turns attach no images; the references are
the self-check's proof that each turn's checks fail before it and pass after
it (10 checks). ``heavy_variants.py`` grades 15 results in stock Blender: the
five right edits pass every check, and fourteen wrong ones (the flag on the
second-highest hill, floating or sunk; only the floating trees set down; a
tree moved sideways; a reduction by a tenth; the terrain halved instead;
every wall face turned inwards; a wall vertex moved; the terrain flipped too;
the roof left behind; a move along +Y; a nudged rock; a deleted house) fail
exactly the checks for them::

    Blender --background --factory-startup --python tests/python/angelo_agent/heavy_variants.py

What each side can use. The MCP side reads geometry through Blender's Python
API in model-written code. On Angelo only the control has a fully typed
route: a ``transform`` program or ``angelo_set_transform``. The others need
scene Python, because Angelo has no typed read for them; with
``--angelo-always-allow-scripts`` that Python runs without a review, as for a
person who chose Always allow scripts (`Approvals`_):

* ``flag``: no typed read returns a mesh's highest vertex and where it is
  (``angelo_inspect_sculpt`` gives world bounds, so the height but not the
  place; ``angelo_read_data`` pages 64 vertices a call);
* ``trees``: no typed ray cast or surface height, and no drop-to-surface
  operation; a Shrinkwrap constraint would declare the 692,224-vertex terrain
  as an input, which programs refuse above 100,000 vertices;
* ``heaviest``: scene search rows carry no face counts and cannot be sorted by
  them, and a Decimate through a program is refused for a mesh over 100,000
  vertices;
* ``normals``: ``mesh_recalculate_normals`` is typed and fits the walls, but no
  typed read reports inconsistent winding, so the fix is never observed.

Running it::

    python3 tests/python/angelo_agent/scenario_references.py --scenario heavy   # once per machine
    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --task-set scenario-heavy --dry-run-scenario --output DIR
    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --task-set scenario-heavy --authorize-billed-runs ...

Reference images
================

* JPEG, 1280 x 720, at the highest quality in ``JPEG_QUALITIES`` that keeps
  each file within ``REFERENCE_MAX_BYTES`` (500,000 bytes). Claude Code
  re-encodes any attached image over 512,000 bytes as a JPEG of its own;
  under the budget all three runtimes pass the file through unchanged, which
  the dry run verifies byte for byte on each side's recorded model requests.
* Kept in ``~/.angelo-bench/scenarios/references/<id>/<digest>/`` (or under
  ``ANGELO_SCENARIO_REFERENCES``) with ``manifest.json`` (SHA-256, size and
  media type per image), the ground truth's ``capture.npz``,
  ``ground-truth.blend`` and ``selfcheck.json``.
* ``<digest>`` covers the scenario module and the shared builder, job and
  capture sources, so a changed ground truth gets new references and a run
  can never mix versions. Line endings do not change it.
* The self-check fails a reference job, and no reference is written, unless
  each turn's checks fail before that turn and pass after it, and every
  check passes at the end.

To render them on their own (no provider; ``--force`` re-renders)::

    python3 tests/python/angelo_agent/scenario_references.py --scenario bridge

Closed checks
=============

Checks measure what a person sees, not how it was built: an Array modifier,
duplicates and Geometry Nodes instances pass alike. Objects are matched by the
names the prompts state (``{"name": "Valley"}``, which also matches
``Valley.001``, or ``{"prefix": "Lamp"}``). A box that measures a thing the
brief names uses ``{"whole": "Parked Van"}``: the object of that name, the
parts named with it as a word prefix (``Parked Van Cabin``, never ``Parked
Vans Sign``) and everything parented under them, so the whole van is measured
whether it is one mesh, a body with named parts or an Empty with children. A
box for something "N m tall" also bounds its bottom on the ground the brief
states, so a lone roof slab cannot pass. The kinds:

* ``object``, ``count``: presence, absence, type and number.
* ``bbox``: world bounds of the evaluated geometry, instances included.
* ``probe``: a ray that passes through other objects to the first hit of the
  target, with an expected axis range, or an expected miss (a gap between
  posts, a clear arch opening).
* ``parts``: connected mesh pieces inside a region, by size (the rocks).
* ``material``: base colour family, roughness, emission strength and colour,
  read from the BSDF or Emission node the material's output uses, as its
  node graph computes them over the object's faces (``oneshot_tests.rst``,
  "How material colours are read"). A predicate an unreadable texture
  decides is ``inconclusive``: never passed, reported apart from a fail.
* ``light``, ``world``, ``camera``: elevation, colour family and energy;
  a low-sun Sky texture the World Output uses, or the colour family the sky
  shows near the horizon (``zenith_color``: overhead), evaluated per
  direction from the graph (``oneshot_tests.rst``, "How the sky is read");
  location, aim and lens.
* ``motion``: world bounds of the target at given frames; the current frame
  is restored.
* ``scene``: values of scene and render settings.
* ``armature``, ``gait``, ``keyframed``: for rigged and animated scenarios.

The island adds kinds for what a look-development build must have, whatever
it looks like:

* ``geometry``: the evaluated vertex and face count of the object's own mesh
  (its instances are not counted): real geometric detail, however it was made.
* ``modifier``: a modifier of one of the given types that renders.
* ``displacement``: a material whose output has Displacement linked and whose
  method displaces geometry (``DISPLACEMENT`` or ``BOTH``, not bump only).
* ``instances``: evaluated instances made by the named object (Geometry
  Nodes, particles or a collection instance), counted up to five million.
* ``volume``: the named object is a volume, evaluates to one (Geometry
  Nodes), or has a material, authored or set by its node tree, with a Volume
  shader; ``{"world": True}`` asks the World for one.
* ``camera`` also takes ``dof``, ``fstop``, ``focus_distance`` (to the focus
  object when there is one) and ``sensor``.

Checks only read the scene (motion checks move the frame and restore it). A
check that raises fails on its own without stopping the others. Evaluated
counts are the viewport's, like everything a check reads: a build that shows
fewer instances in the viewport than in the render is counted as shown.

Background jobs and output files
================================

A turn may name files the session must have produced (``outputs`` in the
turn: a name, and for an image the least size) and, for the dry run, the
background job a perfect agent starts (``job``). They are facts about the
session's folders, not the scene:

* **Approval.** A scenario lists the extra tools its tester approves in
  ``APPROVABLE``; only ``angelo_job_start`` may be added that way
  (``scenario_session.SCENARIO_EXTRAS``). File writes, deletes, generation
  and the rest stay declined.
* **Waiting.** ``angelo_job_start`` returns at once and the render runs as
  Blender's own job (``long_jobs.rst``). While it runs it holds its own
  resolution, samples and output path on the scene, and scene edits are
  refused. So Angelo's driver waits for the job (at most the scenario's
  ``JOB_WAIT_S``; the launchers add it to their process timeouts) before it
  grades the turn, digests the scene, saves the copy and runs the Undo probe.
  A recording keeps running, one grab every five seconds instead of two a
  second.
* **Grading.** ``scenario_checks.evaluate_outputs`` looks for each file by
  name (not in hidden folders, so never in a job's staging) and reads an
  image's size from its PNG, JPEG or OpenEXR header. The file judged is the
  one the agent names as final: the turn's final reply, then the last
  render's job receipt, then the requested name (``final_render.py``;
  ``oneshot_tests.rst``, "Which file is the final still"), and the result
  records which and why. Angelo's driver looks in
  the chat's workspace, where a job publishes; the MCP driver in its
  Blender's working and temporary folders, since that side has no workspace.
  The results are appended to the turn's checks and the turn passes only
  with both. The record also keeps the session's jobs (kind, status,
  seconds, the output name asked for and the one written).
* **Not part of the saved scene.** The end-of-session pass on the saved
  scene and the reference self-check run the scene checks only, so a turn
  with an output also needs a scene check that its own work satisfies (the
  island's last turn turns on depth of field).

``--stage-copies`` (scenario sets, Angelo side) keeps a copy of the scene
after every turn in ``trial-01/qa/stages/NN-<turn>.blend``, without a
thumbnail and without touching the open file, for stage renders.
``--render-device gpu`` (scenario sets, Angelo side) enables the machine's
GPU for Cycles in the trial's preferences, as a person with a GPU has it;
the scene still chooses its device. The factory preference is CPU, on which
a 4K Cycles frame with volumes takes hours. Its devices are selected as on
the GPU test machines (``bench_gpu``, ``final_runs.rst``), and a machine that
lists no GPU fails the trial instead of rendering on the CPU. On those
machines every side already gets the GPU preference, with every scene on
device GPU.

Per-side metrics
================

``scenario-scorecard.json`` holds, per side and trial:

* turns passed (closed checks), failed, inconclusive, ungraded and not
  attempted, completed and timed out, and wall time per turn and in total. A
  turn no check graded is ``ungraded``, and a turn whose only unmet checks
  could not be decided is ``inconclusive``; each is shown as such in the
  report and never counted as passed (``native_coverage.rst``, "Grading
  contract");
* model calls (MCP: API calls; Angelo: typed-plan rounds or, for Codex,
  turn requests), tool calls, tokens, the runtime's cost estimate (Claude
  Code's; Codex reports none) and the list price, compared on one basis for
  every side (``cost_basis``, ``final_runs.rst``). A total is unknown when a
  turn that ran did not record its value, never a partial sum, and so is a
  turn's wall time; a skipped turn adds nothing (``bench_totals.py``);
* Python: the MCP side's ``execute_blender_code`` calls; Angelo's reviewed
  Python requests and how many were approved;
* approvals requested, approved and declined (the MCP side has no gate);
* errors: MCP tool errors, API retries, permission denials; Angelo receipt
  failures and error turns;
* the Undo probe: Undo steps taken back to the start, turn boundaries a
  person can return to, and whether one Undo goes back exactly one turn;
* hygiene: objects the prompts never asked for, default names, duplicate
  materials, empty material slots, negative scales, a non-Object mode
  (counted as issues) and the data-blocks the session left without users,
  shown apart (Blender drops them on save; the factory file's own are the
  baseline);
* end-of-session compliance: every turn's checks re-run on the saved scene,
  as passed / failed / inconclusive of all;
* the view scores below.

Scoring method
==============

Scores compare geometry, not pixels of a render, so neither renderer noise
nor colour management enters them. For every fixed camera
(``scenario_bridge.CAMERAS``: front, side, top and hero, and the three
close-ups) ``scenario_capture.capture`` casts one ray per pixel
(384 x 216) through the evaluated scene with ``Scene.ray_cast``, modifiers
and instances included, at the scoring frame and with render visibility.
The ground truth and each side go through the same function in the same stock
Blender.

``Scene.ray_cast`` tests every object for every ray, so one process takes
rays x objects time; a 10,000-part scene needed over an hour. The capture
jobs therefore run through ``scenario_capture_boot.py``
(``scenario_references.run_job``). It splits the unchanged ``capture()``
across background Blender processes (``scenario_capture_split``): the job
saves a copy of the prepared scene, and each worker claims row bands from a
queue and casts exactly the rays one process would. The job reassembles the
bands and numbers the hit names as ``capture()`` does, so ``capture.npz`` is
byte-identical (``capture_split_proof.py`` compares the two on a saved scene).
The worker count is ``os.cpu_count()`` minus two, the same for every side,
scenario and reference. None of the reference-digest sources change, so
reference folders are kept. A capture that fails is not relaunched in the same
report run, which keeps its ``capture.log``; a one-shot scene is always
captured with ``oneshot_job.py``.

Per view, ``scenario_scoring.score_view`` reports:

* ``scene_iou``: intersection over union of the pixels where any geometry was
  hit (the silhouette against the sky);
* ``subject_iou``: the same for the scenario's subject only (for the bridge:
  objects named ``Bridge*``, ``Railing*``, ``Lamp*`` and ``Car*`` and their
  children), so a correct terrain cannot hide a wrong bridge;
* ``depth_rel_median`` and ``depth_rel_mean``: the relative planar depth
  error over pixels both hit;
* ``depth_agreement``: the share of pixels hit in either capture where both
  hit and the depths agree within 5 %, one number for shape and placement.

Means weight every view equally. The report shows, per view, the reference,
each side's render and a silhouette overlap (white both, orange reference
only, blue that side only). The ground truth scored against itself gives 1.0
on every view; that is the dry run's acceptance floor (0.99).

Approvals
=========

Angelo's chat keeps the product's default approval, Auto bounded edits:
typed bounded edits (programs, recipes, preview Accept/Discard, sculpting)
run without asking, as for a person (``--angelo-approval ask`` on
``bench_mcp_vs_angelo.py``, ``--approval ask`` on ``eval_live_agent.py``,
puts the chat in Ask).
What still asks is answered by the scenario's tester: the harness's typed
allowlist and, with ``--python-approvals approve`` (the default for scenario
sets), reviewed Python as well, as an artist building a scene would; every
approval is counted. The island's tester also approves ``angelo_job_start``
(`Background jobs and output files`_). Reviewed Python takes two clicks in
the app (the first shows the script, the second runs it) and the tester does
the same. File writes and deletes, generation, delegation, memory notes and
workspace changes are declined, and in Ask so are preview acceptance and
sculpting. ``--python-approvals decline`` compares the default rule;
``--angelo-always-allow-scripts`` (``--always-allow-scripts`` on
``eval_live_agent.py``) runs scripts unasked, as a person who chose Always
allow scripts. The Blender MCP side runs model-written Python without an
approval step.

Demo recording
==============

``--demo-record`` (scenario sets only) records Angelo's trial window as a
video:

* The trial window opens at 1440 x 810 (16:9) and switches to Director Mode
  (the 3D viewport and the chat with the trial's conversation); the first
  message waits for the layout.
* The viewport looks from the scenario's demo view (``DEMO_VIEWS``) in
  Material Preview without overlays, and uses the scene's own lights and
  world once the scene has a Sun.
* Every 0.5 s a timer grabs the window with ``screen.screenshot``, which reads
  Blender's own framebuffer: no screen-recording permission, and other windows
  covering it do not matter. After the last turn it keeps recording until the
  viewport has compiled its shaders, then stops before the end-of-session
  steps.
* On the host the scorecard encodes the frames in stock Blender's own FFmpeg:
  ``angelo-trial-NN/demo/<id>-angelo-1080p.mp4`` (H.264, yuv420p, 1920 x 1080,
  30 fps, letterboxed if the window is not 16:9; every grab is one video
  frame) and ``poster-1080p.png`` (the last frame, the finished scene). The
  HTML report embeds both.
* Each grab redraws the window on the main thread, so a recorded run's Angelo
  timings are not comparable with unrecorded runs; the plan and report say
  so. Record a separate run when the timings matter.

A recording made with ``eval_live_agent.py --demo-record`` alone is encoded
by calling ``scenario_demo.encode(<trial>/qa/demo, <out folder>, "<id>")``.

Dry run
=======

No account and no network service: every part of a real run is real except
the model. The MCP side runs the real Claude Code CLI against a local
Anthropic Messages endpoint (``scenario_fake_models.FakeMessages``) and the
real ``codex app-server`` against the local Responses endpoint of
``codex_prompt_probe``; Angelo runs the unchanged trial driver behind a
scripted runtime. Each fake model plays a perfect agent that applies the
ground truth turn by turn. The dry run then builds the bench's own summary and
the scenario report exactly as after a real run. It passes only when, on
every side, every turn passed, the reference bytes reached the model input
unchanged, the end-of-session copy scored at least 0.99 against the ground
truth, every check passed again on the saved scene, the Undo probe and hygiene
report ran, and, with ``--demo-record``, the demo video and poster were
encoded.

From the repository root (it starts GUI processes, so run it on an otherwise
idle machine)::

    export GIT_LFS_SKIP_SMUDGE=1
    nice -n 10 python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py \
      --task-set scenario-bridge --dry-run-scenario --demo-record \
      --angelo-binary <Angelo executable> \
      --output <new folder>

The host Python needs NumPy. ``--mcp-addon-dir`` may point at any copy of the
Blender MCP add-on; the bench copies it into each trial. ``dry-run.json``
lists every check; the exit code is 0 only when all pass.

Running a scenario
==================

``--plan-only`` prints the plan without sending anything. References are
rendered, with no provider, before the first message. Every prompt of a real
run is sent to the model on the chosen connection's account, so the bench
refuses to start one without ``--authorize-billed-runs`` and an explicit
``--connection-id``. On macOS the stock Blender, the Blender MCP server and
its add-on default to their installed locations; elsewhere give
``--stock-blender``, ``--mcp-server-dir`` and ``--mcp-addon-dir``::

    export GIT_LFS_SKIP_SMUDGE=1
    python3 tests/python/angelo_agent/bench_mcp_vs_angelo.py --authorize-billed-runs \
      --task-set scenario-bridge --trials 1 --task-timeout 600 \
      --connection-id <connection id> --source-connections-dir <connection store> \
      --angelo-binary <Angelo executable> \
      --angelo-observation turn-context --model <model id> --effort low \
      --output <new folder>

* ``--runtime codex`` runs Codex on both sides (``codex app-server`` on the
  MCP side, a Codex connection on Angelo's) and needs ``--codex-home``, a
  dedicated signed-in Codex profile used by both sides.
* ``--sides angelo`` or ``--sides mcp`` runs one side (``both``, the
  default, runs both).
* ``--task-timeout`` must cover the longest turn; the bridge's valley and
  bridge turns write long scripts or plans, and the island's turns take
  longer still (``--task-timeout 900``).
* The outputs are the bench's usual ``SUMMARY.md`` and
  ``bench-mcp-vs-angelo.json`` plus the scenario files above (`How a run
  works`_).

Adding a scenario
=================

#. Write ``scenario_<id>.py`` with ``ID``, ``TITLE``, ``FRAME``, ``TURNS``
   (``id``, ``title``, ``prompt``, ``references`` (at most four views) and
   ``checks`` per turn), ``CAMERAS`` (at least front, side, top and hero with
   role ``reference``, plus close-ups with role ``closeup``), ``SUBJECT``,
   ``build_turn(bpy, turn_id)`` and ``build(bpy, upto)``. Build only with the
   data API and ``scenario_build_kit``: the builder also runs over the MCP
   socket and as reviewed Python in the dry run.
#. Name every object the checks target in the prompt, in quotes. Derive check
   values from the module's constants, not from the prompt text.
#. Add the id to ``scenario_specs.SCENARIO_IDS`` and, for the recording, a
   view to ``scenario_demo.DEMO_VIEWS`` (else the hero camera is used).
#. Render the references: the self-check must pass. A check that already
   passes before its turn, or fails after it, stops the job and names it in
   ``selfcheck.json``.
#. For a file the session must produce, add ``outputs`` (and ``job`` for the
   dry run) to the turn, ``APPROVABLE`` and ``JOB_WAIT_S`` to the module, and
   a scene check the same turn satisfies (`Background jobs and output
   files`_).
#. Look at every reference image, run the dry run, then extend
   ``test_native_scenario_registry.py`` with anything the new scenario relies
   on.

Limits
======

* One trial per side is a sample of one; it shows where each side
  struggles, not a rate.
* The island's checks cannot tell a good picture from a poor one.
* A check sees the viewport's evaluation: the island's grass check asks for
  800 instances because the prompt itself asks for fewer in the viewport
  than at render.
* Prompts give names, sizes and coordinates so checks can be closed; vaguer
  requests may favour either side.
* Scores measure geometry from fixed views; materials and lighting are judged
  by the checks and by people looking at the report.
* Claude Code, Codex and Angelo send images at the runtime's own detail; the
  bytes are identical, the model's view of them follows each runtime's
  defaults.
* Stock Blender (MCP side) and the Angelo build are different binaries; the
  references, captures and scores all use stock Blender.
