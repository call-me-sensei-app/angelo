# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The basics in every mode, in the running app: menus, Save and Undo, and the chat (``director``, ``chat_toggle``).

Evidence: a user (2026-10-21) wrote "The menu bar disappears in director mode, I have to switch back to 'manual' mode
to e.g. save the file", and "I can't figure out how to display the chat window in manual mode". Since 1.0.4 Director
keeps Blender's own top-bar menus, the Chat button (with its shortcut on it) is the same in both modes, and in Manual
the chat follows the person across workspace tabs unless they hid it.

Real default startup and real keys (``Window.event_simulate``). No CLI, account, network or inference. The save and
undo chords are read from the user keymap (``wm.save_mainfile`` in Window, ``ed.undo`` in Screen), so the platform's
own keys are pressed (Cmd on macOS). Each is pressed with the pointer over the chat and over the 3D view. Not covered:
a typed draft in a connected chat (the profile has no connection, so the composer is hidden); ``chat_view``'s input
modal passes every Ctrl/Cmd chord on, which ``test_native_chat_toggle`` does not pin either.

Run through ``run_native_suites.py --gui --include test_blender_mode_basics``; check ``mode-basics.json`` for
``success`` and ``phase == "complete"``. Screenshots: one per mode and state, named ``basics-*.png``.
"""

import json
import os
import tempfile
import time
import traceback
from pathlib import Path

import bpy

from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-mode-basics-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "mode-basics.json"
BLEND = OUT / "mode-basics.blend"
DRAFT = "Keep this draft when I switch tabs"
bpy.context.preferences.use_preferences_save = False
state = {"phase": "boot", "success": False, "checks": {}, "screenshots": [], "menus_drawn": {}}
started = time.monotonic()


def record_menus():
    """Note, per mode, each time the top bar draws Blender's menus (File, Edit, Render, Window, Help)."""
    from bl_ui import space_topbar
    from angelo_agent import director
    menus = space_topbar.TOPBAR_MT_editor_menus
    original = menus.draw_collapsible.__func__

    def draw_collapsible(cls, context, layout):
        mode = "director" if director.is_director(context.window) else "manual"
        state["menus_drawn"][mode] = state["menus_drawn"].get(mode, 0) + 1
        return original(cls, context, layout)
    menus.draw_collapsible = classmethod(draw_collapsible)


def window():
    return bpy.context.window_manager.windows[0]


def chat_state():
    from angelo_agent import chat_toggle
    return chat_toggle.chat_state(window(), bpy.context.window_manager.windows)


def area_of(kind):
    if kind == "chat":
        return chat_state()[2]
    return max((a for a in window().screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)


def chord(keymap, idname):
    """The person's own chord for ``idname`` in ``keymap`` (the user keyconfig, as Blender merged it)."""
    items = bpy.context.window_manager.keyconfigs.user.keymaps[keymap].keymap_items
    item = next(i for i in items if i.idname == idname and i.active and i.map_type == 'KEYBOARD'
                and not getattr(i.properties, "exit", False))
    keys = {name: getattr(item, name) == 1 for name in ("ctrl", "alt", "shift", "oskey")}
    return item.type, keys


def press(over, key, modifiers):
    """``key`` with ``modifiers``, the pointer resting over the chat or the 3D view."""
    area = area_of(over)
    x, y = area.x + area.width // 2, area.y + area.height // 3
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=x, y=y)
    window().event_simulate(type=key, value='PRESS', x=x, y=y, **modifiers)
    window().event_simulate(type=key, value='RELEASE', x=x, y=y, **modifiers)


def press_chat_shortcut():
    from angelo_agent import shortcuts
    item = shortcuts.bound(shortcuts.CHAT.idname)
    press("view", item["type"], {name: item[name] for name in ("ctrl", "alt", "shift", "oskey")})


def areas():
    """The window's editors as the person sees them: type, rectangle and whether it is the chat."""
    from angelo_agent import console
    return [(a.type, a.x, a.y, a.width, a.height, console.is_chat(a)) for a in window().screen.areas]


def chat_visible():
    """A chat docked in this window at a usable size on an outer edge, not only present in its layout."""
    area = chat_state()[2]
    if area is None or chat_state()[0] != "docked":
        return False
    edges = (min(a.x for a in window().screen.areas), max(a.x + a.width for a in window().screen.areas))
    return area.width >= 200 and area.height >= window().height * 0.6 and (
        abs(area.x - edges[0]) <= 4 or abs(area.x + area.width - edges[1]) <= 4)


def record_follows():
    """Every ``follow`` call: the tab, the chat state, and whether a dock was asked for (diagnosis)."""
    from angelo_agent import chat_toggle, ui
    state["follows"] = []
    if not hasattr(chat_toggle, "follow"):
        return   # A build before 1.0.4: nothing follows.
    original, dock = chat_toggle.follow, ui.request_chat_dock

    def follow():
        window_ = window()
        state["follows"].append(("follow", round(time.monotonic() - started, 2), window_.workspace.name,
                                 chat_state()[0], [a.type for a in window_.screen.areas]))
        return original()

    def request_chat_dock(*args, **kwargs):
        state["follows"].append(("dock", round(time.monotonic() - started, 2), window().workspace.name, areas()))
        return dock(*args, **kwargs)
    chat_toggle.follow = follow
    ui.request_chat_dock = request_chat_dock


def tabs():
    """Manual workspaces by kind: ``(with_3d_view, without)``, the Director copy excluded, current first."""
    from angelo_agent import director_layout
    spaces = [w for w in bpy.data.workspaces if not director_layout.is_director_workspace(w)]
    spaces.sort(key=lambda w: w != window().workspace)
    with_view = [w for w in spaces if any(a.type == 'VIEW_3D' for s in w.screens for a in s.areas)]
    return with_view, [w for w in spaces if w not in with_view]


def cube():
    return bpy.data.objects["Cube"]


def capture(name):
    path = OUT / name
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))


def chat_drawn(name):
    """Vision check on the screenshot ``name``: the chat's own background, uniform and dark, at three heights along
    the chat's left margin (window pixels; the screenshot's rows run top-down). A frame still showing the editors the
    dock split from has their lighter, varied greys there."""
    import array
    area = chat_state()[2]
    if area is None:
        return False
    image = bpy.data.images.load(str(OUT / name), check_existing=False)
    try:
        width, height = image.size
        pixels = array.array('f', [0.0]) * (width * height * 4)
        image.pixels.foreach_get(pixels)
        scale = width / window().width
        samples = []
        for fraction in (0.2, 0.5, 0.85):
            x = int((area.x + 8) * scale)
            y = int((area.y + area.height * fraction) * scale)   # Blender images are stored bottom-up too.
            index = (y * width + x) * 4
            samples.append(tuple(round(v, 3) for v in pixels[index:index + 3]))
        state.setdefault("chat_pixels", {})[name] = samples
        return max(max(s) for s in samples) < 0.16 and max(max(s) for s in samples) - min(min(s) for s in samples) < 0.02
    finally:
        bpy.data.images.remove(image)


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_MODE_BASICS", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def wait(phase, condition, limit=20):
    if condition():
        state.pop("waiting", None)
        return True
    state.setdefault("waiting", time.monotonic())
    if time.monotonic() - state["waiting"] > limit:
        state["checks"]["timed_out_in_" + phase] = False
        state.pop("waiting", None)
        return True
    return False


def basics_steps(mode):
    """Save and Undo by keys over the chat and over the 3D view: ``(phase, over, what)`` in order."""
    return [(f"{mode}_{what}_{over}", over, what) for what in ("save", "undo") for over in ("chat", "view")]


def start_basic(phase, over, what):
    checks = state["checks"]
    if what == "save":
        cube().location.x += 1.0
        bpy.ops.ed.undo_push(message="Mode basics: an edit to save")   # As an edit does: the file is now unsaved.
        checks[phase + "_dirty_before"] = bpy.data.is_dirty
        state["mtime"] = BLEND.stat().st_mtime_ns
        key, modifiers = chord("Window", "wm.save_mainfile")
    else:
        state["before_undo"] = cube().location.x
        bpy.ops.ed.undo_push(message="Mode basics: before the move")
        cube().location.x += 2.0
        bpy.ops.ed.undo_push(message="Mode basics: the move")
        key, modifiers = chord("Screen", "ed.undo")
    state["keys_" + what] = [key, modifiers]
    press(over, key, modifiers)


def basic_done(what):
    if what == "save":
        return not bpy.data.is_dirty and BLEND.stat().st_mtime_ns != state["mtime"]
    return abs(cube().location.x - state["before_undo"]) < 1e-6


def tick():
    try:
        assert time.monotonic() - started < 300, "Fixture timed out in " + state["phase"]
        checks, phase = state["checks"], state["phase"]
        from angelo_agent import chat_toggle, director, ui
        settled = director._timer is None and ui._layout_timer is None
        if phase == "boot":
            if not settled or not director.is_director(window()) or not chat_state()[2]:
                return .2
            record_menus()
            record_follows()
            bpy.ops.wm.save_as_mainfile(filepath=str(BLEND))
            state["queue"] = basics_steps("director")
            state["phase"] = "director_shown"
            return .5
        if phase == "director_shown":
            chat_toggle.redraw_top_bars()
            if not wait(phase, lambda: state["menus_drawn"].get("director", 0) > 0):
                return .2
            capture("basics-1-director.png")
            checks["director_draws_blenders_menus"] = state["menus_drawn"].get("director", 0) > 0
            checks["director_has_its_chat"] = chat_state()[0] == "docked"
            # getattr: the same script records a build before 1.0.4 (its screenshots are the "before" pictures).
            label = getattr(chat_toggle, "button_text", lambda: bpy.app.translations.pgettext_iface("Chat"))()
            state["chat_label"] = label
            checks["the_chat_button_names_its_shortcut"] = label != bpy.app.translations.pgettext_iface("Chat")
            view = area_of("view")
            with bpy.context.temp_override(window=window(), area=view,
                                           region=next(r for r in view.regions if r.type == 'WINDOW')):
                window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=view.x + 40, y=view.y + view.height - 40)
                bpy.ops.wm.call_menu(name="TOPBAR_MT_file")
            state["phase"] = "director_file_menu"
            return .6
        if phase == "director_file_menu":
            capture("basics-2-director-file-menu.png")
            window().event_simulate(type='ESC', value='PRESS')
            window().event_simulate(type='ESC', value='RELEASE')
            state["phase"] = "basics"
            state["next_mode"] = "director_chat"
            return .5
        if phase == "basics":   # The queued Save/Undo steps of the current mode, one at a time.
            step = state.get("step")
            if step is None:
                if not state["queue"]:
                    state["phase"] = state.pop("next_mode")
                    return .2
                step = state["step"] = state["queue"].pop(0)
                start_basic(*step)
                return .5
            name, _over, what = step
            if not wait(name, lambda: basic_done(what), limit=10):
                return .2
            checks[name + "_works"] = basic_done(what)
            state["step"] = None
            return .3
        if phase == "director_chat":
            press_chat_shortcut()
            state["phase"] = "director_hidden"
            return .5
        if phase == "director_hidden":
            if not wait(phase, lambda: settled and chat_state()[0] == "hidden"):
                return .2
            capture("basics-3-director-chat-hidden.png")
            checks["director_hides_its_chat"] = chat_state()[0] == "hidden" and director.is_director(window())
            checks["the_3d_view_takes_the_window"] = len(window().screen.areas) == 1
            bpy.ops.angelo.toggle_chat()
            state["phase"] = "director_back"
            return .5
        if phase == "director_back":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked"):
                return .2
            chat, view = area_of("chat"), area_of("view")
            checks["director_shows_it_again_on_the_right"] = chat.x > view.x and director.is_director(window())
            checks["at_directors_width"] = abs(chat.width / window().width - 0.35) < 0.06
            director.request_mode(window(), "MANUAL")
            state["phase"] = "manual"
            return .8
        if phase == "manual":
            if not wait(phase, lambda: settled and not director.is_director(window()) and chat_visible()):
                return .2
            capture("basics-4-manual.png")
            checks["manual_chat_drawn"] = chat_drawn("basics-4-manual.png")
            checks["manual_draws_blenders_menus"] = state["menus_drawn"].get("manual", 0) > 0
            state["queue"] = basics_steps("manual")
            state["phase"], state["next_mode"] = "basics", "manual_tab"
            return .3
        if phase == "manual_tab":
            from angelo_agent import chat_toggle
            if not chat_toggle.FOLLOW_TABS:   # 1.0.4 ships without tab-follow; its phases wait for 1.0.4.x.
                checks["tab_follow_is_off"] = True
                bpy.ops.angelo.intro('INVOKE_DEFAULT')
                state["phase"] = "welcome"
                return 1.0
            with_view, without = tabs()
            state["tabs"] = [w.name for w in with_view[1:4]] + [w.name for w in without[:1]]
            checks["the_startup_file_has_tabs_to_switch"] = len(with_view) >= 4 and bool(without)
            chat_state()[2].spaces.active.history[-1].body = DRAFT
            window().workspace = bpy.data.workspaces[state["tabs"][0]]
            state["phase"] = "manual_followed"
            return .5
        if phase == "manual_followed":
            if not wait(phase, lambda: settled and window().workspace.name == state["tabs"][0] and chat_visible()):
                return .2
            if not state.pop("settled_once", False):   # What the person sees: one beat after the dock, not its tick.
                state["settled_once"] = True
                return .6
            state["areas_followed"] = areas()
            chat = chat_state()[2]
            state["chat_followed"] = {"space": chat.spaces.active.type, "ui_type": chat.ui_type,
                                      "regions": [(r.type, r.width, r.height) for r in chat.regions]}
            from angelo_agent import chat_view
            before = chat_view.draw_count
            capture("basics-5-manual-other-tab-chat-followed.png")
            # Diagnosis: did the chat's own draw run during the capture's redraw, and did it fail?
            state["chat_draws_in_capture"] = chat_view.draw_count - before
            state["chat_last_error"] = chat_view.last_error
            state["chat_region"] = [(r.type, r.x, r.y, r.width, r.height) for r in chat.regions]
            checks["the_followed_chat_is_drawn"] = chat_drawn("basics-5-manual-other-tab-chat-followed.png")
            checks["the_chat_follows_to_another_tab"] = chat_visible()
            checks["with_the_draft_being_written"] = chat_state()[2].spaces.active.history[-1].body == DRAFT
            # Diagnosis (run 3): that capture showed the split's copied editors where the area list has the chat.
            # The pointer moving over the chat, as a person's would, then a second capture.
            window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=chat.x + chat.width // 2,
                                    y=chat.y + chat.height // 2)
            state["phase"] = "manual_followed_pointer"
            return .6
        if phase == "manual_followed_pointer":
            from angelo_agent import chat_view
            before = chat_view.draw_count
            capture("basics-5b-manual-other-tab-pointer-over-chat.png")
            state["chat_draws_in_capture_after_pointer"] = chat_view.draw_count - before
            state["drawn_after_pointer"] = chat_drawn("basics-5b-manual-other-tab-pointer-over-chat.png")
            press_chat_shortcut()
            state["phase"] = "manual_hidden"
            return .5
        if phase == "manual_hidden":
            if not wait(phase, lambda: settled and chat_state()[0] == "hidden"):
                return .2
            window().workspace = bpy.data.workspaces[state["tabs"][1]]
            state["phase"] = "manual_stays_hidden"
            return 1.2
        if phase == "manual_stays_hidden":
            capture("basics-6-manual-hidden-stays-hidden.png")
            checks["a_hidden_chat_stays_hidden_in_another_tab"] = (window().workspace.name == state["tabs"][1]
                                                                  and chat_state()[0] == "hidden")
            press_chat_shortcut()
            state["phase"] = "manual_shown_again"
            return .5
        if phase == "manual_shown_again":
            if not wait(phase, lambda: settled and chat_state()[0] == "docked"):
                return .2
            window().workspace = bpy.data.workspaces[state["tabs"][3]]   # No 3D view: nothing to dock beside.
            state["phase"] = "manual_no_view"
            return 1.0
        if phase == "manual_no_view":
            checks["a_tab_without_a_3d_view_is_left_alone"] = chat_state()[0] == "hidden"
            window().workspace = bpy.data.workspaces[state["tabs"][2]]
            state["phase"] = "manual_after_no_view"
            return .5
        if phase == "manual_after_no_view":
            if not wait(phase, lambda: settled and chat_visible()):
                return .2
            if not state.pop("settled_once", False):
                state["settled_once"] = True
                return .6
            state["areas_carried"] = areas()
            capture("basics-7-manual-choice-carried.png")
            checks["the_carried_chat_is_drawn"] = chat_drawn("basics-7-manual-choice-carried.png")
            checks["the_choice_carries_to_the_next_tab"] = chat_visible()
            bpy.ops.angelo.intro('INVOKE_DEFAULT')
            state["phase"] = "welcome"
            return 1.0
        if phase == "welcome":
            capture("basics-8-welcome.png")
            finish()
            return None
    except Exception as error:
        finish(error)
        return None
    return .2


bpy.app.timers.register(tick, first_interval=.5)
