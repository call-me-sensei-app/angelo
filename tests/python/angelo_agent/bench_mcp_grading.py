# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host checks for the Blender MCP side of ``bench_mcp_vs_angelo.py``.

The MCP side has no Angelo controller, so the benchmark driver (never the model)
grades its scene by sending Python to the MCP add-on's socket, using the same
null-delimited JSON request the MCP server itself sends. That code imports the
exact predicates the Angelo side is graded with:

* the task list and checks of ``eval_live_tasks.tasks_for`` (the default, long or coverage set);
* ``angelo_agent.scene_validation.verify`` with ``VerificationContext``;
* ``scene_digest``, ``bind_checks``, ``grading_targets`` and ``smooth_shaded``, copied verbatim at run
  time out of ``eval_live_agent_session.py`` (that driver refuses to import
  outside its own trial, so its helpers are extracted with ``ast``).

``grade_code`` mirrors ``Trial.grade`` of that session line for line. The only
difference is that the read-only digest crosses the socket as JSON, so both
sides of that comparison are JSON-normalized (tuples become lists; float values
survive exactly). A question's answer is read by the driver itself
(``bench_mcp_answers``): ``answer_setup_code`` has Blender make the question's
fixtures and compute its ground truth before the prompt, and
``answer_restore_code`` undoes those fixtures after grading. Nothing else here
changes the scene. Dependency-free on the host.
"""

import ast
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SESSION_SCRIPT = HERE / "eval_live_agent_session.py"
SESSION_HELPERS = ("scene_digest", "bind_checks", "grading_targets", "smooth_shaded")
IMPORT_PATHS = (str(ROOT / "scripts/addons_core"), str(HERE))


def session_helpers_source(path=SESSION_SCRIPT):
    """The unchanged source of the grading helpers of the Angelo trial driver."""
    source = Path(path).read_text(encoding="utf-8")
    found = {node.name: ast.get_source_segment(source, node) for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name in SESSION_HELPERS}
    missing = [name for name in SESSION_HELPERS if name not in found]
    if missing:
        raise RuntimeError("eval_live_agent_session.py no longer defines %s" % ", ".join(missing))
    return "\n\n\n".join(found[name] for name in SESSION_HELPERS)


def _prelude(more=()):
    # Blender's bundled Python ignores PYTHONDONTWRITEBYTECODE, so keep the
    # worktree free of bytecode explicitly. sys.path is restored after import.
    # ``more`` names further harness modules a request needs; without it the text is what it always was.
    return "\n".join((
        "import copy, json, sys",
        "import bpy",
        "sys.dont_write_bytecode = True",
        "_saved_path = list(sys.path)",
        "sys.path[:0] = [p for p in %r if p not in sys.path]" % (list(IMPORT_PATHS),),
        "try:",
        "    from angelo_agent.scene_validation import VerificationContext, verify",
        "    import eval_live_tasks as live",
        *("    import %s" % name for name in more),
        "finally:",
        "    sys.path[:] = _saved_path",
        "",
        session_helpers_source(),
        "",
        "",
        "def _normal(value):",
        "    return json.loads(json.dumps(value, default=repr))",
        "",
        "",
        "_scene = bpy.context.window_manager.windows[0].scene",
        "",
    ))


def snapshot_code():
    """Read-only: the session's scene digest and whether the eval object exists."""
    return _prelude() + (
        "result = {'digest': _normal(scene_digest(_scene)),\n"
        "          'eval_object_present': _scene.objects.get(live.EVAL_OBJECT) is not None,\n"
        "          'objects': sorted(o.name for o in _scene.objects)}\n")


def reset_code(task_id, task_set="default", count=40):
    """``--after-failure reset``: the harness restores the reference state a task needs (live.reset_for_task)."""
    return _prelude() + "\n".join((
        "_task = next(t for t in live.tasks_for(%r, %d) if t['id'] == %r)" % (task_set, count, task_id),
        "result = {'steps': live.reset_for_task(bpy, _scene, _task)}"))


def fixture_code(task_id, task_set="default", count=40):
    """The harness-made object a task edits, when missing (live.prepare_fixture)."""
    return _prelude() + "\n".join((
        "_task = next(t for t in live.tasks_for(%r, %d) if t['id'] == %r)" % (task_set, count, task_id),
        "result = {'steps': live.prepare_fixture(bpy, _scene, _task)}"))


def answer_setup_code(task_id, task_set="default", count=40):
    """Before a question's prompt: its harness-made fixtures and its ground truth (bench_mcp_answers.setup)."""
    return _prelude(("bench_mcp_answers",)) + "\n".join((
        "_task = next(t for t in live.tasks_for(%r, %d) if t['id'] == %r)" % (task_set, count, task_id),
        "result = _normal(bench_mcp_answers.setup(bpy, _scene, _task))",
        ""))


def answer_restore_code(undo=()):
    """After a question was graded: undo the transient fixtures of its setup (bench_mcp_answers.restore).

    ``undo`` is what the setup reported: Blender uses it only if it no longer holds its own record of them.
    """
    return _prelude(("bench_mcp_answers",)) + "\n".join((
        "_undo = json.loads(%r)" % json.dumps(list(undo)),
        "result = {'restored': bench_mcp_answers.restore(bpy, _scene, _undo)}",
        ""))


def grade_code(task_id, digest, task_set="default", count=40):
    """Read-only: the session's ``Trial.grade`` for one task of a task set, after the model's turn."""
    return _prelude() + "\n".join((
        "_task = next(t for t in live.tasks_for(%r, %d) if t['id'] == %r)" % (task_set, count, task_id),
        "_digest = json.loads(%r)" % json.dumps(digest),
        "_obj = _scene.objects.get(live.EVAL_OBJECT)",
        "if _task['kind'] == 'read_only':",
        "    _passed = _normal(scene_digest(_scene)) == _digest",
        "    _host_check = {'scene_unchanged': _passed}",
        "else:",
        "    _host_check = (verify(VerificationContext(grading_targets(_scene, _obj), list(_scene.objects)),",
        "                          bind_checks(_task['checks'], _obj))",
        "                   if _obj is not None else {'success': False, 'status': 'fail', 'reason': 'object missing'})",
        "    _passed = bool(_host_check['success'])",
        "    if _obj is not None and _task.get('smooth_shading'):",
        "        _host_check['smooth_shaded'] = smooth_shaded(_obj)",
        "        _passed = _passed and _host_check['smooth_shaded']",
        # Diagnostics only; never part of pass/fail. Edit Mode leaves base mesh data unflushed.
        "result = {'host_check': _normal(_host_check), 'host_check_passed': bool(_passed),",
        "          'context_mode': bpy.context.mode, 'eval_object_mode': _obj.mode if _obj is not None else None,",
        "          'objects': sorted(o.name for o in _scene.objects)}",
        ""))


def _scenario_prelude(task_set):
    """Imports for the scenario helpers; sys.path is restored as in ``_prelude``.

    The scenario module itself is imported here too: ``scenario_specs`` loads it
    lazily, and after the path is restored a later import would fail in a
    Blender where nothing else imported it (the model never does).
    """
    return "\n".join((
        "import json, sys",
        "import bpy",
        "sys.dont_write_bytecode = True",
        "_saved_path = list(sys.path)",
        "sys.path[:0] = [p for p in %r if p not in sys.path]" % (list(IMPORT_PATHS),),
        "try:",
        "    import eval_live_tasks as live",
        "    import scenario_checks, scenario_hygiene, scenario_specs",
        "    scenario_specs.module(scenario_specs.scenario_id(%r))" % task_set,
        "finally:",
        "    sys.path[:] = _saved_path",
        "_window = bpy.context.window_manager.windows[0]",
        "_scene = _window.scene",
        ""))


def scenario_grade_code(task_id, task_set, count=40):
    """Read-only (motion checks restore the frame): a scenario turn's closed checks and the state digest."""
    return _scenario_prelude(task_set) + "\n".join((
        "_task = next(t for t in live.tasks_for(%r, %d) if t['id'] == %r)" % (task_set, count, task_id),
        "_result = scenario_checks.evaluate(bpy, _scene, _task['scenario_checks'])",
        "result = json.loads(json.dumps({'host_check': _result, 'host_check_passed': _result['success'],",
        "    'state_digest': scenario_hygiene.state_digest(bpy, _scene), 'context_mode': bpy.context.mode,",
        "    'objects': sorted(o.name for o in _scene.objects)[:200]}, default=repr))",
        ""))


def scenario_setup_code(task_set, path, sha256):
    """Before the digest of the first turn: the scenario's start file appended into the scene (``setup``)."""
    return _scenario_prelude(task_set) + "\n".join((
        "import time",
        "_started = time.perf_counter()",
        "_module = scenario_specs.module(scenario_specs.scenario_id(%r))" % task_set,
        "result = json.loads(json.dumps(_module.setup(bpy, _scene, %r, %r), default=repr))" % (str(path), sha256),
        "result['setup_s'] = round(time.perf_counter() - _started, 3)",
        ""))


def scenario_digest_code(task_set):
    """Read-only: the scene's state digest and orphan data-blocks before the first turn."""
    return _scenario_prelude(task_set) + ("result = {'digest': scenario_hygiene.state_digest(bpy, _scene), "
                                  "'orphans': scenario_hygiene.orphans(bpy)}\n")


def scenario_finish_code(task_set, digests, baseline, blend_path, result_path, probe=True):
    """End of session, from a timer outside the add-on's request: hygiene, a saved copy, then the Undo probe.

    The add-on executes requests inside its own handler; saving and undoing
    replace Blender data under it, so the work runs from a timer and writes
    its result to ``result_path`` for the driver to collect. ``probe`` False
    leaves the Undo probe to ``scenario_undo_code`` (after an ask-why follow-up).
    """
    return _scenario_prelude(task_set) + "\n".join((
        "_name = scenario_specs.scenario_id(%r)" % task_set,
        "_digests = %r" % (list(digests),),
        "_baseline = %r" % (baseline or {},),
        "def _finish():",
        "    output = scenario_hygiene.finish(bpy, bpy.context.window_manager.windows[0],",
        "                                     scenario_specs.module(_name).TURNS, _digests, %r, _baseline, %r)"
        % (str(blend_path), bool(probe)),
        "    import os",
        "    with open(%r, 'w') as handle:" % (str(result_path) + ".partial"),
        "        json.dump(output, handle, default=repr)",
        "    os.replace(%r, %r)  # The driver sees the file only when it is complete." % (str(result_path) + ".partial",
                                                                                      str(result_path)),
        "    return None",
        "bpy.app.timers.register(_finish, first_interval=0.2)",
        "result = {'scheduled': True}",
        ""))


def scenario_undo_code(task_set, digests, result_path):
    """The Undo probe ``scenario_finish_code`` left for later, from a timer, its result in ``result_path``."""
    return _scenario_prelude(task_set) + "\n".join((
        "_digests = %r" % (list(digests),),
        "def _probe():",
        "    output = scenario_hygiene.late_undo_probe(bpy, bpy.context.window_manager.windows[0], _digests)",
        "    import os",
        "    with open(%r, 'w') as handle:" % (str(result_path) + ".partial"),
        "        json.dump(output, handle, default=repr)",
        "    os.replace(%r, %r)" % (str(result_path) + ".partial", str(result_path)),
        "    return None",
        "bpy.app.timers.register(_probe, first_interval=0.2)",
        "result = {'scheduled': True}",
        ""))


def staged_expected_scene_code(stage):
    """Dry-run only (no model): build the graded end states with plain ``bpy`` so the
    grading path is proven to pass on the stock binary before anything is billed.
    ``"cube"`` creates the cube task's result; ``"finish"`` then applies the
    transform, bevel-and-shade and material results to it."""
    if stage == "cube":
        return "\n".join((
            "import bpy",
            "scene = bpy.context.window_manager.windows[0].scene",
            "mesh = bpy.data.meshes.new('Eval Cube')",
            "verts = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]",
            "faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]",
            "mesh.from_pydata(verts, [], faces)",
            "mesh.update()",
            "scene.collection.objects.link(bpy.data.objects.new('Eval Cube', mesh))",
            "bpy.context.view_layer.update()",
            "result = {'created': 'Eval Cube'}",
            ""))
    return "\n".join((
        "import bpy",
        "obj = bpy.data.objects['Eval Cube']",
        "mesh = obj.data",
        "obj.scale = (2.0, 0.5, 1.5)",
        "bevel = obj.modifiers.new('Bevel', 'BEVEL')",
        "bevel.width, bevel.segments = 0.125, 3",
        "for face in mesh.polygons:",
        "    face.use_smooth = True",
        "material = bpy.data.materials.new('QA Graded Material')",
        "principled = next(n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED')",
        "principled.inputs['Base Color'].default_value = (0.25, 0.5, 0.75, 1.0)",
        "principled.inputs['Roughness'].default_value = 0.3",
        "principled.inputs['Metallic'].default_value = 0.4",
        "mesh.materials.append(material)",
        "bpy.context.view_layer.update()",
        "result = {'finished': obj.name}",
        ""))
