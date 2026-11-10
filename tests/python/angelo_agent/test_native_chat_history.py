# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full transcript access with bounded layout, independently of provider context."""
import suite_isolation  # noqa: F401
import sys
import ast
import tempfile
import uuid
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent import chat_history, chat_blocks
from angelo_agent.sessions import SessionStore


class HistoryTests(unittest.TestCase):
    def test_all_rows_are_reachable_in_both_directions(self):
        messages = [{"role": "user" if i == 0 else "activity", "text": str(i)} for i in range(10001)]
        original = list(messages)
        state = {}
        seen = set()
        while True:
            rows = chat_history.page(messages, state, "chat")
            self.assertLessEqual(len(rows), 160)
            seen.update(row["text"] for row in rows)
            if state["history_range"][0] == 0:
                break
            chat_history.move(state, "earlier")
        self.assertEqual(len(seen), len(messages))
        self.assertIs(rows[0], messages[0])
        while state["history_range"][1] < len(messages):
            chat_history.move(state, "later")
            rows = chat_history.page(messages, state, "chat")
        self.assertIs(rows[-1], messages[-1])
        self.assertEqual(messages, original)

    def test_older_page_stays_put_during_append_and_session_switch_resets(self):
        messages = list(range(1000))
        state = {}
        chat_history.page(messages, state, "a")
        chat_history.move(state, "earlier")
        old = chat_history.page(messages, state, "a")
        messages.extend(range(1000, 1200))
        self.assertEqual(old, chat_history.page(messages, state, "a"))
        self.assertEqual(chat_history.page(messages, state, "b"), messages[-160:])
        chat_history.move(state, "first")
        self.assertEqual(chat_history.page(messages, state, "b"), messages[:160])
        chat_history.move(state, "latest")
        self.assertEqual(chat_history.page(messages, state, "b"), messages[-160:])

    def test_wheel_append_freezes_and_bottom_returns_to_follow(self):
        from angelo_agent import chat_scrollbar
        messages = list(range(1000))
        state = {"max_scroll": 1000}
        scrolls = {"a": 0}
        area = SimpleNamespace(tag_redraw=lambda: None)
        chat_history.page(messages, state, "a")
        with patch.object(chat_scrollbar, "_fade_later"):
            chat_scrollbar.handle(SimpleNamespace(type="WHEELUPMOUSE"), state, area, 100, 200, "a", scrolls)
            state["history_scroll"] = scrolls["a"]  # Actual draw seam, before chat_blocks.build.
            chat_history.page(messages, state, "a")
            self.assertEqual(state["history_start"], 840)
            chat_scrollbar.handle(SimpleNamespace(type="WHEELDOWNMOUSE"), state, area, 100, 200, "a", scrolls)
            state["history_scroll"] = scrolls["a"]
            chat_history.page(messages, state, "a")
            self.assertIsNone(state["history_start"])
            messages.append(1000)
            self.assertEqual(chat_history.page(messages, state, "a")[-1], 1000)
            chat_scrollbar.handle(SimpleNamespace(type="WHEELUPMOUSE"), state, area, 100, 200, "a", scrolls)
            messages.append(1001)  # Append arrives in the same redraw as the wheel.
            state["history_scroll"] = scrolls["a"]
            self.assertEqual(chat_history.page(messages, state, "a"), list(range(841, 1001)))

    def test_full_history_survives_provider_reset_compaction_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SessionStore(directory, str(uuid.uuid4()))
            state = store.new()
            state["messages"] = [{"role": "user" if i % 2 == 0 else "assistant", "text": str(i)}
                                 for i in range(10001)]
            store.save(state)
            state["native_id"], state["runtime_sessions"] = None, {}
            store.compact(state)
            reopened = SessionStore(directory, state["id"]).load()
            self.assertEqual(reopened["messages"], state["messages"])
            view = {}
            chat_history.page(reopened["messages"], view, state["id"])
            chat_history.move(view, "first")
            self.assertEqual(chat_history.page(reopened["messages"], view, state["id"])[0]["text"], "0")

    def test_hidden_page_can_be_crossed_without_a_scrollbar(self):
        from angelo_agent import chat_scrollbar
        messages = [{"role": "user", "text": "first"}] + [{"role": "user", "text": "hidden", "withdrawn": True}] * 500
        controller = SimpleNamespace(state={"id": "chat", "messages": messages}, _request_generation=0)
        state = {"max_scroll": 0}
        area = SimpleNamespace(tag_redraw=lambda: None)
        event = SimpleNamespace(type="WHEELUPMOUSE", value="PRESS")
        def block(_controller, ui, message, identity, *args):
            return None if message.get("withdrawn") else ("user", message, (), 20, identity, 0)
        with patch.object(chat_blocks, "_block", side_effect=block), patch.object(chat_blocks.chat_guard, "checked", side_effect=lambda blocks, *args: blocks):
            for _ in range(10):
                blocks = chat_blocks.build(controller, None, set(), state, 400, 14, 22, 300)
                if state["history_range"][0] == 0:
                    break
                chat_scrollbar.handle(event, state, area, 100, 200, "chat", {"chat": 0})
        self.assertEqual(blocks[0][1]["text"], "first")

    def test_resize_and_reply_growth_keep_anchor_relative_to_viewport_top(self):
        block = lambda key, height: ("assistant", {}, (), height, key, 0)
        state = {"history_visible": 100}
        old = [block(i, 20) for i in range(160)]
        chat_history.scroll(state, old, 1000, 3100, 100)
        old_positions = dict(state["history_offsets"])
        new = old[:-1] + [block(159, 100)]  # A streaming reply below the viewport grew.
        value = chat_history.scroll(state, new, 1000, 3080, 200)
        anchor = min(old_positions, key=lambda key: abs(old_positions[key] - 1000 - 100))
        self.assertEqual(state["history_offsets"][anchor] - value - 200,
                         old_positions[anchor] - 1000 - 100)

    def test_editors_have_independent_pages_offsets_and_cleanup(self):
        a, b = SimpleNamespace(as_pointer=lambda: 10), SimpleNamespace(as_pointer=lambda: 20)
        views = {10: {"session": "chat"}, 20: {"session": "chat"}}
        messages = list(range(1000))
        for view in views.values():
            chat_history.page(messages, view, "chat")
        chat_history.move(views[10], "first")
        self.assertEqual(chat_history.page(messages, views[10], "chat")[0], 0)
        self.assertEqual(chat_history.page(messages, views[20], "chat")[-1], 999)
        scrolls = {chat_history.scroll_key(a, "chat"): 100, chat_history.scroll_key(b, "chat"): 200,
                   (10, "old chat"): 300}
        chat_history.prune(views, scrolls, {20})
        self.assertEqual(views.keys(), {20})
        self.assertEqual(scrolls, {(20, "chat"): 200})

    def test_stream_overlay_keeps_original_or_generation_item_identity(self):
        original = {"role": "assistant", "text": ""}
        controller = SimpleNamespace(state={"id": "chat", "messages": [original]},
            _request_generation=1, _items={"reply": 0}, _streamed_replies={(1, "reply"): "stream"},
            redact=lambda text: text, api_key="")
        state = {}
        def block(controller, ui, message, identity, *args):
            return ("assistant", message, (), 20, identity, 0)
        with patch.object(chat_blocks, "_block", side_effect=block), patch.object(chat_blocks.chat_guard, "checked", side_effect=lambda blocks, *args: blocks):
            first = chat_blocks.build(controller, None, set(), state, 400, 14, 22, 300)
            second = chat_blocks.build(controller, None, set(), state, 400, 14, 22, 300)
            self.assertEqual(first[0][4], ("chat", id(original)))
            self.assertEqual(first[0][4], second[0][4])
            controller._items = {}
            first = chat_blocks.build(controller, None, set(), state, 400, 14, 22, 300)
            second = chat_blocks.build(controller, None, set(), state, 400, 14, 22, 300)
            self.assertEqual(first[-1][4], ("chat", (1, "reply")))
            self.assertEqual(first[-1][4], second[-1][4])
            self.assertNotIn("_display_identity", original)

    def test_scrollbar_boundary_release_reaches_actual_first_and_latest(self):
        from angelo_agent import chat_scrollbar
        state = {"message_viewport": (0, 100, 400, 500), "max_scroll": 1000,
                 "history_range": (500, 660, 1000), "scroll_drag": (0, 0)}
        area = SimpleNamespace(tag_redraw=lambda: None)
        with patch.object(chat_scrollbar, "_fade_later"):
            chat_scrollbar.handle(SimpleNamespace(type="LEFTMOUSE", value="RELEASE"), state, area,
                                  100, 200, "chat", {"chat": 1000})
            self.assertEqual(state["history_start"], 0)
            self.assertEqual(state["history_transition"], "first")
            state.update(scroll_drag=(0, 0), history_range=(0, 160, 1000))
            chat_scrollbar.handle(SimpleNamespace(type="LEFTMOUSE", value="RELEASE"), state, area,
                                  100, 200, "chat", {"chat": 0})
            self.assertIsNone(state["history_start"])
            self.assertEqual(state["history_transition"], "latest")

    def test_requested_first_page_is_not_sliced_away(self):
        messages = [{"role": "user", "text": "FIRST"}] + [{"role": "activity", "text": "tool"}] * 10000
        controller = SimpleNamespace(state={"id": "chat", "messages": messages}, _request_generation=0)
        state = {"history_session": "chat", "history_start": 0, "history_scroll": 1}
        def block(controller, ui, message, identity, *args):
            return ("user", message, (), 20, identity, 0)
        with patch.object(chat_blocks, "_block", side_effect=block), patch.object(chat_blocks.chat_guard, "checked", side_effect=lambda blocks, *args: blocks):
            blocks = chat_blocks.build(controller, None, set(), state, 400, 14, 22, 300)
        self.assertEqual(blocks[0][1]["text"], "FIRST")
        self.assertEqual(len(blocks), 160)

    def test_changed_drawing_owners_and_gui_fixture_parse(self):
        root = Path(__file__).resolve().parents[3]
        paths = [root / "scripts/addons_core/angelo_agent" / name for name in
                 ("chat_view.py", "chat_history.py", "chat_blocks.py", "chat_scrollbar.py", "marble_frames.py", "reply_overlay.py")]
        paths.append(Path(__file__).with_name("test_blender_chat_history.py"))
        for path in paths:
            with self.subTest(path=path.name):
                ast.parse(path.read_text())

    def test_spanning_group_is_a_fragment_and_older_tail_is_not_live(self):
        from angelo_agent import ui_text
        rows = [{"role": "activity", "text": "", "tool": "angelo_observe_scene", "status": "completed"}
                for _ in range(400)]
        state = {}
        page = chat_history.page(rows, state, "chat")
        blocks = [("activity", message, (), 36, i, 0) for i, message in enumerate(page)]
        groups = chat_history.group_blocks(blocks, set(), True, state)
        header = next(block[1] for block in groups if block[0] == "activity_group")
        self.assertEqual(header["title"], ui_text.iface_("More steps"))
        self.assertTrue(header["live"])  # The actual transcript tail is on this latest page.
        chat_history.move(state, "first")
        page = chat_history.page(rows, state, "chat")
        blocks = [("activity", message, (), 36, i, 0) for i, message in enumerate(page)]
        groups = chat_history.group_blocks(blocks, set(), True, state)
        header = next(block[1] for block in groups if block[0] == "activity_group")
        self.assertFalse(header["live"])
        self.assertEqual(header["status"], "done")
        self.assertEqual(header["title"], ui_text.iface_("More steps"))
        self.assertEqual(state["history_fragments"], 1)

    def test_first_latest_controls_include_pixel_endpoints(self):
        view = SimpleNamespace(BG=None, TEXT=None, MUTED=None, shape=lambda *args: None, text=lambda *args: None)
        state = {"history_range": (840, 1000, 1000), "history_scroll": 500, "max_scroll": 1000, "hits": []}
        chat_history.draw(view, state, 400, 700)
        self.assertTrue(any(action == "history" and value == "latest" for rect, action, value in state["hits"]))
        state.update(history_range=(0, 160, 1000), history_scroll=0, hits=[])
        chat_history.draw(view, state, 400, 700)
        self.assertTrue(any(action == "history" and value == "first" for rect, action, value in state["hits"]))

    def test_open_work_group_crosses_overlapping_pages_without_cache_growth(self):
        rows = [{"role": "activity", "text": "", "tool": "angelo_observe_scene", "status": "completed"}
                for _ in range(1000)]
        state, expanded = {}, set()
        chat_history.page(rows, state, "chat")
        chat_history.move(state, "first")
        for direction in (None, "later", "later", "earlier"):
            if direction:
                chat_history.move(state, direction)
            page = chat_history.page(rows, state, "chat")
            blocks = [("activity", message, (), 36, id(message), 0) for message in page]
            if direction is None:
                expanded.add(("group", blocks[0][4]))
            grouped = chat_history.group_blocks(blocks, expanded, True, state)
            header = next(block[1] for block in grouped if block[0] == "activity_group")
            self.assertTrue(header["opened"])
            self.assertFalse(header["live"])
            self.assertEqual(len(expanded), 1)
            self.assertLessEqual(sum(len(ids) for ids in state["history_group_members"].values()), 160)

    def test_shared_row_keeps_position_on_transition(self):
        block = lambda key: ("activity", {}, (), 20, key, 0)
        state = {"history_visible": 100}
        old = [block(i) for i in range(120, 280)]
        chat_history.scroll(state, old, 3100, 3100)
        state["history_transition"] = "earlier"
        new = [block(i) for i in range(160)]
        value = chat_history.scroll(state, new, 3100, 3100)
        self.assertEqual(state["history_offsets"][120] - value, 100)
        self.assertLessEqual(len(state["history_offsets"]), 160)


if __name__ == "__main__":
    unittest.main()
