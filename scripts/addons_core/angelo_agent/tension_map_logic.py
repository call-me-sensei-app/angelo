# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tension map's choices and wording, without Blender (``tension_map`` applies them, ``tension_map_card``
draws them).

The native Tension modifier writes each vertex's strain (``length / rest - 1``: compression below zero, stretch
above) to a float attribute; the viewport overlay colours it blue to red. The card's range controls choose the
strain shown at full colour, in steps a person can read at a glance (percent of the rest length).
"""

from dataclasses import dataclass

from .ui_text import n_

ATTRIBUTE = "tension"               # The modifier's default vertex attribute: what a material's Attribute node reads.
REST_ATTRIBUTE = "tension_rest"     # Captured rest positions (the Captured rest shape).
MODIFIER_NAME = "Tension"

# (value, label) in the card's segmented controls. A value set elsewhere (the Overlays popover, a script) shows
# with no segment chosen, never snapped to one.
STRETCH_STEPS = (0.05, 0.1, 0.25, 0.5, 1.0)
COMPRESS_STEPS = (0.05, 0.1, 0.25, 0.5)
OPACITY_STEPS = (0.4, 0.7, 0.85, 1.0)

REST_SOURCES = (("ORIGINAL", n_("Original")), ("SHAPE_KEY", n_("Shape key")), ("ATTRIBUTE", n_("Captured")))
REST_HELP = {
    "ORIGINAL": n_("Measured against the mesh before shape keys and modifiers: posing, cloth and deformers show."),
    "SHAPE_KEY": n_("Measured against the shape key below: animate the other keys to see them stretch."),
    "ATTRIBUTE": n_("Measured against the shape captured with Capture rest shape: sculpt or edit, then compare."),
}


def percent(value):
    """``0.25`` -> ``"25%"``; a fraction of a percent keeps one decimal."""
    scaled = value * 100.0
    return ("%d%%" % round(scaled)) if abs(scaled - round(scaled)) < 0.05 else ("%.1f%%" % scaled)


def segments(steps):
    return tuple((value, percent(value)) for value in steps)


def chosen_step(value, steps):
    """The step ``value`` matches (within float noise), or None."""
    return next((step for step in steps if abs(step - value) <= 1e-4 * max(1.0, step)), None)


def rest_choice(sources):
    """One rest source for several objects' modifiers: theirs when they agree, else None (no segment chosen)."""
    found = set(sources)
    return found.pop() if len(found) == 1 else None


def target_names(objects):
    """The meshes a click measures: the active object first, then the other selected ones, each once; only local
    meshes (a linked object's modifiers cannot be added to). ``objects``: (name, type, is_local, is_active)."""
    meshes = [(name, active) for name, kind, local, active in objects if kind == 'MESH' and local]
    ordered = [name for name, active in meshes if active] + [name for name, active in meshes if not active]
    return list(dict.fromkeys(ordered))


@dataclass(frozen=True)
class Snapshot:
    """What the card shows, read from the scene and the 3D view (``tension_map.Live.snapshot``)."""

    names: tuple = ()           # The measured meshes, in order.
    rest_sources: tuple = ()    # Each measured mesh's modifier rest source.
    shape_key: str = ""         # The first mesh's rest shape key.
    shape_keys: tuple = ()      # The first mesh's shape keys.
    attribute: str = ATTRIBUTE  # The first mesh's vertex attribute (what a material reads).
    shown: bool = False         # The overlay is on in this window's 3D views.
    values: tuple = None        # (stretch, compress, opacity) of the overlay, None without a 3D view.

    @property
    def measuring(self):
        return bool(self.rest_sources)
