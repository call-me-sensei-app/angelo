# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""``property_set`` values (pure Python, no bpy): generic RNA paths plus the common-path list.

``property_set`` writes settings on one staged owner: an object (its own
settings or, through ``data.`` and ``active_material.``, its data and active
material) or the bound scene (and, through ``world.``, its World). Any path
the generic rules in ``rna_rules`` admit is accepted; the kernel checks every
value against the installed RNA definition and reads it back.

``PROPERTY_PATHS`` lists the common, documented paths with tighter bounds than
RNA's. They order dependent writes (render engine before samples, display
device before view transform), are the scene settings ``angelo_scene_settings``
reports and its revision covers, and are checked against RNA by
``test_blender_property_paths.py``. Enumerations marked dynamic depend on the
build or on other settings; RNA decides by assignment.
"""

import re

from .action_schema_types import NULLABLE_POINTER, OBJECT_REFERENCE, number, integer, enum, text, array, obj
from .rna_rules import (PATH_PATTERN, PropertyPathError, SCENE, OBJECT, owner_of, family, screen_path,  # noqa: F401
                        json_value, object_reference, plain, tolerance, same as same_value)

MAX_VALUES = 32
# Owner families: one program operation writes settings of exactly one owner.
DYNAMIC = None  # Enum items come from RNA at run time.

# path -> (owner, object types for data paths or None, kind, *arguments)
# kinds: bool | int lo hi | float lo hi | floats length lo hi | enum items|DYNAMIC | string max | object type
# Order is significant: the kernel writes in this order, so settings that
# restrict another's items (colour-management device -> view -> look, unit
# system -> length unit) come first.
PROPERTY_PATHS = {
    # Object settings.
    "hide_viewport": (OBJECT, None, "bool"),
    "hide_render": (OBJECT, None, "bool"),
    "hide_select": (OBJECT, None, "bool"),
    "display_type": (OBJECT, None, "enum", ("BOUNDS", "WIRE", "SOLID", "TEXTURED")),
    "show_wire": (OBJECT, None, "bool"),
    "show_in_front": (OBJECT, None, "bool"),
    "show_name": (OBJECT, None, "bool"),
    "show_axis": (OBJECT, None, "bool"),
    "show_bounds": (OBJECT, None, "bool"),
    "color": (OBJECT, None, "floats", 4, 0, 1),
    "pass_index": (OBJECT, None, "int", 0, 32767),
    "visible_camera": (OBJECT, None, "bool"),
    "visible_shadow": (OBJECT, None, "bool"),
    "visible_diffuse": (OBJECT, None, "bool"),
    "visible_glossy": (OBJECT, None, "bool"),
    "visible_transmission": (OBJECT, None, "bool"),
    "visible_volume_scatter": (OBJECT, None, "bool"),
    "display_bounds_type": (OBJECT, None, "enum", ("BOX", "SPHERE", "CYLINDER", "CONE", "CAPSULE")),
    "show_transparent": (OBJECT, None, "bool"),
    "show_texture_space": (OBJECT, None, "bool"),
    "is_holdout": (OBJECT, None, "bool"),
    "is_shadow_catcher": (OBJECT, None, "bool"),
    "hide_probe_volume": (OBJECT, None, "bool"),
    "hide_probe_sphere": (OBJECT, None, "bool"),
    "hide_probe_plane": (OBJECT, None, "bool"),
    "empty_display_type": (OBJECT, None, "enum", ("PLAIN_AXES", "ARROWS", "SINGLE_ARROW", "CIRCLE", "CUBE", "SPHERE",
                                                  "CONE")),
    "empty_display_size": (OBJECT, None, "float", .0001, 1000),
    # Curve and text geometry (Curve data; text objects use TextCurve data).
    "data.dimensions": (OBJECT, ("CURVE",), "enum", ("2D", "3D")),
    "data.extrude": (OBJECT, ("CURVE", "FONT"), "float", 0, 1000),
    "data.bevel_depth": (OBJECT, ("CURVE", "FONT"), "float", 0, 1000),
    "data.bevel_resolution": (OBJECT, ("CURVE", "FONT"), "int", 0, 32),
    "data.resolution_u": (OBJECT, ("CURVE", "FONT"), "int", 1, 64),
    "data.render_resolution_u": (OBJECT, ("CURVE", "FONT"), "int", 0, 64),
    "data.offset": (OBJECT, ("CURVE", "FONT"), "float", -1000, 1000),
    "data.fill_mode": (OBJECT, ("CURVE", "FONT"), "enum", ("FULL", "BACK", "FRONT", "HALF", "NONE", "BOTH")),
    "data.use_fill_caps": (OBJECT, ("CURVE",), "bool"),
    "data.body": (OBJECT, ("FONT",), "string", 1024),
    "data.size": (OBJECT, ("FONT",), "float", .0001, 10000),
    "data.space_character": (OBJECT, ("FONT",), "float", 0, 10),
    "data.space_word": (OBJECT, ("FONT",), "float", 0, 10),
    "data.space_line": (OBJECT, ("FONT",), "float", 0, 10),
    "data.shear": (OBJECT, ("FONT",), "float", -1, 1),
    "data.offset_x": (OBJECT, ("FONT",), "float", -1000, 1000),
    "data.offset_y": (OBJECT, ("FONT",), "float", -1000, 1000),
    "data.align_x": (OBJECT, ("FONT",), "enum", ("LEFT", "CENTER", "RIGHT", "JUSTIFY", "FLUSH")),
    "data.align_y": (OBJECT, ("FONT",), "enum", ("TOP", "TOP_BASELINE", "CENTER", "BOTTOM_BASELINE", "BOTTOM")),
    # Camera depth of field and lens shift (the rest is camera_configure).
    "data.dof.use_dof": (OBJECT, ("CAMERA",), "bool"),
    "data.dof.focus_distance": (OBJECT, ("CAMERA",), "float", 0, 100000),
    "data.dof.aperture_fstop": (OBJECT, ("CAMERA",), "float", .1, 1000),
    "data.dof.aperture_blades": (OBJECT, ("CAMERA",), "int", 0, 16),
    "data.shift_x": (OBJECT, ("CAMERA",), "float", -10, 10),
    "data.shift_y": (OBJECT, ("CAMERA",), "float", -10, 10),
    "data.dof.aperture_ratio": (OBJECT, ("CAMERA",), "float", .01, 1000),
    "data.dof.aperture_rotation": (OBJECT, ("CAMERA",), "float", -3.14159, 3.14159),
    "data.lens_unit": (OBJECT, ("CAMERA",), "enum", ("MILLIMETERS", "FOV")),
    "data.show_passepartout": (OBJECT, ("CAMERA",), "bool"),
    "data.passepartout_alpha": (OBJECT, ("CAMERA",), "float", 0, 1),
    "data.display_size": (OBJECT, ("CAMERA",), "float", .01, 1000),
    "data.show_limits": (OBJECT, ("CAMERA",), "bool"),
    "data.show_mist": (OBJECT, ("CAMERA",), "bool"),
    "data.show_sensor": (OBJECT, ("CAMERA",), "bool"),
    # Light contributions, temperature and shadows (type, energy, colour and size are light_configure).
    "data.diffuse_factor": (OBJECT, ("LIGHT",), "float", 0, 1000),
    "data.specular_factor": (OBJECT, ("LIGHT",), "float", 0, 1000),
    "data.volume_factor": (OBJECT, ("LIGHT",), "float", 0, 1000),
    "data.transmission_factor": (OBJECT, ("LIGHT",), "float", 0, 1000),
    "data.exposure": (OBJECT, ("LIGHT",), "float", -32, 32),
    "data.use_temperature": (OBJECT, ("LIGHT",), "bool"),
    "data.temperature": (OBJECT, ("LIGHT",), "float", 800, 20000),
    "data.normalize": (OBJECT, ("LIGHT",), "bool"),
    "data.use_soft_falloff": (OBJECT, ("LIGHT",), "bool"),
    "data.use_shadow_jitter": (OBJECT, ("LIGHT",), "bool"),
    "data.shadow_jitter_overblur": (OBJECT, ("LIGHT",), "float", 0, 100),
    "data.shadow_filter_radius": (OBJECT, ("LIGHT",), "float", 0, 100),
    "data.shadow_maximum_resolution": (OBJECT, ("LIGHT",), "float", 0, 1000),
    # Scene: render, sampling, colour management, frame range, camera, units.
    "render.engine": (SCENE, None, "enum", ("BLENDER_EEVEE", "BLENDER_WORKBENCH", "CYCLES")),
    "render.resolution_x": (SCENE, None, "int", 4, 16384),
    "render.resolution_y": (SCENE, None, "int", 4, 16384),
    "render.resolution_percentage": (SCENE, None, "int", 1, 400),
    "render.pixel_aspect_x": (SCENE, None, "float", 1, 200),
    "render.pixel_aspect_y": (SCENE, None, "float", 1, 200),
    "render.film_transparent": (SCENE, None, "bool"),
    "render.fps": (SCENE, None, "int", 1, 1000),
    "render.fps_base": (SCENE, None, "float", .001, 1000),
    "render.use_motion_blur": (SCENE, None, "bool"),
    "render.use_simplify": (SCENE, None, "bool"),
    "render.simplify_subdivision": (SCENE, None, "int", 0, 6),
    "render.image_settings.file_format": (SCENE, None, "enum", DYNAMIC),
    "render.image_settings.color_mode": (SCENE, None, "enum", ("BW", "RGB", "RGBA")),
    "render.image_settings.color_depth": (SCENE, None, "enum", DYNAMIC),
    "eevee.taa_render_samples": (SCENE, None, "int", 1, 65536),
    "eevee.taa_samples": (SCENE, None, "int", 0, 65536),
    "eevee.use_raytracing": (SCENE, None, "bool"),
    "eevee.use_shadows": (SCENE, None, "bool"),
    "eevee.use_volumetric_shadows": (SCENE, None, "bool"),
    "eevee.fast_gi_method": (SCENE, None, "enum", ("AMBIENT_OCCLUSION_ONLY", "GLOBAL_ILLUMINATION")),
    "display_settings.display_device": (SCENE, None, "enum", DYNAMIC),
    "view_settings.view_transform": (SCENE, None, "enum", DYNAMIC),
    "view_settings.look": (SCENE, None, "enum", DYNAMIC),
    "view_settings.exposure": (SCENE, None, "float", -32, 32),
    "view_settings.gamma": (SCENE, None, "float", 0, 5),
    "frame_start": (SCENE, None, "int", 0, 1048574),
    "frame_end": (SCENE, None, "int", 0, 1048574),
    "frame_step": (SCENE, None, "int", 1, 100),
    "camera": (SCENE, None, "object", "CAMERA"),
    "render.use_border": (SCENE, None, "bool"),
    "render.use_crop_to_border": (SCENE, None, "bool"),
    "render.use_persistent_data": (SCENE, None, "bool"),
    "render.use_compositing": (SCENE, None, "bool"),
    "render.use_sequencer": (SCENE, None, "bool"),
    "render.use_freestyle": (SCENE, None, "bool"),
    "render.use_high_quality_normals": (SCENE, None, "bool"),
    "render.use_simplify_normals": (SCENE, None, "bool"),
    "render.simplify_subdivision_render": (SCENE, None, "int", 0, 6),
    "render.simplify_child_particles": (SCENE, None, "float", 0, 1),
    "render.motion_blur_shutter": (SCENE, None, "float", 0, 100),
    "render.motion_blur_position": (SCENE, None, "enum", ("START", "CENTER", "END")),
    "render.dither_intensity": (SCENE, None, "float", 0, 2),
    "eevee.motion_blur_steps": (SCENE, None, "int", 1, 64),
    "eevee.motion_blur_max": (SCENE, None, "int", 0, 2048),
    "eevee.use_fast_gi": (SCENE, None, "bool"),
    "eevee.fast_gi_resolution": (SCENE, None, "enum", ("1", "2", "4", "8", "16")),
    "eevee.fast_gi_step_count": (SCENE, None, "int", 1, 64),
    "eevee.fast_gi_distance": (SCENE, None, "float", 0, 100000),
    "eevee.ray_tracing_method": (SCENE, None, "enum", ("PROBE", "SCREEN")),
    "eevee.shadow_ray_count": (SCENE, None, "int", 1, 4),
    "eevee.shadow_step_count": (SCENE, None, "int", 1, 16),
    "eevee.volumetric_tile_size": (SCENE, None, "enum", ("1", "2", "4", "8", "16")),
    "eevee.volumetric_samples": (SCENE, None, "int", 1, 256),
    "eevee.volumetric_start": (SCENE, None, "float", .000001, 100000),
    "eevee.volumetric_end": (SCENE, None, "float", .000001, 100000),
    "eevee.volumetric_light_clamp": (SCENE, None, "float", 0, 100000),
    "eevee.clamp_surface_direct": (SCENE, None, "float", 0, 100000),
    "eevee.clamp_surface_indirect": (SCENE, None, "float", 0, 100000),
    "eevee.use_overscan": (SCENE, None, "bool"),
    "eevee.overscan_size": (SCENE, None, "float", 0, 50),
    # Cycles settings, listed under unavailable_in_build where Cycles is not compiled in.
    "cycles.samples": (SCENE, None, "int", 1, 16777216),
    "cycles.preview_samples": (SCENE, None, "int", 0, 16777216),
    "cycles.use_adaptive_sampling": (SCENE, None, "bool"),
    "cycles.adaptive_threshold": (SCENE, None, "float", 0, 1),
    "cycles.use_denoising": (SCENE, None, "bool"),
    "cycles.max_bounces": (SCENE, None, "int", 0, 1024),
    "use_preview_range": (SCENE, None, "bool"),
    "frame_preview_start": (SCENE, None, "int", 0, 1048574),
    "frame_preview_end": (SCENE, None, "int", 0, 1048574),
    "use_gravity": (SCENE, None, "bool"),
    "gravity": (SCENE, None, "floats", 3, -1000, 1000),
    "audio_volume": (SCENE, None, "float", 0, 100),
    "unit_settings.system": (SCENE, None, "enum", ("NONE", "METRIC", "IMPERIAL")),
    "unit_settings.scale_length": (SCENE, None, "float", .00001, 100000),
    "unit_settings.length_unit": (SCENE, None, "enum", DYNAMIC),
}
ORDER = {path: index for index, path in enumerate(PROPERTY_PATHS)}
# Common paths first, in their dependency order; any other path after them.
_LATE = len(ORDER)
# The RNA type each common value kind has in the installed build.
RNA_TYPES = {"bool": "BOOLEAN", "int": "INT", "float": "FLOAT", "floats": "FLOAT", "enum": "ENUM",
             "string": "STRING", "object": "POINTER"}
_SCALAR = {"oneOf": [{"type": "boolean"}, {"type": "number", "minimum": -1e30, "maximum": 1e30},
                     {"type": "string", "minLength": 1, "maxLength": 1024}]}
OBJECT_VALUE = OBJECT_REFERENCE  # An object in an open map (action_schema_types).
VALUE = {"oneOf": [*_SCALAR["oneOf"], {"type": "array", "items": _SCALAR, "minItems": 1, "maxItems": 32},
                   OBJECT_VALUE, {"type": "null"}]}
# A map whose values are never objects (node sockets and settings, F-Curve modifier settings): its schema offers
# no {"object": ...}, which the host refused there anyway (open_values).
SCALAR_VALUE = {"oneOf": [member for member in VALUE["oneOf"] if member is not OBJECT_VALUE]}


def values_schema():
    """An open map: settings paths to JSON values; RNA, not the schema, types each value.

    ``camera`` is a typed pointer (``NULLABLE_POINTER``): its handle or ``$alias``, or
    ``{"object": handle}``, the form of every other object pointer in the map,
    which ``canonical`` stores as the handle alone. Short identifiers resolve either.
    """
    return {"type": "object", "properties": {"camera": NULLABLE_POINTER},
            "additionalProperties": VALUE, "propertyNames": {"pattern": PATH_PATTERN},
            "minProperties": 1, "maxProperties": MAX_VALUES}


# Material paths (``active_material.``) write the live material, which every object using it shows. A material
# another user also shows is refused unless ``shared`` is ALL_USERS (``material_sharing``). Only that value is
# spelled: leaving it out refuses, which keeps the plan schema and the Codex reference small.
ALL_USERS = "ALL_USERS"
PROPERTY_SET_SCHEMA = obj({"values": values_schema(), "shared": enum([ALL_USERS])}, ["values"])


def screen_values(values):
    """Raise a typed error for any denied path or non-JSON value, before schema validation."""
    if type(values) is not dict:
        return
    if not values:
        raise PropertyPathError("EMPTY", "values", "name at least one settings path")
    if len(values) > MAX_VALUES:
        raise PropertyPathError("NOT_ALLOWED", "values", "at most %d settings per operation" % MAX_VALUES)
    for path, value in values.items():
        screen_path(path)
        if path != "camera":
            json_value(value, path)


# Object and data settings property_keyframe admits by their shape that RNA marks not animatable (swept over the
# object and data types in test_blender_geometry_measures): refused before approval, not after it at staging.
NOT_ANIMATABLE = frozenset((
    "active_material_index active_shape_key_index hide_select matrix_world matrix_local matrix_basis "
    "use_simulation_cache data.uv_layer_clone_index data.uv_layer_stencil_index data.remesh_voxel_size "
    "data.remesh_voxel_adaptivity data.use_remesh_fix_poles data.use_remesh_preserve_volume "
    "data.use_remesh_preserve_attributes data.remesh_mode data.use_mirror_x data.use_mirror_y data.use_mirror_z "
    "data.use_mirror_vertex_groups data.radial_symmetry data.use_mirror_topology data.use_paint_bone_selection "
    "data.use_paint_mask data.use_paint_mask_vertex data.resolution_v data.angle_x data.angle_y data.angle "
    "data.normalize data.use_nodes data.points_u data.points_v data.points_w").split())


def animatable(path):
    """Object and data settings can be keyed (RNA decides is_animatable); scene settings, common paths whose
    value is an enum, text or pointer, and ``NOT_ANIMATABLE`` cannot."""
    return (owner_of(path) == OBJECT and value_kind(path) in (None, "bool", "int", "float", "floats")
            and path not in NOT_ANIMATABLE)


def data_family(path):
    """The staged datablock family a path writes: data, material or world, or None for the owner itself."""
    return family(path)


def value_kind(path):
    """The value kind of a common path (``PROPERTY_PATHS``), else None (RNA decides)."""
    entry = PROPERTY_PATHS.get(path)
    return entry[2] if entry else None


def canonical(values):
    """Cross-field rules of screened values; returns them in write order (common paths first)."""
    if not values:
        raise PropertyPathError("EMPTY", "values", "name at least one settings path")
    if len(values) > MAX_VALUES:
        raise PropertyPathError("NOT_ALLOWED", "values", "at most %d settings per operation" % MAX_VALUES)
    from .action_schema_types import _validate
    for path, value in values.items():
        screen_path(path)
        if path == "camera" and object_reference(value) is not None:
            values[path] = value = object_reference(value)  # One stored form (values_schema).
        if path != "camera":
            json_value(value, path)
        if path in PROPERTY_PATHS:  # Common paths keep their tighter typed bounds.
            _validate(value, value_schema(path), "values." + path)
    if len({owner_of(path) for path in values}) != 1:  # Named: unnamed, a model guessed which.
        objects = ", ".join(path[:64] for path in values if owner_of(path) != SCENE)
        raise PropertyPathError("MIXED_OWNERS", "values", "object and scene settings need separate operations (not "
                                "scene settings: %s)" % (objects if len(objects) <= 200 else objects[:199] + "…"))
    if "active_material_index" in values and any(family(path) == "material" for path in values):
        from .program_rules import SLOT_CONFLICT
        raise PropertyPathError("ACTIVE_SLOT_CONFLICT", "active_material_index", SLOT_CONFLICT)
    if "frame_start" in values and "frame_end" in values and values["frame_start"] > values["frame_end"]:
        raise PropertyPathError("NOT_ALLOWED", "frame_start", "the start frame cannot follow the end frame")
    order = list(values)
    return {path: values[path] for path in sorted(values, key=lambda path: (ORDER.get(path, _LATE), order.index(path)))}


def check_shared(values, shared):
    """``shared`` ALL_USERS states an edit of the active material for every user: it needs a material path."""
    if shared == ALL_USERS and not any(family(path) == "material" for path in values):
        raise PropertyPathError("ALL_USERS_MATERIAL_ONLY", "shared", "shared ALL_USERS edits the active material for "
                                "every object that uses it; it needs an active_material. path")


def references(values):
    """Object handles a canonical value set depends on (object-pointer settings)."""
    refs = [values["camera"]] if isinstance(values.get("camera"), str) else []
    return refs + [object_reference(value) for value in values.values() if object_reference(value) is not None]


def value_schema(path):
    """The typed schema of a common path (its documented bounds)."""
    _owner, _types, kind, *arguments = PROPERTY_PATHS[path]
    if kind == "bool":
        return {"type": "boolean"}
    if kind == "int":
        return integer(arguments[0], arguments[1])
    if kind == "float":
        return number(arguments[0], arguments[1])
    if kind == "floats":
        return array(number(arguments[1], arguments[2]), arguments[0], arguments[0])
    if kind == "enum":
        return enum(arguments[0]) if arguments[0] is not DYNAMIC else text(64, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.,+()/\-]{0,63}$")
    if kind == "string":
        return text(arguments[0])
    return NULLABLE_POINTER


def holder_and_name(owner, path):
    """Walk ``path`` to (holder struct, final property name) on a staged or live owner.

    Plain attribute access along the path, so the same walk serves the kernel,
    commit transfer, verification and observation; the RNA rules are applied by
    ``rna_rules.resolve`` where a value is written.
    """
    screen_path(path)
    *parents, name = path.split(".")
    holder = owner
    for parent in parents:
        rna = getattr(holder, "bl_rna", None)
        if rna is not None and rna.properties.get(parent) is None:  # RNA properties only, never Python attributes.
            raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", path, "the owner has no " + parent)
        holder = getattr(holder, parent, None)
        if holder is None:
            raise PropertyPathError("PROPERTY_MISSING_IN_BUILD", path, "the owner has no " + parent)
    return holder, name


def same(path, expected, actual):
    """Whether a read-back equals the requested value (float32 tolerance for numbers)."""
    if value_kind(path) == "float" and type(expected) is int:
        expected = float(expected)
    return same_value(expected, actual)
