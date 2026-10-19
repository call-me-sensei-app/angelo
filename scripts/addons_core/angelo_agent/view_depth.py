# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``angelo_view`` with DEPTH shading: how far each pixel of the targets is from the camera, as grey.

Shading shows form by light, which flattens depth seen head on: from the front
a nose's projection, a brow's overhang or a cheek's recess read only as
shadow. DEPTH draws the targets alone (``view_silhouette``: meshes and
mesh-like objects as triangles, hair as closed strands) at the view's own
camera and maps each pixel's distance from the camera onto grey: the nearest
point white, the farthest a dark grey, nothing drawn black. The record states
the distances that white and that grey stand for (``depth.near_world`` and
``far_world``, world units along the view), so a grey level reads as a
distance. Like WEIGHT it never changes the scene or the viewport, and every
call is drawn (never cached).
"""

import concurrent.futures
import time

from .view_png import encode_png

FARTHEST = 48     # The grey level of the farthest drawn point; nothing drawn is 0.
NOTE = ("Depth drawn by the host: only the targets, white at the nearest point to this camera, dark grey at the "
        "farthest (depth.near_world and far_world, world units along the view), black where nothing is drawn. "
        "Not a render, and not authority for edits.")


def distances(depth, camera):
    """Distance along the view (world units) of every pixel at window ``depth`` (rows top first); NaN where empty."""
    import numpy
    height, width = depth.shape
    inverse = numpy.linalg.inv(numpy.array(camera["projection_matrix"], dtype=numpy.float64))
    rows, columns = numpy.nonzero(depth < 1.0)
    ndc = numpy.column_stack(((columns + 0.5) / width * 2.0 - 1.0, 1.0 - (rows + 0.5) / height * 2.0,
                              depth[rows, columns].astype(numpy.float64) * 2.0 - 1.0, numpy.ones(len(rows))))
    view = ndc @ inverse.T
    found = numpy.full(depth.shape, numpy.nan)
    found[rows, columns] = -view[:, 2] / view[:, 3]    # A camera looks along its -Z.
    return found


def picture(depth, camera):
    """``(RGBA bytes bottom row first, facts)`` of one camera's depth."""
    import numpy
    along = distances(depth, camera)
    drawn = ~numpy.isnan(along)
    grey = numpy.zeros(depth.shape, dtype=numpy.uint8)
    facts = {"drawn_share": round(float(drawn.mean()), 4)}
    if drawn.any():
        near, far = float(numpy.nanmin(along)), float(numpy.nanmax(along))
        span = max(far - near, 1e-12)
        grey[drawn] = numpy.round(255.0 - (along[drawn] - near) / span * (255.0 - FARTHEST)).astype(numpy.uint8)
        facts.update(near_world=round(near, 6), far_world=round(far, 6))
    rgba = numpy.empty(depth.shape + (4,), dtype=numpy.uint8)
    rgba[..., :3] = grey[..., None]
    rgba[..., 3] = 255
    return rgba[::-1].tobytes(), facts


def _encoded(plan, depth):
    """Worker thread: the plan's depth picture, its facts added to ``plan["depth"]``, encoded (a pair's maker too)."""
    rgba, found = picture(depth, plan["camera"])
    plan["depth"].update(found)
    return plan.get("encode", encode_png)(rgba, plan["width"], plan["height"])


def capture(window, plans, objects, checked):
    """``view_capture``'s capture record for DEPTH: every plan drawn, its depth facts on the plan."""
    from . import view_silhouette
    started = time.perf_counter()
    shapes, totals = view_silhouette.geometry(window, objects)
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plans) or 1, thread_name_prefix="AngeloViewPNG") as pool:
        futures = {}
        for index, plan in enumerate(plans):
            _mask, depth, plan["depth"] = view_silhouette.draw(shapes, plan["camera"], plan["width"], plan["height"])
            plan["depth"].pop("strands", None)
            futures[index] = pool.submit(_encoded, plan, depth)
        drawn = time.perf_counter()
        encoded = {index: future.result() for index, future in futures.items()}
    uncertainty = []
    if totals["not_drawn"]:
        uncertainty.append({"code": "depth_targets_not_drawn", "detail": (
            "%d targets have no surface or curves to draw" % totals["not_drawn"])})
    return {"encoded": encoded, "unavailable": [], "compiling": False,
            "draw_wall_ms": (drawn - started) * 1000, "encode_wait_wall_ms": (time.perf_counter() - drawn) * 1000,
            "uncertainty": uncertainty}
