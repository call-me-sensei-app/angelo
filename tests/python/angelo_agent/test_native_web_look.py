# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web export's look without Blender (doc/angelo/web_export.rst, "The look"): the three.js tone mapping chosen
by measurement, the ``angelo_look`` extras, the World's form, its Radiance picture as the page decodes it, the .glb
patch after gltfpack, the Compare view's camera and the server's Compare route."""

import hashlib
import http.client
import json
import math
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import web_budget, web_compare, web_compat, web_environment, web_glb, web_look, web_tone  # noqa: E402
from angelo_agent import web_tone_known, web_world  # noqa: E402
from angelo_agent.web_preview_server import PreviewServer  # noqa: E402


def glb(document, binary=b""):
    """A .glb of ``document`` and ``binary`` (both padded as the format requires)."""
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    binary += b"\0" * (-len(binary) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text
    if binary:
        chunks += struct.pack("<II", len(binary), 0x004E4942) + binary
    return struct.pack("<III", 0x46546C67, 2, 12 + len(chunks)) + chunks


class ToneTests(unittest.TestCase):
    def test_every_candidate_is_found_as_itself(self):
        # A view transform that is exactly one of three.js's is that one, with no difference to report.
        for tone, output in web_tone.CANDIDATES:
            found = web_tone.nearest(web_tone.three_display(tone, output, web_tone.SAMPLES))
            self.assertEqual((found["tone_mapping"], found["output"], found["difference"]), (tone, output, 0.0))

    def test_a_curve_between_them_is_given_the_nearest_with_its_difference(self):
        # A held-out curve, not any candidate: a Reinhard on luminance. Whatever wins, its difference is the
        # smallest of all candidates and greater than zero.
        samples = web_tone.SAMPLES
        reference = web_tone.srgb_encode(samples / (1.0 + samples))
        found = web_tone.nearest(reference)
        scores = [float(np.mean(np.abs(web_tone.three_display(tone, output, samples) - reference))) * 100
                  for tone, output in web_tone.CANDIDATES]
        self.assertAlmostEqual(found["difference"], round(min(scores), 1))
        self.assertGreater(found["difference"], web_tone.EXACT)

    def test_three_js_curves_at_known_points(self):
        grey = np.array([[0.18, 0.18, 0.18]])
        self.assertAlmostEqual(float(web_tone.three_display("LinearToneMapping", web_tone.SRGB, grey)[0, 0]),
                               0.4613, places=3)   # Middle grey through the sRGB transfer only.
        self.assertEqual(float(web_tone.three_display("LinearToneMapping", web_tone.LINEAR_SRGB, grey)[0, 0]), 0.18)
        white = np.array([[100.0, 100.0, 100.0]])
        for tone in ("AgXToneMapping", "ACESFilmicToneMapping", "NeutralToneMapping"):   # Roll-offs never clip early.
            self.assertGreater(float(web_tone.three_display(tone, web_tone.SRGB, white)[0, 0]), 0.95, tone)
            self.assertLess(float(web_tone.three_display(tone, web_tone.SRGB, grey)[0, 0]), 0.8, tone)

    def test_exposure_is_two_to_the_stops(self):
        self.assertEqual(web_tone.exposure(0), 1.0)
        self.assertEqual(web_tone.exposure(1.5), round(2 ** 1.5, 6))
        self.assertEqual(web_tone.exposure(-2), 0.25)

    def test_the_thresholds_agree_with_the_findings(self):
        self.assertEqual((web_tone.EXACT, web_tone.CLOSE), (web_compat.EXACT_TONE, web_compat.CLOSE_TONE))

    def test_an_unreadable_configuration_is_none(self):
        self.assertIsNone(web_tone.measure(str(ROOT / "no-such-config.ocio"), "AgX", None))


class KnownToneTests(unittest.TestCase):
    """Angelo's own configuration is measured ahead (``web_tone_known``), so an export never waits for OpenColorIO."""
    CONFIG = ROOT / "release/datafiles/colormanagement/config.ocio"

    def setUp(self):
        web_tone._measured.clear()
        self.addCleanup(web_tone._measured.clear)

    def test_the_table_was_measured_on_the_shipped_configuration_and_code(self):
        self.assertEqual(web_tone_known.CONFIG_SHA256, hashlib.sha256(self.CONFIG.read_bytes()).hexdigest(),
                         "Write web_tone_known.py again: make_web_tone_known.py")
        self.assertEqual(web_tone_known.WEB_TONE_SHA256,
                         hashlib.sha256((ROOT / "scripts/addons_core/angelo_agent/web_tone.py").read_bytes())
                         .hexdigest(), "Write web_tone_known.py again: make_web_tone_known.py")

    def test_every_view_of_the_display_without_a_look_is_in_it(self):
        text = self.CONFIG.read_text(encoding="utf-8")
        display = re.split(r"\n  \S", text.split("displays:", 1)[1].split("\n  sRGB:", 1)[1], maxsplit=1)[0]
        views = re.findall(r"!<View> \{name: ([^,]+),", display)
        self.assertGreater(len(views), 3)
        for view in views:
            self.assertIn((view, None), web_tone_known.MEASURED)

    def test_the_shipped_configuration_is_answered_without_measuring(self):
        with mock.patch.object(web_tone, "_measure", side_effect=AssertionError("measured")):
            found = web_tone.measure(str(self.CONFIG), "Filmic", "None")
        tone, output, difference = web_tone_known.MEASURED[("Filmic", None)]
        self.assertEqual(found, {"tone_mapping": tone, "output": output, "difference": difference})

    def test_any_other_configuration_is_measured(self):
        with tempfile.TemporaryDirectory() as folder:
            other = Path(folder) / "config.ocio"
            other.write_bytes(self.CONFIG.read_bytes() + b"\n# a studio's own\n")
            with mock.patch.object(web_tone, "_measure", return_value=None) as measured:
                web_tone.measure(str(other), "Filmic", None)
                web_tone.measure(str(self.CONFIG), "Not A View", None)
        self.assertEqual(measured.call_count, 2)


def look(**changes):
    return dict({"view_transform": "Filmic", "look": None, "exposure": 1.0, "gamma": 1.0, "curves": False,
                 "white_balance": False, "display": "sRGB", "readable": True, "tone_mapping": "AgXToneMapping",
                 "output": "srgb", "difference": 2.5}, **changes)


class ExtrasTests(unittest.TestCase):
    def test_three_js_settings_by_their_own_names(self):
        extras = web_look.extras(look(), {"kind": "NONE", "background": True}, None)["angelo_look"]
        self.assertEqual((extras["toneMapping"], extras["toneMappingExposure"], extras["outputColorSpace"]),
                         ("AgXToneMapping", 2.0, "srgb"))
        self.assertEqual(extras["blender"]["viewTransform"], "Filmic")
        self.assertIsNone(extras["environment"])

    def test_each_world_form(self):
        colour = web_look.extras(look(), {"kind": "COLOR", "color": [0.1, 0.2, 0.3], "intensity": 2.0,
                                          "background": False}, None)["angelo_look"]
        self.assertEqual(colour["environment"], {"type": "color", "color": [0.1, 0.2, 0.3], "intensity": 2.0})
        self.assertFalse(colour["background"])
        picture = {"data": b"#?RADIANCE", "width": 1024, "height": 512}
        image = web_look.extras(look(), {"kind": "IMAGE", "intensity": 0.8, "status": "ready"}, picture)["angelo_look"]
        self.assertEqual((image["environment"]["bufferView"], image["environment"]["source"]), (b"#?RADIANCE", "hdri"))
        pending = web_look.extras(look(), {"kind": "BAKE", "status": "pending"}, None)["angelo_look"]
        self.assertEqual(pending["environment"], {"type": "pending"})
        failed = web_look.extras(look(), {"kind": "BAKE", "status": "failed"}, None)["angelo_look"]
        self.assertIsNone(failed["environment"])

    def test_findings_say_what_the_look_loses(self):
        def codes(**changes):
            return [item["code"] for item in web_compat.findings({"look": look(**changes)})]
        self.assertEqual(codes(difference=0.0), [])
        self.assertEqual(codes(), ["tone_close"])
        self.assertEqual(codes(difference=8.1), ["tone_far"])
        self.assertEqual(codes(readable=False, difference=None), ["tone_unread"])
        self.assertEqual(sorted(codes(difference=0.0, gamma=1.3, curves=True, white_balance=True, display="Rec.2020")),
                         ["display_curves", "display_device", "display_gamma", "white_balance"])
        found = web_compat.english(web_compat.findings({"look": look(look="AgX - Punchy", difference=8.1)})[0])
        self.assertIn("Filmic (AgX - Punchy)", found["message"])
        self.assertIn("8.1%", found["message"])


class RadianceTests(unittest.TestCase):
    @staticmethod
    def decode(data):
        """The file as look.js parseRadiance decodes it (three.js HDRLoader's scale)."""
        header, _, rest = data.partition(b"\n\n")
        size, _, body = rest.partition(b"\n")
        height, width = int(size.split()[1]), int(size.split()[3])
        body, at, rows = bytearray(body), 0, []
        for _ in range(height):
            row = np.zeros((width, 4), np.uint8)
            if width >= 8 and body[at] == 2 and body[at + 1] == 2:
                at += 4
                for channel in range(4):
                    x = 0
                    while x < width:
                        count = body[at]
                        at += 1
                        if count > 128:
                            row[x:x + count - 128, channel] = body[at]
                            at += 1
                            x += count - 128
                        else:
                            row[x:x + count, channel] = body[at:at + count]
                            at += count
                            x += count
            else:
                row[:] = np.frombuffer(bytes(body[at:at + width * 4]), np.uint8).reshape(width, 4)
                at += width * 4
            rows.append(row)
        rgbe = np.array(rows, dtype=np.float64)
        return header, rgbe[..., :3] * (2.0 ** (rgbe[..., 3:] - 128) / 255.0) * (rgbe[..., 3:] > 0)

    def test_round_trip_within_rgbe_precision(self):
        rng = np.random.default_rng(7)
        for width in (300, 1024, 4):   # Partial last literal run, whole runs, too narrow for runs (flat).
            rows = width // 2 or 2
            pixels = rng.random((rows, width, 3)) ** 3 * 2.0 ** rng.uniform(-8, 12, (rows, width, 1))
            pixels[0, 0] = 0.0   # Black stays black.
            header, back = self.decode(web_environment.radiance(pixels))
            self.assertTrue(header.startswith(b"#?RADIANCE\nFORMAT=32-bit_rle_rgbe"))
            self.assertEqual(back.shape, pixels.shape)
            peak = pixels.max(axis=2, keepdims=True)
            self.assertLess(float(np.max(np.abs(back - pixels) / np.maximum(peak, 1e-30))), 2.0 / 128)
            self.assertEqual(float(back[0, 0].max()), 0.0)

    def test_negatives_and_nan_are_black(self):
        _header, back = self.decode(web_environment.radiance(np.array([[[-1.0, np.nan, 0.5]] * 8])))
        self.assertEqual(back[0, 0, 0], 0.0)
        self.assertEqual(back[0, 0, 1], 0.0)
        self.assertAlmostEqual(back[0, 0, 2], 0.5, places=2)


class PictureFailureTests(unittest.TestCase):
    def test_a_reason_never_carries_a_local_path(self):
        self.assertEqual(web_environment._reason(FileNotFoundError(2, "No such file", "/Users/someone/sky.exr")),
                         "FileNotFoundError")
        self.assertEqual(web_environment._reason(web_environment.PictureError("the bake took longer than 120 s")),
                         "the bake took longer than 120 s")


class GlbTests(unittest.TestCase):
    def test_extras_with_bytes_become_a_buffer_view(self):
        document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [], "extras": {"kept": 1}}],
                    "buffers": [{"byteLength": 3}], "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 3}]}
        picture = b"#?RADIANCE picture"
        extras = {"angelo_look": {"environment": {"bufferView": picture}}}
        data = web_glb.add_scene_extras(glb(document, b"abc"), extras)
        patched, binary = web_budget.read_glb(data)
        extras = patched["scenes"][0]["extras"]
        self.assertEqual(extras["kept"], 1)
        view = patched["bufferViews"][extras["angelo_look"]["environment"]["bufferView"]]
        self.assertEqual(binary[view["byteOffset"]:view["byteOffset"] + view["byteLength"]], picture)
        self.assertEqual(binary[:3], b"abc")
        self.assertEqual(web_budget.measure_bytes(data)["environment_bytes"], len(picture))

    def test_an_empty_file_gets_its_first_buffer(self):
        empty = glb({"asset": {"version": "2.0"}, "scenes": [{}]})
        data = web_glb.add_scene_extras(empty, {"angelo_look": {"x": b"1234"}})
        patched, binary = web_budget.read_glb(data)
        self.assertEqual(patched["buffers"][0]["byteLength"], 4)
        self.assertEqual(binary, b"1234")

    def test_the_exporter_gets_its_own_binary_data(self):
        wrapped = web_glb.for_exporter({"a": [b"x", {"b": b"y"}], "c": 1}, lambda data: ("binary", data))
        self.assertEqual(wrapped, {"a": [("binary", b"x"), {"b": ("binary", b"y")}], "c": 1})


class CompareCameraTests(unittest.TestCase):
    def test_a_y_up_camera_becomes_blenders(self):
        # three.js: at (0, 1, 5) looking down -Z at the origin's height. Blender: at (0, -5, 1) looking along +Y.
        matrix = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 1, 5, 1]   # Column-major, as matrixWorld.elements.
        seen = web_compare.camera({"matrix": matrix, "fov": 90, "near": 0.1, "far": 100, "width": 200, "height": 100})
        view = seen["view_matrix"]
        ahead = [sum(view[row][k] * point for k, point in enumerate((0.0, 3.0, 1.0, 1.0))) for row in range(4)]
        self.assertAlmostEqual(ahead[2], -8.0)   # Blender's (0, 3, 1) is 8 in front of the camera.
        self.assertAlmostEqual(ahead[0], 0.0)
        self.assertAlmostEqual(ahead[1], 0.0)
        projection = seen["projection_matrix"]
        self.assertAlmostEqual(projection[1][1], 1.0 / math.tan(math.radians(45)))
        self.assertAlmostEqual(projection[0][0], projection[1][1] / 2.0)   # A 2:1 canvas.

    def test_size_and_bad_cameras(self):
        self.assertEqual(web_compare.size({"width": 2560, "height": 1440}), (1280, 720))
        self.assertEqual(web_compare.size({"width": 800, "height": 600}), (800, 600))
        for bad in ({"matrix": [0] * 15}, {"matrix": [float("nan")] * 16, "fov": 40, "near": 1, "far": 2},
                    {"matrix": [1] * 16, "fov": 400, "near": 1, "far": 2}, {"width": True, "height": 1}):
            with self.assertRaises(web_compare.CompareError):
                web_compare.camera(dict({"width": 10, "height": 10}, **bad))


class _Inputs(list):
    def __getitem__(self, key):
        return next(item for item in self if item.name == key) if isinstance(key, str) else list.__getitem__(self, key)

    def get(self, key):
        return next((item for item in self if item.name == key), None)


def _node(kind, name, inputs=(), outputs=("Out",), **properties):
    node = SimpleNamespace(type=kind, name=name, bl_idname="ShaderNode" + kind.title(), mute=False, label="",
                           target="ALL", is_active_output=True, **properties)
    node.inputs = _Inputs(SimpleNamespace(name=key, identifier=key, default_value=value, is_unavailable=False)
                          for key, value in inputs)
    node.outputs = _Inputs(SimpleNamespace(name=key, identifier=key, default_value=(0.5, 0.5, 0.5, 1.0))
                           for key in outputs)
    return node


def _world(nodes, links):
    tree = SimpleNamespace(nodes=nodes, links=[
        SimpleNamespace(from_node=a, from_socket=a.outputs[out], to_node=b, to_socket=b.inputs[to], is_muted=False,
                        is_valid=True) for a, out, b, to in links])
    return SimpleNamespace(name="World", node_tree=tree, color=(0.05, 0.05, 0.05))


def _scene(world, transparent=False):
    return SimpleNamespace(world=world, render=SimpleNamespace(film_transparent=transparent, engine="CYCLES"))


def _graph(colour_from=None, strength_from=None, extra=()):
    output = _node("OUTPUT_WORLD", "Output", (("Surface", None), ("Volume", None)))
    background = _node("BACKGROUND", "Background", (("Color", (0.2, 0.3, 0.4, 1.0)), ("Strength", 1.5)))
    links = [(background, "Out", output, "Surface")] + list(extra)
    nodes = [output, background]
    if colour_from is not None:
        nodes.append(colour_from)
        links.append((colour_from, "Out", background, "Color"))
    return nodes, links, background


class WorldPlanTests(unittest.TestCase):
    def image(self):
        return SimpleNamespace(name="sky.exr", source="FILE", packed_file=SimpleNamespace(size=10), size=(64, 32),
                               colorspace_settings=SimpleNamespace(name="Linear Rec.709"), is_dirty=False)

    def environment(self):
        return _node("TEX_ENVIRONMENT", "Environment", (("Vector", (0, 0, 0)),), projection="EQUIRECTANGULAR",
                     image=self.image())

    def test_no_world_and_a_plain_colour(self):
        self.assertEqual(web_world.plan(_scene(None))["kind"], "NONE")
        nodes, links, _background = _graph()
        plan = web_world.plan(_scene(_world(nodes, links), transparent=True))
        self.assertEqual((plan["kind"], plan["color"], plan["intensity"], plan["background"]),
                         ("COLOR", [0.2, 0.3, 0.4], 1.5, False))
        rgb = _node("RGB", "RGB")
        plan = web_world.plan(_scene(_world(*_graph(rgb)[:2])))
        self.assertEqual((plan["kind"], plan["color"]), ("COLOR", [0.5, 0.5, 0.5]))

    def test_an_hdri_read_along_the_view_or_turned_about_z(self):
        texture = self.environment()
        plan = web_world.plan(_scene(_world(*_graph(texture)[:2])))
        self.assertEqual((plan["kind"], plan["image"], plan["turn"], plan["intensity"]), ("IMAGE", "sky.exr", 0.0, 1.5))
        coordinates = _node("TEX_COORD", "Coordinates", outputs=("Generated",))
        mapping = _node("MAPPING", "Mapping", (("Vector", (0, 0, 0)), ("Location", (0, 0, 0)),
                                               ("Rotation", (0, 0, math.pi / 2)), ("Scale", (1, 1, 1))),
                        vector_type="POINT")
        nodes, links, _background = _graph(texture, extra=[(mapping, "Out", texture, "Vector"),
                                                            (coordinates, "Generated", mapping, "Vector")])
        plan = web_world.plan(_scene(_world(nodes + [mapping, coordinates], links)))
        self.assertEqual((plan["kind"], plan["turn"]), ("IMAGE", 0.25))
        mapping.vector_type = "TEXTURE"   # The inverse turn.
        self.assertEqual(web_world.plan(_scene(_world(nodes + [mapping, coordinates], links)))["turn"], 0.75)
        mapping.inputs["Rotation"].default_value = (0.3, 0, 0)   # Tilted: not a turn of the picture.
        self.assertEqual(web_world.plan(_scene(_world(nodes + [mapping, coordinates], links)))["kind"], "BAKE")

    def test_everything_else_is_baked_and_says_what_differs(self):
        sky = _node("TEX_SKY", "Sky", sky_type="MULTIPLE_SCATTERING")
        self.assertEqual(web_world.plan(_scene(_world(*_graph(sky)[:2])))["kind"], "BAKE")
        path = _node("LIGHT_PATH", "Light Path", outputs=("Is Camera Ray",))
        nodes, links, background = _graph()
        plan = web_world.plan(_scene(_world(nodes + [path], links + [(path, "Is Camera Ray", background, "Strength")])))
        self.assertEqual((plan["kind"], plan["light_path"]), ("BAKE", True))
        found = {item["code"] for item in web_compat.findings({"world": dict(plan, size=(1024, 512))})}
        self.assertEqual(found, {"world_baked", "world_light_path"})

    def test_the_key_follows_the_state_of_the_world(self):
        sky = _node("TEX_SKY", "Sky", sky_type="MULTIPLE_SCATTERING", sun_elevation=0.2)
        world = _world(*_graph(sky)[:2])
        before = web_world.plan(_scene(world))["key"]
        self.assertEqual(web_world.plan(_scene(world))["key"], before)
        sky.sun_elevation = 0.6
        self.assertNotEqual(web_world.plan(_scene(world))["key"], before)


class CompareRouteTests(unittest.TestCase):
    def setUp(self):
        self.server = PreviewServer()
        self.addCleanup(self.server.close)

    def request(self, method, name, body=None, origin=True):
        origin_value = "http://127.0.0.1:%d" % self.server.port
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        headers = {"Host": "127.0.0.1:%d" % self.server.port, "Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin_value
        connection.request(method, "/%s/%s" % (self.server.token, name), body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()

    def test_the_page_asks_and_the_main_thread_answers(self):
        camera = json.dumps({"id": 3, "matrix": [1] * 16}).encode()
        self.assertEqual(self.request("POST", "compare", camera, origin=False)[0], 403)   # Only its own page.
        self.assertIsNone(self.server.take_compare())
        self.assertEqual(self.request("POST", "compare", camera)[0], 204)
        self.assertEqual(self.request("POST", "compare", b"x" * 5000)[0], 413)
        self.assertEqual(self.server.take_compare()["id"], 3)
        self.assertIsNone(self.server.take_compare())   # Taken once.
        before = self.server.viewports
        self.server.publish_viewport(b"\x89PNG picture", {"ok": True, "request": 3})
        self.assertEqual(self.server.viewports, before + 1)
        self.assertEqual(self.request("GET", "viewport.png"), (200, b"\x89PNG picture"))
        report = json.loads(self.request("GET", "viewport.json")[1])
        self.assertEqual((report["ok"], report["request"], report["version"]), (True, 3, before + 1))

    def test_a_viewport_wakes_the_event_stream(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        connection.request("GET", "/%s/events" % self.server.token,
                           headers={"Host": "127.0.0.1:%d" % self.server.port})
        response = connection.getresponse()
        self.assertIn(b"event: version", response.fp.readline() + response.fp.readline())
        response.fp.readline()
        self.server.publish_viewport(None, {"ok": False, "request": 1, "error": "no 3D viewport"})
        self.assertEqual(response.fp.readline(), b"event: viewport\n")
        connection.close()


if __name__ == "__main__":
    unittest.main()
