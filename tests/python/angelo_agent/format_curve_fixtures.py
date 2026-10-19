# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native display-copy curve allocation, pixel parity and saved-state regressions; no render."""

import hashlib
from pathlib import Path

import bpy

from angelo_agent import view_finished, view_save
from angelo_agent.references import ReferenceStore


def curve_state(view):
    mapping = view.curve_mapping
    state = {"enabled": view.use_curve_mapping, "allocated": mapping is not None}
    if mapping is not None:
        state["mapping"] = {"tone": mapping.tone, "clip": mapping.use_clip,
                            "bounds": [mapping.clip_min_x, mapping.clip_min_y, mapping.clip_max_x, mapping.clip_max_y],
                            "black": list(mapping.black_level), "white": list(mapping.white_level),
                            "curves": [[(list(point.location), point.handle_type) for point in curve.points]
                                       for curve in mapping.curves]}
    return state


def _custom_curve(view, white):
    view.use_curve_mapping = True
    view.curve_mapping.curves[3].points[1].location = (1.0, white)
    view.curve_mapping.update()


def _display_reference(image, source_custom, path):
    """Blender's own save of ``image`` in a scene's display colours (the render window's), with the source
    curve when the case has one: what the native display copy must equal byte for byte."""
    scene = bpy.data.scenes.new("Curve fixture display reference")
    try:
        scene.view_settings.view_transform = "Standard"
        scene.view_settings.look = "None"
        settings = scene.render.image_settings
        settings.media_type, settings.file_format = "IMAGE", "PNG"
        settings.color_mode, settings.color_depth, settings.compression = "RGBA", "8", 0
        settings.color_management = "FOLLOW_SCENE"
        if source_custom:
            _custom_curve(scene.view_settings, 0.4)
        image.save_render(str(path), scene=scene)
    finally:
        bpy.data.scenes.remove(scene)


def curve_cases(output, report):
    output.mkdir(parents=True, exist_ok=True)
    references = ReferenceStore(output / "references", "b4277921-4816-4e09-8097-088511d5a2af")
    image = bpy.data.images.new("Curve fixture pixels", width=8, height=8, alpha=True, float_buffer=True)
    palette = [-0.1, 0.0, 0.04, 1.0, 0.35, 0.12, 0.04, 1.0, 1.2, 2.0, 4.0, 1.0, 0.001, 0.5, 0.9, 0.4]
    image.pixels[:] = palette * 16
    image.update()
    names = []
    for source_custom in (False, True):
        for output_custom in (False, True):
            name = "Curve fixture %s %s" % (source_custom, output_custom)
            scene = bpy.data.scenes.new(name)
            names.append((name, source_custom, output_custom))
            scene.render.ffmpeg.ffmpeg_preset = "GOOD"
            scene.view_settings.view_transform = "Standard"
            scene.view_settings.look = "None"
            settings = scene.render.image_settings
            settings.media_type, settings.file_format = "IMAGE", "PNG"
            settings.color_mode, settings.color_depth, settings.compression = "RGBA", "8", 0
            settings.color_management = "OVERRIDE"
            settings.linear_colorspace_settings.name = "sRGB"
            assert settings.view_settings.curve_mapping is None
            if output_custom:
                _custom_curve(settings.view_settings, 0.65)
            if source_custom:
                _custom_curve(scene.view_settings, 0.4)
    before_file, after_file = output / "before.blend", output / "after.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(before_file), check_existing=False, copy=True, compress=False)
    records = {}
    # The native helper saves exactly what the render window shows, source curve included; only the earlier
    # route approximates a custom source curve with the output's and must say so.
    native = view_save.native() is not None
    for index, (name, source_custom, output_custom) in enumerate(names):
        scene = bpy.data.scenes[name]
        view = scene.render.image_settings.view_settings
        before, source_before = curve_state(view), curve_state(scene.view_settings)
        active_before = output / (str(index) + "-active-before.png")
        image.save_render(str(active_before), scene=scene)
        # Existing behavior uses the output's curve when both flags are on, otherwise identity.
        # For the missing-mapping case, identity must not require allocation to produce these pixels.
        expected = output / (str(index) + "-expected.png")
        view.use_curve_mapping = source_custom and output_custom
        image.save_render(str(expected), scene=scene)
        view.use_curve_mapping = output_custom
        reference, left, exact, _multilayer = view_finished._display_copy(scene, references, name, image=image)
        preview = Path(reference["path"]).read_bytes()
        after = curve_state(view)
        active_after = output / (str(index) + "-active-after.png")
        image.save_render(str(active_after), scene=scene)
        if native:
            expected = output / (str(index) + "-display-expected.png")
            _display_reference(image, source_custom, expected)
        records[name] = {"before": before, "after": after, "exact": exact, "unrestored": left, "native": native,
                         "preview_sha256": hashlib.sha256(preview).hexdigest(),
                         "expected_sha256": hashlib.sha256(expected.read_bytes()).hexdigest()}
        checks = report["checks"]
        checks[name + ":output_curve_unchanged"] = before == after and left == []
        checks[name + ":source_curve_unchanged"] = source_before == curve_state(scene.view_settings)
        checks[name + ":preview_parity"] = preview == expected.read_bytes()
        checks[name + ":active_output_parity"] = active_before.read_bytes() == active_after.read_bytes()
        checks[name + ":approximation_disclosed"] = exact is (native or not source_custom)
        references.discard_observation(reference)
    bpy.ops.wm.save_as_mainfile(filepath=str(after_file), check_existing=False, copy=True, compress=False)
    for path in (before_file, after_file):
        bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
        for name, _source_custom, _output_custom in names:
            restored = curve_state(bpy.data.scenes[name].render.image_settings.view_settings)
            records[name]["reloaded_" + path.stem] = restored
            report["checks"][name + ":saved_state_" + path.stem] = restored == records[name]["before"]
    report["curve_cases"] = records
