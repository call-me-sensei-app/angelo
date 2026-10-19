# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The scripted models of a one-shot dry run: build, render, look, reply, all in the one message.

No process is started here. The dry run itself needs the Angelo binary,
stock Blender, the MCP server and both clients
(``bench_mcp_vs_angelo.py --task-set oneshot-<id> --dry-run-scenario``);
these tests pin what each scripted model asks for.
"""

import ast
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import oneshot_fake_models as oneshot_fake  # noqa: E402
import scenario_dry_run  # noqa: E402
import scenario_fake_models as fake  # noqa: E402
import scenario_specs as specs  # noqa: E402


class ScriptedModelTests(unittest.TestCase):
    def setUp(self):
        self.name = "oneshot_footbridge"
        self.task, = specs.tasks(self.name)

    def test_the_turn_code_builds_renders_and_reports_the_still(self):
        code = oneshot_fake.turn_code(self.name, self.task)
        ast.parse(code)
        self.assertIn("import scenario_oneshot_footbridge as _scenario", code)
        self.assertLess(code.index("build_turn(bpy, 'brief')"), code.index("bpy.ops.render.render(write_still=True)"))
        self.assertIn("os.path.join(os.getcwd(), 'renders/footbridge-final.png')", code)
        self.assertTrue(code.splitlines()[-1].startswith("result = {") and "'still': _scene.render.filepath" in code)
        plain = fake.turn_code(self.name, "brief")                # Angelo's scripted runtime: the job tool renders.
        self.assertNotIn("render.render", plain)

    def messages(self):
        tool = {"type": "tool_use", "id": "t1", "name": fake.TOOL, "input": {}}
        built = {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": json.dumps({
            "status": "ok", "result": {"turn": "brief", "still": "/run/mcp-trial-01/renders/footbridge-final.png"}})}]}
        return [{"role": "user", "content": [{"type": "text", "text": self.task["prompt"]}]},
                {"role": "assistant", "content": [tool]}, {"role": "user", "content": [built]}]

    def test_claude_side_builds_then_reads_its_render_then_replies(self):
        model = oneshot_fake.OneShotMessages.__new__(oneshot_fake.OneShotMessages)
        model.scenario, model.tasks = self.name, [self.task]
        tools = [{"name": fake.TOOL}, {"name": "Read"}]
        messages = self.messages()
        first = model._decide({"messages": messages[:1], "tools": tools})
        self.assertEqual(first[:2], ("tool", fake.TOOL))
        self.assertEqual(first[2]["code"], oneshot_fake.turn_code(self.name, self.task))
        second = model._decide({"messages": messages, "tools": tools})
        self.assertEqual(second, ("tool", "Read", {"file_path": "/run/mcp-trial-01/renders/footbridge-final.png"}))
        read = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "Read", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "image"}]}]}]
        third = model._decide({"messages": messages + read, "tools": tools})
        self.assertEqual(third[0], "text")
        self.assertTrue(third[1].startswith("Done"))
        deferred = model._decide({"messages": messages[:1], "tools": [{"name": "ToolSearch"}, {"name": "Read"}]})
        self.assertEqual(deferred[:2], ("tool", "ToolSearch"))    # As for a scenario: the MCP tool is loaded first.
        self.assertEqual(oneshot_fake.still_path(messages), "/run/mcp-trial-01/renders/footbridge-final.png")
        nested = [{"role": "user", "content": [{"type": "tool_result", "content": json.dumps(
            {"text": json.dumps({"still": "/a/b.png"})})}]}]
        self.assertEqual(oneshot_fake.still_path(nested), "/a/b.png")
        self.assertIsNone(oneshot_fake.still_path(messages[:2]))

    def test_the_still_path_keeps_a_windows_paths_own_backslashes(self):
        # On a Windows dry run the backslashes were stripped and Claude Code's Read got "C:angelo...".
        still = "C:\\bench\\scripted-oneshot-castle\\mcp-trial-01\\renders/castle-final.png"
        result = json.dumps({"status": "ok", "result": {"turn": "brief", "still": still}})
        for content in ([{"type": "text", "text": result}], json.dumps({"text": result}),
                        [{"type": "text", "text": "Result: " + result}]):
            with self.subTest(content=str(content)[:40]):
                messages = [{"role": "user", "content": [{"type": "tool_result", "content": content}]}]
                self.assertEqual(oneshot_fake.still_path(messages), still)

    def test_codex_side_builds_looks_and_replies(self):
        script = oneshot_fake.codex_script(self.name, [self.task])
        self.assertEqual([step[0] for step in script], ["exec", "exec", "message"])
        self.assertIn("tools.%s(" % fake.TOOL, script[0][1])
        self.assertIn("render.render", script[0][1])
        self.assertIn("tools.%s(" % oneshot_fake.SCREENSHOT, script[1][1])

    def test_angelo_side_looks_through_the_scene_camera(self):
        from angelo_agent import view_spec
        (name, arguments), = oneshot_fake.angelo_follow_up(self.task)
        self.assertEqual(name, "angelo_view")
        self.assertEqual(view_spec.validate_view(name, arguments)["views"], ["SCENE_CAMERA"])
        source = (HERE / "scenario_dry_run_session.py").read_text()
        self.assertLess(source.index("oneshot_fake_models.angelo_follow_up(task)"), source.index('"angelo_job_start"'))

    def test_the_dry_run_uses_the_one_shot_models_only_for_one_shot_tests(self):
        source = (HERE / "scenario_dry_run.py").read_text()
        self.assertIn("oneshot_fake.OneShotMessages if specs.is_oneshot(name) else fake.FakeMessages", source)
        self.assertIn("(oneshot_fake if specs.is_oneshot(name) else fake).codex_script(name, tasks)", source)
        self.assertIn("oneshot_report.dry_run_checks(output)", source)
        self.assertIs(scenario_dry_run.oneshot_fake, oneshot_fake)
        scenario = fake.codex_script("bridge", specs.tasks("bridge"))   # A scenario's script is what it was.
        self.assertEqual(len(scenario), 2 * len(specs.tasks("bridge")))
        self.assertFalse(any(oneshot_fake.SCREENSHOT in step[1] for step in scenario))


if __name__ == "__main__":
    unittest.main()
