# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live-eval coverage set's operator_run family tasks (phase 5) and the fixtures the harness makes for them.

Dependency-free; imported through ``eval_live_tasks`` by the host launcher,
both trial drivers and grading. One task per family, each graded by closed
host checks: an Edit-mode transform of the eval cube, stroke and hair-curve
subdivision, a two-line text and an applied constraint. No native kind draws
Grease Pencil strokes or adds a hair-curves object, so those two tasks edit
objects the harness makes before the task (``prepare_fixture``) on both
benchmark sides, recorded as ``harness_fixture``; the agent never makes them.
"""

# The coverage set's text ("$text") and the harness-made fixtures ("$strokes", "$hair").
TEXT_OBJECT = "Eval Text"
STROKES_OBJECT = "Eval Strokes"
HAIR_OBJECT = "Eval Hair Curves"


def _count(value):
    return {"minimum": value, "maximum": value}


def operator_family_tasks(quoted, copy_quoted):
    """One task per operator_run family of coverage phase 5, each graded by closed checks."""
    return [
        {"id": "edit_mode_transform", "kind": "edit", "requires_object": True,
         "prompt": ("Make %s 2.5 m tall by moving only its four highest vertices up 0.5 m in its mesh, without "
                    "moving or scaling the object.") % quoted,
         "checks": [{"kind": "dimensions", "target": "$eval", "expected": [2, 2, 2.5], "tolerance": 0.0001},
                    {"kind": "transform", "target": "$eval", "property": "scale", "expected": [1, 1, 1],
                     "tolerance": 0.0001}],
         "source": "native coverage: operator_run (transform.translate on selected vertices)"},
        {"id": "grease_pencil_strokes", "kind": "edit", "requires_object": False, "fixture": "strokes",
         "prompt": ("Subdivide every stroke of the Grease Pencil object '%s' once, adding one point between each "
                    "pair of neighbouring points.") % STROKES_OBJECT,
         "checks": [{"kind": "curve_counts", "target": "$strokes", "points": _count(14), "curves": _count(2)}],
         "source": "native coverage: operator_run (grease_pencil.stroke_subdivide); harness-made strokes"},
        {"id": "hair_curves", "kind": "edit", "requires_object": False, "fixture": "hair",
         "prompt": ("Subdivide every curve of the hair curves object '%s' once, adding one point between each pair "
                    "of neighbouring points.") % HAIR_OBJECT,
         "checks": [{"kind": "curve_counts", "target": "$hair", "points": _count(21), "curves": _count(3)}],
         "source": "native coverage: operator_run (curves.subdivide); harness-made hair curves"},
        {"id": "two_line_text", "kind": "edit", "requires_object": False,
         "prompt": "Add a text object named '%s' with two lines: 'Angelo' on the first and 'Studio' on the second." % TEXT_OBJECT,
         "checks": [{"kind": "object_type", "target": "$text", "expected": "FONT"},
                    {"kind": "curve_counts", "target": "$text", "characters": _count(13), "lines": _count(2)}],
         "source": "native coverage: create_object (TEXT), operator_run (font.line_break, font.text_insert)"},
        {"id": "constraint_apply", "kind": "edit", "requires_object": True,
         "prompt": ("Give %s a Copy Location constraint that follows %s at 0.5 influence, then apply the constraint "
                    "so the copy keeps that position without it.") % (copy_quoted, quoted),
         "checks": [{"kind": "transform", "target": "$copy", "property": "location", "expected": [1.5, 0, 0],
                     "tolerance": 0.0001}],
         "source": "native coverage: constraint_add, operator_run (constraint.apply)"},
    ]


def prepare_fixture(bpy, scene, task):
    """The harness-made object a task edits (``task["fixture"]``), when missing; returns the steps taken.

    ``strokes``: a Grease Pencil object with two four-point strokes on layer
    'Ink' at the current frame; ``hair``: hair curves with three four-point
    curves. ``bpy`` is passed in so this module stays importable without Blender.
    """
    name = {"strokes": STROKES_OBJECT, "hair": HAIR_OBJECT}.get(task.get("fixture"))
    if name is None or scene.objects.get(name) is not None:
        return []
    if task["fixture"] == "strokes":
        data = bpy.data.grease_pencils.new(name)
        drawing = data.layers.new("Ink", set_active=True).frames.new(scene.frame_current).drawing
        drawing.add_strokes([4, 4])
        drawing.attributes["position"].data.foreach_set("vector", [value for index in range(8) for value in (
            index % 4 * 0.5, index // 4, 0)])
    else:
        data = bpy.data.hair_curves.new(name)
        data.add_curves([4, 4, 4])
        data.attributes["position"].data.foreach_set("vector", [value for index in range(12) for value in (
            index // 4, 0, index % 4 * 0.25)])
    scene.collection.objects.link(bpy.data.objects.new(name, data))
    return ["fixture:" + task["fixture"]]
