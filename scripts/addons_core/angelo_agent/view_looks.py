# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The viewport looks ``angelo_view`` draws: the display settings each one sets for a draw and puts back.

``view_viewport.DisplayOverrides`` writes these to the drawing viewport only
for the call and restores them in ``finally`` (``view_motion`` does the same
for a strip). Every look is Blender's own Workbench or EEVEE draw with fixed
settings, so a look reads the same in every scene and every artist viewport:

* SOLID, ATTRIBUTE, WIREFRAME: Workbench as before.
* MATCAP: Workbench lit by a clay matcap in one grey, no shadows or cavity:
  the form alone, as sculptors judge it (Blender's sculpt look).
* CURVATURE: Workbench with flat light, one grey and Blender's cavity (world
  and screen space, ridges lighter, valleys darker): where the surface bends.
* MATERIAL, LIT: EEVEE as before.
* UV_CHECKER: Workbench in one flat grey with the viewport UV checker (doc/angelo/uv_checker.rst) on each mesh's
  active UV map. Every other look turns the checker off for its draw, so a person's own checker never reaches them.
"""

BACKGROUND = (0.05, 0.05, 0.055)
GREY = (0.8, 0.8, 0.8)
MATCAP = "clay_studio.exr"   # Blender 5.2's clay matcap (datafiles/studiolights/matcap).
_PLAIN = (("background_type", "VIEWPORT"), ("background_color", BACKGROUND), ("show_xray", False),
          ("show_backface_culling", False), ("show_object_outline", False), ("show_specular_highlight", False),
          ("use_dof", False))
# Ordered: ``type`` first, so values that depend on it (``studio_light`` reads
# and writes a different stored field per type and light) are captured and restored
# while that type is active.
SHADING = {
    "SOLID": (("type", "SOLID"), ("light", "STUDIO"), ("studio_light", "Default"),
              ("color_type", "MATERIAL"), ("show_shadows", True), ("show_cavity", True),
              ("background_type", "VIEWPORT"), ("background_color", BACKGROUND), ("show_xray", False),
              ("show_backface_culling", False), ("show_object_outline", False),
              ("show_specular_highlight", False), ("use_dof", False)),
    # SOLID with each mesh's active colour attribute (Workbench "Attribute" colours).
    "ATTRIBUTE": (("type", "SOLID"), ("light", "STUDIO"), ("studio_light", "Default"), ("color_type", "VERTEX"),
                  ("show_shadows", False), ("show_cavity", False)) + _PLAIN,
    "UV_CHECKER": (("type", "SOLID"), ("light", "STUDIO"), ("studio_light", "Default"), ("color_type", "SINGLE"),
                   ("single_color", GREY), ("show_shadows", False), ("show_cavity", False)) + _PLAIN,
    "MATCAP": (("type", "SOLID"), ("light", "MATCAP"), ("studio_light", MATCAP), ("color_type", "SINGLE"),
               ("single_color", GREY), ("show_shadows", False), ("show_cavity", False)) + _PLAIN,
    "CURVATURE": (("type", "SOLID"), ("light", "FLAT"), ("color_type", "SINGLE"), ("single_color", GREY),
                  ("show_shadows", False), ("show_cavity", True), ("cavity_type", "BOTH"),
                  ("cavity_ridge_factor", 1.0), ("cavity_valley_factor", 1.0),
                  ("curvature_ridge_factor", 2.0), ("curvature_valley_factor", 2.0)) + _PLAIN,
    "WIREFRAME": (("type", "WIREFRAME"), ("wireframe_color_type", "OBJECT"), ("show_xray_wireframe", False),
                  ("background_type", "VIEWPORT"), ("background_color", BACKGROUND)),
    "MATERIAL": (("type", "MATERIAL"), ("use_scene_lights", False), ("use_scene_world", False),
                 ("render_pass", "COMBINED"), ("use_compositor", "DISABLED"), ("studio_light", "forest.exr"),
                 ("studiolight_rotate_z", 0.0), ("studiolight_intensity", 1.0),
                 ("studiolight_background_alpha", 0.0), ("studiolight_background_blur", 0.5)),
    # The same EEVEE draw lit by the scene itself: its lights, and its world as light and background.
    "LIT": (("type", "MATERIAL"), ("use_scene_lights", True), ("use_scene_world", True),
            ("render_pass", "COMBINED"), ("use_compositor", "DISABLED")),
}
EEVEE_SHADINGS = frozenset({"MATERIAL", "LIT"})
# ``SpaceView3D.uv_checker`` (View3DUVChecker) for each look: on only in UV_CHECKER, in fixed settings; off in the rest.
UV_CHECKER = {"UV_CHECKER": (("show", True), ("pattern", "COLOR_GRID"), ("uv_map", "ACTIVE"), ("scale", 1.0),
                             ("opacity", 1.0), ("use_selected_only", False), ("show_flipped", True))}
UV_CHECKER_OFF = (("show", False),)
OVERLAY = {"WIREFRAME": (("show_overlays", False), ("wireframe_threshold", 1.0), ("wireframe_opacity", 1.0))}
# Studio-light names come from installed files; if one is missing, keep the
# artist's light and say so rather than failing the observation.
BEST_EFFORT = frozenset({"studio_light"})


# Look -> its display_keys: asked for before every capture and strip. Cached because SHADING, OVERLAY and UV_CHECKER
# are constants; a look whose writes came to depend on the scene or machine must not be cached here.
_display_keys = {}


def display_keys(shading):
    """``frozenset{(struct, property)}`` a viewport look in ``shading`` may write and put back
    (``DisplayOverrides``): what those writes publish to ``bpy.msgbus`` (render_extensions). Worked out once per
    look: the policy check before each capture asks for it (test_native_view_policy_work)."""
    keys = _display_keys.get(shading)
    if keys is None:
        keys = _display_keys[shading] = frozenset(
            {("View3DShading", name) for name, _value in SHADING[shading]}
            | {("View3DOverlay", name) for name, _value in OVERLAY.get(shading, (("show_overlays", False),))}
            | {("View3DUVChecker", name) for name, _value in UV_CHECKER.get(shading, UV_CHECKER_OFF)}
            | {("RegionView3D", "use_clip_planes")})
    return keys


_drawn_with = {}   # Look -> drawn_with: also asked for before every capture (view_policy.display_dirties).


def drawn_with(shading):
    """``(shading type, overlays shown)`` a viewport look in ``shading`` draws with; worked out once per look."""
    found = _drawn_with.get(shading)
    if found is None:
        found = _drawn_with[shading] = (dict(SHADING[shading])["type"],
                                        dict(OVERLAY.get(shading, (("show_overlays", False),)))["show_overlays"])
    return found
