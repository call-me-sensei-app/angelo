# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` reference pairs: an attached image beside a free camera's view, in one picture.

A free camera item with ``reference`` (view_spec) names an image attached to
this conversation. Its view is drawn at that image's aspect, so a camera at
the image's viewpoint frames the scene as the image frames it, and the
picture the model gets is the image on the left and the view on the right,
each ``resolution`` pixels on its longer side, a dark gap between them
(agent_execution.rst, "Free cameras and reference pictures").

* **Read-only.** The image is read from the conversation's private folder by
  its id (``ReferenceStore.attached_image``), never from a path the model
  gives. ``imbuf`` decodes and scales it outside Blender's Main: no
  data-block, Undo step or file is made, as for ``view_picture``.
* **Bounded before decoding.** The size comes from the file's first bytes
  (``view_picture.header`` for PNG and JPEG, ``webp_size`` for WebP, and
  palette or grey PNGs of 1-4 bits) and an image too large to decode, or more
  than ``MAX_ASPECT`` times wider than tall or taller than wide, is refused
  naming the field; so is a file that does not decode, without its path.
* **As displayed.** A JPEG's EXIF orientation, which imbuf does not apply, is
  applied, so a phone photo stored sideways is shown and matched upright.
* **The view's pixels are untouched.** ``encoder`` returns the function that
  makes the PNG from the GPU read on a worker thread (NumPy and the standard
  library, never ``bpy``): the reference's pixels, prepared on the main
  thread, are placed beside the drawn view, which is copied as drawn.
* **Over the view, or as outlines** (``compare``). OVERLAY is one picture of
  the view's size, the image at half opacity over it. OUTLINE is the view
  with two outlines on it: the image's subject (``view_outline.subject_mask``:
  its transparent pixels, or a plain background found from its border) in
  orange, and the targets' silhouette at the same camera
  (``view_silhouette``, drawn on the main thread before the view) in cyan;
  where only one of them covers is tinted its colour, and the record states
  their distances in pixels and world units, overlap, centre offset and size
  ratio (``view_outline.compare``), worked out on the worker thread.
"""

import struct

from .refusal_codes import RefusalError
from .view_png import encode_png
from .view_spec import OUTLINE, OVERLAY, PAIR

GAP = 2                # Dark pixels between the image and the view (view_motion's tile gap).
BACKGROUND = 24
MAX_ASPECT = 8.0       # Widest or tallest reference shape a pair is made for.
REFERENCE_RGB, TARGET_RGB = (255, 140, 0), (0, 220, 255)   # OUTLINE: the image's subject, the targets.
TINT = 0.35            # How strongly OUTLINE tints where only one of them covers.
LAYOUTS = {PAIR: "REFERENCE_LEFT_VIEW_RIGHT", OVERLAY: "REFERENCE_OVER_VIEW", OUTLINE: "OUTLINES_ON_VIEW"}
# A side-by-side pair is twice a view's width: its halves are scaled down so the picture stays deliverable, at
# most this wide (Claude's limit on an image's side when a request holds many images) and this many pixels (an
# RGB PNG of them stays under the conversation's 5 MiB image limit even when nothing compresses).
MAX_PICTURE_SIDE = 2000
MAX_PICTURE_PIXELS = 1_600_000


def webp_size(path):
    """``(width, height)`` of a WebP file from its first chunk header, or None for another file."""
    with open(path, "rb") as handle:
        head = handle.read(30)
    if len(head) < 30 or head[:4] != b"RIFF" or head[8:12] != b"WEBP":
        return None
    kind = head[12:16]
    if kind == b"VP8 " and head[23:26] == b"\x9d\x01\x2a":
        width, height = struct.unpack("<HH", head[26:30])
        return width & 0x3FFF, height & 0x3FFF
    if kind == b"VP8L" and head[20] == 0x2F:
        bits = int.from_bytes(head[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if kind == b"VP8X":
        return int.from_bytes(head[24:27], "little") + 1, int.from_bytes(head[27:30], "little") + 1
    return None


ORIENTATION_TAG = 0x0112  # EXIF Orientation: 1 as stored; 2-8 mirror and/or rotate it for display.
# Display from stored rows (top row first) for each EXIF orientation; 5 to 8 also swap width and height.
_ORIENT = {2: lambda r: r[:, ::-1], 3: lambda r: r[::-1, ::-1], 4: lambda r: r[::-1],
           5: lambda r: r.transpose(1, 0, 2), 6: lambda r: r[::-1].transpose(1, 0, 2),
           7: lambda r: r[::-1, ::-1].transpose(1, 0, 2), 8: lambda r: r[:, ::-1].transpose(1, 0, 2)}


def _png_low_depth_size(path):
    """``(width, height)`` of a PNG of 1, 2 or 4 bits a sample (palette or grey), which imbuf decodes, else None."""
    with open(path, "rb") as handle:
        head = handle.read(26)
    if len(head) == 26 and head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR" and head[24] in (1, 2, 4):
        return struct.unpack(">II", head[16:24])
    return None


def _tiff_orientation(tiff):
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return 1
    order = "<" if tiff[:2] == b"II" else ">"
    offset = struct.unpack(order + "I", tiff[4:8])[0]
    if offset + 2 > len(tiff):
        return 1
    for index in range(min(struct.unpack(order + "H", tiff[offset:offset + 2])[0], 256)):
        entry = offset + 2 + index * 12
        if entry + 12 > len(tiff):
            return 1
        tag, kind = struct.unpack(order + "HH", tiff[entry:entry + 4])
        if tag == ORIENTATION_TAG and kind == 3:  # SHORT
            value = struct.unpack(order + "H", tiff[entry + 8:entry + 10])[0]
            return value if 1 <= value <= 8 else 1
    return 1


def jpeg_orientation(path):
    """The EXIF orientation (1-8) a JPEG's APP1 segment states; 1 when it states none (imbuf applies none)."""
    with open(path, "rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            return 1
        for _segment in range(64):
            marker = handle.read(4)
            if len(marker) < 4 or marker[0] != 0xFF or marker[1] in (0xD9, 0xDA):
                return 1
            length = struct.unpack(">H", marker[2:4])[0]
            if length < 2:
                return 1
            body = handle.read(length - 2)
            if marker[1] == 0xE1 and body[:6] == b"Exif\x00\x00":
                return _tiff_orientation(body[6:])
    return 1


def reference(references, reference_id, where):
    """An attached image's facts for a pair, or a VALIDATION refusal naming ``where`` (``views[1].reference``).

    ``{"id", "path", "field", "stored_size", "orientation", "source_size", "aspect", "bytes"}``: ``source_size``
    and ``aspect`` are as the image is displayed (EXIF orientation applied). Nothing is decoded here.
    """
    from . import view_picture
    try:
        path = references.attached_image(reference_id)
    except ValueError as error:
        raise RefusalError("%s: %s (an attached image's reference_id is listed with the message that attached it)"
                           % (where, error), code="VALIDATION") from None
    found = view_picture.header(path) if path.suffix != ".webp" else None
    size = found[:2] if found else webp_size(path) if path.suffix == ".webp" else _png_low_depth_size(path)
    if size is None or size[0] < 1 or size[1] < 1:
        raise RefusalError("%s: the image's size cannot be read from its header (a PNG, JPEG or WebP image)" % where,
                           code="VALIDATION")
    limit = view_picture.MAX_FLOAT_PIXELS if found and found[2] != 8 else view_picture.MAX_PIXELS
    if size[0] * size[1] > limit:
        raise RefusalError("%s is %dx%d: more than the %d million pixels of this kind that can be shown"
                           % ((where,) + tuple(size) + (limit // 1_000_000,)), code="VALIDATION")
    orientation = jpeg_orientation(path) if found and path.suffix in (".jpg", ".jpeg") else 1
    width, height = size[::-1] if orientation >= 5 else size
    if max(width / height, height / width) > MAX_ASPECT:
        raise RefusalError("%s is a %dx%d image: a pair is made for images at most %g times wider than tall or "
                           "taller than wide" % (where, width, height, MAX_ASPECT), code="VALIDATION")
    return {"id": path.stem, "path": path, "field": where, "stored_size": list(size), "orientation": orientation,
            "source_size": [width, height], "aspect": width / height, "bytes": path.stat().st_size}


def tile(pair, size):
    """The reference's pixels at ``size`` (width, height) as displayed: ``(RGBA rows top first, transparent share,
    its alpha rows or None when opaque)``.

    Main thread (``imbuf``). EXIF orientation is applied; transparency is shown over view_picture's grey
    checkerboard and stated. A file that does not decode is a VALIDATION refusal naming the field, never the
    private path imbuf's error holds.
    """
    import imbuf
    import numpy
    from .view_picture import flatten
    stored = tuple(size[::-1]) if pair["orientation"] >= 5 else tuple(size)  # Scaled as stored, then turned.
    try:
        buffer = imbuf.load(str(pair["path"]))
    except (ValueError, OSError, RuntimeError):
        raise RefusalError("%s: the attached image could not be decoded" % pair["field"], code="VALIDATION") from None
    try:
        if tuple(buffer.size) != tuple(pair["stored_size"]):
            raise RefusalError("%s: the image's pixels do not match its header's size" % pair["field"],
                               code="VALIDATION")
        if tuple(buffer.size) != stored:
            buffer.resize(stored, method="BILINEAR")  # A box filter when scaling down.
        if buffer.buffer_type != "BYTE":
            buffer.convert_buffer_type("BYTE")
        with buffer.with_buffer() as view:
            rows = numpy.array(numpy.asarray(view)[::-1])  # A copy, top row first, before the buffer is freed.
    finally:
        buffer.free()
    turn = _ORIENT.get(pair["orientation"])
    rows = numpy.ascontiguousarray(turn(rows)) if turn else rows
    shown, share = flatten(rows)
    return shown, share, (rows[..., 3].copy() if share else None)


def encoder(rows, compare=PAIR, alpha=None, target=None, facts=None, depth=None, camera=None):
    """The PNG maker of one pair: ``encode(rgba, width, height)`` of the drawn view (bottom row first).

    OUTLINE needs the targets' mask and depth (rows top first) and the camera, and fills ``facts`` with the
    comparison.
    """
    def encode(rgba, width, height):
        import numpy
        view = numpy.frombuffer(bytes(rgba), dtype=numpy.uint8).reshape(height, width, 4)[::-1]
        if compare != OUTLINE and target is not None:   # With targets, a pair or overlay states OUTLINE's measures.
            measure(rows, alpha, target, facts, depth, camera)
        if compare == OVERLAY:
            sheet = ((view.astype(numpy.uint16) + rows) // 2).astype(numpy.uint8)
            sheet[..., 3] = 255
        elif compare == OUTLINE:
            sheet = outlined(view, rows, alpha, target, facts, depth, camera)
        else:
            sheet = numpy.empty((height, width * 2 + GAP, 4), dtype=numpy.uint8)
            sheet[...] = BACKGROUND
            sheet[..., 3] = 255
            sheet[:, :width] = rows
            sheet[:, width + GAP:] = view
        return encode_png(sheet.tobytes(), sheet.shape[1], height, bottom_up=False)
    return encode


def outlined(view, rows, alpha, target, facts, depth=None, camera=None):
    """The view with the image's subject and the targets outlined and their difference tinted; fills ``facts``."""
    import numpy
    from . import view_outline
    subject, target = measure(rows, alpha, target, facts, depth, camera)
    sheet = view.astype(numpy.float32)
    for only, colour in ((subject & ~target, REFERENCE_RGB), (target & ~subject, TARGET_RGB)):
        sheet[only, :3] = sheet[only, :3] * (1.0 - TINT) + numpy.array(colour, numpy.float32) * TINT
    sheet = numpy.clip(sheet + 0.5, 0, 255).astype(numpy.uint8)
    sheet[..., 3] = 255
    view_outline.draw_edges(sheet, subject, REFERENCE_RGB, width=2)
    return view_outline.draw_edges(sheet, target, TARGET_RGB, width=2)


def measure(rows, alpha, target, facts, depth=None, camera=None):
    """The image's subject and the targets' outline, made alike, and their comparison in ``facts``."""
    from . import view_outline
    if alpha is not None:   # A cut-out: its own transparency says what the subject is, holes included.
        raw, found = alpha > 127, {"subject_from": "TRANSPARENCY"}
    else:
        raw, found = view_outline.subject_mask(rows)
        found["subject_from"] = found.pop("background") + "_BACKGROUND"
    # Both outlines made the same way (view_outline.normalized); holes compared only where both can show them.
    holes = alpha is not None
    subject, target = view_outline.normalized(raw, holes), view_outline.normalized(target, holes)
    found.update(touches_border=view_outline.touches_border(subject), holes="COMPARED" if holes else "FILLED")
    scale = view_outline.outline_pixel_world(target, depth, camera) if depth is not None else None
    if scale:
        facts.update(pixel_world=scale, pixel_world_at="TARGETS_OUTLINE")
    elif facts.get("pixel_world"):
        facts["pixel_world_at"] = "LOOK_AT"
    facts.update(view_outline.compare(subject, target, facts.get("pixel_world")), reference=found,
                 closed_px=view_outline.close_radius(target.shape))
    if facts["compared"] and depth is not None:
        facts["gaps"] = view_outline.gaps(subject, target, depth, camera)
    return subject, target


def fitted(width, height, item):
    """A pair's view size (``width`` x ``height`` each half) scaled down, aspect kept, until its picture fits."""
    if type(item) is not dict or item.get("compare", PAIR) != PAIR:
        return width, height
    import math
    scale = min(1.0, (MAX_PICTURE_SIDE - GAP) / (2.0 * width),
                math.sqrt(MAX_PICTURE_PIXELS / float((2 * width + GAP) * height)))
    return max(1, int(width * scale)), max(1, int(height * scale))


def picture_size(width, height, compare=PAIR):
    """The size of a pair's picture whose image and view are each ``width`` x ``height``."""
    return (width * 2 + GAP if compare == PAIR else width), height


def prepare(plans, window=None, objects=()):
    """Give every pair's plan its image's pixels and its PNG maker (``encode``). Main thread, before drawing.

    An image several pairs of one call share is decoded once for each size it is shown at; OUTLINE plans
    also get the targets' silhouette at their camera (``view_silhouette``), the targets read once.
    """
    decoded, shapes = {}, None
    for plan in plans:
        pair = plan.get("pair")
        if pair is not None:
            key = (pair["id"], plan["width"], plan["height"])
            if key not in decoded:
                decoded[key] = tile(pair, (plan["width"], plan["height"]))
            rows, pair["transparent"], alpha = decoded[key]
            compare = plan["item"].get("compare", PAIR) if type(plan.get("item")) is dict else PAIR
            if compare != OUTLINE and not objects:
                plan["encode"] = encoder(rows, compare)
                continue
            from . import view_silhouette
            if shapes is None:
                shapes = view_silhouette.geometry(window, objects)
            mask, depth, plan["outline"] = view_silhouette.draw(shapes[0], plan["camera"], plan["width"],
                                                                plan["height"])
            plan["outline"].pop("strands", None)
            for key in ("not_drawn", "particle_hair_cut_at_points"):
                if shapes[1].get(key):
                    plan["outline"][key] = shapes[1][key]
            plan["encode"] = encoder(rows, compare, alpha, mask, plan["outline"], depth, plan["camera"])


def _compare(plan):
    return plan["item"].get("compare", PAIR) if type(plan.get("item")) is dict else PAIR


def with_pair(plan, view):
    """A view record, with a pair's layout, sizes and reference when ``plan`` is a pair's."""
    if plan.get("pair") is not None:
        view.update(pair_record(plan["pair"], plan["width"], plan["height"], _compare(plan)))
        if "outline" in plan:
            view["outline"] = plan["outline"]
    return view


def note(plans):
    """What a result's note adds when it holds a pair ('' otherwise)."""
    shown = {_compare(plan) for plan in plans if plan.get("pair") is not None}
    text = ""
    if PAIR in shown:
        text += (" A reference pair is one picture: the attached image on the left, scaled to the view's size, and "
                 "the view drawn at the image's aspect on the right.")
    if OVERLAY in shown:
        text += " An OVERLAY is the attached image at half opacity over the view drawn at its aspect."
    if OUTLINE in shown:
        text += (" An OUTLINE is the view with the image's subject outlined orange and the targets' silhouette cyan, "
                 "tinted where only one covers.")
    if any("outline" in plan for plan in plans):   # An OUTLINE, or a pair or overlay with targets (not drawn).
        text += (" A view's outline compares the image's subject with the targets' silhouette, both outer outlines "
                 "made alike (gaps under closed_px*2 joined, holes filled unless the image is a cut-out): their "
                 "distances both ways (px, and world at the targets' outline depth), overlap, centre offset "
                 "[right, down] and size ratio, and gaps: where they are farthest apart, the targets' surface point "
                 "there (surface_world) and the offset in the view's plane to the image's outline (offset_world; "
                 "OUT: the image reaches past the targets, IN: the targets reach past it).")
    return text


def notes(plans):
    """Uncertainty about OUTLINE comparisons, once every picture is made."""
    found = []
    for index, plan in enumerate(plans):
        facts = plan.get("outline") or {}
        source = facts.get("reference", {}).get("subject_from")
        if source == "BUSY_BACKGROUND":
            found.append({"code": "reference_background_busy", "detail": (
                "views[%d]: the image's border is not one colour, so its subject outline is a guess from the colour "
                "of its border" % index)})
        elif source == "AMBIGUOUS_BACKGROUND":
            found.append({"code": "reference_background_ambiguous", "detail": (
                "views[%d]: a second colour covers %d%% of the image's border; the other (%d%%) was taken as the "
                "background, which may be the subject's own colour. A cut-out (transparent background) is outlined "
                "by its transparency" % (index, round(100 * (1 - facts["reference"]["background_share_of_border"])),
                                         round(100 * facts["reference"]["background_share_of_border"])))})
        if facts.get("particle_hair_cut_at_points"):
            found.append({"code": "outline_particle_hair_cut", "detail": (
                "views[%d]: particle hair is drawn up to %d points; the rest is not in the targets' outline"
                % (index, facts["particle_hair_cut_at_points"]))})
        if facts.get("reference", {}).get("touches_border"):
            found.append({"code": "reference_subject_cropped", "detail": (
                "views[%d]: the image's subject touches its border, so part of its outline is the image's edge"
                % index)})
        if facts.get("not_drawn"):
            found.append({"code": "outline_targets_not_drawn", "detail": (
                "views[%d]: %d targets have no surface or curves to outline" % (index, facts["not_drawn"]))})
    return found


def pair_record(pair, width, height, compare=PAIR):
    """What a pair's view record states besides a view's: the picture's layout, size and the reference's facts.

    ``width`` and ``height`` are the view's (and the reference's shown) size; the record's are the picture's.
    """
    picture_width, picture_height = picture_size(width, height, compare)
    reference = {"reference_id": pair["id"], "source_size": list(pair["source_size"]), "shown_size": [width, height]}
    if pair.get("transparent"):
        reference["transparent"] = pair["transparent"]  # The fraction shown over a grey checkerboard.
    if pair.get("orientation", 1) != 1:
        reference["exif_orientation"] = pair["orientation"]  # Applied: source_size is the displayed size.
    return {"width": picture_width, "height": picture_height, "view_size": [width, height],
            "layout": LAYOUTS[compare], "reference": reference}
