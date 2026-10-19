# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native pixel-buffer probes shared by the reviewed-script recovery fixture."""

import bpy

from angelo_agent import script_errors


def _image():
    return bpy.data.images["QA Tex"]


def _facts():
    image = _image()
    return {"dirty": image.is_dirty, "packed_size": image.packed_file.size if image.packed_file else -1,
            "pixel": round(image.pixels[0], 4), "energy": bpy.data.lights["Light"].energy}


def _equal_size_colors():
    """Find distinct constant colors whose native PNG repacks have identical byte lengths."""
    sizes = {}
    image = _image()
    for value in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        image.pixels[:] = [value, value, value, 1.0] * 64
        image.pack()
        size = image.packed_file.size
        if size in sizes:
            original = sizes[size]
            image.pixels[:] = [original, original, original, 1.0] * 64
            image.pack()
            assert image.packed_file.size == size
            return value
        sizes[size] = value
    raise AssertionError("No equal-size native PNG pair found")


def pixel_cases(report, build, run_script):
    """Dirty/equal-size alias writes must never claim complete recovery or scene Undo."""
    for name in ("already_dirty", "equal_size_repack", "named_write"):
        for raises in (True, False):
            build("OBJECT")
            if name == "equal_size_repack":
                value = _equal_size_colors()
                write = "getattr(im, 'pix' + 'els')[:] = [%r, %r, %r, 1.0] * 64\ngetattr(im, 'pa' + 'ck')()\n" % (
                    value, value, value)
            else:
                _image().pixels[:] = [0.3, 0.3, 0.3, 1.0] * 64
                write = ("im.pixels" if name == "named_write" else "getattr(im, 'pix' + 'els')") + "[:] = [0.8, 0.8, 0.8, 1.0] * 64\n"
            before = _facts()
            # Prove this source evades the name scan for the two hidden writes.
            code = "import bpy\nim = bpy.data.images['QA Tex']\n" + write + "bpy.data.lights['Light'].energy *= 2\n"
            if raises:
                code += "raise ValueError('pixel probe')\n"
            result = run_script(code)
            after = _facts()
            key = "pixels:%s:%s" % (name, "raises" if raises else "finishes")
            report["cases"][key] = {"before": before, "after": after, "result": result}
            checks = report["checks"]
            if name != "named_write":
                checks[key + ":not_detected_by_source_names"] = script_errors.beyond_undo(code) is None
            if name == "already_dirty":
                checks[key + ":dirty_before"] = before["dirty"] is True
            if name == "equal_size_repack":
                checks[key + ":same_packed_size"] = before["packed_size"] == after["packed_size"]
            if raises:
                checks[key + ":no_complete_rollback_claim"] = result.get("rolled_back") is False
                checks[key + ":unknown_effects_explicit"] = (
                    result.get("partial_changes_possible") is True and result.get("image_pixels_restored") is False
                    and result.get("external_effects_restored") is False and "scene_changed" not in result)
                checks[key + ":main_restore_truthful"] = (
                    result.get("main_data_restored") is (name != "named_write")
                    and (after["energy"] == before["energy"]) is (name != "named_write"))
            else:
                checks[key + ":no_full_scene_undo_claim"] = result.get("success") is True and result.get("undoable") is False
                checks[key + ":undo_scope_explicit"] = (
                    result.get("main_data_undoable") is False and result.get("undo_scope") == "MAIN_DATA"
                    and result.get("image_pixels_undoable") is False and result.get("external_effects_undoable") is False)


def success_history_cases(report, build, run_script):
    """A successful script may invalidate history; availability never proves one-step coverage."""
    scripts = {
        "ordinary": "bpy.data.lights['Light'].energy *= 2\n",
        "disable_global": "bpy.context.preferences.edit.use_global_undo = False\nbpy.data.lights['Light'].energy *= 2\n",
        "zero_steps": "bpy.context.preferences.edit.undo_steps = 0\nbpy.data.lights['Light'].energy *= 2\n",
        "own_step": "bpy.data.lights['Light'].energy *= 2\nbpy.ops.ed.undo_push(message='own-step')\n",
        "own_step_alias": "bpy.data.lights['Light'].energy *= 2\ngetattr(bpy.ops.ed, 'undo' + '_push')(message='own-step')\n",
    }
    for name, code in scripts.items():
        build("OBJECT")
        bpy.context.preferences.edit.undo_steps = 32
        for image in list(bpy.data.images):
            bpy.data.images.remove(image)
        bpy.ops.ed.undo_push(message="Image-free baseline")
        before = bpy.data.lights["Light"].energy
        result = run_script(code)
        preferences = bpy.context.preferences.edit
        key = "history:" + name
        row = {"result": result, "before_energy": before, "after_energy": bpy.data.lights["Light"].energy,
               "global_undo": preferences.use_global_undo, "undo_steps": preferences.undo_steps}
        report["cases"][key] = row
        report["checks"][key + ":script_executed"] = result.get("success") is True and row["after_energy"] == before * 2
        report["checks"][key + ":coverage_unverified"] = (
            result.get("undoable") is False and result.get("main_data_undoable") is False
            and "unverified" in result.get("undo", ""))
        if name in {"disable_global", "zero_steps"}:
            report["checks"][key + ":unavailable"] = result.get("undo_available") is False
            report["checks"][key + ":preferences_untouched"] = (
                preferences.use_global_undo is (name != "disable_global") and preferences.undo_steps == (0 if name == "zero_steps" else 32))
        else:
            report["checks"][key + ":availability_separate"] = result.get("undo_available") is True
            bpy.ops.ed.undo()
            row["first_undo_energy"] = bpy.data.lights["Light"].energy
            report["checks"][key + ":native_undo_behavior_preserved"] = row["first_undo_energy"] == (
                before if name == "ordinary" else before * 2)
