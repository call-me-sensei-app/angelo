# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Material shaders compiled ahead of an agent's EEVEE view, off Blender's main loop (doc/angelo/agent_execution.rst).

MATERIAL and LIT views are EEVEE viewport image renders, which compile every material shader they need on the main
thread (``use_deferred_compilation = !is_viewport_image_render``, eevee_material.cc): 446-652 ms in the parallel
benchmark's C2 trials, 4.0 s on a cold launch. A viewport draw queues them to Blender's background compile instead.
``_bpy._angelo_live.shader_state`` reads which materials the scene draws are missing, queued or failed.

* ``queue``: one private compile-ahead EEVEE draw (``_angelo_live.draw(..., compile_ahead=True, lit=, only=)``) an
  object at a time of the fewest objects that draw every missing material, in the look asked for (VW-M-1, VW8-H-4).
* ``wait``: the view stays pending (``StudioPending``) and Blender runs; a cold EEVEE is queued again
  (``REQUEUE_SECONDS``, REVIEW-VIEW-WARM H3). It is drawn only once nothing of its scene is missing or queued,
  whenever it arrived: a queued shader the draw meets is finalized on the main loop without a bound, a missing one
  compiled there (VW-H-3, VW8-H-2). Past ``WAIT_SECONDS`` with anything not ready or unknown: ``LATE``; preparation
  that raised: ``BROKE``; another scene shown: ``MOVED``/``RELOADED``. Failed compiles are named (``notes``).
* ``soon``: after a merge, once the person is idle, the same queue, then the parts' first draws (M3, M5).

First draws (option 3): Metal builds a pipeline at a shader's first draw on the main thread (~80 ms cold). Before the
view, every object drawing a material compiled during the wait, then every instancer, is drawn on its own offscreen
(``only=``), batched by the ``(material, format)`` keys it brings (``_batch``: one object with new keys, up to
``CHUNK_MAX`` whose keys are all drawn; alone after a draw over ``SLOW``; instancers up to ``GROUP_MAX``), until
``TICK_SECONDS`` of a tick are spent (REVIEW-VIEW-SPREAD H1, -2 HB/MA, -3 H1/H2/M1). Residuals, as before the
fix: one object with many new materials, a new variant of a compiled material, other frames of a strip, a new world
(compiled in any draw, eevee_world.cc). A background Blender (a worker) draws its views as before.
"""

import time

import bpy

from .view_first_draw import (CHUNK_MAX, GROUP_MAX, OVERRIDE_TYPES, SIZE, SLOW, TICK_SECONDS,  # noqa: F401
                              _alive, _batch, _draw, _drawing, _first_draw_objects, _format, _pointer, _region, _uid,
                              first_draws)

WAIT_SECONDS = 60.0
REQUEUE_SECONDS = 0.25
IDLE_TRIES = 40          # ``soon`` waits for the person's tool or playback to end: at most this many ticks of 0.25 s.
STATUS = "Compiling shaders for a view"
NOTE = "Blender compiles the scene's new material shaders in the background; the view is drawn once they are ready."
STOPPED = {"error": "The view was stopped with its turn while shaders compiled; nothing in the scene changed.",
           "read_only": True, "scene_changed": False}
MOVED = ("The window shows another scene or view layer since this view was asked, so it was not drawn; nothing in the "
         "scene changed. Ask again for the scene shown now.")
LATE = {"error": "Blender has not finished preparing this view's shaders after %d s, so the view was not drawn "
                 % WAIT_SECONDS +
                 "(drawing it now would hold Blender until they are ready); nothing in the scene changed. Ask again "
                 "shortly.", "read_only": True, "scene_changed": False}
BROKE = ("Preparing this view's shaders failed ({}), so the view was not drawn; nothing in the scene changed. Ask "
         "again.")
SOLID_NOTE = ("This {look} view could not be prepared ({reason}: {names}) and made no progress since it was last "
              "asked, so this picture is drawn in Solid instead: material display colours under studio light, not "
              "this scene's {look} materials or lighting. Ask for {look} again later.")
SOLID_BOUND_NOTE = ("This {look} view could not be prepared ({reason}: {names}) after {times} tries in a row, its "
                    "shaders still changing, so this picture is drawn in Solid instead: material display colours "
                    "under studio light, not this scene's {look} materials or lighting. Ask for {look} again later.")
MAX_DENIALS = 3   # A view denied this many times in a row answers Solid whatever changed (VW10-M-2).
FAILED_NOTE = ("These shaders failed to compile; Blender draws such a material with its error shading and such a world "
               "with its default world, in this view and in the viewport alike.")
RELOADED = ("The scene was reloaded (an undo or a file load) since this view was asked, so it was not drawn; the view "
            "changed nothing. Ask again for the scene as it is now.")
_used = [False]   # Whether an agent's EEVEE view ran in this session (``soon`` works only then).
_lit = [True]     # Whether its last one was LIT (``soon`` warms that look).
_turn = [0]       # Which instancer the next queue draw takes when no plain object draws a missing material.
_denied = {}      # (window, LIT) -> (what was not ready when its last view was denied, denials in a row).
_soon = {}        # A window's pointer -> the materials a merge queued, until their first draws are done.


def compiling():
    """Whether Blender's background shader-compilation job is running."""
    try:
        return bool(bpy.app.is_job_running("SHADER_COMPILATION"))
    except (AttributeError, TypeError, ValueError):
        return False


def _background():
    return getattr(bpy.app, "background", True) is not False   # Unknown (a stand-in): as in the background.


def state(window, area=None):
    """``(materials, missing, queued[, names])`` of ``window``'s scene as the 3D view ``area`` (default: the window's
    first) draws it (module docstring), or None when it cannot be read."""
    try:
        import _bpy
        from . import live_windows
        live = getattr(_bpy, "_angelo_live", None)
        if not hasattr(live, "shader_state"):
            return None
        area = area or next((a for a in live_windows.areas(window) if a.type == 'VIEW_3D'), None)
        space = area.spaces.active if area is not None else None
        found = (live.shader_state(window.view_layer.depsgraph, space) if space is not None else
                 live.shader_state(window.view_layer.depsgraph))
        return tuple(found) if found is not None else None
    except (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
        return None


def queue(window, area=None, lit=True):
    """Queue the shaders ``window``'s scene still misses (module docstring); True when a draw ran. Never raises: a
    build or state that cannot queue leaves a view to compile as before. Each draw is one object, the fewest that
    draw every missing material, those with the fewest materials first, until ``TICK_SECONDS`` are spent (every
    pipeline a draw meets is built in it: one draw of all of them built their compiled ones too, VW8-H-4); the rest
    on the next call. While a missing material is drawn by no such object, one instancer a call, in turn."""
    if _background() or window is None:
        return False
    found = state(window, area)
    if found is None or not found[1]:
        return False
    try:
        host = _region(window, area)
        if host is None:
            return False
        if len(found) <= 4:
            return _draw(window, *host, None, lit) is not None   # An older build: the whole scene, as before.
        missing = set(found[4])
        direct, instancers, covered = _drawing(window, missing)
        picks, left = [], set(missing)
        depsgraph = window.view_layer.depsgraph

        def slots(entry):   # What else the object draws: each is a pipeline its draw may build.
            try:
                return len(entry[0].evaluated_get(depsgraph).material_slots)
            except (AttributeError, TypeError, ReferenceError, RuntimeError):
                return 0
        for obj, keys in sorted(direct, key=slots):
            brings = {name for name, _kind in keys} & left
            if brings:
                picks.append(obj)
                left -= brings
        if left and instancers:
            _turn[0] += 1
            picks.append(instancers[_turn[0] % len(instancers)])
        if not picks:
            return _draw(window, *host, None, lit) is not None   # Held by nothing found: the whole scene.
        tick = time.perf_counter()
        for index, obj in enumerate(picks):
            if index and time.perf_counter() - tick >= TICK_SECONDS:
                break
            _draw(window, *host, [obj], lit)
        return True
    except (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError, SystemError):
        return False


def _busy(window):
    """The person's tool, navigation or playback runs in ``window``: nothing is drawn meanwhile."""
    try:
        from .input_policy import tool_running
        screen = getattr(window, "screen", None)
        return bool(tool_running(window) or getattr(screen, "is_animation_playing", False))
    except (AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
        return True


def _key(window):
    """Who owns a view's preparation: the window with the scene and view layer it shows, by name. Work owed for one
    scene is never adopted by another (BREAKIT-viewwarm11 VW11-H-1: its objects drawn alone on another depsgraph draw
    nothing, yet counted as done)."""
    try:
        pointer = window.as_pointer()
    except (AttributeError, ReferenceError):
        pointer = id(window)
    try:
        return pointer, window.scene.name_full, window.view_layer.name
    except (AttributeError, ReferenceError):
        return pointer, None, None


def soon(window):
    """``queue`` once ``window`` is idle, from a timer (never inside the caller's operator): after a merge. Then,
    once those materials are compiled, their first draws a tick at a time while the window is idle, unless a view
    asked meanwhile takes them over (``wait``); never past ``WAIT_SECONDS``."""
    if _background() or not _used[0]:
        return
    from . import live_target
    tries, key, held = [0], _key(window), live_target.window_ref(window)   # Held by identity across ticks.

    def spread():
        entry = _soon.get(key)
        if entry is None or entry["run"] is not spread:
            return None   # A view took them over, or a later merge's own timer runs.
        window = held()
        if window is None:   # Closed, or a load replaced it: owed to its scene's next view (C2's Windows crash).
            entry["run"] = None
            return None
        try:
            if time.monotonic() > entry["deadline"]:   # Left owed: the next view takes them over (-4 M1).
                entry["run"] = None
                return None
            if _busy(window):
                return 0.25
            if _key(window) != key:
                entry["run"] = None   # Another scene shown: owed to this one's next view, never drawn on another.
                return None
            current = state(window)
            if current is not None and len(current) > 3:
                _redo(entry["pace"], set(current[3]))   # An edit meanwhile: drawn again once compiled (VW11-H-3).
                if entry["names"] & set(current[3]):
                    return 0.1   # Still compiling.
            if entry["objects"] is None or entry.get("generation") != _generation(window):
                entry["generation"] = _generation(window)   # Listed again for what the scene is now (VW11-H-2).
                entry["objects"] = _first_draw_objects(window, entry["names"])
            if entry["objects"] and first_draws(window, entry["objects"], pace=entry["pace"]) and entry["objects"]:
                return 0.02
        except Exception:   # noqa: BLE001 - a timer must never raise: kept owed, the next view takes it (VW10-H-1).
            entry["run"] = None
            return None
        _soon.pop(key, None)   # All drawn.
        return None

    def run():
        window = held()
        if window is None:
            return None
        tries[0] += 1
        if _busy(window) and tries[0] < IDLE_TRIES:
            return 0.25
        if not _busy(window) and queue(window, None, _lit[0]):
            found = state(window)
            names = set(found[3]) if found is not None and len(found) > 3 else set()
            previous = _soon.get(key)
            names |= previous["names"] if previous else set()   # A later merge keeps an earlier one's (MB).
            if names:
                pace = previous["pace"] if previous else {}   # What an earlier merge drew is not drawn again.
                pace["lit"] = _lit[0]
                _soon[key] = {"names": names, "objects": None, "pace": pace, "run": spread,
                              "deadline": time.monotonic() + WAIT_SECONDS}
                bpy.app.timers.register(spread, first_interval=0.1)
        return None
    bpy.app.timers.register(run, first_interval=0.0)


def wait(window, area, run, controller=None, lit=True, solid=None):
    """A pending result that calls ``run()`` (the view, drawn then) once the shaders ``window``'s scene needs are
    compiled and first-drawn; None when none is missing or queued, so the caller draws at once. Denied (``LATE``,
    ``BROKE``) a second time with nothing changed since, it answers ``solid()`` instead, labelled Solid with what is
    still not ready (the coordinator, 2026-10-25: no material that never compiles may deny every image, VW9-H-1)."""
    if _background():
        return None
    _used[0], _lit[0] = True, lit
    try:
        window.view_layer.update()   # An edit not yet evaluated is part of what the view shows (REVIEW-VIEW-WARM-2).
    except (AttributeError, RuntimeError, ReferenceError):
        pass
    queue(window, area, lit)
    found = state(window, area)
    owner = _key(window)
    merged = _soon.pop(owner, None)   # This scene's owed first draws: a merge's, a denied view's (M3).
    if merged is not None and (merged["pace"].get("lit", True) != lit or
                               merged.get("generation") != _generation(window)):
        merged["objects"] = None   # Another look, or the scene changed since it was listed: listed again (VW11-H-2).
    if merged is not None and merged["objects"] is not None and not merged["objects"]:
        merged = None
    if (found is None and not _reader()) or (found is not None and not (found[1] or found[2]) and merged is None):
        _denied.pop((owner, lit), None)   # Drawn at once: a capture of the look asked for (VW11-M-2).
        return None   # Nothing pending (or an older build that cannot tell): drawn at once, as before.
    from .recovery_evidence import collect
    from .studio_agent import StudioPending
    from .view_spec import TOOL_NAME
    from . import live_target
    # Held by identity, never as objects: the view is drawn ticks later, after a load, a closed window or a deleted
    # scene may have freed them (C2's Windows crash, live_target). The scene by its session uid and address (an Undo
    # keeps the uid and re-makes it), the view layer by its scene and its place there (a rename is the same layer,
    # another layer given its name is not: BREAKIT-lt-pa LT-M-1); names only for what the agent is told.
    held_window, held_scene = live_target.window_ref(window), live_target.scene_ref(window.scene)
    held_layer = live_target.view_layer_ref(window.view_layer)
    names = (getattr(window.scene, "name_full", None), getattr(window.view_layer, "name", None))
    address = _address(window.scene)
    call = getattr(controller, "executing_call", None)
    started = time.monotonic()
    # The view draws only once nothing of the scene it draws is missing or queued, whenever it arrived (a merge, the
    # person's edit): a queued shader the draw meets is finalized on the main loop without a bound (gpu_shader.cc),
    # a missing one compiled there (VW8-H-2). Every material pending during the wait gets its first draws.
    progress = {"state": found, "at": started, "queued_at": started,
                "names": set(found[3]) if found is not None and len(found) > 3 else set(),
                "objects": None, "generation": None, "readable": [],
                "pace": dict(merged["pace"], lit=lit) if merged is not None else {"lit": lit},
                "taken": merged["objects"] if merged is not None and merged["objects"] is not None else []}
    if merged is not None:
        progress["names"] |= merged["names"]

    def drawn_first(window):
        """The first draws, a tick at a time; True once none is left. Whatever the scene gained meanwhile (a merge, an
        edit) joins them (VW9-H-4): rebuilt whenever the depsgraph was evaluated again."""
        now_generation = _generation(window)
        if progress["objects"] is None or now_generation != progress["generation"]:
            progress["generation"] = now_generation
            base = progress["objects"] if progress["objects"] is not None else list(progress["taken"])
            held = {(_pointer(obj), keys) for obj, keys in base}
            progress["objects"] = base + [entry for entry in _first_draw_objects(window, progress["names"])
                                          if (_pointer(entry[0]), entry[1]) not in held]
        if progress["objects"]:
            first_draws(window, progress["objects"], area, progress["pace"])
            return False   # The view draws on a later tick, never in the same one as a first draw.
        return True

    def ready(now, window):
        current = state(window, area)
        progress["state"] = current
        if current is not None and len(current) > 3:
            progress["names"] |= set(current[3])
            _redo(progress["pace"], set(current[3]))
            progress["readable"] = list(current[7]) if len(current) > 7 else [str(item) for item in current[3]]
        if now - started >= WAIT_SECONDS:   # Never drawn while anything is not ready, unknown or owed its first draw:
            if progress["objects"] is None:   # Listed, not guessed: owed only if something draws them.
                progress["objects"] = list(progress["taken"]) + _first_draw_objects(window, progress["names"])
            owed = bool(progress["objects"])
            progress["late"] = current is None or bool(current[1] or current[2]) or owed   # the backlog stays (-H-2).
            return True
        if _busy(window) or current is None:
            return False
        if current[1] or current[2]:
            if current[1] and now - progress["queued_at"] >= REQUEUE_SECONDS:   # A cold engine asks for nothing until
                progress["queued_at"] = now                                    # its own shaders are ready (H3).
                queue(window, area, lit)
            return False
        return drawn_first(window)

    def poll():
        now = time.monotonic()
        window, scene, layer = held_window(), held_scene(), held_layer()
        try:
            reloaded = window is None or scene is None or _address(scene) != address   # A load, an Undo, a close.
            moved = reloaded or layer is None or window.scene != scene or window.view_layer != layer
            reloaded = reloaded or (moved and _reloaded(window, names))
        except ReferenceError:   # A file load or an undo freed what the view was asked for.
            moved = reloaded = True
        if moved:
            keep()   # Owed to the scene it was asked for, when it is shown again (VW11-H-1).
            return {"error": RELOADED if reloaded else MOVED, "read_only": True, "scene_changed": False}
        view, denial = run, None
        try:
            if not ready(now, window):
                return None
        except Exception as error:   # noqa: BLE001 - never drawn on a guess (VW8-H-2): the agent asks again.
            denial = ({"error": BROKE.format(error), "read_only": True, "scene_changed": False}, "preparation failed")
        if denial is None and progress.get("late"):   # VW-H-3: finalizing a queued compile waits without a bound.
            denial = (dict(LATE), _reason(progress))
        if denial is not None:
            keep()   # Owed across the denial: the next request goes on from here (VW10-H-1).
            key, signature = (owner, lit), (denial[1], _signature(progress, names))
            last, count = _denied.get(key, (None, 0))
            _denied[key] = (signature, count + 1)
            if solid is None or (last != signature and count + 1 < MAX_DENIALS):
                return denial[0]
            view = solid   # Denied again with nothing changed, or too often: Solid, labelled (VW9-H-1c, VW10-M-2).
            progress["reason"], progress["bound"] = denial[1], last != signature
        try:
            with collect() as records:
                result = view()
        except Exception as error:   # noqa: BLE001 - the view's own refusal, as a direct call would answer it.
            return {"error": str(error), "read_only": True, "scene_changed": False}
        barrier = getattr(controller, "recovery_barrier", None)
        if barrier is not None and isinstance(result, dict) and not result.get("error"):
            result = barrier.read(TOOL_NAME, records, result, credit=getattr(call, "parent_call", None) is None)
        if isinstance(result, dict) and isinstance(result.get("timing"), dict):
            result["timing"]["shaders_waited_ms"] = round((time.monotonic() - started) * 1000, 1)
        if view is solid and isinstance(result, dict) and not result.get("error"):
            look = "LIT" if lit else "MATERIAL"
            listed = ", ".join(sorted(map(str, progress["readable"]))) or "unknown"
            text = SOLID_BOUND_NOTE if progress.get("bound") else SOLID_NOTE
            result.setdefault("uncertainty", []).append({"code": "shaders_not_ready", "detail": text.format(
                look=look, reason=progress.get("reason", "compiling"), names=listed, times=MAX_DENIALS)})
            result["uncertain"] = True
        elif view is run:
            _denied.pop((owner, lit), None)
        return result

    def keep():
        """What is still owed, kept for the window's next view (as a merge's is): never lost to a denial, a stop or
        a failure (VW10-H-1); a drawn view takes it and leaves none."""
        if progress["names"] or progress["objects"]:
            if len(_soon) > 16:   # Windows and scenes come and go: what is owed never grows without bound.
                _soon.clear()
            _soon[owner] = {"names": set(progress["names"]), "objects": progress["objects"],
                            "pace": progress["pace"], "run": None, "deadline": 0.0,
                            "generation": progress.get("generation")}

    pending = StudioPending(poll, keep)
    pending.status_text, pending.activity_note, pending.stopped_result = STATUS, NOTE, dict(STOPPED)
    pending.timeout = WAIT_SECONDS + 30.0
    return pending


def _redo(pace, pending):
    """Forget the first draws done for materials pending again: an edit recompiles a material under the same UID, so
    what was drawn before is not what the view will draw (VW10-H-2)."""
    if not pending:
        return
    for look, (done, drawn) in list(pace.get("books", {}).items()):
        pace["books"][look] = ({item for item in done if not any(key[0] in pending for key in (item[1] or ()))},
                               {key for key in drawn if key[0] not in pending})


def _reason(progress):
    """Why a view is not ready: its shaders are compiling, its first draws are owed, or its state cannot be read."""
    current = progress.get("state")
    if current is None:
        return "unreadable shader state"
    return "compiling" if current[1] or current[2] else "first draws owed"


def _signature(progress, names):
    """What is not ready, as finely as progress shows: each pending material's UID with its state (missing or
    queued), the first draws owed, and the scene (VW10-M-2)."""
    current = progress.get("state")
    pending = frozenset()
    if current is not None and len(current) > 4:
        missing = set(current[4])
        pending = frozenset((uid, "missing" if uid in missing else "queued") for uid in current[3])
    owed = frozenset((_pointer(obj), keys) for obj, keys in (progress["objects"] or ()))
    return names, pending, owed


def _generation(window):
    """The depsgraph's evaluation count (``_angelo_live.update_count``), or None where it cannot be read."""
    try:
        import _bpy
        return _bpy._angelo_live.update_count(window.view_layer.depsgraph)
    except (ImportError, AttributeError, TypeError, ValueError, RuntimeError, ReferenceError):
        return None


def _reader():
    """Whether this build reads the shader state (``_angelo_live.shader_state``)."""
    try:
        import _bpy
        return hasattr(getattr(_bpy, "_angelo_live", None), "shader_state")
    except ImportError:
        return False


def failed(window, shading):
    """The names of what ``window``'s scene draws in an EEVEE ``shading`` whose compile failed: its materials, and its
    world in LIT, and in MATERIAL when it is the view layer's override (otherwise a studio light)."""
    from .view_looks import EEVEE_SHADINGS
    found = state(window) if shading in EEVEE_SHADINGS else None
    names = set(found[5]) if found is not None and len(found) > 5 else set()
    if found is not None and len(found) > 6 and (shading == "LIT" or (len(found) > 8 and found[8])):
        names |= set(found[6])   # MATERIAL draws the view layer's world override too (VW10-H-3).
    return names


def note(names):
    """The uncertainty note of a view that drew ``names`` (``failed``) with Blender's error or default shading."""
    return {"code": "shaders_failed", "detail": FAILED_NOTE + " " + ", ".join(sorted(names))}


def notes(window, shading):
    """The view's uncertainty notes from its scene's shader state: what failed to compile, drawn with Blender's error
    shading or default world in an EEVEE look (BREAKIT-viewwarm VW-H-1, VW8-H-1: the view must say so)."""
    names = failed(window, shading)
    return [note(names)] if names else []


def _address(value):
    return getattr(value, "as_pointer", lambda: id(value))()


def view(window, args, references, controller, lit):
    """``wait`` for ``view_capture.execute``'s agent view: the view and its Solid fallback drawn later through the
    window held by identity (``live_target``), never the object (C2's Windows crash)."""
    from . import live_target, view_capture, view_policy
    try:   # A view the approval mode refuses (Read-only, Ask, a sculpt session) never waits or draws first: the
        # capture answers it at once, from the cache or with the refusal (it waited, then refused: T4 C3 audit).
        if "frames" in args:   # A safe display write does not admit a strip's frame changes, playback or jobs.
            from . import view_motion
            if view_motion._refusal(window, view_policy.mode_of(controller),
                                    view_capture.display_keys("LIT" if lit else "MATERIAL")):
                return None
        view_policy.admit_display(view_capture._draw_area(view_capture._view3d_areas(window)).spaces.active,
                                  "LIT" if lit else "MATERIAL", controller)
    except Exception:   # noqa: BLE001 - whatever it is, the capture path meets it again and answers it.
        return None
    held = live_target.window_ref(window)

    def again(shading=None):
        shown = live_target.require(held, "The window this view was asked in is gone; ask again.")
        return view_capture.execute(shown, dict(args, shading=shading) if shading else args, references, controller)
    return wait(window, None, again, controller, lit, lambda: again("SOLID"))


def _reloaded(window, names):
    """Whether the window shows the same-named scene and view layer as ``names`` (an undo or a reload re-made them)."""
    try:
        return None not in names and (window.scene.name_full, window.view_layer.name) == names
    except (AttributeError, ReferenceError):
        return False


def release():
    _used[0] = False
    _denied.clear()
    _soon.clear()
