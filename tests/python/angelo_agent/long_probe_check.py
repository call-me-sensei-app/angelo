# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Probe margins of any long candidate's stand-in (``scenario_specs.ONESHOT_LONG``), e.g. ``oneshot_town_centre``.

    python3 long_probe_check.py --scenario oneshot_town_centre [--blender PATH]

The same proof as ``long_probe_margins`` (which stays as candidates 1 and 2 were frozen with it): every probe of the
test, its origin moved ``long_probe_margins.MARGIN`` (5 cm) sideways in eight directions square to the ray, must still
pass on the stand-in. Results go per probe under ``probe_margins`` in the reference folder's ``selfcheck.json``; the
run exits non-zero when any probe fails at any offset. It reuses ``long_probe_margins.margins`` and the grader's own
probe check unchanged.
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


def blender_main(argv):
    import bpy
    import scenario_checks
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=CANDIDATES, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    probes = [check for check in specs.module(args.scenario).CHECKS if check["kind"] == "probe"]
    report = proof.margins(bpy, bpy.context.scene, scenario_checks, probes)
    path = Path(args.out) / "selfcheck.json"
    data = json.loads(path.read_text())
    data["probe_margins"] = {"margin_m": proof.MARGIN, "directions": 8, "probes": len(report),
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
    with tempfile.TemporaryDirectory(prefix="long-probe-check-") as scratch:
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
