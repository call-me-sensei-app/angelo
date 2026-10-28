# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure the parts check's accuracy on labeled parts sheets: the gate on ``parts_check.READY``. Runs in Blender.

    Angelo --background --factory-startup --python tests/python/angelo_agent/measure_parts_check.py -- \\
        --labels labels.json --characters DIR --sheets DIR --connection-id ID --out results.json \\
        [--source-connections-dir DIR] [--codex-home DIR] [--model MODEL] [--trials 3] [--claude PATH] [--codex PATH]

The shipped path: each character image (``<characters>/<id>-*.png``) and its sheet (``<sheets>/B4-<id>.png``) go
through ``studio_workflow_ops.fitted_check_images`` and ``studio_enhance.check_native`` with
``flow_templates.CHARACTER_SLOTS``, at the effort Studio uses. The connection is isolated as ``eval_live_agent``
isolates one (its metadata copied into a new store, never a credential). Every trial counts: a trial that doesn't
complete or returns no result is recorded as a failure and scores as a wrong answer, never a pass. Item recall is
graded by hand from the recorded replies, because the labels name items by meaning.

Labels: ``{"sheets": {"<id>": {"missing": [...], "different": [...]}}}``. Results: every reply, its duration, and the
per-sheet score (flagged exactly when the labels list a missing item).
"""

import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def arguments():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("--labels", "--characters", "--sheets", "--connection-id", "--out"):
        parser.add_argument(name, required=True)
    parser.add_argument("--source-connections-dir", default="")
    parser.add_argument("--codex-home", default="")
    parser.add_argument("--model", required=True, help="the exact model id (a measurement never runs a default)")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--claude", default="", help="the Claude Code executable (default: as the app finds it)")
    parser.add_argument("--codex", default="", help="the Codex executable (default: as the app finds it)")
    return parser.parse_args(argv)


def score(result, labeled_missing):
    """True when the check flags the sheet exactly when the labels list a missing item; a failure is wrong."""
    if result is None:
        return False
    return bool(result["missing"]) == bool(labeled_missing)


def main():
    args = arguments()
    from angelo_agent import flow_templates, studio_workflow_ops
    from angelo_agent.connections import ConnectionStore
    from angelo_agent.studio_enhance import check_native
    from eval_live_agent import isolated_connection
    labels = json.loads(Path(args.labels).read_text())["sheets"]
    work = Path(tempfile.mkdtemp(prefix="angelo-parts-check-"))
    store_dir = work / "connections"
    source = args.source_connections_dir or str(Path.home() / "Library/Application Support/Angelo Agent Test/connections")
    isolated_connection(source, args.connection_id, store_dir, codex_home=args.codex_home)
    record = ConnectionStore(store_dir).records[args.connection_id]
    settings = {"codex_path": args.codex, "claude_path": args.claude, "model": record.get("model") or "",
                "reuse_codex_login": record["reuse_native"], "reasoning": "low", "auth_directory": str(store_dir),
                "connection_id": None if record["reuse_native"] else record["id"],
                "native_profile": record.get("native_profile")}
    library = work / "library"
    library.mkdir()
    # Differences from Studio, recorded with the results: settings are built here (as ui.connection_settings builds
    # them for a stored connection) and the connection's model catalog isn't passed (``models`` = []).
    out = {"model": args.model, "mode": record["mode"], "connection": args.connection_id[:8], "trials": args.trials,
           "effort": "low (studio_enhance.EnhancementContext)", "models_catalog_passed": False,
           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "results": []}
    for sheet_id in sorted(labels, key=lambda key: int(key.lstrip("c"))):
        character = next(Path(args.characters).glob(f"{sheet_id}-*.png"))
        sheet = Path(args.sheets) / f"B4-{sheet_id}.png"
        images = studio_workflow_ops.fitted_check_images([character, sheet], library)
        for trial in range(args.trials):
            started, result, failure = time.monotonic(), None, ""
            try:
                result = check_native(str(library), record, settings, "", args.model, [], images,
                                      flow_templates.CHARACTER_SLOTS, threading.Event())
            except Exception as error:  # Every way a trial fails is recorded and scored as wrong.
                failure = f"{type(error).__name__}: {error}"[:400]
            entry = {"sheet": sheet_id, "trial": trial + 1, "seconds": round(time.monotonic() - started, 1),
                     "result": result, "failure": failure, "labeled_missing": labels[sheet_id]["missing"],
                     "right": score(result, labels[sheet_id]["missing"])}
            out["results"].append(entry)
            print("ANGELO_PARTS_CHECK", json.dumps(entry), flush=True)
            Path(args.out).write_text(json.dumps(out, indent=1))
    rows = out["results"]
    flagged = lambda row: bool(row["result"] and row["result"]["missing"])  # noqa: E731
    out["summary"] = {
        "accuracy": f"{sum(r['right'] for r in rows)}/{len(rows)}",
        "tp": sum(flagged(r) and bool(r["labeled_missing"]) for r in rows),
        "fp": sum(flagged(r) and not r["labeled_missing"] for r in rows),
        "fn": sum(not flagged(r) and bool(r["labeled_missing"]) for r in rows),
        "tn": sum(not flagged(r) and not r["labeled_missing"] and r["result"] is not None for r in rows),
        "failed": sum(r["result"] is None for r in rows),
        "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else None}
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    Path(args.out).write_text(json.dumps(out, indent=1))
    print("ANGELO_PARTS_CHECK_SUMMARY", json.dumps(out["summary"]), flush=True)


try:
    main()
except SystemExit:
    raise
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
