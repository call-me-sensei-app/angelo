# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the rig check puts on screen before anyone uses it: its + menu row's words and glyph, and the card's kind.
No Blender dependency, and nothing else imported, so Angelo's start-up and the + menu load only this; the card
(``skeleton_report_card``) and the checks load on first use (doc/angelo/engine_skeleton.rst, "Cost").
"""

from .ui_text import n_

KIND = "skeleton_check"
GLYPH = "skeleton"
MENU_TITLE = n_("Check a rig for a game engine")
MENU_WHY = n_("Does this armature fit the humanoid skeleton of Unreal, Unity or Godot?")
LAST_TARGET = ["UNREAL"]   # the engine the person chose last, for the next card


def draw_glyph(view, x, y, size, color):
    """A figure of joints and bones: the + menu tile's and the card's mark (y up)."""
    s = size / 32
    cx = x + 16 * s
    joints = {"head": (cx, y + 25 * s), "neck": (cx, y + 20 * s), "hips": (cx, y + 12 * s),
              "hand_l": (cx - 9 * s, y + 13 * s), "hand_r": (cx + 9 * s, y + 13 * s),
              "foot_l": (cx - 6 * s, y + 4 * s), "foot_r": (cx + 6 * s, y + 4 * s)}
    for a, b in (("neck", "hips"), ("neck", "hand_l"), ("neck", "hand_r"), ("hips", "foot_l"), ("hips", "foot_r")):
        view.stroke(*joints[a], *joints[b], color, 1.6 * s)
    hx, hy = joints["head"]
    view.shape(hx - 3.5 * s, hy - 3.5 * s, 7 * s, 7 * s, color, 3.5 * s)
    for name in ("neck", "hips", "hand_l", "hand_r", "foot_l", "foot_r"):
        jx, jy = joints[name]
        view.shape(jx - 1.8 * s, jy - 1.8 * s, 3.6 * s, 3.6 * s, color, 1.8 * s)
