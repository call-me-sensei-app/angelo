# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scroll direction and the composer caret in the running app, through real window events.

Scroll: one simulated trackpad swipe goes to the chat transcript and to a stock Blender Outliner (``view2d``), then
the opposite swipe. Both must move their views the same way, and the transcript must follow the swipe one window
pixel per pixel. Blender's own views follow the person's system setting, so matching them is the requirement.

Caret: drafts are typed as key events (long wrapped rows, accented, Japanese and emoji text), then a real click
places the caret. The click goes on the drawn glyphs: the right ink edge of a rendering of just the text before the
target column. Three checks follow. The caret index must be that column, read from the real ``ConsoleLine``. The
drawn caret (found by a blink-on/blink-off pixel difference) must lie between that prefix's ink and the next
glyph's ink. A typed character must land there. The checks repeat at the display's own scale and at two
Resolution Scales.

GUI --factory-startup --enable-event-simulate only; transcript rows are set directly; no provider, account or
network.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy
import numpy as np

assert '--enable-event-simulate' in sys.argv and not bpy.app.background
from angelo_agent import product_env  # noqa: E402  (bundled add-on path)

product_env.clear('DISABLE_DEFAULT_CHAT')  # The default startup with its chat.
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-chat-scroll-caret-qa"))
OUT.mkdir(parents=True, exist_ok=True)
bpy.context.preferences.view.show_splash = False
bpy.context.preferences.use_preferences_save = False
REPORT = OUT / "chat-scroll-caret.json"
state = {"phase": "boot", "success": False, "checks": {}, "measures": {}, "screenshots": []}
started = time.monotonic()
DRAFTS = (
    "The roof should be steeper, then add a brick chimney on the left side, two windows facing the garden "
    "path and a small porch light above the door",
    "Café crème brûlée façade naïve über 日本語のテキスト and 🧊 ice cubes, then the pâtissière's déjà-vu "
    "reçu: naïveté, Ångström, Øresund and 東京の夜景 again, with more plain words after them so the draft wraps",
)
# Preferences > Interface > Resolution Scale, multiplied by the display's pixel size: 2.0 is a Retina display's
# factor where the window has none (a non-Retina display, or a window kept behind), the fractions round differently.
SCALES = (1.0, 2.0, 1.37, 0.85)


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


def send(kind, x, y, value='NOTHING', unicode=None):
    arguments = dict(type=kind, value=value, x=int(round(x)), y=int(round(y)))
    if unicode:
        arguments["unicode"] = unicode
    window().event_simulate(**arguments)


def redraw():
    with bpy.context.temp_override(window=window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)


def pixels(name):
    """The window as drawn, ``(height, width, 4)`` floats, y up; saved as ``name`` for the record."""
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
    print("ANGELO_CHAT_SCROLL_CARET", json.dumps({"success": state["success"], "phase": state["phase"]}), flush=True)
    bpy.ops.wm.quit_blender()


# -- scroll ----------------------------------------------------------------------------------------------------

_outliner = {}


def outliner_top():
    """The Outliner this fixture made (the factory layout has one of its own): its region and top view row."""
    area = next(a for a in window().screen.areas if a.as_pointer() == _outliner["pointer"])
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return region, region.view2d.region_to_view(0, region.height - 1)[1]


def swipe(region, dy):
    """A two-finger swipe over ``region``: the window manager's delta is ``xy - prev_xy`` = ``dy``."""
    x, y = region.x + region.width / 2, region.y + region.height / 2
    send('MOUSEMOVE', x, y)
    send('TRACKPADPAN', x, y + dy)
    send('MOUSEMOVE', x, y)


def scroll_steps():
    from angelo_agent import chat_view
    area3d = next(a for a in window().screen.areas if a.type == 'VIEW_3D')
    before = {a.as_pointer() for a in window().screen.areas}
    with bpy.context.temp_override(window=window(), area=area3d):
        bpy.ops.screen.area_split(direction='VERTICAL', factor=0.35)
    split = next(a for a in window().screen.areas if a.as_pointer() not in before)
    split.ui_type = 'OUTLINER'
    _outliner["pointer"] = split.as_pointer()
    for index in range(160):
        bpy.context.scene.collection.objects.link(bpy.data.objects.new("Scroll row %03d" % index, None))
    controller().state["messages"][:] = [{"role": "user" if i % 2 else "assistant", "text": "Row %d " % i + "words " * 30}
                                         for i in range(40)]
    controller().version += 1
    yield lambda: view_state().get("max_scroll", 0) > 400
    region, top = outliner_top()
    # Every swipe stays inside both regions (the pan's end point must be over the region it scrolls).
    distance = int(min(120, outliner_top()[0].height / 3, chat()[1].height / 3))
    swipe(region, distance)   # Blender's view down first: an Outliner at its top cannot move further up.
    yield 0.3
    identity = controller().state["id"]
    s = scale()
    for label, dy in (("view_up", -distance), ("view_down", distance)):
        region, top = outliner_top()
        before_chat = chat_view._scroll.get(identity, 0)
        if label == "view_down":
            chat_view._scroll[identity] = before_chat = 300  # Room to move toward the newest rows.
        swipe(region, dy)
        yield 0.3
        moved_outliner = outliner_top()[1] - top          # View space: positive is the view moving up.
        swipe(chat()[1], dy)
        yield 0.3
        moved_chat = chat_view._scroll.get(identity, 0) - before_chat   # Rows up from the newest: up is positive.
        check("swipe_" + label + "_moves_the_chat_as_blender_moves_its_outliner",
              moved_outliner and moved_chat and (moved_outliner > 0) == (moved_chat > 0),
              outliner=moved_outliner, chat=moved_chat, dy=dy)
        check("swipe_" + label + "_moves_the_transcript_one_window_pixel_per_pixel",
              abs(abs(moved_chat) * s - abs(dy)) <= 1.5, chat_window_pixels=moved_chat * s, dy=dy, scale=s)
    chat_view._scroll[identity] = 0
    controller().state["messages"][:] = [{"role": "user", "text": "Type below."}]
    controller().version += 1


# -- caret -----------------------------------------------------------------------------------------------------

def band(row_y, size):
    """Window rows of a draft row's glyphs and caret (the caret spans ``y - 3 .. y + line_height - 6``)."""
    region, s = chat()[1], scale()
    return int(region.y + (row_y - 3) * s), int(region.y + (row_y + size + 2) * s) + 1


def composer_columns():
    """Window columns of the composer card: nothing else on those rows (other editors, the status bar) counts."""
    region, s = chat()[1], scale()
    x, _, width, _ = view_state()["composer"]
    return int(region.x + x * s), int(region.x + (x + width) * s)


def ink_columns(image, rows):
    (lo, hi), (left, right) = rows, composer_columns()
    lit = image[lo:hi, left:right, :3].mean(axis=2) > 0.6     # Light text on the composer's dark card.
    return np.flatnonzero(lit.any(axis=0)) + left


def caret_columns(shown, hidden, rows):
    (lo, hi), (left, right) = rows, composer_columns()
    differs = np.abs(shown[lo:hi, left:right, :3] - hidden[lo:hi, left:right, :3]).sum(axis=2) > 0.3
    return np.flatnonzero(differs.sum(axis=0) >= max(2, (hi - lo) // 2)) + left


def blink(visible):
    from angelo_agent import chat_composer
    chat_composer.time = SimpleNamespace(monotonic=lambda: 1000.25 if visible else 1000.75)


def caret_steps(draft, label):
    from angelo_agent import chat_composer
    region = chat()[1]
    chat_composer.set_draft(line(), "", 0)
    redraw()
    composer = view_state()["composer"]
    s = scale()
    cx, cy = region.x + (composer[0] + 20) * s, region.y + (composer[1] + composer[3] - 20) * s
    send('MOUSEMOVE', cx, cy)
    send('LEFTMOUSE', cx, cy, 'PRESS')
    send('LEFTMOUSE', cx, cy, 'RELEASE')
    yield 0.2
    for character in draft:
        send('A', cx, cy, 'PRESS', unicode=character)
    deadline = time.monotonic() + 10
    yield lambda: line().body == draft or time.monotonic() > deadline
    check(label + "_typed_draft_arrives_whole", line().body == draft, typed=line().body)
    if line().body != draft:
        return   # Nothing below means anything on a draft that did not arrive as typed.
    redraw()
    rows = view_state()["draft_rows"]
    size = view_state()["font_size"]
    check(label + "_draft_wraps_to_several_rows", len(rows) >= 2, rows=len(rows))
    targets = []
    for index, (value, offset, row_y) in enumerate(rows[:2]):
        # Columns right after a glyph that draws ink (a letter, kana or kanji; not a space or an emoji, which may
        # draw nothing without its font): one word end mid-row and the last letter boundary of the row.
        ends = [i for i in range(1, len(value)) if value[i] == " " and value[i - 1].isalnum()]
        last = len(value.rstrip()) - 1
        for column in ([ends[len(ends) // 2]] if ends else []) + ([last] if value[last - 1].isalnum() else []):
            targets.append((offset + column, index, offset, row_y))
    check(label + "_has_caret_targets_on_two_rows", len({index for _, index, _, _ in targets}) == 2, targets=len(targets))
    for caret, index, offset, row_y in targets:
        rows_px = band(row_y, size)
        # The glyphs before the caret alone, drawn by the app: where they end is where the caret belongs. Fewer rows
        # make a shorter composer, so that rendering's row sits elsewhere: read it from its own draw.
        chat_composer.set_draft(line(), draft[:caret], caret)
        blink(False)
        prefix = pixels("%s-%d-prefix.png" % (label, caret))
        prefix_rows = view_state()["draft_rows"]
        check("%s_caret_%d_prefix_wraps_as_the_draft" % (label, caret), len(prefix_rows) > index,
              rows=len(prefix_rows), index=index)
        if len(prefix_rows) <= index:
            continue
        prefix_ink = ink_columns(prefix, band(prefix_rows[index][2], size))
        chat_composer.set_draft(line(), draft, len(draft))
        full = pixels("%s-%d-full.png" % (label, caret))
        full_ink = ink_columns(full, rows_px)
        left_edge = prefix_ink.max()
        later = full_ink[full_ink > left_edge + 1]
        right_edge = later.min() if len(later) else left_edge + 6 * s
        click_x = left_edge + 1
        click_y = region.y + (row_y + size * 0.35) * s
        send('MOUSEMOVE', click_x, click_y)
        send('LEFTMOUSE', click_x, click_y, 'PRESS')
        send('LEFTMOUSE', click_x, click_y, 'RELEASE')
        yield 0.2
        landed = chat_composer.cursor_position(line())
        name = "%s_caret_%d" % (label, caret)
        check(name + "_click_on_the_glyph_edge_puts_the_caret_there", landed == caret, landed=landed, expected=caret)
        blink(True)
        shown = pixels("%s-%d-caret.png" % (label, caret))
        blink(False)
        hidden = pixels("%s-%d-no-caret.png" % (label, caret))
        columns = caret_columns(shown, hidden, rows_px)
        drawn = int(columns.min()) if len(columns) else None
        tolerance = 1.5 * s
        check(name + "_drawn_caret_sits_between_the_glyphs", drawn is not None
              and left_edge - tolerance <= drawn <= right_edge + tolerance,
              drawn=drawn, prefix_ink_right=int(left_edge), next_ink_left=int(right_edge), scale=s)
        send('A', click_x, click_y, 'PRESS', unicode="Z")
        yield 0.2
        check(name + "_typing_inserts_where_the_caret_is_drawn", line().body == draft[:caret] + "Z" + draft[caret:])
        chat_composer.set_draft(line(), draft, len(draft))
    blink(True)
    chat_composer.time = time


def steps():
    from angelo_agent import console
    yield lambda: any(console.is_chat(a) for a in window().screen.areas)
    controller().state["messages"][:] = [{"role": "user", "text": "Type below."}]
    controller().version += 1
    yield lambda: view_state().get("composer", (0, 0, 0, 0))[2] > 0
    state["display_scale"] = scale()
    state["phase"] = "scroll"
    yield from scroll_steps()
    for resolution in SCALES:
        bpy.context.preferences.view.ui_scale = resolution
        yield 0.5
        state["phase"] = "caret %.2f" % resolution
        for index, draft in enumerate(DRAFTS):
            yield from caret_steps(draft, "scale%.2f_draft%d" % (resolution, index))
    bpy.context.preferences.view.ui_scale = 1.0


_steps = steps()
_waiting = {}


def tick():
    try:
        if time.monotonic() - started > 400:
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


bpy.app.timers.register(tick, first_interval=1.0, persistent=True)
