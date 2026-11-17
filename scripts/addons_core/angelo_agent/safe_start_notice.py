# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The notices after a start that did not finish or add-ons that failed: which add-ons this launch left off (the one
it died loading first) and how to turn one back on; which ones failed as they loaded, why, and a **Turn off** for each.

``angelo_safe_start`` (scripts/modules) leaves third-party add-ons off before any loads when the last start never
completed, and calls ``show`` and ``show_failures`` once this start has completed (doc/angelo/app_identity.rst, "A
start that did not finish"). Each notice is its own card kind, so an update's notice queued beside it does not replace
it (``card_host.request`` keeps one card per kind); it stays until closed. Names are read as Blender lists them
(``bl_info`` or an extension's manifest, read as text), never by loading the add-on. A failure's cause is program text
(the exception's first line, folders shortened to a file's name), shown as is.

Main thread only.
"""

from . import card_kit as kit
from .ui_formats import format_list
from .ui_text import iface_, language, n_
from .update_card import NoticeCard

TITLE = n_("Angelo started without some add-ons")
BODY = n_("The last start did not finish, so Angelo opened with only the add-ons that come with it. Left off: "
          "{names}. To turn one back on, enable it in Preferences > Add-ons. If Angelo then cannot start, it "
          "opens without it again.")
# The last start died while it enabled one of them (angelo_start_records.Held.note_loading): that one leads. Something
# else (an earlier add-on's thread or timer) may have ended it while this one loaded, hence "while", not "because".
BODY_CULPRIT = n_("Angelo stopped while {culprit} was loading, so Angelo opened with only the add-ons that come with "
                  "it. Left off: {names}. To turn one back on, enable it in Preferences > Add-ons. If Angelo then "
                  "cannot start, it opens without it again.")
# Add-ons that raised as they loaded (angelo_addon_failures): still enabled, one row each with its cause and Turn off.
FAILED_TITLE = n_("Some add-ons could not load")
FAILED_INTRO = n_("These add-ons failed as they loaded. They stay on, so Angelo tries them again at each start: turn "
                  "one off until it supports this version of Blender. Anything one set up before it failed stays "
                  "until Angelo restarts without it.")
TURN_OFF = n_("Turn off")
TURNED_OFF = n_("Turned off")
MORE = n_("And {count} more. The console names each one and why.")
MOST_ROWS = 5   # A base change can break dozens: the card names this many, then how many more.

shown = None   # (title, body) of the notice this launch showed, for its fixtures.
shown_failures = None   # (title, [(module, name, cause)], more) of the failed add-ons' notice.


def display_names(modules):
    """``modules`` as the person knows them: each add-on's own name, else its module name."""
    import addon_utils
    try:
        known = {module.__name__: module for module in addon_utils.modules(refresh=False)}
    except Exception:   # A name is a convenience: the module name stands.
        known = {}
    names = []
    for module in modules:
        found = known.get(module)
        name = addon_utils.module_bl_info(found).get("name") if found is not None else None
        names.append(name or module)
    return names


def text(modules, culprit=None):
    """``(title, body)`` for the add-ons left off; the one the last start died loading, when known, leads."""
    if culprit in modules:
        rest = [module for module in modules if module != culprit]
        return iface_(TITLE), iface_(BODY_CULPRIT, culprit=display_names([culprit])[0],
                                     names=format_list(display_names(rest)))
    return iface_(TITLE), iface_(BODY, names=format_list(display_names(modules)))


def failure_rows(failures):
    """``([(module, name, cause)], more)``: the first ``MOST_ROWS`` failures as the card lists them, and how many
    more there are."""
    shown_now = failures[:MOST_ROWS]
    names = display_names([failure.module for failure in shown_now])
    return [(f.module, name, f.cause) for f, name in zip(shown_now, names)], len(failures) - len(shown_now)


class FailuresCard(NoticeCard):
    """The failed add-ons' corner notice: an introduction, then one row per add-on (its name, its cause and **Turn
    off**), then how many more. ``turn_off(module)`` acts; a row then says it is off."""

    kind = "addon_failures"

    def __init__(self, rows, more, turn_off):
        super().__init__("alert", iface_(FAILED_TITLE), iface_(FAILED_INTRO))
        self.rows, self.more, self.turn_off, self.off = rows, more, turn_off, set()

    def version(self):
        return tuple(sorted(self.off)), language()

    def _build(self, view, scale, region_w, ui):
        from .update_card import WIDTH
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=22)
        icon = 40 * scale
        b.icon(self.icon, b.pad, b.y, 40)
        text_x = b.pad + icon + 14 * scale
        text_w = width - text_x - b.pad - 20 * scale
        top = b.y
        b.paragraph(self.title, 17, kit.TEXT, x=text_x, width=text_w, leading=1.3)
        b.paragraph(self.body, 13, kit.MUTED, x=text_x, width=text_w, leading=1.45)
        b.y = max(b.y, top + icon)
        for module, name, cause in self.rows:
            b.gap(12)
            b.rule()
            b.gap(10)
            off = module in self.off
            label = iface_(TURNED_OFF if off else TURN_OFF)
            button_w = b.measure(label, 13 * scale) + 28 * scale
            row = b.y
            b._button((("turn_off", module, label, "secondary", not off), button_w),
                      b.pad + b.inner - button_w, 13, 30 * scale)
            b.y = row
            text_width = b.inner - button_w - 12 * scale
            b.paragraph(name, 13.5, kit.TEXT, width=text_width, leading=1.35)
            b.paragraph(cause, 11.5, kit.MUTED, width=text_width, leading=1.4, mono=True)
            b.y = max(b.y, row + 30 * scale)
        if self.more:
            b.gap(12)
            b.paragraph(iface_(MORE, count=self.more), 13, kit.MUTED, leading=1.45)
        b.close_button("close")
        return b

    def act(self, action, value, host):
        if action == "turn_off" and value and value not in self.off:
            try:
                self.turn_off(value)
                self.off.add(value)
            except Exception as error:   # The row stays offered; the console says why.
                print("Angelo: could not turn off add-on %s: %s" % (value, error))
            host.redraw()
            return
        super().act(action, value, host)


def _card(kind, title, body):
    from . import card_host

    class SafeStartCard(NoticeCard):
        pass
    SafeStartCard.kind = kind   # One card per kind: the left-off notice and the failures one never replace each other.
    card_host.request(SafeStartCard("alert", title, body, lifetime=0))
    card_host.pump(lambda: True)


def show(modules, culprit=None):
    global shown
    if not modules:
        return
    title, body = text(modules, culprit)
    shown = (title, body)
    _card("safe_start", title, body)


def show_failures(failures):
    """One card naming each add-on that failed as it loaded, with its cause and a **Turn off**."""
    global shown_failures
    if not failures:
        return
    from . import card_host
    import angelo_addon_failures
    rows, more = failure_rows(failures)
    shown_failures = (iface_(FAILED_TITLE), rows, more)
    card_host.request(FailuresCard(rows, more, angelo_addon_failures.turn_off))
    card_host.pump(lambda: True)
