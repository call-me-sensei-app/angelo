# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What Media Studio's Workflows tab shows for a template run: its stage, parts and sheet geometry. No Blender.

The Workflows tab runs a template as an ordinary Generation Flow (``flow_templates``), so everything it
shows is read from that flow's evaluated states, which ``flow_host`` prepares on its timer:

* **FILL**: no flow yet, or its sheet hasn't been made. The card shows the template's one input.
* **SHEET**: the parts sheet is being made.
* **REVIEW**: the sheet is split and no part has been modelled: the review stop. Nothing is paid
  for again until the person chooses to make the parts.
* **PARTS**: per-part 3D models are being made, or some failed.
* **PLACE**: the body's model is ready and no part is still being made, so Place Character can run (failed
  parts can still be retried).

Geometry is in region pixels with y up, as Studio draws.
"""

from .flow_fanout import part_label
from .flow_templates import CHARACTER_STEP, PARTS_STEP, SHEET_STEP, SPLIT_STEP
from .ui_text import n_

FILL, SHEET, REVIEW, PARTS, PLACE = "FILL", "SHEET", "REVIEW", "PARTS", "PLACE"
DONE, MAKING, WAITING, FAILED = "DONE", "MAKING", "WAITING", "FAILED"
OFF = "OFF"   # A part the person left out at the review stop and that was never made (drawn only; never counted).
# Parts whose placement the research found unreliable (hats, hair, held items): the card asks to check them.
CHECK_KINDS = frozenset({"HAIR", "HEADWEAR", "ACCESSORY"})
# Parts Place Character can't do without (character_compose places everything on the body): never left out.
REQUIRED_KINDS = frozenset({"BODY"})
# What the tab calls a part, by its slot's kind (translated where drawn; slot ids and object names stay English).
# Context "Angelo": Blender's own catalogue translates "Hair" for hair particles (fur, body hair) in some languages.
KIND_TITLES = {"BODY": n_("Body", "Angelo"), "HAIR": n_("Hair", "Angelo"), "HEADWEAR": n_("Headwear", "Angelo"),
               "INNER": n_("Inner layer", "Angelo"), "OUTER": n_("Outer layer", "Angelo"),
               "LOWER": n_("Lower garments", "Angelo"), "FOOTWEAR": n_("Footwear", "Angelo"),
               "ACCESSORY": n_("Accessory", "Angelo")}


def _part_state(state):
    value = (state or {}).get("state")
    if value == "READY":
        return DONE
    if value == "RUNNING":
        return MAKING
    if value in {"FAILED", "BLOCKED"}:
        return FAILED
    return WAITING


def view(flow, states):
    """{stage, sheet, split, parts: [{id, label, kind, crop, step, status, model, check}], counts} for a template run."""
    states = states or {}
    split = (states.get(SPLIT_STEP) or {}).get("value") or {}
    steps = {node.get("part"): node["id"] for node in (flow or {}).get("nodes", [])
             if node.get("fanout") == PARTS_STEP and node["type"] == "GENERATE_3D"}
    parts = []
    for part in split.get("parts", []):
        step = steps.get(part["id"])
        state = states.get(step) if step else None
        parts.append({"id": part["id"], "label": part_label(part), "kind": part["kind"], "crop": part.get("asset"),
                      "title": KIND_TITLES.get(part["kind"]), "number": part["item"] + 1 if part.get("numbered") else None,
                      "step": step, "status": _part_state(state), "model": (state or {}).get("chosen"),
                      "check": part["kind"] in CHECK_KINDS, "required": part["kind"] in REQUIRED_KINDS,
                      # Failed and can be made again (a BLOCKED step, such as one without an account, can't be retried).
                      "retry": (state or {}).get("state") == "FAILED"})
    counts = {status: sum(1 for p in parts if p["status"] == status) for status in (DONE, MAKING, WAITING, FAILED)}
    sheet = states.get(SHEET_STEP) or {}
    if (states.get(CHARACTER_STEP) or {}).get("state") == "READY" and not counts[MAKING]:
        stage = PLACE   # Failed parts can still be retried from here; parts still being made keep it at PARTS.
    elif parts and (counts[DONE] or counts[MAKING] or counts[FAILED]):
        stage = PARTS
    elif parts:
        stage = REVIEW
    elif sheet.get("state") == "RUNNING" or (sheet.get("state") == "READY" and not split):
        stage = SHEET
    else:
        stage = FILL
    return {"stage": stage, "sheet": sheet, "split": split, "parts": parts, "counts": counts}


def tile_status(part, skipped):
    """A part's status as its tile says it: OFF for a part left out at the review stop that nothing has made (not
    WAITING, which would say it is still coming); a part made before it was left out keeps its own status."""
    return OFF if part["id"] in skipped and part["status"] == WAITING else part["status"]


def fit_count(parts, boxes, skipped):
    """(fitted, total) for the fit line: the parts Place puts on the body (not the body itself, none left out), and how
    many of them the fit found; a box kept for a part since left out counts for neither."""
    placed = [part["id"] for part in parts if part["kind"] not in REQUIRED_KINDS and part["id"] not in skipped]
    return sum(1 for identity in placed if identity in boxes), len(placed)


def chip_rows(widths, width, gap=6):
    """[(x offset, row, width)] for chips side by side in a column ``width`` wide: one that doesn't fit its row starts
    the next, and none is wider than the column (its text is elided), so no chip runs past the card."""
    placed, x, row = [], 0, 0
    for chip in widths:
        chip = min(chip, width)
        if x and x + chip > width:
            x, row = 0, row + 1
        placed.append((x, row, chip))
        x += chip + gap
    return placed


def pair(needs, width, gap=8):
    """The widths of two buttons sharing a row ``width`` wide: halves when both labels fit one, else each its label's
    need plus half the spare; None when the labels can't share the row (they stack, each full width)."""
    first, second = needs
    half = (width - gap) / 2
    if first <= half and second <= half:
        return half, half
    spare = width - gap - first - second
    return None if spare < 0 else (first + spare / 2, second + spare / 2)


def contain(size, rect):
    """(x, y, w, h) of an image of ``size`` (width, height) fitted inside ``rect``, centred, as Studio draws it."""
    x, y, w, h = rect
    width, height = size
    if width <= 0 or height <= 0 or w <= 0 or h <= 0:
        return (x, y, 0, 0)
    scale = min(w / width, h / height)
    iw, ih = width * scale, height * scale
    return (x + (w - iw) / 2, y + (h - ih) / 2, iw, ih)


def outlines(split, slots, drawn):
    """[(part id, label, (x, y, w, h))] over a sheet drawn at ``drawn``: each part's front-view box.

    Boxes are stored in sheet pixels, x from their cell's centre and y down from the cell top
    (``flow_parts``); ``split["grid"]`` holds the cells' edges and ``split["size"]`` the sheet size.
    """
    grid, size = split.get("grid"), split.get("size")
    if not grid or not size or not drawn[2] or not drawn[3]:
        return []
    columns = {slot["id"]: index for index, slot in enumerate(slots)}
    scale = drawn[2] / size[0]
    boxes = []
    for part in split.get("parts", []):
        column = columns.get(part["slot"])
        if column is None or column + 1 >= len(grid["x"]):
            continue
        centre = (grid["x"][column] + grid["x"][column + 1]) / 2
        x0, y0, x1, y1 = part["box"]
        top = grid["y"][0]
        left, right = centre + x0, centre + x1
        # Sheet y runs down from the top; Studio's y runs up from the bottom.
        rect = (drawn[0] + left * scale, drawn[1] + drawn[3] - (top + y1) * scale, (right - left) * scale, (y1 - y0) * scale)
        boxes.append((part["id"], part_label(part), rect))
    return boxes
