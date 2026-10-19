# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Tiny real Cycles looks: effective per-layer bounds, denoising and exact restore."""

import re

import bpy

from angelo_agent import render_extensions


def run(window, start, pending_result, check, report):
    scene = window().scene
    scene.render.engine = "CYCLES"
    scene.render.use_single_layer = False
    scene.render.use_compositing = scene.render.use_sequencer = False
    scene.cycles.device = "CPU"
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.denoising_use_gpu = False
    for obj in scene.objects:
        if obj.type != "CAMERA":
            obj.hide_render = True
    first = window().view_layer
    others = [scene.view_layers.new('Samples "\\%d' % n) for n in (1, 2)]
    layers = [first, *others]
    rows = []
    report["layer_samples"] = rows
    cases = (("USE", 128, [128, 8, 0], [32, 8, 32], 128),
             ("USE", 8, [128, 0, 4], [32, 8, 4], 128),
             ("BOUNDED", 16, [128, 8, 0], [16, 8, 16], 16),
             ("IGNORE", 64, [128, 8, 0], [32, 32, 32], 64))
    for index, (mode, scene_count, counts, expected, original_max) in enumerate(cases):
        scene.cycles.samples, scene.cycles.use_layer_samples = scene_count, mode
        scene.cycles.use_denoising = False
        for layer, count in zip(layers, counts):
            layer.samples, layer.cycles.use_denoising = count, False
        stats = []

        @render_extensions.host_handler  # A measurement of the fixture's, not add-on code a look must refuse.
        def record(*args):
            stats.extend(str(arg) for arg in args)

        bpy.app.handlers.render_stats.append(record)
        try:
            pending = start(resolution=256)
            check("layer_%d_denoising_enabled" % index,
                  scene.cycles.use_denoising and all(layer.cycles.use_denoising for layer in layers))
            result = yield from pending_result(pending)
        finally:
            bpy.app.handlers.render_stats.remove(record)
        observed = {int(match.group(1)) for text in stats for match in re.finditer(r"Sample \d+/(\d+)", text)}
        rows.append({"mode": mode, "scene_samples": scene_count, "layer_samples": counts,
                     "expected": expected, "native_sample_totals": sorted(observed),
                     "rendered": result.get("rendered"), "stats": stats})
        check("layer_%d_actual_native_samples" % index, observed == set(expected))
        check("layer_%d_receipt" % index, result.get("success") and result["rendered"]["samples"] == max(expected)
              and result["rendered"]["scene_samples"] == original_max and result["rendered"]["denoised"])
        check("layer_%d_exact_restore" % index,
              scene.cycles.samples == scene_count and scene.cycles.use_layer_samples == mode
              and not scene.cycles.use_denoising and [layer.samples for layer in layers] == counts
              and not any(layer.cycles.use_denoising for layer in layers))
    for layer in others:
        scene.view_layers.remove(layer)
