.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
..
.. SPDX-License-Identifier: GPL-2.0-or-later

Background Jobs
===============

Renders, texture bakes, physics and simulation bakes and USD/Alembic exports
can take minutes or hours, and how long is not known in advance. They never run
inside an agent turn, a staged program, a commit or a reviewed Python script.
The agent starts them with ``angelo_job_start``; they run as Blender's own
window-manager jobs (the same jobs as F12, Bake or File > Export), with
progress and a Cancel button in the chat and the status bar, and their result
reaches the agent in a later message. Long or unbounded work gets this job
contract, never a typed program with a time bound.

Module ownership
----------------

============================  ===================================================================
Module                        Responsibility
============================  ===================================================================
``job_spec.py``               The two tool schemas, kinds, per-kind fields and bounds. Pure.
``job_store.py``              Records, ``jobs.json``, startup reconciliation and the model-facing
                              entries of the jobs block. Pure.
``job_manager.py``            One job at a time: queue, progress, ETA, time budget, cancel,
                              file-close interruption. Assigns job IDs. Pure.
``job_outputs.py``            Output admission, anchored hidden staging held from creation to
                              publication, all-or-nothing set publication. Pure.
``job_output_names.py``       The names an output is published under: its own, else the next
                              free one with the job's ID. Pure.
``job_settings.py``           Scene settings a job changes for itself, recorded on the scene and
                              restored (also after a save or a crash).
``job_runners.py``            The runner base, renders and exports on Blender's jobs.
``job_render_outputs.py``     Every file a render writes, named before it starts (per view,
                              per File Output item), and the check of what was written. Pure.
``job_render_scene.py``       A scene's views and File Output nodes read into that plan, and
                              the settings that send each file into the job's staging.
``job_render_samples.py``     Effective per-layer sample counts (Cycles policies, EEVEE layer
                              counts) and the layer settings a job holds. Pure.
``job_animated_settings.py``  Scene animation of a job's own settings, muted for the job, and
                              the greatest value of an inherited animated setting. Pure.
``render_reach.py``           Compositor File Output nodes and the other scenes a Sequencer's
                              scene strips render. Pure.
``render_extensions.py``      Add-on or script Python a render or bake would run (engine,
                              handlers, drivers), and the approval bound to it.
``render_drivers.py``         Scripted drivers whose own Python a render would run: on what
                              the scene reaches, beyond arithmetic, not blocked by Blender's
                              restricted mode.
``job_bakes.py``              Texture, physics and simulation bake runners.
``job_bake_caches.py``        A simulation bake cached on disk: its folder in the job's
                              output, the settings that send it there, and the path from the
                              .blend the scene reads it by afterwards.
``job_bake_targets.py``       A texture bake's images and objects: the materials' own bake
                              image or a named one, UDIM tiles, the checks Blender makes only
                              inside its job, and whether a bake that ended wrote the images.
``job_host.py``               The tools on ``BlenderExecutor`` (admission, execute) and the scene
                              guard while a job runs.
``job_ui.py``                 The shared manager in Blender: poll timer, file events, notices,
                              status-bar item, ``angelo.job_cancel`` / ``angelo.job_open``.
``job_chat.py``               The chat card: pure layout, states and translatable text.
``job_approvals.py``          Approval text from the host's admission facts.
``chat_cards.py``             Cards attached below an activity row (flow runs, jobs): height,
                              drawing, clicks, pressed state and keyboard focus.
``render_device.py``          Cycles on the GPU by default: the startup device setup, the
                              device a render uses and why, the duration estimate (see
                              "Render device").
``bpy_angelo_jobs.cc``        ``_bpy._angelo_jobs``: progress, stop, the job notifier and the
                              document's auto-execution flag.
``view_render.py``            ``angelo_view``'s RENDERED look: a render through the same manager
                              that is not a job tool (see "Looks"). ``view_finished.py`` shows
                              a finished job's picture to the model, ``view_job_output.py``
                              one output of a job that published several. ``view_wait.py``
                              holds a view call while its render job finishes.
============================  ===================================================================

The tools
---------

``angelo_job_start`` takes a ``kind`` and its fields (``job_spec.FIELDS``;
any other field is refused, never ignored). A ``RENDER_STILL`` also reads a
whole, in-range ``frame`` alone as ``frame_start``, and a ``frame_end`` equal
to its ``frame_start`` as the same one frame (``job_spec.still_frame``): both
name exactly one frame, so refusing them would only cost the model a correction
round. Any other frame field or value is refused:

=====================  ===========================================  ======================
Kind                   Fields (required in bold)                    Blender job
=====================  ===========================================  ======================
``RENDER_STILL``       **output**, frame_start, resolution,         Render (one frame)
                       samples, format
``RENDER_ANIMATION``   **output**, frame_start, frame_end,          Render animation
                       resolution, samples, format
``BAKE_TEXTURE``       **target**, bake_type, samples, output,      Cycles object bake
                       sources, cage, extrusion, ray_distance,
                       image, resolution
``BAKE_PHYSICS``       (none)                                       Bake All Dynamics
``BAKE_SIMULATION``    **target**, output                           Geometry-nodes bake
``EXPORT_USD``         **output**, frame_start, frame_end           USD export job
``EXPORT_ALEMBIC``     **output**, frame_start, frame_end           Alembic export job
=====================  ===========================================  ======================

Every kind also takes ``minutes`` (the time budget, default per kind, at most
720) and ``queue``. ``output`` is a new workspace-relative path: a file for a
still or an export, a folder for an animation, baked images, a simulation's disk
cache, or a still that writes several files (`Stereo, multi-view and File Output
renders`_). ``resolution``, ``samples``, ``format`` and the frame range apply to
the job only. Bounds are checked before anything starts (``job_spec``): at most
10,000 frames, 16,384 pixels a side, 100 million pixels a frame, 16,384 samples
and 2 x 10^13 pixel-samples-frames in all. A refusal names the bound and asks
the agent to ask the person, not to split the work on its own.

A texture bake names everything it reads and writes; nothing comes from the
person's selection or active object:

* ``target`` is the object baked onto. With ``sources`` (1 to 8 other observed
  objects) the bake is high to low: Blender's Selected to Active, with the
  sources and the target handed to its bake operator in a context override.
  ``cage`` is a mesh with the target's faces that encloses the sources;
  ``extrusion`` (how far out rays start) and ``ray_distance`` (how far they
  reach, 0 for no limit) default to the person's own bake settings. Every one of
  Blender's twelve Cycles bake types works either way. Displacement is not among
  them: Blender bakes it only from an object's own Multires levels, with another
  operator, which is not a job kind.
* ``image`` is the image to bake into, by its resource ID
  (``dat:<epoch>:IMAGE:<uid>``), whatever node is active in the materials.
  ``resolution`` instead makes a new image of that size, named
  ``<target> <Map>``: Non-Color for normal, position, UV, roughness, ambient
  occlusion and shadow maps, 32-bit float for position, 8-bit otherwise, kept by
  a fake user until a material uses it. With neither, each material's active,
  selected Image Texture node decides, as in Blender.

The call returns at once with the job ID (``j<n>``, unique across restarts) and
its status (``running`` or ``queued`` with its position). A job with an output
also says where it lands, without a local path (``job_host.output_location``):
``output`` is relative to the chat's file workspace, ``workspace`` says which
folder that is (the file tools' workspace, whose root the context names once:
the folder the saved .blend file is in, or, while the .blend is unsaved,
Angelo's own folder for the chat, reached through the card's Open button),
``output`` is where it writes (the next free name when the one asked for is
taken, `Taken names`_, with ``requested`` and a note saying so), and
``if_taken`` the name after it, should that be taken too as the job ends
(``job_output_names``, the same rule as publication; the jobs block names what
was published). With these the agent can tell the person where a render is
saved and whether its name is final, without guessing a local path. A
Codex plan stopped at a later item keeps them in its correction prompt
(``program_completion.continuation_receipt``). ``angelo_job_cancel``
stops a queued or running job of the same conversation; it needs no approval
(it only stops approved work and undoes the job's own temporary changes) and
answers ``stopping``; the outcome arrives in the jobs block.

Admission and approval
----------------------

``BlenderExecutor.precondition`` runs the same admission as ``execute``
(``job_host._admit``), so an approval never offers what would be refused: the
request is validated, the target resolved from an observed handle, the output
admitted, the runner built (which refuses what Blender would refuse or ask
about later) and the queue checked (``JobManager.admit``). The precondition
carries the runner's admission facts (``Runner.describe``), so the approval
shows the resolution, samples, frames, images or caches the job will really use,
read from the person's own settings even while another job has replaced them
(``job_settings.scene_value``).

Refused at admission: a render without a camera; a multi-view render with no
enabled view or with views sharing a file suffix; a File Output node in a linked
node group, with a video format, or with linked items that share a name or carry
``#``, braces or a separator in it (the job could not name their files);
``samples`` on an engine without them; a texture bake onto a mesh without UVs or
faces, outside Object Mode, excluded from the view layer or disabled in renders,
with no bake image (neither ``image`` nor ``resolution`` and a material without
an active, selected Image Texture node), with a linked material or image the job
could not use, from a source outside the view layer, with a cage that is not a
uniquely named mesh with the target's faces, into a UDIM image with a tile that
has no pixels or with none of the target's faces in any of its tiles; a physics
bake with nothing left to bake, or a disk cache in an unsaved file; a simulation
bake without a target, of a linked object, whose bakes are set to disk when the
request has no ``output``, or with an ``output`` in an unsaved file or in a
workspace that is not the file's folder; a staged change in progress. An
output whose name is taken is never refused: the job writes under the next
free name (`Taken names`_).

A Claude plan's job start whose arguments are invalid is refused before the
plan runs, as at admission: the plan runs up to it, nothing after it runs, and
the model gets the error and one correction (``agent_integration.rst``,
"Operations the host refused alone"). A self-check never holds a job start
back.

Every documented bound (frames, resolution, samples, the time budget, the
pixel-samples-frames work) applies to the values the job will really use, not
only to the request's own fields: a render's frame range, resolution and samples
inherited from the scene, a texture bake's image sizes and scene samples, the
span a physics bake steps through (earliest cache start to latest end) and each
simulation's own or the scene's range (``job_spec.check_frames``,
``check_resolution``, ``check_samples``). Each runner's ``admit`` runs at
admission and again when the job starts, since a queued job's scene can change
in between; a refusal names the source and the limit ("The frame range of the
scene, 1 to 20000, is 20,000 frames; a job does at most 10,000. Ask the person
for a shorter range ..."), so the model can relay it.

Render sample bounds include every rendered view layer. Cycles' Use, Bounded
and Ignore policies apply when samples are inherited. An explicit
``samples=N`` replaces the scene count for the job and caps each nonzero layer
override at N; zero/default layers inherit N. It can raise quality above the
scene's saved count while preserving lower explicit layer overrides. The work
bound sums the rendered layers' counts before multiplying by views and frames.
The receipt's ``samples`` is their maximum; multiple layers and an explicit
single-layer selection add ``layer_samples`` to the receipt and approval.
Scene and per-layer denoising retain the person's settings for production
renders. A preview additionally preserves a lower original inherited count,
since its sample cap only reduces rendering work.

A refused request started nothing, and its result says so
(``scene_changed: false``), whichever point refused it: the request's own
fields (``validate_job``, before anything runs: the host answers in one line
that names the limit and what it got, since a Claude plan's schema carries no
string lengths), admission at dispatch, admission again after the approval,
Blender refusing the job at its start (``status: failed``), or a cancel of a
job that ended. A turn therefore goes on (``agent_integration.rst``, "Failed
plan steps"): in a typed plan, Claude's or Codex's, the model gets the refusal
and submits a corrected plan, approved again, within the request's correction
budget (``claude_outcomes.correctable_failure`` names the job tools beside the
staged programs); as a tool call the refusal is an answer inside the turn. A
decline, an expired approval and a result that does not say the scene is
unchanged end the turn.

In a Claude plan, a job started after the plan's own script recorded a failing
kit check renders that build: nothing holds it, but its approval lists the
checks failing then ("These self-checks of the agent's scripts fail now: ..."),
as a fact (``self_checks.SelfChecks.sharer``; ``agent_execution.rst``, "The
script kit and self-checks"). A cancel in a plan whose job is still stopping
lets that job end, for up to 15 s (``plan_recovery.STOP_WAIT``; the measured
stop latency is 0.3-3 s, see `Lifecycle`_), before the plan's next step, so a fix after a cancel
does not meet a busy scene (``WRITER_BUSY``); the cancel's result then has the
job's final status. A Codex turn's next call comes after a model round.

A job start that waits for the person's approval is a call the host holds, so
neither turn limit runs meanwhile (``turn_activity``: the Codex wait asks
``tools_pending``; a Claude plan's operations run between rounds, outside the
wait). A started job is not held: the call returns at once with the job's ID
and the job runs outside the turn.

The job tools are declared only where they can run: a binary without the native
``_bpy._angelo_jobs`` (a stale development build) neither declares them nor
mentions them in its instructions (``job_spec.native_jobs_available``). Release
builds always have the module, so their requests are byte-identical either way.

Animated settings
~~~~~~~~~~~~~~~~~

Blender evaluates a scene's animation on every frame it renders, and an
animated or driven setting is set again each time: a keyed ``cycles.samples``
of 64 and 128 rendered those counts against an approved 8. So every setting a
job sets for itself is held: ``job_settings.Overrides`` mutes the F-Curves on
the scene that write that exact path (the scene's Action, its NLA strips'
Actions and its drivers; ``job_animated_settings``), records them in the
restore tag and unmutes them with the restore, also from a file saved while
the job ran. A channel already muted is left alone. With ``samples``, the job
writes the Cycles layer policy and every rendered layer's count too (unchanged
where they need no change), so all of them are held and what renders on every
frame is what the approval states. A setting the job inherits counts at its
greatest value on the job's frames (keyframes of the scene's own Action with
the Replace blend, evaluated per frame); a driver, an NLA strip, another blend
or an animated layer policy cannot be known before rendering, and admission
refuses with a request for an explicit value, which the job then holds. The
same applies to an inherited animated resolution.

EEVEE has no layer policy: a nonzero view-layer count replaces the scene's
(Blender's ``eevee_sampling.cc``), so admission measures it as Cycles' Use and
an explicit ``samples`` or a look caps each nonzero layer count by writing it.

Sequencer scene strips and extension code
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With its Sequencer on, a render also renders the scenes of its unmuted scene
strips: Camera input renders that scene through its own pipeline (its
compositor, File Output nodes and render settings), Sequencer input renders
that scene's strips in turn (``render_reach``). A job plans only its own
scene's File Output nodes, so a File Output node of a scene it reaches refuses
the job; the other scenes are listed in the approval ("The Sequencer also
renders ... with their own render settings") and bound like its files: a
queued job whose Sequencer now renders other scenes is refused at start. Every
reachable strip, scene and node group is visited once; past 10,000 strips,
4,096 groups or 100,000 nodes admission refuses rather than approve an
unchecked tail.

A render or bake can run Python that does not ship with Angelo: an add-on's
render engine, ``bpy.app.handlers`` the job fires (render, compositor and
frame-change lists for renders, the bake lists for texture bakes, the
frame-change lists for physics and simulation bakes, and for every kind the
depsgraph-update lists, which the job's own temporary settings and frame
changes trigger) and scripted drivers
whose own Python the render would run (``render_extensions``,
``render_drivers``). A driver counts only when it needs Python (Scripted, not a
simple expression, or using ``self``), the render evaluates it (its data-block
is reached from the scene through ID references, ``bpy.data.user_map``:
objects, their data, materials, node groups, the world, the compositor,
background-set and Sequencer strip scenes; another scene's object is not), and
its expression goes beyond the arithmetic, builtins and ``math``/``bl_math``
functions Blender lets any file run. With the document's auto-execution off,
Blender's restricted mode blocks every other expression except one that calls
an entry of ``bpy.app.driver_namespace``, so only a namespace entry that does
not ship with Angelo (an add-on's function) counts then. Restricted mode is
trusted only while auto-execution has stayed off since the document loaded:
Blender keeps running an expression it compiled while auto-execution was on
(``render_drivers.restricted`` remembers, the preference is followed on the
message bus, a load starts again). Without the native flag, auto-execution
counts as on. The approval names it ("Runs Python from
add-ons or scripts, which Angelo can't check") and carries its fingerprint
(the engine, each handler's list, module and name, each such driver's owner,
path and expression), so the precondition check before dispatch refuses a
change since the approval, and ``Runner.start`` refuses a change while the job
was queued (``STALE_SETTING``; starting it again asks again). A handler's body
is the extension's internals: reloading the same handler does not ask again.
The code is never disabled: approving the job approves running it. Not
covered: exports (exporter hooks); ``bpy.msgbus`` subscribers to the settings a
job sets (Blender calls them after each write, and Python cannot list them; the
application can, ``_bpy._angelo_jobs.msgbus_subscribers``, and every view
uses it, but a job's approval does not list them, since every job already needs
the person's explicit approval); and Python an add-on runs beside a job rather
than in answer to it, from a timer, a modal operator or a draw callback, which
neither Python nor the application can list.

Lifecycle
---------

States: ``queued`` → ``starting`` → ``running`` (→ ``stopping``) → ``done``,
``cancelled``, ``failed`` or ``interrupted``. One job runs at a time across the
application; at most four wait (``queue=true``); a queued job is measured again
when it starts. ``job_ui`` polls every 0.25 s while a job is queued or running
and redraws the chat and the status bar only when what they show changed.

Progress is Blender's own job progress (``WM_jobs_progress_type``). For an
animation it is the finished frames (counted as whole files in staging) plus the
current frame's progress. The ETA appears once progress is at least 2% after
3 s. The time budget cancels a job with reason ``time_budget``. A runner
``start`` either starts Blender's job or leaves nothing behind.

Stop asks Blender's job to stop at its next break check, as Escape does
(``_angelo_jobs.stop``); nothing is freed or killed. Measured stop latency on the
fixture scenes (``test_blender_long_jobs``, Apple M-series CPU, two suites in
parallel): animation render 0.3-3.0 s (mid-sample), texture bake 1.3-1.9 s,
physics bake 0.5 s, simulation bake 0.3 s, Alembic export 0.3 s.

What a job changes
------------------

* **Its own settings.** Resolution, samples, image format, output path, engine
  (texture bake) or frame range (USD) are set only while it runs. The values
  they replaced are recorded on the scene first (``_angelo_job_restore``), so
  the job restores them whatever its outcome, and a file saved meanwhile is
  restored when it is next opened or after a crash. A setting is an RNA path
  from the scene, so a compositor node's directory or a modifier's bake
  directory is recorded like a render setting. Settings constrain each other (an
  image format takes its colour depth, its view format and, for a video output,
  its media type with it), so a restore repeats until every recorded value is
  back; a value Blender did not take is named in the receipt (``unrestored``)
  and on the card, never dropped silently.
* **Files.** Blender writes into a hidden staging folder beside the output
  (``.angelo-job-<id>``, carrying a marker; the file tools cannot read hidden
  paths). ``job_outputs.Staging`` creates it through the file tools' anchored
  I/O (``checkpoint_filesystem``: POSIX directory descriptors, verified Windows
  handles) and holds the workspace root and every folder down to the output's
  folder as no-follow handles until it has published or discarded it. Blender
  writes by path, so that path is walked again without following a link and
  must reach the very folders held: at start, on every progress poll and before
  publishing. A link, junction or moved folder anywhere on the way stops the job
  and publishes nothing (``OutputMoved``); its staging is removed through the
  held handles.
* **Publication** is all or nothing and never overwrites. A folder output (an
  animation's frames, baked images) becomes a new folder. A file output is
  published with every dependency its exporter wrote beside it, keeping their
  relative layout: a USD export's ``textures/`` arrives next to the file
  (``export_textures_mode='NEW'``, relative paths). If any name of that set is
  taken, the whole set goes to the next free name (`Taken names`_; a folder
  holding the set when it has dependencies, so its references still resolve).
  Files are new hard links made through the held handles; a failure removes
  what that publication created. A USD export must resolve every asset reference
  (``UsdUtils.ComputeAllDependencies``, on the staged set before it is
  published); otherwise the job fails, nothing is published and the receipt
  lists the missing names. A cancel or failure
  removes staging, except that an animation keeps its finished frames (Blender
  writes a frame only after it finished it). Blender Undo does not remove files.
* **Texture bakes** replace the pixels of the target's images, without Undo,
  and Blender writes even a stopped bake's partial result. Up to 4096 x 4096
  pixels in all the runner keeps a float copy and puts it back on cancel or
  failure; above that the approval says a cancel leaves a partial bake. A new
  image (``resolution``) is removed again instead. A position map is baked
  with one sample whatever the request or the scene asks
  (``job_bakes.ONE_SAMPLE``): Cycles stores a position bake as the sum of its
  samples, not their mean (measured in 5.2.2 with Blender's own operator: a
  point at 0.516 is stored as 0.516, 2.06 and 8.21 with 1, 4 and 16 samples),
  and a position needs no second sample. The approval shows the one sample.
* **A named image** (``image`` or ``resolution``) is baked into by pointing
  every material of the target at it while the job runs
  (``job_bake_targets.TargetNodes``): an Image Texture node holding it becomes
  the active, selected, shown texture node, added for the job if the material
  has none. When the job ends each node tree is exactly as it was: the active
  node, the selection, the texture shown in textured views, and no added node.
  The change is recorded with the job's settings, so a file saved meanwhile is
  put back on its next open. Linked materials are refused.
* **UDIM images** are baked tile by tile by Blender, into the tiles the
  target's faces lie in (its active UV map, ``job_bake_targets.covered_tiles``);
  the approval names those tiles and the image's tile count. The other tiles'
  pixels are left as they are, and the image's own files are never written: like
  every bake, the result is in the image, unsaved. Python reaches only an image's
  first tile, so a UDIM image has no float copy: its backup is each tile's own
  saved file or packed data, valid while the image has no unsaved changes and
  every tile has one. A cancel or failure then makes Blender read every tile
  again (``reload_tiles``), which also leaves the image without unsaved changes.
  Otherwise the approval says a cancel leaves a partial bake and to save the
  image first. With an ``output`` every tile is copied there as
  ``<image>.<tile>.<suffix>``, so the published set loads as one UDIM image.
* **Whether Blender baked.** Blender runs ``object_bake_complete`` for every
  bake job that was not stopped, also one it refused inside the job (a cage
  whose modifier changed its faces), so the handler is no evidence. The runner
  reads it from the images (``job_bake_targets.written``): an image without
  unsaved changes has them after a bake only if it was written; one that already
  had them is compared with the copy kept at start. A bake that wrote nothing is
  ``failed`` and publishes nothing. Only an image that already had unsaved
  changes and has no copy (above the bound, or UDIM) cannot be checked; its
  receipt says so. If some images were written and another was not, the job
  fails and restores its kept copies before releasing them. Ordinary image
  restoration reads the float pixels back and compares them with those copies;
  a removed, resized or unverifiably restored image produces the partial-bake
  note. Every remaining image is still attempted. An entirely untouched bake
  avoids writing its unchanged copies back, which would mark them dirty.
* **Physics bakes** bake the caches that were not baked; Blender skips baked
  ones, which stay as they are. A cancel deletes only the bakes the job made.
* **Simulation bakes** replace the target's earlier packed bake (Blender
  deletes it at start) with one packed into the file; a cancel deletes the
  partial bake.
* **Simulation bakes on disk** (`Disk-cached simulation bakes`_): with an
  ``output`` the cache is written into the job's folder, and when it is whole
  the target's bake target becomes Disk and its bake directory the published
  folder, as a path from the .blend. That is the one scene change a finished
  disk bake leaves; a cancel or failure leaves the bake settings as they were.

While a job runs
----------------

``job_host.guard`` runs before every tool's precondition and execution: scene
edits are refused with the job's ID and progress; reads are allowed, except
while the job locks Blender (physics and simulation bakes, exports, a render
with Lock Interface), when only tools that read no scene data run. A new turn is
refused while Blender is locked (``turn_context.prepare``). Media Studio
generation, workspace mode and the job tools themselves stay available.

A view naming a render job may wait while the interface is locked because
waiting reads only job records. It repeats the ordinary guard before decoding
the finished output: another queued job may have started in the same tick.

A file load or snapshot restore ends Blender's jobs: active jobs become
``interrupted`` (``file_closed``) and their staging is removed. A job left
active by a crash or quit becomes ``interrupted`` (``app_closed``) on the next
start and its marked staging is removed. When a job ends, the scene caches are
invalidated as an unknown edit, because its depsgraph updates ran on the job's
thread.

What the agent sees
-------------------

To inspect a render in the turn that starts it, a plan can follow
``angelo_job_start`` with ``angelo_view`` using ``shading: RENDERED`` and
``image: LAST_JOB``. A later call can use the returned job ID. The view waits
within a shared ten-minute request allowance, returning the picture or an
actionable progress/terminal result. ``file`` still names exactly one published
output; ``frame`` selects an animation frame. The alias is fixed at admission,
and a replaced or missing job is not silently substituted. The wait never
cancels the production job. Image completeness and overall job completion are
separate facts; cancelled animations can retain useful completed frames.

Each later message of the chat that started a job carries a jobs block
(``context_ledger.TurnBlocks.jobs``): the jobs whose state the native context
has not received, as compact JSON after "Background jobs (host status, untrusted
data):". An entry has ``job``, ``kind``, ``status``, ``output``, and while
running ``progress``, ``eta_s`` (rounded to 5 s) and ``frames``; a Cycles
render also has ``device`` (and ``device_why`` when that is the CPU) and, before
it ends, ``estimate_s`` when one is known (`Render device`_); once ended
``seconds``, ``files``, ``images``, ``caches``, ``kept``, ``unrestored``,
``note``, ``reason`` and ``outputs`` (`Output identity`_). No local paths. The
ledger keeps one digest per job, so a finished job is reported once and a
running one when its progress moved. A full message lists active jobs and up to
five finished within a day. The person sees a host-only notice row when a job
ends; it is never sent to a model. The chat stores it in English
(``job_chat.stored_notice``) and shows it translated from the live record
(``console.display_text``). The job's record keeps whether its finished
entry, or its picture, reached the model and when (``shown``,
``agent_execution.rst``, "How a request got to its result"); the entry the
model reads is unchanged.

The two tools are a deferred family (``JOBS`` in ``agent_guidance``): Claude
plans list their names and read the schemas and ``JOB_GUIDANCE`` with
``angelo_agent_capabilities``; Codex threads on OpenAI defer them in the
``more`` namespace; voice and external Codex threads declare them in full.
Collaborators never get them.

Taken names
-----------

A job never moves, renames or replaces a file. When its output's name is taken,
by anything (a person's file, an earlier job's output, a file another program
writes while the job runs), the job writes under the next free name instead,
and that is never a refusal (``job_output_names``): ``<name>-<job>`` for a
folder, ``<stem>-<job><suffix>`` for a lone file, and a new ``<stem>-<job>``
folder that a file with dependencies (a USD export's textures) moves into
whole, so its relative references resolve; then ``-2``, ``-3`` ... up to 64
names, past which nothing is published. A taken name is not an error because
the usual cause is ordinary work: re-rendering a still after a fix, under the
name the person asked for, would otherwise end the turn with "already exists"
or lead the agent to delete the earlier file. Nothing is replaced, not even a
chat's own earlier output (`Limits`_).

* **Said before it runs.** When the requested name is taken at approval, the
  card says so and where the output goes ("<path> already exists and is never
  overwritten; this job writes <name> instead"); when a name a USD export
  writes beside its file is taken (its ``textures`` or ``volumes`` folder), it
  names the folder the set goes to. The card reserves the job's ID, so two
  cards pending at once never name the same path, and the dispatch keeps its
  choice: a name the card called taken is never written, even if freed before
  the click. That is shown only, never compared at the click
  (``job_host.Precondition``), so a name taken or freed meanwhile never refuses
  the approval; a name taken after the card goes to the next free one. The
  start result's ``output`` is where it writes (``requested`` the one asked
  for, and the note says why), ``if_taken`` the next free name after it, and
  its card and jobs block name that path while it runs. A set (a USD export)
  falls back to a folder whatever it holds, and an Alembic export is one file.
  Every name taken (64, the job's ID in 63) is refused at admission, as is a
  name longer than the file system allows; candidates stay within 255 bytes,
  and a link at the name is a taken name, never followed (a hidden or private
  name never an output).
* **Said if Angelo closes while it publishes.** Before the first link of a
  publication that takes more than one, its record says where it goes, so the
  next start says "Angelo closed while this job was publishing its output,
  which may be unfinished", with that path, instead of "Nothing was published".
* **Never overwritten, whatever appears meanwhile.** Publication creates each
  name with a call that fails on an existing one (a new folder, a new hard link
  through the held handles) and tries the names in order, so a file that
  appears between approval and publication, or during it, is never
  overwritten: the output takes the next free name. A set whose name, or one
  of its files' names, appears while it is published takes back what it made
  (only entries still the ones it made, by device and file ID) and moves whole
  into the next free folder.
* **Named in the result.** The jobs block and the receipt's ``output`` name the
  path the output has, and the card's Open opens it.

``test_native_job_free_names.py`` pins each rule on real files, on POSIX and on
the Windows handle backend (``fake_nt_files``); ``windows_ntfs_checks.py`` has
the collisions, case-insensitive ones included, on a real NTFS volume.

Looks
-----

``angelo_view`` with RENDERED shading (``agent_execution.rst``, "Rendered
looks") renders the scene camera through this manager under the internal kind
``job_spec.PREVIEW``. It is not a kind of ``angelo_job_start`` and differs from
a job where a look must: it needs no approval (a scene whose render would run
add-on or script Python, or render other scenes through its Sequencer, gets
no look; see "Sequencer scene strips and extension code"), its tool call waits for it and
is answered with the image, its time budget ends it once it has samples to
show (a partial picture), its output is one PNG in the conversation's private
folder (no workspace staging), and when it ends its record is dropped, with no
chat notice, status-bar entry or jobs-block entry. A look of a ``region`` is
admitted and recorded for the pixels of that part, and sets Blender's render
region for the job. It is a job where safety needs
it: one runs at a time (a look is refused while a job runs, and a job started
while a look renders waits with ``queue=true``), the guard refuses scene edits
meanwhile, its settings are recorded on the scene and restored (also from a
file saved meanwhile), a file load interrupts it, and its card and the status
bar show progress and Cancel. A look left by a crash is dropped at the next
start, after its settings were restored.

Model-visible bytes
-------------------

The job tools were introduced as toolset 34 and extended in toolset 38
(``agent_integration.rst``, "Toolset 38"), which changes only what a model reads
on demand. Toolset 34's cost against toolset 33 (compact JSON): Claude plan
schema +39 B (their deferred names), Claude instructions +97 B (one sentence),
Codex plan instructions +114 B, Codex plan output schema +19 B, Codex dynamic
tools +2,020 B (deferred: request bytes, not prompt bytes, since Codex sends a
model no deferred declaration), voice dynamic tools +1,976 B and
instructions +97 B; one lookup of both tools costs 3,121 B.
The full table and the signed-out figures are in ``agent_integration.rst``,
"Toolset 34". ``strict_problems`` finds nothing in the Codex output schema of
either variant or in the strict forms of the two tool schemas.

Toolset 38 against toolset 37: Claude's instructions, a Codex thread's plan
instructions and its output schema are byte-identical.
``angelo_job_start``'s declaration grows by 628 B (five properties, one
sentence) and ``JOB_GUIDANCE`` by 761 B, so one lookup of both tools costs
4,520 B instead of 3,121 B; Codex's deferred dynamic tools grow by 564 B
(request bytes) and a voice thread's by 628 B. Showing one output adds
``angelo_view``'s ``file``: 25 B in Claude's plan schema and 212 B in the view
tool's declaration (``agent_integration.rst``, "Toolset 38"). Each finished
job with several outputs also adds its ``outputs`` to one jobs block, once:
about 60 to 110 B an output, at most 24 outputs.

Stereo, multi-view and File Output renders
------------------------------------------

Stereoscopy and compositor File Output nodes write files beyond the render's
own image, the nodes at paths stored in the scene. A job may write only inside
its output and must say what it will write, so it plans every file before it
starts (``job_render_scene.Plan``, ``job_render_outputs.plan``):

* the render's own image: one file per enabled view (Blender appends the view's
  file suffix, also for a single view), or one file for the frame when the
  format combines the views (anaglyph or side by side; a multi-view EXR, kept
  when the person's own format is one and the job writes EXR);
* one file per linked item of every unmuted File Output node, in the scene's
  compositor and in the node groups it uses, per view when the item's format
  keeps views apart; a multi-layer node writes one file holding its items.

Every output that keeps views apart checks the view suffixes for unsafe path
characters and duplicates, including individual File Output items and
multi-layer nodes when the main render combines its views. Unused suffixes
on combined outputs and unlinked items do not constrain those valid outputs.
The complete output plan also requires distinct names ignoring case and Unicode
composition, including
Unicode casefold aliases. This portable naming constraint prevents an item or
view from overwriting another on supported case-insensitive filesystems. A
refusal names the two conflicting file patterns and asks for distinct item
names or view suffixes; no output kind or image format is removed.

The job names all of them itself, so no stored path or name template is used:
``frame_<frame><view suffix>`` for the image and, for a node, a folder named
after it holding ``<item><frame><view suffix>`` (``layers<frame>`` for a
multi-layer node). While the job runs, the render path, the image format with
its view format, and each node's directory, file name and file extension point
into the staging folder; they are RNA paths from the scene, recorded with the
job's other settings and restored exactly on success, cancel and failure, and
from a file saved meanwhile. Blender expands ``{variables}`` in output paths
and writes nothing for a path it cannot expand, so the job writes its own paths
with their braces doubled: a workspace folder may have braces in its name.

The approval lists every planned file with its view, node and item. When the
render ends, what was written is sorted against the plan
(``job_render_outputs.sort_files``): a file the plan does not name fails the job
and publishes nothing; a frame counts as finished when its own image files
exist, which Blender writes last, and an unfinished frame's File Output files
are removed before publishing, so a cancelled animation keeps whole frames of
every output. Blender writes a File Output item only when its input is an image;
an item fed one colour is planned, listed and then reported in ``missing``
rather than invented. A still that writes several files publishes a folder
named like the still without its file suffix; the start result, the card and the
jobs block name that folder. A queued job is approved for the files planned
then: if the views or nodes changed before it starts, it is refused.

A scene set to write a video offers no still format until its media type
changes, so a job sets the media type too and restores it with the colour depth
and mode a format change alters. A subclass without an output folder of its own
keeps the two refusals: it has nowhere to send those files.

Disk-cached simulation bakes
----------------------------

Blender writes a disk bake at the directory stored on the modifier, which may
be anywhere, and asks before it replaces bake data it finds there. A job can
neither scope that directory nor answer the question, so a bake set to disk
runs as a job only into the job's own folder: ``BAKE_SIMULATION`` with an
``output`` (a new workspace folder) bakes to disk there (``job_bake_caches``). While it runs, each
geometry-nodes modifier of the target that has simulation zones gets a folder
of its own in the staging folder as its bake directory, with Disk as its bake
target, and its simulation bakes inherit both; these are recorded settings like
any other. The staging folder is new, so Blender finds no data to ask about; a
bake that did not start is detected (the job type has no progress) and never
reported as one that ran.

* **Whole or nothing.** When Blender's job ends, every simulation must have one
  ``meta`` file per frame of its range, and its ``blobs``. Then the folder is
  published (``<output>/<modifier>/<bake id>/``) and the scene pointed at it. A
  cancel, or a bake that could not write every frame, makes Blender forget the
  partial bake without deleting anything by path (the directory is cleared
  first, so its own Delete Bake finds no folder), restores the settings and
  removes the staged files through the held folders.
* **What the scene references afterwards.** The modifier's bake target is Disk
  and its directory ``//<output>/<modifier folder>``: a path from the .blend,
  never an absolute one. The cache is found again when the file is reopened and
  when the project folder is moved or copied as a whole. This needs a saved
  file whose folder is the chat's file workspace, which is what a saved file's
  chat has; otherwise the request is refused, and the refusal says that a bake
  without ``output`` is packed into the file.
* **Earlier bakes.** A cache the scene referenced before is neither written nor
  deleted: after a finished bake it is left on disk, unreferenced; after a
  cancel it is read again. An earlier packed bake is deleted by Blender when
  any bake starts, as for a packed job, and the approval says so. Jobs never
  overwrite, so a second disk bake names a new folder.
* **Identity.** The receipt lists each cache with ``cache`` (``<modifier>/<bake
  id>``), ``name`` (the object), ``frames`` and ``file`` (its folder in the
  output).

Output identity
---------------

A finished job's receipt lists what it produced as ``outputs``, one entry per
result, so the agent and any tool that shows a job's result can name one
without a path. An entry is plain data: ``file`` (relative to the job's
published ``output``, present when a file was written) and what the result is.
For a texture bake: ``image`` (the baked image's resource ID, usable at once in
``node_edit`` and ``world_configure``), ``name`` and ``map`` (the bake type).
A UDIM image has one entry per tile with ``tile`` (its number); a tile the bake
did not write is listed only when its copy was published, without ``map``.
For a render that writes several files: ``view`` (or ``views`` when one file
holds them), and for a File Output node ``node`` with ``slot`` (the item) or
``layers`` (a multi-layer file's items); an animation's ``file`` is a pattern
with ``####`` for the frame. A plain still or animation reports no ``outputs``:
its ``output`` is the file or folder. For a simulation's disk cache:
``cache``, ``name``, ``frames`` and the cache's folder as ``file``.
``job_store.output_entries`` is the one reader: it keeps known keys with bounded
scalar values only (a record on disk is data, never authority), and a model's
entry carries at most 24 of them, with ``outputs_more`` counting the rest.

An output is addressed by its job ID and its ``file``, exactly as reported,
plus a frame number for an animation's pattern. ``job_ui.output_file(record,
name, frame)`` returns that published file's local path, or None for a name the
job did not report, a frame outside its range, any ancestor or file link, or a file
moved since; ``job_ui.output_frames(record, name)`` lists the frames of a
pattern that exist (a stopped job kept only some). A tool that shows a job's
result reaches it through these functions and never builds a path itself
(``job_store.published_file`` and ``published_frames`` are their Blender-free
bodies). Without a name ``output_file`` returns the output of a job that wrote
one file.

Discovery does not authorize reopening that path: an external process can
replace a directory or file between lookup and decoding. ``job_store.copy_output``
holds the existing no-follow directory/file handles through a bounded stable
copy into the observation store. It checks file and directory identity before
returning. ``view_finished.file_source`` admits and decodes only those private
bytes, including the native OpenEXR and stored-data paths, and removes the
copy on success or failure. The copy has the existing finished-picture limit
of 256 MiB; this adds one streaming read/write and no second full in-memory
buffer. Legacy still and animation receipts use the same consumption path.
Job receipts do not retain immutable file content: an ordinary file deliberately
edited or replaced before capture is still viewable. Results label the source
``CURRENT_OUTPUT_FILE`` and say so; engine, samples and output labels are the
original job's recorded metadata, not proof that the current file still holds
its original pixels. The confinement proof covers linked outside files and
races during capture, not historical authenticity of ordinary output bytes.

**Looking at one output.** ``angelo_view`` with RENDERED shading, ``image``
(the job's ID) and ``file`` (an output's ``file``; ``frame`` for a pattern)
shows that picture to the model: one stereo view, one File Output item, one
UDIM tile, one baked map (``view_job_output``; ``agent_execution.rst``,
"Rendered looks"). A map of data (``job_spec.DATA_MAPS``) is shown as its
stored values, with no view transform, and the result says so; a job that
published one file needs no name; a job with several and no ``file`` is
answered with their names. A bake is looked at through its published copy, so
it needs an ``output``; a file that holds several layers or views in one
OpenEXR is not shown. The call is bounded like every finished picture, and it
streams the bounded input copy described above. When such a picture reaches the
model, the job's record notes it: ``shown.image_at``, ``shown.images`` and, per
output, ``shown.files`` (``agent_execution.rst``, "How a request got to its
result").
An absent image mark means delivery is unestablished. Codex code-mode tool
results expose image URLs, but the app-server does not report whether code
forwarded them with ``image()``; those writes remain unconfirmed in the request
receipt and do not mark the job shown. This does not change the pictures sent.

The chat card and the status bar
--------------------------------

The card sits below the ``angelo_job_start`` activity row: a status dot and
label, the job's title and facts, a progress bar with frames and ETA while it
runs, the output path, and its buttons: Cancel (disabled "Stopping…" once
asked), Open (primary; only for published files, disabled if they were moved)
and Details (time budget, engine, receipt, the Undo note). The buttons are
ordinary canvas hits, so their hover, pressed, keyboard focus (Tab and Shift+Tab
in reading order, Return or Space, Escape) and disabled states, with the reason
on hover, are ``canvas_controls``' like every other chat control, and the card
mirrors with the chat in Arabic (``canvas_rtl``). The status bar shows the
running job with Cancel, then a finished job with Open for 30 s.

All text follows ``localization.rst``: ``job_chat``, ``job_ui``,
``job_approvals`` and ``chat_cards`` are UI modules for the bypass scanner;
``job_spec`` and ``job_store`` are model-facing; ``job_manager``,
``job_runners``, ``job_bakes`` and ``job_host`` keep model-facing English and
mark the reasons and notes the card shows with ``n_``, so the card translates
them; counted phrases use ``plural_``; operator reports use ``rpt_``; the
locked-turn error is a ``display_text.Text``.

Native support
--------------

* ``_bpy._angelo_jobs`` (``bpy_angelo_jobs.cc``): ``progress(scene, type)``,
  ``stop(scene, type)`` and ``notify()`` for a closed table of job types owned by
  a Scene, main thread only, and ``scripts_auto_execute()``, the document's
  ``G_FLAG_SCRIPT_AUTOEXEC`` (set by the preference, ``--enable/--disable-autoexec``
  and a trusted reload; Python cannot read it otherwise). ``stop`` also sets ``G.is_break`` for the Cycles
  texture bake, which checks only that. ``notify`` sends ``NC_WM | ND_JOB``, the
  notifier the status bar (a global area outside ``Screen.areas``) redraws on.
* ``WM_jobs_progress_type`` reads the progress of one job type.
* ``wm.usd_export`` has a hidden ``as_background_job`` option, as Alembic export
  has, so a script can start the export as a job.
* **Python on job threads.** Python render engines (Cycles) call into Python
  from the render or bake thread. Only the main thread counts nested
  ``bpy_context_set``/``bpy_context_clear`` calls and owns ``bpy.context``;
  another thread only takes the GIL. Its count, made before the GIL is held,
  would race the main thread's: one lost update would leave the level negative,
  and the main thread would never set its context again in that session
  ("Python context internal state bug" on every call). The suite runner fails
  any log with that message. Angelo's depsgraph handler ignores updates
  delivered on a job thread (no Blender data may be read there).

Validation
----------

* Host: ``test_native_long_jobs.py`` (bounds on effective values, records,
  queue, cancel, budget, start and finish failures),
  ``test_native_long_jobs_outputs.py`` (anchored staging, set publication with
  dependencies, fallback names, a link before start or before publishing, a
  moved folder, a link inside staging, crash cleanup; each on POSIX descriptors
  and on the Windows handle backend through ``fake_nt_files``) and
  ``test_native_long_jobs_ui.py`` (card states and translatability, approval
  facts, jobs block deltas, the guard, the deferred family, the strict plan
  schema in both variants, no job tools without the native module) and
  ``test_native_job_free_names.py`` (a taken name gets the next free one, said
  before it runs and in the result, and nothing existing is ever changed).
* ``test_blender_long_jobs`` (GUI): every kind on real Blender jobs through
  ``BlenderExecutor``: progress range and growth, ETA, stop latency, whole kept
  frames, restored settings (also from a file saved mid-job), queued facts, the
  guard, the time budget, image restore, own physics bakes only, a USD export's
  textures published beside it with every reference resolving, a dangling USD
  reference never reported done, export cancel, a queued job whose folder became
  a link or whose scene range grew past the bound refused at start (and at
  admission), simulation target and packing, file-load interruption and crash
  reconciliation. Log scan: no traceback or Python context error.
* ``test_blender_production_bakes`` (GUI): high-to-low and cage bakes on a
  plane and a high-poly mesh with a planted pyramid: the pyramid is in the baked
  normal map where it is and nowhere else, a ray without extrusion misses it and
  a cage finds it; approval facts and text; the person's selection, active
  object, mode, bake panel and the material's node tree unchanged after done,
  cancel and failure; a cancel puts the pixels back; a bake Blender refuses in
  its job is never done; the published copy and its image identity in the jobs
  block; a second bake through a material using the baked map; a new image from
  ``resolution`` and its removal on cancel; the job's overhead beside Blender's
  own bake.
* ``test_blender_production_renders`` (GUI): a stereo scene whose compositor
  has File Output nodes (PNG items, a multi-layer EXR, one in a group, one
  muted, one fed a single colour) pointing at a folder outside the workspace:
  the approval lists every file; the published still and animation hold exactly
  those files, each with the pixels Blender writes for that view and item when
  it renders the scene by itself; a multi-view EXR in one file; nothing in the
  person's folder; the path, the 32-bit EXR format and every node's settings
  unchanged after done, cancel, Blender's own refusal and a stale queued job;
  whole frames of every output on cancel; a second job rendering the first
  one's published view; a video-output scene; a workspace path with braces.
* ``test_native_job_evidence`` (host): a real controller composes the next
  message from finished records of each new case; the real ``ClaudeProvider``
  and ``CodexProvider`` deliver it to their stand-ins (a fixture Claude Code
  process, a scripted Codex app-server), which receive every output with its
  identity, the real output folder and no local path.
* ``test_native_job_render_outputs`` (host): the plan per view, format and
  node, what cannot be named, the sort of written files, the approval's list.
* ``test_blender_job_output_views`` (GUI) and ``test_native_view_job_output``
  (host): one output of a job shown by ``angelo_view`` (``agent_execution.rst``,
  "Rendered looks"): planted defects in a baked normal map, one stereo view and
  one UDIM tile read from both runtimes' payloads, stored values without a view
  transform, a position bake's one sample, every refusal and the shown mark.
* ``test_native_job_turn_recovery`` (host): the shape of every refusal; a job
  refused at admission, at its start or as a cancel gets a corrected plan on
  Claude's loop and on Codex's, within the shared budget, and is an answer
  inside the turn as a tool call; a decline and an unknown state end the turn;
  a job start held for approval outlasts both turn limits, and the same wait
  without a held call is given up.
* ``test_blender_udim_bakes`` (GUI): a mesh with faces in tiles 1001 and 1002
  bakes into a three-tile image saved in the person's own folder: those two
  tiles hold the bake inside the faces' UV squares, tile 1003 is byte-identical
  (each tile read from a saved copy), the person's tile files are never written,
  every tile is published with its number; a cancel puts file-backed and packed
  tiles back and leaves the image without unsaved changes; an image with
  unsaved changes says so before the job; a refused bake, an empty tile and UVs
  outside every tile; a second bake through the baked UDIM image.
* ``test_blender_disk_simulation`` (GUI): a saved project's simulation baked to
  disk: a file per frame in the published cache; the simulation reads it after
  the job, after saving and reopening, and after the project folder is copied
  elsewhere and the original removed; the modifier holds a path from the .blend
  and no absolute one; an unsaved file, a foreign workspace and a disk bake
  without output are refused with what works; a cancel and a bake that cannot
  write its frames remove what they staged, leave the bake settings as they
  were and the earlier cache untouched and in use; a second bake into a new
  folder; an Alembic export job of the baked motion.
* ``test_blender_job_restore`` (background): settings that constrain each other
  restored whatever the order, settings found by name, an unreachable setting
  reported, and a file saved while a bake's or a render's changes were applied
  opened as the person left it.
* ``test_blender_job_card`` (GUI, event simulation): the approval card, every
  card state with screenshots for visual inspection, pixel checks of hover and pressed,
  a disabled button's reason, real Cancel and Open clicks, keyboard focus,
  notices, the status bar, and the card in Arabic: mirrored, with the pointer at
  a button's drawn place reaching it.

::

  python3 tests/python/angelo_agent/run_host_tests.py --max-skips 0
  python3 tests/python/angelo_agent/run_native_suites.py --binary <Angelo> --gui \
    --include test_blender_long_jobs --include test_blender_job_card

Render device
-------------

Cycles renders on the GPU by default, on every machine with a GPU Cycles
supports. Stock Blender never lists the compute devices at startup (some drivers
crash while listing them), so until a person opens Preferences > System, or
Cycles' Render panel draws, the device list is empty and a scene set to GPU
renders on the CPU without a word (``blender_device_info``). A render started
from a script, as every agent render is, met that empty list.

* **Scene default.** Angelo's Cycles defaults ``cycles.device`` to ``GPU``
  (``intern/cycles/blender/addon/properties.py``). A file saved with CPU chosen
  keeps CPU; a scene that never chose renders on the GPU when one is set up, and
  on the CPU otherwise.
* **Setup never blocks.** ``render_device.setup`` runs one second after
  startup, after Load Factory Preferences, and when a Cycles render's facts
  are measured. It never lists devices in the app: listing initializes GPU
  drivers while holding the interpreter lock, and can end the process (why
  Blender does not list at startup). A short child process of the same app
  (``--background --factory-startup``) lists them and prints the result; a
  driver that crashes or hangs ends only the child (stopped after 120 s). A
  main-thread timer then adds Cycles' device entries as Cycles would (every
  GPU enabled, the CPU not) and selects the first backend with a device:
  OptiX, CUDA, HIP, Metal, oneAPI. Cycles initializes that backend itself
  when it first renders, on the render thread for a render job.
* **The person's choice wins.** A compute backend someone set
  (``is_property_set``: Preferences > System, or Import Blender Settings),
  None included, is left alone and never listed. Blender's untouched default
  (None, or Metal on Apple Silicon) is not a choice. A backend Angelo set and
  saved preferences kept stays Angelo's; its listing only refreshes which
  devices exist.
* **Persisted the Angelo way.** Angelo never saves Blender's preferences for
  this (their dirty flag is restored). What it applied is
  ``render-device.json`` in the state folder, compared at each start to tell
  Angelo's setup from the person's.
* **Falls back to the CPU with a note.** No supported GPU, or a listing child
  that crashed, hung or could not start, leaves Cycles on the CPU and says
  why: in the console, and to the agent in ``device_why``. Background (``-b``)
  runs do not load the agent add-on and keep Blender's behaviour.
* ``test_blender_cycles_device_default`` (background native suite) pins the
  scene default (a saved CPU stays CPU, a scene that never chose reads GPU)
  and the child listing in the real binary.

What the agent reads (facts, never rules):

* The scene settings of each turn carry ``device`` for a Cycles scene: for
  example ``GPU (Metal: Apple M4 Pro (GPU - 20 cores))``, or ``CPU`` with
  ``device_why`` and, when the scene chose CPU while a GPU is set up,
  ``gpu_available``. It is read from the preferences only; it never lists
  devices.
* A Cycles render's facts (the approval, ``angelo_job_start``'s result, the
  jobs block, ``angelo_view``'s RENDERED result) carry the device measured
  when it started. ``angelo_job_start`` also returns ``cancel`` (the call that
  stops it) and, when this session already finished a render on the same
  device, ``estimate_s`` with ``estimate_from``: that render's seconds scaled
  by megapixels x samples x frames. Preparation does not scale that way, so it
  is an estimate, and it says what it was made from.
* ``--cycles-device`` on Blender's command line overrides everything, as in
  Blender, and the facts say so.

Limits
------

* One job at a time; the queue holds four and does not survive a restart.
* A job never replaces an existing file, including an earlier output of the same
  chat: a re-render lands under the next free name (`Taken names`_).
* A disk-cached simulation bake needs a saved file whose folder is the chat's
  workspace. It bakes every simulation zone of its target, not one zone; Bake
  nodes and physics point caches on disk are not redirected (a physics disk
  cache is written beside the .blend by Blender, as before).
* A stopped bake into a UDIM image with unsaved changes, or with a tile that has
  no saved file, keeps its partial result (the approval says so first).
* A render plans at most 64 files a frame. A File Output node's own file name
  and directory are not used: its files are named by the job, inside its output.
* ``angelo_view`` shows one published picture file: not a bake made without an
  ``output`` (its result is only in the image), and not a file that holds
  several layers or views in one OpenEXR. A PNG, JPEG or OpenEXR file only.
* A bake's failure reason is generic ("see its Info log"): Blender's own report
  is not readable from Python. Displacement baking (Multires) is not a job kind.
* A cancelled texture bake above 4096 x 4096 pixels keeps its partial result.
* Undo while a render runs may restore or keep the job's temporary settings;
  the job restores the recorded originals when it ends.
* Memory, file and team-library writes are refused while a job runs, like
  scene edits; only tools listed in ``job_host.SAFE_BESIDE_JOB`` run.
* Blender writes by path. A folder swapped for a link while a job runs is found
  by the next progress poll (0.25 s), which stops the job and publishes nothing,
  but a frame Blender finished in between can land at the link's target.
* Not measured: GPU rendering, production-size scenes, and the GUI job suites
  on Windows (the host suites run the Windows handle backend through the
  ``fake_nt_files`` stand-in, and ``windows_ntfs_checks.py`` checks staging and
  publication on a real NTFS volume).

Extending background jobs
-------------------------

* **New kind**: add it to ``job_spec`` (``KINDS``, ``FIELDS``, ``REQUIRED``,
  ``DEFAULT_MINUTES``, ``LOCKING`` if Blender locks its interface), a runner in
  ``job_runners``/``job_bakes`` whose constructor refuses what Blender would
  refuse or ask about, the job type in ``bpy_angelo_jobs.cc`` (owned by the
  Scene), titles in ``job_chat.TITLES`` and approval text in ``job_approvals``;
  extend both suites. A new kind changes the tool schema, so it needs the next
  ``routing.TOOLSET_VERSION``.
* **Restore beyond the bound**: a native image snapshot (``IMB_dupImBuf`` per
  tile) would make every texture bake restorable, an unsaved UDIM image
  included, and let a job check any image it wrote.
* **Persistent queue**: queued requests are lost on restart (recorded as
  ``interrupted``); re-admitting them needs a fresh approval.
* **Progress for exports**: USD and Alembic report coarse progress; finer
  progress needs their job callbacks to report frames.
* **Why a bake failed**: ``_bpy._angelo_jobs`` could return the last error
  report of a job type, so a refused bake's receipt carries Blender's reason.
* **Toolset**: the job tools last changed in toolset 38. A change to their
  schemas or guidance needs the next ``routing.TOOLSET_VERSION``.
