# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The table of ``scenario_oneshot_grand_apartment``, part 1: the entrance hall, the north rooms and the balcony.

Every row is one distinct piece (``long_kit_flat.P``): its name, placeholder shape, place, size, brief phrase and
what the checks measure. Check letters: t a ray down onto its seat or top; b its footprint and overall height;
l open underneath between its legs (a level ray under its seat or top meets nothing of it); h its knobs or pulls standing proud of its front (its box reaches past the
stated depth); w a frame or mirror standing on its wall; r a hollow basin below its rim; p a door standing proud of its front;
z its overall height; c its colour family (col). Positions (x, y) are floor coordinates of its centre.
"""

from long_kit_flat import E, N, P, S, W

H, L, B, D, K, PA, LA = ("Entrance Hall", "Living Room", "Balcony", "Dining Room", "Kitchen", "Pantry",
                         "Laundry Room")

PIECES = [
    # The entrance hall: the long hall from the front door at its east end.
    P(H, "Console Table", "table", (10.0, 0.54), (1.1, 0.32, 0.82),
      "walnut, on the north wall at {at}, {w} x {d} x {h} m, four slender tapered legs, a shallow drawer "
      "with a brass ring pull", S, "walnut", 0.82, "tbl"),
    P(H, "Round Mirror", "wall", (10.0, 0.7), (0.7, 0.04, 0.7),
      "{w} m across, hammered brass frame, on the north wall above it at x = {x}, {z} m up",
      S, "brass", checks="w", z=1.55),
    P(H, "Key Bowl", "cyl", (10.25, 0.56, 0.82), (0.2, 0.2, 0.06), "small turned olive-wood bowl for keys on the console", mat="oak"),
    P(H, "Shoe Cabinet", "cab", (9.8, -0.54), (1.0, 0.32, 0.95),
      "slim, white lacquer, on the south wall at {at}, {w} x {d} x {h} m, two tilt-out doors each with a "
      "long brushed-steel bar pull", N, "white", 0.95, "tbh", 2),
    P(H, "Rush Bench", "bench", (4.4, -0.51), (1.2, 0.38, 0.46),
      "oak with a woven rush seat, on the south wall at {at}, {w} x {d} m, seat {h} m, four turned legs "
      "joined by stretchers, three loose cushions in indigo ticking", N, "oak", 0.46,
      "tbl"),
    P(H, "Coat Stand", "lamp", (10.55, -0.3), (0.3, 0.3, 1.85),
      "bentwood, black-stained beech, by the front door, {h} m tall, three curved feet, eight curled hooks", S, ("black", "black"), checks="z"),
    P(H, "Coats", "box", (10.55, -0.22, 1.0), (0.4, 0.2, 0.75), "a camel overcoat, a yellow raincoat and a striped scarf on the coat stand", mat="mustard"),
    P(H, "Umbrella Stand", "cyl", (10.75, 0.5), (0.25, 0.25, 0.5), "ribbed sage-green ceramic by the door, a black and a tartan umbrella in it", mat="sage"),
    P(H, "Runner", "rug", (-0.5, 0.0), (16.0, 0.8, 0.01), "flat-woven, rust and indigo stripes, down the hall", mat="rug"),
    P(H, "Doormat", "rug", (10.5, 0.0), (0.6, 0.9, 0.015), "coir, inside the front door", mat="jute"),
    P(H, "Harbour Photograph", "wall", (-2.8, 0.7), (1.0, 0.04, 0.7),
      "black-and-white harbour print, {w} x {h} m, thin black frame and white mount, on the north wall at x"
      " = {x}, {z} m up", S, "black", checks="w", z=1.55),
    P(H, "Fern Print", "wall", (-3.8, -0.7), (0.5, 0.04, 0.7),
      "botanical ferns, {w} x {h} m, gilt frame, on the south wall at x = {x}, {z} m up",
      N, "gilt", checks="w", z=1.5),
    P(H, "Brass Sconce", "wall", (-6.0, 0.7), (0.15, 0.18, 0.3), "brass, fluted glass shade, on the north wall by the living-room doorway", S, "brass", z=1.9),
    P(H, "Swing Arm Sconce", "wall", (1.5, -0.7), (0.12, 0.4, 0.25), "matte black, linen drum shade, on the south wall", N, "black", z=1.75),
    P(H, "Fiddle Leaf Fig", "plant", (-10.5, 0.3), (0.45, 0.45, 1.7), "tall, in a seagrass basket at the hall's west end"),

    # The living room.
    P(L, "Fireplace", "fireplace", (-4.23, 4.0), (1.5, 0.36, 1.15),
      "carved white marble surround on the east wall at {at}, {w} x {d} m, {h} m to its mantel shelf, "
      "fluted pilasters, arched cast-iron insert, brass fender", W, "marble",
      1.15, "tb"),
    P(L, "Carriage Clock", "box", (-4.2, 3.6, 1.15), (0.12, 0.08, 0.16), "small, brass, on the mantel", W, "brass"),
    P(L, "Pillar Candles", "cyl", (-4.2, 4.45, 1.15), (0.2, 0.2, 0.25), "three ivory candles of different heights on a brass tray on the mantel", mat="linen"),
    P(L, "Overmantel Mirror", "wall", (-4.05, 4.0), (0.9, 0.05, 1.1),
      "tall, arched, gilded frame, {w} x {h} m, on the east wall above the fireplace at y = {y}, {z} m up", W, "gilt", checks="w", z=1.9),
    P(L, "Chesterfield Sofa", "sofa", (-8.3, 4.0), (2.4, 0.95, 0.8),
      "deep-buttoned bottle-green velvet facing the fireplace, at {at}, {w} x {d} x {h} m, seat {top} m, "
      "rolled arms, three seat cushions, turned walnut bun feet",
      E, "green", 0.44, "tbc", col="green"),
    P(L, "Scatter Cushions", "box", (-8.55, 4.0, 0.44), (1.6, 0.15, 0.35), "five on the sofa, each different: rust velvet, cream cable knit, striped linen, teal silk, sheepskin", E, "rust"),
    P(L, "Wool Throw", "box", (-8.3, 5.05, 0.44), (0.25, 0.9, 0.3), "chunky cream wool over the sofa's north arm", E, "linen"),
    P(L, "Coffee Table", "sled", (-6.65, 4.0), (1.2, 0.6, 0.42),
      "honed travertine top at {at}, {w} x {d} m, long side along the sofa, {h} m high on two "
      "blackened-steel sled bases", E, "travertine", 0.42, "tb"),
    P(L, "Pampas Vase", "cyl", (-6.8, 4.3, 0.42), (0.14, 0.14, 0.5), "tall smoked glass with dried pampas on the coffee table", mat="glass"),
    P(L, "Art Books", "box", (-6.5, 3.7, 0.42), (0.3, 0.24, 0.12), "three large books stacked on the coffee table", mat="books"),
    P(L, "Lounge Chair", "arm", (-6.4, 2.15), (0.78, 0.82, 0.8),
      "mid-century, cognac leather cushions on a splayed walnut frame with open arms, at {at} facing north,"
      " {w} x {d} x {h} m, seat {top} m", N, "cognac", 0.4, "tbc", col="warm"),
    P(L, "Wingback Chair", "arm", (-6.4, 5.8), (0.85, 0.85, 1.15),
      "mustard boucle with piped seams and turned oak front legs, at {at} facing south, {w} x {d} m, seat "
      "{top} m, wings {h} m high", S, "mustard", 0.45, "tbc", col="warm"),
    P(L, "Tufted Pouffe", "stool", (-5.3, 2.55), (0.5, 0.5, 0.42),
      "round, button-tufted rust velvet at {at}, {w} m across, {h} m high, four short brass-capped legs", N, "rust", 0.42, "tbc", col="warm"),
    P(L, "Side Table", "round", (-8.45, 5.65), (0.45, 0.45, 0.55),
      "turned walnut, baluster stem on tripod feet, at {at}, {w} m across, top {h} m", N, "walnut", 0.55, "tb"),
    P(L, "Ginger Jar Lamp", "lamp", (-8.45, 5.7, 0.55), (0.35, 0.35, 0.6), "celadon crackle-glaze ginger jar, pleated ivory shade, on the side table", mat=("sage", "shade")),
    P(L, "Nest of Tables", "table", (-8.45, 2.35), (0.5, 0.4, 0.5), "three black-lacquered tables with gold-tipped legs at the sofa's other end", E, "black"),
    P(L, "Bookcase", "shelf", (-9.4, 0.99), (2.0, 0.38, 2.3),
      "slate blue, on the south wall at {at}, {w} x {d} x {h} m, moulded cornice, six shelves, two base "
      "doors each with a brass knob", N, "slate",
      2.3, "tbhc", 2, "blue"),
    P(L, "Books", "books", (-9.4, 0.99), (2.0, 0.38, 2.3), "about eighty of mixed sizes and colours on the bookcase, some lying flat, ceramic bookends", N, "books"),
    P(L, "Record Cabinet", "cablegs", (-6.9, 1.03), (1.6, 0.45, 0.68),
      "teak, on the south wall at {at}, {w} x {d} x {h} m, sliding tambour doors, four black hairpin legs", N, "teak", 0.68, "tb"),
    P(L, "Turntable", "box", (-7.2, 1.05, 0.68), (0.45, 0.36, 0.14), "walnut plinth and clear dust cover, on the record cabinet", N, "walnut"),
    P(L, "Floor Speaker", "box", (-5.85, 0.98), (0.22, 0.3, 1.05), "tall and slim, white oak, grey cloth grille, beside the record cabinet", N, "oak"),
    P(L, "Television", "wall", (-6.9, 0.8), (1.45, 0.05, 0.84),
      "65-inch flat screen, {w} x {h} m, on the south wall above the record cabinet at x = {x}, {z} m up", N, "screen", checks="w", z=1.5),
    P(L, "Persian Rug", "rug", (-6.95, 4.0), (3.0, 2.2, 0.01),
      "hand-knotted, deep reds and indigo, {w} x {d} m, at {at}, long side along the sofa", E, "rug", checks="b"),
    P(L, "Arc Floor Lamp", "lamp", (-9.4, 5.75), (0.4, 0.4, 2.0), "white marble base, brushed-steel arc over the sofa, domed shade", mat=("steel", "shade")),
    P(L, "Monstera", "plant", (-10.45, 6.45), (0.7, 0.7, 1.5), "large, glazed indigo pot on a low wooden stand, north-west corner"),
    P(L, "Olive Tree", "plant", (-4.55, 6.45), (0.5, 0.5, 1.6), "small, terracotta pot, north-east corner"),
    P(L, "Linen Curtains", "curtains", (-10.9, 4.0), (2.2, 0.06, 2.45), "full-length oatmeal linen on a brass pole at the west window", E, "linen"),
    P(L, "Velvet Curtains", "curtains", (-7.5, 6.9), (2.8, 0.06, 2.5), "heavy forest-green velvet drawn back either side of the balcony door, black iron rail", S, "green"),
    P(L, "Abstract Painting", "wall", (-10.95, 5.95), (1.0, 0.05, 0.8),
      "unframed ochre-and-teal oil on deep canvas, {w} x {h} m, on the west wall at y = {y}, {z} m up", E, "paint_art", checks="w", z=1.6),
    P(L, "Log Basket", "cyl", (-4.35, 5.15), (0.45, 0.45, 0.4), "wicker, chopped birch logs, beside the fireplace", mat="rattan"),

    # The balcony, outside the living room's north wall.
    P(B, "Bistro Table", "round", (-9.8, 8.1), (0.6, 0.6, 0.72),
      "round, folding, sage-green enamelled steel, at {at}, {w} m across, top {h} m, cross-braced base", N, "sage", 0.72, "tbc", col="green"),
    P(B, "Folding Chair", "chair", (-10.45, 8.1), (0.44, 0.48, 0.82),
      "slatted acacia, brass hinges, crossed legs, at {at} facing the table, {w} x {d} x {h} m, seat {top} "
      "m", E, "acacia", 0.45, "tbl"),
    P(B, "Wire Chair", "chair", (-9.15, 8.1), (0.46, 0.5, 0.86),
      "white powder-coated wire bistro chair, sage linen seat pad, at {at} facing the table, {w} x {d} x "
      "{h} m, seat {top} m", W, "white", 0.46, "tbl"),
    P(B, "Outdoor Rug", "rug", (-9.8, 8.1), (1.8, 1.4, 0.01), "navy-and-white chevron weave under the bistro set", mat="navy"),
    P(B, "Sun Lounger", "lounger", (-5.4, 8.1), (0.7, 1.95, 0.9),
      "reclining teak at {at}, {d} m long, {w} m wide, foot to the west, seat {top} m, raised back {h} m, "
      "four legs, navy-and-white striped cushion", W, "teak", 0.35, "tb"),
    P(B, "Concrete Side Table", "cyl", (-4.45, 7.45), (0.4, 0.4, 0.45), "small concrete drum beside the lounger", mat="concrete"),
    P(B, "Lantern", "box", (-5.9, 7.45), (0.22, 0.22, 0.45), "black metal candle lantern with glass panes by the lounger", mat="black"),
    P(B, "Planter Box", "box", (-7.9, 8.75), (1.6, 0.4, 0.5), "long cedar box of lavender and rosemary along the balustrade", mat="cedar"),
    P(B, "Lemon Tree", "plant", (-6.7, 8.6), (0.6, 0.6, 1.5), "heavy with fruit, large cobalt-glazed pot", mat=("cobalt", "leaf")),
    P(B, "Watering Can", "box", (-8.95, 8.7), (0.4, 0.15, 0.3), "galvanised, by the planter",
      mat="steel"),

    # The dining room.
    P(D, "Refectory Table", "table", (-1.75, 3.9), (2.2, 1.0, 0.76),
      "limed oak at {at}, {w} m along X by {d} m, top {h} m, thick top on four square legs joined by a low "
      "box stretcher", S, "oak", 0.76, "tbl"),
    P(D, "Windsor Chair", "chair", (-2.3, 2.95), (0.5, 0.5, 0.95),
      "honey ash, saddle seat, spindle back, splayed turned legs, at {at} facing the table, {w} x {d} x {h}"
      " m, seat {top} m", N, "pine", 0.45, "tbl"),
    P(D, "Ladderback Chair", "chair", (-1.2, 2.95), (0.46, 0.46, 1.05),
      "ebonised beech, five curved slats, rush seat, at {at} facing the table, {w} x {d} x {h} m, seat "
      "{top} m", N, "black", 0.46, "tbl"),
    P(D, "Velvet Chair", "chair", (-2.3, 4.85), (0.52, 0.54, 0.82),
      "dusty-pink velvet, curved channel-tufted back, four slim brass legs, at {at} facing the table, {w} x"
      " {d} x {h} m, seat {top} m", S, "pink", 0.47, "tblc", col="warm"),
    P(D, "Cantilever Chair", "cantilever", (-1.2, 4.85), (0.47, 0.56, 0.8),
      "bent chrome tube, woven cane seat and back, at {at} facing the table, {w} x {d} x {h} m, seat {top} "
      "m", S, "chrome", 0.45, "tb"),
    P(D, "Carver Chair", "carver", (-3.3, 3.9), (0.6, 0.55, 1.1),
      "dark walnut armchair, open arms, turned front legs, high oxblood-leather back, at {at} at the "
      "table's west end, {w} x {d} x {h} m, seat {top} m", E, "oxblood", 0.46, "tblc", col="warm"),
    P(D, "Wishbone Chair", "chair", (-0.2, 3.9), (0.55, 0.52, 0.76),
      "Danish, natural oak, Y-shaped splat, steam-bent top rail, paper-cord seat, at {at} at the table's "
      "east end, {w} x {d} x {h} m, seat {top} m", W, "oak", 0.45, "tbl"),
    P(D, "Place Settings", "box", (-1.75, 3.9, 0.76), (1.9, 0.75, 0.02), "six: white stoneware plates on linen napkins, brass cutlery, stemmed glasses", mat="ceramic"),
    P(D, "Table Runner", "rug", (-1.75, 3.9, 0.76), (1.8, 0.3, 0.005), "hand-blocked indigo linen down the table", mat="navy"),
    P(D, "Candelabra", "cyl", (-1.75, 3.9, 0.765), (0.3, 0.3, 0.45), "three-branch silver with ivory candles, mid-table", mat="chrome"),
    P(D, "Rosewood Sideboard", "cablegs", (0.22, 5.4), (1.8, 0.45, 0.78),
      "on the east wall at {at}, {w} x {d} x {h} m, tapered legs, four middle drawers each with a sculpted "
      "wooden pull, a sliding door each end", W,
      "walnut", 0.78, "tbh", 4),
    P(D, "Fruit Bowl", "cyl", (0.22, 5.0, 0.78), (0.3, 0.3, 0.15), "footed ceramic bowl of pears and figs on the sideboard", mat="ceramic"),
    P(D, "China Cabinet", "cab", (-3.72, 5.8), (1.1, 0.46, 2.05),
      "duck-egg blue, glazed, on the west wall at {at}, {w} x {d} x {h} m, scalloped cornice, two glass "
      "doors each with a brass knob, two drawers below each with a porcelain knob", E, "duckegg", 2.05, "tbhc", 4, "blue"),
    P(D, "Blue Willow Dishes", "box", (-3.72, 5.8, 1.0), (0.9, 0.3, 0.8), "blue-and-white plates, tureens and teacups on the cabinet's shelves", E, "ceramic"),
    P(D, "Bar Cart", "table", (-3.45, 1.3), (0.75, 0.45, 0.82), "brass, two smoked-glass tiers, spoked wheels, decanters and glasses, south-west corner", E, "brass"),
    P(D, "Still Life", "wall", (-3.95, 3.9), (0.9, 0.06, 0.7),
      "lemons in oil, {w} x {h} m, carved gilt frame, on the west wall above the carver at y = {y}, {z} m "
      "up", E, "gilt", checks="w", z=1.65),
    P(D, "Bronze Mirror", "wall", (0.45, 5.4), (0.8, 0.04, 1.0),
      "tall, bevelled, thin bronze frame, {w} x {h} m, on the east wall above the sideboard at y = {y}, {z}"
      " m up", W, "brass", checks="w", z=1.6),
    P(D, "Kilim Rug", "rug", (-1.75, 3.9), (3.2, 2.4, 0.01),
      "flat-woven terracotta geometric wool, {w} x {d} m, under the table at {at}", S, "terracotta", checks="b"),
    P(D, "Kentia Palm", "plant", (-0.1, 6.6), (0.6, 0.6, 1.8), "ribbed white pot, north-east corner", mat=("ceramic", "leaf")),
    P(D, "Roman Blinds", "box", (-1.75, 6.88, 1.7), (1.8, 0.04, 0.4), "mustard linen, at the window", mat="mustard"),

    # The kitchen.
    P(K, "Base Cabinets", "cab", (2.35, 6.65), (3.5, 0.6, 0.88),
      "Shaker, sage green, along the north wall from x = 0.6 to x = 4.1, {d} m deep, {h} m high, six doors "
      "and drawers each with a brass cup handle or knob", S, "sage", 0.88, "bhc", 6,
      "green"),
    P(K, "Worktop", "box", (2.35, 6.64, 0.88), (3.52, 0.62, 0.04),
      "honed white marble, 4 cm thick, bullnose edge, over the run, top {top} m", S,
      "marble", 0.92, "z"),
    P(K, "Belfast Sink", "sink", (2.6, 6.62), (0.6, 0.45, 0.92),
      "deep fireclay, set into the worktop under the window at {at}, {w} m wide, basin floor about 0.2 m "
      "below the worktop's {top} m", S, "ceramic", 0.92, "r"),
    P(K, "Bridge Tap", "box", (2.6, 6.88, 0.92), (0.3, 0.06, 0.3), "brass, cross-head handles, swan-neck spout, behind the sink", mat="brass"),
    P(K, "Range Cooker", "cab", (4.55, 6.65), (0.9, 0.6, 0.92),
      "dual-fuel, cream enamel, at {at}, {w} m wide, {d} m deep, hob {h} m high with five burners, two oven"
      " doors with chrome rail handles, black control knobs", S, "cream", 0.92, "Th",
      2),
    P(K, "Copper Hood", "box", (4.55, 6.75, 1.6), (0.9, 0.4, 0.6), "brushed copper canopy above the range", mat="gilt"),
    P(K, "Whistling Kettle", "cyl", (4.35, 6.55, 0.92), (0.2, 0.2, 0.22), "red enamel, on a burner", mat="red"),
    P(K, "Glazed Wall Cabinets", "box", (1.15, 6.775, 1.45), (1.1, 0.35, 0.8), "0.35 m deep, west of the window, 1.45 to 2.25 m up, reeded-glass doors, small brass knobs", mat="sage"),
    P(K, "Copper Pans", "box", (5.22, 6.85, 1.3), (0.4, 0.15, 0.5), "three graduated saucepans on a wall pot rack beside the hood", mat="gilt"),
    P(K, "Knife Strip", "box", (1.2, 6.92, 1.15), (0.45, 0.03, 0.2), "walnut, magnetic, five knives, above the worktop", mat="walnut"),
    P(K, "Bread Bin", "box", (0.9, 6.7, 0.92), (0.35, 0.25, 0.22), "cream enamel roll-top, on the worktop", mat="cream"),
    P(K, "Stand Mixer", "box", (3.8, 6.7, 0.92), (0.22, 0.35, 0.35), "pistachio green, on the worktop", mat="mint"),
    P(K, "Plate Rack", "box", (1.9, 6.75, 0.92), (0.4, 0.25, 0.3), "wooden, drying plates, beside the sink", mat="oak"),
    P(K, "Herb Pots", "box", (2.6, 6.95, 1.1), (0.6, 0.12, 0.2), "three terracotta pots of basil, thyme and parsley on the sill", mat="pot"),
    P(K, "Mint Fridge", "cab", (5.11, 5.2), (0.7, 0.68, 1.75),
      "retro, pastel mint, rounded corners, two chrome lever handles, on the east wall at {at}, {w} x {d} x"
      " {h} m", W, "mint", 1.75, "tbhc", 2, "green"),
    P(K, "Island", "island", (2.6, 4.2), (2.0, 0.95, 0.92),
      "at {at}, {w} m along X by {d} m, butcher-block oak top {h} m high, navy panelled sides, three "
      "drawers on its north side each with a brass bin pull", N, "navy", 0.92,
      "tbhc", 3, "blue"),
    P(K, "Fruit Basket", "cyl", (2.0, 4.3, 0.92), (0.3, 0.3, 0.12), "wire, lemons and apples, on the island", mat="steel"),
    P(K, "Saddle Counter Chair", "stool", (2.0, 3.3), (0.42, 0.4, 0.66),
      "tan leather saddle seat {top} m high, {w} m across, four black steel legs, footrest ring, at {at}", N, "cognac", 0.66, "tbc", col="warm"),
    P(K, "Rattan Counter Chair", "stool", (2.65, 3.3), (0.45, 0.45, 0.85),
      "woven rattan seat {top} m high, low curved back {h} m, four bamboo legs, at {at}", N, "rattan", 0.68, "tl"),
    P(K, "Tractor Seat", "stool3", (3.3, 3.3), (0.4, 0.4, 0.72),
      "industrial swivel seat, pressed-steel tractor seat {top} m high, cast-iron tripod base, at {at}",
      N, "graphite", 0.72, "t"),
    P(K, "Breakfast Table", "round", (4.55, 2.0), (0.8, 0.8, 0.74),
      "round, white marble top {h} m high, fluted cast-iron pedestal, at {at}, {w} m across", N, "marble", 0.74, "tb"),
    P(K, "Bentwood Chair", "chair", (4.55, 1.3), (0.43, 0.5, 0.88),
      "cafe chair in walnut stain, looped back, cane seat, at {at} facing the table, {w} x {d} x {h} m, "
      "seat {top} m", N, "walnut", 0.46, "tbl"),
    P(K, "Spindle Chair", "chair", (5.15, 2.0), (0.44, 0.46, 0.98),
      "farmhouse spindle-back painted cherry red, at {at} facing the table, {w} x {d} x {h} m, seat {top} m",
      W, "red", 0.45, "tblc", col="warm"),
    P(K, "Utensil Rail", "wall", (0.55, 5.6), (0.8, 0.08, 0.35), "brass, on the west wall, ladle, whisk, spatula and tongs on S-hooks", E, "brass", z=1.35),
    P(K, "Station Clock", "wall", (0.55, 4.6), (0.4, 0.05, 0.4),
      "round, {w} m across, black frame, on the west wall at y = {y}, {z} m up", E,
      "black", checks="w", z=2.0),
    P(K, "Open Shelf", "wall", (0.55, 3.0), (1.2, 0.25, 0.05), "chunky oak on brass brackets, west wall", E, "oak", z=1.5),
    P(K, "Storage Jars", "box", (0.68, 3.0, 1.525), (0.25, 1.0, 0.22), "glass, cork lids, pasta, rice and lentils, on the open shelf", mat="glass"),
    P(K, "Striped Runner", "rug", (2.3, 5.95), (1.6, 0.6, 0.01), "striped cotton, in front of the sink",
      mat="navy"),

    # The pantry.
    P(PA, "Pine Shelving", "shelf", (5.75, 4.2), (3.6, 0.4, 2.1),
      "slatted pine on square uprights along the west wall at {at}, {w} x {d} x {h} m, five shelves", E, "pine", 2.1, "tb"),
    P(PA, "White Shelving", "shelf", (7.2, 4.6), (3.2, 0.5, 2.0),
      "white-painted along the east wall at {at}, {w} x {d} x {h} m, marble slab at 0.9 m, three shelves "
      "above", W, "white", 2.0, "tb"),
    P(PA, "Preserve Jars", "box", (5.75, 4.6, 1.2), (2.0, 0.3, 0.2), "rows of jam, pickles and peaches under gingham lids on the shelves", E, "glass"),
    P(PA, "Baskets", "box", (5.75, 3.2, 0.4), (1.0, 0.32, 0.25), "wicker, bread rolls and apples, lower pine shelves", E, "rattan"),
    P(PA, "Spice Rack", "wall", (6.45, 6.95), (0.6, 0.1, 0.5), "tiered, forty small glass jars, north wall above the freezer", S, "oak", z=1.45),
    P(PA, "Chest Freezer", "cab", (6.45, 6.62), (0.9, 0.6, 0.85),
      "white, at the north end at {at}, {w} x {d} x {h} m, lid handle across its front",
      S, "white", 0.85, "tbh", 1),
    P(PA, "Flour Bins", "cyl", (6.45, 2.6), (0.35, 0.35, 0.5), "three galvanised, lidded, in a row", mat="steel"),
    P(PA, "Wine Rack", "box", (5.71, 1.6), (0.9, 0.32, 1.0), "pine, twenty-four bottles, by the door",
      E, "pine"),
    P(PA, "Step Ladder", "box", (6.5, 3.4), (0.4, 0.35, 0.45), "two-step folding, red-painted beech",
      mat="red"),
    P(PA, "Vegetable Crates", "box", (7.15, 2.0), (0.45, 0.4, 0.6), "stacked wooden crates of potatoes, onions and squash", mat="pine"),

    # The laundry room.
    P(LA, "Washing Machine", "washer", (9.6, 6.65), (0.6, 0.6, 0.85),
      "front-loading, white, at {at}, {w} x {d} x {h} m, round porthole door 0.4 m across centred 0.45 m "
      "up, its chrome rim proud of the front", S, "white", 0.85, "tbp"),
    P(LA, "Tumble Dryer", "cab", (10.3, 6.65), (0.6, 0.6, 0.85),
      "heat-pump, graphite grey, beside it at {at}, {w} x {d} x {h} m, round tinted door, control dial", S, "graphite", 0.85, "tbc", col="grey"),
    P(LA, "Utility Sink", "usink", (8.15, 6.7), (0.6, 0.5, 0.9),
      "deep stainless steel on a steel stand, north wall, at {at}, {w} m wide, rim {top} m, basin about 0.3"
      " m deep", S, "steel", 0.9, "r"),
    P(LA, "Gooseneck Tap", "box", (8.15, 6.9, 0.9), (0.06, 0.06, 0.4), "tall, over the utility sink",
      mat="chrome"),
    P(LA, "Broom Cupboard", "cab", (7.83, 4.0), (0.8, 0.55, 2.1),
      "tall, pale grey, on the west wall at {at}, {w} x {d} x {h} m, two doors each with a black T-bar "
      "handle", E, "grey", 2.1, "tbhc", 2, "grey"),
    P(LA, "Ironing Board", "table", (10.0, 2.2), (1.3, 0.38, 0.9),
      "floral cover, folding steel legs, at {at}, {w} x {d} m, set at {h} m", N, "linen",
      0.9, "tb"),
    P(LA, "Steam Iron", "box", (10.48, 2.2, 0.9), (0.28, 0.12, 0.15), "blue, standing on the board's narrow end", mat="cobalt"),
    P(LA, "Clothes Airer", "box", (9.3, 3.7), (1.0, 0.5, 1.2), "freestanding folding wood, shirts and towels on it", mat="pine"),
    P(LA, "Laundry Baskets", "cyl", (8.4, 2.5), (0.5, 0.5, 0.45), "two woven, one of folded linen", mat="rattan"),
    P(LA, "Wall Shelf", "wall", (10.35, 6.95), (0.9, 0.25, 0.04), "white, above the dryer", S, "white",
      z=1.4),
    P(LA, "Detergents", "box", (10.35, 6.82, 1.42), (0.7, 0.18, 0.25), "bottles, a box of pegs and a jar of soap flakes on the shelf", mat="paleblue"),
    P(LA, "Folding Table", "table", (10.65, 4.3), (1.2, 0.6, 0.85),
      "beech top, folding chrome legs, on the east wall at {at}, {w} x {d} x {h} m", W, "pine", 0.85, "tbl"),
    P(LA, "Mop Bucket", "cyl", (8.0, 1.3), (0.35, 0.35, 0.35), "galvanised, with a mop", mat="steel"),
    P(LA, "Anti Fatigue Mat", "rug", (9.6, 6.0), (1.4, 0.6, 0.015), "grey rubber, in front of the machines", mat="graphite"),
]
