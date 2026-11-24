# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A folder attached as context is still read after an agent script has run, in Director, with a person's clicks.

C's ``may_write`` refuses a person's project folder once any agent script has run in the process, and the folder's
anchored walk asked it: every read failed for the rest of such a session (the context-folders break-it review's third
read, SHOULD 1). Each walk is now judged by the folder's own grant in C (``context_folder.ReadOnlyFolder._granted``).

Real default startup, a stand-in ``codex`` app-server whose scripted turns make the agent's tool calls
(``fake_provider_cli.py``: no account, network or inference), synthetic window events where a person acts:

1. One folder, outside the temporary folder, attached through + > Folder as context… and Blender's file browser.
2. An agent script runs, approved on its card by the person's press.
3. Then LIST, READ, SEARCH and VIEW of that folder each work.

Run through ``run_native_suites.py --gui --include test_blender_context_folder_script_gui``; check
``context-folder-script-gui.json`` for ``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import base64
import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-context-folder-script-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402
import qa_input  # noqa: E402
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')
fake_provider_cli.sign_in(os.environ["HOME"], "codex")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_CODEX_TURNS="ok")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "context-folder-script-gui.json"
FOLDER = OUT / "castle project"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
READS = (("list", {"action": "LIST"}), ("read", {"action": "READ", "path": "README.md"}),
         ("search", {"action": "SEARCH", "query": "walls"}), ("view", {"action": "VIEW", "path": "art/wall.png"}))
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "observed": {},
         "fixture": "Default Director startup, stand-in codex app-server making scripted tool calls, synthetic "
                    "window events; no provider, account, network or inference"}
checks, started = state["checks"], time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def hits():
    return view_state().get("popup_hits", []) + view_state().get("hits", [])


def point(action=None, value=None):
    _, region = chat()
    rect = next((r for r, a, v in hits() if (action is None or a == action) and (value is None or v == value)), None)
    return None if rect is None else qa_input.chat_point(region, rect)


def click(action=None, value=None):
    from angelo_agent import chat_view
    _, region = chat()
    found = next(((r, a) for r, a, v in hits() if (action is None or a == action) and (value is None or v == value)),
                 None)
    assert found is not None, "not drawn: %s %s" % (action, value)
    qa_input.window_click(window(), region, found[0], found[1], scale=chat_view.ui_scale())


def send(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text
    x, y = region.x + region.width // 2, region.y + 60
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    for value in ('PRESS', 'RELEASE'):
        window().event_simulate(type='RET', value=value, x=x, y=y)


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def idle():
    c = controller()
    return not c.busy and not (c._worker and c._worker.is_alive()) and not c.pending


def capture(name, win=None):
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=win or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(OUT / name))
    state["screenshots"].append(name)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def turn_requests():
    path = OUT / "fake-cli" / "codex-requests.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    return [row for row in rows if row["method"] == "turn/start"]


def rows(tool):
    return [m for m in controller().state["messages"] if m.get("tool") == tool]


def finish(error=None):
    if error:
        try:
            state["rows"] = [{k: str(v)[:300] for k, v in m.items() if k in ("role", "tool", "status", "result")}
                             for m in controller().state["messages"][-8:]]
            capture("failure.png")
        except Exception as failure:   # noqa: BLE001 - a diagnosis never hides the error
            state["diagnosis_error"] = repr(failure)
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CONTEXT_FOLDER_SCRIPT_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}),
          flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=45):
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


def file_browser():
    for win in bpy.context.window_manager.windows:
        area = next((a for a in win.screen.areas if a.type == 'FILE_BROWSER'), None)
        if area is not None:
            return win, area
    return None


def browser_directory():
    directory = file_browser()[1].spaces.active.params.directory
    return directory.decode("utf-8", "surrogateescape") if isinstance(directory, bytes) else directory


def directory_field():
    from _bpy import _angelo_jobs
    win, _area = file_browser()
    buttons = [(idname, x, y, w, h) for pointer, _block, _text, idname, x, y, w, h in _angelo_jobs.region_buttons()
               if pointer == win.as_pointer() and w > 0 and h > 0]
    parent = next(((x, y) for idname, x, y, _w, _h in buttons if idname == "FILE_OT_parent"), None)
    if parent is None:
        return None
    row = [(w, (x, y, w, h)) for idname, x, y, w, h in buttons if not idname and y == parent[1] and x > parent[0]]
    return max(row)[1] if row else None


def attach_step():
    """One step of + > Folder as context… > the browser's directory field > Accept, as the person does it."""
    step = state.setdefault("attach_step", "menu")
    if step == "menu":
        if wait(lambda: point("attach") and file_browser() is None):
            click("attach")
            state["attach_step"] = "row"
    elif step == "row":
        if wait(lambda: point(value="context_folder")):
            click(value="context_folder")
            state["attach_step"] = "browser"
    elif step == "browser":
        if wait(lambda: file_browser() is not None and directory_field() is not None):
            win, _area = file_browser()
            bpy.context.window_manager.clipboard = str(FOLDER)   # The person copied it.
            x, y, w, h = directory_field()
            qa_input.press(win, x + w / 2, y + h / 2)
            state["attach_step"] = "keys"
    elif step == "keys":
        win, _area = file_browser()
        x, y, w, h = directory_field() or (0, 0, 0, 0)
        for kind, mods in (("A", {"ctrl": True}), ("V", {"ctrl": True}), ("RET", {})):
            for value in ('PRESS', 'RELEASE'):
                win.event_simulate(type=kind, value=value, x=int(x + w / 2), y=int(y + h / 2), **mods)
        state["attach_step"] = "typed"
    elif step == "typed":
        if wait(lambda: Path(browser_directory()) == FOLDER
                and qa_input.region_button(file_browser()[0], "file.execute") is not None):
            win, _area = file_browser()
            capture("cfs-1-browser.png", win)
            x, y, w, h = qa_input.region_button(win, "file.execute")
            qa_input.press(win, x + w / 2, y + h / 2)
            state.pop("attach_step")
            return go("attached")
    return .3


def tick():
    try:
        assert time.monotonic() - started < 360, "Context folder script fixture timed out in " + state["phase"]
        from angelo_agent import context_folder, context_folder_host as host, director, quick_connect, ui
        phase, c = state["phase"], controller()
        if phase == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            offer = ("welcome_connect", "codex_subscription")
            if not wait(lambda: not ui._authentication.running and point(*offer)):
                return .2
            click(*offer)
            return go("connected")
        if phase == "connected":
            if not wait(lambda: quick_connect.flow("codex_subscription") is None and ui.connection_available()
                        and c.selection_provider == "codex_subscription" and c.selection_models):
                return .2
            (FOLDER / "art").mkdir(parents=True, exist_ok=True)
            (FOLDER / "README.md").write_text("Castle game\nWalls are 4 m tall.\n", encoding="utf-8")
            (FOLDER / "art" / "wall.png").write_bytes(PNG)
            checks["the_folder_is_outside_the_temporary_folder"] = not str(FOLDER).startswith(
                os.path.realpath(tempfile.gettempdir()))
            return go("attach")
        if phase == "attach":
            return attach_step()
        if phase == "attached":
            if not wait(lambda: host.folder_of(c) is not None and point(host.REMOVE_ACTION)):
                return .2
            checks["the_folder_is_attached"] = Path(host.folder_of(c)).name == FOLDER.name
            capture("cfs-2-attached.png")
            fake_provider_cli.script_codex_turns(["call_approval"], OUT / "fake-cli")
            state["observed"]["turns_before_script"] = len(turn_requests())
            send("Name the scene's objects")
            return go("script_card")
        if phase == "script_card":   # The person's Approve on the script's card (retried as an aimed click is).
            seen = state["observed"]
            call = next((call for call in c.pending.values() if call.name == "angelo_execute_python" and call.approval
                         and not getattr(call, "executing", False)), None)
            if call is None:
                if seen.get("script_clicks") and idle():
                    return go("script_ran")
                wait(lambda: False)   # Times out with the rows if the card never comes.
                return .2
            if point("approve", call.id) and time.monotonic() - seen.get("clicked_at", 0.0) > 4.0:
                assert seen.get("script_clicks", 0) < 3, "the script's Approve took no effect after three clicks"
                if not seen.get("script_clicks"):
                    capture("cfs-3-script-card.png")
                click("approve", call.id)
                seen["script_clicks"], seen["clicked_at"] = seen.get("script_clicks", 0) + 1, time.monotonic()
            return .3
        if phase == "script_ran":
            if not wait(lambda: idle() and len(turn_requests()) > state["observed"]["turns_before_script"]):
                return .2
            ran = rows("angelo_execute_python")
            checks["an_agent_script_ran_first"] = bool(ran) and ran[-1].get("status") == "completed"
            state["reads"] = 0
            return go("read")
        if phase == "read":   # Each read in a turn of its own, after the script.
            index = state["reads"]
            if index == len(READS):
                capture("cfs-4-reads-after-script.png")
                finish()
                return None
            name, arguments = READS[index]
            if state.get("reading") != name:
                fake_provider_cli.script_codex_call(name, context_folder.TOOL, arguments, OUT / "fake-cli")
                fake_provider_cli.script_codex_turns(["call:" + name], OUT / "fake-cli")
                state["reading"], state["before"] = name, len(rows(context_folder.TOOL))
                send("Look in my folder (%s)" % name)
                return .3
            if not wait(lambda: idle() and len(rows(context_folder.TOOL)) > state["before"]):
                return .2
            result = rows(context_folder.TOOL)[-1].get("result") or {}
            state["observed"][name] = {k: str(v)[:300] for k, v in result.items()}
            checks["%s_works_after_a_script" % name] = not result.get("error") and {
                "list": lambda: any(e.get("name") == "README.md" for e in result.get("entries", [])),
                "read": lambda: "Walls are 4 m tall." in str(result.get("text")),
                "search": lambda: any(h.get("path") == "README.md" for h in result.get("hits", [])),
                "view": lambda: bool(result.get("views")),
            }[name]()
            state["reads"] = index + 1
            state.pop("reading")
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5, persistent=True)
