# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""After a Restore, the document a person sees and saves is the one open at the Restore, never the hidden working copy.

Blender opens a document only from a file, so Restore (and Undo restore, and a safety reload) opens a private working
copy of the snapshot (``<state>/checkpoints/<session>/recoveries/recovered-<uuid>.blend``) and leaves the person's
file untouched. ``begin`` has the native side remember the document open just before the load (its path, the relative
spelling of its paths, packed files included, and its compression); ``adopt`` then makes the working copy that
document (native, ``WM_angelo_adopt_restored_document``): the title bar, Save, the quit prompt and every chat name that
file, an Untitled document stays Untitled, and nothing writes the file until the person confirms the first Save over it
(doc/angelo/checkpoint_policy.rst, "The document after a Restore"). ``adopt`` takes no path, so nothing a snapshot
recorded can reach it. Afterwards ``bpy.data.filepath`` is the person's document path everywhere. Main thread only.
"""

import os


def _native(name):
    try:
        import _bpy
    except ImportError:
        return None
    return getattr(getattr(_bpy, "_angelo_checkpoint", None), name, None)


def set_state_root(root):
    """Where Restore's working copies live (``<root>/checkpoints``): only a file there is ever treated as one."""
    native = _native("set_state_root")
    if native is not None and root:
        native(os.path.realpath(str(root)))


def begin(state_root=None):
    """Remember the document open now, just before a Restore replaces it, and where its working copies live (none
    without the native step)."""
    set_state_root(state_root)
    native = _native("begin_restore")
    if native is not None:
        native()


def adopt():
    """Make the working copy a Restore just opened the document ``begin`` remembered; True.

    Raises ``ValueError`` when the open document is not a working copy or the remembered document is not a project
    file, so the caller reports a recovery copy. None without the native step (only a host test has none).
    """
    native = _native("adopt_restored_document")
    return None if native is None else native()


def is_recovery_copy(path):
    """Whether ``path`` is a Restore's private working copy (False without the native step)."""
    native = _native("is_recovery_copy")
    return bool(native and path and native(path))


# A refused adoption: the person is editing a private recovery copy, and the notice says so (``recovery_copy_open``).
RECOVERY_COPY = 'The scene is open as a recovery copy, not your file; File > Save asks where to save it: '  # i18n: stored diagnostic


def adopt_or_say(warnings, redact=str):
    """``adopt``, a refusal appended to ``warnings`` as ``RECOVERY_COPY`` and its reason (``redact`` filters it)."""
    try:
        adopt()
    except ValueError as error:
        warnings.append(RECOVERY_COPY + redact(str(error)))


def copy_open(warnings):
    """Whether ``warnings`` say the person is left on a recovery copy."""
    return any(warning.startswith(RECOVERY_COPY) for warning in warnings)
