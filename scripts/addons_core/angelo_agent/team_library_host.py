# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread binding of the team-library tools: preconditions, worker jobs and their results.

Contract: doc/angelo/team_library.rst. ``BlenderExecutor`` routes the
``angelo_team_*`` family here.

* **Threads.** Every call returns a ``TeamPending`` at once. Its network work
  (console requests, storage transfers, file checks) runs on one worker thread
  per call, at most ``MAX_ACTIVE`` at a time; Blender work (an import, adding a
  preview to the chat) runs when the controller polls it on the main thread.
  Preconditions (before an approval card) never touch the network: an import's
  card shows what earlier results said about the item (``team_library_results``).
* **Gate.** Each call needs the signed-in team account with the console scope
  (``team_library_access``); the client sends its access token to the console
  only.
* **Temporary files** live in a private folder per call (``tempfile.mkdtemp``,
  mode 0700) that is removed when the call ends, is stopped or fails.
* **Failures** are reported, never retried, except a library ``complete`` the
  console answers with 429 (documented as idempotent and retried). An upload
  that fails after the console reserved the item deletes that reservation.
  Refusals raised before the scene can change carry a closed code
  (``refusal_codes``), so they never raise the request's recovery barrier: a
  refused file is ``UNSUPPORTED``, a transfer failure has its own code
  (``team_library_transfer``) and the four-at-a-time limit is
  ``LIMIT_EXCEEDED``. Every other failure stays unclassified and fails closed.
* **Scene claims.** Workers never touch Blender data, so their failures report
  ``scene_changed: false``. A failure on the main thread of an import into the
  scene claims that only when raised before Blender changed anything or after a
  verified rollback; otherwise it reports ``partial_changes_possible``.
"""

import hashlib
import re
import shutil
import tempfile
import threading
from pathlib import Path

from . import team_library_access, team_library_results as results
from .studio_agent import StudioPending
from .team_errors import TeamError
from .refusal_codes import RefusalError, refusal_code
from .team_library_client import TeamLibraryClient, TeamLibraryError
from .team_library_files import (IMAGE_FORMATS, MAX_BYTES, ImportFailed, NotImported, Refused, check_image, extension,
                                 inspect, sniff)
from .team_library_spec import PREFIX, validate_team
from .team_library_transfer import Cancelled, TransferError, download, fetch, upload

MAX_ACTIVE = 4
READ_SECONDS, IMPORT_SECONDS, SAVE_SECONDS = 120, 900, 1800
PREVIEW_BYTES = 5 * 1024 * 1024
COMPLETE_ATTEMPTS = 5
_active = set()
_active_lock = threading.Lock()


def _message(error):
    if isinstance(error, TeamError):
        return error.message
    if isinstance(error, (Refused, TransferError, ValueError)):
        return str(error)
    if isinstance(error, OSError):
        return "Angelo couldn't use its temporary folder for this transfer; nothing was changed."
    return "The team library request failed unexpectedly; nothing was changed."


def _closed_code(error):
    """The closed code of a refusal raised before any scene change, or None (left unclassified).

    Only host-authored refusals count; a console or gateway code never becomes one.
    """
    if isinstance(error, (Refused, NotImported)):
        return "UNSUPPORTED"
    if isinstance(error, (TransferError, RefusalError, TeamBusyError)):
        code = refusal_code(error)
        return None if code in {"UNKNOWN", "INTERNAL_ERROR"} else code
    return None


def _failure(message, code):
    """A failure that left the scene unchanged."""
    result = {"error": message, "scene_changed": False}
    if code:
        result["error_code"] = code
    return result


def _finish_failure(error, changes_scene):
    """A failure while the main thread finished a call.

    An import into the scene claims an unchanged scene only for a refusal raised
    before Blender changed anything or after a verified rollback: an
    unclassified failure never claims it (agent_execution.rst).
    """
    unchanged = (not changes_scene or isinstance(error, (Refused, NotImported))
                 or getattr(error, "scene_unchanged", False) is True)
    if unchanged:
        return _failure(_message(error) if not isinstance(error, RuntimeError) else str(error)[:400], _closed_code(error))
    message = (str(error)[:400] if isinstance(error, ImportFailed) else
               "The import failed after Blender began changing the scene; part of it may remain. "
               "Check the scene before continuing.")
    return {"error": message, "partial_changes_possible": True}


class TeamBusyError(RuntimeError):
    """Refused before any work: four team-library calls already run."""
    partial_changes_possible = False
    refusal_code = "LIMIT_EXCEEDED"


class Job:
    """One worker thread and the private folder it may write; the result is read once on the main thread."""

    def __init__(self, work, folder=None):
        self.cancel, self.done = threading.Event(), threading.Event()
        self.value, self.error, self.folder = None, None, folder
        self.error_code = None  # A closed refusal code for ``error``, or None when unclassified.
        self._lock, self._released = threading.Lock(), False
        with _active_lock:
            if len(_active) >= MAX_ACTIVE:
                self._remove_folder()
                raise TeamBusyError("Wait for a team-library request to finish (four run at a time).")
            _active.add(self)
        threading.Thread(target=self._run, args=(work,), daemon=True, name="AngeloTeamLibrary").start()

    def _run(self, work):
        try:
            self.value = work(self.cancel)
        except Exception as error:  # noqa: BLE001 - every failure becomes a fixed, secret-free message.
            # The worker has no Blender data access: whatever failed, no scene changed.
            self.error, self.error_code = _message(error), _closed_code(error)
        finally:
            with _active_lock:
                _active.discard(self)
            with self._lock:
                self.done.set()
                released = self._released
            if released:
                self._remove_folder()

    def release(self):
        """Stop the worker if it still runs; remove the folder now, or when it stops."""
        self.cancel.set()
        with self._lock:
            self._released = True
            finished = self.done.is_set()
        if finished:
            self._remove_folder()

    def _remove_folder(self):
        if self.folder is not None:
            shutil.rmtree(self.folder, ignore_errors=True)


class TeamPending(StudioPending):
    """A team-library call the controller polls on the main thread (``Controller._pump_events``)."""

    def __init__(self, job, finish, *, status_text, activity_note, timeout, changes_scene=False):
        super().__init__(self._poll_job, job.cancel.set, cleanup=job.release)
        self.job, self._finish, self.changes_scene = job, finish, changes_scene
        self.status_text, self.activity_note, self.timeout = status_text, activity_note, timeout

    def _poll_job(self):
        if not self.job.done.is_set():
            return None
        if self.job.error is not None:
            return _failure(self.job.error, self.job.error_code)
        try:
            return self._finish(self.job.value)
        except Exception as error:  # noqa: BLE001 - an import rolls itself back before raising.
            return _finish_failure(error, self.changes_scene)


def _file_name(title, suffix):
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip().replace(" ", "-")[:80].strip(".-")
    return (stem or "angelo-item") + suffix


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _window_for(scene):
    """A window showing ``scene`` with a 3D Viewport, or None (the scene or every such window closed)."""
    import bpy
    try:
        scene.name
    except ReferenceError:
        return None
    for window in bpy.context.window_manager.windows:
        if window.scene == scene and any(a.type == 'VIEW_3D' for a in window.screen.areas):
            return window
    return None


class TeamLibraryService:
    def __init__(self, controller, window):
        if controller is None:
            raise RuntimeError("Team-library tools must be bound to an active chat.")
        self.controller, self.window = controller, window

    def _client(self):
        account = team_library_access.account()
        reason = team_library_access.refusal(account)
        if reason:
            raise ValueError(reason)
        return TeamLibraryClient(account)

    def _pending(self, work, finish, status, timeout, folder=None, note="", changes_scene=False):
        job = Job(work, folder)
        return TeamPending(job, finish or (lambda value: value), status_text=status, timeout=timeout,
                           activity_note=note or "Reading your Angelo team's library; nothing changes.",
                           changes_scene=changes_scene)

    # Preconditions: main thread, before the approval card, no network ----------------------------

    def precondition(self, name, arguments):
        args = validate_team(name, arguments)
        client = self._client()
        if name == PREFIX + "library_import":
            facts = results.known(client.org_id, args["item_id"])
            if not facts:
                raise RefusalError("Look the item up first (angelo_team_library_get, or find it with "
                                   "angelo_team_library_search) so the approval can show what it is.", code="VALIDATION")
            places, note = results.importable({"kind": facts.get("kind"), "mimeType": facts.get("mimeType"),
                                               "fileName": facts.get("fileName")})
            into = args.get("into", "SCENE")
            if places is None:
                raise RefusalError(note, code="UNSUPPORTED")
            if into not in places:
                raise RefusalError("Only images can be imported into STUDIO; import this model into SCENE.", code="VALIDATION")
            if into == "SCENE" and not any(a.type == 'VIEW_3D' for a in self.window.screen.areas):
                raise RefusalError("Keep a 3D Viewport (or the Director preview) open to import into the scene.", code="UNSUPPORTED")
            return (client.org_id, args["item_id"], into, facts.get("updatedAt"), facts.get("sizeBytes"))
        if name == PREFIX + "library_save":
            from .team_library_save import check_source
            return (client.org_id, args["space"], check_source(self.controller, self.window, args))
        return None

    # Execution: main thread, returns a TeamPending ----------------------------------------------------

    def execute(self, name, arguments):
        args = validate_team(name, arguments)
        client = self._client()
        return getattr(self, "_" + name.removeprefix(PREFIX))(client, args)

    def _library_search(self, client, args):
        def work(_cancel):
            items, cursor = client.search(space=args.get("space", "team"), query=args.get("query"),
                                          kind=args.get("kind"), tag=args.get("tag"),
                                          ip_entity_id=args.get("ip_entity_id"), cursor=args.get("cursor"),
                                          limit=args.get("limit", 10))
            for item in items:
                results.remember(client.org_id, item)
            return {"organization": client.org_name, "space": args.get("space", "team"),
                    "items": [results.item(i, client.user_id) for i in items], "next_cursor": cursor}
        return self._pending(work, None, "Searching the team library", READ_SECONDS)

    def _library_get(self, client, args):
        folder = Path(tempfile.mkdtemp(prefix="angelo-team-")) if args.get("preview") else None

        def work(cancel):
            item = client.item(args["item_id"])
            results.remember(client.org_id, item)
            value = {"item": results.item(item, client.user_id, full=True)}
            if folder is None:
                return value
            if not item.get("thumbnailUrl"):
                value["preview_note"] = "This item has no thumbnail."
                return value
            try:
                data = fetch(item["thumbnailUrl"], limit=PREVIEW_BYTES)
                fmt = sniff(data[:65536])
                if fmt not in IMAGE_FORMATS:
                    raise Refused("The thumbnail isn't an image.")
                check_image(data, "The thumbnail")
            except (TransferError, Refused) as error:
                value["preview_note"] = str(error)
                return value
            path = folder / ("preview" + IMAGE_FORMATS[fmt])
            path.write_bytes(data)
            value["_preview"] = path
            return value

        def finish(value):
            preview = value.pop("_preview", None)
            if preview is not None:
                try:
                    value["views"] = self.controller.references.import_images([str(preview)])
                except (ValueError, OSError):
                    value["preview_note"] = "The thumbnail couldn't be added to the chat."
            return value
        return self._pending(work, finish, "Reading the team library", READ_SECONDS, folder)

    def _ips_list(self, client, args):
        def work(_cancel):
            ips, cursor = client.ips_page(space=args.get("space", "team"), archived=args.get("archived", False),
                                          limit=args.get("limit", 20), cursor=args.get("cursor"))
            return {"organization": client.org_name, "ips": [results.ip(value) for value in ips], "next_cursor": cursor}
        return self._pending(work, None, "Reading your team's IPs", READ_SECONDS)

    def _ip_get(self, client, args):
        def work(_cancel):
            detail, cursor = client.ip_page(args["ip_id"], kind=args.get("kind"), limit=args.get("limit", 50),
                                            cursor=args.get("cursor"))
            return {"ip": results.ip(detail.get("ip") or {}),
                    "entities": [results.entity_summary(e) for e in detail.get("entities") or [] if isinstance(e, dict)],
                    "next_cursor": cursor}
        return self._pending(work, None, "Reading an IP", READ_SECONDS)

    def _ip_entity(self, client, args):
        def work(_cancel):
            value = client.entity(args["ip_id"], args["entity_id"])
            for reference in value.get("references") or []:
                media = reference.get("media") if isinstance(reference, dict) else None
                if isinstance(media, dict) and isinstance(reference.get("libraryItemId"), str):
                    results.remember(client.org_id, reference["libraryItemId"], title=reference.get("title"),
                                     kind=media.get("kind"), mimeType=media.get("mimeType"),
                                     sizeBytes=media.get("sizeBytes"))
            return results.entity(value)
        return self._pending(work, None, "Reading an IP entity", READ_SECONDS)

    def _ips_resolve(self, client, args):
        def work(_cancel):
            try:
                value = client.resolve(args["entities"], args.get("purposes"), args.get("limit", 8))
            except TeamLibraryError as error:
                if error.code == "CHARACTER_NOT_READY":
                    return results.not_ready(error, args["entities"])
                raise
            names = {e.get("entityId"): e.get("name") for e in value.get("entities") or [] if isinstance(e, dict)}
            for reference in value.get("references") or []:
                if isinstance(reference, dict) and isinstance(reference.get("libraryItemId"), str):
                    results.remember(client.org_id, reference["libraryItemId"], kind="image",
                                     title="%s · %s" % (names.get(reference.get("entityId")) or "IP reference",
                                                        str(reference.get("kind", "")).replace("_", " ")))
            return results.resolved(value)
        return self._pending(work, None, "Resolving IP entities", READ_SECONDS)

    def _library_import(self, client, args):
        item_id, into = args["item_id"], args.get("into", "SCENE")
        scene = self.window.scene
        folder = Path(tempfile.mkdtemp(prefix="angelo-team-"))

        def work(cancel):
            item = client.item(item_id)
            results.remember(client.org_id, item)
            places, note = results.importable(item)
            if places is None:  # The item changed since its card: the precondition's own refusals.
                raise RefusalError(note, code="UNSUPPORTED")
            if into not in places:
                raise RefusalError("Only images can be imported into STUDIO; import this model into SCENE.", code="VALIDATION")
            target = folder / "download"
            digest = item.get("sha256") if isinstance(item.get("sha256"), str) else None
            download(client.download_link(item_id), target, limit=MAX_BYTES[item["kind"]],
                     expected=item.get("sizeBytes"), sha256=digest, cancel=cancel)
            facts = inspect(target, item["kind"])
            final = target.with_name("item" + extension(facts["format"]))
            target.rename(final)
            return {"item": item, "facts": facts, "path": final}

        def finish(value):
            from . import team_library_import
            facts = value["facts"]
            if into == "STUDIO":
                result = team_library_import.import_into_studio(value["path"], value["item"])
            else:
                window = _window_for(scene)
                if window is None:
                    raise NotImported("The scene's window closed (or another file was opened); nothing was imported.")
                result = team_library_import.import_into_scene(window, value["path"], facts, value["item"])
            return dict(result, format=facts["format"].upper(), bytes=facts["bytes"])
        return self._pending(work, finish, "Importing from the team library", IMPORT_SECONDS, folder,
                             note="Downloading from your team's library; the file is checked before Blender reads it.",
                             changes_scene=into == "SCENE")

    def _library_save(self, client, args):
        from .team_library_save import prepare
        folder = Path(tempfile.mkdtemp(prefix="angelo-team-"))
        try:
            prepared = prepare(self.controller, self.window, args, folder)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        metadata = {"title": args["title"].strip(), "tags": args.get("tags", []), "provenance": prepared["provenance"]}
        if args.get("description", "").strip():
            metadata["description"] = args["description"].strip()
        for key in ("width", "height"):
            if prepared.get(key):
                metadata[key] = prepared[key]

        def work(cancel):
            path, size = prepared["path"], prepared["size"]
            reserved = client.create_upload(space=args["space"], file_name=_file_name(args["title"], prepared["extension"]),
                                            mime_type=prepared["mime"], size=size, sha256=_sha256(path))
            try:
                upload(reserved["uploadUrl"], reserved.get("headers") or {"content-type": prepared["mime"]}, path, size,
                       cancel=cancel)
                thumbnail = prepared.get("thumbnail")
                if thumbnail is not None and reserved.get("thumbnailUploadUrl"):
                    try:
                        upload(reserved["thumbnailUploadUrl"], {"content-type": "image/webp"}, thumbnail,
                               thumbnail.stat().st_size, cancel=cancel)
                    except TransferError:
                        pass  # Optional: the console shows a typed placeholder.
                item = self._complete(client, reserved["itemId"], metadata, cancel)
            except Exception:
                try:
                    client.delete_item(reserved["itemId"])
                except TeamError:
                    pass  # The console never lists an incomplete upload; best effort to free its quota.
                raise
            results.remember(client.org_id, item)
            shared = ("everyone in %s" % client.org_name) if args["space"] == "team" else "only you"
            return {"saved": results.item(item, client.user_id, full=True), "item_id": item.get("id"),
                    "visible_to": shared, "blender_undoable": False, "scene_changed": False}
        return self._pending(work, None, "Saving to the team library", SAVE_SECONDS, folder,
                             note="Uploading to your team's library; Blender Undo doesn't reverse an upload.")

    @staticmethod
    def _complete(client, item_id, metadata, cancel):
        for attempt in range(COMPLETE_ATTEMPTS):
            try:
                return client.complete_upload(item_id, metadata)
            except TeamLibraryError as error:
                if error.code != "RATE_LIMITED" or attempt == COMPLETE_ATTEMPTS - 1:
                    raise
                if cancel.wait(min(30, error.retry_after or 2)):
                    raise Cancelled("The upload was stopped; nothing was added to the library.") from None
        raise TransferError("The console stayed busy; nothing was added to the library. Try again later.", "LIMIT_EXCEEDED")


def service_for(controller, window):
    return TeamLibraryService(controller, window)
