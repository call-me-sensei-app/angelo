# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where each Angelo Library item is used: one category per item, derived from its words when the index is built.

Categories follow use, not origin: a model is furniture or a vehicle, a texture set is wood or brick, an HDRI is
studio or night lighting. Each kind has an ordered list of ``(category, words)`` rules; an item takes the first rule
any of its words (title, tags, catalog categories) matches, else the kind's last category. The vocabulary is
generic (the words people tag such things with), the same for every item and source: no item is named here. A
source counts only as a fallback signal where the source itself says what the items are (museum collections are
history and art; ToonLab's procedural rocks are nature).

Standard library only.
"""

import re

RULES = {
    "model": (
        ("science", {"skull", "cranium", "mandible", "maxilla", "skeleton", "bone", "fossil", "specimen", "anatomy",
                     "tooth", "teeth", "vertebra", "femur", "humerus", "calcaneus", "talus", "pelvis", "shell",
                     "radius", "ulna", "tibia", "fibula", "cuneiform", "cuboid", "navicular", "scaphoid", "lunate",
                     "capitate", "hamate", "trapezium", "trapezoid", "pisiform", "triquetrum", "metatarsal",
                     "metacarpal", "phalanx", "scapula", "clavicle", "patella", "sternum", "coral", "mussel", "clam",
                     "bivalve", "snail", "skeletal", "insect", "beetle", "butterfly"}),
        ("vehicles", {"vehicle", "car", "truck", "bus", "ship", "boat", "plane", "airplane", "aircraft",
                      "helicopter", "bike", "bicycle", "motorcycle", "train", "tank", "spaceship", "rocket",
                      "tractor", "wagon"}),
        ("characters", {"character", "person", "people", "human", "animal", "creature", "monster", "robot", "dog",
                        "cat", "horse", "bird", "fish", "zombie", "knight"}),
        ("furniture", {"furniture", "chair", "table", "sofa", "couch", "bed", "shelf", "shelves", "cabinet", "desk",
                       "bench", "stool", "wardrobe", "drawer", "armchair", "dresser", "nightstand", "bookshelf"}),
        ("food", {"food", "fruit", "vegetable", "bread", "cake", "drink", "bottle", "cup", "mug", "plate",
                  "kitchen", "apple", "banana", "pizza", "burger"}),
        ("tools", {"tool", "tools", "weapon", "sword", "axe", "hammer", "gun", "rifle", "pistol", "knife",
                   "shield", "bow", "spear", "wrench", "saw", "shovel"}),
        ("architecture", {"architecture", "building", "house", "tower", "towers", "wall", "landmark", "bridge",
                          "modular", "door", "window", "fence", "ruins", "castle", "roof", "stairs", "column",
                          "arch", "gate", "pillar", "temple", "church", "village", "structural"}),
        ("nature", {"rock", "rocks", "stone", "cliff", "cliffs", "boulder", "formation", "formations", "tree",
                    "plant", "plants", "vegetation", "bush", "shrub", "grass", "flower", "mushroom", "log", "stump",
                    "nature", "leaf", "leaves", "fern", "cactus", "mountain"}),
        ("history", {"sculpture", "statue", "bust", "artifact", "artefact", "antique", "relic", "vase", "pottery",
                     "figurine", "coin", "mask", "museum", "ancient"}),
        ("props", set()),
    ),
    "texture": (
        ("fabric", {"fabric", "textile", "cloth", "leather", "carpet", "rug", "denim", "wool", "linen"}),
        ("metal", {"metal", "steel", "iron", "rust", "rusty", "copper", "aluminium", "aluminum", "brass", "chrome"}),
        ("wood", {"wood", "wooden", "planks", "plank", "bark", "veneer", "plywood", "timber", "parquet"}),
        ("brick", {"brick", "bricks", "brickwork"}),
        ("tiles", {"tiles", "tile", "tiled", "mosaic", "ceramic"}),
        ("paving", {"asphalt", "pavement", "road", "paving", "cobblestone", "sidewalk", "pathway", "path"}),
        ("concrete", {"concrete", "plaster", "cement", "stucco", "plaster-concrete"}),
        ("ground", {"ground", "dirt", "soil", "sand", "gravel", "mud", "grass", "moss", "terrain", "snow",
                    "leaves", "forest", "aerial"}),
        ("stone", {"stone", "stones", "rock", "rocks", "cliff", "marble", "granite", "slate", "stonework"}),
        ("other", set()),
    ),
    "hdri": (
        ("studio", {"studio", "softbox", "infinity"}),
        ("night", {"night", "stars", "moon"}),
        ("sunset", {"sunrise-sunset", "sunset", "sunrise", "dusk", "dawn"}),
        ("indoor", {"indoor", "interior", "room", "hall", "warehouse", "workshop"}),
        ("skies", {"skies", "pure skies", "sky"}),
        ("urban", {"urban", "city", "street", "town"}),
        ("nature", set()),
    ),
    "vfx": (
        ("fire", {"fire", "flame", "explosion", "blast", "burning", "burn"}),
        ("smoke", {"smoke", "dust", "cloud", "fog", "puff", "gas", "steam"}),
        ("magic", {"magic", "spell", "energy", "electric", "portal", "rune", "glow", "sparkle", "star", "light"}),
        ("impacts", {"impact", "muzzle", "bullet", "tracer", "slash", "gun", "spark", "debris", "decal"}),
        ("other", set()),
    ),
}
SOURCE_FALLBACK = {("model", "smithsonian"): "history", ("model", "met"): "history",
                   ("model", "toonlab-rock"): "nature", ("model", "plateau"): "architecture"}
NATURAL_HISTORY = frozenset({"smithsonian"})   # Its open-access 3D is mostly natural-history specimens.
_WORD = re.compile(r"[a-z0-9][a-z0-9-]*")
_BINOMIAL = re.compile(r"^[A-Z][a-z]+ [a-z]{3,}\b")   # "Genus species": a museum's natural-history specimen.


def words(item):
    """Every lower-case word and whole phrase of the item's title, tags and catalog categories."""
    found = set()
    for text in [item.get("title") or ""] + list(item.get("tags") or ()) + list(item.get("categories") or ()):
        text = str(text).lower()
        found.add(text.strip())
        for word in _WORD.findall(text):
            found.add(word)
            found.add(word.rstrip("0123456789"))   # "cuneiform2" is a cuneiform.
    return found


def categorize(item):
    """The item's one category within its kind."""
    rules = RULES[item["kind"]]
    found = words(item)
    if item["kind"] == "model" and item.get("source") == "toonlab-rock":
        return "nature"   # Procedural rocks: their words (fragment, slab) describe shape, not use.
    for category, vocabulary in rules[:-1]:
        if found & vocabulary:
            return category
    if item["kind"] == "model" and item.get("source") in NATURAL_HISTORY and _BINOMIAL.match(item.get("title") or ""):
        return "science"
    return SOURCE_FALLBACK.get((item["kind"], item.get("source")), rules[-1][0])


def ids(kind):
    return [category for category, _words in RULES[kind]]
