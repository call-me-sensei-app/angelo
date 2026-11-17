# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Learn mode's how-to card in the real chat, and Teach me as you work from the chat bar's + menu
(doc/angelo/learn_mode.rst; doc/angelo/features.rst, "Learn: how to do it yourself").

Real default startup and real input (``Window.event_simulate``). No account, network or inference: the steps are
rows written into the chat as a finished request leaves them (``activity_rows``), which is all a card is built from.

Steps, each captured:

1. Learn is off by default. A typed step is closed like any step: no card is drawn.
2. Still off, a real click on its row opens it as before learn existed: no card, nothing of learn runs in the draw
   (``learn_cards.has_card`` and ``learn_resolver.card`` are counted: zero), and the transcript's rows are exactly
   the rows drawn with learn's hooks taken out. A second click closes it.
3. The chat bar's + opens Angelo's menu; the Learn link is in view in its footer with nothing scrolled; a real click
   on it opens the Learn card, which says learn is turned on here; a real click on the card's switch turns Teach me
   as you work on for this chat, and ``learn.json`` holds this chat's id.
4. With it on, the steps of the latest request are open by themselves and not folded into a work group; the earlier
   request's step stays closed; a step with no hand command says so; a scripted step has no card. A real click on the
   earlier step opens its card under the row, inside the chat, with the command's own name as the app gives it, a key
   cap for the bevel (the default key configuration binds one), where it is, the settings used and what it needs.
5. In Japanese and in Arabic (mirrored) what the app itself names on the card reads in that language (recorded as
   skipped where the build has no translations); whether Angelo's own words do yet is recorded, not required: they
   reach the catalogues at the refresh after the string freeze.
6. Turned off again (the operator, as operator search runs it): every card is closed.
7. The cost of off: a long chat (60 requests, every step opened) drawn with learn off and with learn's hooks taken
   out, alternated; the per-draw times are recorded (``measures["off_draw_ms"]``).

Run through ``run_native_suites.py --gui --include test_blender_learn_cards``; check ``learn-cards.json`` for
``success`` and ``phase == "complete"``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path
from unittest import mock

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (synthetic clicks)
import qa_plus_menu  # noqa: E402  (the + menu, read from the chat's canvas state)

product_env.clear("DISABLE_DEFAULT_CHAT")
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-learn-cards-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "learn-cards.json"
prefs = bpy.context.preferences
prefs.use_preferences_save = False
prefs.view.show_splash = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "skipped": [], "drawn": {},
         "measures": {}}
checks = state["checks"]
started = time.monotonic()

BUILD = {"kind": "create_primitive", "target": None,
         "parameters": {"primitive": "CYLINDER", "radius": 0.04, "depth": 0.1, "segments": 32}}
BEVEL = {"kind": "mesh_bevel", "target": "scn:x:1",
         "parameters": {"edge_indices": [1, 2, 3], "offset": 0.002, "segments": 3}}
SMOOTH = {"kind": "modifier_add", "target": "scn:x:1", "parameters": {"type": "SUBSURF"}}
BEND = {"kind": "mesh_bend", "target": "scn:x:1", "parameters": {"rise": 0.1}}


def step(call_id, *operations):
    return {"role": "activity", "call_id": call_id, "text": "", "tool": "angelo_scene_program",
            "arguments": {"mode": "APPLY", "operations": list(operations)}, "status": "completed",
            "result": {"status": "committed", "changes": {"added": [], "removed": [], "modified": []}}}


FIRST = [{"role": "user", "text": "Make a small cup"}, step("learn-1", BUILD, BEVEL),
         {"role": "assistant", "text": "A small cup is in the scene."}]
SECOND = [{"role": "user", "text": "Smooth it and bow it"}, step("learn-2", SMOOTH), step("learn-3", BEND),
          {"role": "activity", "call_id": "learn-4", "text": "", "tool": "angelo_execute_python",
           "arguments": {"code": "pass"}, "status": "completed", "result": {"status": "completed"}},
          {"role": "assistant", "text": "Smoothed and bowed."}]


def window():
    return bpy.context.window_manager.windows[0]


def chat():
    from angelo_agent import console
    area = next(a for a in window().screen.areas if console.is_chat(a))
    return area, next(r for r in area.regions if r.type == 'WINDOW')


def view_state():
    from angelo_agent import chat_view
    return chat_view._views.get(chat()[0].as_pointer(), {})


def controller():
    from angelo_agent import ui
    return ui._controller


def redraw():
    """Forget what the cards last drew, then draw the window once: what is recorded after it is this frame's."""
    view_state().pop("learn_drawn", None)
    chat()[0].tag_redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
    return view_state().get("learn_drawn") or {}


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def point(rect):
    from qa_input import chat_point   # The chat's points as window pixels (its scale, right to left mirrored).
    return chat_point(chat()[1], rect)


def hit(action, value=None):
    view = view_state()
    return next((rect for rect, found, key in view.get("popup_hits", []) + view.get("hits", [])
                 if found == action and (value is None or key == value)), None)


def card_hit(action):
    """Window coordinates of a control of the card on screen (``card_host``), by its action."""
    from angelo_agent import card_host
    active = card_host._active
    area = next(a for a in window().screen.areas if a.as_pointer() == active.area)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    x, y, wide, high = next(rect for rect, name, _ in reversed(active.plan.hits) if name == action)
    return region.x + x + wide / 2, region.y + y + high / 2


def write(rows):
    """Rows as a finished request leaves them in the chat."""
    chat_controller = controller()
    with chat_controller.lock:
        chat_controller.state["messages"].extend(json.loads(json.dumps(rows)))
    chat_controller.version += 1


def wait(phase, condition, limit=20):
    since = state.setdefault("since", {}).setdefault(phase, time.monotonic())
    if condition():
        return True
    if time.monotonic() - since > limit:
        raise AssertionError("Timed out in " + phase)
    return False


def go(phase, delay=0.4):
    state["phase"] = phase
    return delay


def finish(error=None):
    if error:
        state["error"] = "".join(traceback.format_exception(error)).replace("Traceback (most recent", "Trace (most recent")
    state["success"] = not error and state["phase"] == "complete" and bool(checks) and all(checks.values())
    state["failed_checks"] = [name for name, ok in checks.items() if not ok]
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_LEARN_CARDS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    prefs.view.language = 'en_US'
    bpy.ops.wm.quit_blender()


def language(code, name):
    """The open cards in another interface language, captured; skipped where the build has no translations.

    What the app itself names (a command, a mode) must read in that language now. Angelo's own words on the card are
    in the catalogues only after the refresh that follows the string freeze: whether the heading is translated is
    recorded (``state["catalogue"]``), and required by ``test_native_translation`` once the catalogues hold it."""
    offered = {item.identifier for item in prefs.view.bl_rna.properties["language"].enum_items}
    if code not in offered:
        state["skipped"].append("language %s: this build has no translations" % code)
        return
    from angelo_agent import learn_card_draw, learn_resolver, ui_text
    english = learn_resolver.Lookup().mode_label("OBJECT")
    heading = ui_text.iface_(learn_card_draw.HEADING)
    prefs.view.language, prefs.view.use_translate_interface = code, True
    try:
        learn_resolver.forget()
        drawn = redraw()
        state["drawn"][name] = drawn
        theirs = learn_resolver.Lookup().mode_label("OBJECT")
        checks["what_the_app_names_reads_in_" + name] = theirs != english and any(
            theirs in card["texts"] for card in drawn.values())
        state.setdefault("catalogue", {})[name] = ui_text.iface_(learn_card_draw.HEADING) != heading
        capture("learn-5-%s.png" % name)
    finally:
        prefs.view.language = 'en_US'
        learn_resolver.forget()


def footer(code, name):
    """The open + menu in a language whose footer links are longer, captured: every link in view, nothing scrolled.

    Three links can take two rows there (``chat_add_layout.LINK_WRAPPED``); how many rows they took is recorded."""
    offered = {item.identifier for item in prefs.view.bl_rna.properties["language"].enum_items}
    if code not in offered:
        state["skipped"].append("language %s: this build has no translations" % code)
        return
    prefs.view.language, prefs.view.use_translate_interface = code, True
    try:
        redraw()
        view = view_state()
        box = view.get("popup_rect")
        links = [rect for rect, _action, value in qa_plus_menu.hits(view)
                 if value in {"learn", "report_bug", "request_feature"}]
        scroll = (qa_plus_menu.popup(view).get("layout") or {}).get("max_scroll")
        state.setdefault("footer", {})[name] = {"links": links, "rows": len({rect[1] for rect in links}),
                                                "max_scroll": scroll}
        checks["the_footer_stays_in_view_in_" + name] = (
            scroll == 0 and len(links) == 3 and bool(box) and all(qa_plus_menu.inside(rect, box) for rect in links))
        capture("learn-3-plus-menu-%s.png" % name)
    finally:
        prefs.view.language = 'en_US'
        redraw()


def in_view(card):
    """Whether a drawn card's whole rect is inside the chat's region."""
    if not card:
        return False
    _x, bottom, _wide, high = card["rect"]
    return bottom >= 0 and bottom + high <= chat()[1].height


def wheel(kind):
    """One real wheel step over the chat."""
    _area, region = chat()
    qa_input.send(window(), [(kind, 'PRESS', (region.x + region.width / 2, region.y + region.height / 2))])
    redraw()


def neutral():
    """Learn's hooks taken out of the transcript, as it drew before learn existed."""
    from angelo_agent import learn_rows
    return [mock.patch.object(learn_rows, "begin", lambda controller: False),
            mock.patch.object(learn_rows, "under", lambda message, *rest: (message, rest[-1])),
            mock.patch.object(learn_rows, "draw_under", lambda *rest: 0)]


def rows_drawn(patches=()):
    """The transcript's geometry after a draw with ``patches`` applied for it: the replies' rects and every row's hit
    rects (a card, or any height learn added to a row, moves what is under it)."""
    for patch in patches:
        patch.start()
    try:
        redraw()
        view = view_state()
        return {"replies": dict(view.get("message_rects") or {}),
                "hits": sorted((tuple(rect), str(action), str(key)) for rect, action, key in view.get("hits", []))}
    finally:
        for patch in patches:
            patch.stop()


def off_opened():
    """Off, a step opened: no card, nothing of learn asked while drawing, and the transcript's rows before learn."""
    from angelo_agent import learn_cards, learn_resolver
    asked = {"has_card": 0, "card": 0}

    def counted(name, original):
        def call(*args, **kwargs):
            asked[name] += 1
            return original(*args, **kwargs)
        return call
    with mock.patch.object(learn_cards, "has_card", counted("has_card", learn_cards.has_card)), \
            mock.patch.object(learn_resolver, "card", counted("card", learn_resolver.card)):
        drawn = redraw() or redraw()   # Two draws: the layout, then a draw from it.
    state["measures"]["off_asked"] = asked
    checks["off_an_opened_step_draws_no_card"] = not drawn
    checks["off_nothing_of_learn_runs_in_the_draw"] = asked == {"has_card": 0, "card": 0}
    ours, before = rows_drawn(), rows_drawn(neutral())
    state["measures"]["off_rows"] = {"replies": len(ours["replies"]), "hits": len(ours["hits"])}
    checks["off_the_rows_are_the_rows_before_learn"] = bool(ours["replies"] and ours["hits"]) and ours == before


def off_cost(requests=60, rounds=4, draws=10):
    """A long chat with every step opened, drawn with learn off and with learn's hooks out, alternated (ABAB)."""
    rows = []
    for index in range(requests):
        rows += [{"role": "user", "text": "Step %d" % index}, step("cost-%d" % index, BUILD, BEVEL),
                 {"role": "assistant", "text": "Done %d." % index}]
    write(rows)
    view_state().setdefault("expanded", set()).update("cost-%d" % index for index in range(requests))
    area, region = chat()

    def timed(patches):
        for patch in patches:
            patch.start()
        try:
            with bpy.context.temp_override(window=window(), area=area, region=region):
                bpy.ops.wm.redraw_timer(type='DRAW', iterations=2)   # Untimed: the layout of this arm, built.
                clock = time.perf_counter()
                bpy.ops.wm.redraw_timer(type='DRAW', iterations=draws)
                return (time.perf_counter() - clock) * 1000 / draws
        finally:
            for patch in patches:
                patch.stop()
    off, before = [], []
    for _ in range(rounds):
        off.append(timed(()))
        before.append(timed(neutral()))
    median = lambda values: sorted(values)[len(values) // 2]   # noqa: E731
    state["measures"]["off_draw_ms"] = {"rows": len(rows), "learn_off": [round(v, 3) for v in off],
                                        "before_learn": [round(v, 3) for v in before],
                                        "median_off": round(median(off), 3), "median_before": round(median(before), 3)}
    checks["the_cost_of_off_is_measured"] = len(off) == len(before) == rounds


def tick():
    from angelo_agent import card_host, director, learn_option, learn_resolver, learn_ui, ui_text
    phase = state["phase"]
    try:
        assert time.monotonic() - started < 240, "Suite timed out"
        if phase == "boot":
            if not (qa_boot.started() and director.is_director(window()) and controller() is not None and hit("attach")):
                return .3
            state["chat"] = controller().state["id"]
            checks["teach_me_starts_off"] = not learn_ui.teaching(state["chat"])
            write(FIRST)
            return go("closed")
        if phase == "closed":
            if not wait(phase, lambda: hit("expand", "learn-1") is not None):
                redraw()
                return .2
            checks["a_closed_step_draws_no_card"] = "learn-1" not in redraw()
            capture("learn-1-closed.png")
            qa_input.press(window(), lambda: point(hit("expand", "learn-1")))
            return go("off_opened")
        if phase == "off_opened":   # Teach me as you work is off: an opened step is as it was before learn.
            if not wait(phase, lambda: "learn-1" in view_state().get("expanded", set())):
                return .2
            off_opened()
            capture("learn-2-off-opened.png")
            qa_input.press(window(), lambda: point(hit("expand", "learn-1")))
            return go("off_closed")
        if phase == "off_closed":
            if not wait(phase, lambda: "learn-1" not in view_state().get("expanded", set())):
                return .2
            qa_input.press(window(), lambda: point(hit("attach")))
            return go("menu")
        if phase == "opened":
            if not wait(phase, lambda: "learn-1" in redraw()):
                return .2
            card = redraw()["learn-1"]
            state["drawn"]["opened"] = card
            lookup = learn_resolver.Lookup()
            region = chat()[1]
            x, _y, wide, high = card["rect"]
            checks["the_card_names_the_commands_as_the_app_does"] = all(
                lookup.operator_label(name) in card["texts"] for name in ("mesh.primitive_cylinder_add", "mesh.bevel"))
            from angelo_agent import learn_cards, learn_routes
            items = [route.item for operation in learn_cards.operations(FIRST[1])
                     for route in learn_routes.hand(operation).routes if route.item]
            checks["a_path_ends_with_the_menus_own_text"] = bool(items) and all(
                lookup.item_label(item) in card["texts"] for item in items)
            checks["the_bevel_has_a_key_cap"] = bool(card["caps"]) and set(card["caps"]) >= set(
                lookup.keys("mesh.bevel", (("affect", "EDGES"),), "VIEW_3D", "EDIT"))
            checks["the_card_says_what_it_needs"] = lookup.mode_label("EDIT") in card["texts"]
            checks["the_card_shows_the_settings_used"] = any("32" in text for text in card["texts"])
            checks["the_card_is_inside_the_chat"] = x >= 0 and x + wide <= region.width and high > 60
            return go("closing")
        if phase == "closing":   # The opened card made the chat taller: the wheel brings the card and its row back.
            if not wait(phase, lambda: in_view(redraw().get("learn-1")) and hit("expand", "learn-1") is not None):
                wheel("WHEELUPMOUSE")
                return .2
            capture("learn-4-opened-earlier.png")   # The earlier step's card, whole, under its row.
            qa_input.press(window(), lambda: point(hit("expand", "learn-1")))
            return go("closed_again")
        if phase == "closed_again":   # Closed; the wheel goes back down to the latest request's open cards.
            if not wait(phase, lambda: "learn-1" not in redraw() and "learn-2" in redraw()):
                wheel("WHEELDOWNMOUSE")
                return .2
            checks["a_second_click_closes_it"] = True
            language("ja_JP", "japanese")
            language("ar_EG", "arabic")
            bpy.ops.angelo.learn_teach(session_id=state["chat"])
            return go("turned_off")
        if phase == "menu":
            if not wait(phase, lambda: qa_plus_menu.at_top(view_state())):
                return .2
            view = view_state()
            link, box = qa_plus_menu.hit(view, "popup_add_open", "learn"), view.get("popup_rect")
            checks["the_learn_link_is_in_view_without_scrolling"] = (
                (view["popover"].get("layout") or {}).get("max_scroll") == 0 and bool(link) and bool(box)
                and qa_plus_menu.inside(link, box))
            capture("learn-3-plus-menu.png")
            footer("fr_FR", "french")
            footer("ru_RU", "russian")
            return go("menu_link")
        if phase == "menu_link":   # Drawn in English again: the link is where a click now finds it.
            if not wait(phase, lambda: qa_plus_menu.hit(view_state(), "popup_add_open", "learn") is not None):
                return .2
            qa_input.press(window(), *point(qa_plus_menu.hit(view_state(), "popup_add_open", "learn")))
            return go("card", .5)
        if phase == "card":
            if not wait(phase, lambda: card_host.is_active("learn") and card_host._active.plan is not None):
                return .2
            plan = card_host._active.plan
            texts = [op[1] for op in plan.ops if op[0] in {"text", "mono"}]
            state["drawn"]["learn_card"] = {"texts": texts, "actions": sorted({name for _, name, _ in plan.hits})}
            checks["a_real_click_on_the_link_opens_the_learn_card"] = ui_text.iface_(learn_ui.TITLE) in texts
            checks["the_learn_card_holds_the_switch"] = any(name == "teach" for _, name, _ in plan.hits)
            shown = " ".join(" ".join(texts).split())   # The paragraph's wrapped lines, as one run of words.
            about = " ".join(ui_text.iface_(learn_ui.ABOUT).split())
            checks["the_learn_card_says_it_is_turned_on_here"] = about in shown
            capture("learn-3-learn-card.png")
            qa_input.press(window(), *card_hit("teach"))
            return go("turned_on", .5)
        if phase == "turned_on":
            if not wait(phase, lambda: learn_ui.teaching(state["chat"])):
                return .2
            checks["a_real_click_turns_teach_me_on_for_this_chat"] = True
            saved = json.loads((Path(learn_ui._directory()) / learn_option.NAME).read_text(encoding="utf-8"))
            checks["the_choice_is_saved_for_this_chat"] = saved == {"version": 1, "teach": [state["chat"]]}
            capture("learn-3-switch-on.png")
            qa_input.press(window(), *card_hit("close"))
            return go("card_closed", .5)
        if phase == "card_closed":
            if not wait(phase, lambda: not card_host.is_active("learn")):
                return .2
            write(SECOND)
            return go("taught")
        if phase == "taught":
            if not wait(phase, lambda: {"learn-2", "learn-3"} <= set(redraw())):
                return .2
            drawn = redraw()
            state["drawn"]["taught"] = drawn
            checks["the_latest_requests_steps_open_by_themselves"] = {"learn-2", "learn-3"} <= set(drawn)
            checks["an_earlier_requests_step_stays_closed"] = "learn-1" not in drawn
            checks["a_scripted_step_has_no_card"] = "learn-4" not in drawn
            from angelo_agent import learn_routes
            checks["a_step_with_no_hand_command_says_so"] = ui_text.iface_(learn_routes.OWN_TOOL) in " ".join(drawn["learn-3"]["texts"])
            capture("learn-4-taught.png")
            qa_input.press(window(), lambda: point(hit("expand", "learn-1")))
            return go("opened")
        if phase == "turned_off":
            if not wait(phase, lambda: not learn_ui.teaching(state["chat"]) and not redraw()):
                return .2
            checks["turned_off_every_card_is_closed"] = True
            capture("learn-6-off.png")
            off_cost()
            return go("complete", .2)
        if phase == "complete":
            finish()
            return None
        raise AssertionError("unknown phase " + phase)
    except Exception as error:  # Report and quit: a GUI suite never hangs.
        finish(error)
        return None


bpy.app.timers.register(qa_input.after_clicks(tick), first_interval=0.5, persistent=True)
