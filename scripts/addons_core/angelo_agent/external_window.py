# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An app's picture of Angelo's window (``angelo_window``): its layout, with no pixel of the interface or the scene.

A screenshot of the interface can carry anything drawn there: file paths in Properties, the Outliner or an add-on's
sidebar, a popup, a tooltip, a card over the viewport. Masking a list of editors cannot promise what is left, so an
app is sent no interface pixels at all. The picture is window-sized and flat grey with each editor outlined; the
layout comes back as data: each editor's type and box, and why it is not drawn (``layout``).

The 3D viewports are grey too in 2.0.1. Drawing a viewport again offscreen is an image render: in Material Preview
EEVEE compiles its static, default, world, subsurface and volume shaders and its lighting specializations on the main
thread, and Workbench renders its anti-aliasing samples in one call, with no bound Angelo can hold to. An app gets the
scene's pixels the way Angelo's own agent does, bounded and prepared in the background (``view_warm``):
``angelo_viewport`` (the person's viewport direction) and ``angelo_look`` (any angle). Viewports drawn into the
window picture are planned for 2.1.0.

Read-only, cheap and bounded: no draw, no data-block, Undo step or setting; a grey image of the window's size (scaled
to the encoder's limit) and its layout. Main thread, window required. doc/angelo/external_mcp.rst, "Looks".
"""

from . import live_windows

GREY, OUTLINE = 72, 48   # A masked editor's flat grey and its outline, as view_editor masks.
VIEWPORT = ("a 3D viewport: its scene comes from angelo_viewport (the person's viewport direction) or angelo_look "
            "(any angle); the window picture draws no viewport in Angelo 2.0.1")
NOT_A_VIEWPORT = "not a 3D viewport: its interface is never sent"


def _kind(area):
    return getattr(area, "ui_type", "") or area.type


def layout(window):
    """``(size, editors)``: the window's extent in its own pixels and each editor's ``type``, ``box`` [x, y, width,
    height] (from the window's bottom left) and ``why`` it is not drawn. Raises RuntimeError without editors (a window
    between screens)."""
    areas = live_windows.areas(window)
    if not areas:
        raise RuntimeError("The window is changing screens; ask again shortly")
    size = [max(area.x + area.width for area in areas), max(area.y + area.height for area in areas)]
    editors = [{"type": _kind(area), "box": [area.x, area.y, area.width, area.height], "shown": False,
                "why": VIEWPORT if area.type == "VIEW_3D" else NOT_A_VIEWPORT} for area in areas]
    return size, editors


def compose(size, editors):
    """The window picture (RGBA, bottom row first): flat grey with each editor outlined."""
    import numpy
    width, height = size
    rows = numpy.full((height, width, 4), GREY, dtype=numpy.uint8)
    rows[..., 3] = 255
    for entry in editors:
        x, y, w, h = entry["box"]
        left, bottom = max(0, x), max(0, y)
        right, top = min(width, x + w), min(height, y + h)
        if right <= left or top <= bottom:
            continue
        rows[bottom, left:right, :3] = OUTLINE
        rows[top - 1, left:right, :3] = OUTLINE
        rows[bottom:top, left, :3] = OUTLINE
        rows[bottom:top, right - 1, :3] = OUTLINE
    return rows


def capture(window, references):
    """The window picture into a private reference of the conversation: ``(view id, layout)``."""
    import os
    from . import owned_paths
    from .view_png import MAX_DIMENSION, encode_png
    size, editors = layout(window)
    factor = -(-max(size) // MAX_DIMENSION)   # Whole steps down to what the encoder takes, before drawing anything.
    scaled = [max(1, size[0] // factor), max(1, size[1] // factor)]
    scaled_editors = [dict(entry, box=[value // factor for value in entry["box"]]) for entry in editors]
    rows = compose(scaled, scaled_editors)
    data = encode_png(rows.tobytes(), rows.shape[1], rows.shape[0], bottom_up=True)
    reference = references.new_observation("Window layout")
    try:
        owned_paths.require_writable(reference["path"])
        with open(reference["path"], "wb") as handle:
            handle.write(data)
        os.chmod(reference["path"], 0o600)
    except Exception:
        references.discard_observation(reference)
        raise
    return reference["id"], {"size": size, "editors": editors}
