# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The web preview's server and the export's draw-call merging, without Blender (doc/angelo/web_export.rst).

The server is loopback only, behind a random path, refuses other Host headers (DNS rebinding) and anything outside
its page folder, and tells the page when a new export is ready. The merge bakes world transforms into vertices and
joins only static, unshared, unskinned nodes; the exporter's classes are stood in for by plain ones here.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import contextlib
import errno
import http.client
import io
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import web_merge  # noqa: E402
from angelo_agent.web_preview_server import PreviewServer, page_policy  # noqa: E402


class PreviewServerTests(unittest.TestCase):
    def setUp(self):
        self.server = PreviewServer(marks={"<!--WORDMARK-->": "<svg id='mark'></svg>"})
        self.addCleanup(self.server.close)

    def get(self, path, host=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", path, headers={"Host": host or "127.0.0.1:%d" % self.server.port})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.read(), response

    def test_loopback_only_with_a_random_path(self):
        self.assertEqual(self.server._server.server_address[0], "127.0.0.1")
        self.assertRegex(self.server.url, r"^http://127\.0\.0\.1:\d+/[A-Za-z0-9_-]{16,}/$")

    def test_the_page_is_served_with_its_policy(self):
        status, kind, body, response = self.get("/%s/" % self.server.token)
        self.assertEqual(status, 200)
        self.assertTrue(kind.startswith("text/html"))
        self.assertIn(b"<svg id='mark'></svg>", body)   # The wordmark, inline.
        policy = response.getheader("Content-Security-Policy")
        self.assertEqual(policy, page_policy(body.decode("utf-8")))
        self.assertIn("default-src 'none'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_other_hosts_paths_and_files_are_refused(self):
        token = self.server.token
        self.assertEqual(self.get("/%s/" % token, host="evil.example:%d" % self.server.port)[0], 403)
        self.assertEqual(self.get("/wrong-token/")[0], 404)
        for path in ("../web_preview_server.py", "vendor/../../web_preview.py", ".hidden", "vendor//three/LICENSE"):
            self.assertEqual(self.get("/%s/%s" % (token, path))[0], 404, path)
        status, kind, _body, _response = self.get("/%s/vendor/three/build/three.module.min.js" % token)
        self.assertEqual((status, kind), (200, "text/javascript; charset=utf-8"))
        wasm = self.get("/%s/vendor/three/examples/jsm/libs/draco/gltf/draco_decoder.wasm" % token)
        self.assertEqual(wasm[1], "application/wasm")

    def test_a_published_export_reaches_the_page_and_its_event_stream(self):
        token = self.server.token
        self.assertEqual(self.get("/%s/scene.glb" % token)[0], 404)   # Nothing exported yet.
        self.server.publish(b"glTF-bytes", {"status": "ok", "budget": {"bytes": 10}})
        status, kind, body, _response = self.get("/%s/scene.glb" % token)
        self.assertEqual((status, kind, body), (200, "model/gltf-binary", b"glTF-bytes"))
        report = self.get("/%s/report.json" % token)[2]
        self.assertIn(b'"version": 1', report)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/%s/events" % token, headers={"Host": "localhost:%d" % self.server.port})
        response = connection.getresponse()
        self.assertEqual(response.getheader("Content-Type"), "text/event-stream")
        self.assertEqual(response.fp.readline(), b"event: version\n")
        self.assertEqual(response.fp.readline(), b"data: 1\n")

    def test_closing_ends_the_server(self):
        port = self.server.port
        self.server.close()
        self.server.close()   # Idempotent.
        with self.assertRaises(OSError):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.request("GET", "/")
            connection.getresponse()


class PreviewServerLoadTests(unittest.TestCase):
    """A page load while the server's accept thread can't run, and a browser that goes away mid-reply.

    The accept thread needs Python's lock, which Blender's main thread keeps through the long C calls of an export
    while a World bake loads every core; the browser meanwhile opens the page's files over many connections at once.
    Every connection must wait in the backlog rather than be refused (Windows) or dropped (macOS, Linux)."""

    PAGE_LOAD = 32       # Connections at once: a reload's modules, with room for a second reload and the stream.
    CONNECT_S = 3.0      # Past macOS's and Linux's first SYN retry (1 s): a dropped connection times out.

    def setUp(self):
        self.server = PreviewServer()
        self.addCleanup(self.server.close)
        self.address = ("127.0.0.1", self.server.port)
        self.host = "127.0.0.1:%d" % self.server.port

    def request(self, name):
        return ("GET /%s/%s HTTP/1.0\r\nHost: %s\r\n\r\n" % (self.server.token, name, self.host)).encode("ascii")

    def hold_accepts(self):
        """The accept loop stops after its next accept until ``release`` is set, as while the main thread exports."""
        held, release = threading.Event(), threading.Event()
        serve = self.server._server.process_request

        def process_request(request, address):
            held.set()
            release.wait(30)
            serve(request, address)
        self.server._server.process_request = process_request
        self.addCleanup(release.set)
        return held, release

    def test_a_page_load_is_never_refused_while_the_server_cannot_accept(self):
        held, release = self.hold_accepts()
        first = socket.create_connection(self.address, timeout=5)
        self.addCleanup(first.close)
        first.sendall(self.request("app.js"))
        self.assertTrue(held.wait(5))
        files = ["app.js", "look.js", "panel.js", "compare.js", "shaders.js", "app.css",
                 "vendor/three/build/three.module.min.js", "report.json", ""]
        outcomes = [None] * self.PAGE_LOAD

        def load(index):
            try:
                sock = socket.create_connection(self.address, timeout=self.CONNECT_S)
            except OSError as error:   # Refused (Windows) or timed out after dropped SYNs (macOS, Linux).
                outcomes[index] = error
                return
            sock.settimeout(10)
            sock.sendall(self.request(files[index % len(files)]))
            outcomes[index] = sock
        threads = [threading.Thread(target=load, args=(index,)) for index in range(self.PAGE_LOAD)]
        for thread in threads:
            thread.start()
        for version in range(5):   # Re-exports land meanwhile.
            self.server.publish(b"glTF-%d" % version, {"status": "ok"})
        for thread in threads:
            thread.join(self.CONNECT_S + 5)
        release.set()
        failed = [repr(item) for item in outcomes if not isinstance(item, socket.socket)]
        for item in outcomes:
            if isinstance(item, socket.socket):
                self.addCleanup(item.close)
        self.assertEqual(failed, [], "connections refused or dropped while the server could not accept")
        for sock in [first] + outcomes:
            reply = b""
            while chunk := sock.recv(65536):
                reply += chunk
            self.assertTrue(reply.startswith(b"HTTP/1.0 200 "), reply[:80])

    def test_an_aborted_reply_is_quiet_and_the_server_keeps_serving(self):
        self.server.publish(b"g" * (32 << 20), {"status": "ok"})   # Larger than any socket buffer.
        ended = threading.Event()
        shutdown = self.server._server.shutdown_request

        def shutdown_request(request):
            shutdown(request)
            ended.set()
        self.server._server.shutdown_request = shutdown_request
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed):
            sock = socket.create_connection(self.address, timeout=5)
            sock.sendall(self.request("scene.glb"))
            self.assertTrue(sock.recv(1))   # The reply has started.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
            sock.close()                    # A reset, as a reload aborts the download (WinError 10053/10054).
            self.assertTrue(ended.wait(10))
        self.assertEqual(printed.getvalue(), "")
        with socket.create_connection(self.address, timeout=5) as again:
            again.sendall(self.request("report.json"))
            self.assertTrue(again.recv(64).startswith(b"HTTP/1.0 200 "))

    def test_only_a_browser_going_away_counts_as_aborted(self):
        from angelo_agent.web_preview_server import aborted
        for error in (ConnectionAbortedError(10053, "aborted"), BrokenPipeError(), ConnectionResetError(),
                      TimeoutError(), OSError(errno.EPIPE, "pipe")):
            self.assertTrue(aborted(error), error)
        for error in (OSError(errno.EACCES, "denied"), ValueError("bad"), KeyError("x"), None):
            self.assertFalse(aborted(error), error)


# Stand-ins for the exporter's gltf2_io classes, with their constructor signatures.
class BinaryData:
    def __init__(self, data, target=None):
        self.data, self.bufferViewTarget = data, target


class Accessor:
    def __init__(self, buffer_view, byte_offset, component_type, count, extensions, extras, max, min, name,  # noqa: A002
                 normalized, sparse, type):  # noqa: A002
        self.buffer_view, self.byte_offset, self.component_type, self.count = buffer_view, byte_offset, component_type, count
        self.extensions, self.extras, self.max, self.min, self.name = extensions, extras, max, min, name
        self.normalized, self.sparse, self.type = normalized, sparse, type


class Primitive:
    def __init__(self, attributes, extensions, extras, indices, material, mode, targets):
        self.attributes, self.extensions, self.extras, self.indices = attributes, extensions, extras, indices
        self.material, self.mode, self.targets = material, mode, targets


class Mesh:
    def __init__(self, extensions, extras, name, primitives, weights):
        self.extensions, self.extras, self.name, self.primitives, self.weights = extensions, extras, name, primitives, weights


class Node:
    def __init__(self, camera, children, extensions, extras, matrix, mesh, name, rotation, scale, skin, translation,
                 weights):
        self.camera, self.children, self.extensions, self.extras, self.matrix = camera, children, extensions, extras, matrix
        self.mesh, self.name, self.rotation, self.scale, self.skin = mesh, name, rotation, scale, skin
        self.translation, self.weights = translation, weights


class Scene:
    def __init__(self, nodes):
        self.nodes = nodes


def accessor(values, kind, component=5126):
    array = np.asarray(values, dtype=web_merge.DTYPE[component])
    return Accessor(BinaryData(array.tobytes()), None, component, len(array), None, None, None, None, None, False, None,
                    kind)


def triangle(material, normal=(0.0, 0.0, 1.0)):
    positions = accessor([(0, 0, 0), (1, 0, 0), (0, 1, 0)], "VEC3")
    normals = accessor([normal] * 3, "VEC3")
    return Primitive({"POSITION": positions, "NORMAL": normals}, None, None, accessor([0, 1, 2], "SCALAR", 5123),
                     material, None, None)


def node(name, primitives, translation=None, scale=None, mesh=None, **fields):
    mesh = mesh or Mesh(None, None, name, primitives, None)
    return Node(fields.get("camera"), fields.get("children", []), fields.get("extensions"), None, None, mesh, name,
                None, scale, fields.get("skin"), translation, None)


class MergeTests(unittest.TestCase):
    def positions(self, primitive):
        return web_merge._values(primitive.attributes["POSITION"])

    def test_static_nodes_sharing_a_material_become_one_draw(self):
        wood, stone = object(), object()
        scene = Scene([node("A", [triangle(wood)], translation=[10, 0, 0]), node("B", [triangle(wood)]),
                       node("C", [triangle(stone)]), node("D", [triangle(stone)], scale=[2, 2, 2])])
        result = web_merge.merge_scene(scene, static=lambda name: True)
        self.assertEqual(result, {"objects": 4, "groups": 2})
        self.assertEqual(len(scene.nodes), 1)
        merged = scene.nodes[0].mesh.primitives
        self.assertEqual({id(item.material) for item in merged}, {id(wood), id(stone)})
        joined = next(item for item in merged if item.material is wood)
        self.assertEqual(sorted(map(tuple, self.positions(joined).tolist()))[-1], (11.0, 0.0, 0.0))   # A's world.
        self.assertEqual(web_merge._values(joined.indices).reshape(-1).tolist(), [0, 1, 2, 3, 4, 5])
        self.assertEqual(joined.attributes["POSITION"].max, [11.0, 1.0, 0.0])

    def test_what_the_web_could_tell_apart_stays(self):
        wood = object()
        shared = Mesh(None, None, "Shared", [triangle(wood)], None)
        scene = Scene([
            node("Moving", [triangle(wood)]),                                  # Animated on the Blender side.
            node("Twin1", None, mesh=shared), node("Twin2", None, mesh=shared),   # One mesh drawn twice.
            node("Skinned", [triangle(wood)], skin=object()),
            node("Lamp", [triangle(wood)], extensions={"KHR_lights_punctual": {"light": 0}}),
            node("Parent", [triangle(wood)], children=[node("Child", [triangle(wood)])]),
            node("Alone", [triangle(wood)]),
        ])
        before = list(scene.nodes)
        result = web_merge.merge_scene(scene, static=lambda name: name != "Moving")
        # Only Child and Alone qualify; they merge with each other.
        self.assertEqual(result["objects"], 2)
        self.assertEqual(scene.nodes[:-1], before[:6])
        self.assertEqual(before[5].children, [])

    def test_a_single_candidate_keeps_its_name(self):
        scene = Scene([node("Only", [triangle(object())])])
        self.assertEqual(web_merge.merge_scene(scene, static=lambda name: True), {"objects": 0, "groups": 0})
        self.assertEqual(scene.nodes[0].name, "Only")

    def test_mirrored_objects_keep_outward_faces_and_unit_normals(self):
        wood = object()
        scene = Scene([node("M", [triangle(wood)], scale=[-1, 2, 1]), node("N", [triangle(wood)])])
        web_merge.merge_scene(scene, static=lambda name: True)
        joined = scene.nodes[0].mesh.primitives[0]
        indices = web_merge._values(joined.indices).reshape(-1).tolist()
        self.assertIn(indices[:3], ([2, 1, 0], [5, 4, 3]))   # One of the two triangles was mirrored and flipped.
        normals = web_merge._values(joined.attributes["NORMAL"])
        self.assertTrue(np.allclose(np.linalg.norm(normals, axis=1), 1.0))

    def test_large_merges_use_32_bit_indices(self):
        wood = object()
        count = 40000
        def big(name):
            positions = accessor(np.random.default_rng(1).random((count, 3)), "VEC3")
            return node(name, [Primitive({"POSITION": positions}, None, None,
                                         accessor(np.arange(count - count % 3), "SCALAR", 5125), wood, 4, None)])
        scene = Scene([big("A"), big("B")])
        web_merge.merge_scene(scene, static=lambda name: True)
        self.assertEqual(scene.nodes[0].mesh.primitives[0].indices.component_type, web_merge.UNSIGNED_INT)
        self.assertEqual(struct.calcsize("<I"), 4)


if __name__ == "__main__":
    unittest.main()
