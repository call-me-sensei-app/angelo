# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The names a person sees for the shipped biome presets and their layers, marked for translation (``n_``).

The preset files (``assets/biomes/*.angelo-biome.json``) keep their English names as data: the agent, the scatter
system's node frames and a saved biome read them. The Scatter card shows these labels instead, translated at display
time. ``test_native_scatter_card`` fails when a preset or one of its layers has no label here, so a new preset never
reaches the card untranslated.
"""

from .ui_text import n_

BIOME_LABELS = {
    "conifer-slopes": (n_("Conifer slopes"),
                       n_("Pines and young pines on moderate slopes, rocks where it is steep, ferns in the understory.")),
    "desert-scrub": (n_("Desert scrub"),
                     n_("Scattered shrubs and cacti far apart, stones and dry grass on flat and gently sloping ground.")),
    "dune-grass": (n_("Dune grass"), n_("Beach grass in clumps above the shoreline, driftwood and shells lower down.")),
    "meadow": (n_("Meadow"), n_("Grass clumps with flowers in drifts and the odd stone, on gentle ground.")),
    "mixed-temperate-forest": (n_("Mixed temperate forest"),
                               n_("Broadleaf and conifer canopy, shrubs and an undergrowth of ferns and grass.")),
    "rocky-scree": (n_("Rocky scree"), n_("Boulders and loose stones on steep ground, sparse tough grass in between.")),
}

LAYER_LABELS = {
    "Pines": n_("Pines"), "Young pines": n_("Young pines"), "Rocks": n_("Rocks"), "Ferns": n_("Ferns"),
    "Cacti": n_("Cacti"), "Shrubs": n_("Shrubs"), "Stones": n_("Stones"), "Dry grass": n_("Dry grass"),
    "Driftwood": n_("Driftwood"), "Beach grass": n_("Beach grass"), "Shells": n_("Shells"), "Grass": n_("Grass"),
    "Flowers": n_("Flowers"), "Broadleaf trees": n_("Broadleaf trees"), "Conifers": n_("Conifers"),
    "Undergrowth": n_("Undergrowth"), "Fallen wood": n_("Fallen wood"), "Boulders": n_("Boulders"),
    "Tough grass": n_("Tough grass"),
}
