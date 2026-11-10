# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A team runtime reaches only its gateway; WebFetch goes through it; an organization's web-search switch is heeded.

Contract: doc/angelo/ai_providers.rst, "Team connections reach only the gateway" and "Web search turned off by the
organization". ``team_network_probe.py`` runs the real Claude Code and Codex through the same fence.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import api_connections, team_network, web_fetch_hook, web_search_access  # noqa: E402
from angelo_agent.claude_transport import claude_arguments, claude_session, launch_values  # noqa: E402
from angelo_agent.runtime_launch import child_environment  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from platform_env import essential_environ  # noqa: E402

GRANT_KEY = "agk_" + "k" * 43
GATEWAY = "https://gateway.example.test"
TICKET = "/v1/web/pages/" + "p" * 40 + "." + "s" * 43
PROXIES = {"HTTPS_PROXY": "http://proxy:3128", "https_proxy": "http://proxy:3128", "Http_Proxy": "http://proxy:3128",
           "all_proxy": "socks5://proxy:1080", "NO_PROXY": "*", "no_proxy": "localhost"}


class FenceTests(unittest.TestCase):
    def test_every_proxy_spelling_is_replaced_by_the_refusing_proxy_and_only_the_gateway_is_exempt(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                env = team_network.apply(dict(PROXIES, PATH="/usr/bin"), runtime, "gateway.example.test")
                proxies = {k: v for k, v in env.items() if k.upper() in {"HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"}}
                self.assertEqual(set(proxies.values()), {team_network.REFUSING_PROXY})
                proxies = {k: v for k, v in env.items() if k.upper().endswith("_PROXY") and k.upper() != "NO_PROXY"}
                self.assertEqual(set(proxies.values()), {team_network.REFUSING_PROXY})
                self.assertEqual(set(proxies), {"HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY",
                                                "all_proxy", "GRPC_PROXY", "grpc_proxy"})   # "Http_Proxy" is gone too.
                self.assertEqual({k: v for k, v in env.items() if k.upper() == "NO_PROXY"},
                                 {"NO_PROXY": "gateway.example.test", "no_proxy": "gateway.example.test"})
                self.assertEqual(env["PATH"], "/usr/bin")
                self.assertEqual(env.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"),
                                 "1" if runtime == "claude" else None)
                self.assertEqual(team_network.HOOK_SCRIPT in env, runtime == "claude")
        self.assertTrue(Path(team_network.apply({}, "claude", "g.example.test")[team_network.HOOK_SCRIPT]).is_file())
        # Settings that open connections of their own or name another proxy never reach a team child.
        opened = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4317", "otel_traces_exporter": "otlp",
                  "CLAUDE_CODE_ENABLE_TELEMETRY": "1", "no_grpc_proxy": "*", "npm_config_https_proxy": "http://p:1",
                  "NPM_CONFIG_NO_PROXY": "*", "LANG": "en_US.UTF-8"}
        env = team_network.apply(dict(opened), "codex", "g.example.test")
        self.assertEqual({k for k in opened if k in env}, {"LANG"})
        with self.assertRaises(ValueError):
            team_network.apply({}, "claude", "")

    def test_team_children_are_fenced_and_personal_children_keep_the_persons_network(self):
        with patch.dict(os.environ, dict(PROXIES, PATH="/usr/bin", ANGELO_GATEWAY_URL=GATEWAY), clear=True):
            for mode in ("claude_team", "codex_team"):
                with self.subTest(mode=mode):
                    env = child_environment(mode, GRANT_KEY)
                    self.assertEqual(env["HTTPS_PROXY"], team_network.REFUSING_PROXY)
                    self.assertEqual(env["NO_PROXY"], "gateway.example.test")
            for mode in ("claude_api", "claude_subscription", "codex_subscription"):
                with self.subTest(mode=mode):
                    env = child_environment(mode, "" if mode.endswith("subscription") else "sk-test")
                    self.assertEqual({k: env[k] for k in PROXIES if k in env}, PROXIES if os.name != "nt" else
                                     {k: env[k] for k in PROXIES if k in env})
                    self.assertNotIn("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", env)
                    self.assertNotIn(team_network.HOOK_SCRIPT, env)

    def test_only_a_team_claude_session_skips_the_direct_preflight_and_hooks_webfetch(self):
        for mode, gateway in (("claude_team", True), ("claude_api", False), ("claude_subscription", False)):
            with self.subTest(mode=mode):
                command, _ = claude_session("claude", SimpleNamespace(turn_options=None,
                                                                       state={"provider": mode, "options": {}}))
                settings = json.loads(command[command.index("--settings") + 1])
                self.assertEqual(settings.get("skipWebFetchPreflight", False), gateway)
                self.assertEqual(bool(settings["hooks"]), gateway)
                self.assertIs(launch_values(command[1:])["gateway"], gateway)

    def test_an_administrators_hook_lock_keeps_claude_codes_own_preflight(self):
        from angelo_agent import claude_managed
        self.assertEqual(team_network.settings(True, hooks_blocked=True), {})
        self.assertIn("skipWebFetchPreflight", team_network.settings(True, hooks_blocked=False))
        self.assertEqual(team_network.settings(False, hooks_blocked=False), {})
        for found, blocked in (([], False), ([{"env": {}}], False), ([{"disableAllHooks": True}], True),
                               ([{}, {"allowManagedHooksOnly": True}], True), ([{"disableAllHooks": "true"}], False)):
            self.assertIs(claude_managed.hooks_blocked(found), blocked, found)

    def test_a_hooked_worker_launch_is_accepted_whatever_this_host_reads_of_the_policy(self):
        values = {"tools": "WebSearch,WebFetch", "direct": False, "fast": False, "model": "", "effort": "low",
                  "thinking": False, "resume": "", "gateway": True, "hooks_blocked": False}
        arguments = claude_arguments("claude", values)[1:]
        with patch("angelo_agent.claude_managed.hooks_blocked", return_value=True):
            self.assertIs(launch_values(arguments)["gateway"], True)
            # The launch the host then runs follows its own reading: Claude Code's preflight, no hook.
            rebuilt = dict(launch_values(arguments))
            rebuilt.pop("hooks_blocked")
            settings = json.loads(claude_arguments("claude", rebuilt)[claude_arguments("claude", rebuilt).index(
                "--settings") + 1])
            self.assertNotIn("skipWebFetchPreflight", settings)

    def test_an_administrators_proxy_override_is_named_for_the_providers_page(self):
        from angelo_agent import claude_managed
        found = [{"env": {"HTTPS_PROXY": "http://corp:8080", "no_proxy": "corp.example", "LANG": "C"}},
                 {"env": {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector"}}, {"env": "not a dict"}, {}]
        self.assertEqual(claude_managed.env_overrides(found), ["HTTPS_PROXY", "NO_PROXY", "OTEL_EXPORTER_OTLP_ENDPOINT"])
        self.assertEqual(claude_managed.env_overrides([{"env": {"PATH": "/x"}}]), [])
        from angelo_agent.providers_page_model import CLAUDE_ROUTE_LINE, CLAUDE_ROUTE_TITLE
        template = (ROOT / "locale/angelo/angelo.pot").read_text(encoding="utf-8")
        for message in (CLAUDE_ROUTE_TITLE, CLAUDE_ROUTE_LINE):
            self.assertIn('msgid "%s"' % message.replace('"', '\\"'), template)

    def test_a_worker_cannot_drop_or_add_the_gateway_hook(self):
        values = {"tools": "WebSearch,WebFetch", "direct": False, "fast": False, "model": "", "effort": "low",
                  "thinking": False, "resume": ""}
        from angelo_agent.runtime_plan import Plans
        plans = Plans()
        plans._recorded = lambda session: set()
        for mode, asked, expected in (("claude_team", False, True), ("claude_api", True, False)):
            with self.subTest(mode=mode), patch.dict(os.environ, {"PATH": "/usr/bin", "ANGELO_GATEWAY_URL": GATEWAY},
                                                     clear=True), \
                    patch("angelo_agent.runtime_environment.require_angelo_profile"):
                chat = SimpleNamespace(mode=mode, api_key=GRANT_KEY, store_directory="/tmp", settings={})
                arguments = claude_arguments("claude", dict(values, gateway=asked))[1:]
                command, _env = plans._claude({"arguments": arguments}, chat, {}, "claude")
                self.assertIs(launch_values(command[1:])["gateway"], expected)


class HookTests(unittest.TestCase):
    ENV = {"ANTHROPIC_BASE_URL": GATEWAY + "/v1/anthropic", "ANTHROPIC_API_KEY": GRANT_KEY}
    INPUT = {"tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a?b=1", "prompt": "Title?"}}

    def answer(self, status, body, calls=None):
        def transport(url, headers, data, timeout):
            if calls is not None:
                calls.append(("POST", url, headers, json.loads(data)))
            return status, json.dumps(body).encode() if not isinstance(body, bytes) else body
        return transport

    def test_a_ticket_replaces_the_url_on_the_gateway_origin_and_keeps_the_prompt(self):
        calls = []
        answer = web_fetch_hook.decision(self.INPUT, self.ENV, self.answer(200, {"path": TICKET}, calls))
        output = answer["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow")
        self.assertEqual(output["updatedInput"], {"url": GATEWAY + TICKET, "prompt": "Title?"})
        method, url, headers, body = calls[0]
        self.assertEqual((method, url, body), ("POST", GATEWAY + "/v1/web/tickets", {"url": "https://example.com/a?b=1"}))
        self.assertEqual(headers["x-api-key"], GRANT_KEY)

    def test_other_tools_are_left_alone(self):
        self.assertIsNone(web_fetch_hook.decision({"tool_name": "WebSearch", "tool_input": {}}, self.ENV,
                                                  self.answer(500, {})))

    def test_every_failure_denies_and_nothing_falls_back_to_a_direct_fetch(self):
        blocked = {"error": {"code": "WEB_FETCH_BLOCKED", "message": "Anthropic's domain check does not allow it."}}
        cases = {
            "blocked": (self.answer(403, blocked), "Anthropic's domain check does not allow it."),
            "server error": (self.answer(503, {"error": {"code": "WEB_FETCH_UNAVAILABLE", "message": "Off."}}),
                             "WEB_FETCH_UNAVAILABLE"),
            "another origin": (self.answer(200, {"path": "https://evil.example/v1/web/pages/x"}), "HTTP 200"),
            "a path that is not a ticket": (self.answer(200, {"path": "/v1/anthropic/v1/messages"}), "HTTP 200"),
            "not JSON": (self.answer(200, b"<html>"), "HTTP 200"),
        }
        for name, (transport, reason) in cases.items():
            with self.subTest(name):
                output = web_fetch_hook.decision(self.INPUT, self.ENV, transport)["hookSpecificOutput"]
                self.assertEqual(output["permissionDecision"], "deny")
                self.assertIn(reason, output["permissionDecisionReason"])
        for error in (web_fetch_hook.Unreachable("x"), ConnectionRefusedError("y")):
            def unreachable(*args, error=error):
                raise error
            output = web_fetch_hook.decision(self.INPUT, self.ENV, unreachable)["hookSpecificOutput"]
            self.assertEqual(output["permissionDecision"], "deny")
        for env in ({}, {"ANTHROPIC_BASE_URL": GATEWAY + "/v1/anthropic"}, {"ANTHROPIC_API_KEY": GRANT_KEY}):
            self.assertEqual(web_fetch_hook.decision(self.INPUT, env, self.answer(200, {"path": TICKET}))
                             ["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(web_fetch_hook.decision({"tool_name": "WebFetch", "tool_input": {}}, self.ENV,
                                                 self.answer(200, {"path": TICKET}))
                         ["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_any_failure_still_answers_with_a_denial(self):
        with patch.object(web_fetch_hook, "decision", side_effect=RuntimeError("boom")), \
                patch("sys.stdin", SimpleNamespace(buffer=SimpleNamespace(read=lambda size: json.dumps(self.INPUT).encode()))), \
                patch("sys.stdout") as out:
            self.assertEqual(web_fetch_hook.main(), 0)
        written = json.loads("".join(call.args[0] for call in out.write.call_args_list))
        self.assertEqual(written["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_the_hook_loads_none_of_angelos_heavier_modules(self):
        # It runs once per WebFetch call: only the standard library and the distrust list (loaded by file).
        code = ("import json, runpy, sys; runpy.run_path(%r, run_name='hook'); "
                "print(json.dumps(sorted(m for m in sys.modules if m.startswith('angelo'))))"
                % str(ROOT / "scripts/addons_core/angelo_agent/web_fetch_hook.py"))
        loaded = json.loads(subprocess.run([sys.executable, "-I", "-S", "-c", code], capture_output=True, text=True,
                                           timeout=60).stdout)
        self.assertEqual(loaded, [])
        context = web_fetch_hook._tls_context({})
        self.assertIsNot(context.sslsocket_class, __import__("ssl").SSLSocket)   # The distrust check is on.
        self.assertEqual([web_fetch_hook._gateway_origin(u) for u in (
            "https://gateway.example.test/v1/anthropic", "http://127.0.0.1:8080/v1/anthropic",
            "http://gateway.example.test/v1/anthropic", "https://user:pw@gateway.example.test/v1",
            "https://gateway.example.test:8443/v1/anthropic", "", None)],
            ["https://gateway.example.test", "http://127.0.0.1:8080", None, None, None, None, None])

    def test_the_script_runs_isolated_as_claude_code_runs_it(self):
        seen = []

        class Gateway(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                seen.append((self.path, self.headers.get("x-api-key"), self.rfile.read(int(self.headers["content-length"]))))
                data = json.dumps({"path": TICKET}).encode()
                self.send_response(200)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        origin = "http://127.0.0.1:%d" % server.server_address[1]   # http only to loopback (team_http).
        # The variables every real process has stay (child_environment inherits them): Winsock needs SYSTEMROOT.
        env = team_network.apply(essential_environ(PATH=os.environ.get("PATH", ""), ANTHROPIC_API_KEY=GRANT_KEY,
                                                   ANTHROPIC_BASE_URL=origin + "/v1/anthropic"), "claude", "127.0.0.1")
        # Claude Code writes UTF-8 whatever the system's code page; a prompt in any language comes back unchanged.
        hook_input = dict(self.INPUT, tool_input=dict(self.INPUT["tool_input"], prompt="見出しは? Überschrift?"))
        result = subprocess.run([sys.executable, *team_network.HOOK_COMMAND.split()[1:-1], env[team_network.HOOK_SCRIPT]],
                                input=json.dumps(hook_input, ensure_ascii=False).encode("utf-8"),
                                capture_output=True, env=env, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow", output.get("permissionDecisionReason"))
        self.assertEqual(output["updatedInput"]["url"], origin + TICKET)
        self.assertEqual(output["updatedInput"]["prompt"], "見出しは? Überschrift?")
        self.assertEqual(seen[0][:2], ("/v1/web/tickets", GRANT_KEY))


class WebSearchSwitchTests(unittest.TestCase):
    TEXT = "API Error: 400 web search is not enabled for this organization"

    def setUp(self):
        web_search_access._off.clear()
        self.addCleanup(web_search_access._off.clear)

    def test_the_apis_refusal_is_recognised_and_nothing_else_is(self):
        for content in (self.TEXT, "API Error: 403 Web search is not enabled for your organisation.",
                        [{"type": "text", "text": self.TEXT}]):
            self.assertTrue(web_search_access.turned_off(content, True), content)
        quoted = "A forum post: \"API Error: 400 web search is not enabled for this organization\" means..."
        for content, error in ((self.TEXT, False), (self.TEXT, None), ("", True), (None, True), (quoted, True),
                               ("web search is not enabled for this organization", True)):
            self.assertFalse(web_search_access.turned_off(content, error), (content, error))

    def test_once_off_the_connection_loses_websearch_until_the_memory_lapses(self):
        chat = SimpleNamespace(state={"connection_id": "c-1", "provider": "claude_team"})
        other = SimpleNamespace(state={"connection_id": "c-2", "provider": "claude_team"})
        legacy = SimpleNamespace(state={"provider": "claude_api"})
        self.assertEqual(web_search_access.native_tools(chat), ("WebSearch", "WebFetch"))
        web_search_access.record_off(chat, now=100.0)
        self.assertEqual(web_search_access.native_tools(chat, now=101.0), ("WebFetch",))
        self.assertEqual(web_search_access.native_tools(other, now=101.0), ("WebSearch", "WebFetch"))
        self.assertEqual(web_search_access.native_tools(legacy, now=101.0), ("WebSearch", "WebFetch"))
        lapsed = 100.0 + web_search_access.REMEMBER_SECONDS
        self.assertEqual(web_search_access.native_tools(chat, now=lapsed), ("WebSearch", "WebFetch"))

    def test_the_launch_then_says_web_search_is_unavailable(self):
        from angelo_agent.claude_text import NO_WEB_SEARCH
        chat = SimpleNamespace(turn_options=None, state={"provider": "claude_team", "connection_id": "c-1",
                                                         "options": {}})
        web_search_access.record_off(chat)
        command, initialize = claude_session("claude", chat)
        self.assertEqual(command[command.index("--tools") + 1], "WebFetch")
        self.assertIn(NO_WEB_SEARCH.strip(), initialize["request"]["systemPrompt"][0])

    def test_only_a_failed_websearch_call_of_the_round_counts(self):
        seen = []
        searches = web_search_access.Searches(lambda: seen.append(True))
        searches.observe({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "search", "name": "WebSearch", "input": {"query": "q"}},
            {"type": "tool_use", "id": "fetch", "name": "WebFetch", "input": {"url": "https://x.example"}}]}})
        # A fetched page that quotes the refusal, even as an error, is not a search: it never counts.
        for identity in ("fetch", "unknown", "search"):
            searches.observe({"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": identity, "is_error": True, "content": self.TEXT}]}})
        self.assertEqual(seen, [True])
        for odd in ({}, None, {"type": "user", "message": {"content": "text"}}, {"type": "user", "message": None}):
            searches.observe(odd)

    def test_a_claude_turn_notes_the_switch_for_its_connection(self):
        from angelo_agent.claude_transport import ClaudeTurn
        turn = ClaudeTurn.__new__(ClaudeTurn)
        turn.controller = SimpleNamespace(state={"provider": "claude_team", "connection_id": "c-9"})
        turn.note_web_search_off()
        self.assertTrue(web_search_access.is_off(turn.controller))


class InstallerMirrorTests(unittest.TestCase):
    def test_a_policy_mirror_also_serves_the_installers_at_the_same_paths(self):
        from angelo_agent.update_feed import install_source
        url = "https://downloads.angelo.studio/1.0.10/Angelo-1.0.10-macos.dmg"
        self.assertEqual(install_source(url, "https://it.example.test/angelo/stable.json"),
                         "https://it.example.test/angelo/1.0.10/Angelo-1.0.10-macos.dmg")
        self.assertEqual(install_source(url, ""), url)
        # A file Angelo doesn't install itself is never redirected.
        self.assertEqual(install_source("https://github.com/x/y.dmg", "https://it.example.test/stable.json"),
                         "https://github.com/x/y.dmg")

    def test_the_preparer_fetches_from_the_mirror_and_checks_the_feeds_digest(self):
        from angelo_agent import update_flow
        from angelo_agent.update_download import DownloadError
        asked = []

        def fetch(expected, target, **options):
            asked.append(expected)
            raise DownloadError("http_404")
        preparer = update_flow.Preparer("/tmp/none", "1.0.9", "macos-arm64", "/x", fetch=fetch,
                                        mirror="https://it.example.test/angelo/stable.json", log=lambda *a: None)
        entry = SimpleNamespace(url="https://downloads.angelo.studio/1.0.10/A.dmg", sha256="a" * 64, size=5)
        with self.assertRaises(update_flow.PrepareError):
            preparer._fetch(entry, Path("/tmp"), "update.dmg", 0, {})
        self.assertEqual((asked[0].url, asked[0].sha256, asked[0].size),
                         ("https://it.example.test/angelo/1.0.10/A.dmg", "a" * 64, 5))
        # A mirror that holds only stable.json: the feed's own host next, with the same checks.
        self.assertEqual((asked[1].url, asked[1].sha256, asked[1].size), (entry.url, "a" * 64, 5))

    def test_only_a_file_the_mirror_lacks_is_fetched_elsewhere(self):
        from angelo_agent import update_flow
        from angelo_agent.update_download import DownloadError
        for code in ("incomplete", "offline", "timeout", "cancelled", "disk_space", "digest", "http_503"):
            asked = []

            def fetch(expected, target, code=code, **options):
                asked.append(expected.url)
                raise DownloadError(code)
            preparer = update_flow.Preparer("/tmp/none", "1.0.9", "macos-arm64", "/x", fetch=fetch,
                                            mirror="https://it.example.test/stable.json", log=lambda *a: None)
            entry = SimpleNamespace(url="https://downloads.angelo.studio/1.0.10/A.dmg", sha256="a" * 64, size=5)
            with self.subTest(code=code), self.assertRaises(update_flow.PrepareError):
                preparer._fetch(entry, Path("/tmp"), "update.dmg", 0, {})
            # The partial download stays the mirror's, to resume there next time.
            self.assertEqual(asked, ["https://it.example.test/1.0.10/A.dmg"])


class PersonalKeyCheckTests(unittest.TestCase):
    def test_a_forbidden_personal_key_is_never_sent_to_its_provider(self):
        sent = []
        checks = api_connections.ApiConnections(transport=lambda request: sent.append(request) or {})
        record = {"id": "r-1", "mode": "openai_images_api"}
        with patch("angelo_agent.connection_access.personal_allowed", return_value=False), \
                patch("angelo_agent.managed_policy.current",
                      return_value=SimpleNamespace(allow_personal_connections=False, require_team_sign_in=False)):
            checks.start(record, "sk-test")
        self.assertEqual(checks.results["r-1"].state, "failed")
        self.assertNotIn("r-1", checks.running)
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
