# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted rounds of ``test_blender_script_stream.py``. Dependency-free.

Each plan carries one ``angelo_execute_python`` step and streams through the
stand-in ``claude`` CLI as Claude Code writes a StructuredOutput call
(``fake_provider_cli.plan_events``, the rounds' shape from
``reply_stream_fixtures``). The script is generic Blender code, not any
benchmark's scene: the suite checks only how the chat draws code being
written. No round runs its script: the runtime refuses the first, the person
stops the second, and the keyed one is refused too.
"""

import reply_stream_fixtures as base
from reply_stream_fixtures import KEY, SESSION, operation, round_events  # noqa: F401  (the suite's names)

PIECES = [("Plinth", "(0, 0, 0.1)", "(1.2, 1.2, 0.2)"), ("Column", "(0, 0, 1.2)", "(0.3, 0.3, 2.0)"),
          ("Cap", "(0, 0, 2.3)", "(0.8, 0.8, 0.2)"), ("Arm", "(0.6, 0, 2.1)", "(1.2, 0.1, 0.1)"),
          ("Shade", "(1.1, 0, 1.9)", "(0.4, 0.4, 0.3)")]


def script_code(copies=4, material="Painted Iron"):
    """About 4,000 characters of ordinary Blender Python, written line by line as a model writes one."""
    lines = ["import bpy", "import math", "", "made = []", "material = bpy.data.materials.new(\"%s\")" % material,
             "material.diffuse_color = (0.08, 0.09, 0.1, 1.0)", ""]
    for copy in range(copies):
        lines.append("# Lamp %d" % (copy + 1))
        for name, location, scale in PIECES:
            lines += ["bpy.ops.mesh.primitive_cube_add(location=%s)" % location.replace("(0,", "(%d," % (copy * 3)),
                      "piece = bpy.context.active_object",
                      "piece.name = \"%s %d\"" % (name, copy + 1),
                      "piece.scale = %s" % scale,
                      "piece.data.materials.append(material)",
                      "made.append(piece.name)"]
        lines.append("")
    lines += ["for piece in made:", "    bpy.data.objects[piece].rotation_euler[2] = math.radians(0)",
              "result = {\"made\": made}"]
    return "\n".join(lines)


CODE = script_code()
PLAN = {"operations": [operation("angelo_execute_python", description="Four lamp posts", code=CODE)],
        "reply": "Added four lamp posts."}
STOPPED = {"operations": [operation("angelo_execute_python", description="Lamp row",
                                    code=script_code(6, "Weathered Bronze"))], "reply": "Added a row of lamps."}
# The key's line, then ~200 characters more: at 5 characters a fragment the well shows that line, redacted, for
# well over its twice-a-second update before the code ends (test_blender_script_stream, check 4).
KEYED_CODE = ("import bpy\ntoken = \"%s\"\nlength = len(token)\nparts = token.split(\"-\")\n"
              "first = parts[0] if parts else \"\"\ncount = len(parts)\nscene = bpy.context.scene\n"
              "scene[\"token_parts\"] = count\nresult = {\"length\": length, \"parts\": count}" % KEY)
KEYED = {"operations": [operation("angelo_execute_python", description="Token length", code=KEYED_CODE)]}
CHARS, SECONDS = 10, 0.04  # About 250 characters a second, as recorded Opus 5.5 scripts were written.


def written_rounds():
    """The script streams for about 19 s, stays whole on screen for a moment, then the runtime refuses it."""
    return [round_events("msg_script", [PLAN], CHARS, SECONDS, base.refused("msg_script"), pause=1.5)]


def stopped_rounds():
    return [base.answered("msg_script_stopped", [STOPPED], CHARS, SECONDS)]


def keyed_rounds(gate):
    """The script carrying the key, fragment by fragment behind ``gate`` (``reply_stream_fixtures.key_rounds``)."""
    return [round_events("msg_script_key", [KEYED], base.KEYED_CHARS, 0.05, base.refused("msg_script_key"),
                         gate=gate)]
