# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""No network: provider error bodies become bounded, sanitized, user-visible details."""

import base64
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import studio_http
from angelo_agent.provider_errors import MAX_DETAIL, MAX_ERROR_BODY, credential_values, error_detail, sanitize
from angelo_agent.studio_generation import (GenerationError, StudioJobs, generate, image_outputs, request_bytes,
                                            service_json)
from angelo_agent.studio_state import StudioLibrary
from angelo_agent.generative import GenerationBinding
from angelo_agent.provider_registry import SPECS

KEY = "AIzaFixtureKeyNotSentAnywhere_0123456789abc"
GEMINI = SPECS["gemini_images_api"]["base_url"] + "/interactions"
GOOGLE_400 = {"error": {"code": 400, "status": "INVALID_ARGUMENT",
                        "message": "Invalid value at 'response_format.mime_type', \"image/png\""}}


class FakeResponse:
    def __init__(self, status, body):
        self.status, self.body, self.reads = status, body, []

    def read(self, amount=None):
        self.reads.append(amount)
        return self.body if amount is None else self.body[:amount]

    def getheader(self, name):
        return str(len(self.body)) if name == "Content-Length" else None


class FakeConnection:
    """Stands in for the pinned TLS connection; records the request, never opens a socket."""
    response = None
    sent = []

    def __init__(self, host, address, timeout):
        self.host = host

    def request(self, method, path, body=None, headers=None):
        FakeConnection.sent.append((method, self.host, path, headers))

    def getresponse(self):
        return FakeConnection.response

    def close(self):
        pass


def fake_network(status, body):
    FakeConnection.response, FakeConnection.sent = FakeResponse(status, body), []
    return (patch.object(studio_http, "_PinnedHTTPS", FakeConnection),
            patch.object(studio_http, "public_addresses", lambda host: ["8.8.8.8"]))


class SanitizeTests(unittest.TestCase):
    def test_google_error_keeps_status_and_message(self):
        detail = error_detail(json.dumps(GOOGLE_400).encode(), http_status=400)
        self.assertEqual(detail, "INVALID_ARGUMENT — Invalid value at 'response_format.mime_type', \"image/png\"")

    def test_exact_credentials_and_key_shapes_are_redacted(self):
        bearer = "Bearer opaque-session-credential-value"
        secrets = credential_values({"x-goog-api-key": KEY, "Authorization": bearer, "Content-Type": "application/json"})
        self.assertEqual(set(secrets), {KEY, bearer, "opaque-session-credential-value"})
        text = sanitize(f"bad key {KEY}; also {bearer}; x-goog-api-key: other-value api_key=abc123 "
                        "sk-proj-Abcdefghijklmnop tsk_live_0123456789 msy_ABCDEFGH12345 "
                        "AIzaSyD-0123456789abcdefghijklmnopqrstu eyJhbGciOiJIUzI1NiJ9abcdEFGH1234 "
                        "d3a1b2c4-5e6f-4a7b-8c9d-0e1f2a3b4c5d", secrets)
        for leaked in (KEY, "opaque-session-credential-value", "other-value", "abc123", "Abcdefghijklmnop",
                       "0123456789", "ABCDEFGH12345", "AIzaSy", "eyJhbGci", "d3a1b2c4"):
            self.assertNotIn(leaked, text)
        self.assertIn("[REDACTED]", text)

    def test_model_names_and_words_are_not_over_redacted(self):
        text = sanitize("models/gemini-3-pro-image-preview and gemini-3.1-flash-image are not found "
                        "for API version v1beta; RESOURCE_EXHAUSTED generationConfig.imageConfig")
        self.assertIn("gemini-3-pro-image-preview", text)
        self.assertIn("gemini-3.1-flash-image", text)
        self.assertIn("generationConfig.imageConfig", text)

    def test_url_login_query_and_fragment_are_removed(self):
        text = sanitize("See https://user:pw@storage.example/bucket/out.png?X-Goog-Signature=abc&key=1#frag "
                        "or https://ai.google.dev/gemini-api/docs/rate-limits")
        self.assertIn("https://storage.example/bucket/out.png?[REDACTED]", text)
        self.assertIn("https://ai.google.dev/gemini-api/docs/rate-limits", text)
        for leaked in ("user", "pw@", "Signature", "abc", "frag"):
            self.assertNotIn(leaked, text)

    def test_control_and_format_characters_cannot_spoof_the_ui(self):
        text = sanitize("line one\nline two\r\x1b[31mred\x00 ‮evil end\t")
        self.assertEqual(text, "line one line two [31mred evil end")

    def test_truncation_is_bounded_and_happens_after_redaction(self):
        message = "x" * (MAX_DETAIL - 10) + " " + KEY + " " + "y" * 5000
        text = sanitize(message, (KEY,))
        self.assertLessEqual(len(text), MAX_DETAIL)
        self.assertTrue(text.endswith("…"))
        self.assertNotIn("AIza", text)
        detail = error_detail(json.dumps({"error": {"status": "INVALID_ARGUMENT", "message": "z" * 10000}}).encode())
        self.assertEqual(len(detail), MAX_DETAIL)

    def test_other_provider_shapes_and_unusable_bodies(self):
        cases = (
            ({"error": {"message": "Invalid size", "type": "invalid_request_error", "code": None}}, "invalid_request_error — Invalid size"),
            ({"error": {"code": "InvalidParameter", "message": "The parameter size is invalid"}}, "InvalidParameter — The parameter size is invalid"),
            ({"code": 2002, "message": "Insufficient credit"}, "code 2002 — Insufficient credit"),
            ({"message": "Invalid model"}, "Invalid model"),
            ({"error": {"code": "https://errors.example/bad", "message": "Bad step"}}, "Bad step"),
            ([{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota"}}], "RESOURCE_EXHAUSTED — Quota"),
            ({"error": "Model is overloaded"}, "Model is overloaded"),
        )
        for body, expected in cases:
            self.assertEqual(error_detail(json.dumps(body).encode(), http_status=400), expected)
        for body in (b"<html>Bad gateway</html>", b"", b"\xff\xfe", b"[]", b'"text"', b'{"error": {"code": 400}}'):
            self.assertEqual(error_detail(body, http_status=400), "")
        self.assertEqual(error_detail(b'{"error": {"message": "cut' + b"x" * MAX_ERROR_BODY + b'"}}'), "")


class TransportTests(unittest.TestCase):
    def test_gemini_400_surfaces_sanitized_detail_and_never_the_key(self):
        body = dict(GOOGLE_400, error=dict(GOOGLE_400["error"], message=GOOGLE_400["error"]["message"] + " key " + KEY))
        connection, addresses = fake_network(400, json.dumps(body).encode())
        with connection, addresses, self.assertRaises(GenerationError) as caught:
            service_json(GEMINI, {"x-goog-api-key": KEY}, {"model": "gemini-3.1-flash-image", "input": "Cat"})
        message = str(caught.exception)
        self.assertTrue(message.startswith("Gemini Images rejected the request (HTTP 400): INVALID_ARGUMENT — "
                                           "Invalid value at 'response_format.mime_type'"), message)
        self.assertNotIn(KEY, message)
        self.assertNotIn('"model"', message)  # The request body is never echoed.
        self.assertEqual(FakeConnection.response.reads, [MAX_ERROR_BODY + 1])
        self.assertEqual(len(FakeConnection.sent), 1)

    def test_interactions_error_shape_reads_as_one_sentence(self):
        # The shape Google returned for the rejected image/png request.
        body = {"error": {"code": "invalid_request", "message": "The value 'image/png' is not supported for "
                          "'response_format.mime_type'. Supported values: 'image/jpeg'."}}
        connection, addresses = fake_network(400, json.dumps(body).encode())
        with connection, addresses, self.assertRaises(GenerationError) as caught:
            request_bytes(GEMINI, body=b"{}", headers={"x-goog-api-key": KEY}, service=True)
        self.assertEqual(str(caught.exception), "Gemini Images rejected the request (HTTP 400): invalid_request — The value "
                         "'image/png' is not supported for 'response_format.mime_type'. Supported values: 'image/jpeg'. Not retried")

    def test_known_statuses_keep_guidance_and_unparseable_bodies_stay_generic(self):
        connection, addresses = fake_network(403, json.dumps({"error": {"status": "PERMISSION_DENIED", "message": "No access"}}).encode())
        with connection, addresses, self.assertRaises(GenerationError) as caught:
            request_bytes(GEMINI, body=b"{}", headers={"x-goog-api-key": KEY}, service=True)
        self.assertEqual(str(caught.exception), "This key doesn't have access to the model. Gemini Images said: PERMISSION_DENIED — No access")
        connection, addresses = fake_network(503, b"<html>upstream</html>")
        with connection, addresses, self.assertRaises(GenerationError) as caught:
            request_bytes(GEMINI, body=b"{}", headers={"x-goog-api-key": KEY}, service=True)
        self.assertEqual(str(caught.exception), "Gemini Images returned a server error (HTTP 503); not retried")

    def test_asset_downloads_never_read_or_show_the_error_body(self):
        connection, addresses = fake_network(404, json.dumps({"message": "secret listing"}).encode())
        with connection, addresses, self.assertRaises(GenerationError) as caught:
            request_bytes("https://cdn.example/image.png")
        self.assertEqual(str(caught.exception), "The download host rejected the request (HTTP 404); not retried")
        self.assertEqual(FakeConnection.response.reads, [])

    def test_success_status_with_error_payload_is_sanitized(self):
        def transport(url, **kwargs):
            return json.dumps({"code": 2010, "message": "Prompt rejected for key " + KEY}).encode()
        with self.assertRaises(GenerationError) as caught:
            service_json(SPECS["tripo_api"]["base_url"] + "/tasks", {"Authorization": "Bearer " + KEY}, {}, transport=transport)
        self.assertEqual(str(caught.exception), "Tripo 3D rejected the request: code 2010 — Prompt rejected for key [REDACTED]. Not retried")

    def test_gemini_failed_interaction_without_image_reports_its_error(self):
        response = {"status": "failed", "steps": [], "errors": [{"code": "https://errors.example/x", "message": "Blocked\nby policy"}]}
        with self.assertRaises(GenerationError) as caught:
            image_outputs("gemini_images_api", response)
        self.assertTrue(str(caught.exception).endswith(". Gemini reported: Blocked by policy"))

    def test_failed_studio_asset_records_the_sanitized_provider_message(self):
        body = dict(GOOGLE_400, error=dict(GOOGLE_400["error"], message="Bad request for " + KEY + "\nretry later"))
        connection, addresses = fake_network(400, json.dumps(body).encode())
        binding = GenerationBinding("fixture", "gemini_images_api", ("gemini-3.1-flash-image",), True, KEY)
        with connection, addresses, tempfile.TemporaryDirectory() as root:
            store = StudioLibrary(root)
            asset = store.add("IMAGE", "Cat", "Nano Banana 2", model_id="gemini-3.1-flash-image")
            jobs = StudioJobs(store, request_bytes)
            jobs.start(asset, binding, {"aspect": "1:1", "resolution": "1K"}, [])
            deadline = time.monotonic() + 3
            while jobs.active and time.monotonic() < deadline:
                jobs.pump()
                time.sleep(.01)
            jobs.close()
            failed = store.assets[asset["id"]]
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error"], "Gemini Images rejected the request (HTTP 400): INVALID_ARGUMENT — "
                                              "Bad request for [REDACTED] retry later. Not retried")
            saved = (store.directory / "outputs" / asset["id"] / "job-state.json").read_text()
            self.assertNotIn(KEY, saved + json.dumps(store.assets))
        self.assertEqual(len(FakeConnection.sent), 1)  # One POST; never retried.

    def test_gemini_requests_documented_jpeg_and_saves_it_as_jpg(self):
        # Minimal JPEG header: SOI, then a baseline SOF0 frame declaring 2x3 pixels.
        jpeg = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x03\x00\x02\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01\xff\xd9"
        requests = []
        def transport(url, **kwargs):
            requests.append(json.loads(kwargs["body"]))
            content = [{"type": "image", "mime_type": "image/jpeg", "data": base64.b64encode(jpeg).decode()}]
            return json.dumps({"status": "completed", "steps": [{"type": "model_output", "content": content}]}).encode()
        binding = GenerationBinding("fixture", "gemini_images_api", ("gemini-3.1-flash-image",), True, KEY)
        with tempfile.TemporaryDirectory() as root:
            result = generate(binding, "gemini-3.1-flash-image", "Cat", {"aspect": "1:1", "resolution": "1K"}, [],
                              Path(root), lambda _: None, threading.Event(), transport)
            self.assertEqual(result["files"], ["image-1.jpg"])
            self.assertEqual((Path(root) / "image-1.jpg").read_bytes(), jpeg)
        self.assertEqual(requests[0]["response_format"], {"type": "image", "mime_type": "image/jpeg",
                                                          "aspect_ratio": "1:1", "image_size": "1K"})
        self.assertIs(requests[0]["store"], False)

    def test_generate_raises_the_detail_to_callers(self):
        connection, addresses = fake_network(400, json.dumps(GOOGLE_400).encode())
        binding = GenerationBinding("fixture", "gemini_images_api", ("gemini-3.1-flash-image",), True, KEY)
        with connection, addresses, tempfile.TemporaryDirectory() as root, self.assertRaises(GenerationError) as caught:
            generate(binding, "gemini-3.1-flash-image", "Cat", {}, [], Path(root), lambda _: None, threading.Event())
        self.assertIn("INVALID_ARGUMENT", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
