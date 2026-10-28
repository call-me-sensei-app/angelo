# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Route parity for data on results the compiled app makes now (scripts/angelo/route_parity.py).

The host suite checks the recorded corpus (``route_parity_corpus``); a field a producer adds after the corpus was
recorded only shows up in fresh results. This makes them in the real Blender (a script that adds objects, a Sun
light and a Sky; typed programs with checks that certify, create a light and set the World; a preview and its
Accept; a search and an object detail) and runs every route's presentation code over them with the registry
(``scripts/angelo/route_parity.json``): any field one route carries and another drops without a registered
reason fails, naming the route and the path.

``ANGELO_ROUTE_PARITY_RECORD=<folder>`` also writes the results as corpus files (``route_parity_corpus.py``'s
format, shrunk and scrubbed) to refresh the host suite's corpus by hand.

Run by ``run_native_suites.py --include test_blender_route_parity`` (BACKGROUND). No provider, account or network.
"""

import json
import os
import sys
import traceback
from pathlib import Path

import bpy

assert bpy.app.background and "--factory-startup" in sys.argv
ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
for path in (ROOT / "scripts/addons_core", ROOT / "scripts/angelo", Path(__file__).resolve().parent):
    sys.path.insert(0, str(path))
from angelo_agent import native_programs as native, scene_queries as queries, scene_settings  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402
import route_parity  # noqa: E402
import route_parity_corpus  # noqa: E402

bpy.context.preferences.filepaths.file_preview_type = "NONE"
W = bpy.context.window
report = {"checks": {}, "observed": {}, "binary": bpy.app.binary_path, "success": False}
results = []   # (tool, certified, raw result)


def check(name, condition, **observed):
    report["checks"][name] = bool(condition)
    if observed:
        report["observed"][name] = observed


def final(result):
    """A staged native job driven to its end as the controller drives it (poll until final or ready, then commit);
    any other result as it is. The executor hands back the job itself, not a result, for its native job tools
    (BlenderExecutor.supports_native_job: angelo_scene_program, angelo_accept_preview, angelo_apply_recipe)."""
    if not getattr(result, "is_program_job", False):
        return result
    polled = result.poll()
    while not (polled.get("final") or polled.get("ready_to_commit")):
        polled = result.poll()
    return result.commit() if polled.get("ready_to_commit") else polled


def execute(name, arguments):
    """One executor call's final result."""
    return final(BlenderExecutor(W).execute(name, arguments))


def run(arguments):
    """Bind, stage and commit (or stage a PREVIEW of) an angelo_scene_program; its final result."""
    native.precondition(W, "angelo_scene_program", arguments)
    return final(native.begin(W, "angelo_scene_program", arguments))


def keep(tool, result, certified=False):
    results.append((tool, certified, result))
    return result


def make_results():
    script = ("import bpy, math\n"
              "for i in range(3):\n"
              "    bpy.ops.mesh.primitive_cube_add(size=1, location=(i * 2, 0, 0.5))\n"
              "    bpy.context.object.name = 'QA Post %d' % i\n"
              "sun = bpy.data.objects.new('QA Script Sun', bpy.data.lights.new('QA Script Sun', 'SUN'))\n"
              "bpy.context.scene.collection.objects.link(sun)\n"
              "sun.rotation_euler = (math.radians(50), 0, math.radians(30))\n"
              "world = bpy.context.scene.world or bpy.data.worlds.new('World')\n"
              "bpy.context.scene.world = world\n"
              "world.use_nodes = True\n"
              "sky = world.node_tree.nodes.new('ShaderNodeTexSky')\n"
              "world.node_tree.links.new(sky.outputs[0], world.node_tree.nodes['Background'].inputs[0])\n"
              "result = {'posts': 3}\n")
    scripted = keep("angelo_execute_python", execute(
        "angelo_execute_python", {"description": "Posts, a sun and a sky", "code": script}))
    check("a_script_completes", scripted.get("success") is True, error=scripted.get("error"))
    cube = run(validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "write_set": [], "operations": [{"kind": "create_primitive", "target": None,
            "parameters": {"primitive": "CUBE", "name": "QA Checked Cube", "size": 2, "location": [0, 4, 1],
                           "alias": "checked"}}],
        "expected_checks": [{"kind": "dimensions", "target": "$checked", "expected": [2, 2, 2], "tolerance": .0001}]}))
    keep("angelo_scene_program", cube, certified=True)
    check("a_checked_program_commits", cube.get("status") == "committed", error=cube.get("error"))
    light = run(validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "write_set": [], "expected_checks": [], "operations": [
            {"kind": "light_create", "parameters": {"name": "QA Key", "alias": "key", "location": [4, -4, 5],
                                                    "look_at": [0, 0, 0], "settings": {"light_type": "AREA",
                                                                                       "energy": 300}}}]}))
    keep("angelo_scene_program", light)
    check("a_light_program_commits", light.get("status") == "committed", error=light.get("error"))
    scene = W.scene
    sky = run(validate_call("angelo_scene_program", {
        "mode": "APPLY", "read_set": [], "expected_checks": [],
        "write_set": [{"handle": scene_settings.scene_handle(scene), "revision": scene_settings.settings_revision(scene)}],
        "operations": [{"kind": "world_configure", "target": scene_settings.scene_handle(scene), "parameters": {
            "strength": .5, "sky": {"type": "MULTIPLE_SCATTERING", "sun_elevation": .35, "sun_rotation": 0.0}}}]}))
    keep("angelo_scene_program", sky)
    check("a_world_program_commits", sky.get("status") == "committed" and "world" in sky, error=sky.get("error"))
    post = bpy.data.objects["QA Post 0"]
    W.view_layer.update()
    preview = run(validate_call("angelo_scene_program", {
        "mode": "PREVIEW", "read_set": [], "expected_checks": [
            {"kind": "dimensions", "target": queries.object_handle(post), "expected": [1, 1, 2], "tolerance": .001}],
        "write_set": [{"handle": queries.object_handle(post), "revision": queries.object_revision(W, post)}],
        "operations": [{"kind": "transform", "target": queries.object_handle(post),
                        "parameters": {"scale": [1, 1, 2]}}]}))
    keep("angelo_scene_program", preview, certified=True)
    check("a_preview_stages", preview.get("status") == "preview", error=preview.get("error"))
    if preview.get("preview_id"):
        accepted = execute("angelo_accept_preview", {"preview_id": preview["preview_id"]})
        keep("angelo_accept_preview", accepted, certified=True)
        check("its_accept_commits", accepted.get("status") == "committed", error=accepted.get("error"))
    found = keep("angelo_scene_search", execute("angelo_scene_search", {"query": "QA"}))
    check("a_search_answers", not found.get("error"), error=found.get("error"))
    detail = keep("angelo_scene_object", execute("angelo_scene_object", {
        "object": queries.object_handle(bpy.data.objects["QA Key"]), "sections": ["TRANSFORM", "CAMERA_LIGHT"]}))
    check("an_object_detail_answers", not detail.get("error"), error=detail.get("error"))


def gate():
    corpus = [("fresh-%s#%d" % (tool, index), tool, result) for index, (tool, _certified, result) in enumerate(results)]
    findings, problems = route_parity.check(route_parity.load_registry(), corpus)
    problems = [problem for problem in problems if " matched nothing" not in problem]  # Stale: the host suite's.
    counts, lines = route_parity.summary(findings)
    report["observed"]["findings"] = counts
    report["observed"]["open"] = lines
    check("every_fresh_field_reaches_every_route_or_a_registered_filter_drops_it", not problems, problems=problems)
    record = os.environ.get("ANGELO_ROUTE_PARITY_RECORD")
    if record:
        items = [{"tool": tool, "certified": certified, "source": "native suite", "result": route_parity_corpus.shrink(raw)}
                 for tool, certified, raw in results]
        Path(record).mkdir(parents=True, exist_ok=True)
        (Path(record) / "native-fresh.json").write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n")


try:
    make_results()
    gate()
    report["success"] = all(report["checks"].values()) and bool(report["checks"])
except Exception:
    report["error"] = traceback.format_exc()
    traceback.print_exc()
finally:
    native.cleanup_all()
    (OUT / "route-parity.json").write_text(json.dumps(report, indent=2, default=str))
failed = sorted(name for name, passed in report["checks"].items() if not passed)
print("ROUTE_PARITY failed checks:", json.dumps(failed))
assert report["success"], failed or report.get("error")
