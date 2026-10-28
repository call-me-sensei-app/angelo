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
``job_deadline.py``           The time budget as a deadline the job's own break check reads;
                              a Cycles job's peak memory (see "Time budget").
``job_outputs.py``            Output admission, anchored hidden staging held from creation to
                              publication, all-or-nothing set publication. Pure.
``job_output_names.py``       The names an output is published under: its own, else the next
                              free one with the job's ID. Pure.
``job_recovery.py``           An animation render's finished frames kept after a quit, crash,
                              file load or failed publication, and rendering the rest. Pure.
``job_frame_check.py``        Whether a written PNG, JPEG or OpenEXR file is whole, from its
                              own bytes. Pure.
``job_resume.py``             Rendering the frames a kept render lacks into its folder, on the
                              person's click. Pure.
``job_render_keep.py``        How an animation render ends without removing a finished frame
                              (``RenderRunner``'s ``KeepFrames`` part).
``job_exports.py``            USD and Alembic export runners.
``job_settings.py``           Scene settings a job changes for itself, recorded on the scene and
                              restored (also after a save or a crash).
``job_runners.py``            The runner base and renders on Blender's jobs.
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
``job_fluid.py``              A Mantaflow (smoke, fire, liquid) bake: a ``BAKE_SIMULATION`` whose
                              target is a fluid domain, into the job's folder, its cache state
                              read and written directly (see `Fluid bakes`_).
``job_fluid_plan.py``         A fluid bake's grid, memory and disk estimates, expected files and
                              unsafe folder names, before it starts. Pure.
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
``render_device.py``          Cycles on the GPU by default (see "Render device"): the agent's
                              name for ``angelo_render_device.device`` (the setup and its
                              saved listing), with ``render_device_facts`` (the device a
                              render uses and why, the estimate) and ``render_device_session``
                              (the gate Cycles calls). The package lives in
                              ``scripts/modules``: Cycles imports it, never the agent.
``bpy_angelo_jobs.cc``        ``_bpy._angelo_jobs``: progress, stop, the job notifier, the
                              document's auto-execution flag and the desktop notification calls.
``job_desktop.py``            Outside the window: the computer held awake while a job runs, and the
                              operating system's notification when a long job ends (see
                              `Away from the computer`_).
``job_desktop_prefs.py``      Its two preferences, a mix-in of the add-on's preferences; switched on
                              the AI Providers page (``providers_page_model``).
``desktop_os.py``             The power and attention calls through ctypes: macOS power
                              assertion, Windows power request, Windows taskbar flash.
``bpy_angelo_desktop*.mm/cc`` macOS Notification Center (UserNotifications); a stub elsewhere.
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
``RENDER_ANIMATION``   **output**, frame_start, frame_end, name,    Render animation
                       resolution, samples, format
``BAKE_TEXTURE``       **target**, bake_type, samples, output,      Cycles object bake
                       sources, cage, extrusion, ray_distance,
                       image, resolution
``BAKE_PHYSICS``       (none)                                       Bake All Dynamics
``BAKE_SIMULATION``    **target**, output (required for a fluid     Geometry-nodes bake;
                       domain)                                      Mantaflow bake
``EXPORT_USD``         **output**, frame_start, frame_end           USD export job
``EXPORT_ALEMBIC``     **output**, frame_start, frame_end           Alembic export job
=====================  ===========================================  ======================

Every kind also takes ``minutes`` (the time budget, default per kind, at most
720) and ``queue``. A render's ``format`` is PNG (the default), JPEG, OpenEXR,
or ``SCENE``: the scene's own image settings, untouched, as F12 writes them
(multilayer OpenEXR with every pass of every rendered layer, TIFF, DPX and the
rest, with the scene's codec, colour depth and mode; `Deliverables`_). An
animation renders on the scene's own frame step, as F12 does. ``output`` is a new workspace-relative path: a file for a
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
and redraws the chat and the status bar only when what they show changed. A
RENDERED look is also watched every 20 ms and finished as soon as its render
ends (``look_notify``; ``ANGELO_LOOK_NOTIFY=0`` turns it off).

Progress is Blender's own job progress (``WM_jobs_progress_type``). For an
animation it is the finished frames (counted as whole files in staging) plus the
current frame's progress. The ETA appears once progress is at least 2% after
3 s. The time budget cancels a job with reason ``time_budget`` (see "Time
budget"). A runner ``start`` either starts Blender's job or leaves nothing
behind.

Time budget
-----------

The budget is enforced where the job runs, not only by the main thread:

* **A deadline the job reads itself.** When a render job or texture bake starts
  (not the agent's looks, ``RENDER_PREVIEW``, whose budget ends them once they
  have samples to show),
  the manager sets its budget as a deadline (``job_deadline.arm`` →
  ``_angelo_jobs.deadline``, ``BKE_angelo_job_deadline.hh``). The render
  job's ``render_breakjob`` and the bake's ``bake_break``, which every
  renderer polls from its own thread, return true once it passes, so the job
  stops itself however busy the main thread is. The deadline belongs to that
  one job, its kind (render or bake) and its scene: a render or bake the
  person starts meanwhile, of another scene or another kind, never reads it,
  and the job's own end callback (``render_endjob``, ``bake_freejob``) clears
  it, so it cannot outlive the job (a Reload Scripts or a dropped manager
  included). The manager's 0.25 s timer still checks the budget too. A job is
  recorded as cancelled with reason ``time_budget`` only when its break check
  returned true because of the deadline (``_angelo_jobs.deadline_fired``); a
  job that finished on its own keeps its output, however late the timer sees
  its end (the break-it review found finished stills thrown away when the
  end was seen just past the budget). A build without ``deadline_fired`` uses
  the timer alone. The native suite ``test_blender_render_deadline`` (GUI,
  every platform, a GPU required) holds the main thread past a GPU still's
  budget and checks that its own break check stopped it, that the person's
  render afterwards and a render under another scene's passed deadline run to
  their end, and that a texture bake is stopped by its deadline and its image
  put back.
* **Cycles' Metal BVH build reads its cancel.** Its throttler waits
  (``BVHMetalBuildThrottler::acquire`` and ``wait_for_all``,
  ``intern/cycles/device/metal/bvh.mm``) used to sleep until every earlier
  GPU build finished, never reading the session's cancel. On a memory-starved
  machine those builds can take hours, so neither Cancel nor the budget could
  end the render. They now return on cancel. A build still on the GPU writes
  its result only through its BVH's build owner (``BVHMetal::BuildOwner``),
  which the BVH's destructor detaches, so a cancelled session can free its
  BVHs without a late completion handler writing into freed memory. A BLAS
  build acquires before it creates its command buffer, so a cancelled acquire
  releases only the buffers it allocated: a command buffer dropped uncommitted
  still runs its completion handlers on deallocation, and they would release
  everything a second time (found by the break-it review, reproduced on macOS
  26). The throttler is process-wide: the next render still waits for builds
  already on the GPU, but that wait is cancellable too.
* **The estimate is a fact, never a refusal.** A render's time is a fixed
  part (kernel load, scene sync, BVH build, GPU synchronization, writing the
  image) plus a part that grows with megapixels x samples x frames.
  ``render_device`` keeps the last few finished Cycles renders per device.
  Two of the same scene (``detail["scene_uid"]``, local only) and the same
  frames x views, with work at least 2x apart, times at least 1 s apart
  (``FIT_MIN_SPREAD``) and the larger also the slower, give a fitted estimate:
  fixed part plus the per-work part scaled (``render_device.fitted``).
  Otherwise one render gives a scaled fact: its time before the first sample
  (the manager records ``first_progress_s``) plus the rest scaled, else its
  whole time. The estimate goes to the approval card (translated) and to the
  model's facts; the deadline, not the estimate, ends a render at its budget.
  Measured on Mac 1 (2026-10-23, 3 scenes x Cycles CPU/GPU/EEVEE, stills):
  one-render estimates were over 2x the real time in 60-71% of 513 cases; a
  fit of an unchanged scene was 0.88-1.15x with exact times, and at most
  1.48x with the manager's 0.25 s end stamps added once the two times are
  1 s apart (with only the larger at least 1 s: up to 3.2x). A fit across a
  scene that changed between its two renders (a small look, more building, a
  big render) was over 2x in 25-30% of cases whatever the check, and fits
  across frame counts read an animation's per-frame setup as work (break-it
  review), hence the same-scene and same-frames rule. Refusing a render on
  its estimate needs a check that the scene is unchanged since the fitted
  renders; that is in the 1.1 backlog, not here.
* **Peak memory.** While a Cycles job runs, once it has shown progress (before
  that the scene's Render still holds an earlier render's stats),
  ``job_deadline.observe_memory`` keeps the largest memory its render reported
  (``_angelo_jobs.render_stats``); a finished job's entry carries
  ``peak_memory_mb``.
* **Known edges.** A deadline that passes in the milliseconds between a
  still's last sample and the pipeline's final break check, or the timer
  stopping a job Blender has finished but not yet handled, still counts as a
  budget stop (as before the deadline). A bake the deadline stopped is
  reported to Blender's own ``object_bake_complete`` handlers as complete
  (the deadline sets neither ``G.is_break`` nor the job's stop flag); Angelo's
  runner records it as stopped and puts the image back. The estimate's work
  model (megapixels x samples) ignores adaptive sampling's noise threshold,
  so a high sample cap may be overstated; not yet measured. A one-render
  estimate of an animation repays the observed render's setup on every
  frame: overstated when that setup held the session's one-off kernel load,
  or when Cycles' persistent data keeps the scene between frames (review
  round 3 modelled 4.8-8.3x). Either way the estimate is a fact, not a
  limit.
* **Limits.** The main thread can still be held up while the GPU is starved
  (Blender's Metal presentation waits for the GPU), so the interface may be
  slow until the render ends; the render now ends. Running agent renders in a
  separate process, which ends the GPU work too, is the 1.1 design (backlog).
  Found by a 4K, 512-sample island render (1.0.3, 2026-10-22) that waited
  hours in ``build_TLAS`` with the machine's memory exhausted.

Stop asks Blender's job to stop at its next break check, as Escape does
(``_angelo_jobs.stop``); nothing is freed or killed. Measured stop latency on the
fixture scenes (``test_blender_long_jobs``, Apple M-series CPU, two suites in
parallel): animation render 0.3-3.0 s (mid-sample), texture bake 1.3-1.9 s,
physics bake 0.5 s, simulation bake 0.3 s, Alembic export 0.3 s.

What a job changes
------------------

* **Its own settings.** Resolution, samples, image format (with OpenEXR's JPEG
  preview off, a file the job could not name), output path, engine
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
  held handles, except an animation's, whose finished frames are kept where the
  folder went (`Keeping frames after a crash`_).
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
  writes a frame only after it finished it), also after a quit, a crash, a file
  load or a full disk (`Keeping frames after a crash`_). A frame counts as
  finished only when its files are whole (``job_frame_check``). Blender Undo
  does not remove files.
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
``interrupted`` (``file_closed``) and their staging is removed, except an
animation render's, whose finished frames are published once the next document
is open. A job left active by a crash or quit becomes ``interrupted``
(``app_closed``) on the next start; an animation render keeps its finished
frames (`Keeping frames after a crash`_), any other job's marked staging is
removed. When a job ends, the scene caches are
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

Away from the computer
----------------------

A long render or bake usually runs while the person is elsewhere
(``job_desktop``). Both behaviours are on by default, each with a switch in the
Background jobs section of Preferences > AI Providers (``job_keep_awake``,
``job_notifications``, add-on preferences declared in ``job_desktop_prefs``), and both
cost nothing while no job runs: they are called from the job poll, which runs
only while a job is queued or running.

* **The computer stays awake.** From a job's start until no job is queued or
  running, Angelo holds the system awake against idle sleep: a macOS power
  assertion of type ``PreventUserIdleSystemSleep`` (``pmset -g assertions``
  lists it as "Angelo is running a background job"), a Windows power request
  of type ``PowerRequestSystemRequired`` (``powercfg /requests``). The display
  may still sleep, and a sleep the person asks for (a closed lid, the Sleep
  command) still happens. The assertion ends with the process, so a crash never
  leaves the computer awake. The agent's looks (``PREVIEW``) never hold it; a
  job queued behind a look does. Linux: nothing is held. Stock Blender 5.2 holds
  no assertion during a render or bake (none of these calls is in its source).
* **The end of a long job is announced.** A job that ran for 60 s or more
  (``NOTIFY_AFTER_SECONDS``) and ended on its own (done, failed, or stopped by
  its time budget: an allowlist, ``job_desktop.ANNOUNCED``) posts the operating
  system's notification: "<what the job did> finished/failed/stopped at its time
  budget" and "Took <duration>", with the workspace-relative output when one was
  published. It never carries a job's reason or note (a failed publication's
  reason can name a local path). On macOS it is Notification Center, under
  Angelo's name and icon, shown also while Angelo is in front (the person may be
  on another display or at a locked screen). A cancel by the person or the
  agent, a file load or a crash is not announced. The text is interface text,
  translated at display time, given to the system in reading order (the Arabic
  catalogue is stored in visual order; ``for_the_system``), and never sent to a
  model (the jobs block tells the model).
* **Permission and fallbacks.** macOS asks once ever whether Angelo may post
  notifications: when the first job of a session starts, while the person is at
  the computer; the permission is read again at every job start, so a change in
  System Settings counts for the next job. When notifications are not allowed,
  unavailable (an ad-hoc signed development build, a process outside an
  application bundle) or unsupported (Windows, Linux), Angelo asks for attention
  instead: the Dock icon bounces once, or the Windows taskbar button flashes
  until Angelo comes to the front.
* **Never in an automation run** (``app_notices.automation_off``): no question,
  notification or attention, unless ``ANGELO_ENABLE_JOB_NOTIFICATIONS=1`` (the
  suite that proves them). Keeping the computer awake still applies. A
  benchmark launcher that sets neither automation variable is a real user's
  launch: the question appears there once per Mac, on the first job.
* **Windows stays off until verified there** (``desktop_os.WINDOWS_VERIFIED``):
  without it, keep-awake and the taskbar flash answer "unsupported" on Windows
  unless ``ANGELO_WINDOWS_JOB_DESKTOP=1`` is set. Verification on a Windows build
  (or the bundled Python of a released Windows app with this checkout's add-on
  on ``sys.path``), with that variable set:

  1. ``KeepAwake().hold("Angelo check")`` returns True; ``powercfg /requests``
     (an administrator prompt) lists the app's process under SYSTEM with that
     reason; after ``release()`` it is gone, and gone after the process ends
     without a release (kill it while held).
  2. With Angelo's window behind another window, ``flash_taskbar()`` returns
     "flashed" and the taskbar button flashes until Angelo is brought to the
     front; with Angelo in front it returns "foreground" and nothing flashes.
  3. ``test_native_job_desktop`` passes on Windows (the structure layouts and the
     opt-in gate), and ``test_blender_job_desktop`` passes with the variable set
     (``held_while_running`` reads the hold itself on Windows).

  Then set ``WINDOWS_VERIFIED = True`` and drop the variable from these steps.
* Every call is non-blocking and never raises into the job poll or a job's end.
  ``test_native_job_desktop`` pins the policy, the text, the fallbacks, the
  Windows structure layouts and (on macOS) the real assertion read back from
  ``pmset``.

Fluid bakes
-----------

``BAKE_SIMULATION`` whose ``target`` is a Mantaflow domain (smoke, fire or
liquid) bakes it as Blender's own job (``job_fluid``). Mantaflow is compiled into
the release builds (``WITH_MOD_FLUID``) and out of the development build.

* **Why a job.** Blender's Bake button invokes ``fluid.bake_all`` and the bake runs
  as a window-manager job (``WM_JOB_TYPE_OBJECT_SIM_FLUID``, owned by the Scene)
  with the interface locked; a script's ``bpy.ops.fluid.bake_all()`` (the
  operator's ``exec``) bakes synchronously on the main thread: the app does not
  redraw and nothing stops it until the last frame.
* **Stopping.** The fluid job checks only ``G.is_break`` (Escape, Pause Bake),
  never its own stop flag, so the stock status bar's job Cancel does not stop it.
  ``_angelo_jobs.stop`` sets ``G.is_break`` for the ``FLUID`` type, as for the
  texture bake.
* **Always to disk, in the job's folder.** A domain caches to the directory it
  names, so ``output`` (a new workspace folder) is required and the file must be
  saved with the chat's workspace as its folder, as for a disk-cached simulation
  bake. While it runs the domain's directory is ``<staging>/<modifier>``. When a
  file exists for every frame of every cache the domain writes (data, and noise,
  mesh, secondary particles or guiding when enabled; any name Mantaflow itself
  accepts when it reads a frame, ``job_fluid_plan.missing``) the folder is
  published and the domain reads it by ``//<output>/<modifier>``. A domain in
  Replay (Blender's default, which simulates during playback and is not a bake)
  becomes All, so the baked cache is what plays; the approval says so. A domain a
  script left without a cache folder (Blender's Replay would write its frames
  beside the app's working directory) gets the published one, and the approval
  says that too.
* **The domain's state, never through RNA.** Setting a domain's cache type through
  RNA frees its cache, deleting the files in whatever directory it names then
  (``rna_Fluid_domain_data_reset``), and its baked flags and pause frames cannot be
  set from Python. The job reads and writes them directly
  (``_angelo_jobs.fluid_state`` / ``fluid_state_set``, which also frees the solver
  in memory, never a file, and re-evaluates the domain as a side effect: a user-edit
  tag would invalidate its point cache, and Blender frees an invalidated Replay
  cache at the start frame), keeps the person's state on the domain while it runs
  (``_angelo_fluid_restore``, with the .blend it was relative to; a file saved
  meanwhile is put back when it is next opened, a malformed tag is dropped) and
  puts it back exactly on every outcome but a finished bake, a failed publication
  included, with the scene frame a stopped bake leaves. Nothing of the person's
  own cache is written or deleted: after a cancel it is still on disk and read;
  after a finished bake it stays on disk, unreferenced, and the approval says so.
* **Before it starts** (and again when a queued job starts): a domain of this
  file, evaluated in the viewport, with no fluid bake already running in the
  scene, a size on every axis (its mesh times its scale, as Blender lays its grid)
  and at least one flow that adds fluid of its kind (inflow or geometry, enabled in
  the viewport, in the domain's flow collection when it names one; flows outside
  the domain's bounds are named); a folder name Mantaflow would not rewrite (it
  replaces ``< > : " | ? *`` and control characters, and on Windows reserved names,
  before writing: ``BLI_path_make_safe``; a ``\`` in a POSIX name is refused too)
  and a cache path of at most 700 bytes (Blender cuts it at 768); frames within
  the job bounds; no more files than a publication walks (20,000 less a margin;
  per frame a config file, one file per grid cache with OpenVDB or up to one a grid
  with Uni, and a liquid mesh's file plus its speed vectors); the memory its grid
  needs (an upper-bound estimate from its cells, noise and particles) within the
  computer's; at least 2 GiB free. The approval
  states the grid, the voxel size, frames, caches, the memory and disk estimates
  and the free space.
* **While it runs** the bytes per frame actually written are measured every 5 s;
  when the frames left would not fit with 2 GiB to spare, the job stops itself and
  fails with that reason (``disk_needed_gib``, ``disk_free_gib``), publishing
  nothing and putting the domain back; it stops the same way when the files
  written approach the publication bound.
* **Whole means whole.** A non-empty file for every frame of every cache, and no
  error reported by Blender's bake (``fluid_state``'s ``error``, then
  ``blender_error`` in the receipt). Escape or Pause Bake in Blender ends the job
  as cancelled ("stopped in Blender"), told apart from a failure by the pause
  frame. ``G.is_break`` is global: a stop also stops any other job of the person's
  that checks it, as Escape does in Blender.
* **What the agent reads.** The receipt has ``objects``, ``caches`` and one output
  ``{file: <modifier folder>, cache: <modifier>, name: <domain>, frames}``; a
  failed bake's ``missing`` names the frames per cache. A physics bake refused for
  having no point caches names the scene's fluid domains and this kind.

Validation: ``test_blender_release_features`` bakes a smoke and a liquid domain
headless and reads both back from their disk caches. ``test_blender_mantaflow``
(GUI) bakes smoke the way the Bake button does and checks the main thread, the
progress, the files, the frames, the viewport and Pause Bake. ``test_blender_fluid_jobs``
(GUI) covers the job: refusals, publication and the relative path (also after
reopening), Replay to All, a cancel through ``_angelo_jobs.stop`` within the stop
bound with the state and an earlier cache put back exactly, a file saved mid-bake,
and the disk guard. ``test_native_job_fluid_plan`` checks the grid against
Blender's formula, the estimates, the frame names, the file bound, the disk
outlook, the path rule and the approval text.

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
``frame_<frame><view suffix>`` for the image (or the job's frame name, `Deliverables`_) and, for a node, a folder named
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

Keeping frames after a crash
----------------------------

Stock Blender writes each frame into the output folder as it finishes, so a
quit or crash keeps every finished frame. A job writes into its hidden staging
folder and publishes at the end, and used to remove that folder at the next
start: a crash at hour 9 of a 10-hour render lost every frame (VFX audit A1,
2026-10-21). Now nothing a render finished is removed by a quit, a crash, a file
load, a full disk or a failure while publishing (``job_recovery``):

* **What is saved.** When an animation render starts, its record keeps, locally
  and never for a model, ``detail["recovery"]``: the workspace root, the output,
  the plan of files (``job_render_outputs``), the frame range, and the .blend
  file and scene it renders.
* **At the next start** (``job_ui._reconcile``), or once the next document is
  open after a file load, each animation render a previous run left with a
  staging folder is reopened through held, no-follow handles: only the folder
  named for the job and carrying its marker, reached without a link. A frame is
  kept when every file of the render's own image is there, planned, a regular
  file with no other name, and whole; its File Output files go with it. Kept
  frames are published as the job's output, under its own name or the next free
  one, never over anything; the record becomes ``interrupted`` with ``kept``
  (``12/120 frames``), ``frames_kept``, ``files``, ``torn`` and a note. The
  staging folder is removed only after every kept file is published.
* **Whole, not just present.** Blender writes each frame straight to its final
  name (no temporary name), and a failed write only stops the animation, so a
  crash or a full disk mid-write leaves a torn file under the frame's real name.
  ``job_frame_check`` reads it: PNG must end with IEND, JPEG with EOI, OpenEXR
  must have every chunk offset filled in (OpenEXR writes them as zeros and fills
  them when it closes the file) and its last chunk inside the file; scanline and
  tiled, single and multipart (multilayer, multi-view) files are read. Another
  format, or an OpenEXR header larger than the 1 MiB read of it, cannot be
  checked; the frame being written when the render ended early (the last
  finished one) is then dropped, since only it can be torn. The same
  check runs when a render ends normally, so a frame a full disk tore is never
  published as whole (``failed``, "Blender stopped while writing a frame").
* **Safe to repeat.** The name a publication goes to is saved on the record
  before its folder is made (``recover_into``; a save that fails stops it before
  anything is made); a crash or a full disk during it leaves what was linked,
  keeps the staging folder, and the next start links the rest into the same
  folder (a folder made just before a crash is reused; a name already there is
  the same file or is left as it is). The staging folder is removed only after
  every kept file is published, its marker last, so a crash midway still leaves
  a folder the next start knows as the job's. Starting twice changes nothing.
* **A render's own end never removes a finished frame either.** It publishes
  only the plan's whole frames, whatever else the folder holds: a stray file
  (Finder's ``.DS_Store``, an AppleDouble ``._`` file) is left out and named in
  ``unexpected``, and no longer fails the render; there is no walk budget, so a
  long render with File Output items publishes whole. A file-system error, every
  name taken, a finish that raises, or a folder on the way moved or replaced by a
  link keeps the staging folder (``failed``, "kept for the next start"); a
  folder moved inside the workspace is followed (the held folder's current path)
  and its frames are published where it went, at once. Only a still keeps the
  old rule that an unplanned file publishes nothing.
* **Not reachable is not gone.** When the folders down to the staging folder
  cannot be walked at a start (a drive not connected, a folder moved, a link on
  the way, a marker or an earlier attempt's folder that can't be read), the
  record keeps it and says so; a later start publishes it.
* **Unreadable is not missing or torn** (release audit of 1.0.4/1.0.5: a
  transient EIO or EACCES on the staging folder's listing, a frame's metadata or
  its open made recovery find no frame and remove the folder). Every check raises
  ``OSError`` for anything but a name that is not there or a link
  (``job_recovery_read``); recovery and a render's own end then publish and
  remove nothing, keep the staging folder and say why (``UNREAD``: "some of its
  files couldn't be read now"); the next start tries again. A
  folder without the job's marker, or one that is gone while its folder is
  there, is never touched. A record on disk is data: its frame range and plan
  are bounded like a job's, and it never publishes into a staging folder.
* **Records from 1.0.3** have no saved plan: their plain ``frame_<n>`` frames are
  kept; a folder holding anything else (views, File Output items) is left
  exactly as it was, and the receipt says so.
* **Rendering the rest.** The card of a render whose output lacks frames shows
  "Angelo closed during the render · 12 of 120 frames rendered" and a "Render
  the 108 missing frames" button. The person's click is the approval (as F12
  is): it starts a render job of the first to the last missing frame with the
  original request's format, resolution, samples and time budget, queued behind
  a running job, and publishes into the same folder, linking only names that are
  free (``job_recovery.publish``). It is disabled, with the reason on hover,
  while the file or scene that rendered it is not the one open, for an unsaved
  file, and while the rest is rendering; it is refused, with the reason on the
  card, when the scene would now write other files (views or File Output nodes
  changed). The original card then shows the new job; its jobs-block entry has
  ``resumes`` and, when it ends, ``sequence`` (``120/120 frames``). The agent
  has no resume field yet (it would change the tool schema).

``test_native_job_recovery.py`` pins each rule on real files, on POSIX and on the
Windows handle backend; ``test_blender_long_jobs`` reconciles a crash in the
running app.

Deliverables
------------

A compositor or a pipeline expects the files stock Blender writes for the scene:
its format with its codec and depth, shot names with the frame padding they ask
for, and only the frames on the scene's frame step (VFX audit A2). A job writes
exactly those, still only inside its staging folder and published as a new
folder:

* **The scene's own format** (``format: SCENE``). The job leaves
  ``render.image_settings`` as it is, so whatever the agent set there with
  ``property_set`` (``media_type`` MULTI_LAYER_IMAGE for a multilayer
  OpenEXR, whose only ``file_format`` is OPEN_EXR_MULTILAYER; ``media_type``
  IMAGE then ``file_format`` for the other stills; ``exr_codec``,
  ``color_depth``, TIFF's ``tiff_codec`` ...) is what is written; the approval
  lists those settings. A scene set to write a video is refused, since a job
  writes image files. A multilayer OpenEXR holds every pass of every rendered
  view layer (which passes, A3, is the view layers' business). Blender 5.2
  turns Stereo 3D views into Multi-View when the multilayer media type is
  chosen, and the job writes them so, as F12 does; a scene that still holds
  that pair (Blender would write no file at all) is refused. A still in the
  scene's format is named with that format's suffix.
* **Frame names** (``name``, animations): a stem and the frame number's ``#``
  run, at its end (``sh010_v003.####`` writes ``sh010_v003.0001.exr``; ``##``
  pads to two digits and Blender writes more when a frame needs them). With
  ``format: SCENE`` and no ``name``, the scene's own file name is used when it
  is a plain stem (no folders, braces or variables), with ``####`` added when it
  has none, as Blender does; otherwise ``frame_``. Blender's own answer for each
  name is compared with the plan before the render starts.
* **Frame step.** The scene's ``frame_step`` (Blender evaluates every frame and
  writes only those on the step); the frame bound counts the frames written.
  Progress, the check of what was written, keeping frames after a crash,
  rendering the rest and the outputs the agent reads all follow the step and the
  padding (``job_render_outputs.frame_numbers``, ``job_store.frame_run``).
* **Names that can't be files.** A name whose start (before its first dot) is a
  Windows device name for some frame number (``CON``, ``COM#``), or whose frame
  number completes an image extension Blender recognizes and replaces
  (``x.jp#`` is ``x.jp2`` at frame 2), is refused; the scene's own file name is
  then not used. A File Output node's folder never starts with the frame name's
  stem. A stereo or multi-view render names each view's file as the render
  writes it: the view's suffix before the extension of the whole name
  (``st010.v1.01_L.png``).
* **Approved means unchanged.** A queued job whose scene's frame step or own
  image settings (format, depth, colour mode, that format's codec) changed since
  its approval is refused when it starts; a resume compares the same settings.
* **Whole frames.** ``job_frame_check`` also reads TIFF: its first directory,
  which libtiff links from the header when it closes the file, and every strip
  or tile must end inside the file.
* **Schema cost.** One ``format`` item and one field: Claude's and Codex's
  instructions carry the job tool's argument summary on every turn (+21 B each).
  The formats themselves are scene settings ``property_set`` already writes, so
  they cost nothing per turn.

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
with ``#`` for the frame, as many as its padding. A plain still or animation reports no ``outputs``:
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
* ``test_native_job_recovery`` (host): frame completeness for PNG, JPEG and
  OpenEXR (single, multipart, unfilled offset tables, cut chunks), finished
  frames kept after a crash and the torn one dropped, a taken name, a full disk
  during recovery and the next start finishing into the same folder, a crash
  after publication, File Output frames, nothing finished, a link in staging, an
  unmarked folder, 1.0.3 records, rendering the rest into the same folder and
  its refusals, the card text and buttons, the jobs-block entry, and a failed
  finish keeping its frames.
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
  Claude's loop and on Codex's, within the correction budgets, and is an answer
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
* **Listing never runs in the app.** Listing initializes GPU drivers while
  holding the interpreter lock, and can end the process (why Blender does not
  list at startup). A short child process of the same app
  (``--background --factory-startup``) lists them and prints the result; a
  driver that crashes or hangs ends only the child (stopped after 120 s). The
  main thread then adds Cycles' device entries as Cycles would (every GPU
  enabled, the CPU not) and selects the first backend with a device: OptiX,
  CUDA, HIP, Metal, oneAPI. Cycles initializes that backend itself when it
  first renders, on the render thread for a render job.
* **Saved, so later starts begin on the GPU.** The listing is saved for this
  build (the app's resolved path, version and build hash) in
  ``render-device.json``, for seven days (a listing that found no GPU: one
  day, so a GPU added or plugged back in reaches background runs; a windowed
  start sees it at once). Every later start applies it when Cycles
  registers, before any window draws or anything renders: a few preference
  writes, no child. A windowed start lists once per launch to refresh it (at
  once when nothing is saved, as on a first launch, otherwise a second after
  startup); its writes wait while a render or bake job runs (that job reads the
  preferences on its own thread). A background run lists only when it renders
  and nothing is saved; before a GPU render from the saved listing it checks in
  the process that a device it set up (saved, or kept in saved preferences)
  still exists (the driver initialization Cycles is about to do anyway) and
  lists again when none does. It never probes other backends in the process
  (that is the crash risk the child exists for). A refresh that
  finds Angelo's GPU gone keeps the backend (Cycles renders on the CPU and the
  facts say why; a GPU that comes back is used again): Angelo never sets
  None, so a None in the preferences is always someone's. A listing that
  fails keeps a setup already in place; with none, the failure is saved:
  background runs then skip listing for 24 hours (a driver that crashes or
  hangs would otherwise cost every render) and render on the CPU with the
  reason, while a windowed start still lists once in the background (renders
  never wait for it). A crash or a hang past 120 s counts at once; a
  background render's 30 s wait running out counts on the second time in a
  row; a cancelled render never counts. Each process writes the file through its own
  temporary name, so parallel background renders cannot corrupt it.
* **Every Cycles session waits for the decision**
  (``render_device_session``). Cycles reads the compute device when it creates
  or resets a session, so its add-on calls the gate at exactly those points:
  ``register`` (windowed starts, with or without the agent add-on),
  ``update`` (final renders and bakes, on every path: F12, render and bake
  jobs, ``bpy.ops.render.render``, ``blender -b -f``) and ``view_update`` when
  a Rendered viewport makes its session. Cycles imports only
  ``angelo_render_device`` (``scripts/modules``; standard library and ``bpy``),
  never the agent package: Cycles' start-up must not pay for or reorder the
  agent's imports (a native suite's checkout-first import met the bundled
  agent Cycles had loaded, 2026-10-22). It reads the agent's state-folder
  override through the agent's own ``product_env.py`` loaded from its file
  under a private name. A background start imports nothing of it at
  ``register``: its first render or bake decides (a run that never renders
  pays nothing). ``test_blender_cycles_device_default`` pins that a
  background Cycles render leaves ``angelo_agent`` unimported. On the main thread the gate
  decides, and a final render that finds nothing decided (a build's first
  launch, a background run with nothing saved) waits up to 30 s for the
  listing; a background run then stops it and renders on the CPU. On a render
  or bake job's thread the gate never writes preferences: while nothing is
  decided it waits, as long, for the main thread's timer to apply the running
  listing; a refresh of a decided setup never holds a render. A cancelled
  render (the engine's ``test_break``: Esc, a file load, quitting) stops
  waiting at once. Material previews and the viewport never wait; a viewport
  made before the setup changed is made again on its next update. Once
  decided, the gate is a few preference reads (0.5 ms measured on a T4 VM).
  The listing child itself (``ANGELO_GPU_LISTING_CHILD`` in its environment)
  never sets devices up.
* **The person's choice wins.** A compute backend someone set
  (``is_property_set``: Preferences > System, or Import Blender Settings),
  None included, is left alone and never listed, and so is Cycles'
  ``--cycles-device`` option (parsed as Cycles parses it: after ``--``,
  abbreviations included). Blender's untouched default (None, or Metal on
  Apple Silicon) is not a choice. A backend Angelo set and saved preferences
  kept stays Angelo's; its listing refreshes which devices exist. Load Factory
  Preferences decides again (a persistent handler: reading a file keeps it).
* **Persisted the Angelo way.** Angelo never saves Blender's preferences for
  this (their dirty flag is restored). What it applied and what it listed are
  ``render-device.json`` in the state folder, compared at each start to tell
  Angelo's setup from the person's.
* **Falls back to the CPU with a reason.** No supported GPU, or a listing child
  that crashed, hung or could not start, leaves Cycles on the CPU and says
  why: in the console, to the agent in ``device_why``, and on the job's card.
* **The job card says the device.** A Cycles render or texture-bake job records
  the device its own scene's Cycles session used, read as the session began
  (``render_device.shown``), not the one measured at admission, and corrected
  to the CPU when the launch's listing, reporting later, no longer has that GPU
  (``render_device.corrected``): the card shows
  "On the GPU: Tesla T4 (OptiX)" or "On the CPU: <reason>", translated, the
  reason in the warning colour unless the person chose the CPU (the scene, a
  None they set in Preferences, the command line). The
  record's ``device`` and ``device_why`` (what the model reads) are updated to
  the same device; the card's ``device_shown`` never reaches a model.
* ``test_blender_cycles_device_default`` (background native suite) pins the
  scene default (a saved CPU stays CPU, a scene that never chose reads GPU),
  the child listing in the real binary, the saved listing applied with no
  child, and a background Cycles render passing the gate.
  ``test_native_render_device`` and ``test_native_render_device_session`` pin
  the decisions, every start mode, the card line and the break-it review's
  reproductions (2026-10-21).
* **What a configured GPU costs.** Once a GPU backend is set, Cycles
  initializes it in each process that renders, also for a scene set to the
  CPU (Cycles looks up GPU denoising devices): measured on a Tesla T4 VM, a
  CPU scene's render started 0.33 s later in a fresh background process than
  with no GPU set (stock Blender with a GPU chosen does the same). A GPU
  render pays it anyway.

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
* Frames are kept whole only for formats ``job_frame_check`` reads (PNG, JPEG,
  OpenEXR); a File Output item in another format is trusted when its frame is
  not the last finished one, and the last finished frame of a render that ended
  early is dropped when one of its files is in such a format, even if whole (a
  resume renders it again). A file whose bytes were zeroed but whose structure is
  intact (only a power loss can leave one) is not detected.
* A staging folder moved outside the workspace, or out of reach for good, stays
  hidden where it is; the card says so.
* Between a file load's start and Blender stopping the render, the staging
  folder is no longer held, so a link swapped in then is not found.
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
* **Toolset**: the job tools last changed in toolset 41 (fluid bakes). A change to their
  schemas or guidance needs the next ``routing.TOOLSET_VERSION``.
