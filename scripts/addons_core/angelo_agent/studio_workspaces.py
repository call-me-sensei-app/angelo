# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Manual's workspace tabs for Media Studio and Generation Flows, and the one place that routes to them (1.0.5).

Each mode has one home per destination (``doc/angelo/studio_entry_points.rst``, rev 2): in Manual a workspace tab,
in Director its own window. The strip reads Director · Media Studio · Generation Flows · Layout · …; each tab is one
editor filling the area, without Blender's header, sidebar or toolbar (the top bar and status bar stay). A file gets
the tabs once: they are recorded on the file when added, so a tab the person deleted is never added back to it
(``FLAG``). ``show`` is the routing every entry uses in Manual: Studio and Flows switch to their tab, and a click on
the pressed button goes back to the workspace they came from. Navigating never edits a layout: the workspace left
behind keeps every area, and a hidden chat stays hidden.
"""

import json
import time

import bpy
from bpy.props import EnumProperty
from bpy.types import Menu, Operator

from . import live_target, live_windows, timer_jobs
from .studio_tab_fill import fill   # noqa: F401  The build calls it by this name (GUI suites replace it).
from .studio_tab_order import in_order, place, strip   # noqa: F401  The tab strip's order, by these names.

TABS = {"studio": "Media Studio", "flows": "Generation Flows"}   # i18n: workspace data names (product names), never translated
TAB_KEY = "angelo_tab"                # On a tab's workspace: which destination it is.
UNFINISHED = "angelo_tab_unfinished"  # On a tab a build is still making: never the tab until the build finishes it.
FLAG = "angelo_studio_tabs"           # On the file's other workspaces: JSON list of the tabs added to it once.
_came_from = {}                       # Window pointer -> the workspace name to go back to.
_building = False
_holding = None   # The tab build's timer step while it holds ui._layout_timer.
_kept_modes = {}  # Workspace name -> its own object mode, while a build gives it the window's (``_same_mode``).
last_build = {}   # {"seconds", "kinds", "done"} of the last build: how long the window showed other tabs (GUI suites).
QUIET = 1.0       # Seconds the start-up must stay quiet before a build switches the window's tabs.
ATTEMPTS = 3      # Builds tried in one session; a build that stopped part-way is rolled back and tried again.
STARTUP_LIMIT = 15.0   # Seconds the start-up's cards wait at most for the start-up build (never forever).
_startup = {"pending": True, "until": None}   # The session's first build: part of the start-up, under its cover.


def tab_kind(workspace):
    return workspace.get(TAB_KEY) if workspace is not None else None


def tab(kind):
    """The finished ``kind`` tab: never one a build left half-made in a saved file (quit.blend, a save mid-build)."""
    return next((w for w in bpy.data.workspaces if tab_kind(w) == kind and UNFINISHED not in w), None)


def unfinished():
    """Tabs a stopped build left half-made in a saved file (quit.blend, a save mid-build): the next build removes them,
    and one runs for them alone too (``ensure``)."""
    return [w.name for w in bpy.data.workspaces if UNFINISHED in w]


def added_once():
    """The tabs this file has had, read from any workspace that carries the flag."""
    for workspace in bpy.data.workspaces:
        value = workspace.get(FLAG)
        if value:
            try:
                return set(json.loads(value))
            except ValueError:
                continue
    return set()


def _record(kinds):
    value = json.dumps(sorted(kinds))
    for workspace in bpy.data.workspaces:
        if tab_kind(workspace) is None:
            workspace[FLAG] = value


def missing():
    """Tabs to add to this file: never one it had and the person deleted."""
    had = added_once()
    return [kind for kind in TABS if tab(kind) is None and kind not in had]


def _manual_source(window):
    from .director_layout import is_director_workspace
    current = window.workspace
    if not is_director_workspace(current) and tab_kind(current) is None:
        return current
    return next((w for w in bpy.data.workspaces if not is_director_workspace(w) and tab_kind(w) is None), None)


def _alive(held):   # Read live at each resumed step; gone, the build stops (live_target.Gone, break-it SW2-H-1).
    return live_target.require(held, "the window closed while its tabs were built")   # i18n: console log detail


def _switched(held, name, passes=20):
    """Timer steps until the window shows workspace ``name``: Blender applies a switch on its next event-loop pass, so
    each step is one pass (one drawn frame), never a fixed delay. Yields nothing more once it shows."""
    for _ in range(passes):
        if _alive(held).workspace.name == name:
            return
        yield 0.0
    raise RuntimeError("the window did not switch to %r" % name)


def _layout_idle():
    """No other part of Angelo is changing this window's layout (Director's start-up, the default layout)."""
    from . import director, ui
    return director._timer is None and ui._layout_timer in (None, _holding)


def _switch(held, name):
    """Timer steps that show ``workspace`` in ``window``, queueing a switch only when it is not already shown.

    A switch queued to the workspace already shown is never waited for (``_switched`` sees it shown) and stays in
    Blender's notifier queue; ``workspace.duplicate`` then stores its copy's layout for the next switch
    (``temp_layout_store``), and that stale switch applied the copy's layout to the wrong workspace: a null layout in
    ``ED_workspace_change`` and a crash at startup (1.0.5 preview: "Couldn't find layout in this workspace: 'Layout'
    screen: 'Layout.001'")."""
    for _ in range(100):   # Never interleave with another layout change's switches (at most ~10 s).
        if _layout_idle():
            break
        yield 0.1
    else:
        raise RuntimeError("the window's layout stayed busy")
    window, workspace = _alive(held), bpy.data.workspaces[name]   # Gone meanwhile: KeyError ends the build.
    if window.workspace != workspace:
        window.workspace = workspace
        yield from _switched(held, name)


def _same_mode(workspace, home):
    """Before the build switches the window to ``workspace``: give it ``home``'s object mode. Blender sets the active
    object to a workspace's own mode on every switch, so a build passing through another workspace left a sculpting
    object in Object Mode (1.0.5 final round, dispatch_fixes: "Prepare this object for sculpting first" after the
    start-up build). ``restore_modes`` gives each its own back once the window shows ``home`` again."""
    if workspace is not None and home is not None and workspace != home and workspace.object_mode != home.object_mode:
        _kept_modes.setdefault(workspace.name, workspace.object_mode)
        workspace.object_mode = home.object_mode


def restore_modes():
    for name, mode in _kept_modes.items():
        if name in bpy.data.workspaces:
            bpy.data.workspaces[name].object_mode = mode
    _kept_modes.clear()


def startup_quiet(window):
    """Nothing is holding ``window``'s layout: Director's and the default layout's own start-up, Blender's splash
    while Angelo's start-up is pending, a card (the setup card, the start panel, a notice), the welcome or its
    scheduling, and any modal Blender tool. A build switches the window's workspace, which a card or the welcome
    over the 3D view does not survive (1.0.5 first launch: the setup card closed unanswered, the welcome never
    opened, and the window was left in Manual on another tab, without the chat)."""
    from . import card_host, director, intro, splash_setup, ui
    from .input_policy import interaction_active
    return not (director._timer is not None or ui._layout_timer is not None or splash_setup.pending()
                or intro._timer is not None or intro.is_open() or card_host.is_active() or interaction_active(window))


def startup_build_pending():
    """The session's start-up still owes the tabs: Angelo's start-up cards (the setup card, the start panel, the
    welcome) wait for this, so the build runs under the start-up cover before the person can act, never while they
    use the app (1.0.5: the build ran after the cards and switched the window under the person's clicks). At most
    ``STARTUP_LIMIT`` seconds, so a build that cannot run never holds the cards."""
    if not _startup["pending"] or bpy.app.background:
        return False
    if _startup["until"] is None:
        _startup["until"] = time.monotonic() + STARTUP_LIMIT
    if time.monotonic() > _startup["until"] or not missing():
        _startup["pending"] = False
    return _startup["pending"]


def startup_ready(window):
    """The start-up build may run: Director's and the default layout's own start-up have settled and no Blender tool
    is active. Blender's startup splash may still be up: Angelo's start-up closes it by a workspace switch anyway."""
    from . import director, ui
    from .input_policy import interaction_active
    return director._timer is None and ui._layout_timer is None and not interaction_active(window)


def _remove(window, workspace):
    """Delete a tab a stopped build left half-made (Blender's Delete Workspace on it), so it is built again later;
    never a tab the person can see as a copy of Layout."""
    name = workspace.name
    try:
        with bpy.context.temp_override(window=window, workspace=workspace):
            bpy.ops.workspace.delete()
    except (RuntimeError, TypeError) as error:   # Still marked unfinished: the next build removes it.
        print("Angelo: a half-made %r tab could not be removed:" % name, error)


def _removed(held, name):
    """Timer steps removing the half-made tab ``name`` a stopped build left in a saved file: Blender applies a delete
    on its next pass, and a tab built meanwhile would be named ".001"."""
    _remove(_alive(held), bpy.data.workspaces[name])
    for _ in range(20):
        if name not in bpy.data.workspaces:
            return
        yield 0.0
    raise RuntimeError("the half-made %r tab was not removed" % name)


def build(window, kinds):
    """A generator (timer steps): add each tab in ``kinds`` and put the strip in order; the window ends where it was.

    Blender's workspace operators (duplicate, close area, reorder) act on the window's active workspace, so each
    step shows the tab it works on; every step waits exactly for its switch to apply (``_switched``) and acts at
    once, so the whole build shows for a few frames instead of the second it took with fixed delays (1.0.5 preview:
    the Media Studio tab flashed ~1-2 s after startup). Building with no switch at all needs a native API (1.0.6).
    It holds the window's identity and workspace names, reads the window after every yield, and stops once gone."""
    return _build(live_target.window_ref(window), kinds)


def _build(held, kinds):
    global _building
    from .director_layout import is_director_workspace
    home_name, started, made, done = None, time.monotonic(), [], False
    try:   # Nothing is claimed outside the cleanup: a window gone before the first step still releases it (SW3-M-1).
        _building = True
        for name in unfinished():   # Never adopted, never kept.
            yield from _removed(held, name)
        home_name = _alive(held).workspace.name
        for kind in kinds:
            existing = bpy.data.workspaces.get(TABS[kind])
            if existing is not None and tab_kind(existing) is None:   # Never a ".001": adopt the one of that name.
                existing[TAB_KEY] = kind
                _record(added_once() | {kind})
                continue
            source = _manual_source(_alive(held))
            if source is None:
                return
            _same_mode(source, bpy.data.workspaces.get(home_name))
            yield from _switch(held, source.name)
            before = {w.as_pointer() for w in bpy.data.workspaces}
            with bpy.context.temp_override(window=_alive(held)):
                bpy.ops.workspace.duplicate()
            created = next((w for w in bpy.data.workspaces if w.as_pointer() not in before), None)
            if created is None:
                return
            created.name, created[TAB_KEY], created[UNFINISHED] = TABS[kind], kind, True
            made.append(name := created.name)
            yield from _switched(held, name)   # The duplicate queued its own switch; never a second one.
            keep = max(live_windows.areas(_alive(held)), key=lambda a: a.width * a.height, default=None)
            if keep is None:   # Between screens after the switch: the tab is built again on its next check.
                return
            keep = keep.as_pointer()
            while len(live_windows.areas(_alive(held))) > 1:   # One editor fills the tab (as director_layout.build).
                window = _alive(held)
                area = next(a for a in live_windows.areas(window) if a.as_pointer() != keep)
                with bpy.context.temp_override(window=window, area=area):
                    if not bpy.ops.screen.area_close.poll() or bpy.ops.screen.area_close() != {'FINISHED'}:
                        return
                yield 0.0   # The join applies on the next pass.
            fill(_alive(held), kind)
            made.remove(name)   # Finished: it stays, the tab from now on.
            del bpy.data.workspaces[name][UNFINISHED]
            _record(added_once() | {kind})
        if not place():   # A build without WorkSpace.angelo_move_after: the operator, one switch per tab.
            for name in [w.name for w in [tab("flows"), tab("studio")] + [w for w in bpy.data.workspaces
                                                                       if is_director_workspace(w)] if w is not None]:
                _same_mode(bpy.data.workspaces.get(name), bpy.data.workspaces.get(home_name))   # By name: held across
                yield from _switch(held, name)                                                   # yields, never an ID.
                with bpy.context.temp_override(window=_alive(held)):
                    bpy.ops.workspace.reorder_to_front()
        done = True
    finally:
        _building = False
        if timer_jobs.leaving():   # A load or a quit closed it (timer_jobs): nothing of that file is read.
            made = []
        else:
            try:
                window = held()   # None once it closed or a load replaced it: never the old window (live_target).
                # Home first: deleting the tab the window shows switches it to a neighbour, with that one's mode.
                home = bpy.data.workspaces.get(home_name or "")
                if window is not None and home is not None and window.workspace != home:
                    window.workspace = home   # Wherever the build stopped, the window ends where it was: mode too.
                rollback = window or live_windows.first(bpy.context.window_manager)
                for name in made:   # Stopped part-way (an operator refused, an error, the window closed): roll back.
                    if name in bpy.data.workspaces and rollback is not None:
                        _remove(rollback, bpy.data.workspaces[name])
            except ReferenceError:   # Its data went away meanwhile: nothing left to put back.
                pass
        last_build.update(seconds=round(time.monotonic() - started, 3), kinds=list(kinds), done=done,
                          rolled_back=list(made))


def ensure(window):
    """Add the missing tabs to this file once, as a timer job. The session's first build is part of the start-up
    (``startup_build_pending``): Angelo's default layout runs it before Director's (``studio_tab_build``); without that
    layout (opted out) it runs here as soon as the layout is idle, under the start-up cover, and the start-up cards
    wait for it. Any later build (a file loaded without the tabs) waits until the app has been quiet for ``QUIET``
    seconds (``startup_quiet``): never under a card or the welcome. A build that stopped part-way rolled itself back
    and is tried again at the next quiet moment (``ATTEMPTS``)."""
    import time
    if bpy.app.background:   # No windows to show a tab in (headless runs, background suites).
        return
    kinds = missing()
    if not kinds and not _building and not in_order():
        place()   # A file whose strip was saved out of order (or reordered by hand) gets the spec order back.
    if not (kinds or unfinished()) or _building:
        return
    quiet_since, attempts = [None], [0]

    def wait_then_build(target):   # The live window (live_windows.deferred waits while it is between screens), or None.
        if target is None or target.screen is None or target.screen.is_temporary or not (missing() or unfinished()):
            _startup["pending"] = False
            return None
        if _building or _holding is not None:
            return 0.5
        if startup_build_pending():   # Part of the start-up: now, under its cover; its cards wait.
            if not startup_ready(target):
                return 0.1
        elif not startup_quiet(target):
            quiet_since[0] = None
            return 0.5
        else:
            now = time.monotonic()
            if quiet_since[0] is None or now - quiet_since[0] < QUIET:
                quiet_since[0] = quiet_since[0] or now
                return 0.25
        attempts[0] += 1

        def retry():   # Stopped part-way and rolled back: tried again when quiet.
            if attempts[0] < ATTEMPTS:
                quiet_since[0] = None
                bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer,
                                                              wait_then_build), first_interval=1.0)
        from .studio_tab_build import run
        run(target, retry)
        return None
    pointer = window.as_pointer()
    # The start-up build at the first pass: its switches free any popup open by then (1.0.5 RC, app_identity: a
    # Help menu opened a second after the add-on was enabled closed under the build).
    bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, wait_then_build),
                            first_interval=0.0 if startup_build_pending() else 0.5)


def in_manual(window):
    from .director_layout import is_director
    return window is not None and not is_director(window)


def active(window, kind):
    """Whether ``window`` shows the ``kind`` tab (the top-bar button is pressed)."""
    return window is not None and tab_kind(window.workspace) == kind and UNFINISHED not in window.workspace


def show(window, kind, toggle=False):
    """In Manual, switch to the ``kind`` tab; with ``toggle`` and the tab already shown, go back to where the person
    came from. Returns False when Manual has no such tab (Director, or a deleted tab): the caller opens the window."""
    if not in_manual(window):
        return False
    target = tab(kind)
    if target is None:
        return False
    if active(window, kind):
        if toggle:
            back = bpy.data.workspaces.get(_came_from.get(window.as_pointer(), ""))
            if back is not None and tab_kind(back) is None:
                window.workspace = back
        return True
    if tab_kind(window.workspace) is None:
        _came_from[window.as_pointer()] = window.workspace.name
    window.workspace = target
    return True


def came_from(window):
    return _came_from.get(window.as_pointer()) if window is not None else None


def add_tab(window, kind):
    """+ Add Workspace ▸ Angelo: switch to the tab when the file has it (never a ".001"), else add it (also back
    after the person deleted it: asking for it is the person's own choice)."""
    if tab(kind) is not None:
        window.workspace = tab(kind)
        return
    job, held = timer_jobs.track(build(window, [kind])), live_target.window_ref(window)   # Outlives the call (SW2-H-1).

    def step():
        if timer_jobs.aborted(job):   # A load, a quit or unregister ended it (timer_jobs): it never finished.
            return None
        try:
            return next(job)
        except StopIteration:
            target, shown = tab(kind), held()
            if target is not None and shown is not None:
                shown.workspace = target
            return None
        except Exception as error:   # As ensure's: the tab is not added, the timer ends cleanly.
            print("Angelo: the %s tab was not added:" % TABS[kind], error)
            job.close()
            return None
    bpy.app.timers.register(step, first_interval=0.01)


class ANGELO_OT_workspace_tab_add(Operator):
    """Show this workspace, adding it when this file has none"""
    bl_idname = "angelo.workspace_tab_add"
    bl_label = "Add Angelo Workspace"
    bl_options = {'INTERNAL'}
    kind: EnumProperty(items=(("studio", "", ""), ("flows", "", "")), options={'SKIP_SAVE'})

    def execute(self, context):
        if context.window is None or _building:
            return {'CANCELLED'}
        add_tab(context.window, self.kind)
        return {'FINISHED'}


class ANGELO_MT_workspace_add(Menu):
    """+ Add Workspace's Angelo section (C: workspace_edit.cc draws it before General when it is registered)."""
    bl_label = "Angelo"

    def draw(self, context):
        from . import flow_open, glyph_tint, studio_toggle
        from .ui_text import iface_
        layout = self.layout
        layout.operator("angelo.workspace_tab_add", text=iface_(studio_toggle.MENU_LABEL), translate=False,
                        icon_value=studio_toggle.icon_id(False, glyph_tint.colour("menu_item"))).kind = "studio"
        layout.operator("angelo.workspace_tab_add", text=iface_(flow_open.MENU_LABEL), translate=False,
                        icon_value=flow_open.icon_id(glyph_tint.colour("menu_item"))).kind = "flows"


CLASSES = (ANGELO_OT_workspace_tab_add, ANGELO_MT_workspace_add)


@bpy.app.handlers.persistent
def _loaded(_):
    _came_from.clear()
    window = live_windows.first(bpy.context.window_manager)
    if window is not None:
        ensure(window)


def register():
    from . import studio_tab_build
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(_loaded)
    studio_tab_build.register()   # Its load and quit handlers.
    # Never during registration: bpy.data is restricted there (_RestrictData), and reading it failed the whole
    # add-on's register. The first check runs from a timer, once the file is in place.
    bpy.app.timers.register(_first_check, first_interval=0.0)   # The first pass after registration.


def _first_check():
    if bpy.app.background:
        return None
    window = live_windows.first(bpy.context.window_manager)
    if window is not None:
        ensure(window)
    return None


def unregister():
    from . import studio_tab_build
    if _loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_loaded)
    studio_tab_build.unregister()   # A running build ends: the file stays, so it rolls back.
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
