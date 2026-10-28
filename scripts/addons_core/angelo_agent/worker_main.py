# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Start-up of a worker Blender inside ``Angelo --background`` (``worker_process`` starts it).

``angelo_headless.py -- --worker`` comes here. The start-up is the headless MCP child's (``headless_main``): the
same read-only preferences, the same few registrations (the approval and commit operators, the program handlers,
document and scene tracking; no chat, sign-in, provider, catalog or timer) and the same window-free execution: the
document is loaded into the background window record with ``load_ui=False, use_scripts=False`` and every tool runs
against that window's scene and view layer. Then it connects (``worker_channel``), says ``ready`` and serves
(``worker_host``) until Angelo closes the connection, and removes its private folder. With
``ANGELO_WORKER_CHAT_HOST`` on (the default) it also hosts its chat (``worker_chat_host``); ``0`` keeps the shadow.
"""

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

from . import headless_preferences
from .worker_process import PRIVATE_PREFIX, STATE


def parse(argv):
    parser = argparse.ArgumentParser(prog="angelo-worker")
    parser.add_argument("--channel", required=True)
    parser.add_argument("--file", required=True, help="the part file this worker owns (made empty if missing)")
    parser.add_argument("--private", required=True, help="the private folder Angelo made for this worker")
    return parser.parse_args(argv)


def isolated(environ, private):
    """The private folder Angelo made for this worker, holding ``ANGELO_STATE_DIR``; refuse to run elsewhere."""
    state = environ.get("ANGELO_STATE_DIR")
    if not state or environ.get("ANGELO_HEADLESS") != "1":
        raise RuntimeError("A worker runs only as Angelo starts it")
    private = Path(private)
    info = os.lstat(private)
    owner_only = sys.platform == "win32" or (info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700)
    if (not stat.S_ISDIR(info.st_mode) or not owner_only or not private.name.startswith(PRIVATE_PREFIX)
            or Path(state) != private / STATE):
        raise RuntimeError("The worker's private folder is not one Angelo made")
    return private


def open_part(bpy, part):
    """Load the part file's data into the background window; a missing one starts empty, saved there and then
    opened like an existing one, so its load starts the part's own Undo history (``_prepare_preferences`` asked the
    binary to keep one for every later load: ``program_undo.keep_background_document``). Empty includes the start-up
    file's collection: a part's collections are the ones its worker made, and the merge keeps exactly those
    (``worker_merge``), never a "Collection.006" around every part."""
    part = Path(part)
    if part.exists():
        result = bpy.ops.wm.open_mainfile(filepath=str(part), load_ui=False, use_scripts=False)
        if "FINISHED" not in result:
            raise RuntimeError("Blender could not open the part file")
        return
    for collection in (bpy.data.objects, bpy.data.meshes, bpy.data.cameras, bpy.data.lights, bpy.data.materials,
                       bpy.data.collections):
        for block in list(collection):
            collection.remove(block)
    result = bpy.ops.wm.save_as_mainfile(filepath=str(part), check_existing=False)
    if "FINISHED" not in result:
        raise RuntimeError("Blender could not create the part file")
    open_part(bpy, part)


def main(argv, environ=None):
    environ = os.environ if environ is None else environ
    options = parse(argv)
    private = isolated(environ, options.private)
    try:
        _serve(options)
    finally:
        shutil.rmtree(private, ignore_errors=True)


def _serve(options):
    import bpy
    from . import live_windows, ui, worker_channel
    from .headless_main import _prepare_preferences, _register
    from .worker_host import WorkerHost
    endpoint = worker_channel.connect(options.channel)
    try:
        headless_preferences.protect(bpy)   # First: nothing below can save preferences.
        _prepare_preferences(bpy)   # Before any load: Undo history (headless_main.UNDO_STEPS steps), no thumbnails.
        bpy.context.preferences.filepaths.save_version = 0
        _register(bpy, ui)
        open_part(bpy, options.file)
        if live_windows.first(bpy.context.window_manager) is None:
            raise RuntimeError("No background window to work in")
        from .program_undo import keep_background_document
        if not keep_background_document():   # An older binary: every edit would refuse (break-it L4-3), so no start.
            raise RuntimeError("This Angelo build keeps no Undo history in background, so a worker could apply no "
                               "edits; update Angelo")
        host = WorkerHost(ui, bpy, endpoint, options.file)
        from . import worker_chat_seams
        if worker_chat_seams.enabled():   # Design A′: this worker hosts its chat (worker_chat_host).
            host.chats = _host_chats(host, endpoint)
    except Exception as error:  # noqa: BLE001 - Angelo reads why, then the worker exits.
        endpoint.send({"seq": 0, "ready": {"ok": False, "error": "%s: %s" % (type(error).__name__, error)}})
        endpoint.close()
        return
    host.start_reader()
    endpoint.send({"seq": 0, "ready": {"ok": True, "pid": os.getpid(), "blender": bpy.app.version_string,
                                       "document": list(ui.document_key()), "layout": host.layout()}})
    try:
        host.run()
    finally:
        endpoint.close()


def _host_chats(host, endpoint):
    """This worker's own chats (design A′) and what they need of the lead: model runtimes over the relay
    (``runtime_relay``), usage and telemetry forwarded (``worker_usage``), progress reports to the lead's tiles."""
    import time
    from . import parallel_sessions, runtime_relay, worker_usage
    from .worker_chat_host import WorkerChatHost
    chats = WorkerChatHost(host)
    runtime_relay.install(endpoint, sessions=lambda: list(chats.chats))
    worker_usage.install(lambda event: chats.outbox.put(dict(event, t=time.time())))
    chats.usage_required = True   # Every chat it opens forwards its usage, or it is not hosted.
    parallel_sessions.note_progress = chats.news   # This process has no tiles: a report is the lead's (``news``).
    return chats
