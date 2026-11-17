# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the agent-script confinement costs per script, in the app (doc/angelo/script_confinement.rst, "Cost").

Run in a built app, background: ``blender --background --factory-startup --python benchmark_confinement.py --
OUT.json``. Angelo is enabled as it starts (its confinement installed); then two scripts each run WARM times untimed
and RUNS times timed, a new scene object set for each run (made outside the timing):

* ``trivial``: ``x = 1``, the confinement's own cost;
* ``modeling``: twenty bevelled cubes with a material, a typical agent edit;
* ``allocation``: a million-item list and a 200,000-entry dict (what the allocator hook costs at its worst);
* ``bmesh``: a 200,000-vertex grid built and every vertex moved through bmesh;
* ``generators``, ``asyncio``, ``numpy``: generator- and coroutine-heavy work, and an import, inside the script;

then the cost of one step of a generator a script left, resumed outside it (``step_ms``: a region per step with the
guard), and of one ``io.open_code`` by Angelo's own code once a script has run (``open_code_us``).

With ``_bpy._angelo_guard`` (the confinement's build) a run goes through ``script_confinement.run_confined``, as the
executor runs it; without it (the base build) through ``exec`` of the same code compiled under the same file name,
as the base executor did. The caller alternates the two builds (ABAB) and pools each arm's per-script times; this
file only measures one process. No provider, no network.
"""

import json
import statistics
import sys
import time

import addon_utils
import bpy

RUNS, WARM = 30, 5
HEAVY_RUNS, HEAVY_WARM = 10, 2   # The heavy scripts: four ABAB rounds still give 40 timed runs per arm.
MARK = "<angelo-reviewed-script>"
SCRIPTS = {
    "trivial": "x = 1",
    "modeling": (
        "import bpy\n"
        "material = bpy.data.materials.new('Bench')\n"
        "for index in range(20):\n"
        "    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(index * 0.6, 0, 0))\n"
        "    cube = bpy.context.active_object\n"
        "    bevel = cube.modifiers.new('Bevel', 'BEVEL')\n"
        "    bevel.width = 0.05\n"
        "    cube.data.materials.append(material)\n"),
    "allocation": ("items = [index * 2 for index in range(1_000_000)]\n"
                   "names = {index: str(index) for index in range(200_000)}\nresult = len(items) + len(names)\n"),
    "bmesh": ("import bmesh, bpy\nmesh = bpy.data.meshes.new('Grid')\nbm = bmesh.new()\n"
              "bmesh.ops.create_grid(bm, x_segments=446, y_segments=446, size=1.0)\n"
              "for vertex in bm.verts:\n    vertex.co.z += 0.1\nbm.to_mesh(mesh)\nbm.free()\n"),
    # Generators and coroutines inside the script (a region): none is left, so none is watched step by step.
    "generators": ("total = sum(x for x in range(200_000))\n"
                   "def numbers():\n    for index in range(200_000):\n        yield index\n"
                   "total += sum(numbers())\n"),
    "asyncio": ("import asyncio\nasync def step(index):\n    await asyncio.sleep(0)\n    return index\n"
                "async def main():\n    return sum([await step(index) for index in range(2000)])\n"
                "result = asyncio.run(main())\n"),
    "numpy": "import numpy\nresult = int(numpy.arange(1000).sum())\n",
}
HEAVY = ("allocation", "bmesh")
STEPS = 400   # Steps of a generator a script left, resumed outside it (each one a region with the guard).


def clean():
    for item in list(bpy.data.objects):
        bpy.data.objects.remove(item, do_unlink=True)
    for collection in (bpy.data.meshes, bpy.data.materials):
        for item in list(collection):
            if item.users == 0:
                collection.remove(item)


def main(out):
    addon_utils.enable("angelo_agent", default_set=False, persistent=False)
    try:
        import _bpy
        native = getattr(_bpy, "_angelo_guard", None) is not None
    except ImportError:
        native = False
    prepare = lambda source: None   # noqa: E731  Outside the timing: approval is not the confinement's cost.
    if native:
        from angelo_agent import script_confinement
        sys.path.insert(0, __file__.rsplit("/", 1)[0])
        from confinement_attacks_admit import admit

        def prepare(source):
            admit("bench", source)

        def run_text(source):
            script_confinement.run_confined(source, {"__name__": "__angelo_script__"}, None, "bench")
    else:
        def run_text(source):
            exec(compile(source, MARK, "exec"), {"__name__": "__angelo_script__"})
    report = {"native": native, "runs": RUNS, "ms": {}}
    for name, source in SCRIPTS.items():
        times = []
        runs, warm = (HEAVY_RUNS, HEAVY_WARM) if name in HEAVY else (RUNS, WARM)
        for index in range(warm + runs):
            clean()
            prepare(source)
            started = time.perf_counter()
            run_text(source)
            spent = (time.perf_counter() - started) * 1000
            if index >= warm:
                times.append(spent)
        report["ms"][name] = times
        report[name] = {"median": statistics.median(times), "p95": sorted(times)[int(0.95 * (len(times) - 1))]}
    # A generator the script left, resumed outside it: per-step cost.
    holder = {}
    left = "def later():\n    while True:\n        yield 1\nHELD['generator'] = later()\n"
    prepare(left)
    if native:
        script_confinement.run_confined(left, {"__name__": "__angelo_script__", "HELD": holder}, None, "bench")
    else:
        exec(compile(left, MARK, "exec"), {"__name__": "__angelo_script__", "HELD": holder})
    generator = holder["generator"]
    for _ in range(20):
        next(generator)
    started = time.perf_counter()
    for _ in range(STEPS):
        next(generator)
    report["step_ms"] = (time.perf_counter() - started) * 1000 / STEPS
    # Angelo's own code loads (io.open_code, judged once a script has run): per call.
    import io
    paths = [module.__file__ for module in list(sys.modules.values())
             if getattr(module, "__file__", None) and str(module.__file__).endswith(".py")][:200]
    started = time.perf_counter()
    for path in paths:
        io.open_code(path).close()
    report["open_code_us"] = (time.perf_counter() - started) * 1e6 / max(1, len(paths))
    report["open_code_files"] = len(paths)
    with open(out, "w") as handle:
        json.dump(report, handle)
    print("CONFINEMENT_BENCH", json.dumps({key: report[key] for key in report if key != "ms"}))


main(sys.argv[sys.argv.index("--") + 1])
