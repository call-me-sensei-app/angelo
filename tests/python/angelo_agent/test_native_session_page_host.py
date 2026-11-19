# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live session page through the real external host (break-it review 1 of the live page: M1, S2-S4, S6).

The real listener, ``external_mcp.Server``, ``ExternalHost`` and ``Controller`` of ``test_native_external_agent``'s
harness (its own tests are not run again here), with the real ``session_page`` and its real site:

* ``angelo_send``'s result carries ``live_page``, which opens, and the conversation says so with Stop sharing;
* the link dies when the app disconnects, reconnects with a new key, the file changes, the chat's document goes
  invalid, apps' access goes off or the app's access is revoked (``external_access``): ``session_page.session_on``,
  the one test every path uses;
* Stop sharing holds across a restart, for the app's new chat and a move, also when the chat is drawn first
  after a restart; only Share again brings a new link back;
* a page's defect never fails the app's message or stops the listener;
* the page shows this conversation's tokens and cost, never a plan's name or window.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import http.client
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_native_external_agent as agent_tests  # noqa: E402  (the real-host harness; its tests stay its own)
from angelo_agent import external_access, external_host, session_page, session_page_state  # noqa: E402
from angelo_agent.external_clients import AppRegistry  # noqa: E402


@contextlib.contextmanager
def notices():
    """``chat_notices`` as the notice tests import it: under stand-ins for Blender's modules, undone after."""
    from test_native_notice_resend import STUBS
    from angelo_agent import ui_text
    before = set(sys.modules)
    stubs = {name: mock.MagicMock(name=name) for name in STUBS}
    same = lambda message, context=None: message   # noqa: E731  English, as without Blender.
    stubs["bpy.app"].translations = SimpleNamespace(pgettext_iface=same, pgettext_tip=same, pgettext_rpt=same,
                                                    pgettext_data=same, locale="en_US")
    import angelo_agent
    console = angelo_agent.__dict__.pop("console", None)   # The real console, as the notice tests import it.
    with mock.patch.dict(ui_text._pgettext, clear=True), mock.patch.dict(sys.modules, stubs):
        sys.modules.pop("angelo_agent.console", None)
        from angelo_agent import chat_notices
        try:
            yield chat_notices
        finally:
            if console is not None:
                angelo_agent.console = console
            package = sys.modules.get("angelo_agent")
            for name in [name for name in sys.modules if name not in before and name.startswith("angelo_agent.")]:
                if package is not None and name.count(".") == 1:
                    package.__dict__.pop(name.split(".", 1)[1], None)


def opens(url):
    """The page's status as a browser on this computer gets it (404 once its link died)."""
    match = re.match(r"^http://127\.0\.0\.1:(\d+)(/s/[A-Za-z0-9_-]+/)$", url)
    try:
        connection = http.client.HTTPConnection("127.0.0.1", int(match.group(1)), timeout=5)
        connection.request("GET", match.group(2), headers={"Host": "127.0.0.1:" + match.group(1)})
        status = connection.getresponse().status
        connection.close()
        return status
    except OSError:
        return None   # Nothing listens: no site is left on the server.


@unittest.skipIf(sys.platform == "win32", agent_tests.LISTENER)
class LivePageThroughTheHostTests(agent_tests.AppTests):
    def setUp(self):
        import angelo_agent
        console = SimpleNamespace(submit=None)   # The harness replaces it; this lets it resolve when run alone.
        for patcher in (mock.patch.dict(sys.modules, {"angelo_agent.console": console}),
                        mock.patch.object(angelo_agent, "console", console, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        super().setUp()
        session_page.unregister()
        self.addCleanup(session_page.unregister)
        for patcher in (mock.patch.object(external_host, "_host", self.host),
                        mock.patch.object(session_page, "_feed_scene", lambda host: None)):   # No Blender exporter.
            patcher.start()
            self.addCleanup(patcher.stop)
        self.env.provider = agent_tests.TurnProvider(self.release, script=False)
        self.document = self.env.document

    while_parked = None   # A test's press while an app's read is parked in the host.

    def tick(self):
        super().tick()
        session_page.pump_safely(self.host)   # As external_host.pump runs it after the host's own pump.
        if self.while_parked is not None and self.host._agent_waiters:
            press, self.while_parked = self.while_parked, None
            press()

    def send(self, text="Make a cube"):
        result = self.call("angelo_send", {"message": text})
        self.assertFalse(result["isError"], result)
        return result["structuredContent"]

    def test_a_send_carries_the_live_page_and_its_chat_offers_stop_sharing(self):
        page = self.send()["live_page"]
        self.assertRegex(page, r"^http://127\.0\.0\.1:\d+/s/[A-Za-z0-9_-]{32}/$")
        self.assertEqual(opens(page), 200)
        controller = self.conversation()
        self.until(lambda: any((row.get("i18n") or [None])[0] == session_page.SHARED
                               for row in controller.state["messages"]))
        notice = next(row for row in controller.state["messages"] if (row.get("i18n") or [None])[0] == session_page.SHARED)
        with notices() as chat_notices:
            card = chat_notices._card(notice, "k", True, SimpleNamespace(), controller, ())
            self.assertIn(chat_notices.STOP_SHARING, [action for action, *_ in card["actions"]])
        self.assertEqual(self.send("And a sphere")["live_page"], page, "one link per conversation")

    def shared_notice(self, controller):
        """The conversation's latest share notice ("{app} can show this session on a live page…"), or None."""
        rows = [row for row in controller.state["messages"] if (row.get("i18n") or [None])[0] == session_page.SHARED]
        return rows[-1] if rows else None

    def test_the_share_notice_opens_the_page_in_the_persons_browser_only_while_it_is_live(self):
        """2.0.1 acceptance on 904e6ff: the share notice offered only Stop sharing, so the person could not open the
        page from Angelo. Open opens the conversation's own page, on the person's click, while it is live."""
        page = self.send()["live_page"]
        controller = self.conversation()
        self.until(lambda: self.shared_notice(controller) is not None)
        with notices() as chat_notices:
            bpy = sys.modules["bpy"]

            def actions():
                card = chat_notices._card(self.shared_notice(controller), "k", True, SimpleNamespace(), controller, ())
                return [action for action, *_ in card["actions"]]

            def press_open():
                bpy.ops.wm.url_open.reset_mock()
                chat_notices.dispatch(SimpleNamespace(), controller, {}, chat_notices.OPEN_PAGE, None)
                return [call.kwargs.get("url") for call in bpy.ops.wm.url_open.call_args_list]

            self.assertEqual(actions(), [chat_notices.OPEN_PAGE, chat_notices.STOP_SHARING], "Open first")
            bpy.ops.wm.url_open.assert_not_called()   # Nothing opens on its own: only the press.
            self.assertEqual(press_open(), [page], "the person's browser, through Angelo's own URL opening")
            self.assertEqual(opens(page), 200, "opening it changes nothing about the page")
            session_page.stop_sharing(controller)
            self.assertEqual(actions(), [], "after Stop sharing the notice offers neither")
            self.assertEqual(press_open(), [], "a stale card's press opens nothing")
            session_page.share_again(controller)
            again = self.send("Again")["live_page"]
            self.until(lambda: self.shared_notice(controller) is not None and chat_notices.OPEN_PAGE in actions())
            self.assertEqual(press_open(), [again], "the new link, never the old one")
            AppRegistry(self.state).disconnect(self.name)   # The page ends with the session.
            self.tick()
            self.assertEqual(actions(), [])
            self.assertEqual(press_open(), [])

    def page_get(self, page, name):
        """(status, content type, body) of ``name`` under the page, as the page's own browser gets it."""
        match = re.match(r"^http://127\.0\.0\.1:(\d+)(/s/[A-Za-z0-9_-]+/)$", page)
        connection = http.client.HTTPConnection("127.0.0.1", int(match.group(1)), timeout=5)
        try:
            connection.request("GET", match.group(2) + name, headers={"Host": "127.0.0.1:" + match.group(1)})
            response = connection.getresponse()
            return response.status, response.getheader("Content-Type"), response.read()
        finally:
            connection.close()

    def test_a_view_and_a_markdown_result_reach_the_open_page_through_the_real_build(self):
        """2.0.1 acceptance on f9f749b: Latest views stayed empty although the agent had checked a view. The page
        read the picture while its file was still being written (reserved empty, then filled) and never again, and
        listed it all the same, so the page asked for a picture it never got. A picture is listed only once the page
        can serve it, and one not readable yet is read again on the next tick, with no change in the chat."""
        import json
        from browser_web_preview_drag import png
        page = self.send()["live_page"]
        controller = self.conversation()
        self.release.set()
        self.until(lambda: not controller.turn_running())
        viewer = http.client.HTTPConnection("127.0.0.1", int(page.split(":")[2].split("/")[0]), timeout=5)
        self.addCleanup(viewer.close)
        viewer.request("GET", "/" + page.split("/", 3)[3] + "events",
                       headers={"Host": "127.0.0.1:" + page.split(":")[2].split("/")[0]})
        stream = viewer.getresponse()   # Kept: an event stream's response owns its socket; dropping it closes the page.
        self.addCleanup(stream.close)
        self.assertEqual(stream.status, 200, "a page is open")
        reference = controller.references.new_observation("ISO")   # Reserved empty, as a capture starts.
        result = ("I built the stall.\n\n- **Counter:** reused the default Cube.\n- **Awning:** *eight* stripes, "
                  "`Material.001` kept.\n\nAll checks passed.")
        controller.state["messages"] += [
            {"role": "activity", "tool": "angelo_view", "status": "completed", "result": {"views": [{"id": reference["id"]}]}},
            {"role": "assistant", "text": result}]
        controller.version += 1

        def state():
            self.tick()
            return json.loads(self.page_get(page, "state.json")[2])
        self.until(lambda: state()["result"] == result)
        self.assertEqual(state()["pictures"], [], "not listed while the page can't serve it")
        self.assertEqual(self.page_get(page, "picture/" + reference["id"])[0], 404)
        Path(reference["path"]).write_bytes(png(320, 200, (40, 160, 170)))   # The capture is written.
        self.until(lambda: state()["pictures"] == [reference["id"]])   # No change in the chat: the next tick.
        status, kind, body = self.page_get(page, "picture/" + reference["id"])
        self.assertEqual((status, kind, body[:8]), (200, "image/png", b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(state()["result"], result, "the agent's markdown as written: the page renders it safely")
        self.assertIn(result, [row.get("text") for row in state()["rows"]])

    def test_the_link_dies_with_the_session_whatever_ends_it(self):
        enders = {
            "the app disconnects": lambda: AppRegistry(self.state).disconnect(self.name),
            "it reconnects with a new key": lambda: AppRegistry(self.state).connect("claude_code"),
            "the file changes": lambda: setattr(self.env, "document", "another-file"),
            "its document goes invalid": lambda: setattr(self.conversation(), "document_invalid", True),
            "apps' access goes off": lambda: self.host.stop("Access is off"),
            "its access is revoked": lambda: external_access.revoke(self.host, self.name, "Revoked"),
        }
        for why, end in enders.items():
            with self.subTest(why):
                self.release.set()   # Each turn ends at once: the next way of ending starts from an idle chat.
                page = self.send()["live_page"]
                controller = self.conversation()
                self.assertEqual(opens(page), 200)
                end()
                self.tick()
                self.assertNotEqual(opens(page), 200, "the link died: " + why)
                self.assertFalse(session_page.pages().live(controller.state["id"]))
                self.until(lambda: not controller.turn_running())
                self.tearDown_session()

    def tearDown_session(self):
        """A fresh connection and file for the next way of ending (the harness's own state, restored)."""
        self.env.document = self.document
        AppRegistry(self.state).connect("claude_code")
        self.host.reload_apps(force=True)
        if self.host.server is None:
            self.host.pump()
        self.host.mapping.pop(self.name, None)   # The next send starts a fresh conversation for the app.

    def test_stop_sharing_holds_across_a_restart_and_only_share_again_brings_a_new_link(self):
        page = self.send()["live_page"]
        controller = self.conversation()
        session_page.stop_sharing(controller)
        self.assertNotEqual(opens(page), 200)
        session_page.unregister()   # Angelo quits and starts again: the person's choice is read back.
        self.assertNotIn("live_page", self.send("Next"))
        session_page.share_again(controller)
        again = self.send("Again")["live_page"]
        self.assertNotEqual(again, page, "a new token")
        self.assertEqual(opens(again), 200)

    def test_after_a_restart_the_chat_drawn_first_still_reads_the_persons_choice(self):
        """Review 2, N2: the conversation's notice, drawn before any send after a restart, reads the host's store."""
        self.send()
        controller = self.conversation()
        session_page.stop_sharing(controller)
        session_page.unregister()   # Angelo quits and starts again.
        with notices() as chat_notices:   # The person opens the chat: its notice asks whether sharing is stopped.
            self.assertTrue(chat_notices._page_stopped(controller))
            self.assertFalse(chat_notices._page_live(controller))
        self.assertNotIn("live_page", self.send("Next"), "drawing the chat never forgets the choice")

    def test_stop_sharing_holds_for_the_apps_new_chat_and_a_move_until_share_again(self):
        """Review 2, N3: the person's choice is about the app, so its next conversation stays unshared and says so."""
        self.send()
        session_page.stop_sharing(self.conversation())
        self.release.set()
        self.until(lambda: not self.conversation().turn_running())
        before = self.conversation().state["id"]
        self.assertFalse(self.call("angelo_new_chat", {})["isError"])
        self.assertNotIn("live_page", self.send("Next"), "a new chat is the same app: still stopped")
        fresh = self.conversation()
        self.assertNotEqual(fresh.state["id"], before)
        self.assertTrue(any((row.get("i18n") or [None])[0] == session_page.STOPPED for row in fresh.state["messages"]),
                        "the new conversation offers Share again itself")
        self.until(lambda: not fresh.turn_running())
        self.env.document = ("doc-b", 1)   # The window's file changes: the app's next message moves on.
        self.assertNotIn("live_page", self.send("After the move"))
        moved = self.conversation()
        self.assertNotEqual(moved.state["id"], fresh.state["id"])
        session_page.share_again(moved)
        self.until(lambda: not moved.turn_running())
        self.assertEqual(opens(self.send("Shared again")["live_page"]), 200)

    def test_a_page_defect_never_fails_the_message_or_stops_the_listener(self):
        from angelo_agent import swallowed_errors
        with mock.patch.object(session_page_state, "build", side_effect=RuntimeError("defect")), \
                swallowed_errors.expect(RuntimeError):
            sent = self.send()
            self.assertNotIn("live_page", sent, "no page, and the message went")
            self.assertIn("could not start (RuntimeError)", sent["live_page_ended"]["why"], "the app is told why")
            self.tick()
        self.assertIsNotNone(self.host.server, "the listener goes on")
        self.assertFalse(self.call("angelo_read", {})["isError"])
        page = self.send("And a sphere")["live_page"]   # The next message gets its page.
        site = session_page.pages().site(self.conversation().state["id"])
        # Any text the state may hold is escaped (an app's own message cannot carry a lone surrogate: its transport
        # refuses one; the agent's words and Angelo's notices can).
        site.publish({"status": "ready", "result": "lone \udcff surrogate"})
        self.assertEqual(opens(page), 200)

    def test_the_page_shows_this_conversations_tokens_and_cost_never_a_plan(self):
        self.send()
        controller = self.conversation()
        entry = session_page.pages().entry(controller.state["id"])
        with mock.patch("angelo_agent.usage_view.bar", return_value={
                "tokens": "8.2k tokens", "money": "≈ $0.06", "plan": "Included in your Claude Max plan",
                "window": "5-hour limit: 40% used", "cached": "80% cached", "ring": 0.4, "warn": False}):
            entry.pop("usage_seen", None)
            state = session_page._build(controller, entry)
        self.assertEqual(state["usage"], {"tokens": "8.2k tokens", "money": "≈ $0.06"})


    def test_a_send_with_a_goal_keeps_its_page_publishing(self):
        """The 2.0.1 acceptance rerun (3b2b492bb4a): every page died at its first publish, because the page built its
        turn through an unbound AgentChannels._state that read the host's goals. The real send, pump and build."""
        sent = self.call("angelo_send", {"message": "Add ten cubes", "goal": "Ten cubes on a circle"})
        self.assertFalse(sent["isError"], sent)
        page = sent["structuredContent"]["live_page"]
        controller = self.conversation()
        for _ in range(20):
            self.tick()
        self.assertEqual(opens(page), 200, "the page still answers after its publishes")
        entry = session_page.pages().entry(controller.state["id"])
        self.assertIsNotNone(entry, "the page was never withdrawn by a build error")
        self.assertEqual(session_page._build(controller, entry)["conversation"], controller.state["id"],
                         "a build of a conversation whose send carried a goal never raises")

    def test_a_page_that_ends_after_an_error_says_so_to_the_app_and_the_person(self):
        """Since 3b2b492bb4a's crash: a page pump_safely ended kept a dead link nobody was told about."""
        from angelo_agent import swallowed_errors
        page = self.send()["live_page"]
        controller = self.conversation()
        entry = session_page.pages().entry(controller.state["id"])
        with mock.patch.object(session_page_state, "build", side_effect=RuntimeError("defect")), \
                mock.patch.object(session_page, "INTERVAL", 0.0), mock.patch.object(entry["site"], "viewers", 1), \
                swallowed_errors.expect(RuntimeError):
            controller.version += 1   # A change to publish to the page someone has open.
            self.tick()
        self.assertNotEqual(opens(page), 200)
        status = self.call("angelo_status")["structuredContent"]
        self.assertIn("stopped after an error (RuntimeError)", status["live_page_ended"]["why"])
        self.assertIn("next angelo_send starts a new one", status["live_page_ended"]["next"])
        from angelo_agent.session_page_ended import FAILED
        said = [row for row in controller.state["messages"] if (row.get("i18n") or [None])[0] == FAILED]
        self.assertEqual(len(said), 1, "the person's notice, once")
        again = self.send("And a sphere")
        self.assertEqual(opens(again["live_page"]), 200, "the next message starts a new page")
        self.assertNotIn("live_page_ended", self.call("angelo_status")["structuredContent"])

    def test_a_swallowed_defect_fails_the_harness(self):
        """The harness's own check: a catch-all that swallows an error nobody expected is caught (and cleared)."""
        from angelo_agent import swallowed_errors
        swallowed_errors.report("a probe", AttributeError("x"), quiet=True)
        self.assertEqual(swallowed_errors.caught(), ["a probe: AttributeError: x"])
        swallowed_errors.reset()
        with swallowed_errors.expect(AttributeError):
            swallowed_errors.report("a probe", AttributeError("x"), quiet=True)
        self.assertEqual(swallowed_errors.caught(), [])


    def test_a_send_that_waits_returns_its_live_page_and_every_field_of_the_send(self):
        """The 2.0.1 final smoke (904e6ff9724): Claude Code sends with wait_seconds, and its answer had no live_page."""
        plain = self.send()   # The send's own answer: the fields a waiting send must keep.
        self.release.set()
        self.until(lambda: not self.conversation().turn_running())
        waited = self.call("angelo_send", {"message": "And a sphere", "wait_seconds": 5})
        self.assertFalse(waited["isError"], waited)
        value = waited["structuredContent"]
        self.assertEqual(value["live_page"], plain["live_page"], "starting a session returns its link")
        for key in plain:
            with self.subTest(key=key):
                self.assertIn(key, value, "a field of the send's answer was dropped by the wait")
    def test_a_link_the_person_stopped_sharing_during_the_wait_is_not_offered(self):
        """The review of b3726b1c11f (NIT-1): the page ended for a reason other than an error while the send waited.
        Failed on b3726b1c11f: the waiting send's answer still offered the dead link."""
        page = self.send()["live_page"]
        self.assertEqual(self.call("angelo_read", {})["structuredContent"]["live_page"], page, "every read says it")

        def press():   # The person's Stop sharing, then the turn ends.
            session_page.stop_sharing(self.conversation())
            self.release.set()
        self.while_parked = press
        waited = self.call("angelo_send", {"message": "And a sphere", "wait_seconds": 5})
        self.assertFalse(waited["isError"], waited)
        self.assertIsNone(self.while_parked, "the person pressed during the wait")
        self.assertNotIn("live_page", waited["structuredContent"], "a link that no longer opens")
        self.assertNotEqual(opens(page), 200)
        self.assertNotIn("live_page", self.call("angelo_read", {})["structuredContent"])

for _name in dir(agent_tests.AppTests):   # The harness's own tests run in their own file, not again here.
    if _name.startswith("test_") and _name not in vars(LivePageThroughTheHostTests):
        setattr(LivePageThroughTheHostTests, _name, None)


if __name__ == "__main__":
    unittest.main()
