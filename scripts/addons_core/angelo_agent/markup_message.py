# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a marked-up picture sends Angelo's agent, and how its marks are flattened onto it (2.1.0).

doc/angelo/attachment_markup.rst. One message carries:

1. **The marked picture**: the original with every mark and numbered comment pin drawn on it (``flatten_ops``, drawn
   by ``markup_flatten`` at ``flat_size``), a new private reference whose record names its original (``markup_of``).
2. **The original picture**, so the model can see what a mark covers.
3. **One structured block** (``model_block``) in the turn's attachment context: the picture's size, each comment's
   number, place and note, each mark's kind, colour and place, and the person's instructions, all in the original's
   pixels. Its comment numbers are the numbers on the pins, which is what ties the words to the drawing.

The block's explanation (``ABOUT``) is text for the model, fixed English and never translated; the person's own words
(notes, text marks, instructions) go as they typed them. To the person the pair is one attachment (``counted``). No
Blender here.
"""

from . import markup_doc as md
from .ui_text import n_

KIND = "picture_markup"
ABOUT = ("The person marked up an attached picture to show you what they mean. marked_picture is the original with "
         "their marks and numbered comment pins drawn on it; original_picture is the same picture without them. "
         "Every position is in the original picture's pixels. Each comment's n is the number drawn on its pin; read "
         "the comments with the pins they point at, and the instructions as the person's request about the picture.")
COORDINATES = "picture pixels of the original: origin top-left, x to the right, y down"
MAX_FLAT = 4096    # The marked picture's longest side; the providers scale larger pictures down anyway.
STROKE_PX = {1: 3.0, 2: 5.0, 3: 8.0}   # A width level's pixels at a picture with a long edge of 1024 or less.
PIN_PX = 14.0      # A comment pin's radius, likewise.
REGION = (0.15, 0.74, 0.75, 1.0)   # A comment region's outline: Angelo's teal, never one of the person's colours.


def _whole(values):
    return [int(round(v)) for v in values]


def _bounds(points):
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]


def pins(doc):
    """Each comment's pin: its number, the pin's centre (a region's top-left corner) and its region, if any."""
    out = []
    for number, comment in enumerate(doc.comments, 1):
        box = comment.get("box")
        out.append({"n": number, "at": list(box[:2]) if box else list(comment["at"]), "box": box, "id": comment["id"]})
    return out


def _mark(mark):
    entry = {"kind": mark["kind"], "colour": mark["colour"]}
    if mark["kind"] == "arrow":
        entry["from"], entry["to"] = _whole(mark["start"]), _whole(mark["end"])
    elif mark["kind"] == "pen":
        entry["box"] = _whole(_bounds(mark["points"]))
    elif mark["kind"] == "text":
        entry["at"], entry["text"] = _whole(mark["at"]), mark["text"]
    else:
        entry["box"] = _whole(mark["box"])
    return entry


def model_block(doc, marked_id):
    """The structured block the turn's attachment context carries for one marked-up picture."""
    comments = []
    for pin, comment in zip(pins(doc), doc.comments):
        entry = {"n": pin["n"]}
        entry.update({"box": _whole(comment["box"])} if "box" in comment else {"at": _whole(comment["at"])})
        if comment["text"].strip():
            entry["text"] = comment["text"].strip()
        comments.append(entry)
    return {"kind": KIND, "about": ABOUT, "original_picture": doc.image, "marked_picture": md.reference_id(marked_id),
            "size": list(doc.size), "coordinates": COORDINATES, "comments": comments,
            "marks": [_mark(mark) for mark in doc.marks], "instructions": doc.instructions.strip()}


def send_references(original, marked):
    """The message's picture records: the marked picture first (it is what the person shows), then the original."""
    return [{**marked, "markup_of": original["id"]}, dict(original)]


def flat_size(size):
    """The marked picture's size: the original's, shrunk by the whole step that brings its longest side to at most
    ``MAX_FLAT`` (as the picture's pixels are read, ``studio_tile_previews.bounded``), so the drawing and the picture
    under it have the same pixels."""
    w, h = size
    step = -(-max(w, h) // MAX_FLAT)   # ceil
    return max(1, w // step), max(1, h // step)


def flatten_ops(doc):
    """What to draw over the original, in its pixels, marks first and pins on top:

    ``("ellipse"|"rect", box, rgba, width)``, ``("arrow", (start, end), rgba, width, head)``,
    ``("stroke", points, rgba, width)``, ``("text", at, rgba, text, size)``, ``("region", box, rgba, width)`` and
    ``("pin", n, centre, radius)``. Widths, text and pins grow with the picture (``markup_doc.scale_of``) so marks
    read the same on a small capture and a 4K render.
    """
    scale = md.scale_of(doc.size)
    ops = []
    for mark in doc.marks:
        colour, width = md.COLOURS[mark["colour"]], STROKE_PX[mark["width"]] * scale
        kind = mark["kind"]
        if kind in ("ellipse", "rect"):
            ops.append((kind, list(mark["box"]), colour, width))
        elif kind == "arrow":
            ops.append(("arrow", (list(mark["start"]), list(mark["end"])), colour, width, max(4.0 * width, 12.0 * scale)))
        elif kind == "pen":
            ops.append(("stroke", [list(p) for p in mark["points"]], colour, width))
        else:
            ops.append(("text", list(mark["at"]), colour, mark["text"], md.TEXT_PX * scale * (0.75 + 0.25 * mark["width"])))
    for pin in pins(doc):
        if pin["box"]:
            ops.append(("region", list(pin["box"]), REGION, STROKE_PX[1] * scale))
        ops.append(("pin", pin["n"], pin["at"], PIN_PX * scale))
    return ops


FULL_BOX = n_("The message box has as many pictures as it can carry. Send it or remove one first")


def room(controller):
    """ValueError(FULL_BOX) when the message box carries no more pictures (``references.MAX_IMAGES``): checked before
    any picture is made for it (break-it of 2298d608dd1, N2)."""
    from .references import MAX_IMAGES
    if len(controller.attachments) >= MAX_IMAGES:
        raise ValueError(FULL_BOX)
