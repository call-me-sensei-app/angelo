# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent runtime never runs with the person's own Codex config; only what the sign-in reads is passed on.

Coordinator rulings of 2026-11-20: Angelo's runtime never sends Angelo turns where the person's own Codex config
points, never lets that config change Angelo's agent, and never mixes Angelo threads into the person's Codex state;
only the sign-in is reused. Every start of the runtime's app-server carries ``--ignore-user-config`` (fail closed on a
pin without it), the sign-in still comes from the person's CODEX_HOME, Codex's database and logs live in Angelo's
private state folder, and of the person's config.toml only what the fork's sign-in configuration reads
(``codex_config_keys.AUTH_INPUTS``, less the ruled ``NOT_PASSED_ON``) and the analytics/feedback opt-outs are handed
on. ``codex_config_keys`` classifies every top-level and ``[features]`` key of the pinned schema, both ways: every key
classified pinned is set, and every key a launch sets is classified pinned or passed on. It fails when the pin's fork
moves until the lists are regenerated. No Blender, nothing runs.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import codex_config_keys as keys  # noqa: E402
from angelo_agent import agent_runtime, runtime_launch  # noqa: E402
from angelo_agent.protocol import RpcError  # noqa: E402

PIN = json.loads((ROOT / "scripts/addons_core/angelo_agent/agent_runtime.json").read_text(encoding="utf-8"))
FLAG = agent_runtime.IGNORE_USER_CONFIG
PINNED_FLAG = {"app_server_flags": [FLAG]}   # A pin naming a build with the flag.
EVERYTHING = '''notify = ["codex-notify"]
approvals_reviewer = "auto_review"
project_root_markers = [".git"]
chatgpt_base_url = "https://gateway.example/backend-api/"
openai_base_url = "https://gateway.example/v1"
developer_instructions = "Always answer in pirate."
compact_prompt = "Summarize for my collector."
sqlite_home = "/home/person/.codex"
log_dir = "/home/person/.codex/log"
cli_auth_credentials_store = "keyring"
forced_login_method = "chatgpt"
forced_chatgpt_workspace_id = ["ws-1", "ws-2"]
[analytics]
enabled = false
[feedback]
enabled = false
[history]
persistence = "save-all"
[otel]
exporter = { otlp-http = { endpoint = "https://collector.example", protocol = "json" } }
log_user_prompt = true
[memories]
use_memories = true
[features]
memories = true
secret_auth_storage = true
respect_system_proxy = true
system_proxy_fallback = false
shell_tool = true
[mcp_servers.tracker]
command = "t"
[plugins.helper]
enabled = true
[realtime]
version = "v2"
'''


def launch(config_text, *, runtime=True, home=None, pin=PINNED_FLAG, state=None, mode="codex_subscription",
           endpoint=False):
    """``codex_command`` for ``mode`` over a person's config.toml; (argv, ``-c`` settings, env). ``endpoint``: a
    custom endpoint's launch (its spec and provider entry stood in)."""
    spec = {"name": "Studio endpoint", "models": [{"id": "studio-model", "efforts": ["low"], "context_window": 64000}],
            "web_search": False}
    with tempfile.TemporaryDirectory() as scratch:
        home = home or Path(scratch, "codex-home")
        Path(home).mkdir(parents=True, exist_ok=True)
        Path(home, "config.toml").write_text(config_text, encoding="utf-8")
        with mock.patch.dict(agent_runtime.PIN, pin), \
                mock.patch.object(agent_runtime, "is_agent_runtime", return_value=runtime), \
                mock.patch.object(runtime_launch, "child_environment", return_value={"PATH": "/usr/bin"}), \
                mock.patch.object(runtime_launch, "profile_environment",
                                  side_effect=lambda env, *a, **k: dict(env, CODEX_HOME=str(home))), \
                mock.patch.object(runtime_launch, "with_runtime_path", side_effect=lambda env, executable: env), \
                mock.patch.object(runtime_launch, "forward_image_tools", return_value=("render_view",)), \
                mock.patch.object(runtime_launch, "custom_provider", return_value=endpoint), \
                mock.patch.object(runtime_launch, "spec_for", return_value=spec), \
                mock.patch.object(runtime_launch, "runtime_wire", return_value=None), \
                mock.patch.object(runtime_launch, "is_endpoint", return_value=endpoint), \
                mock.patch.object(runtime_launch.team_runtime, "model_provider_entry",
                                  return_value={"model_providers.%s.base_url" % mode: '"https://studio.example/v1"'}), \
                mock.patch("angelo_agent.endpoint_catalog.write", return_value=str(Path(scratch, "catalog.json"))):
            command, env = runtime_launch.codex_command("/x/codex", mode, str(state or Path(scratch, "state")))
    return command, dict(value.split("=", 1) for flag, value in zip(command, command[1:]) if flag == "-c"), env


def classified(name, kinds):
    """Whether the ``-c`` key ``name`` belongs to a key classified as one of ``kinds``."""
    if name.startswith("features."):
        return keys.FEATURES.get(name.split(".", 2)[1], (None,))[0] in kinds
    return keys.KEYS.get(name.split(".", 1)[0], (None,))[0] in kinds


class ClassificationTests(unittest.TestCase):
    def test_every_schema_key_is_classified_with_a_reason(self):
        self.assertEqual(tuple(sorted(keys.KEYS)), keys.SCHEMA_KEYS)
        self.assertEqual(tuple(sorted(keys.FEATURES)), keys.FEATURE_KEYS)
        for table, entries in (("", keys.KEYS), ("features.", keys.FEATURES)):
            for key, (kind, reason) in entries.items():
                with self.subTest(table + key):
                    self.assertIn(kind, (keys.PINNED, keys.PASSED_ON, keys.DEFAULT))
                    self.assertTrue(reason.strip())

    def test_the_key_lists_are_the_pinned_forks(self):
        self.assertEqual(keys.SCHEMA_FORK_COMMIT, PIN["fork"]["commit"],
                         "the Codex pin moved: regenerate codex_config_keys from the new fork "
                         "(scripts/angelo/codex_config_inputs.py --fork <checkout>) and classify every addition")

    def test_everything_the_sign_in_reads_is_passed_on(self):
        """Re-review of ffa4cb56717, MUST-3: the keyring backend comes from features.secret_auth_storage, which a
        hand-picked list missed. The passed-on set is the fork's sign-in inputs, less the ruled exclusions."""
        passed = set(runtime_launch.SIGN_IN_KEYS) | {"features." + name for name in runtime_launch.SIGN_IN_FEATURES}
        self.assertEqual(passed, set(keys.AUTH_INPUTS) - set(keys.NOT_PASSED_ON))
        self.assertLessEqual(set(keys.NOT_PASSED_ON), set(keys.AUTH_INPUTS))
        classified_passed = {key for key, (kind, _r) in keys.KEYS.items() if kind == keys.PASSED_ON}
        classified_passed |= {"features." + key for key, (kind, _r) in keys.FEATURES.items() if kind == keys.PASSED_ON}
        self.assertEqual(classified_passed, passed | set(runtime_launch.OPT_OUTS))


class LaunchTests(unittest.TestCase):
    def test_the_runtime_starts_without_the_persons_config(self):
        command, _settings, _env = launch(EVERYTHING)
        self.assertEqual(command[1:3], ["app-server", FLAG])

    def test_a_pin_without_the_flag_starts_nothing(self):
        """Fail closed: never the person's config.toml silently loaded instead."""
        with self.assertRaises(RpcError):
            launch(EVERYTHING, pin={"app_server_flags": []})

    def test_only_what_the_sign_in_reads_is_passed_on(self):
        """Two config.toml files that differ in every key give launches that differ only in the passed-on keys."""
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        _command, first, _env = launch(EVERYTHING, state=state.name)
        _command, second, _env = launch('model = "other"\n', state=state.name)
        differing = {key for key in set(first) | set(second) if first.get(key) != second.get(key)}
        expected = {"analytics.enabled": "false", "cli_auth_credentials_store": '"keyring"',
                    "feedback.enabled": "false", "forced_chatgpt_workspace_id": '["ws-1", "ws-2"]',
                    "forced_login_method": '"chatgpt"', "features.secret_auth_storage": "true"}
        self.assertEqual(differing, set(expected), "features.shell_tool stays Angelo's pin, whatever the person's")
        self.assertEqual({key: first[key] for key in sorted(expected)}, expected)

    def test_the_persons_proxy_features_never_route_angelos_turns(self):
        """Re-read of 0b6b952594, MUST-1 (ruled): respect_system_proxy and system_proxy_fallback set the proxy policy
        of every Codex request, Angelo's model turns too; Angelo's network contract (HTTPS_PROXY/NO_PROXY) decides.
        Neither is carried, so the fork default (or an admin layer) applies."""
        _command, settings, _env = launch(EVERYTHING)
        self.assertEqual([key for key in settings if "proxy" in key], [])
        self.assertLessEqual({"features.respect_system_proxy", "features.system_proxy_fallback"},
                             set(keys.NOT_PASSED_ON))
        _command, opted_in, _env = launch("[analytics]\nenabled = true\n[feedback]\nenabled = true\n")
        self.assertNotIn("analytics.enabled", opted_in, "an opt-in is never passed on")

    def test_a_passed_on_value_reads_back_exactly(self):
        """Re-review NIT-1: json.dumps wrote a character beyond the BMP as a surrogate pair, which TOML refuses."""
        for value in ("ws-1", "ws-\U0001F600", 'quote " and \\ backslash', "new\nline\x7f", "é"):
            with self.subTest(value=value):
                self.assertEqual(tomllib.loads("x = " + runtime_launch.toml_string(value))["x"], value)
        _command, settings, _env = launch('forced_chatgpt_workspace_id = ["ws-\U0001F600", "b"]\n')
        self.assertEqual(tomllib.loads("x = " + settings["forced_chatgpt_workspace_id"])["x"], ["ws-\U0001F600", "b"])

    def test_a_config_nested_past_the_parser_is_ignored(self):
        """Re-review NIT-2: tomllib raises RecursionError on deep nesting; the launch reads none of that file."""
        command, settings, _env = launch("a = " + "[" * 5000 + "]" * 5000 + "\n")
        self.assertIn(FLAG, command)
        self.assertNotIn("cli_auth_credentials_store", settings)

    def test_no_override_needs_the_persons_config(self):
        """Fork review I2: under the flag the user layer is empty, so ``mcp_servers.<name>.enabled=false`` would make a
        server with no transport and the app-server would refuse to start (and the fallback load reuses the same -c
        set). Nothing under the flag names a server or a plugin of the person's config.toml."""
        _command, settings, _env = launch(EVERYTHING)
        self.assertEqual([key for key in settings if key.startswith(("mcp_servers.", "plugins."))], [])

    def test_the_pins_that_keep_codex_home_files_out(self):
        """Fork review S3: under the flag Codex still finds the person's skills, rules/*.rules, hooks.json and
        agents/*.toml through CODEX_HOME. These pins keep each out; dropping one fails here."""
        _command, settings, _env = launch(EVERYTHING)
        pins = ("features.skip_host_skill_discovery", "features.shell_tool", "features.hooks", "agents.enabled")
        self.assertEqual({key: settings.get(key) for key in pins},
                         {"features.skip_host_skill_discovery": "true", "features.shell_tool": "false",
                          "features.hooks": "false", "agents.enabled": "false"})

    def test_a_keyring_sign_in_is_found(self):
        """Fork review I1 and re-review MUST-3: under the flag the store mode defaults to File and the keyring backend
        to Direct (macOS, Linux), so a keyring sign-in would read as signed out and a new one land in plaintext
        auth.json. The store mode and the backend's feature are passed on."""
        for store, secret in (("keyring", "true"), ("auto", "false"), ("file", None)):
            with self.subTest(store=store, secret=secret):
                text = 'cli_auth_credentials_store = "%s"\n' % store
                text += "[features]\nsecret_auth_storage = %s\n" % secret if secret else ""
                _command, settings, _env = launch(text)
                self.assertEqual(settings.get("cli_auth_credentials_store"), '"%s"' % store)
                self.assertEqual(settings.get("features.secret_auth_storage"), secret)

    def test_every_pinned_key_is_set_and_every_set_key_is_classified(self):
        """Both ways (re-review NIT-3): a key classified pinned is set (an endpoint-only one by an endpoint's launch),
        and every key any launch sets is classified pinned, or passed on when it came from the person's file."""
        launches = {"plan": launch(EVERYTHING)[1], "api": launch(EVERYTHING, mode="codex_api")[1],
                    "endpoint": launch(EVERYTHING, mode="studio-endpoint", endpoint=True)[1]}
        for key, (kind, _reason) in keys.KEYS.items():
            if kind == keys.PINNED and key != "features":
                where = launches["endpoint"] if key in keys.ENDPOINT_ONLY else launches["plan"]
                with self.subTest(pinned=key):
                    self.assertTrue(any(name == key or name.startswith(key + ".") for name in where),
                                    "%s is classified pinned but its launch does not set it" % key)
        for name, (kind, _reason) in keys.FEATURES.items():
            if kind == keys.PINNED:
                with self.subTest(pinned="features." + name):
                    self.assertIn("features." + name, launches["plan"])
        for label, settings in launches.items():
            for name in settings:
                with self.subTest(launch=label, key=name):
                    self.assertTrue(classified(name, (keys.PINNED, keys.PASSED_ON)),
                                    "%s is set at launch but not classified pinned or passed on" % name)

    def test_sign_in_is_reused_and_codex_state_is_angelos(self):
        """The sign-in still comes from the person's CODEX_HOME (auth.json, and the keyring entry keyed by its path),
        with their credential store passed on; Codex's database and logs go to Angelo's state folder."""
        with tempfile.TemporaryDirectory() as person, tempfile.TemporaryDirectory() as state:
            command, settings, env = launch('cli_auth_credentials_store = "keyring"\n', home=Path(person), state=state)
            self.assertEqual(env["CODEX_HOME"], person, "the person's CODEX_HOME: their sign-in")
            self.assertIn(FLAG, command)
            self.assertEqual(settings["cli_auth_credentials_store"], '"keyring"')
            for key in ("sqlite_home", "log_dir"):
                with self.subTest(key):
                    place = Path(tomllib.loads("x = " + settings[key])["x"])
                    self.assertEqual(place.parts[:len(Path(state).parts) + 1], Path(state, "codex-state").parts)
                    self.assertNotIn(Path(person), [place, *place.parents], "never inside the person's ~/.codex")


class OwnCodexTests(unittest.TestCase):
    """The person's own Codex (a build without the agent runtime, or ANGELO_AGENT_RUNTIME=off, the one exception in
    a release) has no such flag: its launch keeps the per-key pins, and each server and plugin its config.toml lists is
    disabled by name."""

    def test_the_per_key_pins_hold(self):
        command, settings, _env = launch(EVERYTHING, runtime=False)
        self.assertNotIn(FLAG, command)
        expected = {"otel.exporter": '"none"', "otel.trace_exporter": '"none"', "otel.log_user_prompt": "false",
                    "otel.log_agent_responses": "false", "otel.log_guardian_assessments": "false",
                    "features.memories": "false", "memories.generate_memories": "false",
                    "memories.use_memories": "false", "history.persistence": '"none"',
                    "approvals_reviewer": '"user"', "project_root_markers": "[]", "notify": "[]",
                    "mcp_servers.tracker.enabled": "false", "plugins.helper.enabled": "false"}
        self.assertEqual({key: settings.get(key) for key in expected}, expected)


if __name__ == "__main__":
    unittest.main()
