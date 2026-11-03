# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Writing a linked asset (.glb, .fbx) back from the saved working .blend, in a background Angelo. No ``bpy``; a
thread waits.

Blender's glTF exporter changes the scene's frame (to frame 0, and through the frame range to sample animations),
which puts animated values back to their keys: in the person's session that would undo a pose they haven't keyed.
So the write-back runs as an engine's own .blend import does (Godot's), in a background Angelo reading the file
just saved
(``--background --factory-startup <file>``), and the person's session is never touched. The child writes to a
hidden temporary file beside the asset and replaces it in one step, keeping the asset's permissions, so a failure
leaves the last good file. The exporter (``gltf`` or ``fbx``) and its options are the engine adapter's ``durable``.
The child also records the new file's digest (``stamp``, ``engine_link_assets.write_stamp``'s format): the file is
replaced whatever became of the session that asked, which may end, or quit, while it runs, and a next link must find
its working .blend current and never re-import over it.
A glTF write-back gives the objects ``export_baked.plan`` found current their baked material first (``baked``,
``[(object, material)]``), on their own copy of a shared mesh, in the child's own copy of the file only. One write-back runs at a time; the newest
save during one runs once it ends.
"""

import json
import os
import queue
import subprocess
import threading

from . import thread_start

TIMEOUT_SECONDS = 600

CHILD = r"""
import hashlib, json, os, sys, tempfile
import addon_utils, bpy
stamp, target, exporter = json.loads(sys.argv[-5]), sys.argv[-4], sys.argv[-3]
options, baked = json.loads(sys.argv[-2]), json.loads(sys.argv[-1])
folder = os.path.dirname(target)
descriptor, scratch = tempfile.mkstemp(prefix=".angelo-link-", suffix=os.path.splitext(target)[1], dir=folder)
os.close(descriptor)
try:
    if exporter == "fbx":
        addon_utils.enable("io_scene_fbx", default_set=False, persistent=False)
        if "object_types" in options:
            options["object_types"] = set(options["object_types"])
        result = bpy.ops.export_scene.fbx(filepath=scratch, check_existing=False, use_selection=False, **options)
    else:
        addon_utils.enable("io_scene_gltf2", default_set=False, persistent=False)
        for object_name, material_name in baked:   # export_baked.plan's "used": this copy only, never saved.
            obj, material = bpy.data.objects.get(object_name), bpy.data.materials.get(material_name)
            if obj is not None and material is not None and obj.library is None:
                if obj.data.users > 1:   # Its own mesh, so an object sharing it keeps its materials.
                    obj.data = obj.data.copy()
                if not obj.material_slots:   # As the web export writes it (export_baked.substitute).
                    obj.data.materials.append(material)
                for slot in obj.material_slots:   # On the mesh: the exporter reads a modified mesh's materials.
                    slot.link, slot.material = 'DATA', material
        result = bpy.ops.export_scene.gltf(filepath=scratch, check_existing=False, export_format="GLB",
                                           will_save_settings=False, use_active_scene=True, use_selection=False,
                                           **options)
    if "FINISHED" not in result or not os.path.getsize(scratch):
        raise RuntimeError("Blender's %s exporter didn't write the file" % exporter)
    if os.path.exists(target):
        os.chmod(scratch, os.stat(target).st_mode & 0o7777)
    else:
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(scratch, 0o666 & ~mask)
    with open(scratch, "rb") as handle:   # Before the replace: the stamp follows it at once, and names these bytes.
        sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    os.replace(scratch, target)
except BaseException:
    if os.path.exists(scratch):
        os.remove(scratch)
    raise
if stamp:
    temporary = "%s.%d.tmp" % (stamp["path"], os.getpid())
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(temporary, flags, 0o644), "w", encoding="utf-8") as handle:
            json.dump({"sha256": sha256, "asset": stamp["asset"]}, handle)
        os.replace(temporary, stamp["path"])
    except OSError:   # The asset is written; a session still there stamps it again (engine_link_saves.written).
        if os.path.exists(temporary):
            os.remove(temporary)
"""


class WriteBack:
    """Background write-backs of linked assets. ``request`` from the main thread; ``finished`` reports them there."""

    def __init__(self, binary):
        self._binary = binary
        self._lock = threading.Lock()
        self._running = None      # The caller's key of the write-back running (engine_link: its save).
        self._again = None        # (key, blend, target, exporter, options, baked, stamp): the newest during one.
        self._done = queue.Queue()

    def request(self, key, blend, target, exporter, options, baked=(), stamp=None):
        """Write ``target`` from the saved ``blend`` with ``exporter`` ("gltf", "fbx") and its ``options``, glTF with
        the ``baked`` ``[(object, material)]`` assigned first, and ``stamp`` ({"path", "asset"}) after; one write-back
        at a time."""
        baked = [list(pair) for pair in baked]
        with self._lock:
            if self._running is not None:
                self._again = (key, blend, target, exporter, options, baked, stamp)
                return
            self._running = key
        try:
            thread_start.start(threading.Thread(target=self._run, args=(key, blend, target, exporter, options, baked,
                                                                        stamp),
                                                name="Angelo engine link write-back", daemon=True), self._idle)
        except Exception as failure:   # noqa: BLE001 - this save ends unwritten, said (``finished``); the next starts.
            self._done.put((key, type(failure).__name__))

    def _idle(self):
        with self._lock:
            self._running = None

    def busy(self):
        with self._lock:
            return self._running is not None

    def finished(self):
        """[(key, error or None)] completed since the last call."""
        items = []
        while True:
            try:
                items.append(self._done.get_nowait())
            except queue.Empty:
                return items

    def _run(self, key, blend, target, exporter, options, baked=(), stamp=None):
        error = None
        command = [self._binary, "--background", "--factory-startup", blend, "--python-exit-code", "1",
                   "--python-expr", CHILD, "--", json.dumps(stamp), target, exporter, json.dumps(options),
                   json.dumps(list(baked))]
        try:
            completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       timeout=TIMEOUT_SECONDS, env=_child_environment())
            if completed.returncode != 0:
                error = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["failed"]
                error = error[0][:4096]   # Whole, so engine_link_facts.plain sees any path in it before a cut.
        except (OSError, subprocess.SubprocessError) as failure:
            error = type(failure).__name__
        with self._lock:   # Before its end is reported: ``busy`` then means a newer save, never this one.
            again, self._again = self._again, None
            self._running = again[0] if again is not None else None
        self._done.put((key, error))
        if again is not None:
            self._run(*again)


def _child_environment():
    """The child writes nothing of Angelo's own: no engine link, no update checks, no chat."""
    environ = dict(os.environ)
    environ.update(ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off")
    environ.pop("ANGELO_LINK_RECORD", None)
    return environ
