# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reference results of the request survey's graded edits, built with plain ``bpy`` (no agent, no model).

``builders(bpy, scene)`` maps the id of every survey edit that has checks to a
function that leaves the scene as a correct answer to that request would, in
survey order on the factory scene. ``test_blender_survey_tasks.py`` walks them
through the trial driver's own grading steps to prove that each task's checks
fail before its edit and pass after it. The builds are one valid construction
each, not the only one the checks accept.

``near_misses(bpy, scene)`` maps some of those ids to results that look like
the request and are not it (four copies for five, a hole along the other axis,
an orbit that ends half-way through): each build returns the function that
undoes it, and each must fail.
"""

import math

from eval_live_tasks import COPY_OBJECT, EVAL_OBJECT, RIG_OBJECT, SKETCH_OBJECT

FACTORY_CAMERA, FACTORY_LIGHT = "Camera", "Light"
SPHERE, GROUND, CLOTH = "Sphere", "Plane", "Cloth"


def builders(bpy, scene):
    return _builds(bpy, scene)[0]


def near_misses(bpy, scene):
    return _builds(bpy, scene)[1]


def _builds(bpy, scene):
    import bmesh
    from mathutils import Vector

    def link(obj, location=(0, 0, 0)):
        obj.location = location
        scene.collection.objects.link(obj)
        return obj

    def mesh_object(name, build_geometry, location=(0, 0, 0)):
        mesh, geometry = bpy.data.meshes.new(name), bmesh.new()
        try:
            build_geometry(geometry)
            geometry.to_mesh(mesh)
        finally:
            geometry.free()
        return link(bpy.data.objects.new(name, mesh), location)

    def principled(name, color, roughness, metallic):
        material = bpy.data.materials.new(name)
        if material.node_tree is None:  # New materials have a node tree in this Blender; older ones need use_nodes.
            material.use_nodes = True
        set_principled(material, color, roughness, metallic)
        return material

    def bsdf(material):
        return next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")

    def set_principled(material, color, roughness, metallic):
        node = bsdf(material)
        node.inputs["Base Color"].default_value = color
        node.inputs["Roughness"].default_value = roughness
        node.inputs["Metallic"].default_value = metallic

    def light_object(name, kind, location, rotation=(0, 0, 0)):
        obj = link(bpy.data.objects.new(name, bpy.data.lights.new(name, kind)), location)
        obj.rotation_euler = rotation
        return obj

    def aim(obj, point):
        obj.rotation_euler = (Vector(point) - obj.location).to_track_quat("-Z", "Y").to_euler()

    def eval_cube():
        return scene.objects[EVAL_OBJECT]

    def set_values(owner, values):
        for path, value in values.items():
            holder, _, name = path.rpartition(".")
            setattr(owner.path_resolve(holder) if holder else owner, name, value)

    def linear_keys(owner):
        from bpy_extras import anim_utils
        data = owner.animation_data
        for curve in anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot).fcurves:
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"

    def world_box(obj):
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        return [evaluated.matrix_world @ Vector(corner) for corner in evaluated.bound_box]

    def extrude_top():
        mesh = eval_cube().data
        geometry = bmesh.new()
        try:
            geometry.from_mesh(mesh)
            top = max(geometry.faces, key=lambda face: face.calc_center_median().z)
            extruded = bmesh.ops.extrude_face_region(geometry, geom=[top])
            bmesh.ops.delete(geometry, geom=[top], context="FACES")
            moved = [element for element in extruded["geom"] if isinstance(element, bmesh.types.BMVert)]
            bmesh.ops.translate(geometry, vec=(0, 0, 1), verts=moved)
            geometry.to_mesh(mesh)
        finally:
            geometry.free()

    def top_face_trim():
        obj = eval_cube()
        obj.data.materials.append(principled("Trim", (1, 0.77, 0.34, 1), 0.3, 1))
        top = max(obj.data.polygons, key=lambda face: face.center.z)
        top.material_index = len(obj.data.materials) - 1

    def glass_sphere():
        material = principled("Glass", (1, 1, 1, 1), 0.0, 0)
        bsdf(material).inputs["Transmission Weight"].default_value = 1.0
        scene.objects[SPHERE].data.materials.append(material)

    def vertex_colors():
        mesh = eval_cube().data
        colors = mesh.color_attributes.new("Paint", "FLOAT_COLOR", "POINT")
        top = max(vertex.co.z for vertex in mesh.vertices)
        for vertex, item in zip(mesh.vertices, colors.data):
            item.color = (1, 0.9, 0, 1) if vertex.co.z >= top - 1e-4 else (1, 1, 1, 1)

    def donut():
        major, minor = 24, 12
        vertices = [((1.0 + 0.3 * math.cos(2 * math.pi * j / minor)) * math.cos(2 * math.pi * i / major),
                     (1.0 + 0.3 * math.cos(2 * math.pi * j / minor)) * math.sin(2 * math.pi * i / major),
                     0.3 * math.sin(2 * math.pi * j / minor)) for i in range(major) for j in range(minor)]
        faces = [(i * minor + j, ((i + 1) % major) * minor + j, ((i + 1) % major) * minor + (j + 1) % minor,
                  i * minor + (j + 1) % minor) for i in range(major) for j in range(minor)]
        mesh = bpy.data.meshes.new("Torus")
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        link(bpy.data.objects.new("Torus", mesh), (6, 0, 0.3))

    def duplicate_copy():
        original = eval_cube()
        duplicate = original.copy()
        duplicate.data = original.data.copy()
        duplicate.name = COPY_OBJECT
        link(duplicate, (3, 0, 0))

    def join_copy():
        target, source = eval_cube(), scene.objects[COPY_OBJECT]
        with bpy.context.temp_override(active_object=target, object=target, selected_objects=[target, source],
                                       selected_editable_objects=[target, source]):
            bpy.ops.object.join()

    def remove_object(obj):
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and not data.users:
            bpy.data.batch_remove([data])

    def drop_animation(owner):
        action = owner.animation_data.action
        owner.animation_data_clear()
        bpy.data.actions.remove(action)

    def array(count, spacing):
        """An Array modifier on the sphere; returns the function that removes it again."""
        sphere = scene.objects[SPHERE]
        name = sphere.modifiers.new("Array", "ARRAY").name
        set_values(sphere.modifiers[name], {"count": count, "use_relative_offset": False,
                                            "use_constant_offset": True, "constant_offset_displace": (spacing, 0, 0)})
        return lambda: sphere.modifiers.remove(sphere.modifiers[name])

    def hole(rotation, depth=10, location=(0, 0, 0.5)):
        """A round cut into Eval Cube along the rotated cylinder's axis; returns the function that undoes it."""
        cutter = mesh_object("Hole Cutter", lambda g: bmesh.ops.create_cone(
            g, cap_ends=True, segments=32, radius1=0.3, radius2=0.3, depth=depth), location)
        cutter.rotation_euler = rotation
        cutter.hide_viewport = cutter.hide_render = True
        name = eval_cube().modifiers.new("Boolean", "BOOLEAN").name
        set_values(eval_cube().modifiers[name], {"operation": "DIFFERENCE", "object": cutter, "solver": "EXACT"})
        return lambda: (eval_cube().modifiers.remove(eval_cube().modifiers[name]), remove_object(cutter))

    def apply_modifiers():
        obj = eval_cube()
        mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(bpy.context.evaluated_depsgraph_get()))
        obj.modifiers.clear()
        obj.data = mesh

    def lights_collection():
        light = scene.objects[FACTORY_LIGHT]
        lighting = bpy.data.collections.new("Lighting")
        scene.collection.children.link(lighting)
        for collection in list(light.users_collection):
            collection.objects.unlink(light)
        lighting.objects.link(light)

    def world_grey():
        background = next(node for node in scene.world.node_tree.nodes if node.type == "BACKGROUND")
        background.inputs["Color"].default_value = (0.05, 0.05, 0.05, 1)

    def camera_move_aim():
        camera = scene.objects[FACTORY_CAMERA]
        camera.location = (0, -12, 3)
        aim(camera, (0, 0, 0))

    def frame_object():
        camera = scene.objects[FACTORY_CAMERA]
        coordinates = [value for corner in world_box(eval_cube()) for value in corner]
        camera.location = camera.camera_fit_coords(bpy.context.evaluated_depsgraph_get(), coordinates)[0]

    def top_camera_active():
        camera = link(bpy.data.objects.new("Top Camera", bpy.data.cameras.new("Top Camera")), (0, 0, 20))
        scene.camera = camera

    def spin_turntable():
        obj = eval_cube()
        for frame, angle in ((1, 0.), (120, 2 * math.pi)):
            obj.rotation_euler.z = angle
            obj.keyframe_insert("rotation_euler", index=2, frame=frame)
        linear_keys(obj)

    def hops(*frames):
        """The sphere leaves the ground and lands again between each pair of ``frames``; returns the undo."""
        sphere = scene.objects[SPHERE]
        ground = sphere.location.z
        tops = [(first + second) // 2 for first, second in zip(frames, frames[1:])]
        for frame, lift in sorted([(frame, 0.0) for frame in frames] + [(frame, 1.0) for frame in tops]):
            sphere.location.z = ground + lift
            sphere.keyframe_insert("location", index=2, frame=frame)
        sphere.location.z = ground
        return lambda: (drop_animation(sphere), setattr(sphere.location, "z", ground))

    def orbit(*keys):
        """The camera turns about Eval Cube: ``keys`` are (frame, turns made so far); returns the undo."""
        camera = scene.objects[FACTORY_CAMERA]
        pivot = link(bpy.data.objects.new("Orbit Pivot", None), tuple(eval_cube().matrix_world.translation))
        bpy.context.view_layer.update()
        placed = camera.matrix_world.copy()
        camera.parent, camera.matrix_parent_inverse = pivot, pivot.matrix_world.inverted()
        for frame, turns in keys:
            pivot.rotation_euler.z = 2 * math.pi * turns
            pivot.keyframe_insert("rotation_euler", index=2, frame=frame)
        linear_keys(pivot)

        def undo():
            camera.parent = None
            camera.matrix_world = placed
            drop_animation(pivot)
            remove_object(pivot)
        return undo

    def procedural_wood():
        material = principled("Wood", (0.35, 0.2, 0.1, 1), 0.6, 0)
        nodes, links = material.node_tree.nodes, material.node_tree.links
        wave, ramp = nodes.new("ShaderNodeTexWave"), nodes.new("ShaderNodeValToRGB")
        for stop, color in zip(ramp.color_ramp.elements, ((0.25, 0.13, 0.06, 1), (0.5, 0.3, 0.15, 1))):
            stop.color = color
        links.new(wave.outputs["Fac"], ramp.inputs["Fac"])
        links.new(ramp.outputs["Color"], bsdf(material).inputs["Base Color"])
        scene.objects[GROUND].data.materials.append(material)

    def scatter_rocks():
        group = bpy.data.node_groups.new("Rocks", "GeometryNodeTree")
        group.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        new = lambda kind: group.nodes.new(kind)
        source, output, join = new("NodeGroupInput"), new("NodeGroupOutput"), new("GeometryNodeJoinGeometry")
        points, rock = new("GeometryNodeDistributePointsOnFaces"), new("GeometryNodeMeshIcoSphere")
        instances = new("GeometryNodeInstanceOnPoints")
        points.inputs["Density"].default_value = 0.5   # 400 square metres: about 200 points.
        rock.inputs["Radius"].default_value = 0.1
        for start, end in ((source.outputs[0], points.inputs["Mesh"]),
                           (points.outputs["Points"], instances.inputs["Points"]),
                           (rock.outputs["Mesh"], instances.inputs["Instance"]), (source.outputs[0], join.inputs[0]),
                           (instances.outputs["Instances"], join.inputs[0]), (join.outputs[0], output.inputs[0])):
            group.links.new(start, end)
        scene.objects[GROUND].modifiers.new("Rocks", "NODES").node_group = group

    def scatter_density():
        group = scene.objects[GROUND].modifiers["Rocks"].node_group
        points = next(node for node in group.nodes if node.bl_idname == "GeometryNodeDistributePointsOnFaces")
        points.inputs["Density"].default_value = 1.25
        scene.objects[GROUND].update_tag()

    def rig_chain():
        data = bpy.data.armatures.new(RIG_OBJECT)
        rig = link(bpy.data.objects.new(RIG_OBJECT, data))
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode="EDIT")
        parent = None
        for index in range(3):
            bone = data.edit_bones.new("Bone %d" % (index + 1))
            bone.head, bone.tail = (0, 4, index), (0, 4, index + 1)
            if parent is not None:
                bone.parent, bone.use_connect = parent, True
            parent = bone
        bpy.ops.object.mode_set(mode="OBJECT")

    def auto_weights():
        sphere, rig = scene.objects[SPHERE], scene.objects[RIG_OBJECT]
        every = [vertex.index for vertex in sphere.data.vertices]
        for bone in rig.data.bones:
            sphere.vertex_groups.new(name=bone.name).add(every, 1.0 / len(rig.data.bones), "REPLACE")
        set_values(sphere.modifiers.new("Armature", "ARMATURE"), {"object": rig, "use_vertex_groups": True})

    def gp_smiley():
        sketch = bpy.data.objects.new(SKETCH_OBJECT, bpy.data.grease_pencils.new(SKETCH_OBJECT))
        sketch.data.layers.new("Layer")
        link(sketch)

    def cloth_drape():
        box = world_box(eval_cube())
        centre = [sum(corner[axis] for corner in box) / len(box) for axis in range(2)]
        cloth = mesh_object(CLOTH, lambda g: bmesh.ops.create_grid(g, x_segments=10, y_segments=10, size=3),
                            (centre[0], centre[1], max(corner.z for corner in box) + 1.0))
        cloth.modifiers.new("Cloth", "CLOTH")
        eval_cube().modifiers.new("Collision", "COLLISION")

    def bake_sim():
        cloth = scene.objects[CLOTH]
        cache = cloth.modifiers["Cloth"].point_cache
        cache.frame_start, cache.frame_end = 1, 100
        frame = scene.frame_current
        with bpy.context.temp_override(scene=scene, active_object=cloth, object=cloth, point_cache=cache):
            bpy.ops.ptcache.bake(bake=True)
        scene.frame_set(frame)

    middle = lambda: (scene.frame_start + scene.frame_end) // 2
    near = {
        "array_row": {"four copies": lambda: array(4, 1.5), "five copies 1 m apart": lambda: array(5, 1.0)},
        "boolean_hole": {"a hole from side to side": lambda: hole((0, math.pi / 2, 0)),
                         "a pocket that stops inside": lambda: hole((math.pi / 2, 0, 0), 1.0, (0, -1, 0.5))},
        "bounce": {"three bounces": lambda: hops(1, 17, 33, 48), "one bounce": lambda: hops(1, 48)},
        "camera_orbit": {"a turn that ends half-way through": lambda: orbit(
            (scene.frame_start, 0.0), (middle(), 1.0), (scene.frame_end, 1.0)),
            "half a turn": lambda: orbit((scene.frame_start, 0.0), (scene.frame_end, 0.5))},
    }
    return {
        "create_cube": lambda: mesh_object(EVAL_OBJECT, lambda g: bmesh.ops.create_cube(g, size=2.0)),
        "delete_default_cube": lambda: bpy.data.objects.remove(scene.objects["Cube"]),
        "extrude_top": extrude_top,
        "sphere_on_top": lambda: mesh_object(SPHERE, lambda g: bmesh.ops.create_uvsphere(
            g, u_segments=32, v_segments=16, radius=0.5), (0, 0, 2.5)),
        "ground_plane": lambda: mesh_object(GROUND, lambda g: bmesh.ops.create_grid(
            g, x_segments=1, y_segments=1, size=10), (0, 0, -1)),
        "red_plastic": lambda: eval_cube().data.materials.append(
            principled("Red Plastic", (0.8, 0.02, 0.02, 1), 0.15, 0)),
        "recolor_material": lambda: set_principled(bpy.data.materials["Red Plastic"], (0.05, 0.1, 0.4, 1), 0.2, 0),
        "top_face_trim": top_face_trim,
        "glass_sphere": glass_sphere,
        "vertex_colors": vertex_colors,
        "text_hello": lambda: setattr(link(bpy.data.objects.new("Text", bpy.data.curves.new("Text", "FONT")),
                                           (0, -2, 0)).data, "extrude", 0.05),
        "table": lambda: mesh_object("Table Top", lambda g: bmesh.ops.create_cube(g, size=1.0), (4, 0, 0.75)),
        "donut": donut,
        "duplicate_copy": duplicate_copy,
        "parent_copy": lambda: setattr(scene.objects[COPY_OBJECT], "parent", eval_cube()),
        "join_copy": join_copy,
        "bevel_modifier": lambda: set_values(eval_cube().modifiers.new("Bevel", "BEVEL"),
                                             {"width": 0.05, "segments": 3}),
        "subdivision": lambda: set_values(eval_cube().modifiers.new("Subdivision", "SUBSURF"),
                                          {"levels": 2, "render_levels": 3}),
        "array_row": lambda: array(5, 1.5),
        "boolean_hole": lambda: hole((math.pi / 2, 0, 0)),   # The cylinder's axis runs front to back.
        "apply_modifiers": apply_modifiers,
        "light_brighter": lambda: set_values(scene.objects[FACTORY_LIGHT].data,
                                             {"energy": 2000., "color": (1, 0.85, 0.7)}),
        "soft_shadows": lambda: set_values(scene.objects[FACTORY_LIGHT].data, {"shadow_soft_size": 0.5}),
        "lights_collection": lights_collection,
        "sun_light": lambda: set_values(scene.objects[FACTORY_LIGHT], {"data.type": "SUN",
                                                                      "rotation_euler": (math.pi / 4, 0, 0)}),
        "spot_from_above": lambda: setattr(light_object("Spot", "SPOT", (0, 0, 6)).data, "spot_size", math.radians(30)),
        "three_point": lambda: [light_object(name, "AREA", location) for name, location in (
            ("Key", (4, -4, 4)), ("Fill", (-4, -4, 3)), ("Rim", (0, 5, 4)))],
        "world_grey": world_grey,
        "camera_move_aim": camera_move_aim,
        "lens_35": lambda: set_values(scene.objects[FACTORY_CAMERA].data, {"lens": 35.}),
        "dof": lambda: set_values(scene.objects[FACTORY_CAMERA].data, {
            "dof.use_dof": True, "dof.aperture_fstop": 1.8, "dof.focus_object": eval_cube()}),
        "frame_object": frame_object,
        "top_camera_active": top_camera_active,
        "vertical_resolution": lambda: set_values(scene, {"render.resolution_x": 1080, "render.resolution_y": 1920,
                                                          "render.resolution_percentage": 100}),
        "color_management": lambda: set_values(scene, {
            "view_settings.view_transform": "AgX", "view_settings.look": "AgX - Punchy",
            "view_settings.exposure": 0.5}),
        "eevee_quality": lambda: set_values(scene, {"render.use_motion_blur": True, "eevee.taa_render_samples": 128}),
        "spin_turntable": spin_turntable,
        "bounce": lambda: hops(1, 24, 48),
        "timeline": lambda: set_values(scene, {"render.fps": 30, "frame_end": 300}),
        "marker": lambda: scene.timeline_markers.new("Impact", frame=60),
        "camera_orbit": lambda: orbit((scene.frame_start, 0.0), (scene.frame_end, 1.0)),
        "lightmap_uv": lambda: eval_cube().data.uv_layers.new(name="Lightmap"),
        "procedural_wood": procedural_wood,
        "scatter_rocks": scatter_rocks,
        "scatter_density": scatter_density,
        "rig_chain": rig_chain,
        "auto_weights": auto_weights,
        "gp_smiley": gp_smiley,
        "cloth_drape": cloth_drape,
        "bake_sim": bake_sim,
        "purge_unused": lambda: bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True),
    }, near
