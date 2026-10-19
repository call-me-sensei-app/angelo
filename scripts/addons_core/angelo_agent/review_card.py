# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Review & Accept for a native preview, as a centred Angelo card (``card_host``) instead of a stock dialog.

The card only presents and collects the decision. It reads the result in plain words (``review_text``:
added, changed and removed objects, object counts, every check) with the exact submitted settings one click
away in a mono box; the operator's own review lines stay the exact text they were. ``ANGELO_OT_accept_native_preview`` (agent_workspace_ui)
still computes the exact review in ``invoke``, stays running while the card shows, and runs its unchanged
``execute`` with the person's choice: the same confirmation property, the same review fingerprint, the same
document and staleness checks, the same acceptance. Accept with the box unticked is refused by ``execute``,
as the dialog's confirm was. Escape, Cancel and the close button cancel; nothing is applied.
"""

from . import card_kit as kit
from .ui_text import iface_, language, n_

TITLE = n_("Accept this preview?")
LEAD = n_("Nothing in your scene changes until you accept. Accept applies the preview as one Undo step, after "
          "checking that your scene has not changed since.")
CONFIRM = n_("I reviewed this exact result, its settings and its checks")
WHAT = n_("What this preview does")
SHOW_EXACT, HIDE_EXACT = n_("Show the exact settings"), n_("Hide the exact settings")
ACCEPT, CANCEL = n_("Accept"), n_("Cancel")
WIDTH, LIST_HEIGHT, EXACT_HEIGHT = 620, 220, 200


class ReviewAcceptCard(kit.BaseCard):
    kind, modal, priority = "native_review", True, 80
    cancel_action, default_action, outside_dismisses = "cancel", "accept", False

    def __init__(self, descriptor, confirmed, set_confirmed):
        from . import review_text
        self.descriptor, self.confirmed, self._set_confirmed = descriptor, bool(confirmed), set_confirmed
        self.lines = list(review_text.exact_settings(descriptor))   # The exact settings, for the mono box.
        self.exact = False      # The mono box opens on request.
        self.decision = None    # "accept" or "cancel", read by the waiting operator.

    def version(self):
        return self.confirmed, self.exact, language()   # A language switch lays the card out again in that language.

    def layout(self, view, region_w, region_h, scale, ui):
        def plan(size):
            builder = self._build(view, size, region_w, ui)
            top = min((region_h + builder.height()) / 2, region_h - 10 * builder.s)
            return builder.place((region_w - builder.width) / 2, top, True, (region_w, region_h))
        return kit.fit(plan, region_w, region_h, scale)

    def _build(self, view, scale, region_w, ui):
        width = min(WIDTH * scale, region_w - 40 * scale)
        b = kit.Builder(view, scale, width, ui, padding=26)
        b.heading(iface_(TITLE), 20)
        b.gap(6)
        b.paragraph(iface_(LEAD), 13.5, kit.MUTED)
        b.gap(14)
        from . import review_text  # Readable words for the result (display only); the exact settings stay mono.
        b.paragraph(iface_(WHAT), 12, kit.MUTED)
        b.gap(4)
        b.scrollbox("review", review_text.sections(self.descriptor), LIST_HEIGHT, size=13, mono=False)
        b.gap(10)
        b.disclosure("exact", iface_(HIDE_EXACT if self.exact else SHOW_EXACT), self.exact, size=12.5)
        if self.exact:
            b.gap(4)
            b.scrollbox("exact", "\n".join(self.lines), EXACT_HEIGHT, size=11.5, mono=True)
        b.gap(14)
        b.checkbox("confirm", None, iface_(CONFIRM), self.confirmed)
        b.gap(18)
        b.buttons([("cancel", None, iface_(CANCEL), "secondary", True), ("accept", None, iface_(ACCEPT), "primary", True)])
        b.close_button("cancel")
        return b

    def act(self, action, value, host):
        if action == "exact":
            self.exact = not self.exact
        elif action == "confirm":
            self.confirmed = not self.confirmed
            self._set_confirmed(self.confirmed)
        elif action in {"accept", "cancel", "dismiss", "close"}:
            self.decision = "accept" if action == "accept" else "cancel"
            host.close("done")

    def closed(self, how):
        if self.decision is None:
            self.decision = "cancel"


def start(operator, context, descriptor):
    """Show the card for ``operator`` (the accept operator, after its ``invoke`` prepared the exact review) and keep
    the operator running until the person decides (``wait``)."""
    from . import card_host

    def set_confirmed(value):
        try:
            operator.review_confirmed = value
        except ReferenceError:   # The operator already ended.
            pass
    card = ReviewAcceptCard(descriptor, operator.review_confirmed, set_confirmed)
    operator._review_card = card
    card_host.request(card)
    card_host.pump(lambda: True)
    operator._review_timer = context.window_manager.event_timer_add(0.05, window=context.window)
    context.window_manager.modal_handler_add(operator)
    return {'RUNNING_MODAL'}


def wait(operator, context, event):
    """The operator's ``modal``: Return accepts and Escape cancels (as the dialog's keys did), wherever the pointer
    is; the card's own buttons decide too. The decision runs the operator's unchanged ``execute``."""
    from . import card_host
    card = operator._review_card
    if card.decision is None and event.value == 'PRESS' and event.type in {'RET', 'NUMPAD_ENTER', 'ESC'}:
        card.decision = "cancel" if event.type == 'ESC' else "accept"
    if card.decision is None:
        return {'PASS_THROUGH'}
    context.window_manager.event_timer_remove(operator._review_timer)
    if card_host.active_card() is card:
        card_host.close("done")
    card_host._queue[:] = [item for item in card_host._queue if item[2] is not card]   # Never shown later.
    return operator.execute(context) if card.decision == "accept" else {'CANCELLED'}
