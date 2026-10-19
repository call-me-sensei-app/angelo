# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Mini one-shot test ``oneshot-mini-footbridge-arched``: one everyday request on the factory scene (``oneshot_minis``).

The message names no object, so the checks find what was built by shape: its
plan, the deck's top surface, the posts and the rails as separate pieces, and
the material on all of it. The factory scene is kept: the default cube may
stay or go, and neither is checked.
"""

import oneshot_minis as minis
from oneshot_minis import CAMERAS, FINAL_STILL, FINAL_VIEW, FRAME, GROUND_TRUTH, LIMITS, SUBJECT  # noqa: F401

ID = "oneshot_mini_footbridge_arched"
TITLE = "Slightly arched stone footbridge at the origin"
RISE = 0.3   # The ground truth's arch: mid-span above the deck's ends, in metres.
PROMPT = "Add a slightly arched stone footbridge at the origin: " + minis.FOOTBRIDGE_REQUEST
CHECKS = minis.footbridge_checks((0.08, 0.8), 1.0)
TURNS = minis.turn(TITLE, PROMPT, CHECKS)


def build_turn(bpy, turn_id):
    if turn_id != "brief":
        raise KeyError(turn_id)
    minis.footbridge(bpy, RISE)


def build(bpy, upto=None):
    build_turn(bpy, "brief")
