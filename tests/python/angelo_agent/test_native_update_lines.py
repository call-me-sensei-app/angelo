# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Release lines in the update feed: a newer line is the person's choice, and their own line keeps updating.

Contract: doc/angelo/updates_and_reports.rst, "Release lines". Builds before 1.0.9 read only ``latest``; that a real
shipped build does so is proved by update_lines_shipped_client.py, run against the published apps at release time.
"""

import base64
import dataclasses
import io
import json
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_native_update_cards as cards  # noqa: E402
import test_native_update_ready as ready  # noqa: E402
import update_fixtures as fx  # noqa: E402
from angelo_agent import card_kit as kit, update_card, update_check, update_feed, update_state  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402

sys.path.insert(0, str(HERE.parents[2] / "scripts/angelo"))
import sign_update_feed as tool  # noqa: E402

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)
MAC = "macos-arm64"


def release(version, **changes):
    """A ``lines`` entry, installable by Angelo itself."""
    item = fx.payload(version=version)["latest"]
    item["downloads"][MAC]["url"] = "https://downloads.angelo.studio/%s/Angelo-%s-macos-arm64.dmg" % (version, version)
    item.update(changes)
    return item


def feed_of(latest="1.0.9", lines=("1.1.0",), **changes):
    document = fx.payload(version=latest, latest=release(latest),
                          lines=[release(v) if isinstance(v, str) else v for v in lines], **changes)
    return update_feed.parse_payload(json.dumps(document).encode(), NOW)


class ParseTests(unittest.TestCase):
    def refused(self, **changes):
        with self.assertRaises(update_feed.FeedError):
            update_feed.parse_payload(json.dumps(fx.payload(version="1.0.9", **changes)).encode(), NOW)

    def test_a_feed_without_lines_reads_as_before(self):
        document = fx.payload(version="1.0.9")
        self.assertEqual(update_feed.parse_payload(json.dumps(document).encode(), NOW).lines, ())

    def test_lines_are_full_releases_with_the_same_rules(self):
        self.assertEqual(feed_of().lines[0].version, "1.1.0")
        self.assertEqual(feed_of(lines=("1.1.0", release("1.2.0", notes_url="https://example.com/"))).lines[1:], ())
        self.assertEqual(feed_of(lines=(dict(release("1.1.0"), version="1.1"),)).lines, ())

    def test_a_line_appears_once_and_never_beside_latest(self):
        self.refused(lines=[release("1.0.10")])
        self.refused(lines=[release("1.1.0"), release("1.1.1")])

    def test_the_list_is_bounded(self):
        self.refused(lines="1.1.0")
        many = feed_of(lines=[release("1.%d.0" % n) for n in range(1, update_feed.MAX_LINES + 2)])
        self.assertEqual(len(many.lines), update_feed.MAX_LINES, "more than this build knows never refuses the feed")
        self.assertEqual(many.lines[-1].version, "1.%d.0" % (update_feed.MAX_LINES + 1), "the newest stay")


class OfferTests(unittest.TestCase):
    def offer(self, current, **kwargs):
        return update_feed.offer_for(feed_of(**kwargs), current, MAC)

    def test_a_build_behind_on_its_line_gets_both_options(self):
        offer = self.offer("1.0.5")
        self.assertEqual((offer.release.version, offer.choice, offer.later.release.version), ("1.0.9", False, "1.1.0"))
        self.assertTrue(offer.release.automatic_install)
        self.assertTrue(offer.later.choice)
        self.assertIs(offer.newer_line, offer.later)

    def test_the_newest_of_a_line_still_has_the_newer_line(self):
        offer = self.offer("1.0.9")
        self.assertEqual((offer.release.version, offer.choice, offer.critical, offer.later), ("1.1.0", True, False, None))
        self.assertIs(offer.newer_line, offer)

    def test_a_newer_line_keeps_updating_from_lines_while_latest_stays_behind_it(self):
        offer = self.offer("1.1.0", lines=("1.1.2",))
        self.assertEqual((offer.release.version, offer.choice, offer.later), ("1.1.2", False, None))
        self.assertIsNone(self.offer("1.1.2", lines=("1.1.2",)))

    def test_latest_is_an_ordinary_update_whatever_its_line(self):
        offer = update_feed.offer_for(feed_of(latest="1.1.0", lines=()), "1.0.9", MAC)
        self.assertEqual((offer.release.version, offer.choice, offer.newer_line), ("1.1.0", False, None))

    def test_the_newest_of_several_newer_lines_is_the_choice(self):
        self.assertEqual(self.offer("1.0.9", lines=("1.2.0", "1.1.4")).release.version, "1.2.0")

    def test_the_recommended_line_and_a_yes_win_over_a_newer_plain_line(self):
        feed = feed_of(lines=(release("1.1.5", recommended=True), "1.2.0"))
        self.assertEqual(update_feed.offer_for(feed, "1.0.9", MAC).release.version, "1.1.5")
        feed = feed_of(lines=("1.1.5", "1.2.0"))
        self.assertEqual(update_feed.offer_for(feed, "1.0.9", MAC, "1.1").release.version, "1.1.5")

    def test_a_critical_flag_of_another_line_never_turns_a_choice_into_a_demand(self):
        offer = self.offer("1.0.9", lines=(release("1.1.0", critical=True),))
        self.assertEqual((offer.choice, offer.critical), (True, False))

    def test_a_line_below_the_minimum_is_told_so_and_still_decides(self):
        offer = self.offer("1.0.9", minimum_supported="1.1.0")
        self.assertEqual((offer.choice, offer.critical, offer.unsupported), (True, True, True))

    def test_an_update_with_no_build_for_this_platform_keeps_the_newer_line(self):
        document = fx.payload(version="1.0.10", lines=[release("1.1.0")])
        document["latest"]["downloads"].pop(MAC)
        offer = update_feed.offer_for(update_feed.parse_payload(json.dumps(document).encode(), NOW), "1.0.9", MAC)
        self.assertEqual((offer.download, offer.later.release.version), (None, "1.1.0"))

    def test_the_recommendation_is_read_and_defaults_off(self):
        self.assertFalse(self.offer("1.0.9").release.recommended)
        self.assertTrue(self.offer("1.0.9", lines=(release("1.1.0", recommended=True),)).release.recommended)
        with self.assertRaises(update_feed.FeedError):
            update_feed.parse_payload(json.dumps(fx.payload(latest=release("1.0.9", recommended="yes"))).encode(), NOW)


class ForwardTests(unittest.TestCase):
    def test_a_line_this_build_cannot_read_never_stops_its_own_updates(self):
        future = release("1.2.0")
        future["downloads"]["linux-arm64"] = dict(future["downloads"][MAC])
        feed = feed_of(lines=("1.1.0", future))
        self.assertEqual([r.version for r in feed.lines], ["1.1.0"])
        self.assertEqual(update_feed.offer_for(feed, "1.0.5", MAC).release.version, "1.0.9")


class CheckerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.seed, self.keys = fx.new_key()
        self.directory = folder.name
        self.latest = "1.0.9"
        self.lines = [release("1.1.0")]

    def fetch(self, method, url, headers=None, **kwargs):
        from angelo_agent.app_http import Response
        return Response(200, {}, fx.envelope(fx.payload(version=self.latest, lines=self.lines, sequence=4), self.seed))

    def checker(self, current="1.0.9"):
        return update_check.Checker(self.directory, fetch=self.fetch, clock=lambda: NOW.timestamp(), keys=self.keys,
                                    current=current, platform=MAC, policy=Policy())

    def test_staying_keeps_the_line_quiet_through_its_later_releases(self):
        checker = self.checker()
        checker.skip(checker.check().offer)
        self.assertTrue(checker.check().skipped)
        self.lines = [release("1.1.1")]
        self.assertTrue(checker.check().skipped, "1.1.1 is the same line")
        self.lines = [release("1.2.0")]
        self.assertFalse(checker.check().skipped, "a newer line is a new choice")

    def test_staying_leaves_the_own_update_and_keeps_the_line_in_sight(self):
        checker = self.checker()
        checker.skip(checker.check().offer)
        self.latest = "1.0.10"
        result = checker.check()
        self.assertEqual((result.offer.release.version, result.skipped, result.offer.later), ("1.0.10", False, None))
        self.assertEqual(result.declined.release.version, "1.1.0")

    def test_a_manual_check_asks_again(self):
        checker = self.checker()
        checker.skip(checker.check().offer)
        self.assertFalse(checker.check(manual=True).skipped)
        self.assertEqual(checker.state().skipped_line, "")
        self.assertFalse(checker.check().skipped)

    def test_a_yes_is_remembered_and_lifts_a_later(self):
        checker = self.checker()
        offer = checker.check().offer
        checker.snooze(offer)
        checker.accept(offer)
        state = update_state.load(self.directory)
        self.assertEqual((state.accepted_line, state.snoozed_version), ("1.1", ""))

    def test_a_yes_keeps_the_own_update_beside_it_and_survives_a_further_line(self):
        checker = self.checker()
        checker.accept(checker.check().offer)
        self.latest, self.lines = "1.0.10", [release("1.1.0"), release("1.2.0")]
        offer = checker.check().offer
        self.assertEqual((offer.release.version, offer.later.release.version), ("1.0.10", "1.1.0"))

    def test_a_damaged_yes_never_stops_a_check(self):
        checker = self.checker()
        update_state.save(self.directory, update_state.UpdateState(accepted_line="1.x"))
        self.assertEqual(checker.check().kind, update_check.UPDATE)

    def test_the_yes_is_spent_once_the_line_is_reached(self):
        checker = self.checker()
        checker.accept(checker.check().offer)
        self.lines = [release("1.1.5")]
        self.checker("1.1.0").check()
        self.assertEqual(update_state.load(self.directory).accepted_line, "")

    def test_a_skipped_update_of_the_own_line_never_hides_the_newer_line(self):
        checker = self.checker("1.0.5")
        update_state.save(self.directory, update_state.UpdateState(skipped_version="1.0.9"))
        result = checker.check()
        self.assertEqual((result.offer.release.version, result.offer.later.release.version, result.skipped),
                         ("1.0.9", "1.1.0", False))

    def test_a_skipped_own_update_is_not_prepared_but_the_newer_line_still_shows(self):
        update_state.save(self.directory, update_state.UpdateState(skipped_version="1.0.9"))
        offer = self.checker("1.0.5").check().offer
        self.assertTrue(offer.own_skipped)

    def test_staying_on_a_recommended_line_leaves_a_newer_line_to_ask(self):
        checker = self.checker()
        self.lines = [release("1.1.5", recommended=True), release("1.2.0")]
        checker.skip(checker.check().offer)
        result = checker.check()
        self.assertEqual((result.offer.release.version, result.skipped), ("1.2.0", False))
        self.assertEqual(result.declined.release.version, "1.1.5")

    def test_stay_goes_quiet_with_two_newer_lines(self):
        checker = self.checker()
        self.lines = [release("1.1.4"), release("1.2.0")]
        for _attempt in range(3):
            result = checker.check()
            if result.skipped:
                break
            checker.skip(result.offer)
        self.assertTrue(result.skipped)
        self.assertEqual(checker.state().skipped_line, "1.2")

    def test_a_recommended_older_line_asks_once_and_never_lowers_the_stay(self):
        checker = self.checker()
        self.lines = [release("1.1.4", recommended=True), release("1.2.0")]
        update_state.save(self.directory, update_state.UpdateState(skipped_line="1.2"))
        result = checker.check()
        self.assertEqual((result.offer.release.version, result.skipped), ("1.1.4", False))
        checker.skip(result.offer)
        self.assertEqual(checker.state().skipped_line, "1.2")
        self.assertTrue(checker.check().skipped)

    def test_recommending_asks_once_more_and_a_stay_then_holds(self):
        checker = self.checker()
        checker.skip(checker.check().offer)
        self.lines = [release("1.1.0", recommended=True)]
        result = checker.check()
        self.assertFalse(result.skipped, "the recommendation is news")
        checker.skip(result.offer)
        self.assertTrue(checker.check().skipped, "the person answered it")
        self.lines = [release("1.1.0")]
        self.assertTrue(checker.check().skipped)


class ReadyTests(ready.ReadyCase):
    def offer(self, version="1.1.0", url=None, **changes):
        return update_feed.offer_for(feed_of(), "1.0.9", MAC)

    def test_a_choice_downloads_nothing_on_its_own(self):
        offer = self.offer()
        self.assertFalse(self.ready.consider(offer))
        self.assertFalse(self.ready.consider(offer, manual=True), "even Check for Updates… only asks")
        self.assertEqual(self.preparer.prepared, [])
        self.assertEqual(self.ready.automatic(offer), "choice")

    def test_the_recommendation_downloads_nothing_either(self):
        offer = update_feed.offer_for(feed_of(lines=(release("1.1.0", recommended=True),)), "1.0.9", MAC)
        self.assertFalse(self.ready.consider(offer))
        self.assertEqual((self.preparer.prepared, self.ready.automatic(offer)), ([], "choice"))

    def test_the_yes_starts_the_real_preparation_and_outlives_the_session(self):
        offer = self.offer()
        self.ready.accept(offer)
        self.finish_worker()
        self.assertEqual(self.preparer.prepared, ["1.1.0"])
        self.assertTrue(self.ready.ready_for(offer))
        self.assertEqual(update_state.load(self.folder).accepted_line, "1.1")
        self.ready._accepted_line = ""   # The next launch reads it back.
        self.assertTrue(self.ready.consider(offer))

    def both(self):
        return update_feed.offer_for(feed_of(latest="1.0.10"), "1.0.9", MAC)

    def test_a_yes_works_when_the_state_folder_cannot_be_written(self):
        offer = self.offer()
        with mock.patch.object(update_state, "save", return_value=False):
            self.ready.accept(offer)
        self.finish_worker()
        self.assertEqual(self.preparer.prepared, ["1.1.0"])

    def test_the_yes_prepares_the_newer_line_and_the_own_update_stays_ready(self):
        whole = self.both()
        self.ready.consider(whole, manual=True)
        self.finish_worker()
        self.ready.accept(whole.later, whole)
        self.finish_worker()
        self.assertEqual(self.preparer.prepared, ["1.0.10", "1.1.0"])
        self.assertIs(self.ready.target(whole), whole.later)
        self.assertTrue(self.ready.ready_for(whole))
        self.n.update_ui.offer = whole
        self.n.update_ui.decide("skip", whole.later)
        self.assertIs(self.ready.target(self.n.update_ui.offer), self.n.update_ui.offer)
        self.assertEqual(self.n.update_ui.offer.release.version, "1.0.10")
        self.assertTrue(self.ready.ready_for(self.n.update_ui.offer), "the own update never left")

    def test_restart_installs_the_chosen_side_and_keeps_the_whole_offer(self):
        whole = self.both()
        self.ready.consider(whole, manual=True)
        self.finish_worker()
        self.ready.accept(whole.later, whole)
        self.finish_worker()
        with mock.patch.object(self.restart, "begin") as begin:
            self.ready.restart(whole, None)
        offer, ready, _host = begin.call_args.args
        self.assertIs(offer, whole)
        self.assertEqual(ready.version, "1.1.0")

    def test_a_skipped_update_is_not_prepared_automatically(self):
        whole = dataclasses.replace(self.both(), own_skipped=True)
        with mock.patch.object(self.ready, "background_allowed", return_value=True):
            self.assertFalse(self.ready.consider(whole))
        self.assertEqual(self.preparer.prepared, [])

    def test_a_newer_line_that_cannot_be_prepared_gives_the_own_update_back(self):
        whole = self.both()
        self.preparer.outcome = self.n.update_ready._flow().PrepareError("verify:bad")
        self.ready.accept(whole.later, whole)
        self.finish_worker()
        self.assertEqual(self.ready.automatic(whole.later), "failed")
        self.assertIs(self.ready.target(whole), whole)
        self.preparer.outcome = None
        self.assertTrue(self.ready.consider(whole, manual=True))
        self.finish_worker()
        self.assertIn("1.0.10", self.preparer.prepared)

    def test_stay_after_a_passing_failure_stops_every_retry(self):
        offer = self.offer()
        self.preparer.outcome = self.n.update_ready._flow().PrepareError("download:offline", transient=True)
        self.ready.accept(offer)
        self.finish_worker()
        self.n.update_ui.decide("skip", offer)
        self.preparer.outcome = None
        self.ready._failed.clear()
        self.ready.tick()
        self.finish_worker()
        self.assertEqual(self.preparer.prepared, ["1.1.0"], "tried once, before the person stayed")
        self.assertEqual(self.ready.automatic(offer), "choice")
        for _attempt in range(50):
            if ("forget", "1.1.0") in self.preparer.cleaned:
                break
            time.sleep(0.02)
        self.assertIn(("forget", "1.1.0"), self.preparer.cleaned)

    def test_stay_on_a_ready_line_takes_the_yes_back_and_clears_it(self):
        offer = self.offer()
        self.ready.accept(offer)
        self.finish_worker()
        self.n.update_ui.decide("skip", offer)
        self.assertFalse(self.ready.ready_for(offer))
        self.assertFalse(self.ready.consider(offer))

    def test_a_new_line_is_news_while_the_own_update_is_quiet(self):
        whole = self.both()
        ui = self.n.update_ui
        with mock.patch.object(self.ready, "background_allowed", return_value=True):
            ui.apply(update_check.Result(update_check.UPDATE, whole), False)
            self.assertTrue(self.queued("update"), "the newer line is shown at once")
            self.finish_worker()
            self.n.card_host._queue.clear()
            ui.apply(update_check.Result(update_check.UPDATE, whole), False)
        self.assertFalse(self.queued("update"), "told once")
        self.assertEqual(ui.checker().state().told_line, "1.1.0")
        changed = dataclasses.replace(whole.later, release=dataclasses.replace(whole.later.release, recommended=True))
        with mock.patch.object(self.ready, "background_allowed", return_value=True):
            ui.apply(update_check.Result(update_check.UPDATE, dataclasses.replace(whole, later=changed), snoozed=True), False)
        self.assertTrue(self.queued("update"), "a new recommendation beats a pause")

    def test_later_survives_a_relaunch(self):
        whole = self.both()
        ui = self.n.update_ui
        ui.checker().tell_line("1.1.0")
        with mock.patch.object(self.ready, "background_allowed", return_value=True):
            ui.apply(update_check.Result(update_check.UPDATE, whole, snoozed=True), False)
        self.assertFalse(self.queued("update"))

    def test_offline_mode_sends_the_choice_to_the_download(self):
        offer = self.offer()
        self.ready.consider(offer)
        with mock.patch.object(self.n.update_ui, "explicit_offline", return_value=True):
            self.assertEqual(self.ready.automatic(offer), "manual")

    def test_an_install_that_cannot_replace_itself_keeps_its_reason(self):
        self.preparer.reason = "permission"
        self.ready._support_reason = None
        offer = self.offer()
        self.assertFalse(self.ready.consider(offer))
        self.assertEqual(self.ready.automatic(offer), "permission")


class AskingLive(cards.Live):
    def __init__(self, phase="notify", why="", later_why="choice"):
        super().__init__(phase, why=why)
        self.later_why, self.accepted, self.chosen = later_why, [], None

    def unsupported_reason(self, selected):
        return self.later_why if selected.choice else super().unsupported_reason(selected)

    def phase(self, selected):
        return "notify" if selected.choice and self.state != "restarting" else self.state

    def target(self, selected):
        return self.chosen or selected

    def accept(self, selected, whole):
        self.accepted.append(selected)


class CardTests(unittest.TestCase):
    def card(self, phase="notify", why="", later_why="choice", recommended=False, up_to_date=True, **changes):
        self.live = AskingLive(phase, why, later_why)
        self.decisions = []
        newer = dataclasses.replace(cards.offer(), choice=True, **changes)
        release_ = dataclasses.replace(newer.release, recommended=recommended, version="1.1.0")
        newer = dataclasses.replace(newer, release=release_)
        offer = newer if up_to_date else dataclasses.replace(
            cards.offer(), release=dataclasses.replace(newer.release, version="1.0.10", recommended=False), later=newer)
        return update_card.UpdateCard(offer, "1.0.9", lambda c, o: self.decisions.append((c, o)), live=self.live)

    def plan(self, card, region=(1147, 787), scale=1.0):
        return card.layout(cards.VIEW, region[0], region[1], scale, kit.UiState())

    def test_up_to_date_shows_both_sides(self):
        plan = self.plan(self.card())
        text, buttons = cards.flat(plan), cards.labels(plan)
        self.assertIn("Angelo 1.1 is available", text)
        self.assertIn("You're up to date", text)
        self.assertLess(text.index("Angelo 1.0.9"), text.index("A newer version"))
        self.assertIn("Update", buttons)
        self.assertIn("Stay on Angelo 1.0", buttons)
        self.assertNotIn("Download", buttons)

    def test_an_update_of_the_own_line_stands_beside_the_newer_line(self):
        plan = self.plan(self.card(up_to_date=False))
        text, buttons = cards.flat(plan), cards.labels(plan)
        self.assertIn("Angelo 1.0.10", text)
        self.assertIn("An update to your version", text)
        self.assertIn("A newer version", text)
        self.assertEqual(buttons.count("Update"), 1)

    def test_a_ready_update_keeps_the_newer_line_in_view(self):
        plan = self.plan(self.card("ready", up_to_date=False))
        self.assertIn("Restart to Update", cards.labels(plan))
        self.assertIn("Update", cards.labels(plan))

    def test_recommended_leads_and_says_so_in_both_states(self):
        for up_to_date in (True, False):
            with self.subTest(up_to_date=up_to_date):
                text = cards.flat(self.plan(self.card(recommended=True, up_to_date=up_to_date)))
                self.assertIn("We recommend moving to Angelo 1.1", text)
                self.assertIn("Angelo 1.1 (recommended)", text)
                own = "You're up to date" if up_to_date else "An update to your version"
                self.assertIn(own, text, "both options stay visible")
                self.assertLess(text.index("A newer version"), text.index(own))

    def test_every_state_fits_every_region(self):
        for up_to_date in (True, False):
            for phase in ("notify", "ready", "downloading", "preparing"):
                for recommended in (False, True):
                    for region in cards.SIZES:
                        for scale in (1.0, 2.0):
                            with self.subTest(phase=phase, up=up_to_date, rec=recommended, region=region, scale=scale):
                                self.plan(self.card(phase, recommended=recommended, up_to_date=up_to_date), region, scale)

    def test_update_starts_the_newer_line_and_the_card_stays(self):
        card, host = self.card(up_to_date=False), cards.Host()
        later = card.offer.later
        card.act("accept", None, host)
        self.assertEqual((self.live.accepted, host.closed), ([later], []))

    def test_only_the_chosen_side_can_restart(self):
        card = self.card("ready", up_to_date=False)
        self.live.chosen = card.offer.later
        self.live.phase = lambda selected: "ready"
        self.assertEqual(cards.labels(self.plan(card)).count("Restart to Update"), 1)

    def test_restarting_is_a_spinner_and_nothing_acts(self):
        card, host = self.card("restarting", up_to_date=False), cards.Host()
        plan = self.plan(card)
        self.assertIn("Restarting Angelo", cards.flat(plan))
        self.assertEqual(cards.labels(plan), [])
        card.act("accept", None, host)
        self.assertEqual(self.live.accepted, [])

    def test_a_critical_own_update_keeps_its_warning_and_stay_stays(self):
        card = self.card(up_to_date=False)
        card.offer = dataclasses.replace(card.offer, critical=True)
        plan = self.plan(card)
        self.assertIn("important", cards.flat(plan))
        self.assertIn("Stay on Angelo 1.0", cards.labels(plan))

    def test_stay_decides_about_the_newer_line_not_the_own_update(self):
        card, host = self.card(up_to_date=False), cards.Host()
        later = card.offer.later
        card.act("skip", None, host)
        self.assertEqual((self.decisions, host.closed), ([("skip", later)], ["skip"]))

    def test_an_install_that_cannot_replace_itself_downloads_instead(self):
        plan = self.plan(self.card(later_why="permission"))
        self.assertIn("Download", cards.labels(plan))
        self.assertIn("An administrator installed Angelo here", cards.flat(plan))

    def test_an_unsupported_line_says_so_and_cannot_stay(self):
        plan = self.plan(self.card(unsupported=True, critical=True))
        self.assertIn("no longer supported", cards.flat(plan))
        self.assertNotIn("Stay on Angelo 1.0", cards.labels(plan))


class ToolTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "CHANGELOG.md"
        self.source.write_text(ready_changelog("1.2.1", "1.2.0", "1.1.1", "1.1.0", "1.0.10", "1.0.9"), encoding="utf-8")

    def published(self, document):
        path = self.folder / "published.json"
        path.write_text(json.dumps({"payload": base64.b64encode(json.dumps(document).encode()).decode()}), encoding="utf-8")
        return tool.previous_payload(path)

    def build(self, version, published, **kwargs):
        raw = tool.build_payload(version, 12, [(MAC, {"url": "https://downloads.angelo.studio/x.dmg", "sha256": "a" * 64,
                                                       "size": 5})], source=self.source, now=NOW, previous=11,
                                 published=published, **kwargs)
        return update_feed.parse_payload(raw, NOW), json.loads(raw)

    def refused(self, *args, **kwargs):
        with self.assertRaises(tool.FeedToolError):
            self.build(*args, **kwargs)

    def test_a_new_line_goes_beside_latest_and_older_builds_keep_their_release(self):
        before = fx.payload(version="1.0.9")
        feed, document = self.build("1.1.0", self.published(before), as_line=True)
        self.assertEqual((feed.latest.version, [r.version for r in feed.lines]), ("1.0.9", ["1.1.0"]))
        self.assertEqual(document["latest"], before["latest"], "byte for byte what older builds read today")

    def test_a_patch_of_the_old_line_keeps_the_new_line(self):
        before = fx.payload(version="1.0.9", lines=[release("1.1.0", recommended=True)])
        feed, _ = self.build("1.0.10", self.published(before))
        self.assertEqual((feed.latest.version, [r.version for r in feed.lines]), ("1.0.10", ["1.1.0"]))
        self.assertTrue(feed.lines[0].recommended)

    def test_the_next_release_of_a_line_replaces_it_and_keeps_its_recommendation(self):
        before = fx.payload(version="1.0.9", lines=[release("1.1.0", recommended=True)])
        feed, _ = self.build("1.1.1", self.published(before), as_line=True)
        self.assertEqual([(r.version, r.recommended) for r in feed.lines], [("1.1.1", True)])

    def test_a_newer_line_never_reaches_latest_by_accident(self):
        before = self.published(fx.payload(version="1.0.9", lines=[release("1.1.0")]))
        self.refused("1.1.1", before)
        feed, _ = self.build("1.1.1", before, end_line=True)
        self.assertEqual((feed.latest.version, feed.lines), ("1.1.1", ()))

    def test_ending_a_line_keeps_only_newer_lines(self):
        before = self.published(fx.payload(version="1.0.9", lines=[release("1.1.0"), release("1.2.0")]))
        feed, _ = self.build("1.1.1", before, end_line=True)
        self.assertEqual([r.version for r in feed.lines], ["1.2.0"])

    def test_an_older_version_never_ends_a_line(self):
        self.refused("1.0.9", self.published(fx.payload(version="1.1.0")), end_line=True)

    def test_the_same_version_can_be_issued_again(self):
        before = self.published(fx.payload(version="1.0.9", lines=[release("1.1.0")]))
        feed, _ = self.build("1.0.9", before)
        self.assertEqual((feed.latest.version, len(feed.lines)), ("1.0.9", 1))

    def test_as_line_needs_a_published_feed_and_a_newer_line(self):
        self.refused("1.1.0", None, as_line=True)
        self.refused("1.0.10", self.published(fx.payload(version="1.0.9")), as_line=True)
        self.refused("1.0.9", self.published(fx.payload(version="1.1.0")), as_line=True)

    def test_a_release_is_never_replaced_by_an_older_one(self):
        before = self.published(fx.payload(version="1.0.9", lines=[release("1.1.1")]))
        self.refused("1.1.0", before, as_line=True)

    def test_build_without_a_published_feed_is_refused_unless_it_is_the_first(self):
        with redirect_stderr(io.StringIO()) as errors:
            code = tool.main(["build", "--version", "1.0.10", "--sequence", "12", "--out", str(self.folder / "p.json")])
        self.assertEqual(code, 1)
        self.assertIn("--previous is required", errors.getvalue())

    def test_as_line_keeps_the_minimum_and_the_crash_endpoint(self):
        path = self.folder / "live.json"
        document = fx.payload(version="1.0.9", minimum_supported="1.0.5", sequence=11,
                              services={"crash_reports": "https://console.angelo.studio/c"})
        path.write_text(json.dumps({"payload": base64.b64encode(json.dumps(document).encode()).decode()}), encoding="utf-8")
        out, dmg = self.folder / "payload.json", self.folder / "Angelo-1.1.0-macos-arm64.dmg"
        dmg.write_bytes(b"x")
        from unittest import mock
        with mock.patch.object(tool.changelog, "SOURCE", self.source), mock.patch.object(
                tool.build_payload, "__kwdefaults__", dict(tool.build_payload.__kwdefaults__, source=self.source)):
            tool.main(["build", "--version", "1.1.0", "--sequence", "12", "--as-line", "--previous", str(path),
                       "--download", MAC + "=" + str(dmg), "--out", str(out)], out=io.StringIO())
        feed = update_feed.parse_payload(out.read_bytes(), NOW)
        self.assertEqual((feed.minimum_supported, feed.crash_reports_url),
                         ("1.0.5", "https://console.angelo.studio/c"))

    def test_recommend_changes_only_the_flag_and_the_feed_stamps(self):
        document = fx.payload(version="1.0.9", lines=[release("1.1.0")], sequence=12)
        raw = tool.recommend_payload(self.published(document), "1.1", True, 13, now=NOW)
        feed = update_feed.parse_payload(raw, NOW, last_sequence=12)
        self.assertEqual((feed.sequence, feed.lines[0].recommended, feed.latest.version), (13, True, "1.0.9"))
        after = json.loads(raw)
        self.assertEqual(after["latest"], document["latest"])
        back = tool.recommend_payload(after, "1.1", False, 14, now=NOW)
        self.assertNotIn("recommended", json.loads(back)["lines"][0])

    def test_recommend_refuses_a_line_that_is_not_published(self):
        with self.assertRaises(tool.FeedToolError):
            tool.recommend_payload(fx.payload(version="1.0.9"), "1.1", True, 13, now=NOW)

    def test_the_feed_tool_refuses_what_a_build_would_leave_out(self):
        future = release("1.2.0")
        future["downloads"]["linux-arm64"] = dict(future["downloads"][MAC])
        with self.assertRaises(tool.FeedToolError):
            self.build("1.0.10", self.published(fx.payload(version="1.0.9", lines=[future])))


def ready_changelog(*versions):
    entries = "\n".join("## %s — 2026-11-0%d\nBased on Blender 5.2.2 LTS.\n\nSummary of %s.\n\n### Highlights\n- One\n- Two\n- Three\n"
                        % (v, i + 1, v) for i, v in enumerate(versions))
    return "# Angelo release notes\n\n" + entries


if __name__ == "__main__":
    unittest.main()
