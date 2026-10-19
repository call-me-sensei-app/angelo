# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded in-memory view images and REUSE cameras. No Blender dependency.

``view_capture`` owns reads and writes on Blender's main thread. ``ui`` clears
both stores on document load, Undo/Redo and unregister, and the depsgraph
handler drops a scene's images after any evaluated change. Image keys also
carry the document epoch and every target's exact revision, so an epoch
rotated elsewhere or an untagged target edit can never be served a stale image.
Only encoded PNG bytes and plain camera values are kept: no Blender data,
GPU resources or file paths.
"""

from collections import OrderedDict
import copy

MAX_IMAGES = 6
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_CAMERAS = 32


def image_key(*, scene_uid, epoch, view_layer, frame, viewport, targets, revisions,
              shading, size, camera):
    """Everything that selects these pixels, or None when they must not be reused.

    A target without an exact revision (unsupported or over-budget state) has
    no proof of freshness, so its captures are never cached. Whole-scene views
    have no targets and rely on depsgraph eviction plus the other key fields.
    """
    if len(targets) != len(revisions) or any(revision is None for revision in revisions):
        return None
    frozen_camera = tuple(float(value) for row in camera["view_matrix"] for value in row) + tuple(
        float(value) for row in camera["projection_matrix"] for value in row)
    return (scene_uid, epoch, view_layer, frame, tuple(viewport), tuple(targets), tuple(revisions),
            shading, tuple(size), frozen_camera)


class ViewImageCache:
    """LRU of at most ``limit`` PNG images and ``byte_limit`` encoded bytes."""

    def __init__(self, limit=MAX_IMAGES, byte_limit=MAX_IMAGE_BYTES):
        self.limit, self.byte_limit = limit, byte_limit
        self._entries = OrderedDict()
        self._bytes = 0
        self.counters = {"hits": 0, "misses": 0, "uncacheable": 0, "evictions": 0, "invalidations": 0}

    def __len__(self):
        return len(self._entries)

    def get(self, key):
        if key is None:
            self.counters["uncacheable"] += 1
            return None
        entry = self._entries.get(key)
        if entry is None:
            self.counters["misses"] += 1
            return None
        self.counters["hits"] += 1
        self._entries.move_to_end(key)
        return dict(entry, camera=copy.deepcopy(entry["camera"]))

    def put(self, key, png, width, height, camera):
        if key is None or not isinstance(png, bytes) or len(png) > self.byte_limit:
            return False
        self._remove(key)
        self._entries[key] = {"png": png, "width": width, "height": height, "camera": copy.deepcopy(camera)}
        self._bytes += len(png)
        while self._entries and (len(self._entries) > self.limit or self._bytes > self.byte_limit):
            self._remove(next(iter(self._entries)))
            self.counters["evictions"] += 1
        return True

    def drop_scene(self, scene_uid):
        stale = [key for key in self._entries if key[0] == scene_uid]
        for key in stale:
            self._remove(key)
        self.counters["invalidations"] += bool(stale)
        return len(stale)

    def clear(self):
        self.counters["invalidations"] += bool(self._entries)
        self._entries.clear()
        self._bytes = 0

    def stats(self):
        return dict(self.counters, entries=len(self._entries), bytes=self._bytes,
                    limit=self.limit, byte_limit=self.byte_limit)

    def _remove(self, key):
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= len(entry["png"])


class ViewCameraStore:
    """Runtime-only REUSE authority: the exact camera behind each view_id.

    These cameras never authorize edits or pixel targeting; sculpt targeting
    keeps its own depth-backed observations in the chat reference store.
    """

    def __init__(self, limit=MAX_CAMERAS):
        self.limit = limit
        self._cameras = OrderedDict()

    def __len__(self):
        return len(self._cameras)

    def remember(self, view_id, camera):
        self._cameras.pop(view_id, None)
        self._cameras[view_id] = copy.deepcopy(camera)
        while len(self._cameras) > self.limit:
            self._cameras.popitem(last=False)

    def resolve(self, view_id, *, epoch, scene_uid):
        camera = self._cameras.get(view_id)
        if camera is None:
            raise ValueError("This view_id is unknown or expired (document load, Undo/Redo, restart or "
                             "eviction); capture a new view to compare against")
        if camera["epoch"] != epoch or camera["scene_uid"] != scene_uid:
            raise ValueError("This view_id belongs to another document or scene; capture a new view")
        self._cameras.move_to_end(view_id)  # Keep an actively reused baseline alive.
        return copy.deepcopy(camera)

    def clear(self):
        self._cameras.clear()


images = ViewImageCache()
cameras = ViewCameraStore()


def clear():
    """Document load, Undo/Redo and unregister: release every image and camera."""
    images.clear()
    cameras.clear()


def note_scene_change(scene_uid):
    """Depsgraph update for this scene: its pixels may differ now."""
    return images.drop_scene(scene_uid)
