# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Live-agent evaluation tasks, test-only approval policy and journey statistics.

Dependency-free: imported by the host launcher (``eval_live_agent.py``), by the
in-app driver (``eval_live_agent_session.py``) and by host tests. Nothing here
starts a provider, reads credentials or touches Blender.

Tasks reuse the closed host checks of ``eval_agent_tasks.py`` against an exact
object name stated in the prompt. Each trial is one fresh Angelo process and one
chat, so its first message starts the provider runtime cold and the rest are
warm follow-ups. Success is graded only by those host checks after the turn
and, for a question, by its answer against ground truth the harness computes
(``eval_gate``, ``eval_grading``), never by the model's own claims.
"""

import copy

import eval_agent_tasks as graded
import eval_gate
import eval_node_tasks as node_tasks, eval_rig_tasks  # noqa: E401
import scenario_specs
# Journey statistics live beside the tasks (re-exported for eval_live_agent and the reports).
from eval_live_journeys import JOURNEYS, journey_samples, percentile, summarize_journeys  # noqa: F401
# The coverage set's operator_run family tasks, their objects and harness-made fixtures (re-exported).
from eval_operator_tasks import HAIR_OBJECT, STROKES_OBJECT, TEXT_OBJECT, operator_family_tasks, prepare_fixture  # noqa: F401

EVAL_OBJECT = "Eval Cube"
# The coverage set's duplicate; graded as "$copy". The scene is "$scene".
COPY_OBJECT = "Eval Cube Copy"
# The coverage set's armature (bone_set); graded as "$rig".
RIG_OBJECT = "Eval Rig"
# The coverage set's Grease Pencil object; graded as "$sketch".
SKETCH_OBJECT = "Eval Sketch"
PREVIEW_LOCATION = [3.0, 0.0, 0.0]

# The tester's rule answers only what asks. In Auto, the default the eval runs in, the product approves
# every BOUNDED_MUTATION itself (preview Accept/Discard, sculpting and flow edits too), as in a person's
# chat; what still asks is a recovery-review correction and everything outside that set (in Ask, every
# edit). The harness approves only these typed, bounded scene edits (host-validated, one Undo step each)
# and declines everything else that asks: reviewed Python, file writes/deletes, Studio, delegation,
# workspace changes, memory notes and sculpting. NEVER_APPROVED is what the tester never approves when
# it asks; angelo_accept_preview is among them (the preview journey accepts through the human UI
# continuation, Controller.accept_native_preview), although Auto applies a model's own accept unasked.
# angelo_apply_recipe is approved like the angelo_scene_program it stages: the
# approval shows the host-expanded program, and only that program runs.
HARNESS_APPROVABLE = frozenset({"angelo_create_cube", "angelo_set_transform", "angelo_apply_batch",
                                "angelo_scene_program", "angelo_apply_recipe", "angelo_discard_preview"})
NEVER_APPROVED = frozenset({"angelo_execute_python", "angelo_write_file", "angelo_delete_file",
                            "angelo_delegate", "angelo_accept_preview", "angelo_memory_update",
                            "angelo_set_workspace_mode"})


def approval_decision(tool_name, arguments=None, *, delegation=None):
    """``"approve"`` only for the closed typed allowlist; every other name is declined.

    ``delegation`` (``eval_live_agent.py --approve-delegation``) is the one
    ``(connection_id, model)`` a read-only consultation may use: the run's own
    connection and an exact catalog model. Any other consultation is declined.
    """
    if (tool_name == "angelo_delegate" and delegation is not None and isinstance(arguments, dict)
            and (arguments.get("connection_id"), arguments.get("model")) == tuple(delegation)):
        return "approve"
    if tool_name in HARNESS_APPROVABLE and tool_name not in NEVER_APPROVED and not tool_name.startswith("angelo_studio_"):
        return "approve"
    return "decline"


def _on_eval_object(checks):
    """eval_agent_tasks checks retargeted from their ``$mesh`` alias to the eval object."""
    result = copy.deepcopy(checks)
    for check in result:
        if check.get("target") == "$mesh":
            check["target"] = "$eval"
    return result


def _graded_checks(task_id, skip_kinds=()):
    fixture = next(task for task in graded.TASK_FIXTURES if task["id"] == task_id)
    return _on_eval_object([check for check in fixture["program"]["expected_checks"] if check["kind"] not in skip_kinds])


def build_tasks(version=None):
    """The ordered task list for one trial (one chat). Later tasks reuse the cube.
    ``version`` is the gate-set version (``eval_gate``; None is the current one): it changes only the question."""
    quoted = "'" + EVAL_OBJECT + "'"
    return eval_gate.versioned([
        {"id": "read_only_question", "kind": "read_only", "requires_object": False,
         "prompt": "How many mesh objects are in the scene, and what are their names? Answer in one short "
                   "sentence. Do not change anything.",
         "checks": [], "source": "new read-only question"},
        {"id": "cube_creation", "kind": "edit", "requires_object": False,
         "prompt": "Create a cube named %s that is exactly 2 m on each side, centred at the world origin." % quoted,
         # The fixture's object_name check names its own staged alias, not this prompt's object.
         "checks": _graded_checks("01_cube_dimensions_counts", skip_kinds=("object_name",)),
         "source": "eval_agent_tasks 01_cube_dimensions_counts"},
        {"id": "transform", "kind": "edit", "requires_object": True,
         "prompt": "Scale %s so its dimensions are exactly 4 m along X, 1 m along Y and 3 m along Z." % quoted,
         "checks": _graded_checks("02_fit_nonuniform_dimensions"),
         "source": "eval_agent_tasks 02_fit_nonuniform_dimensions"},
        {"id": "bevel_and_shade", "kind": "edit", "requires_object": True,
         "prompt": "Add a non-destructive bevel to %s with a width of 0.125 m and 3 segments, and shade it "
                   "smooth." % quoted,
         # Modifier names are the agent's choice: the session binds the checks to the bevel it finds.
         "checks": [{"kind": "modifier", "target": "$eval", "name": "$bevel", "type": "BEVEL",
                     "values": {"width": 0.125, "segments": 3}, "tolerance": 0.00001}],
         "smooth_shading": True, "source": "eval_agent_tasks 09_bevel_modifier + bevel-and-shade recipe"},
        {"id": "material", "kind": "edit", "requires_object": True,
         "prompt": "Give every face of %s a Principled BSDF material named 'QA Graded Material' with base colour "
                   "(0.25, 0.5, 0.75, 1), roughness 0.3 and metallic 0.4." % quoted,
         "checks": _graded_checks("12_material_assignment_values"),
         "source": "eval_agent_tasks 12_material_assignment_values"},
        {"id": "preview_accept", "kind": "preview", "requires_object": True,
         "prompt": "Stage a PREVIEW only, do not apply it, that moves %s to location (3, 0, 0). I will review "
                   "and accept the preview myself." % quoted,
         "checks": [{"kind": "transform", "target": "$eval", "property": "location",
                     "expected": PREVIEW_LOCATION, "tolerance": 0.0001}],
         "source": "new staged-preview journey"},
    ], version, EVAL_OBJECT)


LONG_SESSION_TASKS = 40
# Billed runs stay within 120; the unbilled long_session_replay.py raises it in its own process.
MAX_LONG_SESSION_TASKS = 120
# Scenario sets (scenario-<id>, scenario_specs) are multi-turn builds against reference images.
TASK_SETS = (("default", "long", "delegation", "coverage", "survey", "survey-questions", "rig") + scenario_specs.TASK_SETS
             + scenario_specs.ONESHOT_TASK_SETS)   # oneshot-<id>: one message, one turn (doc/angelo/oneshot_tests.rst).
# The objects of the delegation set, by check alias: four 1 m cubes on the X axis.
AUDIT_OBJECTS = {"$a": "Audit A", "$b": "Audit B", "$c": "Audit C", "$d": "Audit D"}
AUDIT_X = {"$a": 0.0, "$b": 2.0, "$c": 4.0, "$d": 6.0}
AUDIT_BEVELS = {"$a": 0.02, "$b": 0.05, "$c": 0.02, "$d": 0.08}
CONSULT = " If a read-only collaborator is available in this chat, ask it to %s first."


def task_targets(task):
    """Check alias -> object name a task is graded on (the eval cube unless the task names others)."""
    return dict(task.get("targets") or {"$eval": EVAL_OBJECT})


def required_objects(task):
    """Objects an earlier task must have created; the task is skipped, not retried, without them."""
    if task.get("targets"):
        return sorted(task["targets"].values())
    return [EVAL_OBJECT] if task.get("requires_object") else []


def build_delegation_tasks():
    """Read-heavy audits where a fast read-only collaborator may help, then their edits.

    Every prompt says to consult a collaborator only if one is available, so
    the same prompts measure both arms: ``--approve-delegation MODEL`` offers
    the run's own connection with that one model, and without it the
    collaborator list is empty and the lead does the work itself. Edits are
    graded by closed host checks; questions by an unchanged scene and the names
    their answer must contain (a weaker check: extra names still pass).
    """
    every = {"targets": dict(AUDIT_OBJECTS), "requires_object": False}
    setup = [check for alias in AUDIT_OBJECTS for check in (
        {"kind": "dimensions", "target": alias, "expected": [1.0, 1.0, 1.0], "tolerance": 0.0001},
        {"kind": "transform", "target": alias, "property": "location", "expected": [AUDIT_X[alias], 0.0, 0.0],
         "tolerance": 0.0001},
        {"kind": "modifier", "target": alias, "name": "$bevel", "type": "BEVEL",
         "values": {"width": AUDIT_BEVELS[alias], "segments": 2}, "tolerance": 0.00001})]
    fixed = [{"kind": "modifier", "target": alias, "name": "$bevel", "type": "BEVEL",
              "values": {"width": 0.02, "segments": 2}, "tolerance": 0.00001} for alias in AUDIT_OBJECTS]
    far = [check for alias in ("$c", "$d") for check in (
        {"kind": "material_assignment", "target": alias, "material": "Audit Far", "all_faces": True},
        {"kind": "material_values", "target": alias, "material": "Audit Far", "base_color": [1.0, 0.0, 0.0, 1.0],
         "roughness": 0.5, "metallic": 0.0, "tolerance": 0.00001})]
    return [
        dict(every, id="audit_setup", kind="edit", source="delegation: setup", checks=setup,
             prompt="Create four cubes named 'Audit A', 'Audit B', 'Audit C' and 'Audit D', each exactly 1 m on "
                    "every side, at locations (0, 0, 0), (2, 0, 0), (4, 0, 0) and (6, 0, 0). Give each a "
                    "non-destructive bevel modifier with 2 segments and these widths: Audit A 0.02 m, Audit B "
                    "0.05 m, Audit C 0.02 m and Audit D 0.08 m."),
        dict(every, id="audit_question", kind="read_only", source="delegation: audit question", checks=[],
             reply_includes=["Audit B", "Audit D"],
             prompt="Which of the objects Audit A to Audit D have a bevel width other than 0.02 m? Answer with "
                    "the object names only. Do not change anything." + CONSULT % "do this audit"),
        dict(every, id="audit_fix", kind="edit", source="delegation: audit then fix", checks=fixed,
             prompt="Set the bevel width to 0.02 m on every Audit object whose bevel width is not 0.02 m, and "
                    "leave the others unchanged." + CONSULT % "find which objects need the change"),
        dict(every, id="audit_view", kind="read_only", source="delegation: view check", checks=[],
             reply_includes=["Audit D"],
             prompt="Looking at the scene from the front, along +Y, which Audit object appears furthest to the "
                    "right? Answer with one name. Do not change anything." + CONSULT % "check the view"),
        dict(every, id="audit_material", kind="edit", source="delegation: filter then edit", checks=far,
             prompt="Give every Audit object whose X location is greater than 3 m a new Principled BSDF material "
                    "named 'Audit Far' with base colour (1, 0, 0, 1), roughness 0.5 and metallic 0. Leave the other "
                    "Audit objects unchanged." + CONSULT % "list the objects that qualify"),
    ]


def build_coverage_tasks():
    """Native-coverage journeys: scene settings, World and object management on one scene.

    The default opening, then five edits a native kind covers since toolset 28
    (property_set on the scene and on an object, world_configure,
    object_duplicate and collection_link), a rig posed and configured with
    ``bone_set``, a hair particle system through modifier_add and particle_set,
    and points scattered by Distribute Points on Faces (also toolset 28), one
    operator_run edit (an allow-listed Blender operator, Triangulate Faces),
    ``node_edit`` edits of that Geometry Nodes graph, of the World's graph and of
    a procedural material (``node_graph`` checks read nodes, values and links by
    type, so the model's node names do not matter) and a Grease Pencil object;
    then one task per operator_run family (``eval_operator_tasks``:
    an Edit-mode transform, stroke and hair-curve subdivision on harness-made
    fixtures, a two-line text and an applied constraint) and one per animation
    mechanism (``eval_animation_tasks``; after the operator tasks, so the copy
    is not yet animated when its constraint is applied). Each is graded by
    closed host checks on ``$scene`` (the
    trial's scene), ``$eval``, ``$copy`` (``COPY_OBJECT``), ``$rig``
    (``RIG_OBJECT``), ``$sketch`` (``SKETCH_OBJECT``; closed checks cannot read
    Grease Pencil layers, so the second layer is asked for but not graded),
    ``$text``, ``$strokes`` or ``$hair``.
    Whether a model finds the deferred kinds (a lookup round on first use) is
    what a billed run of this set measures.
    """
    quoted, copy_quoted = "'" + EVAL_OBJECT + "'", "'" + COPY_OBJECT + "'"
    tasks = [task for task in build_tasks() if task["id"] in {"read_only_question", "cube_creation"}]
    tasks += [
        {"id": "render_settings", "kind": "edit", "requires_object": True,
         "prompt": "Set the render resolution to 1280 by 720 pixels at 50 percent, and make the film transparent.",
         "checks": [{"kind": "property", "target": "$scene", "values": {
             "render.resolution_x": 1280, "render.resolution_y": 720, "render.resolution_percentage": 50,
             "render.film_transparent": True}}],
         "source": "native coverage: property_set (scene render settings)"},
        {"id": "world_background", "kind": "edit", "requires_object": True,
         "prompt": "Make the world background colour (0.02, 0.03, 0.1) with a strength of 0.5.",
         "checks": [{"kind": "world_settings", "target": "$scene", "values": {"color": [0.02, 0.03, 0.1], "strength": 0.5},
                     "tolerance": 0.0001}],
         "source": "native coverage: world_configure"},
        {"id": "frame_range", "kind": "edit", "requires_object": True,
         "prompt": "Set the scene to 30 frames per second with a frame range of 1 to 120.",
         "checks": [{"kind": "property", "target": "$scene", "values": {
             "render.fps": 30, "frame_start": 1, "frame_end": 120}}],
         "source": "native coverage: property_set (frame range and rate)"},
        {"id": "duplicate_and_collect", "kind": "edit", "requires_object": True,
         "prompt": ("Make a separate copy of %s named %s at location (3, 0, 0), then move both cubes into a new "
                    "collection named 'Eval Props'.") % (quoted, copy_quoted),
         "checks": [{"kind": "object_exists", "target": "$copy", "expected": True},
                    {"kind": "transform", "target": "$copy", "property": "location", "expected": [3, 0, 0],
                     "tolerance": 0.0001},
                    {"kind": "collection_membership", "target": "$eval", "collections": ["Eval Props"]},
                    {"kind": "collection_membership", "target": "$copy", "collections": ["Eval Props"]}],
         "source": "native coverage: object_duplicate, collection_link"},
        {"id": "render_visibility", "kind": "edit", "requires_object": True,
         "prompt": "Hide %s from renders but keep it visible in the viewport." % copy_quoted,
         "checks": [{"kind": "property", "target": "$copy", "values": {"hide_render": True, "hide_viewport": False}}],
         "source": "native coverage: property_set (object visibility)"},
        {"id": "rig_pose", "kind": "edit", "requires_object": True,
         "prompt": ("Create an armature named %s with two bones: 'Upper' from (0, 0, 0) to (0, 0, 1), and 'Lower' "
                    "from (0, 0, 1) to (0, 0, 2) parented and connected to 'Upper'. Then pose 'Lower' rotated 0.5 "
                    "radians about its own X axis, and turn off deform for 'Upper'.") % ("'" + RIG_OBJECT + "'"),
         "checks": [{"kind": "rig", "target": "$rig", "minimum_bones": 2, "maximum_bones": 2,
                     "expected_bones": ["Upper", "Lower"], "require_finite": True},
                    {"kind": "bone", "target": "$rig", "bone": "Lower", "tail": [0, 0, 2], "parent": "Upper",
                     "connected": True, "rotation": [0.5, 0, 0], "tolerance": 0.0001},
                    {"kind": "bone", "target": "$rig", "bone": "Upper", "settings": {"use_deform": False}}],
         "source": "native coverage: armature_create, bone_set (pose and Bone settings)"},
        {"id": "particle_hair", "kind": "edit", "requires_object": True,
         "prompt": "Give %s a hair particle system named 'Eval Hair' with 300 strands, each 0.2 m long." % quoted,
         "checks": [{"kind": "particle_values", "target": "$eval", "name": "Eval Hair",
                     "values": {"type": "HAIR", "count": 300, "hair_length": 0.2}}],
         "source": "native coverage: modifier_add PARTICLE_SYSTEM, particle_set"},
        # Graded by the named modifier and its graph (node_graph: node types, a value and a link).
        {"id": "scatter_points", "kind": "edit", "requires_object": True,
         "prompt": ("Scatter points over the surface of %s with a Geometry Nodes modifier named 'Eval Scatter' "
                    "that uses a Distribute Points on Faces node with a density of 5.") % copy_quoted,
         "checks": [{"kind": "modifier_stack", "target": "$copy", "positions": [{"name": "Eval Scatter", "index": 0}]},
                    {"kind": "node_graph", "target": "$copy", "modifier": "Eval Scatter",
                     "nodes": [{"type": "GeometryNodeDistributePointsOnFaces", "inputs": {"Density": 5}}],
                     "links": [{"from_type": "NodeGroupInput", "from_socket": "Geometry",
                                "to_type": "GeometryNodeDistributePointsOnFaces", "to_socket": "Mesh"}],
                     "tolerance": 0.0001}],
         "source": "native coverage: geometry_nodes (Distribute Points on Faces size rule)"},
        *node_tasks.after_scatter(copy_quoted),
        {"id": "triangulate_mesh", "kind": "edit", "requires_object": True,
         "prompt": ("Triangulate every face of %s in its mesh data with Blender's Triangulate Faces operator, not "
                    "with a modifier.") % quoted,
         "checks": [{"kind": "mesh_counts", "target": "$eval", "vertices": {"minimum": 8, "maximum": 8},
                     "edges": {"minimum": 18, "maximum": 18}, "faces": {"minimum": 12, "maximum": 12}}],
         "source": "native coverage: operator_run (mesh.quads_convert_to_tris)"},
        *node_tasks.before_sketch(quoted),
        {"id": "grease_pencil_sketch", "kind": "edit", "requires_object": False,
         "prompt": "Add a new Grease Pencil object named '%s' for drawing, with a second layer named 'Ink'." % SKETCH_OBJECT,
         "checks": [{"kind": "object_exists", "target": "$sketch", "expected": True},
                    {"kind": "object_type", "target": "$sketch", "expected": "GREASEPENCIL"}],
         "source": "native coverage: create_object (GREASEPENCIL), operator_run (grease_pencil.layer_add)"},
    ]
    from eval_animation_tasks import animation_coverage_tasks  # One task per animation mechanism.
    return tasks + operator_family_tasks(quoted, copy_quoted) + animation_coverage_tasks(quoted, copy_quoted)


def build_long_session_tasks(count=LONG_SESSION_TASKS, version=None):
    """A deterministic long chat on one scene: the default opening, then a cycle of edits and questions.

    After the read-only question and the cube, each cycle of five moves the cube,
    resizes it, adds (once) or reconfigures its bevel, gives it a new uniquely
    named material and asks a read-only question about it. Every edit is graded
    by closed host checks on the cube, as the default tasks are; no previews, so
    both benchmark sides run every task. Each value differs from the previous
    cycle's, so no edit can pass without changing the scene.
    """
    if type(count) is not int or not 2 <= count <= MAX_LONG_SESSION_TASKS:
        raise ValueError("Use 2-%d long-session tasks" % MAX_LONG_SESSION_TASKS)
    quoted = "'" + EVAL_OBJECT + "'"
    tasks = [task for task in build_tasks(version) if task["id"] in {"read_only_question", "cube_creation"}]
    bevel_added = False
    for index in range(count - len(tasks)):
        cycle, step = divmod(index, 5)
        number = "%02d" % (index + 1)
        if step == 0:
            location = [float(cycle % 5 - 2), float(cycle % 3 - 1), float(cycle % 2)]
            tasks.append({"id": "move_" + number, "kind": "edit", "requires_object": True,
                          "prompt": "Move %s to location (%g, %g, %g)." % (quoted, *location),
                          "checks": [{"kind": "transform", "target": "$eval", "property": "location",
                                      "expected": location, "tolerance": 0.0001}],
                          "source": "long session: move"})
        elif step == 1:
            size = [1.0 + cycle % 4 * 0.5, 2.0 - cycle % 3 * 0.5, 1.0 + cycle % 2]
            tasks.append({"id": "resize_" + number, "kind": "edit", "requires_object": True,
                          "prompt": "Scale %s so its dimensions are exactly %g m along X, %g m along Y and %g m "
                                    "along Z." % (quoted, *size),
                          "checks": [{"kind": "dimensions", "target": "$eval", "expected": size, "tolerance": 0.0001}],
                          "source": "long session: resize"})
        elif step == 2:
            width, segments = round(0.05 + cycle % 4 * 0.025, 3), 2 + cycle % 3
            prompt = ("Add a non-destructive bevel to %s with a width of %g m and %d segments." if not bevel_added else
                      "Change the bevel modifier on %s to a width of %g m and %d segments.") % (quoted, width, segments)
            bevel_added = True
            tasks.append({"id": "bevel_" + number, "kind": "edit", "requires_object": True, "prompt": prompt,
                          "checks": [{"kind": "modifier", "target": "$eval", "name": "$bevel", "type": "BEVEL",
                                      "values": {"width": width, "segments": segments}, "tolerance": 0.00001}],
                          "source": "long session: bevel"})
        elif step == 3:
            name = "Long Material " + number
            color = [round(0.2 + cycle % 4 * 0.2, 2), round(0.8 - cycle % 3 * 0.25, 2), round(0.3 + cycle % 2 * 0.4, 2), 1]
            roughness, metallic = round(0.2 + cycle % 5 * 0.15, 2), round(cycle % 3 * 0.3, 2)
            tasks.append({"id": "material_" + number, "kind": "edit", "requires_object": True,
                          "prompt": "Give every face of %s a new Principled BSDF material named '%s' with base colour "
                                    "(%g, %g, %g, 1), roughness %g and metallic %g." % (quoted, name, *color[:3],
                                                                                       roughness, metallic),
                          "checks": [{"kind": "material_assignment", "target": "$eval", "material": name,
                                      "all_faces": True},
                                     {"kind": "material_values", "target": "$eval", "material": name,
                                      "base_color": color, "roughness": roughness, "metallic": metallic,
                                      "tolerance": 0.00001}],
                          "source": "long session: material"})
        else:
            tasks.append({"id": "question_" + number, "kind": "read_only", "requires_object": True,
                          "prompt": "What are the current dimensions and location of %s? Answer in one short sentence. "
                                    "Do not change anything." % quoted,
                          "checks": [], "source": "long session: read-only question"})
    return eval_gate.versioned(tasks, version, EVAL_OBJECT)


AFTER_FAILURE_MODES = ("skip", "reset")


def reset_for_task(bpy, scene, task):
    """``--after-failure reset``: the reference state ``task`` needs, made by the harness, never the agent.

    A failed task used to leave its dependants skipped for the rest of the
    trial, hiding their own outcomes. This restores only what the task needs to
    be attempted: frame 1, the 2 m eval cube at the origin when it is missing,
    and a Bevel modifier when the prompt changes one. Returns the steps taken
    (empty when nothing was needed); the task record keeps them. ``bpy`` is
    passed in so this module stays importable without Blender.
    """
    steps = []
    if scene.frame_current != 1:
        scene.frame_set(1)
        steps.append("frame")
    if task["requires_object"] and scene.objects.get(EVAL_OBJECT) is None:
        import bmesh
        mesh = bpy.data.meshes.new(EVAL_OBJECT + " Mesh")
        geometry = bmesh.new()
        try:
            bmesh.ops.create_cube(geometry, size=2.0)
            geometry.to_mesh(mesh)
        finally:
            geometry.free()
        scene.collection.objects.link(bpy.data.objects.new(EVAL_OBJECT, mesh))
        steps.append("eval_object")
    obj = scene.objects.get(EVAL_OBJECT)
    if (obj is not None and task["prompt"].startswith("Change the bevel modifier")
            and not any(modifier.type == "BEVEL" for modifier in obj.modifiers)):
        obj.modifiers.new("Bevel", "BEVEL")
        steps.append("bevel")
    return steps


def tasks_for(task_set="default", count=LONG_SESSION_TASKS, version=None):
    """The task list of a named set, identical in the host launcher, the in-app driver and grading.
    ``version`` builds a gate set (default, long) as that gate-set version recorded it (``eval_gate``).
    Prospective one-shot tasks add the common QL1 directive before ``eval_parallel.begin`` splits the arms;
    their raw scenario briefs and image/check definitions are not changed."""
    if scenario_specs.is_scenario(task_set):
        tasks = scenario_specs.tasks(scenario_specs.scenario_id(task_set))
        if task_set in scenario_specs.ONESHOT_TASK_SETS:
            import eval_quality_loop
            return eval_quality_loop.apply(tasks)
        return tasks
    if task_set in ("survey", "survey-questions"):
        import eval_survey_tasks  # It imports this module, so it loads on first use.
        return eval_survey_tasks.build_survey_tasks(questions_only=task_set != "survey")
    builders = {"default": lambda: build_tasks(version), "long": lambda: build_long_session_tasks(count, version),
                "delegation": build_delegation_tasks, "coverage": build_coverage_tasks, "rig": eval_rig_tasks.build_rig_tasks}
    if task_set not in builders:
        raise ValueError("Choose a task set: " + ", ".join(TASK_SETS))
    return builders[task_set]()


def plan(trials, tasks=None):
    """What an authorized run will do, printed before anything starts."""
    tasks = build_tasks() if tasks is None else tasks
    if type(trials) is not int or not 1 <= trials <= 20:
        raise ValueError("Use 1-20 trials")
    previews = sum(task["kind"] == "preview" for task in tasks)
    return {"trials": trials, "angelo_processes": trials, "chats": trials, "tasks_per_trial": len(tasks),
            "task_ids": [task["id"] for task in tasks],
            "user_messages": trials * len(tasks), "local_preview_accepts": trials * previews,
            "billing": "Each user message is one billed provider turn on the selected connection; a turn can "
                       "contain several model rounds. Preview accepts are local UI actions, not provider turns. "
                       "Dependent tasks are skipped, not retried, when their object is missing."}


TEAM_MODES = ("claude_team", "codex_team")
TEAM_PREFIX = "team:"  # --team-mode: the connection is the signed-in team's, resolved in the trial.


def refusal_reasons(authorized, connection_id):
    """Why a run must not start; empty means both explicit guards are present."""
    import uuid
    reasons = []
    if authorized is not True:
        reasons.append("pass --authorize-billed-runs to confirm that every planned message is billed")
    if str(connection_id).startswith(TEAM_PREFIX):
        if connection_id[len(TEAM_PREFIX):] not in TEAM_MODES:
            reasons.append("--team-mode must be one of %s" % ", ".join(TEAM_MODES))
    elif not connection_id:
        reasons.append("pass --connection-id with the one existing connection to use")
    else:
        try:
            if str(uuid.UUID(connection_id)) != connection_id:
                raise ValueError
        except (ValueError, AttributeError, TypeError):
            reasons.append("--connection-id must be a connection UUID in its lowercase canonical form")
    return reasons


def task_outcomes(records, tasks=None):
    """Completion, host-check and grade counts per task id (``eval_grading.task_outcomes``)."""
    import eval_grading
    return eval_grading.task_outcomes(records, tasks)
