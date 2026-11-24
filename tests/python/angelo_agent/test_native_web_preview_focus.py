# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the live preview frames (doc/angelo/web_export.rst, "Framing"): Angelo's selection, active object and scene
camera reach the page as a small focus message, without re-exporting the scene, and the page frames the camera,
the selection or everything.

Real use (2026-11-15): the page always framed the whole scene, so reviewing a face or a hand meant orbiting in by
hand after every reload. Any scene has the same need (a prop on a table, one building of a street), so the page
follows what the person looks at in Angelo, not a body part."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import http.client
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "scripts/addons_core/angelo_agent/web_preview"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402


class FocusRouteTests(unittest.TestCase):
    def setUp(self):
        self.server = PreviewServer()
        self.addCleanup(self.server.close)

    def get(self, name):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        connection.request("GET", "/%s/%s" % (self.server.token, name),
                           headers={"Host": "127.0.0.1:%d" % self.server.port})
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_focus_is_published_by_name_without_a_new_export(self):
        self.server.publish(b"glb", {"status": "ok"})
        version = self.server.version
        self.assertEqual(json.loads(self.get("focus.json")[1]), {"selected": [], "active": None, "camera": None,
                                                                 "version": 0})
        self.server.publish_focus({"selected": ["Head", "Hair"], "active": "Head", "camera": "Shot"})
        focus = json.loads(self.get("focus.json")[1])
        self.assertEqual((focus["selected"], focus["active"], focus["camera"], focus["version"]),
                         (["Head", "Hair"], "Head", "Shot", 1))
        self.assertEqual(self.server.version, version)   # The scene is not exported again.
        self.assertEqual(self.get("scene.glb"), (200, b"glb"))

    def test_the_same_focus_twice_is_one_change(self):
        self.server.publish_focus({"selected": ["A"], "active": "A", "camera": None})
        self.server.publish_focus({"selected": ["A"], "active": "A", "camera": None})
        self.assertEqual(json.loads(self.get("focus.json")[1])["version"], 1)

    def test_a_focus_must_be_names(self):
        for bad in ({"selected": "A", "active": None, "camera": None},
                    {"selected": [3], "active": None, "camera": None},
                    {"selected": [], "active": 3, "camera": None},
                    {"selected": [], "active": None, "camera": None, "path": "/Users/x"}):
            with self.assertRaises(ValueError, msg=bad):
                self.server.publish_focus(bad)

    def test_a_focus_wakes_the_event_stream(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        connection.request("GET", "/%s/events" % self.server.token,
                           headers={"Host": "127.0.0.1:%d" % self.server.port})
        response = connection.getresponse()
        self.assertIn(b"event: version", response.fp.readline() + response.fp.readline())
        response.fp.readline()
        self.server.publish_focus({"selected": ["Hand.L"], "active": "Hand.L", "camera": None})
        self.assertEqual(response.fp.readline(), b"event: focus\n")
        self.assertEqual(response.fp.readline(), b"data: 1\n")
        connection.close()


class FrameReportTests(unittest.TestCase):
    """What the page frames comes back to Angelo (doc/angelo/web_export.rst, "Framing"): the mode, Follow, the
    objects or camera framed and the view's position and target, from this page only, as checked data. The agent
    reads it in angelo_view's WEB facts (what the person's browser looks at), and the GUI scenario asserts it."""

    REPORT = {"mode": "selection", "follow": True, "framed": ["Head"], "camera": None, "focus_version": 2,
              "position": [0.0, 1.6, 2.4], "target": [0.0, 1.6, 0.0]}

    def setUp(self):
        self.server = PreviewServer()
        self.addCleanup(self.server.close)

    def post(self, body, origin=None, name="frame"):
        port = self.server.port
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        connection.request("POST", "/%s/%s" % (self.server.token, name), body=data,
                           headers={"Host": "127.0.0.1:%d" % port, "Origin": origin or "http://127.0.0.1:%d" % port,
                                    "Content-Type": "application/json", "Content-Length": str(len(data))})
        status = connection.getresponse().status
        connection.close()
        return status

    def test_the_pages_frame_state_reaches_angelo(self):
        self.assertIsNone(self.server.page_frame())
        self.assertEqual(self.post(self.REPORT), 204)
        self.assertEqual(self.server.page_frame(), self.REPORT)
        self.assertEqual(self.post(dict(self.REPORT, mode="camera", camera="Shot", framed=[])), 204)
        self.assertEqual((self.server.page_frame()["mode"], self.server.page_frame()["camera"]), ("camera", "Shot"))

    def test_only_its_own_page_reports_and_only_the_known_shape(self):
        port = self.server.port
        self.assertEqual(self.post(self.REPORT, origin="http://evil.example"), 403)
        self.assertEqual(self.post(self.REPORT, origin="http://127.0.0.1:%d" % (port + 1)), 403)
        for bad in ([], "x", dict(self.REPORT, mode="sideways"), dict(self.REPORT, follow="yes"),
                    dict(self.REPORT, framed="Head"), dict(self.REPORT, framed=[3]), dict(self.REPORT, target=[0, 1]),
                    dict(self.REPORT, position=[0, "1", 2]),
                    dict(self.REPORT, target=[0, float("nan"), 0]), dict(self.REPORT, extra=1),
                    dict(self.REPORT, focus_version="2")):
            with self.subTest(bad=bad):
                self.assertEqual(self.post(bad), 400)
        self.assertEqual(self.post(b"{" + b" " * (64 * 1024) + b"}"), 413)
        self.assertIsNone(self.server.page_frame(), "nothing refused was kept")

    def test_the_agent_reads_what_the_browser_frames(self):
        from angelo_agent import view_web
        self.post(self.REPORT)
        facts = view_web.page_facts(self.server)
        self.assertEqual(facts["framing"], {"mode": "selection", "follow": True, "framed": ["Head"], "camera": None})
        fresh = PreviewServer()
        self.addCleanup(fresh.close)
        self.assertNotIn("framing", view_web.page_facts(fresh), "no report yet: nothing claimed")


class PageTests(unittest.TestCase):
    """The page's side, read from its source: the browser test (gui_web_preview) drives it for real."""

    def test_the_page_listens_for_focus_and_offers_three_frame_modes(self):
        app = "".join((PAGE / name).read_text(encoding="utf-8") for name in ("app.js", "framing.js"))
        page = (PAGE / "index.html").read_text(encoding="utf-8")
        self.assertIn('addEventListener("focus"', app)
        for control in ("frame-camera", "frame-selection", "frame-all", "frame-follow"):
            self.assertIn('id="%s"' % control, page)
            self.assertIn('$("%s")' % control, app)

    def test_the_page_reports_what_it_frames_whenever_it_changes(self):
        """Every path that changes what the page frames ends in draw() (a button, Follow, the first load, Angelo's
        focus); draw() reports, and the report goes out only when the state differs from the last one sent."""
        source = (PAGE / "framing.js").read_text(encoding="utf-8")
        self.assertIn('fetch("frame"', source)
        draw = re.search(r"\n  function draw\(\) \{(.*?)\n  \}\n", source, re.S)
        self.assertIsNotNone(draw)
        self.assertIn("report();", draw.group(1))
        report = re.search(r"\n  function report\(\) \{(.*?)\n  \}\n", source, re.S)
        self.assertIsNotNone(report)
        self.assertRegex(report.group(1), r"=== sent", "unchanged state is not sent again")
        for key in ("mode", "follow", "framed", "camera", "focus_version", "position", "target"):
            self.assertIn(key, report.group(1))

    def test_every_frame_label_is_translated(self):
        source = (ROOT / "scripts/addons_core/angelo_agent/web_preview.py").read_text(encoding="utf-8")
        for key in ("frame_camera", "frame_selection", "frame_all", "frame_follow", "frame_missing"):
            self.assertRegex(source, r'"%s": n_\(' % re.escape(key))


if __name__ == "__main__":
    unittest.main()
