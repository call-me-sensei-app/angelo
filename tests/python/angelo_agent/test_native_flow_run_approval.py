# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A Generation Flow run starts only from the dialog that showed it, once (1.0.5 break-it review, part A).

Blender's Repeat Last (Shift+R) and Repeat History call an operator's ``execute`` again with its last properties and
no dialog. ``angelo.flow_run`` compared only the plan's digest with the last dialog's, which a failed or stopped run
leaves unchanged, and the dialog's ticks were already cleared, so a repeat started every paid generation again, the
steps the person had unticked included. The real ``flow_ui`` and ``flow_graph`` run here; ``bpy``, the plan builder and
``start_run`` are stand-ins. Reproduction: notes flow-chrome-105/repro_repeat_last_run.py.
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


def _bpy_modules():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    identity = lambda text, context=None: text  # noqa: E731
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=identity, pgettext_tip=identity,
                                                           pgettext_rpt=identity, pgettext=identity, locale="en_US"),
                              handlers=handlers, background=True)
    bpy.utils = types.ModuleType("bpy.utils")
    bpy.utils.register_class = bpy.utils.unregister_class = lambda cls: None
    previews = types.ModuleType("bpy.utils.previews")
    previews.new = lambda: {}
    bpy.utils.previews = previews
    bpy.data = SimpleNamespace(node_groups=[])
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
            "bpy.app.handlers": handlers, "bpy.utils": bpy.utils, "bpy.utils.previews": previews}


MODULES = _bpy_modules()
with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import flow_ui  # noqa: E402

FLOW = {"nodes": [{"id": "Cheap", "type": "GENERATE_IMAGE"}, {"id": "Expensive", "type": "GENERATE_3D"}], "links": []}


def _line(node, price):
    return {"node": node, "label": node, "action": "GENERATE", "count": 1, "review": False, "price_each": price,
            "reason": "retry after failure", "account": "acct", "approval_digest": node}


PLAN = {"scope": "ALL", "node": "", "lines": [_line("Cheap", 0.04), _line("Expensive", 0.50)], "digest": "same-plan"}


class Lines(list):
    def clear(self):
        del self[:]

    def add(self):
        item = SimpleNamespace(node="", label="", detail="", include=True, stop=False)
        self.append(item)
        return item


class RunApprovalTests(unittest.TestCase):
    def setUp(self):
        self.started = []
        self.lines = Lines()
        self.context = SimpleNamespace(window=object(), window_manager=SimpleNamespace(
            angelo_flow_run_lines=self.lines, invoke_props_dialog=lambda op, **kwargs: {'RUNNING_MODAL'}))
        flow_ui._dialog.clear()
        self.patches = [mock.patch.dict(sys.modules, MODULES),
                        mock.patch.object(flow_ui, "_tree", lambda name: SimpleNamespace(name=name)),
                        mock.patch.object(flow_ui, "build_plan", lambda tree, window, scope, node: (FLOW, PLAN)),
                        mock.patch.object(flow_ui, "connection_needs", lambda *args, **kwargs: {}),
                        mock.patch.object(flow_ui.flow_host, "connected_kinds", lambda: set()),
                        mock.patch.object(flow_ui.flow_host, "start_run", lambda tree, selection, owner:
                                          self.started.append(sorted(l["node"] for l in selection["now"])))]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        flow_ui._dialog.clear()

    def operator(self):
        op = flow_ui.ANGELO_OT_flow_run()
        op.tree, op.scope, op.node, op.approval = "Flow", "ALL", "", ""
        op.reports = []
        op.report = lambda kind, text: op.reports.append((sorted(kind), text))
        return op

    def confirm_cheap_only(self, op):
        self.assertEqual(op.invoke(self.context, None), {'RUNNING_MODAL'})
        for item in self.lines:
            item.include = item.node == "Cheap"   # The person unticks Expensive.
        return op.execute(self.context)

    def test_the_dialog_starts_what_the_person_confirmed(self):
        self.assertEqual(self.confirm_cheap_only(self.operator()), {'FINISHED'})
        self.assertEqual(self.started, [["Cheap"]])

    def test_repeating_the_confirmed_run_starts_nothing(self):
        op = self.operator()
        self.confirm_cheap_only(op)
        self.assertEqual(op.execute(self.context), {'CANCELLED'}, "Repeat Last calls execute with the same properties")
        self.assertEqual(self.started, [["Cheap"]], "nothing more started, and never the unticked step")

    def test_a_run_without_its_dialog_starts_nothing(self):
        op = self.operator()
        self.assertEqual(op.execute(self.context), {'CANCELLED'})
        flow_ui._dialog.update(plan=PLAN, flow=FLOW, approval="a-token-from-another-dialog")
        self.assertEqual(op.execute(self.context), {'CANCELLED'}, "a dialog's token is not this operator's")
        self.assertEqual(self.started, [])

    def test_a_newer_dialog_voids_an_older_one(self):
        first, second = self.operator(), self.operator()
        first.invoke(self.context, None)
        second.invoke(self.context, None)
        self.assertEqual(first.execute(self.context), {'CANCELLED'})
        self.assertEqual(self.started, [])

    def test_the_operator_is_never_in_repeat_history(self):
        self.assertNotIn('REGISTER', flow_ui.ANGELO_OT_flow_run.bl_options)


if __name__ == "__main__":
    unittest.main()
