# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The composer in the running app: its own scroll and its text-field keys, through real window events.

Scroll (at 1x, at a 1.5 interface scale and in Arabic): a 30-row draft opens at the caret; wheel notches and a
trackpad swipe over the composer scroll the draft, stop at either end and never move the transcript behind it (which
has rows to scroll); the scrollbar's thumb drags the draft; a click after scrolling lands on the row drawn there and
the caret is drawn on it (a blink-on/blink-off pixel difference in that row's band); typing after scrolling brings the
caret back only as far as needed; a drag held past the top scrolls on and extends the selection; a long paste opens
at the caret.

Keys: every entry of this platform's ``composer_keys.TABLE`` (and its Shift variant where Shift extends) is sent as a
key event over the composer and its effect compared with ``composer_edit.apply`` on the rows the app drew, with a
capture each; a few are also checked against fixed expectations, double and triple clicks select a word and a
paragraph, the draft's Undo leaves a cube Blender's Undo would remove, and an untouched draft passes the key on to
Blender's Undo; ``composer_gui_review`` adds the later break-it rounds' (Home and the numpad over the 3D view stay
its own, Undo after typing over a selection, Up/Down's column). A keyconfig preset reload follows and the keys work after it.

GUI --factory-startup --enable-event-simulate only; transcript rows are set directly; no provider, account or network.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import time
import types
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

assert '--enable-event-simulate' in sys.argv and not bpy.app.background
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default startup with its chat.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-composer-qa"))
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "composer-gui.json"
state = {"phase": "boot", "success": False, "checks": {}, "measures": {}, "screenshots": []}
started = time.monotonic()
LONG = "\n".join("line %02d" % index for index in range(30))
MATRIX = ("Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon "
          "phi chi psi omega.\nSecond paragraph: can't stop, won't stop.\nThird")


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
    return ui.get_controller()


def line():
    return chat()[0].spaces.active.history[-1]


def scale():
    from angelo_agent import chat_view
    return chat_view.ui_scale()


def at(x, y):
    """Window pixels of a layout point on the composer's text (unmirrored ``x``, as the text field is drawn)."""
    region, s = chat()[1], scale()
    return region.x + x * s, region.y + y * s


def centre():
    x, y, w, h = view_state()["composer"]
    return at(x + w / 2, y + h / 2)


def send(kind, x, y, value='PRESS', **extra):
    if kind in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
        value = 'NOTHING'   # Window.event_simulate refuses any other value for motion (rna_wm_api.cc).
    window().event_simulate(type=kind, value=value, x=int(round(x)), y=int(round(y)), **extra)


def key(kind, **held):
    x, y = centre()
    send(kind, x, y, 'PRESS', **held)
    send(kind, x, y, 'RELEASE', **held)


def redraw():
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)


def pixels(name):
    path = OUT / name
    redraw()
    with bpy.context.temp_override(window=window()):
        bpy.ops.screen.screenshot(filepath=str(path))
    state["screenshots"].append(str(path))
    image = bpy.data.images.load(str(path))
    try:
        width, height = image.size
        values = np.empty(width * height * 4, np.float32)
        image.pixels.foreach_get(values)
        return values.reshape(height, width, 4)
    finally:
        bpy.data.images.remove(image)


def check(name, condition, **measures):
    state["checks"][name] = bool(condition)
    if measures:
        state["measures"][name] = measures


def finish(error=None):
    if error:
        state.update(success=False, error=str(error), traceback=traceback.format_exc())
    else:
        state.update(phase="complete", success=bool(state["checks"]) and all(state["checks"].values()))
    state["elapsed_seconds"] = round(time.monotonic() - started, 1)
    REPORT.write_text(json.dumps(state, indent=2, default=str))
    print("ANGELO_COMPOSER_GUI", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


def set_text(value, caret, anchor=None):
    from angelo_agent import chat_composer
    chat_composer.set_draft(line(), value, caret)
    view_state()["selection"] = (min(anchor, caret), max(anchor, caret)) if anchor not in (None, caret) else None
    redraw()


def shown():
    return [value.rstrip() for value, _, _ in view_state().get("draft_rows", [])]


def transcript():
    from angelo_agent import chat_view
    return chat_view._scroll.get((chat()[0].as_pointer(), controller().state["id"]), 0)


def blink(visible):
    from angelo_agent import chat_composer
    chat_composer.time = SimpleNamespace(monotonic=lambda: 1000.25 if visible else 1000.75)


def primary():
    return {"oskey": True} if sys.platform == "darwin" else {"ctrl": True}


# -- the composer's own scroll ----------------------------------------------------------------------------------

def scroll_steps(label):
    from angelo_agent import chat_composer
    region = chat()[1]
    s = scale()
    set_text(LONG, len(LONG))
    yield 0.2
    check(label + "_a_long_draft_opens_at_the_caret", shown() == ["line %02d" % i for i in range(22, 30)],
          shown=shown())
    pixels(label + "-open.png")
    before = transcript()
    x, y = centre()
    send('MOUSEMOVE', x, y)
    send('WHEELUPMOUSE', x, y)
    yield 0.2
    redraw()
    check(label + "_a_wheel_notch_scrolls_the_draft_three_rows", shown()[0] == "line 19", shown=shown())
    pixels(label + "-wheel.png")
    for _ in range(12):
        send('WHEELUPMOUSE', x, y)
    yield 0.3
    redraw()
    check(label + "_the_wheel_stops_at_the_top", shown()[0] == "line 00", shown=shown())
    for _ in range(12):
        send('WHEELDOWNMOUSE', x, y)
    yield 0.3
    redraw()
    check(label + "_the_wheel_stops_at_the_bottom", shown()[-1] == "line 29", shown=shown())
    pitch = view_state()["composer_view"]["line_height"]
    dy = int(round(2.5 * pitch * s))
    send('MOUSEMOVE', x, y)
    send('TRACKPADPAN', x, y - dy)   # The fingers' travel, after the system's direction: the view moves up.
    send('MOUSEMOVE', x, y)
    yield 0.3
    redraw()
    rows = int(dy / s / pitch)   # Whole rows; the rest carries to the next swipe.
    check(label + "_a_trackpad_swipe_scrolls_the_draft_as_blenders_views", rows >= 2
          and shown()[0] == "line %02d" % (22 - rows), shown=shown(), rows=rows)
    check(label + "_the_transcript_behind_never_moved", transcript() == before, before=before, after=transcript())
    # The scrollbar: shown after a scroll; its thumb drags the draft to the top.
    send('WHEELUPMOUSE', x, y)
    yield 0.2
    redraw()
    bar = view_state().get("composer_bar")
    check(label + "_the_scrollbar_shows_after_a_scroll", bool(bar))
    if bar:
        image = pixels(label + "-bar.png")
        track, thumb = bar
        tx, ty = at(thumb[0] + thumb[2] / 2, thumb[1] + thumb[3] / 2)
        card = at(view_state()["composer"][0] + 30, thumb[1] + thumb[3] / 2)
        lighter = image[int(ty), int(tx), :3].mean() - image[int(card[1]), int(card[0]), :3].mean()
        check(label + "_the_thumb_is_drawn_lighter_than_the_card", lighter > 0.05, difference=float(lighter))
        qa_input.send(window(), qa_input.drag_path((tx, ty), (tx, ty + 210 * s), s))   # Up: toward the start.
        yield 0.3
        redraw()
        check(label + "_dragging_the_thumb_scrolls_the_draft", shown()[0] == "line 00", shown=shown())
        check(label + "_the_bar_never_moved_the_caret", line().current_character == len(LONG))
    # A click after scrolling: on the row drawn there, and the caret drawn on it.
    rows_drawn = view_state()["draft_rows"]
    value, offset, row_y = rows_drawn[3]
    size, drawn = view_state()["font_size"], view_state()["text_scale"]
    cx, cy = at(chat_composer.column_x(value, 4, size, drawn) + 0.5, row_y + size * 0.35)
    send('MOUSEMOVE', cx, cy)
    send('LEFTMOUSE', cx, cy, 'PRESS')
    send('LEFTMOUSE', cx, cy, 'RELEASE')
    yield 0.3
    check(label + "_a_click_after_scrolling_lands_on_the_row_drawn_there",
          line().current_character == offset + 4, caret=line().current_character, expected=offset + 4)
    blink(True)
    shown_image = pixels(label + "-caret.png")
    blink(False)
    hidden_image = pixels(label + "-no-caret.png")
    blink(True)
    lo, hi = int(region.y + (row_y - 3) * s), int(region.y + (row_y + size + 2) * s) + 1
    left, right = int(region.x + 24 * s), int(region.x + (24 + view_state()["composer"][2]) * s)
    differs = np.abs(shown_image[lo:hi, left:right, :3] - hidden_image[lo:hi, left:right, :3]).sum(axis=2) > 0.3
    columns = np.flatnonzero(differs.sum(axis=0) >= max(2, (hi - lo) // 2)) + left
    check(label + "_the_caret_is_drawn_on_the_clicked_row", len(columns) > 0 and abs(columns.min() - cx) <= 3 * s,
          columns=[int(c) for c in columns[:4]], click=cx)
    chat_composer.time = time
    # Typing after scrolling away: the caret's row comes back into view, at the edge, not centred.
    set_text(LONG, len(LONG))
    yield 0.2
    for _ in range(12):
        send('WHEELUPMOUSE', x, y)
    yield 0.2
    send('Q', x, y, 'PRESS', unicode="q")
    yield 0.3
    redraw()
    check(label + "_typing_brings_the_caret_back_only_as_far_as_needed", shown()[-1] == "line 29q", shown=shown())
    # A drag held past the top scrolls on and extends the selection.
    for _ in range(4):
        send('WHEELUPMOUSE', x, y)
    yield 0.2
    redraw()
    first = view_state()["composer_first"]
    value, offset, row_y = view_state()["draft_rows"][2]
    px, py = at(chat_composer.column_x(value, 2, size, drawn) + 0.5, row_y + size * 0.35)
    above = region.y + (view_state()["composer_view"]["rect"][1] + view_state()["composer_view"]["rect"][3] + 30) * s
    qa_input.send(window(), qa_input.drag_path((px, py), (px, above), s, release=False))
    yield 0.6
    held = view_state()["composer_first"]
    qa_input.send(window(), [qa_input.release_at((px, above))])
    yield 0.3
    selection = view_state().get("selection") or (None, None)
    check(label + "_a_drag_past_the_top_scrolls_on_while_held", held <= first - 3, first=first, held=held)
    check(label + "_and_the_selection_reaches_the_top_row_shown", selection[1] == offset + 2
          and selection[0] is not None and selection[0] < LONG.index("line %02d" % first), selection=selection)
    after = view_state()["composer_first"]
    yield 0.3
    check(label + "_the_scroll_stops_when_the_button_goes_up", view_state()["composer_first"] == after)
    pixels(label + "-drag-select.png")
    # A long paste opens at the caret.
    set_text("start", 5)
    yield 0.2
    bpy.context.window_manager.clipboard = "\n" + "\n".join("paste %02d" % i for i in range(40))
    key('V', **primary())
    yield 0.3
    redraw()
    check(label + "_a_long_paste_opens_at_the_caret", shown()[-1] == "paste 39"
          and line().current_character == len(line().body), shown=shown()[-2:])
    pixels(label + "-paste.png")
    check(label + "_the_transcript_never_moved_in_any_of_it", transcript() == before, after=transcript())


# -- the text-field keys -----------------------------------------------------------------------------------------

def entries():
    from angelo_agent import composer_keys
    platform = composer_keys.platform_of()
    for kind, modifiers, action, extends in composer_keys.TABLE[platform]:
        held = {composer_keys._EVENT_FLAG[m]: True for m in modifiers.split()}
        yield kind, held, action, extends and "shift" in modifiers.split()
        if extends and "shift" not in modifiers.split():
            yield kind, dict(held, shift=True), action, True


def prepare(action):
    """The draft, caret and selection an entry starts from; scroll and page keys get the long draft."""
    long = action.startswith(("scroll_", "page_"))
    value = LONG if long else MATRIX
    set_text(value, 0)
    rows = view_state()["draft_wrap"](value)
    caret = rows[15][1] + 3 if long else rows[min(1, len(rows) - 1)][1] + 4
    anchor = caret - 4 if action in {"copy", "cut"} else None
    set_text(value, caret, anchor)
    view_state()["composer_kill"] = "KILLED"
    bpy.context.window_manager.clipboard = "PASTE"
    return value, caret, anchor


def key_steps(label):
    from angelo_agent import chat_composer, composer_edit, composer_keys, composer_scroll
    platform = composer_keys.platform_of()
    state["platform"] = platform
    for index, (kind, held, action, extend) in enumerate(entries()):
        name = "%s_%s_%s" % (label, kind.lower(), "+".join(sorted(held)) or "plain")
        if action in {"undo", "redo"}:
            set_text("", 0)
            yield 0.1
            for char in "ab":
                send('A', *centre(), 'PRESS', unicode=char)
            yield 0.2
            if action == "redo":
                key('Z', **primary())
                yield 0.1
            key(kind, **held)
            yield 0.2
            expected = "" if action == "undo" else "ab"
            check(name + "_" + action, line().body == expected, body=line().body)
            continue
        value, caret, anchor = prepare(action)
        yield 0.1
        rows = view_state()["draft_wrap"](value)
        size, drawn = view_state()["font_size"], view_state()["text_scale"]
        first = view_state()["composer_first"]
        edit = composer_edit.apply(action, extend, composer_edit.Draft(value, caret, anchor), rows,  # Not upstream.
                                   lambda c, up, row: chat_composer.vertical_caret(rows, c, up, size, drawn, row),
                                   platform,
                                   clipboard="PASTE", kill="KILLED")
        key(kind, **held)
        yield 0.15
        selection = view_state().get("selection")
        got = (line().body, line().current_character, selection)
        want = (edit.draft.value, edit.draft.caret, edit.draft.selection)
        ok = got == want
        if edit.scroll:
            target = {"top": 0, "bottom": len(rows), "page_up": first - 7, "page_down": first + 7}[edit.scroll]
            ok = ok and view_state()["composer_first"] == composer_scroll.clamp(target, len(rows))
        if edit.clipboard is not None:
            ok = ok and bpy.context.window_manager.clipboard == edit.clipboard
        check(name + "_" + action + ("_extends" if extend else ""), ok, got=got, want=want)
        pixels("%s-key-%02d-%s.png" % (label, index, action))
    # Fixed expectations for the keys people name first (the table's own oracle above checks the wiring).
    set_text(MATRIX, 0)
    rows = view_state()["draft_wrap"](MATRIX)
    set_text(MATRIX, rows[1][1] + 4)
    key('LEFT_ARROW', oskey=True) if platform == "mac" else key('HOME')
    yield 0.1
    check(label + "_line_start_is_the_visual_rows_start", line().current_character == rows[1][1])
    key('DOWN_ARROW', oskey=True) if platform == "mac" else key('END', ctrl=True)
    yield 0.1
    check(label + "_draft_bottom", line().current_character == len(MATRIX))
    key('LEFT_ARROW', alt=True) if platform == "mac" else key('LEFT_ARROW', ctrl=True)
    yield 0.1
    check(label + "_word_left", line().current_character == len(MATRIX) - len("Third"))
    # Double and triple clicks.
    set_text(MATRIX, 0)
    yield 0.1
    value, offset, row_y = view_state()["draft_rows"][0]
    size, drawn = view_state()["font_size"], view_state()["text_scale"]
    cx, cy = at(chat_composer.column_x(value, 8, size, drawn) + 0.5, row_y + size * 0.35)
    send('MOUSEMOVE', cx, cy)
    for _ in range(2):
        send('LEFTMOUSE', cx, cy, 'PRESS')
        send('LEFTMOUSE', cx, cy, 'RELEASE')
    yield 0.2
    check(label + "_a_double_click_selects_the_word", view_state().get("selection") == (6, 10),
          selection=view_state().get("selection"))
    pixels(label + "-double-click.png")
    send('LEFTMOUSE', cx, cy, 'PRESS')
    send('LEFTMOUSE', cx, cy, 'RELEASE')
    yield 0.2
    check(label + "_a_triple_click_selects_the_paragraph",
          view_state().get("selection") == (0, MATRIX.index("\n") + 1), selection=view_state().get("selection"))
    pixels(label + "-triple-click.png")
    yield 0.6   # Past the double-click time: the next click starts afresh.


def undo_steps():
    """The draft's Undo never undoes the scene; an untouched draft leaves the key to Blender's Undo, as before."""
    with bpy.context.temp_override(window=window()):
        bpy.ops.mesh.primitive_cube_add('EXEC_DEFAULT', True)
    cube = bpy.context.active_object.name
    set_text("", 0)
    yield 0.2
    for char in "abc":
        send('A', *centre(), 'PRESS', unicode=char)
    yield 0.2
    for _ in range(3):
        key('Z', **primary())
    yield 0.4
    check("draft_undo_never_reaches_the_scene", line().body == "" and cube in bpy.data.objects,
          body=line().body, cube=cube in bpy.data.objects)
    set_text("left as it was", 0)   # Changed outside the composer (as a sent message is): a new, empty history.
    key('Z', **primary())
    yield 0.6
    check("an_untouched_draft_leaves_undo_to_blender", cube not in bpy.data.objects and line().body == "left as it was")


def steps():
    from angelo_agent import console
    yield lambda: any(console.is_chat(a) for a in window().screen.areas)
    controller().state["messages"][:] = [{"role": "user" if i % 2 else "assistant", "text": "Row %d " % i + "words " * 30}
                                         for i in range(40)]
    controller().version += 1
    yield lambda: view_state().get("composer", (0, 0, 0, 0))[2] > 0 and view_state().get("max_scroll", 0) > 200
    x, y = centre()
    send('MOUSEMOVE', x, y)
    send('LEFTMOUSE', x, y, 'PRESS')
    send('LEFTMOUSE', x, y, 'RELEASE')
    yield 0.3
    state["phase"] = "scroll 1x"
    yield from scroll_steps("x1")
    state["phase"] = "keys"
    yield from key_steps("keys")
    state["phase"] = "undo"
    yield from undo_steps()
    state["phase"] = "review round 3"
    import composer_gui_review
    # Blender runs this file with --python into its own namespace, not sys.modules['__main__']: hand over its globals.
    yield from composer_gui_review.steps(types.SimpleNamespace(**globals()))
    state["phase"] = "preset reload"
    preset = bpy.utils.preset_find("Blender", "keyconfig")
    bpy.ops.preferences.keyconfig_activate(filepath=preset)
    yield 0.8
    yield from key_steps("after_preset_reload")
    bpy.context.preferences.view.ui_scale = 1.5
    yield 0.6
    state["phase"] = "scroll 1.5x"
    yield from scroll_steps("x15")
    bpy.context.preferences.view.ui_scale = 1.0
    view = bpy.context.preferences.view
    languages = {item.identifier for item in view.bl_rna.properties["language"].enum_items}
    check("arabic_is_available", bpy.app.build_options.international and "ar_EG" in languages)
    if "ar_EG" in languages:
        view.language, view.use_translate_interface = "ar_EG", True
        yield 0.8
        state["phase"] = "scroll arabic"
        yield from scroll_steps("arabic")
        view.language = "en_US"


_steps = steps()
_waiting = {}


def tick():
    try:
        if time.monotonic() - started > 480:
            raise RuntimeError("Timed out in " + state["phase"])
        condition = _waiting.get("condition")
        if condition is not None and not condition():
            return 0.1
        step = next(_steps)
        _waiting["condition"] = step if callable(step) else None
        return 0.1 if callable(step) else step
    except StopIteration:
        finish()
    except Exception as error:
        finish(error)
    return None


sys.path.insert(0, str(Path(__file__).resolve().parent))  # qa_boot and qa_input, inside the app.
import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_input  # noqa: E402  (drags as a person makes them)

bpy.app.timers.register(qa_boot.gated(tick), first_interval=1.0, persistent=True)
