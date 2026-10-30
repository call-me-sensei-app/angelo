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
One write-back runs at a time; the newest save during one runs once it ends.
"""

import json
import os
import queue
import subprocess
import threading

TIMEOUT_SECONDS = 600

CHILD = r"""
import json, os, sys, tempfile
import addon_utils, bpy
target, exporter, options = sys.argv[-3], sys.argv[-2], json.loads(sys.argv[-1])
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
    os.replace(scratch, target)
except BaseException:
    if os.path.exists(scratch):
        os.remove(scratch)
    raise
"""


class WriteBack:
    """Background write-backs of linked assets. ``request`` from the main thread; ``finished`` reports them there."""

    def __init__(self, binary):
        self._binary = binary
        self._lock = threading.Lock()
        self._running = None      # The key (target path) being written.
        self._again = None        # (key, blend, target, exporter, options): the newest request made while one ran.
        self._done = queue.Queue()

    def request(self, key, blend, target, exporter, options):
        """Write ``target`` from the saved ``blend`` with ``exporter`` ("gltf", "fbx") and its ``options``; one
        write-back at a time."""
        with self._lock:
            if self._running is not None:
                self._again = (key, blend, target, exporter, options)
                return
            self._running = key
        threading.Thread(target=self._run, args=(key, blend, target, exporter, options),
                         name="Angelo engine link write-back", daemon=True).start()

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

    def _run(self, key, blend, target, exporter, options):
        error = None
        command = [self._binary, "--background", "--factory-startup", blend, "--python-exit-code", "1",
                   "--python-expr", CHILD, "--", target, exporter, json.dumps(options)]
        try:
            completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       timeout=TIMEOUT_SECONDS, env=_child_environment())
            if completed.returncode != 0:
                error = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or ["failed"]
                error = error[0][:300]
        except (OSError, subprocess.SubprocessError) as failure:
            error = type(failure).__name__
        self._done.put((key, error))
        with self._lock:
            again, self._again = self._again, None
            self._running = again[0] if again is not None else None
        if again is not None:
            self._run(*again)


def _child_environment():
    """The child writes nothing of Angelo's own: no engine link, no update checks, no chat."""
    environ = dict(os.environ)
    environ.update(ANGELO_DISABLE_INTRO="1", ANGELO_AGENT_RUNTIME="off")
    environ.pop("ANGELO_LINK_RECORD", None)
    return environ
