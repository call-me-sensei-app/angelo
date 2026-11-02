# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Angelo Library's downloads (library_fetch.py) against a loopback server: the index refresh and its checks,
item files (size, checksum, redirects, names), the cache and its trim. Host tests only; nothing leaves this
computer (``ANGELO_LIBRARY_URL`` points the client at the loopback server)."""

import gzip
import hashlib
import json
import os
import tempfile
import threading
import unittest
import unittest.mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import team_library_fixtures  # noqa: F401  (puts the add-on folder on sys.path)
from angelo_agent import library_catalog as catalog, library_fetch as fetch


class Server:
    """Serves ``routes`` (path -> bytes, or (status, headers, bytes)); records every path asked."""

    def __init__(self):
        self.routes, self.asked = {}, []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.asked.append(self.path)
                route = owner.routes.get(self.path)
                status, headers, body = route if isinstance(route, tuple) else (200 if route is not None else 404, {}, route or b"")
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FetchCase(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.addCleanup(self.server.close)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        patcher = unittest.mock.patch.dict(os.environ, {fetch.URL_VARIABLE: self.server.base + "/latest.json"})
        patcher.start()
        self.addCleanup(patcher.stop)
        no_proxy = unittest.mock.patch("urllib.request.getproxies", return_value={})
        no_proxy.start()
        self.addCleanup(no_proxy.stop)

    def publish(self, version="v1", items=None, tamper=False):
        document = {"schema": catalog.SCHEMA, "version": version, "items": items or []}
        packed = gzip.compress(json.dumps(document).encode())
        self.server.routes["/index-%s.json.gz" % version] = packed + (b"x" if tamper else b"")
        self.server.routes["/latest.json"] = json.dumps({
            "schema": catalog.SCHEMA, "version": version, "url": self.server.base + "/index-%s.json.gz" % version,
            "size": len(packed), "sha256": hashlib.sha256(packed).hexdigest()}).encode()

    def file(self, name, data, path=None, **overrides):
        path = path or "/files/" + name
        self.server.routes[path] = data
        return dict({"role": "model", "name": name, "url": self.server.base + path, "size": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()}, **overrides)


class IndexTests(FetchCase):
    def test_refresh_downloads_once_and_keeps_it(self):
        self.publish("v1")
        document = fetch.refresh(self.root / "index")
        self.assertEqual(document["version"], "v1")
        self.assertIsNone(fetch.refresh(self.root / "index", "v1"))   # Current: latest.json only.
        self.assertEqual(self.server.asked.count("/index-v1.json.gz"), 1)
        self.assertEqual(fetch.read_local(self.root / "index")["version"], "v1")

    def test_a_tampered_index_is_refused_and_the_last_good_one_stays(self):
        self.publish("v1")
        fetch.refresh(self.root / "index")
        self.publish("v2", tamper=True)
        with self.assertRaises(fetch.FetchError) as caught:
            fetch.refresh(self.root / "index", "v1")
        self.assertEqual(caught.exception.refusal_code, "LIMIT_EXCEEDED")   # Longer than latest.json said.
        self.assertEqual(fetch.read_local(self.root / "index")["version"], "v1")

    def test_a_changed_local_index_is_not_read(self):
        self.publish("v1")
        fetch.refresh(self.root / "index")
        stored = next((self.root / "index").glob("index-*.json.gz"))
        data = bytearray(stored.read_bytes())
        data[12] ^= 0xFF
        stored.write_bytes(bytes(data))
        self.assertIsNone(fetch.read_local(self.root / "index"))

    def test_another_schema_asks_for_an_update(self):
        self.server.routes["/latest.json"] = json.dumps({"schema": "toonlab-catalog/9"}).encode()
        with self.assertRaises(fetch.FetchError) as caught:
            fetch.refresh(self.root / "index")
        self.assertIn("update Angelo", str(caught.exception))

    def test_a_decompression_bomb_is_bounded(self):
        with unittest.mock.patch.object(fetch, "MAX_INDEX_JSON", 1000):
            with self.assertRaises(fetch.FetchError):
                fetch._decode(gzip.compress(b" " * 5000))

    def test_a_https_link_off_toonlab_hosts_is_never_asked(self):
        with unittest.mock.patch.dict(os.environ, {fetch.URL_VARIABLE: "https://evil.example/latest.json"}):
            with self.assertRaises(fetch.FetchError) as caught:
                fetch.refresh(self.root / "index")
        self.assertEqual(caught.exception.refusal_code, "UNSUPPORTED")


    def test_no_request_follows_a_redirect(self):
        self.server.routes["/latest.json"] = (302, {"Location": self.server.base + "/elsewhere.json"}, b"")
        self.server.routes["/elsewhere.json"] = b"{}"
        with self.assertRaises(fetch.FetchError):
            fetch.refresh(self.root / "index")
        self.assertNotIn("/elsewhere.json", self.server.asked)
        self.server.routes["/t.png"] = (302, {"Location": self.server.base + "/u.png"}, b"")
        self.server.routes["/u.png"] = team_library_fixtures.png()
        self.assertIsNone(fetch.thumbnail(self.root, self.server.base + "/t.png"))
        self.assertNotIn("/u.png", self.server.asked)

    def test_an_unusable_index_never_replaces_the_last_good_one(self):
        self.publish("v1")
        fetch.refresh(self.root / "index")
        packed = gzip.compress(json.dumps({"schema": "other/2", "version": "v2", "items": []}).encode())
        self.server.routes["/index-v2.json.gz"] = packed
        self.server.routes["/latest.json"] = json.dumps({
            "schema": catalog.SCHEMA, "version": "v2", "url": self.server.base + "/index-v2.json.gz",
            "size": len(packed), "sha256": hashlib.sha256(packed).hexdigest()}).encode()
        with self.assertRaises(fetch.FetchError):
            fetch.refresh(self.root / "index", "v1")
        self.assertEqual(fetch.read_local(self.root / "index")["version"], "v1")

    def test_malformed_documents_are_fetch_errors(self):
        self.server.routes["/latest.json"] = b"[" * 50000 + b"]" * 50000
        with self.assertRaises(fetch.FetchError):
            fetch.refresh(self.root / "index")
        with self.assertRaises(fetch.FetchError):
            fetch._decode(gzip.compress(b"[1, 2]"))


class FileTests(FetchCase):
    def item(self, files):
        return {"id": "src:thing", "files": files}

    def test_files_are_checked_cached_and_hashed_again_before_use(self):
        files = [self.file("model.gltf", b"{}"), self.file("textures/a.png", b"PNGDATA", role="resource")]
        paths = fetch.fetch_files(self.root, self.item(files), files)
        self.assertEqual(paths["textures/a.png"].read_bytes(), b"PNGDATA")
        self.assertTrue(fetch.cached(self.root, self.item(files), files))
        asked = len(self.server.asked)
        fetch.fetch_files(self.root, self.item(files), files)
        self.assertEqual(len(self.server.asked), asked)   # Cached and still matching: nothing downloaded.
        paths["model.gltf"].write_bytes(b"{!")             # Changed on disk: downloaded again.
        fetch.fetch_files(self.root, self.item(files), files)
        self.assertEqual(paths["model.gltf"].read_bytes(), b"{}")

    def test_a_wrong_checksum_or_size_leaves_nothing(self):
        bad = self.file("model.glb", b"abc", sha256="0" * 64)
        with self.assertRaises(fetch.FetchError) as caught:
            fetch.fetch_files(self.root, self.item([bad]), [bad])
        self.assertEqual(caught.exception.refusal_code, "UNSUPPORTED")
        folder = fetch.item_folder(self.root, self.item([bad]), [bad])
        self.assertEqual(list(folder.iterdir()), [])
        longer = dict(self.file("long.glb", b"abcdef"), size=3)
        with self.assertRaises(fetch.FetchError):
            fetch.fetch_files(self.root, self.item([longer]), [longer])

    def test_md5_when_there_is_no_sha256(self):
        data = b"variant"
        entry = dict(self.file("v.hdr", data), md5=hashlib.md5(data).hexdigest())
        entry.pop("sha256")
        fetch.fetch_files(self.root, self.item([entry]), [entry])
        entry["md5"] = "0" * 32
        with self.assertRaises(fetch.FetchError):
            fetch.fetch_files(self.root, self.item([entry]), [entry])

    def test_a_redirect_off_the_hosts_is_refused(self):
        entry = self.file("r.glb", b"x")
        self.server.routes["/elsewhere/x.glb"] = b"x"
        self.server.routes["/files/r.glb"] = (302, {"Location": self.server.base.replace("127.0.0.1", "127.0.0.2")
                                                    + "/elsewhere/x.glb"}, b"")
        with self.assertRaises(fetch.FetchError):
            fetch.fetch_files(self.root, self.item([entry]), [entry])

    def test_names_never_leave_the_item_folder(self):
        for name in ("../escape.glb", "a/../../escape.glb", "/abs.glb"):
            entry = dict(self.file("ok.glb", b"x"), name=name)
            with self.subTest(name=name), self.assertRaises(fetch.FetchError):
                fetch.fetch_files(self.root, self.item([entry]), [entry])
        self.assertFalse((self.root / "files" / "escape.glb").exists())

    def test_the_cache_is_trimmed_least_recently_used_first(self):
        folder = self.root / "files"
        for index, name in enumerate(("old", "mid", "new")):
            entry = folder / name
            entry.mkdir(parents=True)
            (entry / "data").write_bytes(b"x" * 100)
            os.utime(entry, (1000 + index, 1000 + index))
        fetch.trim(folder, 250)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["mid", "new"])

    def test_a_stopped_download_ends_and_keeps_nothing(self):
        import threading as threads
        stop = threads.Event()
        stop.set()
        entry = self.file("big.glb", b"x" * 4096)
        with self.assertRaises(fetch.FetchError) as caught:
            fetch.fetch_files(self.root, self.item([entry]), [entry], cancel=stop)
        self.assertEqual(caught.exception.refusal_code, "CANCELLED")

    def test_trim_removes_links_as_links_and_never_their_targets(self):
        victim = self.root / "victim"
        victim.mkdir()
        (victim / "precious").write_bytes(b"x" * 500)
        folder = self.root / "files"
        folder.mkdir()
        (folder / "linked").symlink_to(victim, target_is_directory=True)
        os.utime(folder / "linked", (1, 1), follow_symlinks=False)
        (folder / "real").mkdir()
        (folder / "real" / "data").write_bytes(b"y" * 100)
        fetch.trim(folder, 0)
        self.assertTrue((victim / "precious").exists())
        self.assertFalse((folder / "linked").exists())

    def test_thumbnail_pixel_bombs_are_refused_from_the_header(self):
        self.server.routes["/bomb.png"] = team_library_fixtures.png_header(60000, 60000)
        self.server.routes["/big.png"] = team_library_fixtures.png_header(4096, 4096)
        self.assertIsNone(fetch.thumbnail(self.root, self.server.base + "/bomb.png"))
        self.assertIsNone(fetch.thumbnail(self.root, self.server.base + "/big.png"))

    def test_thumbnails_are_images_only(self):
        self.server.routes["/t.png"] = team_library_fixtures.png()
        self.server.routes["/t.html"] = b"<html>"
        self.assertIsNotNone(fetch.thumbnail(self.root, self.server.base + "/t.png"))
        self.assertIsNone(fetch.thumbnail(self.root, self.server.base + "/t.html"))


if __name__ == "__main__":
    unittest.main()
