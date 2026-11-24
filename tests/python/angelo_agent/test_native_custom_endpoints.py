# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Custom model endpoints: an organization's own model server as coding connections (ai_providers.rst).

Covers custom_endpoints.py (the description's parser), the ``CustomEndpoints`` policy key, team_reads'
``customEndpoints``, endpoint_connections.py (modes, specs, records, ids, the seat rule), the registry's helpers
for dynamic modes, the Codex launch (provider entry, key only in the environment, the network lock, the model
catalog, no process-wide effort), endpoint_catalog.py, endpoint_check.py against a local fake server, and the
chat's team-access answer for a team whose coding runs on its endpoints. No real server or provider is contacted.
The names here are made up; nothing is shaped by one organization's server.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from host_waits import until  # noqa: E402
import platform_env  # noqa: E402
from angelo_agent import (custom_endpoints, endpoint_catalog, endpoint_check, endpoint_connections,  # noqa: E402
                          endpoint_routes, managed_policy, provider_registry, team_access, team_network, team_reads)
from angelo_agent.endpoint_connections import ENV_KEY, mode_for  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.provider_registry import (CODING_MODES, CUSTOM_PROVIDER_MODES, TEAM_MODES, billing_kind,  # noqa: E402
                                            custom_provider, is_coding_mode, organization_mode, service_mode,
                                            spec_for)
from angelo_agent.runtime_launch import child_environment, codex_command  # noqa: E402
from angelo_agent.team_view import AccountView  # noqa: E402

CONSOLE = "https://console.angelo.studio"
IDENTITY = "5d0a8a43-0b7e-4f37-9c55-6c1b5b7c2a10"
HEX = IDENTITY.replace("-", "")
KEY = "sk-member-" + "x" * 24
POLICY_ORG = "2b8c4d1e-5f6a-4b7c-8d9e-0f1a2b3c4d5e"   # A policy's Organization (CustomEndpoints requires one).


def description(**changes):
    value = {"id": IDENTITY, "name": "Example Proxy", "baseUrl": "https://llm.example.test/", "server": "litellm",
             "formats": {"openai-responses": {"path": "/v1"},
                         "gemini": {"path": "/gemini/v1beta", "auth": {"header": "x-goog-api-key", "scheme": "none"}}},
             "models": [{"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"},
                        {"id": "house-model", "format": "openai-responses"},
                        {"id": "gemini-4-carbon", "format": "gemini", "profile": "gemini-4-carbon"}]}
    value.update(changes)
    return value


def view(**changes):
    values = dict(status="signed_in", org_id="org-1", org_name="Northwind Studio", user_id="user-1",
                  email="mika@example.test", seat=True, scopes=frozenset({"api", "generate"}), providers=())
    return AccountView(**(values | changes))


class Endpoints:
    """The effective endpoints of this process for one test."""

    def __init__(self, *entries, admitted=True, source="console"):
        self.endpoints = custom_endpoints.endpoints(list(entries), source)[0]
        self.admitted = admitted

    def __enter__(self):
        endpoint_connections.set_current(self.endpoints, self.admitted)
        return self.endpoints

    def __exit__(self, *exc):
        endpoint_connections.set_current(())
        endpoint_connections._relayed.clear()


class ParserTests(unittest.TestCase):
    def test_a_usable_description_parses_whole(self):
        (endpoint,), left_out = custom_endpoints.endpoints([description()])
        self.assertEqual(left_out, 0)
        self.assertEqual(endpoint.id, HEX)
        self.assertEqual(endpoint.base_url, "https://llm.example.test")
        self.assertEqual(endpoint.url("openai-responses"), "https://llm.example.test/v1")
        self.assertEqual(endpoint.format("gemini").header, "x-goog-api-key")
        self.assertEqual([m.profile for m in endpoint.models], ["gpt-6.1-sol", None, "gemini-4-carbon"])

    def test_unusable_entries_are_left_out_whole_and_counted(self):
        bad = [description(baseUrl="http://llm.example.test"),          # Plain http off loopback.
               description(baseUrl="https://user:pw@llm.example.test"),  # A credential in the address.
               description(baseUrl="https://llm.example.test/?key=1"),
               description(baseUrl="https://llm.example.test/?"), description(baseUrl="https://llm.example.test/#"),
               description(baseUrl="https://llm.example.test:0/"),
               description(extraHeaders={"authorization": "x"}),          # The key's header is never fixed.
               description(extraHeaders={"host": "x"}),
               description(formats={"openai-responses": {"path": "/v1", "auth": {"header": "cookie"}}}),
               description(models=[]), description(id="not-a-uuid"), "text"]
        for entry in bad:
            with self.subTest(entry=str(entry)[:60]):
                self.assertEqual(custom_endpoints.endpoints([entry]), ((), 1))
        found, left_out = custom_endpoints.endpoints([description(), description()])
        self.assertEqual((len(found), left_out), (1, 1))   # A repeated id keeps the first.

    def test_every_servers_model_name_shape_is_admitted_as_is(self):
        shapes = ["/data/models/qwen3-35b", "qwen3.6:35b-a3b-q4_K_M", "openai/gpt-6.1-sol", "@openai-prod/gpt-6.1-sol",
                  "my-deployment_01", "anthropic.claude-sonnet-5-5-v1:0", 'odd"name', "x" * 200]
        entry = description(models=[{"id": shape, "format": "openai-responses"} for shape in shapes])
        (endpoint,), left_out = custom_endpoints.endpoints([entry])
        self.assertEqual(([m.id for m in endpoint.models], left_out), (shapes, 0))
        # The Gemini wire writes the name into its URL path unencoded (models/{model}:streamGenerateContent).
        for bad in ("", " lead", "two words", "tab\tname", "caf\u00e9", "\uff20openai/gpt", "x" * 201,
                    "a?b", "a#b", "a%2Fb", "a\\b", "../x", "models/../x", "./x", "x/.", "..", "a//b", "//a", "a/"):
            with self.subTest(bad=bad):
                broken = description(models=[{"id": bad, "format": "openai-responses"}])
                self.assertEqual(custom_endpoints.endpoints([broken]), ((), 1))

    def test_a_gemini_model_name_never_starts_with_a_slash(self):
        # The Gemini wire writes models/{model}:streamGenerateContent: a leading "/" leaves an empty URL segment.
        gemini = description(models=[{"id": "/data/models/x", "format": "gemini"}])
        self.assertEqual(custom_endpoints.endpoints([gemini]), ((), 1))
        responses = description(models=[{"id": "/data/models/x", "format": "openai-responses"}])
        self.assertEqual(custom_endpoints.endpoints([responses])[1], 0)   # vLLM's served path, on Responses.

    def test_an_odd_model_name_reaches_codex_intact(self):
        import tomllib
        odd = 'odd"name/x:1'
        entry = description(formats={"openai-responses": {"path": "/v1"}},
                            models=[{"id": odd, "format": "openai-responses"}])
        managed_policy.reset(managed_policy.Policy())
        self.addCleanup(managed_policy.reset)
        mode = mode_for(HEX, "openai-responses")
        with tempfile.TemporaryDirectory() as state, Endpoints(entry), \
                platform_env.cleared(PATH="/usr/bin"):
            command, _env = codex_command("codex", mode, state, reuse_login=False,
                                          connection_id=endpoint_connections.connection_id(CONSOLE, "o", "u", mode),
                                          api_key=KEY)
        pairs = dict(item.split("=", 1) for item in command[command.index("-c"):][1::2])
        self.assertEqual(tomllib.loads("model = " + pairs["model"])["model"], odd)   # Codex reads -c values as TOML.

    def test_credential_headers_and_unsafe_values_are_never_fixed_headers(self):
        for name in sorted(custom_endpoints.CREDENTIAL_HEADERS):
            with self.subTest(name=name):
                self.assertEqual(custom_endpoints.endpoints([description(extraHeaders={name: "x"})]), ((), 1))
        for value in ("caf\u00e9", "zero\u200bwidth", "line\nbreak", ""):
            with self.subTest(value=value):
                self.assertEqual(custom_endpoints.endpoints([description(extraHeaders={"x-route": value})]), ((), 1))
        for name in ("x-tenant-key", "x-session-token", "x-client-secret", "x-auth-scope", "x-db-password",
                     "x-credential-id", "x-accesskey", "x-private-keys"):   # Names that say they carry a secret.
            with self.subTest(name=name):
                self.assertEqual(custom_endpoints.endpoints([description(extraHeaders={name: "x"})]), ((), 1))
        # Each supported server's routing and attribution headers pass: words, not substrings ("portkey").
        for name in ("x-portkey-provider", "x-portkey-config", "x-portkey-metadata", "http-referer", "x-title",
                     "x-openrouter-title", "x-litellm-tags", "x-ms-client-request-id", "x-team", "x-keyboard"):
            with self.subTest(allowed=name):
                self.assertEqual(custom_endpoints.endpoints([description(extraHeaders={name: "v"})])[1], 0)
        for value in ("Bearer abc", "bearer abc", "Basic dXNlcjpwdw==", "BEARER\tabc"):   # Written like a credential.
            with self.subTest(value=value):
                self.assertEqual(custom_endpoints.endpoints([description(extraHeaders={"x-route": value})]), ((), 1))
        (endpoint,), _ = custom_endpoints.endpoints([description(extraHeaders={"x-route": "blue team"})])
        self.assertEqual(endpoint.extra_headers, (("x-route", "blue team"),))
        # A format's own key header may be any of them: that is where the key goes.
        auth = description(formats={"openai-responses": {"auth": {"header": "x-portkey-api-key", "scheme": "none"}}},
                           models=[{"id": "m", "format": "openai-responses"}])
        self.assertEqual(custom_endpoints.endpoints([auth])[1], 0)

    def test_origin(self):
        (endpoint,), _ = custom_endpoints.endpoints([description(baseUrl="https://llm.example.test:8443/x")])
        self.assertEqual(endpoint.origin, "https://llm.example.test:8443")
        (endpoint,), _ = custom_endpoints.endpoints([description()])
        self.assertEqual(endpoint.origin, "https://llm.example.test:443")
        for header in ("apikey", "x-functions-key"):   # The console refuses the same names.
            self.assertIn(header, custom_endpoints.CREDENTIAL_HEADERS)

    def test_an_organizations_shared_key_is_never_accepted(self):
        # Maintainer, 2026-11-12: an organization's own secrets never reach the app. "org_key" (from the console or
        # the policy) is an unknown credential: the entry is left out and counted, like any unusable one.
        for source in ("console", "policy"):
            with self.subTest(source=source):
                self.assertEqual(custom_endpoints.endpoints([description(credential="org_key")], source), ((), 1))
        self.assertEqual(custom_endpoints.CREDENTIALS, ("member_key", "none", "command"))
        self.assertFalse(hasattr(team_reads, "org_key_path"))

    def test_loopback_http_unknown_formats_and_non_codex_profiles(self):
        local = description(baseUrl="http://127.0.0.1:8000", formats={"openai-responses": {}, "future-wire": {}},
                            models=[{"id": "m", "format": "openai-responses", "profile": "claude-opus-5-5"},
                                    {"id": "n", "format": "future-wire"}])
        (endpoint,), _ = custom_endpoints.endpoints([local])
        self.assertEqual(endpoint.base_url, "http://127.0.0.1:8000")
        self.assertEqual([f for f, _ in endpoint.formats], ["openai-responses"])
        self.assertIsNone(endpoint.models[0].profile)   # Claude runs on another runtime: untested here.

    def test_the_console_answer_and_the_policy_key(self):
        answer = {"data": {"providers": [], "customEndpoints": [description(), {"id": "bad"}]}}
        endpoints, left_out = team_reads.custom_endpoints(answer)
        self.assertEqual((len(endpoints), left_out), (1, 1))
        self.assertEqual(team_reads.custom_endpoints({"data": {"providers": []}}), ((), 0))
        policy = managed_policy.combine([("test", {"CustomEndpoints": json.dumps([description()]),
                                                   "Organization": POLICY_ORG})])
        self.assertEqual(len(policy.custom_endpoints), 1)
        self.assertEqual(policy.problems, ())
        broken = managed_policy.combine([("test", {"CustomEndpoints": [description(), "x"],
                                                   "Organization": POLICY_ORG})])
        self.assertEqual((len(broken.custom_endpoints), broken.problems), (1, ("CustomEndpoints",)))
        self.assertIsNone(managed_policy.combine([]).custom_endpoints)


class RegistryTests(unittest.TestCase):
    def test_modes_round_trip_and_static_sets_stay_static(self):
        mode = mode_for(HEX, "openai-responses")
        self.assertEqual(mode, "endpoint_openai_responses_" + HEX)
        self.assertEqual(endpoint_connections.parse_mode(mode), ("openai-responses", HEX))
        for other in ("endpoint_openai_responses_" + HEX[:-1], "endpoint_chat_" + HEX, "codex_api", None, 3):
            self.assertIsNone(endpoint_connections.parse_mode(other))
        self.assertNotIn(mode, CODING_MODES | CUSTOM_PROVIDER_MODES | TEAM_MODES)
        self.assertTrue(is_coding_mode(mode) and custom_provider(mode) and organization_mode(mode))
        self.assertTrue(organization_mode("codex_team"))
        self.assertFalse(organization_mode("codex_api") or is_coding_mode("seedream_api") or custom_provider("claude_api"))

    def test_a_spec_follows_its_format_and_maps_models_to_their_profiles(self):
        with Endpoints(description()):
            spec = spec_for(mode_for(HEX, "openai-responses"))
            self.assertEqual((spec["base_url"], spec["wire_api"], spec["runtime"]), ("https://llm.example.test/v1",
                                                                                     "responses", "codex"))
            self.assertEqual(service_mode(mode_for(HEX, "openai-responses")), "codex_api")
            self.assertEqual(billing_kind(mode_for(HEX, "openai-responses")), "API_KEY")
            sol, house = spec["models"]
            self.assertEqual((sol["efforts"], sol["context_window"], sol["untested"]),
                             (["low", "medium", "high"], 922000, False))
            self.assertEqual((house["efforts"], house["images"], house["untested"]), ([], False, True))
            self.assertFalse(spec["web_search"])
            self.assertFalse(spec["openai_service"], "an unmapped model may sit behind a translating server")
            gemini = spec_for(mode_for(HEX, "gemini"))
            self.assertEqual((gemini["wire_api"], gemini["personal"]), ("gemini", "gemini_api"))
            self.assertNotIn("provider_settings", gemini)   # The wire's stream rules come with the wire (provider_entry).
        only_gpt = description(models=[{"id": "a", "format": "openai-responses", "profile": "gpt-6.1-sol"}])
        with Endpoints(only_gpt):
            self.assertTrue(spec_for(mode_for(HEX, "openai-responses"))["openai_service"])

    def test_a_removed_endpoint_still_resolves_and_refuses_to_run(self):
        mode = mode_for(HEX, "openai-responses")
        with Endpoints():
            spec = spec_for(mode)
            self.assertTrue(spec["removed"])
            self.assertEqual(spec["models"], [])
            with self.assertRaisesRegex(RpcError, "no longer offers"):
                child_environment(mode, KEY)

    def test_a_worker_asks_angelo_for_its_chat_spec(self):
        mode = mode_for(HEX, "openai-responses")
        with Endpoints(description()) as endpoints:
            answer = endpoint_connections.build_spec(endpoints[0], "openai-responses")
        with Endpoints(), patch("angelo_agent.runtime_relay.relayed", return_value=True), \
                patch("angelo_agent.runtime_relay.probe", return_value={"ok": True, "spec": answer}) as probe:
            self.assertEqual(spec_for(mode)["base_url"], "https://llm.example.test/v1")
            spec_for(mode)
            probe.assert_called_once_with("endpoint_spec")   # Kept for the worker's chat.


class RecordTests(unittest.TestCase):
    def test_records_need_a_seat_and_ids_are_stable(self):
        endpoints = custom_endpoints.endpoints([description()])[0]
        records = endpoint_connections.records(view(), CONSOLE, endpoints)
        self.assertEqual(sorted(r["name"] for r in records.values()),
                         ["Example Proxy · Gemini", "Example Proxy · OpenAI"])
        self.assertEqual(records, endpoint_connections.records(view(), CONSOLE, endpoints))
        other = endpoint_connections.records(view(user_id="user-2"), CONSOLE, endpoints)
        self.assertFalse(records.keys() & other.keys())
        self.assertEqual(endpoint_connections.records(view(status="signed_out"), CONSOLE, endpoints), {})
        self.assertFalse(endpoint_connections.account_admits(view(seat=False)))
        self.assertFalse(endpoint_connections.account_admits(view(scopes=frozenset({"api"}))))
        self.assertTrue(endpoint_connections.account_admits(view()))
        slot = endpoint_connections.key_identity(CONSOLE, "org-1", "user-1", HEX)
        self.assertNotIn(slot, records)   # One key slot per endpoint, shared by its formats.

    def test_the_policy_wins_even_empty(self):
        console = custom_endpoints.endpoints([description()])[0]
        signed_in = view(custom_endpoints=console)
        self.assertEqual(endpoint_connections.effective(signed_in, managed_policy.Policy())[:2], (console, "console"))
        self.assertEqual(endpoint_connections.effective(signed_in, managed_policy.Policy(custom_endpoints=())),
                         ((), "policy", 0))
        self.assertEqual(endpoint_connections.effective(view(status="signed_out", custom_endpoints=console),
                                                        managed_policy.Policy())[0], ())


class LaunchTests(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(managed_policy.Policy())
        self.addCleanup(managed_policy.reset)

    def launch(self, entry, format_id, run=None, policy=None):
        if policy is not None:
            managed_policy.reset(policy)
        mode = mode_for(HEX, format_id)
        identity = endpoint_connections.connection_id(CONSOLE, "org-1", "user-1", mode)
        catalog = {"models": [{"slug": "gpt-6.1-sol", "use_responses_lite": True, "context_window": 272000}]}
        # The Gemini wire's own catalog (``codex debug models`` with a Gemini provider) lists Carbon.
        gemini = {"models": [{"slug": "gemini-4-carbon", "truncation_policy": {"mode": "tokens", "limit": 10000}}]}
        run = run or (lambda argv, **k: SimpleNamespace(returncode=0, stdout=json.dumps(
            catalog if "--bundled" in argv else gemini).encode()))
        endpoint_catalog._cache.clear()
        from angelo_agent import agent_runtime
        with tempfile.TemporaryDirectory() as state, Endpoints(entry), \
                patch.object(agent_runtime, "kind", return_value="bundled"), \
                patch.object(agent_runtime, "supports_wire", return_value=True), \
                platform_env.cleared(PATH="/usr/bin", HTTPS_PROXY="http://corp:3128"), \
                patch("angelo_agent.endpoint_catalog.child_process.run", run), \
                patch("angelo_agent.endpoint_catalog._signature", return_value=("codex", 1, 1)):
            command, env = codex_command("codex", mode, state, reuse_login=False, connection_id=identity, api_key=KEY)
            pairs = dict(item.split("=", 1) for item in command[command.index("-c"):][1::2])
            catalog_file = pairs.get("model_catalog_json")
            written = json.loads(Path(json.loads(catalog_file)).read_text()) if catalog_file else None
        return mode, command, env, pairs, written

    def test_the_openai_format_runs_on_its_server_with_the_key_only_in_the_environment(self):
        mode, command, env, pairs, written = self.launch(description(), "openai-responses")
        entry = lambda key: json.loads(pairs[f"model_providers.{mode}.{key}"])  # noqa: E731
        self.assertEqual(json.loads(pairs["model_provider"]), mode)
        self.assertEqual(entry("base_url"), "https://llm.example.test/v1")
        self.assertEqual((entry("wire_api"), entry("env_key")), ("responses", ENV_KEY))
        self.assertFalse(entry("requires_openai_auth") or entry("supports_websockets"))
        self.assertNotIn(f"model_providers.{mode}.capabilities.request_compression", pairs)
        self.assertEqual(env[ENV_KEY], KEY)
        self.assertNotIn(KEY, " ".join(command))
        self.assertEqual(json.loads(pairs["web_search"]), "disabled")
        self.assertNotIn("model_reasoning_effort", pairs)   # Each turn states its own, or none (untested models).
        self.assertEqual(env["NO_PROXY"], "llm.example.test")   # Only the server's host; everything else refused.
        self.assertEqual(env["HTTPS_PROXY"], team_network.REFUSING_PROXY)
        # The launched Codex gets the mapped model's profile entry under the server's name; the unmapped house-model
        # gets none, so Codex keeps its generic metadata for it. The wire's own entries follow Angelo's: an explicit
        # catalog replaces Codex's built-in one, and a server name the runtime knows keeps what it had.
        self.assertEqual(json.loads(pairs["model_catalog_json"]), str(Path(env["CODEX_HOME"]) / "angelo-endpoint-models.json"))
        self.assertEqual([m["slug"] for m in written["models"]], ["team-gpt-sol", "gpt-6.1-sol"])
        self.assertEqual(written["models"][0]["display_name"], "team-gpt-sol")
        self.assertTrue(written["models"][0]["use_responses_lite"])   # gpt-6.1-sol's own request form.

    def test_the_gemini_format_takes_its_own_header_and_timeout(self):
        mode, command, env, pairs, written = self.launch(description(), "gemini")
        self.assertEqual(json.loads(pairs[f"model_providers.{mode}.env_http_headers.x-goog-api-key"]), ENV_KEY)
        self.assertNotIn(f"model_providers.{mode}.env_key", pairs)
        # An organization's own server: just under the host's limit, re-sent once (ENDPOINT_STREAM_SETTINGS).
        self.assertEqual(json.loads(pairs[f"model_providers.{mode}.stream_idle_timeout_ms"]), 570000)
        self.assertEqual(json.loads(pairs[f"model_providers.{mode}.stream_max_retries"]), 1)
        self.assertEqual(env[ENV_KEY], KEY)   # Scheme "none": the key alone.
        # The mapped model gets Carbon's entry from the Gemini wire's catalog (not Codex's bundled one, which has none).
        self.assertEqual([m["slug"] for m in written["models"]], ["gemini-4-carbon"])
        self.assertEqual(written["models"][0]["truncation_policy"], {"mode": "tokens", "limit": 10000})

    def test_other_headers_schemes_and_fixed_headers(self):
        entry = description(formats={"openai-responses": {"path": "/openai/v1", "auth": {"header": "api-key"}}},
                            models=[{"id": "deploy-1", "format": "openai-responses"}],
                            extraHeaders={"x-route": "blue"})
        mode, _command, env, pairs, written = self.launch(entry, "openai-responses")
        self.assertEqual(json.loads(pairs[f"model_providers.{mode}.env_http_headers.api-key"]), ENV_KEY)
        self.assertEqual(env[ENV_KEY], "Bearer " + KEY)
        self.assertEqual(json.loads(pairs[f"model_providers.{mode}.http_headers.x-route"]), "blue")
        self.assertIsNone(written)   # Only an untested model: nothing to map.

    def test_the_organization_policy_and_its_roots(self):
        policy = managed_policy.Policy(allow_personal_connections=False, extra_ca_certificates="/etc/roots.pem")
        _mode, _command, env, _pairs, _written = self.launch(description(), "openai-responses", policy=policy)
        self.assertEqual(env["CODEX_CA_CERTIFICATE"], "/etc/roots.pem")   # Allowed: the organization's own.

    def test_a_runtime_that_cannot_list_its_catalog_still_launches(self):
        failing = lambda *a, **k: SimpleNamespace(returncode=2, stdout=b"")  # noqa: E731
        _mode, _command, _env, pairs, written = self.launch(description(), "openai-responses", run=failing)
        self.assertIsNone(written)
        self.assertNotIn("model_catalog_json", pairs)


class FormatAndCredentialTests(unittest.TestCase):
    def setUp(self):
        managed_policy.reset(managed_policy.Policy())
        self.addCleanup(managed_policy.reset)

    def claude_env(self, auth, credential="member_key", extra=None):
        entry = description(formats={"anthropic-messages": {"path": "/anthropic", "auth": auth}},
                            models=[{"id": "team-opus", "format": "anthropic-messages", "profile": "claude-opus-5-5"},
                                    {"id": "gpt-6.1-sol", "format": "anthropic-messages", "profile": "gpt-6.1-sol"}],
                            credential=credential, extraHeaders=extra or {})
        mode = mode_for(HEX, "anthropic-messages")
        with Endpoints(entry), platform_env.cleared(PATH="/usr/bin"):
            spec = spec_for(mode)
            return spec, child_environment(mode, KEY if credential != "none" else "")

    def test_the_messages_format_runs_claude_code_on_the_server(self):
        spec, env = self.claude_env({"header": "authorization", "scheme": "bearer"}, extra={"x-route": "blue"})
        self.assertEqual((spec["runtime"], spec["personal"]), ("claude", "claude_api"))
        self.assertNotIn("wire_api", spec)
        self.assertEqual([m["untested"] for m in spec["models"]], [False, True])   # Only Claude profiles fit.
        self.assertFalse(custom_provider(mode_for(HEX, "anthropic-messages")))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://llm.example.test/anthropic")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], KEY)
        self.assertEqual(env["ANTHROPIC_CUSTOM_HEADERS"], "x-route: blue")
        self.assertEqual(env["NO_PROXY"], "llm.example.test")

    def test_each_header_reaches_claude_code_in_its_own_variable(self):
        _spec, env = self.claude_env({"header": "x-api-key", "scheme": "none"})
        self.assertEqual(env["ANTHROPIC_API_KEY"], KEY)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        _spec, env = self.claude_env({"header": "x-portkey-api-key", "scheme": "none"})
        self.assertEqual(env["ANTHROPIC_CUSTOM_HEADERS"], "x-portkey-api-key: " + KEY)
        self.assertEqual(env["ANTHROPIC_API_KEY"], endpoint_connections.NO_KEY)   # Never the key in another header.
        _spec, env = self.claude_env({"header": "authorization", "scheme": "bearer"}, credential="none")
        self.assertEqual(env["ANTHROPIC_API_KEY"], endpoint_connections.NO_KEY)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)

    def test_a_server_that_checks_no_key_gets_no_key_header_from_codex(self):
        entry = description(credential="none")
        with Endpoints(entry):
            entry_fields = endpoint_connections.provider_entry(spec_for(mode_for(HEX, "openai-responses")))
        self.assertFalse([k for k in entry_fields if k.startswith(("env_key", "env_http_headers"))])

    def test_a_command_comes_only_from_the_policy(self):
        command = description(credential="command", command=["/usr/local/bin/sso-key", "--llm"], refreshSeconds=900)
        self.assertEqual(custom_endpoints.endpoints([command]), ((), 1))   # From the console: left out.
        (endpoint,), _ = custom_endpoints.endpoints([command], "policy")
        self.assertEqual((endpoint.command, endpoint.refresh_seconds), (("/usr/local/bin/sso-key", "--llm"), 900))
        for bad in (["sso-key"], [], ["/bin/x"] * 17):
            self.assertEqual(custom_endpoints.endpoints([dict(command, command=bad)], "policy")[1], 1)
        self.assertEqual(custom_endpoints.endpoints([dict(command, refreshSeconds=5)], "policy")[1], 1)
        policy = managed_policy.combine([("test", {"CustomEndpoints": [command], "Organization": POLICY_ORG})])
        self.assertEqual(policy.custom_endpoints[0].credential, "command")

    def test_a_key_program_runs_without_angelos_secrets_and_its_first_line_is_the_key(self):
        from angelo_agent import endpoint_keys
        (endpoint,), _ = custom_endpoints.endpoints(
            [description(credential="command", command=["/opt/sso/key"], refreshSeconds=600)], "policy")
        seen = {}

        def run(argv, **options):
            seen.update(argv=argv, env=options["env"])
            return SimpleNamespace(returncode=0, stdout="\n  short-lived-key  \nnoise\n")
        environ = {"PATH": "/usr/bin", "ANGELO_CACHE_SECRET": "s", "ANTHROPIC_API_KEY": "k", "HOME": "/h"}
        person = platform_env.essential_environ(PATH="/usr/bin", HOME="/h")   # With what every process has.
        policy = managed_policy.Policy(custom_endpoints=(endpoint,), organization=POLICY_ORG)   # Read as it runs.
        with platform_env.cleared(environ), patch.object(managed_policy, "load", return_value=policy):
            self.assertTrue(endpoint_keys.run_command(endpoint, run))
            until(lambda: not endpoint_keys.running(endpoint.id), what="the key program's job")
        finished = endpoint_keys.take_finished(now=100.0)
        self.assertEqual(finished, [(endpoint.id, "short-lived-key", "", 600)])
        self.assertEqual(seen["argv"], ["/opt/sso/key"])
        self.assertEqual(seen["env"], person)
        self.assertFalse(endpoint_keys.due(endpoint.id, now=100.0 + 0.5 * 600))
        self.assertTrue(endpoint_keys.due(endpoint.id, now=100.0 + 0.8 * 600))
        endpoint_keys._finished.append(("failed", None, "x", 0))   # A program that gave no key: run again later.
        endpoint_keys.take_finished(now=100.0)
        self.assertFalse(endpoint_keys.due("failed", now=100.0 + 60))
        self.assertTrue(endpoint_keys.due("failed", now=100.0 + endpoint_keys.RETRY_SECONDS))
        endpoint_keys.forget()

    def test_no_seat_means_a_clear_reason_and_no_launch(self):
        from angelo_agent import endpoint_ui
        policy_only = managed_policy.Policy(custom_endpoints=custom_endpoints.endpoints([description()], "policy")[0],
                                            organization="org-1")   # The view's organization.
        no_seat = view(seat=False)
        ui = SimpleNamespace(_memory_keys={}, _controllers={})
        with patch.object(endpoint_ui, "_ui", return_value=ui), patch("angelo_agent.endpoint_ui._load_saved"):
            endpoint_ui.refresh(ui, no_seat, CONSOLE, policy_only)
            try:
                records = endpoint_ui.records()
                self.assertEqual(len(records), 2)   # Listed, like team connections, and refused by the seat rule.
                for record in records.values():
                    self.assertEqual(endpoint_ui.reason(record).template, endpoint_connections.NO_SEAT)
                    self.assertFalse(endpoint_ui.ready(record))
                with platform_env.cleared(PATH="/usr/bin"), \
                        self.assertRaisesRegex(RpcError, "needs a seat"):
                    child_environment(mode_for(HEX, "openai-responses"), KEY)
            finally:
                endpoint_ui.close()

    def test_endpoint_chats_run_no_web_tools(self):
        from angelo_agent import web_search_access
        context = SimpleNamespace(state={"provider": mode_for(HEX, "anthropic-messages")},
                                  native_tools=("WebSearch", "WebFetch", "Read"))
        self.assertEqual(web_search_access.native_tools(context), ("Read",))


class _EndpointUiCase(unittest.TestCase):
    """endpoint_ui with a stand-in for ui and its key store."""

    def setUp(self):
        from angelo_agent import endpoint_ui
        self.endpoint_ui = endpoint_ui
        self.ui = SimpleNamespace(_memory_keys={}, _controllers={}, connection_in_use=lambda identity: False)
        self.ui.replace_connection_key = lambda record, key: self.ui._memory_keys.__setitem__(record["id"], key)
        self.vault = SimpleNamespace(saved={}, busy=lambda slot: False, stored={}, state={},
                                     load=lambda slot: None, forget=lambda slot: None)
        self.vault.save = lambda slot, secret, label: self.vault.saved.__setitem__(slot, secret)
        for target, value in (("_ui", self.ui), ("_vault", self.vault)):
            patcher = patch.object(endpoint_ui, target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch("angelo_agent.runtime_bindings.end_connection")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(endpoint_ui.close)

    def refresh(self, account=None, now=None, **changes):
        account = account or view()
        listed = custom_endpoints.endpoints([description(**changes)], "policy")[0]
        policy = managed_policy.Policy(custom_endpoints=listed,
                                       organization=account.org_id)   # The view's organization: its endpoints run.
        self.endpoint_ui.refresh(self.ui, account, CONSOLE, policy, now=now)


class KeyBindingTests(_EndpointUiCase):
    """A kept key goes only to the address it was given for (an admin may change an endpoint's address)."""

    def test_a_moved_address_drops_the_key_and_asks_for_it_again(self):
        self.refresh()
        self.endpoint_ui.set_key(HEX, KEY)
        slot = self.endpoint_ui.key_slot(HEX)
        kept, saved_key = endpoint_routes.read(self.vault.saved[slot])   # Saved with its whole route.
        self.assertEqual((kept["origin"], saved_key), ("https://llm.example.test:443", KEY))
        self.assertEqual(set(self.ui._memory_keys.values()), {KEY})
        self.refresh(baseUrl="https://elsewhere.example.test")
        self.assertFalse(self.endpoint_ui.has_key(HEX))
        self.assertNotIn(KEY, self.ui._memory_keys.values())
        record = next(iter(self.endpoint_ui.records().values()))
        self.assertEqual(self.endpoint_ui.reason(record).template, self.endpoint_ui.MOVED)
        self.assertEqual(self.endpoint_ui.reason(record).fields["host"], "elsewhere.example.test")
        # The key saved for the old address is read back, and still never applied to the new one.
        self.endpoint_ui.key_loaded(self.ui, slot, "load", "loaded", self.vault.saved[slot])
        self.assertFalse(self.endpoint_ui.has_key(HEX))
        self.endpoint_ui.set_key(HEX, "sk-new-" + "y" * 20)
        self.assertEqual(self.endpoint_ui.reason(record), "")

    def test_a_saved_key_for_this_address_is_applied(self):
        self.refresh()
        slot = self.endpoint_ui.key_slot(HEX)
        saved = endpoint_routes.stored(endpoint_routes.route(endpoint_connections.find(HEX)), KEY)
        self.assertTrue(self.endpoint_ui.key_loaded(self.ui, slot, "load", "loaded", saved))
        self.assertTrue(self.endpoint_ui.has_key(HEX))
        self.assertEqual(self.endpoint_ui.stored_value("not json"), (None, None))
        old_form = json.dumps({"origin": "https://llm.example.test:443", "key": KEY})   # Saved before routes were kept.
        why = endpoint_routes.change(endpoint_routes.read(old_form)[0], endpoint_connections.find(HEX))
        self.assertEqual(why.template, endpoint_routes.PATH_CHANGED)


class SeatTests(_EndpointUiCase):
    """The seat rule applies to what runs, not only to what starts (policy-only endpoints included)."""

    def setUp(self):
        super().setUp()
        from angelo_agent import runtime_bindings
        self.ended = []
        patcher = patch.object(runtime_bindings, "end_connection", side_effect=self.ended.append)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_removed_seat_ends_what_runs_and_a_returned_one_restores_it(self):
        self.refresh(now=0.0)
        self.endpoint_ui.set_key(HEX, KEY)
        records = list(self.endpoint_ui.records())
        self.assertEqual(set(self.ui._memory_keys.values()), {KEY})
        self.refresh(view(seat=False), now=10.0)
        self.assertEqual(self.ui._memory_keys, {})
        self.assertEqual(sorted(self.ended), sorted(records))   # Every runtime on them ends, a worker's too.
        self.assertFalse(endpoint_connections.admitted())
        self.assertEqual(self.endpoint_ui.reason(self.endpoint_ui.records()[records[0]]).template,
                         endpoint_connections.NO_SEAT)
        with platform_env.cleared(PATH="/usr/bin"), \
                self.assertRaisesRegex(RpcError, "needs a seat"):
            child_environment(mode_for(HEX, "openai-responses"), KEY)
        self.refresh(now=20.0)   # The seat given back: the key the person entered applies again.
        self.assertEqual(set(self.ui._memory_keys.values()), {KEY})

    def test_offline_keeps_a_confirmed_seat_for_ten_minutes_then_stops(self):
        self.refresh(now=0.0)
        self.endpoint_ui.set_key(HEX, KEY)
        offline = view(status="offline", seat_confirmed_at=100.0)   # The last token that confirmed the seat.
        self.refresh(offline, now=100.0)
        self.assertTrue(endpoint_connections.admitted())
        self.assertEqual(set(self.ui._memory_keys.values()), {KEY})
        self.assertFalse(self.endpoint_ui.grace_ended(now=100.0 + endpoint_connections.OFFLINE_GRACE - 1))
        self.assertTrue(self.endpoint_ui.grace_ended(now=100.0 + endpoint_connections.OFFLINE_GRACE))
        self.refresh(offline, now=100.0 + endpoint_connections.OFFLINE_GRACE)
        self.assertFalse(endpoint_connections.admitted())
        self.assertEqual(self.ui._memory_keys, {})
        record = next(iter(self.endpoint_ui.records().values()))
        self.assertEqual(self.endpoint_ui.reason(record).template, self.endpoint_ui.UNCONFIRMED)
        self.assertTrue(self.endpoint_ui.has_key(HEX))   # Kept for this account, never sent while unconfirmed.
        self.refresh(now=900.0)   # Reached again: confirmed, and the key applies again.
        self.assertEqual(set(self.ui._memory_keys.values()), {KEY})

    def test_signing_out_forgets_the_keys(self):
        self.refresh(now=0.0)
        self.endpoint_ui.set_key(HEX, KEY)
        self.refresh(view(status="signed_out", org_id="", user_id=""), now=5.0)
        self.assertFalse(self.endpoint_ui.has_key(HEX))
        self.assertEqual(self.endpoint_ui.records(), {})


class ClaudeWindowTests(unittest.TestCase):
    """A mapped Claude model behind a gateway keeps its real context window (Claude Code's ``modelOverrides``)."""

    def test_mapped_claude_models_are_overridden_and_nothing_else(self):
        from angelo_agent.claude_transport import claude_arguments, launch_values
        entry = description(formats={"openai-responses": {"path": "/v1"}, "anthropic-messages": {"path": ""}},
                            models=[{"id": "team-opus", "format": "anthropic-messages", "profile": "claude-opus-5-5"},
                                    {"id": "claude-haiku-5-5", "format": "anthropic-messages",
                                     "profile": "claude-haiku-5-5"},   # The real name: nothing to correct.
                                    {"id": "house-claude", "format": "anthropic-messages"},   # Unmapped: assumed 200K.
                                    {"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"}])
        with Endpoints(entry):
            overrides = endpoint_connections.claude_model_overrides(mode_for(HEX, "anthropic-messages"))
            self.assertEqual(overrides, {"claude-opus-5-5": "team-opus"})
            self.assertEqual(endpoint_connections.claude_model_overrides(mode_for(HEX, "openai-responses")), {})
            self.assertEqual(endpoint_connections.claude_model_overrides("claude_api"), {})
            values = {"tools": "", "direct": False, "fast": False, "model": "team-opus", "effort": "high",
                      "thinking": False, "resume": "", "gateway": False, "overrides": overrides}
            command = claude_arguments("claude", values)
            settings = json.loads(command[command.index("--settings") + 1])
            self.assertEqual(settings["modelOverrides"], {"claude-opus-5-5": "team-opus"})
            self.assertEqual(launch_values(command[1:])["overrides"], overrides)
        self.assertEqual(endpoint_connections.claude_model_overrides(mode_for(HEX, "anthropic-messages")), {})  # Removed.

    def test_a_model_claude_code_cannot_place_keeps_the_200k_window(self):
        # Claude Code 2.1.323 assumes 1M (was 200K) for a model ID it doesn't recognize, so a smaller server model
        # fills and fails every turn; CLAUDE_CODE_DISABLE_1M_CONTEXT=1 keeps 200K. Set only for such a launch: a mapped
        # or real Claude name keeps its own window.
        from angelo_agent.claude_transport import claude_arguments, launch_values
        entry = description(formats={"openai-responses": {"path": "/v1"}, "anthropic-messages": {"path": ""}},
                            models=[{"id": "team-opus", "format": "anthropic-messages", "profile": "claude-opus-5-5"},
                                    {"id": "claude-haiku-5-5", "format": "anthropic-messages",
                                     "profile": "claude-haiku-5-5"},
                                    {"id": "house-llm", "format": "anthropic-messages"}])
        messages = mode_for(HEX, "anthropic-messages")
        with Endpoints(entry):
            models = ("team-opus", "claude-haiku-5-5", "house-llm", "", "claude-opus-4-8", "claude-sonnet-5",
                      "claude-3-5-sonnet-20241022", "claude-opus-5-5[1m]", "house-llm[1m]", "claude-house")
            unknown = {model: endpoint_connections.claude_window_unknown(messages, model) for model in models}
            # Claude Code places its own model IDs and aliases (real ones outside Angelo's price table included), and a
            # name with the [1m] suffix states its window; a "claude-" name of no Claude family is still a guess.
            # Angelo's price table plays no part.
            self.assertEqual(unknown, {"team-opus": False, "claude-haiku-5-5": False, "house-llm": True, "": True,
                                       "claude-opus-4-8": False, "claude-sonnet-5": False,
                                       "claude-3-5-sonnet-20241022": False, "claude-opus-5-5[1m]": False,
                                       "house-llm[1m]": False, "claude-house": True})
            for mode in (mode_for(HEX, "openai-responses"), "claude_api", "claude_subscription"):
                self.assertFalse(endpoint_connections.claude_window_unknown(mode, "house-llm"), mode)
            for model, expected in (("house-llm", {"CLAUDE_CODE_DISABLE_1M_CONTEXT": "1"}), ("team-opus", None)):
                values = {"tools": "", "direct": False, "fast": False, "model": model, "effort": "high",
                          "thinking": False, "resume": "", "gateway": False,
                          "overrides": endpoint_connections.claude_model_overrides(messages),
                          "window_200k": unknown[model]}
                command = claude_arguments("claude", values)
                with self.subTest(model=model):
                    self.assertEqual(json.loads(command[command.index("--settings") + 1]).get("env"), expected)
                    self.assertEqual(launch_values(command[1:])["window_200k"], unknown[model])


class PersonScopeTests(_EndpointUiCase):
    """A shared studio workstation: another Angelo account on the same OS user never gets the first person's key.

    The key store item's account is ``<key slot>@<profile>`` (``saved_keys.account_name``), and the slot is
    ``key_identity(console, organization, person, endpoint)``: the person is part of the item's identity, not only
    the OS user."""

    def test_another_person_or_organization_never_gets_the_first_persons_key(self):
        loads = []
        self.vault.load = loads.append
        self.refresh(now=0.0)
        self.endpoint_ui.set_key(HEX, KEY)
        first = self.endpoint_ui.key_slot(HEX)
        saved = self.vault.saved[first]
        for other in (view(user_id="user-2"), view(org_id="org-2")):
            with self.subTest(org=other.org_id, user=other.user_id):
                loads.clear()
                self.refresh(other, now=10.0)
                slot = self.endpoint_ui.key_slot(HEX)
                self.assertEqual(slot, endpoint_connections.key_identity(CONSOLE, other.org_id, other.user_id, HEX))
                self.assertNotEqual(slot, first)
                self.assertFalse(self.endpoint_ui.has_key(HEX))   # The first person's key left memory.
                self.assertNotIn(KEY, self.ui._memory_keys.values())
                self.endpoint_ui.pump()   # The key store is asked for this person's own item only.
                self.assertEqual(loads, [slot])
                # The first person's item, read back late, is no slot of this account's: never applied.
                self.assertFalse(self.endpoint_ui.key_loaded(self.ui, first, "load", "loaded", saved))
                self.assertFalse(self.endpoint_ui.has_key(HEX))


class BindingSweepTests(unittest.TestCase):
    def test_sign_out_ends_team_and_endpoint_children_and_the_policy_ends_only_personal_ones(self):
        from angelo_agent import runtime_bindings
        bound = [("codex_team", "a"), (mode_for(HEX, "gemini"), "b"), ("codex_api", "c"), ("claude_subscription", "d")]
        service = SimpleNamespace(end=lambda keep: sum(1 for b in bound if keep(b)))
        with patch.object(runtime_bindings, "_services", return_value=[service]):
            self.assertEqual(runtime_bindings.end_organization(), 2)
            self.assertEqual(runtime_bindings.end_personal(), 2)


class TurnAndAccessTests(unittest.TestCase):
    def test_an_unmapped_model_is_labelled_untested_in_the_picker(self):
        from angelo_agent.chat_popovers import model_name
        with Endpoints(description()):
            house, = [m for m in spec_for(mode_for(HEX, "openai-responses"))["models"] if m["untested"]]
        self.assertEqual(model_name(house), "house-model · untested")
        self.assertEqual(model_name({"name": "team-gpt-sol", "untested": False}), "team-gpt-sol")

    def test_an_untested_model_turn_sends_no_effort(self):
        from angelo_agent.chat_settings import codex_turn_options
        models = [{"id": "house-model", "name": "house-model", "efforts": [], "default": True, "fast_tier": None,
                   "images": False, "untested": True},
                  {"id": "team-gpt-sol", "name": "team-gpt-sol", "efforts": ["low", "medium", "high"],
                   "default": False, "fast_tier": None, "images": True, "untested": False}]
        controller = SimpleNamespace(state={"options": {"model": "house-model"}}, models=models, settings={})
        self.assertNotIn("effort", codex_turn_options(controller))
        controller.state["options"] = {"model": "team-gpt-sol", "effort": "high"}
        self.assertEqual(codex_turn_options(controller)["effort"], "high")

    def test_a_team_on_its_endpoints_alone_is_ready_or_asks_for_the_key(self):
        bare = view(providers=())
        self.assertEqual(team_access.access(bare, False, "", None)["state"], team_access.NO_PROVIDERS)
        self.assertEqual(team_access.access(bare, False, "", False)["state"], team_access.ENDPOINT_KEY)
        self.assertEqual(team_access.access(bare, False, "", True)["state"], team_access.READY)
        self.assertEqual(team_access.access(view(seat=False), False, "", True)["state"], team_access.NO_SEAT)

    def test_saved_chats_keep_their_endpoint_mode(self):
        from angelo_agent import session_validation
        mode = mode_for(HEX, "gemini")
        self.assertEqual(session_validation.index_record({"id": IDENTITY, "provider": mode})["provider"], mode)


class _FakeServer(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "ok"
    bodies = []

    def log_message(self, *args):
        pass

    def _send(self, status, body, kind="application/json"):
        data = body.encode()
        self.send_response(status)
        self.send_header("content-type", kind)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    listed = ["team-gpt-sol"]   # What the model list names, per test.

    def do_GET(self):
        if self.mode == "no_list":
            return self._send(404, "{}")
        self._send(200, json.dumps({"data": [{"id": name} for name in self.listed]}))

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        _FakeServer.bodies.append(body)
        text = json.dumps(body)
        answered = "_call_output" in text
        custom = '"type": "custom"' in text
        if self.mode == "missing_key":
            return self._send(500, json.dumps({"error": {"message": "No api key passed in."}}))
        if (self.mode == "custom_400" and custom) or (self.mode == "result_400" and answered):
            return self._send(400, json.dumps({"error": {"message": "Unsupported item type"}}))
        if self.mode == "text_call" or (self.mode == "custom_strip" and custom) or answered:
            events = [{"type": "response.output_text.delta", "delta": 'angelo_check({"ok": true})'
                       if self.mode == "text_call" else "done"}]
        elif custom:
            events = [{"type": "response.output_item.done", "item": {
                "type": "custom_tool_call", "call_id": "call_c1", "name": "angelo_check", "input": "ok"}}]
        else:
            events = [{"type": "response.output_item.done",
                       "item": {"type": "function_call", "name": "angelo_check", "arguments": "{}"}}]
        self._send(200, "".join("data: %s\n\n" % json.dumps(e) for e in events), "text/event-stream")


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeServer)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        entry = description(baseUrl="http://127.0.0.1:%d" % self.server.server_address[1],
                            formats={"openai-responses": {"path": "/v1"}},
                            models=[{"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"}])
        self.endpoint = custom_endpoints.endpoints([entry])[0][0]

    def run_check(self, mode, shapes=None):
        _FakeServer.mode, _FakeServer.bodies = mode, []
        opener = endpoint_check.urllib.request.build_opener(endpoint_check.urllib.request.ProxyHandler({}))
        return endpoint_check.run(self.endpoint, KEY, opener, shapes)

    CODE_MODE = {"team-gpt-sol": ("custom", True)}   # gpt-6.1-sol's catalog entry: code mode, Responses Lite.

    def test_shapes_come_from_each_mapped_models_catalog_entry(self):
        from angelo_agent import endpoint_check_tools as tools
        catalog = {"gpt-6.1-sol": {"tool_mode": "code_mode_only", "use_responses_lite": True},
                   "gpt-5.5-terra": {"use_responses_lite": False}}
        (endpoint,), _ = custom_endpoints.endpoints([description(models=[
            {"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"},
            {"id": "team-terra", "format": "openai-responses", "profile": "gpt-5.5-terra"},
            {"id": "house-model", "format": "openai-responses"}])])
        self.assertEqual(tools.shapes(endpoint, catalog), {"team-gpt-sol": ("custom", True),
                                                           "team-terra": ("function", False),
                                                           "house-model": ("function", False)})

    def test_a_code_mode_model_gets_its_freeform_tool_the_lite_way_and_its_result_goes_back(self):
        report = self.run_check("ok", self.CODE_MODE)
        templates = [f.text.template for f in report.findings]
        self.assertIn(endpoint_check.TOOL_OK, templates)
        self.assertIn(endpoint_check.RESULT_OK, templates)
        self.assertTrue(report.ok)
        first, second = _FakeServer.bodies[0], _FakeServer.bodies[1]
        self.assertNotIn("tools", first)   # As Codex sends Responses Lite: an additional_tools item, not ``tools``.
        listed = first["input"][0]
        exec_tool = listed["tools"][0]["tools"][0]
        self.assertEqual((listed["type"], listed["role"], listed["tools"][0]["name"]),
                         ("additional_tools", "developer", "functions"))
        self.assertEqual((exec_tool["type"], exec_tool["format"]["type"]), ("custom", "grammar"))
        self.assertEqual([i.get("type") for i in second["input"][2:]], ["custom_tool_call", "custom_tool_call_output"])
        self.assertEqual(second["input"][3]["call_id"], "call_c1")

    def test_a_server_that_drops_or_refuses_custom_tools_is_named_for_the_admin(self):
        for mode, template in (("custom_strip", endpoint_check.CUSTOM_DROPPED),
                               ("custom_400", endpoint_check.CUSTOM_REFUSED)):
            with self.subTest(mode=mode):
                report = self.run_check(mode, self.CODE_MODE)
                finding = next(f for f in report.findings if f.text.template == template)
                self.assertEqual((finding.level, finding.text.fields["profile"]), (endpoint_check.ERROR, "gpt-6.1-sol"))
                self.assertIn("admin", str(finding.text))
                self.assertFalse(report.ok)

    def test_a_refused_tool_result_ends_the_check_as_an_error_in_every_shape(self):
        for shapes in (None, self.CODE_MODE):
            with self.subTest(shapes=shapes):
                report = self.run_check("result_400", shapes)
                finding = next(f for f in report.findings if f.text.template == endpoint_check.RESULT_REFUSED)
                self.assertEqual(finding.text.fields["status"], 400)
                self.assertFalse(report.ok)

    def test_a_working_server_lists_models_and_calls_the_tool(self):
        report = self.run_check("ok")
        templates = [f.text.template for f in report.findings]
        self.assertIn(endpoint_check.MODELS_LISTED, templates)
        self.assertIn(endpoint_check.TOOL_OK, templates)
        self.assertTrue(report.ok)

    def test_a_missing_key_is_named_whatever_the_status(self):
        report = self.run_check("missing_key")
        refused = next(f for f in report.findings if f.text.template == endpoint_check.KEY_REFUSED)
        self.assertEqual(refused.text.fields["status"], 500)
        self.assertFalse(report.ok)
        self.assertNotIn(KEY, json.dumps([str(f.text.fields) for f in report.findings]))

    def test_a_route_that_lists_other_names_or_none_is_said_once_and_partial_lists_warn_per_model(self):
        """A pass-through route lists the provider's names (or nothing): that list can't judge the configured
        models, so one neutral line says so; a route listing some configured models warns about the others."""
        entry = description(baseUrl="http://127.0.0.1:%d" % self.server.server_address[1],
                            formats={"openai-responses": {"path": "/v1"}},
                            models=[{"id": "team-gpt-sol", "format": "openai-responses", "profile": "gpt-6.1-sol"},
                                    {"id": "team-mini", "format": "openai-responses"}])
        endpoint = custom_endpoints.endpoints([entry])[0][0]
        opener = endpoint_check.urllib.request.build_opener(endpoint_check.urllib.request.ProxyHandler({}))
        _FakeServer.mode = "ok"
        self.addCleanup(setattr, _FakeServer, "listed", ["team-gpt-sol"])
        for listed, template, level in ((["fake-model", "gemini-2.0-flash"], endpoint_check.MODELS_ROUTE, "ok"),
                                        ([], endpoint_check.MODELS_ROUTE, "ok"),
                                        (["team-gpt-sol"], endpoint_check.MODELS_MISSING, "warning"),
                                        (["team-gpt-sol", "team-mini"], endpoint_check.MODELS_LISTED, "ok")):
            with self.subTest(listed=listed):
                _FakeServer.listed = listed
                first = endpoint_check.run(endpoint, KEY, opener).findings[0]
                self.assertEqual((first.text.template, first.level), (template, level))
                if template == endpoint_check.MODELS_MISSING:
                    self.assertEqual(first.text.fields["models"], "team-mini")

    def test_tool_calls_as_text_and_no_model_list(self):
        report = self.run_check("text_call")
        self.assertIn(endpoint_check.TOOL_AS_TEXT, [f.text.template for f in report.findings])
        report = self.run_check("no_list")
        self.assertEqual(report.findings[0].text.template, endpoint_check.MODELS_UNLISTED)

    def test_streaming_is_judged_from_arrival_times_never_from_a_fast_reply(self):
        spread = [0.5 + 0.05 * i for i in range(20)]
        bunched = [3.0 + 0.001 * i for i in range(20)]
        fast = [0.01 * i for i in range(20)]
        self.assertEqual(endpoint_check.streaming_finding("gemini", spread).text.template, endpoint_check.STREAMING_OK)
        self.assertEqual(endpoint_check.streaming_finding("gemini", bunched).text.template, endpoint_check.BUFFERED)
        self.assertEqual(endpoint_check.streaming_finding("gemini", fast).text.template,
                         endpoint_check.STREAMING_UNKNOWN)
        self.assertEqual(endpoint_check.streaming_finding("gemini", spread[:3]).level, endpoint_check.WARNING)


class CatalogTests(unittest.TestCase):
    def test_entries_take_the_servers_name_and_skip_unmapped_models(self):
        spec = {"models": [{"id": "team-gpt-sol", "profile": "gpt-6.1-sol"}, {"id": "house-model", "profile": None}]}
        entries = endpoint_catalog.entries(spec, {"gpt-6.1-sol": {"slug": "gpt-6.1-sol", "priority": 1}})
        self.assertEqual(entries, [{"slug": "team-gpt-sol", "display_name": "team-gpt-sol", "priority": 1}])


if __name__ == "__main__":
    unittest.main()
