# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live view's input hold without Blender (break-it P1-3): the 3D view a worker's live view covers never takes
input, whatever Angelo's handler hands on, and the exceptions it makes are only what they protect.

The in-Blender proofs are ``test_blender_live_view.py`` (the lease, framing, drags, the worker's size) and the session
rail's GUI suite (a passing modal, typing with a corner card, a lost release, the lead's capture).
"""

import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"
SOURCE = ADDON.parents[2] / "source/blender"
sys.path.insert(0, str(ADDON.parent))


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.__getattr__ = lambda name: type(name, (), {})   # Any Blender base class: a plain class.
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    bpy.app = SimpleNamespace(handlers=handlers, background=True)
    return {"bpy": bpy, "bpy.types": bpy.types, "bpy.app": bpy.app, "bpy.app.handlers": handlers}


with patch.dict(sys.modules, fake_bpy()):
    import angelo_agent  # noqa: E402
    from angelo_agent import live_layout, parallel_sessions, session_rail_input, worker_sessions  # noqa: E402
# The package as imported here: a module's own ``from . import name`` must find these (and the fakes set on them).
PACKAGE = {"angelo_agent": angelo_agent, "angelo_agent.parallel_sessions": parallel_sessions}


def inside(rect, x, y):
    return rect[0] <= x < rect[0] + rect[2] and rect[1] <= y < rect[1] + rect[3]


def card_host_with(card):
    """The rule ``card_host.takes_input`` and ``takes_keys`` follow, for one card over area 7 (pinned below)."""
    return SimpleNamespace(
        takes_input=lambda pointer, x, y: pointer == 7 and (card.modal or inside(card.rect, x, y)),
        takes_keys=lambda pointer: pointer == 7 and (card.modal or card.field))


def area(x, y, width, height, pointer=1):
    return SimpleNamespace(x=x, y=y, width=width, height=height, as_pointer=lambda: pointer,
                           regions=[SimpleNamespace(type='WINDOW', x=x, y=y, width=width, height=height)])


def event(kind, x=50, y=50, value='PRESS'):
    return SimpleNamespace(type=kind, value=value, mouse_x=x, mouse_y=y)


class NativeHoldTests(unittest.TestCase):
    """The backstop is Blender's own event dispatch: no handler of a held area runs."""

    def test_a_held_area_runs_none_of_its_handlers(self):
        source = (SOURCE / "windowmanager/intern/wm_event_system.cc").read_text(encoding="utf-8")
        loop = source[source.index("ED_screen_areas_iter (&win, screen, area) {\n          /* After restoring"):]
        hold = loop.index("WM_angelo_input_held(&win, area)")
        self.assertLess(hold, loop.index("wm_event_do_handlers_area_regions(C, event, area)"))
        self.assertIn("!wm_event_always_pass(event) && WM_angelo_input_held(&win, area)", loop)

    def test_over_a_held_area_the_window_runs_only_allowed_items_and_no_drop(self):
        source = (SOURCE / "windowmanager/intern/wm_event_system.cc").read_text(encoding="utf-8")
        self.assertIn("g_angelo_window_hold && !WM_angelo_input_hold_allows(kmi.idname)", source)
        self.assertIn("event->type == EVT_DROP && !g_angelo_window_hold", source)
        window = source[source.index("WM_angelo_input_held_at(&win, event->xy)"):]
        self.assertLess(window.index("g_angelo_window_hold = held;"),
                        window.index("wm_handlers_do(C, event, &win.runtime->handlers)"))

    def test_a_hold_is_a_lease_on_an_area_the_window_still_shows(self):
        source = (SOURCE / "windowmanager/intern/wm_angelo_input_hold.cc").read_text(encoding="utf-8")
        self.assertIn("BLI_time_now_seconds() + seconds", source)
        self.assertIn("hold.until > now", source)
        # Never a freed area or one of a screen no longer shown (P1-4 M1); never the guarded heap (P1-4 L5).
        self.assertIn("return shown_now(hold.win, hold.area) ? &hold : nullptr;", source)
        self.assertNotIn("static Vector<", source)
        self.assertNotIn("static Set<", source)

    def test_no_drop_offer_brush_or_overlay_of_the_document_over_a_held_view(self):
        drag = (SOURCE / "windowmanager/intern/wm_dragdrop.cc").read_text(encoding="utf-8")
        body = drag[drag.index("static wmDropBox *wm_dropbox_active("):]
        self.assertLess(body.index("WM_angelo_input_held_at(win, event->xy)"), body.index("dropbox_active(C, &region"))
        draw = (SOURCE / "windowmanager/intern/wm_draw.cc").read_text(encoding="utf-8")
        overlays = draw[draw.index("/* Draw overlays and paint cursors. */"):]
        self.assertLess(overlays.index("WM_angelo_input_held(win, area)"), overlays.index("wm_paintcursor_draw("))

    def test_python_sees_hold_allow_held_and_an_offscreen_capture(self):
        source = (SOURCE / "python/intern/bpy_angelo_live.cc").read_text(encoding="utf-8")
        for name in ('{"hold",', '{"release_holds",', '{"allow",', '{"held",', '{"window_pixels",'):
            self.assertIn(name, source)
        capture = (SOURCE / "windowmanager/intern/wm_angelo_input_hold.cc").read_text(encoding="utf-8")
        self.assertIn("WM_window_pixels_read_from_offscreen(C, win, r_size)", capture)
        self.assertIn("ED_screen_areas_iter (win, screen, area)", capture)   # Global areas too (P1-4 M3).
        self.assertIn("CTX_wm_region_popup_set(C, popup_prev);", capture)
        self.assertNotIn("frontbuffer", capture.split("The lead's own picture")[1])


class AddonHoldTests(unittest.TestCase):
    def test_the_live_view_is_drawn_only_while_its_view_is_held(self):
        draw = (ADDON / "live_view_draw.py").read_text(encoding="utf-8")
        body = draw[draw.index("def draw():"):draw.index("def _free_offscreens")]
        self.assertIn("if not _showing(window, area, session_id):", body)
        showing = draw[draw.index("def _showing("):draw.index("def _chip(")]
        self.assertIn("return holding(window, area)", showing)   # The label's reading (chip_rect) shares it.
        self.assertIn("live_view_draw.renew(window)", (ADDON / "session_rail_input.py").read_text(encoding="utf-8"))
        self.assertIn("live_view.allow(sorted(live_view_nav.ALLOWED))",
                      (ADDON / "session_rail_input.py").read_text(encoding="utf-8"))

    def test_the_lead_capture_never_reads_the_front_buffer_while_workers_run(self):
        source = (ADDON / "view_editor.py").read_text(encoding="utf-8")
        body = source[source.index("def screenshot("):source.index("def _scale(")]
        self.assertLess(body.index("live_view_draw.capture(window)"), body.index("bpy.ops.screen.screenshot"))
        self.assertNotIn("redraw_timer", (ADDON / "live_view_draw.py").read_text(encoding="utf-8"))

    def test_a_drag_comes_first_and_a_passing_tool_never_opens_a_live_view(self):
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        modal = source[source.index("    def modal("):source.index("    def _live(")]
        self.assertLess(modal.index("if drag.mode:"), modal.index("input_policy.canvas_blocking(window)"))
        self.assertIn("if not (live and region is not None) and input_policy.canvas_blocking(window):", modal)
        # Live only while held: a lapsed lease gives the document back whole (P1-4 M2); renewed before handling.
        self.assertIn("live_view_draw.holding(window, target)", modal)
        self.assertLess(modal.index("live_view_draw.renew(window)"), modal.index("live = ("))
        watch = source[source.index("def _watch("):source.index("def _depsgraph_changed(")]
        self.assertIn("except Exception as error:", watch)

    def test_navigation_keys_belong_to_the_live_view_even_while_the_chat_types(self):
        source = (ADDON / "session_rail_input.py").read_text(encoding="utf-8")
        live = source[source.index("    def _live("):source.index("    def _cursor(")]
        self.assertIn("not input_policy.follows_pointer(event.type)", live)
        self.assertIn("event.type not in MODIFIER_KEYS", live)   # P1-3 L1.
        self.assertIn("_blur_chat(window)", live)                # P1-3 L2.


class ExceptionTests(unittest.TestCase):
    """What the handler hands on is only what it protects (P1-3 M3)."""

    def setUp(self):
        self.view = area(0, 100, 800, 500, pointer=7)
        self.chat = area(800, 100, 400, 500, pointer=8)
        self.window = SimpleNamespace(screen=SimpleNamespace(areas=[self.view, self.chat]), as_pointer=lambda: 3)
        self.card = SimpleNamespace(modal=False, field=False, rect=(500, 300, 200, 100))
        self.fakes = [patch.dict(sys.modules, PACKAGE)] + [patch.object(angelo_agent, name, value, create=True)
                                                           for name, value in (
            ("chat_view", SimpleNamespace(ui_scale=lambda: 1.0)), ("card_host", card_host_with(self.card)),
            ("chat_launcher", SimpleNamespace(_places={}, inside=lambda place, x, y: False)),
            ("intro", SimpleNamespace(is_open=lambda: False, _open={})))]
        for fake in self.fakes:
            fake.start()

    def tearDown(self):
        for fake in self.fakes:
            fake.stop()

    def test_only_sides_shared_with_another_area_are_edges(self):
        edge = session_rail_input._on_edge
        self.assertTrue(edge(self.window, self.view, 797, 300))     # Shared with the chat: Blender resizes there.
        self.assertFalse(edge(self.window, self.view, 2, 300))      # The window's own border: no edge.
        self.assertFalse(edge(self.window, self.view, 400, 597))    # The top: the window's border here.
        self.assertFalse(edge(self.window, self.view, 400, 300))

    def test_a_corner_card_takes_pointer_events_on_itself_and_never_keys(self):
        under = session_rail_input._overlay_under
        self.assertTrue(under(self.window, self.view, event('LEFTMOUSE', 550, 450)))   # On its rectangle.
        self.assertFalse(under(self.window, self.view, event('X', 550, 450)))         # A key over it: never its.
        self.assertFalse(under(self.window, self.view, event('LEFTMOUSE', 50, 150)))   # Beside it.
        self.card.field = True
        self.assertTrue(under(self.window, self.view, event('X', 550, 450)))          # A card with a text field.
        self.card.modal, self.card.field = True, False
        self.assertTrue(under(self.window, self.view, event('X', 50, 150)))           # A modal card: everything.

    def test_card_host_takes_keys_only_for_a_modal_card_or_a_field(self):
        source = (ADDON / "card_host.py").read_text(encoding="utf-8")
        body = source[source.index("def takes_keys("):source.index("def queued(")]
        self.assertIn('state.card.modal or getattr(state.card, "key_event", None)', body)

    def test_the_chat_keeps_typed_keys_over_a_corner_card(self):
        source = (ADDON / "chat_view.py").read_text(encoding="utf-8")
        body = source[source.index("def over_overlay("):source.index("def clear_blocked_notice(")]
        self.assertIn("card_host.takes_keys(pointer)", body)
        self.assertNotIn("shown_on", body)


class WorkerSizeTests(unittest.TestCase):
    """The lead reads a part's real size, measured by its worker (P1-3 M5)."""

    def test_a_size_crosses_the_process_boundary_checked(self):
        base = {"revision": 1, "collections": [], "objects": ["Cube"]}
        self.assertEqual(live_layout.clean(dict(base, size=[1.5, 2, 0.25]))["size"], [1.5, 2.0, 0.25])
        for bad in ([1, 2], [1, 2, -1], [1, 2, float("inf")], [1, 2, float("nan")], ["1", 2, 3], None, "big"):
            self.assertIsNone(live_layout.clean(dict(base, size=bad))["size"], bad)

    def test_world_bounds_cross_the_process_boundary_checked(self):
        """Overlap scope (a), the maintainer 2026-10-25: where each part stands, beside its size."""
        base = {"revision": 1, "collections": [], "objects": ["Cube"]}
        good = [[-1, 0, 0.5], [1, 2.5, 3]]
        self.assertEqual(live_layout.clean(dict(base, bounds=good))["bounds"], [[-1.0, 0.0, 0.5], [1.0, 2.5, 3.0]])
        for bad in ([[1, 0, 0], [0, 0, 0]], [[0, 0], [1, 1]], [[0, 0, 0], [1, 1, float("nan")]],
                    [[0, 0, 0], [1, 1, float("inf")]], [["0", 0, 0], [1, 1, 1]], [[0, 0, 0]], None, "box"):
            self.assertIsNone(live_layout.clean(dict(base, bounds=bad))["bounds"], bad)

    def test_the_worker_measures_size_and_bounds_once_natively(self):
        source = (ADDON / "live_layout.py").read_text(encoding="utf-8")
        body = source[source.index("def measure("):source.index("def clean(")]
        self.assertIn("_bpy._angelo_live.depsgraph_bounds", body)
        self.assertIn("_bpy._angelo_live.depsgraph_size", body)   # A build without bounds: its size alone.
        self.assertNotIn("object_instances", body)   # Never a Python loop per instance on each save (P1-4 M4).
        native = (ADDON.parents[2] / "source/blender/python/intern/bpy_angelo_live_bounds.cc")
        if native.exists():   # One helper rule for both, in the native pass (Boolean operands, wire/bounds, render).
            code = native.read_text(encoding="utf-8")
            self.assertIn("is_helper(ob, dob != nullptr && left_out.contains(dob), operands)", code)
            self.assertIn("render_left_out(data_.dupli_parent, data_.dupli_list, view_layer, known)", code)   # Nested.
            self.assertIn("merge_shown(depsgraph, helpers_alone ? Skip::Nothing : Skip::Undrawn, r_min, r_max)",
                          code[code.index("bool live_depsgraph_bounds("):])   # All helpers: still a size (M3).

    def test_status_states_where_a_part_stands(self):
        with patch.dict(sys.modules, PACKAGE), patch.dict(parallel_sessions.sizes, {"w1": [2.0, 1.0, 1.0]}, clear=True), \
                patch.dict(parallel_sessions.boxes, {"w1": [[-1.00049, 0, 0], [0.99951, 1, 1]]}, clear=True):
            row = worker_sessions._size("w1", {})
            parallel_sessions._note_size("w1", SimpleNamespace(part_layout={"size": [1, 1, 1]}))
            self.assertNotIn("w1", parallel_sessions.boxes)   # A save without bounds forgets the last ones.
        self.assertEqual(row["bounds_m"], [[-1.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
        self.assertLess(len(row["size_note"]), 110)   # Once in the tool's description, not per row (OVERLAP-A L3).
        from angelo_agent import worker_spec
        status = next(tool for tool in worker_spec.WORKER_TOOLS if tool["name"].endswith("status"))
        self.assertIn("helper geometry is left out unless a part has nothing else", status["description"])

    def test_a_save_without_a_size_forgets_the_last_one(self):
        with patch.dict(sys.modules, PACKAGE), patch.dict(parallel_sessions.sizes, {"w1": [1, 1, 1]}, clear=True):
            parallel_sessions._note_size("w1", SimpleNamespace(part_layout={"size": None}))
            self.assertNotIn("w1", parallel_sessions.sizes)

    def test_status_says_when_a_size_is_only_the_cage(self):
        with patch.dict(sys.modules, PACKAGE), patch.dict(parallel_sessions.sizes, {"w1": [1.66, 1.66, 1.66]}, clear=True):
            self.assertEqual(worker_sessions._size("w1", {"size": (2, 2, 2)})["size_m"], [1.66, 1.66, 1.66])
            cage = worker_sessions._size("w2", {"size": (2, 2, 2)})
        self.assertEqual(cage["size_m"], [2, 2, 2])
        self.assertIn("simplified copy", cage["size_note"])


if __name__ == "__main__":
    unittest.main()
