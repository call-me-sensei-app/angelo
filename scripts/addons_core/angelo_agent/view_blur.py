# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Motion blur in ``angelo_view`` pictures: never drawn into a viewport look, stated for a render.

EEVEE blurs an image render of the viewport (``GPUOffScreen.draw_view3d``) by
the motion between that draw and the previous draw on the same offscreen
whenever the scene renders with motion blur and the view layer allows it
(``eevee_motion_blur.cc``: on for image renders, post-process only in a
viewport; the velocity's previous step is the offscreen's last draw). A view
call drew all its views, and a strip all its frames, on one offscreen per
size, so every view after the first was smeared toward the previous view's
camera or frame. Recorded one-shot bridge trials with an animated car (Sol 3
of 8, Astra 2 of 4) called those views ghosted or corrupted and the first one
clean. A new offscreen has no earlier draw, so its velocity is zero: while blur
is on, each MATERIAL or LIT draw gets its own. Workbench looks never blur, and
nothing here changes the person's settings.

A RENDERED look is the scene as F12 renders it, blur included: its result says
so (``rendered_blur``), with the shutter, so a blurred moving object is not
mistaken for a modelling fault.
"""

# The viewport looks EEVEE draws (view_capture.SHADING); the others are Workbench or the weight shader.
EEVEE_LOOKS = frozenset({"MATERIAL", "LIT"})
# Engines whose F12 render applies the scene's motion blur (Workbench has none).
BLUR_ENGINES = frozenset({"BLENDER_EEVEE", "CYCLES"})
NOT_DRAWN = "NOT_DRAWN"  # A viewport look's fact when the scene renders with blur: this picture shows none.


def scene_blurs(scene, view_layer):
    """Whether the scene's render settings and this view layer enable motion blur."""
    return bool(getattr(scene.render, "use_motion_blur", False)) and bool(getattr(view_layer, "use_motion_blur", True))


def own_offscreen_per_draw(scene, view_layer, shading):
    """Whether each draw needs a fresh offscreen so EEVEE has no earlier draw to blur toward."""
    return shading in EEVEE_LOOKS and scene_blurs(scene, view_layer)


def rendered_blur(scene, view_layer, engine):
    """The blur a RENDERED look of this scene contains, or None: shutter (in frames) and its position."""
    if engine not in BLUR_ENGINES or not scene_blurs(scene, view_layer):
        return None
    render = scene.render
    return {"shutter": round(float(getattr(render, "motion_blur_shutter", 0.5)), 4),
            "position": str(getattr(render, "motion_blur_position", "CENTER"))}
