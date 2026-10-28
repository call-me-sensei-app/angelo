# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Proof that the split ray-cast capture is byte-identical to the serial one, on a real saved scene.

In stock Blender (background; heavy: the serial pass is rays x objects on one core):

    Blender -b --factory-startup --python-exit-code 1 --python capture_split_proof.py -- \\
        --blend final.blend --scenario NAME --out DIR [--workers N]

This opens the scene the way the capture jobs do (frame, render visibility),
runs ``scenario_capture.capture`` in this process and
``scenario_capture_split.capture_split`` across workers on the same prepared
scene, writes both archives and compares every array ``save_capture`` stores
(depths, hit ids and the name/parent/resolution metadata) byte for byte. It
prints ``CAPTURE_SPLIT_PROOF {...}`` with the timings and exits 1 on any
difference. On the host, ``python capture_split_proof.py --compare A.npz B.npz``
compares two archives already written, for example a serial capture job
against a split one.
"""

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True


def compare_archives(first, second):
    """``{"identical": bool, "arrays": {key: {...}}}``: dtype, shape and bytes of every array in both archives."""
    import numpy
    report = {}
    with numpy.load(first) as a, numpy.load(second) as b:
        for key in sorted(set(a.files) | set(b.files)):
            if key not in a.files or key not in b.files:
                report[key] = {"identical": False, "missing_from": "first" if key not in a.files else "second"}
                continue
            x, y = a[key], b[key]
            same = x.dtype == y.dtype and x.shape == y.shape and x.tobytes() == y.tobytes()
            entry = {"identical": bool(same), "dtype": str(x.dtype), "shape": list(x.shape), "bytes": int(x.nbytes)}
            if not same and x.shape == y.shape and key.endswith((".depth", ".ids")):
                entry["differing_values"] = int(numpy.count_nonzero(x.view(numpy.uint32) != y.view(numpy.uint32)))
            report[key] = entry
    return {"identical": all(entry["identical"] for entry in report.values()), "arrays": report}


def summary(result):
    arrays = result["arrays"]
    return {"identical": result["identical"], "arrays": len(arrays),
            "pixels": sum(entry["shape"][0] * entry["shape"][1] for key, entry in arrays.items()
                          if key.endswith(".depth") and "shape" in entry),
            "bytes_compared": sum(entry.get("bytes", 0) for entry in arrays.values()),
            "differing": {key: entry for key, entry in arrays.items() if not entry["identical"]}}


def blender_main(argv):
    import bpy
    import scenario_capture as capture
    import scenario_capture_split as split
    import scenario_specs as specs
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--workers", type=int, default=None, help="default: scenario_capture_split.worker_count()")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    scenario = specs.module(args.scenario)
    bpy.ops.wm.open_mainfile(filepath=args.blend, load_ui=False)
    scene = bpy.context.scene
    capture.match_render_visibility(bpy, scene)
    scene.frame_set(scenario.FRAME)
    started = time.monotonic()
    split_data = split.capture_split(bpy, scene, scenario.CAMERAS, out / ".capture-split", workers=args.workers)
    split_s = time.monotonic() - started
    started = time.monotonic()
    serial_data = capture.capture(bpy, scene, scenario.CAMERAS)
    serial_s = time.monotonic() - started
    capture.save_capture(out / "split.npz", split_data)
    capture.save_capture(out / "serial.npz", serial_data)
    result = compare_archives(out / "serial.npz", out / "split.npz")
    report = dict(summary(result), blend=args.blend, scenario=args.scenario, objects=len(scene.objects),
                  workers=args.workers or split.worker_count(), serial_s=round(serial_s, 2), split_s=round(split_s, 2))
    (out / "proof.json").write_text(json.dumps(dict(report, detail=result), indent=2))
    print("CAPTURE_SPLIT_PROOF", json.dumps(report), flush=True)
    return 0 if result["identical"] else 1


def main():
    if "--compare" in sys.argv:
        first, second = sys.argv[sys.argv.index("--compare") + 1:][:2]
        result = compare_archives(first, second)
        print(json.dumps(summary(result), indent=2))
        return 0 if result["identical"] else 1
    return blender_main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])


if __name__ == "__main__":
    sys.exit(main())
