# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A window, scene or view layer held across a tick or an operator call, read only while that same one exists.

A load (``wm.read_homefile``, ``wm.open_mainfile``, factory settings, a script that does one), a closed window or a
deleted scene frees what a held ``bpy.types.Window``, ``Scene`` or ``ViewLayer`` points at, and Python is not told: a
later read is a use-after-free. macOS read the freed memory and went on; Windows faulted (NTSTATUS 0xC0000005, the C2
build on 2026-10-25: ``test_blender_script_document_replaced`` in ``document_scene.shown_scene`` after an approved
script replaced the document, ``test_blender_parallel_lead`` in ``BlenderExecutor.precondition`` after a load). So a
holder keeps an identity, never the object, and each read finds the live object in Blender's own lists, or None.

Identities (break-it SW-H-2: an address alone is not a lifetime; a new window can take a closed one's address):

- A window: its address, its window manager's ``session_uid`` (a load that replaces the window manager, and so every
  window, gives the new one a new number; a load that keeps it keeps its windows), and a lifetime token. A survey
  runs at each bind and each read (main thread only), never between them: it gives a window a new token when its
  address is new or when, on the same workspace, it shows another screen that is not its own maximized area or the
  way back from one (Blender names that screen "<screen>-nonnormal"). An expired binding stays expired.
  Residue, for 1.1 (a native per-window id, an RNA change and a rebuild): between two surveys, however many ticks
  apart, a window closed and a new one at its address is not seen when the new one is on another workspace, or on
  the same workspace showing a screen at the closed screen's address or one named as its maximized area. Such a
  binding then resolves to the new window: a live window, never freed memory, but not the original one.
- A scene: its ``session_uid``, which Blender gives each ID once per process from one counter (a load, an append or a
  link takes new numbers; Undo keeps them), so it is never another scene's.
- A view layer: its scene's identity and its address among that scene's view layers; once gone, gone.

Use the class attributes (``window = WindowAttribute()``, ``scene = SceneAttribute()``, ``layer =
ViewLayerAttribute()``) and assign and read as before, or ``window_ref``/``scene_ref`` for a closure. Anything that is
not Blender's own (None, a host test's stand-in, a worker's remote window) is kept as it is. No bpy at import.
"""

import itertools
import threading

_windows = {}            # Window address -> (token, workspace, screen, screen name), from the last survey.
_tokens = itertools.count(1)


def _blender(value, kind):
    return type(value).__module__ == "bpy.types" and type(value).__name__ == kind


def _pointer(value):
    return value.as_pointer() if value is not None else 0


def _main():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("live_target reads Blender's windows on the main thread only")


def survey(windows):
    """Bring the lifetime tokens up to date with ``windows`` (every window of the window manager). Main thread only: it
    reads Blender's windows and the shared token table (break-it SW2-H-2: a worker lane surveyed them)."""
    _main()
    present = {}
    for window in windows:
        shown = window.screen
        address, workspace, screen = window.as_pointer(), _pointer(window.workspace), _pointer(shown)
        name = shown.name if shown is not None else ""
        token, was_workspace, was_screen, was_name = _windows.get(address, (None, None, None, ""))
        if not screen:   # Between screens (being created or closed): what it shows is not known yet.
            screen, workspace, name = was_screen or 0, was_workspace or workspace, was_name
        # Another screen on the same workspace is another window, unless it is this one's maximized area or the way
        # back from it: Blender names that screen after the window's own ("<screen>-nonnormal", screen_edit.cc).
        toggled = name.startswith(was_name + "-nonnormal") or was_name.startswith(name + "-nonnormal")
        replaced = (token is not None and workspace == was_workspace and screen and was_screen and screen != was_screen
                    and not (name and was_name and toggled))
        present[address] = (next(_tokens) if token is None or replaced else token, workspace, screen, name)
    _windows.clear()
    _windows.update(present)


def window_ref(window):
    """A callable returning this same window while it exists, else None (closed, replaced, or another document)."""
    if not _blender(window, "Window"):
        return lambda: window
    import bpy
    from . import live_windows
    _main()
    manager = bpy.context.window_manager
    survey(live_windows.every_window(manager))
    address, document = window.as_pointer(), manager.session_uid
    token = _windows[address][0] if address in _windows else None

    def live():
        _main()
        manager = bpy.context.window_manager
        if token is None or manager is None or manager.session_uid != document:
            return None   # Another window manager: a load replaced it, and every window it had.
        windows = live_windows.every_window(manager)
        survey(windows)
        if _windows.get(address, (None,))[0] != token:
            return None
        return next((candidate for candidate in windows if candidate.as_pointer() == address), None)
    return live


def scene_ref(scene):
    """A callable returning this same scene while it exists in the file (by its session uid), else None."""
    if not _blender(scene, "Scene"):
        return lambda: scene
    uid = scene.session_uid

    def live():
        import bpy
        return next((candidate for candidate in bpy.data.scenes if candidate.session_uid == uid), None)
    return live


def view_layer_ref(layer):
    """A callable returning this same view layer while it and its scene exist, else None."""
    if not _blender(layer, "ViewLayer"):
        return lambda: layer
    owner, address, gone = scene_ref(layer.id_data), layer.as_pointer(), [False]

    def live():   # Once gone, gone: a later layer at its address in the same scene is another one.
        scene = None if gone[0] else owner()
        found = None if scene is None else next(
            (candidate for candidate in scene.view_layers if candidate.as_pointer() == address), None)
        gone[0] = found is None
        return found
    return live


class Gone(RuntimeError):
    """A held window, scene or view layer is gone (``require``)."""


def require(held, message):
    """``held()``, or ``Gone(message)`` once it is gone: for a generator resumed across timer passes."""
    value = held()
    if value is None:
        raise Gone(message)
    return value


class _Attribute:
    make = None

    def __set_name__(self, owner, name):
        self.slot = "_live_" + name

    def __set__(self, instance, value):
        instance.__dict__[self.slot] = type(self).make(value)

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        ref = instance.__dict__.get(self.slot)
        return None if ref is None else ref()


class WindowAttribute(_Attribute):
    """A class attribute holding a window by its identity: each read is that live window, or None once it is gone."""
    make = staticmethod(window_ref)


class SceneAttribute(_Attribute):
    """A class attribute holding a scene by its identity: each read is that live scene, or None once it is gone."""
    make = staticmethod(scene_ref)


class ViewLayerAttribute(_Attribute):
    """A class attribute holding a view layer by its identity: each read is that live layer, or None once gone."""
    make = staticmethod(view_layer_ref)
