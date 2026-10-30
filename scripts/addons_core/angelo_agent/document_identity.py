# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The open document's identity across save, quit and reopen, so a chat keeps working on its own file.

A chat is bound to ``ui.document_key()``: (the load serial, its scene's session uid). Both are renewed by every load,
so every handle, preview, stage and call made before a load stays stale after it (``document_scene``). This module
says when a load brought back the *same document*, so a chat that holds work about it is rebound to the new key
instead of being invalidated (the 1.0.6 GUI test: Save As stool.blend, quit, Open Recent stool.blend, and the chat
said "Document changed"; the maintainer, 2026-10-29: "a document identity that survives save, quit and reopen").

* **The identity** is a random id saved as the ``_angelo_document_id`` custom property of every local scene (the
  underscore keeps it out of the Custom Properties panel). Opening a file reads it (the value all local scenes that
  carry one agree on); a file without one, and every home file, gets a new id in memory. Opening never writes it:
  each Save does (``save_pre``), never a home-file save nor a request snapshot (a checkpoint never changes the file).
* **The document's path** is where it was read from or last saved to (a Save As moves it): ``bpy.data.filepath``,
  after a Restore too (the native step gives the restored document the path open at the Restore, never the path a
  checkpoint recorded: ``restored_document``). A Save Copy, a home-file save (Save Startup File: ``save_pre("")``) and
  a save into Angelo's own state folder never move it nor touch a chat's record; Auto Save and quit.blend run no save
  handler. It restores only this document's own history (a chat's own checkpoints), so the restored
  state keeps the document's id when its snapshot carries none (a snapshot taken before the first Save), and takes
  the snapshot's id when it carries another.
* **The same document** is the same id, at the same path as written, in the same file (``file_identity``,
  ``same_file`` and its block-clone limit; read only from the open document's file at its load and save). The same id
  at another path (a copy, the file a Save As left, the original moved) is never taken on its own, nor the same path
  in a file that cannot confirm it (unknown, or changed: a remount, another tool's rewrite): a waiting chat, and a
  bound chat whose own path came back unconfirmed (``_await``), is offered the open file (``document_offer``) and only
  the person's Continue moves it. Anything else (another id or none, another path, an unsaved file) invalidates a
  bound chat as before (``document_work.document_changed``). Recover Last Session and Recover Auto Save read the
  original path back (``readfile.cc``, ``G_FILE_RECOVER_READ``). A successful load ends a lead's workers once, first
  (``worker_ending.end_after_change``, as Restore does); the same document (Revert, Open Recent) then rebinds the
  chat by scene name, memory stale first (``restart``). A failed load changes nothing, workers and batch included.
* **A relaunch.** Each chat saves the file it works on (``document_file``: the id, the path and its scene's name as
  the file holds it; host-only, never sent to a model). A restored chat that holds work and whose file is not the open
  one waits for it (``awaited_file``): nothing binds it to the open document, its input is refused with the file to
  open (``awaiting``, shown by the composer), and opening that file binds it. A chat whose file was never saved (or
  whose path this build cannot store) can never continue, and says so. A chat saved before this build has no record
  and binds as before. An app's MCP conversation follows these rules too (``external_host``).

Binding never fails for one chat's persistence failure (its memory changes in RAM and the chat says it could not be
saved: ``document_work.stale_memory``), and one chat's failure never stops another's nor the rest of a load or save
handler (``_each``). A background Angelo (a worker, a headless host) has no person to reopen a file: there every
load is another document, as in 1.0.6 (``document_scene.unattended``). Main thread. No bpy at import.
"""

import os
import re
import sys
import unicodedata
import uuid

from .ui_text import n_

PROPERTY = "_angelo_document_id"
AWAITING = n_("This chat works on {file}. Open that file to continue, or start a new chat.")
UNSAVED = n_("This chat's file was never saved, so it can't be opened again. Start a new chat.")
SAVE_FAILED = n_("Angelo couldn't save this chat. It keeps working, but what happens in it now may be lost if Angelo "
                 "quits.")   # The chat's own row when a bind's save failed (``document_work.save_failed``).
# Model-facing tool results (English): the file was loaded again, so nothing of the earlier load runs.
LEFT_STAGES = "The Blender file was opened again; previews and staged jobs of the earlier load were discarded"  # i18n: tool-result error returned to the model
LEFT_CALLS = "The Blender file was opened again; observe the scene again before editing"  # i18n: tool-result error returned to the model
_ID = re.compile(r"[0-9a-f]{32}")
MAX_PATH = 32767   # Windows' long-path limit; a scene name is far shorter.

_open = (None, None, "", None)   # (the load serial, the open document's id, its path, its file): every load renews it.
_previous = None   # At load_pre: (id, path, file) of the document being left.
_leaving = None    # Between load_pre and load_post: ((outgoing id, path, file), [(chat, its key, scene names)]).
_saving = None             # save_pre's target ("" for the home file): only a save of the document itself moves it.


def _local(scene):
    return getattr(scene, "library", None) is None and getattr(scene, "override_library", None) is None


def stored(scenes):
    """The id ``scenes`` carry: the one value every local scene that has one agrees on, else None."""
    values = {scene.get(PROPERTY) for scene in scenes if _local(scene)} - {None}
    value = values.pop() if len(values) == 1 else None
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def file_identity(path):
    """The file at ``path``: [device, file number, creation stamp], or None (no file, or no file number). Only ever
    the open document's own file, just read or written, so a hung share stalls nothing; never a recorded path.

    On macOS ``st_dev`` is given at mount time by mount order (an external drive or disk image remounted gets
    another), so there the file is its number and its creation time (``st_birthtime``, which survives a remount; the
    stamp is 0 elsewhere). On Windows ``st_dev`` is the volume serial and ``st_ino`` the file index; on Linux the
    device and inode. Compare two with ``same_file``."""
    try:
        info = os.stat(path)
    except (OSError, ValueError):
        return None
    if not info.st_ino:
        return None
    stamp = 0
    if sys.platform == "darwin":
        stamp = getattr(info, "st_birthtime_ns", None)
        if stamp is None:
            stamp = int(getattr(info, "st_birthtime", 0) * 1_000_000_000)
    return [int(info.st_dev), int(info.st_ino), int(stamp)]


def same_file(recorded, file):
    """Whether two ``file_identity`` values are one file: the same number and creation stamp where a stamp was read
    (macOS: the device number is ignored, it changes with mount order), else the same device and number. Unknown:
    False. A known limit, by construction: a byte-identical block-level clone of the volume (dd, asr, a duplicated
    disk image, a cloned SD card) mounted at the same path holds the same number and stamp, as does a FAT copy whose
    creation second was set to the original's on purpose; no metadata tells them apart, so they read as the file."""
    if recorded is None or file is None or len(recorded) != 3 or len(file) != 3:
        return False
    if recorded[2] or file[2]:
        return list(recorded[1:]) == list(file[1:])
    return list(recorded[:2]) == list(file[:2])


def current(ui):
    """The open document's id; derived on first use after a load (``after_load`` sets it at every load)."""
    global _open
    if _open[0] != ui._document_serial or _open[1] is None:
        path = ui.bpy.data.filepath or ""
        _open = (ui._document_serial, (stored(ui.bpy.data.scenes) if path else None) or uuid.uuid4().hex, path,
                 file_identity(path) if path else None)
    return _open[1]


def document_path(ui):
    current(ui)
    return _open[2]


def open_document(ui):
    """(id, path, file) of the open document."""
    current(ui)
    return _open[1:]


def _canonical(path):
    """A path as written, normalized without touching the file system (load handlers never stat a share)."""
    return os.path.normcase(os.path.abspath(path))


def same_document(recorded_id, recorded_path, identity, path, recorded_file=None, file=None):
    """Whether (``identity``, ``path``, ``file``) is the document recorded as (``recorded_id``, ``recorded_path``,
    ``recorded_file``): the same id, at the same path as written, in the same file. A path names whatever file is
    there now (a retargeted symlink, a same-named volume, a reused drive letter, a file replaced by a copy), so the
    file must match too; an unknown file never matches (fails closed). The same id otherwise is only ever offered to
    the person (``document_offer``)."""
    if not (recorded_id and identity and recorded_path and path) or recorded_id != identity:
        return False
    return _canonical(recorded_path) == _canonical(path) and same_file(recorded_file, file)


def same_place(recorded_id, recorded_path, identity, path):
    """The same id at the same path as written, whatever file is there (unknown or changed: offered, never changed)."""
    return bool(recorded_id and recorded_id == identity and recorded_path and path
                and _canonical(recorded_path) == _canonical(path))


def elsewhere(record, identity, path, file):
    """The open document carries ``record``'s id but is not its document: another path, or file (``document_offer``)."""
    return bool(record["id"] and record["id"] == identity and record["path"] and path
                and not same_document(record["id"], record["path"], identity, path, record["file"], file))


def _private(ui, path):
    """A path inside Angelo's own state folder (its checkpoints and Restore copies), never the person's document."""
    try:
        root = _canonical(ui.state_directory())
    except Exception:   # No state folder known (a host without one): nothing is private by this rule.
        return False
    return _canonical(path) == root or _canonical(path).startswith(root.rstrip(os.sep) + os.sep)


def _text(value, limit):
    """A string a session can store: within ``limit``, no NUL, and valid UTF-8 (any printable or not)."""
    try:
        return isinstance(value, str) and len(value) <= limit and "\0" not in value and value.encode("utf-8") is not None
    except UnicodeError:
        return False


def clean_record(value):
    """A saved ``document_file`` as this build reads it, else None (``SessionStore``: never trusted beyond this)."""
    if not isinstance(value, dict) or set(value) != {"id", "path", "scene", "file"}:
        return None
    identity, path, scene, file = value["id"], value["path"], value["scene"], value["file"]
    if identity is not None and not (isinstance(identity, str) and _ID.fullmatch(identity)):
        return None
    if not (_text(path, MAX_PATH) and _text(scene, MAX_PATH)):
        return None
    if file is not None and not (isinstance(file, list) and len(file) == 3
                                 and all(type(item) is int and -2 ** 63 <= item < 2 ** 64 for item in file)):
        return None
    return {"id": identity, "path": path, "scene": scene, "file": list(file) if file is not None else None}


def _display(path):
    """The file's name as the composer shows it: control characters (never a name's own text) as U+FFFD."""
    name = os.path.basename(path) or "?"
    return "".join("�" if unicodedata.category(char) == "Cc" else char for char in name)


def unattended():
    from .document_scene import unattended as background
    return background()


def _chat_of_window(chat):
    """A chat of Angelo's own window: not a worker's chat (bound to one, or hosted in it)."""
    return (getattr(chat, "remote_executor", None) is None and not getattr(chat, "hosted_in_worker", False)
            and getattr(chat, "chat_host", None) is None)


def _bound_here(chat, ui):
    document = getattr(chat, "document", None)
    return (_chat_of_window(chat) and document is not None and document[0] == ui._document_serial
            and not chat.document_invalid and getattr(chat, "awaited_file", None) is None)


def _scene_name(ui, uid):
    return next((scene.name_full for scene in ui.bpy.data.scenes if scene.session_uid == uid), None)


def _scene_uid(ui, names):
    """The scene a rebound chat works on: the first of ``names`` the file has, else the file's only scene, else 0
    (a uid no scene has: the chat then says its scene is gone, ``document_scene.GONE``, and never takes another)."""
    scenes = list(ui.bpy.data.scenes)
    for name in names:
        found = next((scene.session_uid for scene in scenes if name and scene.name_full == name), None)
        if found is not None:
            return found
    return scenes[0].session_uid if len(scenes) == 1 else 0


def _each(chats, action):
    """``action`` for every chat; one chat's failure never stops another's nor the handler that called this."""
    for chat in list(chats):
        try:
            action(chat)
        except Exception as error:   # Logged by kind only: a message can carry a path.
            print("Angelo: chat %s's document binding failed (%s)" % (
                getattr(chat, "state", {}).get("id", "?"), type(error).__name__), flush=True)


def remember(chat, ui, *, saved=False, persist=False):
    """Record the file ``chat`` works on, for a relaunch: at each request (``ui.prepare_controller``), at each Save
    (``saved``: the file now holds its scene's live name) and when a load binds it. The scene's name is kept as the
    file holds it: a rename since the last save is not in the file. True when the record changed."""
    if unattended() or not _bound_here(chat, ui):
        return False
    identity, path, file = open_document(ui)
    if not _text(path, MAX_PATH):
        path = ""   # A path no session can store reads as never saved: the chat waits, never binds elsewhere.
    old = clean_record(chat.state.get("document_file"))
    live = _scene_name(ui, chat.document[1])
    same = old is not None and old["id"] == identity and old["path"] == path
    kept = old["scene"] if same and old["scene"] else None
    scene = (kept if kept and (not saved or live is None) else live) or kept or ""
    record = {"id": identity, "path": path, "scene": scene, "file": file}
    record = clean_record(record) or dict(record, scene="")
    if record == old:
        return False
    with chat.lock:
        chat.state["document_file"] = record
    if persist:
        from .document_work import save_quietly
        save_quietly(chat)
    return True


def restore(chat, ui):
    """``ui.get_controller`` and an app's MCP conversation (``external_host``): a chat just read from its saved state
    binds to the open document as before, unless it holds work about a file that is not the open one: then it waits
    for that file (module docstring). A recorded chat never takes the open document as a legacy one does."""
    from .document_work import holds_document_work
    record = clean_record(chat.state.get("document_file"))
    if record is None or unattended() or not _chat_of_window(chat) or not holds_document_work(chat):
        chat.set_document(ui.document_key())
    elif same_document(record["id"], record["path"], *open_document(ui)[:2], record["file"], open_document(ui)[2]):
        _bind(chat, ui, [record["scene"]])
    else:
        chat.awaited_file = record
        chat.version += 1
        from .document_offer import post
        post(chat, ui)


def _leave(chat):
    """What ``document_work.document_changed`` revokes at a load, before the old document is freed: the chat's own
    previews and stages, its queued calls, a running turn or voice, and its script consent. It stays bound; while it
    waits for the load, a wait cancelled here says its document changed (``view_wait``'s ``document_leaving``)."""
    chat._script_consent = None
    chat.invalidate_native_stages(LEFT_STAGES)
    chat.cancel_tools(LEFT_CALLS)
    if chat.busy or getattr(chat, "voice", None) is not None:
        chat.stop(LEFT_CALLS)


def _bind(chat, ui, names, before=None, *, replaced=True):
    """``chat`` works on the open document again: its own scene by ``names``. Its memory is stale before anything
    else can fail (it is rebound already: its earlier receipts must never read as current). ``before``: its key in the
    earlier load (``replaced`` False: that load read nothing, so nothing else changes). Its workers were ended by the
    load already (``after_load``), never here."""
    from .document_work import stale_memory
    key = (ui._document_serial, _scene_uid(ui, names))
    chat.awaited_file, chat.document_leaving = None, False
    chat.document = key
    if replaced:
        stale_memory(chat, "restart", rescope=False)   # Never raises; its notes keep their scope (stale_memory).
    try:
        if before is not None:
            from .checkpoint_scene_map import continues
            continues(key, before)   # A Restore of a checkpoint from before the load still finds its scene.
    finally:
        remember(chat, ui, saved=True, persist=True)
        chat.version += 1


def _await(chat, ui, outgoing, names):
    """A bound chat whose path came back in a file that cannot confirm it: it waits and is offered it (``CHANGED``)."""
    scene = next((name for name in names if name), "")
    record = clean_record({"id": outgoing[0], "path": outgoing[1], "scene": scene, "file": outgoing[2]})
    chat.document_leaving, chat.document = False, None
    chat.awaited_file = record or {"id": outgoing[0], "path": "", "scene": "", "file": None}
    chat.version += 1
    from .document_offer import post
    post(chat, ui)


def _completes(ui):
    """Whether this process decides a deferred chat after the load (``ui.document_load_post``, ``_load_failed``). One
    that registers only ``ui.document_load_pre`` (``headless_main._register``) never would: nothing is deferred there."""
    handlers = getattr(getattr(getattr(ui, "bpy", None), "app", None), "handlers", None)
    return bool(handlers) and getattr(ui, "document_load_post", 0) in handlers.load_post and _load_failed in handlers.load_post_fail


def leaving(ui):
    """``ui.document_load_pre``, before the load serial is renewed: each chat of the outgoing document that holds work
    about it leaves it (``_leave``) and waits for the load to say whether it is the same document (``after_load``).
    The returned chats are left out of ``follow``."""
    global _leaving, _previous
    from .document_work import holds_document_work
    _leaving = None
    identity, path, file = open_document(ui)
    _previous = (identity, path, file)
    chats = list(ui._controllers.values())
    for chat in chats:
        chat.document_leaving = False
    if ui._restoring_snapshot or unattended() or not _completes(ui):
        return ()   # A Restore rebinds every chat itself (checkpoint_restore_ui).
    # An unsaved document too: its chats are invalidated when the load succeeds (it is never the same document),
    # and kept as they were when it fails.
    left = []

    def leave(chat):
        if not _bound_here(chat, ui) or not holds_document_work(chat):
            return
        record = clean_record(chat.state.get("document_file"))
        same = record is not None and record["id"] == identity and record["path"] == path
        names = [record["scene"] if same else None, _scene_name(ui, chat.document[1])]
        chat.document_leaving = True
        left.append((chat, chat.document, names))
        _leave(chat)
    _each(chats, leave)
    _leaving = ((identity, path, file), left)
    return tuple(chat for chat, _key, _names in left)


def follow(ui, left):
    """``ui.document_load_pre``, after the serial is renewed: every other chat is told of the load (``set_document``), as
    up to 1.0.6 only with a window chat (``ui._controller``): the headless host reloads its own file and owns that."""
    _each([chat for chat in ui._controllers.values() if chat not in left and getattr(ui, "_controller", None)],
          lambda chat: chat.set_document(ui.document_key()))


def after_load(ui, *, failed=False):
    """``ui.document_load_post`` (and ``load_post_fail``: nothing was read, so the document and its path are the ones
    before the load): the open document's identity; each chat that left the outgoing document is rebound when
    this is the same document, else invalidated as before; a restored chat waiting for this file binds to it."""
    global _open, _leaving, _previous
    left, _leaving = _leaving, None
    previous, _previous = _previous or _open[1:], None
    path = ui.bpy.data.filepath or ""
    file = previous[2]
    if failed:
        identity, path = previous[0], previous[1]
    elif ui._restoring_snapshot:
        # A Restore reads only this document's own history (its chats' checkpoints): a snapshot taken before the first
        # Save carries no id and stays this document (a new id would strand the saved file's chats); one carrying
        # another id takes it. The person's document stays where it lives, whatever file the Restore opened.
        identity, path = stored(ui.bpy.data.scenes) or previous[0], previous[1]
    else:
        identity = stored(ui.bpy.data.scenes) if path else None
        file = file_identity(path) if path else None   # The file just read: no other path is ever looked at.
    _open = (ui._document_serial, identity or uuid.uuid4().hex, path or "", file)
    if ui._restoring_snapshot or unattended():
        return
    incoming = _open[1:]

    def arrive(entry):
        chat, before, names = entry
        if failed:   # Nothing was read: nothing of the chat changes, its workers and batch included.
            _bind(chat, ui, [_scene_name(ui, before[1])] + names, before, replaced=False)
            return
        same = same_document(left[0][0], left[0][1], incoming[0], incoming[1], left[0][2], incoming[2])
        place = not same and same_place(left[0][0], left[0][1], incoming[0], incoming[1])
        from .worker_ending import REPLACED, end_after_change
        # The load replaced this chat's document: its workers end here, once, before its fate is decided, so no
        # branch below (rebind, an offer then Continue, or invalidation) can keep a part built before the load.
        end_after_change(chat, None if same or place else REPLACED)
        if same:
            _bind(chat, ui, names, before)
        elif place:
            _await(chat, ui, left[0], names)   # Its file cannot confirm it: the person is asked, never told it changed.
        else:
            chat.document_leaving = False
            chat.set_document(ui.document_key())   # Another document: ``document_work.document_changed``.

    def waiting(chat):
        record = getattr(chat, "awaited_file", None)
        if record is not None and same_document(record["id"], record["path"], incoming[0], incoming[1],
                                                record["file"], incoming[2]):
            _bind(chat, ui, [record["scene"]])
        elif record is not None:
            from .document_offer import post
            post(chat, ui)
    _each(left[1] if left is not None else (), lambda entry: arrive(entry))
    if not failed:
        _each(ui._controllers.values(), waiting)


def after_save(ui):
    """``ui.document_save_post`` (never a request snapshot's): the document now lives where it was saved, and the file
    each of its chats works on holds its scenes' names. Only a save of the document itself does this: never a home-file
    save (``save_pre("")``), a Save Copy (written elsewhere) or a save into Angelo's state folder (module docstring)."""
    global _open, _saving
    target, _saving = _saving, None
    path = ui.bpy.data.filepath or ""
    if not target or not path or _canonical(target) != _canonical(path) or _private(ui, target):
        return
    # Blender writes a temporary file and renames it over the target: the file is a new one after every save.
    _open = (ui._document_serial, current(ui), path, file_identity(path))
    _each(ui._controllers.values(), lambda chat: remember(chat, ui, saved=True, persist=True))
    for chat in ui._controllers.values():
        if getattr(chat, "offered_file", None) is not None:
            chat.version += 1   # Its offer may no longer stand: its card redraws without Continue.


def stamp(ui, filepath):
    """``save_pre``: the open document's id into every local scene, so the file carries it. Not into the home file
    (``filepath`` is empty for it: every new document would share its id), nor a request snapshot, nor in a
    background Angelo."""
    global _saving
    _saving = filepath or ""
    if not filepath or ui._capturing_checkpoint or unattended():
        return
    identity = current(ui)
    for scene in ui.bpy.data.scenes:
        if _local(scene) and scene.get(PROPERTY) != identity:
            try:
                scene[PROPERTY] = identity
            except (TypeError, AttributeError, RuntimeError):
                pass   # A scene that refuses it (not editable): the file then carries no agreed id and reads as none.


def _before_save(filepath=""):
    from . import ui
    stamp(ui, filepath)


def _load_failed(filepath=""):
    from . import ui
    after_load(ui, failed=True)


HANDLERS = (("save_pre", _before_save), ("load_post_fail", _load_failed))


def register():
    import bpy
    for name, callback in HANDLERS:
        bpy.app.handlers.persistent(callback)
        handlers = getattr(bpy.app.handlers, name)
        if callback not in handlers:
            handlers.append(callback)


def unregister():
    import bpy
    for name, callback in HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        while callback in handlers:
            handlers.remove(callback)
