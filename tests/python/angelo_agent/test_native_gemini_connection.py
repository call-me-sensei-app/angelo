# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Gemini coding connection (Carbon only, gated on the agent runtime's Gemini wire) and one Google key shared by
several connections (``shared_keys``, ``shared_keys_ui``), on any host OS with fakes for the window."""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ADDON = HERE.parents[2] / "scripts/addons_core"
sys.path[:0] = [str(ADDON), str(HERE)]
from angelo_agent import agent_runtime, provider_registry, runtime_launch, saved_keys_ui, shared_keys, shared_keys_ui
from angelo_agent import chat_settings, team_runtime
from angelo_agent.api_connections import _models, probe_request
from angelo_agent.connections import ConnectionStore
from angelo_agent.protocol import RpcError
from angelo_agent.provider_registry import CODING, GENAI, SPECS

KEY = "AIza-test-key-not-real-000000000000000"
OTHER = "AIza-other-key-not-real-0000000000000"
UI_SOURCE = (ADDON / "angelo_agent/ui.py").read_text()


def with_wire(supported=True):
    """The pin as a runtime with (or without) the Gemini wire declares it (``wire_apis``)."""
    wires = list(agent_runtime.PIN.get("wire_apis", ()))
    wires = wires + ["gemini"] if supported else [w for w in wires if w != "gemini"]
    return patch.dict(agent_runtime.PIN, {"wire_apis": wires})


class GeminiSpecTests(unittest.TestCase):
    def test_carbon_is_the_only_gemini_coding_model_and_defaults_to_medium(self):
        spec = SPECS["gemini_api"]
        self.assertEqual((spec["group"], spec["provider"], spec["method"], spec["runtime"]),
                         (CODING, "GOOGLE", "API_KEY", "codex"))
        self.assertEqual([m["id"] for m in spec["models"]], ["gemini-4-carbon"])
        carbon = spec["models"][0]
        self.assertEqual(chat_settings.default_effort(carbon["efforts"], carbon["id"]), "medium")
        self.assertEqual(chat_settings.default_effort(["low", "medium", "high"], "gemini-4-argon"), "high")
        self.assertEqual(carbon["efforts"], ["low", "medium", "high"])   # Thinking cannot be turned off.
        self.assertTrue(carbon["default"])
        self.assertEqual(carbon["context_window"], 1048576)
        self.assertEqual(spec["wire_api"], "gemini")
        coding_google = [mode for mode, s in SPECS.items() if s["group"] == CODING and s["provider"] == "GOOGLE"]
        self.assertEqual(coding_google, ["gemini_api"])   # No team route until the gateway allows the path.

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

    def test_the_provider_entry_names_the_gemini_wire_and_a_long_stream_idle_limit(self):
        entry = {key.split(".", 2)[2]: json.loads(value) for key, value in team_runtime.model_provider_entry("gemini_api").items()}
        self.assertEqual(entry["wire_api"], "gemini")
        self.assertEqual(entry["base_url"], "https://generativelanguage.googleapis.com/v1beta")
        self.assertEqual(entry["env_http_headers.x-goog-api-key"], "ANGELO_GEMINI_API_KEY")
        self.assertNotIn("env_key", entry)   # Google takes no bearer token: the key goes in its own header.
        self.assertEqual(entry["stream_idle_timeout_ms"], 900000)   # Google: at least 15 minutes.
        grok = {key.split(".", 2)[2]: json.loads(value) for key, value in team_runtime.model_provider_entry("grok_api").items()}
        self.assertEqual(grok["wire_api"], "responses")
        self.assertNotIn("stream_idle_timeout_ms", grok)

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
        listed = _models("gemini_api", {"models": [{"name": "models/gemini-4-carbon"}, {"name": "models/gemini-4-argon"},
                                                   {"name": "models/gemini-3.8-flash"}]})
        self.assertEqual([m["id"] for m in listed], ["gemini-4-carbon"])   # Only the admitted model.
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
