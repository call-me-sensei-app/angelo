# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Measure Fit to My Image on the research characters: the gate on ``parts_ground.MEASURED``. Runs in Blender.

    Angelo --background --factory-startup --python tests/python/angelo_agent/measure_parts_ground.py -- \\
        --characters DIR --sheets DIR --connection-id ID --out results.json --model MODEL \\
        [--source-connections-dir DIR] [--codex-home DIR] [--trials 3] [--claude PATH] [--codex PATH] [--only c1,c2]

The shipped path: each character image (``<characters>/<id>-*.png``) is reduced as Studio sends it
(``parts_ground.CHARACTER_EDGE``), its sheet (``<sheets>/B4-<id>.png``) is split by ``flow_parts.split_sheet`` with
``flow_templates.CHARACTER_SLOTS``, the cut-outs are numbered by ``parts_ground.contact_sheet``, and
``studio_enhance.ground_native`` asks the connection at the effort Studio uses. The connection is isolated as
``eval_live_agent`` isolates one (its metadata copied into a new store, never a credential). Every trial is recorded:
a trial that doesn't complete or returns no result is a failure (Place would keep the sheet's boxes). Scoring
against the hand-labelled truth is done outside the app (compose-eval tools: ``score2d.py``); boxes are written in
the character image's own pixels (``scale_to_source``).
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
    for name in ("--characters", "--sheets", "--connection-id", "--out"):
        parser.add_argument(name, required=True)
    parser.add_argument("--source-connections-dir", default="")
    parser.add_argument("--codex-home", default="")
    parser.add_argument("--model", required=True, help="the exact model id (a measurement never runs a default)")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--claude", default="", help="the Claude Code executable (default: as the app finds it)")
    parser.add_argument("--codex", default="", help="the Codex executable (default: as the app finds it)")
    parser.add_argument("--only", default="", help="comma-separated character ids (default: every character found)")
    return parser.parse_args(argv)


def pixels(path):
    """RGB float pixels of an image file, top row first (as flow_split_host reads a sheet)."""
    import bpy
    import numpy as np
    image = bpy.data.images.load(str(path), check_existing=False)
    try:
        width, height = image.size
        buffer = np.empty(width * height * 4, np.float32)
        image.pixels.foreach_get(buffer)
        return np.flipud(buffer.reshape(height, width, 4))[..., :3].copy(), (width, height)
    finally:
        bpy.data.images.remove(image)


def request_files(character, sheet, folder):
    """(files, part ids, size as sent, source size): the character image and numbered parts as Studio sends them."""
    import numpy as np
    from angelo_agent import flow_templates, parts_ground
    from angelo_agent.flow_parts import split_sheet
    from angelo_agent.flow_split_host import _png
    from angelo_agent.studio_host import write_fitted
    split = split_sheet(pixels(sheet)[0], flow_templates.CHARACTER_SLOTS)
    counts = {}
    for part in split["parts"]:
        counts[part["slot"]] = counts.get(part["slot"], 0) + 1
    parts = [dict(part, id=f"{part['slot']}-{part['item'] + 1}" if counts[part["slot"]] > 1 else part["slot"])
             for part in split["parts"]]
    parts = parts_ground.ordered(parts)
    sheet_rgb = parts_ground.contact_sheet([part["front"]["rgba"] for part in parts])
    _png(folder / "parts.png", np.concatenate([sheet_rgb, np.full(sheet_rgb.shape[:2] + (1,), 255, np.uint8)], axis=2))
    sent = write_fitted(Path(character), folder / "character.png", parts_ground.CHARACTER_EDGE)
    _, size = pixels(sent)
    _, source = pixels(character)
    return [sent, folder / "parts.png"], [part["id"] for part in parts], size, source


def main():
    args = arguments()
    from angelo_agent.connections import ConnectionStore
    from angelo_agent.studio_enhance import ground_native
    from eval_live_agent import isolated_connection
    work = Path(tempfile.mkdtemp(prefix="angelo-parts-ground-"))
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
    out = {"model": args.model, "mode": record["mode"], "connection": args.connection_id[:8], "trials": args.trials,
           "effort": "low (studio_enhance.EnhancementContext)", "models_catalog_passed": False,
           "started": time.strftime("%Y-%m-%d %H:%M:%S"), "results": []}
    only = {name for name in args.only.split(",") if name}
    characters = sorted(Path(args.characters).glob("c*-*.png"), key=lambda path: int(path.name.split("-")[0][1:]))
    for character in characters:
        identity = character.name.split("-")[0]
        if only and identity not in only:
            continue
        folder = work / identity
        folder.mkdir()
        files, part_ids, size, source_size = request_files(character, Path(args.sheets) / f"B4-{identity}.png", folder)
        for trial in range(args.trials):
            started, result, failure = time.monotonic(), None, ""
            try:
                result = ground_native(str(library), record, settings, "", args.model, [], files, part_ids, size,
                                       threading.Event())
            except Exception as error:  # Every way a trial fails is recorded; Place would keep the sheet's boxes.
                failure = f"{type(error).__name__}: {error}"[:400]
            entry = {"character": identity, "trial": trial + 1, "seconds": round(time.monotonic() - started, 1),
                     "result": result, "failure": failure, "parts": part_ids,
                     "scale_to_source": [source_size[0] / size[0], source_size[1] / size[1]]}
            out["results"].append(entry)
            print("ANGELO_PARTS_GROUND", json.dumps({k: v for k, v in entry.items() if k != "result"}), flush=True)
            Path(args.out).write_text(json.dumps(out, indent=1))
    rows = out["results"]
    out["summary"] = {"trials": len(rows), "parsed": sum(r["result"] is not None for r in rows),
                      "median_seconds": sorted(r["seconds"] for r in rows)[len(rows) // 2] if rows else None}
    out["finished"] = time.strftime("%Y-%m-%d %H:%M:%S")
    Path(args.out).write_text(json.dumps(out, indent=1))
    print("ANGELO_PARTS_GROUND_SUMMARY", json.dumps(out["summary"]), flush=True)


try:
    main()
except SystemExit:
    raise
except Exception:
    import traceback
    traceback.print_exc()
    sys.exit(1)
