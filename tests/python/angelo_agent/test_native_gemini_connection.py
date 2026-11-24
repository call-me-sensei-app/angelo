# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Gemini coding connection (Carbon and Neon, gated on the agent runtime's Gemini wire) and one Google key shared by
several connections (``shared_keys``, ``shared_keys_ui``), on any host OS with fakes for the window."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core"
sys.path[:0] = [str(ADDON), str(HERE)]
from angelo_agent import agent_runtime, provider_registry, runtime_launch, saved_keys_ui, shared_keys, shared_keys_ui
from angelo_agent import chat_settings, custom_endpoints, endpoint_catalog, model_profiles, team_runtime
from angelo_agent.api_connections import _models, probe_request
from angelo_agent.connections import ConnectionStore
from angelo_agent.protocol import RpcError
from angelo_agent.provider_registry import CODING, GENAI, SPECS

KEY = "AIza-test-key-not-real-000000000000000"
OTHER = "AIza-other-key-not-real-0000000000000"
UI_SOURCE = (ADDON / "angelo_agent/ui.py").read_text()


LEVELS = [{"effort": level, "description": level} for level in ("low", "medium", "high")]
# What the pinned runtime lists for its Gemini wire (``codex debug models`` with a Gemini provider): Carbon alone.
CARBON_ENTRY = {"slug": "gemini-4-carbon", "display_name": "Gemini 4 Carbon", "default_reasoning_level": "medium",
                "supported_reasoning_levels": LEVELS, "shell_type": "shell_command",
                "truncation_policy": {"mode": "tokens", "limit": 10000}, "context_window": 1048576,
                "input_modalities": ["text", "image"]}
GPT_ENTRY = {"slug": "gpt-6.1-sol", "use_responses_lite": True}


def listing(calls=None):
    """A runtime that lists Carbon for its Gemini wire and GPT for its bundled (Responses) catalog."""
    def run(argv, **_kwargs):
        if calls is not None:
            calls.append(argv)
        models = [GPT_ENTRY] if "--bundled" in argv else [CARBON_ENTRY]
        return SimpleNamespace(returncode=0, stdout=json.dumps({"models": models}).encode())
    return run


def gemini_launch(run, directory):
    """``codex_command`` for the Gemini connection on the bundled runtime; (``-c`` settings, written catalog)."""
    endpoint_catalog._cache.clear()
    with with_wire(True), patch.object(agent_runtime, "kind", return_value="bundled"), \
            patch("angelo_agent.endpoint_catalog.child_process.run", run), \
            patch("angelo_agent.endpoint_catalog._signature", return_value=("codex", 1, 1)):
        command, _env = runtime_launch.codex_command("/app/Helpers/codex", "gemini_api", directory, True, api_key=KEY)
    config = dict(v.split("=", 1) for i, v in enumerate(command) if i > 0 and command[i - 1] == "-c")
    path = config.get("model_catalog_json")
    return config, json.loads(Path(json.loads(path)).read_text()) if path else None


def with_wire(supported=True):
    """The pin as a runtime with (or without) the Gemini wire declares it (``wire_apis``)."""
    wires = list(agent_runtime.PIN.get("wire_apis", ()))
    wires = wires + ["gemini"] if supported else [w for w in wires if w != "gemini"]
    return patch.dict(agent_runtime.PIN, {"wire_apis": wires})


class GeminiSpecTests(unittest.TestCase):
    def test_carbon_and_neon_are_the_gemini_coding_models(self):
        """Carbon and Neon (maintainer 2026-11-23); Carbon stays the connection's default at medium, Neon runs at
        the common default, high. Argon stays out."""
        spec = SPECS["gemini_api"]
        self.assertEqual((spec["group"], spec["provider"], spec["method"], spec["runtime"]),
                         (CODING, "GOOGLE", "API_KEY", "codex"))
        self.assertEqual([m["id"] for m in spec["models"]], ["gemini-4-carbon", "gemini-4-neon"])
        carbon, neon = spec["models"]
        self.assertEqual(chat_settings.default_effort(carbon["efforts"], carbon["id"]), "medium")
        self.assertEqual(chat_settings.default_effort(neon["efforts"], neon["id"]), "high")
        self.assertEqual(chat_settings.default_effort(["low", "medium", "high"], "gemini-4-argon"), "high")
        for model in (carbon, neon):
            with self.subTest(model=model["id"]):
                # Thinking cannot be turned off; Neon's xhigh waits for the runtime to send it (it sends "high").
                self.assertEqual(model["efforts"], ["low", "medium", "high"])
                self.assertEqual(model["context_window"], 1048576)
                self.assertTrue(model["images"])
                self.assertIn(model["id"], model_profiles.MODELS)
                self.assertEqual(model_profiles.MODELS[model["id"]]["runtime"], "codex")
                self.assertEqual(model_profiles.MODELS[model["id"]]["context_window"], model["context_window"])
        self.assertEqual((carbon["default"], neon["default"]), (True, False))   # Existing connections keep Carbon.
        self.assertEqual(spec["wire_api"], "gemini")
        coding_google = [mode for mode, s in SPECS.items() if s["group"] == CODING and s["provider"] == "GOOGLE"]
        self.assertEqual(coding_google, ["gemini_api"])   # No team route until the gateway allows the path.

    def test_a_chat_that_chooses_neon_sends_neon_at_its_own_default(self):
        """The launch's process default is the connection's default model; each turn names the chosen one."""
        models = _models("gemini_api", {"models": [{"name": "models/gemini-4-carbon"}, {"name": "models/gemini-4-neon"}]})
        options = chat_settings.stored_options({"model": "gemini-4-neon"})
        controller = SimpleNamespace(state={"options": options}, settings={}, models=models)
        self.assertEqual(chat_settings.codex_turn_options(controller)["model"], "gemini-4-neon")
        self.assertEqual(chat_settings.codex_turn_options(controller)["effort"], "high")
        controller.state["options"] = chat_settings.stored_options({})
        self.assertEqual(chat_settings.codex_turn_options(controller)["model"], "gemini-4-carbon")
        self.assertEqual(chat_settings.codex_turn_options(controller)["effort"], "medium")

    def test_an_endpoints_gemini_model_may_be_mapped_to_neon(self):
        self.assertEqual(custom_endpoints._profile("gemini-4-neon", "gemini"), "gemini-4-neon")
        self.assertIsNone(custom_endpoints._profile("gemini-4-neon", "anthropic-messages"))
        self.assertIsNone(custom_endpoints._profile("gemini-4-argon", "gemini"))

    def test_the_connection_is_offered_only_when_the_runtime_has_the_wire(self):
        with with_wire(False):
            self.assertFalse(provider_registry.available("gemini_api"))
            self.assertNotIn("GOOGLE", [p for p, _, _ in provider_registry.provider_items(CODING)])
            self.assertEqual(provider_registry.methods(CODING, "GOOGLE"), ())
            self.assertIn("GOOGLE", [p for p, _, _ in provider_registry.provider_items(GENAI)])   # Images stay.
        with with_wire(True):
            self.assertTrue(provider_registry.available("gemini_api"))
            self.assertIn("GOOGLE", [p for p, _, _ in provider_registry.provider_items(CODING)])
            self.assertEqual(provider_registry.methods(CODING, "GOOGLE"), ("API_KEY",))
        for mode in SPECS:   # Every other mode is unaffected by the gate.
            if mode != "gemini_api":
                self.assertTrue(provider_registry.available(mode), mode)

    def test_the_form_s_provider_list_follows_availability_while_the_app_runs(self):
        """A remote switch or the runtime's wire can change what is available after the add-on loaded; the New
        connection form asks each time (a list built at import kept GOOGLE out of a run that enabled the wire)."""
        def coding():
            return [p for p, _, _ in provider_registry.provider_choice_items(CODING)]
        with with_wire(False):
            self.assertNotIn("GOOGLE", coding())
        with with_wire(True):
            self.assertIn("GOOGLE", coding())
            first = provider_registry.provider_choice_items(CODING)
            self.assertIs(provider_registry.provider_choice_items(CODING), first, "Blender's strings stay alive")
        self.assertNotIn("_connection_provider_items", UI_SOURCE)
        self.assertIn("provider_choice_items(self.group)", UI_SOURCE)

    def test_the_gate_is_the_pins_wire_list_never_a_thread_parameter(self):
        """The release checks prove each ``features`` entry as a thread/start parameter and each ``wire_apis`` entry
        by ``codex debug models`` on that wire; a wire listed among the parameters would fail every release."""
        self.assertEqual(agent_runtime.PIN["wire_apis"], ["gemini"])
        self.assertTrue(set(agent_runtime.PIN["features"]).isdisjoint(agent_runtime.PIN["wire_apis"]))
        self.assertEqual(provider_registry.runtime_wire("gemini_api"), "gemini")
        for mode, spec in SPECS.items():
            if mode != "gemini_api":
                self.assertIsNone(provider_registry.runtime_wire(mode), mode)
        self.assertTrue(agent_runtime.supports_wire("responses"), "Codex's own wire needs no listing")
        with with_wire(False):
            self.assertFalse(agent_runtime.supports_wire("gemini"))
            self.assertTrue(agent_runtime.supports_wire("responses"))

    def test_the_provider_entry_names_the_gemini_wire_and_its_stream_rules(self):
        entry = {key.split(".", 2)[2]: json.loads(value) for key, value in team_runtime.model_provider_entry("gemini_api").items()}
        self.assertEqual(entry["wire_api"], "gemini")
        self.assertEqual(entry["base_url"], "https://generativelanguage.googleapis.com/v1beta")
        self.assertEqual(entry["env_http_headers.x-goog-api-key"], "ANGELO_GEMINI_API_KEY")
        self.assertNotIn("env_key", entry)   # Google takes no bearer token: the key goes in its own header.
        # The wire's silent-stream rules: re-sent once, before the host's own give-up (test_native_stream_silence).
        self.assertEqual((entry["stream_idle_timeout_ms"], entry["stream_max_retries"]), (360000, 1))
        grok = {key.split(".", 2)[2]: json.loads(value) for key, value in team_runtime.model_provider_entry("grok_api").items()}
        self.assertEqual(grok["wire_api"], "responses")
        # A Responses provider: the runtime's own Responses rules, written out.
        self.assertEqual((grok["stream_idle_timeout_ms"], grok["stream_max_retries"]), (300000, 5))

    def test_only_angelos_runtime_launches_gemini(self):
        with tempfile.TemporaryDirectory() as directory, with_wire(True):
            with patch.object(agent_runtime, "kind", return_value="own"), self.assertRaises(RpcError):
                runtime_launch.codex_command("/usr/local/bin/codex", "gemini_api", directory, True, api_key=KEY)
            with patch.object(agent_runtime, "kind", return_value="bundled"):
                command, env = runtime_launch.codex_command("/app/Helpers/codex", "gemini_api", directory, True,
                                                            api_key=KEY)
            config = dict(v.split("=", 1) for i, v in enumerate(command) if i > 0 and command[i - 1] == "-c")
            self.assertEqual(json.loads(config["model"]), "gemini-4-carbon")
            self.assertEqual(json.loads(config["model_reasoning_effort"]), "medium")
            self.assertEqual(json.loads(config["model_providers.gemini_api.wire_api"]), "gemini")
            self.assertEqual(env["ANGELO_GEMINI_API_KEY"], KEY)
            self.assertNotIn(KEY, " ".join(command))
            self.assertEqual(config["include_environment_context_time"], "false")   # No time in the opening.
        with tempfile.TemporaryDirectory() as directory, with_wire(False), \
                patch.object(agent_runtime, "kind", return_value="bundled"), self.assertRaises(RpcError):
            runtime_launch.codex_command("/app/Helpers/codex", "gemini_api", directory, True, api_key=KEY)

    def test_every_gemini_model_runs_on_the_runtime_s_metadata(self):
        """The runtime's Gemini wire lists Carbon alone: Neon gets Carbon's entry (the same API) under its own name,
        efforts and default, never Codex's generic fallback (10 KB of tool output)."""
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            config, written = gemini_launch(listing(calls), directory)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("--bundled", calls[0])   # The Gemini wire's own catalog, listed offline.
        self.assertIn('model_providers.angelo_catalog.wire_api="gemini"', calls[0])
        self.assertEqual(calls[0][-2:], ["debug", "models"])
        carbon, neon = written["models"]
        self.assertEqual(carbon, CARBON_ENTRY)   # A listed model keeps its own entry.
        self.assertEqual((neon["slug"], neon["display_name"]), ("gemini-4-neon", "Gemini 4 Neon"))
        self.assertEqual(neon["default_reasoning_level"], "high")
        self.assertEqual([level["effort"] for level in neon["supported_reasoning_levels"]], ["low", "medium", "high"])
        for key in ("truncation_policy", "shell_type", "context_window", "input_modalities"):
            self.assertEqual(neon[key], CARBON_ENTRY[key], key)
        self.assertEqual(json.loads(config["model_reasoning_effort"]), "medium")   # The process default: Carbon's.

    def test_a_runtime_that_cannot_list_its_gemini_catalog_still_launches(self):
        failing = lambda *a, **k: SimpleNamespace(returncode=2, stdout=b"")  # noqa: E731
        with tempfile.TemporaryDirectory() as directory:
            config, written = gemini_launch(failing, directory)
        self.assertIsNone(written)
        self.assertNotIn("model_catalog_json", config)
        self.assertEqual(json.loads(config["model"]), "gemini-4-carbon")

    def test_an_endpoint_model_mapped_to_neon_gets_neon_s_entry(self):
        catalog = {"gemini-4-carbon": CARBON_ENTRY}
        spec = {"models": [{"id": "house-neon", "profile": "gemini-4-neon"}, {"id": "house-x", "profile": None},
                           {"id": "house-carbon", "profile": "gemini-4-carbon"}]}
        neon, carbon = endpoint_catalog.entries(spec, catalog)
        self.assertEqual((neon["slug"], neon["display_name"], neon["default_reasoning_level"]),
                         ("house-neon", "house-neon", "high"))
        self.assertEqual((carbon["slug"], carbon["default_reasoning_level"]), ("house-carbon", "medium"))
        self.assertIsNone(endpoint_catalog.entry("gemini-4-argon", catalog))   # Only a model the registry admits.

    def test_an_inherited_gemini_key_never_reaches_a_child(self):
        with patch.dict(os.environ, {"ANGELO_GEMINI_API_KEY": OTHER, "GEMINI_API_KEY": OTHER, "GOOGLE_API_KEY": OTHER}):
            env = runtime_launch.child_environment("grok_api", KEY)
        self.assertFalse({"ANGELO_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"} & set(env))

    def test_the_check_reads_googles_catalog_without_billing(self):
        request = probe_request("gemini_api", KEY)
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000")
        self.assertEqual(request.get_header("X-goog-api-key"), KEY)
        self.assertNotIn(KEY, request.full_url)
        listed = _models("gemini_api", {"models": [{"name": "models/gemini-4-argon"}, {"name": "models/gemini-4-neon"},
                                                   {"name": "models/gemini-4-carbon"}, {"name": "models/gemini-3.8-flash"}]})
        self.assertEqual([m["id"] for m in listed], ["gemini-4-carbon", "gemini-4-neon"])   # Only admitted models.
        self.assertEqual([m["default"] for m in listed], [True, False])
        # A key whose catalog lists Neon alone (Carbon not yet enabled for it) gets Neon as its default.
        alone = _models("gemini_api", {"models": [{"name": "models/gemini-4-neon"}, {"name": "models/gemini-4-argon"}]})
        self.assertEqual([(m["id"], m["default"]) for m in alone], [("gemini-4-neon", True)])
        self.assertTrue(SPECS["gemini_api"]["models"][1]["default"] is False, "the registry entry is not changed")
        self.assertEqual(_models("gemini_api", {"models": [{"name": "models/gemini-3.8-flash"}]}), [])


class SharedKeyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = ConnectionStore(self.directory.name)
        self.images = self.store.add("GOOGLE", "API_KEY", "My Gemini", group=GENAI)
        with with_wire(True):
            self.coding = self.store.add("GOOGLE", "API_KEY", "Carbon", group=CODING)
        self.grok = self.store.add("XAI", "API_KEY", "Grok", group=CODING)
        # Each add saves and reloads the store: use its current records.
        self.images, self.coding, self.grok = (self.store.records[r["id"]] for r in (self.images, self.coding, self.grok))

    def tearDown(self):
        self.directory.cleanup()

    def test_only_connections_of_one_key_family_with_their_own_key_are_sources(self):
        records = self.store.records
        self.assertEqual([r["id"] for r in shared_keys.sources(records, "gemini_api", exclude=self.coding["id"])],
                         [self.images["id"]])
        self.assertEqual([r["id"] for r in shared_keys.sources(records, "gemini_images_api", exclude=self.images["id"])],
                         [self.coding["id"]])   # Either way round.
        self.assertEqual(shared_keys.sources(records, "grok_api"), [])
        self.assertEqual(shared_keys.sources(records, "openai_images_api"), [])
        shared_keys.link(records, self.coding, self.images["id"])
        self.assertEqual(shared_keys.sources(records, "gemini_images_api", exclude=self.images["id"]), [])   # One level.
        with self.assertRaises(ValueError):
            shared_keys.link(records, self.grok, self.images["id"])
        with self.assertRaises(ValueError):   # A source with dependents keeps its own key.
            shared_keys.link(records, self.images, self.coding["id"])

    def test_the_link_is_stored_and_a_bad_one_reads_as_none(self):
        shared_keys.link(self.store.records, self.coding, self.images["id"])
        self.store.save()
        saved = ConnectionStore(self.directory.name)
        self.assertEqual(saved.records[self.coding["id"]]["key_from"], self.images["id"])
        self.assertIsNone(saved.records[self.images["id"]]["key_from"])
        self.assertEqual(shared_keys.source_of(saved.records, saved.records[self.coding["id"]])["id"], self.images["id"])
        raw = json.loads(saved.path.read_text())
        for record in raw["connections"]:
            if record["id"] == self.grok["id"]:
                record["key_from"] = record["id"]   # A link to itself.
            if record["id"] == self.images["id"]:
                record["key_from"] = "not-a-uuid"
        saved.path.write_text(json.dumps(raw))
        again = ConnectionStore(self.directory.name)
        self.assertIsNone(again.records[self.grok["id"]]["key_from"])
        self.assertIsNone(again.records[self.images["id"]]["key_from"])

    def test_a_link_to_another_family_or_a_removed_connection_does_not_hold(self):
        records = self.store.records
        self.coding["key_from"] = self.grok["id"]   # Hand-edited: another family.
        self.assertIsNone(shared_keys.source_of(records, self.coding))
        self.assertEqual(shared_keys.dependents(records, self.grok["id"]), [])
        self.coding["key_from"] = "00000000-0000-4000-8000-000000000000"   # Removed elsewhere.
        self.assertIsNone(shared_keys.source_of(records, self.coding))

    def test_the_sources_key_is_mirrored_and_removal_strands_dependents(self):
        records, memory = self.store.records, {self.images["id"]: KEY}
        shared_keys.link(records, self.coding, self.images["id"])
        self.assertEqual(shared_keys.mirror(records, memory, self.images["id"]), [self.coding["id"]])
        self.assertEqual(memory[self.coding["id"]], KEY)
        memory[self.images["id"]] = OTHER   # The source's key replaced.
        shared_keys.mirror(records, memory, self.images["id"])
        self.assertEqual(memory[self.coding["id"]], OTHER)
        del memory[self.images["id"]]
        shared_keys.mirror(records, memory, self.images["id"])
        self.assertNotIn(self.coding["id"], memory)
        self.assertEqual(shared_keys.orphans(records, self.images["id"]), [self.coding["id"]])
        self.assertIsNone(self.coding["key_from"])

    def test_a_linked_connection_has_no_saved_key_and_says_whose_key_it_uses(self):
        self.assertTrue(saved_keys_ui.uses_saved_key(self.coding))
        shared_keys.link(self.store.records, self.coding, self.images["id"])
        self.assertFalse(saved_keys_ui.uses_saved_key(self.coding))
        self.assertTrue(saved_keys_ui.uses_saved_key(self.images))
        fake = type("FakeUI", (), {"connections": lambda _self: self.store})()
        with patch.object(shared_keys_ui, "_ui", return_value=fake):
            label = saved_keys_ui.storage_label(self.coding, True)
            self.assertEqual(label.template, "Uses the API key of {name}")
            self.assertEqual(label.fields["name"], "My Gemini")
            self.coding["key_from"] = "00000000-0000-4000-8000-000000000000"
            self.assertIn("removed", saved_keys_ui.storage_label(self.coding, False).template)
        self.assertIsNone(shared_keys_ui.storage_label(self.grok))   # A key of its own: no shared line.


class FormContractTests(unittest.TestCase):
    """The connection form's wiring in ui.py (its behaviour runs in Blender: test_blender_saved_keys.py)."""

    def test_the_form_offers_the_shared_key_and_applies_it(self):
        self.assertIn("key_source: EnumProperty(", UI_SOURCE)
        self.assertIn("items=shared_keys_ui.key_source_items", UI_SOURCE)
        self.assertIn("shared_keys_ui.offers_choice(self, mode)", UI_SOURCE)
        self.assertIn("shared_keys_ui.link(record, source)", UI_SOURCE)
        self.assertIn("shared_keys_ui.unlink(record)", UI_SOURCE)

    def test_replacing_or_removing_a_source_reaches_its_dependents(self):
        replace = UI_SOURCE[UI_SOURCE.index("def replace_connection_key"):UI_SOURCE.index("def remember_saved_key")]
        self.assertIn("shared_keys_ui.in_use(identity)", replace)
        self.assertIn("shared_keys_ui.share(identity)", replace)
        removal = UI_SOURCE[UI_SOURCE.index("class ANGELO_OT_connection_remove"):]
        self.assertIn("shared_keys_ui.strand(self.connection_id)", removal[:4000])


if __name__ == "__main__":
    unittest.main()
