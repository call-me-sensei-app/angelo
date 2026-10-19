# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team library client and storage transfers against the fake console (team_library_client.py,
team_library_transfer.py).

Contract 2026-10-08: the data envelope and cursors, visibility (personal items
only to their owner), presigned storage links that must never receive
credentials, one refresh on a 401, and the console's refusals (quota, seat or
role, CHARACTER_NOT_READY with per-entity fields) in the person's terms with the
request's token redacted. No real console or storage is used.
"""

import hashlib
import os
import tempfile
import threading
import unittest

import team_library_fixtures as f
from fake_team_console import FakeConsole
from angelo_agent.team_errors import TeamError
from angelo_agent.team_library_client import TeamLibraryClient, TeamLibraryError
from angelo_agent.team_library_transfer import Cancelled, TransferError, download, fetch, upload


class ClientCase(unittest.TestCase):
    def setUp(self):
        self.console = FakeConsole()
        self.addCleanup(self.console.close)
        self.library = self.console.library
        self.account = f.signed_in(self, self.console)
        self.client = TeamLibraryClient(self.account)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def storage_requests(self):
        return [r for r in self.console.requests if r[1].startswith("/api/dev-storage/")]


class LibraryTests(ClientCase):
    def test_search_pages_newest_first_and_hides_others_personal_items(self):
        for n in range(5):
            self.library.add_item("Ship %d" % n, f.glb(), "model/gltf-binary", tags=["ship"])
        self.library.add_item("My sketch", f.png(), "image/png", space="personal")
        self.library.add_item("Their sketch", f.png(), "image/png", space="personal", owner="user-2")
        first, cursor = self.client.search(query="ship", limit=3)
        self.assertEqual([i["title"] for i in first], ["Ship 0", "Ship 1", "Ship 2"])
        rest, end = self.client.search(query="ship", limit=3, cursor=cursor)
        self.assertEqual([i["title"] for i in rest], ["Ship 3", "Ship 4"])
        self.assertIsNone(end)
        personal, _ = self.client.search(space="personal")
        self.assertEqual([i["title"] for i in personal], ["My sketch"])
        self.assertEqual(self.client.search(kind="image")[0], [])

    def test_items_and_downloads_use_short_lived_links_without_credentials(self):
        data = f.glb(images=[f.png()])
        identity = self.library.add_item("Hero ship", data, "model/gltf-binary")
        self.assertEqual(self.client.item(identity)["title"], "Hero ship")
        link = self.client.download_link(identity)
        target = os.path.join(self.folder.name, "download")
        self.assertEqual(download(link, target, limit=1 << 20, expected=len(data)), len(data))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), data)
        (request,) = self.storage_requests()
        self.assertNotIn("authorization", {name.lower() for name in request[3]})
        self.assertTrue(all(r[3].get("Authorization", "").startswith("Bearer ")
                            for r in self.console.requests if "/library" in r[1]))

    def test_downloads_are_capped_and_size_checked(self):
        identity = self.library.add_item("Big", bytes(4096), "model/gltf-binary")
        link = self.client.download_link(identity)
        with self.assertRaisesRegex(TransferError, "larger than Angelo imports"):
            download(link, os.path.join(self.folder.name, "a"), limit=1024)
        with self.assertRaisesRegex(TransferError, "doesn't match the library"):
            download(link, os.path.join(self.folder.name, "b"), limit=1 << 20, expected=4000)
        stop = threading.Event()
        stop.set()
        with self.assertRaises(Cancelled):
            download(link, os.path.join(self.folder.name, "c"), limit=1 << 20, cancel=stop)

    def test_storage_links_must_be_https_or_loopback_and_carry_no_credentials(self):
        target = os.path.join(self.folder.name, "x")
        for url in ("http://example.test/a", "ftp://example.test/a", "https://user:pw@example.test/a", "file:///etc/hosts"):
            with self.subTest(url=url), self.assertRaises(TransferError):
                download(url, target, limit=10)
        with self.assertRaisesRegex(TransferError, "asked for credentials"):
            upload("https://example.test/put", {"Authorization": "Bearer x"}, __file__, 10)
        with self.assertRaisesRegex(TransferError, "isn't https"):
            fetch("http://10.0.0.1/thumb", limit=10)

    def test_upload_reserves_puts_and_completes_with_provenance(self):
        path = os.path.join(self.folder.name, "image.png")
        data = f.png(8, 8)
        with open(path, "wb") as handle:
            handle.write(data)
        reserved = self.client.create_upload(space="team", file_name="Ship-sketch.png", mime_type="image/png",
                                             size=len(data), sha256=hashlib.sha256(data).hexdigest())
        upload(reserved["uploadUrl"], reserved["headers"], path, len(data))
        item = self.client.complete_upload(reserved["itemId"], {
            "title": "Ship sketch", "tags": ["ship"], "provenance": {"source": "upload", "scene": "Scene"}})
        self.assertEqual((item["title"], item["space"], item["provenance"]["scene"]), ("Ship sketch", "team", "Scene"))
        self.assertEqual(self.client.complete_upload(reserved["itemId"], {"title": "Again"})["title"], "Ship sketch",
                         "complete is idempotent")
        put = [r for r in self.storage_requests() if r[0] == "PUT"]
        self.assertEqual(len(put), 1)
        self.assertNotIn("authorization", {name.lower() for name in put[0][3]})

    def test_a_mismatched_upload_is_refused_by_the_console(self):
        path = os.path.join(self.folder.name, "image.png")
        with open(path, "wb") as handle:
            handle.write(f.png())
        reserved = self.client.create_upload(space="personal", file_name="a.png", mime_type="image/png", size=999,
                                             sha256=None)
        with self.assertRaises(TransferError):
            upload(reserved["uploadUrl"], reserved["headers"], path, 999)  # The file is shorter than declared.
        with self.assertRaisesRegex(TeamLibraryError, "hasn't arrived"):
            self.client.complete_upload(reserved["itemId"], {})


class RefusalTests(ClientCase):
    def test_not_visible_items_say_so(self):
        identity = self.library.add_item("Their sketch", f.png(), "image/png", space="personal", owner="user-2")
        with self.assertRaises(TeamLibraryError) as caught:
            self.client.item(identity)
        self.assertEqual(caught.exception.code, "NOT_FOUND")
        self.assertIn("isn't visible to you", caught.exception.message)

    def test_role_refusals_keep_the_console_reason_and_say_who_can_fix_it(self):
        self.library.fail["/library/stats"] = (403, "FORBIDDEN", "Only an admin can see this.")
        with self.assertRaises(TeamLibraryError) as caught:
            self.client.stats()
        self.assertEqual(caught.exception.message, "Only an admin can see this. Ask a team admin if you need access.")

    def test_quota_and_rate_limits(self):
        self.library.quota = 10
        with self.assertRaisesRegex(TeamLibraryError, "storage is full. Ask a team admin"):
            self.client.create_upload(space="team", file_name="a.png", mime_type="image/png", size=100, sha256=None)
        self.library.fail["/library"] = (429, "RATE_LIMITED", "slow down")
        with self.assertRaises(TeamLibraryError) as caught:
            self.client.search()
        self.assertEqual(caught.exception.code, "RATE_LIMITED")

    def test_an_expired_access_token_is_refreshed_once(self):
        self.library.add_item("Ship", f.glb(), "model/gltf-binary")
        with self.console.lock:
            self.console.access.clear()  # Every issued access token now answers 401.
        refreshes = len(self.console.refresh_requests())
        self.assertEqual(len(self.client.search()[0]), 1)
        self.assertEqual(len(self.console.refresh_requests()), refreshes + 1)

    def test_the_request_token_never_appears_in_a_refusal(self):
        token = self.account.access_token()
        self.library.fail["/library"] = (400, "VALIDATION_FAILED", "bad header Bearer %s here" % token)
        with self.assertRaises(TeamLibraryError) as caught:
            self.client.search()
        self.assertNotIn(token, caught.exception.message)

    def test_signed_out_accounts_refuse_before_any_request(self):
        self.account.sign_out()
        with self.assertRaises(TeamError):
            self.client.search()


class IpTests(ClientCase):
    def setUp(self):
        super().setUp()
        lib = self.library
        self.ip = lib.add_ip("Sky Kingdom")
        self.aiko = lib.add_entity(self.ip, "character", "Aiko", version=3)
        self.bram = lib.add_entity(self.ip, "character", "Bram")
        self.castle = lib.add_entity(self.ip, "location", "Castle")
        sheet = lib.add_item("Aiko sheet", f.png(), "image/png")
        close = lib.add_item("Aiko face", f.png(), "image/png")
        lib.add_reference(self.aiko, sheet, "character_sheet")
        lib.add_reference(self.aiko, close, "close_up")
        lib.add_reference(self.bram, lib.add_item("Bram front", f.png(), "image/png"), "turnaround", view="front")

    def test_list_detail_entity_and_readiness(self):
        (ip,) = self.client.ips()
        self.assertEqual(ip["name"], "Sky Kingdom")
        detail = self.client.ip(self.ip)
        self.assertEqual({e["name"] for e in detail["entities"]}, {"Aiko", "Bram", "Castle"})
        aiko = self.client.entity(self.ip, self.aiko)
        self.assertTrue(aiko["readiness"]["ready"])
        bram = self.client.entity(self.ip, self.bram)
        self.assertEqual(bram["readiness"]["missing"], ["character_sheet", "close_up"])
        self.assertIsNone(self.client.entity(self.ip, self.castle)["readiness"])

    def test_resolve_puts_identity_first_and_refuses_unready_characters(self):
        value = self.client.resolve([{"entity_id": self.aiko, "version": 2}], limit=4)
        self.assertEqual([r["kind"] for r in value["references"]], ["character_sheet", "close_up"])
        self.assertEqual(value["entities"][0]["version"], 2)
        with self.assertRaises(TeamLibraryError) as caught:
            self.client.resolve([{"entity_id": self.aiko}, {"entity_id": self.bram}])
        self.assertEqual(caught.exception.code, "CHARACTER_NOT_READY")
        self.assertIn("Bram", caught.exception.message)
        self.assertEqual(caught.exception.fields["entities.1"][1], "Add at least one close-up of the face.")


class ConsoleGenerationTests(ClientCase):
    """The current console, and fallbacks for the earlier one."""

    def write(self, data, name="file"):
        path = os.path.join(self.folder.name, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def reserve(self, data, mime="image/png", name="a.png", sha256=True):
        digest = hashlib.sha256(data).hexdigest() if sha256 else None
        return self.client.create_upload(space="team", file_name=name, mime_type=mime, size=len(data), sha256=digest)

    def test_storage_enforces_the_declared_checksum(self):
        data = f.png(8, 8)
        reserved = self.reserve(data)
        self.assertIn("x-amz-checksum-sha256", reserved["headers"])
        with self.assertRaisesRegex(TransferError, "HTTP 403"):  # The signed header was left out.
            upload(reserved["uploadUrl"], {"content-type": "image/png"}, self.write(data), len(data))
        other = f.png(8, 8)[:-1] + b"\x00"
        with self.assertRaisesRegex(TransferError, "HTTP 400"):  # Other bytes: BadDigest.
            upload(reserved["uploadUrl"], reserved["headers"], self.write(other, "other"), len(other))
        upload(reserved["uploadUrl"], reserved["headers"], self.write(data), len(data))
        item = self.client.complete_upload(reserved["itemId"], {"title": "Checked"})
        self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())

    def test_complete_refuses_content_that_isnt_what_was_declared(self):
        data = f.jpeg_header(4, 4)
        reserved = self.reserve(data, mime="image/png")
        upload(reserved["uploadUrl"], reserved["headers"], self.write(data), len(data))
        with self.assertRaises(TeamLibraryError) as caught:
            self.client.complete_upload(reserved["itemId"], {})
        self.assertEqual(caught.exception.code, "UPLOAD_INVALID")
        self.assertTrue(caught.exception.message.startswith("The team library refused the file: This isn't a PNG file"))
        self.assertTrue(caught.exception.message.endswith("Nothing was added to the library."))
        self.assertNotIn(reserved["itemId"], self.library.items, "the console deleted the reservation")
        fbx = b"glTF" + bytes(60)
        reserved = self.reserve(fbx, mime="application/octet-stream", name="hero.fbx")
        upload(reserved["uploadUrl"], reserved["headers"], self.write(fbx, "hero.fbx"), len(fbx))
        with self.assertRaisesRegex(TeamLibraryError, "This isn't a FBX file"):
            self.client.complete_upload(reserved["itemId"], {})

    def test_quota_counts_recent_reservations_and_the_sweep_frees_old_ones(self):
        self.library.quota = 1000
        self.reserve(bytes(600), mime="application/octet-stream", name="a.obj", sha256=False)
        with self.assertRaisesRegex(TeamLibraryError, "storage is full"):
            self.reserve(bytes(600), mime="application/octet-stream", name="b.obj", sha256=False)
        self.library.age_reservations(16 * 60)  # Older than the upload link: no longer counted.
        self.reserve(bytes(600), mime="application/octet-stream", name="c.obj", sha256=False)
        self.library.age_reservations(61 * 60)
        self.reserve(bytes(10), mime="application/octet-stream", name="d.obj", sha256=False)
        self.assertEqual(sum(1 for i in self.library.items.values() if i["_status"] == "pending"), 1, "swept")

    def test_downloads_verify_the_librarys_checksum(self):
        data = f.glb()
        good = self.library.add_item("Good", data, "model/gltf-binary", sha256=hashlib.sha256(data).hexdigest())
        bad = self.library.add_item("Bad", data, "model/gltf-binary", sha256="0" * 64)
        for identity, failing in ((good, False), (bad, True)):
            item = self.client.item(identity)
            target = os.path.join(self.folder.name, identity)
            call = lambda: download(self.client.download_link(identity), target, limit=1 << 20,  # noqa: E731
                                    expected=item["sizeBytes"], sha256=item["sha256"])
            if failing:
                with self.assertRaisesRegex(TransferError, "doesn't match the checksum"):
                    call()
            else:
                self.assertEqual(call(), len(data))

    def add_ips(self, count):
        for n in range(count):
            identity = self.library.add_ip("World %d" % n)
            self.library.ips[identity]["updatedAt"] = "2026-10-08T12:%02d:00.000Z" % n

    def test_ip_list_pages_on_both_consoles(self):
        self.add_ips(5)
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.library.legacy = legacy
                names, cursor = [], None
                while True:
                    page, cursor = self.client.ips_page(limit=2, cursor=cursor)
                    names += [ip["name"] for ip in page]
                    if cursor is None:
                        break
                    self.assertEqual(cursor.startswith("local:"), legacy)
                self.assertEqual(names, ["World 4", "World 3", "World 2", "World 1", "World 0"])

    def test_ip_detail_pages_filters_and_leaves_media_out_on_both_consoles(self):
        ip = self.library.add_ip("Sky Kingdom")
        for name, kind in (("Zed", "character"), ("Aiko", "character"), ("Castle", "location"), ("Bram", "character")):
            entity = self.library.add_entity(ip, kind, name)
            self.library.add_reference(entity, self.library.add_item(name, f.png(), "image/png"), "close_up")
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.library.legacy = legacy
                names, cursor = [], None
                while True:
                    detail, cursor = self.client.ip_page(ip, kind="character", limit=2, cursor=cursor)
                    self.assertNotIn("media", detail)
                    names += [e["name"] for e in detail["entities"]]
                    if cursor is None:
                        break
                self.assertEqual(names, ["Aiko", "Bram", "Zed"])

    def test_a_bad_cursor_on_a_paging_console_is_reported_not_hidden(self):
        self.add_ips(3)
        with self.assertRaises(TeamLibraryError) as caught:
            self.client.ips_page(limit=2, cursor="not-a-cursor")
        self.assertEqual(caught.exception.status, 400)
        with self.assertRaises(TeamError):
            self.client.ips_page(limit=2, cursor="local:x")


if __name__ == "__main__":
    unittest.main()
