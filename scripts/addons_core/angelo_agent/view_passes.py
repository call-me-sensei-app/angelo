# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image: "<source>:<pass>"``: one pass of one view layer of a multilayer render.

``RENDER_RESULT:<pass>``: Blender saves its Render Result once as a multilayer
OpenEXR into the conversation's private folder, with an image format of the
save's own, never writing the scene (``view_save``).
``j<n>:<pass>`` and ``LAST_JOB:<pass>``: a job's multilayer output, through
the existing private copy. ``view_exr`` reads the pass's channels only
(doc/angelo/view_sources.rst, "Render passes and view layers").

``<pass>`` is a pass name on the scene's active view layer, else on the only
layer that has it, or ``<layer>.<pass>``. **What each pass looks like:**
colour passes (RGB or RGBA) go through Blender's own display copy
(``view_finished.linear_file``), so they look as the Image Editor shows that
pass; data passes are shown as stated values and the result says how: depth
and mist near-white to far-black over their finite range, normals as
``(n + 1) / 2``, positions per axis over their range, object and material
indices and Cryptomatte as one colour per ID. Main thread; model-facing text
is English.
"""

from .view_exr import ExrError, read_channels, read_parts, write_rgba
from .view_pixels import encode_rgba_png, to_bytes

LISTED = 12             # Passes a refusal names.
FAR = 1e9               # Depth at or beyond this is no hit (Cycles writes 1e10).
DATA_PASSES = ("Normal", "Position", "Vector", "UV", "Denoising Normal", "Denoising Depth")
UNSET_SPACE = "render.image_settings.linear_colorspace_settings.name"


def split_channel(name):
    """``(layer, pass, channel)`` of a multilayer channel name; Blender's pass and channel names hold no dot."""
    pieces = name.rsplit(".", 2)
    if len(pieces) == 1:
        return "", "", pieces[0]
    return ("", pieces[0], pieces[1]) if len(pieces) == 2 else tuple(pieces)


def catalogue(path):
    """``{(layer, pass): (part index, {channel: full name})}`` of a multilayer OpenEXR; RuntimeError for views."""
    with open(path, "rb") as handle:
        parts = read_parts(handle)[0]
    found = {}
    for part in parts:
        if part.view:
            raise RuntimeError("The render holds stereo views; a pass of one view is not shown")
        for name, *_rest in part.channels:
            layer, render_pass, channel = split_channel(name)
            entry = found.setdefault((layer, render_pass or "Combined"), (part.index, {}))
            if entry[0] != part.index:
                raise RuntimeError("Pass %s.%s is split across parts of the file" % (layer[:64], render_pass[:64]))
            entry[1][channel] = name
    return found


def _listed(found):
    names = ["%s.%s" % pair if pair[0] else pair[1] for pair in sorted(found)]
    return ", ".join(names[:LISTED]) + (" and %d more" % (len(names) - LISTED) if len(names) > LISTED else "")


def choose(found, item, active_layer):
    """The ``(layer, pass)`` that ``item`` names (RuntimeError naming what the file holds otherwise)."""
    if "." in item:
        layer, render_pass = item.rsplit(".", 1)
        if (layer, render_pass) in found:
            return layer, render_pass
    having = [pair for pair in found if pair[1] == item]
    if len(having) == 1:
        return having[0]
    if (active_layer, item) in having:
        return active_layer, item
    if having:
        raise RuntimeError("Several view layers have %s: name one as <layer>.%s (%s)"
                           % (item[:64], item[:64], _listed({pair: None for pair in having})))
    raise RuntimeError("The render has no pass %s. It holds: %s" % (item[:64], _listed(found)))


def _hash_colours(ids):
    """A distinct colour per ID (bits of its float or integer value); 0 is black."""
    import numpy
    bits = numpy.ascontiguousarray(ids, dtype=numpy.float32).view(numpy.uint32)
    mixed = (bits * numpy.uint32(2654435761)) ^ (bits >> numpy.uint32(15))
    colours = numpy.stack([(mixed >> shift) & 255 for shift in (0, 8, 16)], axis=-1).astype(numpy.float32) / 255.0
    colours = 0.25 + 0.75 * colours
    colours[ids == 0] = 0.0
    return colours


def data_rows(render_pass, channels):
    """A data pass as RGB floats 0 to 1 (top row first) and what the result says of the mapping."""
    import numpy
    names = sorted(channels)
    planes = [channels[name] for name in names]
    upper = {name.upper(): plane for name, plane in channels.items()}  # Blender names Cryptomatte's channels rgba.
    if render_pass.startswith("Crypto") and set(upper) >= {"R", "G", "B", "A"}:
        colours = (_hash_colours(upper["R"]) * numpy.clip(upper["G"], 0, 1)[..., None]
                   + _hash_colours(upper["B"]) * numpy.clip(upper["A"], 0, 1)[..., None])
        ids = numpy.unique(upper["R"][upper["G"] > 0])
        facts = {"mapping": "ID_COLOURS", "ids": int(len(ids))}
        return colours, facts, "each object or material ID a colour of its own"
    if render_pass in ("IndexOB", "IndexMA") or (len(planes) == 1 and render_pass.startswith("Index")):
        values = numpy.rint(planes[0])
        return (_hash_colours(values), {"mapping": "ID_COLOURS", "ids": int(len(numpy.unique(values[values > 0])))},
                "each index a colour of its own, 0 black")
    if render_pass in ("Depth", "Mist", "Denoising Depth") or len(planes) == 1:
        values = planes[0]
        hit = numpy.isfinite(values) & (values < FAR)
        if render_pass == "Mist":
            grey = numpy.clip(1.0 - values, 0.0, 1.0)
            facts, how = {"mapping": "INVERTED"}, "1 - mist: near white, far black"
        elif hit.any():
            near, far = float(values[hit].min()), float(values[hit].max())
            ascending = (values - near) / max(far - near, 1e-12)
            facts = {"mapping": "RANGE", "range": [round(near, 4), round(far, 4)]}
            if render_pass in ("Depth", "Denoising Depth"):
                grey = numpy.where(hit, 1.0 - ascending, 0.0)
                how = "near white to far black between %.4g and %.4g, no hit black" % (near, far)
            else:  # Any other single value (a mask, a value AOV): lowest black, highest white.
                grey = numpy.where(hit, ascending, 0.0)
                how = "lowest black to highest white between %.4g and %.4g" % (near, far)
        else:
            grey, facts, how = numpy.zeros_like(values), {"mapping": "RANGE", "range": None}, "nothing was hit"
        return numpy.repeat(grey[..., None], 3, axis=2), facts, how
    rgb = numpy.stack((planes + [numpy.zeros_like(planes[0])] * 3)[:3], axis=-1)
    if render_pass in ("Normal", "Denoising Normal"):
        return (rgb + 1.0) * 0.5, {"mapping": "HALF_PLUS_HALF"}, "(n + 1) / 2 per axis"
    if render_pass == "Position":
        finite = numpy.isfinite(rgb).all(axis=-1)
        low = numpy.where(finite[..., None], rgb, numpy.inf).reshape(-1, 3).min(axis=0)
        high = numpy.where(finite[..., None], rgb, -numpy.inf).reshape(-1, 3).max(axis=0)
        scaled = (rgb - low) / numpy.maximum(high - low, 1e-12)
        bounds = [[round(float(a), 4), round(float(b), 4)] for a, b in zip(low, high)] if finite.any() else None
        return scaled, {"mapping": "RANGE_PER_AXIS", "range": bounds}, "each axis between its lowest and highest value"
    clipped = float(((rgb < 0) | (rgb > 1)).any(axis=-1).mean())
    return rgb, {"mapping": "STORED", "clipped": round(clipped, 4)}, "its stored values from 0 to 1"


def pass_source(scene, path, references, label, what, facts, item, active_layer, then=None):
    """The ``view_finished.Source`` of one pass of the multilayer OpenEXR at ``path`` (a private copy)."""
    import numpy
    from .view_finished import THEN, Source, linear_file
    then = THEN if then is None else then
    from .view_picture import colour
    try:
        found = catalogue(path)
        layer, render_pass = choose(found, item, active_layer)
        part, channels = found[(layer, render_pass)]
        values = read_channels(path, part, list(channels.values()))
    except ExrError as error:
        raise RuntimeError("%s could not be read: %s" % (label, error)) from None
    planes = {channel: values[name] for channel, name in channels.items()}
    facts.update(layer=layer, render_pass=render_pass, channels="".join(sorted(planes)),
                 passes=len(found), size=list(next(iter(planes.values())).shape[::-1]))
    of_layer = " of view layer " + layer if layer else ""
    label, what = label + " " + render_pass, "%s, pass %s%s" % (what, render_pass, of_layer)
    if set(planes) >= {"R", "G", "B"} and not render_pass.startswith("Crypto"):
        rgba = numpy.stack([planes["R"], planes["G"], planes["B"], planes.get("A", numpy.ones_like(planes["R"]))], -1)
        linear = references.new_observation(label + " · linear")
        try:
            write_rgba(linear["path"], rgba)
            copy, unrestored, exact, more = linear_file(scene, linear["path"], references, label)
        finally:
            references.discard_observation(linear)
        facts.update(more, values="DISPLAY", color=colour(scene))
        return Source(copy["path"], label, what, facts, copy, unrestored, exact, then=then,
                      note=" A colour pass, shown through the scene's view transform as the Image Editor shows it.")
    rgb, mapping, how = data_rows(render_pass, planes)
    facts.update(mapping, values="STATED")
    rows = numpy.empty(rgb.shape[:2] + (4,), dtype=numpy.uint8)
    rows[..., :3], rows[..., 3] = to_bytes(rgb), 255
    reference = references.new_observation(label + " · data")
    try:
        with open(reference["path"], "wb") as handle:
            handle.write(encode_rgba_png(rows))
    except Exception:
        references.discard_observation(reference)
        raise
    return Source(reference["path"], label, what, facts, reference, then=then,
                  note=" A data pass, shown as %s; no view transform." % how)


def render_result_source(window, checked, references, controller):
    """``RENDER_RESULT:<pass>``: Blender's Render Result saved once as a private multilayer OpenEXR."""
    import bpy
    from . import view_save
    from .view_finished import _busy
    scene = window.scene
    result = next((image for image in bpy.data.images if image.type == "RENDER_RESULT"), None)
    if result is None:
        raise RuntimeError("Blender's Render Result holds no render yet")
    if _busy():
        raise RuntimeError("A render is running; the Render Result's passes can be shown once it ends")
    if scene.render.use_multiview:
        raise RuntimeError("The scene renders stereo views; a pass of one view is not shown")
    space_unset = getattr(getattr(scene.render.image_settings, "linear_colorspace_settings", None), "name", None) == ""
    saved = references.new_observation("Render Result · passes")
    try:
        unrestored = view_save.multilayer_copy(scene, result, saved["path"])  # Never writes the scene.
        slots = result.render_slots
        facts = {"image": "RENDER_RESULT", "slot": slots[slots.active_index].name if len(slots) else ""}
        source = pass_source(scene, saved["path"], references, "Render Result", "Blender's current Render Result",
                             facts, checked["item"], window.view_layer.name)
    except RuntimeError as error:
        if "does not have any image data" in str(error):
            raise RuntimeError("Blender's Render Result holds no render yet") from None
        raise
    finally:
        references.discard_observation(saved)
    if space_unset and UNSET_SPACE in unrestored:
        # Only on a build without view_save's helper: a format change names an unset output colour space
        # (BKE_image_format_update_color_space_for_type) and Python cannot unset it again; the default it names
        # writes the same pixels, so say so precisely.
        unrestored = [path for path in unrestored if path != UNSET_SPACE]
        source.side_effects.append({"code": "output_color_space_named", "detail": (
            "The scene's output colour space was unset; Blender named its default (%s) when the format changed for "
            "the save, which writes the same files" % scene.render.image_settings.linear_colorspace_settings.name)})
    source.unrestored = list(unrestored) + source.unrestored
    return source


def file_pass_source(window, path, references, label, what, facts, item):
    """One pass of a job's published multilayer OpenEXR, read from one bounded private copy."""
    from .job_store import copy_output
    from .view_exr import MAGIC
    from .view_finished import FILE_THEN
    from .view_picture import MAX_BYTES
    private = references.new_observation(label + " · source copy")
    try:
        try:
            with open(private["path"], "wb") as destination:
                copy_output(path, destination, MAX_BYTES)
        except (OSError, ValueError) as error:
            raise RuntimeError("%s could not be read safely: its file changed, is linked, or exceeds %d MB"
                               % (label, MAX_BYTES // (1024 * 1024))) from error
        with open(private["path"], "rb") as handle:
            if handle.read(4) != MAGIC:
                raise RuntimeError("%s is one picture, not a multilayer OpenEXR: it has no passes; leave the pass "
                                   "out" % label)
        facts["source"] = "CURRENT_OUTPUT_FILE"
        return pass_source(window.scene, private["path"], references, label, what, facts, item,
                           window.view_layer.name, then=FILE_THEN)
    finally:
        references.discard_observation(private)
