# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Running the build of Manual's Media Studio and Generation Flows tabs (``studio_workspaces.build``) as a timer job.

The build switches the window through the tabs it makes, so it holds the layout while it runs, starts before
Director's layout at start-up (``startup_build``), and ends cleanly when a load or a quit discards its timer
(``load_post``, ``exit_pre``) or the add-on is unregistered (``stop``). What the build does, and when a later build may
run, is ``studio_workspaces``.
"""

import bpy

from . import live_target, studio_workspaces as tabs, timer_jobs

_job = {"generator": None}   # The running build, while its timer step runs it.
TOOLS_WAIT = 2.0   # Seconds tools wait for a build at most, from the first tick they waited: a turn never hangs on it.
_tools = {"since": None}   # When the GUI pump first held its tools back for the running build.


def run(window, retry=None):
    """Start a build of ``window``'s missing tabs now. The build switches the window through the tabs it makes: it is
    a layout change like a dock or Director's start-up, so it holds ``ui._layout_timer`` and everything that waits
    for a settled layout waits for it too (35f5b544698's final line: suites and the chat read the window mid-build
    and saw a tab's screen). ``retry`` runs once it has stopped part-way and rolled back."""
    from . import ui
    held, home, settle = live_target.window_ref(window), window.workspace.name, [20]   # Never the window itself.

    def step():
        if timer_jobs.aborted(generator):   # A load, a quit or unregister ended it: it holds nothing any more.
            return None
        if _job["generator"] is generator:
            try:
                return next(generator)
            except StopIteration:
                pass
            except Exception as error:   # A tab not built is added on a later load; never a broken timer.
                print("Angelo: Media Studio / Generation Flows tabs not added:", error)
                generator.close()
            _job["generator"] = None
        elif tabs._holding is not step:   # Ended early (a load, a quit, ``stop``): it holds nothing any more.
            return None
        # The build queued the switch back home: hold the layout until it shows (at most 20 passes).
        target = held()   # This window, live (a screen-less one is between screens: not home yet), else None.
        settle[0] -= 1
        if (target is not None and (target.screen is None or target.workspace.name != home)
                and home in bpy.data.workspaces and settle[0]):
            return 0.0
        tabs.restore_modes()   # The window shows home: each workspace the build passed gets its own mode back.
        _release(step)
        if tabs.missing() and retry is not None:
            retry()
        return None
    # Tracked: a load, a quit or unregister closes it while Blender's data exists, never Python's finalization, and
    # releases the layout it holds (timer_jobs).
    generator = _job["generator"] = timer_jobs.track(tabs.build(window, tabs.missing()), lambda: _release(step))
    tabs._holding = ui._layout_timer = step
    bpy.app.timers.register(step, first_interval=0.0)


def _release(step):
    from . import ui
    if ui._layout_timer is step:
        ui._layout_timer = None
    if tabs._holding is step:
        tabs._holding = None
    tabs._startup["pending"] = False   # Done or rolled back: the cards go on; a retry waits for a quiet moment.


def startup_build(window):
    """Angelo's default layout calls this before Director's start-up: True while the session's first build runs
    (started here when it is owed), so Director's layout and its chat come after it. Built first, the switches it
    needs happen before anything of Angelo's is on the window: nothing that waits for the chat or a settled layout
    (a person, an add-on, a suite) ever sees the window leave it (1.0.5 RC: built after Director had settled, it
    switched the window ~2.5 s after start, and seven unchanged suites lost the chat). ``ensure`` retries a stop."""
    if tabs._holding is not None or tabs._building:
        return True
    if (not tabs.startup_build_pending() or window.screen is None or window.screen.is_temporary
            or not tabs.startup_ready(window)):
        return False
    run(window)
    return True


def stop(leaving=False):
    """End the running build now and release the layout it held. Left held, the layout never settled again: Director,
    the dock and every card waited for it forever (1.0.5 RC, fresh_chat_document: a document opened during the build
    left the window on a half-made Generation Flows tab). ``leaving`` (a load, a quit: ``load_post``, ``exit_pre``):
    nothing is rolled back into, or read from, the file that is going away. Without it (the add-on unregistered, the
    file stays) the build rolls back as any stopped build. Each workspace it passed gets its own mode back while that
    file's data exists (a quit's data lives until after quit.blend is written; an ID write applies at once). Only this
    build's own state is cleared: + Add Workspace's build (``add_tab``) is ended by ``timer_jobs`` and clears its
    own."""
    generator, _job["generator"] = _job["generator"], None
    if generator is not None:
        timer_jobs.close(generator, leaving)
    if tabs._holding is not None:   # Also a build that finished and waits for the window to show home.
        _release(tabs._holding)
    tabs.restore_modes()


@bpy.app.handlers.persistent
def load_post(_):
    """A load read a file and discarded the build's timer (non-persistent): end the build. A failed load
    (``load_post_fail``) keeps the file and the timer, and the build goes on (break-it review of 5ca5f33c94f: ended at
    ``load_pre``, a failed load left a half-made tab and the window on it). A load that kept the layout (Load UI off,
    as Restore opens a checkpoint) kept the workspaces too: the build rolls back and they get their modes back."""
    kept = timer_jobs.layout_kept()
    if not kept:
        tabs._kept_modes.clear()   # Those workspaces belonged to the layout that is gone.
    stop(leaving=not kept)


@bpy.app.handlers.persistent
def exit_pre(*_args):
    """Quitting: end it before Blender frees its data, never in Python's finalization (``timer_jobs``). The data still
    exists and quit.blend is written from it: modes go back; a half-made tab stays marked unfinished
    (``studio_workspaces.UNFINISHED``), since a rollback's operators would apply only after the file is written."""
    stop(leaving=True)


HANDLERS = (("load_post", load_post), ("exit_pre", exit_pre))


def register():
    for name, handler in HANDLERS:
        if handler not in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).insert(0, handler)


def unregister():
    for name, handler in HANDLERS:
        if handler in getattr(bpy.app.handlers, name):
            getattr(bpy.app.handlers, name).remove(handler)
    stop()


def tools_wait():
    """Whether the GUI pump holds its tools (agent turns, connected apps' calls, approved script files) this tick:
    while the build runs, as it closes the extra areas of the tab it makes, and a script changing one of them left
    notifiers visiting a freed area (1.0.5 RC, asset_browser_states: a crash in wm_event_do_notifiers right after
    ED_area_do_listen). Held, never dropped or reordered: the pump just runs them on a later tick. At most
    ``TOOLS_WAIT`` seconds from the first tick held, then they go whatever the build does."""
    import time
    if not (tabs._building or tabs._holding is not None):
        _tools["since"] = None
        return False
    now = time.monotonic()
    if _tools["since"] is None:
        _tools["since"] = now
    return now - _tools["since"] < TOOLS_WAIT
