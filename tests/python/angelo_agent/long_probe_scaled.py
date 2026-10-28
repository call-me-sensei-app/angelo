# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Probe margins with each probe's own margin, for a long candidate that declares one (``oneshot_tempest``).

    python3 long_probe_scaled.py --scenario oneshot_tempest [--blender PATH]

The same proof as ``long_probe_check`` (every probe, its origin moved sideways in eight directions square to the ray,
must still pass on the stand-in), for scenes whose parts are smaller than the fixed 5 cm move: the scenario module's
``probe_margin(check)`` gives each probe's move by one rule for every probe of that test (``oneshot_tempest``: 5 cm,
or a fifth of the target's smallest stated extent across the ray when that is smaller). A module without
``probe_margin`` gets ``long_probe_margins.MARGIN`` for every probe, exactly as ``long_probe_check``. The grader's own
probe check runs unchanged on the moved copies; results go under ``probe_margins`` in ``selfcheck.json`` (each probe
with its margin), and the run exits non-zero when any probe fails at any offset.
"""

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import long_probe_margins as proof  # noqa: E402
import scenario_specs as specs  # noqa: E402

CANDIDATES = tuple("oneshot_" + name.replace("-", "_") for name in specs.ONESHOT_LONG)


def margin_of(module, check):
    rule = getattr(module, "probe_margin", None)
    return rule(check) if rule else proof.MARGIN


def scaled_margins(bpy, scene, checks_module, module, probes):
    """``[{label, margin_m, passed, failed}]`` for every probe, each tried at its own margin in eight directions."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    report = []
    for check in probes:
        margin = margin_of(module, check)
        failed = []
        for move in proof.offsets(check["direction"], margin):
            passed, details = checks_module._check_probe(bpy, scene, depsgraph, proof.moved(check, move))
            if not passed:
                failed.append({"move": list(move), "details": details})
        report.append({"label": check["label"], "origin": check["origin"], "margin_m": margin, "passed": not failed,
                       "failed": failed})
    return report


def blender_main(argv):
    import bpy
    import scenario_checks
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=CANDIDATES, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    module = specs.module(args.scenario)
    probes = [check for check in module.CHECKS if check["kind"] == "probe"]
    report = scaled_margins(bpy, bpy.context.scene, scenario_checks, module, probes)
    path = Path(args.out) / "selfcheck.json"
    data = json.loads(path.read_text())
    data["probe_margins"] = {"margin_rule": (module.probe_margin.__doc__ or "").strip() if hasattr(module, "probe_margin")
                             else "%g m for every probe" % proof.MARGIN, "directions": 8, "probes": len(report),
                             "passed": sum(entry["passed"] for entry in report), "results": report}
    path.write_text(json.dumps(data, indent=2, default=str))
    ok = all(entry["passed"] for entry in report)
    print("LONG_PROBE_MARGINS", json.dumps({"scenario": args.scenario, "probes": len(report),
                                            "passed": data["probe_margins"]["passed"], "ok": ok}), flush=True)
    return 0 if ok else 1


def host_main(argv):
    import tempfile
    import scenario_references as references
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=CANDIDATES, required=True)
    parser.add_argument("--blender", default=references.STOCK_BLENDER)
    args = parser.parse_args(argv)
    folder = specs.references_dir(args.scenario)
    if not (folder / specs.MANIFEST).is_file():
        raise SystemExit("No references at %s: run scenario_references.py first" % folder)
    with tempfile.TemporaryDirectory(prefix="long-probe-scaled-") as scratch:
        code = references.run_job(args.blender, ["--scenario", args.scenario, "--out", str(folder)], Path(scratch),
                                  folder / "probe-margins.log", timeout=900, script=Path(__file__),
                                  blend=folder / "ground-truth.blend")
    summary = json.loads((folder / "selfcheck.json").read_text()).get("probe_margins", {})
    print("PROBE_MARGINS", args.scenario, folder, json.dumps({k: summary.get(k) for k in ("probes", "passed")}),
          "exit", code, flush=True)
    return 0 if code == 0 else 1


if __name__ == "__main__":
    try:
        import bpy  # noqa: F401
        INSIDE = True
    except ImportError:
        INSIDE = False
    if INSIDE:
        sys.exit(blender_main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []))
    sys.exit(host_main(sys.argv[1:]))
