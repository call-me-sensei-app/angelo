# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A newer major line is offered exactly as a newer minor line is: 1.0.11 sees 2.0.0 the way it would have seen 1.1.0.

The release that was 1.1.0 ships as 2.0.0 (the maintainer, 2026-11-13: "for the sake of trust, we should just label it
v2.0.0"). update_feed compares releases and lines as numbers, so nothing may treat a new major as anything but a newer
line: the person's choice, offered beside their own line's updates, recommended when the feed says so, and an
ordinary update only when the feed makes it ``latest``. Each case is run with the newer line as 1.1.0 and as 2.0.0 and
must come out the same; a 2.0.0 build then updates along its own line. Contract: doc/angelo/updates_and_reports.rst,
"Release lines".
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import update_fixtures as fx  # noqa: E402
from test_native_update_lines import MAC, NOW, release  # noqa: E402
from angelo_agent import update_feed  # noqa: E402

RUNNING = "1.0.11"


def feed(latest, lines):
    document = fx.payload(version=latest, latest=release(latest),
                          lines=[release(v) if isinstance(v, str) else v for v in lines])
    return update_feed.parse_payload(json.dumps(document).encode(), NOW)


def shape(offer, newer):
    """What the person is offered, with the newer line's version written as NEWER."""
    def name(version):
        return "NEWER" if version == newer else version
    if offer is None:
        return None
    later = offer.later and (name(offer.later.release.version), offer.later.choice, offer.later.release.recommended)
    return (name(offer.release.version), offer.choice, offer.critical, offer.release.recommended, later)


class NewerMajorIsANewerLine(unittest.TestCase):
    def same_for_both(self, build):
        """``build(newer)`` -> an offer; it must not depend on whether the newer line is 1.1.0 or 2.0.0."""
        minor, major = shape(build("1.1.0"), "1.1.0"), shape(build("2.0.0"), "2.0.0")
        self.assertEqual(major, minor)
        return major

    def test_a_newer_line_is_the_persons_choice(self):
        got = self.same_for_both(lambda newer: update_feed.offer_for(feed(RUNNING, (newer,)), RUNNING, MAC))
        self.assertEqual(got, ("NEWER", True, False, False, None))

    def test_a_recommended_newer_line_is_offered_as_recommended(self):
        got = self.same_for_both(lambda newer: update_feed.offer_for(
            feed(RUNNING, (release(newer, recommended=True),)), RUNNING, MAC))
        self.assertEqual(got, ("NEWER", True, False, True, None))

    def test_latest_is_an_ordinary_update_only_when_the_feed_makes_it_so(self):
        got = self.same_for_both(lambda newer: update_feed.offer_for(feed(newer, ()), RUNNING, MAC))
        self.assertEqual(got, ("NEWER", False, False, False, None))

    def test_the_own_lines_update_comes_with_the_newer_line_beside_it(self):
        got = self.same_for_both(lambda newer: update_feed.offer_for(feed("1.0.12", (newer,)), RUNNING, MAC))
        self.assertEqual(got, ("1.0.12", False, False, False, ("NEWER", True, False)))

    def test_a_yes_to_the_line_is_honoured(self):
        def offer(newer):
            line = update_feed.line_of(newer)
            return update_feed.offer_for(feed(RUNNING, (newer, "3.1.0")), RUNNING, MAC, line)
        self.assertEqual(self.same_for_both(offer)[0], "NEWER")

    def test_a_critical_flag_never_turns_the_choice_into_a_demand(self):
        got = self.same_for_both(lambda newer: update_feed.offer_for(
            feed(RUNNING, (release(newer, critical=True),)), RUNNING, MAC))
        self.assertEqual(got[1:3], (True, False))

    def test_the_lines_are_numbers_not_strings(self):
        self.assertGreater(update_feed.line_key("2.0"), update_feed.line_key("1.10"))
        self.assertEqual(update_feed.line_of("2.0.0"), "2.0")


class TwoPointZeroUpdatesAlongItsLine(unittest.TestCase):
    def test_its_own_later_release_is_an_ordinary_update_and_1_0_never_goes_back(self):
        offer = update_feed.offer_for(feed("1.0.12", ("2.0.1",)), "2.0.0", MAC)
        self.assertEqual((offer.release.version, offer.choice, offer.later), ("2.0.1", False, None))
        self.assertIsNone(update_feed.offer_for(feed("1.0.12", ("2.0.0",)), "2.0.0", MAC))


if __name__ == "__main__":
    unittest.main()
