# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""First-hour problems: a beginner stuck on something Blender does, in one message, graded by closed checks.

Task sets ``firsthour-dev``, ``firsthour-heldout`` and ``firsthour`` (both) of ``eval_live_agent.py``
(doc/angelo/learn_mode.rst, "First-hour help"). Each case is a real kind of first-hour failure taken from a public
source (``SOURCE``): a forum thread, a Q&A site, Blender's tracker or a guide. The harness prepares the state the person
is in (``prepare``: objects, the mode, tool settings, and the report Blender showed, written to the window's report
list as Blender writes one), sends the person's message once, and grades the scene afterwards (``grade``): a check
reads whether the cause is gone or the person's goal is met, by any valid route (each check lists the routes it
accepts). Nothing here is shown to the model, and no model judges a result.

* **dev**: the cases the fix is developed on (their transcripts may be read): the two problems raised under Blender
  5.3's announcement (an error on a modest laptop, a bone weight that won't paint to zero) and two more.
* **heldout**: measured only. Their transcripts are not read while the fix is developed, and no case is added, removed
  or changed after the before-run (an error in a case found later is fixed for every side and both runs re-graded,
  and reported).

The messages are paraphrased in a beginner's words, never quoted; each says what the person wants, never how. The
module is dependency-free (``prepare``/``grade`` take ``bpy``), so host tests can read the registry:
test_native_firsthour_tasks.py. ``test_blender_firsthour_cases.py`` proves in Blender that each prepared state fails
its checks and each reference fix (``reference_fix``) passes them.
"""

DEV, HELDOUT = "dev", "heldout"
GROUPS = ("firsthour-dev", "firsthour-heldout", "firsthour")   # Runner groups (firsthour_run.py), not task sets.
MESH = "Rock"


def _case(case_id, split, source, prompt, prepare, grade, reference_fix, why):
    return {"id": "firsthour_" + case_id, "split": split, "source": source, "prompt": prompt,
            "prepare": prepare, "grade": grade, "reference_fix": reference_fix, "why": why}


# -- shared fixture pieces -------------------------------------------------------------------------------------------

def _view3d(bpy):
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == 'VIEW_3D')
    return window, area, next(r for r in area.regions if r.type == 'WINDOW')


def _override(bpy):
    window, area, region = _view3d(bpy)
    return bpy.context.temp_override(window=window, area=area, region=region)


def _mode(bpy, obj, mode):
    with _override(bpy):
        bpy.context.view_layer.objects.active = obj
        for other in bpy.context.view_layer.objects:
            other.select_set(other is obj)
        bpy.ops.object.mode_set(mode=mode)


def _object_mode(bpy):
    with _override(bpy):
        if bpy.context.object is not None and bpy.context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')


def report(bpy, kind, text):
    """Blender's report as the person saw it: in the window's report list (the status bar and Info log)."""
    import eval_reports
    eval_reports.add(bpy, kind, text)


def _rock(bpy, name=MESH, size=1.0, location=(0.0, 0.0, 1.0)):
    import bmesh
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=size)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _rigged_arm(bpy, groups=("Upper",)):
    """A column mesh parented to an armature with one bone per group, weights 1.0 everywhere."""
    import bmesh
    arm_data = bpy.data.armatures.new("Rig")
    rig = bpy.data.objects.new("Rig", arm_data)
    bpy.context.scene.collection.objects.link(rig)
    with _override(bpy):
        bpy.context.view_layer.objects.active = rig
        bpy.ops.object.mode_set(mode='EDIT')
        for index, name in enumerate(groups):
            bone = arm_data.edit_bones.new(name)
            bone.head, bone.tail = (0.0, 0.0, index * 1.0), (0.0, 0.0, index * 1.0 + 1.0)
        bpy.ops.object.mode_set(mode='OBJECT')
    mesh = bpy.data.meshes.new("Arm")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=12, radius1=0.3, radius2=0.3, depth=len(groups))
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0.0, 0.0, len(groups) / 2))
    bm.to_mesh(mesh)
    bm.free()
    arm = bpy.data.objects.new("Arm", mesh)
    bpy.context.scene.collection.objects.link(arm)
    arm.parent = rig
    arm.modifiers.new("Armature", 'ARMATURE').object = rig
    for name in groups:
        arm.vertex_groups.new(name=name).add(range(len(mesh.vertices)), 1.0, 'REPLACE')
    return rig, arm


# -- dev -------------------------------------------------------------------------------------------------------------

def _prep_autonormalize(bpy):
    _rig, arm = _rigged_arm(bpy, ("Upper",))
    _mode(bpy, arm, 'WEIGHT_PAINT')
    settings = bpy.context.scene.tool_settings
    settings.use_auto_normalize = True
    settings.weight_paint.unified_paint_settings.weight = 0.0   # Per paint mode since Blender 5.0.


def _grade_autonormalize(bpy):
    arm = bpy.data.objects["Arm"]
    deform = [g for g in arm.vertex_groups if bpy.data.objects["Rig"].data.bones.get(g.name)]
    return {"painting_toward_zero_is_not_pushed_back": (not bpy.context.scene.tool_settings.use_auto_normalize
                                                        or len(deform) >= 2),
            "the_arm_keeps_its_rig": arm.parent is not None and any(m.type == 'ARMATURE' for m in arm.modifiers)}


def _fix_autonormalize(bpy):
    bpy.context.scene.tool_settings.use_auto_normalize = False


def _prep_gpu_memory(bpy):
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'GPU'
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 3840, 2160, 200
    scene.cycles.texture_limit_render = 'OFF'
    report(bpy, 'ERROR', "System is out of GPU memory")


def _grade_gpu_memory(bpy):
    """Any of: render on the CPU, a smaller image (at most 4K at 100%), a texture limit, or another engine."""
    scene = bpy.context.scene
    pixels = scene.render.resolution_x * scene.render.resolution_y * (scene.render.resolution_percentage / 100) ** 2
    return {"the_render_fits_a_small_gpu": (scene.render.engine != 'CYCLES' or scene.cycles.device == 'CPU'
                                            or pixels <= 3840 * 2160 or scene.cycles.texture_limit_render != 'OFF')}


def _fix_gpu_memory(bpy):
    bpy.context.scene.render.resolution_percentage = 100


def _prep_camera_active(bpy):
    _rock(bpy)
    camera = bpy.data.objects["Camera"]
    _mode(bpy, camera, 'OBJECT')


def _grade_camera_active(bpy):
    """The rock is the active object (in Edit Mode already, or ready for the person's Tab)."""
    obj = bpy.context.view_layer.objects.active
    return {"the_rock_is_ready_to_edit": obj is not None and obj.name == MESH and obj.type == 'MESH'}


def _fix_camera_active(bpy):
    _mode(bpy, bpy.data.objects[MESH], 'EDIT')


def _prep_hidden(bpy):
    _rock(bpy).hide_set(True)


def _grade_hidden(bpy):
    obj = bpy.data.objects.get(MESH)
    return {"the_rock_shows_again": obj is not None and obj.visible_get()}


def _fix_hidden(bpy):
    bpy.data.objects[MESH].hide_set(False)


# -- held out --------------------------------------------------------------------------------------------------------

def _prep_locked_group(bpy):
    _rig, arm = _rigged_arm(bpy, ("Upper", "Lower"))
    arm.vertex_groups["Upper"].lock_weight = True
    arm.vertex_groups.active_index = arm.vertex_groups["Upper"].index
    _mode(bpy, arm, 'WEIGHT_PAINT')


def _grade_locked_group(bpy):
    return {"the_group_can_be_painted": not bpy.data.objects["Arm"].vertex_groups["Upper"].lock_weight}


def _fix_locked_group(bpy):
    bpy.data.objects["Arm"].vertex_groups["Upper"].lock_weight = False


def _prep_face_select(bpy):
    import bmesh
    mesh = bpy.data.meshes.new("Path")
    bm = bmesh.new()
    bmesh.ops.create_circle(bm, segments=16, radius=1.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Path", mesh)
    bpy.context.scene.collection.objects.link(obj)
    _mode(bpy, obj, 'EDIT')
    bpy.context.scene.tool_settings.mesh_select_mode = (False, False, True)


def _grade_face_select(bpy):
    mode = tuple(bpy.context.scene.tool_settings.mesh_select_mode)
    return {"points_or_edges_can_be_picked": mode[0] or mode[1]}


def _fix_face_select(bpy):
    bpy.context.scene.tool_settings.mesh_select_mode = (True, False, False)


def _prep_unselectable(bpy):
    _rock(bpy).hide_select = True


def _grade_unselectable(bpy):
    return {"the_rock_can_be_clicked": not bpy.data.objects[MESH].hide_select}


def _fix_unselectable(bpy):
    bpy.data.objects[MESH].hide_select = False


def _prep_flipped(bpy):
    obj = _rock(bpy)
    obj.data.flip_normals()


def _outward_share(obj):
    from mathutils import Vector
    centre = sum((v.co for v in obj.data.vertices), Vector()) / len(obj.data.vertices)
    outward = sum(1 for p in obj.data.polygons if p.normal.dot(p.center - centre) > 0)
    return outward / max(1, len(obj.data.polygons))


def _grade_flipped(bpy):
    obj = bpy.data.objects.get(MESH)
    return {"faces_point_outward": obj is not None and _outward_share(obj) > 0.95}


def _fix_flipped(bpy):
    bpy.data.objects[MESH].data.flip_normals()


def _prep_unapplied_scale(bpy):
    with _override(bpy):
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0.0, 0.0, 0.5))
    obj = bpy.context.object
    obj.name = "Plank"
    obj.scale = (4.0, 1.0, 0.25)
    bevel = obj.modifiers.new("Bevel", 'BEVEL')
    bevel.width, bevel.segments = 0.05, 3
    bpy.data.objects.remove(bpy.data.objects["Cube"], do_unlink=True)


def _grade_unapplied_scale(bpy):
    """Even: the bevel works on unscaled geometry (scale applied, or the bevel itself kept on a uniform object)."""
    obj = bpy.data.objects.get("Plank")
    if obj is None:
        return {"the_bevel_is_even": False, "the_plank_keeps_its_size": False}
    uniform = max(obj.scale) - min(obj.scale) < 1e-3
    beveled = any(m.type == 'BEVEL' for m in obj.modifiers) or len(obj.data.vertices) > 8   # Or applied.
    return {"the_bevel_is_even": uniform and beveled,
            "the_plank_keeps_its_size": all(abs(a - b) < 0.02 for a, b in zip(obj.dimensions, (4.0, 1.0, 0.25)))}


def _fix_unapplied_scale(bpy):
    obj = bpy.data.objects["Plank"]
    with _override(bpy):
        bpy.context.view_layer.objects.active = obj
        obj.select_set(True)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)


def _prep_black_render(bpy):
    for name in ("Light",):
        if bpy.data.objects.get(name):
            bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)
    world = bpy.context.scene.world
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs["Strength"].default_value = 0.0


def _grade_black_render(bpy):
    scene = bpy.context.scene
    lights = [o for o in scene.objects if o.type == 'LIGHT' and o.data.energy > 0 and not o.hide_render]
    strength = 0.0
    if scene.world and scene.world.use_nodes:
        strength = max((n.inputs["Strength"].default_value for n in scene.world.node_tree.nodes
                        if n.type == 'BACKGROUND'), default=0.0)
    emissive = any(n.type == 'EMISSION' or (n.type == 'BSDF_PRINCIPLED' and n.inputs["Emission Strength"].default_value
                                            > 0) for m in bpy.data.materials if m.use_nodes and m.users
                   for n in m.node_tree.nodes)
    return {"something_lights_the_scene": bool(lights) or strength > 0 or emissive}


def _fix_black_render(bpy):
    bpy.context.scene.world.node_tree.nodes["Background"].inputs["Strength"].default_value = 1.0


def _prep_no_camera(bpy):
    bpy.data.objects.remove(bpy.data.objects["Camera"], do_unlink=True)
    report(bpy, 'ERROR', "No camera found in scene")


def _grade_no_camera(bpy):
    camera = bpy.context.scene.camera
    return {"the_scene_has_a_camera": camera is not None and camera.type == 'CAMERA'}


def _fix_no_camera(bpy):
    data = bpy.data.cameras.new("Camera")
    obj = bpy.data.objects.new("Camera", data)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.scene.camera = obj


def _prep_texture_unseen(bpy):
    obj = bpy.data.objects["Cube"]
    material = bpy.data.materials.new("Checker")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    checker = nodes.new('ShaderNodeTexChecker')
    material.node_tree.links.new(checker.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.clear()
    obj.data.materials.append(material)
    _window, area, _region = _view3d(bpy)
    area.spaces.active.shading.type = 'SOLID'
    area.spaces.active.shading.color_type = 'MATERIAL'


def _grade_texture_unseen(bpy):
    _window, area, _region = _view3d(bpy)
    shading = area.spaces.active.shading
    return {"the_view_shows_textures": shading.type in {'MATERIAL', 'RENDERED'}
            or (shading.type == 'SOLID' and shading.color_type == 'TEXTURE')}


def _fix_texture_unseen(bpy):
    _view3d(bpy)[1].spaces.active.shading.type = 'MATERIAL'


def _prep_proportional(bpy):
    obj = _rock(bpy)
    _mode(bpy, obj, 'EDIT')
    bpy.context.scene.tool_settings.use_proportional_edit = True


def _grade_proportional(bpy):
    return {"a_vertex_moves_alone": not bpy.context.scene.tool_settings.use_proportional_edit}


def _fix_proportional(bpy):
    bpy.context.scene.tool_settings.use_proportional_edit = False


def _prep_slow_render(bpy):
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples, scene.cycles.use_adaptive_sampling, scene.cycles.use_denoising = 4096, False, False
    scene.cycles.time_limit = 0.0


def _grade_slow_render(bpy):
    cycles = bpy.context.scene.cycles
    return {"the_render_does_far_less_work": (bpy.context.scene.render.engine != 'CYCLES' or cycles.samples <= 512
                                              or (cycles.use_adaptive_sampling and cycles.adaptive_threshold >= 0.05)
                                              or 0 < cycles.time_limit <= 120)}


def _fix_slow_render(bpy):
    bpy.context.scene.cycles.samples = 256


def _prep_mirror_origin(bpy):
    import bmesh
    mesh = bpy.data.meshes.new("Half")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.translate(bm, verts=bm.verts, vec=(0.5, 0.0, 0.0))   # The half's seam lies on its own local X = 0.
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Half", mesh)
    obj.location = (3.0, 0.0, 0.5)
    bpy.context.scene.collection.objects.link(obj)
    # The origin moved away from the seam (a common slip): the mirror then lands far from the half.
    from mathutils import Matrix
    mesh.transform(Matrix.Translation((2.0, 0.0, 0.0)))
    obj.location.x -= 2.0
    obj.modifiers.new("Mirror", 'MIRROR')


def _grade_mirror_origin(bpy):
    obj = bpy.data.objects.get("Half")
    if obj is None:
        return {"the_two_halves_meet": False}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    xs = sorted({round((evaluated.matrix_world @ v.co).x, 3) for v in evaluated.data.vertices})
    width = xs[-1] - xs[0] if xs else 0.0
    return {"the_two_halves_meet": abs(width - 2.0) < 0.02}


def _fix_mirror_origin(bpy):
    from mathutils import Matrix
    obj = bpy.data.objects["Half"]
    obj.data.transform(Matrix.Translation((-2.0, 0.0, 0.0)))
    obj.location.x += 2.0


def _prep_not_in_render(bpy):
    _rock(bpy).hide_render = True


def _grade_not_in_render(bpy):
    obj = bpy.data.objects.get(MESH)
    if obj is None:
        return {"the_rock_renders": False}
    collections = [c for c in bpy.data.collections if obj.name in c.objects]
    return {"the_rock_renders": not obj.hide_render and not any(c.hide_render for c in collections)}


def _fix_not_in_render(bpy):
    bpy.data.objects[MESH].hide_render = False


def _prep_missing_texture(bpy):
    obj = _rock(bpy, "Trunk")
    material = bpy.data.materials.new("Bark")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    image = bpy.data.images.new("bark_diffuse.png", 4, 4)
    image.source, image.filepath = 'FILE', "//textures/bark_diffuse.png"   # Moved away with the textures folder.
    node = nodes.new('ShaderNodeTexImage')
    node.image = image
    material.node_tree.links.new(node.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(material)


def _missing_images(bpy):
    import os
    used = set()
    for obj in bpy.context.scene.objects:
        for slot in getattr(obj, "material_slots", ()):
            tree = slot.material.node_tree if slot.material and slot.material.use_nodes else None
            for node in tree.nodes if tree else ():
                if node.type == 'TEX_IMAGE' and node.image is not None and any(
                        link.from_node == node for link in tree.links):
                    used.add(node.image)
    return [i for i in used if i.source == 'FILE' and not i.packed_file
            and not os.path.isfile(bpy.path.abspath(i.filepath))]


def _grade_missing_texture(bpy):
    obj = bpy.data.objects.get("Trunk")
    return {"no_missing_image_is_used": obj is not None and not _missing_images(bpy),
            "the_trunk_keeps_a_material": obj is not None and any(s.material for s in obj.material_slots)}


def _fix_missing_texture(bpy):
    tree = bpy.data.materials["Bark"].node_tree
    tree.nodes.remove(next(n for n in tree.nodes if n.type == 'TEX_IMAGE'))


def _prep_subsurf_ball(bpy):
    cube = bpy.data.objects["Cube"]
    modifier = cube.modifiers.new("Subdivision", 'SUBSURF')
    modifier.levels = modifier.render_levels = 2


def _grade_subsurf_ball(bpy):
    cube = bpy.data.objects.get("Cube")
    if cube is None:
        return {"still_a_box": False, "still_smoothed": False}
    evaluated = cube.evaluated_get(bpy.context.evaluated_depsgraph_get())
    corner = max(abs(c) for v in evaluated.data.vertices for c in v.co)
    return {"still_a_box": corner >= 0.95 and min(cube.dimensions) >= 1.9,
            "still_smoothed": len(evaluated.data.vertices) > 8}


def _fix_subsurf_ball(bpy):
    cube = bpy.data.objects["Cube"]
    bevel = cube.modifiers.new("Bevel", 'BEVEL')
    bevel.width, bevel.segments = 0.1, 2
    with _override(bpy):
        bpy.context.view_layer.objects.active = cube
        bpy.ops.object.modifier_move_to_index(modifier="Bevel", index=0)


def _prep_snapping(bpy):
    _rock(bpy)
    settings = bpy.context.scene.tool_settings
    settings.use_snap = True
    settings.snap_elements = {'INCREMENT'}


def _grade_snapping(bpy):
    return {"the_rock_moves_smoothly": not bpy.context.scene.tool_settings.use_snap}


def _fix_snapping(bpy):
    bpy.context.scene.tool_settings.use_snap = False


def _prep_xray(bpy):
    _view3d(bpy)[1].spaces.active.shading.show_xray = True


def _grade_xray(bpy):
    shading = _view3d(bpy)[1].spaces.active.shading
    xray = shading.show_xray_wireframe if shading.type == 'WIREFRAME' else shading.show_xray
    return {"the_view_is_solid_again": shading.type != 'WIREFRAME' and not xray}


def _fix_xray(bpy):
    _view3d(bpy)[1].spaces.active.shading.show_xray = False


CASES = (
    _case("weight_zero", DEV, "x.com/Blender/status/2120186918793072864 (5.3 launch replies); blog.cgcookie.com "
          "(auto-normalize pushes a single group back up)",
          "I'm weight painting my arm. I set the weight to 0 to take the influence off, but painting does nothing, it "
          "stays red. How do I paint it down to zero?",
          _prep_autonormalize, _grade_autonormalize, _fix_autonormalize, "Auto Normalize with one deform group"),
    _case("laptop_gpu_error", DEV, "x.com/Blender/status/2120186918793072864 (5.3 launch replies); "
          "projects.blender.org/infrastructure/blender-open-data/issues/97619; projects.blender.org/blender/blender/"
          "issues/121441",
          "I'm on a cheap laptop and when I press render I just get an error and nothing renders. What do I do?",
          _prep_gpu_memory, _grade_gpu_memory, _fix_gpu_memory, "a 4K x 200% Cycles render on a small GPU"),
    _case("tab_does_nothing", DEV, "blenderartists.org/t/stuck-in-object-mode/356572",
          "I want to edit the shape of my rock but pressing Tab does nothing.",
          _prep_camera_active, _grade_camera_active, _fix_camera_active, "the camera is the active object"),
    _case("object_vanished", DEV, "uhiyama-lab.com/en/notes/blender/common-mistakes-troubleshooting/",
          "My rock just disappeared from the viewport, I think I pressed something. How do I get it back?",
          _prep_hidden, _grade_hidden, _fix_hidden, "hidden with H"),
    _case("locked_group", HELDOUT, "developer.blender.org/T72524 (weight paint not adding or subtracting); "
          "blenderartists.org/t/weight-paint-glitch/674792",
          "Weight painting on the Upper group isn't changing anything no matter what weight I use. Lower works fine.",
          _prep_locked_group, _grade_locked_group, _fix_locked_group, "the group's weights are locked"),
    _case("cant_pick_points", HELDOUT, "blenderartists.org/t/problems-with-model-editing/647998",
          "I made a circle and I'm in edit mode but I can't click any of its points.",
          _prep_face_select, _grade_face_select, _fix_face_select, "face select on a mesh with no faces"),
    _case("cant_click_object", HELDOUT, "developer.blender.org/T67325 (unable to select object); "
          "redlib r/blender 'Why can't I click objects'",
          "Clicking on my rock doesn't select it, but I can click the other things.",
          _prep_unselectable, _grade_unselectable, _fix_unselectable, "selection turned off in the Outliner"),
    _case("dark_patches", HELDOUT, "blender.stackexchange.com (recalculate normals, a common answer)",
          "My rock looks weird and dark, like it's inside out, especially in the render.",
          _prep_flipped, _grade_flipped, _fix_flipped, "normals point inward"),
    _case("uneven_bevel", HELDOUT, "blender.stackexchange.com (apply scale before bevel, a common answer)",
          "The bevel on my plank is much thicker on some edges than others. I want the same rounded edge everywhere.",
          _prep_unapplied_scale, _grade_unapplied_scale, _fix_unapplied_scale, "object scale not applied"),
    _case("black_render", HELDOUT, "blenderartists.org/t/serious-issues-getting-started/455818",
          "Every render I make is completely black. The viewport shows my cube fine.",
          _prep_black_render, _grade_black_render, _fix_black_render, "no light and a black world"),
    _case("no_camera", HELDOUT, "projects.blender.org/blender/blender/issues/56244 (doesn't render)",
          "I pressed F12 and it won't render, it says something at the bottom and then nothing happens.",
          _prep_no_camera, _grade_no_camera, _fix_no_camera, "no scene camera"),
    _case("texture_not_showing", HELDOUT, "cgcookie.com community (texture not visible in the viewport)",
          "I added a checker texture to my cube's material but the cube still looks plain grey.",
          _prep_texture_unseen, _grade_texture_unseen, _fix_texture_unseen, "Solid shading shows no textures"),
    _case("everything_moves", HELDOUT, "uhiyama-lab.com/en/notes/blender/common-mistakes-troubleshooting/",
          "When I move one point of my rock a whole area moves with it. I only want to move that one point.",
          _prep_proportional, _grade_proportional, _fix_proportional, "Proportional Editing is on"),
    _case("render_forever", HELDOUT, "developer.blender.org/T40780 (memory issues on low-end hardware); "
          "blenderartists.org/t/question-from-a-beginner/322466",
          "Rendering one picture of the default cube takes forever on my old computer. Can it be faster?",
          _prep_slow_render, _grade_slow_render, _fix_slow_render, "4096 samples, no adaptive sampling or denoise"),
    _case("mirror_far_away", HELDOUT, "blender.stackexchange.com (mirror modifier and the object origin)",
          "I added a Mirror modifier to my half model but the copy shows up far away instead of joining it.",
          _prep_mirror_origin, _grade_mirror_origin, _fix_mirror_origin, "the origin is off the seam"),
    _case("not_in_render", HELDOUT, "blenderartists.org/t/object-does-not-appear-in-rendered-mode-or-render/1153652",
          "My rock is right there in the viewport but it's missing from the rendered picture.",
          _prep_not_in_render, _grade_not_in_render, _fix_not_in_render, "Disable in Renders is on"),
    _case("pink_texture", HELDOUT, "blenderartists.org/t/why-in-material-preview-is-my-displacement-material-pink/1229742; "
          "help.poliigon.com/en/articles/10002965-missing-textures-pink-textures-in-blender",
          "My tree trunk shows up bright pink. It used to have a bark picture on it.",
          _prep_missing_texture, _grade_missing_texture, _fix_missing_texture, "the image file is missing"),
    _case("subsurf_ball", HELDOUT, "blenderbasecamp.com/how-to-keep-sharp-edges-when-using-the-subdivision-surface-"
          "modifier/; en.wikibooks.org/wiki/Blender_3D:_Noob_to_Pro/Beginning_Tips",
          "I added Subdivision Surface to my cube to smooth it, but now it's a ball. I want a box with soft edges.",
          _prep_subsurf_ball, _grade_subsurf_ball, _fix_subsurf_ball, "nothing holds the box's edges"),
    _case("moves_in_steps", HELDOUT, "renderhub.com/forum/14138/blender-wont-scale-smoothly-jumps-in-1-increments; "
          "docs.blender.org/manual/en/2.80/scene_layout/object/editing/transform/control/precision.html",
          "When I drag my rock it jumps in big steps instead of moving smoothly.",
          _prep_snapping, _grade_snapping, _fix_snapping, "snapping to increments is on"),
    _case("see_through", HELDOUT, "cgcookie.com/community/16431-x-ray-in-viewport-even-when-x-ray-is-off",
          "Suddenly everything in my viewport is see-through. I can see the back of my cube.",
          _prep_xray, _grade_xray, _fix_xray, "X-ray is on"),
)


def task_set(case_entry):
    """Each case is its own task set (``firsthour-<case>``): one fresh process and one chat per trial, so no case
    starts from another's scene or conversation."""
    return "firsthour-" + case_entry["id"][len("firsthour_"):]


TASK_SETS = tuple(task_set(c) for c in CASES)


def cases(group):
    """The cases of a runner group: ``firsthour-dev``, ``firsthour-heldout`` or ``firsthour`` (both)."""
    if group not in GROUPS:
        raise ValueError("Choose one of " + ", ".join(GROUPS))
    split = {"firsthour-dev": DEV, "firsthour-heldout": HELDOUT}.get(group)
    return [c for c in CASES if split is None or c["split"] == split]


def tasks(name):
    """The one task of task set ``firsthour-<case>``. Graded by the case's own checks (``firsthour_checks``)."""
    found = [c for c in CASES if task_set(c) == name]
    if not found:
        raise ValueError("No first-hour case " + name)
    return [{"id": c["id"], "kind": "firsthour", "source": c["source"], "prompt": c["prompt"], "checks": [],
             "firsthour_checks": [c["id"]], "split": c["split"]} for c in found]


def case(task_id):
    return next(c for c in CASES if c["id"] == task_id)


def prepare(bpy, task_id):
    """The person's state before the message: Blender's factory scene in Object Mode, then the case's fixture."""
    _object_mode(bpy)
    case(task_id)["prepare"](bpy)
    return case(task_id)["why"]


def grade(bpy, task_id):
    """``(passed, checks)``: every named check of the case must hold."""
    checks = case(task_id)["grade"](bpy)
    return bool(checks) and all(checks.values()), checks
