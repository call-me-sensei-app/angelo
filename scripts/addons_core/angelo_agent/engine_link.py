# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine live link: an asset picked in a game engine's editor is edited in Angelo and updates in that editor,
live where the editor can show it. Main thread; every engine through its adapter (``engine_link_assets.adapter``).

* **Off until the person turns it on** (``start``; the chat bar's + menu, File > Godot Live Link). It then listens on
  loopback (``engine_link_server``) and writes the endpoint record every engine's plugin reads
  (``engine_link_protocol``). It stays on across launches until turned off (``engine_link_ui.remember``).
* **A request** (``open`` from an editor's "Edit in Angelo") is routed by the engine the editor named when it
  authenticated: that engine's adapter resolves the asset, or the request is refused (an engine with no adapter:
  code ``engine``). It never opens anything by itself: it is held as ``pending`` and the person answers it in Angelo
  (``engine_link_ui``'s card), because opening replaces Angelo's open file. ``accept`` opens the asset's .blend, or
  imports the asset into its working .blend.
* **While linked**, every change to the followed scene (the person's, the agent's) is exported after the scene has
  been still (``scene_changes``) and sent to the editors of that engine and project that show live scenes
  (capability ``live``), which show it in place of the asset's instances without touching their scene. **Saving**
  in Angelo is the durable step: a .blend is saved, any other kind is written back with the adapter's exporter;
  every editor of the project is told to re-import either way.
* The link ends when another file is opened, the file is saved elsewhere, the editor asks, or the link stops.

doc/angelo/engine_link.rst is the contract; doc/angelo/godot_link.rst is Godot's.
"""

import os
import secrets
import shutil
import sys
import threading
import time

import bpy

from . import (engine_link_assets as assets, engine_link_import, engine_link_protocol as protocol, live_windows,
               private_roots, scene_changes)

POLL_SECONDS = 0.1
QUIET_SECONDS = 0.35       # Stillness before a live export.
MAX_QUIET_SECONDS = 10.0
SHARE = 3.0
REQUEST_SECONDS = 600.0    # A request nobody answered expires.

hooks = {"request": None, "withdraw": None, "changed": None}   # engine_link_ui's card and redraws.
_state = {"server": None, "token": None, "record": None, "pending": None, "session": None, "opening": None,
          "dirty_at": None, "exporting": False, "last_ms": 0.0, "scene": None, "folder": None, "window": None,
          "version": 0, "reports": [], "error": None, "writing": False, "edit_notice": False,
          "writeback": None, "covers": []}   # covers: the live scene version each write-back in flight holds.


class LinkBusy(RuntimeError):
    """Another running Angelo already has the engine link."""


def running():
    return _state["server"] is not None


def session():
    """The linked asset (``engine_link_assets.Asset``) or None."""
    found = _state["session"]
    return found["asset"] if found else None


def pending():
    """The request waiting for the person: {"asset", "client", "id", "project_name", "node", "live"} or None."""
    return _state["pending"]


def clients():
    server = _state["server"]
    return server.clients() if server is not None else {}


def editors(live=False):
    """How many editors of the linked asset's engine and project are connected now (0 when nothing is linked);
    ``live``: only those that show live scenes."""
    found = _state["session"]
    if found is None:
        return 0
    return len([info for info in clients().values() if assets.serves(found["asset"], info, live)])


def _notify(name, *args):
    hook = hooks.get(name)
    if hook is not None:
        try:
            hook(*args)
        except Exception as error:  # A drawing problem never breaks the link.
            print("Angelo: engine link UI:", type(error).__name__, error)


# Turning it on and off -------------------------------------------------------------------------------------------

def start():
    """Listen for the engines' plugins; raises LinkBusy when another Angelo has the link, OSError when it can't."""
    if running():
        return
    from . import app_version, engine_link_server
    token = secrets.token_bytes(32)
    folder = private_roots.mkdtemp("angelo-engine-link-")   # The live exports.
    server = None
    record = protocol.record_path()
    try:   # The record last: a failure before it leaves nothing behind.
        server = engine_link_server.LinkServer(token, {"name": "Angelo",
                                                      "version": app_version.display(app_version.current())})
        app = "" if bpy.app.background else protocol.app_bundle(bpy.app.binary_path)   # None for a background session.
        protocol.write_record(record, {"protocol": protocol.PROTOCOL, "port": server.port, "pid": os.getpid(),
                                       "token": token.hex(), "app": app, "binary": bpy.app.binary_path,
                                       "platform": sys.platform})
    except BaseException as error:
        if server is not None:
            server.close()
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(error, FileExistsError):
            raise LinkBusy(error.args[0] if error.args else 0) from None
        raise
    from .engine_link_writeback import WriteBack
    _state.update(server=server, token=token, record=str(record), error=None, folder=folder,
                  writeback=WriteBack(bpy.app.binary_path))
    for handlers, function in _HANDLERS:
        if function not in handlers:
            handlers.append(function)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)
    _notify("changed")


def stop():
    """Stop listening: tell the editors, drop the session and the request, remove the record. True when it was on."""
    server = _state["server"]
    if server is None:
        return False
    if _state["session"] is not None:
        _end("stopped")
    if _state["pending"] is not None:
        _withdraw()
    _state["server"] = None
    server.close()
    protocol.remove_record(_state["record"])
    for handlers, function in _HANDLERS:
        if function in handlers:
            handlers.remove(function)
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _state["folder"]:
        shutil.rmtree(_state["folder"], ignore_errors=True)
    _state.update(token=None, record=None, folder=None, dirty_at=None, opening=None, window=None)
    _notify("changed")
    return True


# Requests from the editors ---------------------------------------------------------------------------------------------

def _reply(client, header):
    server = _state["server"]
    if server is not None:
        server.send(client, header)


def _handle(client, header):
    kind = header["type"]
    if kind == "connected":   # The indicators say whether an editor is there to see the asset.
        _notify("changed")
        return
    if kind == "disconnected":
        found = _state["pending"]
        if found is not None and found["client"] == client:
            _withdraw()
        _notify("changed")
        return
    if kind == "status":
        _state["reports"] = (_state["reports"] + [dict(header, client=client)])[-20:]
        return
    request_id = str(header.get("id", ""))[:64]
    server = _state["server"]
    info = server.info_of(client) if server is not None else {}   # Also for an editor that has just left.
    engine = info.get("engine", "godot")
    adapter = assets.adapter(engine)
    if adapter is None:   # One listener serves every engine; each has an adapter, or its requests are refused.
        _reply(client, {"type": "refused", "id": request_id, "code": "engine",
                        "message": "This Angelo can't link with %s yet" % engine[:40]})
        return
    linked = _state["session"]
    if kind == "close":   # By the engine's name for the asset: its file needn't resolve now (moved, deleted, or a
        # request that names only the asset).
        ident = str(header.get("asset", ""))[:1024]
        if linked is not None and linked["asset"].ident == ident and assets.serves(linked["asset"], info):
            _end("engine")
        _reply(client, {"type": "closed", "id": request_id, "asset": ident})
        return
    try:
        asset = adapter.resolve(header)
    except assets.AssetError as error:
        _reply(client, {"type": "refused", "id": request_id, "code": error.code, "message": error.english()})
        return
    if linked is not None and linked["asset"] == asset:   # Already open: this editor joins and gets it now.
        _reply(client, {"type": "opened", "id": request_id, "asset": asset.ident})
        _state["dirty_at"] = 0.0
        return
    if _state["pending"] is not None:
        old = _state["pending"]
        _reply(old["client"], {"type": "refused", "id": old["id"], "code": "superseded",
                               "message": "A newer request replaced this one"})
    _state["pending"] = {"asset": asset, "client": client, "id": request_id, "at": time.monotonic(),
                         "project_name": str(header.get("project_name") or os.path.basename(asset.project))[:200],
                         "node": str(header.get("node") or "")[:200], "live": "live" in assets.capabilities(info)}
    _reply(client, {"type": "pending", "id": request_id, "asset": asset.ident})
    _notify("request", _state["pending"])
    _attention()


def _attention():
    """Ask the system to show Angelo: on Windows a flashing taskbar button (the plugin raises Angelo on macOS)."""
    if sys.platform == "win32":
        from . import desktop_os
        desktop_os.flash_taskbar()


def _withdraw():
    _state["pending"] = None
    _notify("withdraw")


def decline(code="declined", message="Not opened in Angelo"):
    """The person said Not now (or the request couldn't be opened): the editor hears so and nothing opens."""
    found = _state["pending"]
    if found is None:
        return
    _reply(found["client"], {"type": "refused", "id": found["id"], "code": code, "message": message})
    _withdraw()


def accept(window, request=None):
    """Open the pending request's asset in ``window`` (the card's Open, after any save the person chose); with
    ``request``, only while that request is still the pending one (a card answers its own request, never a newer).

    A .blend opens as it is; any other kind opens its working .blend, imported from the asset first when there is
    none or the asset changed since Angelo last read or wrote it. Returns the asset; raises OSError or RuntimeError."""
    found = _state["pending"]
    if found is None or request is not None and found is not request:
        return None
    asset = found["asset"]
    if asset.kind != ".blend":
        try:
            assets.ensure_working_folder(asset)   # Refuses a working folder or file that is a link.
        except OSError:
            decline("failed", "Angelo couldn't use its working folder in the project")
            raise
    _withdraw()
    if _state["session"] is not None:
        _end("replaced")
    editable = asset.editable
    _state["opening"] = {"asset": asset, "client": found["client"], "id": found["id"]}
    if asset.kind == ".blend" or assets.working_is_current(asset):
        result = _in(window, lambda: bpy.ops.wm.open_mainfile(filepath=editable))
    else:
        result = _in(window, lambda: engine_link_import.working_file(asset, _writing))
    if "FINISHED" not in result:
        _state["opening"] = None
        _reply(found["client"], {"type": "refused", "id": found["id"], "code": "failed",
                                 "message": "Angelo couldn't open the asset"})
        raise RuntimeError("Angelo couldn't open " + asset.ident)
    if _state["opening"] is not None:   # The load handler didn't run (a background session): link now.
        _begin(live_windows.first(bpy.context.window_manager, temporary=False) or window)
    return asset


def _writing(on):
    """While on, a save is the link's own (the working file's import), not a person's edit to write back."""
    _state["writing"] = on


def _in(window, function):
    """``function()`` in ``window``'s context (a session without windows: the plain context)."""
    if window is None:
        return function()
    with bpy.context.temp_override(window=window):
        return function()


def _begin(window):
    opening, _state["opening"] = _state["opening"], None
    asset = opening["asset"]
    params = assets.adapter(asset.engine).import_params(asset)
    _state.update(session={"asset": asset, "params": params}, dirty_at=0.0,
                  scene=None, window=window.as_pointer() if window else None, last_ms=0.0)
    _reply(opening["client"], {"type": "opened", "id": opening["id"], "asset": asset.ident})
    _notify("changed")


def _end(reason):
    found, _state["session"] = _state["session"], None
    _state["dirty_at"] = None
    if found is not None:
        _broadcast(found["asset"], {"type": "ended", "asset": found["asset"].ident, "reason": reason})
    _notify("changed")


def end():
    """Unlink from the engine (the person's Stop Editing); Angelo keeps the file open."""
    if _state["session"] is not None:
        _end("angelo")


def _broadcast(asset, header, payload=b"", live=False):
    """Send to every editor of the asset's engine and project; ``live``: only those that show live scenes."""
    server = _state["server"]
    if server is None:
        return 0
    return server.broadcast(header, payload, only=lambda info: assets.serves(asset, info, live))


# Following the scene ---------------------------------------------------------------------------------------------

def _window():
    wm = bpy.context.window_manager
    return live_windows.find(wm, _state["window"], live=False) or live_windows.first(wm, temporary=False)


def _followed(window):
    """(scene, view layer) the link exports: the window's, or the context's in a session without windows."""
    if window is not None:
        return window.scene, window.view_layer
    if bpy.app.background:
        return bpy.context.scene, bpy.context.view_layer
    return None, None


@bpy.app.handlers.persistent
def _on_depsgraph(scene, depsgraph):
    if _state["session"] is None or _state["exporting"] or threading.current_thread() is not threading.main_thread():
        return
    window = _window()
    followed, view_layer = _followed(window)
    if followed is None or scene.session_uid != followed.session_uid:
        return
    changed, _state["scene"] = scene_changes.matters(depsgraph, scene, view_layer, _state["scene"])
    if changed:
        _state["dirty_at"] = time.monotonic()


def _tick():
    server = _state["server"]
    if server is None:
        return None
    for client, header in server.drain():
        try:
            _handle(client, header)
        except Exception as error:  # One bad request never stops the link.
            _state["error"] = "%s: %s" % (type(error).__name__, str(error)[:200])
    _written()
    found = _state["pending"]
    if found is not None and time.monotonic() - found["at"] > REQUEST_SECONDS:
        decline()
    dirty_at = _state["dirty_at"]
    if _state["session"] is not None and dirty_at is not None:
        quiet = scene_changes.quiet_seconds(_state["last_ms"], QUIET_SECONDS, SHARE, MAX_QUIET_SECONDS)
        if time.monotonic() - dirty_at >= quiet:
            window = _window()
            if window is not None and window.screen is None:
                return live_windows.RETRY_S   # Between screens: the export waits for it (the scene stays dirty).
            if window is not None and not scene_changes.busy(window) or window is None and bpy.app.background:
                _state["edit_notice"] = False
                publish(window)
            elif window is not None and scene_changes.editing(window) and not _state.get("edit_notice"):
                _state["edit_notice"] = True   # Once per stay in Edit Mode: the editor says why nothing arrives yet.
                asset = _state["session"]["asset"]
                _broadcast(asset, {"type": "status", "asset": asset.ident, "code": "edit_mode",
                                   "message": "Angelo is in Edit Mode; %s updates when you leave it"
                                   % assets.display_name(asset.engine)}, live=True)
    return POLL_SECONDS


def publish(window):
    """Export the linked scene now and send it to the project's editors that show live scenes; returns the bytes
    sent (0: none)."""
    found = _state["session"]
    _state["dirty_at"] = None
    if found is None:
        return 0
    asset = found["asset"]
    if not editors(live=True):
        return 0   # No editor of that project shows live scenes: nothing to show it to (it gets one when it asks).
    path = os.path.join(_state["folder"], "live.glb")
    _state["exporting"] = True
    started = time.perf_counter()
    try:
        assets.export(window, path, assets.adapter(asset.engine).live_options(found["params"]))
        with open(path, "rb") as handle:
            body = handle.read()
    except Exception as error:
        _state["error"] = "%s: %s" % (type(error).__name__, str(error)[:200])
        _broadcast(asset, {"type": "status", "asset": asset.ident, "code": "export_failed",
                           "message": "Angelo couldn't export the change; %s shows the last one"
                           % assets.display_name(asset.engine)}, live=True)
        return 0
    finally:
        _state["exporting"] = False
        _state["last_ms"] = (time.perf_counter() - started) * 1000.0
        _state["scene"] = scene_changes.scene_key(*_followed(window))
    _state["version"] += 1
    sent = _broadcast(asset, {"type": "scene", "asset": asset.ident, "version": _state["version"],
                              "export_ms": round(_state["last_ms"], 1)}, body, live=True)
    return len(body) if sent else 0


# Files -----------------------------------------------------------------------------------------------------------

@bpy.app.handlers.persistent
def _load_post(*_args):
    opening = _state["opening"]
    path = bpy.data.filepath
    if opening is not None and path and assets.same_file(path, opening["asset"].editable):
        _begin(live_windows.first(bpy.context.window_manager, temporary=False))
        return
    found = _state["session"]
    if found is not None and not (path and assets.same_file(path, found["asset"].editable)):
        _end("other_file")
    if _state["pending"] is not None:   # A load drops cards: the person still has the request to answer.
        _notify("request", _state["pending"])


@bpy.app.handlers.persistent
def _save_post(filepath, *_args):
    found = _state["session"]
    if found is None or _state.get("writing") or not filepath:
        return
    asset = found["asset"]
    if not assets.same_file(filepath, asset.editable):
        if bpy.data.filepath and not assets.same_file(bpy.data.filepath, asset.editable):
            _end("saved_elsewhere")   # Save As to another file: that file isn't the asset.
        return   # A copy (a checkpoint, Save Copy) is not the asset's save.
    durable = assets.adapter(asset.engine).durable(asset, found["params"])
    if durable is not None:   # In a background Angelo from the saved file; the editor hears when it's written.
        covers = _state["covers"]   # [running, queued]: a newer save replaces the queued one (WriteBack.request).
        del covers[1 if _state["writeback"].busy() else 0:]
        covers.append(_state["version"])
        _state["writeback"].request(asset.path, filepath, asset.path, *durable)
        return
    _broadcast(asset, {"type": "saved", "asset": asset.ident, "files": [asset.ident],
                       "scene_version": _state["version"]})


def _written():
    """Finish the write-backs that ended: the stamp and the editors' re-import, or why the asset wasn't written."""
    for target, error in _state["writeback"].finished():
        covers = _state["covers"].pop(0) if _state["covers"] else _state["version"]
        found = _state["session"]
        if found is None or not assets.same_file(found["asset"].path, target):
            continue
        asset = found["asset"]
        if error is not None:
            _state["error"] = "write-back: " + error
            _broadcast(asset, {"type": "status", "asset": asset.ident, "code": "write_failed",
                               "message": "Angelo saved its file but couldn't write the %s" % asset.kind})
            continue
        try:
            assets.write_stamp(asset)
        except OSError as failure:
            _state["error"] = "stamp: %s" % failure
        _broadcast(asset, {"type": "saved", "asset": asset.ident, "files": [asset.ident], "scene_version": covers})


@bpy.app.handlers.persistent
def _exit_pre(*_args):
    stop()


def _atexit():   # Sockets and files only: Blender's data is already freed at atexit.
    server = _state["server"]
    if server is not None:
        server.close()
        protocol.remove_record(_state["record"])


_HANDLERS = ((bpy.app.handlers.depsgraph_update_post, _on_depsgraph), (bpy.app.handlers.load_post, _load_post),
             (bpy.app.handlers.save_post, _save_post))


def register():
    import atexit
    if _exit_pre not in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.append(_exit_pre)
    atexit.register(_atexit)


def unregister():
    import atexit
    stop()
    if _exit_pre in bpy.app.handlers.exit_pre:
        bpy.app.handlers.exit_pre.remove(_exit_pre)
    atexit.unregister(_atexit)
