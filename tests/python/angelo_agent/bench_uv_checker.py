# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The viewport UV checker's performance proof (doc/angelo/uv_checker.rst, "Drawing cost"); run by hand, alone.

Host Python; launches background Angelos that run ``bench_uv_checker_draw.py`` (a GPU is needed). Run it on a
quiet machine (Mac 1 under the billed lock), never beside builds or suites:

    python3 bench_uv_checker.py --main MAIN_BINARY --branch BRANCH_BINARY --out DIR [--pairs 3]

* **Off costs nothing.** ``--pairs`` A/B pairs of processes, main then branch, alternating (ABAB), each the heavy
  scene in Solid with the checker off. Passes when the difference of the two builds' mean medians is within the
  run-to-run range (the larger spread of either build's medians).
* **On is never slower than stock Blender's nearest route** (a checker image texture in a material in Material
  Preview), on the heavy scene and on a dense mesh in Edit Mode: one branch process, conditions interleaved in
  rounds. Passes when the checker in Solid and over Material Preview is not slower than that route while navigating
  (every engine drawing). Static frames are reported too: there the engines show what they accumulated and only
  overlays draw, which isolates the checker's own pass.

Writes ``DIR/uv-checker-bench.json``; prints the verdict. Every number names its binary.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "bench_uv_checker_draw.py"


def launch(binary, mode, out, timeout=900):
    target = out / f"{mode}-{Path(binary).parents[2].parent.name}-{time.strftime('%H%M%S')}.json"
    env = dict(os.environ, ANGELO_UV_BENCH_MODE=mode, ANGELO_UV_BENCH_OUT=str(target))
    subprocess.run([binary, "--background", "--factory-startup", "--python", str(SCRIPT)], env=env, timeout=timeout,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    report = json.loads(target.read_text())
    if not report.get("success"):
        raise SystemExit(f"{binary} {mode} failed:\n{report.get('error')}")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--main", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--before", help="an earlier branch build: its on-costs are reported beside the branch's")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = {"binaries": {"main": args.main, "branch": args.branch}, "off": {"main": [], "branch": []}}
    for _ in range(args.pairs):
        for arm in ("main", "branch"):
            report = launch(getattr(args, arm), "off", args.out)
            result["off"][arm].append(report["conditions"]["solid_off"]["navigating"]["median_ms"])
            result.setdefault("off_static", {}).setdefault(arm, []).append(
                report["conditions"]["solid_off"]["static"]["median_ms"])
            result["scene"], result["size"] = report["scene"], report["size"]
            result.setdefault("has_checker", {})[arm] = report["has_checker"]
    means = {arm: statistics.mean(values) for arm, values in result["off"].items()}
    spread = max(max(values) - min(values) for values in result["off"].values())
    result["off_verdict"] = {"main_mean_ms": round(means["main"], 3), "branch_mean_ms": round(means["branch"], 3),
                             "difference_ms": round(means["branch"] - means["main"], 3),
                             "run_to_run_range_ms": round(spread, 3),
                             "pass": abs(means["branch"] - means["main"]) <= spread}
    compare = launch(args.branch, "compare", args.out)
    for key, source in (("compare", "conditions"), ("edit", "edit_conditions")):
        result[key] = {name: data["navigating"]["median_ms"] for name, data in compare[source].items()}
        result[key + "_p75"] = {name: data["navigating"]["p75_ms"] for name, data in compare[source].items()}
        result[key + "_static"] = {name: data["static"]["median_ms"] for name, data in compare[source].items()}
    result["edit_scene"] = compare["edit_scene"]
    stock, edit_stock = (result["compare"]["material_preview_stock_checker_material"],
                         result["edit"]["edit_material_preview_stock_checker_material"])
    result["on_verdict"] = {
        "solid_checker_not_slower_than_stock": result["compare"]["solid_checker"] <= stock,
        "material_preview_checker_not_slower_than_stock": result["compare"]["material_preview_checker"] <= stock,
        "edit_solid_checker_not_slower_than_stock": result["edit"]["edit_solid_checker"] <= edit_stock,
    }
    result["first_use_ms"] = compare.get("first_use_ms")
    if args.before:
        before = launch(args.before, "compare", args.out)
        result["before"] = {"binary": args.before, "first_use_ms": before.get("first_use_ms")}
        for key, source in (("compare", "conditions"), ("edit", "edit_conditions")):
            for kind in ("navigating", "static"):
                result["before"][f"{key}_{kind}"] = {name: [data[kind]["median_ms"], data[kind]["p75_ms"]]
                                                     for name, data in before[source].items()}
    (args.out / "uv-checker-bench.json").write_text(json.dumps(result, indent=1))
    print(json.dumps({key: result[key] for key in ("off_verdict", "compare", "compare_static", "edit", "edit_static",
                                                   "on_verdict")}, indent=1))
    return 0 if result["off_verdict"]["pass"] and all(result["on_verdict"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
