# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Would quitting now interrupt someone's work? The one answer the update's restart asks.

A restart quits Angelo, and quitting stops whatever runs inside it. ``reasons()``
lists what would be lost, from the owners' own state, cheapest first; an empty
list means a restart interrupts nothing. Each reason is a stable code that the
update card turns into a sentence (``update_card``):

``scene``        a chat turn waiting for its scene to be shown (``document_scene.hold``): it never ends by itself
``agent``        a chat turn, a tool call or an External CLI read (``Controller.busy`` / ``pending``)
``preview``      a staged preview waiting for Accept or Discard (it outlives the turn)
``flow``         a Generation Flow run
``generation``   a Generative Studio job (paid, in flight)
``job``          a background render, bake or export (``job_manager``)
``render``       a render, bake, composite or export Blender runs for the person
``interaction``  a modal tool or drag in progress in a window
``unknown``      an owner could not be asked: fail closed, never guess "idle"

Main thread only (it reads Blender's and the owners' live state). Each owner is
imported lazily, so a build or a test without one reads it as idle.
"""

BLENDER_JOBS = ("RENDER", "RENDER_PREVIEW", "OBJECT_BAKE", "COMPOSITE")
# Modal operators that are no one's work: the notice card the person is pressing Restart to Update on.
OWN_OPERATORS = frozenset({"angelo.card_host"})
OTHER_JOBS = ("POINTCACHE", "BAKE_GEOMETRY_NODES", "USD_EXPORT", "ALEMBIC_EXPORT")
ORDER = ("scene", "agent", "preview", "flow", "generation", "job", "render", "interaction", "unknown")


def _controllers():
    from . import ui
    return list(ui._controllers.values())


def _scene(controllers):
    return any(c.busy and getattr(c, "_held", False) for c in controllers)


def _agent(controllers):
    return any(c.busy or bool(getattr(c, "pending", None)) for c in controllers)


def _preview(controllers):
    return any(bool(getattr(c, "_native_preview_cards", None)) for c in controllers)


def _flow():
    from . import flow_host
    return bool(flow_host.active_runs())


def _generation():
    from . import studio
    jobs = getattr(studio, "_jobs", None)
    return bool(jobs is not None and jobs.active)


def _job():
    from . import job_ui
    manager = job_ui.manager(create=False)
    return bool(manager is not None and manager.has_work())


def _render(bpy):
    if any(bpy.app.is_job_running(kind) for kind in BLENDER_JOBS):
        return True
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        return False
    return any(_angelo_jobs.progress(scene, kind) is not None for scene in bpy.data.scenes for kind in OTHER_JOBS)


def _interaction(bpy):
    from . import input_policy, live_windows
    # Every window, one between screens included: its modal tool is still the person's work (fail closed).
    return any(name not in OWN_OPERATORS for window in live_windows.every_window(bpy.context.window_manager)
               for name in input_policy.active_interactions(window))


def reasons(bpy=None):
    """Codes of the work a restart would interrupt now, in ``ORDER``; ``[]`` when nothing is running."""
    if bpy is None:
        import bpy
    found = []
    try:
        controllers = _controllers()
    except Exception:
        controllers = None
        found.append("unknown")
    checks = (("scene", lambda: _scene(controllers)), ("agent", lambda: _agent(controllers)),
              ("preview", lambda: _preview(controllers)), ("flow", _flow), ("generation", _generation), ("job", _job), ("render", lambda: _render(bpy)),
              ("interaction", lambda: _interaction(bpy)))
    for code, check in checks:
        if controllers is None and code in {"agent", "preview"}:
            continue
        try:
            busy = check()
        except ImportError:
            busy = False   # That part of Angelo is not in this build: nothing of it can run.
        except Exception:
            busy = None
        if busy is None and "unknown" not in found:
            found.append("unknown")
        elif busy:
            found.append(code)
    return sorted(found, key=ORDER.index)


def idle(bpy=None):
    return not reasons(bpy)
