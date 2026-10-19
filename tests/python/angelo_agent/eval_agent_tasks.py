# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Provider-independent host-graded native task matrix; no model calls.

These are small operation programs, not visual/artistic quality benchmarks.
Success is determined by closed host checks, irrespective of an agent's optional
reasoning-round ``verify`` flag. Use a NEW empty scratch scene per fixture.
"""

import copy


def action(kind, target=None, **parameters):
    return {"kind": kind, "target": target, "parameters": parameters}


def primitive(kind="CUBE", alias="mesh", **parameters):
    return action("create_primitive", primitive=kind, alias=alias, name="QA " + alias, **parameters)


def check(kind, target="$mesh", **parameters):
    return {"kind": kind, "target": target, **parameters}


def dimensions(values):
    return check("dimensions", expected=values, tolerance=0.0001)


def counts(vertices, edges, faces):
    return check("mesh_counts", vertices={"minimum": vertices, "maximum": vertices},
                 edges={"minimum": edges, "maximum": edges}, faces={"minimum": faces, "maximum": faces})


def clean():
    return check("mesh_degenerate", max_faces=0, max_edges=0, epsilon=0.00000001)


def modifier(name, kind, values):
    return check("modifier", name=name, type=kind, values=values, tolerance=0.00001)


def task(identity, description, operations, checks, expected="pass", categories=()):
    return {"id": identity, "description": description, "categories": list(categories),
            "program": {"mode": "APPLY", "read_set": [], "write_set": [], "operations": operations,
                        "expected_checks": checks}, "expected_check_status": expected,
            "paid_requests": 0, "fixture_scope": "isolated_scratch_scene"}


TASK_FIXTURES = [
    task("01_cube_dimensions_counts", "Create a named manifold cube of exact size and topology.",
         [primitive(size=2)], [dimensions([2, 2, 2]), counts(8, 12, 6), clean(),
                              check("object_name", expected="QA mesh"), check("mesh_manifold", expected="CLOSED")], categories=("dimensions", "counts", "topology")),
    task("02_fit_nonuniform_dimensions", "Set explicit nonuniform positive dimensions.",
         [primitive(), action("transform", "$mesh", scale=[2, 0.5, 1.5])],
         [dimensions([4, 1, 3]), check("finite_coordinates")], categories=("dimensions", "transform")),
    task("03_plane_open_surface", "Create a clean open quad with zero thickness.",
         [primitive("PLANE", size=3)], [dimensions([3, 3, 0]), counts(4, 4, 1), clean(), check("mesh_manifold", expected="OPEN")], categories=("dimensions", "counts", "topology")),
    task("04_grid_topology", "Create a bounded grid with exact mesh counts.",
         [primitive("GRID", size=2, x_segments=3, y_segments=2)],
         [dimensions([2, 2, 0]), counts(12, 17, 6), clean(), check("mesh_manifold", expected="OPEN")], categories=("counts", "topology")),
    task("05_sphere_closed", "Create a bounded smooth primitive with finite closed topology.",
         [primitive("UV_SPHERE", radius=1, segments=16, rings=8)],
         [check("object_type", expected="MESH"), check("finite_coordinates"), clean(), check("mesh_manifold", expected="CLOSED")], categories=("primitive", "topology")),
    task("06_face_extrusion", "Extrude one explicit plane face by a finite distance.",
         [primitive("PLANE"), action("mesh_extrude", "$mesh", face_indices=[0], translation=[0, 0, 1])],
         [dimensions([2, 2, 1]), check("finite_coordinates"), clean()], categories=("modeling", "dimensions")),
    task("07_face_inset", "Inset one explicit cube face without changing exterior dimensions.",
         [primitive(), action("mesh_inset", "$mesh", face_indices=[0], thickness=0.1)],
         [dimensions([2, 2, 2]), check("finite_coordinates"), clean(), check("mesh_manifold", expected="CLOSED")], categories=("modeling", "topology")),
    task("08_edge_bevel", "Bevel one explicitly selected cube edge on staged geometry.",
         [primitive(), action("mesh_bevel", "$mesh", edge_indices=[0], offset=0.1, segments=2)],
         [check("finite_coordinates"), clean(), check("mesh_manifold", expected="CLOSED")], categories=("modeling", "topology")),
    task("09_bevel_modifier", "Add a non-destructive bevel with exact read-back.",
         [primitive(), action("modifier_add", "$mesh", type="BEVEL", name="QA Bevel", settings={"width": 0.125, "segments": 3})],
         [modifier("QA Bevel", "BEVEL", {"width": 0.125, "segments": 3}), dimensions([2, 2, 2])], categories=("modifiers", "dimensions")),
    task("10_subdivision_modifier", "Add bounded subdivision without applying it.",
         [primitive(), action("modifier_add", "$mesh", type="SUBSURF", name="QA Subdivision", settings={"levels": 1, "render_levels": 2})],
         [modifier("QA Subdivision", "SUBSURF", {"levels": 1, "render_levels": 2}), counts(8, 12, 6)], categories=("modifiers", "counts")),
    task("11_solidify_modifier", "Prepare a plane with an exact solidify configuration.",
         [primitive("PLANE"), action("modifier_add", "$mesh", type="SOLIDIFY", name="QA Thickness", settings={"thickness": 0.25, "offset": 0})],
         [modifier("QA Thickness", "SOLIDIFY", {"thickness": 0.25, "offset": 0}), check("finite_coordinates")], categories=("modifiers",)),
    task("12_material_assignment_values", "Assign all faces a simple Principled material with exact values.",
         [primitive(), action("material_principled", "$mesh", name="QA Graded Material", inputs={"Base Color": [0.25, 0.5, 0.75, 1], "Roughness": 0.3, "Metallic": 0.4})],
         [check("material_assignment", material="QA Graded Material", all_faces=True),
          check("material_values", material="QA Graded Material", base_color=[0.25, 0.5, 0.75, 1], roughness=0.3, metallic=0.4, tolerance=0.00001)], categories=("material",)),
    task("13_uv_cube_projection", "Project every cube face to a finite explicit UV map.",
         [primitive(), action("uv_project", "$mesh", method="CUBE", uv_map="QA UV")],
         [check("uv_layer", name="QA UV", require_nonempty=True, require_finite=True), clean()], categories=("uv",)),
    task("14_uv_planar_projection", "Planar-project a bounded quad into a complete finite map.",
         [primitive("PLANE"), action("uv_project", "$mesh", method="PLANAR", axis="Z", uv_map="QA Planar UV")],
         [check("uv_layer", name="QA Planar UV", require_nonempty=True, require_finite=True), counts(4, 4, 1)], categories=("uv", "counts")),
    task("15_parenting", "Parent two explicit staged objects while retaining world dimensions.",
         [primitive(alias="parent", location=[3, 0, 0]), primitive(), action("parent_set", "$mesh", parent="$parent", keep_transform=True)],
         [check("parent", expected="$parent", parent_type="OBJECT"), dimensions([2, 2, 2]),
          {"kind": "object_count", "minimum": 2, "maximum": 2, "types": ["MESH"]}], categories=("parenting", "dimensions", "counts")),
    task("16_animation_location", "Insert two direct location keyframes and prove channel output.",
         [primitive(), action("keyframe_insert", "$mesh", property="location", value=[0, 0, 0], frame=1, interpolation="LINEAR"),
          action("keyframe_insert", "$mesh", property="location", value=[3, 2, 1], frame=20, interpolation="LINEAR")],
         [check("animation", property="location", samples=[{"frame": 1, "expected": [0, 0, 0]}, {"frame": 20, "expected": [3, 2, 1]}], tolerance=0.00001, require_keyframes=True)], categories=("animation",)),
    task("17_rest_rig", "Create two finite nondegenerate parented rest bones.",
         [action("armature_create", name="QA Rig", alias="rig", bones=[{"name": "root", "head": [0, 0, 0], "tail": [0, 0, 1]},
                                                                        {"name": "tip", "head": [0, 0, 1], "tail": [0, 0, 2], "parent": "root", "connected": True}])],
         [check("rig", "$rig", minimum_bones=2, maximum_bones=2, expected_bones=["root", "tip"], require_finite=True)], categories=("rig",)),
    task("18_explicit_rig_weights", "Bind all cube vertices to one declared bone with normalized explicit weights.",
         [primitive(), action("armature_create", name="QA Bind Rig", alias="rig", bones=[{"name": "root", "head": [0, 0, -1], "tail": [0, 0, 1]}]),
          action("armature_bind", "$mesh", armature="$rig", name="QA Armature Bind", groups=[{"bone": "root", "weights": [{"vertex": i, "weight": 1} for i in range(8)]}])],
         [check("armature_binding", armature="$rig", modifier="QA Armature Bind", every_vertex_weighted=True, tolerance=0.00001),
          check("rig", "$rig", minimum_bones=1, maximum_bones=1, expected_bones=["root"], require_finite=True)], categories=("rig", "weights")),
    task("19_reject_wrong_dimensions", "A real staged operation with an unmet dimension target must not commit.",
         [primitive(), action("transform", "$mesh", scale=[2, 1, 1])], [dimensions([9, 2, 2])], expected="fail", categories=("rejection", "dimensions")),
    task("20_reject_wrong_manifold", "An open plane cannot satisfy a closed-manifold assertion.",
         [primitive("PLANE")], [check("mesh_manifold", expected="CLOSED")], expected="fail", categories=("rejection", "topology")),
    task("21_reject_missing_uv", "Absence of the specifically requested UV map is an observable failure.",
         [primitive()], [check("uv_layer", name="Never Created UV", require_nonempty=True, require_finite=True)], expected="fail", categories=("rejection", "uv")),
    task("22_inconclusive_constrained_animation", "An unsupported constrained animation cannot silently pass.",
         [primitive(), action("constraint_add", "$mesh", type="LIMIT_LOCATION", name="QA Unsupported Output", settings={}),
          action("keyframe_insert", "$mesh", property="location", value=[0, 0, 0], frame=1),
          action("keyframe_insert", "$mesh", property="location", value=[1, 2, 3], frame=20)],
         [check("animation", property="location", samples=[{"frame": 20, "expected": [1, 2, 3]}], tolerance=0.00001, require_keyframes=True)], expected="inconclusive", categories=("rejection", "animation")),
    task("23_camera_aim_and_reproject", "Create an aimed perspective camera, then configure it as orthographic.",
         [action("camera_create", name="QA Camera", alias="camera", location=[6, -6, 4], look_at=[0, 0, 1],
                 settings={"lens": 35, "clip_start": 0.05, "clip_end": 250}),
          action("camera_configure", "$camera", settings={"projection": "ORTHO", "ortho_scale": 12, "sensor_fit": "VERTICAL", "sensor_height": 30})],
         [check("object_type", "$camera", expected="CAMERA"),
          check("camera_settings", "$camera", values={"projection": "ORTHO", "ortho_scale": 12, "lens": 35, "sensor_fit": "VERTICAL",
                                                     "sensor_height": 30, "clip_start": 0.05, "clip_end": 250}, tolerance=0.00001),
          check("aim", "$camera", point=[0, 0, 1], tolerance=0.0001),
          {"kind": "object_count", "minimum": 1, "maximum": 1, "types": ["CAMERA"]}], categories=("camera", "aim")),
    task("24_area_key_light", "Create a rectangular area key light aimed at the origin.",
         [action("light_create", name="QA Key Light", alias="key", location=[4, -3, 5], look_at=[0, 0, 0],
                 settings={"light_type": "AREA", "shape": "RECTANGLE", "size": 2, "size_y": 0.5, "energy": 500, "color": [1, 0.9, 0.8]})],
         [check("light_settings", "$key", values={"light_type": "AREA", "shape": "RECTANGLE", "size": 2, "size_y": 0.5, "energy": 500,
                                                 "color": [1, 0.9, 0.8], "use_shadow": True}, tolerance=0.00001),
          check("aim", "$key", point=[0, 0, 0], tolerance=0.0001)], categories=("light", "aim")),
    task("25_point_to_spot_configure", "Convert a new point light into an aimed spot light with an explicit cone.",
         [action("light_create", name="QA Lamp", alias="lamp", location=[0, 0, 4], settings={"energy": 100}),
          action("light_configure", "$lamp", look_at=[1, 0, 0],
                 settings={"light_type": "SPOT", "spot_size": 0.6, "spot_blend": 0.3, "radius": 0.25})],
         [check("light_settings", "$lamp", values={"light_type": "SPOT", "spot_size": 0.6, "spot_blend": 0.3, "radius": 0.25, "energy": 100},
                tolerance=0.00001),
          check("aim", "$lamp", point=[1, 0, 0], tolerance=0.0001)], categories=("light", "aim")),
    task("26_reject_wrong_light_energy", "A light whose energy misses the requested value must not commit.",
         [action("light_create", name="QA Dim Light", alias="dim", settings={"light_type": "SUN", "energy": 2})],
         [check("light_settings", "$dim", values={"energy": 5}, tolerance=0.00001)], expected="fail", categories=("rejection", "light")),
]


def fixtures():
    """Independent canonical plans, validated through the installed host schemas."""
    from angelo_agent.program_spec import validate_program
    result = copy.deepcopy(TASK_FIXTURES)
    for fixture in result:
        fixture["program"] = validate_program("angelo_scene_program", fixture["program"])
    return result
