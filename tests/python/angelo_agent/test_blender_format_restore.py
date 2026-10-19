# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No-render native format/coercion and display-copy restoration, including derived arrays."""

import json
import os
import sys
import traceback
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent import job_settings, view_finished, view_render_restore  # noqa: E402
from angelo_agent.references import ReferenceStore  # noqa: E402

OUT = Path(os.environ["ANGELO_QA_DIR"])
OUT.mkdir(parents=True, exist_ok=True)
REPORT = {"success": False, "checks": {}, "cases": []}


def snapshot(owner, prefix="", depth=3):
    """Independent native-state read, including readonly derived arrays the restore journal omits."""
    values = {}
    for prop in owner.bl_rna.properties:
        name = prop.identifier
        if name == "rna_type" or prop.type == "COLLECTION" or prop.is_runtime:
            continue
        value = getattr(owner, name)
        if prop.type == "POINTER":
            if depth and value is not None and not isinstance(value, bpy.types.ID):
                values.update(snapshot(value, prefix + name + ".", depth - 1))
        elif getattr(prop, "is_array", False):
            values[prefix + name] = list(value)
        elif isinstance(value, (str, bool, int, float)):
            values[prefix + name] = value
    return values


def matrix():
    references = ReferenceStore(OUT / "references", "c7b98e82-b264-462f-b785-44baae959588")
    image = bpy.data.images.new("Format fixture pixels", width=4, height=4, alpha=True, float_buffer=True)
    image.pixels[:] = [0.35, 0.12, 0.04, 1.0] * 16
    image.update()
    try:
        for method in ("override_only", "display_copy"):
            for file_format in ("PNG", "JPEG", "OPEN_EXR"):
                for colour in (None, "Linear Rec.709", "ACEScg", "Non-Color"):
                    key = "%s:%s:%s" % (method, file_format, colour)
                    scene = bpy.data.scenes.new("Format fixture " + key)
                    try:
                        scene.render.ffmpeg.ffmpeg_preset = "GOOD"
                        settings = scene.render.image_settings
                        settings.media_type = "IMAGE"
                        settings.file_format = file_format
                        settings.color_mode = "RGB" if file_format == "JPEG" else "RGBA"
                        settings.color_depth = "32" if file_format == "OPEN_EXR" else "8"
                        settings.compression = 67
                        settings.color_management = "OVERRIDE"
                        settings.view_settings.exposure = 1.25
                        settings.view_settings.use_white_balance = True
                        settings.view_settings.white_balance_temperature = 5400.0
                        settings.view_settings.white_balance_tint = 7.0
                        if colour is not None:
                            settings.linear_colorspace_settings.name = colour
                        before = snapshot(settings)
                        row = {"method": method, "format": file_format, "colour": colour, "before": before}
                        if method == "override_only":
                            values, _exact = view_render_restore.picture_values(scene)
                            override = view_render_restore.LookSettings(scene, "format-fixture")
                            override.apply(values)
                            row["journal_paths"] = list(override.originals)
                            row["journal_bytes"] = len(scene[job_settings.TAG].encode())
                            left = override.restore()
                        else:
                            reference, left, _exact, _multilayer = view_finished._display_copy(scene, references, key, image=image)
                            REPORT["checks"][key + ":png_written"] = Path(reference["path"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
                            references.discard_observation(reference)
                        after = snapshot(settings)
                        row.update(after=after, unrestored=left, changes={name: [before.get(name), after.get(name)]
                                   for name in before.keys() | after.keys() if before.get(name) != after.get(name)})
                        REPORT["cases"].append(row)
                        REPORT["checks"][key + ":exact_state"] = not row["changes"] and left == []
                        REPORT["checks"][key + ":journal_removed"] = job_settings.TAG not in scene
                    finally:
                        bpy.data.scenes.remove(scene)
    finally:
        bpy.data.images.remove(image)


def main():
    assert "--factory-startup" in sys.argv and bpy.app.background
    try:
        matrix()
        from format_curve_fixtures import curve_cases
        curve_cases(OUT / "curves", REPORT)
        REPORT["success"] = bool(REPORT["checks"]) and all(REPORT["checks"].values())
    except Exception:
        REPORT["error"] = traceback.format_exc()
    (OUT / "format-restore.json").write_text(json.dumps(REPORT, indent=2))
    assert REPORT["success"], REPORT.get("error") or [key for key, passed in REPORT["checks"].items() if not passed]
    print("ANGELO_FORMAT_RESTORE_OK", json.dumps({"cases": len(REPORT["cases"]), "checks": len(REPORT["checks"])}), flush=True)


if __name__ == "__main__":
    main()
