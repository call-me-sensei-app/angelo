# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scripted models for the unbilled dry run of a one-shot test (``scenario_dry_run``; no account, no inference).

Each plays a perfect agent for the one message of the test: it builds the
ground truth, renders the still the brief asks for, looks at its own result
and replies. Nothing is typed after the brief, so the dry run proves the
whole one-shot path on every side:

* ``OneShotMessages`` (the real Claude Code CLI on the MCP side): builds and
  renders through ``execute_blender_code``, then opens the PNG it rendered
  with Claude Code's own ``Read`` tool. That file is in stock Blender's
  working folder, outside Claude Code's, so the call succeeds only with the
  one-shot client allowances (``oneshot_mcp.client_arguments``).
* ``codex_script`` (the real ``codex app-server`` on the MCP side): builds
  and renders, then takes a screenshot through the MCP server.
* ``angelo_follow_up`` (Angelo's scripted runtime): after the build and
  before the render job, a view of the scene through the scene camera.

A mini asks for no still, so its scripted models only make the change and
reply.
"""

import json
import re

import scenario_fake_models as fake

READ = "Read"
SCREENSHOT = "mcp__blender__get_screenshot_of_window_as_image"
_STILL = re.compile(r'"still":\s*"([^"]+)"')


def turn_code(scenario, task):
    """The brief's ground truth and its still, with the still's path in the result (what a model reads back).

    A test that asks for no still (a mini) is the ground truth alone.
    """
    if not task.get("scenario_job"):
        return fake.turn_code(scenario, task["id"])
    lines = fake.turn_code(scenario, task["id"], task.get("scenario_job")).split("\n")
    lines[-1] = "result = {'turn': %r, 'objects': len(bpy.context.scene.objects), 'still': _scene.render.filepath}" \
        % task["id"]
    return "\n".join(lines)


def _stills(value):
    """Every ``still`` a tool result names, decoding JSON written inside JSON (strings, lists and objects)."""
    if isinstance(value, dict):
        if isinstance(value.get("still"), str):
            yield value["still"]
        for item in value.values():
            yield from _stills(item)
    elif isinstance(value, list):
        for item in value:
            yield from _stills(item)
    elif isinstance(value, str) and "still" in value:
        try:
            decoded = json.loads(value)
        except ValueError:   # Text around the result: its JSON string, unescaped once (or as written).
            match = _STILL.search(value)
            if match:
                try:
                    yield json.loads('"%s"' % match.group(1))
                except ValueError:
                    yield match.group(1)
            return
        yield from _stills(decoded)


def still_path(messages):
    """The still's path in the newest tool result that names one, or None.

    The result is decoded rather than searched with its backslashes removed: a Windows path has its own
    (a Windows dry run read ``C:angelo...``, and Claude Code's Read found no such file).
    """
    for message in reversed(messages):
        if message.get("role") != "user" or not isinstance(message.get("content"), list):
            continue
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                for path in _stills(block.get("content")):
                    return path
    return None


def codex_script(scenario, tasks):
    """FakeResponses steps: build and render, look at a screenshot, then the closing message."""
    task = tasks[0]
    build = ("const r = await tools.%s({code: %s}); text(JSON.stringify(r).slice(0, 300));"
             % (fake.TOOL, json.dumps(turn_code(scenario, task))))
    look = ("const r = await tools.%s({size_limit_in_bytes: 200000}); text('looked at the window');" % SCREENSHOT)
    steps = [("exec", build)] + ([("exec", look)] if task.get("scenario_outputs") else [])
    return steps + [("message", "Done: %s." % task["title"])]


def angelo_follow_up(task):
    """Tool calls Angelo's scripted runtime makes after the build, before the render job: one look at the scene.

    None for a test that asks for no still (a mini): its scripted agent only makes the change.
    """
    if not task.get("scenario_outputs"):
        return []
    return [("angelo_view", {"views": ["SCENE_CAMERA"], "shading": "SOLID", "resolution": 512})]


class OneShotMessages(fake.FakeMessages):
    """``FakeMessages`` for a one-shot brief: build and render, read the rendered image, reply."""

    def _decide(self, body):
        messages = body.get("messages") or []
        tools = {tool.get("name") for tool in body.get("tools") or []}
        decision = super()._decide(body)
        if decision[0] == "tool" and decision[1] == fake.TOOL:
            pending = self._turn_of(messages)
            return ("tool", fake.TOOL, {"code": turn_code(self.scenario, pending)}) if pending else decision
        if decision[0] == "text" and decision[1].startswith("Done"):
            path = still_path(messages)
            if path and READ in tools and not self._used(messages, READ):
                return ("tool", READ, {"file_path": path})
        return decision

    @staticmethod
    def _used(messages, name):
        return any(isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == name
                   for message in messages if message.get("role") == "assistant"
                   for block in (message.get("content") if isinstance(message.get("content"), list) else ()))
