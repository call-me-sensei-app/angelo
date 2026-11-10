# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Main-thread binding of the Angelo Library tools: preconditions, the catalog wait, previews and imports.

Contract: doc/angelo/library.rst, "Agent tools". ``BlenderExecutor`` routes the ``angelo_library_*`` family here.
Schemas and validation: ``library_spec``; the catalog, downloads and checks: ``library_host``; the import itself:
``library_import``. Results and refusals are model-facing English, never translated.

* **Threads.** A search with the catalog in memory and no preview answers at once. Otherwise the call returns a
  ``LibraryPending`` the controller polls on the main thread: it waits for the catalog (at most ``CATALOG_WAIT``),
  downloads thumbnails on one worker thread per call, or waits for ``library_host``'s file download (its own worker
  pool) and then imports on the main thread. Preconditions (before the approval card) never touch the network: they
  read the catalog in memory and the scene.
* **Refusals** before the scene can change carry a closed code (``refusal_codes``) and ``scene_changed: false``: an
  unknown item, a wrong argument for the item's kind or a texture set without meshes is ``VALIDATION``; a catalog
  that is unreachable or still loading is ``EXPIRED``; a download or check failure has ``library_host.describe``'s
  code. An import that failed after Blender began changing the scene claims an unchanged scene only after
  ``library_import``'s verified rollback; otherwise it reports ``partial_changes_possible``.
* **Stop** ends the wait. A download already running finishes into the library's cache (it is shared with the
  Library overlay and holds only public files), and nothing is imported.
"""

import shutil
import threading
import time

from . import library_catalog, library_host, live_target, live_windows, private_roots, shared_replace
from .library_spec import DEFAULT_RESULTS, IMPORT, MAX_PREVIEWS, SEARCH, compact, resolutions, validate_library
from .refusal_codes import RefusalError, refusal_code, refusal_details, coded
from .studio_agent import StudioPending
from .team_library_files import ImportFailed, NotImported, Refused

CATALOG_WAIT = 20.0              # Seconds a call waits for the catalog to load before it refuses.
SEARCH_SECONDS, IMPORT_SECONDS = 120, 900
PREVIEW_FOLDER = "angelo-library-"   # private_roots.SCRATCH_PREFIXES: thumbnails copied under an image suffix.
IMAGES_PER_IMPORT = 4            # references.MAX_IMAGES: one import_images call takes at most four files.


class LibraryPending(StudioPending):
    """A library call the controller polls on the main thread (``Controller._pump_events``)."""

    def __init__(self, poll, *, status_text, activity_note, timeout, cancel=lambda: None, cleanup=lambda: None):
        super().__init__(poll, cancel, cleanup=cleanup)
        self.status_text, self.activity_note, self.timeout = status_text, activity_note, timeout


def _failure(message, code=None):
    """A failure that left the scene unchanged; ``code`` is a closed refusal code, or None (unclassified)."""
    result = {"error": message, "scene_changed": False}
    if code:
        result["error_code"] = code
    return result


def _finish_failure(error):
    """A failure on the main thread while importing: unchanged only when refused first or rolled back verifiably."""
    if isinstance(error, (NotImported, Refused)):
        return _failure(str(error)[:400], "UNSUPPORTED")
    if isinstance(error, RefusalError):
        return _failure(str(error)[:400], refusal_code(error))
    if isinstance(error, ImportFailed) and error.scene_unchanged is True:
        return _failure(str(error)[:400])
    message = (str(error)[:400] if isinstance(error, ImportFailed) else
               "The import failed after Blender began changing the scene; part of it may remain. "
               "Check the scene before continuing.")
    return {"error": message, "partial_changes_possible": True}


def _catalog_refusal(lib):
    if lib.status in ("idle", "loading"):
        return RefusalError("The Angelo Library's catalog is still loading; nothing changed. Search the library "
                            "(angelo_library_search waits for it), then import.", code="EXPIRED")
    detail = (" (%s)" % lib.error[:200]) if lib.error else ""
    return RefusalError("The Angelo Library is unavailable%s; nothing changed. Check the network and try again "
                        "later." % detail, code="EXPIRED")


def _image_suffix(head):
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if head[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


class _Worker:
    """One daemon thread; ``value`` or ``error`` is read once on the main thread after ``done``."""

    def __init__(self, work):
        self.cancel, self.done = threading.Event(), threading.Event()
        self.value, self.error = None, None
        threading.Thread(target=self._run, args=(work,), daemon=True, name="AngeloLibraryAgent").start()

    def _run(self, work):
        try:
            self.value = work(self.cancel)
        except Exception as error:  # noqa: BLE001 - reported as a fixed sentence; the worker touches no Blender data.
            self.error = error
        finally:
            self.done.set()


def _previews(root, entries, folder, cancel):
    """Download (or reuse) thumbnails and copy each under its image suffix: ``[(item id, Path)]``. Worker thread."""
    from . import library_fetch
    found = []
    for identity, url in entries:
        if cancel.is_set():
            break
        path = library_fetch.thumbnail(root, url)
        if path is None:
            continue
        data = shared_replace.read_bytes(path)   # At most MAX_THUMB (2 MB); another Angelo's fetch may replace it.
        suffix = _image_suffix(data[:12])
        if suffix is None:
            continue
        target = folder / ("%d%s" % (len(found) + 1, suffix))
        target.write_bytes(data)
        found.append((identity, target))
    return found


def _window_for(scene):
    """A window showing ``scene`` with a 3D Viewport, or None (the scene or every such window closed)."""
    import bpy
    try:
        scene.name
    except ReferenceError:
        return None
    for window in live_windows.windows(bpy.context.window_manager):
        if window.scene == scene and any(a.type == 'VIEW_3D' for a in live_windows.areas(window)):
            return window
    return None


class LibraryService:
    window = live_target.WindowAttribute()   # Its imports finish ticks later: read live then (live_target).

    def __init__(self, controller, window):
        if controller is None:
            raise coded(RuntimeError("Angelo Library tools must be bound to an active chat."), "OWNERSHIP")
        self.controller, self.window = controller, window

    # Shared checks: main thread, catalog in memory, no network ---------------------------------------------------

    def _item(self, args):
        """The library and the item an import names, checked against the arguments its kind takes."""
        lib = library_host.library()
        lib.ensure_catalog()   # Returns at once; starts a load when none is in memory.
        if lib.catalog is None:
            raise _catalog_refusal(lib)
        item = lib.get(args["item_id"])
        if item is None:
            raise RefusalError("No Angelo Library item has this id; find one with angelo_library_search and pass its "
                               "id exactly.", code="VALIDATION")
        kind, resolution = item["kind"], args.get("resolution")
        if resolution is not None and kind not in ("texture", "hdri"):
            raise RefusalError("resolution applies to texture sets and HDRIs only; leave it out for this %s." % kind,
                               code="VALIDATION")
        if resolution is not None and resolution not in resolutions(item):
            raise RefusalError("This item comes in %s." % ", ".join(resolutions(item)), code="VALIDATION")
        if "location" in args and kind not in ("model", "vfx"):
            raise RefusalError("location places models and VFX sprites only; leave it out for this %s." % kind,
                               code="VALIDATION")
        if "objects" in args and kind != "texture":
            raise RefusalError("objects names a texture set's meshes only; leave it out for this %s." % kind,
                               code="VALIDATION")
        return lib, item

    def _targets(self, item, args):
        """The meshes a texture set goes on: the named handles, else the selected meshes. [] for other kinds."""
        if item["kind"] != "texture":
            return []
        if args.get("objects"):
            from .scene_queries import SceneQueryError, resolve_object
            objects = []
            for handle in args["objects"]:
                try:
                    objects.append(resolve_object(self.window, handle))
                except SceneQueryError as error:
                    raise RefusalError(str(error), code=refusal_code(error), details=refusal_details(error)) from None
        else:
            layer = self.window.view_layer
            objects = [o for o in self.window.scene.objects if o.type == 'MESH' and o.select_get(view_layer=layer)]
            if not objects:
                raise RefusalError("A texture set needs meshes: pass objects (handles from a scene lookup), or select "
                                   "the meshes first.", code="VALIDATION")
        for obj in objects:
            if obj.type != 'MESH':
                raise RefusalError("“%s” is a %s; a texture set goes on meshes." % (obj.name[:64], obj.type.lower()),
                                   code="VALIDATION")
            if obj.library is not None or getattr(obj.data, "library", None) is not None:
                raise RefusalError("“%s” is linked from another file and can't take a new material." % obj.name[:64],
                                   code="VALIDATION")
        return objects

    def _require_view(self):
        if not any(a.type == 'VIEW_3D' for a in live_windows.areas(self.window)):
            raise RefusalError("Keep a 3D Viewport (or the Director preview) open to import into the scene.",
                               code="UNSUPPORTED")

    # Preconditions: main thread, before the approval card ----------------------------------------------------------

    def precondition(self, name, arguments):
        args = validate_library(name, arguments)
        if name != IMPORT:
            return None
        if args['item_id'].startswith('fab:'):
            from . import fab_library_agent
            return fab_library_agent.precondition(self, args)
        if args['item_id'].startswith('blender:'):
            from . import blender_assets_agent
            return blender_assets_agent.precondition(self, args)
        _lib, item = self._item(args)
        targets = self._targets(item, args)
        self._require_view()
        files, used = library_catalog.files_for(item, args.get("resolution"))
        return (item["id"], used, tuple((f["name"], f["size"]) for f in files), tuple(o.session_uid for o in targets),
                tuple(args.get("location") or ()))

    # Execution: main thread ------------------------------------------------------------------------------------------

    def execute(self, name, arguments):
        args = validate_library(name, arguments)
        return self._search(args) if name == SEARCH else self._import(args)

    def _search_result(self, lib, args):
        limit = args.get("limit", DEFAULT_RESULTS)
        offset = args.get("offset", 0)
        items, total = lib.search(args["query"], args.get("kind"), limit, offset)
        rows = [compact(item, library_catalog.item_bytes(item), lib.cached(item)) for item in items]
        result = {"query": args["query"], "total": total, "items": rows, "scene_changed": False}
        result['next_offset'] = offset + len(rows) if offset + len(rows) < total else None
        if args.get("kind"):
            result["kind"] = args["kind"]
        if not rows:
            result["note"] = "Nothing in the library matches; try fewer or more general words, or another kind."
        return result, items

    def _search(self, args):
        if args.get('source') == 'fab':
            from . import fab_library_agent
            return fab_library_agent.search(self, args)
        if args.get('source') == 'blender':
            from . import blender_assets_agent
            return blender_assets_agent.search(self, args)
        lib = library_host.library()
        lib.ensure_catalog()
        if lib.catalog is not None and not args.get("preview"):
            return self._search_result(lib, args)[0]
        deadline = time.monotonic() + CATALOG_WAIT
        state = {"result": None, "worker": None, "folder": None}

        def poll():
            if state["result"] is None:
                if lib.catalog is None:
                    if lib.status in ("idle", "loading") and time.monotonic() < deadline:
                        return None
                    if lib.status in ("idle", "loading"):
                        return _failure("The Angelo Library's catalog didn't load within %d seconds; nothing changed. "
                                        "Try the search again shortly." % CATALOG_WAIT, "EXPIRED")
                    refusal = _catalog_refusal(lib)
                    return _failure(str(refusal), refusal.code)
                result, items = self._search_result(lib, args)
                if not args.get("preview"):
                    return result
                state["result"] = result
                entries = [(item["id"], item["thumb"]) for item in items[:MAX_PREVIEWS] if item.get("thumb")]
                if not entries:
                    return dict(result, preview_note="These results have no thumbnails.")
                state["folder"] = folder = private_roots.allocate(PREVIEW_FOLDER)
                state["worker"] = _Worker(lambda cancel: _previews(lib.root, entries, folder, cancel))
                return None
            worker = state["worker"]
            if not worker.done.is_set():
                return None
            return self._attach_previews(state["result"], worker)

        def cancel():
            if state["worker"] is not None:
                state["worker"].cancel.set()

        def cleanup():
            folder = state["folder"]
            if folder is not None:   # import_images copied the pictures; a still-running worker only writes here.
                shutil.rmtree(folder, ignore_errors=True)

        return LibraryPending(poll, cancel=cancel, cleanup=cleanup, status_text="Searching the Angelo Library",
                              activity_note="Searching the Angelo Library; nothing changes.", timeout=SEARCH_SECONDS)

    def _attach_previews(self, result, worker):
        if worker.error is not None or not worker.value:
            return dict(result, preview_note="The thumbnails couldn't be downloaded; the results stand.")
        found = worker.value
        views = []
        try:
            for start in range(0, len(found), IMAGES_PER_IMPORT):
                views += self.controller.references.import_images([str(path) for _id, path in
                                                                   found[start:start + IMAGES_PER_IMPORT]])
        except (ValueError, OSError):
            return dict(result, preview_note="The thumbnails couldn't be added to the chat; the results stand.")
        # The pictures, in order, show these items (the first results that have a thumbnail).
        return dict(result, views=views, previews=[identity for identity, _path in found])

    def _import(self, args):
        if args['item_id'].startswith('fab:'):
            from . import fab_library_agent
            return fab_library_agent.use(self, args)
        if args['item_id'].startswith('blender:'):
            from . import blender_assets_agent
            return blender_assets_agent.use(self, args)
        lib, item = self._item(args)
        target_ids = [o.session_uid for o in self._targets(item, args)]
        self._require_view()
        held_scene = live_target.scene_ref(self.window.scene)   # Read when the download ends: deleted meanwhile, None.
        point, used = args.get("location"), library_catalog.files_for(item, args.get("resolution"))[1]
        stop = threading.Event()   # Stop on the call stops its download too.
        future = lib.fetch(item, args.get("resolution"), cancel=stop)

        def poll():
            if not future.done():
                return None
            if future.cancelled():
                return _failure("The download was stopped; nothing changed.", "CANCELLED")
            error = future.exception()
            if error is not None:
                message, code = library_host.describe(error)
                return _failure(message, code)
            try:
                return self._finish_import(held_scene(), item, future.result(), point, target_ids, used)
            except Exception as failure:  # noqa: BLE001 - library_import rolls itself back before raising.
                return _finish_failure(failure)

        # cleanup runs once, after the import's result or a stop: the cache may trim the item again.
        return LibraryPending(poll, status_text="Importing from the Angelo Library", timeout=IMPORT_SECONDS, cancel=stop.set,
                              cleanup=future.lease.release,
                              activity_note="Downloading from the Angelo Library; the files are checked before "
                                            "Blender reads them.")

    @staticmethod
    def _finish_import(scene, item, facts, point, target_ids, used):
        import bpy
        from . import library_import
        from .blender_tools import scene_diff, scene_signature
        window = _window_for(scene) if scene is not None else None
        if window is None:
            raise NotImported("The scene's window closed (or another file was opened); nothing was imported.")
        by_id = {o.session_uid: o for o in scene.objects}
        targets = [by_id[i] for i in target_ids if i in by_id]
        if len(targets) != len(target_ids):
            raise NotImported("An object chosen for this material was deleted meanwhile; nothing was imported.")
        area = next(a for a in live_windows.areas(window) if a.type == 'VIEW_3D')
        region = next(r for r in area.regions if r.type == 'WINDOW')
        before = scene_signature(scene)
        with bpy.context.temp_override(window=window, area=area, region=region):
            # Never set_view: the agent doesn't change the person's view (library_import.import_item).
            result = library_import.import_item(bpy.context, item, facts, point=point, targets=targets)
        result["changes"] = scene_diff(before, scene_signature(scene))
        if used:
            result["resolution"] = used
        return result


def service_for(controller, window):
    return LibraryService(controller, window)

