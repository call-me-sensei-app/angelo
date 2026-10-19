# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exact-binary disposable native regressions, zero account/inference calls.

Run Angelo --background --factory-startup --disable-autoexec
--python-exit-code 1 --python this_file. Reports go only to a private temp root.
"""

import json
import os
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import patch

os.environ["ANGELO_DISABLE_DEFAULT_CHAT"] = "1"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import bpy
from angelo_agent.blender_tools import BlenderExecutor
from angelo_agent.codex_delivery import STALE_CALL
from angelo_agent.controller import Controller, ToolDispatchBudget
from angelo_agent.memory_projects import admit_saved_project, capture_loaded_project
from angelo_agent.providers import CodexProvider
from angelo_agent.task_memory import read_memory, update_memory

assert "--factory-startup" in sys.argv and "--background" in sys.argv
assert Path(bpy.app.binary_path).name.lower() in {"angelo", "angelo.exe"}, bpy.app.binary_path
assert bpy.app.build_options.opensubdiv, "Use an Angelo build with OpenSubdiv enabled"
expected_binary = os.environ.get("ANGELO_EXPECTED_BINARY")
if expected_binary:
    assert Path(bpy.app.binary_path).resolve() == Path(expected_binary).resolve(), bpy.app.binary_path
OUT = Path(os.environ.get("ANGELO_RUNTIME_QA_DIR", str(Path(tempfile.gettempdir()) / "angelo-runtime-fixes-native")))
OUT.mkdir(parents=True, exist_ok=True)
TEMP = tempfile.TemporaryDirectory(prefix="isolated-", dir=OUT)
DIRECTORY = Path(TEMP.name)
REPORT = {"binary": bpy.app.binary_path, "version": bpy.app.version_string,
          "opensubdiv": bool(bpy.app.build_options.opensubdiv), "paid_requests": 0, "checks": {}}
controllers = []


def check(name, condition):
    REPORT["checks"][name] = bool(condition)
    assert condition, name


def document():
    return str(uuid.uuid4()), bpy.context.window.scene.session_uid


def controller(case, doc=None, *, session_id=None):
    value = Controller(DIRECTORY / case, session_id=session_id or str(uuid.uuid4()))
    value.set_document(doc or document())
    controllers.append(value)
    return value


def executor():
    return BlenderExecutor(bpy.context.window)


def pump(c, native):
    c.pump_tools(native, c.document, budget=ToolDispatchBudget(seconds=1),
        apply_mutation=lambda call: c.execute_tool(call, native, c.document))


def saved_receipt(c, path):
    profile = str(c.store.directory.resolve())
    loaded = capture_loaded_project(bpy.context.window, c.document, profile)
    return admit_saved_project(path, c.document, profile, loaded_project=loaded)


def brief(c):
    c.state = update_memory(c.state, {"expected_revision": c.state["task_memory"]["revision"],
        "patch": {"objective": "Retain this verified fixture project", "constraints": ["No implicit destructive cleanup"]}},
        c.document, provenance={"kind": "approved_update", "id": "host:fixture-human"},
        profile_id=str(c.store.directory.resolve()))
    c.save()


class RPC:
    def __init__(self):
        self.closed, self.responses, self.requests = threading.Event(), [], []
    def respond(self, identity, payload):
        self.responses.append((identity, payload))
    def request(self, method, params, **kwargs):
        self.requests.append((method, params))
        return {}
    def close(self):
        self.closed.set()


# The real provider handler/routing and real native executor, not provider.run.
c = controller("turn-fence")
c.state["native_id"] = "fixture-thread"
c.state["options"]["approval"] = "AUTO_BOUNDED"
c._begin_metrics()
c.busy = True
old = c.runtime_context()
p = CodexProvider(old, {})
p.rpc, p.thread_id = RPC(), "fixture-thread"
p.bind_request(old)
p._scope.bind_turn("old-turn")
c.stop()
c._begin_metrics()
c.busy, c.cancelled = True, False
current = c.runtime_context()
p.bind_request(current)
p._scope.bind_turn("new-turn")
native, before = executor(), len(bpy.context.scene.objects)
arguments = {"name": "Native Fenced Cube", "size": 1, "location": [0, 0, 0]}
p._request("late-rpc", "item/tool/call", {"threadId": "fixture-thread", "turnId": "old-turn",
    "callId": "unseen-old-id", "tool": "angelo_create_cube", "arguments": arguments})
pump(c, native)
check("old_context_stays_cancelled_after_next_turn", old.cancelled)
check("late_old_turn_does_not_change_real_scene", len(bpy.context.scene.objects) == before and not c.pending)
check("late_transport_request_returns_failure", p.rpc.responses[-1][1]["success"] is False)
p._event("turn/completed", {"threadId": "fixture-thread", "turn": {"id": "old-turn", "status": "completed"}})
check("old_completion_cannot_finish_new_turn", not p.done.is_set())
p._request("current-rpc", "item/tool/call", {"threadId": "fixture-thread", "turnId": "new-turn",
    "callId": "current-native-id", "tool": "angelo_create_cube", "arguments": arguments})
pump(c, native)
check("current_turn_performs_actual_native_operation", len(bpy.context.scene.objects) == before + 1)
check("current_turn_native_result_delivered", p.rpc.responses[-1][1]["success"] is True)
p.close()

# Stop after a real staged commit cannot overwrite success during host cleanup.
from angelo_agent import native_programs, scene_queries
native_programs.register()
c = controller("commit-outcome")
c._begin_metrics()
c.busy = True
source = bpy.context.scene.objects["Cube"]
handle = scene_queries.object_handle(source)
program = {"mode": "APPLY", "read_set": [], "write_set": [{"handle": handle,
    "revision": scene_queries.object_revision(bpy.context.window, source)}], "operations": [
        {"kind": "transform", "target": handle, "parameters": {"location": [1.25, 0, 0]}}],
    "expected_checks": [{"kind": "transform", "target": handle, "property": "location",
                         "expected": [1.25, 0, 0], "tolerance": .00001}]}
native, committed = executor(), []
c.request_tool("staged-once", "angelo_scene_program", program, committed.append)
c.pump_tools(native, c.document)
call = next(iter(c.pending.values()))
c.approve_tool(call, native, c.document)
for _ in range(200):
    c.pump_tools(native, c.document, budget=ToolDispatchBudget(seconds=1))
    job = c.native_jobs.get(call.id)
    if job is not None and job.ready_to_commit:
        break
else:
    raise AssertionError("Native program was not ready: " + str(committed))
original_discard = c._discard_native_job
injected = False
def stop_during_completion(completed_call):
    global injected
    if not injected:
        injected = True
        check("native_stage_really_committed_before_stop", source.location.x == 1.25)
        c.stop()
    return original_discard(completed_call)
with patch.object(c, "_discard_native_job", side_effect=stop_during_completion):
    result = c.commit_native_job(call, native, c.document)
check("stop_after_native_commit_preserves_actual_success", len(committed) == 1 and committed[0]["success"]
      and committed[0]["scene_changed"] and not committed[0].get("cancelled") and result["success"] and source.location.x == 1.25)

# Actual Controller.send + warm native provider binding while admission yields.
from angelo_agent import controller as controller_module
for stop_case in (False, True):
    label = "stop" if stop_case else "idle"
    c = controller("admission-" + label)
    c.state["native_id"], c.state["options"]["approval"] = "warm-fixture-thread", "AUTO_BOUNDED"
    c._begin_metrics()
    c.busy = True
    old = c.runtime_context()
    p = CodexProvider(old, {})
    p.rpc, p.thread_id = RPC(), "warm-fixture-thread"
    p.bind_request(old)
    p._scope.bind_turn("admission-turn-a")
    c.provider = p
    old_rpc, old_metrics = p.rpc, c._request_metrics
    if not stop_case:
        old.emit("finished", {"status": "completed"})
        c.pump_events()
    entered, release, new_bound, finish_new = (threading.Event() for _ in range(4))
    original_check = controller_module.check_invocation
    failures = []
    def pause_guard(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_check(*args, **kwargs)
    def new_run(*args, **kwargs):
        current_context = c.runtime_context()
        c._connect_runtime(current_context)
        p._scope.bind_turn("admission-turn-b")
        new_bound.set()
        finish_new.wait(5)
    def old_request():
        try:
            p._request("admission-rpc-a", "item/tool/call", {"threadId": "warm-fixture-thread", "turnId": "admission-turn-a",
                "callId": "unseen-admission-a", "tool": "angelo_create_cube",
                "arguments": {"name": "Rejected Admission Race Cube", "size": 1, "location": [0, 0, 0]}})
        except Exception as error:
            failures.append(str(error))
    before = [(obj.name, tuple(obj.location)) for obj in bpy.context.scene.objects]
    worker = threading.Thread(target=old_request)
    with patch.object(controller_module, "check_invocation", side_effect=pause_guard), patch.object(c, "_run", side_effect=new_run):
        worker.start()
        if stop_case:
            assert entered.wait(5)
            c.stop()
            c.busy = False  # Prior host worker retired; no provider inference.
            c._finish_metrics("interrupted")
        else:
            # The finished request closed its delivery owner before "finished" was queued, so
            # its turn's late call never reaches admission (request_fences.DeliveryOwner). It is
            # still answered: codex app-server waits for every tool-call reply (codex_delivery).
            worker.join(5)
            check("idle_late_call_never_reaches_admission", not entered.is_set() and not worker.is_alive()
                  and not c.pending)
            check("idle_late_call_gets_one_stale_refusal", old_rpc.responses == [("admission-rpc-a", {
                "success": False, "contentItems": [{"type": "inputText", "text": STALE_CALL}]})])
            # Codex still has that turn open: it is interrupted so the next message cannot join it.
            p._scope.turns.join()
            check("idle_stale_turn_is_interrupted", old_rpc.requests.count(
                ("turn/interrupt", {"threadId": "warm-fixture-thread", "turnId": "admission-turn-a"})) == 1)
        c.send("New unrelated fixture human request")
        assert new_bound.wait(5)
        release.set()
        worker.join(5)
        check(label + "_admission_yield_keeps_genuine_warm_transport", c.provider is p and p.rpc is old_rpc and p.has_warm_runtime())
        if stop_case:
            # Admitted before Stop closed its owner: no later phase, its reply included. Stop
            # interrupts that turn, and codex app-server then cancels the waiting call itself.
            for _ in range(100):
                if any(method == "turn/interrupt" for method, _params in old_rpc.requests):
                    break
                time.sleep(.05)
            p._scope.turns.join()
            check("stop_admitted_call_sends_no_stale_reply_and_interrupts_its_turn", not failures
                  and not worker.is_alive() and old_rpc.responses == [] and old_rpc.requests.count(("turn/interrupt", {
                      "threadId": "warm-fixture-thread", "turnId": "admission-turn-a"})) == 1)
        else:
            check("idle_stale_refusal_is_the_only_reply", not failures and not worker.is_alive()
                  and len(old_rpc.responses) == 1)
        pump(c, executor())
        check(label + "_admission_race_does_not_execute_real_native_edit", not c.pending
              and before == [(obj.name, tuple(obj.location)) for obj in bpy.context.scene.objects])
        check(label + "_admission_never_charges_new_request_metrics", old.cancelled and c._request_metrics is not old_metrics
              and not c._request_metrics.snapshot()["counts"].get("tool_invocations"))
        finish_new.set()
        c._worker.join(5)

# An admitted call whose request ends without Stop or an overdue interrupt: its reply is
# withheld (never a "nothing was run" refusal), its turn is interrupted and the durable
# result says what actually happened (codex_delivery.close_orphaned_turn).
c = controller("orphaned-admitted")
c.state["native_id"], c.state["options"]["approval"] = "warm-fixture-thread", "AUTO_BOUNDED"
c._begin_metrics()
c.busy = True
old = c.runtime_context()
p = CodexProvider(old, {})
p.rpc, p.thread_id = RPC(), "warm-fixture-thread"
p.bind_request(old)
p._scope.bind_turn("orphan-turn")
c.provider = p
before = [(obj.name, tuple(obj.location)) for obj in bpy.context.scene.objects]
p._request("orphan-rpc", "item/tool/call", {"threadId": "warm-fixture-thread", "turnId": "orphan-turn",
    "callId": "orphan-call", "tool": "angelo_create_cube",
    "arguments": {"name": "Orphaned Admission Cube", "size": 1, "location": [0, 0, 0]}})
check("orphan_call_was_admitted", len(c.pending) == 1)
old.emit("finished", {"status": "failed"})  # E.g. the request failed after turn/start; no Stop.
c.busy = False
c._finish_metrics("failed")
pump(c, executor())
p._scope.turns.join()
stored = next((value["result"] for key, value in c.state["results"].items() if key.endswith("/orphan-call")), {})
after = [(obj.name, tuple(obj.location)) for obj in bpy.context.scene.objects]
REPORT["orphaned_admitted_result"] = stored
check("orphaned_admitted_call_withholds_its_reply", p.rpc.responses == [])
check("orphaned_admitted_call_interrupts_its_turn", p.rpc.requests == [
    ("turn/interrupt", {"threadId": "warm-fixture-thread", "turnId": "orphan-turn"})])
check("orphaned_admitted_result_matches_the_scene", not c.pending and bool(stored) and (
    (stored.get("success") is True and after != before) or ("error" in stored and after == before)))

# The next human message never joins an older open turn: the guard interrupts it and waits.
c = controller("turn-guard")
c.state["native_id"] = "guard-thread"
c._begin_metrics()
c.busy = True
context = c.runtime_context()
p = CodexProvider(context, {})
p.rpc, p.thread_id = RPC(), "guard-thread"
p.bind_request(context)
p._event("turn/started", {"threadId": "guard-thread", "turn": {"id": "older-open-turn"}})
order = []
def guarded_request(method, params, **kwargs):
    order.append((method, params.get("turnId")))
    if method == "turn/interrupt":
        p._event("turn/completed", {"threadId": "guard-thread", "turn": {"id": params["turnId"], "status": "interrupted"}})
        return {}
    still_open = p._scope.turns.open_on("guard-thread")
    p._event("turn/completed", {"threadId": "guard-thread", "turn": {"id": "fresh-turn", "status": "completed"}})
    return {"turn": {"id": "fresh-turn" if not still_open else "older-open-turn"}}
p.rpc.request = guarded_request
status = p.run("guard-thread", "A new human message")
check("guard_interrupts_the_older_turn_before_turn_start", order == [("turn/interrupt", "older-open-turn"),
                                                                     ("turn/start", None)])
check("guarded_message_gets_a_fresh_turn", status == "completed" and p.turn_id == "fresh-turn"
      and p._scope.turns.open_on("guard-thread") == [])
p.close()

# Stop during that guard, through the real send path: the wait ends at once, nothing is
# sent, the request finishes as interrupted and the warm connection stays usable.
c = controller("guard-stop")
c.state["native_id"] = "stop-thread"
c._begin_metrics()
c.busy = True
old = c.runtime_context()
p = CodexProvider(old, {})
p.rpc, p.thread_id = RPC(), "stop-thread"
p.bind_request(old)
c.provider = p
old.emit("finished", {"status": "completed"})
c.pump_events()
p._event("turn/started", {"threadId": "stop-thread", "turn": {"id": "unfinished-turn"}})  # Never completes.
rpc = p.rpc
c.send("A message the person stops while the earlier turn is ending")
deadline = time.monotonic() + 5
while ("turn/interrupt", {"threadId": "stop-thread", "turnId": "unfinished-turn"}) not in rpc.requests \
        and time.monotonic() < deadline:
    time.sleep(.01)
stopped_at = time.monotonic()
c.stop()
c._worker.join(5)
REPORT["guard_stop_seconds"] = round(time.monotonic() - stopped_at, 3)
c.pump_events()
check("guard_stop_ends_the_wait_at_once", not c._worker.is_alive() and REPORT["guard_stop_seconds"] < 1.5)
check("guard_stop_sends_nothing", "turn/start" not in [method for method, _ in rpc.requests])
check("guard_stop_finishes_as_interrupted", not c.busy and c.status == "Interrupted"
      and not any(message.get("role") == "error" for message in c.state["messages"]))
# The person is told, in their language, and their text is offered back to the input.
NOT_SENT = "Your message wasn't sent. Send it again when you're ready."
not_sent_rows = [m for m in c.state["messages"] if m.get("text") == NOT_SENT]
check("guard_stop_shows_one_translatable_notice", len(not_sent_rows) == 1 and not_sent_rows[0]["role"] == "system"
      and not_sent_rows[0].get("i18n") == [NOT_SENT, {}])
check("guard_stop_offers_the_text_back", c.unsent_draft == "A message the person stops while the earlier turn is ending")
if bpy.app.build_options.international:  # The notice is drawn from its template in the interface language.
    from angelo_agent import ui_text
    view = bpy.context.preferences.view
    language, translate = view.language, view.use_translate_interface
    view.use_translate_interface, view.language = True, "ja_JP"
    REPORT["not_sent_notice_ja"] = ui_text.display_notice(not_sent_rows[0])
    view.language, view.use_translate_interface = language, translate
    check("guard_stop_notice_is_translated", REPORT["not_sent_notice_ja"]
          == "メッセージは送信されませんでした。準備ができたらもう一度送信してください。")
# drafts.restore_unsent on a real Blender console line: an empty input gets the text, caret after it.
from angelo_agent.drafts import restore_unsent
console_window = bpy.context.window_manager.windows[0]
console_area = console_window.screen.areas[0]
previous_type = console_area.ui_type
console_area.ui_type = "CONSOLE"
with bpy.context.temp_override(window=console_window, area=console_area):
    bpy.ops.console.history_append(text="", current_character=0)
console_line = console_area.spaces.active.history[-1]
restored = restore_unsent(c, active=True, spaces=[console_line], saved={},
                          read=lambda line: (line.body, line.current_character), write=lambda line, draft: (
                              setattr(line, "body", draft[0]), setattr(line, "current_character", draft[1])))
REPORT["restored_draft"] = [console_line.body, console_line.current_character]
check("guard_stop_text_returns_to_an_empty_input", restored
      and console_line.body == "A message the person stops while the earlier turn is ending"
      and console_line.current_character == len(console_line.body.encode("utf-8")) and c.unsent_draft is None)
console_area.ui_type = previous_type
check("guard_stop_keeps_the_warm_connection", c.provider is p and p.rpc is rpc and not rpc.closed.is_set()
      and p.has_warm_runtime() and rpc.requests.count(("turn/interrupt", {"threadId": "stop-thread",
                                                                            "turnId": "unfinished-turn"})) == 1)
# Codex completes the interrupted turn; the message sent again gets a fresh turn.
p._event("turn/completed", {"threadId": "stop-thread", "turn": {"id": "unfinished-turn", "status": "interrupted"}})
resent = []
def resend_request(method, params, **kwargs):
    resent.append(method)
    p._event("turn/completed", {"threadId": "stop-thread", "turn": {"id": "resent-turn", "status": "completed"}})
    return {"turn": {"id": "resent-turn"}}
rpc.request = resend_request
c.send("The message sent again")
c._worker.join(10)
c.pump_events()
check("guard_stop_resend_gets_a_fresh_turn", resent == ["turn/start"] and p.turn_id == "resent-turn"
      and not c.busy and c.status == "Completed")
p.close()

# A real successful mutation followed by final storage failure delivers truth.
c = controller("outcome")
c._begin_metrics()
c.busy = True
native, replies, second = executor(), [], []
c.request_tool("once", "angelo_create_cube", {"name": "Native Durable Cube", "size": 1, "location": [2, 0, 0]}, replies.append)
c.pump_tools(native, c.document)
call = next(iter(c.pending.values()))
c.request_tool("once", "angelo_create_cube", call.arguments, second.append)
original_save = c.store.save
def fail_final(state):
    if state["results"].get(call.id, {}).get("phase") == "completed":
        raise OSError("Native fixture final-save failure")
    original_save(state)
c.store.save = fail_final
before = len(bpy.context.scene.objects)
c.execute_tool(call, native, c.document)
check("final_save_failure_does_not_hide_real_native_cube", len(bpy.context.scene.objects) == before + 1)
check("all_waiters_receive_actual_success_once", len(replies) == len(second) == 1 and replies == second and replies[0]["success"])
check("storage_failure_is_explicit_not_operation_failure", replies[0]["journal_durable"] is False and "error" not in replies[0])
from angelo_agent.sessions import read_transcript  # noqa: E402  (the saved base and every appended record)
disk = read_transcript(c.store.path)
check("durable_execution_start_and_id_guard_remain", disk["results"][call.id]["phase"] == "execution_started" and call.id in disk["invocations"]["entries"])
check("actual_outcome_completion_metrics_recorded", c._request_metrics.snapshot()["counts"]["tool_completions"] == 1)
check("actual_native_execution_records_main_thread_peak",
      c._request_metrics.snapshot()["phases"].get("main_thread_block_ms", 0) > 0)
c.emit("finished", {"status": "completed"})
c.pump_events()
check("failed_final_storage_does_not_strand_ui_busy", not c.busy and bool(c.persistence_warning))
resumed = controller("outcome", c.document, session_id=c.state["id"])
resumed.busy = True
restart_replies = []
resumed.request_tool("once", "angelo_create_cube", call.arguments, restart_replies.append)
pump(resumed, executor())
check("restart_never_repeats_undurable_completed_mutation", len(bpy.context.scene.objects) == before + 1 and restart_replies[0].get("replayed") and "error" in restart_replies[0])
c.store.save = original_save

# The bundled interpreter classifies an admitted reviewed script into fixed
# labels only; admission alone runs nothing in the scene.
c = controller("fallback")
c._begin_metrics()
c.busy = True
before = len(bpy.context.scene.objects)
c.request_tool("reviewed-script", "angelo_execute_python", {"description": "Bevel the cube",
               "code": "import bmesh\nbpy.context.object.modifiers.new('Bevel', 'BEVEL')"}, [].append)
counts = c._request_metrics.snapshot()["counts"]
check("reviewed_script_admission_counts_fixed_categories", counts["python_fallbacks"] == 1
      and counts.get("python_fallback_geometry") == 1 and counts.get("python_fallback_modifiers") == 1)
check("fallback_classification_executes_nothing", len(bpy.context.scene.objects) == before and len(c.pending) == 1)

# Actual .blend save/load continuity and actual scene handles, never guesses.
project = DIRECTORY / "original-project.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(project), check_existing=False)
c = controller("memory")
check("saved_project_admitted_from_actual_native_window", c.bind_project(saved_receipt(c, project)))
c.busy = True
readbacks = []
c.request_tool("actual-scene-read", "angelo_scene_search", {"limit": 10}, readbacks.append)
pump(c, executor())
handle = readbacks[0]["objects"][0]["object"]
check("memory_uses_real_native_scene_handle", handle.startswith("scn:") and handle in json.dumps(c.state["task_memory"]))
c.mark_memory_stale("undo")
check("undo_invalidation_removes_all_real_handle_values", handle not in json.dumps(c.state["task_memory"]))
c.busy = False
brief(c)
identity, original_scope = c.state["id"], dict(c.state["task_memory"]["scope"])
c.close()
controllers.remove(c)
bpy.ops.wm.open_mainfile(filepath=str(project), load_ui=False, use_scripts=False)
c = controller("memory", session_id=identity)
check("restart_initially_isolates_old_native_document", read_memory(c.state, c.document)["scope_mismatch"])
check("exact_saved_file_continuity_rebinds_public_brief", c.bind_project(saved_receipt(c, project)))
memory = read_memory(c.state, c.document)
check("restored_notes_keep_objective_and_no_authority", memory["brief"]["objective"] == "Retain this verified fixture project" and memory["authority"] == "none")
check("rehydrated_native_receipts_remain_stale", memory["stale"] and all(entry["stale"] and not entry["object_handles"] for entry in memory["verified_results"]))
check("no_old_scene_handle_or_private_project_path_leaks", handle not in json.dumps(memory) and str(project) not in json.dumps(memory))

# Large/changed projects have an explicit human-only notes transfer, no hashing
# or native side effect and no fake exact-continuity promise.
c.save()
c.close()
controllers.remove(c)
bpy.context.scene.objects.get("Cube").location.x += 1
bpy.ops.wm.save_as_mainfile(filepath=str(project), check_existing=False)
bpy.ops.wm.open_mainfile(filepath=str(project), load_ui=False, use_scripts=False)
changed = controller("memory", session_id=identity)
check("changed_saved_file_does_not_auto_attach_old_brief", not changed.bind_project(saved_receipt(changed, project)))
profile = str(changed.store.directory.resolve())
loaded = capture_loaded_project(bpy.context.window, changed.document, profile)
with patch("angelo_agent.memory_projects.MAX_PROJECT_BYTES", 16):
    try:
        admit_saved_project(project, changed.document, profile, loaded_project=loaded)
    except ValueError:
        check("large_file_limit_reports_unsupported_automatic_continuity", True)
    else:
        raise AssertionError("large file unexpectedly admitted")
before = [(obj.name, tuple(obj.location)) for obj in bpy.context.scene.objects]
review = changed.project_rebind_review()
check("explicit_human_exact_revision_rebind_available", changed.confirm_project_rebind(loaded, review["revision"]))
check("human_notes_transfer_does_not_touch_native_geometry", before == [(obj.name, tuple(obj.location)) for obj in bpy.context.scene.objects])
memory = read_memory(changed.state, changed.document)
check("human_transfer_never_grants_authority_or_saved_hash_proof", memory["authority"] == "none" and memory["stale"] and memory["scope"]["project"] is None)
try:
    changed.confirm_project_rebind({"approved": True}, memory["revision"])
except ValueError:
    check("json_cannot_confirm_project_identity", True)
else:
    raise AssertionError("JSON confirmation admitted")
try:
    capture_loaded_project(bpy.context.window, ("forged-runtime", changed.document[1] + 1), profile)
except ValueError:
    check("native_attestation_rejects_wrong_loaded_scene_identity", True)
else:
    raise AssertionError("wrong loaded scene identity admitted")

# Port post-load lifecycle failure to actual native snapshot loading. Essential
# in-memory revocation MUST finish before any (possibly failing) transcript save.
record, snapshot = changed.checkpoints.reserve(str(project))
bpy.ops.wm.save_as_mainfile(filepath=str(snapshot), copy=True, check_existing=False)
changed.checkpoints.complete(record["id"])
scope = changed.project_scope_for_restore(record["id"])
sibling = Controller(changed.store.directory, session_id=str(uuid.uuid4()))
sibling.set_document(changed.document)
controllers.append(sibling)
brief(sibling)
sibling_scope = sibling.project_scope_for_restore(record["id"], source_controller=changed)
changed.state["native_id"] = sibling.state["native_id"] = "prior-runtime-thread"
changed.state["runtime_sessions"] = {"codex_subscription": {"native_id": "prior-runtime-thread"}}
changed._begin_metrics()
old_context = changed.runtime_context()
cube = bpy.context.scene.objects.get("Cube")
snapshot_x = cube.location.x
cube.location.x += 7
bpy.ops.wm.open_mainfile(filepath=str(snapshot), load_ui=False, use_scripts=False)
restored_document = document()
with patch.object(changed, "save", side_effect=OSError("After actual native snapshot load")) as save:
    outcome = changed.rebind_after_restore(restored_document, record["id"], project_scope=scope)
    save.assert_not_called()
sibling_outcome = sibling.rebind_after_restore(restored_document, record["id"], project_scope=sibling_scope)
check("real_native_snapshot_loaded_before_lifecycle", bpy.context.scene.objects.get("Cube").location.x == snapshot_x)
check("post_load_fencing_completes_without_any_save", changed.document == restored_document and old_context.cancelled and changed.state["native_id"] is None)
check("all_chat_native_sessions_revoked", sibling.document == restored_document and sibling.state["native_id"] is None and not changed.state["runtime_sessions"])
check("live_same_project_recovery_retains_only_stale_public_notes", outcome["memory_rebound"] and sibling_outcome["memory_rebound"] and read_memory(changed.state, restored_document)["stale"])
check("restore_never_implicitly_starts_new_mutation", not changed.busy and not changed.pending and not sibling.busy)

for value in controllers:
    value.close()
REPORT["passed"] = len(REPORT["checks"])
(OUT / "runtime-recovery.json").write_text(json.dumps(REPORT, indent=2))
print("ANGELO_RUNTIME_RECOVERY_QA", json.dumps(REPORT), flush=True)
TEMP.cleanup()
