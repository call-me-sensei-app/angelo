# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed semantic field allowlists; never generic RNA or custom properties."""


MODIFIER_FIELDS = {
    "BEVEL": ("width", "width_pct", "segments", "affect", "limit_method", "angle_limit", "offset_type", "use_clamp_overlap", "vertex_group",
              "edge_weight", "vertex_weight", "invert_vertex_group", "profile_type", "profile", "material", "loop_slide", "mark_seam",
              "mark_sharp", "harden_normals", "face_strength_mode", "miter_outer", "miter_inner", "spread", "vmesh_method"),
    "SUBSURF": ("levels", "render_levels", "subdivision_type", "quality", "uv_smooth", "boundary_smooth", "use_creases",
                "show_only_control_edges", "use_custom_normals", "use_limit_surface", "use_adaptive_subdivision", "adaptive_space",
                "adaptive_pixel_size", "adaptive_object_edge_length"),
    "MULTIRES": ("levels", "sculpt_levels", "render_levels", "total_levels", "uv_smooth", "boundary_smooth"),
    "MIRROR": ("use_axis", "use_bisect_axis", "use_bisect_flip_axis", "use_clip", "use_mirror_merge", "merge_threshold", "mirror_object",
               "use_mirror_vertex_groups", "use_mirror_u", "use_mirror_v", "use_mirror_udim", "mirror_offset_u", "mirror_offset_v", "offset_u", "offset_v", "bisect_threshold"),
    "SOLIDIFY": ("thickness", "offset", "use_even_offset", "use_quality_normals", "use_rim", "material_offset", "vertex_group",
                 "solidify_mode", "thickness_clamp", "use_thickness_angle_clamp", "thickness_vertex_group", "edge_crease_inner", "edge_crease_outer",
                 "edge_crease_rim", "material_offset_rim", "shell_vertex_group", "rim_vertex_group", "invert_vertex_group", "use_flat_faces",
                 "use_flip_normals", "use_rim_only", "nonmanifold_thickness_mode", "nonmanifold_boundary_mode", "nonmanifold_merge_threshold", "bevel_convex"),
    "ARRAY": ("fit_type", "count", "relative_offset_displace", "constant_offset_displace", "use_relative_offset", "use_constant_offset", "use_object_offset", "offset_object", "curve", "start_cap", "end_cap", "use_merge_vertices", "merge_threshold",
              "fit_length", "use_merge_vertices_cap", "offset_u", "offset_v"),
    "BOOLEAN": ("operation", "solver", "operand_type", "object", "collection", "double_threshold", "use_self", "use_hole_tolerant", "material_mode", "debug_options"),
    "ARMATURE": ("object", "use_vertex_groups", "use_bone_envelopes", "use_deform_preserve_volume", "vertex_group", "invert_vertex_group", "use_multi_modifier"),
    "SHRINKWRAP": ("target", "auxiliary_target", "wrap_method", "wrap_mode", "offset", "use_project_x", "use_project_y", "use_project_z", "use_negative_direction", "use_positive_direction", "vertex_group",
                   "cull_face", "project_limit", "subsurf_levels", "use_invert_cull", "invert_vertex_group"),
    "DECIMATE": ("decimate_type", "ratio", "iterations", "angle_limit", "use_collapse_triangulate", "use_symmetry", "symmetry_axis", "vertex_group",
                 "invert_vertex_group", "vertex_group_factor", "use_dissolve_boundaries", "delimit"),
    "DISPLACE": ("strength", "mid_level", "direction", "space", "vertex_group", "invert_vertex_group", "texture", "texture_coords",
                 "uv_layer", "texture_coords_object", "texture_coords_bone"),
    "SCREW": ("object", "steps", "render_steps", "iterations", "axis", "angle", "screw_offset", "merge_threshold", "use_normal_flip",
              "use_normal_calculate", "use_object_screw_offset", "use_merge_vertices", "use_smooth_shade", "use_stretch_u", "use_stretch_v"),
    "TRIANGULATE": ("quad_method", "ngon_method", "min_vertices", "keep_custom_normals"),
    "WELD": ("mode", "merge_threshold", "vertex_group", "invert_vertex_group", "loose_edges"),
    "REMESH": ("mode", "octree_depth", "scale", "threshold", "use_smooth_shade", "use_remove_disconnected", "voxel_size", "adaptivity",
               "sharpness"),
    "SMOOTH": ("factor", "iterations", "use_x", "use_y", "use_z", "vertex_group"),
    "LAPLACIANSMOOTH": ("iterations", "lambda_factor", "lambda_border", "use_x", "use_y", "use_z", "use_volume_preserve", "use_normalized", "vertex_group"),
    "CORRECTIVE_SMOOTH": ("factor", "iterations", "scale", "smooth_type", "rest_source", "use_only_smooth", "use_pin_boundary", "vertex_group"),
    "WEIGHTED_NORMAL": ("mode", "weight", "thresh", "keep_sharp", "use_face_influence", "vertex_group", "invert_vertex_group"),
    "NORMAL_EDIT": ("mode", "mix_mode", "mix_factor", "mix_limit", "target", "offset", "vertex_group"),
    "EDGE_SPLIT": ("split_angle", "use_edge_angle", "use_edge_sharp"),
    "CURVE": ("object", "deform_axis", "vertex_group", "invert_vertex_group"),
    "LATTICE": ("object", "strength", "vertex_group"),
    "MESH_DEFORM": ("object", "precision", "use_dynamic_bind", "vertex_group", "is_bound"),
    "SURFACE_DEFORM": ("target", "falloff", "strength", "vertex_group", "is_bound"),
    "NODES": ("node_group", "use_pin_to_last", "use_apply_on_spline", "bake_target"),
}

# Every semantic field of these types is listed above, so their revisions are exact.
REVISION_MODIFIERS = frozenset({"BEVEL", "SUBSURF", "MIRROR", "SOLIDIFY", "ARRAY", "BOOLEAN", "ARMATURE", "TRIANGULATE", "WELD",
                                "DECIMATE", "WEIGHTED_NORMAL", "SHRINKWRAP", "DISPLACE", "SCREW", "CURVE", "REMESH"})

CONSTRAINT_COMMON_FIELDS = ("owner_space", "target_space", "space_object", "space_subtarget", "influence", "mute", "enabled")
CONSTRAINT_TARGET_FIELDS = ("target", "subtarget")
CONSTRAINT_BONE_TARGET_FIELDS = CONSTRAINT_TARGET_FIELDS + ("head_tail", "use_bbone_shape")
CONSTRAINT_LIMIT_FIELDS = ("min_x", "max_x", "min_y", "max_y", "min_z", "max_z", "use_transform_limit")
CONSTRAINT_AXIS_LIMIT_FIELDS = ("use_min_x", "use_min_y", "use_min_z", "use_max_x", "use_max_y", "use_max_z")
# Every mutable semantic field on these native constraint types is covered.
# UI expansion/active and derived errors/validity are not evaluation inputs.
CONSTRAINT_TYPE_FIELDS = {
    "COPY_LOCATION": CONSTRAINT_BONE_TARGET_FIELDS + ("use_x", "use_y", "use_z", "invert_x", "invert_y", "invert_z", "use_offset"),
    "COPY_ROTATION": CONSTRAINT_TARGET_FIELDS + ("use_x", "use_y", "use_z", "invert_x", "invert_y", "invert_z", "euler_order", "mix_mode", "use_offset"),
    "COPY_SCALE": CONSTRAINT_TARGET_FIELDS + ("use_x", "use_y", "use_z", "power", "use_make_uniform", "use_offset", "use_add"),
    "COPY_TRANSFORMS": CONSTRAINT_BONE_TARGET_FIELDS + ("remove_target_shear", "mix_mode"),
    "LIMIT_LOCATION": CONSTRAINT_LIMIT_FIELDS + CONSTRAINT_AXIS_LIMIT_FIELDS,
    "LIMIT_ROTATION": CONSTRAINT_LIMIT_FIELDS + ("use_limit_x", "use_limit_y", "use_limit_z", "euler_order", "use_legacy_behavior"),
    "LIMIT_SCALE": CONSTRAINT_LIMIT_FIELDS + CONSTRAINT_AXIS_LIMIT_FIELDS,
    "TRACK_TO": CONSTRAINT_BONE_TARGET_FIELDS + ("track_axis", "up_axis", "use_target_z"),
    "DAMPED_TRACK": CONSTRAINT_BONE_TARGET_FIELDS + ("track_axis",),
    "CHILD_OF": CONSTRAINT_TARGET_FIELDS + ("use_location_x", "use_location_y", "use_location_z", "use_rotation_x", "use_rotation_y",
                                            "use_rotation_z", "use_scale_x", "use_scale_y", "use_scale_z", "set_inverse_pending", "inverse_matrix"),
}

# Unsupported types still expose closed descriptive metadata, not an exact revision.
CONSTRAINT_FIELDS = ("target", "subtarget", "pole_target", "pole_subtarget", "space_object", "space_subtarget",
                      "owner_space", "target_space", "influence", "mute", "use_offset", "use_x", "use_y", "use_z",
                      "invert_x", "invert_y", "invert_z", "mix_mode", "chain_count", "pole_angle", "use_tail",
                      "use_stretch", "use_limit_x", "use_limit_y", "use_limit_z", "min_x", "max_x", "min_y", "max_y",
                      "min_z", "max_z", "head_tail", "track_axis", "up_axis", "inverse_matrix", "use_location_x", "use_location_y",
                      "use_location_z", "use_rotation_x", "use_rotation_y", "use_rotation_z", "use_scale_x", "use_scale_y", "use_scale_z",
                      "use_transform_limit", "use_make_uniform", "use_bbone_shape", "remove_target_shear", "euler_order", "use_target_z")

NODE_FIELDS = ("operation", "blend_type", "data_type", "distribution", "subsurface_method", "interpolation",
                "projection", "projection_blend", "extension", "attribute_name", "uv_map", "space", "vector_type",
                "convert_from", "convert_to", "clamp_type", "use_clamp", "use_clamp_result", "use_clamp_factor",
                "use_alpha", "use_map_range", "invert", "normalize", "mode", "component", "domain", "factor_mode",
                "target", "is_active_output", "object", "from_instancer", "noise_dimensions", "noise_type")

TEXTURE_MAPPING_FIELDS = ("vector_type", "translation", "rotation", "scale", "min", "max", "use_min", "use_max",
                          "mapping_x", "mapping_y", "mapping_z", "mapping")
COLOR_MAPPING_FIELDS = ("use_color_ramp", "brightness", "contrast", "saturation", "blend_type", "blend_color", "blend_factor")

KNOWN_NODES = frozenset({"ShaderNodeBsdfPrincipled", "ShaderNodeOutputMaterial", "ShaderNodeMath", "ShaderNodeVectorMath",
                         "ShaderNodeMixRGB", "ShaderNodeMix", "ShaderNodeRGB", "ShaderNodeValue", "ShaderNodeTexCoord",
                         "ShaderNodeMapping", "ShaderNodeHueSaturation", "ShaderNodeInvert", "ShaderNodeSeparateRGB",
                         "ShaderNodeCombineRGB", "ShaderNodeSeparateXYZ", "ShaderNodeCombineXYZ", "ShaderNodeNormalMap",
                         "ShaderNodeBump", "ShaderNodeWavelength", "ShaderNodeFresnel", "ShaderNodeLayerWeight",
                         "ShaderNodeEmission", "ShaderNodeBackground", "ShaderNodeMixShader", "ShaderNodeAddShader",
                         "ShaderNodeGroup", "NodeReroute", "NodeGroupInput", "NodeGroupOutput", "ShaderNodeTexNoise",
                         "ShaderNodeOutputLight"})

KNOWN_CONSTRAINTS = frozenset(CONSTRAINT_TYPE_FIELDS)

# Camera/light object data: every writable semantic and display setting.
# Derived angle_x/angle_y/angle follow lens and sensor. Custom (OSL) camera
# paths, scripts and bytecode are never read: CUSTOM cameras and background
# images are revision limitations instead.
CAMERA_FIELDS = ("type", "lens", "lens_unit", "ortho_scale", "sensor_fit", "sensor_width", "sensor_height",
                 "clip_start", "clip_end", "shift_x", "shift_y", "panorama_type", "fisheye_fov", "fisheye_lens",
                 "latitude_min", "latitude_max", "longitude_min", "longitude_max", "fisheye_polynomial_k0",
                 "fisheye_polynomial_k1", "fisheye_polynomial_k2", "fisheye_polynomial_k3", "fisheye_polynomial_k4",
                 "central_cylindrical_range_u_min", "central_cylindrical_range_u_max", "central_cylindrical_range_v_min",
                 "central_cylindrical_range_v_max", "central_cylindrical_radius", "display_size", "passepartout_alpha",
                 "show_limits", "show_mist", "show_passepartout", "show_safe_areas", "show_safe_center", "show_name",
                 "show_sensor", "show_background_images", "composition_guide_color", "show_composition_center",
                 "show_composition_center_diagonal", "show_composition_thirds", "show_composition_golden",
                 "show_composition_golden_tria_a", "show_composition_golden_tria_b", "show_composition_harmony_tri_a",
                 "show_composition_harmony_tri_b")
CAMERA_DOF_FIELDS = ("use_dof", "focus_object", "focus_subtarget", "focus_distance", "aperture_fstop",
                     "aperture_blades", "aperture_rotation", "aperture_ratio")
CAMERA_STEREO_FIELDS = ("convergence_mode", "pivot", "interocular_distance", "convergence_distance",
                        "use_spherical_stereo", "use_pole_merge", "pole_merge_angle_from", "pole_merge_angle_to")
# Union across light subtypes; readers skip fields a subtype does not expose.
LIGHT_FIELDS = ("type", "color", "energy", "exposure", "normalize", "use_temperature", "temperature",
                "specular_factor", "diffuse_factor", "transmission_factor", "volume_factor", "use_custom_distance",
                "cutoff_distance", "use_shadow", "use_soft_falloff", "shadow_soft_size", "shadow_buffer_clip_start",
                "shadow_filter_radius", "shadow_maximum_resolution", "use_shadow_jitter", "shadow_jitter_overblur",
                "use_absolute_resolution", "angle", "shadow_cascade_max_distance", "shadow_cascade_count",
                "shadow_cascade_exponent", "shadow_cascade_fade", "spot_size", "spot_blend", "show_cone",
                "use_square", "shape", "size", "size_y", "spread")
