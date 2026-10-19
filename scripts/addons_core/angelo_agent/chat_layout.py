# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Codex-reference geometry in logical pixels, independent of Blender rendering."""


def composer_layout(width, draft_rows=1, attachment_rows=0):
    height = 98 + max(0, min(8, draft_rows) - 1) * 22 + attachment_rows * 50
    return {"rect": (15, 20, width - 30, height), "radius": 22,
            "input_x": 28, "toolbar_y": 42, "plus_x": 36,
            "approval_x": 66, "mic_x": width - 72, "send_x": width - 36,
            "bubble_max_width": (width - 32) * 0.7, "message_left": 16, "message_right": 16}
