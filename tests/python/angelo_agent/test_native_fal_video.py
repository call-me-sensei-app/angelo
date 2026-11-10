# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""fal video: the capability table against fal's own input schemas, the queue client's URL checks, prices.

``fixtures/fal_video_schemas.json`` holds the input schema and request path of every endpoint Angelo calls,
reduced from fal's queue OpenAPI documents (read 2026-10-30). Every body ``fal_models.request`` can build, for
every model, mode and offered value, must satisfy its endpoint's schema.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import fal_models, fal_queue, flow_prices  # noqa: E402
from angelo_agent.api_connections import check_connection, probe_request  # noqa: E402
from angelo_agent.generative import GenerationBinding  # noqa: E402
from angelo_agent.provider_errors import credential_values, error_detail  # noqa: E402
from angelo_agent.provider_registry import SPECS, TEAM_MODES, service_mode  # noqa: E402
from angelo_agent.studio_http import GenerationError, SERVICE_HOSTS  # noqa: E402

SCHEMAS = json.loads((Path(__file__).parent / "fixtures/fal_video_schemas.json").read_text())["endpoints"]
URL = "https://v3b.fal.media/files/frame.png"
KEY = "fal-fixture-key:0123456789abcdef"
REQUEST = "019a1b2c-3d4e-7f80-9a1b-2c3d4e5f6a7b"


def binding(team=False):
    models = tuple(fal_models.MODELS)
    if team:
        tokens = type("Tokens", (), {"access_token": lambda self, stale="": "team-token-1",
                                     "redact": lambda self, text: text.replace("team-token-1", "[REDACTED]")})()
        return GenerationBinding("c", "fal_team", models, True, "", "https://gateway.example/v1/fal", tokens, "scope")
    return GenerationBinding("c", "fal_api", models, True, KEY)


def satisfies(value, schema):
    if "anyOf" in schema:
        return any(satisfies(value, option) for option in schema["anyOf"])
    kind = schema.get("type")
    if kind == "string" and not isinstance(value, str):
        return False
    if kind == "boolean" and type(value) is not bool:
        return False
    if kind == "integer" and type(value) is not int:
        return False
    if kind == "number" and (type(value) not in (int, float)):
        return False
    if kind == "array":
        return isinstance(value, list) and len(value) <= schema.get("maxItems", 1000) and all(
            satisfies(item, schema.get("items", {})) for item in value)
    if "enum" in schema and value not in schema["enum"]:
        return False
    if "minimum" in schema and value < schema["minimum"]:
        return False
    return not ("maximum" in schema and value > schema["maximum"])


def bodies(model):
    """Every (mode, endpoint, body) the table builds for ``model``: each offered value of each setting once."""
    for mode in fal_models.MODES:
        offer = fal_models.capabilities(model, mode)
        if offer is None:
            continue
        inputs = {fal_models.TEXT: [{}], fal_models.REFERENCE: [{"references": [URL] * offer["references"]}],
                  fal_models.IMAGE: [{"first": URL}] + ([{"first": URL, "last": URL}] if offer["last_frame"] else [])}[mode]
        variants = [{"duration": d} for d in offer["durations"]] + [{"resolution": r} for r in offer["resolutions"]] \
            + [{"aspect": a} for a in offer["aspects"]] + [{"audio": True}, {"audio": False}]
        for given in inputs:
            for options in variants:
                endpoint, body = fal_models.request(model, "A fox runs through snow", options, **given)
                yield mode, endpoint, body


class CapabilityTableTests(unittest.TestCase):
    def test_every_body_satisfies_its_endpoints_schema(self):
        for model in fal_models.MODELS:
            for mode, endpoint, body in bodies(model):
                with self.subTest(model=model, mode=mode, endpoint=endpoint):
                    self.assertIn(endpoint, SCHEMAS, "an endpoint fal documents")
                    schema = SCHEMAS[endpoint]
                    self.assertFalse(set(schema["required"]) - set(body), "every required field is sent")
                    for key, value in body.items():
                        self.assertIn(key, schema["properties"], key)
                        self.assertTrue(satisfies(value, schema["properties"][key]), (key, value))

    def test_every_endpoint_is_reached_and_named_by_its_app(self):
        reached = {endpoint for model in fal_models.MODELS for _mode, endpoint, _body in bodies(model)}
        self.assertEqual(reached, fal_models.ENDPOINTS, "no unreachable endpoint in the table")
        for endpoint in fal_models.ENDPOINTS:
            with self.subTest(endpoint=endpoint):
                app = fal_models.app_of(endpoint)
                self.assertTrue(endpoint == app or endpoint.startswith(app + "/"))
                self.assertEqual(len(app.split("/")), 2, "owner/app")
                self.assertTrue(SCHEMAS[endpoint]["requests"].startswith(app))

    def test_duration_types_follow_each_schema(self):
        expected = {"seedance-2.0": "5", "veo-3.1": "6s", "h3-max": 5.0, "wan-3.0": 5, "kling-o3-pro": "5"}
        for model, value in expected.items():
            with self.subTest(model=model):
                options = dict(fal_models.defaults(model), duration=6 if model == "veo-3.1" else 5)
                self.assertEqual(fal_models.request(model, "x", options)[1]["duration"], value)

    def test_modes_and_frames(self):
        self.assertEqual(fal_models.mode_for(), fal_models.TEXT)
        self.assertEqual(fal_models.mode_for(first=True, last=True), fal_models.IMAGE)
        self.assertEqual(fal_models.mode_for(references=2), fal_models.REFERENCE)
        with self.assertRaises(ValueError):
            fal_models.mode_for(last=True)
        with self.assertRaises(ValueError):
            fal_models.mode_for(first=True, references=1)
        endpoint, body = fal_models.request("veo-3.1", "x", {"duration": 8}, first=URL, last=URL)
        self.assertEqual(endpoint, "fal-ai/veo3.1/first-last-frame-to-video")
        self.assertEqual((body["first_frame_url"], body["last_frame_url"]), (URL, URL))
        self.assertEqual(fal_models.request("kling-3.0-pro", "", {}, first=URL)[1]["start_image_url"], URL)
        self.assertNotIn("aspect_ratio", fal_models.request("seedance-2.0", "x", {"aspect": "1:1"}, first=URL)[1],
                         "an image mode follows the frame's shape")
        with self.assertRaises(ValueError):
            fal_models.request("kling-3.0-pro", "x", {}, references=[URL])

    def test_unsupported_values_are_refused_before_anything_is_sent(self):
        for model, options in (("veo-3.1", {"duration": 5}), ("seedance-2.0-fast", {"resolution": "1080p"}),
                               ("veo-3.1", {"aspect": "1:1"}), ("wan-3.0", {"duration": "5"}), ("h3-max", {"audio": "on"})):
            with self.subTest(model=model, options=options), self.assertRaises(ValueError):
                fal_models.request(model, "x", options)
        with self.assertRaises(ValueError):
            fal_models.request("seedance-2.0", "", {})   # Its text mode needs a prompt.
        with self.assertRaises(ValueError):
            fal_models.request("nope", "x", {})

    def test_registry_offers_the_table_and_a_team_route(self):
        spec = SPECS["fal_api"]
        self.assertEqual((spec["group"], spec["kind"], spec["method"]), ("GENAI", "VIDEO", "API_KEY"))
        videos = [m for m in spec["models"] if "kind" not in m]
        self.assertEqual(sorted(m["id"] for m in videos), sorted(fal_models.MODELS))
        self.assertEqual(videos[0]["id"], fal_models.DEFAULT_MODEL, "Studio selects the first ready model")
        self.assertEqual([m["id"] for m in spec["models"] if m["default"]], [fal_models.DEFAULT_MODEL])
        self.assertIn("fal_team", TEAM_MODES)
        self.assertEqual((service_mode("fal_team"), SPECS["fal_team"]["gateway_path"]), ("fal_api", "/v1/fal"))
        self.assertTrue({"queue.fal.run", "rest.fal.ai"} <= SERVICE_HOSTS)
        self.assertTrue(spec["key_url"].startswith("https://fal.ai/"))


class KeyTests(unittest.TestCase):
    def test_probe_sends_key_scheme_to_fal_api_and_never_the_queue(self):
        request = probe_request("fal_api", KEY)
        self.assertEqual(request.full_url, "https://api.fal.ai/v1/models?limit=1")
        self.assertEqual(request.get_header("Authorization"), "Key " + KEY)
        result = check_connection("fal_api", KEY, transport=lambda r: {"models": [], "has_more": True})
        self.assertEqual((result.state, result.verified), ("verified", True))
        self.assertEqual({m["access"] for m in result.models}, {"not_checked"})

    def test_requests_carry_key_and_errors_redact_it(self):
        self.assertEqual(binding().request_headers(), {"Authorization": "Key " + KEY})
        self.assertIn(KEY, credential_values({"Authorization": "Key " + KEY}), "the bare key, not only the header")
        detail = error_detail(json.dumps({"detail": "bad key " + KEY}).encode(), credential_values({"Authorization": "Key " + KEY}))
        self.assertNotIn(KEY, detail)

    def test_fastapi_validation_details_are_read(self):
        body = json.dumps({"detail": [{"loc": ["body", "duration"], "msg": "Input should be '4s', '6s' or '8s'"}]})
        self.assertEqual(error_detail(body.encode()), "duration: Input should be '4s', '6s' or '8s'")


def answer(prefix="bytedance/seedance-2.0", identity=REQUEST, **changes):
    base = f"https://queue.fal.run/{prefix}/requests/{identity}"
    return dict({"request_id": identity, "status": "IN_QUEUE", "status_url": base + "/status", "response_url": base,
                 "cancel_url": base + "/cancel"}, **changes)


class HandleTests(unittest.TestCase):
    endpoint = "bytedance/seedance-2.0/fast/text-to-video"

    def test_app_and_endpoint_forms_are_this_requests_own(self):
        for prefix in ("bytedance/seedance-2.0", self.endpoint):
            with self.subTest(prefix=prefix):
                handle = fal_queue.checked_handle(self.endpoint, answer(prefix))
                self.assertEqual(handle.status_url, f"https://queue.fal.run/{prefix}/requests/{REQUEST}/status")
                self.assertEqual(fal_queue.handle_from(handle.saved()), handle)

    def test_any_other_link_is_refused(self):
        good = answer()
        bad = [{"status_url": good["status_url"].replace("queue.fal.run", "queue.fal.run.evil.example")},
               {"status_url": good["status_url"].replace("https:", "http:")},
               {"status_url": good["status_url"].replace("queue.fal.run", "queue.fal.run:8443")},
               {"status_url": good["status_url"] + "?fal_webhook=https://evil.example"},
               {"status_url": good["status_url"].replace(REQUEST, "019a1b2c-0000-7f80-9a1b-2c3d4e5f6a7b")},
               {"response_url": "https://queue.fal.run/minimax/h3-max/requests/" + REQUEST},
               {"cancel_url": "https://user:pw@queue.fal.run/bytedance/seedance-2.0/requests/%s/cancel" % REQUEST},
               {"request_id": "../../x"}, {"cancel_url": None}]
        for change in bad:
            with self.subTest(change=change), self.assertRaises(GenerationError):
                fal_queue.checked_handle(self.endpoint, dict(good, **change))
        tampered = fal_queue.checked_handle(self.endpoint, good).saved()
        tampered["status_url"] = "https://evil.example/status"
        with self.assertRaises(GenerationError):
            fal_queue.handle_from(tampered)

    def test_team_requests_go_to_the_gateway_route(self):
        handle = fal_queue.checked_handle(self.endpoint, answer())
        seen = []

        def transport(url, **kwargs):
            seen.append((url, kwargs.get("headers", {}).get("Authorization"), kwargs.get("method")))
            return json.dumps({"status": "IN_PROGRESS", "request_id": REQUEST}).encode()
        fal_queue.status(binding(team=True), handle, transport)
        fal_queue.cancel(binding(team=True), handle, transport)
        self.assertEqual(seen, [(f"https://gateway.example/v1/fal/bytedance/seedance-2.0/requests/{REQUEST}/status",
                                 "Bearer team-token-1", None),
                                (f"https://gateway.example/v1/fal/bytedance/seedance-2.0/requests/{REQUEST}/cancel",
                                 "Bearer team-token-1", "PUT")])

    def test_submit_admits_only_table_endpoints(self):
        with self.assertRaises(GenerationError):
            fal_queue.submit(binding(), "fal-ai/flux/dev", {}, transport=lambda *a, **k: b"{}")

    def test_output_video_url_must_be_public_https(self):
        self.assertEqual(fal_queue.output_video({"video": {"url": "https://v3b.fal.media/out.mp4"}}), "https://v3b.fal.media/out.mp4")
        for value in ({}, {"video": {}}, {"video": {"url": "http://v3b.fal.media/out.mp4"}}):
            with self.subTest(value=value), self.assertRaises(GenerationError):
                fal_queue.output_video(value)


class ReferenceVideoTests(unittest.TestCase):
    def test_models_that_take_a_reference_video_send_it_in_their_own_field(self):
        expected = {"seedance-2.0": "video_urls", "seedance-2.5": "video_urls", "h3-max": "reference_video_urls",
                    "wan-3.0": "reference_video_urls"}
        for model, field in expected.items():
            with self.subTest(model=model):
                endpoint, body = fal_models.request(model, "Follow this camera move", {}, videos=["https://v3b.fal.media/m.mp4"])
                self.assertEqual((endpoint.endswith("/reference-to-video"), body[field]), (True, ["https://v3b.fal.media/m.mp4"]))
                self.assertTrue(satisfies(body[field], SCHEMAS[endpoint]["properties"][field]))
        for model in ("veo-3.1", "kling-o3-pro"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                fal_models.request(model, "x", {}, videos=["https://v3b.fal.media/m.mp4"])

    def test_a_reference_videos_price_follows_each_models_rule(self):
        seconds = {"duration": 5, "resolution": "720p", "reference_video_seconds": 4.0}
        self.assertAlmostEqual(flow_prices.estimate("fal_api", "seedance-2.0", seconds), round(0.3034 * 0.6 * 9, 4))
        self.assertAlmostEqual(flow_prices.estimate("fal_api", "wan-3.0", seconds), 0.5)
        self.assertIsNone(flow_prices.estimate("fal_api", "h3-max", dict(seconds, resolution="768P")))
        self.assertIsNone(flow_prices.estimate("fal_api", "seedance-2.0", dict(seconds, reference_video_seconds=-1)),
                          "an unreadable length is unknown, never guessed")

    def test_an_mp4s_length_is_read_from_its_movie_header(self):
        from angelo_agent.fal_video import mp4_seconds
        box = b"\0\0\0\x6cmvhd" + b"\0" * 4 + b"\0" * 8 + (1000).to_bytes(4, "big") + (4500).to_bytes(4, "big") + b"\0" * 80
        self.assertEqual(mp4_seconds(b"\0\0\0\x20ftypisom" + b"\0" * 20 + box), 4.5)
        self.assertIsNone(mp4_seconds(b"no movie here"))


class PriceTests(unittest.TestCase):
    def test_per_second_rates_times_length(self):
        cases = [("seedance-2.0-fast", {"duration": 5, "resolution": "720p"}, 0, 1.2095),
                 ("veo-3.1", {"duration": 8, "resolution": "1080p", "audio": True}, 0, 3.2),
                 ("veo-3.1", {"duration": 8, "resolution": "1080p", "audio": False}, 0, 1.6),
                 ("kling-o3-pro", {"duration": 10, "audio": True}, 0, 1.4),
                 ("h3-max", {"duration": 6, "resolution": "768P"}, 0, 0.48),
                 ("h3-max-turbo", {"duration": 6, "resolution": "768P"}, 2, 0.30),
                 ("wan-3.0", {"duration": 4, "resolution": "480p"}, 0, 0.2)]
        for model, options, references, usd in cases:
            for mode in ("fal_api", "fal_team"):
                with self.subTest(model=model, mode=mode):
                    self.assertAlmostEqual(flow_prices.estimate(mode, model, options, references), usd, places=4)

    def test_seedance_480p_and_4k_follow_its_published_token_formula(self):
        tokens = lambda w, h: w * h * 24 / 1024   # noqa: E731 (fal's formula, per second)
        self.assertAlmostEqual(flow_prices.estimate("fal_api", "seedance-2.0", {"duration": 15, "resolution": "4k"}),
                               15 * round(tokens(3840, 2160) * 0.008 / 1000, 4), places=3)
        self.assertAlmostEqual(flow_prices.estimate("fal_api", "seedance-2.0-fast", {"duration": 4, "resolution": "480p"}),
                               4 * round(tokens(864, 480) * 0.0112 / 1000, 4), places=3)

    def test_veo_fast_reference_is_flat_per_second(self):
        for resolution in ("720p", "4k"):
            self.assertAlmostEqual(flow_prices.estimate("fal_api", "veo-3.1-fast", {"duration": 8, "resolution": resolution,
                                                                                   "audio": True}, 1), 1.2)

    def test_a_flow_step_after_a_model_change_keeps_only_values_the_model_takes(self):
        fitted = fal_models.fitted_options("seedance-2.0-fast", fal_models.TEXT,
                                           {"resolution": "4k", "duration": 5, "audio": True, "aspect": "1:1"})
        self.assertEqual(fitted, {"duration": 5, "audio": True, "aspect": "1:1"})
        self.assertEqual(fal_models.fitted_options("h3-max", fal_models.TEXT, {"audio": True, "duration": 40}), {})
        self.assertAlmostEqual(flow_prices.price({"model": "seedance-2.0-fast", "options": {"resolution": "4k"}}), 1.2095)

    def test_every_model_has_a_dated_sourced_row_and_defaults_price(self):
        for model in fal_models.MODELS:
            with self.subTest(model=model):
                entry = flow_prices.PRICES[("fal_api", model)]
                self.assertEqual(entry["as_of"], flow_prices.FAL_AS_OF)
                self.assertTrue(entry["source"].startswith("https://fal.ai/models/"))
                self.assertIsNotNone(entry["estimate"]({}, 0), "a step that keeps the model's defaults has a price")
                self.assertGreater(flow_prices.price({"model": model, "options": {}}), 0)


if __name__ == "__main__":
    unittest.main()
