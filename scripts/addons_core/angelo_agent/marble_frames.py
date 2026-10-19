# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Marble frames that cost only the mark: the chat's last full draw kept as a texture, the mark drawn over it.

Blender can only redraw a whole region, and the chat's whole draw lays out and draws every visible row. While a
turn waits, a frame the marble timer asked for (``request``) therefore does not run that draw again:

* **Full draw** (``keep``): while a wait runs, the chat draws into an offscreen texture the size of its region,
  with the hero and the live row's icon only *placed* (``marble_draw.placing``: where, under which matrix and
  scissor), never drawn into it. The texture is drawn to the region and the marks over it.
* **Marble frame** (``reuse``): when the timer asked for this frame and nothing the picture shows has changed
  (``key``), the region draws the kept texture (one quad) and the marks at their places.

``key`` is what a frame without new input can change: the region's size and scale, the chat's session, version
(every row and status), attachments, scroll, the draft and its font, the canvas's input serial and pointer states
(hover, press, focus), the scrollbar's visibility, background jobs' shown progress, the interface language and the
hero's size (it shrinks once). A popover or an approval card in view, which animate on their own, is never reused,
and nothing is reused for longer than ``REFRESH`` seconds, so a change no key names is at most that late. Any
other redraw (input, a new row, a resize) is a full draw, as before. The texture lives only while a wait runs
(``release``).
"""

import time

from . import marble_draw

REFRESH = 2.0  # The longest a kept picture is reused (seconds): a full draw every two seconds while the mark moves.
bypass = False  # Diagnostics: draw straight to the region as before (the GUI suite compares the two pictures).
trace = None  # Diagnostics: a list to record each kept draw's framebuffer viewports, before and after (fixtures).
_kept = {}  # area pointer -> {"offscreen", "size", "key", "at", "places"}
_requested = set()  # Areas whose next draw the marble timer asked for.
_shader = None
stats = {"full": 0, "reused": 0, "reused_seconds": 0.0, "full_seconds": 0.0}  # For the GUI fixtures.
frame_cost = 0.0  # A marble frame's recent cost in seconds (moving average): marble_host paces frames by it.


def request(area):
    """The marble timer asks for ``area``'s next draw (``marble_host``)."""
    _requested.add(area.as_pointer())


def release(now=False):
    """The wait ended: no frame is asked for any more. The kept pictures are GPU textures, so they are freed by
    the chat's next draw (``keep``), where a GPU context is current; ``now`` frees them at once (unregister)."""
    _requested.clear()
    if now:
        _kept.clear()


def visible():
    """Whether a kept picture placed any mark in view (True before the first: it is not known yet)."""
    return not _kept or any(entry["places"] for entry in _kept.values())


def key(area, region, controller, state, wait, now):
    """What a frame with no new input could show differently, or None when it may not be reused at all."""
    from . import chat_scrollbar, chat_view, job_ui, ui_text
    if state.get("popover") or any(call.approval for call in tuple(controller.pending.values())):
        return None
    space = area.spaces.active
    history = space.history
    scroll_shown = bool(state.get("scroll_drag") or state.get("scroll_hover")
                        or now - state.get("scroll_time", 0) < chat_scrollbar.LINGER)
    return (region.width, region.height, chat_view.ui_scale(), chat_view._font, ui_text.is_rtl(),
            controller.state["id"], controller.version, str(controller.status), len(controller.attachments),
            chat_view._scroll.get(controller.state["id"]), history[-1].body if history else "", space.font_size,
            state.get("input_serial"), state.get("hover"), state.get("pressed"), state.get("focus"),
            state.get("focused"), state.get("selection"), scroll_shown, tuple(job_ui._shown.values()),
            round(wait.hero_size(now) * 2))


def _blit(offscreen, width, height):
    global _shader
    import gpu
    from gpu_extras.batch import batch_for_shader
    if _shader is None:
        _shader = gpu.shader.from_builtin('IMAGE')
    gpu.state.blend_set('NONE')
    batch = batch_for_shader(_shader, 'TRIS', {"pos": [(0, 0), (width, 0), (width, height), (0, height)],
                                               "texCoord": [(0, 0), (1, 0), (1, 1), (0, 1)]},
                             indices=[(0, 1, 2), (0, 2, 3)])
    _shader.bind()
    _shader.uniform_sampler("image", offscreen.texture_color)
    batch.draw(_shader)


def _wait(controller):
    from . import marble_host
    return marble_host.current(controller) if controller is not None else None


def reuse(area, region):
    """Draw a marble frame from the kept picture: True when it did (the chat's own draw is skipped)."""
    pointer = area.as_pointer()
    asked = pointer in _requested
    _requested.discard(pointer)
    entry = _kept.get(pointer)
    if not asked or entry is None:
        return False
    from . import chat_view, ui
    controller, now = ui._controller, time.monotonic()
    wait = _wait(controller)
    if wait is None or now - entry["at"] > REFRESH or entry["size"] != (region.width, region.height):
        return False
    state = chat_view._views.get(pointer, {})
    if entry["key"] is None or key(area, region, controller, state, wait, now) != entry["key"]:
        return False
    global frame_cost
    started = time.perf_counter()
    _blit(entry["offscreen"], region.width, region.height)
    marble_draw.replay(entry["places"], now)
    spent = time.perf_counter() - started
    stats["reused"] += 1
    stats["reused_seconds"] += spent
    frame_cost = spent if not frame_cost else 0.8 * frame_cost + 0.2 * spent
    return True


def _created_in_region(gpu, size):
    """A new offscreen, with the region's framebuffer bound again afterwards and the framebuffer stack as it was.

    Creating an offscreen ends with Blender's ``GPU_framebuffer_restore()``, which binds the window's own
    framebuffer: every draw after it in this region's draw would land in the window, not the region (the chat's
    first frame of a wait showed Blender's console text). A ``with region.bind():`` around the creation cannot
    undo that: its exit refuses ("Framebuffer is not bound", the window's is bound by then) and leaves the stack
    one deeper on every wait. So the region's framebuffer is pushed by entering its bind, and the new
    offscreen's ``unbind(restore=True)`` pops it and binds it again.
    """
    pushed = gpu.state.active_framebuffer_get().bind()
    pushed.__enter__()   # Pushes the region's framebuffer, which stays bound.
    try:
        offscreen = gpu.types.GPUOffScreen(*size)
    except BaseException:
        pushed.__exit__(None, None, None)   # Nothing was created: the region's is still bound, an ordinary exit.
        raise
    offscreen.unbind(restore=True)   # Pops the region's framebuffer and binds it again.
    return offscreen


class keep:
    """Around the chat's full draw: into a kept picture while a wait runs, then the marks over it."""

    def __init__(self, area, region):
        self.area, self.region, self.offscreen, self.bound = area, region, None, None

    def __enter__(self):
        from . import ui
        self.controller = ui._controller
        self.wait = _wait(self.controller)
        pointer = self.area.as_pointer()
        if self.wait is None or bypass:
            _kept.clear()  # Every chat's: here a GPU context is current to free them in.
            return self
        import gpu
        self.started = time.perf_counter()
        size = (self.region.width, self.region.height)
        entry = _kept.get(pointer)
        if entry is None or entry["size"] != size:
            offscreen = _created_in_region(gpu, size)
            entry = _kept[pointer] = {"offscreen": offscreen, "size": size, "key": None, "at": 0.0, "places": []}
        self.entry = entry
        self.offscreen = entry["offscreen"]
        self.before = list(gpu.state.active_framebuffer_get().viewport_get()) if trace is not None else None
        self.bound = self.offscreen.bind()
        self.bound.__enter__()
        marble_draw.placing.clear()
        marble_draw.placing.append([])
        return self

    def __exit__(self, kind, error, traceback):
        if self.offscreen is None:
            return False
        places = marble_draw.placing.pop() if marble_draw.placing else []
        self.bound.__exit__(kind, error, traceback)
        from . import chat_view
        now = time.monotonic()
        width, height = self.entry["size"]
        _blit(self.offscreen, width, height)
        if trace is not None and len(trace) < 64:  # Where the picture went: the region's own framebuffer.
            import gpu
            trace.append({"before": self.before, "after": list(gpu.state.active_framebuffer_get().viewport_get()),
                          "size": [width, height], "error": chat_view.last_error})
        marble_draw.replay(places, now)
        state = chat_view._views.get(self.area.as_pointer(), {})
        good = kind is None and chat_view.last_error is None
        self.entry.update(places=places, at=now, key=key(self.area, self.region, self.controller, state, self.wait,
                                                         now) if good else None)
        stats["full"] += 1
        stats["full_seconds"] += time.perf_counter() - self.started
        return False
