.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

Shared UI components (ui_kit)
*****************************

Angelo draws its own surfaces (cards, the chat, Preferences pages) in one visual language: card_kit's
navy-charcoal and brand teal, amber for "needs you" and paid work, coral for errors and destructive actions.
Every colour, size and radius comes from ``ui_tokens.py``. The shared controls are drawn once, in the
``ui_kit*`` modules, so every surface gets the same states, scaling and right-to-left behaviour.

Components
==========

========================  ===================================================================================
Module                    Components
========================  ===================================================================================
``ui_kit.py``             State model (``State``, ``state_for``), focus ring, outline, check mark, text that
                          keeps blending on, the plan dispatcher and ``register_hits`` for the chat canvas.
``ui_kit_status.py``      Status chip (running, done, failed, needs you, preview, paid), inline notice (info,
                          warn, error, success; actions; a collapsed "Details" for technical text), code block.
``ui_kit_controls.py``    Toggle switch (and settings row), segmented control (optional colour swatches): one
                          selection mark, the chosen segment filled flush in the track whose border is the
                          only one; every swatch the same rounded square with the same shadow rim, so a
                          dark and a light sample differ only in colour (the setup card's Look and the
                          Providers sheet use it through ``b.segmented``).
``ui_kit_field.py``       Text field, plain and secret: placeholder, caret, error line, mask, reveal, paste.
``ui_kit_select.py``      Dropdown: the closed control and an anchored, keyboard-navigable popover list.
``ui_kit_rows.py``        List row, letter tile, provider tile, the auto-hiding draggable scrollbar.
``ui_kit_builder.py``     ``card_kit.Builder`` methods for all of the above (a mixin).
``card_draw.py``          Button styles, including ``destructive`` (coral).
``canvas_controls.py``    The edge-clamped tooltip (``tip_rect``): flush with its control near an edge, inside
                          the region, below the control when there is no room above.
========================  ===================================================================================

Each module starts with its API reference. Every component is a stateless draw function taking a view (the
chat canvas module or ``card_host``'s view), a rectangle in region pixels, the UI scale and the pointer and
focus state. Hit testing stays with the host.

Using them
==========

* **In a card** (``card_host`` cards, Preferences pages built with ``card_kit.Builder``): call the Builder
  methods (``b.notice(...)``, ``b.toggle(...)``, ``b.text_field(...)``, ...). They lay the component out,
  register its hits and Tab stops, and the plan draws it with the frame's hover, pressed and focus state. An
  open dropdown is an overlay: drawn last and hit first. A card that edits text keeps a ``FieldState`` and
  implements ``key_event(event, host)``; ``card_host`` hands it each key press before its own Tab, Return and
  Escape handling.
* **On the chat canvas**: draw with the component's function and ``ui_kit.state_for(state, action, value)``,
  and add its hits with ``ui_kit.register_hits(state, hits)``: ``canvas_controls`` still tracks hover, press
  and Tab focus for them but leaves their look to the component.
* Strings reach the kit already translated (``ui_text.iface_``); the kit translates its own few labels
  (chip names, Copy, Copied, Details, Paste) at display time.

Windows and areas
=================

Every Angelo walk over Blender's windows and their areas goes through ``angelo_live_windows``
(``scripts/modules``; the agent imports it as ``angelo_agent.live_windows``): ``windows(manager)``,
``first``, ``find(manager, pointer)``, ``areas(window)``, ``screen_areas(screen)`` and ``window_areas``. A
window has no screen for a moment while an operator creates or closes it; the helper never yields one, so a
draw callback, timer or modal handler cannot raise there and lose its frame. ``temporary=False`` also leaves
out Preferences, the render view and file browsers. Reads that never touch a screen (an interaction guard, a cursor
restore, the windows before a new one opens) use ``every_window``, so a tool running in a window between screens is
never read as idle. A timer step for one window is built with ``deferred(manager, pointer, step)``: it hands
``step`` the live window, waits while that window is between screens (with ``settle=True``, while any window is: a
step that may open a chat or a window), and hands it None once the window is gone. Every wait is bounded by
``WAIT_S`` (``Wait``, fed on every tick; one not fed for ``GAP_S`` is left over and starts anew), so a window that
never gets a screen counts as gone and no work is held back for good. Windows a module tracks by pointer (Studio,
flows) are pruned with ``prune``, which forgets a window only once it is gone or shows something else, and their
toggles leave a window between screens alone. Startup timers wait (bounded, a fresh ``Wait`` per run) for
``settled`` before choosing the main window. A direct ``window.screen.<attr>`` read sits behind a ``screen is None``
check or on a window from ``live_windows``. ``chat_toggle.chat_state`` reads ``every_window`` and answers ``"changing"``, never
``"hidden"``, while the chat is not in the asked window and a window is between screens, so nothing opens a second
chat. The toggle's watch and tool dispatch wait (bounded) for ``settled``, and tools never run while the main window
has no screen (they stay queued until it has one, never sent to another window). That hold alone has no time limit,
by design: a window has no screen only while an operator creates or closes it, and a closed main window stops
matching at once (``main_window`` then falls back to a main window that has one).
``ui.main_window`` stays the window it follows, never a substitute. A timer step for one window built from
``bpy.app.timers`` is a ``deferred`` step. Callers pass the window manager
(``bpy.context`` or the
operator's ``context``), so the helper never imports ``bpy`` and the Console bootstraps and
``angelo_render_device`` (which must not import the agent) use it too. A screenshot mask fails closed when the
window has no editors to mask. ``test_native_live_windows.py`` fails on any direct ``.windows`` or ``.areas``
read in Angelo's own Python (the add-on, ``scripts/modules/angelo_*`` and the Console bootstraps, the startup
layout and ``scripts/angelo``); on a timer step that looks its window up itself (directly, through a module helper,
``partial`` or a method) instead of through ``deferred``; on a deferred step that may open a chat or a window
without ``settle=True`` (or a plain timer that may do so without consulting ``settled`` or ``chat_state``); on a
``chat_state`` call given a live-only window list; on window pointers collected from the live-only list; and on a
``window.screen.<attr>`` read in a function that neither checks the screen nor takes its windows from the helper.

A multi-step job a timer resumes (a generator: the chat dock, Hide, Director's mode switch, the Media Studio and
Generation Flows tab builds) is made with ``timer_jobs.track(job, release)``; ``release`` frees what its driver holds
beyond the job (the layout lock, ``layout_hold``) and touches only Python state. ``timer_jobs`` closes each job still
running, as *aborted*, and calls its release:

- at ``load_post``, once a load has read a file. Not at ``load_pre``: a load that fails (``load_post_fail``: a
  missing or corrupt file) keeps the file and the jobs' timers, and its jobs go on as if nothing happened.
  ``load_post`` fires only for reads that replace the data (a preferences-only read fires no load handler and
  discards no timer). Every job works on the windows' layout: when the load replaced it (the workspaces there at
  ``load_pre`` are gone; unknown counts as replaced) the job closes with ``timer_jobs.leaving()`` True; when it kept
  it (Load UI off, as Angelo's Restore opens a checkpoint) the job closes as after any stop and puts the layout back;
- at ``exit_pre``, before quit.blend, the add-ons' unregister and ``BKE_blender_free``; ``leaving()`` stays True from
  then on, through Python's finalization;
- at the add-on's ``unregister``, the file staying: each cleanup puts the file back as after any stop.

A job whose own step ran the load or the unregister ends, and is released, as soon as that step returns.

A job's cleanup asks ``leaving()`` before it calls anything or reads ``bpy``, so nothing of a file going away is read
or put back; a step asks ``timer_jobs.aborted(job)`` before resuming it, so a closed generator's StopIteration never
reads as a finished job (a half-built layout written as the mode, a half-made tab switched to). At ``load_post`` a
layout lock whose timer the load discarded is released too, a hold that is not a job's included (``layout_hold``).
Left to Python, a suspended job's cleanup ran in the next file after a load, or in interpreter finalization after
Blender had freed its data (1.0.6 RC: SIGSEGV at quit in the tab build's ``finally``); closed at ``load_pre``, a failed
load left half-made tabs and stuck locks (break-it review). ``test_native_timer_jobs_scan.py`` fails on a timer step
that resumes an untracked job (by ``next``, ``send``, ``throw`` or a ``for`` loop, of a closure or module variable) or
does not ask ``aborted`` first; on a driver that holds a layout lock and gives its job no release; and, in a module
that uses ``bpy``, on a cleanup across a ``yield`` that calls anything or reads ``bpy`` before asking ``leaving()``,
or a ``with`` across one.

Right to left
=============

Layout is written left to right and mirrored by ``canvas_rtl``. Code, typed text and secrets read left to
right in every language (their frame and buttons still mirror). Check marks are symbols and never mirror.

Testing
=======

Pure logic (masking, editing keys, layout maths, hits, the scrollbar and the tooltip placement) is host-tested
in ``test_native_ui_kit.py``. ``test_blender_ui_kit_gallery`` shows every component in every state in a real
``card_host`` card, drives it with real events and screenshots it in English, Arabic and a narrow card.
