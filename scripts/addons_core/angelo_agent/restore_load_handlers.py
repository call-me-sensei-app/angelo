# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Add-on load handlers taken out for one safety reload after a failed Restore (``checkpoint_restore_ui``).

An add-on whose load handler changes the document on every load makes the native safety reload refuse; the reload
is tried once more with those handlers suspended, and the notice names their add-ons as candidates.
"""

import sys
from pathlib import Path

from . import handler_owner


def bundled(ui, callback):
    """Blender's or Angelo's own handler, by its code's file (``handler_owner.bundled``)."""
    return handler_owner.bundled(ui.bpy, callback)


def suspend(ui):
    """Take add-on load handlers (not Blender's bundled ones, not Angelo's) out for one reload."""
    saved = []
    for name in ('load_pre', 'load_post'):
        callbacks = getattr(ui.bpy.app.handlers, name)
        removed = [callback for callback in list(callbacks) if not bundled(ui, callback)]
        for callback in removed:
            callbacks.remove(callback)
        saved.append((callbacks, removed))
    return saved


def resume(saved):
    """Put suspended persistent handlers back, as a file load keeps persistent ones."""
    for callbacks, removed in saved:
        for callback in removed:
            if hasattr(callback, '_bpy_persistent') and callback not in callbacks:
                callbacks.append(callback)


def owners(saved):
    """The add-on names of suspended handlers, for the notice (data, never English prose).

    A handler's ``__module__`` names its add-on only when that module's file is the handler's own code
    file (an extension's ``bl_ext.<repository>.<name>`` gives ``<name>``); otherwise, as for a script,
    the code file's name stands in.
    """
    names = []
    for _, removed in saved:
        for callback in removed:
            module, path = str(getattr(callback, '__module__', '') or ''), handler_owner.defining_file(callback)
            loaded = getattr(sys.modules.get(module), '__file__', None) if module not in ('', '__main__') else None
            try:
                trusted = bool(loaded and path) and Path(loaded).resolve() == Path(path).resolve()
            except (OSError, ValueError, TypeError):
                trusted = False
            parts = module.split('.')
            name = (parts[2] if parts[0] == 'bl_ext' and len(parts) > 2 else parts[0]) if trusted else Path(path or '?').name
            if name not in names:
                names.append(name)
    return names
