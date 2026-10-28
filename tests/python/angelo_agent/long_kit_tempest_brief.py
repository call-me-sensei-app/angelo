# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The brief of ``scenario_oneshot_tempest`` (long candidate 6): a ship in a bottle on a period desk in a dark study.

Paragraphs with ``{key}`` for every checked number from ``long_kit_tempest_values.V``; ``text()`` fills them. It
describes the scene as a person would ask for it, piece by piece with its construction, counts, materials, sizes and
wear (taken from the source showpiece's tour and its construction); it never says how to build it or in what order.
"""

import long_kit_tempest_values as values

OPENING = (
    "A ship in a bottle, as a finished showpiece: a stormy sea, a three-masted frigate and a blue whale sealed inside "
    "a hand-blown glass bottle on a carved walnut stand, on a period desk in a dark study at night. Build it. The "
    "attached pictures show it: the hero shot and a front elevation of the bottle. Take the layout and mood from them; "
    "where they and this brief disagree, the brief wins. Everything is real geometry at production detail, down to "
    "the instruments in the captain's cabin: planked, fitted, worn and weathered, with scratches, tarnish and stains "
    "where things are handled, never a toy-shaped shell or a painted backdrop. It is a still: no animation and no "
    "simulation.")

CONVENTIONS = (
    "Conventions. Metres, Z up, at real size; the study's open side faces -Y. I find things by name: name every piece "
    "exactly as quoted, and give every part of a piece a name starting with the piece's name (e.g. 'Asterion Main Mast "
    "Iron Band'), never the name of what it stands on; anything you add gets a meaningful name starting with "
    "'Study', 'Bottle' or 'Asterion'. Keep the file tidy: nothing from the default scene remains, no unused leftovers, "
    "and a material on everything that shows.")

STUDY = (
    "The study. 'Study Floor', wide dark oak boards with worn seams, {floor_w} m along X by {floor_d} m along Y, "
    "centred on the origin, top at z = 0. 'Study Wall' plaster in smoked blue-grey, {wall_h} m high and {wall_t} m "
    "thick, along the north (y = 2), west and east edges; the south side is open. 'Study Wainscot', panelled dark oak "
    "{wainscot_h} m high round the three walls: walnut stiles every 0.6 m, a moulded top rail and a thin brass picture "
    "rail just above it. 'Study Window', in the north wall centred at x = {win_x}, {win_w} m wide, sill {win_sill} m "
    "and head {win_head} m up: a walnut-lined recess 0.2 m deep, a central mullion and two transoms making six panes "
    "of moonlit, rain-streaked glass, and a deep sill board. 'Study Curtains', heavy brown linen, each {curtain_w} m "
    "wide, hanging in deep vertical folds to the floor either side of the window from a black iron pole. 'Study "
    "Bookcase', walnut, against the north wall centred at ({case_x}, {case_y}), {case_w} x {case_d} x {case_h} m: "
    "three uprights, a moulded cornice and five shelves with thick front edges. 'Study Atlases', about sixty bound "
    "atlases and logbooks {book_low} to {book_high} m tall and of different thicknesses, in green, brown, slate and "
    "oxblood leather, densely filling the four lower shelves, thirteen to a shelf, a few leaning a little, each spine with two bands "
    "of gilt-stamped bands a third of the way from each end.")

DESK = (
    "'Study Desk', a period pedestal desk in dark oak, centred at ({desk_x}, {desk_y}), {desk_w} m along X by "
    "{desk_d} m, top {desk_top} m up: a moulded top with a gold-embossed green-leather writing surface inset, a long central "
    "drawer over the knee hole, two pedestals of three graduated drawers on plinth bases, a brass bail pull on every "
    "drawer, the board seams and the worn edge of the top showing years of use. On it: 'Study Chart', a parchment "
    "chart of the North Atlantic dated 1847, {chart_w} x {chart_d} m, lying centred at ({chart_x}, {chart_y}) turned "
    "{chart_turn} degrees, its edges curling up, with a faint grid, coastlines, a dashed sailing route, an eight-point "
    "compass rose and its title; 'Study Dividers', brass chart dividers, two 0.12 m legs hinged at the top, lying open "
    "on the chart; 'Study Brass Compass', a desk compass {compass_dia} m across at ({compass_x}, {compass_y}): a "
    "brass ring round an aged paper dial printed with a sixteen-point rose, 120 degree ticks and N, E, S and W, and a "
    "separate needle on a spindle; 'Study Coins', twelve old brass coins of different sizes, 1.5 to 3 cm across, "
    "scattered between x = {coins_x0} and {coins_x1}, y = {coins_y0} and {coins_y1}; 'Study Lantern', a hexagonal "
    "brass lantern {lamp_h} m tall to its bail ring, at ({lamp_x}, {lamp_y}): a {lamp_foot} m hexagonal foot, six "
    "brass corner posts framing six amber glass panes, an upper gallery, a sloping ventilated hood with eight vent "
    "slots, an iron vent cap and a bail ring, and inside a wax candle with a glowing flame (its panes 0.16 m tall, "
    "the hood from 0.16 m across at its foot to 0.04 m at its top and 0.05 m tall, the vent cap 0.035 m across and "
    "tall, the bail ring 0.046 m across, the candle 0.03 m across and 0.043 m tall); 'Study Candle Lantern', a "
    "smaller brass candle lantern of the same kind, {candle_h} m tall, at the desk's back-left corner, at "
    "({candle_x}, {candle_y}).")

STAND = (
    "The stand. 'Bottle Plinth', carved walnut, centred at (0, {axis_y}) on the desk, {plinth_w} m along X by "
    "{plinth_d} m at its widest moulding, top {plinth_top} m up: a darker bevelled lower moulding 1.2 cm tall, a walnut "
    "body 1.7 cm tall set back 6 mm, and a darker moulded top 0.7 cm tall, on four bun feet 2.8 cm across showing 4 mm "
    "below it, with a brass fitting 1.5 x 1.3 cm and 1.2 cm tall on each corner. "
    "Two sculpted walnut cradles stand on it and hold the bottle, 'Bottle Cradle West' at x = {cradle_wx} and 'Bottle "
    "Cradle East' at x = {cradle_ex}, each {cradle_t} m thick and {cradle_d} m across: a bevelled saddle whose "
    "upper edge is curved to hug the bottle's underside, its horns rising to {cradle_horn} m up either side and each "
    "topped by a brass ball finial {finial} m across, standing on a pedestal block 3.8 x 3.1 cm and 1 cm tall "
    "under each horn, with a thin "
    "vertical brass inlay 5 cm long on each face. 'Bottle Plaque', an engraved brass plaque {plaque_w} x {plaque_h} m "
    "on the plinth's front, centred at x = {plaque_x}, {plaque_z} m up, in a raised brass surround held by two "
    "domed screws, engraved 'Tempest' in a large italic and below it 'THE ASTERION · NORTH ATLANTIC · 1847' in small "
    "capitals inside a ruled border.")

BOTTLE = (
    "'Bottle Glass', the hand-blown bottle lying along X, its axis at y = {axis_y}, z = {axis_z}: a domed base at "
    "x = {base_x}, a straight body {body_dia} m across from x = {body_x0} to {body_x1}, a rounded shoulder narrowing "
    "to a neck {neck_dia} m across from x = {neck_x0} to {neck_x1}, and a rolled lip {lip_dia} m across at x = "
    "{lip_x}. Clear glass with a faint green tint, walls {glass_wall} m thick, index of refraction 1.46, so the study "
    "bends round its shoulder; it is open at the neck and hollow, and everything below sits inside it. The neck: "
    "'Bottle Cork', a natural cork stopper with rough, pitted relief, pushed into the neck from x = {cork_x0} to "
    "{cork_x1}, {cork_in} m across at the lip and {cork_out} m at its outer end; 'Bottle Neck Rope', {rope_turns} "
    "tight turns of laid three-strand hemp, each 3 mm thick and 3.2 mm apart, each turn slightly wavy, round the neck from x = {rope_x0} to "
    "{rope_x1}; 'Bottle Knot', a bulky mariner's knot in the same rope on the neck's front, at ({knot_x}, {knot_y}, "
    "{knot_z}), 1.8 cm across. Two lines hang from the knot ('Bottle Suspension Lines'), 2 mm and 1.6 mm thick, each tied to an instrument's ring, so "
    "nothing floats: 'Bottle Compass', a hanging brass compass {hc_dia} m across and 6 mm deep, its face toward -Y, "
    "centred at ({hc_x}, {hc_y}, {hc_z}): a bezel 2.5 mm thick with a fine engraved ring, a housing 4.2 mm deep "
    "with a back, an "
    "engraved rose with sixteen points, 120 ticks and N, E, S and W, a separate magnetic needle 3.5 cm long, "
    "diamond-shaped, on an iron spindle 3 mm across, and a suspension ring 4 mm across on top, the brass tarnished and finely scratched; 'Bottle Lantern', a small hexagonal "
    "brass lantern {hl_h} m tall from its foot to its vent cap, centred at ({hl_x}, {hl_y}, {hl_z}), built like the "
    "desk lantern: amber glass panes between six posts, a ventilated hood, a candle and its glowing flame, and an iron "
    "bail ring.")

INSIDE = (
    "Inside the bottle. 'Bottle Sea', a stormy ocean whose surface lies at a mean z = {sea_z}, rough with irregular "
    "swell and choppy crests and troughs up to {swell} m above and below it, sharper crests running across the "
    "bottle with the wind, white foam on the crests and lingering foam streaks where the surface folds, over a body "
    "of deep blue-green water that fills the bottle below it, out to the glass and no further. 'Bottle Storm Clouds', "
    "a volumetric storm sky of billowing, self-shadowed dark grey cloud with brighter tops, from about z = "
    "{cloud_base} up to the glass, held inside the bottle; 'Bottle Rain', about 700 fine slanting streaks, each "
    "about 6 mm long, falling from the clouds to the sea, inside the glass. 'Bottle Moon', a small glowing pale-blue moon {moon_dia} m across, low in "
    "the clouds at ({moon_x}, {moon_y}, {moon_z}).")

HULL = (
    "The Asterion, a three-masted frigate riding the swell upright, bow toward the neck (+X), on the bottle's axis "
    "line (y = {axis_y}). 'Asterion Hull', one continuous oak hull, round-bilged with a slight tumblehome (widest just below "
    "the gun ports, tucking in toward the rail), fining to a sharp stem and "
    "full under the quarterdeck, from its flat stern transom at x = {stern_x} to its stem at x = {stem_x}, {beam} m "
    "in beam, its keel at z = {keel_z} below the sea and its sides rising to the cap rail at {rail_z} m amidships, "
    "sweeping up {sheer} m toward bow and stern. It is planked in caulked strakes about 3 mm wide that follow its "
    "curve, weathered and darker below the water; five thin dark wales 7 mm apart below the gun ports, a heavier dark "
    "wale along the waterline, a rubbed brass rub rail at {rub_z} m and a gilt upper rail at 0.949 m, each running "
    "the full length; bulwarks of 35 panels a side, 7 mm tall, with a brass stanchion at every third panel and a dark "
    "cap rail. The transom, 2.8 cm wide, has five open gallery windows 2.9 x 5.3 mm, 5 mm apart and centred 0.935 m "
    "up, each with gilt vertical and horizontal frame bars and a glass pane. Gun ports: six a side, each opening "
    "{port_w} x {port_h} m in a 7.5 x 7 mm brass surround, centred {port_z} m up at x = {port_x0} and every "
    "{port_dx} m from aft to fore, each with a 7 x 5.5 mm oak lid hinged above it and raised about 13 degrees; "
    "'Asterion Cannons', twelve iron barrels 4 mm across at the breech tapering to 3 mm, run out through the ports, "
    "each muzzle with a raised ring standing {gun_out} m clear of the hull side. 'Asterion Main Deck', 49 caulked "
    "teak planks 3.5 mm wide laid across the ship at z = {deck_z} from x = {qd_x} forward, stopping short round the "
    "hatch; 'Asterion Quarterdeck', raised aft of it from the stern to x = {qd_x}, 3.9 cm wide, its planking at z = "
    "{qd_z}, with a gilt gallery rail 6.6 mm high along each side carried on nine brass balusters 3.7 mm apart.")

RIG = (
    "'Asterion Fore Mast' at x = {fore_x}, 'Asterion Main Mast' at x = {main_x} and 'Asterion Mizzen Mast' at x = "
    "{mizzen_x}, on the centre line, their tops at z = {fore_top}, {main_top} and {mizzen_top}: tapered spars "
    "{mast_dia} m across at the deck, bound with iron bands 5 mm across and 1.5 mm tall at 0.951, 0.973, 1.001 and "
    "1.032 m up wherever the mast reaches them, and each with a round crow's nest {nest_dia} m across at its rim and "
    "0.9 cm at its floor, 3.8 mm deep, {nest_down} m below its top, railed by eight brass posts 5.4 mm tall under a "
    "brass cap ring. Each mast crosses three yards, slung 5 mm ahead of it and braced round about {brace} degrees, "
    "the lowest 2 mm thick and each higher one thinner, and carries three square sails, one under each yard: stained, "
    "finely woven linen with a seamed texture, laced along its head to the yard, its foot 12% narrower than its "
    "head, bellying about 7 mm forward in the wind, held at its two lower corners by sheets, no reefs taken (yard "
    "height, sail width x depth): "
    + "; ".join("'%s' {%s_yard}, {%s_w} x {%s_d} m" % ((name,) + (name.split(" ", 1)[1].lower().replace(" ", "_"),) * 3)
                for name, *_rest in values.SAILS) + ". "
    "'Asterion Jibs', two filled triangular headsails of the same linen ahead of the fore mast: the outer from 1.054 "
    "m up on the fore mast to the bowsprit's end, the inner from 1.034 m up to the bowsprit's middle. 'Asterion "
    "Bowsprit', a spar 4 mm across at the bow tapering forward, rising from ({sprit_x0}, {axis_y}, {sprit_z0}) to "
    "({sprit_x1}, {axis_y}, {sprit_z1}). 'Asterion Standing Rigging', every line separate and drawn taut: four "
    "shrouds a side for each mast (24), 0.5 mm thick, their feet spread along the hull side from 2.9 cm aft of the "
    "mast and their heads meeting 2.9 cm below its top; fifteen ratlines 0.4 mm thick evenly spaced up each set of "
    "four shrouds, about 6 mm apart on the main (90); a backstay a side from just below each top aft to the hull side 3.9 cm behind the mast (6); "
    "and three forestays, from each top forward to the foot of the next mast and from the fore top to the bowsprit's "
    "end. 'Asterion Running Rigging': two lifts for each yard from just below its mast's top to its yard arms (18) and "
    "two sheets for each sail from its lower corners down to the deck or to the yard below (18). On deck: 'Asterion "
    "Pin Rails', six oak rails 7.8 mm long on the bulwarks, at x = {pin_x0}, {pin_x1} and {pin_x2}, {pin_off} m "
    "either side of the centre line, each with three turned belaying pins 5.4 mm tall; 'Asterion Line Coils', four "
    "turns of line 5 to 7 mm across flaked down on deck; 'Asterion Companion Hatch', at x = {hatch_x}: two oak "
    "coamings 2.3 cm long and 4.5 mm high either side of a hinged grating of seven brass bars 2.1 cm long, 2.4 mm "
    "apart; 'Asterion Capstan', at x = {capstan_x}, oak, 1.1 cm across at its foot and 0.9 cm at its head, 1 cm "
    "tall, with a 2 cm bar through its head; 'Asterion Water Barrels', two at x = {barrel_x0} and {barrel_x1}, y = "
    "{barrel_y}, each {barrel_dia} m across and {barrel_h} m tall with two iron hoops; 'Asterion Pennant', a long "
    "rust-red weather pennant {pennant_l} m long flying from the main top. 'Asterion Helm', on the quarterdeck at x = "
    "{helm_x}, centred {helm_z} m up: an eight-spoke oak wheel {helm_rim} m across its 1.3 mm rim, the spokes "
    "running out past the rim to turned handles {helm_reach} m across tip to tip, turned to face fore and aft on a "
    "brass hub 3.2 mm across and 4 mm long, carried on a dark oak pedestal from the quarterdeck.")

BELOW = (
    "Below decks, real rooms inside the hull. 'Asterion Frames': thirteen steam-bent oak frames 1.7 mm thick and "
    "1.3 cm apart inside the hull from x = -0.11 forward, deck beams 1.6 x 1.9 mm under the main deck, a pillar at "
    "every third frame, and a keelson 3.6 mm square along the hold. 'Asterion Gun Deck Floor', 47 planks 3.6 mm wide "
    "at z = {gd_z}, {gd_w} m wide amidships, from the stern to the bow, with trodden stains and scuffs. On it, "
    "'Asterion Gun Deck Guns', ten long guns, five a side at x = " + ", ".join("%g" % x for x in values.GUN_DECK_GUNS)
    + ", each an iron barrel 3.4 mm across pointing outboard on an oak carriage 9 x 7.8 mm and 4 mm tall with four "
    "trucks 3.2 mm across, a breeching rope from the hull side round its breech, a round shot 1.6 mm across and an "
    "open powder bucket beside it; 'Asterion Hammocks', six sagging canvas hammocks 2.6 cm long and 6.6 mm wide, "
    "three a side at x = -0.052, -0.018 and 0.016, 1.2 cm either side of the centre line, slung under the main "
    "deck's beams by four end lines each converging to the beams; 'Asterion Mess Table', a crew table 1.4 x 0.8 cm on "
    "the centre line at x = {mess_x}, top {mess_top} m up, on two legs between two benches 1.7 cm long, a wooden "
    "bowl and a pewter mug on it; 'Asterion Companion Ladder', seven treads between two rails 1.2 cm apart from the "
    "hatch down to the gun deck; 'Asterion Gun Deck Lanterns', two small brass lanterns hung from the beams, one "
    "aft and one forward. Aft, behind 'Asterion Cabin Bulkhead', a panelled bulkhead across the hull at x = "
    "{bulkhead_x} with a door opening 1.8 cm wide and 2.7 cm tall under a lintel, its panelled door with two "
    "recessed panels standing open on its hinges with a brass knob, lies the captain's cabin, its joints stained "
    "with moisture: 'Asterion Chart Table', centred at ({table_x}, {axis_y}), {table_w} x {table_d} m, top "
    "{table_top} m up, on four legs, the stern windows' mullions and a sill behind it; on it 'Asterion Cabin Chart', "
    "the North Atlantic 1847, 1.1 x 1.6 cm, a brass telescope 5 mm long, a chart compass ring, a little ink pot, "
    "'Asterion Dip Pen', a worn turned wooden holder 1.5 mm long with fine finish scratches, a tarnished brass "
    "ferrule, a pointed steel nib with its central slit and breather hole, and 'Asterion Chronometer', an 1847 marine "
    "chronometer in a mahogany box 2.8 mm square and 1.3 mm tall bound with brass on two sides, its lid open and "
    "leaning back on two hinges, a brass gimbal ring on pivots carrying the drum, a scratched bezel, an aged ivory "
    "dial with sixty minute ticks, twelve Roman numerals and 'LONDON · 1847', and three blued steel hands; "
    "'Asterion Cot', a wall cot 2.4 cm long against the +Y side with a mattress, a linen pillow and a folded "
    "slate-blue wool blanket; 'Asterion Sea Chest', 1.3 x 0.6 cm and 1.6 cm tall against the -Y side, bound with two "
    "brass straps and a latch; 'Asterion Cabin Chair', a seat on four legs with a back; 'Asterion Cabin Shelf', a "
    "shelf 1.5 cm long with five navigation volumes; 'Asterion Ships Clock', a brass bezel 5.3 mm across on the "
    "bulkhead; 'Asterion Cabin Lantern', a small brass lantern on the chart table. Below the gun deck: 'Asterion Hold "
    "Floor', planked at z = {hold_z}, {hold_w} m wide amidships, the keelson along it; 'Asterion Hold Casks', twelve "
    "bellied water casks 5.4 to 6.6 mm across and 9 mm tall, each with three iron hoops, six a side; 'Asterion Hold "
    "Crates', six crates 6 x 6 mm and 4.7 mm tall with two battens each; 'Asterion Ballast', nineteen rough flat "
    "grey stones 2 to 4 mm across along the bottom; 'Asterion Hold Rope', a stored coil of three turns; 'Asterion "
    "Hold Stanchions', two oak posts 2.2 mm across from the hold floor to the gun deck; 'Asterion Hold Lamp', a small "
    "safety lamp.")

WHALE = (
    "'Blue Whale', beneath the surface beside the ship on its -Y side, centred on y = {wh_y} and z = {wh_z}, "
    "swimming toward the neck: from its rounded U-shaped snout at x = {wh_nose} to the trailing edge of its flukes at "
    "x = {wh_tail}; the rostrum capped by a smooth rounded tip; one continuous long, tapering body {wh_w} m wide and {wh_h} m deep at its widest, a little over "
    "halfway back from the tail, the head slightly lowered and flattened; mottled blue-grey skin with paler patches, "
    "lighter underneath, the tail stock narrowing to about 6 mm deep before the flukes; about fifty fine throat pleats, grooves running along its underside from the tip of the jaw "
    "back past the flippers; paired blowholes 2.5 mm apart on top of the head at x = 0.057, each in a raised lip, "
    "with a low ridge running forward from them; a small swept dorsal fin 7 mm tall far aft, at x = -0.018; two "
    "long, slender, tapered flippers; and 'Blue Whale Flukes', two thin horizontal foils {wh_span} m from tip to tip, "
    "1.9 cm deep at the root, with rounded leading edges, tapered tips, thin concave trailing edges and a central "
    "notch. 'Blue Whale Eyes', two small dark eyes 1.5 mm across, one each side of the head at x = {eye_x}, {eye_z} m "
    "up, each with a dark pupil in a faint iris, set into the skin under a soft eyelid fold and a fine orbital crease.")

MATERIALS = (
    "Materials, as they look: brass in warm gold, rubbed bright on edges and darker with patina in recesses; gilt "
    "rails a paler gold; iron (cannons, hoops, spindles, vent caps) dark gunmetal grey; the hull's oak a warm "
    "mid-brown greying where the sea reaches it; the decks' teak a lighter honey; the walnut of the stand and the "
    "bookcase a deep red-brown; the sails and hammocks off-white linen stained buff; rope and lines a pale tan hemp; "
    "the cork honey-brown and pitted; the study's plaster a smoked blue-grey; the curtains a dull brown; the sea "
    "deep blue-green under white foam; the whale blue-grey. Wood shows its grain, worn edges and small scratches.")

LIGHT = (
    "Light and camera. The study is dark: cool blue moonlight through the window from high in the north-west, the "
    "lanterns' warm candlelight, a dim warm fill, and a cool glow inside the bottle from the moon. A camera named "
    "'Hero Camera' at ({cam_x}, {cam_y}, {cam_z}) looking at ({look_x}, {look_y}, {look_z}) with a {lens} mm lens, as "
    "the scene's camera.\n\nBefore you call it done, look at it from the hero camera and close up, at the ship, the "
    "cabin, the whale and the neck, next to the pictures, and fix what is wrong.")

SHIP = HULL + " " + RIG
PARAGRAPHS = (OPENING, CONVENTIONS, STUDY, DESK, STAND, BOTTLE, INSIDE, HULL, RIG, BELOW, WHALE, MATERIALS, LIGHT)


def keys(template):
    """The ``{key}`` names a template uses."""
    import string
    return {name for _text, name, _spec, _conv in string.Formatter().parse(template) if name}


def text():
    words = {key: "%g" % value for key, value in values.V.items()}
    return "\n\n".join(paragraph.format(**words) for paragraph in PARAGRAPHS)
