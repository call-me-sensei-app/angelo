# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Committed native updater handoff, shared by macOS and Windows.

The restart module retains the sole installer record and the save/UI callbacks.
Functions accepting ``restart`` borrow that exact module, never copy its record,
create another owner, or authorize install from Python. Its stable delegates keep
native confirmation and timer/handler identities unchanged. Installer argv,
payload validation and platform scripts remain in update_install and its helpers.
"""

import os
import secrets
import time
from pathlib import Path


def quit_native():
    import _bpy
    native = _bpy._angelo_jobs
    for name in ("_update_quit_protocol", "_update_quit_request", "_update_quit_revoke", "_update_quit_status",
                 "_update_quit_catalog_generation"):
        if not callable(getattr(native, name, None)):
            raise RuntimeError("Committed updater quit is unavailable")
    protocol = native._update_quit_protocol()
    if type(protocol) is not int or protocol != 2:
        raise RuntimeError("Committed updater quit is unavailable")
    return native


def catalog_generation(native):
    value = native._update_quit_catalog_generation()
    if type(value) is not int or not 0 < value <= (1 << 64) - 1:
        raise RuntimeError("Catalog edit ownership is unavailable")
    return value


def document_stamp(data):
    # Dirty work explicitly discarded by the person may remain dirty. Any later
    # depsgraph/save/load/Undo change invalidates this captured decision as well.
    return (data.filepath, bool(data.is_dirty),
            tuple((image.as_pointer(), bool(image.is_dirty)) for image in data.images))


def close_original(record):
    if record["original_closed"]:
        return True
    stream = record["process"].stdin
    if stream is None:
        return False
    try:
        stream.close()
        record["original_closed"] = True
        return True
    except Exception:
        return False


def stand_down(restart, record):
    """Revoke only this invocation. Unproved cleanup retains its exact owner."""
    if restart._installer is not record:
        return False
    record["stopping"] = True
    if record["drain_started"] is None:
        record["drain_started"] = time.monotonic()
    owner = record["owner"]
    native_closed = owner is None
    if owner is not None:
        try:
            native_closed = record["native"]._update_quit_revoke(owner) is True
        except Exception:
            native_closed = False
    channel_closed = restart._close_original(record) and native_closed
    process = record["process"]
    try:
        exited = process.poll() is not None
    except Exception:
        exited = False
    if not exited and not record["kill_attempted"]:
        record["kill_attempted"] = True
        try:
            process.kill()
        except Exception:
            pass  # The original process stays retained; this never certifies exit.
    if channel_closed and exited:
        return restart._release_installer(record)
    # We certify only this Popen leader, never an entire helper process tree.
    # Even an unproved surviving helper cannot install without the native frame.
    return False


def release_installer(restart, record):
    if restart._installer is not record:
        return False
    restart._detach_change_handlers()
    record["live"].set_restarting(None)
    restart._installer = None
    return True


def confirm_installer_quit(restart, invocation):
    """Fixed native handler preflight. No grant is written from Python."""
    record = restart._installer
    if (record is None or record["invocation"] != invocation or record["stopping"]
            or record["owner"] is None or not record["original_closed"] or record["changed"]):
        return False
    try:
        status = record["native"]._update_quit_status(record["owner"])
        return (type(status) is dict and type(status.get("schema")) is int and status["schema"] == 1
                and status.get("phase") == "PENDING" and status.get("channel_closed") is False
                and record["process"].poll() is None
                and restart.update_install.ready(record["ready"].work, invocation)
                and not restart.work_guard.reasons() and not restart._other_instances(record["ready"])
                and restart._document_stamp() == record["stamp"]
                and restart._catalog_generation(record["native"]) == record["catalog_generation"]
                and record["live"].preparer().still_valid(record["ready"]))
    except Exception:
        return False


def poll_installer(restart):
    record = restart._installer
    if record is None:
        return None
    try:
        if record["stopping"]:
            if restart._stand_down(record):
                return None
            # Bound automatic cleanup work; retain owner after expiry. A later
            # explicit launch may check again but cannot start a second helper.
            return 0.1 if time.monotonic() - record["drain_started"] < restart.DRAIN_TIMEOUT else None
        if record["process"].poll() is not None:
            raise RuntimeError("Installer exited before committed quit")
        if record["owner"] is not None:
            status = record["native"]._update_quit_status(record["owner"])
            if type(status) is not dict or status.get("phase") != "PENDING":
                raise RuntimeError("Updater quit was cancelled")
        elif restart.update_install.ready(record["ready"].work, record["invocation"]):
            if (record["changed"] or restart.work_guard.reasons() or restart._document_stamp() != record["stamp"]
                    or restart._catalog_generation(record["native"]) != record["catalog_generation"]):
                raise RuntimeError("Work changed while preparing restart")
            stream = record["process"].stdin
            os.set_blocking(stream.fileno(), False)
            # The native request allocates its return capsule before posting the
            # delayed handler. No ordinary quit operator return authorizes install.
            record["owner"] = record["native"]._update_quit_request(stream.fileno(), record["invocation"],
                                                                      record["catalog_generation"])
            if record["owner"] is None or not restart._close_original(record):
                raise RuntimeError("Updater channel ownership failed")
        if time.monotonic() - record["started"] > restart.READY_TIMEOUT:
            raise RuntimeError("Updater quit timed out")
        return 0.1
    except Exception:
        restart._stand_down(record)
        try:
            restart._not_started(record["live"].target(record["offer"]))
        except Exception:
            pass  # A UI failure cannot release or authorize the retained installer.
        if restart._installer is record and time.monotonic() - record["drain_started"] < restart.DRAIN_TIMEOUT:
            return 0.1
        return None


def launch(restart, offer, ready):
    """Start one revocable helper; only committed native exit grants installation."""
    if restart._installer is not None:
        restart._stand_down(restart._installer)
        return
    live = restart._ready_module()
    if restart.work_guard.reasons():
        return
    record = None
    try:
        native = restart._quit_native()  # Older native builds cannot use an unsafe fallback.
        invocation = secrets.token_hex(16)
        install_root, mac = ready.install_root, ready.family == "macos"
        request = restart.update_install.Request(
            family=ready.family, work=Path(ready.work), pid=os.getpid(), version=ready.version,
            executable=restart._executable(ready), open_file=restart.bpy.data.filepath, app=install_root if mac else "",
            new=ready.payload if mac else "", msi="" if mac else ready.payload, root="" if mac else install_root,
            launcher=restart._launcher(ready), requirement=restart._requirement(ready) if mac else "",
            sha256="" if mac else ready.fingerprint, gatekeeper=mac and ready.gatekeeper == "1",
            invocation=invocation)
        record = dict(process=None, native=native, owner=None, invocation=invocation, live=live, ready=ready,
                      offer=offer, stamp=restart._document_stamp(), catalog_generation=restart._catalog_generation(native),
                      changed=False, original_closed=False,
                      stopping=False, drain_started=None, kill_attempted=False, started=time.monotonic())
        for handlers in restart._change_handlers():
            if restart._installer_document_changed not in handlers:
                handlers.append(restart._installer_document_changed)
        process = restart.update_install.start(request)
        record["process"] = process
        restart._installer = record  # Retain exact Popen before any timer/card callback can fail.
        live.set_restarting(ready.version)
        restart.bpy.app.timers.register(restart._poll_installer, first_interval=0.1)
        if not restart.card_host.is_active("update"):
            restart.card_host.request(restart.update_card.UpdateCard(offer, restart.app_version.current(), restart._decide, live=live))
    except Exception:
        if restart._installer is record and record is not None:
            restart._stand_down(record)
        elif restart._installer is None:
            restart._detach_change_handlers()
        restart._not_started(live.target(offer))
