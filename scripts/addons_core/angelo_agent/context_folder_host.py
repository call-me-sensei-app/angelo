# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The chat's side of its folders attached as context (``context_folder``, ``context_folder_grants``): grants, the
manifests, the tools' dispatch.

* ``attach`` / ``make_working`` / ``detach``: the person's grants for this chat (``ui_context_folder``'s operator and
  each folder card's Make working folder and Remove). A worker's chat never has one (its runtimes work on a worker's
  copy).
* ``labels``: each folder's name for the model, in attach order; a name two folders share gets " (2)", " (3)"...
  The model names a folder by it (``folder``), never by a path.
* ``observation``: the request's ``context_folders`` field (``console.submit``): each folder's path-free manifest with
  its role (``working`` or ``reference``), built on a thread of its own and kept for ``MANIFEST_TTL`` seconds;
  ``{"folder": name, "role": ..., "pending": true}`` while it is built. A state field, so a delta sends it only when it
  changed and ``null`` once the last folder is removed.
* ``start``: the controller's dispatch of ``angelo_context_folder`` (main thread): it builds the named folder's
  ``ReadOnlyFolder`` (refusing every Angelo state folder, as the file tools do) and runs the call on a ``FolderJob``
  thread. A chat with no folder gets an error result at once. Pictures the tool views are kept in the chat's own
  references folder. Writes are ``context_folder_write``'s, on the working folder only.
No bpy at import (the controller imports this module).
"""

import os
import threading
import time
import uuid

from . import context_folder, owned_paths
from .ui_text import n_, notice

TOOL = context_folder.TOOL
ATTACHED = n_("Angelo can use the folder “{name}” in this chat.")
# A card of 2.1.0 before several folders (one read-only folder per chat): still drawn as its folder's card.
ATTACHED_BEFORE = n_("Angelo can read the folder “{name}” in this chat. It only reads it, never changes it.")
WORKING_LINE = n_("Working folder: Angelo can add and edit text files here, such as notes and checklists. It asks "
                  "before changing a file that was already here, and never deletes, moves or renames one.")
REFERENCE_LINE = n_("Reference folder: Angelo only reads it, never changes it.")
REMOVED = n_("Angelo no longer uses the folder “{name}” in this chat.")
NOW_WORKING = n_("“{name}” is now this chat's working folder. Angelo only reads the other folders.")
REMOVE_ACTION = "notice_context_folder_remove"
WORKING_ACTION = "notice_context_folder_working"
MANIFEST_TTL = 30.0
_grants = {}      # state folder -> context_folder.Grants
_manifests = {}   # (chat id, folder id) -> {"folder": str, "value": dict or None, "at": monotonic, "busy": bool}
# (chat id, folder id) -> this process's token for that folder's current grant (audit 2026-11-14 F2): a job started
# under one grant delivers nothing once the person removes the folder, which drops the token.
_generations = {}
_lock = threading.Lock()


def grants(directory):
    key = str(directory)
    found = _grants.get(key)
    if found is None:
        found = _grants[key] = context_folder.Grants(directory)
    return found


def worker_chat(controller):
    """A worker's chat, on either path: a lane chat in Angelo (``remote_executor``) or one its worker hosts
    (``chat_host``, design A′, whose store is the lead's state folder). Neither has folders (break-it S2)."""
    return (getattr(controller, "remote_executor", None) is not None
            or getattr(controller, "chat_host", None) is not None)


def _store(controller):
    store = getattr(controller, "store", None)
    if worker_chat(controller) or store is None:
        return None
    return grants(store.directory)


def labels(controller):
    """``[{"id", "label", "path", "working"}]`` of the chat's folders, in attach order, without touching the disk."""
    store = _store(controller)
    if store is None:
        return []
    found, seen = [], {}
    for record in store.records(controller.state["id"]):
        name = record["path"].name or str(record["path"])
        seen[name] = seen.get(name, 0) + 1
        found.append(dict(record, label=name if seen[name] == 1 else "%s (%d)" % (name, seen[name])))
    return found


def working(controller):
    """The working folder's label record while it may be written, else None."""
    store = _store(controller)
    for record in labels(controller):
        if record["working"] and store.writable(controller.state["id"], record["id"]) is not None:
            return record
    return None


def folder_of(controller):
    """The working folder, else the first readable one (None without any)."""
    store = _store(controller)
    return None if store is None else store.get(controller.state["id"])


def resolve(controller, name):
    """``(label record, readable path)`` of the folder ``name`` names (None: the working folder, else the only one),
    or ``(None, error for the model)``."""
    records, store = labels(controller), _store(controller)
    if not records:
        return None, context_folder.NO_FOLDER
    if name is None:
        chosen = next((record for record in records if record["working"]), None)
        if chosen is None and len(records) == 1:
            chosen = records[0]
        if chosen is None:
            return None, context_folder.NAME_FOLDER
    else:
        chosen = next((record for record in records if record["label"] == name), None)
        if chosen is None:
            return None, context_folder.UNKNOWN_FOLDER
    folder = store.readable(controller.state["id"], chosen["id"])
    if folder is None:
        return None, "The attached folder can't be opened now; the person can attach it again"   # i18n: model-facing
    return chosen, folder


def _end_reads(controller, folder_id=None):
    """Every read of this chat's folder (``folder_id``; None: every folder) still under way ends now, and its late
    result is dropped."""
    for job in list(getattr(controller, "delegate_jobs", {}).values()):
        if isinstance(job, context_folder.FolderJob) and folder_id in (None, getattr(job, "folder_id", None)):
            job.cancel(context_folder.REMOVED)


def attach(controller, folder):
    """The person's pick: the folder joins the chat (the working folder when it is the first). Returns its id."""
    chat = controller.state["id"]
    folder_id = grants(controller.store.directory).attach(chat, folder)
    with _lock:
        _generations[(chat, folder_id)] = uuid.uuid4().hex
    return folder_id


def make_working(controller, folder_id):
    """The person's click on a reference folder's card (its own C ticket): that folder becomes the working one."""
    record = next((item for item in labels(controller) if item["id"] == folder_id), None)
    if record is None:
        return False
    grants(controller.store.directory).make_working(controller.state["id"], folder_id)
    controller.emit("message", {"role": "system", "notice": True, **notice(NOW_WORKING, name=record["label"])})
    controller.version += 1
    return True


def detach(controller, folder_id=None, announce=True):
    """One folder off the chat (None: every folder). Narrowing: anyone may. When the working folder goes and another
    becomes the working one, the chat says so (``NOW_WORKING``; break-it M3)."""
    chat, store = controller.state["id"], grants(controller.store.directory)
    ids = [folder_id] if folder_id is not None else [record["id"] for record in store.records(chat)]
    with _lock:   # First: nothing of it is delivered from here on.
        for item in ids:
            _generations.pop((chat, item), None)
            _manifests.pop((chat, item), None)
    _end_reads(controller, folder_id)
    promoted = store.revoke(chat, folder_id)
    record = next((item for item in labels(controller) if item["id"] == promoted), None) \
        if isinstance(promoted, str) else None
    if record is not None and announce:
        announce_working(controller, record)
    return record


def announce_working(controller, record):
    controller.emit("message", {"role": "system", "notice": True, **notice(NOW_WORKING, name=record["label"])})
    controller.version += 1


def forget_chat(directory, chat_id):
    """A chat deleted (to Recently Deleted): its folder grants end with it, and nothing of a read under way is
    delivered. Restoring the chat asks for the folders again (F-4)."""
    with _lock:
        for key in [key for key in _generations if key[0] == chat_id]:
            _generations.pop(key, None)
        for key in [key for key in _manifests if key[0] == chat_id]:
            _manifests.pop(key, None)
    grants(directory).revoke(chat_id)


def _grant_holds(chat, folder_id, generation):
    def holds():
        with _lock:
            return _generations.get((chat, folder_id)) == generation
    return holds


def _excluded(controller):
    files = getattr(controller, "files", None)
    return (getattr(files, "source", None) or getattr(files, "initial", None) or []) if files is not None else []


def _workspace(controller, folder, folder_id):
    """The folder's reader, judged at each walk by its read grant in C (``ReadOnlyFolder._granted``)."""
    from .context_folder_grants import key
    return context_folder.ReadOnlyFolder(folder, excluded=_excluded(controller),
                                         grant=key(controller.state["id"], folder_id))


def working_workspace(controller, record):
    """The working folder's ``WorkingFolder``, which never writes into the chat's other folders; ValueError when the
    folder at its path is no longer the one the person attached."""
    from . import context_folder_grants, context_folder_write
    store, chat = _store(controller), controller.state["id"]
    folder = store.writable(chat, record["id"])
    if folder is None:
        raise ValueError(context_folder_write.NO_WORKING)
    references = []   # As attached and as they are now: a reference folder moved since is still never written.
    for other in store.records(chat):
        if other["id"] != record["id"]:
            references.append(store.saved(chat, other["id"]))
            try:
                references.append(context_folder_grants.identity(other["path"]))
            except OSError:   # Gone from its path: its saved identity still holds.
                pass
    workspace = context_folder_write.WorkingFolder(folder, excluded=_excluded(controller), references=references,
                                                   grant=context_folder_grants.key(chat, context_folder_grants.WORKING))
    if workspace.identity is not None and workspace.identity != store.saved_identity(chat, record["id"]):
        raise ValueError(context_folder_write.NO_WORKING)   # Replaced between the grant check and the open.
    return workspace


def observation(controller):
    """The manifests for this request (a list, attach order), or None without a folder; refreshes stale ones."""
    records = labels(controller)
    if not records:
        return None
    chat, store, found = controller.state["id"], _store(controller), []
    for record in records:
        folder = store.readable(chat, record["id"])
        role = "working" if record["working"] else "reference"
        if folder is None:
            found.append({"folder": record["label"], "role": role, "unreadable": True})
            continue
        key = (chat, record["id"])
        with _lock:
            entry = _manifests.get(key)
            if entry is None or entry["folder"] != str(folder):
                entry = _manifests[key] = {"folder": str(folder), "value": None, "at": 0.0, "busy": False,
                                           "ready": threading.Event()}   # Set once the first manifest is built.
            if time.monotonic() - entry["at"] > MANIFEST_TTL and not entry["busy"]:
                entry["busy"] = True
                threading.Thread(target=_build, args=(entry, _workspace(controller, folder, record["id"]), folder.name),
                                 daemon=True, name="AngeloContextManifest").start()
            value = entry["value"]
        # Its name and role as they are now: the person may switch the working folder between builds.
        found.append(dict(value or {"pending": True}, folder=record["label"], role=role))
    return found


def _build(entry, workspace, label):
    try:
        value = context_folder.manifest(workspace, label)
    except (OSError, ValueError):
        value = {"folder": label, "unreadable": True}
    with _lock:
        entry.update(value=value, at=time.monotonic(), busy=False)
    entry["ready"].set()


def stage_image(references, data, suffix):
    """A viewed picture kept as an image of this chat (``references``' own folder), as a tool image record."""
    owned_paths.require_writable(references.directory)   # The chat's own images folder, as C knows it.
    references.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = references.directory / (str(uuid.uuid4()) + (".jpg" if suffix == ".jpeg" else suffix))
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return {"path": str(path), "id": path.stem, "name": path.name,
            "mime": context_folder.IMAGES[suffix], "kind": "image", "bytes": len(data)}


def start(controller, call):
    """Run ``call`` off the main thread; its result arrives as ``delegate_finished``."""
    call.approval = False   # Read-only, inside what the person attached: never asked about.
    if worker_chat(controller):   # Refused as every tool a worker's chat can't run is (worker_refusals, S2).
        from . import worker_refusals
        try:
            worker_refusals.admit(TOOL, call.arguments)
        except ValueError as error:
            controller.emit("delegate_finished", {"call_id": call.id, "_call": call, "result": {
                "error": str(error), "error_code": "UNSUPPORTED", "scene_changed": False}})
            return None
    record, folder = resolve(controller, call.arguments.get("folder"))
    if record is None:
        controller.emit("delegate_finished", {"call_id": call.id, "result": {"error": folder}, "_call": call})
        return None
    references = controller.references
    try:
        workspace = _workspace(controller, folder, record["id"])
    except (OSError, ValueError):   # Moved, removed or no longer a folder: said without its path.
        controller.emit("delegate_finished", {"call_id": call.id, "_call": call, "result": {
            "error": "The attached folder can't be opened now; the person can attach it again"}})   # i18n: model-facing
        return None
    chat = controller.state["id"]
    with _lock:   # A grant from before this process (a saved one) gets its token on first use.
        generation = _generations.setdefault((chat, record["id"]), uuid.uuid4().hex)
    job = context_folder.FolderJob(controller, call, workspace,
                                   lambda data, suffix: stage_image(references, data, suffix),
                                   granted=_grant_holds(chat, record["id"], generation))
    job.folder_id = record["id"]
    controller.delegate_jobs[call.id] = job
    job.start()
    return None


# The folder cards (chat_notices) -----------------------------------------------------------------------------------

def card_folder(controller, message):
    """The label record of the folder a card names while it is attached, without touching the disk: by the card's
    folder id, or (a card from before several folders) by its name when only one folder has it. None otherwise."""
    recipe = message.get("i18n")
    fields = recipe[1] if isinstance(recipe, (list, tuple)) and len(recipe) > 1 and isinstance(recipe[1], dict) else {}
    folder_id, name = message.get("context_folder_id"), fields.get("name")
    records = labels(controller)
    if folder_id is not None:
        return next((record for record in records if record["id"] == folder_id), None)
    named = [record for record in records if record["path"].name == name]
    return named[0] if len(named) == 1 else None


def attached_stands(controller, message=None):
    """A card's pills are drawn while its folder is attached to this chat: from what is saved and held, never the
    disk (asked at each redraw, on the main thread; F-5). Without ``message``: whether any folder is attached."""
    if message is None:
        return bool(labels(controller))
    return card_folder(controller, message) is not None


def remove(controller, folder_id=None):
    """A card's Remove: its folder (also one moved or deleted since: its grant is still removed, F-4)."""
    records = labels(controller)
    record = next((item for item in records if item["id"] == folder_id), None) if folder_id is not None else (
        records[0] if len(records) == 1 else None)
    if record is None:
        return False
    promoted = detach(controller, record["id"], announce=False)
    controller.emit("message", {"role": "system", "notice": True, **notice(REMOVED, name=record["label"])})
    controller.version += 1
    if promoted is not None:   # Then which folder Angelo writes in now (M3).
        announce_working(controller, promoted)
    return True


from . import grant_guard as _grant_guard  # noqa: E402  (the entry behind a grant: grant_guard.entry)
attach = _grant_guard.entry(attach)   # The person's pick lets the agent read a folder (and write the first one).
make_working = _grant_guard.entry(make_working)   # The person's click moves the writable folder.
