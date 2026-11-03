# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The engine preflight on the engine link's durable save, in a background Angelo. No ``bpy`` here; a thread waits.

After a save of a linked asset (``engine_link``), a background Angelo opens the file just saved, runs the preflight
for the asset's engine and reads the asset back (``engine_preflight.preflight_child``), and writes the report beside
the working file (``engine_preflight.report_path``). A .glb or .fbx is read back from the file the write-back
wrote; a .blend, which the engine converts itself, is first exported to a private temporary file with the engine's
own Blender profile (Godot's glTF options from its import settings, Unity's FBX script), which is removed after.

It starts only after the write-back finished, so the engine's re-import never waits for it, and it never changes
the scene or the asset: report-only. One runs at a time; the newest save during one runs once it ends, and the one
it replaces ends then (its folder removed): the newer report covers it.

Each report names the save it describes: ``save_key`` (the caller's key for the save, written as given; None until
the link numbers its saves), ``blend_identity`` (``export_snapshot.identity`` of the saved .blend when
the save was made, which tells every save apart, two at one version too), ``scene_version`` (the link's version the
save covered), for a .glb or .fbx asset ``target_sha256`` (the SHA-256 of the asset file it read back, as the
write-back wrote it), ``status`` ("done", or "failed" with ``error``) and ``saved_at`` (the wall time it was written:
the link's versions restart with each launch). The asset file is read from a private copy made when the write-back
finished (``export_snapshot``), so the next write-back over it never changes what this one compares; the saved .blend
is opened by the child, which writes nothing when that file is no longer the one saved (a newer save's preflight
follows).
"""

import json
import os
import shutil
import subprocess
import threading
import time

from . import thread_start

TIMEOUT_SECONDS = 600
CHILD = r"""
import json, sys
from angelo_agent import engine_preflight
engine_preflight.preflight_child(json.loads(sys.argv[-1]))
"""


class Runner:
    """Background preflights of linked assets. ``request`` from the main thread."""

    def __init__(self, binary):
        self._binary = binary
        self._lock = threading.Lock()
        self._running = False
        self._again = None
        self.last_error = None

    def request(self, blend, spec):
        """Preflight the saved ``blend`` with ``spec`` (``engine_preflight.preflight_child``); one at a time."""
        with self._lock:
            if self._running:
                dropped, self._again = self._again, (blend, spec)
                if dropped is not None and dropped[1].get("folder"):   # The newer save's report covers it.
                    shutil.rmtree(dropped[1]["folder"], ignore_errors=True)
                return
            self._running = True
        # One that can't start leaves the runner idle and its folder gone; the save reports why (``after_save``).
        thread_start.start(threading.Thread(target=self._run, args=(blend, spec), name="Angelo engine preflight",
                                            daemon=True), lambda: self._idle(spec))

    def _idle(self, spec):
        with self._lock:
            self._running = False
        if spec.get("folder"):
            shutil.rmtree(spec["folder"], ignore_errors=True)

    def busy(self):
        with self._lock:
            return self._running

    def _run(self, blend, spec):
        from .engine_link_writeback import _child_environment
        command = [self._binary, "--background", "--factory-startup", blend, "--python-exit-code", "1",
                   "--python-expr", CHILD, "--", json.dumps(spec)]
        try:
            completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                       timeout=TIMEOUT_SECONDS, env=_child_environment())
            lines = (completed.stderr or b"").decode("utf-8", "replace").strip().splitlines()
            self.last_error = (lines[-1][:300] if lines else "failed") if completed.returncode else None
        except Exception as failure:   # noqa: BLE001 - a preflight that can't start is recorded, never raised.
            self.last_error = type(failure).__name__
        finally:
            if spec.get("folder"):
                shutil.rmtree(spec["folder"], ignore_errors=True)
        if self.last_error is not None:   # The save still gets its report: that it couldn't be checked, and why.
            from .engine_preflight import write_report
            try:
                write_report(spec["report"], {"status": "failed", "scene_version": spec.get("scene_version"),
                                              "blend_identity": spec.get("blend_identity"),
                                              "save_key": spec.get("save_key"),
                                              "error": self.last_error, "saved_at": time.time()})
            except OSError:
                pass
        with self._lock:
            again, self._again = self._again, None
            self._running = again is not None
        if again is not None:
            self._run(*again)


_runner = None


def after_save(binary, asset, blend, written, durable, params, scene_version=None, save_key=None, baked=()):
    """Start the preflight of a save (``request``) with the Angelo at ``binary``, from the main thread; returns why it
    couldn't start, or None. The report's folder is the asset's working folder (``ensure_working_folder``)."""
    from . import engine_link_assets
    if not isinstance(binary, str) or not os.path.isfile(binary):
        return None   # No Angelo to start (a session without its binary): the save is reported without a preflight.
    try:   # Report-only: the save is written and its editors told, so a failure here is the save's error only.
        engine_link_assets.ensure_working_folder(asset)
        request(binary, asset, blend, written, durable, params, scene_version, save_key, baked)
    except OSError as failure:
        return "preflight: %s" % failure
    except Exception as failure:  # noqa: BLE001 - never into the link's timer, which ends other saves too.
        return "preflight: %s" % type(failure).__name__
    return None


def after_blend_save(state, binary, asset, blend, params, save_key=None):
    """``after_save`` for a .blend asset just saved (``engine_link_saves.saved``), at the link's current version; a
    reason it couldn't start goes to the link's ``state["error"]``."""
    state["error"] = after_save(binary, asset, blend, None, None, params, state["version"],
                                save_key) or state["error"]


def after_writeback(state, binary, asset, params, scene_version, save_key=None, baked=()):
    """``after_save`` for a save whose write-back just finished (``engine_link_saves.written``), unless a newer save's
    write-back runs (``state["writeback"]``): that one's preflight covers this save."""
    from . import engine_link_assets
    if state["writeback"].busy():
        return
    durable = engine_link_assets.adapter(asset.engine).durable(asset, params)
    state["error"] = after_save(binary, asset, asset.editable, asset.path, durable, params,
                                scene_version, save_key, baked) or state["error"]


def request(binary, asset, blend, written, durable, params, scene_version=None, save_key=None, baked=()):
    """Preflight ``asset`` after its save to ``blend`` (at the link's ``scene_version``): ``written`` is the file the
    write-back wrote (None for a .blend asset), copied now, and ``durable`` its (exporter, options); for a .blend the
    engine's own profile is used. ``baked``: the write-back's ``[(object, material)]``, which the file carries."""
    global _runner
    from . import engine_preflight, export_snapshot, private_roots
    if asset.engine not in engine_preflight.TARGET_FOR_ENGINE:
        return False
    if written is None:
        durable = engine_preflight.blend_import_profile(asset.engine, params)
        if durable is None:
            return False
    exporter, options = durable
    folder = private_roots.allocate("angelo-preflight-")
    try:
        copy = None
        if written is not None:
            copy = str(folder / ("asset" + os.path.splitext(written)[1]))
            export_snapshot.take(written, copy)
    except OSError:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if _runner is None:
        _runner = Runner(binary)
    _runner.request(blend, {"engine": asset.engine, "report": engine_preflight.report_path(asset),
                            "file": copy, "exporter": exporter, "options": options, "folder": str(folder),
                            "scene_version": scene_version, "blend_identity": export_snapshot.identity(blend),
                            "save_key": save_key, "baked": [list(pair) for pair in baked]})
    return True
