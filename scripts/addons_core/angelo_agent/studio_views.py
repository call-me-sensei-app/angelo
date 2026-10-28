# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Media Studio's state (its tab, drafts and references): which state an area shows. Main thread.

A Studio window (``studio_toggle`` opened it for Studio alone) holds one state, whatever area Blender shows in it.
Blender moves a child window with its parent window's workspace, so when Director and Manual mode switch workspaces,
or Manual's tab build passes the main window through its tabs, the Studio window is shown another screen with
another area; keyed by area, Studio started over on the Image tab with no draft and no references. Keyed by the
window, the state stays, and switching back finds it too. Opening a Studio window starts it fresh, even at the
address of one closed before, and closing it forgets it.

Every other Studio area (Manual's Media Studio tab, a Studio the person placed in a layout) keeps its own state, by
area. The pump drops the state of areas and windows that are gone (a freed address can be given to another area or
window), and a file load forgets all of it before the file is read, the windows kept or not: nothing carries over.
"""

import bpy

from . import live_windows

_windows = {}    # Studio window pointer -> its state
_handlers = []   # the load_pre handler, once registered
_hooks = []      # what else a file load must stop first (Studio's requests in flight: ``on_load``)


def defaults():
    return {"kind": "IMAGE", "collapsed": None, "folder": "ALL", "query": "", "prompt": "", "references": [],
            "models": {}, "aspect": "1:1", "resolution": "2K", "quality": "auto", "background": "auto", "size": "",
            "polycount": "10000", "texture": True, "focus": None, "caret": 0, "selection": None, "create_scroll": 0,
            "grid_scroll": 0, "folder_scroll": 0, "notice": "", "enhanced": None, "expanded_prompt": ""}


def _opened():
    from . import studio_toggle
    return studio_toggle._opened


def _studio_window(area, is_studio):
    """The Studio window whose (first) Studio area ``area`` is, or None."""
    pointer = area.as_pointer()
    opened = _opened()
    for window in live_windows.windows(bpy.context.window_manager):
        if window.as_pointer() in opened:
            first = next((a for a in live_windows.areas(window) if is_studio(a)), None)
            if first is not None and first.as_pointer() == pointer:
                return window
    return None


def view(views, area, is_studio):
    """The state ``area`` shows. ``views`` (Studio's ``_views``) also lists a Studio window's state under the area
    showing it, for the code that finds a state by area pointer (results of AI Enhance, Fit to My Image)."""
    _watch(views)
    pointer = area.as_pointer()
    window = _studio_window(area, is_studio)
    if window is None:
        return views.setdefault(pointer, defaults())
    state = _windows.get(window.as_pointer())
    if state is None:   # A view made just before the window was known keeps what it has.
        state = _windows[window.as_pointer()] = views.get(pointer) or defaults()
    views[pointer] = state
    return state


def forget_window(views, window):
    """Opening or closing a Studio window: its state goes, and so do the areas' entries that showed it."""
    state = _windows.pop(window, None)
    if state is not None:
        for pointer in [p for p, v in views.items() if v is state]:
            views.pop(pointer)


def track(views, busy=()):
    """Studio's pump: drop what belongs to closed windows and freed areas; ``_opened`` keeps live windows only. An
    area in ``busy`` (a request in flight answers to it) keeps its entry until the answer has landed."""
    live = {w.as_pointer() for w in live_windows.every_window(bpy.context.window_manager)}   # Between screens: kept.
    opened = _opened()
    opened.intersection_update(live)
    for window in [w for w in _windows if w not in opened]:
        forget_window(views, window)
    alive = {a.as_pointer() for screen in bpy.data.screens for a in live_windows.screen_areas(screen)}
    for pointer in [p for p in views if p not in alive and p not in busy]:
        views.pop(pointer)


def on_load(hook):
    """Run ``hook`` before a file loads, ahead of forgetting the states (Studio cancels its requests in flight)."""
    if hook not in _hooks:
        _hooks.append(hook)


def _forget(*_args):
    """Before a file loads: every state goes, so the loaded file's areas (or the kept windows) start fresh."""
    for hook in _hooks:
        hook()
    for views in _handlers[1:]:
        views.clear()
    _windows.clear()


_forget._bpy_persistent = True   # bpy.app.handlers.persistent: kept across the loads it watches.


def _watch(views):
    if not _handlers:
        bpy.app.handlers.load_pre.append(_forget)
        _handlers.extend((_forget, views))


def clear(views):
    """Studio's unregister."""
    views.clear()
    _windows.clear()
    _hooks.clear()
    if _forget in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_forget)
    _handlers.clear()
