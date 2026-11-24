# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""An Angelo Library mirror on this computer for GUI suites: a loopback server in the suite's process serves
``latest.json``, the gzipped index and every file, and ``start`` points ``ANGELO_LIBRARY_URL`` at it, so nothing
leaves the machine. Items are built here (``box_glb`` models with a thumbnail each), in the catalogue's own format
(``toonlab-catalog/1``). ``test_blender_library`` keeps its own mirror (gated thumbnails for its streaming check);
this one is for suites that only need models to find and add.
"""

import gzip
import hashlib
import json
import os
import struct
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def box_glb(name="Box", size=(1.0, 1.0, 1.0)):
    """A box standing on its base (z from 0 to its height in Blender, Y-up in glTF), ``size`` = width, depth,
    height in metres."""
    w, d, h = size[0] / 2, size[1] / 2, size[2]
    corners = [(x, y, z) for x in (-w, w) for y in (0.0, h) for z in (-d, d)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    indices = [i for a, b, c, e in faces for i in (a, b, c, a, c, e)]
    positions = b"".join(struct.pack("<3f", *c) for c in corners)
    index_bytes = b"".join(struct.pack("<H", i) for i in indices)
    blob = positions + index_bytes + b"\0" * (-len(index_bytes) % 4)
    document = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}],
                "nodes": [{"mesh": 0, "name": name}],
                "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
                "buffers": [{"byteLength": len(blob)}],
                "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                                {"buffer": 0, "byteOffset": len(positions), "byteLength": len(index_bytes)}],
                "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3",
                               "min": [-w, 0, -d], "max": [w, h, d]},
                              {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR"}]}
    text = json.dumps(document).encode()
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(blob), 0x004E4942) + blob
    return b"glTF" + struct.pack("<II", 2, 12 + len(chunks)) + chunks


def png(width, height, rgb):
    from angelo_agent.view_png import encode_png
    return encode_png(bytes(tuple(rgb) + (255,)) * (width * height), width, height)


class Mirror:
    def __init__(self):
        self.routes, self.items = {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = owner.routes.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

            def log_message(self, *args):
                pass
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def model(self, identity, title, tags, size=(1.0, 1.0, 1.0)):
        """Add a model item: a box of ``size`` named after its title."""
        slug = identity.replace(":", "-")
        data = box_glb(title, size)
        self.routes["/f/%s.glb" % slug] = data
        self.routes["/thumbs/%s.png" % slug] = png(64, 64, (90, 160, 150))
        self.items.append({"id": identity, "kind": "model", "title": title, "tags": list(tags), "categories": [],
                           "source": "fixture", "authors": ["Angelo QA"], "license": "CC0-1.0",
                           "attribution": title + " (CC0)", "attribution_required": False, "popularity": 10,
                           "thumb": self.base + "/thumbs/%s.png" % slug,
                           "files": [{"role": "model", "name": "model.glb", "url": self.base + "/f/%s.glb" % slug,
                                      "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}]})

    def start(self):
        """Publish the index and point the Library at this mirror; returns ``latest.json``'s URL."""
        packed = gzip.compress(json.dumps({"schema": "toonlab-catalog/1", "version": "qa-1",
                                           "items": self.items}).encode())
        self.routes["/index.json.gz"] = packed
        self.routes["/latest.json"] = json.dumps({"schema": "toonlab-catalog/1", "version": "qa-1",
                                                  "url": self.base + "/index.json.gz", "size": len(packed),
                                                  "sha256": hashlib.sha256(packed).hexdigest()}).encode()
        os.environ["ANGELO_LIBRARY_URL"] = self.base + "/latest.json"
        return os.environ["ANGELO_LIBRARY_URL"]
