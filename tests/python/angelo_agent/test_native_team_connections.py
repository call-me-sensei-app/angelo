# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Team connections: registry modes, virtual records, runtimes and generation through the gateway.

Covers team_connections.py, team_endpoints.py, team_errors.py, the TEAM
modes in provider_registry.py, the team branches of providers.py (child
environment, Codex ``model_providers``) and of generative.py /
studio_generation.py (gateway bindings, UNAUTHENTICATED retry, RATE_LIMITED
polling), and the guarantee that a team connection is never written to
connections.json. No real console, gateway or provider is contacted.
"""

import ast
import base64
import json
import os
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
from claude_protocol_fixtures import ClaudeAdapterFixture, NativeInitializeFixture, completed  # noqa: E402
from angelo_agent import gateway_capabilities, provider_registry, team_connections, team_endpoints  # noqa: E402
from angelo_agent.connections import ConnectionStore  # noqa: E402
from angelo_agent.generative import GenerationBinding, bind_team_generation  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402
from angelo_agent.provider_registry import (CODING, GENAI, SPECS, TEAM, TEAM_ENV_KEY, TEAM_MODES,  # noqa: E402
                                            billing_kind, billing_label, methods, service_mode)
from angelo_agent.providers import CodexProvider, child_environment, codex_command  # noqa: E402
from angelo_agent.studio_generation import (GatewayRejection, GenerationError, enhance_gemini, generate,  # noqa: E402
                                            request_bytes)
from angelo_agent.team_errors import SignInRequired, TeamError, api_error, gateway_error, oauth_error  # noqa: E402
from angelo_agent.team_view import AccountView  # noqa: E402

CONSOLE = "https://console.angelo.studio"
GATEWAY = "https://gateway.angelo.studio"
GRANT_KEY = "agk_" + "k" * 43
ROUTES = {"claude_team": "/v1/anthropic", "codex_team": "/v1/openai/v1", "grok_team": "/v1/xai/v1",
          "minimax_team": "/v1/minimax/v1", "kimi_team": "/v1/moonshot/v1", "openai_images_team": "/v1/openai/v1",
          "gemini_images_team": "/v1/google/v1beta", "seedream_team": "/v1/byteplus/api/v3",
          "tripo_team": "/v1/tripo/v3", "meshy_team": "/v1/meshy"}
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (64).to_bytes(4, "big") * 2 + b"\x08\x06\0\0\0").decode()


def view(**changes):
    values = dict(status="signed_in", org_id="org-1", org_name="Northwind Studio", user_id="user-1",
                  email="mika@example.test", seat=True, scopes=frozenset({"api", "generate", "offline_access"}),
                  providers=("anthropic", "google", "openai", "tripo", "xai"))
    return AccountView(**(values | changes))


class Tokens:
    """The TeamAccount surface a binding uses: a new token after each refusal."""

    def __init__(self):
        self.issued, self.lock = ["token-1"], threading.Lock()

    def access_token(self, stale=""):
        with self.lock:
            if stale == self.issued[-1]:
                self.issued.append("token-%d" % (len(self.issued) + 1))
            return self.issued[-1]

    def redact(self, text):
        for token in self.issued:
            text = text.replace(token, "[REDACTED]")
        return text


def team_binding(mode, tokens=None, gateway=GATEWAY):
    everything = view(providers=tuple(sorted(provider_registry.TEAM_PROVIDERS)))
    record = next(r for r in team_connections.records(everything, CONSOLE).values() if r["mode"] == mode)
    return bind_team_generation(record, team_connections.health(everything, record), gateway, tokens or Tokens(),
                                "console|org-1|user-1")


class RegistryTests(unittest.TestCase):
    def test_team_modes_follow_the_contract_routes_and_their_personal_counterparts(self):
        self.assertEqual(TEAM_MODES, set(ROUTES))
        for mode, path in ROUTES.items():
            spec = SPECS[mode]
            personal = SPECS[spec["personal"]]
            with self.subTest(mode=mode):
                self.assertEqual(spec["gateway_path"], path)
                self.assertEqual((spec["group"], spec["provider"], spec["method"]), (personal["group"], personal["provider"], TEAM))
                self.assertNotIn("base_url", spec)
                self.assertNotIn("probe", spec, "the gateway refuses balance and catalog probes")
                self.assertNotIn("auth_header", spec, "the gateway takes a Bearer token")
                self.assertEqual(service_mode(mode), spec["personal"])
                self.assertEqual(provider_registry.team_mode(spec["personal"]), mode)
                self.assertEqual((billing_kind(mode), billing_label(mode)), ("API_KEY", "Angelo team"))
                if spec["runtime"] == "codex":
                    self.assertEqual(spec["env_key"], TEAM_ENV_KEY)
                    self.assertTrue(spec["models"])
                if spec["group"] == GENAI:
                    self.assertEqual(spec["models"], personal["models"])
        self.assertEqual(len(provider_registry.MODE_BY_CONNECTION), len(SPECS), "(group, provider, method) stays unique")
        self.assertTrue(TEAM_MODES.isdisjoint(provider_registry.HTTP_MODES | provider_registry.EXTERNAL_CODEX_MODES))
        self.assertTrue({m for m in TEAM_MODES if SPECS[m]["group"] == CODING} <= provider_registry.CODING_MODES)

    def test_the_new_connection_form_never_offers_team(self):
        for group in (CODING, GENAI):
            for provider, _name, _description in provider_registry.provider_items(group):
                self.assertNotIn(TEAM, methods(group, provider))
        self.assertEqual(methods(GENAI, "OPENAI"), ("API_KEY",), "a one-method provider keeps its one method")


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ConnectionStore(self.folder.name)

    def test_the_store_refuses_team_connections_and_ignores_hand_written_ones(self):
        with self.assertRaisesRegex(ValueError, "never stored"):
            self.store.add("ANTHROPIC", TEAM)
        personal = self.store.add("ANTHROPIC", "API_KEY")
        raw = json.loads(self.store.path.read_text())
        raw["connections"].append(dict(personal, id="00000000-0000-4000-8000-000000000001", method=TEAM,
                                       mode="claude_team"))
        self.store.path.write_text(json.dumps(raw))
        self.assertEqual(list(ConnectionStore(self.folder.name).records), [personal["id"]])

    def test_virtual_records_are_never_written_by_any_save(self):
        records = team_connections.records(view(), CONSOLE)
        self.assertTrue(records)
        from angelo_agent import connection_access
        with (patch.object(connection_access, "_ui", lambda: SimpleNamespace(connections=lambda: self.store)),
              patch.object(connection_access, "_team", lambda: SimpleNamespace(records=lambda: dict(records),
                                                                               signed_in=lambda: True))):
            merged = connection_access.connection_records()
        self.assertTrue(set(records) <= set(merged))
        self.assertEqual(self.store.records, {}, "merging never touches the store")
        self.store.add("OPENAI", "API_KEY", group=GENAI)
        self.store.save()
        text = self.store.path.read_text()
        self.assertNotIn(TEAM, text)
        for identity in records:
            self.assertNotIn(identity, text)


class TeamRecordTests(unittest.TestCase):
    def test_ids_are_stable_per_console_org_person_and_mode(self):
        first = team_connections.records(view(), CONSOLE)
        self.assertEqual(first, team_connections.records(view(), CONSOLE))
        self.assertTrue(set(first).isdisjoint(team_connections.records(view(org_id="org-2"), CONSOLE)))
        self.assertTrue(set(first).isdisjoint(team_connections.records(view(user_id="user-2"), CONSOLE)))
        self.assertTrue(set(first).isdisjoint(team_connections.records(view(), "http://localhost:5280")))

    def test_only_offered_providers_appear_and_only_while_signed_in(self):
        records = team_connections.records(view(providers=("anthropic", "tripo")), CONSOLE)
        self.assertEqual(sorted(r["mode"] for r in records.values()), ["claude_team", "tripo_team"])
        record = next(r for r in records.values() if r["mode"] == "claude_team")
        self.assertEqual(record["name"], "Claude · Northwind Studio")
        self.assertEqual(set(record), {"id", "name", "provider", "method", "mode", "model", "reuse_native", "group",
                                       "native_profile"})
        self.assertEqual(team_connections.records(view(status="signed_out"), CONSOLE), {})
        self.assertEqual(team_connections.records(view(providers=None), CONSOLE), {})
        self.assertIn("Google Gemini", team_connections.missing_providers(view(providers=("anthropic",))))

    def test_reasons_seat_scope_provider_and_grant_key(self):
        records = {r["mode"]: r for r in team_connections.records(view(), CONSOLE).values()}
        claude, images = records["claude_team"], records["openai_images_team"]
        self.assertEqual(team_connections.status(view(), claude, True), "Available in chat")
        self.assertEqual(team_connections.status(view(), images, False), "Team key available")
        self.assertEqual(team_connections.unavailable_reason(view(), claude, False), "Preparing coding-agent access…")
        self.assertEqual(team_connections.unavailable_reason(view(seat=False), claude, True),
                         "You don't have a seat in Northwind Studio. Contact your admin for access.")
        self.assertIn("doesn't allow", team_connections.unavailable_reason(view(scopes=frozenset({"api"})), images, True))
        self.assertEqual(team_connections.unavailable_reason(view(providers=("openai",)), claude, True),
                         "Your team hasn't added a key for Anthropic. Contact your admin for access.")
        self.assertIsNone(team_connections.health(view(seat=False), images))
        self.assertIsNone(team_connections.health(view(), claude), "coding connections have no Studio health")
        health = team_connections.health(view(), images)
        self.assertEqual((health.state, health.verified), ("configured", False))
        self.assertEqual([m["id"] for m in health.models], [m["id"] for m in SPECS["openai_images_api"]["models"]])


class EndpointTests(unittest.TestCase):
    def test_origins_default_and_are_validated(self):
        self.assertEqual(team_endpoints.console_origin({}).url, CONSOLE)
        self.assertEqual(team_endpoints.gateway_origin({}).url, GATEWAY)
        dev = {"ANGELO_CONSOLE_URL": "http://localhost:5280", "ANGELO_GATEWAY_URL": "http://127.0.0.1:8380/"}
        self.assertEqual(team_endpoints.console_origin(dev).url, "http://localhost:5280")
        self.assertEqual(team_endpoints.gateway_origin(dev).url, "http://127.0.0.1:8380")
        self.assertEqual(team_endpoints.gateway_origin({"ANGELO_GATEWAY_URL": "http://[::1]:8380"}).url, "http://[::1]:8380")
        for value in ("http://gateway.angelo.studio", "https://gateway.angelo.studio:8443", "https://user@x.test",
                      "https://x.test/path", "https://x.test/?q=1", "ftp://x.test", "https://", "http://10.0.0.5:8380"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "ANGELO_GATEWAY_URL must be an https"):
                team_endpoints.gateway_origin({"ANGELO_GATEWAY_URL": value})
        self.assertEqual(team_endpoints.gateway_provider(GATEWAY + "/v1/tripo/v3/tasks/x", {}), "tripo")
        self.assertEqual(team_endpoints.gateway_provider("https://api.tripo3d.ai/v3/tasks/x", {}), "")
        self.assertIsNone(team_endpoints.gateway_for_url(GATEWAY + "/v1/x", {"ANGELO_GATEWAY_URL": "nonsense"}))


class ChildEnvironmentTests(unittest.TestCase):
    INHERITED = {"ANTHROPIC_BASE_URL": "https://elsewhere.test", "ANTHROPIC_API_KEY": "inherited",
                 "ANGELO_TEAM_GRANT_KEY": "inherited", "SENSEI_TEAM_GRANT_KEY": "inherited", "PATH": "/usr/bin"}

    def test_personal_connections_still_strip_inherited_endpoints(self):
        with patch.dict(os.environ, self.INHERITED, clear=True):
            for mode in ("claude_api", "claude_subscription", "codex_subscription", "grok_api"):
                env = child_environment(mode, "personal-key")
                self.assertNotIn("ANTHROPIC_BASE_URL", env)
                self.assertFalse({"ANGELO_TEAM_GRANT_KEY", "SENSEI_TEAM_GRANT_KEY"} & set(env))
            self.assertEqual(child_environment("claude_api", "personal-key")["ANTHROPIC_API_KEY"], "personal-key")

    def test_a_team_claude_connection_gets_its_own_gateway_route_and_grant_key(self):
        with patch.dict(os.environ, self.INHERITED, clear=True):
            env = child_environment("claude_team", GRANT_KEY)
        self.assertEqual(env["ANTHROPIC_BASE_URL"], GATEWAY + "/v1/anthropic")
        self.assertEqual(env["ANTHROPIC_API_KEY"], GRANT_KEY)
        self.assertNotIn("ANGELO_TEAM_GRANT_KEY", env)
        with patch.dict(os.environ, dict(self.INHERITED, ANGELO_GATEWAY_URL="http://localhost:8380"), clear=True):
            self.assertEqual(child_environment("claude_team", GRANT_KEY)["ANTHROPIC_BASE_URL"],
                             "http://localhost:8380/v1/anthropic")
        with patch.dict(os.environ, {"ANGELO_GATEWAY_URL": "http://evil.test"}, clear=True):
            with self.assertRaisesRegex(RpcError, "ANGELO_GATEWAY_URL"):
                child_environment("claude_team", GRANT_KEY)

    def test_every_claude_mode_launches_alike_with_its_web_tools(self):
        # Bare mode would leave only shell and file tools, so WebSearch and WebFetch would be gone.
        from angelo_agent.providers import claude_session
        launches = {mode: claude_session("claude", SimpleNamespace(turn_options=None, state={"provider": mode, "options": {}}))
                    for mode in ("claude_team", "claude_api", "claude_subscription")}
        for mode, (command, initialize) in launches.items():
            with self.subTest(mode=mode):
                self.assertNotIn("--bare", command)
                self.assertEqual(command[command.index("--tools") + 1], "WebSearch,WebFetch")
                self.assertEqual((command, initialize), launches["claude_subscription"])

    def test_a_key_or_team_claude_launch_needs_its_own_angelo_profile(self):
        from angelo_agent.providers import profile_environment
        from angelo_agent.runtime_environment import require_angelo_profile
        api = "1b6f3c55-8d0e-4a8e-9b51-7f1f0a3c2d11"
        team = team_connections.connection_id(CONSOLE, "org-1", "user-1", "claude_team")
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            for mode, identity in (("claude_api", api), ("claude_team", team)):
                with self.subTest(mode=mode):
                    env = profile_environment(child_environment(mode, GRANT_KEY), mode, state, False, identity)
                    self.assertEqual(Path(env["CLAUDE_CONFIG_DIR"]), Path(state) / "claude-profile" / identity)
                    require_angelo_profile(mode, env, identity)
                    # A chat without a connection record (ui.connection_settings with record=None) gets no profile.
                    unbound = profile_environment(child_environment(mode, GRANT_KEY), mode, state, True, None)
                    self.assertNotIn("CLAUDE_CONFIG_DIR", unbound)
                    refused = [(unbound, None), (env, None), (env, "2c7e4d66-9e1f-4b9f-8c62-8a2a1b4d3e22"),
                               (dict(env, CLAUDE_CONFIG_DIR=str(Path.home() / ".claude")), identity),
                               (dict(env, CLAUDE_CONFIG_DIR="claude-profile/" + identity), identity),
                               (env, "not-a-connection-id")]
                    for launch_env, connection in refused:
                        with self.assertRaisesRegex(RpcError, "no Angelo profile of its own"):
                            require_angelo_profile(mode, launch_env, connection)
                    # Another sign-in's variable that reached the launch, under any spelling, is refused too.
                    for name in ("CLAUDE_SECURESTORAGE_CONFIG_DIR", "claude_code_oauth_token"):
                        with self.assertRaisesRegex(RpcError, "another sign-in's variables"):
                            require_angelo_profile(mode, dict(env, **{name: ""}), identity)
            require_angelo_profile("claude_subscription", {}, None)  # Its own sign-in is that connection's point.

    def test_a_claude_child_never_inherits_a_host_sign_in_or_bare_mode(self):
        # Set by an enclosing Claude Code session (Blender started from one), or left in a shell.
        inherited = {"CLAUDE_SECURESTORAGE_CONFIG_DIR": "", "CLAUDE_CODE_HOST_CREDS_FILE": "/tmp/creds",
                     "CLAUDE_CODE_HOST_AUTH_ENV_VAR": "HOST_TOKEN", "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST": "1",
                     "CLAUDE_CODE_HOST_GATEWAY_LINEAGE": "1", "CLAUDE_CODE_SIMPLE": "1", "PATH": "/usr/bin"}
        with patch.dict(os.environ, inherited, clear=True):
            for mode in ("claude_api", "claude_team", "claude_subscription"):
                with self.subTest(mode=mode):
                    self.assertEqual(set(child_environment(mode, GRANT_KEY)) & set(inherited), {"PATH"})

    def test_a_claude_session_without_web_search_is_told_so(self):
        from angelo_agent.claude_text import NO_WEB_SEARCH   # A Claude session reads the Claude per-model text.
        from angelo_agent.providers import claude_session
        state = {"provider": "claude_team", "options": {}}
        instructions = {tools: "".join(claude_session("claude", SimpleNamespace(turn_options=None, state=state, native_tools=tools))[1]
                                         ["request"]["systemPrompt"]) for tools in (("WebSearch", "WebFetch"), ("WebFetch",), ())}
        self.assertIn("Use native web search", instructions[("WebSearch", "WebFetch")])
        self.assertNotIn(NO_WEB_SEARCH, instructions[("WebSearch", "WebFetch")])
        self.assertIn(NO_WEB_SEARCH, instructions[("WebFetch",)])
        self.assertIn(NO_WEB_SEARCH, instructions[()])
        own = claude_session("claude", SimpleNamespace(turn_options=None, state=state, native_tools=(),
                                                       base_instructions="Return only the prompt."))[1]
        self.assertTrue("".join(own["request"]["systemPrompt"]).startswith("Return only the prompt."))
        self.assertNotIn(NO_WEB_SEARCH, "".join(own["request"]["systemPrompt"]))

    def test_codex_team_modes_use_a_gateway_model_provider_with_the_key_only_in_the_environment(self):
        # Windows finds the home folder only through its profile variables, so a cleared environment keeps them.
        profile = {key: value for key, value in os.environ.items() if key.upper() in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT")}
        decoding = patch.object(gateway_capabilities, "request_encodings", return_value=frozenset({"gzip", "zstd"}))
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, dict(profile, PATH="/usr/bin"), clear=True), \
                decoding:
            for mode in sorted(m for m in TEAM_MODES if SPECS[m]["runtime"] == "codex"):
                identity = team_connections.connection_id(CONSOLE, "org-1", "user-1", mode)
                command, env = codex_command("codex", mode, state, reuse_login=False, connection_id=identity,
                                             api_key=GRANT_KEY)
                pairs = dict(item.split("=", 1) for item in command[command.index("-c"):][1::2])
                with self.subTest(mode=mode):
                    self.assertEqual(pairs["model_provider"], json.dumps(mode))
                    self.assertEqual(json.loads(pairs[f"model_providers.{mode}.base_url"]), GATEWAY + ROUTES[mode])
                    self.assertEqual(json.loads(pairs[f"model_providers.{mode}.env_key"]), TEAM_ENV_KEY)
                    self.assertEqual(pairs[f"model_providers.{mode}.requires_openai_auth"], "false")
                    # The gateway said it decodes zstd bodies, so the bundled Codex compresses them.
                    self.assertEqual(json.loads(pairs[f"model_providers.{mode}.capabilities.request_compression"]), "zstd")
                    self.assertEqual(env[TEAM_ENV_KEY], GRANT_KEY)
                    self.assertNotIn(GRANT_KEY, " ".join(command))
                    self.assertTrue(env["CODEX_HOME"].endswith(identity), "an isolated Codex profile")
                    self.assertNotIn("cli_auth_credentials_store", pairs)
            self.assertEqual(json.loads(pairs_for_codex_team(state)["model"]), "gpt-6.1-sol")

    def test_every_codex_launch_opens_without_a_date_and_team_routes_keep_zstd(self):
        # The date-free opening and the team route's zstd live in runtime_launch.codex_command, the one launch
        # path, so every Codex launch gets both.
        profile = {key: value for key, value in os.environ.items() if key.upper() in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT")}
        decoding = patch.object(gateway_capabilities, "request_encodings", return_value=frozenset({"zstd"}))
        team = sorted(m for m in TEAM_MODES if SPECS[m]["runtime"] == "codex")
        with tempfile.TemporaryDirectory() as state, patch.dict(os.environ, dict(profile, PATH="/usr/bin"), clear=True), \
                decoding:
            for mode in ["codex_subscription", "codex_api"] + team:
                identity = team_connections.connection_id(CONSOLE, "org-1", "user-1", mode) if mode in team else None
                command, _env = codex_command("codex", mode, state, reuse_login=False, connection_id=identity,
                                              api_key=GRANT_KEY if mode in team else "")
                pairs = dict(item.split("=", 1) for item in command[command.index("-c"):][1::2])
                with self.subTest(mode=mode):
                    self.assertEqual(pairs["include_environment_context_time"], "false")
                    self.assertEqual(pairs["agents.enabled"], "false")   # Codex's own sub-agents and their mode note.
                    self.assertEqual(f"model_providers.{mode}.capabilities.request_compression" in pairs, mode in team)

    def test_a_codex_team_catalog_is_pinned_and_never_probed(self):
        controller = SimpleNamespace(state={"provider": "grok_team"}, api_key=GRANT_KEY)
        with patch("angelo_agent.providers.check_connection", side_effect=AssertionError("probed")):
            models = CodexProvider(controller, {}).connect(catalog_only=True)
            self.assertEqual([m["id"] for m in models], ["grok-4.6"])
            controller.api_key = ""
            with self.assertRaisesRegex(RpcError, "Sign in to your Angelo team"):
                CodexProvider(controller, {}).connect(catalog_only=True)


class ClaudeKeyLaunchTests(ClaudeAdapterFixture, unittest.TestCase):
    def test_a_key_or_team_launch_without_its_profile_runs_nothing(self):
        for mode in ("claude_api", "claude_team"):
            with self.subTest(mode=mode):
                self.host.state["provider"] = mode
                with self.assertRaisesRegex(RpcError, "no Angelo profile of its own"):
                    self.start(None)
                self.run_mock.assert_not_called()  # Not even the version probe.
                self.popen_mock.assert_not_called()

    def test_a_key_or_team_launch_in_its_profile_starts_without_bare(self):
        from angelo_agent.providers import profile_environment
        for mode, identity in (("claude_api", "1b6f3c55-8d0e-4a8e-9b51-7f1f0a3c2d11"),
                               ("claude_team", team_connections.connection_id(CONSOLE, "org-1", "user-1", "claude_team"))):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as state:
                self.host.state["provider"] = mode
                self.provider.settings = {"connection_id": identity}  # As ui.connection_settings(record=...) sets it.
                self.provider.env = profile_environment({"ANTHROPIC_API_KEY": GRANT_KEY}, mode, state, False, identity)
                self.popen_mock.reset_mock()
                self.popen_mock.return_value = NativeInitializeFixture(
                    on_user=lambda process, _: process.stdout.emit(completed()))
                self.assertIsNotNone(self.start(None))
                command, env = self.popen_mock.call_args.args[0], self.popen_mock.call_args.kwargs["env"]
                self.assertNotIn("--bare", command)
                self.assertEqual(Path(env["CLAUDE_CONFIG_DIR"]), Path(state) / "claude-profile" / identity)
                self.provider.close()


def pairs_for_codex_team(state):
    command, _env = codex_command("codex", "codex_team", state, reuse_login=False,
                                  connection_id=team_connections.connection_id(CONSOLE, "o", "u", "codex_team"),
                                  api_key=GRANT_KEY)
    return dict(item.split("=", 1) for item in command[command.index("-c"):][1::2])


class BindingTests(unittest.TestCase):
    def test_each_team_service_resolves_to_its_gateway_route_with_a_fresh_bearer_token(self):
        operations = {"openai_images_team": ("image", "/images/generations"), "gemini_images_team": ("image", "/interactions"),
                      "seedream_team": ("image", "/images/generations"),
                      "tripo_team": ("text_to_3d", "/generation/text-to-model"),
                      "meshy_team": ("image_to_3d", "/openapi/v1/image-to-3d")}
        for mode, (operation, route) in operations.items():
            tokens = Tokens()
            binding = team_binding(mode, tokens)
            with self.subTest(mode=mode):
                self.assertTrue(binding.team)
                self.assertEqual(binding.service, SPECS[mode]["personal"])
                model = binding.model_ids[0]
                self.assertEqual(binding.endpoint(operation, model), GATEWAY + ROUTES[mode] + route)
                self.assertEqual(binding.request_headers(), {"Authorization": "Bearer token-1"})
                renewed = binding.request_headers(stale={"Authorization": "Bearer token-1"})
                self.assertEqual(renewed, {"Authorization": "Bearer token-2"})
                self.assertEqual(binding.redact("x token-1 token-2"), "x [REDACTED] [REDACTED]")
                self.assertNotIn("token", repr(binding))
        first, second = team_binding("tripo_team"), team_binding("tripo_team")
        self.assertEqual(first.scope_digest(), second.scope_digest(), "a new token is not a new approval scope")

    def test_personal_bindings_are_unchanged_and_kinds_do_not_cross(self):
        personal = GenerationBinding("p", "openai_images_api", ("gpt-image-2.5-flare",), True, "personal-key-1234")
        self.assertEqual(personal.endpoint("image", "gpt-image-2.5-flare"), "https://api.openai.com/v1/images/generations")
        self.assertEqual(personal.request_headers(), {"Authorization": "Bearer personal-key-1234"})
        gemini = GenerationBinding("p", "gemini_images_api", ("gemini-3-pro-image",), True, "personal-key-1234")
        self.assertEqual(gemini.request_headers(), {"x-goog-api-key": "personal-key-1234"})
        record = next(r for r in team_connections.records(view(), CONSOLE).values() if r["mode"] == "claude_team")
        with self.assertRaises(ValueError):
            bind_team_generation(record, None, GATEWAY, Tokens(), "s")
        with self.assertRaises(ValueError):
            bind_team_generation({"id": "x", "mode": "openai_images_api"}, None, GATEWAY, Tokens(), "s")


class RenewalTests(unittest.TestCase):
    """A renewed grant key restarts the runtime at the next request and resumes the same native session."""

    def test_a_new_grant_key_keeps_the_team_chats_native_context(self):
        from angelo_agent.controller import Controller
        with tempfile.TemporaryDirectory() as folder:
            chat = Controller(folder)
            identity = team_connections.connection_id(CONSOLE, "org-1", "user-1", "claude_team")
            chat.select("claude_team", {"connection_id": identity}, "agk_" + "a" * 43, identity, [])
            chat.state["native_id"] = "claude-session-1"
            closed = []
            chat.provider = SimpleNamespace(close=lambda: closed.append(True))
            chat.select("claude_team", {"connection_id": identity}, "agk_" + "b" * 43, identity, [])
            self.assertIsNone(chat.provider, "the runtime started with the old key is closed")
            self.assertEqual(chat.state["native_id"], "claude-session-1", "and the next one resumes the session")
            self.assertEqual(chat.api_key, "agk_" + "b" * 43)
            personal = team_connections.connection_id(CONSOLE, "org-1", "user-1", "claude_api")
            chat.select("claude_api", {"connection_id": personal}, "sk-old", personal, [])
            chat.state["native_id"] = "personal-session"
            chat.select("claude_api", {"connection_id": personal}, "sk-new", personal, [])
            self.assertIsNone(chat.state["native_id"], "a personal API key change still starts fresh")


class ErrorMappingTests(unittest.TestCase):
    def body(self, code, **extra):
        return json.dumps({"type": "error", "error": dict({"type": "x", "code": code, "message": "m"}, **extra)}).encode()

    def test_gateway_codes_become_clear_messages(self):
        missing = gateway_error(409, self.body("NO_PROVIDER_KEY", provider="tripo"))
        self.assertEqual(missing.message, "Your team hasn't added a key for Tripo. Contact your admin for access.")
        self.assertIn("don't have a seat", gateway_error(403, self.body("FORBIDDEN")).message)
        limited = gateway_error(429, self.body("RATE_LIMITED", limit="x"), "42")
        self.assertEqual((limited.code, limited.retry_after), ("RATE_LIMITED", 42))
        self.assertIn("try again in 42 s", limited.message)
        self.assertIn("try again later", gateway_error(429, self.body("RATE_LIMITED"), "soon").message)
        self.assertIsInstance(gateway_error(401, self.body("UNAUTHENTICATED")), SignInRequired)
        unavailable = gateway_error(503, self.body("UNAVAILABLE"))
        self.assertNotIsInstance(unavailable, SignInRequired, "a 503 is retryable, never a sign-out")
        for code in ("NOT_FOUND", "PAYLOAD_TOO_LARGE", "UPSTREAM_UNAVAILABLE", "UPSTREAM_TIMEOUT"):
            self.assertIsInstance(gateway_error(500, self.body(code)), TeamError)

    def test_provider_errors_pass_through_and_console_shapes_are_read(self):
        anthropic = json.dumps({"type": "error", "error": {"type": "authentication_error", "message": "bad key"}})
        openai = json.dumps({"error": {"message": "bad", "type": "invalid_request_error", "code": "invalid_api_key"}})
        self.assertIsNone(gateway_error(401, anthropic.encode()))
        self.assertIsNone(gateway_error(401, openai.encode()))
        self.assertIsNone(gateway_error(502, b"<html>bad gateway</html>"))
        console = json.dumps({"error": {"code": "FORBIDDEN", "message": "no seat"}}).encode()
        self.assertIsNone(gateway_error(403, console))
        self.assertEqual(api_error(403, console).code, "FORBIDDEN")
        self.assertIsInstance(api_error(401, b"{}"), SignInRequired)
        self.assertEqual(api_error(429, b"{}", "7").retry_after, 7)
        console_404 = json.dumps({"error": {"code": "NOT_FOUND", "message": "Endpoint was not found."}}).encode()
        self.assertIn("console doesn't offer this request", api_error(404, console_404).message)
        self.assertEqual(gateway_error(404, self.body("NOT_FOUND", provider="meshy")).message,
                         "The Angelo gateway doesn't offer this Meshy operation.")
        self.assertIn("a key for this provider", gateway_error(409, self.body("NO_PROVIDER_KEY")).message)
        self.assertIsInstance(oauth_error(400, b'{"error": "invalid_grant"}'), SignInRequired)
        from angelo_agent.provider_errors import sanitize
        self.assertNotIn(GRANT_KEY, sanitize("echoed key " + GRANT_KEY), "grant keys are redacted like API keys")
        other = oauth_error(400, b'{"error": "invalid_request", "error_description": "Bearer abcdefghijklmnopqrstuvwxyz0123"}')
        self.assertNotIsInstance(other, SignInRequired)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123", other.message)


class FakeGateway:
    def __init__(self, answers):
        self.answers, self.seen = list(answers), []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                self.do_POST()

            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                gateway.seen.append((self.path, self.headers.get("Authorization")))
                status, headers, body = gateway.answers.pop(0)
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class GatewayCapabilityTests(unittest.TestCase):
    """Team Codex requests are compressed only when the gateway itself says it decodes them (gateway_capabilities)."""

    PROFILE = {key: value for key, value in os.environ.items()
               if key.upper() in ("USERPROFILE", "LOCALAPPDATA", "APPDATA", "SYSTEMROOT")}
    HEALTH = json.dumps({"ok": True, "release": "r1", "requestEncodings": ["gzip", "zstd"]}).encode()

    def setUp(self):
        gateway_capabilities.reset()
        self.addCleanup(gateway_capabilities.reset)
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)

    def gateway(self, answers):
        gateway = FakeGateway(answers)
        self.addCleanup(gateway.close)
        return gateway

    def pairs(self, mode, gateway_url, api_key=GRANT_KEY):
        with patch.dict(os.environ, dict(self.PROFILE, PATH="/usr/bin", ANGELO_GATEWAY_URL=gateway_url), clear=True):
            command, _env = codex_command("codex", mode, self.state.name, reuse_login=False,
                                          connection_id=team_connections.connection_id(CONSOLE, "o", "u", mode),
                                          api_key=api_key)
        return dict(item.split("=", 1) for item in command[command.index("-c"):][1::2])

    def test_a_gateway_that_names_zstd_turns_compression_on_after_one_keyless_request(self):
        gateway = self.gateway([(200, {"Content-Type": "application/json"}, self.HEALTH)])
        codex_team = sorted(m for m in TEAM_MODES if SPECS[m]["runtime"] == "codex")
        for mode in codex_team * 2:
            with self.subTest(mode=mode):
                pairs = self.pairs(mode, gateway.url)
                self.assertEqual(json.loads(pairs[f"model_providers.{mode}.capabilities.request_compression"]), "zstd")
        self.assertEqual(gateway.seen, [("/health", None)], "One request per gateway and process, never a key")

    def test_an_older_gateway_or_none_at_all_leaves_requests_uncompressed(self):
        answers = {"no field (a gateway before compression)": (200, {}, b'{"ok":true,"release":"r0"}'),
                   "gzip only": (200, {}, b'{"ok":true,"requestEncodings":["gzip"]}'),
                   "an error status": (503, {}, b'{"ready":false}'),
                   "not JSON": (200, {}, b"<html>"), "a wrong shape": (200, {}, b'{"requestEncodings":"zstd"}')}
        for name, answer in answers.items():
            with self.subTest(name):
                gateway_capabilities.reset()
                gateway = self.gateway([answer])
                self.assertNotIn("model_providers.codex_team.capabilities.request_compression",
                                 self.pairs("codex_team", gateway.url))
                self.assertEqual(gateway.seen, [("/health", None)])
        gateway_capabilities.reset()
        self.assertNotIn("model_providers.codex_team.capabilities.request_compression",
                         self.pairs("codex_team", "http://127.0.0.1:9"), "Nothing listening: off, never an error")

    def test_a_no_is_asked_again_later_and_a_yes_is_kept(self):
        now = [1000.0]
        old = self.gateway([(200, {}, b'{"ok":true}'), (200, {}, self.HEALTH)])
        origin = team_endpoints.gateway_origin({"ANGELO_GATEWAY_URL": old.url})
        clock = lambda: now[0]  # noqa: E731
        self.assertFalse(gateway_capabilities.accepts(origin, "zstd", clock=clock))
        now[0] += gateway_capabilities.RETRY_SECONDS - 1
        self.assertFalse(gateway_capabilities.accepts(origin, "zstd", clock=clock))
        self.assertEqual(len(old.seen), 1, "Remembered for a while: a connect never waits on it again")
        now[0] += 2
        self.assertTrue(gateway_capabilities.accepts(origin, "zstd", clock=clock), "The gateway was deployed since")
        now[0] += 10 * gateway_capabilities.RETRY_SECONDS
        self.assertTrue(gateway_capabilities.accepts(origin, "zstd", clock=clock))
        self.assertEqual(len(old.seen), 2)

    def test_concurrent_connects_share_one_request(self):
        gateway = self.gateway([(200, {}, self.HEALTH)])
        origin = team_endpoints.gateway_origin({"ANGELO_GATEWAY_URL": gateway.url})
        results = []
        workers = [threading.Thread(target=lambda: results.append(gateway_capabilities.accepts(origin, "zstd")))
                   for _ in range(6)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertEqual(results, [True] * 6)
        self.assertEqual(len(gateway.seen), 1)

    def test_personal_modes_never_ask_and_never_compress(self):
        gateway = self.gateway([(200, {}, self.HEALTH)])
        for mode in sorted(m for m in SPECS if SPECS[m]["runtime"] == "codex" and m not in TEAM_MODES
                           and SPECS[m].get("base_url")):
            with self.subTest(mode=mode):
                self.assertFalse(any(".capabilities." in key for key in self.pairs(mode, gateway.url, "personal-key")))
        self.assertEqual(gateway.seen, [])


class GatewayTransportTests(unittest.TestCase):
    def gateway(self, answers):
        gateway = FakeGateway(answers)
        self.addCleanup(gateway.close)
        return gateway

    def test_loopback_http_is_admitted_only_when_configured(self):
        gateway = self.gateway([(200, {}, b'{"ok": 1}')])
        url = gateway.url + "/v1/openai/v1/images/generations"
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(GenerationError, "public HTTPS"):
            request_bytes(url, body=b"{}", service=True)
        with patch.dict(os.environ, {"ANGELO_GATEWAY_URL": gateway.url}, clear=True):
            self.assertEqual(request_bytes(url, body=b"{}", headers={"Authorization": "Bearer t"}, service=True), b'{"ok": 1}')
            with self.assertRaisesRegex(GenerationError, "public HTTPS"):
                request_bytes(url, timeout=5)  # Downloads never go to a loopback host.
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(GenerationError, "not an admitted"):
            request_bytes("https://unlisted.example.test/v1/x", body=b"{}", service=True)

    def test_gateway_refusals_and_provider_refusals_are_told_apart(self):
        refusal = json.dumps({"type": "error", "error": {"type": "x", "code": "NO_PROVIDER_KEY", "provider": "openai",
                                                         "message": "m"}}).encode()
        provider = json.dumps({"error": {"message": "Incorrect API key", "type": "invalid_request_error"}}).encode()
        gateway = self.gateway([(409, {}, refusal), (401, {}, provider)])
        url = gateway.url + "/v1/openai/v1/images/generations"
        with patch.dict(os.environ, {"ANGELO_GATEWAY_URL": gateway.url}, clear=True):
            with self.assertRaises(GatewayRejection) as caught:
                request_bytes(url, body=b"{}", service=True)
            self.assertEqual(caught.exception.code, "NO_PROVIDER_KEY")
            self.assertIn("hasn't added a key for OpenAI", str(caught.exception))
            with self.assertRaisesRegex(GenerationError, "rejected your team's key; ask a team admin") as caught:
                request_bytes(url, body=b"{}", service=True)
            self.assertNotIsInstance(caught.exception, GatewayRejection)

    def test_generation_renews_the_token_once_after_unauthenticated(self):
        unauthenticated = (401, {}, json.dumps({"type": "error", "error": {"code": "UNAUTHENTICATED", "message": "x"}}).encode())
        image = (200, {}, json.dumps({"data": [{"b64_json": PNG}]}).encode())
        gateway = self.gateway([unauthenticated, image, unauthenticated, unauthenticated])
        tokens = Tokens()
        binding = team_binding("openai_images_team", tokens, gateway.url)
        with patch.dict(os.environ, {"ANGELO_GATEWAY_URL": gateway.url}, clear=True), \
                tempfile.TemporaryDirectory() as folder:
            result = generate(binding, "gpt-image-2.5-flare", "A cat", {}, [], Path(folder), lambda _: None,
                              threading.Event())
            self.assertEqual(result["status"], "ready")
            self.assertEqual([auth for _path, auth in gateway.seen], ["Bearer token-1", "Bearer token-2"])
            with self.assertRaisesRegex(GenerationError, "sign-in has expired"):
                generate(binding, "gpt-image-2.5-flare", "A cat", {}, [], Path(folder) / "none", lambda _: None,
                         threading.Event())
        self.assertEqual(gateway.seen[0][0], "/v1/openai/v1/images/generations")

    def test_a_rate_limited_poll_waits_and_continues_the_submitted_task(self):
        limited = GatewayRejection(TeamError("limited", code="RATE_LIMITED", status=429, retry_after=0))
        answers = [{"data": {"task_id": "task_1"}}, limited,
                   {"data": {"status": "success", "progress": 100, "output": {"model_url": "https://cdn.test/m.glb"}}}]
        calls = []

        def transport(url, body=None, headers=None, limit=0, timeout=0, service=False):
            calls.append((url, (headers or {}).get("Authorization")))
            if not service:
                payload = json.dumps({"asset": {"version": "2.0"}}).encode()
                payload += b" " * (-len(payload) % 4)
                return b"glTF" + (2).to_bytes(4, "little") + (20 + len(payload)).to_bytes(4, "little") + \
                    len(payload).to_bytes(4, "little") + b"JSON" + payload
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return json.dumps(answer).encode()
        binding = team_binding("tripo_team")
        with tempfile.TemporaryDirectory() as folder:
            result = generate(binding, binding.model_ids[0], "A chair", {"polycount": 1000}, [], Path(folder),
                              lambda _: None, threading.Event(), transport, poll_interval=0)
        self.assertEqual(result["files"], ["model.glb"])
        self.assertEqual([url for url, _ in calls[:3]], [GATEWAY + "/v1/tripo/v3/generation/text-to-model",
                                                          GATEWAY + "/v1/tripo/v3/tasks/task_1",
                                                          GATEWAY + "/v1/tripo/v3/tasks/task_1"])
        self.assertIsNone(calls[-1][1], "the CDN download carries no credential")

    def test_ai_enhance_uses_the_team_gemini_route(self):
        seen = []

        def transport(url, body=None, headers=None, limit=0, timeout=0, service=False):
            seen.append((url, headers))
            return json.dumps({"steps": [{"type": "model_output", "content": [{"type": "text", "text": "Better"}]}]}).encode()
        binding = team_binding("gemini_images_team")
        self.assertEqual(enhance_gemini(binding, "gemini-3.8-flash", "a cat", "IMAGE", "Nano Banana", transport), "Better")
        self.assertEqual(seen[0][0], GATEWAY + "/v1/google/v1beta/interactions")
        self.assertEqual(seen[0][1]["Authorization"], "Bearer token-1")
        self.assertNotIn("x-goog-api-key", seen[0][1])


if __name__ == "__main__":
    unittest.main()
