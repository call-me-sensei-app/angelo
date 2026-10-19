# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripts and scripted rounds of ``test_blender_turn_recovery.py``.

Dependency-free. The reviewed scripts the stand-in model asks for, and the
stream events the stand-in ``claude`` CLI plays for a round
(``fake_provider_cli.play_round``).
"""

SESSION = "fixture-turn-recovery"
# Adds an object, doubles the camera's focal length, removes an object, enters Edit Mode and raises on line 7. Nothing
# but the two objects shows in a read-back of names, and the mode switch moves Blender to another Undo stack.
RAISING = ("import bpy\n"
           "def build(count):\n"
           "    mesh = bpy.data.meshes.new('Half Applied Mesh')\n"
           "    half = bpy.data.objects.new('Half Applied', mesh)\n"
           "    bpy.context.scene.collection.objects.link(half); bpy.context.scene.camera.data.lens *= 2\n"
           "    bpy.data.objects.remove(bpy.data.objects['Second Try'], do_unlink=True); bpy.ops.object.mode_set(mode='EDIT')\n"
           "    return [0, 1, 2, 3][count]\n"
           "build(4)\n")
CORRECTED = ("import bpy\n"
             "mesh = bpy.data.meshes.new('Half Applied Mesh')\n"
             "half = bpy.data.objects.new('Half Applied', mesh)\n"
             "bpy.context.scene.collection.objects.link(half)\n"
             "half.location = (5, 0, 0)\n"
             "bpy.context.scene.camera.data.lens *= 2\n"
             "result = {'object': half.name}\n")
PLAIN = ("import bpy\n"
         "bpy.context.scene.collection.objects.link(bpy.data.objects.new('Plain Step', None))\n")
IN_EDIT_MODE = ("import bpy\n"
                "bpy.context.scene.camera.data.lens *= 2\n"
                "raise KeyError('Scale')\n")
OWN_STEP = ("import bpy\n"
            "bpy.context.scene.collection.objects.link(bpy.data.objects.new('Kept Half', None))\n"
            "bpy.ops.ed.undo_push(message='A step of the script')\n"
            "raise KeyError('Scale')\n")
PREFERENCES = ("import bpy\n"
               "prefs = bpy.context.preferences\n"
               "result = {'undo_steps': prefs.edit.undo_steps}\n")


def stream(event):
    return {"type": "stream_event", "event": event, "session_id": SESSION}


def result(plan):
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": SESSION, "duration_ms": 1000,
            "structured_output": plan}


def operation(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def reviewed(description, code, **plan):
    """A plan of one reviewed script."""
    return result(dict({"reply": "", "operations": [operation("angelo_execute_python", description=description, code=code)]},
                       **plan))


def lookups_rounds():
    """Rounds 0 and 1: thinking that streams, then a plan of three lookups written slowly enough to be seen."""
    thinking = [stream({"type": "content_block_delta", "index": 0, "delta": {
        "type": "thinking_delta", "thinking": "Checking what this Blender calls the Cycles settings. "}}), {"sleep": 0.4}]
    first = [
        stream({"type": "message_start", "message": {"id": "msg_lookups", "model": "claude-opus-5-5", "usage": {
            "input_tokens": 100, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 9000, "output_tokens": 1}}}),
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
        *(thinking * 3),
        stream({"type": "content_block_stop", "index": 0}),
        stream({"type": "content_block_start", "index": 1, "content_block": {
            "type": "tool_use", "id": "toolu_lookups", "name": "StructuredOutput", "input": {}}}),
        {"sleep": 2.5},
        stream({"type": "content_block_stop", "index": 1}),
        stream({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 300}}),
        result({"reply": "", "operations": [
            operation("angelo_agent_capabilities", family="CHECKS", names=["mesh_manifold", "aim"]),
            operation("angelo_blender_api", domain="RNA", name="NoSuchRenderSettings", member="", offset=0, limit=8),
            operation("angelo_blender_api", domain="RNA", name="CyclesRenderSettings", member="samples", offset=0, limit=8)]})]
    return {0: first, 1: [result({"reply": "The settings type is CyclesRenderSettings; samples is an integer.",
                                  "operations": []})]}
