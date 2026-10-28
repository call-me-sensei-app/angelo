# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Who a ``bpy.app.handlers`` callback belongs to: Blender's own scripts or Angelo's package, else an add-on.

Ownership is decided by the file of the handler's code object, never by its ``__module__``, which any add-on can
set: a handler without Python code, or defined anywhere else, is an add-on's. Used by Restore (which add-on load
handlers to suspend) and by the capture's scene map (an add-on save handler can rename scenes around the write).
"""

from pathlib import Path


def defining_file(callback):
    """The file of the code a handler runs (its code object's, not its writable ``__module__``), or None."""
    target = getattr(callback, 'func', callback)  # functools.partial
    target = getattr(target, '__func__', target)  # A bound method.
    code = getattr(target, '__code__', None) or getattr(getattr(type(target), '__call__', None), '__code__', None)
    return getattr(code, 'co_filename', None) or None


def bundled(bpy, callback):
    """Whether a handler's code lives in Blender's own scripts folder or in Angelo's package folder."""
    path = defining_file(callback)
    if not path:
        return False
    roots = [Path(__file__).resolve().parent]
    system = getattr(getattr(bpy, 'utils', None), 'system_resource', None)
    try:
        if callable(system):
            roots.append(Path(system('SCRIPTS')).resolve())
        defined = Path(path).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return any(defined.is_relative_to(root) for root in roots)
