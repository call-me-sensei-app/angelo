# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An object with several particle systems is deleted together with their settings, and nothing reads freed memory.

Freeing an object frees its particle systems one by one, and each looked at the object's other particle systems'
settings (``psys_free``). When the object goes together with those settings (an orphans purge, a multiple delete,
packing a linked object), the settings were freed first: plain Blender 5.3 read freed memory there (found by
AddressSanitizer while testing the Redo fix on packed linked data, doc/angelo/agent_execution.rst). A release
build reads it silently, so this suite proves the fix only on a build with AddressSanitizer, where the read ends the
process; elsewhere it checks that the data is gone as asked.

Each case, in a new session: an object with two particle systems (one rendered as objects, so that the flag the
loop computes matters), then one of: removing it and both settings in one call; unlinking it and purging orphans;
linking it packed from a library file (the linked original is deleted with its settings). Freeing also read a
system's own settings to tell whether its particles had hair keys; original data holds no particles (evaluated
copies do), so that read is not reached here. The add-on is disabled first. Run with ``--background
--factory-startup --python-exit-code 1``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import traceback
from pathlib import Path

import addon_utils
import bpy

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-particle-object-delete-qa"))
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "particle-object-delete.json"
report = {"success": False, "blender": bpy.app.version_string, "cases": {}, "running": None}
CASES = ("removed with its settings", "purged as an orphan", "linked packed")


def save_report():
    REPORT.write_text(json.dumps(report, indent=2))


def emitter(name):
    mesh = bpy.data.meshes.new(name + " Mesh")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0)], [], [(0, 1, 2)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    for index in range(2):
        obj.modifiers.new("Particles %d" % index, 'PARTICLE_SYSTEM')
    obj.particle_systems[0].settings.render_type = 'OBJECT'
    return obj


def case(name):
    report["running"] = name
    facts = report["cases"][name] = {}
    save_report()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if name == "linked packed":
        library = OUT / "emitters.blend"
        bpy.data.libraries.write(str(library), {emitter("Kit Emitter")}, fake_user=True)
        bpy.ops.wm.read_factory_settings(use_empty=True)
        with bpy.data.libraries.load(str(library), link=True, pack=True) as (_source, target):
            target.objects = ["Kit Emitter"]
        packed = target.objects[0]
        facts.update(packed=packed.is_linked_packed, systems=len(packed.particle_systems),
                     settings=sorted(system.settings.name for system in packed.particle_systems))
        assert packed.is_linked_packed and len(packed.particle_systems) == 2, json.dumps(facts)
    else:
        obj = emitter("Emitter")
        settings = [system.settings for system in obj.particle_systems]
        if name == "removed with its settings":
            bpy.data.batch_remove([obj] + settings)
        else:
            bpy.context.scene.collection.objects.unlink(obj)
            bpy.data.orphans_purge(do_local_ids=True, do_recursive=True)
        facts.update(objects=len(bpy.data.objects), particles=len(bpy.data.particles))
        assert not bpy.data.objects and not bpy.data.particles, "not deleted: %s" % json.dumps(facts)
    facts["passed"] = True


def run():
    addon_utils.disable("angelo_agent", default_set=False)   # Blender only.
    for name in CASES:
        case(name)
    report["running"] = None
    report["success"] = all(facts.get("passed") for facts in report["cases"].values()) and len(
        report["cases"]) == len(CASES)


try:
    run()
except Exception:
    report.update(success=False, error=traceback.format_exc())
finally:
    save_report()
    print("ANGELO_PARTICLE_OBJECT_DELETE", json.dumps({"success": report["success"], "running": report["running"],
                                                      "error": (report.get("error") or "")[-600:]}), flush=True)
    if not report["success"]:
        sys.exit(1)
