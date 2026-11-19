# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live session page an app's agent session shares (2.0.1, doc/angelo/external_mcp.rst, "Live session page").

* One server: the page is a site on the live web preview's own loopback server (``web_preview_server``), never a
  second listener, behind its own unguessable token, with the preview's guards (Host, no-store, nosniff, a strict
  policy); read-only (GET and HEAD only); gone when its session ends or the person stops sharing.
* What it shows is built on the main thread from the conversation (``session_page_state``): the status, the current
  step and the stream (what the app sent, the agent's words, each tool's name and status: never a tool's result
  text), the goal's round and score, the time, the usage and cost, the result and the latest pictures by id; private
  paths projected away; nothing from another chat.
* Published only when the conversation changed, at most twice a second, and only while a page is open.
No Blender here; the browser proof is ``browser_session_page.py`` and the real host's is
``test_native_session_page_host``.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import http.client
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import session_page_state as page_state  # noqa: E402
from angelo_agent import document_offer  # noqa: E402
from angelo_agent.session_page_site import SessionSite  # noqa: E402
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402


def request(port, path, method="GET", host=None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        connection.request(method, path, body=body, headers={"Host": host or "127.0.0.1:%d" % port})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read(), response
    finally:
        connection.close()


class ServerTests(unittest.TestCase):
    def site(self):
        site = SessionSite(marks={"<!--WORDMARK-->": "<svg id='mark'></svg>"})
        self.addCleanup(site.close)
        return site

    def test_the_page_is_a_site_on_the_previews_own_server(self):
        preview = PreviewServer()
        self.addCleanup(preview.close)
        site = self.site()
        self.assertEqual(site.port, preview.port, "one loopback server, never a second listener")
        self.assertRegex(site.url, r"^http://127\.0\.0\.1:%d/s/[A-Za-z0-9_-]{22,}/$" % site.port)
        preview.close()
        self.assertEqual(request(site.port, site.path)[0], 200, "the preview ending leaves the page")
        port = site.port
        site.close()
        with self.assertRaises(OSError):
            request(port, "/")

    def test_only_its_own_paths_read_only_with_the_previews_guards(self):
        site = self.site()
        status, kind, body, response = request(site.port, site.path)
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith("text/html"))
        self.assertIn(b"<svg id='mark'></svg>", body)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        policy = response.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", policy)
        self.assertIn("form-action 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertEqual(request(site.port, site.path, host="evil.example:%d" % site.port)[0], 403)
        self.assertEqual(request(site.port, "/s/wrong-token/")[0], 404)
        self.assertEqual(request(site.port, "/%s/" % site.token)[0], 404, "never the preview's own form of path")
        for name in ("../session_page.py", "preview/../web_preview_server.py", ".hidden", "preview/app.js",
                     "preview/vendor/../../web_preview.py"):
            self.assertEqual(request(site.port, site.path + name)[0], 404, name)
        self.assertEqual(request(site.port, site.path + "preview/look.js")[0], 200, "the preview's look, shared")
        for method, status in (("POST", 405), ("PUT", 501), ("DELETE", 501)):
            self.assertEqual(request(site.port, site.path + "state.json", method=method, body=b"{}")[0], status, method)
        self.assertEqual(request(site.port, "/no-such/capture", method="POST", body=b"{}",
                                 host="evil.example:%d" % site.port)[0], 403, "the Host is checked before any route")

    def test_a_session_page_takes_a_bounded_number_of_open_pages(self):
        from angelo_agent import session_page_site
        site = self.site()
        streams = []
        for _ in range(session_page_site.MAX_VIEWERS):
            connection = http.client.HTTPConnection("127.0.0.1", site.port, timeout=5)
            self.addCleanup(connection.close)
            connection.request("GET", site.path + "events", headers={"Host": "127.0.0.1:%d" % site.port})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            response.fp.readline()
            streams.append(response)
        self.assertEqual(request(site.port, site.path + "events")[0], 429, "past the bound: refused, not a thread")

    def test_each_page_has_its_own_unguessable_token(self):
        first, second = self.site(), self.site()
        self.assertNotEqual(first.token, second.token)
        self.assertGreaterEqual(len(first.token), 22)
        self.assertEqual(request(first.port, first.path + "state.json")[0], 200)
        self.assertEqual(request(first.port, "/s/%s/state.json" % second.token[:-1])[0], 404, "the whole token")

    def test_state_is_pushed_and_the_link_dies_with_the_session(self):
        site = self.site()
        site.publish({"status": "running", "step": "angelo_create_cube"})
        state = json.loads(request(site.port, site.path + "state.json")[2])
        self.assertEqual((state["status"], state["version"]), ("running", 1))
        connection = http.client.HTTPConnection("127.0.0.1", site.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", site.path + "events", headers={"Host": "127.0.0.1:%d" % site.port})
        response = connection.getresponse()
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(response.fp.readline(), b"event: version\n")
        self.assertEqual(response.fp.readline(), b"data: 1\n")
        self.assertEqual(site.viewers, 1, "an open page counts: the main thread publishes only while one is")
        port, path = site.port, site.path
        preview = PreviewServer()   # Another site keeps the server up, so the page's own refusal is what is seen.
        self.addCleanup(preview.close)
        site.close()
        lines = [response.fp.readline() for _ in range(3)]
        self.assertIn(b"event: closed\n", lines)
        self.assertEqual(request(port, path)[0], 404, "the link is dead")

    def test_pictures_and_the_scene_by_their_own_names_only(self):
        site = self.site()
        whole = b"\x89PNG\r\n\x1a\n" + b"pixels" + b"\x00\x00\x00\x00IEND\xaeB`\x82"
        self.assertFalse(site.publish_picture("view-half", whole[:14]), "a capture still being written is refused")
        self.assertFalse(site.has_picture("view-half"))
        self.assertTrue(site.publish_picture("view-1a2b", whole))
        status, kind, body, _response = request(site.port, site.path + "picture/view-1a2b")
        self.assertEqual((status, kind, body), (200, "image/png", whole))
        for name in ("picture/other", "picture/../state.json", "picture/view-1a2b/x"):
            self.assertEqual(request(site.port, site.path + name)[0], 404, name)
        self.assertEqual(request(site.port, site.path + "scene.glb")[0], 404, "no scene yet")
        site.scene_feed = lambda: b"glTF-bytes"
        self.assertEqual(request(site.port, site.path + "scene.glb")[2], b"glTF-bytes")


ROOTS = (("/Users/jack", "<home>"),)


def row(role, **fields):
    return dict(fields, role=role)


class StateTests(unittest.TestCase):
    def build(self, messages, **facts):
        defaults = dict(conversation="c1", version=7, turn="idle", goal=None, usage=None, now=100.0,
                        started=40.0, turn_started=None, app="Codex", roots=ROOTS, scene_version=3)
        defaults.update(facts)
        return page_state.build(messages, **defaults)

    def test_the_status_reads_as_the_person_would(self):
        sent = [row("user", text="Make a cube", request_kind="external_task")]
        self.assertEqual(self.build([])["status"], "ready")
        self.assertEqual(self.build(sent, turn="running")["status"], "running")
        self.assertEqual(self.build(sent, turn="waiting_for_approval")["status"], "waiting_for_approval")
        self.assertEqual(self.build(sent, turn="waiting_for_person")["status"], "waiting_for_person")
        done = self.build(sent + [row("assistant", text="Made the cube.")])
        self.assertEqual((done["status"], done["result"]), ("done", "Made the cube."))
        self.assertIsNone(self.build(sent, turn="running")["result"], "no result while it works")

    def test_the_stream_shows_what_happened_without_results_paths_or_private_rows(self):
        messages = [row("user", text="Save it to /Users/jack/secret/scene.blend", request_kind="external_task"),
                    row("thinking", text="private reasoning"),
                    row("activity", tool="angelo_write_file", status="completed",
                        result={"path": "/Users/jack/secret/out.txt", "ok": True}),
                    row("assistant", text="Wrote /Users/jack/secret/out.txt"),
                    row("system", text="Only you see this", i18n=[document_offer.OFFER, {}])]   # The person's card.
        state = self.build(messages)
        text = json.dumps(state)
        self.assertNotIn("/Users/jack", text)
        self.assertNotIn("private reasoning", text)
        self.assertNotIn("Only you see this", text)
        self.assertNotIn('"result": {', text)
        tools = [entry for entry in state["rows"] if entry["role"] == "tool"]
        self.assertEqual(tools, [{"i": 2, "role": "tool", "tool": "angelo_write_file", "status": "done"}])
        self.assertIn("<home>/secret/out.txt", text)

    def test_the_step_is_the_tool_working_now(self):
        messages = [row("activity", tool="angelo_inspect_scene", status="completed", result={}),
                    row("activity", tool="angelo_create_cube", status="running")]
        self.assertEqual(self.build(messages, turn="running")["step"], "angelo_create_cube")
        self.assertIsNone(self.build(messages[:1])["step"])

    def test_goal_time_usage_and_the_latest_pictures(self):
        goal = {"objective": "A tidy desk", "status": "active", "pass_at": 0.8, "best": 0.62,
                "rounds": [{"n": 1, "score": 0.4}, {"n": 2, "score": 0.62}]}
        usage = {"tokens": "12.4k tokens", "money": "≈ $0.18", "plan": None, "window": None, "cached": "80% cached"}
        views = [row("activity", tool="angelo_view", status="completed", result={"views": [{"id": "v%d" % n}]})
                 for n in range(8)]
        state = self.build(views, goal=goal, usage=usage, turn="running", turn_started=70.0)
        self.assertEqual(state["goal"], {"objective": "A tidy desk", "status": "active", "round": 2, "score": 0.62,
                                         "best": 0.62, "pass_at": 0.8})
        self.assertEqual(state["usage"], {"tokens": "12.4k tokens", "money": "≈ $0.18"},
                         "this conversation's tokens and cost only (review 1, S4)")
        self.assertEqual((state["elapsed"], state["turn_elapsed"]), (60, 30))
        self.assertEqual(state["pictures"], ["v7", "v6", "v5", "v4", "v3", "v2"], "the latest views, newest first")
        self.assertEqual(state["scene_version"], 3)
        self.assertEqual(state["app"], "Codex")

    def test_only_this_conversation_and_never_an_account(self):
        state = self.build([row("assistant", text="Hi")])
        self.assertEqual(set(state), {"conversation", "app", "status", "step", "rows", "result", "goal", "usage",
                                      "elapsed", "turn_elapsed", "pictures", "scene_version", "strings"})
        self.assertEqual(state["conversation"], "c1")


class FakeSite:
    made = 0

    def __init__(self, marks=None):
        FakeSite.made += 1
        self.url = "http://127.0.0.1:1/s/token-%d/" % FakeSite.made
        self.viewers, self.published, self.closed, self.scene_feed = 0, [], False, None

    def publish(self, state):
        self.published.append(state)

    def publish_picture(self, identity, data):
        pass

    def close(self):
        self.closed = True


class RegistryTests(unittest.TestCase):
    def setUp(self):
        from types import SimpleNamespace
        from angelo_agent import session_page
        self.clock = [100.0]
        self.pages = session_page.Pages(make_site=FakeSite, build=lambda controller, page: {"v": controller.version},
                                        clock=lambda: self.clock[0])
        self.controller = SimpleNamespace(state={"id": "c1", "messages": []}, version=1)
        self.alive = True
        self.lookup = lambda identity: (self.controller if self.alive else None)

    def site(self):
        return self.pages.site("c1")

    def test_one_link_per_conversation_until_its_session_ends(self):
        url = self.pages.link(self.controller, "codex")
        self.assertEqual(self.pages.link(self.controller, "codex"), url)
        self.assertEqual(self.site().published, [{"v": 1}], "the page has its content from the start")
        site = self.site()
        self.alive = False   # The app disconnected, the conversation moved or closed.
        self.pages.pump(self.lookup)
        self.assertTrue(site.closed)
        self.assertIsNone(self.pages.site("c1"))

    def test_stopping_sharing_ends_the_link_and_the_apps_next_send_does_not_bring_it_back(self):
        self.pages.link(self.controller, "codex")
        site = self.site()
        self.assertTrue(self.pages.stop_sharing("codex"))
        self.assertTrue(site.closed)
        self.assertIsNone(self.pages.link(self.controller, "codex"), "the person's choice holds")
        self.assertFalse(self.pages.shared("codex"))
        self.pages.share_again("codex")   # Only the person's press.
        self.assertNotEqual(self.pages.link(self.controller, "codex"), site.url, "a new token")

    def test_published_only_on_change_at_most_twice_a_second_and_only_while_viewed(self):
        self.pages.link(self.controller, "codex")
        site = self.site()
        self.controller.version = 2
        self.pages.pump(self.lookup)
        self.assertEqual(len(site.published), 1, "nobody is looking: nothing is built")
        site.viewers = 1
        self.pages.pump(self.lookup)
        self.assertEqual(site.published[-1], {"v": 2})
        self.pages.pump(self.lookup)
        self.assertEqual(len(site.published), 2, "unchanged: nothing")
        self.controller.version = 3
        self.clock[0] += 0.2
        self.pages.pump(self.lookup)
        self.assertEqual(len(site.published), 2, "at most twice a second")
        self.clock[0] += 0.4
        self.pages.pump(self.lookup)
        self.assertEqual(site.published[-1], {"v": 3})


REQUEST = {"meshes": "AUTO", "textures": "AUTO", "merge": True, "selection_only": False}


class ExporterHoldersTests(unittest.TestCase):
    """Break-it review 1 of the live page, M2 and S1: pages hold the live preview's exporter apart from the person's
    preview; the File menu, Stop and the browser follow only the person; a page's hold exports at the timer, never in
    the caller's tick."""

    @classmethod
    def setUpClass(cls):
        from test_native_web_preview_bake import import_preview
        cls.preview, undo = import_preview()
        cls.addClassCleanup(undo)

    def setUp(self):
        import tempfile
        from unittest import mock
        preview = self.preview
        self.refreshed, self.opened = [], []
        for patcher in (mock.patch.object(preview, "refresh", lambda window: self.refreshed.append(window) or {}),
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
        from types import SimpleNamespace
        self.window = SimpleNamespace(as_pointer=lambda: 1)

    def test_pages_hold_the_exporter_apart_from_the_persons_preview(self):
        preview = self.preview
        preview.hold(preview.PAGES, self.window, REQUEST)
        self.assertTrue(preview.exporting())
        self.assertFalse(preview.running(), "the File menu still offers the person their own preview")
        self.assertIsNone(preview.url())
        self.assertEqual(self.refreshed, [], "no export in the caller's tick: the timer exports under its gates")
        self.assertIsNotNone(preview._state["dirty_at"])
        started = preview.start(self.window, REQUEST)
        self.assertEqual(self.opened, [started["url"]], "the person's start opens their browser while pages hold")
        self.assertTrue(preview.running())
        preview.release(preview.PAGES)
        self.assertTrue(preview.running(), "pages letting go never stops the person's preview")
        self.assertTrue(preview.stop())
        self.assertFalse(preview.exporting())

    def test_the_persons_stop_leaves_the_pages_their_scene(self):
        preview = self.preview
        preview.start(self.window, REQUEST)
        preview.hold(preview.PAGES, self.window, REQUEST)
        self.assertTrue(preview.stop())
        self.assertFalse(preview.running())
        self.assertTrue(preview.exporting(), "the pages still hold it")
        preview.release(preview.PAGES)
        self.assertFalse(preview.exporting())


class SceneFeedTests(unittest.TestCase):
    """The pages' hold, kept while a page is open and let go 30 s after the last one closed; never the person's."""

    def setUp(self):
        from types import SimpleNamespace
        from unittest import mock
        import angelo_agent
        from angelo_agent import session_page
        self.calls, self.now = [], [100.0]
        server = SimpleNamespace(version=4, scene_bytes=lambda: b"glTF-bytes")
        fake = SimpleNamespace(PAGES="pages", _state={"server": server},
                               hold=lambda holder, window, request: self.calls.append(("hold", holder)),
                               release=lambda holder: self.calls.append(("release", holder)),
                               start=lambda *args, **keywords: self.fail("never the person's start"),
                               stop=lambda: self.fail("never the person's stop"))
        for patcher in (mock.patch.dict(sys.modules, {"angelo_agent.web_preview": fake}),
                        mock.patch.object(angelo_agent, "web_preview", fake, create=True),
                        mock.patch.object(session_page, "time", SimpleNamespace(monotonic=lambda: self.now[0]))):
            patcher.start()
            self.addCleanup(patcher.stop)
        session_page.unregister()
        self.addCleanup(session_page.unregister)
        session_page._pages = session_page.Pages(make_site=FakeSite, build=lambda controller, page: {})
        self.session_page = session_page
        self.host = SimpleNamespace(env=SimpleNamespace(ui=SimpleNamespace(main_window=lambda: "window")))
        self.controller = SimpleNamespace(state={"id": "c1", "messages": []}, version=1)

    def test_a_viewed_page_holds_the_exporter_and_lets_go_thirty_seconds_after_the_last_closes(self):
        pages = self.session_page._pages
        pages.link(self.controller, "codex")
        site = pages.site("c1")
        self.session_page._feed_scene(self.host)
        self.assertEqual(self.calls, [], "nobody looks: nothing held")
        site.viewers = 1
        self.session_page._feed_scene(self.host)
        self.session_page._feed_scene(self.host)
        self.assertEqual(self.calls, [("hold", "pages")])
        self.assertEqual(site.scene_feed(), b"glTF-bytes")
        site.viewers = 0
        self.session_page._feed_scene(self.host)
        self.now[0] += 29
        self.session_page._feed_scene(self.host)
        self.assertEqual(self.calls, [("hold", "pages")], "kept a while after the last page closed")
        self.now[0] += 2
        self.session_page._feed_scene(self.host)
        self.assertEqual(self.calls, [("hold", "pages"), ("release", "pages")])


class PageTests(unittest.TestCase):
    """The page itself: read-only, translatable, served whole."""

    def test_the_page_offers_no_action_and_loads_nothing_from_elsewhere(self):
        folder = ADDON / "session_page"
        for path in sorted(folder.iterdir()):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path.name):
                self.assertNotIn("http://", text.replace("http://www.w3.org/2000/svg", ""))
                self.assertNotIn("https://", text)
                self.assertNotIn("<form", text)
                self.assertNotIn("method:", text, "the page only reads")
                self.assertNotIn("<input", text)

    def test_the_session_never_waits_on_the_3d_view_and_a_replaced_scene_is_disposed(self):
        """The 2.0.1 audit: P2-7, a renderer that can't start (WebGL off) blanked the status and stopped every
        state.json read, because the 3D setup ran first, unguarded; P2-8, a replaced scene's geometry, materials and
        textures were never freed. ``browser_session_page.py`` proves both in Chromium, WebGL off and on."""
        import re
        script = (ADDON / "session_page/app.js").read_text(encoding="utf-8")
        start = re.search(r"\nfunction startScene\(\) \{\n(.*?)\n\}\n", script, re.S)
        self.assertIsNotNone(start, "the 3D view starts in one function")
        body = start.group(1)
        self.assertIn("new THREE.WebGLRenderer", body)
        self.assertRegex(body, r"try \{", "its start is guarded")
        self.assertNotIn("new THREE.WebGLRenderer", script.replace(start.group(0), ""), "nowhere else")
        self.assertRegex(script, r'text\(view \? "scene_loading" : "scene_unavailable"\)',
                         "the scene's place says it can't be drawn where the view never started")
        self.assertRegex(script, r"scene\.remove\(content\);\s*dispose\(content\)", "a replaced scene is freed")
        dispose = re.search(r"\nfunction dispose\(root\) \{\n(.*?)\n\}\n", script, re.S)
        self.assertIsNotNone(dispose)
        for freed in ("geometry.dispose()", "isTexture", "material.dispose()"):
            self.assertIn(freed, dispose.group(1))

    def test_markdown_is_drawn_as_elements_never_html_and_no_panel_hides_the_progress(self):
        """The 2.0.1 acceptance on f9f749b: the Result showed raw markdown, an empty Latest views panel sat over the
        scene, and a long result squeezed Progress to nothing. ``browser_session_page`` and the Chrome probe draw it."""
        import re
        script = (ADDON / "session_page/app.js").read_text(encoding="utf-8")
        style = (ADDON / "session_page/app.css").read_text(encoding="utf-8")
        self.assertNotIn("innerHTML", script, "the agent's words become elements and text, never markup")
        self.assertNotIn("insertAdjacentHTML", script)
        self.assertRegex(script, r'markdown\(\$\("result-text"\), state\.result', "the result is drawn as markdown")
        self.assertRegex(script, r'\$\("views-panel"\)\.hidden = ids\.length === 0', "an empty panel is hidden")
        self.assertRegex(style, r"\.stream\{[^}]*min-height:\s*\d+px", "Progress keeps room under any result")
        self.assertRegex(style, r"\.result\{[^}]*overflow-y:\s*auto", "a long result scrolls in its own card")

    def test_every_word_the_page_shows_is_translatable(self):
        import re
        from angelo_agent import session_page
        script = (ADDON / "session_page/app.js").read_text(encoding="utf-8")
        used = set(re.findall(r"""\btext\(\s*["']([a-z_]+)["']\s*[,)]""", script))
        self.assertTrue(used)
        self.assertEqual(used - set(session_page.PAGE_TEXT), set())
        statuses = {"ready", "done"} | set(page_state.WORKING)   # Shown as text("status_" + state.status).
        self.assertEqual({"status_" + status for status in statuses} - set(session_page.PAGE_TEXT), set())
        tools = {"queued", "running", "waiting_for_approval", "done", "failed", "stopped", "declined"}
        self.assertEqual({"tool_" + status for status in tools} - set(session_page.PAGE_TEXT), set())


if __name__ == "__main__":
    unittest.main()
