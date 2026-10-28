# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Probe margins of the long candidates' stand-ins (``scenario_oneshot_apartment``, ``scenario_oneshot_high_street``).

    python3 long_probe_margins.py --scenario oneshot_apartment [--blender PATH]

Every probe of a long candidate is aimed at a face with margin, never along a symmetry axis, through a pole or on an
edge (the roadster lesson, 2026-10-25: one ray exactly on a wheel's axis passed through the hub cap's pole and missed
four well placed wheels). This proves it on the stand-in: each probe, its origin moved ``MARGIN`` sideways in eight
directions square to the ray, must still pass (the same object kind, the same range, or still miss). The result is
recorded per probe under ``probe_margins`` in the reference folder's ``selfcheck.json``; the run exits non-zero when
any probe fails at any offset. The grader is unchanged: this runs the same ``scenario_checks`` probe on moved copies.

Host side it runs itself in a background stock Blender on the folder's ``ground-truth.blend`` with the isolated
environment of ``scenario_references``; inside Blender (``--out FOLDER``) it does the work.
"""

import argparse
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

MARGIN = 0.05   # Metres: the sideways move every probe must survive.
LONG_CANDIDATES = ("oneshot_apartment", "oneshot_high_street")


def offsets(direction, margin=MARGIN, count=8):
    """``count`` sideways moves of length ``margin``, square to ``direction``, evenly round it."""
    dx, dy, dz = direction
    length = math.sqrt(dx * dx + dy * dy + dz * dz)
    d = (dx / length, dy / length, dz / length)
    helper = (0.0, 0.0, 1.0) if abs(d[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = (d[1] * helper[2] - d[2] * helper[1], d[2] * helper[0] - d[0] * helper[2], d[0] * helper[1] - d[1] * helper[0])
    norm = math.sqrt(sum(c * c for c in u))
    u = tuple(c / norm for c in u)
    v = (d[1] * u[2] - d[2] * u[1], d[2] * u[0] - d[0] * u[2], d[0] * u[1] - d[1] * u[0])
    moves = []
    for index in range(count):
        angle = 2 * math.pi * index / count
        moves.append(tuple(round(margin * (math.cos(angle) * u[k] + math.sin(angle) * v[k]), 6) for k in range(3)))
    return moves


def moved(check, move):
    return dict(check, origin=[check["origin"][k] + move[k] for k in range(3)])


def margins(bpy, scene, checks_module, probes):
    """``[{label, passed, failed: [{move, details}]}]`` for every probe, each tried at every offset."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    report = []
    for check in probes:
        failed = []
        for move in offsets(check["direction"]):
            passed, details = checks_module._check_probe(bpy, scene, depsgraph, moved(check, move))
            if not passed:
                failed.append({"move": list(move), "details": details})
        report.append({"label": check["label"], "origin": check["origin"], "passed": not failed, "failed": failed})
    return report


def blender_main(argv):
    import bpy
    import scenario_checks
    import scenario_specs as specs
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=LONG_CANDIDATES, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    probes = [check for check in specs.module(args.scenario).CHECKS if check["kind"] == "probe"]
    report = margins(bpy, bpy.context.scene, scenario_checks, probes)
    path = Path(args.out) / "selfcheck.json"
    data = json.loads(path.read_text())
    data["probe_margins"] = {"margin_m": MARGIN, "directions": 8, "probes": len(report),
                             "passed": sum(entry["passed"] for entry in report), "results": report}
    path.write_text(json.dumps(data, indent=2, default=str))
    ok = all(entry["passed"] for entry in report)
    print("LONG_PROBE_MARGINS", json.dumps({"scenario": args.scenario, "probes": len(report),
                                            "passed": data["probe_margins"]["passed"], "ok": ok}), flush=True)
    return 0 if ok else 1


def host_main(argv):
    import tempfile
    import scenario_references as references
    import scenario_specs as specs
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=LONG_CANDIDATES, required=True)
    parser.add_argument("--blender", default=references.STOCK_BLENDER)
    args = parser.parse_args(argv)
    folder = specs.references_dir(args.scenario)
    if not (folder / specs.MANIFEST).is_file():
        raise SystemExit("No references at %s: run scenario_references.py first" % folder)
    with tempfile.TemporaryDirectory(prefix="long-probe-margins-") as scratch:
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
