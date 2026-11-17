# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opening a file while Manual's tab build runs never changes the window's tab or the sculpt's mode
(``studio_workspaces``, ``timer_jobs``).

Evidence (an-blender53 on Mac 1, 2026-11-16, 758b07e8b77): a sculpt file reopened with Load UI off (as Angelo's
Restore and scripts open files) while the build ran kept the build's half-made Media Studio tab; about 2 s later the
next build deleted it while the window showed it, and Blender moved the window to the tab's neighbour with that
one's object mode: the 655k-vertex sculpt went into Edit Mode (+1.3 GB), with "ID user decrement error: SRLayout.001"
and "the half-made 'Media Studio' tab was not removed" in the log.

Real default startup (Director), no input, CLI, account, network or inference. Every workspace but the window's own
is put in Edit Mode, so any switch the build makes without keeping the mode shows. A test hold keeps the build on its
half-made tab for a moment (``_switched`` waits longer: timing only), and the file is opened then:

* ``kept``: the same sculpt file, Load UI off: the window shows the half-made tab when the load lands;
* ``saved``: a copy saved at that moment (as quit.blend or a save mid-build keeps it), opened with its layout, so the
  window starts on the half-made tab;
* ``director``: in Manual with no Director workspace yet, the same file, Load UI off, and Director requested at once:
  Director's own build copies the window's workspace (an-blender53's run2: copied from the half-made tab, Director
  carried its markers, and the next build deleted Director under the window, which went to Modeling in Edit Mode).

In each, watched every event-loop pass until the tabs (and Director) are built again: the sculpt never leaves Sculpt
Mode; the window ends on the workspace the build started from (Director, in ``director``); no workspace Angelo did
not make is removed; no half-made tab or ".001" copy is left and Director carries no tab's marker; the finished tabs
carry the window's workspace mode; every screen a workspace uses has a user; and the log has no failed or refused
removal. Orphan screens and Blender's "ID user decrement error" lines are recorded per case (Blender's own Delete
Workspace makes them for any workspace: see the report's fields). Captures: ``tab-build-keeps-mode-*.png``.

Run through ``run_native_suites.py --gui --include test_blender_tab_build_keeps_mode``; check
``tab-build-keeps-mode.json`` for ``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import re
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
from angelo_agent import director, director_layout, studio_tab_build, studio_workspaces as tabs  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-tab-build-keeps-mode-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "tab-build-keeps-mode.json"
CASES = ("kept", "saved", "director")
state = {"phase": "boot", "success": False, "checks": {}, "cases": {}}
checks = state["checks"]
started = time.monotonic()
HOLD = {"on": False, "shown": None}
_SWITCHED = tabs._switched


def _held_switched(held, name, passes=20):
    """The build's own wait for a switch, then a moment more on a half-made tab while ``HOLD`` is on."""
    yield from _SWITCHED(held, name, passes)
    workspace = bpy.data.workspaces.get(name)
    if HOLD["on"] and workspace is not None and tabs.UNFINISHED in workspace:
        HOLD["shown"] = name
        for _ in range(60):
            if not HOLD["on"]:
                return
            yield 0.05


tabs._switched = _held_switched


class Captured:
    """The process's own stdout and stderr (C's CLOG included) into a file for one case, then back into the log."""

    def __init__(self, name):
        self.path, self.saved = OUT / ("log-%s.txt" % name), None

    def __enter__(self):
        sys.stdout.flush(), sys.stderr.flush()
        self.handle = open(self.path, "w+b")
        self.saved = os.dup(1), os.dup(2)
        os.dup2(self.handle.fileno(), 1), os.dup2(self.handle.fileno(), 2)
        return self

    def close(self):
        if self.saved is None:
            return ""
        sys.stdout.flush(), sys.stderr.flush()
        os.dup2(self.saved[0], 1), os.dup2(self.saved[1], 2)
        os.close(self.saved[0]), os.close(self.saved[1])
        self.saved = None
        self.handle.seek(0)
        text = self.handle.read().decode("utf-8", "replace")
        self.handle.close()
        sys.stdout.write(text)   # Back into the runner's log: it still sees any traceback.
        sys.stdout.flush()
        return text


def window():
    return bpy.context.window_manager.windows[0]


def active():
    view_layer = window().view_layer
    return view_layer.objects.active if view_layer is not None else None


def busy():
    return tabs._building or tabs._holding is not None


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    return str(path)


def zero_user_screens():
    return {s.name for s in bpy.data.screens if s.users == 0}


def view3d_override():
    area = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    return bpy.context.temp_override(window=window(), area=area,
                                     region=next(r for r in area.regions if r.type == 'WINDOW'))


def sculpted():
    """The active object: a grid, sculpted."""
    obj = bpy.data.objects.get("Sculpted")
    if obj is None:
        size = 40
        verts = [(x / 10, y / 10, 0) for y in range(size) for x in range(size)]
        faces = [(y * size + x, y * size + x + 1, (y + 1) * size + x + 1, (y + 1) * size + x)
                 for y in range(size - 1) for x in range(size - 1)]
        mesh = bpy.data.meshes.new("Sculpted")
        mesh.from_pydata(verts, [], faces)
        obj = bpy.data.objects.new("Sculpted", mesh)
        window().scene.collection.objects.link(obj)
    window().view_layer.objects.active = obj
    obj.select_set(True)
    return obj


def angelos(workspace):
    return bool(tabs.tab_kind(workspace) or tabs.UNFINISHED in workspace
                or director_layout.is_director_workspace(workspace))


def strip_step(case):
    """One pass of removing the tabs, as in a file without them: Delete Workspace deletes the window's own workspace,
    so the window shows each tab, deletes it, and goes back home. In ``director``, Director's workspace stops being
    Director's (renamed, unmarked), so Director's build makes a new one. True once done."""
    from angelo_agent.legacy_names import MANUAL_WORKSPACE, WORKSPACE_DIRECTOR
    home = bpy.data.workspaces.get(state.setdefault("strip_home", window().workspace.name))
    if case == "director":
        for old in [w for w in bpy.data.workspaces if director_layout.is_director_workspace(w)]:
            for key in WORKSPACE_DIRECTOR + MANUAL_WORKSPACE:
                if key in old:
                    del old[key]
            old.name = "Retired Director"
    doomed = next((w for w in bpy.data.workspaces if tabs.tab_kind(w)), None)
    if doomed is not None:
        if window().workspace != doomed:
            window().workspace = doomed
        else:
            with bpy.context.temp_override(window=window()):
                bpy.ops.workspace.delete()
        return False
    for workspace in bpy.data.workspaces:
        if tabs.FLAG in workspace:
            del workspace[tabs.FLAG]
    if home is not None and window().workspace != home:
        window().workspace = home
        return False
    state.pop("strip_home")
    return True


def set_up(case):
    """Every other workspace in Edit Mode, the grid in Sculpt Mode."""
    home = window().workspace
    obj = sculpted()
    for workspace in bpy.data.workspaces:
        if workspace != home:
            workspace.object_mode = 'EDIT'
    if obj.mode != 'SCULPT':
        with view3d_override():
            bpy.ops.object.mode_set(mode='SCULPT')
    record = state["cases"][case] = {"home": home.name,
                                     "others": sorted(w.name for w in bpy.data.workspaces if not angelos(w))}
    if case in ("kept", "director"):
        record["file"] = str(OUT / ("sculpt-%s.blend" % case))
        bpy.ops.wm.save_as_mainfile(filepath=record["file"])
    return record


def watch():
    """Every event-loop pass of a case, from the load: the mode and the window's workspace."""
    case = state.get("case")
    if case is None or state["phase"] not in ("loaded", "settle"):
        return 0.0
    try:
        record, obj = state["cases"][case], active()
        if obj is not None:
            modes = record.setdefault("modes", [])
            if not modes or modes[-1] != obj.mode:
                modes.append(obj.mode)
        shown = record.setdefault("shown", [])
        if not shown or shown[-1] != window().workspace.name:
            shown.append(window().workspace.name)
    except (AttributeError, IndexError, ReferenceError):   # Between files.
        pass
    return 0.0


def settled():
    built = (qa_boot.started() and not busy() and tabs.tab("studio") is not None and tabs.tab("flows") is not None
             and not tabs.unfinished())
    return built and (state.get("case") != "director" or director.is_director(window()))


def finish(error=None):
    log = state.pop("log", None)
    if log is not None:
        log.close()
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_TAB_BUILD_KEEPS_MODE", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=30):
    since = state.setdefault("since", {}).setdefault("%s:%s" % (state.get("case"), phase), time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in %s (%s)" % (phase, state.get("case")))
    return False


def tick():
    phase, case = state["phase"], state.get("case")
    try:
        assert time.monotonic() - started < 400, "Suite timed out"
        if phase == "boot":
            if not wait(phase, lambda: qa_boot.started() and not busy() and tabs.tab("studio") is not None, 60):
                return .3
            state["case"], state["phase"] = CASES[0], "setup"
            return .5
        if phase == "setup":
            if case == "director" and director.is_director(window()):
                director.request_mode(window(), "MANUAL", persist=False)
                state["phase"] = "to_manual"
                return .3
            state["phase"] = "stripped"
            return .2
        if phase == "to_manual":
            if not wait(phase, lambda: qa_boot.started() and not director.is_director(window()), 20):
                return .2
            state["phase"] = "setup"
            return .2
        if phase == "stripped":
            if not wait(phase, lambda: strip_step(case), 20):
                return .3
            record = set_up(case)
            record["zero_user_screens_before"] = sorted(zero_user_screens())
            state["log"] = Captured(case).__enter__()
            HOLD.update(on=True, shown=None)
            studio_tab_build.run(window())
            state["phase"] = "building"
            return 0.0
        if phase == "building":
            if not wait(phase, lambda: HOLD["shown"] and window().workspace.name == HOLD["shown"], 15):
                return 0.0
            record = state["cases"][case]
            record["shown_at_load"], record["mode_at_load"] = window().workspace.name, active().mode
            HOLD["on"] = False
            state["phase"] = "loaded"
            if case in ("kept", "director"):
                bpy.ops.wm.open_mainfile(filepath=record["file"], load_ui=False)
                if case == "director":   # At once: Director's build copies whatever the window shows then.
                    director.request_mode(window(), "DIRECTOR", persist=False)
            else:
                record["file"] = str(OUT / "half-made.blend")
                bpy.ops.wm.save_as_mainfile(filepath=record["file"], copy=True)
                bpy.ops.wm.open_mainfile(filepath=record["file"], load_ui=True)
            return .3
        if phase == "loaded":
            if not wait(phase, settled, 40):
                return .2
            state["phase"] = "settle"
            return 2.0   # Long enough for any late delete, switch or second build.
        if phase == "settle":
            record = state["cases"][case]
            record["capture"] = capture("tab-build-keeps-mode-%s.png" % case)
            record["log"] = state.pop("log").close()
            obj, shown = active(), window().workspace
            names = [w.name for w in bpy.data.workspaces]
            record.update(final=shown.name, mode=obj.mode if obj else None, workspaces=names)
            checks[case + "_the_sculpt_never_leaves_sculpt_mode"] = (
                record["mode_at_load"] == "SCULPT" and record.get("modes") == ["SCULPT"] and obj.mode == "SCULPT")
            if case == "director":
                checks[case + "_the_window_ends_in_director"] = director.is_director(window())
            else:
                checks[case + "_the_window_ends_where_the_build_started"] = shown.name == record["home"]
            record["others_after"] = sorted(w.name for w in bpy.data.workspaces if not angelos(w))
            checks[case + "_no_workspace_angelo_did_not_make_is_removed"] = (
                set(record["others"]) <= set(record["others_after"]))
            marked = [w.name for w in bpy.data.workspaces if tabs.UNFINISHED in w
                      or (director_layout.is_director_workspace(w) and tabs.TAB_KEY in w)]
            record["marked"] = marked
            checks[case + "_no_half_made_tab_copy_or_marked_director_is_left"] = (
                not marked and not any(n.startswith(tuple(t + "." for t in tabs.TABS.values())) for n in names))
            record["tab_modes"] = {k: getattr(tabs.tab(k), "object_mode", None) for k in tabs.TABS}
            record["home_mode"] = shown.object_mode   # A load can leave it apart from the object's (Blender's).
            checks[case + "_the_finished_tabs_carry_the_window_s_mode"] = (
                set(record["tab_modes"].values()) == {shown.object_mode})
            used = [s for w in bpy.data.workspaces for s in w.screens]
            checks[case + "_every_screen_in_use_has_a_user"] = bool(used) and all(s.users >= 1 for s in used)
            # Reported, not checked: Blender's own Delete Workspace leaves a deleted copy's screen behind with users
            # (a duplicated workspace's screen is over-counted), and after a load decrements a shown screen below
            # zero ("ID user decrement error"), for any workspace, Angelo's builds or none (doc/angelo/
            # generative_studio.rst). What this suite checks is that no screen a workspace uses lacks a user.
            record["new_zero_user_screens"] = sorted(zero_user_screens() - set(record["zero_user_screens_before"]))
            record["user_decrement_errors"] = re.findall(r"user decrement error: (\S+)", record["log"])
            checks[case + "_the_log_has_no_failed_or_refused_removal"] = not any(
                text in record["log"] for text in ("was not removed", "could not be removed", "is not a half-made",
                                                   "no other workspace to show"))
            record.pop("log")   # Already back in the runner's log.
            following = CASES.index(case) + 1
            if following < len(CASES):
                state["case"], state["phase"] = CASES[following], "setup"
                return .5
            state["phase"] = "complete"
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5, persistent=True)    # Outlives each case's load.
bpy.app.timers.register(watch, first_interval=0.0, persistent=True)
