# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The picture viewer's tools: what each press, drag, release, wheel turn and key does (2.1.0).

doc/angelo/attachment_markup.rst. A ``Session`` holds the document (``markup_doc``), the view (``markup_view``), the
mode (``view``, ``markup``, ``comment``), the tool, the colour and width, what is being drawn or moved (``pending``),
the selection, and the two text fields: the instructions and the inline editor of a text mark or a comment's note
(``ui_kit_field.FieldState``). The overlay (``markup_overlay``) forwards Blender's events here and draws what the
session reports; it never edits the document itself. Pure: no Blender.

Every method that takes input answers what the overlay must do beyond redrawing: ``"close"``, ``"ask_discard"``
(Escape with unsent marks), ``"send"`` (Return in the instructions with something to send) or None.

* A middle drag pans and the wheel zooms about the pointer in every mode; in ``view`` mode a left drag pans too.
  F fits. A tool starts only on the picture, never on the scrim around it.
* A tool's key (``TOOL_KEYS``) switches to it, except while a text field takes typing: then it is a letter.
* A drag is one undo step: the stroke, the shape, the move; the eraser removes each mark its path crosses.
"""

import math

from . import markup_doc as md, markup_resize
from .ui_kit_field import FieldState, field_key
from .ui_text import n_

MODES = ("view", "markup", "comment", "erase", "resize")
ASKED = ("remove_bg", "erase")   # Paid edits: run only after their card is confirmed (``ask``, ``markup_edit``).
# Whether the viewer offers Remove BG and Erase (an image generation through Media Studio). Off ships Markup, Comment
# and Resize only: nothing offers or reaches them (coordinator 2026-11-20: on only once proven live end to end).
AI_EDITS = False
# A bound reached: said in the viewer, never raised out of its modal (break-it of 2298d608dd1, M2).
UNUSED = "unused"   # Session.key's answer for a key the viewer does not use: it passes on (markup_input).
FULL_MARKS = n_("This picture takes at most {count} marks")
FULL_COMMENTS = n_("This picture takes at most {count} comments")


def modes():
    return MODES if AI_EDITS else tuple(mode for mode in MODES if mode != "erase")
ERASE_RADII = {1: 8.0, 2: 16.0, 3: 32.0}   # A brush's radius by width level, at a picture of 1024 px or less.
TOOLS = ("select", "pen", "arrow", "ellipse", "rect", "text", "eraser")
TOOL_KEYS = {"V": "select", "P": "pen", "A": "arrow", "O": "ellipse", "R": "rect", "T": "text", "E": "eraser",
             "C": "comment"}
HIT_PX = 6.0         # How near a mark the pointer must be to pick or erase it, on screen.
DRAG_PX = 4.0        # A press that moves less than this is a click.
WHEEL_STEP = 1.25


class Session:
    def __init__(self, doc, view):
        self.doc, self.view = doc, view
        self.mode, self.tool, self.last_tool = "view", None, "pen"
        self.colour, self.width = md.DEFAULT_COLOUR, md.DEFAULT_WIDTH
        self.pending = None       # What a drag is making: {"kind", ...} in picture pixels.
        self.selected = None
        self.editing = None       # {"kind": "text", "at"} or {"kind": "comment", "id", "new"}, typed into self.field.
        self.field = FieldState()
        self.instructions = FieldState(text=doc.instructions)
        self._press = None        # (button, region x, region y, picture point) of the press in progress.
        self._last = None
        self.erase_strokes, self._erase_redo = [], []   # Erase mode's brush strokes: {"points", "radius"}.
        self.crop, self.scale = None, 100               # Resize mode's crop (x, y, w, h) and scale percent.
        self.confirm = None       # A paid edit waiting for its card's answer (``ask``).
        self.confirmed = None     # The edit the last confirmation started (read by the overlay on "run").
        self.notice = None        # (template, fields) of a refusal the viewer shows until the next press.

    # -- state -----------------------------------------------------------------------------------------------------

    @property
    def can_send(self):
        return not self.doc.empty

    @property
    def typing(self):
        return bool(self.editing) or self.instructions.editing

    def set_mode(self, mode):
        if mode not in modes():
            raise ValueError(mode)
        self._finish_editing(commit=True)
        self.mode = mode
        self.tool = {"view": None, "comment": "comment", "erase": "brush", "resize": "crop"}.get(mode, self.last_tool)
        self.pending = None

    def set_tool(self, tool):
        if tool == "comment":
            return self.set_mode("comment")
        if tool not in TOOLS:
            raise ValueError(tool)
        if self.mode != "markup":
            self.set_mode("markup")
        self.tool = self.last_tool = tool
        if tool != "select":
            self.selected = None

    def set_colour(self, colour):
        md._style(colour, self.width)
        self.colour = colour
        if self.selected:
            self.doc.restyle(self.selected, colour=colour)

    def set_scale(self, scale):
        if scale not in markup_resize.SCALES:
            raise ValueError(scale)
        self.scale = scale

    def resize_plan(self):
        return markup_resize.plan(self.doc.size, self.crop, self.scale)

    def erase_radius(self):
        """The brush's radius in picture pixels at its width level, grown with the picture as marks are."""
        return ERASE_RADII[self.width] * md.scale_of(self.doc.size)

    def clear_erase(self):
        self.erase_strokes, self._erase_redo = [], []

    def ask(self, action):
        """Open the confirmation card of a paid edit; Erase only once something is brushed."""
        if action not in ASKED or not AI_EDITS:
            raise ValueError(action)
        if action == "erase" and not self.erase_strokes:
            return None
        self.commit_editing()
        self.confirm, self.pending, self._press = action, None, None
        return None

    def answer(self, run):
        """The card's Run (True) or Cancel (False); "run" when the edit is to start."""
        action, self.confirm = self.confirm, None
        if run and action:
            self.confirmed = action
            return "run"
        return None

    def set_width(self, width):
        md._style(self.colour, width)
        self.width = width
        if self.selected:
            self.doc.restyle(self.selected, width=width)

    @property
    def pressing(self):
        """A press is in progress: its drag keeps its events even where the pointer leaves the picture's region."""
        return self._press is not None

    def commit_editing(self):
        """Finish the text or note being typed, as Return would (before sending)."""
        self._finish_editing(commit=True)

    def focus_instructions(self):
        self._finish_editing(commit=True)
        self.instructions.editing = True

    # -- pointer ---------------------------------------------------------------------------------------------------

    def press(self, x, y, button="LEFTMOUSE"):
        self.notice = None
        if self.confirm:   # The card's question first: nothing is drawn under it.
            self._press = None
            return None
        point = self.view.to_image(x, y)
        self._press, self._last = (button, x, y, point), (x, y)
        if self.instructions.editing:
            self.instructions.editing = False
        if button != "LEFTMOUSE" or self.mode == "view":
            return None
        self._finish_editing(commit=True)
        if not self.view.contains_region(x, y):
            self._press = None
            return None
        tolerance = self.view.image_length(HIT_PX)
        if self.tool == "select":
            self.selected = self.doc.hit(point, tolerance)
            self.pending = {"kind": "move", "id": self.selected, "dx": 0.0, "dy": 0.0} if self.selected else None
        elif self.tool == "eraser":
            self._erase(point, point)
        elif self.tool in ("pen", "brush"):
            self.pending = {"kind": self.tool, "points": [list(point)]}
        elif self.tool in ("arrow", "ellipse", "rect", "comment", "crop"):
            self.pending = {"kind": self.tool, "start": list(point), "end": list(point)}
        return None

    def move(self, x, y):
        if self._press is None:
            return None
        button, _x, _y, start = self._press
        if button != "LEFTMOUSE" or self.mode == "view":
            self.view.pan(x - self._last[0], y - self._last[1])
            self._last = (x, y)
            return None
        previous, point = self.view.to_image(*self._last), self.view.to_image(x, y)
        self._last = (x, y)
        if self.tool == "eraser":
            self._erase(previous, point)
        elif self.pending and self.pending["kind"] in ("pen", "brush"):
            self.pending["points"].append(list(point))
        elif self.pending and self.pending["kind"] == "move":
            self.pending["dx"], self.pending["dy"] = point[0] - start[0], point[1] - start[1]
        elif self.pending:
            self.pending["end"] = list(point)
        return None

    def release(self, x, y, button="LEFTMOUSE"):
        if self._press is None:
            return None
        self.move(x, y)
        pending, (_button, px, py, start) = self.pending, self._press
        self._press, self.pending = None, None
        clicked = math.hypot(x - px, y - py) < DRAG_PX
        if not pending:
            if self.tool == "text" and self.mode == "markup" and button == "LEFTMOUSE" and self.view.contains_region(px, py):
                self._edit({"kind": "text", "at": list(start)}, "")
            return None
        kind = pending["kind"]
        if kind == "brush":
            points = md.simplify([list(p) for p in pending["points"]])
            radius = self.erase_radius()
            self.erase_strokes.append({"points": md._cap(points), "radius": radius})
            self._erase_redo.clear()
        elif kind == "crop":
            self.crop = None if clicked else tuple(self._box_whole(pending))
        elif kind == "pen":
            if not self._full("marks"):
                self.doc.add_mark("pen", points=pending["points"], colour=self.colour, width=self.width)
        elif kind == "move":
            if not clicked:
                self.doc.move(pending["id"], pending["dx"], pending["dy"])
        elif kind == "comment":
            if self._full("comments"):
                return None
            box = self._box(pending)
            flat = not (abs(box[2]) >= 1 and abs(box[3]) >= 1)   # A drag along one axis has no region: a point.
            identity = (self.doc.add_comment(at=pending["start"]) if clicked or flat
                        else self.doc.add_comment(box=box))
            self._edit({"kind": "comment", "id": identity, "new": True}, "")
        elif not clicked and not self._full("marks"):
            if kind == "arrow":
                self.doc.add_mark("arrow", start=pending["start"], end=pending["end"], colour=self.colour, width=self.width)
            else:
                self.doc.add_mark(kind, box=self._box(pending), colour=self.colour, width=self.width)
        return None

    def wheel(self, x, y, steps):
        self.view.zoom_at(x, y, WHEEL_STEP ** steps)

    # -- keys ------------------------------------------------------------------------------------------------------

    def key(self, kind, unicode="", command=False, shift=False, clipboard=None):
        if self.confirm:   # Answered by a click or an armed focused button (markup_input); never by a bare Return.
            return self.answer(False) if kind == "ESC" else None
        if self.editing:
            return self._editor_key(kind, unicode, command, clipboard)
        if self.instructions.editing:
            result = field_key(self.instructions, kind, unicode, command, clipboard)
            self.doc.instructions = self.instructions.text[:md.MAX_INSTRUCTIONS]
            if result == "submit":
                self.instructions.editing = True
                return "send" if self.can_send else None
            return None
        if command and kind == "Z" and self.mode == "erase":
            source, target = (self._erase_redo, self.erase_strokes) if shift else (self.erase_strokes, self._erase_redo)
            if source:
                target.append(source.pop())
        elif command and kind == "Z":
            (self.doc.redo if shift else self.doc.undo)()
            self.selected = None
        elif kind == "ESC":
            if self.pending:
                self.pending, self._press = None, None
                return None
            return "close" if self.doc.empty else "ask_discard"
        elif kind in ("DEL", "BACK_SPACE") and self.selected:
            self.doc.remove(self.selected)
            self.selected = None
        elif kind == "F" and not command:
            self.view.fit()
        elif kind in TOOL_KEYS and not command:
            self.set_tool(TOOL_KEYS[kind])
        else:
            return UNUSED
        return None

    # -- helpers ---------------------------------------------------------------------------------------------------

    def _box_whole(self, pending):
        """A drag's box clipped to the picture (Resize's crop)."""
        (x0, y0), (x1, y1) = pending["start"], pending["end"]
        w, h = self.doc.size
        xs = sorted((min(max(x0, 0.0), w), min(max(x1, 0.0), w)))
        ys = sorted((min(max(y0, 0.0), h), min(max(y1, 0.0), h)))
        return xs[0], ys[0], xs[1] - xs[0], ys[1] - ys[0]

    def _full(self, kind):
        """True, with the viewer's notice, when the picture takes no more marks (or comments)."""
        items, bound, text = ((self.doc.marks, md.MAX_MARKS, FULL_MARKS) if kind == "marks"
                              else (self.doc.comments, md.MAX_COMMENTS, FULL_COMMENTS))
        if len(items) < bound:
            return False
        self.notice = (text, {"count": bound})
        return True

    @staticmethod
    def _box(pending):
        (x0, y0), (x1, y1) = pending["start"], pending["end"]
        return (x0, y0, x1 - x0, y1 - y0)

    def _erase(self, start, end):
        """Remove every mark the pointer's path from ``start`` to ``end`` crosses (picture pixels)."""
        tolerance = self.view.image_length(HIT_PX)
        steps = max(1, int(math.hypot(end[0] - start[0], end[1] - start[1]) / (tolerance / 2.0)))
        for step in range(steps + 1):
            t = step / steps
            point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
            identity = self.doc.hit(point, tolerance)
            while identity:
                self.doc.remove(identity)
                identity = self.doc.hit(point, tolerance)

    def _edit(self, editing, text):
        self.editing = editing
        self.field = FieldState(text=text, editing=True)

    def _editor_key(self, kind, unicode, command, clipboard):
        result = field_key(self.field, kind, unicode, command, clipboard)
        if result == "submit":
            self._finish_editing(commit=True)
        elif result == "cancel":
            self._finish_editing(commit=False)
        return None

    def _finish_editing(self, commit):
        editing, self.editing = self.editing, None
        if not editing:
            return
        text = self.field.text
        if editing["kind"] == "text":
            if commit and not self._full("marks"):
                self.doc.add_mark("text", at=editing["at"], text=text, colour=self.colour, width=self.width)
        elif commit and text.strip():
            self.doc.set_text(editing["id"], text)
        elif editing.get("new") and not self.doc.comment(editing["id"])["text"]:
            self.doc.retract()   # A new pin left without a note: as if never placed.
