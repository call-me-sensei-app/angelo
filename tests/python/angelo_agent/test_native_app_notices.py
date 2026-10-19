# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Blender side of update notices, crash reports and What's New, on a stand-in bpy (notices_fakes).

update_ui: what a finished check shows (notice, top-bar and status-bar item, result notices), Later and
Skip, the schedule, explicit offline mode, and the redraw that reaches Blender's global bars. crash_ui:
what each consent does with a pending crash (ask, never, always, the machine policy), the dialog without
an endpoint, the automatic send and its notice, and the Save operator. app_notices: automation runs, the
readiness rule and the startup order. font_watch: fonts are loaded again when Blender dropped them.
updates_prefs: the panel draws from its cache only. Source rules: registration from the add-on's entry
point, no file access while drawing, and every interface string translatable. The real window is
``test_blender_update_crash``.
"""

import ast
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ADDON.parent))
import crash_fixtures as cfx  # noqa: E402
import update_fixtures as ufx  # noqa: E402
from notices_fakes import Notices  # noqa: E402
from angelo_agent import crash_report, crash_state, managed_policy, update_check, update_feed, update_state  # noqa: E402
from angelo_agent.managed_policy import Policy  # noqa: E402

NOTICE_VARIABLES = ("ANGELO_DISABLE_INTRO", "ANGELO_DISABLE_DEFAULT_CHAT", "ANGELO_ENABLE_APP_NOTICES",
                    "ANGELO_CRASH_REPORT_URL", "ANGELO_UPDATE_FEED_URL")


class Layout:
    """Records what a draw function asked of a UI layout."""

    def __init__(self, calls=None):
        self.calls = [] if calls is None else calls
        self.enabled = self.active = True
        self.emboss = 'NORMAL'

    def _child(self, *args, **kwargs):
        return Layout(self.calls)

    row = column = split = _child

    def separator(self, **kwargs):
        self.calls.append(("separator",))

    def label(self, text="", **kwargs):
        self.calls.append(("label", text))

    def prop(self, data, name, **kwargs):
        self.calls.append(("prop", name, self.enabled and self.active))

    def operator(self, name, text="", **kwargs):
        self.calls.append(("operator", name, text))
        return SimpleNamespace()


class NoticesCase(unittest.TestCase):
    extra = {}

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in NOTICE_VARIABLES:
            os.environ.pop(name, None)
        managed_policy.reset(Policy())
        self.addCleanup(managed_policy.reset)
        self.notices = Notices(**self.extra)
        self.n = self.notices.__enter__()
        self.addCleanup(self.notices.__exit__, None, None, None)
        self.n.update_ui.directory = lambda: self.folder.name
        self.n.card_host._queue.clear()
        self.seed, self.keys = ufx.new_key()

    def queued(self, kind=None):
        return self.n.card_host.queued(kind)

    def offer(self, **changes):
        from datetime import datetime, timezone
        feed = update_feed.open_feed(ufx.envelope(ufx.payload(**changes), self.seed), self.keys,
                                     datetime(2026, 10, 25, tzinfo=timezone.utc))
        return update_feed.offer_for(feed, "1.0.0", "macos-arm64")


class UpdateUiTests(NoticesCase):
    extra = {"director_layout": SimpleNamespace(is_director=lambda window: True)}

    def test_an_automatic_update_shows_the_notice_and_both_bars_offer_it(self):
        offer = self.offer()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer), manual=False)
        self.assertEqual([c.kind for c in self.queued()], ["update"])
        self.assertIs(self.n.update_ui.offer, offer)
        self.assertIn(("Window", "view_layer"), self.n.record.published, "the status bar is a global area")
        self.assertIn(("workspace", "tools"), self.n.record.published, "so is the top bar")

    def test_skipped_and_later_are_respected_by_automatic_checks(self):
        offer = self.offer()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer, skipped=True), manual=False)
        self.assertEqual((self.queued(), self.n.update_ui.offer), ([], None))
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, offer, snoozed=True), manual=False)
        self.assertEqual(self.queued(), [], "Later: no notice yet")
        self.assertIs(self.n.update_ui.offer, offer, "Later: the bars still offer it")
        self.n.update_ui.apply(update_check.Result(update_check.CURRENT), manual=False)
        self.assertIsNone(self.n.update_ui.offer)

    def test_a_manual_check_always_answers(self):
        cases = {update_check.Result(update_check.CURRENT): ("Angelo is up to date", ["close"]),
                 update_check.Result(update_check.OFFLINE, detail="offline"): ("Could not check for updates", ["close", "retry"]),
                 update_check.Result(update_check.FAILED, detail="bad_signature"): ("Could not check for updates", ["close", "retry"]),
                 update_check.Result(update_check.UNAVAILABLE, detail="no_keys"): ("Could not check for updates", ["close"]),
                 update_check.Result(update_check.DISABLED, detail="policy"): ("Update checks are off", ["close"]),
                 update_check.Result(update_check.OFFLINE, detail="offline_mode"): ("Update checks are off", ["close"])}
        for result, (title, actions) in cases.items():
            with self.subTest(result=result):
                self.n.card_host._queue.clear()
                self.n.update_ui.apply(result, manual=True)
                notice = self.queued("notice")[0]
                self.assertEqual((notice.title, [b[0] for b in notice.buttons]), (title, actions))
        self.assertIn("offline mode", notice.body)
        self.n.card_host._queue.clear()
        self.n.update_ui.apply(update_check.Result(update_check.UPDATE, self.offer()), manual=True)
        self.assertEqual([c.kind for c in self.queued()], ["update"], "the notice itself is the answer")

    def test_only_an_explicit_offline_mode_counts(self):
        app = self.n.bpy.app
        app.online_access, app.online_access_override = False, False   # Blender's preference, off by default.
        self.assertFalse(self.n.update_ui.explicit_offline())
        app.online_access_override = True                               # --offline-mode
        self.assertTrue(self.n.update_ui.explicit_offline())
        app.online_access = True                                        # --online-mode
        self.assertFalse(self.n.update_ui.explicit_offline())

    def test_later_and_skip_are_stored_and_skip_clears_the_offer(self):
        offer = self.offer()
        self.n.update_ui.offer = offer
        self.n.update_ui.decide("later", offer)
        stored = update_state.load(self.folder.name)
        self.assertEqual((stored.snoozed_version, self.n.update_ui.offer), ("1.1.0", offer))
        self.n.update_ui.decide("skip", offer)
        self.assertEqual((update_state.load(self.folder.name).skipped_version, self.n.update_ui.offer), ("1.1.0", None))

    def test_the_director_top_bar_shows_a_pending_update_on_its_left_only(self):
        draw = self.n.update_ui.draw_director_bar
        context = SimpleNamespace(region=SimpleNamespace(alignment='NONE'), window=None)
        holder = SimpleNamespace(layout=Layout())
        draw(holder, context)
        self.assertEqual(holder.layout.calls, [], "nothing without an offer")
        self.n.update_ui.offer = self.offer()
        draw(holder, context)
        self.assertIn(("operator", "angelo.update_show", "Angelo 1.1 available"), holder.layout.calls)
        right = SimpleNamespace(layout=Layout())
        draw(right, SimpleNamespace(region=SimpleNamespace(alignment='RIGHT'), window=None))
        self.assertEqual(right.layout.calls, [])

    def test_the_schedule_runs_once_at_launch_then_daily_and_never_in_automation(self):
        started = []
        self.n.update_ui.start_check = lambda manual=False: started.append(manual) or True
        self.n.update_ui._started = time.monotonic() - 60
        # A build that trusts no key (independent of the keys this checkout ships).
        self.n.update_ui.checker = lambda: update_check.Checker(self.folder.name, keys={})
        self.assertEqual((self.n.update_ui.tick(), started), (3600.0, []), "no trusted key: nothing to check or read")
        self.n.update_ui.checker = lambda: update_check.Checker(self.folder.name, keys=self.keys)
        os.environ["ANGELO_DISABLE_INTRO"] = "1"
        self.assertEqual((self.n.update_ui.tick(), started), (30.0, []), "an automation run")
        del os.environ["ANGELO_DISABLE_INTRO"]
        self.assertEqual((self.n.update_ui.tick(), started), (5.0, [False]), "the launch check")
        update_state.save(self.folder.name, update_state.UpdateState(last_attempt=time.time(), last_success=time.time()))
        self.assertEqual((self.n.update_ui.tick(), started), (60.0, [False]), "a day has not passed")
        update_state.save(self.folder.name, update_state.UpdateState(last_attempt=time.time() - 2 * 86400,
                                                                     last_success=time.time() - 2 * 86400))
        self.n.update_ui.tick()
        self.assertEqual(started, [False, False])

    def test_registration_adds_the_menus_bars_and_timer_and_removes_them(self):
        types = self.n.bpy.types
        self.n.update_ui.register()
        self.assertIn(self.n.update_ui.draw_help, types.TOPBAR_MT_help.appended)
        self.assertIn(self.n.update_ui.draw_help, types.ANGELO_MT_workspace_mode.appended, "Director hides Help")
        self.assertIn(self.n.update_ui.draw_director_bar, types.TOPBAR_HT_upper_bar.appended)
        self.assertIn(self.n.update_ui.draw_status, types.STATUSBAR_HT_header.appended)
        self.assertTrue(self.n.bpy.app.timers.is_registered(self.n.update_ui.tick))
        self.n.update_ui.unregister()
        for header in (types.TOPBAR_MT_help, types.ANGELO_MT_workspace_mode, types.TOPBAR_HT_upper_bar, types.STATUSBAR_HT_header):
            self.assertEqual(header.appended, [])
        self.assertFalse(self.n.bpy.app.timers.is_registered(self.n.update_ui.tick))


class CrashUiTests(NoticesCase):
    def pending(self, age=60, temp=None):
        temp = temp or tempfile.mkdtemp(dir=self.folder.name)
        log = Path(temp) / "angelo.crash.txt"
        log.write_text(cfx.MAC, encoding="utf-8")
        os.utime(log, (time.time() - age, time.time() - age))
        found, records = crash_report.find_pending(temp, crash_state.load(self.folder.name), time.time(), cfx.MAC_ROOTS)
        self.assertIsNotNone(found)
        return found, records

    def consent(self, value):
        state = crash_state.load(self.folder.name)
        state.consent = value
        crash_state.save(self.folder.name, state)

    def status(self, pending):
        return crash_state.load(self.folder.name).status(pending.fingerprint)

    def test_ask_without_an_endpoint_shows_the_dialog_that_cannot_send(self):
        pending, records = self.pending()
        self.n.crash_ui._handle(pending, records)
        card = self.queued("crash")[0]
        self.assertFalse(card.flow.can_send)
        self.assertIs(self.n.crash_ui.flow, card.flow)
        self.assertEqual(self.status(pending), "", "nothing is recorded until the dialog closes")

    def test_ask_with_an_endpoint_can_send(self):
        os.environ["ANGELO_CRASH_REPORT_URL"] = "http://127.0.0.1:9/crash"
        pending, records = self.pending()
        self.n.crash_ui._handle(pending, records)
        self.assertTrue(self.queued("crash")[0].flow.can_send)

    def test_never_records_the_crash_as_declined_and_shows_nothing(self):
        self.consent("never")
        pending, records = self.pending()
        self.n.crash_ui._handle(pending, records)
        self.assertEqual((self.queued(), self.status(pending)), ([], "declined"))

    def test_the_machine_policy_shows_nothing_and_records_nothing(self):
        managed_policy.reset(Policy(disable_crash_reporting=True, keys=frozenset({"DisableCrashReporting"})))
        os.environ["ANGELO_CRASH_REPORT_URL"] = "http://127.0.0.1:9/crash"
        pending, records = self.pending()
        self.n.crash_ui._handle(pending, records)
        self.assertEqual((self.queued(), self.status(pending)), ([], ""))

    def test_always_sends_without_a_dialog_once_an_endpoint_exists(self):
        sent = []
        self.n.crash_ui._send_automatically = sent.append
        self.consent("always")
        os.environ["ANGELO_CRASH_REPORT_URL"] = "http://127.0.0.1:9/crash"
        pending, records = self.pending()
        self.n.crash_ui._handle(pending, records)
        self.assertEqual((len(sent), self.queued()), (1, []))

    def test_always_without_an_endpoint_still_asks(self):
        self.consent("always")
        pending, records = self.pending()
        self.n.crash_ui._handle(pending, records)
        self.assertEqual([c.kind for c in self.queued()], ["crash"])

    def test_a_crash_the_service_asked_to_wait_for_is_not_offered_until_then(self):
        os.environ["ANGELO_CRASH_REPORT_URL"] = "http://127.0.0.1:9/crash"
        pending, records = self.pending()
        state = crash_state.load(self.folder.name)
        state.mark(pending.fingerprint, crash_state.FAILED, not_before=time.time() + 600)
        crash_state.save(self.folder.name, state)
        for consent in ("ask", "always"):
            self.consent(consent)
            self.n.crash_ui._handle(pending, records)
            self.assertEqual(self.queued(), [], consent)
        state = crash_state.load(self.folder.name)
        state.mark(pending.fingerprint, crash_state.FAILED, not_before=time.time() - 1)
        crash_state.save(self.folder.name, state)
        self.consent("ask")
        self.n.crash_ui._handle(pending, records)
        self.assertEqual([c.kind for c in self.queued()], ["crash"])

    def test_older_crashes_are_recorded_superseded(self):
        temp = tempfile.mkdtemp(dir=self.folder.name)
        old = Path(temp) / "scene.crash.txt"
        old.write_text(cfx.MAC + "\n", encoding="utf-8")
        os.utime(old, (time.time() - 3600, time.time() - 3600))
        pending, records = self.pending(temp=temp)
        self.assertEqual([status for _, status in records], ["superseded"])
        self.n.crash_ui._handle(pending, records)
        statuses = sorted(v["status"] for v in crash_state.load(self.folder.name).handled.values())
        self.assertEqual(statuses, ["superseded"])

    def test_the_automatic_send_ends_with_a_notice_offering_the_review(self):
        from angelo_agent import crash_flow, crash_upload
        pending, _ = self.pending()
        document = self.n.crash_ui.build_document(pending)
        automatic = crash_flow.CrashFlow(self.folder.name, pending, document, "http://127.0.0.1:9/crash",
                                         send=lambda url, payload, rid: crash_upload.Outcome(crash_upload.SENT, "http_201", rid))
        self.n.crash_ui._send_automatically(automatic)
        check = next(iter(self.n.bpy.app.timers.registered))
        deadline = time.time() + 5
        while check() is not None and time.time() < deadline:
            time.sleep(0.01)
        notice = self.queued("notice")[0]
        self.assertEqual((notice.title, [b[1] for b in notice.buttons]), ("Crash report sent", ["Ask me next time", "See what was sent"]))
        self.assertEqual(self.status(pending), "sent")
        self.assertEqual(document["app"]["blender_base"], "5.2.2 LTS")

    def test_the_notice_closes_before_the_review_opens(self):
        order = []
        self.n.crash_ui.show_last_sent = lambda: order.append("review")
        host = SimpleNamespace(close=lambda how: order.append("close"))
        self.n.crash_ui._notice_action("view", host)
        self.assertEqual(order, ["close", "review"], "else the review would replace and queue the notice again")
        self.consent("always")
        self.n.crash_ui._notice_action("change", SimpleNamespace(close=lambda how: None))
        self.assertEqual(crash_state.load(self.folder.name).consent, "ask")

    def test_save_report_writes_exactly_the_report(self):
        pending, records = self.pending()
        self.n.crash_ui._handle(pending, records)
        operator = self.n.crash_ui.ANGELO_OT_crash_save_report()
        self.assertTrue(operator.poll(None))
        operator.invoke(SimpleNamespace(window_manager=self.n.bpy.context.window_manager), None)
        self.assertTrue(operator.filepath.startswith("Angelo-crash-report-"))
        operator.filepath = str(Path(self.folder.name) / "report")
        operator.report = lambda kind, text: None
        self.assertEqual(operator.execute(None), {'FINISHED'})
        self.assertEqual((Path(self.folder.name) / "report.json").read_text(), self.n.crash_ui.flow.text + "\n")
        self.assertTrue(self.n.crash_ui.flow.saved)

    def test_the_temporary_folder_is_the_parent_of_the_sessions_own(self):
        self.n.bpy.app.tempdir = "/private/var/folders/x/T/blender_a1b2c3/"
        self.assertEqual(self.n.crash_ui.temp_base(), os.path.normpath("/private/var/folders/x/T"))


class AppNoticesTests(NoticesCase):
    def ready_with(self, operators, intro_seen=True):
        fakes = {"director": SimpleNamespace(_timer=None), "ui": SimpleNamespace(_layout_timer=None),
                 "intro": SimpleNamespace(seen=lambda: intro_seen, is_open=lambda: False, _timer=None)}
        with mock.patch.dict(sys.modules, {"angelo_agent." + k: v for k, v in fakes.items()}):
            window = self.n.bpy.context.window_manager.windows[0]
            window.modal_operators = [SimpleNamespace(bl_idname=name) for name in operators]
            return self.n.app_notices.ready()

    def test_automation_runs_show_no_notice(self):
        automation_off = self.n.app_notices.automation_off
        self.assertFalse(automation_off())
        for name in ("ANGELO_DISABLE_INTRO", "ANGELO_DISABLE_DEFAULT_CHAT"):
            with mock.patch.dict(os.environ, {name: "1"}):
                self.assertTrue(automation_off(), name)
                with mock.patch.dict(os.environ, {"ANGELO_ENABLE_APP_NOTICES": "1"}):
                    self.assertFalse(automation_off(), "the one window suite that tests the cards")

    def test_ready_ignores_a_cards_own_operator_but_waits_for_the_persons(self):
        self.assertTrue(self.ready_with([]))
        self.assertTrue(self.ready_with(["ANGELO_OT_card_host", "ANGELO_OT_chat_input"]))
        self.assertFalse(self.ready_with(["VIEW3D_OT_rotate"]))
        self.assertFalse(self.ready_with([], intro_seen=False), "the welcome screen comes first")


class FontWatchTests(NoticesCase):
    def test_fonts_blender_dropped_are_loaded_again(self):
        reloads = []
        chat = SimpleNamespace(_font=4, _mono=5, reload_fonts=lambda: reloads.append(1))
        with mock.patch.dict(sys.modules, {"angelo_agent.chat_view": chat}):
            self.assertEqual(self.n.font_watch.check(), self.n.font_watch.INTERVAL)
            self.assertEqual(reloads, [])
            self.n.blf.dimensions = lambda font, text: (0.0, 0.0) if font == 5 else (7.0, 12.0)
            self.n.font_watch.check()
        self.assertEqual(reloads, [1])
        self.assertFalse(self.n.font_watch.lost(0), "0 is a font never loaded, not a lost one")
        area = self.n.bpy.context.window_manager.windows[0].screen.areas[0]
        self.assertEqual(area.redraws, 1)


class PreferencesTests(NoticesCase):
    def test_the_panel_draws_from_its_cache_without_reading_files(self):
        prefs = self.n.updates_prefs
        context = SimpleNamespace(window_manager=SimpleNamespace())
        with mock.patch.object(update_state, "load", side_effect=AssertionError("read while drawing")):
            layout = Layout()
            prefs.ANGELO_PT_updates_and_reports().draw_centered(context, layout)
        texts = [call[1] for call in layout.calls if call[0] in {"label", "operator"}] + \
                [call[2] for call in layout.calls if call[0] == "operator"]
        self.assertIn("Not checked yet.", texts)
        self.assertIn("View Last Report Sent", texts)
        self.assertEqual([c[1] for c in layout.calls if c[0] == "prop"], ["angelo_update_automatic", "angelo_crash_consent"])

    def test_the_machine_policy_locks_both_settings_and_says_so(self):
        managed_policy.reset(Policy(disable_update_check=True, disable_crash_reporting=True))
        layout = Layout()
        self.n.updates_prefs.ANGELO_PT_updates_and_reports().draw_centered(SimpleNamespace(window_manager=None), layout)
        self.assertEqual([c for c in layout.calls if c[0] == "label" and c[1] == "Turned off by your organization."].__len__(), 2)
        self.assertEqual([c[2] for c in layout.calls if c[0] == "prop"], [False, False])

    def test_short_relative_times_and_consent_values(self):
        ago = self.n.updates_prefs.ago
        self.assertEqual([ago(5), ago(300), ago(7200), ago(3 * 86400)], ["just now", "5 min ago", "2 h ago", "3 d ago"])
        self.assertEqual(tuple(item[0] for item in self.n.updates_prefs.CONSENT_ITEMS), crash_state.CONSENTS)


NOTICE_MODULES = ("app_notices", "card_host", "card_draw", "crash_card", "crash_ui", "font_watch", "update_card",
                  "update_ui", "updates_prefs", "whatsnew_card")


class SourceRuleTests(unittest.TestCase):
    def tree(self, name):
        return ast.parse((ADDON / (name + ".py")).read_text(encoding="utf-8"))

    def test_registered_from_the_addons_entry_point_not_from_ui_py(self):
        entry = (ADDON / "__init__.py").read_text(encoding="utf-8")
        for name in ("app_notices.register()", "font_watch.register()", "app_notices.unregister()", "font_watch.unregister()"):
            self.assertIn(name, entry)
        ui_source = (ADDON / "ui.py").read_text(encoding="utf-8")
        self.assertNotIn("app_notices", ui_source)
        self.assertNotIn("font_watch", ui_source)

    def test_drawing_never_reads_files_or_state(self):
        forbidden = {"open", "read_json", "read_text", "read_bytes", "load", "state", "refresh", "checker", "last_sent"}
        targets = {"card_draw": None, "card_host": {"_draw"}, "update_ui": {"draw_status", "draw_help", "draw_director_bar"},
                   "updates_prefs": {"draw_centered", "_note"}}
        for module, functions in targets.items():
            for node in ast.walk(self.tree(module)):
                if isinstance(node, ast.FunctionDef) and (functions is None or node.name in functions):
                    for call in ast.walk(node):
                        if isinstance(call, ast.Call):
                            name = getattr(call.func, "attr", getattr(call.func, "id", ""))
                            self.assertNotIn(name, forbidden, "%s.%s calls %s" % (module, node.name, name))

    def test_every_interface_string_is_shown_and_has_named_placeholders(self):
        """Each ``n_()`` constant is used (a dead one would still reach translators) and its placeholders are named.

        That the drawn text really went through ``iface_`` is checked on the laid-out cards themselves
        (test_native_notice_cards, ``TranslationTests``).
        """
        for module in NOTICE_MODULES:
            tree = self.tree(module)
            constants = {}
            for node in tree.body:
                if isinstance(node, ast.Assign):
                    values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
                    targets = node.targets[0].elts if isinstance(node.targets[0], ast.Tuple) else node.targets
                    for target, value in zip(targets, values):
                        if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "n_":
                            constants[target.id] = value.args[0].value
            used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
            for name, text in constants.items():
                with self.subTest(module=module, constant=name):
                    self.assertIn(name, used, "an n_() text that is never shown")
                    self.assertFalse(re.search(r"\{\}|%[sd]|\{\d", text), "placeholders are named: " + text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and getattr(node.func, "attr", "") in {"paragraph", "heading", "disclosure", "checkbox"}:
                    labels = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and " " in a.value]
                    self.assertEqual(labels, [], "%s: literal text in a card: %r" % (module, [a.value for a in labels]))


if __name__ == "__main__":
    unittest.main()
