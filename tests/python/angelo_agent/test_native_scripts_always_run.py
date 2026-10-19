# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A valid script always goes on to its approval: no screen returns it unrun for a typed alternative.

The routing screen (script_routing, since removed) answered a script that typed action kinds could express
with "Not run: ... send it again unchanged and it runs", and on Claude it rejected the whole plan and spent one of
its two rejections. It fired in none of the recorded runs since it landed, and it refused valid work by design:
tools aid the model, never handicap it (doc/angelo/performance.rst, "Tools aid, never handicap"). Pinned on the
real admission path (Controller.request_tool) and in the real Claude plan loop.
"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from angelo_agent.controller import Controller  # noqa: E402
from angelo_agent.request_fences import RequestFence  # noqa: E402

# A transform-only script: exactly what the screen used to return unrun.
MOVE = """# Move 'Islet 3' out from behind the pine. Transform only.
import bpy

def main():
    ob = bpy.data.objects.get('Islet 3')
    if ob is None:
        return {'done': False, 'reason': 'Islet 3 not found'}
    before = [round(v, 1) for v in ob.location]
    ob.location = (240.0, 700.0, 6.0)
    return {'done': True, 'before': before, 'after': [round(v, 1) for v in ob.location]}

result = main()
"""


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.c = Controller(self.temp.name)
        self.c.set_document(("factory-runtime", 17))
        self.c._begin_metrics()
        self.c.busy, self.c.cancelled = True, False
        self.replies = []

    def tearDown(self):
        self.c.close()
        self.temp.cleanup()

    def test_a_script_typed_kinds_could_express_waits_for_its_approval(self):
        self.c.request_tool("script-1", "angelo_execute_python", {"description": "Move the islet", "code": MOVE},
                            self.replies.append, request_fence=RequestFence.capture(self.c))
        self.assertEqual(self.replies, [], "Not answered: admitted and waiting for approval")
        self.assertEqual(len(self.c.pending), 1)
        counts = self.c._request_metrics.snapshot()["counts"]
        self.assertFalse(counts.get("rejections"))
        self.assertEqual(counts["python_fallbacks"], 1)

    def test_an_invalid_script_call_is_still_refused(self):
        self.c.request_tool("script-2", "angelo_execute_python", {"description": "No code"}, self.replies.append,
                            request_fence=RequestFence.capture(self.c))
        self.assertEqual(self.replies[0]["error_code"], "VALIDATION")
        self.assertFalse(self.c.pending)

    def test_the_screen_is_gone(self):
        self.assertIsNone(importlib.util.find_spec("angelo_agent.script_routing"))


class ClaudePlanTests(unittest.TestCase):
    def test_the_script_runs_in_the_plan_that_sends_it(self):
        from claude_protocol_fixtures import host
        from angelo_agent.providers import ClaudeProvider
        from angelo_agent.routing import RuntimeContext
        chat = host()
        chat.state["id"], chat.store, chat.models, chat.resolve_call = "chat-a", None, [], lambda name, arguments: arguments
        chat._closing = False
        context = RuntimeContext(chat, "claude_subscription", {}, "", None, chat._epoch)
        context.request_observation = Mock(side_effect=lambda callback: callback({"context_version": 2, "objects": []}))
        context.events, context.emit = chat.events, chat.emit
        for name in ("record_provider_turn", "record_model_round", "record_runtime_start", "record_provider_usage"):
            setattr(chat, name, Mock())
        context.native_tools = chat.native_tools
        provider = ClaudeProvider(context, {})
        script = {"tool": "angelo_execute_python", "arguments": {"description": "Move the islet", "code": MOVE}}
        provider._turn = Mock(side_effect=[{"reply": "", "operations": [script], "verify": False}])
        result = {"success": True, "result": 1, "output": "", "undoable": True, "external_effects_undoable": False,
                  "verified": {"scene": "Scene", "object_count": 4},
                  "changes": {"added": [], "removed": [], "modified": [{"name": "Islet 3", "fields": ["transforms"]}]}}
        provider._native_call = Mock(side_effect=[result])
        self.assertEqual(provider.run(None, "Move the islet", observation={}), "completed")
        self.assertEqual(provider._turn.call_count, 1, "No rejected plan before it ran")
        self.assertEqual([call.args[0] for call in provider._native_call.call_args_list], ["angelo_execute_python"])


if __name__ == "__main__":
    unittest.main()
