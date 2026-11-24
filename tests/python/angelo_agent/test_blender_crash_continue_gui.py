# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Continue after Angelo closes (``crash_resume``, 2.1.0): Angelo killed mid-script, relaunched, the person's Continue.

Run outside Blender, as the runner does: ``python3 this_file.py --binary <Angelo> --out <new folder>``. Two GUI
launches of the same binary in Director, the default view, with one isolated profile and a stand-in ``claude`` CLI
playing scripted rounds (``fake_provider_cli.py``: no account, network or inference):

1. **work**: a saved project; the person's request; the stand-in moves the Cube (Auto runs it: done), then asks for a
   script; the person approves it on its card; the script writes one line to a marker file and hangs. The driver
   kills the whole process (SIGKILL: nothing of Angelo's runs after it), as a crash or a forced quit would.
2. **continue**: the same project reopened. The chat's card says what the call journal shows (done 1, may have
   applied 1) with **Continue**; the person clicks it; Angelo's own request reaches the model with the journal's
   facts, the model reads the scene, then replies. The script never runs again (the marker keeps one line).

Check ``crash-continue.json`` for ``success``; each launch writes ``<phase>.json`` with its checks and screenshots.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import argparse
import json
import signal
import subprocess
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import native_suite_paths  # noqa: E402  (standard library only)

MARKER_NAME, EMPTY = "script-ran.txt", "QA Crash Script"
REPLY = "Read the scene and finished the request."


def run_host():
    import gui_launch
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    root = native_suite_paths.fresh_fixture_root(args.out)
    binary = Path(args.binary).resolve(strict=True)
    root.mkdir(mode=0o700)
    report = {"success": False, "phases": {}, "binary": str(binary), "paid_requests": 0}
    environ = {key: value for key, value in os.environ.items() if key != "ANGELO_DISABLE_DEFAULT_CHAT"}

    def launch(phase, project=None):
        command = [str(binary), '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                   *gui_launch.window_flags(), *([str(project)] if project else []),
                   '--python', str(Path(__file__).resolve()),
                   '--', '--native-phase', phase, '--fixture-root', str(root)]
        log = (root / (phase + '.log')).open('w')
        return subprocess.Popen(command, env=environ, stdout=log, stderr=subprocess.STDOUT, start_new_session=True), log

    def result(phase):
        path = root / (phase + '.json')
        return json.loads(path.read_text()) if path.is_file() else {"success": False, "error": "no report"}

    try:
        child, log = launch('work')
        marker, deadline = root / MARKER_NAME, time.monotonic() + 240
        while not marker.is_file() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.2)
        # The marker's line is the script running: its journal record was written before it ran, so it is durable.
        report["killed_mid_script"] = marker.is_file() and child.poll() is None
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGKILL)
        child.wait(timeout=30)
        log.close()
        report["phases"]["work"] = result('work')
        report["work_exit"] = child.returncode
        child, log = launch('continue', root / 'project.blend')
        try:
            child.wait(timeout=300)
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=30)
            log.close()
        report["phases"]["continue"] = done = result('continue')
        text = (root / 'continue.log').read_text(errors='replace')
        fatal = ('Segmentation fault', 'Traceback (most recent call last)')
        report["fatal_log_markers"] = [found for found in fatal if found in text]
        report["script_runs"] = len(marker.read_text().splitlines()) if marker.is_file() else 0
        report["success"] = (report["killed_mid_script"] and report["work_exit"] == -signal.SIGKILL
                             and done.get("success") is True and child.returncode == 0 and report["script_runs"] == 1
                             and not report["fatal_log_markers"])
    except Exception as error:   # noqa: BLE001 - reported
        report.update(error=str(error), traceback=traceback.format_exc())
    finally:
        (root / 'crash-continue.json').write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps({"success": report["success"]}), flush=True)


if '--native-phase' not in sys.argv:
    if __name__ == '__main__':
        run_host()
    raise SystemExit


import bpy  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument('--native-phase', required=True, choices=('work', 'continue'))
parser.add_argument('--fixture-root', required=True)
args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
ROOT, PHASE = Path(args.fixture_root).resolve(), args.native_phase
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402
import qa_input  # noqa: E402
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')
ROUNDS = ROOT / "rounds"
if PHASE == "work":
    ROUNDS = fake_provider_cli.fresh(ROUNDS)
    ROUNDS.mkdir(parents=True, exist_ok=True)
    fake_provider_cli.sign_in(os.environ["HOME"], "claude")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(ROOT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_ROUNDS=str(ROUNDS))
os.environ["PATH"] = str(fake_provider_cli.install(ROOT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
CODE = ("import bpy, pathlib, time\nwith open(%r, 'a') as marker:\n    marker.write('ran\\n')\n"
        "bpy.data.objects.new(%r, None)\ntime.sleep(600)\n" % (str(ROOT / MARKER_NAME), EMPTY))
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": []}
checks, started = state["checks"], time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def hits():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {}).get("hits", [])


def point(action, value=None):
    _, region = chat()
    rect = next((r for r, a, v in hits() if a == action and (value is None or v == value)), None)
    return None if rect is None else qa_input.chat_point(region, rect)   # Scale and right-to-left, as the chat maps them.


def click(action, value=None):
    """Through the window, aimed for an approval's own control (``qa_input.window_click``; grant_guard.SHIELDED)."""
    from angelo_agent import chat_view
    _, region = chat()
    rect = next((r for r, a, v in hits() if a == action and (value is None or v == value)), None)
    assert rect is not None, "not drawn: %s %s" % (action, value)
    qa_input.window_click(window(), region, rect, action, scale=chat_view.ui_scale())


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def played():
    counter = ROUNDS / "next"
    return int(counter.read_text()) if counter.is_file() else 0


def script(index, plan):
    (ROUNDS / ("round-%d.json" % index)).write_text(json.dumps([{
        "type": "result", "subtype": "success", "is_error": False, "session_id": "fixture-crash", "duration_ms": 900,
        "structured_output": plan}]), encoding="utf-8")


def move_cube(height):
    from angelo_agent import scene_queries
    cube = window().scene.objects["Cube"]
    return {"reply": "", "operations": [{"tool": "angelo_scene_program", "arguments": {
        "mode": "APPLY", "read_set": [],
        "write_set": [{"handle": scene_queries.object_handle(cube),
                       "revision": scene_queries.object_revision(window(), cube)}],
        "operations": [{"kind": "transform", "target": scene_queries.object_handle(cube),
                        "parameters": {"location": [0, 0, height]}}]}}]}


def capture(name):
    path = ROOT / ("%s-%s" % (PHASE, name))
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(path.name)


def save():
    (ROOT / (PHASE + ".json")).write_text(json.dumps(state, indent=2, default=str))


def diagnose():
    """What the chat and scene held when a phase failed."""
    try:
        c = controller()
        state["diagnosis"] = {
            "objects": sorted(o.name for o in bpy.data.objects), "busy": bool(c.busy), "played": played(),
            "pending": [{"id": k[-12:], "tool": getattr(v, "name", ""), "approval": bool(v.approval),
                         "executing": bool(getattr(v, "executing", False))} for k, v in c.pending.items()],
            "rows": [{k: (str(v)[:160] if k == "text" else v) for k, v in m.items()
                      if k in ("role", "text", "notice", "tool", "status", "request_kind")}
                     for m in c.state["messages"][-8:]]}
    except Exception as failure:   # noqa: BLE001 - a diagnosis never hides the error
        state["diagnosis_error"] = repr(failure)


def finish(error=None):
    if error:
        diagnose()
        state.update(success=False, error=str(error), traceback=traceback.format_exc(),
                     actions=sorted({(a, str(v)) for _, a, v in hits()}))
    else:
        state.update(phase="complete", success=all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    bpy.ops.wm.quit_blender()


def wait(condition, limit=60):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def go(phase):
    state["phase"] = phase
    save()
    return .3


def connected(ui, quick_connect, c):
    return quick_connect.flow("claude_subscription") is None and c.models and ui.connection_available()


def work(phase, c, ui, quick_connect):
    if phase == "boot":
        if not wait(lambda: not ui._authentication.running and point("welcome_connect", "claude_subscription")):
            return .2
        click("welcome_connect", "claude_subscription")
        return go("connected")
    if phase == "connected":
        if not wait(lambda: connected(ui, quick_connect, c)):
            return .2
        bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "project.blend"))
        plan = move_cube(1.5)   # One plan: the move runs (Auto: bounded edit), then the script asks.
        plan["operations"].append({"tool": "angelo_execute_python", "arguments": {
            "code": CODE, "description": "Add an empty named " + EMPTY}})
        script(0, plan)
        area, region = chat()
        area.spaces.active.history[-1].body = "Lift the cube, then add an empty with a script."
        x, y = region.x + region.width // 2, region.y + 60
        for kind, value in (('MOUSEMOVE', 'NOTHING'), ('RET', 'PRESS'), ('RET', 'RELEASE')):
            window().event_simulate(type=kind, value=value, x=x, y=y)
        return go("card")
    if phase == "card":
        call = next((call for call in c.pending.values() if call.approval), None)
        if not wait(lambda: call is not None and point("approve", call.id)):
            return .2
        checks["the_typed_move_ran_first"] = abs(window().scene.objects["Cube"].location.z - 1.5) < 1e-4
        capture("script-card.png")
        state["call"], state["clicks"], state["clicked_at"] = call.id, 0, 0.0
        return go("approving")
    if phase == "approving":   # Review & approve opens the code first; the person then clicks Approve.
        call = c.pending.get(state["call"])
        if call is None or getattr(call, "executing", False):
            return go("running")   # The driver kills the process once the script has written its line.
        # An aimed click waits out the shield first (qa_input.aim): a retry only after that and the card's redraw.
        if point("approve", call.id) and time.monotonic() - state["clicked_at"] > 4.0:
            assert state["clicks"] < 3, "Approve took no effect after three clicks"
            if state["clicks"] == 1:
                capture("script-card-reviewed.png")
            click("approve", call.id)
            state["clicks"], state["clicked_at"] = state["clicks"] + 1, time.monotonic()
        return .3
    return .2


def resume(phase, c, ui, quick_connect):
    from angelo_agent import crash_resume_ui, request_rows
    if phase == "boot":
        if not wait(lambda: connected(ui, quick_connect, c) and point(crash_resume_ui.ACTION)):
            return .2
        card = next(m for m in c.state["messages"] if m.get("notice")
                    and (m.get("text") or "").startswith("Angelo closed while it was working"))
        checks["the_card_counts_the_journal"] = "Done: 1" in card["text"] and "May have applied: 1" in card["text"]
        checks["the_document_is_the_saved_project"] = Path(bpy.data.filepath).name == "project.blend"
        from angelo_agent import activity_text   # The interrupted script's step: Stopped, never Needs you.
        steps = {m.get("tool"): activity_text.step_status(m) for m in c.state["messages"]
                 if m.get("role") == "activity"}
        checks["the_cut_off_steps_read_stopped"] = steps.get("angelo_execute_python") == "stopped"
        capture("continue-card.png")
        script(1, {"reply": REPLY, "operations": [{"tool": "angelo_inspect_scene", "arguments": {}}]})
        click(crash_resume_ui.ACTION)
        return go("continued")
    if phase == "continued":
        if not wait(lambda: not c.busy and any(m.get("tool") == "angelo_inspect_scene"
                                               and m.get("status") == "completed" for m in c.state["messages"])):
            return .2
        state["reply_row"] = any(REPLY in (m.get("text") or "") for m in c.state["messages"])
        prompt = (ROUNDS / "prompt-1.txt").read_text(encoding="utf-8")
        facts = prompt[prompt.find("Angelo closed while it was working"):]   # Each call by its tool and arguments.
        checks["the_model_got_the_journal_facts"] = (
            facts.startswith("Angelo closed") and "done: angelo_scene_program" in facts
            and "may have applied, with no final read-back: angelo_execute_python" in facts
            and "did not run: none" in facts and "read the current scene with a read tool" in facts)
        rows = [m for m in c.state["messages"] if m.get("request_kind") == request_rows.CONTINUE]
        checks["the_request_is_angelos_own_with_the_counts"] = [m.get("parts") for m in rows] == [[1, 1, 0]]
        checks["the_card_is_gone"] = point(crash_resume_ui.ACTION) is None
        capture("continued.png")
        finish()
        return None
    return .2


def tick():
    try:
        assert time.monotonic() - started < 240, "Crash continue fixture timed out in " + state["phase"]
        from angelo_agent import director, quick_connect, ui
        if not director.is_director(window()) or not qa_boot.started():
            return .2
        c = ui.get_controller()
        return (work if PHASE == "work" else resume)(state["phase"], c, ui, quick_connect)
    except Exception as error:
        finish(error)
        return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5, persistent=True)
