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
   Cancel). An untitled file opens the File Browser; the restart continues
   only after the file was saved, and stops if it was not. Images with unsaved
   pixels are saved with the file, and the restart stops if one can't be;
4. starts the installer (``update_install``), waits until it reports it is
   running, and quits. The installer replaces the app once Angelo has exited
   and opens it again with the same file.

At the next launch ``handle_results`` reads what the installer did: a
successful install removes the previous app and the downloads; a failed one is
told once and counted, and after ``update_ready.INSTALL_ATTEMPTS`` failures the
version is offered as a download instead.
"""

import os
import time
from pathlib import Path

import bpy

from . import app_version, card_host, card_kit as kit, update_card, update_flow, update_install, update_state, work_guard
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
OTHER_TITLE = n_("Close the other Angelo windows first")
OTHER_BODY = n_("Another Angelo window is open from the same app. Close it, then restart to update.")
DOWNLOAD, CLOSE = n_("Download"), n_("Close", "Angelo")

_watch = None   # A save in progress through the File Browser: {"offer", "ready", "saved", "failed", "opened"}


def _ready_module():
    from . import update_ready
    return update_ready


def _dirty_images():
    return [image for image in bpy.data.images if image.is_dirty]


def _other_instances(ready):
    if ready.family == "macos":
        from . import update_stage_mac
        executable = update_stage_mac.info(ready.install_root).get("CFBundleExecutable", "")
        return update_stage_mac.other_instances(ready.install_root, executable, os.getpid())
    from . import update_stage_win
    return update_stage_win.other_instances(bpy.app.binary_path, os.getpid())


def begin(offer, ready, host):
    """The card's Restart to Update. Every refusal leaves Angelo and the file as they were."""
    live = _ready_module()
    if ready is None:
        return
    from . import update_ui
    if update_ui.policy_blocks() or live.automatic(offer):   # Withdrawn since it was prepared: never installed.
        live.forget(offer.release.version)
        update_ui.show_notice(offer)
        return
    if work_guard.reasons():
        host.redraw()   # The button is disabled already; the reason is on the card.
        return
    if _other_instances(ready):
        _notice(OTHER_TITLE, OTHER_BODY)   # Its files would be replaced under it.
        return
    if not live.preparer().still_valid(ready):
        live.forget(offer.release.version)
        live.consider(offer, manual=True)   # Prepared again, then offered again.
        host.redraw()
        return
    images = _dirty_images()
    if bpy.data.is_dirty or images:
        name = Path(bpy.data.filepath).name
        host.close("save")
        card_host.request(SaveCard(name, len(images), lambda choice: _after_prompt(choice, offer, ready)))
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
    if not bpy.data.filepath:
        _watch = {"offer": offer, "ready": ready, "saved": False, "failed": False, "opened": time.monotonic()}
        for handler, list_ in ((_on_saved, bpy.app.handlers.save_post), (_on_save_failed, bpy.app.handlers.save_post_fail)):
            if handler not in list_:
                list_.append(handler)
        window = next(iter(bpy.context.window_manager.windows), None)
        with bpy.context.temp_override(window=window):
            bpy.ops.wm.save_as_mainfile('INVOKE_DEFAULT')
        bpy.app.timers.register(_watch_save, first_interval=0.5)
        return
    if _save_file():
        launch(offer, ready)


def _save_images():
    """Save (or pack) every image with unsaved pixels; the names still unsaved, after a notice ([] when done)."""
    try:
        if _dirty_images():
            window = next(iter(bpy.context.window_manager.windows), None)
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
    try:
        saved = bpy.ops.wm.save_mainfile() == {'FINISHED'}
    except RuntimeError as error:
        print("Angelo update: saving before the restart failed:", error)
        saved = False
    if not saved:
        _notice(SAVE_FAILED_TITLE, SAVE_FAILED_BODY)
    return saved


def _on_saved(*_args):
    if _watch is not None:
        _watch["saved"] = True


def _on_save_failed(*_args):
    if _watch is not None:
        _watch["failed"] = True


def _browser_open():
    return any(area.type == 'FILE_BROWSER' for window in bpy.context.window_manager.windows
               for area in window.screen.areas)


def _watch_save():
    """The untitled file's Save As: continue once it is saved, stop if it failed or the browser closed without it."""
    global _watch
    watch = _watch
    if watch is None:
        return None
    if watch["saved"] and bpy.data.filepath:
        _watch = None
        _stop_watching()
        if not _dirty_images():   # Saved before the File Browser opened; nothing changed meanwhile.
            launch(watch["offer"], watch["ready"])
        else:
            _notice(SAVE_FAILED_TITLE, IMAGES_FAILED_BODY)
        return None
    if watch["failed"]:
        _watch = None
        _stop_watching()
        _notice(SAVE_FAILED_TITLE, SAVE_FAILED_BODY)
        return None
    if not _browser_open() and time.monotonic() - watch["opened"] > 1.0:
        _watch = None   # Cancelled: nothing happens, the update stays ready.
        _stop_watching()
        return None
    return 0.3


def _stop_watching():
    for handler, list_ in ((_on_saved, bpy.app.handlers.save_post), (_on_save_failed, bpy.app.handlers.save_post_fail)):
        if handler in list_:
            list_.remove(handler)


def launch(offer, ready):
    """Start the installer, then quit once it is running. Nothing is unsaved by now (or the person chose so)."""
    live = _ready_module()
    waiting = work_guard.reasons()
    if waiting:   # Work began during the prompt: the update stays ready and its card says why it waits.
        print("Angelo update: the restart waits for", ", ".join(waiting))
        if not card_host.is_active("update"):
            card_host.request(update_card.UpdateCard(offer, app_version.current(), _decide, live=live))
        return
    install_root, mac = ready.install_root, ready.family == "macos"
    try:
        requirement = _requirement(ready) if mac else ""
        request = update_install.Request(
            family=ready.family, work=Path(ready.work), pid=os.getpid(), version=ready.version,
            executable=_executable(ready), open_file=bpy.data.filepath, app=install_root if mac else "",
            new=ready.payload if mac else "", msi="" if mac else ready.payload, root="" if mac else install_root,
            launcher=_launcher(ready), requirement=requirement, sha256="" if mac else ready.fingerprint,
            gatekeeper=mac and ready.gatekeeper == "1")
        process = update_install.start(request)
    except Exception as error:   # Whatever stops the hand-over leaves Angelo running and says so.
        print("Angelo update: the installer could not start:", error)
        _not_started(offer)
        return
    live.set_restarting(ready.version)
    if not card_host.is_active("update"):
        card_host.request(update_card.UpdateCard(offer, app_version.current(), _decide, live=live))
    started = time.monotonic()

    def wait():
        if update_install.ready(ready.work):
            bpy.ops.wm.quit_blender()
            return None
        if time.monotonic() - started > READY_TIMEOUT:
            try:   # A late installer must never act behind Angelo's back.
                process.kill()
            except (OSError, AttributeError):
                pass
            live.set_restarting(None)
            _not_started(offer)
            return None
        return 0.2

    bpy.app.timers.register(wait, first_interval=0.2)


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

    def __init__(self, name, image_count, on_choice):
        self.name, self.image_count, self.on_choice = name, image_count, on_choice

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
            body = iface_(SAVE_BODY, name=self.name) if self.name else iface_(SAVE_BODY_UNTITLED)
            b.paragraph(body, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
            if self.image_count:
                images = plural_(self.image_count, "{count} image has unsaved changes too.",
                                 "{count} images have unsaved changes too.")
                b.paragraph(images, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
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
