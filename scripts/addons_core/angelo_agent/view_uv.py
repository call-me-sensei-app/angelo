# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with ``image: "UV"`` or ``"UV:<image>"``: one mesh's UV layout over its texture.

The picture is the 0 to 1 UV square: the texture (the image named after the
colon, else the active material's active Image Texture node, else its first
one) or a grey checkerboard, with the active UV map's polygon edges drawn as
one-pixel orange lines with a dark shadow (doc/angelo/view_sources.rst, "UV
layouts"). A texture not in memory is never loaded: the layout is drawn over
the checkerboard and the result says so.

Object Mode reads the mesh arrays with ``foreach_get``; Edit Mode reads the
live edit mesh through ``bmesh.from_edit_mesh`` (the mesh's own arrays are
stale until Edit Mode ends). Both only read. The raster is pure NumPy, bounded
by ``MAX_SAMPLES`` line samples. Main thread; model-facing text is English.
"""

from .view_framing import output_size
from .view_pixels import box_downscale, box_factor, checkerboard, encode_rgba_png

MAX_LOOPS = 4_000_000        # Face corners read in Object Mode.
MAX_EDIT_FACES = 200_000     # Faces read one by one in Edit Mode.
MAX_SIDE = 4096              # A region raster's longer side.
MAX_SAMPLES = 16_000_000     # Line pixels drawn in all; denser layouts draw a uniform subset of edges.
CHUNK_SAMPLES = 2_000_000    # Line pixels drawn at a time (bounded temporary arrays).
LINE, SHADOW = (255, 140, 0), (0, 0, 0)


def mesh_segments(obj):
    """The active UV map's polygon edges as float32 rows ``[u0, v0, u1, v1]``: ``(segments, map, faces, edit)``."""
    import numpy
    mesh = obj.data
    if mesh.is_editmode:
        import bmesh
        edit = bmesh.from_edit_mesh(mesh)
        layer = edit.loops.layers.uv.active
        if layer is None:
            raise RuntimeError("Mesh '%s' has no UV map" % obj.name[:64])
        if len(edit.faces) > MAX_EDIT_FACES:
            raise RuntimeError("'%s' has %d faces in Edit Mode, more than the %d a UV view reads there; leave Edit "
                               "Mode to view it" % (obj.name[:64], len(edit.faces), MAX_EDIT_FACES))
        rows = []
        for face in edit.faces:
            corners = [tuple(loop[layer].uv) for loop in face.loops]
            rows.extend(a + b for a, b in zip(corners, corners[1:] + corners[:1]))
        segments = numpy.array(rows, dtype=numpy.float32).reshape(-1, 4)
        return segments, layer.name, len(edit.faces), True
    layer = mesh.uv_layers.active
    if layer is None:
        raise RuntimeError("Mesh '%s' has no UV map" % obj.name[:64])
    loops, faces = len(mesh.loops), len(mesh.polygons)
    if loops > MAX_LOOPS:
        raise RuntimeError("'%s' has %d face corners, more than the %d a UV view reads"
                           % (obj.name[:64], loops, MAX_LOOPS))
    uv = numpy.empty(loops * 2, dtype=numpy.float32)
    layer.uv.foreach_get("vector", uv)
    uv = uv.reshape(loops, 2)
    starts, totals = numpy.empty(faces, dtype=numpy.int32), numpy.empty(faces, dtype=numpy.int32)
    mesh.polygons.foreach_get("loop_start", starts)
    mesh.polygons.foreach_get("loop_total", totals)
    following = numpy.arange(1, loops + 1, dtype=numpy.int64)
    if faces:
        following[starts + totals - 1] = starts  # Each face's last corner closes back to its first.
    if not loops:
        return numpy.zeros((0, 4), numpy.float32), layer.name, faces, False
    segments = numpy.concatenate([uv, uv[following % loops]], axis=1)
    return segments, layer.name, faces, False


def pick_texture(obj, name):
    """``(image or None, how it was chosen)``: the named image, else the active material's image texture."""
    if name is not None:
        from .view_image_data import find_image
        return find_image(name), "named"
    material = obj.active_material
    tree = getattr(material, "node_tree", None) if material is not None else None
    if tree is None:
        return None, "none"
    active = tree.nodes.active
    if active is not None and active.bl_idname == "ShaderNodeTexImage" and active.image is not None:
        return active.image, "active image node"
    first = next((node for node in tree.nodes
                  if node.bl_idname == "ShaderNodeTexImage" and node.image is not None), None)
    if first is not None:
        return first.image, "first image node"
    return None, "none"


def raster_size(resolution, aspect, region):
    """The layout's raster: longer side ``resolution``, or larger so a region's part has that many pixels."""
    width, height = output_size(resolution, aspect)
    if region is None:
        return width, height
    scale = resolution / max((region[2] - region[0]) * width, (region[3] - region[1]) * height)
    scale = min(scale, MAX_SIDE / max(width, height))
    return max(1, round(width * scale)), max(1, round(height * scale))


def _resample(rows, width, height):
    """Top-first RGBA ``rows`` at ``width`` x ``height``: a box filter by a whole factor, then nearest pixels."""
    import numpy
    factor = box_factor(rows.shape[1], rows.shape[0], max(width, height))
    if factor > 1:
        rows = (box_downscale(rows.astype(numpy.float32), factor) + 0.5).astype(numpy.uint8)
    ys = numpy.minimum((numpy.arange(height) + 0.5) * rows.shape[0] / height, rows.shape[0] - 1).astype(numpy.int64)
    xs = numpy.minimum((numpy.arange(width) + 0.5) * rows.shape[1] / width, rows.shape[1] - 1).astype(numpy.int64)
    return rows[ys][:, xs]


def _plot(rows, xs, ys, colour):
    height, width = rows.shape[:2]
    inside = (xs >= 0) & (xs < width) & (ys >= 0) & (ys < height)
    rows[ys[inside], xs[inside], :3] = colour


def draw_segments(rows, segments):
    """Draw UV ``segments`` on top-first ``rows`` (UV 0 to 1 fills them); returns the number of edges drawn."""
    import numpy
    height, width = rows.shape[:2]
    if not len(segments):
        return 0
    points = numpy.nan_to_num(segments, nan=-1.0, posinf=-1.0, neginf=-1.0).astype(numpy.float64)
    x0, y0 = points[:, 0] * width, (1.0 - points[:, 1]) * height
    x1, y1 = points[:, 2] * width, (1.0 - points[:, 3]) * height
    longest = numpy.minimum(numpy.maximum(abs(x1 - x0), abs(y1 - y0)), 2.0 * (width + height))
    steps = numpy.ceil(longest).astype(numpy.int64) + 1
    keep = numpy.arange(len(steps))
    if int(steps.sum()) > MAX_SAMPLES:  # A uniform subset of edges, so the layout's shape is still seen everywhere.
        stride = int(numpy.ceil(steps.sum() / MAX_SAMPLES))
        keep = keep[::stride]
    start = 0
    while start < len(keep):
        end = start + max(1, int(numpy.searchsorted(numpy.cumsum(steps[keep[start:]]), CHUNK_SAMPLES)))
        chosen = keep[start:end]
        counts = steps[chosen]
        owner = numpy.repeat(numpy.arange(len(chosen)), counts)
        offsets = numpy.arange(int(counts.sum())) - numpy.repeat(numpy.cumsum(counts) - counts, counts)
        t = offsets / numpy.maximum(counts - 1, 1)[owner]
        edge = chosen[owner]
        xs = numpy.floor(x0[edge] + (x1[edge] - x0[edge]) * t).astype(numpy.int64)
        ys = numpy.floor(y0[edge] + (y1[edge] - y0[edge]) * t).astype(numpy.int64)
        _plot(rows, xs + 1, ys, SHADOW)
        _plot(rows, xs, ys + 1, SHADOW)
        _plot(rows, xs, ys, LINE)
        start = end
    return len(keep)


def source(window, checked, references, controller):
    """The ``view_finished.Source`` of ``UV``: a private RGBA PNG of the layout over its texture."""
    import numpy
    from . import scene_queries
    from .view_finished import Source
    from .view_image_data import cached_metadata, display_rows, images_busy
    from .view_picture import flatten
    handle = checked["targets"][0]
    obj = scene_queries.resolve_object(window, handle)
    if obj.type != "MESH":
        raise RuntimeError("UV shows a mesh's UV layout; '%s' is a %s" % (obj.name[:64], obj.type))
    segments, uv_map, faces, edit = mesh_segments(obj)
    image, chosen = pick_texture(obj, checked.get("item"))
    texture, uncertainty = None, []
    if image is not None and images_busy():
        uncertainty.append({"code": "uv_texture_busy", "detail": (
            "A render, bake or job may be writing images now, so image '%s' was not read; the layout is drawn over a "
            "checkerboard" % image.name[:64])})
    elif image is not None:
        metadata = cached_metadata(image)
        if metadata is None:
            uncertainty.append({"code": "uv_texture_not_loaded", "detail": (
                "Image '%s' is not in memory and was not loaded; the layout is drawn over a checkerboard"
                % image.name[:64])})
        else:
            texture = display_rows(image, metadata, window.scene)[0]
    aspect = texture.shape[1] / texture.shape[0] if texture is not None else 1.0
    width, height = raster_size(checked["resolution"], aspect, checked.get("region"))
    if texture is not None:  # Transparent texels over the checkerboard, as the UV Editor shows them.
        rows = flatten(numpy.ascontiguousarray(_resample(texture, width, height)))[0]
    else:
        rows = checkerboard(height, width)
    drawn = draw_segments(rows, segments)
    corners = segments[:, (0, 1)]  # Each face corner starts exactly one edge.
    outside = float(((corners < 0) | (corners > 1)).any(axis=1).mean()) if len(segments) else 0.0
    facts = {"object": handle, "uv_map": uv_map[:64], "faces": faces, "edges": len(segments), "edges_drawn": drawn,
             "uv_outside": round(outside, 4), "texture": image.name[:128] if image is not None else None,
             "texture_shown": texture is not None, "texture_choice": chosen, "edit_mode": edit}
    if drawn < len(segments):
        uncertainty.append({"code": "uv_edges_subsampled", "detail": "%d of %d edges are drawn, a uniform subset"
                            % (drawn, len(segments))})
    label = "UV %s" % obj.name[:64]
    reference = references.new_observation(label + " · layout")
    try:
        with open(reference["path"], "wb") as handle_file:
            handle_file.write(encode_rgba_png(rows))
    except Exception:
        references.discard_observation(reference)
        raise
    over = ("image '%s'" % image.name[:64]) if texture is not None else "a grey checkerboard"
    note = (" UV space 0 to 1 fills the picture: the edges are orange lines over %s%s." % (
        over, "" if not outside else "; %d%% of UV corners lie outside it and are not shown" % round(outside * 100)))
    return Source(reference["path"], label, "UV map '%s' of '%s'" % (uv_map[:64], obj.name[:64]), facts, reference,
                  then=" It shows the layout now%s." % (", read from Edit Mode" if edit else ""), note=note,
                  framing="UV_LAYOUT", uncertainty=uncertainty)
