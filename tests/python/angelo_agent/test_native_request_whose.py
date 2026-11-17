# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whose request a chat send is, and what Angelo's own request rows keep (``request_rows``). No bpy.

Evidence (break-it EVENT R2, ported from the 1.1 line to 2.0.0):

* R2-L-6: a worker's brief, written by its lead's model with the person's words quoted in it, was sent with no kind,
  so ``request_rows.sender`` took it as the person's own words (task memory accepted a URL in it as their evidence).
  Every chat send names whose it is: the person's Enter (``console.submit``) is the one send without a kind, and a
  connected app's task is marked by ``Controller._external_task``. The audit below reads every ``.send(`` call in the
  add-on that passes ``Controller.send``'s own arguments, so a new non-person send site without a kind fails here.
* R2-L-1: Angelo's own request rows the chat draws as a notice (``chat_blocks``: the worker-done event, a held goal's
  round) had no Restore, so their turns were undone only through Blender Undo.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
import angelo_agent  # noqa: E402
from angelo_agent import request_rows  # noqa: E402

# ``Controller.send``'s own keyword arguments: a ``.send(`` call passing any of them is a chat send.
CHAT_SEND_ARGUMENTS = frozenset({"observation", "recovery_evidence", "host_request", "host_kind", "references",
                                 "pending", "submission", "use_composer_choices"})
# Sends that may omit a kind, with why. Nothing else may.
PERSON_OR_MARKED = {
    ("console.py", "submit"): "the person's Enter; Angelo's follow-ups pass their kind in **send_options",
}


def chat_sends(source, name="<planted>"):
    """``(file, function, line, says_whose)`` for every chat send in ``source``."""
    found, stack = [], []

    class Walker(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "send":
                names = {keyword.arg for keyword in node.keywords}
                if names & CHAT_SEND_ARGUMENTS:
                    whose = any(keyword.arg in {"host_kind", "host_request"}
                                and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is None)
                                for keyword in node.keywords)
                    found.append((name, stack[0] if stack else "", node.lineno, whose))
            self.generic_visit(node)

    Walker().visit(ast.parse(source))
    return found


def every_chat_send():
    return [send for path in sorted(ADDON.rglob("*.py")) for send in chat_sends(path.read_text(), path.name)]


class SendSiteTests(unittest.TestCase):
    def test_every_chat_send_but_the_persons_says_whose_it_is(self):
        unmarked = [(name, function, line) for name, function, line, whose in every_chat_send()
                    if not whose and (name, function) not in PERSON_OR_MARKED]
        self.assertEqual(unmarked, [], "A chat send that is not the person's must pass host_kind (request_rows)")

    def test_the_audit_reads_the_real_send_sites(self):
        sites = {(name, function) for name, function, _line, _whose in every_chat_send()}
        for site in (("worker_sessions.py", "_send"), ("worker_resume.py", "_fire"), ("goal_loop.py", "_send_round"),
                     ("worker_chat_host.py", "_send"), ("console.py", "submit")):
            self.assertIn(site, sites)
        self.assertTrue(set(PERSON_OR_MARKED) <= sites, "An allowance for a send site that no longer exists")

    def test_the_audit_refuses_a_planted_send_without_a_kind(self):
        planted = chat_sends("def brief(chat, turn):\n    chat.send('x', observation=turn.observation)\n"
                             "def event(chat, turn):\n    chat.send('x', observation=1, host_kind=None)\n"
                             "def marked(chat, turn):\n    chat.send('x', observation=1, host_kind=KIND)\n"
                             "def socket(sock):\n    sock.send(b'x')\n")
        self.assertEqual([(function, whose) for _name, function, _line, whose in planted],
                         [("brief", False), ("event", False), ("marked", True)])

    def test_a_workers_brief_is_sent_as_one(self):
        from angelo_agent import worker_routing, worker_sessions
        sent = []
        chat = SimpleNamespace(send=lambda text, **kwargs: sent.append(kwargs))
        fakes = {"ui": SimpleNamespace(prepare_controller=lambda controller: None, main_window=lambda: None),
                 "worker_facts": SimpleNamespace(briefed=lambda controller: None)}
        with mock.patch.multiple(angelo_agent, create=True, **fakes), \
                mock.patch.dict(sys.modules, {"angelo_agent." + name: fake for name, fake in fakes.items()}), \
                mock.patch.object(worker_routing, "deliver", lambda controller, window, text, send, failed: send(
                    SimpleNamespace(observation={}, recovery_evidence={}))):
            worker_sessions._send(chat, "Part: Sofa.", "lead/1")
        self.assertEqual([kwargs.get("host_kind") for kwargs in sent], [request_rows.WORKER_BRIEF])
        drafted = SimpleNamespace(attachments=[{"name": "staged.png"}])   # The files Angelo staged for the brief.
        files, kind, persons = request_rows.sender(drafted, None, sent[0]["host_kind"])
        self.assertEqual((list(files), kind, persons), ([{"name": "staged.png"}], request_rows.WORKER_BRIEF, False))

    def test_angelos_own_follow_up_says_whose_it_is(self):
        from angelo_agent import chat_pending, console
        controller = SimpleNamespace(turn_running=lambda: False, inbox=SimpleNamespace(has_due=lambda: False))
        with mock.patch.object(console, "submit") as submit, \
                mock.patch.object(chat_pending.edit_tool_hold, "running", return_value=False):
            with self.assertRaises(ValueError):
                chat_pending.send_or_queue(controller, "Continue the plan", "Continue")
            submit.assert_not_called()
            chat_pending.send_or_queue(controller, "Continue the plan", "Continue", host_request=[])
            chat_pending.send_or_queue(controller, "Continue the plan", "Continue", host_kind=request_rows.GOAL)
        self.assertEqual(submit.call_count, 2)


class RestoreTests(unittest.TestCase):
    """R2-L-1: every request row the chat draws as a notice keeps its snapshot's Restore, as a bubble does."""

    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {"bpy": mock.MagicMock()})   # The notice's text needs no Blender.
        patcher.start()
        self.addCleanup(patcher.stop)

    def rows(self):
        return [{"role": "user", "request_kind": request_rows.EVENT, "parts": ["1 Sofa"], "checkpoint": "cp-7",
                 "text": "From Angelo"},
                {"role": "user", "request_kind": request_rows.GOAL, "parts": [], "goal": {"round": 2},
                 "checkpoint": "cp-8", "text": "Next round"}]

    def test_each_notice_request_restores_its_snapshot(self):
        from angelo_agent import activity_text, chat_rows
        rows = self.rows()
        self.assertEqual({row["request_kind"] for row in rows}, set(request_rows.NOTICE_REQUESTS))
        for row in rows:
            view = activity_text.notice_view(row)
            self.assertEqual(view["actions"][0][:2], ("restore", row["checkpoint"]), row)
            drawn = SimpleNamespace(measure=lambda text, size: len(text) * size * .5, wrap=None)
            with mock.patch.object(chat_rows, "_view", return_value=drawn), \
                    mock.patch("angelo_agent.ui_kit_status._lines",
                               side_effect=lambda view, text, width, size, mono=False: [text] if text else []), \
                    mock.patch("angelo_agent.ui_kit.measure", side_effect=lambda view, text, size, mono=False:
                               len(text) * size * .5):
                _height, hits = chat_rows._notice_layout({"message": row, "tone": "neutral", "headline": "x",
                                                          "body": "", "actions": view["actions"], "details": None,
                                                          "opened": False}, 400, 600, 1.0, False)
            self.assertIn(("restore", row["checkpoint"]), [hit[1:] for hit in hits], row)

    def test_the_chat_draws_as_notices_exactly_the_requests_that_restore_there(self):
        tree = ast.parse((ADDON / "chat_blocks.py").read_text())
        literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)}
        self.assertFalse(literals & set(request_rows.HOST_REQUESTS), "chat_blocks reads request_rows.drawn_as_notice")
        for row in self.rows():
            self.assertTrue(request_rows.drawn_as_notice(row))
        for kind in request_rows.HOST_REQUESTS - request_rows.NOTICE_REQUESTS:   # Bubbles: Restore under them.
            self.assertFalse(request_rows.drawn_as_notice({"role": "user", "request_kind": kind}))

    def test_without_a_snapshot_or_as_the_persons_it_is_no_such_notice(self):
        from angelo_agent import activity_text
        self.assertIsNone(activity_text.notice_view({"role": "user", "request_kind": request_rows.EVENT, "text": "x"}))
        self.assertIsNone(activity_text.notice_view({"role": "user", "checkpoint": "cp-1", "text": "Make a lamp"}))
        self.assertIsNone(activity_text.notice_view({"role": "user", "request_kind": request_rows.WORKER_BRIEF,
                                                     "checkpoint": "cp-2", "text": "Part: Sofa."}))


if __name__ == "__main__":
    unittest.main()
