# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Text field, plain and secret (``ui_kit``): focus ring, placeholder, caret, error state, mask, reveal and paste.

The field's content lives in a ``FieldState`` the caller keeps (a card attribute, a canvas dict entry). Typing
is a modal key handler: while ``fs.editing`` the host hands each key press to ``handle_event`` (card_host calls a
card's ``key_event(event, host)`` first; a canvas calls it from its own modal). Typed text, like every Blender
text field and the chat composer, reads left to right in every interface language; the placeholder and the
error line are interface text and follow the canvas direction.

API (rects are region pixels ``(x, y, w, h)``, y up; ``ui`` as in ``ui_kit``):

    FieldState(text="", cursor=None, secret=False, revealed=False, editing=False, error="")
    masked(fs) -> str                         what is drawn: bullets for a hidden secret
    field_key(fs, kind, unicode="", command=False, clipboard=None) -> "changed" | "moved" | "submit" |
        "cancel" | None (not handled: Tab and everything else stay with the host)
        kind is a Blender event type (BACK_SPACE, DEL, LEFT_ARROW, RIGHT_ARROW, HOME, END, RET, ESC, V ...);
        ``command`` is Ctrl or Cmd; ``clipboard()`` returns text for Ctrl/Cmd+V.
    handle_event(fs, event, clipboard=None) -> same as field_key, from a ``bpy.types.Event``
    paste(fs, text) -> "changed"              insert at the caret (newlines dropped)
    field_height(scale, error="") -> h        the field plus its error line
    field_hits(rect, scale, action, value=None, secret=False, reveal_action="reveal", paste_action="paste")
        -> [(rect, action, value)]            (secret) Paste and Reveal first, then the field: canvas
                                              hit_at picks the first match (card_kit.Builder reverses them)
    draw_field(view, rect, scale, ui=None, fs=None, placeholder="", action="field", value=None, enabled=True,
               time=0.0, reveal_action="reveal", paste_action="paste")    rect is the whole field+error band
"""

from dataclasses import dataclass
import math

from . import canvas_rtl, ui_kit
from . import ui_tokens as tk
from .ui_kit import dim, register_component, state_for, text_middle
from .ui_text import iface_, n_

BULLET = "•"
FIELD_FILL = (0.063, 0.071, 0.086, 1)
FIELD_LINE = (1, 1, 1, 0.12)
FIELD_LINE_HOVER = (1, 1, 1, 0.22)
PASTE = n_("Paste")


@dataclass
class FieldState:
    text: str = ""
    cursor: int = None      # Caret index into ``text``; None: at the end.
    secret: bool = False
    revealed: bool = False
    editing: bool = False
    error: str = ""         # Already translated; shown under the field in coral.

    def caret(self):
        return len(self.text) if self.cursor is None else max(0, min(self.cursor, len(self.text)))


def masked(fs):
    return BULLET * len(fs.text) if fs.secret and not fs.revealed else fs.text


def paste(fs, text):
    text = "".join(ch for ch in (text or "") if ch not in "\r\n\t")
    caret = fs.caret()
    fs.text = fs.text[:caret] + text + fs.text[caret:]
    fs.cursor = caret + len(text)
    fs.error = ""
    return "changed"


def field_key(fs, kind, unicode="", command=False, clipboard=None):
    caret = fs.caret()
    if kind in {'RET', 'NUMPAD_ENTER'}:
        fs.editing = False
        return "submit"
    if kind == 'ESC':
        fs.editing = False
        return "cancel"
    if command and kind == 'V':
        return paste(fs, clipboard() if clipboard else "")
    if command and kind == 'BACK_SPACE':
        fs.text, fs.cursor = fs.text[caret:], 0
        return "changed"
    if kind == 'BACK_SPACE':
        if caret:
            fs.text, fs.cursor = fs.text[:caret - 1] + fs.text[caret:], caret - 1
            fs.error = ""
        return "changed"
    if kind == 'DEL':
        fs.text = fs.text[:caret] + fs.text[caret + 1:]
        fs.cursor = caret
        return "changed"
    moves = {'LEFT_ARROW': caret - 1, 'RIGHT_ARROW': caret + 1, 'HOME': 0, 'END': len(fs.text)}
    if kind in moves:
        fs.cursor = max(0, min(len(fs.text), moves[kind]))
        return "moved"
    if unicode and unicode.isprintable() and not command and kind != 'TAB':
        fs.text = fs.text[:caret] + unicode + fs.text[caret:]
        fs.cursor = caret + len(unicode)
        fs.error = ""
        return "changed"
    return None


def handle_event(fs, event, clipboard=None):
    if event.value != 'PRESS':
        return None
    return field_key(fs, event.type, getattr(event, "unicode", "") or "", bool(event.ctrl or event.oskey), clipboard)


def field_height(scale, error=""):
    return (tk.FIELD_HEIGHT + (22 if error else 0)) * scale


def _box(rect, scale, error):
    x, y, w, h = rect
    fh = tk.FIELD_HEIGHT * scale
    return (x, y + h - fh, w, fh)


def _buttons(box, scale):
    """The secret field's trailing Reveal and Paste buttons (layout order: Paste, then Reveal at the end)."""
    x, y, w, h = box
    side = h - 8 * scale
    reveal = (x + w - side - 4 * scale, y + 4 * scale, side, side)
    paste_w = 52 * scale
    paste_rect = (reveal[0] - paste_w - 2 * scale, y + 4 * scale, paste_w, side)
    return reveal, paste_rect


def field_hits(rect, scale, action, value=None, secret=False, reveal_action="reveal", paste_action="paste", error=""):
    box = _box(rect, scale, error)
    hits = [(box, action, value)]
    if secret:
        reveal, paste_rect = _buttons(box, scale)
        hits = [(paste_rect, paste_action, value), (reveal, reveal_action, value)] + hits   # Buttons win.
    return hits


@ui_kit.blending
def draw_field(view, rect, scale, ui=None, fs=None, placeholder="", action="field", value=None, enabled=True,
               time=0.0, reveal_action="reveal", paste_action="paste"):
    fs = fs or FieldState()
    s = scale
    box = _box(rect, s, fs.error)
    x, y, w, h = box
    st = state_for(ui, action, value, enabled)
    radius = tk.RADIUS_CONTROL * s
    if fs.editing or st.focus:
        ui_kit.focus_ring(view, box, radius, s)
    view.shape(*box, FIELD_FILL if enabled else dim(FIELD_FILL, 0.5), radius)
    edge = tk.CORAL if fs.error else tk.TEAL if fs.editing else FIELD_LINE_HOVER if st.hover else FIELD_LINE
    ui_kit.outline(box, radius, max(1.0, s * (1.5 if fs.error or fs.editing else 1)), edge)
    pad = 12 * s
    right = w - pad
    if fs.secret:
        reveal, paste_rect = _buttons(box, s)
        right = paste_rect[0] - x - 4 * s
        _paste_button(view, paste_rect, s, state_for(ui, paste_action, value, enabled))
        _eye(view, reveal, s, fs.revealed, state_for(ui, reveal_action, value, enabled))
    shown = masked(fs)
    size = tk.TYPE_BODY * s
    if not shown and not fs.editing:
        text_middle(view, placeholder, x + pad, y, h, size, dim(tk.MUTED, 0.8 if enabled else 0.4))
    else:
        mono = fs.secret and not fs.revealed
        caret_x = ui_kit.measure(view, shown[:fs.caret()], size)
        visible = right - pad
        offset = max(0.0, caret_x - visible + 2 * s)
        with canvas_rtl.upright(x + pad, visible) as tx:   # Typed text reads left to right, as in every Blender field.
            with canvas_rtl.unmirrored():
                with ui_kit.clip(tx - 1, y + 2, visible + 2, h - 4):
                    text_middle(view, shown, tx - offset, y + (1 * s if mono else 0), h, size,
                                tk.TEXT if enabled else dim(tk.TEXT), shaped=False)
                    if fs.editing and math.sin(time * 6.0) > -0.35:   # A blinking caret, on more than off.
                        view.shape(tx - offset + caret_x, y + 9 * s, max(1.0, 1.5 * s), h - 18 * s, tk.TEAL, 0)
    if fs.error:
        ui_kit.text(view, fs.error, rect[0] + 2 * s, rect[1] + 4 * s, tk.TYPE_SMALL * s, tk.CORAL)


def _paste_button(view, rect, s, st):
    if st.hover or st.pressed:
        view.shape(*rect, (1, 1, 1, 0.12 if st.pressed else 0.07), rect[3] / 2)
    if st.focus:
        ui_kit.focus_ring(view, rect, rect[3] / 2, s)
    label = iface_(PASTE)
    size = tk.TYPE_SMALL * s
    tw = ui_kit.measure(view, label, size)
    text_middle(view, label, rect[0] + (rect[2] - tw) / 2, rect[1], rect[3], size,
                tk.TEXT if st.hover or st.focus else tk.MUTED)


def _eye(view, rect, s, revealed, st):
    x, y, w, h = rect
    if st.hover or st.pressed:
        view.shape(*rect, (1, 1, 1, 0.12 if st.pressed else 0.07), w / 2)
    if st.focus:
        ui_kit.focus_ring(view, rect, w / 2, s)
    color = tk.TEXT if st.hover or st.focus or revealed else tk.MUTED
    cx, cy = x + w / 2, y + h / 2
    rx, ry = 7.5 * s, 4.6 * s
    points = [(cx + rx * math.cos(math.radians(a)), cy + ry * math.sin(math.radians(a))) for a in range(0, 361, 20)]
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        view.stroke(x1, y1, x2, y2, color, 1.4 * s)
    r = 2.2 * s
    view.shape(cx - r, cy - r, 2 * r, 2 * r, color, r)
    if not revealed:   # Hidden: the eye is struck through.
        view.stroke(cx - 7 * s, cy - 6 * s, cx + 7 * s, cy + 6 * s, color, 1.5 * s)


register_component("text_field", draw_field)
