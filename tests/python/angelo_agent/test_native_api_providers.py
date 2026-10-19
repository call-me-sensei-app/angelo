# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.api_connections import (ApiConnections, CheckResult, ConnectionCheckError,
                                           check_connection, probe_request, read_probe, _NoRedirect)
from angelo_agent.provider_registry import (CODING, GENAI, SPECS, HTTP_MODES, EXTERNAL_CODEX_MODES, TEAM,
                                            is_coding, provider_items, runtime_kind)
from angelo_agent.connections import ConnectionStore
from angelo_agent.providers import CodexProvider, codex_command, child_environment
from angelo_agent.catalogs import ModelCatalogs
from angelo_agent.controller import Controller
from angelo_agent.chat_popovers import model_rows, popup_layout
from angelo_agent.generative import bind_generation

KEY = "fixture-key-never-sent-to-a-real-provider"


def catalog(mode):
    return {"data": [{"id": m["id"]} for m in SPECS[mode]["models"]]}


class ApiProviderTests(unittest.TestCase):
    def test_all_provider_types_can_coexist_without_exclusive_selection(self):
        # Team connections (method TEAM) are derived from the signed-in account
        # and never stored (test_native_team_connections.py).
        personal = {mode: spec for mode, spec in SPECS.items() if spec["method"] != TEAM}
        with tempfile.TemporaryDirectory() as directory:
            store = ConnectionStore(directory)
            for mode, spec in personal.items():
                record = store.add(spec["provider"], spec["method"], group=spec["group"])
                self.assertEqual(record["mode"], mode)
                record["api_key"] = KEY
            store.save()
            saved = ConnectionStore(directory)
            self.assertEqual(len(saved.records), len(personal))
            self.assertEqual({r["group"] for r in saved.records.values()}, {CODING, GENAI})
            self.assertNotIn(KEY, saved.path.read_text())
            if os.name != "nt":  # POSIX mode bits; on Windows the per-user profile's access list applies
                self.assertEqual(os.stat(saved.path).st_mode & 0o777, 0o600)

    def test_genai_is_api_only_and_cannot_become_bootstrap_coding_connection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConnectionStore(directory)
            image = store.add("OPENAI", "API_KEY", group=GENAI)
            self.assertIsNone(store.active)
            code = store.add("OPENAI", "API_KEY")
            self.assertEqual(store.active, code["id"])
            self.assertNotEqual(code["mode"], image["mode"])
            with self.assertRaises(ValueError):
                store.activate(image["id"])
            for provider, _, _ in provider_items(GENAI):
                with self.assertRaises(ValueError):
                    store.add(provider, "SUBSCRIPTION", group=GENAI)
            with self.assertRaises(ValueError):
                runtime_kind(image["mode"])

    def test_legacy_metadata_migrates_to_coding_without_losing_native_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConnectionStore(directory)
            a = store.add("ANTHROPIC", "SUBSCRIPTION", reuse_native=True)
            raw = json.loads(store.path.read_text())
            raw["version"] = 1
            raw["connections"][0].pop("group")
            store.path.write_text(json.dumps(raw))
            migrated = ConnectionStore(directory).records[a["id"]]
            self.assertEqual(migrated["group"], CODING)
            self.assertTrue(migrated["reuse_native"])

    def test_every_probe_is_get_with_header_auth_not_query_or_billable_endpoint(self):
        for mode in HTTP_MODES:
            request = probe_request(mode, KEY)
            if request is None:
                self.assertEqual(mode, "seedream_api")
                continue
            self.assertEqual(request.get_method(), "GET")
            self.assertTrue(request.full_url.startswith("https://"))
            self.assertNotIn(KEY, request.full_url)
            self.assertNotIn("generations", request.full_url)
            self.assertIsNone(request.data)
            self.assertIn(KEY, str(request.headers))
            if mode == "gemini_images_api":
                self.assertNotIn("Authorization", request.headers)
                self.assertEqual(request.get_header("X-goog-api-key"), KEY)

    def test_coding_checks_only_return_requested_models_and_never_claim_fast(self):
        for mode in EXTERNAL_CODEX_MODES:
            transport = Mock(return_value=catalog(mode))
            result = check_connection(mode, KEY, transport)
            self.assertTrue(result.verified)
            self.assertEqual(result.state, "verified")
            self.assertTrue(result.models)
            self.assertTrue(all(m["fast_tier"] is None for m in result.models))
            self.assertEqual(transport.call_count, 1)
        self.assertEqual(SPECS["minimax_api"]["models"][0]["efforts"], ["none", "high"])

    def test_catalog_without_requested_model_is_not_a_usable_connection(self):
        result = check_connection("kimi_api", KEY, lambda request: {"data": [{"id": "other-embedding"}]})
        self.assertTrue(result.verified)
        self.assertEqual(result.state, "no_models")
        self.assertEqual(result.models, [])

    def test_image_model_names_are_preserved_without_silent_substitution(self):
        result = check_connection("openai_images_api", KEY, lambda request: catalog("openai_images_api"))
        self.assertEqual([m["id"] for m in result.models], ["gpt-image-2.5-flare", "gpt-image-2.5-sunburst"])
        response = {"models": [{"name": "models/gemini-3.1-flash-image"}, {"name": "models/gemini-3-pro-image"},
                               {"name": "models/unrelated-chat-model"}]}
        result = check_connection("gemini_images_api", KEY, lambda request: response)
        self.assertEqual([m["name"] for m in result.models], ["Nano Banana 2", "Nano Banana Pro"])
        # Nano Banana 2.1 is the default only when this key's catalog lists it.
        self.assertEqual([m["id"] for m in result.models if m["default"]], ["gemini-3.1-flash-image"])
        response["models"].append({"name": "models/gemini-nano-banana-2.1"})
        result = check_connection("gemini_images_api", KEY, lambda request: response)
        self.assertEqual([m["name"] for m in result.models], ["Nano Banana 2.1", "Nano Banana 2", "Nano Banana Pro"])
        self.assertEqual([m["id"] for m in result.models if m["default"]], ["gemini-nano-banana-2.1"])

    def test_credit_checks_are_non_generation_and_keep_decimal_balances(self):
        for mode, response in (("tripo_api", {"code": 0, "data": {"balance": 42.25}}), ("meshy_api", {"balance": 0})):
            result = check_connection(mode, KEY, lambda request: response)
            self.assertTrue(result.verified)
            self.assertEqual(result.balance, 42.25 if mode == "tripo_api" else 0)
            self.assertTrue(all(m["access"] == "not_checked" for m in result.models))
        for value in (None, "50", True, -1, float("inf")):
            with self.assertRaises(ConnectionCheckError):
                check_connection("meshy_api", KEY, lambda request: {"balance": value})

    def test_seedream_saves_binding_but_does_not_fake_verification(self):
        transport = Mock()
        result = check_connection("seedream_api", KEY, transport)
        transport.assert_not_called()
        self.assertFalse(result.verified)
        self.assertEqual(result.state, "configured")
        self.assertEqual(result.models[0]["id"], "dola-seedream-5-0-pro-260628")

    def test_invalid_keys_and_provider_error_envelopes_fail_closed(self):
        for key in ("", "bad\nheader", "token with space", "x" * 4097):
            transport = Mock()
            with self.assertRaises(ValueError):
                check_connection("kimi_api", key, transport)
            transport.assert_not_called()
        for value in ({"code": 1002, "message": KEY}, {"error": {"message": KEY}}, {"data": "wrong"}):
            with self.assertRaises(ConnectionCheckError) as error:
                check_connection("kimi_api", KEY, lambda request: value)
            self.assertNotIn(KEY, str(error.exception))

    def test_redirects_do_not_forward_the_credential(self):
        request = probe_request("grok_api", KEY)
        with self.assertRaises(ConnectionCheckError):
            _NoRedirect().redirect_request(request, None, 302, "redirect", {}, "https://other.invalid/")

    def test_background_check_result_is_ignored_after_key_change_or_removal(self):
        started, release = threading.Event(), threading.Event()
        def transport(request):
            started.set()
            release.wait(2)
            return catalog("kimi_api")
        checker = ApiConnections(transport)
        checker.start({"id": "connection", "mode": "kimi_api"}, KEY)
        self.assertTrue(started.wait(1))
        checker.invalidate("connection")
        release.set()
        event = checker.events.get(timeout=2)
        checker.events.put(event)
        self.assertEqual(checker.pump(), [])
        self.assertNotIn("connection", checker.results)
        checker.close()

    def test_background_error_does_not_expose_headers_or_keys(self):
        checker = ApiConnections(lambda request: (_ for _ in ()).throw(RuntimeError(str(request.headers))))
        checker.start({"id": "connection", "mode": "kimi_api"}, KEY)
        event = checker.events.get(timeout=2)
        checker.events.put(event)
        checker.pump()
        self.assertNotIn(KEY, checker.results["connection"].message)
        checker.close()

    def test_custom_providers_have_isolated_native_profiles_and_explicit_env_keys(self):
        inherited = {"OPENAI_API_KEY": "other", "ANTHROPIC_API_KEY": "other", "KIMI_API_KEY": "other",
                     "MINIMAX_API_KEY": "other", "CODEX_HOME": "/unrelated", "OPENAI_BASE_URL": "https://other.invalid"}
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, inherited):
            for mode in EXTERNAL_CODEX_MODES:
                command, env = codex_command("/official/codex", mode, directory, True, api_key=KEY)
                config = dict(v.split("=", 1) for i, v in enumerate(command) if i > 0 and command[i-1] == "-c")
                spec = SPECS[mode]
                self.assertEqual(env[spec["env_key"]], KEY)
                self.assertNotIn(KEY, str(command))
                self.assertTrue(env["CODEX_HOME"].startswith(directory))
                self.assertEqual(json.loads(config["model_provider"]), mode)
                self.assertEqual(json.loads(config[f"model_providers.{mode}.base_url"]), spec["base_url"])
                self.assertEqual(config[f"model_providers.{mode}.requires_openai_auth"], "false")
                self.assertEqual(json.loads(config[f"model_providers.{mode}.wire_api"]), "responses")
                # A provider's own API is never sent a compressed body; only the team gateway decodes one.
                self.assertFalse(any(".capabilities." in key for key in config), mode)
                self.assertNotIn("OPENAI_API_KEY", env)
                self.assertNotIn("ANTHROPIC_API_KEY", env)
                self.assertNotIn("KIMI_API_KEY", env)

    def test_genai_cannot_enter_model_catalog_or_delegate_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            store, catalogs = ConnectionStore(directory), ModelCatalogs(directory)
            image = store.add("TRIPO", "API_KEY", group=GENAI)
            code = store.add("KIMI", "API_KEY")
            catalogs.entries = {image["id"]: SPECS[image["mode"]]["models"], code["id"]: SPECS[code["mode"]]["models"]}
            choices = catalogs.choices(list(store.records.values()))
            self.assertEqual({m["connection_id"] for m in choices}, {code["id"]})
            with patch("angelo_agent.catalogs.threading.Thread") as worker:
                catalogs.refresh(image, {}, KEY)
                worker.assert_not_called()
            catalogs.close()

    def test_generation_bindings_resolve_only_the_selected_service_model(self):
        transport = Mock(return_value=catalog("openai_images_api"))
        health = check_connection("openai_images_api", KEY, transport)
        binding = bind_generation({"id": "images", "mode": "openai_images_api"}, KEY, health)
        self.assertEqual(binding.endpoint("image", "gpt-image-2.5-flare"), "https://api.openai.com/v1/images/generations")
        self.assertEqual(binding.request_headers(), {"Authorization": "Bearer " + KEY})
        self.assertNotIn(KEY, repr(binding))
        self.assertEqual(transport.call_count, 1, "Creating a binding makes no network or generation request")
        for operation, model in (("text_to_3d", "gpt-image-2.5-flare"), ("image", "kimi-k3")):
            with self.assertRaises(ValueError):
                binding.endpoint(operation, model)
        with self.assertRaises(ValueError):
            bind_generation({"id": "coding", "mode": "kimi_api"}, KEY, health)
        with self.assertRaises(ValueError):
            bind_generation({"id": "images", "mode": "openai_images_api"}, KEY, CheckResult("failed", "Failed"))

    def test_all_coding_providers_switch_within_a_shared_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            controller, store = Controller(directory), ConnectionStore(directory)
            identity = controller.state["id"]
            controller.state["messages"] = [{"role": "user", "text": "Keep this conversation"}]
            for mode in EXTERNAL_CODEX_MODES:
                record = store.add(SPECS[mode]["provider"], "API_KEY")
                controller.select(mode, {"connection_id": record["id"]}, KEY, record["id"], SPECS[mode]["models"])
                self.assertEqual(controller.state["id"], identity)
                self.assertEqual(controller.state["messages"][0]["text"], "Keep this conversation")
            with self.assertRaises(ValueError):
                controller.select("tripo_api", {}, KEY)
            controller.close()


class ModelGroupingTests(unittest.TestCase):
    def models(self):
        return [dict(m, provider_id=s["provider"].lower(), provider_name=s["name"], provider=mode,
                     connection_id=mode, connection_name=s["name"], billing="API billing")
                for mode, s in SPECS.items() if mode in EXTERNAL_CODEX_MODES for m in s["models"]]

    def test_generic_groups_keep_exact_connection_and_model_routes(self):
        rows = model_rows(self.models(), "kimi-k3", "kimi_api")
        self.assertEqual(len([r for r in rows if r["kind"] == "provider"]), 3)
        self.assertEqual([r["value"] for r in rows if r["selected"]], [("kimi_api", "kimi-k3")])

    def test_duplicate_runtime_default_alias_is_hidden(self):
        models = self.models() + [dict(SPECS["kimi_api"]["models"][0], id="default", name="Default (recommended)",
            runtime_default_alias=True, connection_id="kimi_api", provider_id="kimi")]
        rows = model_rows(models, "default", "kimi_api")
        self.assertEqual([r["kind"] for r in rows if r["selected"]], ["default"])
        self.assertFalse(any(r["name"] == "Default (recommended)" for r in rows))

    def test_search_and_collapse_do_not_change_billing_routes(self):
        rows = model_rows(self.models(), "", "grok_api", query="MiniMax")
        self.assertEqual([r["value"] for r in rows if r["kind"] == "model"], [("minimax_api", "MiniMax-M3")])
        rows = model_rows(self.models(), "", "grok_api", collapsed=("kimi",))
        self.assertFalse(any(r["kind"] == "model" and r["id"] == "kimi-k3" for r in rows))
        rows = model_rows(self.models(), "", "grok_api", query="not present")
        self.assertEqual(rows[0]["kind"], "empty")


if __name__ == "__main__":
    unittest.main()
