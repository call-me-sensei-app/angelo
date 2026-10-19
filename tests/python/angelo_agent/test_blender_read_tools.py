# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live read tools in the compiled app: angelo_read_data and angelo_blend_file. Run with --factory-startup.

A planted project (read_tools_fixtures.py) holds facts the reads must return
and facts they must never return. The suite checks, with Blender's own RNA,
library reader and files:

* the answers: socket and modifier-input identifiers, add-on (Cycles)
  settings and enum choices, an orphan mesh, a missing texture, a missing
  library and its lost data, an undefined node, another .blend's datablocks;
* that every read, successful or not, leaves the session as it was: datablock
  identities and user counts, the dirty flag, which images are loaded, the
  workspace's files and no dependency-graph update, around each call, and a
  digest of every datablock's values around a sweep of the whole document;
* that a second read after an edit shows the edit;
* that an unknown path, an over-budget read, a file outside the workspace and
  a damaged .blend are clear answers, not errors;
* that the answers reach the model through the real Claude and Codex
  providers over scripted stand-ins (read_tools_stand_ins.py), and through
  the real controller in a read-only chat, with no approval;
* that nothing planted as private appears in any answer.

No provider, account, network or inference. ``$ANGELO_QA_DIR/read-tools.json``
reports named checks, timings and ``success``.
"""

import json
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

import bpy

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup process"
HERE = Path(__file__).resolve().parent
OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-read-tools-test"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path[:0] = [str(HERE.parents[2] / "scripts/addons_core"), str(HERE)]
import read_tools_fixtures as fixtures  # noqa: E402
from angelo_agent import data_read  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.claude_outcomes import native_result_problem  # noqa: E402
from angelo_agent.files import FileWorkspace  # noqa: E402
from angelo_agent.studio_agent import StudioPending  # noqa: E402
from angelo_agent.tool_specs import CODEX_DEFERRED_NAMESPACE, validate_call  # noqa: E402

READ, FILE = "angelo_read_data", "angelo_blend_file"
report = {"checks": {}, "paid_requests": 0, "timings_ms": {}, "characters": {}, "observed": {}}
answers, updates = [], [0]
state = {}


def check(name, condition, detail=None):
    report["checks"][name] = bool(condition)
    if not condition:
        raise AssertionError(name + (": " + json.dumps(detail, default=str)[:2000] if detail is not None else ""))


def on_update(_scene, _depsgraph):
    updates[0] += 1


def tool(name, **arguments):
    """One call as the controller makes it, proving around it that the session did not change."""
    before, count = fixtures.light(state["project"]), updates[0]
    started = time.perf_counter()
    try:
        result = state["executor"].execute(name, validate_call(name, arguments))
        if isinstance(result, StudioPending):
            pending = result
            deadline = time.monotonic() + pending.timeout
            try:
                while (result := pending.poll()) is None:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Workspace metadata did not complete before its deadline")
                    time.sleep(.001)
            finally:
                pending.cancel()
    except Exception as error:
        result = {"error": str(error)}
    elapsed = round((time.perf_counter() - started) * 1000, 3)
    after = fixtures.light(state["project"])
    if before != after or updates[0] != count:
        changed = [key for key in before if before[key] != after[key]]
        raise AssertionError("A read changed the session: %s %s -> %s, %d updates"
                             % (name, json.dumps(arguments), changed, updates[0] - count))
    text = json.dumps(result, separators=(",", ":"), allow_nan=False)
    answers.append(text)
    state["last"] = (elapsed, len(text))
    return result


def execute(name, arguments):
    """Host dispatch for the stand-in runtimes: the same checked call."""
    return tool(name, **arguments)


def named(rows, name, key="name"):
    return next((row for row in rows if row.get(key) == name), None)


def measure(label, name, **arguments):
    result = tool(name, **arguments)
    report["timings_ms"][label], report["characters"][label] = state["last"]
    return result


def planted_answers():
    group = 'data.node_groups["Planted Rock"]'
    sockets = measure("node_sockets", READ, path=group + '.nodes["Planted Noise"].inputs')
    rows = {row["identifier"]: row for row in sockets["items"]}
    check("socket_identifiers", sockets["found"] and {"Vector", "Scale", "Detail", "Roughness", "W"} <= set(rows), rows)
    check("socket_disabled_until_4d", rows["W"]["enabled"] is False and rows["Scale"]["enabled"] is True)
    scale = tool(READ, path=group + '.nodes["Planted Scale"].inputs')
    check("vector_math_scale_socket", [row["identifier"] for row in scale["items"]][-1] == "Scale", scale["items"])
    interface = tool(READ, path=group + ".interface.items_tree", depth=2)
    check("interface_identifiers", named(interface["items"], "Height")["identifier"] == state["height"])
    inputs = measure("modifier_inputs", READ, depth=2,
                     path='scene.objects["Cube"].modifiers["Planted GN"].properties.inputs')
    height = named(inputs["properties"], state["height"])
    check("modifier_input_identifier_and_label", height["label"] == "Height" and height["type"] == "POINTER", height)
    check("modifier_input_value", named(height["properties"], "value")["value"] == 2.5, height)
    label = named(inputs["properties"], state["label"])
    text = named(label["properties"], "value")
    check("modifier_text_input_withheld", text["withheld"] == "SOCKET_TEXT" and "value" not in text, label)
    cycles = measure("cycles_settings", READ, path="scene.cycles", limit=64)
    check("addon_settings_listed", cycles["struct"] == "CyclesRenderSettings"
          and named(cycles["properties"], "samples")["value"] == 137, cycles["properties"][:3])
    device = tool(READ, path="scene.cycles.device")["property"]
    check("enum_choices", {choice["id"] for choice in device["choices"]} >= {"CPU", "GPU"} and device["value"] in ("CPU", "GPU"))
    engine = tool(READ, path="bpy.context.scene.render.engine")["property"]
    # The engine list is computed for the interface; RNA lists its fixed part, and the answer says so.
    check("current_render_engine", engine["value"] == "CYCLES" and (any(c["id"] == "CYCLES" for c in engine["choices"])
                                                                    or engine["choices_partial"] is True), engine)
    cube = tool(READ, path='scene.objects["Cube"]')
    check("object_reference", cube["struct"] == "Object" and named(cube["properties"], "data")["value"]["read"]
          == 'data.meshes["Cube"]', cube["properties"][:3])
    row = named(tool(READ, path="scene.objects")["items"], "Cube")
    check("object_handle_binds_edits", row["handle"].startswith("scn:") and row["type"] == "MESH", row)
    last = tool(READ, path='scene.objects["Cube"].modifiers[-1].name')["property"]
    check("negative_index", last["value"] == "Planted GN")
    check("array_element", tool(READ, path='scene.objects["Cube"].scale[2]')["property"]["value"] == 1.0)


def withheld_answers():
    render = tool(READ, path="scene.render", limit=64)
    more = tool(READ, path="scene.render", offset=render["next_offset"], limit=64) if render["next_offset"] else {"properties": []}
    filepath = named(render["properties"] + more["properties"], "filepath")
    check("output_path_withheld", filepath == {"name": "filepath", "type": "STRING", "withheld": "PATH"}, filepath)
    interface = tool(READ, path='data.node_groups["Planted Rock"].interface.items_tree', depth=2)
    text = named(named(interface["items"], "Label")["properties"], "default_value")
    check("interface_text_withheld", text["withheld"] == "SOCKET_TEXT", text)
    node = tool(READ, path='data.node_groups["Planted Rock"].nodes["Planted String"]', limit=64)
    check("string_node_text_withheld", named(node["properties"], "string")["withheld"] == "SOCKET_TEXT")
    driver = tool(READ, path='scene.objects["Cube"].animation_data.drivers[0].driver')
    check("driver_expression_withheld", named(driver["properties"], "expression")["withheld"] == "SCRIPT")
    images = {row["name"]: row for row in tool(READ, path="data.images")["items"]}
    image = tool(READ, path='data.images["planted_present.png"]', limit=64)
    check("image_path_and_size_withheld", named(image["properties"], "filepath")["withheld"] == "PATH"
          and named(image["properties"], "size")["withheld"] == "LOADS_FILE" and "planted_present.png" in images)
    for path, code in (("data.texts", "DENIED"), ("data.scenes", "DENIED"), ("data.libraries", "DENIED"),
                       ("data.window_managers", "DENIED"), ("scene.background_set", "OTHER_SCENE"),
                       ("scene.background_set.frame_current", "OTHER_SCENE"), ("scene.view_layers[0].depsgraph", "DENIED"),
                       ("scene.rna_type", "DENIED"), ('scene.objects["Cube"].bl_rna', "DENIED")):
        result = tool(READ, path=path)
        check("refused_" + path, (result["found"], result["code"]) == (False, code), result)


def attribute_storage():
    """Fetching an attribute's elements would rewrite single-value storage as an array: they are never fetched."""
    mesh = bpy.data.meshes["Planted Fresh Mesh"]
    before = [(attribute.name, attribute.storage_type) for attribute in mesh.attributes]
    check("fixture_has_single_value_attributes", any(storage == "SINGLE" for _name, storage in before), before)
    single = next(name for name, storage in before if storage == "SINGLE")
    listing = tool(READ, path='data.meshes["Planted Fresh Mesh"].attributes', depth=2)
    row = named(listing["items"], single)
    check("attribute_is_described", named(row["properties"], "storage_type")["value"] == "SINGLE"
          and named(row["properties"], "data")["length"] == len(mesh.attributes[single].data), row)
    for path in ('data.meshes["Planted Fresh Mesh"].attributes["%s"].data' % single, 'data.meshes["Planted Fresh Mesh"].attributes["%s"].data[0]' % single,
                 'data.meshes["Planted Fresh Mesh"].attributes["position"].data[0].vector'):
        refused = tool(READ, path=path)
        check("attribute_elements_refused", (refused["found"], refused["code"]) == (False, "DENIED")
              and refused["length"] > 0 and "single-value" in refused["message"], refused)
    position = tool(READ, path='data.meshes["Planted Fresh Mesh"].vertices[0].co')["property"]
    check("element_values_are_on_vertices", len(position["value"]) == 3)
    check("attribute_storage_unchanged", before == [(attribute.name, attribute.storage_type) for attribute in mesh.attributes])


def file_answers():
    summary = measure("open_file_summary", FILE)
    check("open_file_scope", summary["readable"] and summary["scope"] == "OPEN_FILE" and summary["file"] == "main.blend"
          and summary["saved"] and summary["unsaved_changes"] is state["dirty"], summary)
    meshes = named(summary["datablocks"], "meshes", "type")
    check("counts_with_unused_and_fake_user", meshes["unused"] >= 1 and meshes["kept_by_fake_user"] >= 1, meshes)
    check("orphan_mesh_named", {"type": "meshes", "name": "Planted Orphan"} in summary["unused"]["items"]
          and {"type": "materials", "name": "Planted Orphan Material"} in summary["unused"]["items"], summary["unused"])
    check("fake_user_data_is_not_unused", not named(summary["unused"]["items"], "Planted Kept"))
    files = {row["name"]: row for row in summary["external_files"]["items"]}
    check("missing_texture", files["planted_missing.png"] == {"type": "images", "name": "planted_missing.png",
                                                              "file": "textures/planted_missing.png", "status": "missing"}, files)
    check("present_texture", files["planted_present.png"]["status"] == "present"
          and files["planted_present.png"]["file"] == "textures/planted_present.png")
    check("outside_file_is_a_base_name", files["outside_secret.png"] == {
        "type": "images", "name": "outside_secret.png", "file": "outside_secret.png", "outside": True, "status": "present"})
    check("packed_image", files["planted_packed.png"]["status"] == "packed", files)
    check("missing_first", summary["external_files"]["items"][0]["status"] == "missing"
          and summary["external_files"]["missing"] == 1, summary["external_files"])
    libraries = {row["name"]: row for row in summary["libraries"]["items"]}
    props = libraries["props.blend"]
    check("libraries", (props["file"], props["status"]) == ("libs/props.blend", "present") and props["datablocks"] >= 1
          and libraries["gone.blend"]["status"] == "missing" and libraries["gone.blend"]["file"] == "libs/gone.blend",
          libraries)
    kinds = {(row["kind"], row.get("node") or row["name"]) for row in summary["unresolved"]["items"]}
    check("unresolved_references", {("MISSING_LINKED_DATA", "Planted Prop"), ("MISSING_LIBRARY", "gone.blend"),
                                    ("UNDEFINED_NODE", "Planted Lost Node"),
                                    ("GROUP_NODE_WITHOUT_GROUP", "Planted Empty Group")} <= kinds, kinds)
    page = tool(FILE, list="meshes")
    check("type_page_has_user_counts", named(page["items"], "Planted Orphan") == {"name": "Planted Orphan", "users": 0}
          and named(page["items"], "Planted Kept")["fake_user"] is True, page["items"])
    other = measure("workspace_file", FILE, file="stages/other.blend")
    objects = named(other["datablocks"], "objects", "type")
    check("other_file_names", other["readable"] and other["scope"] == "WORKSPACE_FILE"
          and "Planted In Other File" in objects["names"] and objects["count"] == 2, other)
    names = tool(FILE, file="stages/other.blend", list="objects", limit=1)
    check("other_file_paged", names["total"] == 2 and names["next_offset"] == 1 and names["truncated"], names)
    check("libraries_are_not_linked_by_listing", len(bpy.data.libraries) == 2)


def failure_answers():
    """The four failure paths: clear answers the turn can continue from."""
    unknown = tool(READ, path="scene.cycles.feature_set")
    check("unknown_path_names_valid_members", unknown["found"] is False and unknown["code"] == "UNKNOWN_MEMBER"
          and unknown["resolved"] == "scene.cycles" and "samples" in unknown["members"], unknown)
    big = measure("over_budget_page", READ, path='data.node_groups["Planted Rock"].nodes', depth=2, limit=64)
    rows = len(json.dumps(big["items"], separators=(",", ":")))
    check("over_budget_is_truncated_with_the_next_page", big["found"] and big["truncated"] and big["next_offset"]
          == big["returned"] < big["length"] and rows <= data_read.MAX_CHARS and "offset %d" % big["next_offset"]
          in big["note"], {key: big[key] for key in ("returned", "length", "next_offset", "truncated")})
    rest = tool(READ, path='data.node_groups["Planted Rock"].nodes', depth=2, limit=64, offset=big["next_offset"])
    check("the_next_page_continues", rest["offset"] == big["next_offset"] and rest["items"][0]["index"] == big["next_offset"])
    refused = {}
    for file, code in (("../Client X/alice/secret.blend", "OUTSIDE_WORKSPACE"), (str(state["outside"] / "secret.blend"),
                       "OUTSIDE_WORKSPACE"), ("link.blend", "OUTSIDE_WORKSPACE"), ("stages/corrupt.blend", "UNREADABLE"),
                       ("stages/cut.blend", "UNREADABLE"), ("stages/text.blend", "NOT_BLEND"),
                       ("stages/none.blend", "NOT_FOUND")):
        refused[file] = tool(FILE, file=file)
        check("file_refused_%s" % code + "_" + Path(file).name, (refused[file]["readable"], refused[file]["code"])
              == (False, code), refused[file])
    for result in [unknown, big] + list(refused.values()) + [tool(READ, path="scene.objects.get('Cube')"),
                                                             tool(READ, path='scene.objects["Nope"]'),
                                                             tool(FILE, list="nonsense")]:
        check("failure_is_an_answer_not_an_error", "error" not in result and native_result_problem(result) is None, result)
    report["observed"]["corrupt_message"] = refused["stages/corrupt.blend"]["message"]


def after_an_edit():
    scene = bpy.context.scene
    scene.cycles.samples = 64
    bpy.data.node_groups["Planted Rock"].nodes["Planted Noise"].noise_dimensions = "4D"
    bpy.data.meshes.remove(bpy.data.meshes["Planted Orphan"])
    fixtures.png(state["project"] / "textures" / "planted_missing.png")
    bpy.context.view_layer.update()
    check("edit_samples_read_back", tool(READ, path="scene.cycles.samples")["property"]["value"] == 64)
    sockets = tool(READ, path='data.node_groups["Planted Rock"].nodes["Planted Noise"].inputs')
    check("edit_socket_enabled_read_back", named(sockets["items"], "W", "identifier")["enabled"] is True)
    summary = tool(FILE)
    check("edit_orphan_gone", not named(summary["unused"]["items"], "Planted Orphan")
          and named(summary["unused"]["items"], "Planted Orphan Material") is not None, summary["unused"])
    check("edit_texture_now_present", named(summary["external_files"]["items"], "planted_missing.png")["status"] == "present")


def sweep():
    """Read the whole document breadth-first and prove nothing changed and nothing private left."""
    first, before = fixtures.deep(), fixtures.deep()
    check("the_independent_walk_changes_nothing_itself", first == before, [first, before])
    queue, seen, reads = ["scene", "data"], set(), 0
    started = time.perf_counter()
    while queue and reads < 1500:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        offset = 0
        for _page in range(6):
            result = tool(READ, path=path, depth=2, limit=64, offset=offset)
            reads += 1
            if not result.get("found"):
                break
            rows = result.get("properties") or result.get("items") or []
            check("page_within_budget", len(json.dumps(rows, separators=(",", ":"))) <= data_read.MAX_CHARS + 2000)
            if path.count(".") + path.count("[") < 4:
                for row in rows:
                    if result["kind"] == "COLLECTION" and "withheld" not in row:
                        queue.append("%s[%d]" % (path, row["index"]))
                    elif (row.get("type") in ("POINTER", "COLLECTION") and "withheld" not in row
                          and row.get("value", 1) and row.get("length", 1)):
                        queue.append(path + "." + row["name"])
            if result.get("next_offset") is None:
                break
            offset = result["next_offset"]
    for arguments in ({}, {"list": "UNUSED"}, {"list": "FILES"}, {"list": "LIBRARIES"}, {"list": "UNRESOLVED"},
                      {"list": "images"}, {"list": "libraries"}, {"list": "texts"}, {"file": "stages/other.blend"}):
        tool(FILE, **arguments)
    report["observed"]["sweep"] = {"reads": reads, "paths": len(seen), "seconds": round(time.perf_counter() - started, 2)}
    after = fixtures.deep()
    check("sweep_was_broad", reads >= 300 and before["values"] > 5000, report["observed"]["sweep"])
    check("deep_digest_unchanged_by_the_sweep", before == after, [before, after])


def staging():
    scene = fixtures.tag_stage_scene()
    try:
        results = [state["executor"].execute(READ, {"path": "scene"}), state["executor"].execute(FILE, {})]
    finally:
        bpy.data.scenes.remove(scene)
    check("a_resident_stage_is_never_read", [(r.get("found", r.get("readable")), r["code"]) for r in results]
          == [(False, "STAGING"), (False, "STAGING")], results)
    check("reads_resume_after_the_stage", tool(READ, path="scene.frame_current")["found"])


def controller_path():
    """The real controller in a read-only chat: admitted, run and answered with no approval."""
    from angelo_agent.controller import Controller
    folder = tempfile.TemporaryDirectory()
    controller = Controller(folder.name)
    try:
        controller.set_document(("planted-document", 1))
        controller.busy = True
        controller.state["options"]["approval"] = "READ_ONLY"
        controller.files = FileWorkspace(state["project"])
        replies = []
        for identity, name, arguments in (("a", READ, {"path": "scene.cycles.samples"}), ("b", FILE, {}),
                                          ("c", FILE, {"file": "stages/other.blend"}), ("d", READ, {"path": "scene.nope"})):
            expected_replies = len(replies) + 1
            controller.request_tool(identity, name, arguments, replies.append)
            controller.pump_tools(BlenderExecutor(bpy.context.window_manager.windows[0]), controller.document)
            deadline = time.monotonic() + 10
            while len(replies) < expected_replies and time.monotonic() < deadline:
                controller.pump_events()  # The real UI polls pending results here.
                time.sleep(.001)
        check("read_only_chat_answers_without_approval", len(replies) == 4 and not controller.pending
              and replies[0]["property"]["value"] == 64 and replies[1]["scope"] == "OPEN_FILE"
              and replies[2]["scope"] == "WORKSPACE_FILE" and replies[3]["code"] == "UNKNOWN_MEMBER", replies)
        check("no_snapshot_for_a_read", controller.request_checkpoint is None)
    finally:
        controller.close()
        folder.cleanup()


def runtimes():
    from read_tools_stand_ins import ClaudeStandIn, CodexStandIn, final, plan
    sockets = 'data.node_groups["Planted Rock"].nodes["Planted Noise"].inputs'
    inputs = 'scene.objects["Cube"].modifiers["Planted GN"].properties.inputs'
    # The answers of the acceptance list, and its four failure paths: an unknown path, an over-budget page,
    # a file outside the workspace and a damaged .blend.
    reads = [(READ, {"path": sockets}), (READ, {"path": inputs, "depth": 2}), (READ, {"path": "scene.cycles.samples"}),
             (READ, {"path": "scene.cycles.feature_set"}),
             (READ, {"path": 'data.node_groups["Planted Rock"].nodes', "depth": 2, "limit": 64}),
             (FILE, {}), (FILE, {"file": "stages/other.blend"}),
             (FILE, {"file": "../Client X/alice/secret.blend"}), (FILE, {"file": "stages/corrupt.blend"})]
    planted = ('"identifier":"Detail"', state["height"], '"label":"Height"', '"value":64', '"found":false',
               '"truncated":true,"note":"More rows exist: read again with offset', "Planted Orphan Material",
               "libs/gone.blend", "Planted In Other File", "OUTSIDE_WORKSPACE", "UNREADABLE")
    claude = ClaudeStandIn(execute)
    try:
        status = claude.run("What are the real socket names, and what is unused in this file?", plan(*reads),
                            plan(reply="Read."))
        check("claude_turn_completes_through_every_failure", status == "completed" and len(claude.prompts) == 2,
              [status, claude.system_rows()])
        missing = [fact for fact in planted if fact not in claude.prompts[1]]
        check("claude_model_receives_the_answers", not missing, missing)
        # Each failure is a lookup's data (plan_recovery): no step of the plan is reported as failed.
        head, body = claude.prompts[1].split("\n", 1)
        check("claude_failures_are_answers_not_failed_lookups", head.startswith("Host confirms these native operations")
              and json.loads(body)["result"]["success"] is True, head)
        report["characters"]["claude_results_prompt"] = len(claude.prompts[1])
        bpy.context.scene.cycles.samples = 48
        status = claude.run("And now?", plan((READ, {"path": "scene.cycles.samples"})), plan(reply="48."))
        check("claude_second_read_shows_the_edit", status == "completed" and '"value":48' in claude.prompts[1])
    finally:
        claude.close()
    codex = CodexStandIn(execute)
    try:
        steps = [("tool", name, arguments) + ((CODEX_DEFERRED_NAMESPACE,) if name == FILE else ()) for name, arguments in reads]
        status = codex.run("What are the real socket names, and what is unused in this file?", steps + [final("Read.")])
        texts = codex.answer_texts()
        check("codex_turn_completes_through_every_failure", status == "completed" and len(texts) == len(reads)
              and all(success for success, _answer in codex.answers()), status)
        joined = "\n".join(texts)
        missing = [fact for fact in planted if fact.replace('"value":64', '"value":48') not in joined]
        check("codex_model_receives_the_answers", not missing, missing)
        bpy.context.scene.cycles.samples = 32
        status = codex.run("And now?", [("tool", READ, {"path": "scene.cycles.samples"}), final("32.")])
        check("codex_second_read_shows_the_edit", status == "completed" and codex.answers()[0][1]["property"]["value"] == 32)
    finally:
        codex.close()


def main():
    work = tempfile.TemporaryDirectory(dir=str(OUT))
    planted = fixtures.plant(work.name)
    state.update(planted, executor=BlenderExecutor(bpy.context.window_manager.windows[0]))
    state["executor"].studio_context = SimpleNamespace(files=FileWorkspace(planted["project"]))
    bpy.app.handlers.depsgraph_update_post.append(on_update)
    state["dirty"] = bpy.data.is_dirty
    check("fixture_has_both_libraries", len(bpy.data.libraries) == 2)
    planted_answers()
    withheld_answers()
    attribute_storage()
    file_answers()
    failure_answers()
    check("reads_caused_no_dirty_flag_and_no_update", bpy.data.is_dirty is state["dirty"] and updates[0] == 0, updates)
    sweep()
    check("sweep_caused_no_dirty_flag_and_no_update", bpy.data.is_dirty is state["dirty"] and updates[0] == 0, updates)
    staging()
    after_an_edit()
    controller_path()
    runtimes()
    everything = "\n".join(answers)
    leaks = [secret for secret in fixtures.SECRETS + (str(planted["outside"]), str(planted["project"]), work.name)
             if secret in everything]
    check("nothing_private_in_any_answer", not leaks, leaks)
    report["observed"]["answers"] = {"count": len(answers), "characters": len(everything),
                                     "largest": max(len(text) for text in answers)}
    report["success"] = True


try:
    main()
except Exception:
    report.update(success=False, error=traceback.format_exc())
(OUT / "read-tools.json").write_text(json.dumps(report, indent=2))
print("ANGELO_READ_TOOLS", json.dumps({"success": report.get("success"), "checks": len(report["checks"])}), flush=True)
if not report.get("success"):
    raise RuntimeError(report["error"])
