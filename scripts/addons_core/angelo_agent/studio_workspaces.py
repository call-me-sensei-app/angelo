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

import bpy
from bpy.props import EnumProperty
from bpy.types import Menu, Operator

TABS = {"studio": "Media Studio", "flows": "Generation Flows"}   # i18n: workspace data names (product names), never translated
TAB_KEY = "angelo_tab"                # On a tab's workspace: which destination it is.
FLAG = "angelo_studio_tabs"           # On the file's other workspaces: JSON list of the tabs added to it once.
_came_from = {}                       # Window pointer -> the workspace name to go back to.
_building = False
_holding = None   # The tab build's timer step while it holds ui._layout_timer.
_leaving = False  # A file load is ending the running build: nothing to roll back into a file that is going away.
_kept_modes = {}  # Workspace name -> its own object mode, while a build gives it the window's (``_same_mode``).
last_build = {}   # {"seconds", "kinds", "done"} of the last build: how long the window showed other tabs (GUI suites).
QUIET = 1.0       # Seconds the start-up must stay quiet before a build switches the window's tabs.
ATTEMPTS = 3      # Builds tried in one session; a build that stopped part-way is rolled back and tried again.
STARTUP_LIMIT = 15.0   # Seconds the start-up's cards wait at most for the start-up build (never forever).
_startup = {"pending": True, "until": None}   # The session's first build: part of the start-up, under its cover.


def tab_kind(workspace):
    return workspace.get(TAB_KEY) if workspace is not None else None


def tab(kind):
    return next((w for w in bpy.data.workspaces if tab_kind(w) == kind), None)


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


def _switched(window, workspace, passes=20):
    """Timer steps until ``window`` shows ``workspace``: Blender applies a switch on its next event-loop pass, so each
    step is one pass (one drawn frame), never a fixed delay. Yields nothing more once it shows."""
    for _ in range(passes):
        if window.workspace == workspace:
            return
        yield 0.0
    raise RuntimeError("the window did not switch to %r" % workspace.name)


def _layout_idle():
    """No other part of Angelo is changing this window's layout (Director's start-up, the default layout)."""
    from . import director, ui
    return director._timer is None and ui._layout_timer in (None, _holding)


def _switch(window, workspace):
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
    if window.workspace != workspace:
        window.workspace = workspace
        yield from _switched(window, workspace)


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
    import time
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
    except (RuntimeError, TypeError) as error:
        print("Angelo: a half-made %r tab could not be removed:" % name, error)
        if name in bpy.data.workspaces and TAB_KEY in bpy.data.workspaces[name]:
            del bpy.data.workspaces[name][TAB_KEY]   # At least never taken for a finished tab.


def build(window, kinds):
    """A generator (timer steps): add each tab in ``kinds`` and put the strip in order; the window ends where it was.

    Blender's workspace operators (duplicate, close area, reorder) act on the window's active workspace, so each
    step shows the tab it works on; every step waits exactly for its switch to apply (``_switched``) and acts at
    once, so the whole build shows for a few frames instead of the second it took with fixed delays (1.0.5 preview:
    the Media Studio tab flashed ~1-2 s after startup). Building with no switch at all needs a native API (1.0.6)."""
    global _building
    from .director_layout import is_director_workspace
    import time
    _building = True
    home, started, made, done = window.workspace, time.monotonic(), [], False
    try:
        for kind in kinds:
            existing = bpy.data.workspaces.get(TABS[kind])
            if existing is not None and tab_kind(existing) is None:   # Never a ".001": adopt the one of that name.
                existing[TAB_KEY] = kind
                _record(added_once() | {kind})
                continue
            source = _manual_source(window)
            if source is None:
                return
            _same_mode(source, home)
            yield from _switch(window, source)
            before = {w.as_pointer() for w in bpy.data.workspaces}
            with bpy.context.temp_override(window=window):
                bpy.ops.workspace.duplicate()
            created = next((w for w in bpy.data.workspaces if w.as_pointer() not in before), None)
            if created is None:
                return
            created.name, created[TAB_KEY] = TABS[kind], kind
            made.append(created.name)
            yield from _switched(window, created)   # The duplicate queued its own switch; never a second one.
            keep = max(window.screen.areas, key=lambda a: a.width * a.height).as_pointer()
            while len(window.screen.areas) > 1:   # One editor fills the tab (as director_layout.build closes them).
                area = next(a for a in window.screen.areas if a.as_pointer() != keep)
                with bpy.context.temp_override(window=window, area=area):
                    if not bpy.ops.screen.area_close.poll() or bpy.ops.screen.area_close() != {'FINISHED'}:
                        return
                yield 0.0   # The join applies on the next pass.
            fill(window, kind)
            made.remove(created.name)   # Finished: it stays.
            _record(added_once() | {kind})
        if not place():   # A build without WorkSpace.angelo_move_after: the operator, one switch per tab.
            for workspace in [tab("flows"), tab("studio")] + [w for w in bpy.data.workspaces
                                                             if is_director_workspace(w)]:
                if workspace is not None:
                    _same_mode(workspace, home)
                    yield from _switch(window, workspace)
                    with bpy.context.temp_override(window=window):
                        bpy.ops.workspace.reorder_to_front()
        done = True
    finally:
        _building = False
        if _leaving:
            made, home = [], None
        try:
            # Home first: deleting the tab the window shows switches it to a neighbour, with that one's object mode.
            if home is not None and home.name in bpy.data.workspaces and window.workspace != home:
                window.workspace = home   # Wherever the build stopped, the window ends where it was: mode included.
            for name in made:   # Stopped part-way (an operator refused, an error, the window closed): roll back.
                if name in bpy.data.workspaces:
                    _remove(window, bpy.data.workspaces[name])
        except ReferenceError:   # The file or the window went away (a load, a quit): nothing left to put back.
            pass
        last_build.update(seconds=round(time.monotonic() - started, 3), kinds=list(kinds), done=done,
                          rolled_back=list(made))


def strip():
    """The workspaces as the tab strip shows them, or None on a build without ``WorkSpace.angelo_tab_order``.
    ``bpy.data.workspaces`` is always alphabetical; Blender's tabs sort by the workspace's order, then by name."""
    workspaces = list(bpy.data.workspaces)
    if workspaces and not hasattr(workspaces[0], "angelo_tab_order"):
        return None
    return sorted(workspaces, key=lambda w: (w.angelo_tab_order, w.name))


def in_order():
    """Whether the strip reads Director · Media Studio · Generation Flows first (studio_entry_points.rst §3), the
    pair never split by another tab; a tab the file lacks is skipped. False when the order cannot be read
    (``place`` is idempotent)."""
    from .director_layout import is_director_workspace
    shown = strip()
    if shown is None:
        return False
    names = [w.name for w in shown]
    wanted = [w.name for w in bpy.data.workspaces if is_director_workspace(w)][:1] + [
        t.name for t in (tab("studio"), tab("flows")) if t is not None]
    return names[:len(wanted)] == wanted


def place():
    """Put Media Studio right after Director (first without one) and Generation Flows right after it, without
    switching the window (``WorkSpace.angelo_move_after``, 1.0.5 native). Switching to Director to move it, as
    ``workspace.reorder_to_front`` needs, enters Director mode in Manual and left the pair split ("Media Studio ·
    Director · Generation Flows", 1.0.5 review). Returns False on a build without the call."""
    from .director_layout import is_director_workspace
    studio, flows = tab("studio"), tab("flows")
    first = studio or flows
    if first is None:
        return True
    if not hasattr(first, "angelo_move_after"):
        return False
    director = next((w for w in bpy.data.workspaces if is_director_workspace(w)), None)
    if director is not None:
        director.angelo_move_after(anchor=None)
    anchor = director
    for workspace in (studio, flows):
        if workspace is not None:
            workspace.angelo_move_after(anchor=anchor)
            anchor = workspace
    return True


def fill(window, kind):
    """The tab's one editor, without Blender's header, sidebar or toolbar: Media Studio, or the flow canvas."""
    from . import flow_nodes
    from .editor_types import mark_angelo_owned, select_console, set_editor
    area = window.screen.areas[0]
    if kind == "studio":
        select_console(window, area, generative=True)
    else:
        set_editor(window, area, ui_type=flow_nodes.TREE)
    mark_angelo_owned(window, area)
    from . import chrome_guard
    chrome_guard.guard(area, window)   # Hidden now and kept hidden by events (no polling).
    if kind == "flows":
        from . import flow_window
        flow_window.prepare_area(window, area)


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
    if not kinds or _building:
        return
    quiet_since, attempts = [None], [0]

    def wait_then_build():
        target = next((w for w in bpy.context.window_manager.windows if w.as_pointer() == pointer), None)
        if target is None or target.screen.is_temporary or not missing():
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
                bpy.app.timers.register(wait_then_build, first_interval=1.0)
        from .studio_tab_build import run
        run(target, retry)
        return None
    pointer = window.as_pointer()
    # The start-up build at the first pass: its switches free any popup open by then (1.0.5 RC, app_identity: a
    # Help menu opened a second after the add-on was enabled closed under the build).
    bpy.app.timers.register(wait_then_build, first_interval=0.0 if startup_build_pending() else 0.5)


def in_manual(window):
    from .director_layout import is_director
    return window is not None and not is_director(window)


def active(window, kind):
    """Whether ``window`` shows the ``kind`` tab (the top-bar button is pressed)."""
    return window is not None and tab_kind(window.workspace) == kind


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
    job = build(window, [kind])

    def step():
        try:
            return next(job)
        except StopIteration:
            target = tab(kind)
            if target is not None:
                window.workspace = target
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
    for window in bpy.context.window_manager.windows:
        ensure(window)
        break


def register():
    from . import studio_tab_build
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.handlers.load_post.append(_loaded)
    bpy.app.handlers.load_pre.append(studio_tab_build.load_pre)
    # Never during registration: bpy.data is restricted there (_RestrictData), and reading it failed the whole
    # add-on's register. The first check runs from a timer, once the file is in place.
    bpy.app.timers.register(_first_check, first_interval=0.0)   # The first pass after registration.


def _first_check():
    if bpy.app.background:
        return None
    windows = bpy.context.window_manager.windows if bpy.context.window_manager else []
    if windows:
        ensure(windows[0])
    return None


def unregister():
    from . import studio_tab_build
    if _loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_loaded)
    if studio_tab_build.load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(studio_tab_build.load_pre)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
