# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Geometry invariants for every scissored transcript/popover action."""

import ast
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent.chat_hit_testing import clip_rect, clipped_hits, contains, release_active


class ChatHitTestingTests(unittest.TestCase):
    def test_hidden_and_zero_area_hits_are_not_registered(self):
        viewport = (0, 140, 593, 760)
        actions = ('accept_native_preview', 'discard_native_preview', 'expand', 'stop_native_job',
            'approve', 'decline', 'review', 'restore', 'copy', 'url')
        hits = [((210, 72, 172, 28), action, 'exact') for action in actions]
        self.assertEqual(clipped_hits(hits, viewport), [])
        for rect in ((0, 140, 0, 28), (0, 140, 20, 0), (0, 112, 20, 28)):
            self.assertIsNone(clip_rect(rect, viewport))

    def test_partial_controls_only_activate_their_visible_pixels(self):
        viewport = (0, 140, 593, 760)
        hits = clipped_hits([((-5, 132, 100, 28), 'approve', 'call')], viewport)
        self.assertEqual(hits, [((0, 140, 95, 20), 'approve', 'call')])
        rect = hits[0][0]
        self.assertFalse(contains(rect, 50, 139))
        self.assertTrue(contains(rect, 50, 140))
        self.assertFalse(contains(rect, 95, 150))
        self.assertFalse(contains(rect, 50, 160))

    def test_all_four_edges_and_composer_resize_change_the_effective_hit_area(self):
        self.assertEqual(clip_rect((-10, -10, 120, 120), (0, 0, 100, 100)), (0, 0, 100, 100))
        hits = [((12, 130, 120, 35), 'restore', 'point'), ((12, 870, 120, 35), 'url', 'https://example.invalid')]
        self.assertEqual(clipped_hits(hits, (0, 160, 100, 720)),
            [((12, 160, 88, 5), 'restore', 'point'), ((12, 870, 88, 10), 'url', 'https://example.invalid')])
        self.assertEqual(clipped_hits(hits, (0, 200, 100, 0)), [])

    def test_release_revalidates_exact_control_identity_and_current_geometry(self):
        pressed = ((20, 150, 80, 28), 'discard_native_preview', ('chat', 'stage'))
        self.assertTrue(release_active(pressed, [pressed], 40, 160))
        self.assertFalse(release_active(pressed, [], 40, 160))
        self.assertFalse(release_active(pressed, [(pressed[0], pressed[1], ('chat', 'other-stage'))], 40, 160))
        self.assertFalse(release_active(pressed, [((20, 170, 80, 8), pressed[1], pressed[2])], 40, 160))
        self.assertFalse(release_active(pressed, [pressed], 40, 178))

    def test_native_source_clips_transcript_popup_and_hover_before_input(self):
        root = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent'
        source = (root / 'chat_view.py').read_text()
        self.assertIn('state["hits"] = clipped_hits(state["hits"], message_viewport)', source)
        self.assertIn('clip_rect(rect, message_viewport)', source)
        # The popover clips a hit to its scissor, read back in layout coordinates (mirrored in Arabic: canvas_rtl).
        # The canvas lays out unscaled (chat_view.ui_scale), so the window-pixel scissor is scaled back first.
        self.assertIn('clip_rect(rect, canvas_rtl.scissor(*(value / _scale for value in gpu.state.scissor_get())))', source)
        module = ast.parse(source)
        modal = next(node for node in ast.walk(module) if isinstance(node, ast.FunctionDef) and node.name == 'modal')
        text = ast.unparse(modal)
        self.assertLess(text.index("state.get('composer'"), text.index("for rect, action, value in hits"))
        self.assertIn('release_active((rect, action, value)', text)
        self.assertIn("state.get('hit_region') == (region.width, region.height)", text)


if __name__ == '__main__':
    unittest.main()
