# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A one-time hint in Manual mode saying how to show and hide the Angelo chat (doc/angelo/agent_chat.rst, "Show and
hide").

Evidence: 1.0.2 added the top bar's toggle, yet people still could not find the chat in Manual mode and the
maintainer had to ask where the toggle was (2026-10-20). A bare icon beside the mode menu is easy to miss, and a chat
that disappeared (the toggle, the shortcut, Blender's Close Area, its pop-out window closing) left no trace of how to
bring it back. So the toggle now carries the word Chat (``chat_toggle.draw``), and this card, standing under the
toggle with an arrow at it (the maintainer's follow-up: anchored next to the button, worded by where it really is),
shows once:

* **manual**: the first time the person is in Manual mode, whether they switched to it or Angelo started in it (so a
  1.0.2 user sees it once after updating). Title *Show or hide the chat*.
* **hidden**: the first time the chat disappears in Manual mode, by any means. Title *The chat is hidden*. A visible
  manual hint becomes this one; it closes by itself when the chat is shown again.

The card draws the top bar's Chat button as it looks, the shortcut as it is bound now (read from the keymap, so a
changed or removed shortcut is never misquoted), and a button that does it. Dismissed once, each hint is remembered
per user (``chat-hints.json`` in the state folder); dismissing *The chat is hidden* also covers the first one. A hint
the person never saw (Angelo quit, or its 3D view went away) shows again.

It is a non-modal card (``card_host``): it takes only presses on itself, so orbiting, selecting and typing go on
underneath, and it waits behind a crash report, What's New and an update notice. Nothing shows in a background or
automation run (``app_notices.automation_off``) unless ``ANGELO_ENABLE_CHAT_HINTS=1`` (the hint's own GUI suite).
``seen``/``offer`` are pure, so the rules are host-tested (``test_native_chat_hint``).
"""

import sys

from . import card_kit as kit, live_windows, ui_kit
from .ui_text import iface_, n_

FILE = "chat-hints.json"
KINDS = ("manual", "hidden")
TITLE = {"manual": n_("Show or hide the chat"), "hidden": n_("The chat is hidden")}
# By hint, with the shortcut and without one (the person removed it): whole sentences, the keys a placeholder.
# The button is placed by what is beside it ({mode}: the mode menu's own label), never by a corner: the top bar's
# right-hand part ends with the scene and view layer selectors, so "top right" was not where it is.
BODY = {"manual": (n_("Click Chat in the top bar, next to {mode}, or press {keys}, to hide the chat and give your "
                      "scene the room. Do the same to bring it back."),
                   n_("Click Chat in the top bar, next to {mode}, to hide the chat and give your scene the room. "
                      "Click it again to bring the chat back.")),
        "hidden": (n_("Click Chat in the top bar, next to {mode}, or press {keys}, to bring it back."),
                   n_("Click Chat in the top bar, next to {mode}, to bring it back."))}
ALSO = n_("Window > Angelo Chat opens it too.")
CHAT = n_("Chat")   # The toggle's own label (chat_toggle.LABEL).
SHOW, HIDE, GOT_IT = n_("Show chat"), n_("Hide chat"), n_("Got it")
# Key names, spelled as the keyboard labels them. The "Angelo" context: Blender's catalogue has these words in other
# senses (Shift as a displacement, Option as a checkbox), and it is asked first.
CTRL, ALT, OPTION, SHIFT, COMMAND, WINDOWS = (n_("Ctrl", "Angelo"), n_("Alt", "Angelo"), n_("Option", "Angelo"),
                                              n_("Shift", "Angelo"), n_("Cmd", "Angelo"), n_("Win", "Angelo"))
WIDTH = 372
ARROW_W, ARROW_H = 22, 11      # The arrow from the card's top edge up at the Chat button.
MIN_AREA = 300                 # An area under the button narrower than this (UI-scaled) keeps the card in the 3D view.
BAR = (0.027, 0.396, 0.451, 1)          # The top bar's chrome, #076573 (doc/angelo/app_identity.rst).
PRESSED = (0.278, 0.447, 0.702, 1)      # Blender's pressed button (theme wcol_tool.inner_sel, #4772b3) when unread.
ON_BAR = (0.93, 0.95, 0.94, 1)          # The toggle's glyph and text (chat_toggle.bubble).
_card = None             # The visible or waiting hint.
_last = None             # The main window's chat state at the previous observation.


# -- rules (pure) ----------------------------------------------------------------------------------------------
def seen(stored):
    """The kinds already dismissed, from the stored file's value (anything malformed counts as nothing seen)."""
    if not isinstance(stored, dict):
        return frozenset()
    return frozenset(kind for kind in KINDS if stored.get(kind) is True)


def offer(done, manual, state, previous):
    """The hint to show now, or None. ``state``/``previous``: ``chat_toggle.chat_state`` names, ``previous`` None
    before the first observation (Angelo starting with the chat closed hid nothing)."""
    if not manual:
        return None
    if state == "hidden" and previous in {"docked", "popped"} and "hidden" not in done:
        return "hidden"
    if "manual" not in done:
        return "manual"
    return None


def dismissed(kind):
    """What closing a hint of ``kind`` marks as seen: the hidden hint also says how to show and hide."""
    return {"manual"} if kind == "manual" else {"manual", "hidden"}


def key_caps(item, mac):
    """The key caps of a keymap item (``ctrl``/``alt``/``shift``/``oskey``/``type``/``key name``), translated."""
    if item is None:
        return ()
    caps = []
    if item.get("ctrl"):
        caps.append(iface_(CTRL))
    if item.get("alt"):
        caps.append(iface_(OPTION if mac else ALT))
    if item.get("shift"):
        caps.append(iface_(SHIFT))
    if item.get("oskey"):
        caps.append(iface_(COMMAND if mac else WINDOWS))
    key = item.get("type", "")
    caps.append(key if len(key) == 1 else item.get("name") or key)   # A letter or digit as itself; else its name.
    return tuple(caps)


# -- persistence -----------------------------------------------------------------------------------------------
def _path():
    from pathlib import Path
    from .ui import state_directory
    return Path(state_directory()) / FILE


def load():
    from .studio_state import read_json
    return seen(read_json(_path(), {}))


def remember(kinds):
    from .studio_state import atomic_json, read_json
    stored = read_json(_path(), {})
    stored = dict(stored) if isinstance(stored, dict) else {}
    stored.update({kind: True for kind in kinds})
    try:
        atomic_json(_path(), stored)
    except OSError:
        pass   # Not remembered: the hint shows once more next time, never an error.


# -- the card --------------------------------------------------------------------------------------------------
class ChatHint(kit.BaseCard):
    kind, modal, priority, cancel_action = "chat_hint", False, 5, "dismiss"
    retry_when_lost = True   # A layout change took its 3D view: it was not seen yet.

    def __init__(self, hint, state, shortcut, anchor=lambda: None):
        """``state()`` is the chat's state now; ``shortcut()`` its key caps (may be empty); ``anchor()`` the top bar's
        Chat button as ``(left, right, bottom)`` window pixels (``chat_toggle.anchors``), or None."""
        self.hint, self.state, self.shortcut, self.anchor = hint, state, shortcut, anchor

    def version(self):
        return self.hint, self.state(), self.shortcut(), self.anchor()

    def target_area(self, window):
        """The area right under the Chat button (the chat, the Outliner or the 3D view), so the card stands next to
        it with its arrow; None (the host's largest 3D view) when no area there is wide enough for the card."""
        rect = self.anchor()
        if rect is None:
            return None
        import bpy
        cx = (rect[0] + rect[1]) / 2
        minimum = MIN_AREA * bpy.context.preferences.system.ui_scale
        best = None
        for area in live_windows.areas(window):
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None or not region.x <= cx < region.x + region.width or region.width < minimum:
                continue
            if best is None or region.y + region.height > best[0]:
                best = (region.y + region.height, area)
        return best[1] if best else None

    def layout(self, view, region_w, region_h, scale, ui):
        left, right = overlap_insets()
        point = anchor_in_region(self.anchor())
        return kit.fit(lambda size: self._place(self._build(view, size, region_w - left - right, ui), region_w,
                                                region_h, left, right, point), region_w, region_h, scale)

    @staticmethod
    def _place(builder, region_w, region_h, left=0, right=0, point=None):
        """Under the top bar's Chat button, its arrow pointing up at it (``point``: the button's centre ``x`` in this
        region, when it is over it), kept inside the region. Without one: top right, beside the navigation gizmo.
        ``left``/``right``: the toolbar and sidebar drawn over the view (region overlap); the card never slides
        under them."""
        s = builder.s
        lo, hi = left + 10 * s, region_w - right - builder.width - 10 * s
        if point is not None and lo <= point <= region_w - right:
            arrow_h = ARROW_H * s
            top = max(builder.height() + 10 * s, region_h - arrow_h - 6 * s)
            x = min(max(point - builder.width / 2, lo), max(lo, hi))
            plan = builder.place(x, top, False, (region_w, region_h))
            cx = min(max(point, x + 22 * s), x + builder.width - 22 * s)   # The tip stays on the card's edge.
            plan.ops.append(("kit", "chat_hint_arrow", (cx - ARROW_W * s / 2, top, ARROW_W * s, arrow_h), {},
                             ui_kit.Ui(), s))
            return plan
        gizmo = 96 * s if region_w - left - right > builder.width + 136 * s else 20 * s
        top = max(builder.height() + 10 * s, region_h - 64 * s)
        x = max(lo, region_w - right - builder.width - gizmo)
        return builder.place(x, top, False, (region_w, region_h))

    def _build(self, view, scale, region_w, ui):
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=22)
        icon = 40 * scale
        b.icon("chat", b.pad, b.y, 40)
        text_x = b.pad + icon + 14 * scale
        text_w = width - text_x - b.pad - 20 * scale
        top = b.y
        b.paragraph(iface_(TITLE[self.hint]), 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        caps = self.shortcut()
        with_keys, without = BODY[self.hint]
        mode = iface_("Manual", "Angelo")   # The mode menu's label beside the button (director.draw_topbar).
        body = iface_(with_keys, keys="+".join(caps), mode=mode) if caps else iface_(without, mode=mode)
        b.paragraph(body, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + icon) + 12 * scale
        params = {"label": iface_(CHAT), "caps": caps, "shown": self.state() != "hidden", "pressed": pressed_color()}
        b.kit("chat_hint_keys", keys_height(scale), params)
        b.gap(10)
        b.paragraph(iface_(ALSO), 12.5, kit.MUTED, leading=1.45)
        b.gap(10)
        shown = self.state() != "hidden"
        b.buttons([("dismiss", None, iface_(GOT_IT), "ghost", True),
                   ("toggle", None, iface_(HIDE if shown else SHOW), "primary", True)])
        b.close_button("dismiss")
        return b

    def act(self, action, value, host):
        if action == "toggle":
            window = host.window()
            if window is not None:
                import bpy
                pointer = window.as_pointer()

                def toggle(target):
                    # After this event: the card may stand over the chat, and hiding closes that very area, which
                    # must not happen inside the card's own event handling.
                    if target is not None:
                        with bpy.context.temp_override(window=target):
                            bpy.ops.angelo.toggle_chat()
                    return None
                # settle: a chat in a window between screens would read as hidden and a second one would open.
                bpy.app.timers.register(live_windows.deferred(lambda: bpy.context.window_manager, pointer, toggle,
                                                              settle=True), first_interval=0.01)
        elif action == "dismiss":
            host.close("dismissed")

    def closed(self, how):
        global _card
        if how in {"lost", "replaced"}:
            return   # Waiting again (card_host requeued it): not seen yet.
        if _card is self:
            _card = None
        remember(dismissed(self.hint))


def anchor_in_region(rect):
    """The Chat button's centre ``x`` in the drawing region's pixels, or None (no button drawn, no Blender)."""
    if rect is None:
        return None
    try:
        import bpy
        return (rect[0] + rect[1]) / 2 - bpy.context.region.x
    except Exception:  # noqa: BLE001 - no context (host tests): no anchor.
        return None


def overlap_insets():
    """``(left, right)`` pixels of the 3D view's toolbar and sidebar drawn over its view (region overlap), from the
    drawing context; ``(0, 0)`` without Blender or overlap."""
    try:
        import bpy
        area, region = bpy.context.area, bpy.context.region
        left = right = 0
        for other in area.regions:
            if other.type not in {'TOOLS', 'UI'} or other.width <= 1:
                continue
            if other.x < region.x + region.width and other.x + other.width > region.x:   # Over the view.
                if other.type == 'TOOLS':
                    left = max(left, other.x + other.width - region.x)
                else:
                    right = max(right, region.x + region.width - other.x)
        return left, right
    except Exception:  # noqa: BLE001 - no context (host tests, a draw outside a 3D view): no insets.
        return 0, 0


# -- the drawn top bar button and key caps -------------------------------------------------------------------
def keys_height(scale):
    return 30 * scale


def pressed_color():
    """The top bar's pressed button as the theme draws it, so the drawn Chat button looks like the real one."""
    try:
        import bpy
        color = bpy.context.preferences.themes[0].user_interface.wcol_tool.inner_sel
        return (color[0], color[1], color[2], 1)
    except Exception:  # noqa: BLE001 - no Blender (host tests): Blender's default.
        return PRESSED


def _bubble(view, x, y, size, color, hole, filled=True):
    """The toggle's speech bubble (``chat_toggle.bubble``) at ``size`` pixels: filled with cut-out dots while the chat
    is shown, an outline while hidden, as the top bar draws it."""
    u = size / 32
    view.shape(x + 3.5 * u, y + 9 * u, 25 * u, 19 * u, color, 6 * u)
    view.stroke(x + 9 * u, y + 10 * u, x + 6 * u, y + 3.5 * u, color, 3 * u)
    view.stroke(x + 6 * u, y + 3.5 * u, x + 14 * u, y + 10 * u, color, 3 * u)
    if not filled:   # The outline: the body's inside in the bar's colour.
        view.shape(x + 5.7 * u, y + 11.2 * u, 20.6 * u, 14.6 * u, hole, 3.8 * u)
        return
    for cx in (10.5, 16.0, 21.5):   # The cut-out dots, in the button's colour.
        view.shape(x + (cx - 2.4) * u, y + (17.5 - 2.4) * u, 4.8 * u, 4.8 * u, hole, 2.4 * u)


def _keys(view, rect, scale, ui, label, caps, shown, pressed=PRESSED):
    """The top bar's Chat button on a strip of the bar, then the shortcut's key caps after a divider."""
    x, y, w, h = rect
    s = scale
    size = 12.5 * s
    label_w = ui_kit.measure(view, label, 12.5)
    glyph = 16 * s
    button_w = 8 * s + glyph + 6 * s + label_w + 10 * s
    strip_w = button_w + 12 * s
    view.shape(x, y, strip_w, h, BAR, 7 * s)
    bx, by, bh = x + 6 * s, y + 4 * s, h - 8 * s
    view.shape(bx, by, button_w, bh, pressed if shown else (0, 0, 0, 0), 5 * s)
    _bubble(view, bx + 8 * s, by + (bh - glyph) / 2, glyph, ON_BAR, pressed if shown else BAR, filled=shown)
    ui_kit.text_middle(view, label, bx + 8 * s + glyph + 6 * s, by, bh, size, ON_BAR)
    if not caps:
        return
    cx = x + strip_w + 12 * s
    view.shape(cx, y + 6 * s, 1 * s, h - 12 * s, kit.LINE, 0)
    cx += 13 * s
    for cap in caps:
        cap_w = max(h - 6 * s, ui_kit.measure(view, cap, 12) + 16 * s)
        view.shape(cx, y + 2 * s, cap_w, h - 4 * s, kit.LINE, 6 * s)
        view.shape(cx + 1 * s, y + 3 * s, cap_w - 2 * s, h - 6 * s, kit.PANEL, 5 * s)
        ui_kit.text_middle(view, cap, cx + (cap_w - ui_kit.measure(view, cap, 12)) / 2, y + 2 * s, h - 4 * s,
                           12 * s, kit.TEXT)
        cx += cap_w + 6 * s


ui_kit.register_component("chat_hint_keys", ui_kit.blending(_keys))


def _arrow(view, rect, scale, ui):
    """A small upward triangle in the card's colour, its base on the card's top edge, its tip at the rect's top:
    filled by horizontal strokes (the canvas has rounded rectangles and lines only)."""
    x, y, w, h = rect
    steps = max(2, int(h))
    for i in range(steps):
        t = i / steps                      # 0 at the base, 1 at the tip.
        half = (w / 2) * (1 - t)
        view.stroke(x + w / 2 - half, y + i, x + w / 2 + half, y + i, kit.CARD, 1.4)
    view.stroke(x, y, x + w / 2, y + h, kit.EDGE, 1.0)   # The card's hairline edge, continued.
    view.stroke(x + w / 2, y + h, x + w, y, kit.EDGE, 1.0)


ui_kit.register_component("chat_hint_arrow", ui_kit.blending(_arrow))


# -- watching the window ---------------------------------------------------------------------------------------
def disabled():
    import bpy
    from . import app_notices, product_env
    if bpy.app.background:
        return True
    return app_notices.automation_off() and not product_env.flag("ENABLE_CHAT_HINTS")


def shortcut():
    """The key caps of the toggle's shortcut as the person has it bound now (user keymaps include the add-on's)."""
    from . import shortcuts
    return key_caps(shortcuts.bound(shortcuts.CHAT[0]), sys.platform == "darwin")


def main_window():
    import bpy
    return live_windows.first(bpy.context.window_manager, temporary=False)


def observe():
    """Called twice a second (``chat_toggle._watch``): offer the hint the rules say, show it when the window is ready."""
    global _card, _last
    import bpy
    from . import app_notices, card_host, chat_toggle, director
    if disabled():
        return
    window = main_window()
    if window is None:
        return
    state = chat_toggle.chat_state(window, live_windows.every_window(bpy.context.window_manager))[0]
    if state == "changing":
        return   # A window between screens could hold the chat: judged once it has settled.
    manual = not director.is_director(window) and director._timer is None
    previous, _last = _last, state
    card_host.check_area()   # Its area closed (the chat it stood over): waiting again, shown where it now belongs.
    if _card is not None:
        visible = card_host.is_active("chat_hint")
        if not manual or (_card.hint == "hidden" and state != "hidden"):
            # Director shows the chat itself; a hidden chat brought back needs no hint. Seen if it was on screen.
            if visible:
                card_host.close("mode" if not manual else "done")
            else:
                card_host.withdraw("chat_hint")
                _card = None
        elif state == "hidden" and previous in {"docked", "popped"} and _card.hint == "manual":
            _card.hint = "hidden"                # Hidden while the first hint shows: it says how to bring it back.
            card_host.refresh("chat_hint")
    else:
        hint = offer(load(), manual, state, previous)
        if hint is not None:
            pointer = window.as_pointer()

            def current():
                wm = bpy.context.window_manager
                target = live_windows.find(wm, pointer)
                return chat_toggle.chat_state(target, live_windows.every_window(wm))[0] if target else "hidden"
            _card = ChatHint(hint, current, shortcut, lambda: chat_toggle.anchors.get(pointer))
            card_host.request(_card)
    if card_host.queued("chat_hint"):
        card_host.pump(app_notices.ready)


def reset():
    """Forget the visible card and the last state (unregister)."""
    global _card, _last
    _card = _last = None
