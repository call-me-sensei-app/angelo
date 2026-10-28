# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The table of ``scenario_oneshot_grand_apartment``, part 2: the rooms south of the entrance hall.

Rows and check letters as in ``long_kit_flat_north``.
"""

from long_kit_flat import E, N, P, S, W

M, WR, EN, KR, G, BA, ST = ("Master Bedroom", "Walk-in Wardrobe", "En-suite", "Kids Room", "Guest Bedroom",
                            "Bathroom", "Study")

PIECES = [
    # The master bedroom, with the wardrobe and en-suite opening off its east wall.
    P(M, "Bed", "bed", (-8.6, -1.85), (1.8, 2.1, 1.4),
      "king-size, headboard on the north wall, at {at}, {w} x {d} m, mattress top {top} m, tall "
      "channel-tufted charcoal linen headboard {h} m high, solid oak frame on short tapered legs", S, "charcoal", 0.6, "tbc", col="grey"),
    P(M, "Bedding", "box", (-8.6, -2.1, 0.6), (1.75, 1.5, 0.08), "crisp white linen, four pillows in two sizes, an ochre waffle throw across the foot", S, "linen"),
    P(M, "Round Nightstand", "table3", (-10.0, -1.15), (0.45, 0.45, 0.58),
      "west of the bed at {at}, {w} m across, marble top {h} m high on three brass legs",
      S, "marble", 0.58, "tb"),
    P(M, "Gourd Lamp", "lamp", (-10.0, -1.15, 0.58), (0.3, 0.3, 0.55), "gourd-shaped ox-blood ceramic base, pleated silk shade, on the round nightstand", mat=("oxblood", "shade")),
    P(M, "Alarm Clock", "box", (-9.88, -1.3, 0.58), (0.1, 0.06, 0.1), "small, brass, on the round nightstand", mat="brass"),
    P(M, "Walnut Nightstand", "cab", (-7.15, -1.03), (0.5, 0.42, 0.62),
      "east of the bed at {at}, {w} x {d} x {h} m, two drawers each with a leather tab pull", S, "walnut", 0.62, "tbh", 2),
    P(M, "Reading Lamp", "lamp", (-7.25, -0.95, 0.62), (0.2, 0.2, 0.5), "adjustable brass, hinged arm, on the walnut nightstand", mat=("brass", "brass")),
    P(M, "Novels", "box", (-7.0, -1.05, 0.62), (0.18, 0.24, 0.12), "a stack and a pair of reading glasses on the walnut nightstand", mat="books"),
    P(M, "Dresser", "cab", (-6.1, -6.7), (1.5, 0.5, 0.82),
      "six drawers, cerused oak, on a plinth, on the south wall at {at}, {w} x {d} x {h} m, each drawer "
      "with two antique-brass cup pulls", N, "cerused", 0.82, "tbh", 12),
    P(M, "Jewellery Box", "box", (-6.5, -6.7, 0.82), (0.3, 0.2, 0.12), "walnut with mother-of-pearl inlay, on the dresser", mat="walnut"),
    P(M, "Bud Vase", "cyl", (-5.65, -6.7, 0.82), (0.08, 0.08, 0.25), "glass, one peony, on the dresser", mat="glass"),
    P(M, "Oval Mirror", "wall", (-6.1, -6.95), (0.75, 0.05, 1.0),
      "carved giltwood frame, {w} x {h} m, on the south wall above the dresser at x = {x}, {z} m up", N, "gilt", checks="w", z=1.55),
    P(M, "Slipper Chair", "chair", (-10.15, -5.8), (0.6, 0.62, 0.85),
      "blush-pink velvet, scalloped back {h} m high, walnut legs, south-west corner at {at} facing "
      "north-east, seat {top} m", 45.0, "pink", 0.43, "tzc", col="warm"),
    P(M, "Tripod Lamp", "lamp", (-10.5, -5.0), (0.45, 0.45, 1.6), "floor lamp on three walnut legs, linen drum shade, by the slipper chair", mat=("walnut", "shade")),
    P(M, "Ottoman", "bench", (-8.6, -3.25), (1.4, 0.45, 0.46),
      "upholstered storage ottoman, grey wool, button-tufted hinged lid, short turned feet, at the bed's "
      "foot at {at}, {w} x {d} x {h} m", S, "wool", 0.46, "tbc", col="grey"),
    P(M, "Diamond Rug", "rug", (-8.6, -3.2), (2.4, 3.0, 0.01),
      "pale wool, cream-and-grey diamonds, {w} x {d} m, under the bed's foot at {at}", S, "linen", checks="b"),
    P(M, "Watercolour", "wall", (-8.6, -0.8), (1.2, 0.04, 0.6),
      "landscape of hills, {w} x {h} m, pale oak frame, on the north wall above the headboard at x = {x}, "
      "{z} m up", S, "oak", checks="w", z=1.85),
    P(M, "Writing Desk", "table", (-10.67, -4.0), (1.0, 0.55, 0.74),
      "painted cream, cabriole legs, one drawer with a porcelain knob, under the west window at {at}, {w} x"
      " {d} x {h} m", E, "cream", 0.74, "tbl"),
    P(M, "Bergere Chair", "chair", (-10.0, -4.0), (0.5, 0.52, 0.92),
      "cane-backed, painted ivory, at {at} facing the desk, {w} x {d} x {h} m, seat {top} m", W,
      "linen", 0.46, "tbl"),
    P(M, "Linen Curtains", "curtains", (-8.0, -6.9), (2.6, 0.06, 2.45), "floor-length dusty-blue linen at the south window", N, "paleblue"),
    P(M, "Bird of Paradise", "plant", (-5.4, -1.2), (0.6, 0.6, 1.8), "tall, black ceramic pot, north-east corner", mat=("black", "leaf")),

    # The walk-in wardrobe.
    P(WR, "Oak Frame", "shelf", (-2.85, -2.25), (2.3, 0.6, 2.2),
      "open, along the east wall at {at}, {w} x {d} x {h} m, long hanging rail, hat shelf above, shoe shelf"
      " below", W, "oak", 2.2, "tb"),
    P(WR, "Hanging Clothes", "box", (-2.85, -2.25, 0.9), (2.1, 0.45, 1.0), "shirts, dresses and jackets on wooden hangers, navy, white, camel and burgundy", W, "navy"),
    P(WR, "Tie Rack", "box", (-2.85, -3.25, 1.2), (0.1, 0.3, 0.4), "pull-out, for ties and belts, at the frame's end", W, "walnut"),
    P(WR, "Drawer Chest", "cab", (-3.75, -1.05), (1.0, 0.5, 0.9),
      "eight shallow drawers, white lacquer, velvet-lined glass top, on the north wall at {at}, {w} x {d} x"
      " {h} m, a crystal knob on each drawer", S, "white", 0.9, "tbh", 8),
    P(WR, "Hat Boxes", "cyl", (-3.95, -1.05, 0.9), (0.35, 0.35, 0.36), "three striped, stacked on the drawer chest", mat="cream"),
    P(WR, "Shoe Shelves", "shelf", (-3.55, -3.55), (1.4, 0.3, 1.2), "slanted, along the south wall, ten pairs of shoes", N, "oak"),
    P(WR, "Emerald Pouf", "cyl", (-3.85, -2.35), (0.5, 0.5, 0.42),
      "round, pleated emerald velvet, at {at}, {w} m across, {h} m high", N, "emerald", 0.42, "tbc",
      col="green"),
    P(WR, "Cheval Mirror", "box", (-4.65, -3.2), (0.55, 0.3, 1.7), "full-length, tilting on a mahogany stand", E, "mahogany"),
    P(WR, "Valet Stand", "box", (-4.65, -1.2), (0.45, 0.35, 1.2), "beech, jacket hanger and trouser bar", E, "pine"),

    # The en-suite.
    P(EN, "Roll Top Bath", "bath", (-3.75, -6.52), (1.7, 0.8, 0.62),
      "freestanding, gloss black outside, white inside, four brass claw feet, along the south wall at {at},"
      " {w} x {d} m, rim {h} m high", N, "black_enamel", 0.62, "br"),
    P(EN, "Bath Filler", "box", (-2.75, -6.55), (0.12, 0.12, 1.0), "floor-standing brass, hand shower, at the bath's east end", mat="brass"),
    P(EN, "Walnut Vanity", "cab", (-2.8, -4.6), (1.2, 0.5, 0.85),
      "white quartz top, two drawers each with a leather pull, on the east wall at {at}, {w} x {d} x {h} m", W, "walnut", 0.85, "tbh", 2),
    P(EN, "Vessel Basins", "cyl", (-2.8, -4.3, 0.85), (0.4, 0.4, 0.14), "two round, matte white stone, on the vanity under wall-mounted brass spouts", mat="stone"),
    P(EN, "Backlit Mirror", "wall", (-2.55, -4.6), (0.8, 0.05, 0.8),
      "round, {w} m across, on the east wall above the vanity at y = {y}, {z} m up", W,
      "glass", checks="w", z=1.55),
    P(EN, "Zellige Tiles", "wall", (-2.55, -4.6), (1.4, 0.01, 2.0), "deep green, behind the vanity", W, "zellige", z=1.2),
    P(EN, "Shower Tray", "box", (-4.45, -4.25), (1.0, 0.9, 0.04), "stone, 1 x 0.9 m, north-west corner", mat="stone"),
    P(EN, "Shower Screen", "screen", (-3.94, -4.25), (0.9, 0.02, 2.0),
      "fixed fluted glass, {h} m tall, along the shower's east side", E, "glass", checks="z"),
    P(EN, "Rain Shower", "box", (-4.7, -4.25, 2.0), (0.3, 0.3, 0.1), "brass rainfall head on a wall arm, thermostatic valve", mat="brass"),
    P(EN, "Wall Hung Toilet", "box", (-2.83, -5.7), (0.38, 0.55, 0.4), "white, concealed cistern, brass flush plate, east wall", W, "ceramic"),
    P(EN, "Towel Rail", "wall", (-4.95, -6.45), (0.5, 0.06, 1.0), "heated brushed-brass ladder, west wall", E, "brass", z=1.0),
    P(EN, "Waffle Towels", "box", (-4.88, -6.45, 1.0), (0.08, 0.45, 0.4), "two, folded white, on the rail", mat="linen"),
    P(EN, "Duckboard Mat", "rug", (-3.6, -5.8), (0.8, 0.5, 0.03), "teak, beside the bath",
      mat="teak"),
    P(EN, "Teak Side Table", "cyl", (-4.7, -5.85), (0.3, 0.3, 0.45), "small, turned, beside the bath, a candle and a sponge on it", mat="teak"),
    P(EN, "Window Fern", "plant", (-3.75, -6.86, 1.4), (0.25, 0.25, 0.35), "small, on the sill"),

    # The kids' room.
    P(KR, "House Bed", "housebed", (-2.0, -1.85), (0.9, 1.9, 1.75),
      "single, natural pine, along the west wall at {at}, {w} x {d} m, mattress top {top} m, corner posts "
      "and gable rafters forming a little house roof {h} m high", S, "pine", 0.42, "tb"),
    P(KR, "Rocket Bedding", "box", (-2.0, -2.0, 0.42), (0.85, 1.4, 0.07), "rockets-and-planets duvet, star cushion, knitted bunny, on the bed", mat="paleblue"),
    P(KR, "Plush Toys", "box", (-2.0, -2.65, 0.49), (0.6, 0.25, 0.25), "a bear, an elephant and a fox at the bed's foot", mat="cream"),
    P(KR, "Plywood Desk", "table", (1.17, -2.6), (1.0, 0.55, 0.62),
      "birch plywood, rounded corners, four splayed legs, one drawer with a round painted knob, on the east"
      " wall at {at}, {w} x {d} x {h} m", W, "pine", 0.62, "tbl"),
    P(KR, "Yellow Chair", "chair", (0.6, -2.6), (0.36, 0.38, 0.66),
      "moulded sunflower-yellow plastic on four beech legs, at {at} facing the desk, {w} x {d} x {h} m, "
      "seat {top} m", E, "yellow", 0.36, "tblc", col="warm"),
    P(KR, "Pegboard", "wall", (1.45, -2.6), (0.9, 0.02, 0.6), "painted, above the desk, hooks, scissors and crayon pots", W, "mintpaint", z=1.2),
    P(KR, "Night Light", "cyl", (1.25, -2.25, 0.62), (0.12, 0.12, 0.15), "mushroom-shaped, on the desk",
      mat="cream"),
    P(KR, "Anglepoise Lamp", "lamp", (1.25, -2.95, 0.62), (0.18, 0.18, 0.45), "orange, on the desk",
      mat=("coral", "coral")),
    P(KR, "Toy Chest", "box", (0.8, -6.72), (0.8, 0.45, 0.5),
      "red with white stars, hinged lid, rope end handles, on the south wall at {at}, {w} x {d} x {h} m", N, "red", 0.5, "tbc", col="warm"),
    P(KR, "Alphabet Poster", "wall", (0.8, -6.95), (0.6, 0.03, 0.8),
      "framed, animals, {w} x {h} m, on the south wall above the chest at x = {x}, {z} m up", N, "white", checks="w", z=1.35),
    P(KR, "Ledge Bookshelf", "shelf", (-2.3, -4.5), (1.0, 0.3, 0.9),
      "low, mint green, three tilted ledges, on the west wall at {at}, {w} x {d} x {h} m", E, "mintpaint", 0.9, "tbc", col="green"),
    P(KR, "Picture Books", "books", (-2.3, -4.5), (1.0, 0.3, 0.9), "face out on the ledges", E, "books"),
    P(KR, "Cloud Shelf", "wall", (-2.45, -4.5), (0.6, 0.15, 0.3), "cloud-shaped, tiny wooden figurines, above the bookshelf", E, "white", z=1.45),
    P(KR, "Teepee", "teepee", (0.55, -5.1), (1.2, 1.2, 1.6),
      "canvas on five wooden poles, bunting over its door, at {at}, {w} m across, {h} m tall", N, "canvas", checks="b"),
    P(KR, "Road Rug", "cyl", (-0.45, -4.3), (1.6, 1.6, 0.01),
      "round, printed road map, {w} m across, at {at}", N, "jute", checks="b"),
    P(KR, "Toy Train", "box", (-0.45, -4.3, 0.01), (0.9, 0.5, 0.08), "wooden, five carriages, figure-eight track on the rug", mat="red"),
    P(KR, "Rocking Horse", "box", (-1.85, -6.35), (0.3, 1.0, 0.85), "dapple grey, bow rockers, red leather saddle", E, "grey"),
    P(KR, "Bean Bag", "cyl", (-1.1, -5.7), (0.8, 0.8, 0.6), "teal corduroy", mat="seagreen"),
    P(KR, "Star Wardrobe", "cab", (0.8, -1.05), (0.9, 0.5, 1.5),
      "two doors, pale blue, a wooden star knob on each, on the north wall at {at}, {w} x {d} x {h} m", S, "paleblue", 1.5, "tbhc", 2, "blue"),
    P(KR, "Growth Chart", "wall", (0.15, -0.8), (0.15, 0.02, 1.5), "painted wooden ruler by the door", S, "pine", z=0.95),
    P(KR, "Balloon Curtains", "curtains", (-0.5, -6.9), (2.0, 0.06, 2.3), "hot-air-balloon print, at the window", N, "paleblue"),

    # The guest bedroom.
    P(G, "Iron Bed", "bed", (2.55, -4.0), (1.4, 2.0, 1.2),
      "double, black wrought iron, scrolled head and foot rails, head on the west wall, at {at}, {w} x {d} "
      "m, mattress top {top} m, head rail {h} m high", E, "black", 0.55,
      "tbc", col="grey"),
    P(G, "Patchwork Quilt", "box", (2.75, -4.0, 0.55), (1.35, 1.6, 0.06), "faded blues and creams, two pillows, on the bed", E, "paleblue"),
    P(G, "Towel Stack", "box", (3.3, -4.0, 0.61), (0.3, 0.25, 0.15), "fresh towels tied with ribbon, on the bed", mat="linen"),
    P(G, "Coral Bedside Table", "table", (1.8, -2.95), (0.4, 0.4, 0.6),
      "bamboo-effect, painted coral, four legs, lower shelf, at {at}, {w} m square, {h} m high", E, "coral", 0.6, "tbc", col="warm"),
    P(G, "Opal Lamp", "cyl", (1.8, -2.95, 0.6), (0.2, 0.2, 0.25), "little opal-glass mushroom, on the bedside table", mat="ceramic"),
    P(G, "Campaign Chest", "cab", (4.72, -4.0), (0.9, 0.45, 0.84),
      "teak, brass corner brackets, four drawers each with two brass bail handles, on the east wall at "
      "{at}, {w} x {d} x {h} m", W, "teak", 0.84, "tbh", 8),
    P(G, "Luggage Rack", "table", (4.4, -6.4), (0.65, 0.45, 0.55), "folding beech, webbing straps, a tan leather suitcase on it", N, "pine"),
    P(G, "Peacock Chair", "peacock", (4.35, -1.5), (0.9, 0.8, 1.3),
      "rattan, fan back {h} m high, floral cushion, north-east corner at {at} facing south-west, seat {top}"
      " m", -135.0, "rattan", 0.42, "tz"),
    P(G, "Floor Mirror", "box", (1.95, -6.8), (0.6, 0.06, 1.8), "tall, whitewashed frame, leaning on the south wall", N, "white"),
    P(G, "Coast Map", "wall", (1.55, -4.0), (0.8, 0.04, 0.6),
      "vintage, {w} x {h} m, black frame, on the west wall above the bed at y = {y}, {z} m up", E, "black", checks="w", z=1.6),
    P(G, "Jute Rug", "rug", (3.3, -5.4), (1.6, 1.0, 0.01), "beside the bed", mat="jute"),
    P(G, "Peg Rail", "wall", (2.15, -0.8), (0.8, 0.08, 0.1), "Shaker, five pegs, a cotton robe on it, north wall", S, "oak", z=1.65),
    P(G, "Scalloped Curtains", "curtains", (3.25, -6.9), (2.0, 0.06, 2.3), "white cotton, scalloped hem, at the window", N, "linen"),
    P(G, "Snake Plant", "plant", (4.6, -6.55), (0.35, 0.35, 0.9), "white ceramic pot",
      mat=("ceramic", "leaf")),

    # The bathroom.
    P(BA, "Built In Bath", "tub", (6.05, -6.57), (1.7, 0.75, 0.55),
      "sea-green tiled panel front, along the south wall at {at}, {w} x {d} m, rim {h} m high", N, "seagreen", 0.55, "brc", col="green"),
    P(BA, "Bath Screen", "screen", (6.85, -6.2), (0.8, 0.02, 1.4), "hinged glass on the bath rim, chrome shower riser", N, "glass"),
    P(BA, "Bath Toys", "box", (5.4, -6.3, 0.55), (0.25, 0.12, 0.12), "a net of toys and a yellow rubber duck on the rim", mat="yellow"),
    P(BA, "Pedestal Basin", "basin", (7.2, -3.6), (0.6, 0.48, 0.85),
      "white porcelain, on the east wall at {at}, {w} m wide, rim {h} m high", W,
      "ceramic", 0.85, "r"),
    P(BA, "Pillar Taps", "box", (7.4, -3.6, 0.85), (0.04, 0.3, 0.15), "two chrome, porcelain hot and cold caps", mat="chrome"),
    P(BA, "Toiletries", "box", (7.38, -3.35, 0.85), (0.08, 0.12, 0.15), "soap dish, toothbrush mug, glass lotion bottles, beside the taps", mat="glass"),
    P(BA, "Mirror Cabinet", "wall", (7.45, -3.6), (0.6, 0.14, 0.7),
      "mirror-fronted, chrome knob, {w} x {h} m, on the east wall above the basin at y = {y}, {z} m up", W, "glass", checks="w", z=1.55),
    P(BA, "Close Coupled Toilet", "box", (7.15, -5.2), (0.4, 0.62, 0.8), "white, oak seat", W, "ceramic"),
    P(BA, "Column Radiator", "wall", (5.05, -3.0), (0.8, 0.12, 0.6), "white, towel bar, west wall", E, "white", z=0.45),
    P(BA, "Seagrass Hamper", "cyl", (5.35, -5.4), (0.4, 0.4, 0.6), "lidded laundry hamper",
      mat="rattan"),
    P(BA, "Striped Bath Mat", "rug", (6.0, -5.85), (0.8, 0.5, 0.01), "striped cotton, beside the bath",
      mat="paleblue"),
    P(BA, "Bamboo Shelf", "box", (5.25, -1.6), (0.4, 0.35, 1.6), "narrow, baskets and rolled towels", E, "pine"),

    # The study.
    P(ST, "Pedestal Desk", "desk", (9.25, -5.3), (1.6, 0.8, 0.76),
      "mahogany, embossed green leather top, two pedestals of three drawers facing north each with a brass "
      "swan-neck handle, at {at}, {w} x {d} x {h} m", N,
      ("mahogany", "deepgreen"), 0.76, "tbhc", 6, "green"),
    P(ST, "Typewriter", "box", (9.7, -5.4, 0.76), (0.35, 0.3, 0.15), "black, vintage, on the desk",
      mat="black"),
    P(ST, "Desk Set", "box", (9.25, -5.2, 0.76), (0.5, 0.35, 0.03), "leather blotter, inkstand with fountain pens, letters, on the desk", mat="brown_leather"),
    P(ST, "Bankers Lamp", "lamp", (8.75, -5.5, 0.76), (0.25, 0.25, 0.4), "green glass, on the desk",
      mat=("brass", "deepgreen")),
    P(ST, "Captains Chair", "swivel", (9.25, -4.35), (0.6, 0.6, 0.85),
      "swivel, oxblood leather, brass nailheads, five-star walnut base, at {at} facing the desk, seat {top}"
      " m", S, "oxblood", 0.48, "tc", col="warm"),
    P(ST, "Library Shelves", "shelf", (7.725, -3.9), (3.8, 0.35, 2.4),
      "built-in, deep green, cornice, seven shelves, along the west wall at {at}, {w} x {d} x {h} m", E, "deepgreen", 2.4, "tbc", col="green"),
    P(ST, "Library Books", "books", (7.725, -3.9), (3.8, 0.35, 2.4), "hundreds: leather-bound sets on top, paperbacks below, curiosities between", E, "books"),
    P(ST, "Library Ladder", "box", (8.0, -3.0), (0.5, 0.12, 2.3), "rolling oak, hooked to a brass rail across the shelves", E, "oak"),
    P(ST, "Club Chair", "arm", (10.3, -1.5), (0.9, 0.9, 0.78),
      "Chesterfield, worn brown leather, deep buttoning, rolled arms, bun feet, north-east corner at {at} "
      "facing south-west, seat {top} m", -135.0, "brown_leather", 0.44, "tc", col="warm"),
    P(ST, "Leather Footrest", "box", (9.6, -2.1), (0.45, 0.4, 0.38), "small, matching the club chair",
      mat="brown_leather"),
    P(ST, "Brass Floor Lamp", "lamp", (10.65, -2.5), (0.35, 0.35, 1.7), "tall, tilting shade, by the club chair", mat=("brass", "shade")),
    P(ST, "Floor Globe", "cyl", (8.1, -6.45), (0.55, 0.55, 1.0), "antique, turned wooden stand, brass meridian", mat="walnut"),
    P(ST, "Filing Cabinet", "cab", (10.7, -6.5), (0.45, 0.5, 0.72),
      "two drawers, oak, a brass card-holder handle on each, south-east corner at {at}, {h} m high", W, "oak", 0.72, "th", 2),
    P(ST, "Drinks Tray", "box", (10.7, -6.5, 0.72), (0.35, 0.3, 0.25), "silver, crystal decanter and two tumblers, on the filing cabinet", mat="chrome"),
    P(ST, "Regulator Clock", "wall", (10.2, -0.8), (0.35, 0.12, 0.9),
      "mahogany case, {w} x {h} m, on the north wall at x = {x}, {z} m up", S, "mahogany", checks="w", z=1.7),
    P(ST, "Spaniel Portrait", "wall", (10.95, -5.4), (0.7, 0.06, 0.9),
      "oil, heavy gilt frame, {w} x {h} m, on the east wall at y = {y}, {z} m up", W, "gilt", checks="w", z=1.55),
    P(ST, "Turkish Rug", "rug", (9.25, -4.8), (2.4, 1.8, 0.01),
      "faded, {w} x {d} m, under the desk and chair at {at}", N, "rug", checks="b"),
    P(ST, "Weeping Fig", "plant", (7.95, -1.35), (0.5, 0.5, 1.7), "brass planter"),
    P(ST, "Tweed Curtains", "curtains", (9.25, -6.9), (2.6, 0.06, 2.45), "heavy tweed at the south window",
      N, "wool"),
    P(ST, "Wastepaper Basket", "cyl", (9.95, -5.9), (0.28, 0.28, 0.35), "leather, beside the desk", mat="brown_leather"),
]
