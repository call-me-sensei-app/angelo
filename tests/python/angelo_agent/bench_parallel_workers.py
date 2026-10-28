# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Worker Blenders: start time, tool-call round trip and memory per worker, against the same calls in this process.

Run alone, in a disposable background Angelo (the main one; it starts the workers)::

    <Angelo> --background --factory-startup --disable-autoexec --python-exit-code 1 \\
        --python tests/python/angelo_agent/bench_parallel_workers.py -- --out <folder ending in .noindex>

No provider, model or paid call. Measured, each after untimed warm-up rounds:

* **start:** ``--starts`` cold worker starts on an empty part, launch to ready (``WorkerProcess.ready_seconds``);
* **round trip** through a chat's real path (``Controller.request_tool`` and the pump), p50/p75 ms: a scene read,
  a direct edit (``angelo_set_transform``, saved after each) and a one-object program (staged, committed, checked,
  saved); the same calls on a chat on this process's own document (the window's path, no worker) as the baseline;
  plus the bare connection (``hello``);
* **rail pictures:** a worker's offscreen viewport picture of its part (``worker_host.thumbnail``) at 256 and 512 px,
  Solid and Material: round trip, the draw itself, PNG size, and the worker's footprint peak meanwhile; then four
  workers drawing at once (the stacked peak);
* **memory:** each worker's footprint (macOS ``footprint``, the Activity Monitor figure; Windows private bytes)
  idle and after the edits, with four workers running at once.

Writes ``parallel-workers-bench.json``. Every number names this binary and machine; nothing here is a claim alone.
"""

import json
import os
import platform
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = Path(ARGS[ARGS.index("--out") + 1]) if "--out" in ARGS else Path(tempfile.gettempdir(), "angelo-workers-bench")
STARTS = int(ARGS[ARGS.index("--starts") + 1]) if "--starts" in ARGS else 10
CALLS = int(ARGS[ARGS.index("--calls") + 1]) if "--calls" in ARGS else 30
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui, worker_routing  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import Controller, pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from angelo_agent.worker_process import WorkerProcess  # noqa: E402
from parallel_workers_qa import CASES, WorkerChat, percentile, primitive, program  # noqa: E402

report = {"binary": bpy.app.binary_path, "version": bpy.app.version_string, "platform": sys.platform,
          "machine": platform.machine(), "processor": platform.processor(), "cpus": os.cpu_count(),
          "starts": STARTS, "calls": CALLS}


def footprint(pid):
    """Bytes the OS charges ``pid`` (macOS phys_footprint; Windows private bytes), or None."""
    try:
        if sys.platform == "darwin":
            text = subprocess.run(["footprint", "-p", str(pid), "-f", "bytes"], capture_output=True, text=True,
                                  timeout=30).stdout
            for line in text.splitlines():
                if "phys_footprint:" in line:
                    return int(line.split(":")[1].split()[0])
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                    (name, ctypes.c_size_t) for name in ("PeakWorkingSetSize", "WorkingSetSize",
                                                          "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                                                          "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                                                          "PagefileUsage", "PeakPagefileUsage", "PrivateUsage")]
            handle = ctypes.windll.kernel32.OpenProcess(0x1000 | 0x0010, False, pid)
            counters = Counters(cb=ctypes.sizeof(Counters))
            try:
                if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    return counters.PrivateUsage
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def stats(seconds):
    return {"p50_ms": round(percentile(seconds, .5) * 1000, 2), "p75_ms": round(percentile(seconds, .75) * 1000, 2),
            "n": len(seconds)} if seconds else None


class LocalChat:
    """The same calls on a chat on this process's own document: the window's path, no worker."""

    def __init__(self):
        self.controller = Controller(str(OUT / "state"), session_id=str(uuid.uuid4()))
        self.controller.set_document(ui.document_key())
        # As the window binds every chat: the request snapshot before its first edit (a worker's chat has one too).
        self.controller.checkpoint_factory = lambda target=self.controller: ui.capture_checkpoint(target)
        ui._controllers[self.controller.state["id"]] = self.controller
        self.fence = self.controller.open_external_read()
        self.count = 0

    def call(self, name, arguments):
        self.count += 1
        answers = []
        window = bpy.context.window_manager.windows[0]

        def apply(target, call):
            with bpy.context.temp_override(window=window):
                bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])

        def commit(target, call):
            with bpy.context.temp_override(window=window):
                return bpy.ops.angelo.commit_native_job('EXEC_DEFAULT', True, call_id=call.id,
                                                         session_id=target.state["id"])
        started = time.perf_counter()
        self.controller.request_tool("local-%d" % self.count, name, arguments, answers.append)
        while not answers:
            self.controller.pump_events()
            pump_controller_tools([self.controller], 0, BlenderExecutor(window), ui.document_key(), apply,
                                  commit_mutation=commit)
        return answers[0], time.perf_counter() - started


class PeakSampler:
    """The highest footprint ``pid`` reaches while sampled every ``interval`` seconds (catches peaks inside draws)."""

    def __init__(self, pids, interval=0.2):
        import threading
        self.pids, self.interval, self.peak, self.stopped = list(pids), interval, {}, threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stopped.is_set():
            for pid in self.pids:
                value = footprint(pid)
                if value is not None:
                    self.peak[pid] = max(self.peak.get(pid, 0), value)
            self.stopped.wait(self.interval)

    def stop(self):
        self.stopped.set()
        self.thread.join(10)
        return self.peak


def thumbnails(chat, size, shading, count=None, fresh=True):
    """Round trip and the worker's own draw time of one rail picture, and the worker's footprint peak meanwhile.

    ``fresh``: each picture is really drawn (the view cache is cleared first), as after every change; otherwise an
    unchanged scene is answered from the cache, as a timed refresh while nothing changed would be."""
    count = count or max(10, CALLS // 2)
    from angelo_agent.refusal_codes import RefusalError
    try:
        chat.executor.thumbnail(size, shading, fresh=fresh)
    except RefusalError as error:   # No usable GPU here: the call fails, the worker goes on (worker_host).
        return {"unavailable": str(error), "worker_alive": chat.worker.alive()}
    for _ in range(1):
        chat.executor.thumbnail(size, shading, fresh=fresh)
    sampler = PeakSampler([chat.worker.process.pid])
    trips, draws, size_bytes = [], [], 0
    for _ in range(count):
        started = time.perf_counter()
        answer = chat.executor.thumbnail(size, shading, fresh=fresh)
        trips.append(time.perf_counter() - started)
        draws.append(answer["draw_ms"] / 1000)
        size_bytes = max(size_bytes, max((len(p) * 3 // 4 for p in answer["pictures"]), default=0))
    peak = sampler.stop()
    return {"round_trip": stats(trips), "draw": stats(draws), "png_bytes": size_bytes,
            "footprint_peak_bytes": peak.get(chat.worker.process.pid),
            "footprint_after_bytes": footprint(chat.worker.process.pid)}


def first_picture(count=3):
    """A new worker's first rail picture, which first runs the one-time probe (a child Blender draws one), against
    its second: the probe's cost per worker, measured on fresh workers each time."""
    from angelo_agent.refusal_codes import RefusalError
    from angelo_agent import remote_executor
    rows = []
    for index in range(count):
        remote_executor.OFFSCREEN_VERDICTS.clear()   # Measured fresh each time: what one new worker's probe costs.
        chat = WorkerChat(OUT / "state", OUT / "parts" / ("first-%d.blend" % index), ROOT)
        try:
            chat.call("angelo_create_cube", {"name": "Subject", "size": 1, "location": [0, 0, 0]})
            row = {}
            for label in ("first", "second"):
                started = time.perf_counter()
                try:
                    answer = chat.executor.thumbnail(256, fresh=True)
                except RefusalError as error:
                    row[label] = {"refused": str(error)[:200]}
                    continue
                row[label] = {"round_trip_ms": round((time.perf_counter() - started) * 1000, 2),
                              "draw_ms": answer["draw_ms"], "probe_ms": answer.get("probe_ms")}
            rows.append(row)
        finally:
            chat.stop()
    return rows


def thumbnails_at_once(chats):
    """Every worker draws one picture at the same moment, each on its own thread here: the stacked peak."""
    import threading
    sampler = PeakSampler([chat.worker.process.pid for chat in chats])
    started, done = time.perf_counter(), []

    def one(chat):
        process = chat.worker   # Each worker's own connection: the requests really overlap.
        process.request("thumbnail", 120, size=256, shading="SOLID", session=chat.session, fresh=True)
        done.append(time.perf_counter() - started)
    threads = [threading.Thread(target=one, args=(chat,)) for chat in chats for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(300)
    peak = sampler.stop()
    return {"pictures": len(done), "wall_s": round(time.perf_counter() - started, 3),
            "footprint_peak_sum_bytes": sum(peak.values()), "footprint_peak_bytes": list(peak.values())}


def main_side_pictures(chat, label, count=10):
    """The alternative to a worker drawing its own picture: Angelo links the worker's saved part into a scene of
    its own and draws it offscreen with its own GPU (as the live view would). Link, reload after a worker commit,
    and one 256 px Solid draw, in ms; Angelo's footprint before and after."""
    import gpu
    gpu.init()
    before = footprint(os.getpid())
    path = str(chat.worker.part)
    started = time.perf_counter()
    with bpy.data.libraries.load(path, link=True) as (source, target):
        target.objects = list(source.objects)
    scene = bpy.data.scenes.new("Rail " + label)
    for obj in target.objects:
        if obj is not None:
            scene.collection.objects.link(obj)
    link_s = time.perf_counter() - started
    library = next(lib for lib in bpy.data.libraries if bpy.path.abspath(lib.filepath) == path)
    reloads = []
    for index in range(count):
        chat.call("angelo_scene_program", program([primitive("CUBE", "%s rail %d" % (label, index), "r", size=0.1,
                                                             location=[index * 0.2, -2, 0])]))
        started = time.perf_counter()
        library.reload()
        reloads.append(time.perf_counter() - started)
    # The draw: Angelo's own view code on that scene (window.scene switched for the measurement only; the rail
    # would draw it offscreen without switching), the same 256 px Solid picture a worker draws.
    from angelo_agent import view_cache
    from angelo_agent.references import ReferenceStore
    window = bpy.context.window_manager.windows[0]
    executor = BlenderExecutor(window)
    executor.observation_store = ReferenceStore(str(OUT / "main-side-refs"), str(uuid.uuid4()))
    draws, home = [], window.scene
    try:
        window.scene = scene
        for _ in range(2 + count):
            view_cache.clear()
            started = time.perf_counter()
            executor.execute("angelo_view", {"views": ["ISO"], "shading": "SOLID", "resolution": 256})
            draws.append(time.perf_counter() - started)
    finally:
        window.scene = home
    return {"link_ms": round(link_s * 1000, 2), "reload": stats(reloads), "draw_256_solid": stats(draws[2:]),
            "angelo_footprint_before_bytes": before, "angelo_footprint_after_bytes": footprint(os.getpid())}


def round_trips(chat, label):
    timed, inside = {"read": [], "transform": [], "program": []}, {}
    warm = 2
    first = chat.call("angelo_scene_program", program([primitive("CUBE", label + " Box", "b", size=1)]))
    first = first[0] if isinstance(first, tuple) else first
    assert first.get("status") == "committed", first
    for index in range(warm + CALLS):
        for kind, name, arguments in (
                ("read", "angelo_inspect_scene", {}),
                ("transform", "angelo_set_transform", {"object": label + " Box", "location": [index * 0.01, 0, 0]}),
                ("program", "angelo_scene_program", program([primitive("CUBE", "%s %d" % (label, index), "c",
                                                                     size=0.2, location=[0, index * 0.3, 1])]))):
            started = time.perf_counter()
            result = chat.call(name, arguments)
            seconds = time.perf_counter() - started
            worker_ms = getattr(getattr(chat, "executor", None), "last_worker_ms", None)
            result = result[0] if isinstance(result, tuple) else result
            assert not result.get("error"), (name, result)
            if index >= warm:
                timed[kind].append(seconds)
                if worker_ms is not None:
                    inside.setdefault(kind, []).append(worker_ms / 1000)
    out = {kind: stats(values) for kind, values in timed.items()}
    if inside:   # The worker's own handling time of the same calls (the rest is transport and Angelo's side).
        out["worker_side"] = {kind: stats(values) for kind, values in inside.items()}
    return out


def run():
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)
    # The local baseline commits programs in this background process too: its document needs an Undo history, which
    # background Blender starts only for a later load once asked (headless_main._prepare_preferences).
    from angelo_agent.headless_main import _prepare_preferences
    _prepare_preferences(bpy)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT / "main.blend"), check_existing=False)
    bpy.ops.wm.open_mainfile(filepath=str(OUT / "main.blend"), load_ui=False, use_scripts=False)
    parts = OUT / "parts"
    parts.mkdir(exist_ok=True)
    starts = []
    for index in range(STARTS):
        worker = WorkerProcess(parts / ("start-%d.blend" % index), binary=bpy.app.binary_path,
                               script=ROOT / "scripts/angelo/angelo_headless.py")
        worker.start()
        starts.append(worker.ready_seconds)
        worker.stop()
    report["start"] = {"median_s": round(percentile(starts, .5), 3), "p75_s": round(percentile(starts, .75), 3),
                       "min_s": round(min(starts), 3), "max_s": round(max(starts), 3), "n": len(starts)}

    chats = [WorkerChat(OUT / "state", parts / ("part-%d.blend" % index), ROOT) for index in range(4)]
    report["memory_idle_bytes"] = [footprint(chat.worker.process.pid) for chat in chats]
    bare = []
    for index in range(2 + CALLS):
        started = time.perf_counter()
        chats[0].executor.ask("hello")
        if index >= 2:
            bare.append(time.perf_counter() - started)
    report["bare_round_trip"] = stats(bare)
    saves = []
    for index in range(2 + CALLS):   # Part of every worker edit: the part file is saved after it.
        started = time.perf_counter()
        chats[0].executor.ask("save")
        if index >= 2:
            saves.append(time.perf_counter() - started)
    report["part_save_round_trip"] = stats(saves)
    report["worker"] = round_trips(chats[0], "Worker")
    report["local"] = round_trips(LocalChat(), "Local")
    report["memory_after_edits_bytes"] = [footprint(chat.worker.process.pid) for chat in chats]
    for name, chat in (("vehicle", chats[1]), ("character", chats[2])):   # Held-out parts, built as a chat builds.
        for arguments in CASES[name]()[0]:
            assert chat.call("angelo_scene_program", arguments).get("status") == "committed"
    report["thumbnails"] = {}
    for label, chat in (("boxes", chats[0]), ("vehicle", chats[1]), ("character", chats[2])):
        for size, shading in ((256, "SOLID"), (256, "MATERIAL"), (512, "SOLID")):
            report["thumbnails"]["%s_%d_%s" % (label, size, shading)] = thumbnails(chat, size, shading)
        report["thumbnails"]["%s_256_SOLID_unchanged" % label] = thumbnails(chat, 256, "SOLID", fresh=False)
    report["memory_after_thumbnails_bytes"] = [footprint(chat.worker.process.pid) for chat in chats]
    report["four_at_once"] = thumbnails_at_once(chats[:4])
    report["first_picture"] = first_picture()
    try:
        report["main_side"] = {label: main_side_pictures(chat, label) for label, chat in (("vehicle", chats[1]),
                                                                                       ("character", chats[2]))}
    except Exception:   # A measurement of an alternative; its failure is reported, never the bench's.
        report["main_side_error"] = traceback.format_exc()
    report["main_process_bytes"] = footprint(os.getpid())
    report["overhead_by_size"] = overhead_by_size(chats[3])
    for chat in chats:
        chat.stop()


def overhead_by_size(chat):
    """Where a worker call's extra time goes as its answer grows: Angelo's round trip against the worker's own
    handling time (``worker_ms`` in every answer), for scene reads over 0, 50 and 200 objects."""
    from parallel_workers_qa import op
    rows = []
    for count, grid in ((0, None), (50, (10, 5)), (200, (15, 10))):   # 50, then 150 more: 200 objects.
        if grid:
            result = chat.call("angelo_scene_program", program([
                primitive("CUBE", "Grid %d" % count, "g", size=0.1, location=[0, 0, -3 - count / 50]),
                op("batch", sources=["$g"], array=[{"step": [0.2, 0, 0], "count": grid[0]},
                                                  {"step": [0, 0.2, 0], "count": grid[1]}])]))
            assert result.get("status") == "committed", result
        trips, inside, size = [], [], 0
        for index in range(2 + CALLS):
            started = time.perf_counter()
            answer = chat.executor.ask("execute", name="angelo_inspect_scene", arguments={}, session=chat.session)
            if index >= 2:
                trips.append(time.perf_counter() - started)
                inside.append(answer["worker_ms"] / 1000)
                size = max(size, len(json.dumps(answer)))
        rows.append({"objects": count, "answer_bytes": size, "round_trip": stats(trips), "worker": stats(inside),
                     "transport": stats([a - b for a, b in zip(trips, inside)])})
    return rows


try:
    run()
    report["success"] = True
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    worker_routing.stop_all()
(OUT / "parallel-workers-bench.json").write_text(json.dumps(report, indent=2))
print("PARALLEL-WORKERS-BENCH-DONE", report.get("success"))
