# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Web shaders without Blender (doc/angelo/web_export.rst, "Web shaders"): a shader file's uniforms, the extras a
material carries, the page's built-ins agreeing with Blender's, and the page's capture and shader report reaching
Angelo only from the page itself."""

import http.client
import json
import re
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import web_shader_spec as spec  # noqa: E402
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402

FILE = """uniform float gain; // 0 4 = 1.5
uniform float mix_amount;
uniform vec3 tint; // color
uniform float uTime;
uniform vec2 offset;
void main() {
  gl_FragColor = vec4(tint * gain, 1.0);
}
#pragma angelo vertex
void main() { gl_Position = vec4(position, 1.0); }
"""


class ShaderFileTests(unittest.TestCase):
    def test_a_files_float_and_colour_uniforms_are_editable(self):
        self.assertEqual(spec.file_uniforms(FILE), [("gain", spec.FLOAT, 1.5, 0.0, 4.0),
                                                    ("mix_amount", spec.FLOAT, 0.5, 0.0, 1.0),
                                                    ("tint", spec.COLOR, (1.0, 1.0, 1.0), 0.0, 1.0)])
        # uTime is Angelo's own; a vec2 is no slider or colour: neither is offered.

    def test_the_vertex_section_is_optional(self):
        vertex, fragment = spec.split_source(FILE)
        self.assertIn("gl_Position", vertex)
        self.assertNotIn("gl_Position", fragment)
        self.assertEqual(spec.split_source("void main() {}"), (None, "void main() {}"))

    def test_languages_by_suffix(self):
        self.assertEqual([spec.language(name) for name in ("a.glsl", "b.FRAG", "c.tsl.js", "d.txt")],
                         ["GLSL", "GLSL", "TSL", None])

    def test_extras_carry_the_reference_and_uniforms_only(self):
        self.assertEqual(spec.extras("FILE", {"gain": 2.0, "tint": (1.0, 0.5, 0.0)}, "shaders/glow.glsl"),
                         {"angelo_web_shader": {"kind": "file", "uniforms": {"gain": 2.0, "tint": [1.0, 0.5, 0.0]},
                                                "file": "shaders/glow.glsl", "language": "glsl"}})
        self.assertEqual(spec.extras("TOON", {"bands": 3.0}), {"angelo_web_shader": {"kind": "toon",
                                                                                    "uniforms": {"bands": 3.0}}})

    def test_the_pages_built_ins_declare_exactly_blenders_uniforms(self):
        page = (ADDON / "web_preview/shaders.js").read_text(encoding="utf-8")
        for kind, uniforms in spec.BUILTINS.items():
            block = re.search(r"\n  %s: \{.*?source: `(.*?)`" % kind.lower(), page, re.S)
            self.assertIsNotNone(block, kind)
            declared = re.findall(r"uniform (float|vec3) (\w+);", block.group(1))
            wanted = [("vec3" if kind_ == spec.COLOR else "float", name) for name, kind_, *_rest in uniforms]
            self.assertEqual(declared, wanted, kind)

    def test_angelos_declarations_are_reserved(self):
        declared = set(re.findall(r"(?:uniform|varying) \w+ (\w+);", spec.ANGELO_DECLARATIONS))
        self.assertEqual(declared, spec.RESERVED)


class PageChannelTests(unittest.TestCase):
    def setUp(self):
        self.server = PreviewServer()
        self.addCleanup(self.server.close)
        self.origin = "http://127.0.0.1:%d" % self.server.port

    def post(self, name, body, origin=None, kind="application/json"):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        self.addCleanup(connection.close)
        headers = {"Host": "127.0.0.1:%d" % self.server.port, "Content-Type": kind}
        if origin is not False:
            headers["Origin"] = origin or self.origin
        connection.request("POST", "/%s/%s" % (self.server.token, name), body=body, headers=headers)
        return connection.getresponse().status

    def test_only_the_page_itself_may_report(self):
        report = json.dumps({"version": 3, "shaders": [{"material": "Orb", "ok": False, "error": "0:4 syntax"}]})
        self.assertEqual(self.post("feedback", report, origin="https://evil.example"), 403)
        self.assertEqual(self.post("feedback", report, origin=False), 403)
        self.assertEqual(self.post("feedback", b"[1]"), 400)
        self.assertEqual(self.post("other", report), 404)
        self.assertEqual(self.post("feedback", report), 204)
        self.assertEqual(self.server.feedback["shaders"][0]["error"], "0:4 syntax")

    def test_a_capture_is_asked_over_the_stream_and_answered_with_a_png(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/%s/events" % self.server.token, headers={"Host": "127.0.0.1:%d" % self.server.port})
        stream = connection.getresponse()

        def page():   # What app.js does on a capture event.
            while True:
                line = stream.fp.readline()
                if line.startswith(b"event: capture"):
                    self.assertEqual(self.post("capture", b"not a png", kind="image/png"), 400)
                    self.post("capture", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, kind="image/png")
                    return
        thread = threading.Thread(target=page, daemon=True)
        thread.start()
        while self.server.streams < 1:
            threading.Event().wait(0.01)
        self.assertTrue(self.server.capture(5).startswith(b"\x89PNG"))
        thread.join(5)

    def test_no_page_no_picture(self):
        self.assertIsNone(self.server.capture(0.2))


if __name__ == "__main__":
    unittest.main()
