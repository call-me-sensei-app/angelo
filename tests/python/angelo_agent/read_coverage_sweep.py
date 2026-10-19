# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Record what ``angelo_read_data`` answers across a planted document, for coverage comparison.

Not a test suite: run it once with each binary, then compare the two records with
read_coverage_compare.py. Each binary uses its own bundled ``angelo_agent`` add-on
(set ``ANGELO_READ_COVERAGE_ADDONS`` to a ``scripts/addons_core`` folder to use a
checkout's add-on instead), so a baseline build reads through its own reader::

  "$ANGELO" --background --factory-startup --disable-autoexec --python-exit-code 1 \\
    --python tests/python/angelo_agent/read_coverage_sweep.py -- OUT.json

The document is read_tools_fixtures.plant (the read-tools suite's planted file) plus
a curve, a text object, a Grease Pencil object, a hair-curves object, a point cloud,
an armature with a pose and a particle system, so catalogued getters are reached.
The dependency graph is evaluated once before the sweep, as in an interactive
session. The sweep is breadth-first from ``scene`` and ``data`` (depth 2, pages of 64,
at most ``ANGELO_READ_COVERAGE_MAX`` reads, default 4000) and records, per read and
per row, whether a value was answered (with a digest of it) or why not, and the sweep's
wall and process CPU time (``seconds``, ``cpu_seconds``); on macOS also the instructions
and cycles the process retired during the sweep (``instructions``, ``cycles``), which
other work on the machine changes far less than it changes the times. A sweep that
reaches the read limit before visiting every queued path records ``complete: false``;
raise the limit until both records are complete. No provider, network or user
document is used; the planted files live in a temporary folder.
"""

import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import bpy

assert "--factory-startup" in sys.argv, "Use a disposable factory-startup process"
HERE = Path(__file__).resolve().parent
OUT_FILE = Path(sys.argv[sys.argv.index("--") + 1]) if "--" in sys.argv else Path(tempfile.gettempdir(), "read-coverage.json")
WORK = Path(tempfile.mkdtemp(prefix="angelo-read-coverage-"))
os.environ.update(ANGELO_DISABLE_DEFAULT_CHAT="1", ANGELO_DISABLE_KEYCHAIN="1",
                  ANGELO_CONNECTIONS_DIR=str(WORK / "accounts"), ANGELO_STATE_DIR=str(WORK / "state"))
if os.environ.get("ANGELO_READ_COVERAGE_ADDONS"):
    sys.path.insert(0, os.environ["ANGELO_READ_COVERAGE_ADDONS"])
sys.path.insert(0, str(HERE))
import read_tools_fixtures as fixtures  # noqa: E402
from angelo_agent.blender_tools import BlenderExecutor  # noqa: E402
from angelo_agent.tool_specs import validate_call  # noqa: E402

MAX_READS = int(os.environ.get("ANGELO_READ_COVERAGE_MAX", "4000"))
VOLATILE = {"handle"}  # Session-specific identifiers; never compared.


def digest(value):
    def clean(item):
        if isinstance(item, dict):
            return {key: clean(entry) for key, entry in item.items() if key not in VOLATILE}
        if isinstance(item, list):
            return [clean(entry) for entry in item]
        return item
    text = json.dumps(clean(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def row_state(row):
    if "withheld" in row:
        return "W:" + str(row["withheld"])
    if "unreadable" in row:
        return "U:" + str(row["unreadable"])
    shown = {key: entry for key, entry in row.items() if key not in ("properties", "more")}
    return "V:" + digest(shown)


def plant_extras():
    """Objects whose reads reach catalogued getters (bounds, fonts, poses, particles, curves).

    Each object is planted on its own; one that this Blender cannot create is named in the
    record's ``skipped`` list instead of ending the sweep, so both binaries' records say
    which document they read.
    """
    scene, skipped = bpy.context.scene, []

    def link(name, data):
        obj = bpy.data.objects.new(name, data)
        scene.collection.objects.link(obj)
        return obj

    def curve():
        data = bpy.data.curves.new("Coverage Curve", "CURVE")
        data.splines.new("BEZIER").bezier_points.add(2)
        link("Coverage Curve", data)

    def text():
        data = bpy.data.curves.new("Coverage Text", "FONT")
        data.body = "Coverage"
        link("Coverage Text", data)

    def rig():
        armature = bpy.data.armatures.new("Coverage Armature")
        bpy.context.view_layer.objects.active = link("Coverage Rig", armature)
        bpy.ops.object.mode_set(mode="EDIT")
        armature.edit_bones.new("Coverage Bone").tail = (0, 0, 1)
        bpy.ops.object.mode_set(mode="OBJECT")

    def particles():
        emitter = bpy.data.objects["Cube"]
        emitter.modifiers.new("Coverage Particles", "PARTICLE_SYSTEM")
        emitter.particle_systems[0].settings.count = 10

    steps = (("curve", curve), ("text", text),
             ("hair", lambda: link("Coverage Hair", bpy.data.hair_curves.new("Coverage Hair"))),
             ("points", lambda: link("Coverage Points", bpy.data.pointclouds.new("Coverage Points"))),
             ("pencil", lambda: link("Coverage Pencil", bpy.data.grease_pencils.new("Coverage Pencil"))
              .data.layers.new("Coverage Layer")),
             ("rig", rig), ("particles", particles))
    for name, step in steps:
        try:
            step()
        except Exception as error:  # Recorded: the sweep still runs without this object.
            print("ANGELO_READ_COVERAGE skipped %s: %s" % (name, error), flush=True)
            skipped.append(name)
    return skipped


def read(executor, path, offset):
    arguments = validate_call("angelo_read_data", {"path": path, "depth": 2, "limit": 64, "offset": offset})
    try:
        return executor.execute("angelo_read_data", arguments)
    except Exception as error:  # Recorded, not raised: a failing read is a coverage fact.
        return {"found": False, "code": "EXCEPTION:" + type(error).__name__}


def counters():
    """``(instructions, cycles)`` this process has retired (macOS), or None where unavailable."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes
        info = (ctypes.c_uint64 * 64)()  # rusage_info_v4: a 16-byte UUID, then 64-bit counters.
        if ctypes.CDLL("/usr/lib/libproc.dylib").proc_pid_rusage(os.getpid(), 4, info) != 0:
            return None
        return int(info[2 + 29]), int(info[2 + 30])  # ri_instructions, ri_cycles
    except (OSError, AttributeError):
        return None


def sweep(executor):
    entries, queue, seen, reads = {}, ["scene", "data"], set(), 0
    started, cpu, retired = time.perf_counter(), time.process_time(), counters()
    while queue and reads < MAX_READS:
        path = queue.pop(0)
        if path in seen:
            continue
        seen.add(path)
        offset = 0
        for _page in range(6):
            result = read(executor, path, offset)
            reads += 1
            key = "%s@%d" % (path, offset)
            if not result.get("found"):
                entries[key] = "M:" + str(result.get("code"))
                break
            if result["kind"] == "PROPERTY":
                entries[key] = row_state(result["property"])
                break
            entries[key] = "K:" + result["kind"]
            rows = result.get("properties") or result.get("items") or []
            for row in rows:
                name = row.get("name") if result["kind"] == "STRUCT" else "[%d]" % row["index"]
                entries["%s|%s" % (key, name)] = row_state(row)
                for nested in row.get("properties", []):
                    entries["%s|%s.%s" % (key, name, nested.get("name"))] = row_state(nested)
                if path.count(".") + path.count("[") < 4:
                    if result["kind"] == "COLLECTION" and "withheld" not in row:
                        queue.append("%s[%d]" % (path, row["index"]))
                    elif (row.get("type") in ("POINTER", "COLLECTION") and "withheld" not in row
                          and row.get("value", 1) and row.get("length", 1)):
                        queue.append(path + "." + row["name"])
            if result.get("next_offset") is None:
                break
            offset = result["next_offset"]
    complete = not any(path not in seen for path in queue)
    timing = {"seconds": round(time.perf_counter() - started, 2), "cpu_seconds": round(time.process_time() - cpu, 2)}
    finished = counters()
    if retired and finished:
        timing.update(instructions=finished[0] - retired[0], cycles=finished[1] - retired[1])
    return entries, reads, len(seen), timing, complete


def main():
    planted = fixtures.plant(str(WORK))
    skipped = plant_extras()
    if os.environ.get("ANGELO_READ_COVERAGE_EVALUATE", "1") == "1":
        bpy.context.evaluated_depsgraph_get()
    executor = BlenderExecutor(bpy.context.window_manager.windows[0])
    from angelo_agent.files import FileWorkspace
    from types import SimpleNamespace
    executor.studio_context = SimpleNamespace(files=FileWorkspace(planted["project"]))
    entries, reads, paths, timing, complete = sweep(executor)
    import angelo_agent
    record = {"binary": bpy.app.binary_path, "addon": str(Path(angelo_agent.__file__).parent),
              "version": bpy.app.version_string, "skipped": skipped, "reads": reads, "paths": paths,
              "complete": complete, "entries": entries, **timing}
    OUT_FILE.write_text(json.dumps(record, indent=1, sort_keys=True))
    print("ANGELO_READ_COVERAGE", json.dumps({"reads": reads, "paths": paths, "entries": len(entries),
                                              "complete": complete, "out": str(OUT_FILE), **timing}),
          flush=True)


main()
