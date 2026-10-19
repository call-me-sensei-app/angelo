# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-footbridge-conflict``: the footbridge with its original brief, whose naming rules collide.

The footbridge brief was first written with two naming rules no railing part
can meet together: "every part's name starts with 'Footbridge'" for the
bridge and "every railing part's name starts with 'Railing'" for its
railings. ``oneshot-footbridge`` now excepts the railings from the first
rule. This test keeps the original text, unchanged. Its railing checks look
up railing parts named either way, 'Railing ...' or 'Footbridge Railing
...': the brief asks for both and no name meets both, so it does not say
which rule wins (base Angelo chose 'Railing ...'). It is a regression check
on Angelo against its own earlier behaviour, not a headline comparison with
the MCP side.

Everything except the brief and that railing target is the footbridge's: its
ground truth, cameras, the rest of its checks, limits and still, and its
reference folder (``scenario_specs.SHARED_REFERENCES``), so both tests are
given the same image bytes.
"""

import scenario_oneshot_footbridge as footbridge
from scenario_oneshot_footbridge import (  # noqa: F401  The footbridge's scene and limits, unchanged.
    APPROVABLE, CAMERAS, FINAL_STILL, FINAL_VIEW, FRAME, GROUND_TRUTH, JOB_WAIT_S, LIMITS, SUBJECT, build, build_turn)

ID = "oneshot_footbridge_conflict"
TITLE = footbridge.TITLE + " (original brief, colliding naming rules)"

# The footbridge's brief as first written. This statement is a byte-for-byte copy of the one in
# scenario_oneshot_footbridge.py before its railing exception: put back in place of that module's brief, it gives
# the digest of the reference folder the footbridge's billed runs were given (test_native_oneshot_footbridge_naming).
PROMPT = (
    "A timber footbridge over a stream, late on a summer afternoon. Build it and render one still. The four "
    "attached pictures show the scene I have in mind: the hero shot, a front elevation, a plan from above "
    "(+Y up) and a close-up of the railing. Take the proportions and the mood from them and design the details "
    "yourself; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The stream runs along Y through the origin and the bridge crosses it along X. "
    "I find things by name, so name them exactly as written here and give anything else you add a meaningful "
    "name of its own. Keep the file tidy: nothing from the default scene (its cube, light or camera) remains, "
    "no unused leftovers, and a material on everything that shows.\n\n"
    "The ground. One mesh named 'Ground', about 48 m along X by 40 m along Y: grassy banks at height 0, a "
    "little uneven, cut by a stream channel about 4 m wide whose bed lies about 0.9 m below the banks. Grass on "
    "the banks, earth or gravel in the channel.\n\n"
    "The water. A surface named 'Stream' that fills the channel along its whole length, about 0.45 m below the "
    "banks: dark, clear and glossy (roughness 0.15 or less).\n\n"
    "The footbridge. Weathered timber in a warm brown, rough to the touch; every part's name starts with "
    "'Footbridge'. It is 6 m long (x = -3 to 3) and 1.4 m wide. Its walking surface, 'Footbridge Deck', is "
    "made of real cross planks with small gaps between them, and its top is 0.3 m above the banks "
    "(z = 0.3 m). Carry the deck on beams that rest on the banks and clear the water; how you frame it is up "
    "to you.\n\n"
    "The railings. A handrail on each side with its top 1 m above the deck (z = 1.3 m), on posts roughly every "
    "1.5 m (at least four a side, each post its own piece), with a lower rail or balusters as you see fit. "
    "Every railing part's name starts with 'Railing'. The walkway between the railings stays clear.\n\n"
    "Dressing. At least five rounded stones in and beside the stream, their names starting with 'Stone', each "
    "0.3 to 1 m across. Reeds, grass tufts or anything else that helps the picture is up to you.\n\n"
    "Light. A sun light named 'Sun', 20 to 30 degrees above the horizon and clearly warm in colour, raking "
    "across the bridge so the railings throw shadows on the deck, under a clear sky to match.\n\n"
    "Camera. A camera named 'Hero Camera' at (4.2, -5.6, 3.6), looking at (0, 0, 0.3) with a 35 mm lens, as the "
    "scene's camera. That is the hero shot in the first picture.\n\n"
    "Output. EEVEE with ray tracing on, 2560 x 1440 at 100 %. Render the still from 'Hero Camera' and save it "
    "as a PNG named 'footbridge-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your render next to the pictures and fix what is wrong.")

# Railing parts named by either of the brief's two colliding rules.
RAILING = {"any": [footbridge.RAILING, {"prefix": "Footbridge Railing"}]}
CHECKS = footbridge.checks(RAILING)
TURNS = [dict(footbridge.TURNS[0], prompt=PROMPT, checks=CHECKS)]
