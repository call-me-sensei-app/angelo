# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The tension map card (1.0.9): a corner card (``card_host``, non-modal) over the 3D view, so orbiting, posing and
playback go on while it is open.

It shows the measured meshes, the overlay switch, the rest shape (Original, a Shape key, or Captured with Capture
rest shape), the strain shown at full red and full blue, and the overlay's opacity. Each choice is applied at once
by its ``source`` (``tension_map.Live``: scene changes one Undo step each, the overlay settings the 3D view's, like
Blender's own overlays); the card only reads a ``tension_map_logic.Snapshot`` and draws it. Remove takes the
modifiers off and hides the map; Done only closes the card.
"""

from . import card_kit as kit, tension_map_logic as logic
from .ui_text import iface_, language, n_

KIND = "tension_map"
TITLE = n_("Tension map")
MEASURING = n_("Measuring {names}")
NO_MESH = n_("Select a mesh to measure its tension")
SHOW = n_("Show on the 3D view")
SHOW_CAPTION = n_("Blue is compressed, red is stretched")
REST = n_("Rest shape")
SHAPE_KEY = n_("Shape key")
NO_SHAPE_KEYS = n_("This mesh has no shape keys")
CAPTURE = n_("Capture rest shape")
STRETCH = n_("Full red at stretch")
COMPRESS = n_("Full blue at compression")
OPACITY = n_("Opacity")
MATERIAL_HINT = n_("Materials read it with an Attribute node named \"{name}\" (EEVEE and Cycles).")
REMOVE, DONE = n_("Remove"), n_("Done")
WIDTH = 380
INFO_KIND = "info"   # ui_kit_status.NOTICE_KINDS
SHOWN_NAMES = 3
RANGES = (("stretch", STRETCH, logic.STRETCH_STEPS), ("compress", COMPRESS, logic.COMPRESS_STEPS),
          ("opacity", OPACITY, logic.OPACITY_STEPS))


class TensionCard(kit.BaseCard):
    kind, modal, priority, cancel_action = KIND, False, 20, "done"

    def __init__(self, source):
        """``source``: ``snapshot()`` reads what the card shows; ``apply(action, value)`` changes it."""
        self.source = source
        self.picking = False   # The shape key list is open.

    def version(self):
        return (self.source.snapshot(), self.picking, language())

    # -- layout --------------------------------------------------------------------------------------------------
    def layout(self, view, region_w, region_h, scale, ui):
        from .update_card import corner_plan
        state = self.source.snapshot()
        return kit.fit(lambda size: corner_plan(self._build(view, size, region_w, ui, state), region_w, region_h),
                       region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui, state):
        b = kit.Builder(view, scale, min(WIDTH * scale, region_w - 40 * scale), ui, padding=20)
        b.heading(iface_(TITLE), 17)
        b.paragraph(measuring(state), 12.5, kit.MUTED)
        b.gap(12)
        b.toggle("show", None, iface_(SHOW), state.shown, caption=iface_(SHOW_CAPTION))
        rest = logic.rest_choice(state.rest_sources)
        b.gap(12)
        _label(b, REST)
        b.segmented("rest", [(value, iface_(label)) for value, label in logic.REST_SOURCES], rest, width=b.inner,
                    enabled=state.measuring)
        if rest:
            b.gap(6)
            b.paragraph(iface_(logic.REST_HELP[rest]), 12, kit.MUTED)
        if rest == "SHAPE_KEY":
            b.gap(6)
            if state.shape_keys:
                b.select("key_open", None, [(name, name) for name in state.shape_keys], state.shape_key,
                         open=self.picking, label=iface_(SHAPE_KEY), option_action="key_pick")
            else:
                b.notice(INFO_KIND, iface_(NO_SHAPE_KEYS))
        elif rest == "ATTRIBUTE":
            b.gap(8)
            b.buttons([("capture", None, iface_(CAPTURE), "secondary", True)], align="left")
        if state.values is not None:
            for (action, label, steps), value in zip(RANGES, state.values):
                b.gap(12)
                _label(b, label)
                b.segmented(action, logic.segments(steps), logic.chosen_step(value, steps), width=b.inner)
        if state.measuring:
            b.gap(12)
            b.paragraph(iface_(MATERIAL_HINT, name=state.attribute), 12, kit.MUTED)
        b.gap(14)
        b.buttons([("done", None, iface_(DONE), "primary", True)],
                  left=[("remove", None, iface_(REMOVE), "destructive", state.measuring)])
        b.close_button("done")
        return b

    # -- input ---------------------------------------------------------------------------------------------------
    def act(self, action, value, host):
        if action == "key_open":
            self.picking = not self.picking
        elif action == "key_pick":
            self.picking = False
            self.source.apply("shape_key", value)
        elif self.picking and action in {"done", "close", "dismiss"}:
            self.picking = False   # Escape or a click beside the list closes the list first.
        elif action in {"show", "rest", "capture", "stretch", "compress", "opacity"}:
            self.source.apply(action, value)
        elif action == "remove":
            self.source.apply("remove", None)
            host.close("done")
            return
        elif action in {"done", "close", "dismiss"}:
            host.close("done")
            return
        host.redraw()


def measuring(state):
    names = state.names if state.measuring else ()
    if not names:
        return iface_(NO_MESH)
    shown = list(names[:SHOWN_NAMES]) + (["…"] if len(names) > SHOWN_NAMES else [])
    return iface_(MEASURING, names=", ".join(shown))   # i18n: joins object names (data), no words of its own


def _label(b, label):
    b.paragraph(iface_(label), 12.5, x=b.pad + 2 * b.s)
    b.gap(4)
