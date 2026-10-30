# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The live web preview page's report column can be hidden whole (doc/angelo/web_export.rst, "The live preview").

Dependency-free: the page's own files are read as text and the server is started without Blender. The column
(``web_preview/panel.js``) folds to its title or hides whole, leaving one small button in the corner that carries the
live state's dot; I toggles it from the keyboard. The person's choice survives live updates (the page is never
reloaded for one) and browser reloads (the page's storage, every access guarded so the page works without it). A
hidden column is out of the way at any width: invisible, unclickable and out of the tab order, never a strip that
still covers the scene. A browser check of the same behaviour is in the document's validation section.
"""

import ast
import http.client
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
PAGE = ADDON / "web_preview"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402


def read(name):
    return (PAGE / name).read_text(encoding="utf-8")


def rules(css, selector):
    """The declarations of every rule whose selector list is exactly ``selector``, joined."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    return " ".join(body for found, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css) if found.strip() == selector)


def page_text():
    """``web_preview.PAGE_TEXT`` as {key: English}, read without importing bpy."""
    tree = ast.parse((ADDON / "web_preview.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "PAGE_TEXT":
            return {key.value: value.args[0].value for key, value in zip(node.value.keys, node.value.values)}
    raise AssertionError("PAGE_TEXT not found")


class ReportColumnTests(unittest.TestCase):
    def test_the_column_has_a_hide_control_and_one_small_way_back(self):
        html = read("index.html")
        aside = re.search(r'<aside class="panel" id="panel">(.*?)</aside>', html, re.S).group(1)
        self.assertRegex(aside, r'<button class="fold" id="hide" type="button" aria-expanded="true" '
                                r'aria-controls="panel">')
        self.assertIn('id="fold"', aside)
        after = html.split("</aside>", 1)[1]   # Outside the column, which is inert while hidden.
        self.assertRegex(after, r'<button class="reopen" id="reopen" type="button" aria-expanded="false" '
                                r'aria-controls="panel" hidden>')
        self.assertEqual(html.count('class="reopen"'), 1)
        self.assertIn('<i aria-hidden="true"></i>', after)   # The live state's dot.

    def test_a_hidden_column_is_out_of_the_way_at_every_width(self):
        css = read("app.css")
        away = rules(css, ".panel.away")
        for declaration in ("opacity:0", "visibility:hidden", "pointer-events:none"):
            self.assertIn(declaration, away)
        narrow = css.split("@media (max-width:760px)", 1)[1].split("@media", 1)[0]
        self.assertIn(".panel.away{transform:translateY(", narrow)   # The bottom sheet leaves downwards.
        reopen = rules(css, ".reopen")
        size = {name: int(value) for name, value in re.findall(r"(?<![-\w])(width|height):(\d+)px", reopen)}
        self.assertEqual(set(size), {"width", "height"})
        self.assertLessEqual(max(size.values()), 44, "one small control, never a strip")
        self.assertIn("position:fixed", reopen)
        self.assertIn("display:none", rules(css, ".reopen[hidden]"))
        # No flash of a hidden column: it stays invisible until panel.js applied the saved choice.
        self.assertIn("visibility:hidden", rules(css, ":root:not([data-report]) .panel"))
        self.assertIn('document.documentElement.dataset.report = "ready"', read("panel.js"))

    def test_the_hidden_column_leaves_the_tab_order(self):
        panel = read("panel.js")
        self.assertIn("panel.inert = state.hidden", panel)
        self.assertIn("reopen.hidden = !state.hidden", panel)
        self.assertIn('panel.classList.toggle("away", state.hidden)', panel)

    def test_the_choice_is_kept_and_storage_is_always_guarded(self):
        panel = read("panel.js")
        uses = [line.strip() for line in panel.splitlines() if "localStorage" in line]
        self.assertEqual(len(uses), 2, "one read, one write")
        for line in uses:
            self.assertRegex(line, r"^try \{.*\} catch \{.*\}$", "every storage access in try/catch")
        self.assertIn('const KEY = "angelo.web-preview.report";', panel)
        for handler in ("fold.addEventListener", "hide.addEventListener", "reopen.addEventListener"):
            self.assertIn(handler, panel)
        self.assertEqual(panel.count("save(state);"), 2)   # A fold, and setHidden (hide, show and the key).

    def test_live_updates_keep_the_choice(self):
        app = read("app.js")
        # The column is set up before the renderer, so a renderer failure still leaves the column usable.
        self.assertLess(app.index("setupPanel()"), app.index("new THREE.WebGLRenderer"))
        self.assertEqual(app.count("setupPanel()"), 1)
        # A new export rewrites the column's text, never its fold or hidden state; nothing reloads the page.
        self.assertNotRegex(app, r'classList\.(toggle|add|remove)\("(folded|away)"')
        self.assertNotIn("location.reload", app)
        show = app.split("function show(report)", 1)[1].split("\nfunction ", 1)[0]
        self.assertIn("panel.text(text)", show)
        status = app.split("function status(kind, label)", 1)[1].split("\n}", 1)[0]
        self.assertIn("panel.status(kind)", status)   # The corner button's dot follows live / updating / closed.
        css = read("app.css")
        for kind in ("live", "busy", "down"):
            self.assertIn(".reopen[data-state=%s] i{background:" % kind, css)

    def test_the_keyboard_toggle_leaves_typing_and_shortcuts_alone(self):
        panel = read("panel.js")
        handler = panel.split('window.addEventListener("keydown"', 1)[1].split("});", 1)[0]
        self.assertIn('event.key !== "i" && event.key !== "I"', handler)
        for guard in ("event.ctrlKey", "event.metaKey", "event.altKey", "event.repeat", "editable(event.target)"):
            self.assertIn(guard, handler)

    def test_every_control_is_labelled_with_translated_text(self):
        strings = page_text()
        self.assertEqual({key: strings[key] for key in ("fold", "unfold", "hide", "show")},
                         {"fold": "Hide details", "unfold": "Show details",
                          "hide": "Hide the report", "show": "Show the report"})
        panel = read("panel.js")
        for key in ("fold", "unfold", "hide", "show"):
            self.assertIn("text.%s" % key, panel)
        for button in re.findall(r"<button [^>]*>", read("index.html")):
            self.assertNotRegex(button, r'(aria-label|title)=', "labels come translated from the report")

    def test_the_server_serves_the_column_module(self):
        server = PreviewServer()
        self.addCleanup(server.close)
        connection = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/%s/panel.js" % server.token, headers={"Host": "127.0.0.1:%d" % server.port})
        response = connection.getresponse()
        self.assertEqual((response.status, response.getheader("Content-Type")),
                         (200, "text/javascript; charset=utf-8"))
        self.assertIn(b"export function setupPanel", response.read())


if __name__ == "__main__":
    unittest.main()
