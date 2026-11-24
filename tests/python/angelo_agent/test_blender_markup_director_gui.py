# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Marking up a picture for the agent in Director, the view people start in (doc/angelo/attachment_markup.rst).

Real default startup (Director layout and chat), real window events through ``Window.event_simulate`` wherever a
person would act: clicks on the viewer's drawn controls, drags on the picture, typed keys. The scenario:

* The chat bar's + menu row **Mark up a picture…** (``angelo.markup_picture``, as the row runs it) with nothing
  attached: the 3D view as it is now becomes a picture of the chat and opens in the viewer.
* **Markup**: a pen stroke and an arrow dragged on the picture; Ctrl+Z takes the arrow back, Shift+Ctrl+Z redoes it.
* **Keyboard**: a click in the chat's message box takes the keyboard back; typed with the pointer over the picture,
  the letters reach the chat and never switch the viewer's tool.
* **Comment**: a click on the picture places pin 1; its note is typed into the pin and kept with Return.
* The **Add instructions…** field takes typed words; **Send to Angelo** sends one message with the marked picture
  and the original, and the viewer closes. Nothing in the scene changes at any point.

Variants (native_suite_manifest): English, Arabic (the bars mirror, the picture never) and a narrow window.
Stand-in provider CLIs (``fake_provider_cli.py``) answer start-up checks; no account or inference. Run through
``run_native_suites.py --gui --include test_blender_markup_director_gui``; check ``markup-director.json`` for
``success`` and ``phase == "complete"``. Screenshots after each visible step.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import hashlib
import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-markup-director-qa"))
OUT.mkdir(parents=True, exist_ok=True)
import fake_provider_cli  # noqa: E402
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_hit  # noqa: E402  (where a drawn control is in window pixels)
import qa_input  # noqa: E402  (a click as a person makes it)
from angelo_agent import product_env  # noqa: E402

product_env.clear('DISABLE_DEFAULT_CHAT')  # This suite needs the default Director startup.
os.environ["ANGELO_FAKE_CLI_STATE"] = str(OUT / "fake-cli")
os.environ["PATH"] = str(fake_provider_cli.install(OUT / "bin")) + os.pathsep + os.environ.get("PATH", "")
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
LANGUAGE = os.environ.get("ANGELO_MARKUP_QA_LANGUAGE", "")   # The Arabic variant's "ar_EG"; English otherwise.
REPORT = OUT / "markup-director.json"
state = {"phase": "boot", "success": False, "checks": {}, "steps": {}, "screenshots": {}, "language": LANGUAGE,
         "fixture": "Default Director startup, stand-in Claude connected, the default cube, synthetic window events; "
                    "no provider, account or inference"}
started = time.monotonic()


def window():
    return bpy.context.window_manager.windows[0]


def viewer():
    from angelo_agent import markup_overlay
    return markup_overlay.current()


def region():
    state_ = viewer()
    for area in window().screen.areas:
        if state_ is not None and area.as_pointer() == state_.area:
            return next(r for r in area.regions if r.type == 'WINDOW')
    area = max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    return next(r for r in area.regions if r.type == 'WINDOW')


def to_window(x, y):
    """A point of the viewer's chrome in window pixels: its bars are laid out in region pixels and mirrored right to
    left as they are drawn (``qa_hit.PIXELS``)."""
    x, y = qa_hit.xy(qa_hit.PIXELS, region(), x, y)
    return int(x), int(y)


def on_picture(dx, dy):
    """Window pixels of a point (dx, dy) from the region's centre, where the picture is: never mirrored
    (``qa_hit.RAW``)."""
    r = region()
    return qa_hit.ipoint(qa_hit.RAW, r, (dx, dy, r.width, r.height))


def rect_of(action, value=None):
    plan = viewer().plan if viewer() is not None else None
    return next((rect for rect, a, v in (plan or {}).get("hits", ()) if a == action and (value is None or v == value)),
                None)


def click(action, value=None):
    def run():
        # The viewer lays its bars out when it draws: a step that changed its mode (the last one's check reads the
        # session) may not be drawn yet, so draw once before reading where a control is (a narrow run read the
        # Markup bar's plan just after Back to View, and its Comment was "not drawn").
        with bpy.context.temp_override(window=window()):
            bpy.ops.wm.redraw_timer(type='DRAW_WIN', iterations=1)
        rect = rect_of(action, value)
        assert rect is not None, "not drawn: %s %s" % (action, value)
        qa_input.press(window(), *to_window(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2))
    return run


def send(kind, value='NOTHING', x=None, y=None, **extra):
    cx, cy = on_picture(0, 0)
    window().event_simulate(type=kind, value=value, x=cx if x is None else int(x), y=cy if y is None else int(y),
                            **extra)


def drag(points):
    """A press, moves and a release on the picture, at points relative to the region's centre (the picture's)."""
    def run():
        qa_input.send(window(), qa_input.stroke_path([on_picture(dx, dy) for dx, dy in points]))
    return run


def typed(text, at=None):
    def run():
        names = {" ": "SPACE", ".": "PERIOD"}
        x, y = at() if at else (None, None)
        for char in text:
            kind = names.get(char, char.upper())
            send(kind, 'PRESS', x=x, y=y, unicode=char, shift=char.isupper())
            send(kind, 'RELEASE', x=x, y=y, shift=char.isupper())
    return run


def keys(kind, **mods):
    def run():
        send(kind, 'PRESS', **mods)
        send(kind, 'RELEASE', **mods)
    return run


def scene_keys():
    """Keys the 3D view under the viewer would act on (re-review of 4e712847d3b, M1), with every object selected:
    Delete deletes them, Space plays, an arrow changes the frame, G starts a move the next click would confirm."""
    def run():
        for obj in bpy.context.view_layer.objects:
            obj.select_set(True)
        for kind in ('DEL', 'DEL', 'SPACE', 'LEFT_ARROW', 'G'):
            keys(kind)()
    return run


def covered_view():
    """The covered 3D view's view matrix, rounded: what Numpad 7 or Home would change under the viewer."""
    data = getattr(region(), "data", None)
    matrix = getattr(data, "view_matrix", None)
    return [round(v, 4) for row in matrix for v in row] if matrix is not None else None


def chat_focused():
    from angelo_agent import keyboard_owner
    return keyboard_owner.chat_has_it(window())


def over_the_picture():
    x, y = on_picture(0, 0)
    send('MOUSEMOVE', x=x, y=y)


def chat_keys_over_the_picture():
    """Re-review of b5808fd5a2e, M1: the chat holds the keyboard (a click in its field) and the pointer rests on the
    picture: keys the chat doesn't use reached the 3D view under the viewer. Every object selected, the cube active."""
    def run():
        click_composer()()
        over_the_picture()
        layer = window().view_layer
        for obj in layer.objects:
            obj.select_set(True)
        layer.objects.active = bpy.data.objects.get("Cube") or layer.objects.active
        for kind, mods in (('NUMPAD_0', COMMAND_CTRL), ('J', COMMAND_CTRL), ('NUMPAD_7', {}), ('NUMPAD_PERIOD', {})):
            keys(kind, **mods)()
    return run


COMMAND_CTRL = {"ctrl": True}   # Ctrl on every platform: Blender's object keys (join, camera from view).


def chat_undo_over_the_picture():
    """Re-read of 0690d5c, S4 and N2: the chat holds the keyboard with a draft that has no history of its own (written
    outside the composer), so its Cmd/Ctrl+Z falls back to the scene's Undo. With the picture viewer open, an undoable
    move of the cube stays: the Undo never reaches the scene the viewer hides."""
    def run():
        chat_area().spaces.active.history[-1].body = "a draft written elsewhere"   # No draft history: the scene's Undo.
        cube = bpy.data.objects["Cube"]
        cube.location.x += 0.5
        with bpy.context.temp_override(window=window()):
            bpy.ops.ed.undo_push(message="QA move")   # i18n: an undo step's name in a test
        over_the_picture()
        keys('Z', **COMMAND)()
        from angelo_agent import chat_view
        from qa_input import chat_point
        area = chat_area()
        x, y = chat_point(area, chat_view._views[area.as_pointer()]["composer"])
        send('MOUSEMOVE', x=x, y=y)   # Resting on the chat itself, never a click (N2).
        send('Z', 'PRESS', x=x, y=y, **COMMAND)
        send('Z', 'RELEASE', x=x, y=y, **COMMAND)
    return run


def outliner_beside_the_view():
    """Director shows only the 3D view and the chat: an Outliner is made beside the viewer's 3D view (a split whose new
    part becomes an Outliner), a visible editor the viewer doesn't cover."""
    from angelo_agent import markup_overlay
    shown = markup_overlay.current()
    area = next(a for a in window().screen.areas if a.as_pointer() == shown.area)
    before = {a.as_pointer() for a in window().screen.areas}
    with bpy.context.temp_override(window=window(), screen=window().screen, area=area):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=0.8)
    made = next(a for a in window().screen.areas if a.as_pointer() not in before)
    made.type = 'OUTLINER'


def undo_over_the_outliner():
    """Re-read of 41fd23a: with no chat holding the keyboard, the person's Undo over a visible editor is theirs, viewer
    open or not. A wheel over the Outliner takes the keyboard from the chat (no undo step, as a click would push),
    then Cmd/Ctrl+Z there undoes the cube's move the last step kept."""
    def run():
        area = next(a for a in window().screen.areas if a.type == 'OUTLINER')
        x, y = area.x + area.width // 2, area.y + area.height // 2
        send('MOUSEMOVE', x=x, y=y)
        send('WHEELDOWNMOUSE', 'PRESS', x=x, y=y)
        send('Z', 'PRESS', x=x, y=y, **COMMAND)
        send('Z', 'RELEASE', x=x, y=y, **COMMAND)
    return run


def moved_cube_kept(b, a):
    before, after = dict(b["placed"]), dict(a["placed"])
    return (a["open"] and a["chat_focused"] and a["objects"] == b["objects"] and a["camera"] == b["camera"]
            and a["view"] == b["view"] and round(after["Cube"][0] - before["Cube"][0], 4) == 0.5)


def unchanged_scene(b, a):
    return a["open"] and (a["objects"], a["placed"], a["frame"], a["camera"], a["view"]) == \
        (b["objects"], b["placed"], b["frame"], b["camera"], b["view"]) and not a["playing"]


def chat_area():
    from angelo_agent import console
    return next(a for a in window().screen.areas if console.is_chat(a))


def click_composer():
    """A click in the chat's message box: the chat takes the keyboard back from the viewer (markup_input)."""
    def run():
        from angelo_agent import chat_view
        from qa_input import chat_point
        area = chat_area()
        x, y = chat_point(area, chat_view._views[area.as_pointer()]["composer"])
        send('MOUSEMOVE', x=x, y=y)
        send('LEFTMOUSE', 'PRESS', x=x, y=y)
        send('LEFTMOUSE', 'RELEASE', x=x, y=y)
    return run


def console_is_chat(area):
    from angelo_agent import console
    return console.is_chat(area)


def chat_draft():
    history = chat_area().spaces.active.history
    return history[-1].body if len(history) else ""


def open_from_plus_menu():
    """What the chat bar's + menu row runs (``chat_destinations`` Feature ``markup``): its operator, invoked."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.angelo.markup_picture('INVOKE_DEFAULT')


def controller():
    from angelo_agent import ui
    return ui.get_controller()


def snapshot():
    shown = viewer()
    doc = shown.session.doc if shown is not None else None
    sent = [m for m in controller().state.get("messages", ()) if m.get("role") == "user"]
    last = sent[-1] if sent else {}
    return {"open": shown is not None, "mode": shown.session.mode if shown else None,
            "marks": len(doc.marks) if doc else None, "comments": len(doc.comments) if doc else None,
            "notes": [c.get("text", "") for c in doc.comments] if doc else None,
            "instructions": doc.instructions if doc else None, "asking": bool(shown and shown.session.confirm),
            "attachments": len(controller().attachments), "user_messages": len(sent),
            "last_pictures": len(last.get("references") or ()),   # A user row carries its pictures as references.
            "last_text": last.get("content") or last.get("text"),
            "objects": sorted(o.name for o in bpy.data.objects),
            "placed": sorted((o.name, tuple(round(v, 5) for v in o.location)) for o in bpy.data.objects),
            "frame": window().scene.frame_current, "playing": bool(window().screen.is_animation_playing),
            "camera": getattr(window().scene.camera, "name", None), "view": covered_view(),
            "chat_focused": chat_focused(),
            "draft": chat_draft() if any(console_is_chat(a) for a in window().screen.areas) else ""}


def note_point():
    rect = rect_of("instructions")
    return to_window(rect[0] + rect[2] / 2, rect[1] + rect[3] / 2) if rect else (None, None)


COMMAND = {"oskey": True} if sys.platform == "darwin" else {"ctrl": True}   # Cmd on macOS, Ctrl elsewhere.
# (name, run, test(before, after), screenshot or None, seconds to wait). Each starts from the last.
STEPS = (
    ("plus_menu_opens_the_3d_view", open_from_plus_menu,
     lambda b, a: a["open"] and a["mode"] == "view" and a["attachments"] == b["attachments"] + 1
     and viewer().keyboard and not a["chat_focused"], "markup-open.png", 6.0),   # One owner (S1).
    ("markup_mode", click("mode", "markup"), lambda b, a: a["mode"] == "markup", "markup-tools.png", 4.0),
    ("pen_stroke", drag([(-120, -40), (-90, -10), (-60, 10), (-20, 20)]), lambda b, a: a["marks"] == 1, None, 4.0),
    ("arrow_tool", click("tool", "arrow"), lambda b, a: viewer().session.tool == "arrow", None, 4.0),
    ("arrow", drag([(40, -60), (80, -20), (120, 30)]), lambda b, a: a["marks"] == 2, "markup-marks.png", 4.0),
    ("undo", keys('Z', **COMMAND), lambda b, a: a["marks"] == 1, None, 4.0),
    ("redo", keys('Z', shift=True, **COMMAND), lambda b, a: a["marks"] == 2, None, 4.0),
    # Break-it of 2298d608dd1, M3: typed into the chat with the pointer resting over the picture, letters stay the
    # chat's (P would switch to the pen if the viewer took them), and the viewer gives the keyboard back on that click.
    ("chat_takes_the_keyboard", click_composer(), lambda b, a: not viewer().keyboard, None, 4.0),
    ("chat_keeps_its_letters", typed("pa"),
     lambda b, a: chat_draft().endswith("pa") and viewer().session.tool == "arrow", "markup-chat-typing.png", 4.0),
    ("back_to_view", click("mode", "view"), lambda b, a: a["mode"] == "view" and a["marks"] == 2, None, 4.0),
    # Re-review of 4e712847d3b: the click on the viewer took the keyboard from the chat too (M2), so a letter and
    # Return reach neither its draft nor a send; and no key reaches the 3D view under the viewer (M1).
    ("chat_draft_untouched", lambda: (typed("b")(), keys('RET')()),
     lambda b, a: a["draft"] == b["draft"] and a["draft"].endswith("pa") and a["user_messages"] == b["user_messages"],
     None, 4.0),
    ("scene_keys_stay_off_the_scene", scene_keys(), unchanged_scene, "markup-scene-keys.png", 4.0),
    # Re-review of b5808fd5a2e, M1: the chat holds the keyboard, the pointer rests on the picture; Ctrl+Numpad 0,
    # Ctrl+J and the numpad reach neither the hidden scene nor its view.
    ("chat_keys_stay_off_the_covered_view", chat_keys_over_the_picture(),
     lambda b, a: unchanged_scene(b, a) and a["chat_focused"], "markup-chat-keys.png", 4.0),
    ("chat_undo_stays_off_the_hidden_scene", chat_undo_over_the_picture(), moved_cube_kept, None, 4.0),
    ("outliner_beside_the_view", outliner_beside_the_view,
     lambda b, a: a["open"] and any(x.type == 'OUTLINER' for x in window().screen.areas), None, 4.0),
    ("undo_over_a_visible_editor_is_the_persons", undo_over_the_outliner(),
     lambda b, a: a["open"] and not a["chat_focused"] and round(dict(b["placed"])["Cube"][0]
                                                                 - dict(a["placed"])["Cube"][0], 4) == 0.5, None, 4.0),
    ("comment_mode", click("mode", "comment"), lambda b, a: a["mode"] == "comment", None, 4.0),
    ("pin", drag([(0, 40)]), lambda b, a: a["comments"] == 1, None, 4.0),
    # The note is typed into the pin's own field and kept by Return (a click elsewhere keeps it too).
    ("pin_note_typed", typed("Taller"), lambda b, a: viewer().session.field.text == "Taller" and a["notes"] == [""],
     "markup-comment-typing.png", 4.0),
    ("pin_note_kept", keys('RET'), lambda b, a: a["notes"] == ["Taller"], "markup-comment.png", 4.0),
    ("instructions_field", click("instructions"), lambda b, a: a["open"], None, 4.0),
    ("instructions_typed", typed("Make this part taller.", at=note_point),
     lambda b, a: a["instructions"] == "Make this part taller.", "markup-instructions.png", 4.0),
    # Sent by the Send button in English and the narrow window, by Return in the instructions in Arabic: both routes
    # (Return reached a stale name after Send moved to markup_send, e53f10409d8; the button never did).
    ("send", keys('RET') if LANGUAGE else click("send"),
     lambda b, a: not a["open"] and a["user_messages"] == b["user_messages"] + 1 and a["last_pictures"] >= 2,
     "markup-sent.png", 15.0),
)


def save():
    REPORT.write_text(json.dumps(state, indent=2, default=str))


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"][name] = hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        checks, steps = state["checks"], state["steps"]
        checks["no_scene_change"] = all(s["after"]["objects"] == state["baseline"] for s in steps.values()
                                        if "after" in s)
        checks["marks_drawn_on_screen"] = (state["screenshots"].get("markup-tools.png")
                                           != state["screenshots"].get("markup-marks.png"))
        state["phase"] = "complete"
        state["success"] = all(checks.values()) and all(s["passed"] is not False for s in steps.values())
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    save()
    print("ANGELO_MARKUP_DIRECTOR", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    try:
        assert time.monotonic() - started < 420, "Markup Director fixture timed out in " + state["phase"]
        from angelo_agent import director, ui
        if state["phase"] == "boot":
            if not director.is_director(window()) or not qa_boot.started():
                return .2
            if LANGUAGE:
                bpy.context.preferences.view.language = LANGUAGE
                bpy.context.preferences.view.use_translate_interface = True
            __import__("angelo_agent.grant_guard").grant_guard.stand_in().quick_connect(mode="claude_subscription")
            state["phase"] = "connect"
            return .2
        if state["phase"] == "connect":
            if not ui.connection_available():
                return .2
            send('MOUSEMOVE')
            state["phase"], state["settled"] = "settle", time.monotonic()
            return .2
        if state["phase"] == "settle":
            if time.monotonic() - state["settled"] < 1.5:
                return .2
            state["checks"]["director_is_default"] = director.preferred_mode() == "DIRECTOR"
            state["window"] = [window().width, window().height]
            state["baseline"] = sorted(o.name for o in bpy.data.objects)
            capture("markup-start.png")
            state["phase"], state["index"], state["pending"] = "step", 0, None
            save()
            return .3
        if state["phase"] == "step":
            if state["pending"]:
                name, test, shot, before, sent, wait = state["pending"]
                after = snapshot()
                passed = None if test is None else bool(test(before, after))
                if passed is False and time.monotonic() - sent < wait:
                    return .1  # A click, a drag or typed keys take a few event-loop passes.
                state["steps"][name] = {"passed": passed, "before": before, "after": after}
                state["pending"] = None
                state["index"] += 1
                if shot:
                    capture(shot)
                save()
                if passed is False:
                    finish()
                    return None
            if state["index"] >= len(STEPS):
                finish()
                return None
            name, run, test, shot, wait = STEPS[state["index"]]
            before = snapshot()
            run()
            state["pending"] = (name, test, shot, before, time.monotonic(), wait)
            return .3
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=.5, persistent=True)
