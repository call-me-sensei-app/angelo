# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""One-shot test ``oneshot-bridge-conflict``: the bridge with its original brief, whose end-post rule is unclear.

The bridge brief was first written with railing posts "every 1.5 m from
x = -12 to 12 m" on a deck that ends at x = -12 and 12 m, so posts 0.1 m
thick centred at the ends reach 5 cm past the deck, and an agent that keeps
them on the deck moves them inwards. The checks probe the end post at exactly
x = 12, which only meets the edge of a post moved to 11.95 and misses.
``oneshot-bridge`` now states that the end posts are centred on the deck's
ends. This test keeps the original text, unchanged, and checks that an agent
still reads it as the ground truth does: end posts centred at x = -12 and
12 m (agents have moved them inwards). It is a regression check on Angelo
against its own earlier behaviour, not a headline comparison with the MCP side.

Everything except the brief is the bridge's: its ground truth, cameras,
checks, limits and still, and its reference folder
(``scenario_specs.SHARED_REFERENCES``), so both tests are given the same
image bytes.
"""

import scenario_oneshot_bridge as bridge
from scenario_oneshot_bridge import (  # noqa: F401  The bridge's scene, checks and limits, unchanged.
    APPROVABLE, CAMERAS, FINAL_STILL, FINAL_VIEW, FRAME, GROUND_TRUTH, HYGIENE, JOB_WAIT_S, LIMITS, SUBJECT, build,
    build_turn)

ID = "oneshot_bridge_conflict"
TITLE = bridge.TITLE + " (original brief, unclear end posts)"

# The bridge's brief as first written. This statement is a byte-for-byte copy of the one in
# scenario_oneshot_bridge.py before its end-post statement: put back in place of that module's brief, it gives the
# digest of the reference folder the bridge's billed runs were given (test_native_oneshot_bridge_endpost).
PROMPT = (
    "Build the scene in the four attached pictures and render one still from it. The pictures show the finished "
    "scene: an orthographic front view looking along +Y, a view from upstream, an orthographic top view with +Y "
    "up, and a hero shot. It is a stone arch bridge over a river valley at golden hour, with a small red car "
    "crossing. Match the pictures as closely as you can; where they and this brief disagree, the brief wins.\n\n"
    "Conventions. Metres, Z up. The river runs along Y and the bridge spans X, centred on the origin. I find "
    "things by name, so name them exactly as written here and give anything else you add a meaningful name of "
    "its own. Keep the file tidy: nothing from the default scene (its cube, light or camera) remains, no unused "
    "leftovers, and a material on everything that shows.\n\n"
    "The valley. One solid mesh named 'Valley', like a diorama block: 120 m along X by 80 m along Y, with flat "
    "earth sides going down to a flat base at z = -8 m. Its top has a flat riverbed at z = -4 m where "
    "|x| <= 5 m, smooth earthy slopes, and grassy banks at height 0 from |x| = 9 m that roll up into low hills "
    "(to about 5 m) towards the outer edges. A grass-and-earth material.\n\n"
    "The bridge. Warm grey stone; every part's name starts with 'Bridge'. 'Bridge Deck' is a slab 24 m long "
    "(x = -12 to 12), 5 m wide and 0.6 m thick, its top at z = 1 m. 'Bridge Arch' is a solid half-elliptical "
    "ring under it, 4.4 m wide in Y, springing from z = -2.5 m: its outer curve spans x = -8 to 8 m and rises to "
    "the deck's underside (z = 0.4 m), its opening spans x = -7.2 to 7.2 m and rises to z = -0.4 m. "
    "'Bridge Piers' are thin stone walls (0.5 m thick in X, 4.4 m in Y) standing on the arch and reaching the "
    "deck at x = +/-4, +/-5.5 and +/-7 m. 'Bridge Abutments' are stone blocks under both deck ends, from "
    "|x| = 9 to 12 m and from z = -1 m up to the deck.\n\n"
    "On the deck. Dark iron railings along both long edges, their names starting with 'Railing': square posts "
    "0.1 m thick and 1 m tall at y = +/-2.4 m, every 1.5 m from x = -12 to 12 m (17 a side), with a top rail "
    "1 m above the deck and a middle rail 0.5 m above it, both running the full 24 m. Street lamps, their names "
    "starting with 'Lamp': slim posts 0.16 m across rising from the deck to z = 4.4 m, each topped by a glowing "
    "sphere lantern 0.4 m across, at y = +/-2.05 m and x = -9, -4.5, 0, 4.5 and 9 m (ten lamps). The lanterns "
    "glow warm orange (emission, strength about 4).\n\n"
    "The river. A flat water surface named 'River' at z = -3 m, 15 m wide in X (centred) and 40 m long in Y, "
    "dark teal and glossy (roughness 0.1 or less). Sixteen rocks, their names starting with 'Rock', along the "
    "two water edges, eight a side: 4.5 to 8 m from the river's centre line, spread from y = -18 to 18 m, each "
    "0.7 to 1.6 m across and partly sunk into the bank.\n\n"
    "Light. Golden hour. A sun light named 'Sun', about 5 degrees above the horizon, shining from the +X, -Y "
    "side (azimuth about -35 degrees, measured from +X towards +Y), warm orange, strength around 7, so that it "
    "lights the bridge's downstream face and throws long shadows. A matching hazy sky (a physical sky or a warm "
    "sky colour), so the scene glows as in the hero picture.\n\n"
    "The car. A small red car, its parts named starting with 'Car' (body, cabin and four wheels that touch the "
    "deck), about 4 m long, 1.8 m wide and 1.7 m tall. It drives along +X in the lane at y = -1 m, keyframed "
    "from x = -10.5 m at frame 1 to x = 10.5 m at frame 120, so it is mid-bridge around frame 60. The scene's "
    "frame range is 1 to 120.\n\n"
    "Camera and output. A camera named 'Deck Camera' stands on the deck at (-11, 1.4, 2.6), looks along the "
    "bridge towards (11, -0.6, 1.6) with a 24 mm lens, and is the scene's camera. Render settings: EEVEE, "
    "1920 x 1080 at 100 %, 24 fps, 128 render samples, ray tracing and motion blur on, the AgX view transform. "
    "Render frame 60 from 'Deck Camera' with those settings and save the still as a PNG named "
    "'bridge-final.png'; tell me where it is.\n\n"
    "Before you call it done, look at your result next to the four pictures and fix what does not match.")

TURNS = [dict(bridge.TURNS[0], prompt=PROMPT)]
