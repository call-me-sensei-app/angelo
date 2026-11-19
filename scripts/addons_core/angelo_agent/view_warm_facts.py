# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What ``view_warm`` reads of a view's scene and its progress: why it is not ready, the depsgraph's evaluation count,
whether this build reads the shader state, an ID's address and whether a scene was reloaded under the same names.
Moved from ``view_warm`` unchanged, to keep that module within its size limit."""


def _reason(progress):
    """Why a view is not ready: its shaders are compiling, its first draws are owed, or its state cannot be read."""
    current = progress.get("state")
    if current is None:
        return "unreadable shader state"
    return "compiling" if current[1] or current[2] else "first draws owed"


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


def _address(value):
    return getattr(value, "as_pointer", lambda: id(value))()


def _reloaded(window, names):
    """Whether the window shows the same-named scene and view layer as ``names`` (an undo or a reload re-made them)."""
    try:
        return None not in names and (window.scene.name_full, window.view_layer.name) == names
    except (AttributeError, ReferenceError):
        return False
