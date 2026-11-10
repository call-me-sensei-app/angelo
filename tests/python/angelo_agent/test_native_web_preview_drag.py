# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every drag on the live web preview page is the page's own, never the browser's (doc/angelo/web_export.rst).

1.0.9 on Windows: Compare's Wipe divider barely moved; the browser dragged Angelo's picture out as a file (a ghost
image) and cancelled the page's pointer. The class: any press held and moved over a picture, a canvas or text has
the browser's own default actions (drag-and-drop, selection, touch scrolling, middle-button autoscroll). So every
drag surface goes through ``web_preview/drag.js``: pointer events captured on the surface, the press's default
prevented, nothing draggable, and the stylesheet turns off selection, picture dragging and touch panning.

Dependency-free: the page's files are read as text. ``browser_web_preview_drag.py`` drags every control in Chromium,
Firefox and WebKit (Playwright) and measures where it went.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import http.client
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "scripts/addons_core/angelo_agent/web_preview"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402

# Events that start or follow a drag. Page code listens to none of them itself: drag.js does, for every surface.
DRAG_EVENTS = ("pointerdown", "pointermove", "pointerup", "mousedown", "mousemove", "mouseup", "touchstart",
               "touchmove", "touchend", "dragstart", "drag")


def read(name):
    return (PAGE / name).read_text(encoding="utf-8")


def page_scripts():
    """The page's own modules (the bundled Three.js build is vendor code, reached only through them)."""
    return sorted(path.name for path in PAGE.glob("*.js"))


def rules(css, selector):
    """The declarations of every rule whose selector list is exactly ``selector``, joined."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return " ".join(body for found, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css) if found.strip() == selector)


def function_body(source, name):
    return source.split("export function %s(" % name, 1)[1].split("\n}\n", 1)[0]


class DragHelperTests(unittest.TestCase):
    def test_page_code_follows_no_pointer_itself(self):
        self.assertIn("drag.js", page_scripts())
        for name in page_scripts():
            if name == "drag.js":
                continue
            source = read(name)
            for event in DRAG_EVENTS:
                self.assertNotRegex(source, r"""addEventListener\(\s*["']%s["']""" % event,
                                    "%s listens to %s itself; drags go through drag.js" % (name, event))
            self.assertNotRegex(source, r"\.on(pointer|mouse|touch|drag)\w*\s*=", name)
            self.assertNotIn("setPointerCapture", source, name)

    def test_a_followed_drag_owns_its_pointer(self):
        follow = function_body(read("drag.js"), "followDrag")
        down = follow.split('"pointerdown"', 1)[1].split("\n  });", 1)[0]
        self.assertIn("event.preventDefault()", down, "no native drag, selection or autoscroll from the press")
        self.assertIn("setPointerCapture(pointer)", down, "moves keep arriving outside the surface")
        self.assertIn("event.isPrimary", down, "a second finger never takes over")
        self.assertIn("event.button !== 0", down)
        self.assertLess(down.index("accept(event)"), down.index("event.preventDefault()"),
                        "a press it turns down (a button) keeps its default")
        for ending in ("pointerup", "pointercancel", "lostpointercapture"):
            self.assertIn('"%s"' % ending, follow)
        self.assertIn("noNativeDrag(surface)", follow)

    def test_nothing_on_a_surface_is_dragged_out(self):
        helper = function_body(read("drag.js"), "noNativeDrag")
        self.assertIn("element.draggable = false", helper)
        self.assertRegex(helper, r'addEventListener\("dragstart", \(event\) => event\.preventDefault\(\)\)')
        owned = function_body(read("drag.js"), "ownPresses")
        self.assertIn("noNativeDrag(surface)", owned)
        self.assertIn("event.preventDefault()", owned)

    def test_every_drag_surface_uses_the_helper(self):
        app = read("app.js")
        self.assertEqual(len(re.findall(r"new OrbitControls\(camera, canvas\)", app)), 1)
        self.assertIn("ownPresses(canvas)", app, "the scene's orbit, pan and dolly")
        compare = read("compare.js")
        self.assertRegex(compare, r"followDrag\(view, \{ accept: \(event\) => mode === \"wipe\"")
        self.assertIn("noNativeDrag(shot, difference)", compare, "Angelo's picture and the Difference canvas")

    def test_the_stylesheet_turns_off_selection_dragging_and_touch_panning(self):
        css = read("app.css")
        stage = rules(css, ".stage")
        for declaration in ("user-select:none", "-webkit-user-select:none", "-webkit-touch-callout:none"):
            self.assertIn(declaration, stage)
        self.assertIn("-webkit-user-drag:none", rules(css, ".stage img,.stage canvas"))
        self.assertIn("touch-action:none", rules(css, "#view"))
        self.assertIn("touch-action:none", rules(css, ".compare"))
        # Every picture and canvas on the page is on the stage, so the rules above reach it.
        html = read("index.html")
        stage_html = re.search(r'<main class="stage">(.*?)</main>', html, re.S).group(1)
        self.assertEqual(len(re.findall(r"<(img|canvas)\b", html)), len(re.findall(r"<(img|canvas)\b", stage_html)))

    def test_the_server_serves_the_helper(self):
        server = PreviewServer()
        self.addCleanup(server.close)
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/%s/drag.js" % server.token, headers={"Host": "127.0.0.1:%d" % server.port})
        response = connection.getresponse()
        self.assertEqual((response.status, response.getheader("Content-Type")),
                         (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"export function followDrag", response.read())

    def test_the_browser_check_covers_every_engine(self):
        check = (Path(__file__).with_name("browser_web_preview_drag.py")).read_text(encoding="utf-8")
        self.assertIn('BROWSERS = ("chromium", "firefox", "webkit")', check)
        for name in ("test_wipe_divider_follows_the_mouse", "test_wipe_divider_follows_touch_and_pen",
                     "test_orbit_drag_turns_the_camera_by_its_own_amount"):
            self.assertIn("def %s(" % name, check)


if __name__ == "__main__":
    unittest.main()
