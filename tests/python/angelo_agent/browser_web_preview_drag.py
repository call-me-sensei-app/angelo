# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every drag on the live web preview page, in Chromium, Firefox and WebKit (Playwright), with no Blender.

1.0.9 on Windows: Compare's Wipe divider barely moved, because the browser dragged Angelo's picture out as a file
(a ghost image) and cancelled the page's pointer. Every drag surface now goes through ``web_preview/drag.js``
(doc/angelo/web_export.rst, "The live preview"). This check serves this checkout's page with the real
``PreviewServer``, publishes a small cube and answers the page's Compare requests with a stand-in picture (Angelo's
side of ``web_preview._draw_compare``), then drags each control as a person does and measures where it went:

* the Wipe divider, with the mouse from Angelo's picture and from a label over it, ends under the pointer;
* the orbit drag on the scene's canvas turns the camera by OrbitControls' own amount (a full turn per drag of the
  canvas height's 2 pi), read from the camera the page sends with its next Compare request;
* touch and pen drive the divider too: real touch and pen input in Chromium (DevTools protocol), pointer events
  carrying ``pointerType`` touch and pen in Firefox and WebKit, which Playwright cannot touch-drag;
* through all of it no native ``dragstart`` reaches the window, no pointer is cancelled and no text is selected.

Run it where Playwright and its browsers are installed (not part of the dependency-free host suite)::

    python -m pip install playwright
    python -m playwright install --with-deps chromium firefox webkit
    python tests/python/angelo_agent/browser_web_preview_drag.py [chromium firefox webkit]

Firefox makes no WebGL context headless on a machine without a GPU (a Linux VM): run it in a window under Xvfb,
``ANGELO_QA_HEADED=1 xvfb-run -a python tests/python/angelo_agent/browser_web_preview_drag.py firefox``.

``test_native_web_preview_drag`` pins the same contract in the page's source for the host suite.
"""

import json
import math
import os
import re
import struct
import sys
import threading
import time
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # Reported by the test runner as a skip, with how to install it.
    sync_playwright = None

BROWSERS = ("chromium", "firefox", "webkit")
SIZE = {"width": 1280, "height": 800}
STRINGS = {"title": "Drag check", "compare": "Compare", "wipe": "Wipe", "difference": "Difference",
           "compare_done": "Done", "angelo_side": "Angelo", "web_side": "Three.js", "light_world": "World",
           "light_studio": "Studio", "background": "Background", "live": "Live", "updated": "{time}"}
TOLERANCE_PX = 2.0
TURN_TOLERANCE = 0.03   # Radians. The damped turn converges on the full amount; frames are waited out below.

# Counts what reaches the window from the first moment: native drags, cancelled pointers, the pointer types seen.
WATCH = """
window.__drag = {dragstart: 0, pointercancel: 0, types: []};
addEventListener("dragstart", () => { window.__drag.dragstart += 1; }, true);
addEventListener("pointercancel", () => { window.__drag.pointercancel += 1; }, true);
addEventListener("pointerdown", (event) => { window.__drag.types.push(event.pointerType); }, true);
"""


def cube_glb():
    """A unit cube as a binary glTF: positions and indices, one mesh, one node."""
    corners = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    indices = [index for a, b, c, d in faces for index in (a, b, c, a, c, d)]
    positions = b"".join(struct.pack("<3f", *corner) for corner in corners)
    index_bytes = struct.pack("<%dH" % len(indices), *indices)
    binary = positions + index_bytes
    binary += b"\0" * (-len(binary) % 4)
    document = {
        "asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions), "target": 34962},
                        {"buffer": 0, "byteOffset": len(positions), "byteLength": len(index_bytes), "target": 34963}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
                       "min": [-0.5] * 3, "max": [0.5] * 3},
                      {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"}],
    }
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<I4s", len(text), b"JSON") + text + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def png(width, height, rgb):
    """A plain ``rgb`` picture as PNG bytes."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\0" + bytes(rgb) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class StandInAngelo:
    """The server with a published cube, answering each Compare request as Angelo's timer does."""

    def __init__(self):
        self.server = PreviewServer()
        self.server.publish(cube_glb(), {"status": "ok", "strings": STRINGS, "lang": "en"})
        self.requests = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._answer, daemon=True)
        self._thread.start()

    def _answer(self):
        picture = png(160, 100, (200, 80, 60))
        while not self._stop.is_set():
            request = self.server.take_compare()
            if request is None:
                time.sleep(0.02)
                continue
            self.requests.append(request)
            self.server.publish_viewport(picture, {"ok": True, "request": request.get("id")})

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self.server.close()


def azimuth(request):
    """The page camera's angle around the cube's vertical axis (the orbit target is the cube's centre)."""
    x, _y, z = request["matrix"][12:15]
    return math.atan2(x, z)


def turned(before, after):
    """The signed turn from ``before`` to ``after``, in (-pi, pi]."""
    delta = after - before
    return math.atan2(math.sin(delta), math.cos(delta))


class DragChecks:
    """One browser's checks; ``BROWSER`` is set by the subclasses below."""

    BROWSER = None

    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest("Playwright is not installed: python -m pip install playwright")
        wanted = [name for name in sys.argv[1:] if name in BROWSERS] or list(BROWSERS)
        if cls.BROWSER not in wanted:
            raise unittest.SkipTest("not asked for")
        cls.angelo = StandInAngelo()
        cls.playwright = sync_playwright().start()
        try:
            options = {"headless": os.environ.get("ANGELO_QA_HEADED") != "1"}
            if cls.BROWSER == "firefox":   # Firefox's GPU blocklist refuses WebGL on a software-only machine.
                options["firefox_user_prefs"] = {"webgl.force-enabled": True}
            cls.browser = getattr(cls.playwright, cls.BROWSER).launch(**options)
        except Exception as error:
            cls.playwright.stop()
            cls.angelo.close()
            raise unittest.SkipTest("%s is not installed: %s" % (cls.BROWSER, str(error).splitlines()[0]))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.angelo.close()

    def setUp(self):
        touch = self.BROWSER == "chromium"   # Real touch input needs a touch-enabled context.
        self.context = self.browser.new_context(viewport=SIZE, has_touch=touch)
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.add_init_script(WATCH)
        self.page.goto(self.angelo.server.url)
        # Live, then the scene loaded (the loading bar goes on before the status turns live, and off once loaded).
        self.page.wait_for_selector("#status.live", timeout=30000)
        self.page.wait_for_function("() => !document.querySelector('#loading').classList.contains('on')",
                                    timeout=30000)
        self.wait_frames(10)

    # Helpers ---------------------------------------------------------------------------------------------------

    def wait_frames(self, count):
        self.page.evaluate("""(count) => new Promise((done) => {
            const step = () => (count-- > 0 ? requestAnimationFrame(step) : done());
            step();
        })""", count)

    def open_compare(self):
        asked = len(self.angelo.requests)
        self.page.click("#compare")
        self.page.wait_for_function("() => document.querySelector('#compare-view').dataset.difference !== undefined",
                                    timeout=30000)
        self.assertGreater(len(self.angelo.requests), asked)
        return self.angelo.requests[-1]

    def close_compare(self):
        self.page.click("#compare-done")
        self.page.wait_for_function("() => document.querySelector('#compare-view').hidden")

    def box(self, selector):
        return self.page.eval_on_selector(selector, """(node) => {
            const box = node.getBoundingClientRect();
            return {x: box.left, y: box.top, width: box.width, height: box.height};
        }""")

    def handle_x(self):
        return self.box("#wipe")["x"]

    def mouse_drag(self, start, end, steps=12):
        mouse = self.page.mouse
        mouse.move(*start)
        mouse.down()
        for step in range(1, steps + 1):
            mouse.move(start[0] + (end[0] - start[0]) * step / steps, start[1] + (end[1] - start[1]) * step / steps)
        mouse.up()

    def synthetic_drag(self, selector, start, end, kind, steps=8):
        """Pointer events of ``kind`` (touch, pen) on ``selector``, as a person's finger or pen sends them."""
        self.page.eval_on_selector(selector, """(node, [start, end, kind, steps]) => {
            const send = (type, x, y, buttons) => node.dispatchEvent(new PointerEvent(type, {bubbles: true,
                cancelable: true, composed: true, pointerId: 41, pointerType: kind, isPrimary: true, button:
                type === "pointermove" ? -1 : 0, buttons, clientX: x, clientY: y}));
            send("pointerdown", start[0], start[1], 1);
            for (let i = 1; i <= steps; i += 1)
                send("pointermove", start[0] + (end[0] - start[0]) * i / steps,
                     start[1] + (end[1] - start[1]) * i / steps, 1);
            send("pointerup", end[0], end[1], 0);
        }""", [list(start), list(end), kind, steps])

    def native_drag(self, start, end, kind, steps=8):
        """Chromium's own touch or pen input through the DevTools protocol (real input, not page events)."""
        session = self.context.new_cdp_session(self.page)
        points = [(start[0] + (end[0] - start[0]) * i / steps, start[1] + (end[1] - start[1]) * i / steps)
                  for i in range(steps + 1)]
        if kind == "touch":
            session.send("Input.dispatchTouchEvent", {"type": "touchStart",
                                                      "touchPoints": [{"x": points[0][0], "y": points[0][1]}]})
            for x, y in points[1:]:
                session.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
            session.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        else:
            base = {"pointerType": kind, "button": "left"}
            session.send("Input.dispatchMouseEvent", dict(base, type="mouseMoved", x=points[0][0], y=points[0][1],
                                                          button="none"))
            session.send("Input.dispatchMouseEvent", dict(base, type="mousePressed", x=points[0][0],
                                                          y=points[0][1], clickCount=1, buttons=1))
            for x, y in points[1:]:
                session.send("Input.dispatchMouseEvent", dict(base, type="mouseMoved", x=x, y=y, buttons=1))
            session.send("Input.dispatchMouseEvent", dict(base, type="mouseReleased", x=points[-1][0],
                                                          y=points[-1][1], clickCount=1))
        session.detach()

    def assert_page_kept_the_drag(self):
        state = self.page.evaluate("() => ({...window.__drag, selection: String(getSelection())})")
        self.assertEqual(state["dragstart"], 0, "a native drag-and-drop started")
        self.assertEqual(state["pointercancel"], 0, "the browser cancelled the page's pointer")
        self.assertEqual(state["selection"], "", "the drag selected text")
        self.assertEqual(self.errors, [])
        return state

    def assert_divider_at(self, x, view):
        self.assertAlmostEqual(self.handle_x(), x, delta=TOLERANCE_PX)
        clip = self.page.eval_on_selector("#angelo-shot", "(node) => node.style.clipPath")
        right = float(re.search(r"([\d.]+)%", clip).group(1))   # inset(0 R% 0 0): the picture stops at the divider.
        self.assertAlmostEqual(right, (1 - (x - view["x"]) / view["width"]) * 100, delta=0.5)

    # Checks ----------------------------------------------------------------------------------------------------

    def test_drag_surfaces_turn_off_the_browsers_own_drag(self):
        styles = self.page.evaluate("""() => {
            const style = (selector, name) => getComputedStyle(document.querySelector(selector)).getPropertyValue(name);
            return {view: style("#view", "touch-action"), compare: style("#compare-view", "touch-action"),
                    select: style(".stage", "user-select") || style(".stage", "-webkit-user-select"),
                    shot: document.querySelector("#angelo-shot").draggable,
                    canvas: document.querySelector("#view").draggable};
        }""")
        self.assertEqual((styles["view"], styles["compare"]), ("none", "none"))
        self.assertEqual(styles["select"], "none")
        self.assertEqual((styles["shot"], styles["canvas"]), (False, False))

    def test_wipe_divider_follows_the_mouse(self):
        self.open_compare()
        view = self.box("#compare-view")
        y = view["y"] + view["height"] / 2
        self.assertAlmostEqual(self.handle_x(), view["x"] + view["width"] / 2, delta=TOLERANCE_PX)
        # From Angelo's picture (left of the divider) leftwards; from there to the right, ending over the report
        # column (the pointer stays captured); from the Three.js side back. Each press starts on the view itself.
        for start, end in ((0.4, 0.15), (0.15, 0.85), (0.6, 0.5)):
            self.mouse_drag((view["x"] + view["width"] * start, y), (view["x"] + view["width"] * end, y))
            self.assert_divider_at(view["x"] + view["width"] * end, view)
        # From a label over the picture: still the divider, never the label's text.
        label = self.box("#angelo-side")
        self.mouse_drag((label["x"] + label["width"] / 2, label["y"] + label["height"] / 2),
                        (view["x"] + view["width"] * 0.3, y))
        self.assert_divider_at(view["x"] + view["width"] * 0.3, view)
        self.assertEqual(set(self.assert_page_kept_the_drag()["types"]), {"mouse"})

    def test_wipe_divider_follows_touch_and_pen(self):
        self.open_compare()
        view = self.box("#compare-view")
        y = view["y"] + view["height"] / 2
        for kind, (start, end) in (("touch", (0.45, 0.2)), ("pen", (0.2, 0.7))):
            with self.subTest(kind=kind):
                points = (view["x"] + view["width"] * start, y), (view["x"] + view["width"] * end, y)
                if self.BROWSER == "chromium":
                    self.native_drag(*points, kind)
                else:
                    self.synthetic_drag("#angelo-shot", *points, kind)
                self.assert_divider_at(points[1][0], view)
        self.assertIn("touch", self.assert_page_kept_the_drag()["types"])

    def test_orbit_drag_turns_the_camera_by_its_own_amount(self):
        before = azimuth(self.open_compare())
        self.close_compare()
        canvas = self.box("#view")
        dx = canvas["height"] / 4   # A quarter of the height: a quarter turn.
        centre = (canvas["x"] + canvas["width"] * 0.35, canvas["y"] + canvas["height"] / 2)
        self.mouse_drag(centre, (centre[0] + dx, centre[1]), steps=16)
        self.wait_frames(300)   # Damping hands out the turn over frames; 0.95^300 of it is left.
        after = azimuth(self.open_compare())
        self.assertAlmostEqual(abs(turned(before, after)), 2 * math.pi * dx / canvas["height"], delta=TURN_TOLERANCE)
        self.assert_page_kept_the_drag()


def _browser_case(name):
    return type("%sDragTests" % name.capitalize(), (DragChecks, unittest.TestCase), {"BROWSER": name})


for _name in BROWSERS:
    globals()["%sDragTests" % _name.capitalize()] = _browser_case(_name)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0], "-v"] + [arg for arg in sys.argv[1:] if arg not in BROWSERS])
