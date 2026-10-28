# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Parts-sheet geometry for the Split Sheet step. numpy only; no Blender, no provider.

A parts sheet is a grid on a plain white background: one column per slot (body,
hair, headwear, ...) and one row per view (front on top, back below), with every
cell drawn in the same full-height figure frame. ``split_sheet`` turns its
pixels into parts:

* **Cells.** Thin, nearly full-length light-grey lines separate the cells. When
  the expected number of lines isn't found, the sheet is split evenly.
* **Background.** Near-white pixels connected to the cell's edge are background,
  so an opening that reaches the outside (the face inside a beard, an unbuttoned
  collar) becomes transparent. An enclosed near-white region becomes
  transparent only when its core is as flat as the background (``HOLE_FLATNESS``),
  so a white garment's shaded interior always stays opaque. Image-to-3D providers rebuild
  an opaque white hole as a surface, so getting this right matters more than
  soft edges.
* **Items.** A slot whose template marks ``items: "split"`` (accessories) yields
  one part per island, so a pair of earrings and a bag become separate meshes
  with their own boxes. Other slots stay whole (a pair of boots is one part).
* **Boxes.** Each part records its front-view box in the cell's figure frame,
  with x measured from the cell centre (cells can differ in width) and y from
  the cell top. Compose uses these to place parts against the body.

Everything here is deterministic for the same pixels and layout, so the host can
cache a split by the sheet asset and layout.
"""

import numpy as np

BACKGROUND_TOLERANCE = 24      # max channel distance from white still treated as background
# An enclosed near-white region is a hole when its core (away from anti-aliased edges) is this flat:
# holes measured 4-7 at the 90th percentile and shaded white fabric 10-17 (jackets, hats, a turtleneck,
# on four research sheets), so 7 keeps every white garment opaque.
HOLE_FLATNESS = 7
HOLE_CORE_EROSION = 3
LINE_MAX_WIDTH = 10            # px; a separator line is thin, wide grey runs are content
LINE_MIN_COVERAGE = 0.95       # share of the sheet's height (or width) a separator spans
SPECK_SHARE = 0.001            # islands smaller than this share of a cell are noise
EMPTY_SHARE = 0.002            # a cell with less foreground than this share is empty
MERGE_GAP_SHARE = 0.01         # islands whose pixels come closer than this share of cell height are one item
MAX_ITEMS_PER_SLOT = 6
PROFILE_SAMPLES = 48           # body silhouette widths recorded for compose's body-relative scale


class SheetError(ValueError):
    """The pixels don't form a usable parts sheet; the message says why."""


def _as_rgb(pixels):
    array = np.asarray(pixels)
    if array.ndim != 3 or array.shape[2] < 3:
        raise SheetError("A parts sheet is an RGB image")
    rgb = array[..., :3]
    if rgb.dtype.kind == "f":
        rgb = np.clip(rgb * 255.0 + 0.5, 0, 255)
    return rgb.astype(np.int16)


def _grey_lines(rgb):
    """Boolean masks of pixels that look like the light-grey separator lines."""
    mean = rgb.mean(axis=2)
    neutral = (np.abs(rgb - mean[..., None]).max(axis=2) < 8) & (mean > 150) & (mean < 245)
    return neutral


def _runs(indices, max_width=LINE_MAX_WIDTH):
    """Centres of runs of consecutive indices no wider than ``max_width``."""
    centres, start = [], None
    for position, index in enumerate(indices):
        if start is None:
            start = index
        last = position == len(indices) - 1 or indices[position + 1] != index + 1
        if last:
            if index - start + 1 <= max_width:
                centres.append((start + index) // 2)
            start = None
    return centres


def _bounds(length, count, detected):
    margin = 0.02 * length
    inner = [c for c in detected if margin < c < length - margin]
    if len(inner) == count - 1:
        return [0] + inner + [length], True
    return [round(length * i / count) for i in range(count + 1)], False


def cell_grid(pixels, columns, rows):
    """((x bounds), (y bounds), separators_found) for a ``columns`` x ``rows`` sheet."""
    if not (1 <= columns <= 16 and 1 <= rows <= 4):
        raise SheetError("A parts sheet has 1-16 columns and 1-4 rows")
    rgb = _as_rgb(pixels)
    grey = _grey_lines(rgb)
    height, width = grey.shape
    xs = _runs(np.flatnonzero(grey.mean(axis=0) > LINE_MIN_COVERAGE))
    ys = _runs(np.flatnonzero(grey.mean(axis=1) > LINE_MIN_COVERAGE))
    xb, x_found = _bounds(width, columns, xs)
    yb, y_found = _bounds(height, rows, ys)
    return xb, yb, x_found and (rows == 1 or y_found)


def _label(mask):
    """4-connected component labels of a boolean mask: (labels int32, count).

    Run-length union-find: Python loops over row runs, not pixels, so a
    1000 x 500 cell with a few thousand runs labels in milliseconds.
    """
    height, width = mask.shape
    padded = np.zeros((height, width + 2), bool)
    padded[:, 1:-1] = mask
    changes = np.diff(padded.astype(np.int8), axis=1)
    starts_y, starts_x = np.nonzero(changes == 1)
    ends_y, ends_x = np.nonzero(changes == -1)
    runs = list(zip(starts_y.tolist(), starts_x.tolist(), ends_x.tolist()))  # [x0, x1) per row, row-major
    parent = list(range(len(runs)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    previous, previous_row, current = [], -1, []
    for index, (y, x0, x1) in enumerate(runs):
        if y != previous_row:
            previous = current if y == previous_row + 1 else []
            current, previous_row = [], y
        for other in previous:
            oy, ox0, ox1 = runs[other]
            if ox0 < x1 and x0 < ox1:  # 4-connected: the runs overlap in x
                a, b = find(index), find(other)
                if a != b:
                    parent[a] = b
        current.append(index)
    roots, labels = {}, np.zeros((height, width), np.int32)
    for index, (y, x0, x1) in enumerate(runs):
        root = find(index)
        labels[y, x0:x1] = roots.setdefault(root, len(roots) + 1)
    return labels, len(roots)


def _erode(mask, iterations):
    """4-neighbour binary erosion without scipy."""
    eroded = mask.copy()
    for _ in range(iterations):
        inner = eroded.copy()
        inner[1:, :] &= eroded[:-1, :]
        inner[:-1, :] &= eroded[1:, :]
        inner[:, 1:] &= eroded[:, :-1]
        inner[:, :-1] &= eroded[:, 1:]
        inner[0, :] = inner[-1, :] = False
        inner[:, 0] = inner[:, -1] = False
        eroded = inner
    return eroded


def alpha_mask(cell_rgb):
    """(alpha 0..1 float32, foreground bool) for one cell on a white background."""
    distance = np.abs(cell_rgb - 255).max(axis=2)
    near_white = distance <= BACKGROUND_TOLERANCE
    labels, count = _label(near_white)
    edge = np.unique(np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]]))
    background = np.isin(labels, edge[edge > 0])
    enclosed = near_white & ~background
    if enclosed.any():
        # One erosion for every enclosed region, then each region's 90th percentile from a sort by label.
        core = _erode(enclosed, HOLE_CORE_EROSION)
        core_labels, core_values = labels[core], distance[core]
        if core_labels.size:
            order = np.lexsort((core_values, core_labels))
            core_labels, core_values = core_labels[order], core_values[order]
            starts = np.flatnonzero(np.r_[True, core_labels[1:] != core_labels[:-1]])
            ends = np.r_[starts[1:], core_labels.size]
            flat = [int(core_labels[a]) for a, b in zip(starts, ends)
                    if b - a >= 20 and core_values[a + int(0.9 * (b - a - 1))] <= HOLE_FLATNESS]
            if flat:
                background |= np.isin(labels, flat)
    # Background is transparent; its anti-aliased rim fades in with the distance from white.
    alpha = np.where(background, np.clip((distance - HOLE_FLATNESS) / float(BACKGROUND_TOLERANCE - HOLE_FLATNESS), 0.0, 1.0),
                     1.0).astype(np.float32)
    return alpha, alpha > 0.5


def _dilate(mask, iterations):
    """4-neighbour binary dilation without scipy."""
    grown = mask.copy()
    for _ in range(iterations):
        step = grown.copy()
        step[1:, :] |= grown[:-1, :]
        step[:-1, :] |= grown[1:, :]
        step[:, 1:] |= grown[:, :-1]
        step[:, :-1] |= grown[:, 1:]
        grown = step
    return grown


def _islands(foreground, cell_height):
    """The cell's items as (box (x0, y0, x1, y1), pixel mask), top to bottom: islands closer than
    ``MERGE_GAP_SHARE`` of the cell height are one item, specks and separator remnants dropped.

    Nearness is measured between pixels, not boxes: a bag whose strap reaches up beside a scarf is its own item
    even though their boxes overlap, so each item also carries its own pixels for its cut-out. A piece enclosed by
    another item (a pendant inside its chain's loop) belongs to it, however far apart.
    """
    height, width = foreground.shape
    total = foreground.size
    gap = max(1, int(round(MERGE_GAP_SHARE * cell_height)))
    labels, count = _label(foreground)
    keep = np.zeros(count + 1, bool)
    for index, (ys, xs) in enumerate(_pixels_by_label(labels, count), start=1):
        if ys.size < SPECK_SHARE * total:
            continue
        # A separator line the cell's inset didn't clear: thin, spanning the cell, and along its edge (an item drawn
        # across the cell, such as a staff, sits inside it).
        across = (xs.max() - xs.min() + 1 >= LINE_MIN_COVERAGE * width and ys.max() - ys.min() + 1 <= LINE_MAX_WIDTH
                  and (ys.min() <= 2 * LINE_MAX_WIDTH or ys.max() >= height - 1 - 2 * LINE_MAX_WIDTH))
        down = (ys.max() - ys.min() + 1 >= LINE_MIN_COVERAGE * height and xs.max() - xs.min() + 1 <= LINE_MAX_WIDTH
                and (xs.min() <= 2 * LINE_MAX_WIDTH or xs.max() >= width - 1 - 2 * LINE_MAX_WIDTH))
        keep[index] = not (across or down)
    kept = keep[labels]
    # Items: islands whose pixels come within ``gap`` of each other (half the gap grown around each) group together.
    groups, _ = _label(_dilate(kept, (gap + 1) // 2))
    members = [kept & (groups == group) for group in np.unique(groups[kept])]
    members = _absorb_enclosed(members)
    items = []
    for member in members:
        ys, xs = np.nonzero(member)
        items.append(([int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())], _dilate(member, 2)))
    return sorted(items, key=lambda item: (item[0][1], item[0][0]))


def _absorb_enclosed(members):
    """Merge each item lying wholly inside another item's enclosed holes into that item."""
    merged = True
    while merged and len(members) > 1:
        merged = False
        for a, outer in enumerate(members):
            background, _ = _label(~outer)
            edge = np.unique(np.concatenate([background[0], background[-1], background[:, 0], background[:, -1]]))
            holes = (background > 0) & ~np.isin(background, edge)
            if not holes.any():
                continue
            for b, inner in enumerate(members):
                if b != a and not (inner & ~holes).any():
                    members[a] = outer | inner
                    members.pop(b)
                    merged = True
                    break
            if merged:
                break
    return members


def _pixels_by_label(labels, count):
    """(ys, xs) of each label 1..count, from one sort instead of a scan per label."""
    flat = labels.ravel()
    order = np.argsort(flat, kind="stable")
    bounds = np.searchsorted(flat[order], np.arange(1, count + 2))
    width = labels.shape[1]
    for start, stop in zip(bounds[:-1], bounds[1:]):
        positions = order[start:stop]
        yield positions // width, positions % width


def _part(cell_rgb, alpha, box, cell_width):
    x0, y0, x1, y1 = box
    rgba = np.concatenate([cell_rgb[y0:y1 + 1, x0:x1 + 1].astype(np.uint8),
                           (alpha[y0:y1 + 1, x0:x1 + 1, None] * 255 + 0.5).astype(np.uint8)], axis=2)
    half = cell_width / 2.0
    return {"box": [round(x0 - half, 1), int(y0), round(x1 - half, 1), int(y1)], "rgba": rgba}


def body_profile(foreground, box, cell_width, samples=PROFILE_SAMPLES):
    """[[y, x0, x1]] of the body's silhouette at evenly spaced heights, in the part box's frame.

    Compose compares these drawn widths with the generated 3D body's widths at the same heights,
    so clothing follows the body that was actually generated rather than the drawing.
    """
    x0, y0, x1, y1 = box
    half = cell_width / 2.0
    profile = []
    for y in np.linspace(y0, y1, samples).round().astype(int):
        xs = np.flatnonzero(foreground[y, x0:x1 + 1])
        if xs.size:
            profile.append([int(y), round(float(xs.min() + x0 - half), 1), round(float(xs.max() + x0 - half), 1)])
    return profile


def split_sheet(pixels, slots, *, rows=2):
    """Parts of a sheet. ``slots`` lists each column's ``{"id", "kind", "items"}`` from the template.

    Returns ``{"grid_found", "cell_height", "grid": {"x", "y"}, "size": [width, height], "parts": [...]}``
    (``grid``: the cells' edges in sheet pixels, for drawing boxes over the sheet). Each part is
    ``{"slot", "kind", "item", "front": {"box", "rgba"}, "back": {...} | None}``;
    ``back`` is the matching back-view crop of a whole part (split items don't
    pair reliably across views and have none). Empty columns yield no parts.
    """
    rgb = _as_rgb(pixels)
    xb, yb, found = cell_grid(rgb, len(slots), rows)
    cell_height = yb[1] - yb[0]
    parts = []
    inset = 6  # stay clear of the separator line's anti-aliasing
    for column, slot in enumerate(slots):
        views = []
        for row in range(min(rows, 2)):
            x0, x1, y0, y1 = xb[column] + inset, xb[column + 1] - inset, yb[row] + inset, yb[row + 1] - inset
            cell = rgb[y0:y1, x0:x1]
            alpha, foreground = alpha_mask(cell)
            views.append((cell, alpha, foreground))
        cell, alpha, foreground = views[0]
        if foreground.mean() < EMPTY_SHARE:
            continue
        if slot.get("items") == "split":
            items = _islands(foreground, cell.shape[0])[:MAX_ITEMS_PER_SLOT]
            for item, (box, member) in enumerate(items):
                # Only this item's pixels (and their anti-aliased rim): another item can reach into its box.
                parts.append({"slot": slot["id"], "kind": slot["kind"], "item": item,
                              "front": _part(cell, alpha * member, box, cell.shape[1]), "back": None})
            continue
        ys, xs = np.nonzero(foreground)
        front = _part(cell, alpha, [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())], cell.shape[1])
        back = None
        if len(views) > 1 and views[1][2].mean() >= EMPTY_SHARE:
            bcell, balpha, bfore = views[1]
            bys, bxs = np.nonzero(bfore)
            back = _part(bcell, balpha, [int(bxs.min()), int(bys.min()), int(bxs.max()), int(bys.max())], bcell.shape[1])
        part = {"slot": slot["id"], "kind": slot["kind"], "item": 0, "front": front, "back": back}
        if slot["kind"] == "BODY":
            part["profile"] = body_profile(foreground, [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
                                           cell.shape[1])
        parts.append(part)
    if not any(part["kind"] == "BODY" for part in parts):
        raise SheetError("The sheet has no base body in its body column; regenerate the sheet")
    return {"grid_found": bool(found), "cell_height": int(cell_height), "parts": parts,
            "grid": {"x": [int(v) for v in xb], "y": [int(v) for v in yb]}, "size": [int(rgb.shape[1]), int(rgb.shape[0])]}
