# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An approved script never leaves a mesh Blender cannot draw or evaluate, in a real background Blender.

Blender's low-level mesh API (``from_pydata``, ``vertices.add``/``loops.add``/``polygons.add``, ``foreach_set``)
writes the arrays it is given and checks nothing: a face that names a vertex the mesh does not have, a face whose
corners lie outside the corner array, an edge between vertices that are not there. Blender then reads past the
arrays at the next evaluation, modifier or redraw, which is a crash or wrong memory, not an error a script can
catch. A person typing in the Python console learns to call ``Mesh.validate()``; a model's script is run as written,
and the host reads the scene back (bounds, revisions) straight after it.

Each case is one approved ``angelo_execute_python`` call through Angelo's own route in this background process
(controller, the person's review, ``angelo.approve``, the executor), as ``test_blender_script_document_replaced``.
After it, every mesh of the file must be one ``Mesh.validate()`` finds nothing wrong with, the object the script
made must still exist, and the process must still read its document. The cases differ in which array is wrong and
in how the script wrote it, and one adds a Subdivision modifier so the evaluation runs through a modifier; the
controls are valid meshes written the same ways, which must come through untouched (same counts). Two cases
rewrite the corners of a mesh the file already had, in place and with the same counts: one out of range, one soundly.
Two more rewrite in place, with the same counts, a mesh the first check never reached (external audit, round 2,
F1): one of 600,000 vertices whose one edge is set to (0, 600001), which cost more to validate than that check's
whole budget for unchanged meshes, and one small mesh among 4,000 others, more than that budget covered in one
script. Each must be repaired by the very call that wrote it, and reported.
A script that left an unsound mesh is told so in its result (``meshes_repaired``: the mesh and its counts before
and after Blender's own ``Mesh.validate()``); a sound one is told nothing.

Only a mesh whose topology a script could have written is validated (its topology seal is broken or was never
set; ``test_blender_mesh_topology_seal`` proves the seal on every way to write). ``sequence`` runs twenty-seven
approved scripts on one document that holds the audit's large mesh and the crowd, and reads what each call
validated: the first validates every mesh once, a script that touches nothing validates NONE, a sound rewrite of
the large mesh validates that one mesh, the audit's rewrite and a rewrite of one mesh of the crowd are each
repaired by the call that wrote them. A script that only moves a vertex gets that mesh's coordinates scanned and
nothing validated; one that writes a coordinate that is not a finite number is repaired by its own call (the
one thing ``validate`` does for positions). The twenty calls after validate and scan nothing, and leave every
mesh sound and every coordinate finite.
``ANGELO_QA_CASES`` (comma-separated) runs only the named cases: a crash repro of one. ``ANGELO_QA_MEASURE=1`` also
times the check on scenes it builds (many small meshes, many mid-sized ones, one heavy one, several heavy ones)
and writes the medians into the report (``measure``): the first check of a file, the check once every mesh is
sealed, and four scripts run through the executor with the check and without it, in turn (ABAB): one that
touches nothing, one that rewrites one mesh's edges soundly, the audit's rewrite of one edge out of range, and
one that writes one mesh's coordinates back.
Not a check: numbers for ``agent_execution.rst``.

Writes ``script-invalid-mesh.json`` in ``ANGELO_QA_DIR``; exits 1 on any failed check.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).
from qa_person import as_person  # noqa: E402  The person's own action (C's harness scope).

import json
import tempfile
import time
import traceback
import uuid
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(os.environ.get("ANGELO_QA_DIR") or tempfile.mkdtemp(prefix="angelo-script-invalid-mesh-"))
OUT.mkdir(parents=True, exist_ok=True)
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_CONNECTIONS_DIR=str(OUT / "accounts"),
                  ANGELO_STATE_DIR=str(OUT / "state"))
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ui  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from qa_person import reviewing  # noqa: E402
BlenderExecutor = reviewing(BlenderExecutor)   # Each script runs as one the person approved (qa_person).
from angelo_agent.controller import pump_controller_tools  # noqa: E402
from angelo_agent.headless_main import _prepare_preferences, _register  # noqa: E402

DOCUMENT = OUT / "document.blend"
LINK = ("obj = bpy.data.objects.new({name!r}, mesh)\nbpy.context.scene.collection.objects.link(obj)\n")
TRIANGLE = "[(0, 0, 0), (1, 0, 0), (0, 1, 0)]"
# name -> (code that makes the object ``name`` with its mesh, whether the mesh it writes is valid).
CASES = {
    "valid_from_pydata": ("mesh = bpy.data.meshes.new('M')\nmesh.from_pydata(%s, [], [(0, 1, 2)])\n" % TRIANGLE, True),
    "valid_low_level": ("mesh = bpy.data.meshes.new('M')\nmesh.vertices.add(3)\n"
                        "mesh.vertices.foreach_set('co', [0, 0, 0, 1, 0, 0, 0, 1, 0])\nmesh.loops.add(3)\n"
                        "mesh.loops.foreach_set('vertex_index', [0, 1, 2])\nmesh.polygons.add(1)\n"
                        "mesh.polygons.foreach_set('loop_start', [0])\nmesh.update(calc_edges=True)\n", True),
    "face_names_a_missing_vertex": ("mesh = bpy.data.meshes.new('M')\nmesh.from_pydata(%s, [], [(0, 1, 7)])\n"
                                    % TRIANGLE, False),
    "edge_names_a_missing_vertex": ("mesh = bpy.data.meshes.new('M')\nmesh.from_pydata(%s, [(0, 99)], [])\n"
                                    % TRIANGLE, False),
    # validate() also sets a coordinate that is not a finite number to zero.
    "position_not_a_number": ("mesh = bpy.data.meshes.new('M')\n"
                              "mesh.from_pydata([(0, 0, 0), (1, 0, float('nan')), (0, 1, 0)], [], [(0, 1, 2)])\n", False),
    "corner_rewritten_out_of_range": ("mesh = bpy.data.meshes.new('M')\nmesh.from_pydata(%s, [], [(0, 1, 2)])\n"
                                      "mesh.loops.foreach_set('vertex_index', [0, 1, 500000])\n" % TRIANGLE, False),
    "face_past_the_corner_array": ("mesh = bpy.data.meshes.new('M')\nmesh.vertices.add(3)\n"
                                   "mesh.vertices.foreach_set('co', [0, 0, 0, 1, 0, 0, 0, 1, 0])\nmesh.loops.add(3)\n"
                                   "mesh.loops.foreach_set('vertex_index', [0, 1, 2])\nmesh.polygons.add(2)\n"
                                   "mesh.polygons.foreach_set('loop_start', [0, 9])\n", False),
    "missing_vertex_under_a_modifier": ("mesh = bpy.data.meshes.new('M')\n"
                                        "mesh.from_pydata(%s, [], [(0, 1, 2), (2, 1, 40)])\n" % TRIANGLE, False),
    # A mesh the file already had (``EXISTING``, made before the script), its corners rewritten in place: the same
    # counts before and after, so nothing but its arrays says the script touched it.
    "existing_mesh_rewritten_in_place": ("mesh = bpy.data.meshes['Existing']\n"
                                         "mesh.loops.foreach_set('vertex_index', [0, 1, 77])\nmesh.update()\n", False),
    "existing_mesh_rewritten_soundly": ("mesh = bpy.data.meshes['Existing']\n"
                                        "mesh.loops.foreach_set('vertex_index', [0, 1, 2])\nmesh.update()\n", True),
}
LARGE = 600_000   # Vertices of the audit's mesh: with its one edge, more than the first check's whole budget.
CROWD = 4_000     # Small meshes beside the one rewritten: more than that budget reached in one script.
CASES.update({
    "large_mesh_rewritten_in_place": ("mesh = bpy.data.meshes['Large']\n"
                                      "mesh.edges.foreach_set('vertices', [0, %d])\n" % (LARGE + 1), False),
    "mesh_among_many_rewritten_in_place": ("mesh = bpy.data.meshes['Zz target']\n"
                                           "mesh.loops.foreach_set('vertex_index', [0, 1, 77])\n", False),
})


def _triangle(name):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    return mesh


def _existing():
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Existing object", _triangle("Existing")))


def _large():
    mesh = bpy.data.meshes.new("Large")
    mesh.vertices.add(LARGE)
    mesh.edges.add(1)
    mesh.edges.foreach_set("vertices", [0, 1])
    mesh.update()
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Large object", mesh))


def _crowd():
    for index in range(CROWD):
        _triangle("Crowd %04d" % index)
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Target object", _triangle("Zz target")))


# name -> what the file holds, sound, before the script: the meshes a script rewrites in place.
SETUP = {"existing_mesh_rewritten_in_place": _existing, "existing_mesh_rewritten_soundly": _existing,
         "large_mesh_rewritten_in_place": _large, "mesh_among_many_rewritten_in_place": _crowd}
MODIFIER = "obj.modifiers.new('Subdivision', 'SUBSURF')\n"
checks = {}
report = {"cases": {}, "checks": checks}


def code_of(name):
    return ("import bpy\n" + CASES[name][0] + LINK.format(name=name)
            + (MODIFIER if name.endswith("_under_a_modifier") else ""))


def apply_mutation(target, call):
    with bpy.context.temp_override(window=ui.main_window()):
        __import__("angelo_agent.grant_guard").grant_guard.stand_in().approve('EXEC_DEFAULT', True, call_id=call.id, session_id=target.state["id"])


def run_case(name, code=None):
    """One approved script on the saved document (``code``: on the document as it is now); the tool result, or
    None when none came within a minute."""
    if code is None:
        bpy.ops.wm.open_mainfile(filepath=str(DOCUMENT), load_ui=False, use_scripts=False)
        if name in SETUP:   # The file's own meshes, sound, there before the script.
            SETUP[name]()
    controller = ui.Controller(str(OUT / "state"), session_id=str(uuid.uuid4()))
    controller.state["options"] = dict(controller.state.get("options") or {}, approval="ASK")
    controller.set_document(ui.document_key())
    ui._controllers[controller.state["id"]] = controller
    controller.open_external_read()
    answers = []
    call_id = "q-" + name
    controller.request_tool(call_id, "angelo_execute_python",
                            {"description": name, "code": code or code_of(name)}, answers.append)
    key, approved, deadline = controller.state["id"] + "/" + call_id, False, time.monotonic() + 60
    try:
        while not answers and time.monotonic() < deadline:
            controller.pump_events()
            pump_controller_tools([controller], 0, BlenderExecutor(ui.main_window()), ui.document_key(), apply_mutation)
            call = controller.pending.get(key)
            if call is not None and call.approval and not approved and not call.executing:
                as_person(controller.review_script, call)   # The person's Review of the exact code.
                approved = True
                apply_mutation(controller, call)
            time.sleep(0.005)
    finally:
        ui._controllers.pop(controller.state["id"], None)
    return answers[0] if answers else None


NOTHING = "result = {'ok': True}\n"
SOUND_REWRITE = "import bpy\nbpy.data.meshes['Large'].edges.foreach_set('vertices', [0, 1])\n"
AUDIT_REWRITE = "import bpy\nbpy.data.meshes['Large'].edges.foreach_set('vertices', [0, %d])\n" % (LARGE + 1)
CROWD_REWRITE = "import bpy\nbpy.data.meshes['Zz target'].loops.foreach_set('vertex_index', [0, 1, 77])\n"
MOVED = "import bpy\nbpy.data.meshes['Large'].vertices[0].co = (1.0, 2.0, 3.0)\n"
NOT_A_NUMBER = ("import bpy\nbpy.data.meshes['Zz target'].vertices.foreach_set("
                "'co', [float('nan'), 0, 0, 1, float('inf'), 0, 0, 1, 0])\n")


def sequence():
    """Twenty-seven approved scripts on ONE document holding the audit's mesh and the crowd: what each validated."""
    # Every mesh is sealed by the first script's check; each rewrite after it is of a sealed mesh, in place.
    from angelo_agent import script_mesh_check
    real, outcomes = script_mesh_check.after, []

    def recording(bpy_module, prior=None):
        outcomes.append(real(bpy_module, prior))
        return outcomes[-1]

    bpy.ops.wm.open_mainfile(filepath=str(DOCUMENT), load_ui=False, use_scripts=False)
    _large()
    _crowd()
    meshes = len(bpy.data.meshes)
    steps = [("first", NOTHING), ("untouched", NOTHING), ("sound_rewrite", SOUND_REWRITE), ("audit", AUDIT_REWRITE),
             ("one_of_the_crowd", CROWD_REWRITE), ("positions_moved", MOVED), ("position_not_a_number", NOT_A_NUMBER)]
    steps += [("after_%02d" % call, NOTHING) for call in range(1, 21)]
    script_mesh_check.after = recording
    try:
        results = [(label, run_case("sequence-" + label, code) or {}) for label, code in steps]
    finally:
        script_mesh_check.after = real
    seen = [(outcome or {}).get("validated") for outcome in outcomes]
    scanned = [(outcome or {}).get("scanned") for outcome in outcomes]
    rows = [[row.get("mesh") for row in result.get("meshes_repaired") or []] for _label, result in results]
    report["sequence"] = {"meshes": meshes, "validated": seen, "scanned": scanned, "repaired": rows,
                          "success": [result.get("success") for _label, result in results]}
    checks["sequence_every_script_succeeds_and_is_checked_once"] = (
        all(result.get("success") is True for _label, result in results) and len(seen) == len(steps))
    checks["sequence_the_first_script_validates_every_mesh_once"] = seen[:1] == [meshes] and meshes > CROWD
    checks["sequence_a_script_that_touches_nothing_validates_none"] = seen[1:2] == [0]
    checks["sequence_a_sound_rewrite_validates_that_mesh_only"] = seen[2:3] == [1] and rows[2] == []
    checks["sequence_the_audits_rewrite_is_repaired_by_its_own_call"] = seen[3:4] == [1] and rows[3] == ["Large"]
    checks["sequence_one_mesh_of_the_crowd_is_repaired_by_its_own_call"] = (seen[4:5] == [1]
                                                                            and rows[4] == ["Zz target"])
    # Coordinates alone: scanned, never validated unless one is not a finite number, which its own call repairs.
    checks["sequence_moved_vertices_are_scanned_not_validated"] = (seen[5:6] == [0] and scanned[5:6] == [1]
                                                                    and rows[5] == [])
    checks["sequence_a_position_not_a_number_is_repaired_by_its_own_call"] = (
        seen[6:7] == [1] and scanned[6:7] == [1] and rows[6] == ["Zz target"])
    checks["sequence_nothing_before_them_was_scanned"] = scanned[:5] == [0] * 5
    checks["sequence_the_twenty_calls_after_validate_nothing"] = (
        seen[7:] == [0] * 20 and scanned[7:] == [0] * 20 and not any(rows[7:]))
    import math
    target = bpy.data.meshes["Zz target"]
    checks["sequence_leaves_every_coordinate_finite"] = all(
        math.isfinite(value) for vertex in target.vertices for value in vertex.co)
    checks["sequence_leaves_every_mesh_valid"] = not [m.name for m in bpy.data.meshes if m.validate(verbose=False)]


def main():
    _prepare_preferences(bpy)
    _register(bpy, ui)
    bpy.ops.wm.save_as_mainfile(filepath=str(DOCUMENT))
    checks["this_blender_seals_mesh_topology"] = "is_topology_sealed" in bpy.types.Mesh.bl_rna.properties.keys()
    wanted = [c for c in os.environ.get("ANGELO_QA_CASES", "").split(",") if c] or list(CASES)
    for name in wanted:
        print("ANGELO_SCRIPT_INVALID_MESH case", name, flush=True)   # The last line before a crash names its case.
        result = run_case(name) or {}
        obj = bpy.data.objects.get(name)
        mesh = obj.data if obj is not None else None
        counts = None if mesh is None else (len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons))
        # validate() returns True when it had to correct the mesh: every mesh must already be sound.
        corrected = sorted(m.name for m in bpy.data.meshes if m.validate(verbose=False))
        after = None if mesh is None else (len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons))
        report["cases"][name] = {"success": result.get("success"), "error_code": result.get("error_code"),
                                 "counts": counts, "counts_after_validate": after, "left_invalid": corrected}
        checks[name + "_answered"] = bool(result)
        checks[name + "_object_exists"] = obj is not None
        checks[name + "_leaves_every_mesh_valid"] = not corrected
        rows = result.get("meshes_repaired") or []
        report["cases"][name]["meshes_repaired"] = rows
        report["cases"][name]["meshes_unchecked"] = result.get("meshes_unchecked")
        checks[name + "_leaves_no_mesh_unchecked"] = "meshes_unchecked" not in result
        if CASES[name][1]:
            checks[name + "_succeeds_untouched"] = result.get("success") is True and counts == after and counts == (3, 3, 3, 1)
            checks[name + "_reports_no_repair"] = not rows and "mesh_repair" not in result
        else:   # The model is told: which mesh, what was found, and its counts before and after.
            checks[name + "_says_what_was_repaired"] = (
                result.get("success") is True and len(rows) == 1 and isinstance(rows[0].get("found"), str)
                and bool(rows[0]["found"]) and "still" not in rows[0] and isinstance(result.get("mesh_repair"), str)
                and set(rows[0].get("before", {})) == set(rows[0].get("after", {}))
                == {"vertices", "edges", "corners", "faces"})
        bpy.context.view_layer.update()   # The evaluation a redraw would run, modifiers included.
        window = ui.main_window()
        checks[name + "_process_reads_its_document"] = window is not None and bool(window.view_layer.name)
    if not os.environ.get("ANGELO_QA_CASES"):
        print("ANGELO_SCRIPT_INVALID_MESH case sequence", flush=True)
        sequence()


def grid(name, side):
    """A sound mesh of ``side`` x ``side`` quads, written through numpy as a heavy script would."""
    import numpy
    count = side + 1
    xs, ys = numpy.meshgrid(numpy.arange(count, dtype=numpy.float32), numpy.arange(count, dtype=numpy.float32))
    coords = numpy.stack([xs.ravel(), ys.ravel(), numpy.zeros(count * count, dtype=numpy.float32)], axis=1)
    first = (numpy.arange(side)[:, None] * count + numpy.arange(side)[None, :]).ravel().astype(numpy.int32)
    corners = numpy.stack([first, first + 1, first + count + 1, first + count], axis=1).ravel()
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(count * count)
    mesh.vertices.foreach_set("co", coords.ravel())
    mesh.loops.add(len(corners))
    mesh.loops.foreach_set("vertex_index", corners)
    mesh.polygons.add(side * side)
    mesh.polygons.foreach_set("loop_start", numpy.arange(side * side, dtype=numpy.int32) * 4)
    mesh.update(calc_edges=True)
    return mesh


def measure():
    """Milliseconds of the check on scenes of four shapes, and of four scripts with the check and without it;
    nothing here is a pass or a fail."""
    import statistics
    from angelo_agent import script_mesh_check

    def median(values):
        return round(statistics.median(values), 3)

    def timed(run, repeats=7):
        times = []
        for _ in range(repeats):
            started = time.perf_counter()
            run()
            times.append((time.perf_counter() - started) * 1000)
        return median(times)

    # Each script works on the scene's first mesh. The sound rewrite reads its edges and writes them back; the
    # audit's rewrite names a vertex the mesh does not have, with the same counts.
    scripts = {
        "untouched": NOTHING,
        "one_mesh_rewritten_soundly": (
            "import bpy, numpy\nmesh = bpy.data.meshes['M0']\n"
            "values = numpy.empty(len(mesh.edges) * 2, dtype=numpy.int32)\n"
            "mesh.edges.foreach_get('vertices', values)\nmesh.edges.foreach_set('vertices', values)\n"),
        "one_edge_out_of_range": ("import bpy\nmesh = bpy.data.meshes['M0']\n"
                                  "mesh.edges[0].vertices = (0, len(mesh.vertices) + 1)\n"),
        "one_mesh_positions_written": (
            "import bpy, numpy\nmesh = bpy.data.meshes['M0']\n"
            "values = numpy.empty(len(mesh.vertices) * 3, dtype=numpy.float32)\n"
            "mesh.vertices.foreach_get('co', values)\nmesh.vertices.foreach_set('co', values)\n"),
    }
    numbers, real = {}, script_mesh_check.after
    for label, meshes, side in (("10000_meshes_of_4_quads", 10000, 2), ("1000_meshes_of_1024_quads", 1000, 32),
                                ("1_mesh_of_1000000_quads", 1, 1000), ("4_meshes_of_1000000_quads", 4, 1000)):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        for index in range(meshes):
            grid("M%d" % index, side)
        elements = sum(len(m.vertices) + len(m.edges) + len(m.loops) + len(m.polygons) for m in bpy.data.meshes)
        started = time.perf_counter()
        first = real(bpy)   # No mesh was ever found sound here: every one is validated, once.
        entry = numbers[label] = {
            "meshes": meshes, "elements": elements,
            "first_check_ms": round((time.perf_counter() - started) * 1000, 3),
            "first_check_validated": first.get("validated"),
            # What every later script pays in this scene while it touches no mesh.
            "sealed_check_ms": timed(lambda: real(bpy)), "sealed_check_validated": real(bpy).get("validated"),
            "repaired_when_all_are_sound": len(first["repaired"]), "unchecked": len(first["unchecked"])}
        # ABAB: the same script through the executor with the check and without it, in turn (the executor's own
        # time for the call, native_exec_ms): what the check adds to that script's call, and what it validated.
        executor = BlenderExecutor(ui.main_window())
        for name, code in scripts.items():
            arms, validated = {"with_check": [], "without_check": []}, []

            def checking(bpy_module, prior=None):
                outcome = real(bpy_module, prior)
                validated.append([outcome.get("validated"), outcome.get("scanned")])
                return outcome
            try:
                for pair in range(8):
                    for arm in (("with_check", "without_check") if pair % 2 == 0 else ("without_check", "with_check")):
                        script_mesh_check.after = checking if arm == "with_check" else (lambda bpy, prior=None: None)
                        result = executor.execute("angelo_execute_python", {"description": name, "code": code})
                        if result.get("success") is not True:
                            raise RuntimeError("the script did not succeed: %r" % (result.get("error_code"),))
                        arms[arm].append(result["native_exec_ms"])
                entry[name] = {
                    "script_call_ms": {arm: {"median": median(values), "min": min(values), "max": max(values),
                                             "n": len(values)} for arm, values in arms.items()},
                    "check_adds_ms": round(median(arms["with_check"]) - median(arms["without_check"]), 3),
                    "validated_and_scanned_by_each_checked_call": validated}
            except Exception as error:   # noqa: BLE001 - a number that could not be taken is said, never guessed.
                entry[name] = {"script_call_error": "%s: %s" % (type(error).__name__, str(error)[:200])}
            finally:
                script_mesh_check.after = real
                real(bpy)   # The unchecked arm's last write is checked before the next script kind starts.
    return numbers

try:
    main()
    if os.environ.get("ANGELO_QA_MEASURE") == "1":
        report["measure"] = measure()
except Exception:   # Reported as a failed check, never a silent pass.
    checks["no_error"] = False
    report["error"] = traceback.format_exc()
report["success"] = bool(checks) and all(checks.values())
(OUT / "script-invalid-mesh.json").write_text(json.dumps(report, indent=2))
print("ANGELO_SCRIPT_INVALID_MESH", json.dumps({"success": report["success"], "cases": report["cases"],
                                                "measure": report.get("measure")}), flush=True)
sys.exit(0 if report["success"] else 1)
