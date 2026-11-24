# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Several folders in one chat, in Director, with a person's clicks and keys (agent_chat.rst, "Folder as context").

Real default startup (Director layout and chat), a stand-in ``codex`` app-server whose scripted turns make the
agent's tool calls (``fake_provider_cli.py``: no account, network or inference). Every step a person takes is a
synthetic window event: a press on a drawn control (``qa_input``) or keys. The scenario:

1. **Two folders**: + then the footer's **Folder as context…** opens Blender's file browser; the path is pasted into
   its directory field and Accept pressed, twice. The first is the working folder, the second a reference folder,
   each with its own card.
2. **Switching**: the reference folder's **Make working folder** makes it the working one; the other's card then
   offers it, and pressing it switches back.
3. **A checklist**: a turn's ``angelo_context_folder_write`` CREATE writes ``checklist.md`` into the working folder;
   in Auto it asks nothing; the transcript names the file; the turn's context names both folders with their roles and
   never a path.
4. **Refused**: a turn's write into the reference folder is refused and nothing is written there.
5. **Asks first**: a turn's UPDATE of ``README.md``, which was in the working folder before, waits on its card; the
   file is unchanged until the person's Approve, then has the new text.

Run through ``run_native_suites.py --gui --include test_blender_context_folders_gui``; check
``context-folders-gui.json`` for ``success`` and ``phase == "complete"``. Screenshots after each visible step.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-context-folders-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (a click as a person makes it)
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default Director startup with its chat.
fake_provider_cli.sign_in(os.environ["HOME"], "codex")
os.environ.update(ANGELO_FAKE_CLI_STATE=str(OUT / "fake-cli"), ANGELO_FAKE_CLI_LOGIN_SECONDS="0.5",
                  ANGELO_FAKE_CLI_CODEX_TURNS="ok")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "context-folders-gui.json"
WORK, REF = OUT / "castle game", OUT / "castle references"
README = "The person's own notes about the castle.\n"
CHECKLIST = "# Castle checklist\n\n- [ ] Block out the keep\n- [ ] Walls 4 m tall\n- [ ] Gatehouse\n"
UPDATED = README + "\nWalls: 4 m, per the references.\n"
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
    """The person's click on the drawn control, through the window (aimed for an approval's own control)."""
    from angelo_agent import chat_view
    _, region = chat()
    found = next(((r, a) for r, a, v in hits() if (action is None or a == action) and (value is None or v == value)),
                 None)
    assert found is not None, "not drawn: %s %s" % (action, value)
    state["observed"].setdefault("clicks", []).append([action, value])
    qa_input.window_click(window(), region, found[0], found[1], scale=chat_view.ui_scale())


def send(text):
    area, region = chat()
    area.spaces.active.history[-1].body = text   # Typed into the composer.
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


def folders():
    from angelo_agent import context_folder_host
    return [(record["label"], record["working"]) for record in context_folder_host.labels(controller())]


def folder_id(path):
    from angelo_agent import context_folder_host
    return next(record["id"] for record in context_folder_host.labels(controller()) if record["path"] == path)


def capture(name, win=None):
    path = OUT / name
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=win or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(name)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def turn_requests():
    path = OUT / "fake-cli" / "codex-requests.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []
    return [row for row in rows if row["method"] == "turn/start"]


def cf_tool():
    from angelo_agent import context_folder
    return context_folder.TOOL


def rows(tool):
    return [m for m in controller().state["messages"] if m.get("tool") == tool]


def diagnose():
    try:
        c = controller()
        state["diagnosis"] = {
            "busy": bool(c.busy), "folders": folders(),
            "pending": [{"tool": getattr(v, "name", ""), "approval": bool(v.approval),
                         "executing": bool(getattr(v, "executing", False))} for v in c.pending.values()],
            "rows": [{k: (str(v)[:200] if k in ("text", "result") else v) for k, v in m.items()
                      if k in ("role", "text", "notice", "tool", "status", "result")}
                     for m in c.state["messages"][-8:]]}
        from angelo_agent import chat_notices, context_folder_host
        cards = []
        for m in c.state["messages"]:
            if "context_folder" in m or "context_folder_id" in m:
                card = chat_notices._card(m, 0, False, __import__("angelo_agent").ui, c, set())
                cards.append({"keys": sorted(m), "own": chat_notices.is_own_card(m),
                              "record": str(context_folder_host.card_folder(c, m)),
                              "actions": [a[:2] for a in card["actions"]], "body": card["body"][:80]})
        state["diagnosis"]["folder_cards"] = cards
        capture("failure.png")
    except Exception as failure:   # noqa: BLE001 - a diagnosis never hides the error
        state["diagnosis_error"] = repr(failure) + traceback.format_exc()[-800:]


def finish(error=None):
    if error:
        diagnose()
        state.update(success=False, error=str(error), traceback=traceback.format_exc(),
                     actions=sorted({(a, str(v)) for _, a, v in hits()}))
    else:
        state.update(phase="complete", success=bool(checks) and all(checks.values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_CONTEXT_FOLDERS_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(condition, limit=45):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    assert time.monotonic() - state["waiting"] < limit, "Timed out waiting in " + state["phase"]
    return False


def go(phase, delay=.3):
    state["phase"] = phase
    save()
    return delay


def file_browser():
    for win in bpy.context.window_manager.windows:
        area = next((a for a in win.screen.areas if a.type == 'FILE_BROWSER'), None)
        if area is not None:
            return win, area
    return None


def browser_directory():
    _win, area = file_browser()
    directory = area.spaces.active.params.directory
    return directory.decode("utf-8", "surrogateescape") if isinstance(directory, bytes) else directory


def directory_field():
    """The browser's own directory field as drawn (the widest field right of Parent), or None."""
    from _bpy import _angelo_jobs
    win, _area = file_browser()
    buttons = [(idname, x, y, w, h) for pointer, _block, _text, idname, x, y, w, h in _angelo_jobs.region_buttons()
               if pointer == win.as_pointer() and w > 0 and h > 0]
    parent = next(((x, y) for idname, x, y, _w, _h in buttons if idname == "FILE_OT_parent"), None)
    if parent is None:
        return None
    row = [(w, (x, y, w, h)) for idname, x, y, w, h in buttons if not idname and y == parent[1] and x > parent[0]]
    return max(row)[1] if row else None


def paste_folder(folder):
    """The person pastes the folder's path into the browser's directory field: a press, select all, paste, Return
    (no Python sets what the browser picks; the 2.1.0 break-it review, F-3)."""
    win, _area = file_browser()
    bpy.context.window_manager.clipboard = str(folder)   # The person copied it.
    x, y, w, h = directory_field()
    qa_input.press(win, x + w / 2, y + h / 2)
    state["paste_pending"] = True


def paste_keys():
    win, _area = file_browser()
    x, y, w, h = directory_field() or (0, 0, 0, 0)
    for kind, mods in (("A", {"ctrl": True}), ("V", {"ctrl": True}), ("RET", {})):
        for value in ('PRESS', 'RELEASE'):
            win.event_simulate(type=kind, value=value, x=int(x + w / 2), y=int(y + h / 2), **mods)


def accept_folder(shot):
    win, _area = file_browser()
    capture(shot, win)
    x, y, w, h = qa_input.region_button(win, "file.execute")
    qa_input.press(win, x + w / 2, y + h / 2)


def attach(phase, folder, then, shot):
    """One folder through + > Folder as context… > the file browser, as the person does it."""
    step = state.setdefault("attach_step", "menu")
    if step == "menu":
        if not wait(lambda: point("attach") and file_browser() is None):
            return .2
        click("attach")
        state["attach_step"] = "row"
    elif step == "row":
        if not wait(lambda: point(value="context_folder")):
            return .2
        click(value="context_folder")
        state["attach_step"] = "browser"
    elif step == "browser":
        if not wait(lambda: file_browser() is not None and directory_field() is not None):
            return .2
        paste_folder(folder)
        state["attach_step"] = "keys"
    elif step == "keys":
        paste_keys()
        state["attach_step"] = "typed"
    elif step == "typed":
        if not wait(lambda: Path(browser_directory()) == folder
                    and qa_input.region_button(file_browser()[0], "file.execute") is not None):
            return .2
        accept_folder(shot)
        state.pop("attach_step")
        return go(then)
    return .3


def call_turn(name, tool, arguments, message):
    """The next turn's model makes one ``tool`` call (the stand-in's scripted call), then the person sends."""
    fake_provider_cli.script_codex_call(name, tool, arguments, OUT / "fake-cli")
    fake_provider_cli.script_codex_turns(["call:" + name], OUT / "fake-cli")
    state["observed"]["turns_before_" + name] = len(turn_requests())
    send(message)


def tick():
    try:
        assert time.monotonic() - started < 420, "Context folders fixture timed out in " + state["phase"]
        from angelo_agent import chat_notices, context_folder_host as host, context_folder_write as cw, director, \
            grant_authority, quick_connect, ui
        from angelo_agent.chat_settings import clean_options
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
            for folder in (WORK, REF):
                folder.mkdir(parents=True, exist_ok=True)
            (WORK / "README.md").write_text(README, encoding="utf-8")   # There before Angelo wrote anything.
            (REF / "style.md").write_text("Walls are 4 m tall.\n", encoding="utf-8")
            state["approval"] = clean_options(c.state.get("options"))["approval"]
            checks["the_chat_is_in_auto_as_c_holds_it"] = (state["approval"] == "AUTO_BOUNDED"
                                                           and grant_authority.chat_in_auto(c.state["id"]))
            capture("cf-0-start.png")
            return go("attach_work")
        # 1. Two folders, each through the file browser ----------------------------------------------------------
        if phase == "attach_work":
            return attach(phase, WORK, "work_card", "cf-1-browser-work.png")
        if phase == "work_card":
            if not wait(lambda: folders() == [(WORK.name, True)] and point(host.REMOVE_ACTION, folder_id(WORK))):
                return .2
            checks["the_first_folder_is_the_working_folder"] = True
            capture("cf-2-working-card.png")
            return go("attach_ref")
        if phase == "attach_ref":
            return attach(phase, REF, "ref_card", "cf-3-browser-reference.png")
        if phase == "ref_card":
            if not wait(lambda: folders() == [(WORK.name, True), (REF.name, False)]
                        and point(host.WORKING_ACTION, folder_id(REF))):
                return .2
            checks["the_second_folder_is_a_reference_folder"] = True
            checks["each_folder_has_its_own_card"] = (len([m for m in c.state["messages"]
                                                          if m.get("context_folder_id")]) == 2)
            checks["the_working_card_offers_no_switch"] = not point(host.WORKING_ACTION, folder_id(WORK))
            capture("cf-4-two-cards.png")
            click(host.WORKING_ACTION, folder_id(REF))   # 2. Switching, on the reference folder's card.
            return go("switched")
        if phase == "switched":
            if not wait(lambda: folders() == [(WORK.name, False), (REF.name, True)]
                        and point(host.WORKING_ACTION, folder_id(WORK))):
                return .2
            store = host.grants(ui.state_directory())
            checks["the_persons_click_switched_the_working_folder"] = (
                store.writable(c.state["id"], folder_id(REF)) == REF
                and store.writable(c.state["id"], folder_id(WORK)) is None)
            capture("cf-5-switched.png")
            click(host.WORKING_ACTION, folder_id(WORK))
            return go("switched_back")
        if phase == "switched_back":
            if not wait(lambda: folders() == [(WORK.name, True), (REF.name, False)]):
                return .2
            checks["switched_back_from_the_other_card"] = True
            checks["the_folders_are_outside_the_temporary_folder"] = not str(WORK).startswith(
                os.path.realpath(tempfile.gettempdir()))
            capture("cf-6-switched-back.png")
            # 2b. An agent script runs first (C's write check narrows once one has: break-it third read, SHOULD 1).
            fake_provider_cli.script_codex_turns(["call_approval"], OUT / "fake-cli")
            state["observed"]["turns_before_script"] = len(turn_requests())
            send("Name the scene's objects")
            return go("script_card")
        if phase == "script_card":   # The person's Approve on the script's card (retried as an aimed click is).
            call = next((call for call in c.pending.values() if call.name == "angelo_execute_python" and call.approval
                         and not getattr(call, "executing", False)), None)
            seen = state["observed"]
            if call is None:
                if seen.get("script_clicks") and idle():
                    return go("script_ran")
                wait(lambda: False)   # Times out (with a diagnosis) if the card never comes.
                return .2
            if point("approve", call.id) and time.monotonic() - seen.get("script_clicked_at", 0.0) > 4.0:
                assert seen.get("script_clicks", 0) < 3, "the script's Approve took no effect after three clicks"
                click("approve", call.id)
                seen["script_clicks"], seen["script_clicked_at"] = seen.get("script_clicks", 0) + 1, time.monotonic()
            return .3
        if phase == "script_ran":
            if not wait(lambda: idle() and len(turn_requests()) > state["observed"]["turns_before_script"]):
                return .2
            ran = rows("angelo_execute_python")
            checks["an_agent_script_ran_first"] = bool(ran) and ran[-1].get("status") == "completed"
            call_turn("listing", cf_tool(), {"action": "LIST", "folder": REF.name}, "What is in my references?")
            return go("read_after_script")
        if phase == "read_after_script":
            if not wait(lambda: idle() and len(turn_requests()) > state["observed"]["turns_before_listing"]
                        and rows(cf_tool())):
                return .2
            result = rows(cf_tool())[-1].get("result") or {}
            state["observed"]["listing_after_script"] = result
            checks["a_read_still_works_after_a_script"] = any(
                entry.get("name") == "style.md" for entry in result.get("entries", []))
            capture("cf-6b-read-after-script.png")
            call_turn("checklist", cw.TOOL, {"action": "CREATE", "path": "checklist.md", "content": CHECKLIST},
                      "Write a checklist for the castle in my working folder")
            state["observed"]["asked_for_checklist"] = False
            return go("checklist")
        # 3. A checklist in the working folder -----------------------------------------------------------------
        if phase == "checklist":
            if any(call.approval and not getattr(call, "executing", False) and not getattr(call, "automatic", False)
                   for call in c.pending.values()):
                state["observed"]["asked_for_checklist"] = True
            if not wait(lambda: idle() and len(turn_requests()) > state["observed"]["turns_before_checklist"]
                        and rows(cw.TOOL)):
                return .2
            checks["a_write_still_works_after_a_script"] = (WORK / "checklist.md").is_file()
            checks["the_checklist_is_in_the_working_folder"] = (
                (WORK / "checklist.md").read_text(encoding="utf-8") == CHECKLIST)
            checks["auto_wrote_a_new_file_without_asking"] = not state["observed"]["asked_for_checklist"]
            checks["nothing_was_written_in_the_reference_folder"] = (
                sorted(p.name for p in REF.iterdir()) == ["style.md"])
            written = rows(cw.TOOL)
            from angelo_agent import activity_text
            title = activity_text.step_title(written[-1]) if written else ""
            state["observed"]["checklist_row"] = title
            checks["the_transcript_names_the_file"] = "checklist.md" in title and str(OUT) not in title
            # A state field: sent with the first turn after the folders changed, then only when it changes again.
            request = json.dumps([row.get("input") for row in turn_requests()])
            checks["the_turn_context_names_both_folders_and_roles"] = all(
                word in request for word in ("context_folders", WORK.name, REF.name, "working", "reference"))
            checks["the_turn_context_holds_no_path"] = str(OUT) not in request
            capture("cf-7-checklist-written.png")
            call_turn("reference", cw.TOOL, {"action": "CREATE", "path": "notes.md", "content": "x",
                                             "folder": REF.name}, "Put a note in the references")
            return go("reference")
        # 4. Refused into the reference folder -------------------------------------------------------------------
        if phase == "reference":
            if not wait(lambda: idle() and len(turn_requests()) > state["observed"]["turns_before_reference"]
                        and len(rows(cw.TOOL)) >= 2):
                return .2
            result = rows(cw.TOOL)[-1].get("result") or {}
            state["observed"]["reference_result"] = result
            checks["a_write_into_the_reference_folder_is_refused"] = cw.REFERENCE_ONLY in str(result.get("error"))
            checks["the_reference_folder_is_unchanged"] = sorted(p.name for p in REF.iterdir()) == ["style.md"]
            checks["the_refusal_holds_no_other_edit"] = (result.get("scene_changed") is False
                                                         and not result.get("partial_changes_possible")
                                                         and not result.get("recovery_required"))
            capture("cf-8-reference-refused.png")
            call_turn("readme", cw.TOOL, {"action": "UPDATE", "path": "README.md", "content": UPDATED},
                      "Add the wall height to my README")
            return go("readme_card")
        # 5. A file that was there asks first ----------------------------------------------------------------------
        if phase == "readme_card":
            call = next((call for call in c.pending.values() if call.name == cw.TOOL and call.approval
                         and not getattr(call, "executing", False)), None)
            if not wait(lambda: call is not None and point("approve", call.id)):
                return .2
            state["observed"]["readme_call"] = call.id
            checks["changing_a_file_that_was_there_asks_even_in_auto"] = not getattr(call, "automatic", False)
            checks["the_file_is_unchanged_while_it_asks"] = (WORK / "README.md").read_text(encoding="utf-8") == README
            capture("cf-9-readme-asks.png")
            state["approve_at"] = time.monotonic()
            return go("readme_approve")
        if phase == "readme_approve":   # The person's Approve (retried as an aimed click is).
            call_id, seen = state["observed"]["readme_call"], state["observed"]
            call = c.pending.get(call_id)
            if call is None or getattr(call, "executing", False):
                return go("readme_done")
            if point("approve", call_id) and time.monotonic() - seen.get("clicked_at", 0.0) > 4.0:
                assert seen.get("approve_clicks", 0) < 3, "Approve took no effect after three clicks"
                click("approve", call_id)
                seen["approve_clicks"], seen["clicked_at"] = seen.get("approve_clicks", 0) + 1, time.monotonic()
            return .3
        if phase == "readme_done":
            if not wait(lambda: idle() and (WORK / "README.md").read_text(encoding="utf-8") == UPDATED):
                return .2
            checks["the_persons_approve_wrote_it"] = True
            checks["no_temporary_file_is_left"] = sorted(p.name for p in WORK.iterdir()) == ["README.md",
                                                                                           "checklist.md"]
            checks["the_cards_are_information"] = all(
                chat_notices._content(m)[1] == "info" for m in c.state["messages"] if m.get("context_folder_id"))
            capture("cf-10-readme-approved.png")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5, persistent=True)
