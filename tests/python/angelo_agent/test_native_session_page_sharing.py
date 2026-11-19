# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live session page's sharing, bounds and the person's own preview (break-it review 2 of the live page).

* N3: the person's Stop sharing is a choice about an app, so it holds for that app's next conversation too (a new
  chat, a move after a file change), and only Share again undoes it;
* the choice is kept merged with what another Angelo wrote, never over it;
* a rotated key is another connection: its message gets a new link;
* the send path reads no picture while nobody looks; the first page that looks gets them;
* N4: the viewer cap holds under many pages opening at once, and a closed page stops counting promptly;
* the server stops off the caller's thread (closing the last page never waits for its loop);
* N1: the person's Stop ends their own tab while pages hold the exporter, their next start serves a new one, and no
  module reads the exporter's server except through the person's holder.

``test_native_session_page_host`` covers the same through the real external host (N2, N3).
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import http.client
import json
import re
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import host_waits  # noqa: E402
from test_native_session_page import FakeSite, REQUEST, request  # noqa: E402
from angelo_agent import session_page, session_page_site, web_preview_server  # noqa: E402
from angelo_agent.session_page_site import SessionSite  # noqa: E402

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def controller(identity, label="codex", version=1):
    return SimpleNamespace(state={"id": identity, "messages": []}, version=version, external_client=label)


class SharingTests(unittest.TestCase):
    def setUp(self):
        self.kept = []
        self.pages = session_page.Pages(make_site=FakeSite, build=lambda controller, page: {"v": controller.version},
                                        on_stopped=lambda label, stopped: self.kept.append((label, stopped)))

    def test_the_persons_stop_holds_for_the_app_across_its_conversations(self):
        first = self.pages.link(controller("c1"), "codex")
        self.assertIsNotNone(first)
        self.assertTrue(self.pages.stop_sharing("codex"))
        self.assertTrue(self.pages.site("c1") is None)
        self.assertIsNone(self.pages.link(controller("c2"), "codex"), "a new chat or a move: still stopped")
        self.assertFalse(self.pages.shared("codex"))
        self.assertIsNotNone(self.pages.link(controller("c3", "claude-code"), "claude-code"), "another app's own")
        self.pages.share_again("codex")
        self.assertIsNotNone(self.pages.link(controller("c2"), "codex"))
        self.assertEqual(self.kept, [("codex", True), ("codex", False)], "each change kept as it happens")

    def test_a_rotated_key_is_another_connection_with_a_new_link(self):
        conversation = controller("c1")
        old = self.pages.link(conversation, "codex", key="k1")
        site = self.pages.site("c1")
        new = self.pages.link(conversation, "codex", key="k2")
        self.assertNotEqual(new, old)
        self.assertTrue(site.closed, "the old connection's page ends")

    def test_the_first_page_that_looks_gets_a_full_publish(self):
        self.pages.link(controller("c1"), "codex")
        site = self.pages.site("c1")
        self.assertEqual(len(site.published), 1, "the page has content from the start")
        site.viewers = 1
        self.pages.pump(lambda identity: controller(identity))
        self.assertEqual(len(site.published), 2, "unchanged, yet the first viewer gets what the send path skipped")


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix="smcp-pages-")
        self.path = Path(self.folder) / session_page.STOPPED_FILE

    def test_the_choice_is_kept_by_app_and_merged_with_another_angelos(self):
        session_page._keep_stopped(self.folder, "codex", True)
        self.path.write_text(json.dumps({"apps": ["codex", "cursor"]}), encoding="utf-8")   # Another Angelo wrote.
        session_page._keep_stopped(self.folder, "claude-code", True)
        self.assertEqual(session_page._read_stopped(self.folder), ["claude-code", "codex", "cursor"])
        session_page._keep_stopped(self.folder, "codex", False)
        self.assertEqual(session_page._read_stopped(self.folder), ["claude-code", "cursor"])
        if sys.platform != "win32":
            self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_the_pages_always_read_the_hosts_own_store(self):
        from angelo_agent import external_host
        session_page._keep_stopped(self.folder, "codex", True)
        host = SimpleNamespace(env=SimpleNamespace(state_directory=lambda: self.folder))
        session_page.unregister()
        self.addCleanup(session_page.unregister)
        with mock.patch.object(external_host, "_host", None):
            self.assertIsNone(session_page.pages(), "no host: no pages and no choice to guess")
        with mock.patch.object(external_host, "_host", host):
            self.assertFalse(session_page.pages().shared("codex"), "a notice drawn after a restart reads the store")


class PicturesTests(unittest.TestCase):
    def test_the_send_path_reads_no_picture_while_nobody_looks(self):
        from angelo_agent import external_host
        reads = []
        references = SimpleNamespace(attached_image=lambda identity: reads.append(identity) or Path("/nonexistent"))
        conversation = SimpleNamespace(state={"id": "c1", "provider": "codex", "messages": [
            {"role": "activity", "tool": "angelo_view", "status": "completed", "result": {"views": [{"id": "v1"}]}}]},
            version=1, references=references, busy=False, telemetry=SimpleNamespace(directory=self.folder()),
            turn_started=None, external_client="codex")
        entry = {"site": FakeSite(), "label": "codex", "app": "Codex", "started": 0.0}
        entry["site"].has_picture = lambda identity: False
        with mock.patch.object(external_host, "_host", None), \
                mock.patch("angelo_agent.external_agent.conversation_state",
                           lambda c, asked_goal=None: {"turn": "idle"}):
            state = session_page._build(conversation, entry)
            self.assertEqual(state["pictures"], [], "listed only once the page can serve it: nobody looks")
            self.assertEqual(reads, [], "nobody looks: no picture read on the send path")
            entry["site"].viewers = 1
            session_page._build(conversation, entry)
        self.assertEqual(reads, ["v1"])

    def folder(self):
        return tempfile.mkdtemp(prefix="smcp-usage-")


class BoundsTests(unittest.TestCase):
    def site(self):
        site = SessionSite()
        self.addCleanup(site.close)
        return site

    def stream(self, site):
        connection = http.client.HTTPConnection("127.0.0.1", site.port, timeout=5)
        connection.request("GET", site.path + "events", headers={"Host": "127.0.0.1:%d" % site.port})
        return connection, connection.getresponse()

    def test_the_viewer_cap_holds_when_many_pages_open_at_once(self):
        site, statuses, connections = self.site(), [], []
        count = session_page_site.MAX_VIEWERS * 5
        gate = threading.Barrier(count)

        def open_one():
            connection = http.client.HTTPConnection("127.0.0.1", site.port, timeout=10)
            gate.wait()
            connection.request("GET", site.path + "events", headers={"Host": "127.0.0.1:%d" % site.port})
            statuses.append(connection.getresponse().status)
            connections.append(connection)
        threads = [threading.Thread(target=open_one) for _ in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.addCleanup(lambda: [connection.close() for connection in connections])
        self.assertEqual(statuses.count(200), session_page_site.MAX_VIEWERS)
        self.assertEqual(statuses.count(429), count - session_page_site.MAX_VIEWERS)
        self.assertLessEqual(site.viewers, session_page_site.MAX_VIEWERS)

    def test_a_closed_page_stops_counting_at_once(self):
        site = self.site()
        opened = [self.stream(site) for _ in range(session_page_site.MAX_VIEWERS)]
        host_waits.until(lambda: site.viewers == session_page_site.MAX_VIEWERS, what="the pages opening")
        for connection, response in opened:   # Reloads: the browser drops each stream; nothing is written meanwhile.
            response.close()   # The response holds the socket's file too: both close, as a browser's tab does.
            connection.close()
        host_waits.until(lambda: site.viewers == 0, cap=session_page_site.GONE_POLL * 4 + 1,
                         what="closed pages no longer counted")
        connection, response = self.stream(site)
        self.addCleanup(connection.close)
        self.assertEqual(response.status, 200)

    def test_the_server_stops_off_the_callers_thread(self):
        stopped_on = []
        real = web_preview_server._Server.shutdown

        def shutdown(server):
            stopped_on.append(threading.current_thread())
            real(server)
        with mock.patch.object(web_preview_server._Server, "shutdown", shutdown):
            site = SessionSite()
            site.close()
            host_waits.until(lambda: stopped_on, what="the server stopping")
        self.assertIsNot(stopped_on[0], threading.current_thread(), "a send or new chat never waits for the loop")


class PersonsPreviewTests(unittest.TestCase):
    """N1: the person's own preview tab follows only the person's holder."""

    @classmethod
    def setUpClass(cls):
        from test_native_web_preview_bake import import_preview
        cls.preview, undo = import_preview()
        cls.addClassCleanup(undo)

    def setUp(self):
        preview = self.preview
        self.opened = []
        for patcher in (mock.patch.object(preview, "refresh", lambda window: {}),
                        mock.patch.object(preview, "open_page", self.opened.append),
                        mock.patch.object(preview.private_roots, "mkdtemp", lambda prefix: tempfile.mkdtemp(prefix=prefix)),
                        mock.patch.object(preview.owned_paths, "remove_tree", lambda folder: None),
                        mock.patch.object(preview.web_environment, "stop", lambda: None),
                        mock.patch.object(preview, "_fonts", lambda: {}),
                        mock.patch.object(preview, "_strings", lambda: {}),
                        mock.patch.object(preview.scene_changes, "watch", lambda handler: None),
                        mock.patch.object(preview.scene_changes, "unwatch", lambda handler: None),
                        mock.patch.object(preview.scene_changes, "on_edit", lambda handler: None),
                        mock.patch.object(preview.scene_changes, "off_edit", lambda handler: None)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(preview._shutdown)
        self.window = SimpleNamespace(as_pointer=lambda: 1)

    def test_the_persons_stop_ends_their_tab_while_pages_hold_and_a_new_start_serves_a_new_one(self):
        preview = self.preview
        site = SessionSite()   # A session page on the same server, as when pages hold the exporter.
        self.addCleanup(site.close)
        first = preview.start(self.window, REQUEST)["url"]
        preview.hold(preview.PAGES, self.window, REQUEST)
        self.assertEqual(preview.person_server().url, first)
        preview.stop()
        port, path = re.match(r"^http://127\.0\.0\.1:(\d+)(/.*)$", first).groups()
        self.assertEqual(request(int(port), path)[0], 404, "the person's tab no longer shows their scene")
        self.assertIsNone(preview.person_server(), "the agent's WEB view never points at a preview they stopped")
        self.assertIsNone(preview.url())
        self.assertTrue(preview.exporting(), "the pages keep their scene")
        again = preview.start(self.window, REQUEST)["url"]
        self.assertNotEqual(again, first, "a new tab, a new token")
        self.assertEqual(self.opened, [first, again])
        self.assertEqual(request(int(port), re.match(r"^http://127\.0\.0\.1:\d+(/.*)$", again).group(1))[0], 200)

    def test_no_module_reads_the_exporters_server_but_through_the_persons_holder(self):
        readers = []
        for path in sorted(ADDON.rglob("*.py")):
            if path.name in ("web_preview.py", "session_page.py"):   # The exporter; the pages' own holder.
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"\bweb_preview\._state\b", line):
                    readers.append("%s:%d" % (path.name, number))
        self.assertEqual(readers, [], "use web_preview.person_server(), running() or url()")


if __name__ == "__main__":
    unittest.main()
