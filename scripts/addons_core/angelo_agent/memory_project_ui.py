# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host lifecycle receipts for public brief continuity, never authorization.

Verify only at chat creation, explicit preparation, native Save and load/restore
boundaries. Never hash a project in a redraw or ordinary UI-pump tick.
"""

from pathlib import Path
import queue
import threading

from .memory_projects import admit_saved_project, capture_loaded_project

_receipts = {}
_pending = {}
_ready = queue.Queue(maxsize=8)
_worker = None
_generation = 0


def current_receipt(ui, controller, document=None):
    document = document if document is not None else ui.document_key()
    path = ui.bpy.data.filepath
    profile = str(controller.store.directory.resolve())
    key = (path, repr(document), profile)
    receipt = _receipts.get(key)
    if receipt is not None and receipt.valid_for(document, profile):
        return receipt
    try:
        loaded = capture_loaded_project(ui.main_window(), document, profile)
        receipt = admit_saved_project(path, document, profile, loaded_project=loaded)
    except (OSError, ValueError, TypeError):
        _receipts.pop(key, None)
        return None
    _receipts[key] = receipt
    while len(_receipts) > 8:
        _receipts.pop(next(iter(_receipts)))
    return receipt


def bind_current(ui, controller, *, persist=True):
    if controller.document != ui.document_key() or controller.document_invalid:
        return False
    receipt = current_receipt(ui, controller)
    if receipt is None or not controller.bind_project(receipt):
        return False
    if persist:
        try:
            controller.save()
        except (OSError, ValueError) as error:
            # The in-memory receipt remains verified; persistence failure must
            # not invent another project or grant any native-operation policy.
            controller.project_binding_warning = str(error)
    return True


def schedule(ui, controller):
    """Lookup/draw may queue metadata, but never open/hash a project here."""
    if getattr(controller, "external_client", ""):
        return  # Read-only CLI conversations never carry a project brief.
    document = ui.document_key()
    if controller.document != document or controller.document_invalid:
        return
    key = controller.state['id']
    _pending[key] = (controller, ui.bpy.data.filepath,
        document, str(controller.store.directory))
    while len(_pending) > 8:
        _pending.pop(next(iter(_pending)))


def pump(ui):
    """One file-only worker; apply at most one receipt on the native UI thread."""
    global _worker
    try:
        generation, controller, document, profile, path, receipt = _ready.get_nowait()
    except queue.Empty:
        pass
    else:
        if (generation == _generation and receipt is not None and not controller.document_invalid
                and controller.document == document == ui.document_key()
                and ui.bpy.data.filepath == path):
            try:
                rebound = controller.bind_project(receipt)
            except (OSError, ValueError, TypeError):
                rebound = False
            if rebound:
                _receipts[(path, repr(document), profile)] = receipt
                while len(_receipts) > 8:
                    _receipts.pop(next(iter(_receipts)))
                try:
                    controller.save()
                except (OSError, ValueError) as error:
                    controller.project_binding_warning = str(error)
    if _worker is not None and _worker.is_alive():
        return
    _worker = None
    if not _pending:
        return
    identity = next(iter(_pending))
    controller, path, document, supplied_profile = _pending.pop(identity)
    if controller.document != document or ui.document_key() != document or ui.bpy.data.filepath != path:
        return
    try:
        profile = str(Path(supplied_profile).resolve())
        loaded = capture_loaded_project(ui.main_window(), document, profile)
    except (OSError, ValueError, TypeError):
        return
    generation = _generation
    def verify():
        # Captured strings/identity only. This worker never reads bpy/context.
        try:
            receipt = admit_saved_project(path, document, profile, loaded_project=loaded)
        except (OSError, ValueError, TypeError):
            receipt = None
        try:
            _ready.put_nowait((generation, controller, document, profile, path, receipt))
        except queue.Full:
            pass  # A lifecycle clear superseded the optional verification.
    _worker = threading.Thread(target=verify, daemon=True, name='AngeloProjectContinuity')
    _worker.start()


def clear():
    global _generation
    _generation += 1
    _receipts.clear()
    _pending.clear()
    while True:
        try:
            _ready.get_nowait()
        except queue.Empty:
            break
