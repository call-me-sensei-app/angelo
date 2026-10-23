# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Window helpers for ``test_blender_update_crash``: input on a card's own geometry, screenshots and pixels.

Every input is a real window event (``Window.event_simulate``). Blender queues those events and handles
them only after the current timer callback returns, so the suite is a generator that **yields** between
an input and the screenshot that should show its effect; nothing here sleeps. Coordinates are window
pixels with y up, the same space as the screenshot (read back here with y flipped).
"""

import os
from pathlib import Path

import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR", "/tmp/angelo-update-crash-qa"))
shots = []


def window():
    return bpy.context.window_manager.windows[0]


def host():
    from angelo_agent import card_host
    return card_host


def active(kind):
    """The card of this kind is on screen and laid out (its plan exists after the first draw)."""
    return host().is_active(kind) and host()._active.plan is not None


def card():
    return host().active_card()


def card_region():
    state = host()._active
    area = next(a for a in window().screen.areas if a.as_pointer() == state.area)
    return area, next(r for r in area.regions if r.type == 'WINDOW'), state


def hit_rect(action, value=None):
    """A clickable control's rectangle in window pixels (enabled controls only)."""
    _, region, state = card_region()
    x, y, w, h = next(r for r, a, v in state.plan.hits if a == action and (value is None or v == value))
    return region.x + x, region.y + y, w, h


def button_rect(label):
    """Any button's rectangle by label, disabled ones included (they have no hit area)."""
    _, region, state = card_region()
    x, y, w, h = next(op[1] for op in state.plan.ops if op[0] == "button" and op[2] == label)
    return region.x + x, region.y + y, w, h


def button_state(label):
    _, _, state = card_region()
    return next(op[4] for op in state.plan.ops if op[0] == "button" and op[2] == label)


def labels():
    _, _, state = card_region()
    return [op[2] for op in state.plan.ops if op[0] == "button"]


def text():
    """Everything the card writes, wrapped lines joined with spaces (for sentence checks)."""
    _, _, state = card_region()
    return " ".join(op[1].strip() for op in state.plan.ops if op[0] in {"text", "mono"})


def center(rect):
    return int(rect[0] + rect[2] / 2), int(rect[1] + rect[3] / 2)


def move_to(x, y):
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x) - 3, y=int(y))
    window().event_simulate(type='MOUSEMOVE', value='NOTHING', x=int(x), y=int(y))


def press_at(x, y):
    window().event_simulate(type='LEFTMOUSE', value='PRESS', x=int(x), y=int(y))


def release_at(x, y):
    window().event_simulate(type='LEFTMOUSE', value='RELEASE', x=int(x), y=int(y))


def click_at(x, y):
    move_to(x, y)
    press_at(x, y)
    release_at(x, y)


def click(action, value=None):
    click_at(*center(hit_rect(action, value)))


def key(type_, shift=False):
    """A key press with the pointer over the 3D view but off the card (a card handles keys only there)."""
    _, region, _ = card_region()
    x, y = int(region.x + region.width * 0.9), int(region.y + region.height * 0.9)
    window().event_simulate(type=type_, value='PRESS', shift=shift, x=x, y=y)
    window().event_simulate(type=type_, value='RELEASE', shift=shift, x=x, y=y)


def park_pointer():
    """Over the 3D view's empty top-left, away from every card."""
    _, region, _ = card_region()
    move_to(region.x + 60, region.y + region.height - 60)


def capture(name, target=None):
    """Redraw and save a window. Call it after a ``yield``, so the events sent before it have been handled."""
    path = OUT / name
    with bpy.context.temp_override(window=target or window()):
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=2)
        bpy.ops.screen.screenshot(filepath=str(path))
    shots.append(str(path))
    return path


_cache = {}


def pixels(path):
    """``rows[y][x] -> (r, g, b)`` 0-255, y up, decoded with Blender's own image loader (cached per file)."""
    if path in _cache:
        return _cache[path]
    import numpy as np
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        data = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(data)
    finally:
        bpy.data.images.remove(image)
    _cache[path] = data.reshape(height, width, 4)[:, :, :3] * 255
    return _cache[path]


def color_at(path, x, y, radius=1):
    """The mean colour of a small square around window pixel (x, y)."""
    block = pixels(path)[int(y) - radius:int(y) + radius + 1, int(x) - radius:int(x) + radius + 1]
    return tuple(round(float(v), 1) for v in block.reshape(-1, 3).mean(axis=0))


def fill_of(path, rect):
    """A button's fill: left of its centred label, inside the rounded end."""
    return color_at(path, rect[0] + rect[3] * 0.45, rect[1] + rect[3] / 2)


def ring_of(path, rect):
    """Where the focus ring is drawn: just below the control's bottom edge, at its middle."""
    return color_at(path, rect[0] + rect[2] / 2, rect[1] - 2, radius=0)


def close_to(a, b, tolerance=12):
    return all(abs(p - q) <= tolerance for p, q in zip(a, b))


def brightness(color):
    return sum(color)


def same_pixels(a, b, rect):
    """True when two screenshots are identical inside ``rect`` (window pixels)."""
    import numpy as np
    x, y, w, h = (int(v) for v in rect)
    return bool(np.array_equal(pixels(a)[y:y + h, x:x + w], pixels(b)[y:y + h, x:x + w]))


def bright_pixels(path, rect, threshold=600):
    """How many near-white pixels (text) a screenshot has inside ``rect`` (window pixels)."""
    x, y, w, h = (int(v) for v in rect)
    return int((pixels(path)[y:y + h, x:x + w].sum(axis=2) > threshold).sum())


def peak(path, rect):
    """The brightest pixel inside ``rect`` (window pixels), as a 0-765 sum: a label on a button."""
    x, y, w, h = (int(v) for v in rect)
    return float(pixels(path)[y:y + h, x:x + w].sum(axis=2).max())


def card_rect():
    _, region, state = card_region()
    x, y, w, h = state.plan.card
    return region.x + x, region.y + y, w, h


def chat_fonts():
    """Angelo's own font IDs (``chat_view._font``/``_mono``) with the width each measures for a sample string at the
    size the canvases use: an ID that names no font measures 0."""
    import blf
    from angelo_agent import chat_view
    fonts = {}
    for name in ("_font", "_mono"):
        font = getattr(chat_view, name)
        blf.size(font, 12)
        fonts[name] = (font, round(blf.dimensions(font, "Angelo 1.2 update")[0], 2))
    return fonts


def free_a_chat_font():
    """Frees one of Angelo's own fonts for real, as Blender's interface reload did before 1.0.5 kept a script's
    fonts: ``blf.unload`` on its file until the ID no longer measures (``blf.load`` counts references). blf has no
    ID-to-file lookup, so the files are recorded from a real ``chat_view.reload_fonts``. Returns
    ``{"font", "file", "freed"}`` for the first font Angelo loaded itself, or None when it draws only with fonts
    Blender loaded (nothing of Angelo's to free)."""
    import blf
    from angelo_agent import chat_view, font_watch
    seen, load = {}, blf.load

    def recording(path):
        font = load(path)
        seen[font] = path
        return font

    blf.load = recording
    try:
        chat_view.reload_fonts()
    finally:
        blf.load = load
    font = next((f for f in (chat_view._font, chat_view._mono) if f and f in seen), None)
    if font is None:
        return None
    for _ in range(256):   # Bounded: every load of the file since start-up holds one reference.
        if font_watch.lost(font):
            break
        blf.unload(seen[font])
    return {"font": font, "file": os.path.basename(seen[font]), "freed": font_watch.lost(font)}


def file_browser():
    """``(window, area)`` of an open File Browser, or None."""
    for candidate in bpy.context.window_manager.windows:
        for area in candidate.screen.areas:
            if area.type == 'FILE_BROWSER':
                return candidate, area
    return None


def settle(ticks=2):
    """Let Blender handle the events sent so far (they wait until the timer callback returns)."""
    for _ in range(ticks):
        yield


def until(name, condition, limit=60):
    """Yield until ``condition()`` holds; fail the fixture after ``limit`` seconds."""
    import time
    began = time.monotonic()
    while not condition():
        assert time.monotonic() - began < limit, "Timed out waiting for " + name
        yield
