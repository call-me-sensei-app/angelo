# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update card's add-on section: which of the person's add-ons are not yet known to work on the version the
update installs, and what add-on safe start does if one of them stops Angelo as it starts.

The verdicts come from ``addon_compat`` (doc/angelo/updates_and_reports.rst, "Your add-ons and the update"); this
module only lays them out, translated at layout time (``ui_text``). Add-on names and a failure's cause are the
add-on's own text and are never translated. The list opens and closes in the card (**Show add-ons**), so the card
stays one corner card.
"""

from . import addon_compat as compat, app_version, card_kit as kit
from .ui_text import iface_, n_

ALL_KNOWN = n_("All {count} of your add-ons are known to work on Angelo {version}.")
NOT_KNOWN = n_("Not yet known to work on Angelo {version}: {names}.")
NOT_KNOWN_MORE = n_("Not yet known to work on Angelo {version}: {names} and {more} more.")
SAFE_START = n_("If one stops Angelo as it starts, the next start opens without your add-ons and names the one that "
                "stopped it. Turn it back on in Preferences > Add-ons once it has been updated.")
SHOW, HIDE = n_("Show add-ons"), n_("Hide add-ons")
FAILING = n_("Fails to load now: {cause}")
LIST_MORE = n_("And {more} more. Preferences > Add-ons lists them all.")
# One sentence per ``Verdict.why`` (``addon_compat.judge``).
# app-name: Blender versions an add-on declares, and the Blender version each Angelo release is built on
ADDON_REASONS = {
    "too_new": n_("Made for Blender versions before {bound}. Angelo {version} uses Blender {target}."),
    "too_old": n_("Needs Blender {bound} or newer. Angelo {version} uses Blender {target}."),
    "platform": n_("Its author has not made it for this computer ({bound})."),
    "needs_update": n_("Version {bound} works with Blender {target}. Update it in Preferences > Get Extensions "
                       "before you update Angelo."),
    "newer": n_("Version {bound} is on the Extensions platform. Update it before you update Angelo."),
    "open_range": n_("Made for Blender {bound} and later. Its author has not said it works with Blender {target} "
                     "yet."),
    "no_range": n_("Its author has not said it works with Blender {target} yet."),
    "not_loaded": n_("It is turned on but did not load in this session."),
    "base_unknown": n_("Angelo could not tell which Blender version Angelo {version} uses."),
}
MAX_ROWS = 6   # Rows in the open list; the rest are counted, so the card always fits its corner.


def reason(verdict, version, target):
    """The sentence that says why ``verdict``'s add-on is not known to work."""
    if verdict.kind == compat.FAILING:
        return iface_(FAILING, cause=verdict.why)
    template = ADDON_REASONS.get(verdict.why, ADDON_REASONS["no_range"])
    return iface_(template, bound=verdict.detail, version=version, target=compat.version_text(target or ()))


def section(b, summary, version, expanded):
    """Draws the section into builder ``b`` for an update to Angelo ``version`` (its release version text).
    Nothing for a person with no add-ons of their own (or no verdicts yet)."""
    if summary is None or not summary.verdicts:
        return
    shown = app_version.display(version)
    attention = summary.attention
    if not attention:
        b.paragraph(iface_(ALL_KNOWN, count=summary.known, version=shown), 12.5, kit.MUTED, leading=1.45)
        b.gap(8)
        return
    names, more = summary.named()
    line = iface_(NOT_KNOWN_MORE if more else NOT_KNOWN, version=shown, names=", ".join(names), more=more)
    b.paragraph(line, 13, kit.TEXT, leading=1.4)
    b.paragraph(iface_(SAFE_START), 12.5, kit.MUTED, leading=1.45)
    if expanded:
        b.gap(6)
        for verdict in attention[:MAX_ROWS]:
            b.paragraph(verdict.addon.name, 12.5, kit.TEXT, leading=1.3)
            b.paragraph(reason(verdict, shown, summary.target), 12, kit.MUTED, leading=1.4)
            b.gap(3)
        if len(attention) > MAX_ROWS:
            b.paragraph(iface_(LIST_MORE, more=len(attention) - MAX_ROWS), 12, kit.MUTED, leading=1.4)
    b.gap(2)
    b.disclosure("addons", iface_(HIDE if expanded else SHOW), expanded, size=12.5)
    b.gap(8)
