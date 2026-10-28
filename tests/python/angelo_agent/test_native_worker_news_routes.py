# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every place that serializes a lead's results or history for a model is a known route of ``worker_news`` (break-it
OVH6: six rounds each found a route that bypassed acknowledgement). Call sites are found from the source, never a
hand list of files: a new serializer call anywhere fails here until it is routed (names news through ``noted`` /
``prompt_news`` and acknowledges after its write) or declared to tell nothing (it writes no news field)."""

import ast
import sys
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ADDON.parent))
SERIALIZERS = {"model_result", "plan_results", "history_result_text", "handoff"}
# Each serializer call node, by "module:enclosing function:serializer#ordinal in that function" (break-it OVH7-L-2: a
# second call inside a listed function is a new identity, so it fails here until routed or declared).
ROUTED = {   # call -> how what it writes reaches the model and is acknowledged
    "providers.py:tool_content:model_result#1": "Codex dynamic tools: noted, acknowledged in codex_delivery after "
                                                "rpc.respond",
    "claude_direct.py:_packed:plan_results#1": "Claude direct tools: noted, acknowledged in claude_direct.answer after "
                                               "the write",
    "providers.py:_run_plans:plan_results#1": "Claude typed plans: noted and staged, told by round_written after the "
                                              "round's write",
    "controller.py:_context_blocks:handoff#1": "every opening prompt: prompt_news after the handoff, staged, told by "
                                               "round_written",
}
TELLS_NOTHING = {   # call -> why what it writes carries no news field (so it acknowledges nothing)
    "routing.py:handoff:history_result_text#1": "history rows: rows never tell",
    "routing.py:handoff:history_result_text#2": "history rows (compact form): rows never tell",
    "routing.py:handoff:model_result#1": "history rows (compact form): rows never tell",
    "routing.py:shared_context:handoff#1": "a voice session's history: no worker tools, rows never tell",
    "model_results.py:model_result:model_result#1": "the copy itself (a normalised lookup's slots; its callers route)",
    "model_results.py:plan_results:model_result#1": "the copy itself (its callers route)",
    "worker_schemas.py:render.text:model_result#1": "a worker's lead-resolved schema block: lookups, no worker news",
}


class _Calls(ast.NodeVisitor):
    def __init__(self, module, source):
        self.module, self.source, self.stack, self.count, self.found = module, source, [], {}, {}

    def visit_FunctionDef(self, node):
        self.stack.append(node)
        self.generic_visit(node)
        self.stack.pop()
    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node):
        name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if name in SERIALIZERS and self.stack:
            where = (self.module, ".".join(f.name for f in self.stack), name)
            self.count[where] = self.count.get(where, 0) + 1
            self.found["%s:%s:%s#%d" % (where + (self.count[where],))] = (
                ast.get_source_segment(self.source, self.stack[-1]), node.lineno - self.stack[-1].lineno)
        self.generic_visit(node)


def call_sites():
    """{call identity: (enclosing function's source, the call's line offset in it)} for every serializer call node."""
    found = {}
    for path in sorted(ADDON.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        visitor = _Calls(path.name, source)
        visitor.visit(ast.parse(source))
        found.update(visitor.found)
    return found


class RouteTests(unittest.TestCase):
    def test_every_serializer_call_site_is_a_known_route(self):
        self.assertEqual(set(call_sites()), set(ROUTED) | set(TELLS_NOTHING))

    def test_a_second_call_in_a_listed_function_is_a_new_identity(self):
        source = "def tool_content(c, n, r):\n    a = model_result(n, r)\n    return model_result(n, a)\n"
        visitor = _Calls("providers.py", source)
        visitor.visit(ast.parse(source))
        self.assertEqual(sorted(visitor.found), ["providers.py:tool_content:model_result#1",
                                                 "providers.py:tool_content:model_result#2"])
        self.assertNotIn("providers.py:tool_content:model_result#2", set(ROUTED) | set(TELLS_NOTHING))

    def test_each_routed_call_is_followed_by_its_news(self):
        """The routed call's copy passes through ``noted`` / ``prompt_news`` after it, in the same function."""
        sites = call_sites()
        for site in ROUTED:
            with self.subTest(site=site):
                body, offset = sites[site]
                after = "\n".join(body.splitlines()[offset:])
                self.assertTrue("worker_news.noted(" in after or "worker_news.prompt_news(" in after)

    def test_the_codex_route_names_a_finish_in_the_copy_it_returns(self):
        """One behavioural branch: ``providers.tool_content`` itself (the copy Codex reads)."""
        from types import SimpleNamespace
        from unittest import mock
        from angelo_agent import parallel_sessions, worker_news, worker_sessions
        from angelo_agent.providers import tool_content
        patches = [mock.patch.object(worker_sessions, "_started", {"lead": ["w1"]}),
                   mock.patch.object(worker_sessions, "_parts", {"w1": (1, "Sofa", Path("/a"))}),
                   mock.patch.object(worker_sessions, "finished", lambda s: True),
                   mock.patch.object(parallel_sessions, "_merged", {}), mock.patch.object(parallel_sessions, "_merging", set())]
        patches += [mock.patch.object(worker_news, name, {}) for name in ("_told", "_told_cards", "_staged", "_turn")]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        lead = SimpleNamespace(state={"id": "lead"}, short_identifiers=False)
        items = tool_content(lead, "angelo_scene_search", {"objects": [], "read_only": True})
        self.assertIn('"%s":["1 Sofa"]' % worker_news.FIELD, items[0]["text"])
        self.assertEqual(worker_news.packed()[1], {("w1", 0)})   # Kept for the write, nothing told yet.
        self.assertEqual(worker_news.fresh("lead"), ["w1"])

    def test_each_route_acknowledges_after_its_write(self):
        delivery = (ADDON / "codex_delivery.py").read_text(encoding="utf-8")
        self.assertLess(delivery.index("rpc.respond(request_id, payload)"), delivery.index("worker_news.acknowledge(news)"))
        claude = (ADDON / "claude_direct.py").read_text(encoding="utf-8")
        reply = claude[claude.index("    def result(value):"):]
        self.assertLess(reply.index("if reply("), reply.index("worker_news.acknowledge(news)"))
        usage = (ADDON / "provider_usage.py").read_text(encoding="utf-8")
        self.assertIn("worker_news.round_written(controller)", usage)
        providers = (ADDON / "providers.py").read_text(encoding="utf-8")
        turn = providers[providers.index("    def _turn("):providers.index("    def _run_plans(")]
        self.assertLess(turn.index("self._write({\"type\": \"user\""), turn.index("_record_runtime_round(context, claude=True)"))
        attempts = (ADDON / "codex_attempts.py").read_text(encoding="utf-8")
        send = attempts[attempts.index("def send("):]
        self.assertLess(send.index("provider._scope.turns.start("), send.index("record_runtime_round(context)"))


if __name__ == "__main__":
    unittest.main()
