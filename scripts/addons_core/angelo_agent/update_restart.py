# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Restart to Update: keep the person's work safe, hand over to the installer, quit; and report the outcome.

Contract: doc/angelo/updates_and_reports.rst, "Installing updates". When the
person presses Restart to Update, ``begin``:

1. refuses while a restart would stop work (``work_guard``: an agent turn, a
   preview awaiting Accept, a Flow run, a generation, a render) or while another
   copy of this same Angelo runs; the card says which;
2. checks the prepared update again (``Preparer.still_valid``); a changed stage
   is prepared again instead of installed;
3. asks to save unsaved changes (``SaveCard``: Save and Restart, Don't Save,
   Cancel). An untitled file opens the File Browser, and a restored document's
   first Save over its file asks its question (``restored_document``); the
   restart continues only after the file was saved, and stops if it was not. Images with unsaved
   pixels are saved with the file, and the restart stops if one can't be. Asset catalogs with
   unsaved changes (Blender's own quit dialog asks about them too) are written by that save, with
   "Save modified asset catalogs" on for it whatever the quit dialog last left, and the restart
   stops if one is still unsaved (a library folder that can't be written); Don't Save discards
   them, as Blender's quit does;
4. starts the installer (``update_install``), waits until it reports it is
   running, and quits. The installer replaces the app once Angelo has exited
   and opens it again with the same file.

At the next launch ``handle_results`` reads what the installer did: a
successful install removes the previous app and the downloads, and one Windows
finishes only when it restarts (``update_install.needs_reboot``) says so once; a failed one is
told once and counted, and after ``update_ready.INSTALL_ATTEMPTS`` failures the
version is offered as a download instead.
"""

import os
import time
import sys
from pathlib import Path

import bpy

from . import (app_version, card_host, card_kit as kit, live_windows, update_card, update_flow, update_install, update_state,
               work_guard)
from . import update_restart_handoff as handoff
from .ui_text import iface_, n_, plural_

READY_TIMEOUT = 15.0
SAVE_WIDTH = 480   # Wide enough for Cancel, Don't Save and Save and Restart on one row in English.
SAVE_TITLE = n_("Save changes before restarting?")
SAVE_BODY = n_("Your changes to “{name}” have not been saved.")
SAVE_BODY_UNTITLED = n_("This file has not been saved yet.")
SAVE, DONT_SAVE, CANCEL = n_("Save and Restart"), n_("Don't Save"), n_("Cancel")
FAILED_TITLE = n_("Angelo {version} could not be installed")
FAILED_BODY = n_("Nothing was changed. Angelo will try again, or you can download the update and install it yourself.")
NOT_STARTED_TITLE = n_("The update could not start")
NOT_STARTED_BODY = n_("Angelo is still open and nothing was changed. Try again, or download the update instead.")
SAVE_FAILED_TITLE = n_("Angelo did not restart")
SAVE_FAILED_BODY = n_("Your file could not be saved, so Angelo kept it open. Save it, then restart.")
IMAGES_FAILED_BODY = n_("Some images could not be saved. Save or pack them in the Image Editor, then restart.")
CATALOGS_BODY = n_("Your asset catalogs have unsaved changes.")
CATALOGS_FAILED_BODY = n_("Some asset catalogs could not be saved. Save them in the Asset Browser, then restart.")
RECOVERY_COPY_BODY = n_("This scene is a recovery copy, not your file: save it with File > Save As")
REBOOT_TITLE = n_("Angelo {version} is installed")
REBOOT_BODY = n_("Restart Windows to finish the update: a file that was in use is replaced when Windows starts again.")
OTHER_TITLE = n_("Close the other Angelo windows first")
OTHER_BODY = n_("Another Angelo window is open from the same app. Close it, then restart to update.")
DOWNLOAD, CLOSE = n_("Download"), n_("Close", "Angelo")

_installer = None  # Sole Popen/native owner; handoff borrows this module, never copies its record.
DRAIN_TIMEOUT = 5.0

_watch = None   # A save in progress: {"offer", "ready", "saved_path", "failed", "opened", "asking", "catalogs"}


def _ready_module():
    from . import update_ready
    return update_ready


def _dirty_images():
    return [image for image in bpy.data.images if image.is_dirty]


def _native_jobs():
    try:
        from _bpy import _angelo_jobs
    except ImportError:
        return None
    return _angelo_jobs if hasattr(_angelo_jobs, "catalogs_unsaved") else None


def _catalogs_unsaved():
    """Whether a loaded asset library has catalog changes not written yet; False on a build that cannot tell."""
    native = _native_jobs()
    return bool(native is not None and native.catalogs_unsaved())


def _catalogs_with_file(enable):
    """Set whether saving the file writes modified asset catalogs; the setting before, or None (a build without it)."""
    native = _native_jobs()
    return None if native is None else bool(native.catalogs_save_with_file(enable))


def _other_instances(ready):
    if ready.family == "macos":
        from . import update_stage_mac
        executable = update_stage_mac.info(ready.install_root).get("CFBundleExecutable", "")
        return update_stage_mac.other_instances(ready.install_root, executable, os.getpid())
    from . import update_stage_win
    return update_stage_win.other_instances(bpy.app.binary_path, os.getpid())


def begin(offer, ready, host):
    """The card's Restart to Update. ``offer`` is the card's whole offer: ``target`` says what installs, and every
    card that comes back keeps both options. Every refusal leaves Angelo and the file as they were."""
    live = _ready_module()
    if ready is None:
        return
    from . import update_ui
    chosen = live.target(offer)
    if update_ui.policy_blocks() or live.automatic(chosen):   # Withdrawn since it was prepared: never installed.
        live.forget(chosen.release.version)
        update_ui.show_notice(offer)
        return
    if work_guard.reasons():
        host.redraw()   # The button is disabled already; the reason is on the card.
        return
    if _other_instances(ready):
        _notice(OTHER_TITLE, OTHER_BODY)   # Its files would be replaced under it.
        return
    if not live.preparer().still_valid(ready):
        live.forget(chosen.release.version)
        live.consider(offer, manual=True)   # Prepared again, then offered again.
        host.redraw()
        return
    images, catalogs = _dirty_images(), _catalogs_unsaved()
    if bpy.data.is_dirty or images or catalogs:
        name = Path(bpy.data.filepath).name
        host.close("save")
        card_host.request(SaveCard(name, len(images), lambda choice: _after_prompt(choice, offer, ready),
                                   catalogs=catalogs, file_changed=bpy.data.is_dirty or bool(images)))
        return
    launch(offer, ready)


def _after_prompt(choice, offer, ready):
    global _watch
    if choice == "cancel":
        from . import update_ui
        update_ui.show_notice(offer)   # The update stays ready; its card comes back.
        return
    if choice == "dont_save":
        launch(offer, ready)
        return
    # Images first, as Blender's own quit-after-save does: a generated or packed image is packed into the file,
    # so the file must be written after it (a file that was not modified still is then).
    if _save_images():
        return
    from .restored_document import is_recovery_copy
    if is_recovery_copy(bpy.data.filepath):   # A refused Restore's private copy is never saved over: say why.
        _notice(SAVE_FAILED_TITLE, RECOVERY_COPY_BODY)
        return
    asking = _restored_question()   # Its Save asks first, as File > Save does; a plain save is refused.
    if not bpy.data.filepath or asking:
        _watch = {"offer": offer, "ready": ready, "saved_path": None, "failed": False, "opened": time.monotonic(),
                  "asking": asking, "catalogs": _catalogs_with_file(True)}   # Restored when the watch ends.
        for handler, list_ in ((_on_saved, bpy.app.handlers.save_post), (_on_save_failed, bpy.app.handlers.save_post_fail)):
            if handler not in list_:
                list_.append(handler)
        window = live_windows.first(bpy.context.window_manager)
        with bpy.context.temp_override(window=window):
            (bpy.ops.wm.save_mainfile if asking else bpy.ops.wm.save_as_mainfile)('INVOKE_DEFAULT')
        bpy.app.timers.register(_watch_save, first_interval=0.5)
        return
    if _save_file() and not _catalogs_left():
        launch(offer, ready)


def _save_images():
    """Save (or pack) every image with unsaved pixels; the names still unsaved, after a notice ([] when done)."""
    try:
        if _dirty_images():
            window = live_windows.first(bpy.context.window_manager)
            with bpy.context.temp_override(window=window):
                if bpy.ops.image.save_all_modified.poll():
                    bpy.ops.image.save_all_modified()
    except RuntimeError as error:
        print("Angelo update: saving images before the restart failed:", error)
    unsaved = [image.name for image in _dirty_images()]
    if unsaved:
        _notice(SAVE_FAILED_TITLE, IMAGES_FAILED_BODY)
    return unsaved


def _save_file():
    """Save the open file; False (and a notice) when the save did not succeed.

    The save operator's answer decides: Blender marks the file saved only when it handles the save's notifier,
    after this call, so ``bpy.data.is_dirty`` still reads True here (its quit-after-save dialog trusts the
    write's result the same way).
    """
    previous = _catalogs_with_file(True)   # The save's post handler writes the libraries' catalogs.
    try:
        saved = bpy.ops.wm.save_mainfile() == {'FINISHED'}
    except RuntimeError as error:
        print("Angelo update: saving before the restart failed:", error)
        saved = False
    finally:
        if previous is not None:
            _catalogs_with_file(previous)
    if not saved:
        _notice(SAVE_FAILED_TITLE, SAVE_FAILED_BODY)
    return saved


def _catalogs_left():
    """After the save: True (and a notice) when an asset catalog is still unsaved, so the restart stops."""
    if _catalogs_unsaved():
        _notice(SAVE_FAILED_TITLE, CATALOGS_FAILED_BODY)
        return True
    return False


def _event_path(args):
    """The file a save handler names; a capture of a private snapshot (Angelo's state folder) names none."""
    path = next((value for value in args if isinstance(value, str)), "")
    if not path:
        return ""
    try:
        from . import ui
        state = os.path.realpath(ui.state_directory())
        private = os.path.commonpath([os.path.realpath(path), state]) == state
    except Exception:   # No state folder known (a stand-in host): the path counts as the person's.
        private = False
    return "" if private else path


def _on_saved(*args):
    if _watch is not None and _event_path(args):
        _watch["saved_path"] = _event_path(args)


def _on_save_failed(*args):
    if _watch is not None and _event_path(args):
        _watch["failed"] = True


def _document_saved(watch):
    """Whether the document was saved to its own path, its question closed and nothing left unsaved: read after the
    handler, so a Save Copy elsewhere or a snapshot capture never counts."""
    path, document = watch.get("saved_path"), bpy.data.filepath
    try:
        same = bool(path and document) and os.path.samefile(path, document)
    except OSError:   # Not on disk (yet): the same spelling.
        same = bool(path and document) and os.path.normpath(path) == os.path.normpath(document)
    return same and not _restored_question() and not bpy.data.is_dirty


def _restored_question():
    """Whether the document is a restored one whose first Save over its file still asks (native)."""
    try:
        import _bpy
        return bool(_bpy._angelo_checkpoint.restored_save_pending())
    except (ImportError, AttributeError):
        return False


QUESTION_WAIT = 60.0   # How long a restored document's Save question may stay open before the restart gives up.


def _browser_open():
    return any(area.type == 'FILE_BROWSER' for _window, area in live_windows.window_areas(bpy.context.window_manager))


def _watch_save():
    """The untitled file's Save As: continue once it is saved, stop if it failed or the browser closed without it."""
    global _watch
    watch = _watch
    if watch is None:
        return None
    if _document_saved(watch):
        _watch = None
        _stop_watching(watch)
        if _dirty_images():   # Saved before the File Browser opened; nothing may have changed meanwhile.
            _notice(SAVE_FAILED_TITLE, IMAGES_FAILED_BODY)
        elif not _catalogs_left():
            launch(watch["offer"], watch["ready"])
        return None
    if watch["failed"]:
        _watch = None
        _stop_watching(watch)
        _notice(SAVE_FAILED_TITLE, SAVE_FAILED_BODY)
        return None
    waited = time.monotonic() - watch["opened"]
    if (watch["asking"] and waited > QUESTION_WAIT) or (not watch["asking"] and not _browser_open() and waited > 1.0):
        _watch = None   # Cancelled: nothing happens, the update stays ready.
        _stop_watching(watch)
        return None
    return 0.3


def _stop_watching(watch=None):
    if watch is not None and watch.get("catalogs") is not None:
        _catalogs_with_file(watch["catalogs"])   # The quit dialog's setting, as it was before this save.
    for handler, list_ in ((_on_saved, bpy.app.handlers.save_post), (_on_save_failed, bpy.app.handlers.save_post_fail)):
        if handler in list_:
            list_.remove(handler)


_quit_native = handoff.quit_native


_catalog_generation = handoff.catalog_generation


def _document_stamp():
    return handoff.document_stamp(bpy.data)


def _installer_document_changed(*_args):
    if _installer is not None:
        _installer["changed"] = True


def _change_handlers():
    return tuple(getattr(bpy.app.handlers, name) for name in
                 ("depsgraph_update_post", "load_pre", "save_pre", "undo_pre", "redo_pre"))


def _detach_change_handlers():
    for handlers in _change_handlers():
        if _installer_document_changed in handlers:
            handlers.remove(_installer_document_changed)


_close_original = handoff.close_original


def _stand_down(record):
    """Revoke only this invocation. Unproved cleanup retains its exact owner."""
    return handoff.stand_down(sys.modules[__name__], record)


def _release_installer(record):
    return handoff.release_installer(sys.modules[__name__], record)


def _confirm_installer_quit(invocation):
    """Fixed native handler preflight. No grant is written from Python."""
    return handoff.confirm_installer_quit(sys.modules[__name__], invocation)


def _poll_installer():
    return handoff.poll_installer(sys.modules[__name__])


def launch(offer, ready):
    """Start one revocable helper; only committed native exit grants installation."""
    return handoff.launch(sys.modules[__name__], offer, ready)


def _decide(choice, offer):
    from . import update_ui
    update_ui.decide(choice, offer)


def _requirement(ready):
    """The code requirement the macOS installer checks the new bundle against, right before it swaps."""
    from . import update_stage_mac
    return update_stage_mac.requirement(ready.install_root)


def _executable(ready):
    if ready.family == "macos":
        from . import update_stage_mac
        return str(update_stage_mac.info(ready.install_root).get("CFBundleExecutable", "Angelo"))
    return Path(bpy.app.binary_path).name


def _launcher(ready):
    if ready.family != "windows":
        return ""
    try:
        import angelo_identity
        windows = angelo_identity.data()["windows"]
        name = windows["exe_stem"] + windows["launcher_suffix"] + ".exe"
    except (ImportError, KeyError, TypeError):
        name = Path(bpy.app.binary_path).name
    return str(Path(ready.install_root) / name)


def _notice(title, body, buttons=None, on_action=None, **fields):
    card_host.request(update_card.NoticeCard("alert", iface_(title, **fields), iface_(body),
                                             buttons or [("close", iface_(CLOSE), "primary")], on_action=on_action))


def _not_started(offer):
    def act(action, host):
        if action == "download" and offer.download is not None:
            update_card.open_link(offer.download.url)
            host.close("download")
    _notice(NOT_STARTED_TITLE, NOT_STARTED_BODY, [("close", iface_(CLOSE), "secondary"),
                                                  ("download", iface_(DOWNLOAD), "primary")], act)


def handle_results(preparer, state_directory):
    """At launch: what the previous session's installer did. Successful installs are cleaned up quietly."""
    current = app_version.current()
    keep = set()
    for version, result in preparer.results():
        work = preparer.work(version)
        if result["status"] == "installed" and version == current:
            update_flow.remove_previous(work, preparer.support()[1])
            _record(state_directory, "", 0)
            if update_install.needs_reboot(result):   # Still a success; said once, even if the folder outlives this.
                (work / update_install.RESULT_FILE).unlink(missing_ok=True)
                _notice(REBOOT_TITLE, REBOOT_BODY, version=app_version.display(version))
            continue
        (work / update_install.RESULT_FILE).unlink(missing_ok=True)
        if app_version.parse(version) > app_version.parse(current):
            keep.add(version)
            if result["status"] == "failed":
                state = update_state.load(state_directory)
                count = state.install_failures + 1 if state.install_failed_version == version else 1
                _record(state_directory, version, count)
                print("Angelo update: installing %s failed (%s %s)" % (version, result.get("step", ""),
                                                                        result.get("code", "")))
                _notice(FAILED_TITLE, FAILED_BODY, version=app_version.display(version))
    for name in os.listdir(preparer.root) if preparer.root.is_dir() else ():
        parsed = app_version.parse(name)
        if parsed is not None and parsed > app_version.parse(current):
            keep.add(name)   # A newer update, ready or partly downloaded: kept (and checked when it is offered).
    preparer.clean(keep=keep)


def _record(directory, version, count):
    state = update_state.load(directory)
    state.install_failed_version, state.install_failures = version, count
    update_state.save(directory, state)


class SaveCard(kit.BaseCard):
    """"Save changes before restarting?" over the 3D view, in Angelo's card style."""

    kind, modal, priority, cancel_action = "update_save", True, 90, "cancel"

    def __init__(self, name, image_count, on_choice, catalogs=False, file_changed=True):
        """``catalogs``: asset catalogs are unsaved too; ``file_changed``: the file itself (or an image) is, so the
        file's own line is said (an untitled file's always is: saving it asks for a name)."""
        self.name, self.image_count, self.on_choice = name, image_count, on_choice
        self.catalogs, self.file_changed = catalogs, file_changed

    def layout(self, view, region_w, region_h, scale, ui):
        def build(size):
            width = min(SAVE_WIDTH * size, region_w - 40 * size)
            b = kit.Builder(view, size, width, ui, padding=24)
            icon = 40 * size
            b.icon("alert", b.pad, b.y, 40)
            text_x = b.pad + icon + 14 * size
            text_w = width - text_x - b.pad - 20 * size
            top = b.y
            b.paragraph(iface_(SAVE_TITLE), 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
            if not self.name or self.file_changed:
                body = iface_(SAVE_BODY, name=self.name) if self.name else iface_(SAVE_BODY_UNTITLED)
                b.paragraph(body, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
            if self.image_count:
                images = plural_(self.image_count, "{count} image has unsaved changes too.",
                                 "{count} images have unsaved changes too.")
                b.paragraph(images, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
            if self.catalogs:
                b.paragraph(iface_(CATALOGS_BODY), 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
            b.y = max(b.y, top + icon) + 18 * size
            b.buttons([("dont_save", None, iface_(DONT_SAVE), "secondary", True),
                       ("save", None, iface_(SAVE), "primary", True)],
                      left=[("cancel", None, iface_(CANCEL), "ghost", True)])
            b.close_button("cancel")
            return b.place((region_w - width) / 2, (region_h + b.height()) / 2, True, (region_w, region_h))
        return kit.fit(build, region_w, region_h, scale)

    def act(self, action, value, host):
        if action in {"save", "dont_save", "cancel"}:
            host.close(action)
            self.on_choice(action)
