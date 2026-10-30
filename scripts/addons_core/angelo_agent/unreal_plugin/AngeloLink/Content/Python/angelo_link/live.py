# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
"""Angelo Link's live layer, seen from Python: the Angelo Link Live plugin's native module (C++,
``unreal.AngeloLinkLiveLibrary``) shows Angelo's live .glb in place of the linked asset's instances in the open level,
without changing the level or the asset, and keeps a background editor ticking while a session is live.

When that plugin isn't enabled (no binary for this engine version), ``available()`` is False and the link stays
durable: Angelo is told so in the handshake and sends no live scenes.
"""

import os
import shutil
import tempfile

import unreal

MIN_VERSION = (0, 1)   # The oldest native module this Python drives.
_state = {"folder": None}


def library():
    """The native live layer, or None when it isn't loaded (or is older than this Python)."""
    native = getattr(unreal, "AngeloLinkLiveLibrary", None)
    if native is None:
        return None
    try:
        version = tuple(int(part) for part in str(native.live_version()).split(".")[:2])
    except Exception:
        return None
    return native if version >= MIN_VERSION else None


def available():
    return library() is not None


def show(package, payload):
    """Show Angelo's live .glb ``payload`` in place of ``package``'s instances: (instances showing it, or -1 with the
    error text when it couldn't be shown).
    The file is written to an owner-only folder of this editor's (GLTFCore reads files, not memory) and replaced in
    one step, so the native reader never sees half a scene."""
    native = library()
    if native is None:
        return -1, "This editor has no live layer."
    if _state["folder"] is None or not os.path.isdir(_state["folder"]):
        _state["folder"] = tempfile.mkdtemp(prefix="angelo-link-live-")   # Mode 0700; the user's own %TEMP% on Windows.
        _on_exit()
    path = os.path.join(_state["folder"], "live.glb")
    try:
        with open(path + ".part", "wb") as handle:
            handle.write(payload)
        os.replace(path + ".part", path)
    finally:
        if os.path.exists(path + ".part"):
            os.remove(path + ".part")
    count, error = native.show_preview(path, package)
    return int(count), str(error or "")


def _remove_files():
    folder, _state["folder"] = _state["folder"], None
    if folder:
        shutil.rmtree(folder, ignore_errors=True)


def _on_exit():
    """Remove the scene files when the editor quits (the link is never stopped then). Files only: the native module
    drops its previews itself before the editor tears its world down."""
    if not _state.get("exit_hooked"):
        _state["exit_hooked"] = True
        try:
            unreal.register_python_shutdown_callback(_remove_files)
        except Exception:
            import atexit
            atexit.register(_remove_files)


def count():
    native = library()
    return int(native.preview_count()) if native is not None else 0


def clear():
    native = library()
    if native is not None:
        native.clear_preview()


def set_live(on):
    native = library()
    if native is not None:
        native.set_session_live(bool(on))


def shutdown():
    """End everything live: previews, the unthrottled tick, the scene files."""
    clear()
    set_live(False)
    _remove_files()
