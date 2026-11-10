# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute the composed draw and chat-switch block; native pixels remain a separate gate."""

import suite_isolation  # noqa: F401  First: no provider or credential source reaches this fixture.

import ast
from pathlib import Path
import sys
import time
import types
import unittest
from unittest import mock

ADDON = Path(__file__).resolve().parents[3] / "scripts" / "addons_core" / "angelo_agent"
NS = types.SimpleNamespace


def noop(*args, **kwargs):
    return None


def module(name, **fields):
    result = types.ModuleType(name)
    vars(result).update(fields)
    return result


def definitions(name, wanted, namespace):
    tree = ast.parse((ADDON / name).read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if len(selected) != len(wanted):
        raise AssertionError("Every requested production function must exist")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(ADDON / name), "exec"), namespace)


class ComposedDrawTests(unittest.TestCase):
    def draw(self, steer=False):
        calls, rows_at_page, fallback = [], [], []
        area = NS(as_pointer=lambda: 71, spaces=NS(active=NS(font_size=16, history=[])))
        controller = NS(state={"id": "chat-a", "messages": []}, busy=False)
        state = {}
        history = module("angelo_agent.chat_history")
        definitions("chat_history.py", {"scroll_key", "prepare_scroll"}, vars(history))
        history.group_blocks = lambda blocks, *args: blocks
        history.height = lambda state: 0
        history.scroll = lambda state, blocks, value, maximum, visible: min(value, maximum)
        history.draw = noop
        message = {"role": "user", "text": "Own message", "steer": True}
        blocks = [("user", message, [("Own message", 0)], 100, "row-a", 160)] if steer else []

        def build(controller, ui, expanded, state, *args):
            rows_at_page.append(state["history_scroll"])
            return blocks

        guard = NS(begin_draw=noop, call=lambda label, fn, *args, **kwargs: fn(*args),
                   checked=lambda blocks, *args: blocks, saved_state=lambda: None,
                   draw_plain=lambda *args: fallback.append(args), shown_approvals=lambda c, hits: hits,
                   errors=lambda: "")
        gpu_state = NS(blend_set=noop, scissor_get=lambda: (0, 0, 800, 600),
                       scissor_test_set=noop, scissor_set=noop)
        geometry = {"rect": (0, 0, 800, 40), "bubble_max_width": 600}
        modules = {
            "console": module("angelo_agent.console", is_chat=lambda area: True),
            "ui": module("angelo_agent.ui", get_controller=lambda: controller, connection_available=lambda: True),
            "chat_settings": module("angelo_agent.chat_settings", clean_options=noop, selected_model=noop),
            "chat_layout": module("angelo_agent.chat_layout", composer_layout=lambda *args: geometry),
            "chat_popovers": module("angelo_agent.chat_popovers", effective_effort=noop, effort_name=noop,
                                     model_name=noop, supports_fast=noop),
            "agent_workspace_ui": module("angelo_agent.agent_workspace_ui", native_cards=lambda c: [],
                                          control_lines=noop, preview_available=noop, draw_native_preview=noop),
            "chat_usage": module("angelo_agent.chat_usage", bar_height=lambda *args: 0),
        }
        probe = module("composed_chat_draw", __package__="angelo_agent")
        tokens = NS(CANVAS=object(), BUBBLE=object(), MUTED=object())
        vars(probe).update(
            sys=sys, time=time, bpy=NS(context=NS(area=area, region=NS(width=800, height=600))),
            gpu=NS(matrix=NS(push=noop, pop=noop, scale=noop), state=gpu_state),
            _enabled=True, _scale=1, _font=0, ui_scale=lambda: 1, _views={71: state},
            _scroll={(71, "chat-a"): 37}, _errors={}, draw_count=0, last_error=None,
            chat_guard=guard, canvas_rtl=NS(begin=noop, end=noop),
            chat_composer=NS(hint_width=lambda width: width, attachment_rows=lambda c: 0,
                             composer_rows=lambda *args: 1, draw=noop),
            wrap=lambda *args, **kwargs: [], _wrap=noop,
            change_counts=lambda messages: (0, 0, 0), file_counts=lambda messages: (0, 0, 0),
            shape=noop, text=lambda *args: calls.append(args), icon=noop, measure=lambda *args: 20,
            iface_=lambda value, **fields: value, elide=lambda value, *args: value,
            restore_link=lambda width, label: (0, (0, 1)),
            tk=tokens, chat_history=history, chat_blocks=NS(build=build, cards=lambda *args: []),
            marble_draw=NS(with_hero=lambda blocks, c: blocks), _flow_wrap=noop,
            chat_welcome=NS(draw_empty=noop, reconnect_height=lambda *args: 0),
            chat_pending=NS(tray_height=lambda *args, **kwargs: 0,
                            steer_caption=lambda row: "Delivered while working" if steer else None),
            approval_wait=NS(place=lambda state, c, blocks, scroll, visible: scroll),
            _scissor=lambda *args: args, chat_rows=NS(ROLES=set()),
            chat_scrollbar=NS(draw=noop), clipped_hits=lambda hits, viewport: hits,
            clip_rect=lambda rect, viewport: rect, _this_module=lambda: probe,
            chat_card_draw=NS(draw_pills=noop), chat_start_states=NS(draw_blocked=noop),
            chat_notices=NS(draw=noop), chat_replies=NS(draw=noop),
            canvas_controls=NS(draw_canvas=noop), draw_popover=noop)
        package = module("angelo_agent", __path__=[])
        for name, child in modules.items():
            setattr(package, name, child)
        injected = {"angelo_agent": package, "composed_chat_draw": probe}
        injected.update({"angelo_agent." + name: child for name, child in modules.items()})
        definitions("chat_view.py", {"_draw_chat"}, vars(probe))
        with mock.patch.dict(sys.modules, injected):
            probe._draw_chat()
        self.assertIsNone(probe.last_error, "The actual composed draw must reach completion")
        self.assertEqual(fallback, [], "A row fallback cannot hide a composition NameError")
        self.assertEqual(probe.draw_count, 1)
        return calls, rows_at_page, tokens

    def test_actual_draw_reads_editor_scroll_before_page(self):
        _, observed, _ = self.draw()
        self.assertEqual(observed, [37])

    def test_actual_draw_delivered_steer_uses_theme_role(self):
        calls, _, tokens = self.draw(steer=True)
        captions = [args for args in calls if args[0] == "Delivered while working"]
        self.assertEqual(len(captions), 1)
        self.assertIs(captions[0][-1], tokens.MUTED)



if __name__ == "__main__":
    unittest.main()
