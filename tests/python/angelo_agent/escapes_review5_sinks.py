# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Guard piece 9 (ii): what Angelo's exit and cleanup paths destroy comes from C's record of what Angelo made, never
from Python state alone (doc/angelo/script_confinement.rst, "Angelo's own I/O"), for
test_blender_confinement_escapes. A script that reached an object or module of Angelo's could change the path it
holds in place; each check plants a person's file or folder there, as such a script could, runs the cleanup that
reads it, and requires the person's file to be left. Each failed before the fix.

The sinks are those test_native_destructive_sinks finds reachable from an exit or cleanup entry point:

* folders Angelo made and removes (``owned_paths.remove_tree``): the asset preview and payload copies, a render
  child's copy, a headless session's and a worker's private folders, a worker's scratch, a library host job's folder,
  the web preview's export folder;
* files Angelo keeps and saves over or removes (``owned_replace``): the goals and usage records, a chat's pending
  messages (a chat id that walks out of its folder);
* the headless working copy the launcher made (``discard``), the checkpoint store's entries (a name that leaves its
  folder) and a chat lease's descriptor.

Not planted here, on the same route: the library search's preview folder (a closure's cell; ``remove_tree``), the
engine link's export folder (``remove_tree``) and the Fab browser profile's removals (an ``owned`` check before each),
whose setup needs a running browser or server.
"""

import os
import tempfile
import threading
import types
from pathlib import Path


def _person_folder():
    folder = tempfile.mkdtemp(prefix="review5-sinks-")
    with open(os.path.join(folder, "keep.txt"), "w", encoding="utf-8") as handle:
        handle.write("the person's file")
    return folder


def _person_file(folder, name="notes.txt", text="the person's notes"):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _kept(folder):
    return os.path.isfile(os.path.join(folder, "keep.txt"))


def _clean(*paths):
    import shutil
    for path in paths:
        shutil.rmtree(path, ignore_errors=True) if os.path.isdir(path) else (os.path.exists(path) and os.unlink(path))


def _folder_cleanups():
    """(label, setter(folder) -> cleanup callable): an object of Angelo's whose state names ``folder``."""
    from angelo_agent import (blender_asset_payload, blender_asset_previews, headless_session, job_render_child,
                              team_library_host, worker_process)

    def previews(folder):
        item = blender_asset_previews.Previews.__new__(blender_asset_previews.Previews)
        item.worker, item.folder = None, folder
        return item.close

    def payload(folder):
        item = blender_asset_payload.PayloadCopy.__new__(blender_asset_payload.PayloadCopy)
        item.lock, item.closed, item.folder = threading.Lock(), threading.Event(), folder
        item._claim_cleanup = lambda: True
        return item.close

    def render_child(folder):
        child = job_render_child.Child.__new__(job_render_child.Child)
        child.folder, child.running = folder, (lambda: False)
        return child.close

    def headless(folder):
        session = headless_session.HeadlessSession.__new__(headless_session.HeadlessSession)
        session._private = folder
        return session._remove_private

    def worker_private(folder):
        worker = worker_process.WorkerProcess.__new__(worker_process.WorkerProcess)
        worker.private = Path(folder)
        return worker._remove_private

    def worker_scratch(folder):
        worker = worker_process.WorkerProcess.__new__(worker_process.WorkerProcess)
        worker.scratch = Path(folder)
        return worker._remove_scratch

    def library_job(folder):
        job = team_library_host.Job.__new__(team_library_host.Job)
        job.folder = folder
        return job._remove_folder

    return [("asset_previews", previews), ("asset_payload", payload), ("render_child", render_child),
            ("headless_private", headless), ("worker_private", worker_private), ("worker_scratch", worker_scratch),
            ("library_job", library_job)]


def folders_angelo_made(run, check):
    """Each folder cleanup, given a person's folder in its state, leaves it."""
    removed = []
    for label, make in _folder_cleanups():
        folder = _person_folder()
        try:
            make(folder)()
        except Exception as error:   # noqa: BLE001 - a refusal is fine; what counts is what is left.
            if not _kept(folder):
                removed.append((label, repr(error)))
        if not _kept(folder):
            removed.append(label)
        _clean(folder)
    check("cleanup_folders_only_what_angelo_made", not removed, removed)


def web_preview_folder(run, check):
    from angelo_agent import web_preview, web_environment
    folder = _person_folder()
    saved = dict(web_preview._state)
    stop_environment = web_environment.stop
    try:
        web_environment.stop = lambda *args, **kwargs: None
        web_preview._state.update(server=types.SimpleNamespace(close=lambda: None), folder=folder)
        web_preview.stop()
    finally:
        web_environment.stop = stop_environment
        web_preview._state.clear()
        web_preview._state.update(saved)
    kept = _kept(folder)
    _clean(folder)
    check("web_preview_stop_removes_only_its_folder", kept, folder)


def kept_files(run, check):
    """The goals and usage records, pointed at a person's file, never write over it (or beside it)."""
    from angelo_agent import goal_store, usage_meter
    results = {}
    for label, make in (("goals", lambda root: goal_store.GoalStore(root)),
                        ("usage", lambda root: usage_meter.UsageLedger(root))):
        root, person = tempfile.mkdtemp(prefix="review5-store-"), _person_folder()
        try:
            store = make(root)
            notes = _person_file(person)
            store.path = Path(notes)   # As a script that reached the store could.
            if label == "goals":
                store.put("chat", {"state": "active", "goal": "x"})
                store.drain()
            else:
                store.add("chat", {"usage": {"input_tokens": 10, "output_tokens": 5}}, "API")
                store.flush()
            with open(notes, encoding="utf-8") as handle:
                same = handle.read() == "the person's notes"
            results[label] = same and sorted(os.listdir(person)) == ["keep.txt", "notes.txt"]
        except Exception as error:   # noqa: BLE001 - the detail says what broke.
            results[label] = repr(error)
        finally:
            _clean(root, person)
    check("kept_records_save_over_only_their_own_file", all(value is True for value in results.values()), results)


def pending_messages_chat_id(run, check):
    """A chat id that walks out of the pending folder never removes the file it reaches."""
    from angelo_agent import chat_pending
    root, person = tempfile.mkdtemp(prefix="review5-chats-"), _person_folder()
    os.makedirs(os.path.join(root, "pending"))   # The chat store's own folder, as it is once a chat has saved.
    victim = _person_file(person, "victim.json", "{}")
    relative = os.path.relpath(os.path.join(person, "victim"), os.path.join(root, "pending"))
    controller = types.SimpleNamespace(
        store=types.SimpleNamespace(directory=Path(root)), state={"id": relative},
        inbox=types.SimpleNamespace(version=7, records=lambda: []), _pending_saved=0, _pending_refused_at=None)
    try:
        chat_pending.save(controller, final=True)
        outcome = None
    except Exception as error:   # noqa: BLE001 - a refusal is fine; what counts is what is left.
        outcome = repr(error)
    kept = os.path.isfile(victim)
    _clean(root, person)
    check("pending_messages_remove_only_their_own_file", kept, outcome)


def working_copy(run, check):
    """The headless launcher's discard removes only the copy it made, whatever path and identity its state holds."""
    from angelo_agent import headless_files, headless_working_copy
    person = _person_folder()
    notes = _person_file(person, "scene.blend", "the person's scene")
    try:
        headless_working_copy.discard(Path(notes), Path(person) / "other.blend", list(headless_files.identity(notes)))
    except Exception:   # noqa: BLE001 - a refusal is fine.
        pass
    kept = os.path.isfile(notes)
    _clean(person)
    check("working_copy_discard_removes_only_its_copy", kept, notes)


def checkpoint_entry_names(run, check):
    """An anchored store entry's name can't leave the store's folder."""
    from angelo_agent import checkpoint_filesystem
    person = _person_folder()
    store = Path(tempfile.mkdtemp(prefix="review5-store-"))
    reached = []
    for label, call in (("unlink", lambda folder: checkpoint_filesystem._unlink_file(folder, "../%s/keep.txt"
                                                                                       % os.path.basename(person))),
                        ("remove_entry", lambda folder: checkpoint_filesystem.remove_entry(folder, "../%s/keep.txt"
                                                                                             % os.path.basename(person)))):
        os.makedirs(os.path.join(os.path.dirname(store), os.path.basename(person)), exist_ok=True)
        relocated = os.path.join(os.path.dirname(store), os.path.basename(person), "keep.txt")
        _person_file(os.path.dirname(relocated), "keep.txt", "the person's file")
        descriptor = os.open(str(store), os.O_RDONLY) if os.name != "nt" else None
        try:
            call(store.resolve() if descriptor is None else descriptor)
        except Exception:   # noqa: BLE001 - a refusal is the pass.
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if not os.path.isfile(relocated):
            reached.append(label)
        _clean(os.path.dirname(relocated))
    _clean(person, str(store))
    check("checkpoint_entries_stay_in_their_folder", not reached, reached)


def lease_descriptor(run, check):
    """A chat lease's release closes only the lease file's own descriptor, never one put in its place."""
    from angelo_agent import session_lease
    root = tempfile.mkdtemp(prefix="review5-lease-")

    class Writer:   # A store's writer: weakly referenced by its lease's finalizer.
        leased = True

    writer = Writer()
    writer.path = Path(root) / "chat.jsonl"
    other_path = os.path.join(root, "other.txt")
    other = os.open(other_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        held = session_lease.held(writer)
        try:
            status = os.stat(session_lease.lease_path(writer.path))
            identity = (status.st_dev, status.st_ino)
        except OSError:
            identity = None
        if not held or identity not in session_lease._held:
            check("lease_release_closes_only_its_descriptor", True, "no lock on this file system")
            return
        original = session_lease._held[identity].descriptor
        session_lease._held[identity].descriptor = other   # As a script that reached the lease table could.
        writer._lease()   # The finalizer's release.
        try:
            os.fstat(other)
            alive = True
        except OSError:
            alive = False
        try:
            os.close(original)
        except OSError:
            pass
    finally:
        try:
            os.close(other)
        except OSError:
            pass
        _clean(root)
    check("lease_release_closes_only_its_descriptor", alive, identity)


def run_all(run, check):
    folders_angelo_made(run, check)
    web_preview_folder(run, check)
    kept_files(run, check)
    pending_messages_chat_id(run, check)
    working_copy(run, check)
    checkpoint_entry_names(run, check)
    lease_descriptor(run, check)
