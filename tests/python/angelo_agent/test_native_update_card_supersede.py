# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The update card always shows the latest check's offer (update_ui.show_notice), on a stand-in bpy.

A card that a more important one displaced comes back from the queue (card_host's requeue) and is visible again
before a newer check answers. That check's card used to wait behind it, as one of its own kind: the person saw an
older offer, and only after closing it the newer one, or the same offer twice. Found by
``test_blender_update_addons`` (2026-11-20): the safe-start card displaced the 2.1.1 card, which came back while
Check for Updates found 2.2.0. The newest result replaces a visible update card with a different offer; an update
card already showing that offer is not queued again.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import importlib
import unittest
from types import SimpleNamespace

from test_native_app_notices import NoticesCase, update_check  # noqa: E402


class SupersedeTests(NoticesCase):
    extra = {"director_layout": SimpleNamespace(is_director=lambda window: True)}

    def visible(self, offer):
        """``offer``'s update card on screen, as card_host shows a card from its queue."""
        host = self.n.card_host
        update_card = importlib.import_module("angelo_agent.update_card")   # The stand-in bpy's own import.
        card = update_card.UpdateCard(offer, "1.0.0", lambda *args: None)
        host._active = host.HostState(card, 0, 0, object())
        self.addCleanup(setattr, host, "_active", None)
        return card

    def test_a_newer_offer_replaces_the_visible_card_of_an_earlier_check(self):
        old, new = self.offer(version="1.0.1"), self.offer(version="1.1.0")
        self.visible(old)
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, new), manual=True)
        self.assertIsNone(self.n.card_host.active_card(), "the earlier offer's card is closed")
        self.assertEqual([card.offer.release.version for card in self.queued("update")], ["1.1.0"])

    def test_the_offer_already_on_screen_is_not_queued_again(self):
        offer = self.offer(version="1.0.1")
        card = self.visible(offer)
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer), manual=True)
        self.assertIs(self.n.card_host.active_card(), card, "the card the person reads stays")
        self.assertEqual(self.queued("update"), [], "it would show again after the person closes it")

    def test_another_kind_on_screen_is_left_alone(self):
        # More important than an update card, so the update waits (card_host.request's own rule).
        other = SimpleNamespace(kind="crash", modal=False, priority=50, pictures=(), closed=lambda how: None)
        host = self.n.card_host
        host._active = host.HostState(other, 0, 0, object())
        self.addCleanup(setattr, host, "_active", None)
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, self.offer(version="1.1.0")), manual=True)
        self.assertIs(host.active_card(), other)
        self.assertEqual([card.offer.release.version for card in self.queued("update")], ["1.1.0"])


if __name__ == "__main__":
    unittest.main()
