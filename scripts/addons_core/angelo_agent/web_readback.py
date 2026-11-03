# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web export's read-back, in a background Angelo after the .glb is written: the export returns at once.

``start(controller, path, written, source, merged)`` (main thread, from ``web_host``) gives the export a revision,
copies the .glb into a private folder as it was written (``export_snapshot``: a later export over the same file
never changes what is compared) with the comparison's inputs, and starts a background Angelo that reads the copy back
in an empty scene and compares it with the scene's description taken before the export (``engine_reimport``;
``engine_preflight.readback_child``). One runs at a time; the newest export during one replaces the queued one, and
the replaced one ends at once as superseded (its folder removed). A timer, registered only while a read-back runs or
waits, takes each result on the main thread: the chat that asked gets a notice (the person's row; none for a
superseded one) and the result, with its revision, joins that chat's next request as the ``preflight`` observation
(``for_request``), once. Nothing here touches the scene, and nothing runs when no web export asked for it.
doc/angelo/engine_preflight.rst ("Re-import comparison").
"""

import json
import os
import queue
import shutil
import subprocess
import threading
import weakref

from . import thread_start
from .ui_text import n_

TIMEOUT_SECONDS = 600
POLL_SECONDS = 0.5
MAX_WAITING = 4   # Results a chat holds for its next request; older ones go (each notice already showed them).
CHILD = r"""
import json, sys
from angelo_agent import engine_preflight
engine_preflight.readback_child(json.loads(open(sys.argv[-1], encoding="utf-8").read()))
"""
MATCHES = n_("Read {file} back: it matches the scene")
DIFFERS = n_("Read {file} back: {count} differences from the scene. Ask the agent for them")
FAILED = n_("Angelo couldn't read {file} back to check it")


class _Runner:
    def __init__(self, binary):
        self.binary = binary
        self.lock = threading.Lock()
        self.running = False
        self.queued = None
        self.done = queue.Queue()

    def request(self, job):
        with self.lock:
            dropped, start = None, not self.running
            if self.running:
                dropped, self.queued = self.queued, job
            self.running = True
        if dropped is not None:   # The newest export replaces one still waiting, which ends here, said so.
            shutil.rmtree(dropped["folder"], ignore_errors=True)
            self.done.put((dropped, {"superseded_by": job["revision"]}))
        if start:   # A start that raises leaves the runner idle, so the next export starts one (``thread_start``).
            thread_start.start(threading.Thread(target=self._run, args=(job,), name="Angelo web read-back",
                                                daemon=True), self._idle)

    def _idle(self):
        with self.lock:
            self.running = False

    def _run(self, job):
        from .engine_link_writeback import _child_environment
        result = None
        try:
            subprocess.run([self.binary, "--background", "--factory-startup", "--python-exit-code", "1",
                            "--python-expr", CHILD, "--", job["spec"]], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=TIMEOUT_SECONDS, env=_child_environment())
            with open(job["result"], encoding="utf-8") as handle:
                result = json.load(handle)
        except Exception as error:   # noqa: BLE001 - a read-back that couldn't run is reported, never raised.
            result = {"error": type(error).__name__}
        finally:
            shutil.rmtree(job["folder"], ignore_errors=True)
        self.done.put((job, result))
        with self.lock:
            again, self.queued = self.queued, None
            self.running = again is not None
        if again is not None:
            self._run(again)


_state = {"runner": None, "timer": False, "revision": 0}


def start(controller, path, written, source, merged):
    """Read the .glb at ``path`` (shown as ``written``, workspace-relative) back in the background, from a copy made
    now, and report it to ``controller``'s chat. Returns (state, revision): "running" and the export's revision, which
    its result carries, or ("unavailable", None) when there is no Angelo to start, nothing to copy, no chat to report
    to, or anything else stops it. Report-only: the file is published already, so nothing here fails the export."""
    try:
        return _start(controller, path, written, source, merged)
    except Exception as error:  # noqa: BLE001 - a read-back that can't start is "unavailable", never a failed export.
        print("Angelo: web read-back did not start:", type(error).__name__, error)
        return "unavailable", None


def _start(controller, path, written, source, merged):
    import bpy
    from . import export_snapshot, private_roots
    binary = bpy.app.binary_path
    if not binary or not os.path.isfile(binary):
        return "unavailable", None
    reply = weakref.ref(controller) if controller is not None else None   # Before anything is made: may raise.
    folder = private_roots.allocate("angelo-preflight-")
    try:
        copy = str(folder / "export.glb")
        copied = export_snapshot.take(path, copy)
        spec_file = _write_spec(folder, copy, copied, source, merged)
        if _state["runner"] is None:
            _state["runner"] = _Runner(binary)
        _state["revision"] += 1
        _state["runner"].request({"spec": spec_file, "result": str(folder / "result.json"), "folder": str(folder),
                                  "written": written, "revision": _state["revision"], "controller": reply})
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    if not _state["timer"]:
        _state["timer"] = True
        # Persistent: a file opened during a read-back ends no result, and no later export's.
        bpy.app.timers.register(_tick, first_interval=POLL_SECONDS, persistent=True)
    return "running", _state["revision"]


def _write_spec(folder, copy, copied, source, merged):
    """The child's spec beside the copy; returns its path."""
    import bpy
    home = os.path.expanduser("~")
    needles = {"home folder": home if home not in {"", "/", "~"} else ""}
    if bpy.data.filepath:
        needles["the .blend's folder"] = os.path.dirname(bpy.data.filepath)
    spec = {"path": copy, "identity": copied, "source": source, "merged": merged, "needles": needles,
            "result": str(folder / "result.json")}
    path = str(folder / "spec.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(spec, handle)
    return path


def _deliver(job, result):
    from .ui_text import notice
    controller = job["controller"]() if job["controller"] else None
    if controller is None:
        return
    entry = {"file": job["written"], "revision": job["revision"]}
    if result is not None and "superseded_by" in result:   # The agent hears it ended; the newer one's notice shows.
        entry.update(status="superseded", superseded_by=result["superseded_by"])
        row = None
    elif result is None or "error" in result:
        entry["status"] = "failed"
        entry["error"] = (result or {}).get("error", "failed")
        row = notice(FAILED, file=job["written"])
    else:
        entry.update(status="done", findings=result.get("findings", []), ms=result.get("ms"))
        count = len(entry["findings"])
        row = notice(DIFFERS, file=job["written"], count=count) if count else notice(MATCHES, file=job["written"])
    waiting = getattr(controller, "_web_readbacks", None)
    if waiting is None:
        waiting = controller._web_readbacks = []
    waiting.append(entry)
    del waiting[:-MAX_WAITING]
    if row is not None:
        controller.emit("message", {"role": "system", **row})


def _tick():
    runner = _state["runner"]
    while True:
        try:
            job, result = runner.done.get_nowait()
        except queue.Empty:
            break
        try:
            _deliver(job, result)
        except Exception as error:   # noqa: BLE001 - one chat's delivery never stops the others'.
            print("Angelo: web read-back:", type(error).__name__, error)
    with runner.lock:
        busy = runner.running or runner.queued is not None or not runner.done.empty()
    if busy:
        return POLL_SECONDS
    _state["timer"] = False   # Nothing left: the timer goes until the next web export asks again.
    return None


def for_request(controller):
    """The finished read-backs ``controller``'s next request carries (the ``preflight`` observation), once; or None."""
    waiting = getattr(controller, "_web_readbacks", None)
    if not waiting:
        return None
    from .engine_findings import summarize
    taken, waiting[:] = list(waiting), []
    return [dict({key: value for key, value in entry.items() if key != "findings"}, **summarize(entry["findings"]))
            if "findings" in entry else entry for entry in taken]
