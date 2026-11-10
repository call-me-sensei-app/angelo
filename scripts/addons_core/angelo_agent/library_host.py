# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library in the running app: its catalog, thumbnails and imports (doc/angelo/library.rst).

One ``Library`` per process. Network and file work runs on worker threads (``library_fetch``, ``library_files``);
results reach the main thread through one timer (``_pump``), which also turns downloaded thumbnails into GPU
textures under a time budget (``THUMB_BUDGET`` per tick), so the editor keeps its frame rate while a grid of
thumbnails streams in. Draw callbacks only read: ``texture(item)`` answers what is ready and queues the rest.

* **Catalog**: the cached index loads at first use (a worker reads and checks it), then ``latest.json`` is asked once
  per session and every ``REFRESH_SECONDS``; a new version replaces the catalog only after it has been checked.
* **Imports**: ``fetch(item, resolution, done)`` downloads, verifies and checks on a worker and calls ``done(facts,
  error)`` on the main thread; the caller imports (``library_import``). ``prefetch`` starts the same download early
  (a drag's first move), so a drop on a warm cache imports at once.

bpy is imported lazily; the state works without it for tests.
"""

import collections
import concurrent.futures
import queue
import threading
import time
from pathlib import Path

from . import fab_host, library_catalog, library_fetch
from .team_library_files import Refused

REFRESH_SECONDS = 6 * 3600
THUMB_BUDGET = 0.004      # Seconds of thumbnail loading per timer tick (a 60 Hz frame is 16.7 ms).
THUMB_SIDE = 256          # Thumbnail texels a side at most: a tile draws 112 points, 224 pixels on a 2x display.
TEXEL_BYTES = 8           # RGBA16F, the add-on's picture format.
THUMB_GPU_BYTES = 160 * 1024 * 1024   # GPU thumbnails kept, least recently drawn out first (320 at 256 x 256).
THUMB_ENTRIES = 4000      # Entries kept, unavailable ones (no texture) included.
THUMB_WORKERS = 6
FILE_WORKERS = 4
TICK = 0.02


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.catalog = None
        self.status = "idle"         # idle, loading, ready, offline, failed
        self.error = ""
        self.refreshed = 0.0
        self.listeners = set()       # Callables run on the main thread when the catalog or a thumbnail changes.
        self._results = queue.Queue()
        self.stopping = threading.Event()   # Set at shutdown: every running download stops between chunks.
        self._thumb_pool = DaemonPool(THUMB_WORKERS, "angelo-library-thumb")
        self._file_pool = DaemonPool(FILE_WORKERS, "angelo-library-file")
        self._thumb_files = {}       # url -> Path (downloaded) or None (unavailable)
        self._thumb_wanted = set()   # urls being downloaded
        self._textures = collections.OrderedDict()   # url -> GPUTexture or False, least recently drawn first
        self._texture_bytes = 0
        self._pending_loads = collections.deque()
        self._fetches = {}           # item cache folder (its id and files) -> (Transfer, shared Future)
        self._loading = False

    # -- catalog -----------------------------------------------------------------------------------------------
    def ensure_catalog(self, refresh=False):
        """Start loading (or refreshing) the catalog on a worker; returns at once."""
        due = refresh or time.time() - self.refreshed > REFRESH_SECONDS
        if self._loading or (self.catalog is not None and not due):
            return
        self._loading = True
        if self.catalog is None:
            self.status = "loading"
        _wake()
        try:
            threading.Thread(target=self._load, name="angelo-library-index", daemon=True).start()
        except RuntimeError as error:   # No thread to load on: as a refresh that failed, and the next ask retries.
            self._loading = False
            self._results.put(("refresh_failed", "The library couldn't be read (%s)." % type(error).__name__))

    def _load(self):
        folder = self.root / "index"
        if self.catalog is None:
            document = library_fetch.read_local(folder)
            if document is not None:
                self._offer(document)
        try:
            current = self.catalog.version if self.catalog is not None else None
            document = library_fetch.refresh(folder, current)
            if document is not None:
                self._offer(document)
            self._results.put(("refreshed", None))
        except library_fetch.FetchError as error:
            self._results.put(("refresh_failed", str(error)))
        except Exception as error:   # noqa: BLE001 (any failure leaves the library usable and a retry possible)
            self._results.put(("refresh_failed", "The library couldn't be read (%s)." % type(error).__name__))
        finally:
            self._loading = False

    def _offer(self, document):
        try:
            version, items, dropped = library_catalog.parse(document, library_fetch.loopback_ok())
        except Exception as error:   # noqa: BLE001 (an unusable index, whatever is wrong with it, is reported)
            self._results.put(("refresh_failed", str(error) if isinstance(error, library_catalog.IndexRefused)
                               else "The library index couldn't be read."))
            return
        self._results.put(("catalog", (library_catalog.Catalog(version, items), dropped)))

    def search(self, query, kind=None, limit=60, offset=0, category=None):
        if self.catalog is None:
            return [], 0
        return self.catalog.search(query, kind, limit, offset, category)

    def get(self, identity):
        return self.catalog.get(identity) if self.catalog is not None else None

    # -- thumbnails --------------------------------------------------------------------------------------------
    def texture(self, item):
        """The item's thumbnail as a GPU texture, or None (queued, unavailable, or no thumbnail)."""
        url = item.get("thumb")
        if not url:
            return None
        texture = self._textures.get(url)
        if texture is not None:
            self._textures.move_to_end(url)
            return texture or None
        if url in self._thumb_files:
            if self._thumb_files[url] is None:
                self._keep_texture(url, False)
            elif url not in self._pending_loads:
                self._pending_loads.append(url)
                _wake()
            return None
        if url not in self._thumb_wanted:
            self._thumb_wanted.add(url)
            _wake()
            self._thumb_pool.submit(self._thumb_job, url)
        return None

    def _thumb_job(self, url):
        from .fab_catalog import THUMB_HOST, thumb_url
        hosts = frozenset({THUMB_HOST}) if thumb_url(url) else library_fetch.catalog.FILE_HOSTS
        self._results.put(("thumb", (url, library_fetch.thumbnail(self.root, url, hosts))))

    def _load_thumbnails(self, deadline):
        import bpy
        import gpu
        loaded = False
        while self._pending_loads and time.perf_counter() < deadline:
            url = self._pending_loads.popleft()
            if url in self._textures:
                continue
            self._keep_texture(url, _gpu_thumbnail(bpy, gpu, self._thumb_files[url]) or False)
            loaded = True
        return loaded

    def _keep_texture(self, url, texture):
        """Keep ``texture`` and evict the least recently drawn ones beyond ``THUMB_GPU_BYTES``."""
        self._textures[url] = texture
        self._texture_bytes += gpu_bytes(texture)
        while len(self._textures) > 1 and (self._texture_bytes > THUMB_GPU_BYTES or len(self._textures) > THUMB_ENTRIES):
            _url, evicted = self._textures.popitem(last=False)
            self._texture_bytes -= gpu_bytes(evicted)

    # -- item files --------------------------------------------------------------------------------------------
    def fetch(self, item, resolution=None, done=None, progress=None, cancel=None):
        """Download, verify and check the item's files on a worker; ``done(facts, error)`` on the main thread.

        Concurrent calls that want the same files (the item's cache folder: a digest of its id and its files'
        names, checksums and sizes, so a changed item never joins its old version's download) share one download;
        each keeps its own ``cancel`` (it ends that call's result only; the download stops once no call wants it),
        ``progress`` and result (``Transfer``). Each call returns its own future holding a ``library_fetch.Lease``
        (``future.lease``) on that folder, where its files are, from now until its import ends, so another
        download's trim never deletes the files in between: ``done``'s lease ends when ``done`` returns; a caller
        of the future releases ``future.lease`` once it has imported (a lease nobody releases ends when the future
        is collected)."""
        files, _used = library_catalog.files_for(item, resolution)
        folder = library_fetch.item_folder(self.root, item, files)
        mine = concurrent.futures.Future()
        mine.lease = library_fetch.Lease(folder)
        running = self._fetches.get(str(folder))
        if running is None or not running[0].join(mine, cancel, progress):   # A finished one: check the cache anew.
            transfer = Transfer(self.stopping)
            transfer.join(mine, cancel, progress)
            shared = self._file_pool.submit(self._fetch_job, item, files, transfer)
            shared.add_done_callback(transfer.finish)
            self._fetches[str(folder)] = (transfer, shared)
        _wake()
        if done is not None:
            mine.add_done_callback(lambda f: self._results.put(("fetched", (done, f))))
        return mine

    def prefetch(self, item, resolution=None):
        if library_catalog.item_bytes(item, resolution) <= 256 * 1024 * 1024:
            self.fetch(item, resolution).add_done_callback(lambda f: f.lease.release())

    def _fetch_job(self, item, files, transfer):
        from . import library_files
        try:
            paths = library_fetch.fetch_files(self.root, item, files, cancel=transfer, progress=transfer.progress)
            return library_files.check_item(item, paths)
        finally:
            transfer.close()   # Before the outcome is published: a request from now on starts a new download.

    def cached(self, item, resolution=None):
        return library_fetch.cached(self.root, item, library_catalog.files_for(item, resolution)[0])

    # -- the main-thread pump ----------------------------------------------------------------------------------
    def pump(self):
        changed = False
        while True:
            try:
                kind, value = self._results.get_nowait()
            except queue.Empty:
                break
            if kind == "catalog":
                self.catalog, _dropped = value
                self.status, self.error = "ready", ""
                changed = True
            elif kind == "refreshed":
                self.refreshed = time.time()
            elif kind == "refresh_failed":
                self.error = value
                if self.catalog is None:
                    self.status = "offline"
                changed = True
            elif kind == "thumb":
                url, path = value
                self._thumb_wanted.discard(url)
                self._thumb_files[url] = path
                if path is not None:
                    self._pending_loads.append(url)
            elif kind == "fetched":
                done, future = value
                error = None if future.cancelled() else future.exception()
                try:
                    done(None if error or future.cancelled() else future.result(),
                         error or (concurrent.futures.CancelledError() if future.cancelled() else None))
                except Exception as failure:   # A caller's failure never stops the pump.
                    print("Angelo Library:", failure)
                finally:
                    future.lease.release()   # The import (or its refusal) has run.
        for key in [k for k, (_transfer, f) in self._fetches.items() if f.done()]:
            del self._fetches[key]
        if self._pending_loads and self._load_thumbnails(time.perf_counter() + THUMB_BUDGET):
            changed = True
        if changed:
            for listener in list(self.listeners):
                try:
                    listener()
                except Exception as failure:
                    print("Angelo Library listener:", failure)

    def busy(self):
        """Whether the pump still has work coming (its timer stops when idle: no polling)."""
        return (self._loading or bool(self._thumb_wanted) or bool(self._pending_loads) or not self._results.empty()
                or any(not f.done() for _transfer, f in self._fetches.values()))

    def shutdown(self):
        self.stopping.set()
        self._thumb_pool.shutdown()
        self._file_pool.shutdown()
        self._textures.clear()
        self._texture_bytes = 0


def gpu_bytes(texture):
    """GPU memory of a kept thumbnail texture (0 for an unavailable one)."""
    return texture.width * texture.height * TEXEL_BYTES if texture else 0


def _stopped(future):
    if not future.done():
        future.set_exception(library_fetch.FetchError("The download was stopped.", "CANCELLED"))


class Transfer:
    """One download shared by every call that wants the same files. Each call keeps its own stop, progress and
    result; the download itself stops (``is_set``, read between chunks) at the library's shutdown or once every
    call has stopped. Thread-safe: calls join on the main thread, the download runs on a worker.

    A transfer takes calls only while its download can still serve them: the decision to stop closes it under the
    same lock, and so does the download's end (``close``, before its outcome is published), so a later call never
    inherits a stop or failure from before it asked; it starts a new download instead."""

    def __init__(self, stopping):
        self._stopping, self._lock = stopping, threading.Lock()
        self._calls, self._finished = [], False   # [future, stop event or None, progress or None]

    def join(self, future, stop=None, progress=None):
        """Add a call; False once the download has ended (the caller starts a new one)."""
        with self._lock:
            if self._finished:
                return False
            self._calls.append((future, stop, progress))
            return True

    def is_set(self):
        """The download's own stop: each stopped call's result ends here; True (and closed) once no call wants the
        files, or at the library's shutdown."""
        with self._lock:
            stopped = [call for call in self._calls if call[1] is not None and call[1].is_set()]
            self._calls = [call for call in self._calls if call not in stopped]
            stopping = self._stopping.is_set() or not self._calls
            if stopping:
                self._finished = True
        for future, _stop, _progress in stopped:
            _stopped(future)
        return stopping

    def close(self):
        with self._lock:
            self._finished = True

    def progress(self, done, total):
        with self._lock:
            listeners = [progress for _future, _stop, progress in self._calls if progress is not None]
        for listener in listeners:
            listener(done, total)

    def finish(self, shared):
        """Each remaining call takes the download's outcome (a call stopped meanwhile ends as stopped); the
        transfer then holds no call's future."""
        with self._lock:
            self._finished, calls, self._calls = True, self._calls, []
        for future, stop, _progress in calls:
            if future.done():
                continue
            if (stop is not None and stop.is_set()) or shared.cancelled():
                _stopped(future)
            elif shared.exception() is not None:
                future.set_exception(shared.exception())
            else:
                future.set_result(shared.result())


class DaemonPool:
    """A few daemon worker threads: unlike ``ThreadPoolExecutor``'s, the interpreter never waits for them at exit, so
    quitting Blender never waits on a library download."""

    def __init__(self, workers, name):
        self._jobs = queue.Queue()
        for index in range(workers):
            threading.Thread(target=self._work, name="%s-%d" % (name, index), daemon=True).start()

    def submit(self, function, *args):
        future = concurrent.futures.Future()
        self._jobs.put((future, function, args))
        return future

    def _work(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            future, function, args = job
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(function(*args))
            except BaseException as error:   # noqa: BLE001 (the caller reads it from the future)
                future.set_exception(error)

    def shutdown(self):
        while True:
            try:
                future, _function, _args = self._jobs.get_nowait()
            except (queue.Empty, TypeError):
                break
            future.cancel()
        for _ in range(16):
            self._jobs.put(None)


def _gpu_thumbnail(bpy, gpu, path):
    """A GPU texture of the picture at ``path`` (at most ``THUMB_SIDE`` pixels a side), with no image left in the
    file."""
    image = None
    try:
        image = bpy.data.images.load(str(path), check_existing=False)
        image.colorspace_settings.name = 'Non-Color'   # The stored values, drawn as they are (draw_texture_2d).
        width, height = image.size
        if not (1 <= width <= 4096 and 1 <= height <= 4096):
            return None
        if max(width, height) > THUMB_SIDE:
            factor = THUMB_SIDE / max(width, height)
            image.scale(max(1, int(width * factor)), max(1, int(height * factor)))
            width, height = image.size
        pixels = gpu.types.Buffer('FLOAT', width * height * 4)
        image.pixels.foreach_get(pixels)
        return gpu.types.GPUTexture((width, height), format='RGBA16F', data=pixels)
    except (RuntimeError, ValueError, OSError):
        return None
    finally:
        if image is not None:
            try:
                bpy.data.images.remove(image)
            except (ReferenceError, RuntimeError):
                pass


def describe(error):
    """A person-facing sentence for a failed fetch or check, and its closed code."""
    if isinstance(error, library_fetch.FetchError):
        return str(error), error.refusal_code
    if isinstance(error, Refused):
        return str(error), "UNSUPPORTED"
    if isinstance(error, OSError):
        return "The library's cache folder couldn't be written (%s)." % (error.strerror or "disk error"), "EXPIRED"
    return "The library item couldn't be prepared.", None


_library = None


def library():
    """The process's library (its folder under Angelo's state folder)."""
    global _library
    if _library is None:
        from . import ui
        _library = Library(Path(ui.state_directory()) / "library")
    return _library


def _wake():
    """Run the pump until the work queued now is done (main thread only)."""
    try:
        import bpy
    except ImportError:
        return   # Host tests drive ``pump`` themselves.
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=TICK, persistent=True)


_spun = [None]


def _spin_step():   # True once per tenth of a second: the spinner's next dot.
    changed, _spun[0] = int(time.monotonic() * 10) != _spun[0], int(time.monotonic() * 10)
    return changed


def _tick():
    from . import fab_ui
    # The spinner's next dot at 10 Hz, only while Fab works (no snapshot copy on an idle tick).
    spin = (fab_host.busy() or bool(fab_ui._PENDING) or bool(fab_ui._AUTO)) and _spin_step()
    if fab_host.pump() | fab_ui.resume_pending() | spin:
        from . import library_overlay, preferences_ui
        library_overlay.redraw_all()
        preferences_ui.redraw()
    if _library is not None:
        _library.pump()
    return TICK if fab_host.needs_pump() or (_library is not None and _library.busy()) else None


def unregister():
    global _library
    import bpy
    from . import blender_assets
    try:
        fab_host.unregister()  # Exact browser/Session owner must finish before its module can be discarded.
    except RuntimeError as error:   # Its owner record stays for the next launch's recovery; the rest still unregisters.
        print("Angelo: Fab cleanup at shutdown:", error)
    blender_assets.reset()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if _library is not None:
        _library.shutdown()
    _library = None


def fab_snapshot():
    return fab_host.snapshot()


def _fab(method, *args, **kwargs):
    result = fab_host.human(method, *args, **kwargs)
    _wake()
    return result


def fab_connect(window):
    return _fab('connect')


def fab_disconnect(window):
    return _fab('disconnect')


def fab_search(window, query, *, limit=8):
    return _fab('search', query, limit)


def fab_lookup(window, item_id, show=False):
    return _fab('lookup', item_id, show)


def fab_select(window, item_id, variant_id, *, expected_generation, expected_request_id):
    return _fab('select', item_id, variant_id, expected_generation=expected_generation,
                expected_request_id=expected_request_id) is True


def fab_use(window, area, region, item_id, variant_id, *, expected_generation, expected_request_id, at=None):
    return _fab('use', window, area, region, item_id, variant_id, expected_generation=expected_generation,
                expected_request_id=expected_request_id, at=at)
