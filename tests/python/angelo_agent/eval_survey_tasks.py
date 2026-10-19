# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The request survey: realistic prompts that show where native coverage still costs time.

The benchmark task sets are natively covered by design, so they cannot show
where real requests fall back to reviewed Python, need extra rounds (deferred
schema lookups, rejected plans) or are slow. This set phrases 65 requests the
way a Blender user types them to an agent and runs them, in order, as ONE
continuing chat (``eval_live_agent.py --task-set survey``). The harness approves
typed bounded tools and declines reviewed Python and every other approval
(``eval_live_tasks.approval_decision``), so a declined script is the gap signal.
``survey_report.py`` ranks the gaps by measured latency return, the selection
rule of ``doc/angelo/native_coverage.rst`` ("How to continue").

Each task adds to the live-eval fields:

* ``category``: one of ``CATEGORIES``.
* ``weight``: the estimated relative frequency of such a request among real
  ones (the category totals below sum to 100). These are estimates, not
  telemetry: no usage data of real sessions exists yet.
* ``expect``: ``native`` (a typed kind covers it today), ``approval`` (an
  approval-gated tool that is not Python, such as sculpting or Media Studio,
  which the harness declines), ``python`` (only reviewed Python covers it
  today: a gap if it is in demand) or ``never-native`` (code execution, files,
  network, preferences, gestures or editor chrome by the rule; included to
  verify routing, never counted as a gap).

=================== ====== ===== ===================================================================
Category            Weight Tasks Rationale
=================== ====== ===== ===================================================================
modeling            20     10    Creating and shaping objects is the core of most requests
materials           12     6     Colour and material changes follow almost every model
question            12     6     Users ask about the scene and how to do things as often as they edit
modifiers           9      5     Bevel, subdivision, array and boolean are everyday tools
lighting            9      6     Nearly every render needs lights and a world
camera              8      5     Framing a shot precedes every render
scene-organization  6      4     Deleting, parenting, collections and cleanup: frequent but small
animation           6      5     Simple keyframes and turntables; full character animation is rarer
render-settings     5      4     Usually set once per project
nodes               3      3     Procedural shading and scattering, mostly power users
uv                  2      1     A specialist step before texturing or baking
rigging             2      2     Specialist; usually one armature per character
import-export/file  2      2     Frequent for users, but never native (routing check only)
other               1.5    2     Editor chrome and scripting requests (routing check only)
sculpt-intent       1      1     Mostly done by hand; rare as an agent request
physics/bake        1      2     Rare and long-running (job contract, rule 4)
grease-pencil       0.5    1     A niche workflow for agent requests
=================== ====== ===== ===================================================================

Ordering keeps each task's preconditions: the chat starts on the factory scene
(Cube, Camera, Light), creates the eval cube, then refers to it, to
``live.COPY_OBJECT``, ``live.RIG_OBJECT``, ``live.SKETCH_OBJECT`` and to the
factory Camera and Light (``targets``) before any task can replace them; later
tasks also say "the sphere" or "the ground plane" as a user would. Paths
under ``~/`` name files that are never written: every file, render and script
request is declined by the harness.

Grading (``eval_grading``; ``VERSION`` and the task list's digest are recorded
with every trial, and results of different versions are never pooled):

* 33 edits are graded by the add-on's closed checks (``checks``), as before;
* 18 edits by result checks (``result_checks``, ``survey_checks``): a look, a
  shape or a motion that no closed kind reads. ``creates`` names what a task
  makes (``$sphere``, ``$ground``, ``$cloth``) for the tasks that later say
  "the sphere"; ``fixtures`` (``survey_fixtures``) make a starting state the
  factory scene would otherwise satisfy by default;
* 8 edits are ``ungraded``, each with its reason: their result is a file, an
  editor state or something behind an approval the harness always declines,
  so no scene state can show it. They are counted as ungraded, never passed;
* the 6 questions are graded on their answer (``answer``, ``eval_answers``):
  each prompt asks for one last line in a fixed form and the harness compares
  it with ground truth it computes from the scene (``survey_truth``), after a
  fixture has moved that truth off the factory default where a guess would
  otherwise pass. The camera question runs twice, once with the object inside
  the frame and once cut by it, needs a view through the scene camera before
  the answer, and fails both runs when both get the same answer.

A graded edit whose checks already hold before its turn is recorded as
ungraded for that run: such a check cannot show that the turn did anything
(``eval_grading_live``; the bounce and the orbit, which step frames, are not
probed before the turn).
"""

import eval_answers as answers
import eval_live_tasks as live

# Bumped whenever a prompt, a check, an answer or the order changes: version 1 is the 64-task survey whose
# trials recorded no version (26 edits without checks, questions graded on the unchanged scene).
VERSION = 2
CATEGORIES = ("modeling", "materials", "question", "modifiers", "lighting", "camera", "scene-organization",
              "animation", "render-settings", "nodes", "uv", "rigging", "import-export/file", "other",
              "sculpt-intent", "physics/bake", "grease-pencil")
CATEGORY_WEIGHTS = {"modeling": 20, "materials": 12, "question": 12, "modifiers": 9, "lighting": 9, "camera": 8,
                    "scene-organization": 6, "animation": 6, "render-settings": 5, "nodes": 3, "uv": 2,
                    "rigging": 2, "import-export/file": 2, "other": 1.5, "sculpt-intent": 1, "physics/bake": 1,
                    "grease-pencil": 0.5}
EXPECTS = ("native", "approval", "python", "never-native")
# Factory-scene objects some tasks are graded on (``targets``); they exist from the first message.
FACTORY_CAMERA, FACTORY_LIGHT = "Camera", "Light"
EVAL, COPY, RIG, SKETCH = live.EVAL_OBJECT, live.COPY_OBJECT, live.RIG_OBJECT, live.SKETCH_OBJECT
# Harness-made starting states (survey_fixtures): an Empty no default scene has, and an unused material.
PROBE_OBJECT, ORPHAN_MATERIAL = "Brass Marker", "Survey Orphan"
# Why an edit is never graded (eval_grading): no scene state can show its result in a harness run.
FILE_RESULT = ("The result is a file outside the scene; the harness declines every file, render and export "
               "request and never reads a person's folders. Routing only.")
DECLINED_APPROVAL = ("The request needs an approval the harness always declines (%s), so its result cannot exist "
                     "in a run. Routing only.")


def _task(task_id, category, weight, expect, prompt, checks=(), *, kind="edit", requires_object=True, targets=None,
          results=(), ungraded=None, creates=None, fixtures=(), answer=None):
    task = {"id": task_id, "kind": kind, "category": category, "weight": weight, "expect": expect,
            "requires_object": requires_object, "prompt": prompt, "checks": list(checks),
            "source": "survey: " + category}
    for key, value in (("targets", targets), ("result_checks", list(results)), ("ungraded", ungraded),
                       ("creates", creates), ("fixtures", list(fixtures)), ("answer", answer)):
        if value:
            task[key] = dict(value) if isinstance(value, dict) else value
    return task


def _question(task_id, weight, question, form, truth, *, requires_object=True, fixtures=(), evidence=None, pair=None):
    """A question graded on its answer: ``form`` is the requested answer line, ``truth`` what the harness computes."""
    answer = {"form": form, "truth": dict(truth)}
    if evidence:
        answer["evidence"] = evidence
    if pair:
        answer["pair"] = pair
    return _task(task_id, "question", weight, "native", answers.prompt(question, form), kind="read_only",
                 requires_object=requires_object, fixtures=fixtures, answer=answer)


def _camera_question(task_id, question, state):
    """Whether the eval cube is fully in frame, asked once per harness-made camera state (``pair``)."""
    return _question(task_id, 1, question, "yes_no", {"kind": "camera_frame", "object": EVAL},
                     fixtures=[{"kind": "camera_frame", "object": EVAL, "state": state}],
                     evidence=answers.SCENE_CAMERA, pair="view_check")


def _count(types, minimum, maximum=2048):
    return {"kind": "object_count", "types": list(types), "minimum": minimum, "maximum": maximum}


def _counts(vertices, edges, faces, maximum=None):
    def bound(value):
        return {"minimum": value, "maximum": value if maximum is None else maximum}
    return {"kind": "mesh_counts", "target": "$eval", "vertices": bound(vertices), "edges": bound(edges),
            "faces": bound(faces)}


def _scene(values, tolerance=None):
    check = {"kind": "property", "target": "$scene", "values": dict(values)}
    if tolerance is not None:
        check["tolerance"] = tolerance
    return check


def _cube_checks():
    return next(task["checks"] for task in live.build_tasks() if task["id"] == "cube_creation")


def _modeling_tasks():
    """The opening (factory scene to the eval cube) and the modeling and material requests."""
    quoted = "'" + EVAL + "'"
    return [
        _question("scene_overview", 3, "What's in my scene right now?", "names", {"kind": "object_names"},
                  requires_object=False, fixtures=[{"kind": "probe_object", "name": PROBE_OBJECT}]),
        _task("create_cube", "modeling", 3, "native",
              "Add a cube called %s, 2 m on each side, at the world origin." % quoted, _cube_checks(),
              requires_object=False),
        _task("delete_default_cube", "scene-organization", 2.5, "native", "Delete the default cube, I don't need it.",
              [_count(["MESH"], 1, 1)]),
        _task("extrude_top", "modeling", 3, "native", "Extrude the top face of Eval Cube straight up by 1 m.",
              [_counts(12, 20, 10),
               {"kind": "dimensions", "target": "$eval", "expected": [2, 2, 3], "tolerance": 0.0001}]),
        # After the extrusion, so the right counts are not a plain cube's.
        _question("mesh_stats", 2, "How many vertices, edges and faces does Eval Cube have?", "counts",
                  {"kind": "mesh_counts", "object": EVAL}),
        _task("sphere_on_top", "modeling", 2.5, "native",
              "Put a UV sphere with a 0.5 m radius on top of Eval Cube so it sits on the top face.",
              [_count(["MESH"], 2)], creates={"$sphere": "MESH"}),
        _task("ground_plane", "modeling", 2, "native", "Add a ground plane, 20 m across, under everything.",
              [_count(["MESH"], 3)], creates={"$ground": "MESH"}),
        # One checkable fact of the explanation: the keymap's own entry for the edge bevel tool.
        _question("how_to_bevel", 3, "How do I bevel just the top edges of a mesh by hand in Blender? Explain the "
                                     "steps, don't change my scene.", "shortcut",
                  {"kind": "shortcut", "keymap": "Mesh", "operator": "mesh.bevel", "properties": {"affect": "EDGES"}}),
        _question("render_info", 2, "What render engine and output resolution is this scene set to?", "render",
                  {"kind": "render"}, fixtures=[{"kind": "scene_values", "values": {
                      "render.resolution_x": 1600, "render.resolution_y": 900}}]),
        _task("red_plastic", "materials", 3.5, "native",
              "Make Eval Cube a glossy red plastic and call the material 'Red Plastic'.",
              [{"kind": "material_assignment", "target": "$eval", "material": "Red Plastic", "all_faces": True}]),
        _task("recolor_material", "materials", 2, "native",
              "Change 'Red Plastic' to a deep blue: base colour (0.05, 0.1, 0.4), roughness 0.2 and metallic 0.",
              [{"kind": "material_values", "target": "$eval", "material": "Red Plastic",
                "base_color": [0.05, 0.1, 0.4, 1], "roughness": 0.2, "metallic": 0, "tolerance": 0.0001}]),
        _task("top_face_trim", "materials", 2, "native",
              "Give only the top face of Eval Cube a separate gold material named 'Trim'.",
              [{"kind": "material_assignment", "target": "$eval", "material": "Trim", "all_faces": False}]),
        _task("glass_sphere", "materials", 2.5, "native", "Make the sphere look like clear glass.",
              results=[{"kind": "material_look", "target": "$sphere", "transmission_min": 0.8, "roughness_max": 0.3}]),
        _task("image_texture_disk", "materials", 1, "never-native",
              "Use the photo ~/Pictures/brick_wall.jpg as the texture on the ground plane.",
              ungraded="The photo is a file the harness never provides, and it declines every file request, so no "
                       "scene state can show the result. Routing only."),
        _task("vertex_colors", "materials", 1, "python",
              "Paint the top vertices of Eval Cube yellow with vertex colours.",
              results=[{"kind": "vertex_color", "target": "$eval", "hue": [40, 75]}]),
        _task("text_hello", "modeling", 2, "native",
              "Add 3D text saying 'Hello' in front of Eval Cube and give it some thickness.", [_count(["FONT"], 1)]),
        _task("table", "modeling", 2, "native",
              "Model a simple table next to Eval Cube: a 1.5 m by 0.8 m top, 0.75 m high, with four legs.",
              [_count(["MESH"], 4)]),
        _task("donut", "modeling", 1.5, "python", "Add a donut (a torus) beside the table.",
              results=[{"kind": "mesh_genus", "target": "$new", "genus": 1}]),
        _task("ai_chair", "modeling", 1, "approval", "Generate a 3D model of a wooden chair with AI and put it in the "
                                                     "scene.",
              ungraded=DECLINED_APPROVAL % "billed Media Studio generation"),
        _task("duplicate_copy", "modeling", 1.5, "native",
              "Duplicate Eval Cube, name the copy '%s' and move it 3 m along X." % COPY,
              [{"kind": "object_exists", "target": "$copy", "expected": True},
               {"kind": "transform", "target": "$copy", "property": "location", "expected": [3, 0, 0],
                "tolerance": 0.0001}]),
        _task("parent_copy", "scene-organization", 1.5, "native",
              "Make %s a child of Eval Cube so they move together." % COPY,
              [{"kind": "parent", "target": "$copy", "expected": "$eval"}], targets={"$eval": EVAL, "$copy": COPY}),
        # The join removes the copy, so this grades on the eval cube (24 vertices: two extruded cubes).
        _task("join_copy", "modeling", 1.5, "native", "Join %s into Eval Cube so they're a single object." % COPY,
              [{"kind": "object_exists", "target": "$copy", "expected": False}, _counts(24, 40, 20)]),
    ]


def _scene_tasks():
    """Modifiers, lights, camera, render settings and animation on the scene built so far."""
    light, camera = {"$key": FACTORY_LIGHT}, {"$cam": FACTORY_CAMERA, "$eval": EVAL}
    return [
        _task("bevel_modifier", "modifiers", 2, "native",
              "Round off the edges of Eval Cube with a bevel modifier, 0.05 m wide with 3 segments.",
              [{"kind": "modifier", "target": "$eval", "name": "$bevel", "type": "BEVEL",
                "values": {"width": 0.05, "segments": 3}, "tolerance": 0.00001}]),
        _task("subdivision", "modifiers", 2.5, "native",
              "Smooth Eval Cube out with a subdivision surface modifier: level 2 in the viewport, 3 for renders.",
              results=[{"kind": "modifier", "target": "$eval", "name": "$modifier:SUBSURF", "type": "SUBSURF",
                        "values": {"levels": 2, "render_levels": 3}, "tolerance": 0}]),
        _task("array_row", "modifiers", 2, "native",
              "Turn the sphere into a row of 5 spheres along X, 1.5 m apart, with an array modifier.",
              results=[{"kind": "array_span", "target": "$sphere", "count": 5, "axis": "x", "spacing": 1.5,
                        "tolerance": 0.05}]),
        _task("boolean_hole", "modifiers", 1.5, "native", "Cut a round hole straight through Eval Cube from front to "
                                                          "back.",
              results=[{"kind": "through_hole", "target": "$eval", "axis": "y"}]),
        _task("apply_modifiers", "modifiers", 1, "native", "Apply all the modifiers on Eval Cube.",
              [_counts(100, 100, 50, maximum=250000)]),
        _task("light_brighter", "lighting", 2, "native", "Make the light twice as bright and a little warmer.",
              [{"kind": "light_settings", "target": "$key", "values": {"energy": 2000}, "tolerance": 1}],
              requires_object=False, targets=light),
        _task("soft_shadows", "lighting", 1.5, "native", "Soften the light's shadows by setting its radius to 0.5 m.",
              [{"kind": "light_settings", "target": "$key", "values": {"radius": 0.5}, "tolerance": 0.0001}],
              requires_object=False, targets=light),
        _task("lights_collection", "scene-organization", 1.5, "native",
              "Move the light into a new collection called 'Lighting'.",
              [{"kind": "collection_membership", "target": "$key", "collections": ["Lighting"]}],
              requires_object=False, targets=light),
        _task("sun_light", "lighting", 1.5, "native", "Turn the light into a sun lamp shining down at a 45 degree "
                                                      "angle.",
              [{"kind": "light_settings", "target": "$key", "values": {"light_type": "SUN"}, "tolerance": 0}],
              requires_object=False, targets=light),
        _task("spot_from_above", "lighting", 1, "native",
              "Add a spotlight 4 m above Eval Cube pointing straight down at it, with a 30 degree cone.",
              [_count(["LIGHT"], 2)]),
        _task("three_point", "lighting", 2, "native", "Set up three-point lighting around Eval Cube.",
              [_count(["LIGHT"], 3)]),
        # The factory world is already a dark grey, so the harness starts this one from a blue world.
        _task("world_grey", "lighting", 1, "native", "Make the world background a dark grey.",
              results=[{"kind": "world_look", "grey": True, "brightness": [0.005, 0.35]}],
              fixtures=[{"kind": "world_color", "color": [0.2, 0.45, 0.9]}]),
        _task("camera_move_aim", "camera", 2, "native", "Move the camera to (0, -12, 3) and point it at Eval Cube.",
              [{"kind": "transform", "target": "$cam", "property": "location", "expected": [0, -12, 3],
                "tolerance": 0.001},
               {"kind": "aim", "target": "$cam", "point": [0, 0, 0], "tolerance": 0.2}], targets=camera),
        _task("lens_35", "camera", 1.5, "native", "Switch the camera to a 35 mm lens.",
              [{"kind": "camera_settings", "target": "$cam", "values": {"lens": 35}, "tolerance": 0.0001}],
              requires_object=False, targets={"$cam": FACTORY_CAMERA}),
        _task("dof", "camera", 1.5, "native", "Add depth of field focused on Eval Cube at f/1.8.",
              [{"kind": "property", "target": "$cam", "values": {"data.dof.use_dof": True, "data.dof.aperture_fstop": 1.8},
                "tolerance": 0.0001}], targets=camera),
        _task("frame_object", "camera", 1.5, "native", "Frame Eval Cube so it fills the camera view.",
              results=[{"kind": "camera_frame", "target": "$eval", "min_fill": 0.6, "tolerance": 0.03}]),
        _camera_question("view_check", "Look through the camera: is Eval Cube fully in frame?", "inside"),
        _task("top_camera_active", "camera", 1.5, "native",
              "Add a second camera looking straight down from above and make it the active camera.",
              [_count(["CAMERA"], 2)]),
        _camera_question("view_check_cropped", "Look through the camera again: is Eval Cube fully in frame now?",
                         "cropped"),
        _task("vertical_resolution", "render-settings", 1.5, "native",
              "Set the output to a vertical 1080 by 1920 resolution at 100%.",
              [_scene({"render.resolution_x": 1080, "render.resolution_y": 1920, "render.resolution_percentage": 100})]),
        _task("color_management", "render-settings", 1, "native",
              "Use AgX with the Punchy look and raise the exposure to 0.5.",
              [_scene({"view_settings.view_transform": "AgX", "view_settings.look": "AgX - Punchy",
                       "view_settings.exposure": 0.5}, tolerance=0.0001)]),
        _task("eevee_quality", "render-settings", 1, "native", "Turn on motion blur and set the render samples to 128.",
              [_scene({"render.use_motion_blur": True, "eevee.taa_render_samples": 128})]),
        _task("render_to_file", "render-settings", 1.5, "python",
              "Render the current frame and save it as ~/Desktop/survey_render.png.", ungraded=FILE_RESULT),
        _task("spin_turntable", "animation", 2, "native",
              "Animate Eval Cube spinning one full turn around Z from frame 1 to frame 120 at a constant speed.",
              [{"kind": "keyframes", "target": "$eval", "property": "rotation_euler", "index": 2, "frames": [1, 120],
                "interpolation": "LINEAR"}]),
        _task("bounce", "animation", 1.5, "native",
              "Make the sphere bounce up and down twice over the first 48 frames.",
              results=[{"kind": "bounces", "target": "$sphere", "frames": [1, 48], "count": 2, "min_height": 0.05}]),
        _task("timeline", "animation", 1, "native", "Make the animation 10 seconds long at 30 fps.",
              [_scene({"render.fps": 30, "frame_end": 300})]),
        _task("marker", "animation", 0.5, "python", "Add a timeline marker called 'Impact' at frame 60.",
              results=[{"kind": "markers", "target": "$scene", "present": [{"name": "Impact", "frame": 60}]}]),
        _task("camera_orbit", "animation", 1, "native",
              "Make the camera named 'Camera' orbit once around Eval Cube over the whole animation.",
              results=[{"kind": "orbit", "target": "$cam", "around": "$eval", "turns": 1, "tolerance_deg": 25}]),
    ]


def _specialist_tasks():
    """UV, nodes, rigging, sculpting, Grease Pencil, physics, then the never-native routing checks."""
    return [
        _task("lightmap_uv", "uv", 2, "native",
              "Unwrap Eval Cube with Smart UV Project into a new UV map called 'Lightmap'.",
              [{"kind": "uv_layer", "target": "$eval", "name": "Lightmap", "require_nonempty": True,
                "require_finite": True}]),
        # Read: a procedural texture drives the surface and no image does. That it looks like wood is not read.
        _task("procedural_wood", "nodes", 1.25, "native", "Give the ground plane a procedural wood material built "
                                                          "from shader nodes.",
              results=[{"kind": "procedural_material", "target": "$ground"}]),
        # A density scatters about, not exactly, the asked number: the two ranges do not overlap.
        _task("scatter_rocks", "nodes", 1.25, "native", "Scatter 200 small rocks over the ground plane with geometry "
                                                        "nodes.",
              results=[{"kind": "scatter_count", "range": [160, 240]}]),
        _task("scatter_density", "nodes", 0.5, "python", "Increase the scattered rocks to 500.",
              results=[{"kind": "scatter_count", "range": [400, 600]}]),
        _task("rig_chain", "rigging", 1, "native",
              "Create an armature named '%s' with a chain of three connected bones going straight up from (0, 4, 0), "
              "each 1 m long." % RIG,
              [{"kind": "rig", "target": "$rig", "minimum_bones": 3, "maximum_bones": 3, "require_finite": True}]),
        _task("auto_weights", "rigging", 1, "python", "Bind the sphere to %s with automatic weights." % RIG,
              results=[{"kind": "armature_binding", "target": "$sphere", "armature": "$rig",
                        "modifier": "$modifier:ARMATURE", "every_vertex_weighted": True, "tolerance": 0.0001}]),
        _task("sculpt_rock", "sculpt-intent", 1, "approval", "Sculpt some rocky bumps and cracks into the top of Eval "
                                                             "Cube.", ungraded=DECLINED_APPROVAL % "sculpting"),
        _task("gp_smiley", "grease-pencil", 0.5, "python",
              "Draw a smiley face with Grease Pencil in a new object called '%s'." % SKETCH,
              [{"kind": "object_exists", "target": "$sketch", "expected": True},
               {"kind": "object_type", "target": "$sketch", "expected": "GREASEPENCIL"}]),
        # Read: the set-up (cloth above a collider). The simulated drape itself is not stepped by the grader.
        _task("cloth_drape", "physics/bake", 0.5, "native",
              "Drape a cloth over Eval Cube: a subdivided plane above it with cloth physics, and make Eval Cube a "
              "collision object.", results=[{"kind": "cloth_over", "collider": "$eval", "min_vertices": 25}],
              creates={"$cloth": "MESH"}),
        _task("bake_sim", "physics/bake", 0.5, "python", "Bake the cloth simulation for frames 1 to 100.",
              results=[{"kind": "cache_baked", "target": "$cloth", "modifier": "CLOTH", "frames": [1, 100]}]),
        # The harness leaves one unused material first, so a scene with nothing to purge cannot pass.
        _task("purge_unused", "scene-organization", 0.5, "python",
              "Clean up the file: remove all unused materials and other orphan data.",
              results=[{"kind": "no_orphans", "absent": [ORPHAN_MATERIAL]}],
              fixtures=[{"kind": "orphan_material", "name": ORPHAN_MATERIAL}]),
        _task("viewport_wireframe", "other", 1, "never-native",
              "Switch the viewport to wireframe so I can see through everything.",
              ungraded="Viewport shading is editor state, not scene data, and never native by rule; the harness "
                       "declines the reviewed Python it would take. Routing only."),
        _task("run_script", "other", 0.5, "never-native",
              "Write a Python script that gives every mesh a random colour, and run it.",
              ungraded=DECLINED_APPROVAL % "running model-written code"),
        _task("export_glb", "import-export/file", 1, "never-native", "Export Eval Cube as a GLB to "
                                                                     "~/Desktop/eval_cube.glb.", ungraded=FILE_RESULT),
        _task("save_blend", "import-export/file", 1, "never-native", "Save the file as ~/Desktop/survey.blend.",
              ungraded=FILE_RESULT),
    ]


# The "survey-questions" set: the survey's six questions and the two edits that make the cube they ask about
# (extruded, so its counts are not a default cube's). A short billed run of it shows whether a model follows
# every answer form; the survey's figures need the survey.
QUESTION_SET = ("scene_overview", "create_cube", "extrude_top", "mesh_stats", "how_to_bevel", "render_info",
                "view_check", "view_check_cropped")


def build_survey_tasks(questions_only=False):
    """The ordered survey (one chat per trial); ids, categories, weights and expectations as documented above.
    ``questions_only`` keeps ``QUESTION_SET``, the same tasks in the same order."""
    tasks = _modeling_tasks() + _scene_tasks() + _specialist_tasks()
    return [task for task in tasks if task["id"] in QUESTION_SET] if questions_only else tasks


def category_weights(tasks=None):
    """Summed task weight per category, in ``CATEGORIES`` order."""
    tasks = build_survey_tasks() if tasks is None else tasks
    return {name: sum(task["weight"] for task in tasks if task["category"] == name) for name in CATEGORIES}


def expect_counts(tasks=None):
    """How many tasks expect each routing class."""
    tasks = build_survey_tasks() if tasks is None else tasks
    return {name: sum(task["expect"] == name for task in tasks) for name in EXPECTS}
