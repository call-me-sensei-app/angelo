# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every Cycles session starts after Angelo's GPU setup is decided, in every kind of start.

Contract: doc/angelo/long_jobs.rst, "Render device".

Cycles reads the compute device from the preferences when it creates or resets a session
(``BlenderSession``, ``blender_device_info``). Its add-on calls in here at exactly those points, so no render
path can start before the device is decided:

* ``early`` from Cycles' ``register``: windowed and background starts alike, whether or not the agent add-on
  is enabled. It applies this build's saved listing at once (``render_device.setup``), so a start after the
  first one is on the GPU before anything renders. A windowed start then lists once to refresh it, at once
  when nothing was saved (a first launch) and otherwise a second later.
* ``before_session`` from Cycles' ``update`` (final renders and bakes: F12, render and bake jobs,
  ``bpy.ops.render.render``, ``blender -b -f``) and ``view_update`` when a viewport session is created. On the
  main thread it decides; a final render that finds nothing decided yet (the first launch of a build, a
  background run before any listing) waits up to ``WAIT`` seconds for the listing child. On another thread (a
  render or bake job) it never writes preferences: while nothing is decided it waits, as long, for the main
  thread's timer to apply the running listing (a refresh of a decided setup never holds a render). A render
  that is cancelled stops waiting at once. Material previews and the viewport never wait. Once decided, it
  costs a few preference reads.
* A background run that set the GPU up from the saved listing checks, before a GPU render, that a device it
  set up still exists (``render_device.stale``: the driver initialization Cycles is about to do anyway);
  when none does, it lists again as on a first start.

A final session's device (``render_device.facts`` and ``shown``, read once decided) is kept with its scene and a
count, so a job reports the device its own scene's session rendered on (``used_since``). Never runs in the
listing child itself.
"""

import os
import threading
import time

from . import device as render_device
from . import facts as render_device_facts

STATE_FOLDER = "angelo-agent"   # angelo_agent.state_paths.STATE_FOLDER (a test pins them equal).

WAIT = 30.0     # Seconds a final render waits for a first listing; then Cycles renders on what is set up.
STEP = 0.05     # Seconds between checks while a job thread waits for the main thread.
_count = 0      # Final Cycles sessions started in this process.
_used = None    # (count, scene session_uid, facts, shown) of the latest one.
_clock = time.monotonic


def _child():
    return os.environ.get(render_device.CHILD) == "1"


def _main():
    return threading.current_thread() is threading.main_thread()


def _state_override():
    """The agent's state folder override (``ANGELO_STATE_DIR``, or a deprecated spelling): read by the agent's own
    ``product_env.py``, loaded from its file under a private name, so the agent package is never imported."""
    import os
    if not any(name.endswith("_STATE_DIR") and value for name, value in os.environ.items()):
        return None   # No spelling of the override is set: nothing to read (the common case, and the cheap one).
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "addons_core", "angelo_agent", "product_env.py")
    try:
        spec = importlib.util.spec_from_file_location("_angelo_render_device_product_env", path)
        product_env = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(product_env)
        return product_env.lookup("STATE_DIR")[0]
    except (OSError, ImportError, AttributeError):
        return os.environ.get("ANGELO_STATE_DIR") or None


def _attach():
    if render_device._path is None:
        override = _state_override()
        if override:
            render_device.attach(override)
        else:
            import bpy
            # Not created here: the folder is made only when something is written (a run that only reads makes none).
            render_device.attach(bpy.utils.user_resource("CONFIG", path=STATE_FOLDER, create=False))


def early():
    """Cycles' ``register``: decide from what is known now; a windowed start also lists (once)."""
    if _child():
        return
    import bpy
    try:
        _attach()
        render_device.setup(listing=False)
        if bpy.app.background:
            return   # A background run lists only when a render needs it (before_session).
        if render_device.status()["state"] == "not_set_up":
            render_device.setup()   # Nothing saved for this build: list at once.
        elif not bpy.app.timers.is_registered(_startup):
            bpy.app.timers.register(_startup, first_interval=1.0, persistent=True)   # Refresh the saved listing.
    except Exception as error:
        print("Angelo: Cycles GPU setup failed (%s: %s); Cycles renders on what is set up" % (
            type(error).__name__, error))


def _startup():
    try:
        render_device.setup()
    except Exception as error:
        print("Angelo: Cycles GPU setup failed (%s: %s)" % (type(error).__name__, error))
    return None


def before_session(scene=None, *, viewport=False, preview=False, stop=None):
    """Cycles, before a session reads its device: settle the setup (see the module text). ``scene``: the
    rendered scene, for the device a final session uses; ``stop``: the engine's ``test_break`` (a cancelled
    render, or Blender ending its jobs, stops waiting)."""
    global _count, _used
    if preview or _child():
        return
    try:
        if _main():
            _settle(scene, wait=not viewport, stop=stop)
        else:
            _await_main(stop)
        if viewport:
            return
        _count += 1
        found = None if scene is None else render_device_facts._facts(scene, None)   # Once: (facts, shown).
        _used = None if scene is None else (_count, getattr(scene, "session_uid", None),
                                            *(found if found is not None else (None, None)))
    except Exception as error:   # The device setup never stops a render.
        print("Angelo: settling the Cycles device failed (%s: %s)" % (type(error).__name__, error))


def _settle(scene, wait, stop=None):
    import bpy
    _attach()
    background = bpy.app.background
    render_device.setup(listing=False)
    if background and wait and scene is not None:
        prefs = render_device._cycles_prefs(bpy.context.preferences)
        if prefs is not None and render_device.stale(prefs, scene):   # This machine's GPU changed since it listed.
            render_device.relist()
    if render_device.status()["state"] == "not_set_up" or not background:
        render_device.setup()   # Background: only when nothing is known. Windowed: the launch's one listing.
    if wait and _undecided():
        _await_listing(background, stop)


def _undecided():
    return render_device._listing is not None and render_device.status()["state"] == "listing"


def _await_listing(background, stop=None):
    """Main thread: wait for the listing child, then apply it as the timer would."""
    listing = render_device._listing
    deadline = listing["started"] + WAIT
    while _clock() < deadline and not (stop and stop()):
        try:
            listing["process"].wait(timeout=min(0.25, max(0.0, deadline - _clock())))
            break
        except Exception:   # subprocess.TimeoutExpired: still listing.
            pass
    if render_device._poll() is not None and background:
        # No timer will ever apply it in a background run: stop it, this run renders on the CPU.
        timed_out = _clock() >= deadline
        render_device.stop("Listing GPU devices took over %g s" % WAIT if timed_out
                           else "The render was cancelled while Angelo listed GPU devices", remember=timed_out and "slow")


def _await_main(stop=None):
    """Another thread: while nothing is decided, wait for the main thread to apply the running listing. Never
    writes preferences; a refresh of a decided setup never holds a render."""
    deadline = _clock() + WAIT
    while _undecided() and _clock() < deadline and not (stop and stop()):
        time.sleep(STEP)


def used_since(mark, scene_uid=None):
    """``(facts, shown)`` of the latest final Cycles session started after ``count()`` returned ``mark`` (of the
    scene ``scene_uid`` when given), or None."""
    used = _used
    if mark is None or used is None or used[0] <= mark or used[2] is None:
        return None
    if scene_uid is not None and used[1] != scene_uid:
        return None
    return used[2], used[3]


def count():
    return _count


def reset(*_args):
    """Load Factory Preferences: decide again, as at a start (now, and a moment later in a windowed start)."""
    render_device.reset()
    import bpy
    try:
        render_device.setup(listing=False)
    except Exception as error:
        print("Angelo: Cycles GPU setup failed (%s: %s)" % (type(error).__name__, error))
    if not bpy.app.background and not bpy.app.timers.is_registered(_startup):
        bpy.app.timers.register(_startup, first_interval=0.5, persistent=True)


reset._bpy_persistent = None   # bpy.app.handlers.persistent: reading a file must not remove it.


def _refresh_viewports():
    """After Angelo changed the device: a Rendered viewport's Cycles session is made again on its next update
    (``BlenderSession::synchronize`` compares the session parameters), so tag the scenes such viewports show."""
    import bpy
    import angelo_live_windows as live_windows
    if bpy.app.background:
        return
    try:
        for window in live_windows.windows(bpy.context.window_manager):
            scene = window.scene
            if scene.render.engine != "CYCLES":
                continue
            if any(area.type == "VIEW_3D" and area.spaces.active.shading.type == "RENDERED"
                   for area in live_windows.areas(window)):
                scene.update_tag()
    except (AttributeError, ReferenceError):
        pass   # No window manager yet (Cycles registering at startup): nothing renders yet.


def register():
    """Cycles' ``register``."""
    import bpy
    if _refresh_viewports not in render_device._listeners:
        render_device._listeners.append(_refresh_viewports)
    if reset not in bpy.app.handlers.load_factory_preferences_post:
        bpy.app.handlers.load_factory_preferences_post.append(reset)
    early()


def unregister():
    import bpy
    for timer in (_startup, render_device._poll):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    if reset in bpy.app.handlers.load_factory_preferences_post:
        bpy.app.handlers.load_factory_preferences_post.remove(reset)
    if _refresh_viewports in render_device._listeners:
        render_device._listeners.remove(_refresh_viewports)
    render_device.close()
