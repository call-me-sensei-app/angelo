# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A worker chat keeps working while the lead's script holds Blender's main thread (``worker_dispatch.away``).

This (background) Angelo has one chat bound to a real worker Blender (``parallel_workers_qa.WorkerChat``) and a lead
whose tool pump runs one real ``angelo_execute_python`` (``BlenderExecutor``) that holds the main thread for ``HOLD``
seconds, through the window's own tool pump (``tool_pump.pump_controller_tools``, the lead first, one tick's budget).
Meanwhile the worker chat's runtime is simulated: each result it gets is followed, after ``MODEL_S`` (a model round),
by its next call. Checked, from real results:

* the worker's call admitted as the script starts is answered while the script still runs, and further rounds follow
  during it (before g3b: none, every call waited for the script);
* the lead's script itself completes and the worker's results are its own;
* nothing touches ``bpy`` off the main thread: a guard on ``bpy.data``, ``bpy.context`` and ``bpy.ops`` plus a profile
  hook on the lanes' threads records any use, through one guarded (untimed) round.

The timings (``delay_s``: from a call's admission to its result reaching the runtime, which is what lets the next
model request go out) are written whether or not the checks pass, so the same suite measures a tree before the
change. Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import threading
import time
import traceback
import uuid
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-parallel-away-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import tool_pump, ui, worker_dispatch  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.controller import ToolDispatchBudget  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
from parallel_workers_qa import WorkerChat, percentile, worker_chat  # noqa: E402
from hosted_worker_chat import hosted  # noqa: E402

# Design A′ (the "hosted" variant): the worker chat's model and Controller run in its worker, so a lead holding its
# main thread can only delay what the lead draws; the model's rounds are the stand-in's (``hosted_round``). The lanes
# variant keeps the simulated runtime here (``one_round``).
HOSTED = hosted()

HOLD = float(os.environ.get("ANGELO_AWAY_HOLD", "3.0"))   # The lead's script, seconds on the main thread.
MODEL_S = 0.2   # A simulated model round between a worker's result and its next call.
TRIALS = int(os.environ.get("ANGELO_AWAY_TRIALS", "5"))
SCRIPT = ("import time\nend = time.monotonic() + %r\nloops = 0\nwhile time.monotonic() < end:\n"
          "    loops += 1 + 0 * len(bpy.data.objects)\nresult = {'loops': loops}\n")   # Its Python share.
report = {"variant": "hosted" if HOSTED else "lanes", "checks": {}, "paid_requests": 0,
          "binary_path": bpy.app.binary_path, "platform": sys.platform,
          "hold_s": HOLD, "model_s": MODEL_S, "trials": [], "away_api": hasattr(worker_dispatch, "away")}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition, name


class LeadScript:
    """The lead chat's tool pump: one approved ``angelo_execute_python`` run by Angelo's own executor."""

    def __init__(self, window, seconds):
        self.state, self.remote_executor, self.has_dispatchable_tools = {"id": "lead-script"}, None, True   # Its call.
        self.window, self.code, self.result, self.seconds = window, SCRIPT % seconds, None, seconds
        self.started = self.ended = None

    def pump_tools(self, executor, document, apply_mutation=None, budget=None, commit_mutation=None):
        self.started = time.perf_counter()
        try:
            self.result = BlenderExecutor(self.window).execute(
                "angelo_execute_python", {"description": "Hold the main thread", "code": self.code})
        finally:
            self.ended = time.perf_counter()


class LeadRender(LeadScript):
    """The lead's tool pump inside a render (Blender's own C code, as a view's draw is) until ``seconds``: a Cycles CPU
    still, again and again (a background Blender has no GPU module for ``angelo_view``'s offscreen draw)."""

    def pump_tools(self, executor, document, apply_mutation=None, budget=None, commit_mutation=None):
        scene = bpy.context.scene
        scene.render.engine, scene.cycles.device, scene.cycles.samples = "CYCLES", "CPU", 16
        scene.render.resolution_x, scene.render.resolution_y = 640, 360
        scene.render.threads_mode, scene.render.threads = "FIXED", 2
        self.started, self.views = time.perf_counter(), 0
        try:
            while time.perf_counter() - self.started < self.seconds:
                self.result = {"status": sorted(bpy.ops.render.render(write_still=False))}
                self.views += 1
        except Exception as error:   # noqa: BLE001 - recorded: a machine that cannot render here skips this.
            self.result = {"error": repr(error)[:300]}
        finally:
            self.ended = time.perf_counter()


class Runtime:
    """The worker chat's runtime: its next call ``MODEL_S`` after each result, for as long as ``more`` says."""

    def __init__(self, chat):
        self.chat, self.calls, self.more, self.lock = chat, [], True, threading.Lock()
        self.prefix = "away-" + uuid.uuid4().hex[:12]   # Never an earlier call's id: that one is replayed at once.

    def ask(self):
        with self.lock:
            number = len(self.calls) + 1
            record = {"n": number, "asked": time.perf_counter(), "answered": None, "result": None}
            self.calls.append(record)

        def answered(result):
            record["answered"], record["result"] = time.perf_counter(), result
            if self.more:
                threading.Timer(MODEL_S, self.ask).start()
        self.chat.controller.request_tool("%s-%d" % (self.prefix, number), "angelo_inspect_scene", {}, answered)

    def outstanding(self):
        return [record for record in list(self.calls) if record["answered"] is None]


def apply_mutation(target, call):
    with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
        bpy.ops.angelo.approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])


def one_round(chat, window, seconds, kind=LeadScript):
    """The lead's script (or views) in one UI tick (the lead first), the worker's runtime asking from its start; then
    the window pumps on until every call is answered."""
    lead, runtime = kind(window, seconds), Runtime(chat)
    runtime.ask()   # Admitted as the lead's tick begins: the lane has not dispatched it yet.
    tool_pump.pump_controller_tools([lead, chat.controller], 0, None, None, apply_mutation, budget=ToolDispatchBudget())
    runtime.more = False
    deadline = time.perf_counter() + 60
    while (runtime.outstanding() or not runtime.calls) and time.perf_counter() < deadline:
        chat.pump()
    time.sleep(MODEL_S * 2)   # A timer that fired as ``more`` turned off may still ask once: answer it too.
    while runtime.outstanding() and time.perf_counter() < deadline:
        chat.pump()
    calls = [dict(record, result=None, ok=isinstance(record["result"], dict) and not record["result"].get("error")
                  and not record["result"].get("replayed"),
                  delay_s=round(record["answered"] - record["asked"], 4) if record["answered"] else None,
                  during=bool(record["answered"] and record["answered"] < lead.ended))
             for record in runtime.calls]
    return lead, calls


def hosted_round(chat, window, seconds, kind=LeadScript):
    """A hosted worker chat's model reads its scene every ``MODEL_S`` (one stand-in turn), while the lead's tick runs
    ``kind`` for ``seconds``; each call's delay is the model's own (sent to answered, the monotonic clock)."""
    lead, ids = kind(window, seconds), []
    steps = []
    for number in range(int(seconds / MODEL_S) + 4):
        ids.append("away-%s-%d" % (uuid.uuid4().hex[:10], number))
        steps += [{"call": {"id": ids[-1], "tool": "angelo_inspect_scene", "arguments": {}}}, {"sleep": MODEL_S}]
    chat.say("Keep reading the scene.", steps)   # Returns once the worker's turn reached the model.
    tool_pump.pump_controller_tools([lead, chat.controller], 0, None, None, apply_mutation, budget=ToolDispatchBudget())
    ended_ns = time.monotonic_ns()
    chat.wait_idle()
    rows = {row["id"]: row for row in chat.model_log("tool_result") if row.get("id") in ids}
    calls = []
    for number, identity in enumerate(ids, start=1):
        row = rows.get(identity) or {}
        answered, sent = row.get("answered_ns"), row.get("sent_ns")
        calls.append({"n": number, "ok": row.get("success") is True,
                      "delay_s": round((answered - sent) / 1e9, 4) if answered and sent else None,
                      "during": bool(answered and answered < ended_ns)})
    return lead, calls


def guarded_hosted_round(chat, window):
    """One hosted round with ``bpy``'s entry points guarded and every thread profiled (the channel's reader and the
    mirror's threads were started before it: ``threading.setprofile_all_threads``)."""
    seen, modules = [], ("bpy", "_bpy", "bpy_types", "_bpy_types")
    main = threading.main_thread()

    def profile(frame, event, argument):
        if threading.current_thread() is main:
            return
        if event == "call":
            name = frame.f_globals.get("__name__", "")
            if name.split(".")[0] in modules or "/modules/bpy" in frame.f_code.co_filename:
                seen.append((threading.current_thread().name, "call", name + "." + frame.f_code.co_name))
        elif event == "c_call":
            owner = type(getattr(argument, "__self__", None)).__module__ or ""
            module = getattr(argument, "__module__", None) or ""
            if module.split(".")[0] in modules or owner.split(".")[0] in modules:
                seen.append((threading.current_thread().name, "c_call", owner + ":" + getattr(argument, "__name__", "")))
    real = {name: getattr(bpy, name) for name in ("data", "context", "ops")}
    threading.setprofile_all_threads(profile)
    for name, value in real.items():
        setattr(bpy, name, Guard(value, name, seen))
    try:
        control = threading.Thread(target=lambda: bpy.data.objects, name="AngeloHostedControl")
        control.start()   # The instrument's positive control: another thread's use is recorded.
        control.join()
        report["guard_control_recorded"] = [entry for entry in seen if entry[0] == "AngeloHostedControl"]
        seen[:] = [entry for entry in seen if entry[0] != "AngeloHostedControl"]
        lead, calls = hosted_round(chat, window, min(HOLD, 2.0))
    finally:
        for name, value in real.items():
            setattr(bpy, name, value)
        threading.setprofile_all_threads(None)
    return lead, calls, seen


class Guard:
    """``bpy.data``/``context``/``ops`` stand-in recording any use off the main thread."""

    def __init__(self, target, name, seen):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_seen", seen)

    def __getattr__(self, attribute):
        if threading.current_thread() is not threading.main_thread():
            self._seen.append((threading.current_thread().name, self._name, attribute))
        return getattr(self._target, attribute)

    def __setattr__(self, attribute, value):
        if threading.current_thread() is not threading.main_thread():
            self._seen.append((threading.current_thread().name, self._name, attribute))
        setattr(self._target, attribute, value)


def guarded_round(chat, window):
    """One round with every lane thread profiled and ``bpy``'s entry points guarded."""
    seen, modules = [], ("bpy", "_bpy", "bpy_types", "_bpy_types")

    def profile(frame, event, argument):
        if not threading.current_thread().name.startswith("AngeloWorkerLane"):
            return
        if event == "call":
            name = frame.f_globals.get("__name__", "")
            if name.split(".")[0] in modules or "/modules/bpy" in frame.f_code.co_filename:
                seen.append((threading.current_thread().name, "call", name + "." + frame.f_code.co_name))
        elif event == "c_call":
            owner = type(getattr(argument, "__self__", None)).__module__ or ""
            module = getattr(argument, "__module__", None) or ""
            if module.split(".")[0] in modules or owner.split(".")[0] in modules:
                seen.append((threading.current_thread().name, "c_call", owner + ":" + getattr(argument, "__name__", "")))
    real = {name: getattr(bpy, name) for name in ("data", "context", "ops")}
    worker_dispatch.close_all()
    threading.setprofile(profile)
    for name, value in real.items():
        setattr(bpy, name, Guard(value, name, seen))
    try:
        control = threading.Thread(target=lambda: bpy.data.objects, name="AngeloWorkerLaneControl")
        control.start()   # The instrument's positive control: a lane-named thread's use is recorded.
        control.join()
        report["guard_control_recorded"] = [entry for entry in seen if entry[0] == "AngeloWorkerLaneControl"]
        seen[:] = [entry for entry in seen if entry[0] != "AngeloWorkerLaneControl"]
        chat.pump()   # The chat's lane again, profiled from its first line (an idle tick: nothing is queued).
        report["guarded_lane_started"] = worker_dispatch.lane(chat.controller, create=False) is not None
        lead, calls = one_round(chat, window, min(HOLD, 2.0))
    finally:
        for name, value in real.items():
            setattr(bpy, name, value)
        threading.setprofile(None)
        worker_dispatch.close_all()
    return lead, calls, seen


def run_checks():
    _register(bpy, ui)
    window = bpy.context.window_manager.windows[0]
    chat = worker_chat(OUT / "state", OUT / "parts" / "away.blend", ROOT)
    round_of = hosted_round if HOSTED else one_round
    try:
        warm = chat.call("angelo_inspect_scene", {})
        check("the_worker_answers", not warm.get("error"), warm)
        for trial in range(TRIALS):
            lead, calls = round_of(chat, window, HOLD)
            report["trials"].append({"script_s": round(lead.ended - lead.started, 3),
                                     "script_ok": isinstance(lead.result, dict) and not lead.result.get("error"),
                                     "script_error": (lead.result or {}).get("error"), "calls": calls,
                                     "script_loops": ((lead.result or {}).get("result") or {}).get("loops")})
        views = []
        for trial in range(max(1, TRIALS // 2)):   # A lead inside Blender's C code (a render, as a view's draw).
            lead, calls = round_of(chat, window, HOLD, LeadRender)
            views.append({"views": lead.views, "seconds": round(lead.ended - lead.started, 3), "calls": calls,
                          "error": (lead.result or {}).get("error") if isinstance(lead.result, dict) else repr(lead.result)})
        report["view_trials"] = views
        drawn = [trial for trial in views if not trial["error"]]
        if drawn:
            report["view_first_call_delay_s"] = [trial["calls"][0]["delay_s"] for trial in drawn]
            report["view_rounds_during"] = [sum(call["during"] for call in trial["calls"]) for trial in drawn]
        report["lane_stats"] = dict(worker_dispatch.STATS)
        firsts = [trial["calls"][0]["delay_s"] for trial in report["trials"]]
        report["first_call_delay_s"] = {"p50": percentile(firsts, .5), "p75": percentile(firsts, .75),
                                        "min": min(firsts), "max": max(firsts), "all": firsts}
        report["rounds_during_script"] = [sum(call["during"] for call in trial["calls"]) for trial in report["trials"]]
        check("the_leads_script_ran_whole", all(trial["script_ok"] and trial["script_s"] >= HOLD
                                                 for trial in report["trials"]), report["trials"])
        check("every_worker_call_was_answered_once", all(call["ok"] for trial in report["trials"]
                                                          for call in trial["calls"]), report["trials"])
        check("the_worker_was_answered_while_the_script_ran", all(trial["calls"][0]["during"]
                                                                  for trial in report["trials"]), firsts)
        check("the_workers_next_requests_went_on_during_it", min(report["rounds_during_script"]) >= 2,
              report["rounds_during_script"])
        lead, calls, seen = (guarded_hosted_round if HOSTED else guarded_round)(chat, window)
        report["guarded"] = {"calls": calls, "seen": seen[:50], "lane_steps": dict(worker_dispatch.STATS)}
        check("the_guarded_round_ran_in_a_region", lead.result is not None and calls and all(call["ok"] for call in calls)
              and calls[0]["during"], calls)
        check("the_guard_records_a_lane_threads_use", report["guard_control_recorded"])
        check("nothing_touched_bpy_off_the_main_thread", not seen, seen[:20])
        report["success"] = True
    finally:
        chat.stop()


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    try:
        from angelo_agent import worker_routing
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "parallel-away.json").write_text(json.dumps(report, indent=2, default=str))
print("PARALLEL-AWAY-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()   # os._exit skips it: the DONE line must reach a piped log too.
    os._exit(1)   # Report written and workers stopped above: a failed run never exits 0.
