# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Design A′ in real Blenders: worker chats hosted in their worker, played by a stand-in model (``hosted_worker_chat``).

This (background) Angelo is the lead. Each case makes a worker chat as the lead does (a mirror, ``chat.open`` in a
real worker Blender) and plays its turns through the stand-in ``codex`` app-server, which the lead's
``RuntimeService`` launches and relays to the chat's provider in the worker. Checked, from the real results, the
chat's journal and what the stand-in model saw:

* dispatch: a read and typed edits run in the worker (Auto, and Ask through the mirror's card and Angelo's approve
  operator); the lead's scene never changes; the lead never runs a tool or a provider for the chat;
* python consent through the mirror: a script asks even in Auto, Decline runs nothing, Approve runs it in the worker;
* merge hold and part lease (``worker_chat_lifecycle.merge_guard``): while held the chat admits no new call, the
  part's save waits for the lease, and both release;
* scene-switch reopen with resume: the worker takes another part (``chat.close``, switch, ``chat.open`` with resume)
  and the same conversation continues there;
* pool reuse: a parked worker's process takes the next chat;
* a worker crash mid-call: the admitted call ends interrupted, the turn ends, the file comes back to the lead;
* the lead's Stop on a busy worker ends the turn, and the model child is gone;
* a consent revocation: Auto narrowed to Ask is acknowledged by the worker before the toggle returns, and the next
  edit asks; one a frozen worker cannot acknowledge marks the chat and ends its model child within the deadline.

Only with ``ANGELO_WORKER_CHAT_HOST`` unset or ``1`` (the hosted variant); the lanes variant records ``skipped``.
Run only in a disposable instance with the exact Angelo binary:
``--background --factory-startup --disable-autoexec --python-exit-code 1``. No provider or paid call.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv and bpy.app.background, "Use a disposable headless factory-startup instance"
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-worker-chat-hosted-qa"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"), ANGELO_ENABLE_PARALLEL_AGENTS="1")
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import ui, worker_routing  # noqa: E402
from angelo_agent.headless_main import _register  # noqa: E402
import hosted_worker_chat  # noqa: E402
from hosted_worker_chat import HostedWorkerChat, hosted, pump_lead  # noqa: E402
from parallel_workers_qa import CASES  # noqa: E402

report = {"checks": {}, "paid_requests": 0, "binary_path": bpy.app.binary_path, "platform": sys.platform,
          "timings": {}, "variant": "hosted" if hosted() else "lanes"}
STATE, PARTS = OUT / "state", OUT / "parts"


def check(name, condition, detail=None, soft=False):
    """Record ``name``; a failed check ends its case unless ``soft`` (the case's later checks still run)."""
    report["checks"][name] = bool(condition)
    if not condition and detail is not None:
        report.setdefault("details", {})[name] = detail
    assert condition or soft, name


def committed(result):
    return (isinstance(result, dict) and result.get("status") == "committed" and result.get("success") is True
            and not result.get("error"))


def main_scene():
    return sorted(obj.name for obj in bpy.data.objects)


def wait_until(predicate, timeout=30.0, mirrors=()):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pump_lead(mirrors)
        if predicate():
            return True
        time.sleep(0.01)
    return False


def dispatch(before):
    """A read, an Auto program and an Ask edit, all run in the worker; the lead runs nothing."""
    chat = HostedWorkerChat(STATE, PARTS / "dispatch.blend", ROOT)
    report["timings"]["start_seconds"] = chat.start_seconds
    from angelo_agent import worker_mirror
    check("dispatch_chat_is_a_mirror", worker_mirror.hosted(chat.controller))
    first = chat.call("angelo_inspect_scene", {})
    check("dispatch_read_runs_in_the_worker", isinstance(first.get("objects"), list), first)
    plan, names = CASES["chess"]()
    built = chat.call("angelo_scene_program", plan[0])
    check("dispatch_auto_program_commits_in_the_worker", committed(built), built)
    check("dispatch_part_has_its_objects", names <= set(chat.objects()))
    check("dispatch_lead_scene_unchanged", main_scene() == before, main_scene())
    seen = chat.model_log("tool_result")
    check("dispatch_model_got_every_answer", [row["tool"] for row in seen][:3] == [
        "angelo_inspect_scene", "angelo_scene_program", "angelo_inspect_scene"]
        and all(row.get("success") for row in seen), seen)
    started = chat.model_log("turn_started")
    check("dispatch_each_message_reached_the_model", len(started) >= 3, started)
    # Ask: the card is the mirror's pending summary of the worker's call, approved with Angelo's own operator.
    chat.controller.set_options(approval="ASK")
    check("dispatch_ask_reached_the_worker", wait_until(
        lambda: chat.controller.state["options"].get("approval") == "ASK", mirrors=[chat.controller]),
        chat.controller.state.get("options"))
    key, answers = chat.request("angelo_set_transform", {"object": "Knight Base", "location": [1, 0, 0.09]})
    card = chat.wait_for_approval(key, answers)
    check("dispatch_ask_card_has_the_workers_digest", bool(getattr(card, "approval_digest", "")))
    chat.approve(card)
    moved = chat.wait(answers)
    chat.wait_idle()
    check("dispatch_ask_edit_runs_after_approve", moved.get("success") is True, moved)
    check("dispatch_ask_edit_moved_it", chat.objects()["Knight Base"]["location"][0] == 1.0)
    check("dispatch_lead_runs_no_provider", chat.controller.provider is None)
    return chat


LONG_SCRIPT = "x = 0\nfor i in range(150000000):\n    x += i\nresult = {'sum': x}"   # Many seconds of pure Python


def python_consent():
    """A script asks even in Auto: the mirror's card, Decline runs nothing; Approve (Angelo's operator) runs it."""
    chat = HostedWorkerChat(STATE, PARTS / "python.blend", ROOT)
    code = "import bpy\nbpy.data.objects.new('Scripted', None)\nbpy.context.scene.collection.objects.link(" \
           "bpy.data.objects['Scripted'])\nresult = {'made': 'Scripted'}"
    key, answers = chat.request("angelo_execute_python", {"description": "an empty", "code": code})
    card = chat.wait_for_approval(key, answers)
    check("python_asks_in_auto_through_the_mirror", card is not None and bool(card.approval_digest))
    chat.decline(card)
    declined = chat.wait(answers)
    chat.wait_idle()
    check("python_decline_runs_nothing", declined.get("error_code") == "DECLINED", declined)
    check("python_declined_left_no_object", "Scripted" not in chat.objects())
    key, answers = chat.request("angelo_execute_python", {"description": "an empty", "code": code})
    chat.approve(chat.wait_for_approval(key, answers))
    ran = chat.wait(answers)
    chat.wait_idle()
    check("python_approved_runs_in_the_worker", ran.get("success") is True, ran)
    check("python_object_is_in_the_part", "Scripted" in chat.objects())
    check("python_never_ran_in_the_lead", "Scripted" not in bpy.data.objects)
    chat.stop()


def merge_hold_and_lease():
    """While a merge holds the chat it admits no new call; while the lead leases its part, the part's save waits."""
    from angelo_agent import worker_chat_lifecycle as lifecycle
    chat = HostedWorkerChat(STATE, PARTS / "merge.blend", ROOT)
    chat.call("angelo_create_cube", {"name": "Held", "size": 1, "location": [0, 0, 0]})
    part = Path(chat.worker.part)
    times = report["merge_hold_times"] = {}
    with lifecycle.merge_guard([chat.session], []):   # The hold alone; the lease below.
        times["held"] = time.time()
        key, answers = chat.request("angelo_create_cube", {"name": "During", "size": 1, "location": [2, 0, 0]})
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            chat.pump()
        ran = [row for row in chat.model_log("tool_result") if row.get("id") == key]
        times["checked"], times["ran"] = time.time(), [row.get("t") for row in ran]
        check("merge_hold_admits_no_call", not answers and not ran, {"answers": answers, "ran": ran}, soft=True)
    times["released"] = time.time()
    during = chat.wait(answers)
    chat.wait_idle()
    check("merge_release_lets_the_call_run", during.get("success") is True, during)
    before = part.stat().st_mtime_ns
    with lifecycle.leased_read(part):
        key, answers = chat.request("angelo_create_cube", {"name": "Leased", "size": 1, "location": [4, 0, 0]})
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            chat.pump()
        check("lease_holds_the_parts_save", part.stat().st_mtime_ns == before,
              {"before": before, "now": part.stat().st_mtime_ns, "answered": bool(answers)})
    leased = chat.wait(answers)
    chat.wait_idle()
    check("lease_release_lets_the_edit_finish", leased.get("success") is True, leased)
    check("lease_release_lets_the_part_save", wait_until(lambda: part.stat().st_mtime_ns != before, 10),
          part.stat().st_mtime_ns)
    chat.stop()


def switch_reopen_with_resume():
    """The worker takes another part with the chat still bound: it closes there, the worker switches, and the chat
    opens on the new part with resume, continuing its conversation (C10)."""
    from angelo_agent import worker_journal_owner
    chat = HostedWorkerChat(STATE, PARTS / "switch-a.blend", ROOT)
    chat.call("angelo_create_cube", {"name": "First Part", "size": 1, "location": [0, 0, 0]})
    pid, messages = chat.worker.process.pid, len(chat.controller.state.get("messages") or [])
    chat.worker.part = PARTS / "switch-b.blend"
    chat.worker.start()   # As a part's start switches a running worker (WorkerProcess._switch, then _moved).
    check("switch_kept_the_process", chat.worker.process.pid == pid and chat.worker.how == "switched",
          chat.worker.how)
    check("switch_rehosted_the_chat", worker_journal_owner.hosted(chat.session))
    found = chat.objects()
    check("switch_chat_works_on_the_new_part", "First Part" not in found, sorted(found))
    made = chat.call("angelo_create_cube", {"name": "Second Part", "size": 1, "location": [0, 0, 0]})
    check("switch_edit_runs_on_the_new_part", made.get("success") is True, made)
    texts = [m.get("text") for m in chat.controller.state.get("messages") or [] if m.get("role") == "user"]
    check("switch_resumed_the_same_conversation", len(chat.controller.state.get("messages") or []) > messages
          and texts[0].startswith("Please run angelo_create_cube."), texts)
    resumed = [row for row in chat.model_log("request") if row.get("method") in ("thread/resume", "thread/fork")]
    report["switch_runtime_requests"] = [row.get("method") for row in resumed]
    chat.stop()


def pool_reuse():
    """A done chat's worker process takes the next part (``worker_pool.reuse``'s steps): the done chat closes in the
    worker and leaves, and a new chat opens in the same process with nothing of the first part."""
    from angelo_agent import worker_chat_lifecycle as lifecycle, worker_journal_owner
    done = HostedWorkerChat(STATE, PARTS / "pool-a.blend", ROOT)
    done.call("angelo_create_cube", {"name": "Pool First", "size": 1, "location": [0, 0, 0]})
    worker, pid = done.worker, done.worker.process.pid
    lifecycle.close_chat(done.session, "its worker takes another part")
    worker_routing.detach(done.controller)
    check("pool_done_chat_handed_back", not worker_journal_owner.hosted(done.session))
    reused = HostedWorkerChat(STATE, PARTS / "pool-b.blend", ROOT, worker=worker)
    check("pool_same_process", reused.worker.process.pid == pid and worker.how == "switched", worker.how)
    found = reused.objects()
    check("pool_nothing_of_the_first_part", "Pool First" not in found, sorted(found))
    made = reused.call("angelo_create_cube", {"name": "Pool Second", "size": 1, "location": [0, 0, 0]})
    check("pool_new_chat_edits_there", made.get("success") is True, made)
    from angelo_agent.runtime_service import SESSION_CHILDREN   # Its app-server, a catalog listing, a prewarm.
    check("pool_children_within_the_chats_bound", len(reused.runtime_children()) <= SESSION_CHILDREN,
          len(reused.runtime_children()))
    reused.stop()


def busy_script(chat):
    """An approved long script, running in the worker now: approved, and a second later still unanswered (the
    worker runs a call on its main thread, so its summary cannot say ``executing`` meanwhile)."""
    key, answers = chat.request("angelo_execute_python", {"description": "a long sum", "code": LONG_SCRIPT})
    done = chat.approve(chat.wait_for_approval(key, answers))
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        chat.pump()
    report.setdefault("busy_script", []).append({"approve": sorted(done), "writing": chat.controller.writing()})
    return key, answers, done == {'FINISHED'} and not answers and not [
        row for row in chat.model_log("tool_result") if row.get("id") == key]


def worker_crash_mid_call():
    """The worker dies while it runs a call: the call ends interrupted, the turn ends, the file comes back here."""
    from angelo_agent import session_journal, worker_journal_owner
    chat = HostedWorkerChat(STATE, PARTS / "crash.blend", ROOT)
    key, answers, busy = busy_script(chat)
    check("crash_call_was_running", busy)
    chat.worker.process.kill()   # SIGKILL here, TerminateProcess on Windows (Popen.kill)
    started = time.monotonic()
    # Nothing asks the worker anything after its death: its own end is acted on (``WorkerProcess.watch``, FE D5).
    check("crash_turn_ends_on_the_connections_end", wait_until(lambda: not chat.controller.busy, 10,
                                                               [chat.controller]))
    report["timings"]["crash_to_idle_s"] = round(time.monotonic() - started, 3)
    check("crash_binding_marked_ended", wait_until(lambda: worker_routing.bound(chat.controller) is None, 10,
                                                   [chat.controller]))
    check("crash_file_handed_back", wait_until(lambda: not worker_journal_owner.hosted(chat.session), 10))
    results = session_journal.read(chat.controller.store.path)[0]["results"]
    record = next((value for name, value in results.items() if name == key or name.endswith("/" + key)), None)
    check("crash_call_not_completed", record is None or record.get("phase") != "completed", record)
    check("crash_turn_interrupted", (chat.controller.turn_outcome or ("",))[0] in ("interrupted", "failed"),
          chat.controller.turn_outcome)
    check("crash_model_child_ended", wait_until(lambda: not chat.runtime_children(), 10))
    chat.stop()


def stop_busy_worker():
    """The person's Stop while the worker runs a long call: the turn ends, the model child is gone or stopped, and
    the chat takes the next message."""
    chat = HostedWorkerChat(STATE, PARTS / "stop.blend", ROOT)
    key, answers, busy = busy_script(chat)
    check("stop_call_was_running", busy)
    started = time.monotonic()
    chat.controller.stop()
    check("stop_turn_ends", wait_until(lambda: not chat.controller.busy, 30, [chat.controller]),
          chat.controller.status)
    report["timings"]["stop_to_idle_s"] = round(time.monotonic() - started, 3)
    report["stop_outcome"] = list(chat.controller.turn_outcome or ())
    report["stop_child_alive_after"] = bool(chat.runtime_children())
    check("stop_nothing_answered_as_done", not [row for row in chat.model_log("tool_result") if row.get("id") == key
                                                and row.get("success")], chat.model_log("tool_result"))
    # The worker's main thread still runs the script (Python cannot be interrupted there), so its next message is
    # taken once that call ends: how long that took is recorded (known U8).
    started, after = time.monotonic(), None
    while after is None and time.monotonic() - started < 120:
        after = chat.call("angelo_create_cube", {"name": "After Stop", "size": 1, "location": [0, 0, 0]})
        if after.get("error_code") == "NOT_SENT":
            report.setdefault("stop_next_message_refusals", []).append(after["error"][-120:])
            after = None
    report["timings"]["stop_next_message_taken_s"] = round(time.monotonic() - started, 3)
    check("stop_chat_works_on", isinstance(after, dict) and after.get("success") is True, after)
    chat.stop()


def consent_revocation():
    """Auto narrowed to Ask with Angelo's own approval control while the worker chat is shown: the next edit asks."""
    chat = HostedWorkerChat(STATE, PARTS / "consent.blend", ROOT)
    auto = chat.call("angelo_create_cube", {"name": "Auto Cube", "size": 1, "location": [0, 0, 0]})
    check("consent_auto_edit_runs", auto.get("success") is True, auto)
    shown = ui._sessions.active
    ui._sessions.activate(chat.session)
    try:
        with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
            done = bpy.ops.angelo.set_approval(mode="ASK")
    finally:
        ui._sessions.activate(shown)
    check("consent_toggle_returned", done == {'FINISHED'}, done)
    key, answers = chat.request("angelo_create_cube", {"name": "Asked Cube", "size": 1, "location": [2, 0, 0]})
    card = chat.wait_for_approval(key, answers)
    check("consent_revoked_edit_asks", card is not None)
    chat.decline(card)
    chat.wait(answers)
    chat.wait_idle()
    check("consent_revoked_edit_never_ran", "Asked Cube" not in chat.objects())
    chat.stop()


def unacknowledged_revocation():
    """A revocation the worker cannot acknowledge (it is frozen) fails closed: within the 2 s deadline the chat is
    marked as needing approval and its model child is ended, and nothing more runs."""
    import signal
    from angelo_agent import worker_consent_lead
    if not hasattr(signal, "SIGSTOP"):
        report["unacknowledged_revocation"] = "skipped: no SIGSTOP on this platform"
        return
    chat = HostedWorkerChat(STATE, PARTS / "frozen-consent.blend", ROOT)
    chat.say("Keep working.", [{"hold": "work"}, {"call": {"id": "after-freeze", "tool": "angelo_create_cube",
                                                           "arguments": {"name": "Never", "size": 1,
                                                                         "location": [0, 0, 0]}}}])
    check("frozen_consent_child_running", bool(chat.runtime_children()))
    pid = chat.worker.process.pid
    os.kill(pid, signal.SIGSTOP)
    try:
        shown = ui._sessions.active
        ui._sessions.activate(chat.session)
        started = time.monotonic()
        try:
            with bpy.context.temp_override(window=bpy.context.window_manager.windows[0]):
                bpy.ops.angelo.set_approval(mode="ASK")
        except RuntimeError as error:   # The control's own refusal: recorded; the deadline must still hold.
            report["revocation_toggle_error"] = str(error)[-300:]
        finally:
            ui._sessions.activate(shown)
        report["timings"]["revocation_toggle_returned_s"] = round(time.monotonic() - started, 3)
        marked = wait_until(lambda: worker_consent_lead.needs_approval(chat.session), 5, [chat.controller])
        report["timings"]["revocation_marked_s"] = round(time.monotonic() - started, 3)
        check("frozen_consent_marked_needing_approval", marked, report.get("revocation_toggle_error"), soft=True)
        check("frozen_consent_child_ended", wait_until(lambda: not chat.runtime_children(), 5, [chat.controller]),
              soft=True)
    finally:
        os.kill(pid, signal.SIGCONT)
    chat.release("work")
    wait_until(lambda: not chat.controller.busy, 30, [chat.controller])
    check("frozen_consent_nothing_ran_after", not [row for row in chat.model_log("tool_result")
                                                    if row.get("id") == "after-freeze" and row.get("success")])
    chat.stop()


def run_checks():
    PARTS.mkdir(parents=True, exist_ok=True)
    bpy.context.preferences.edit.use_global_undo = True
    _register(bpy, ui)   # The approve operator, as the window has it.
    HostedWorkerChat.operators()   # Review, Decline and the approval mode, as the window has them.
    if not hosted():
        report.update(success=True, skipped="ANGELO_WORKER_CHAT_HOST=0: lanes; covered by the other suites' lanes "
                                            "variants")
        return
    before = main_scene()
    for case in (lambda: dispatch(before).stop(), python_consent, merge_hold_and_lease, switch_reopen_with_resume, pool_reuse, worker_crash_mid_call,
                 stop_busy_worker, consent_revocation, unacknowledged_revocation):
        started = time.monotonic()
        try:
            case()
        except AssertionError as error:   # Each case on its own workers: one failure never hides the next.
            report.setdefault("failed_cases", {})[case.__name__.replace("<lambda>", "dispatch")] = str(error)
        except Exception:
            report.setdefault("failed_cases", {})[case.__name__.replace("<lambda>", "dispatch")] = \
                traceback.format_exc()[-1500:]
        report["timings"][getattr(case, "__name__", "dispatch").replace("<lambda>", "dispatch") + "_s"] = round(
            time.monotonic() - started, 2)
    check("lead_scene_never_changed", main_scene() == before, main_scene())
    report["success"] = not report.get("failed_cases")


try:
    run_checks()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    report["send_refusals_after_idle"] = hosted_worker_chat.SEND_REFUSALS   # Defect D3, counted, never hidden.
    try:
        worker_routing.stop_all()
    except Exception:
        report.setdefault("cleanup_error", traceback.format_exc())
(OUT / "worker-chat-hosted.json").write_text(json.dumps(report, indent=2, default=str))
print("WORKER-CHAT-HOSTED-DONE", report.get("success"))
if not report.get("success"):
    sys.stdout.flush()
    os._exit(1)
