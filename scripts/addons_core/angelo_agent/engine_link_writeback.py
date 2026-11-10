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
The child also records the new file's digest (``stamp``, ``engine_link_assets.write_stamp``'s format, with the
save, link and process it wrote for): the file is replaced whatever became of the session that asked, which may end,
or quit, while it runs, and a next link must find its working .blend current and never re-import over it. The digest
is of the bytes the child wrote, taken before its replace, and the session reads it from there (``written_stamp``),
never digesting the asset again: a write to the asset after the replace would otherwise be stamped as Angelo's own.
A glTF write-back gives the objects ``export_baked.plan`` found current their baked material first (``baked``,
``[(object, material)]``), on their own copy of a shared or linked mesh (a linked object stays linked), in the child's
own copy of the file only, so the file carries what the live export writes and its read-back expects. One write-back runs at a time; the newest
save during one runs once it ends.
The child's arguments go in a private file (``child_spec``), never on its command line: ``baked`` grows with the
scene, and Windows refuses a command line past 32,767 characters.
"""

import contextlib
import json
import os
import queue
import subprocess
import threading

from . import thread_start

TIMEOUT_SECONDS = 600
# The child's refusal when the .blend it read is not the save that asked for it (``saved``): the file was written
# again (a newer save, or another program) between the save and the child's read. Nothing is written.
SUPERSEDED = "the working file changed after this save"

CHILD = r"""
import hashlib, json, os, sys, tempfile
import addon_utils, bpy
from angelo_agent import shared_replace   # Every add-on replace (the engine re-imports the asset as it changes).
with open(sys.argv[-1], encoding="utf-8") as handle:
    spec = json.load(handle)
stamp, target, exporter, options, baked = (spec[name] for name in ("stamp", "target", "exporter", "options", "baked"))
blend = spec["blend"]


def seen():
    try:
        info = os.stat(blend)
    except OSError:
        return None
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns]


def read():   # The file Blender's load read, identified on the descriptor it read through; else a stat after it.
    try:
        from _bpy import _angelo_saved
        found = _angelo_saved.read_file()
    except Exception:
        found = None
    if found is None or os.path.normcase(os.path.abspath(found[0])) != os.path.normcase(os.path.abspath(blend)):
        return seen()
    return list(found[1:5])


# The file read must be the save that asked (its stat then), else nothing is written: the file the load read itself,
# so a version put at the path and moved away again around the read (ABA) is never taken for it.
if spec.get("saved") is not None and seen() != spec["saved"]:
    raise RuntimeError(spec["superseded"])
if "FINISHED" not in bpy.ops.wm.open_mainfile(filepath=blend, load_ui=False, use_scripts=False):
    raise RuntimeError("Blender couldn't read the saved file")
if spec.get("saved") is not None and read() != spec["saved"]:
    raise RuntimeError(spec["superseded"])
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
        for item in baked:   # export_baked.plan's "used": this copy only, never saved.
            # By (name, library) when given (engine_link_assets.baked_for_save): a linked object or material may share
            # a local one's name.
            keys = (item[0], item[1]) if len(item) == 2 else ((item[0], item[2]), (item[1], item[3]))
            obj, material = bpy.data.objects.get(keys[0]), bpy.data.materials.get(keys[1])
            if obj is not None and material is not None:
                # A linked object stays linked (made local, its linked children and users would lose it), so its
                # parents, constraints and collections hold; this copy's own writes reach it all the same.
                data = obj.data
                if data.library is not None or data.override_library is not None or data.users > 1:
                    obj.data = data.copy()   # Its own mesh: an object sharing it keeps its materials, a linked one too.
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
    shared_replace.replace(scratch, target)   # Held by the engine's import: tried again, else exit 1, said.
except BaseException:
    if os.path.exists(scratch):
        os.remove(scratch)
    raise
if stamp:
    temporary = "%s.%d.tmp" % (stamp["path"], os.getpid())
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(temporary, flags, 0o644), "w", encoding="utf-8") as handle:
            json.dump({"sha256": sha256, "asset": stamp["asset"], "save": stamp.get("save"),
                       "link": stamp.get("link"), "writer": stamp.get("writer")}, handle)
        shared_replace.replace(temporary, stamp["path"])
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

    def request(self, key, blend, target, exporter, options, baked=(), stamp=None, saved=None):
        """Write ``target`` from the saved ``blend`` with ``exporter`` ("gltf", "fbx") and its ``options``, glTF with
        the ``baked`` ``[(object, material)]`` assigned first, and ``stamp`` ({"path", "asset"}) after; one write-back
        at a time. ``saved``: the .blend's stat at the save, settled here off the UI thread before the child reads the
        file (``_expected``); the child writes nothing (``SUPERSEDED``) when the file it read is another."""
        baked = [list(pair) for pair in baked]
        with self._lock:
            if self._running is not None:
                self._again = (key, blend, target, exporter, options, baked, stamp, saved)
                return
            self._running = key
        try:
            thread_start.start(threading.Thread(target=self._run, args=(key, blend, target, exporter, options, baked,
                                                                        stamp, saved),
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

    def _run(self, key, blend, target, exporter, options, baked=(), stamp=None, saved=None):
        error = None
        try:
            with child_spec({"stamp": stamp, "target": target, "exporter": exporter, "options": options,
                             "baked": list(baked), "saved": _expected(saved), "superseded": SUPERSEDED,
                             "blend": blend}) as spec:
                # The child opens the .blend itself, between two stats of it (``CHILD``), never from its arguments.
                command = [self._binary, "--background", "--factory-startup", "--python-exit-code", "1",
                           "--python-expr", CHILD, "--", spec]
                completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                           timeout=TIMEOUT_SECONDS, env=_child_environment())
            if completed.returncode != 0:
                text = (completed.stderr or b"").decode("utf-8", "replace")
                error = text.strip().splitlines()[-1:] or ["failed"]
                error = error[0][:4096]   # Whole, so engine_link_facts.plain sees any path in it before a cut.
                if SUPERSEDED in text:   # Its own refusal, wherever Blender's exit lines put it.
                    error = SUPERSEDED
        except (OSError, subprocess.SubprocessError) as failure:
            error = type(failure).__name__
        with self._lock:   # Before its end is reported: ``busy`` then means a newer save, never this one.
            again, self._again = self._again, None
            self._running = again[0] if again is not None else None
        self._done.put((key, error))
        if again is not None:
            self._run(*again)


def _expected(saved):
    """``saved`` as the child compares it: [device, inode, size, modification time] of the stat taken at the save,
    once its times are past (``change_clock``: a write in their tick would share them), so any write after it shows;
    one no stat equals when that can't be told. A list (already one no stat equals) or None is as given."""
    if saved is None or isinstance(saved, list):
        return saved
    from . import change_clock
    try:
        change_clock.settle(saved, change=False)
    except change_clock.Unsettled:
        return list(change_clock.unique())
    return [saved.st_dev, saved.st_ino, saved.st_size, saved.st_mtime_ns]


@contextlib.contextmanager
def child_spec(values):
    """A background child's arguments as a JSON file in a private folder, removed when the child has ended; yields
    its path, the one argument the child reads. Lists like ``baked`` grow with the scene, and a command line has a
    limit (32,767 characters on Windows), so nothing unbounded goes there."""
    from . import private_roots
    # A folder Windows can't remove yet (an antivirus scan holding spec.json) is left to its family, private: the
    # child's outcome stands, never a write-back that worked reported as failed (1.0.10 break-it).
    with private_roots.TemporaryDirectory("angelo-engine-link-", ignore_cleanup_errors=True) as folder:
        path = os.path.join(folder, "spec.json")
        with open(path, "x", encoding="utf-8") as handle:
            json.dump(values, handle)
        yield path


def _child_environment():
    """The child writes nothing of Angelo's own: no engine link, no update checks, no chat."""
    environ = dict(os.environ)
    environ.update(ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off")
    environ.pop("ANGELO_LINK_RECORD", None)
    return environ
