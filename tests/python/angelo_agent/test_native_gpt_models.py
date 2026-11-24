# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""OpenAI's GPT models in Angelo: the published facts, the shipped defaults, and every list that must agree.

GPT-6.2 Astra and GPT-6.2 Sol (OpenAI, 2026-11-17) are the shipped Astra and Sol (coordinator's ruling,
2026-11-18); GPT-6 Astra and GPT-6.1 Sol stay selectable. Facts are OpenAI's own model pages
(developers.openai.com/api/docs/models/<id>, read 2026-11-18; doc/angelo/gpt_launch.rst, "Model IDs").
Dependency-free: no process, account or model is involved.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _path in (str(HERE.parents[2] / "scripts/addons_core"), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from angelo_agent import model_profiles, provider_registry, usage_view  # noqa: E402
import final_runs_plan  # noqa: E402

ASTRA, SOL = "gpt-6.2-astra", "gpt-6.2-sol"
EARLIER = ("gpt-6-astra", "gpt-6.1-sol")
# A request's input budget: the 1,050,000-token window less the 128,000 output tokens a response may use
# (both models' pages: "922,000 max input tokens").
INPUT_BUDGET = 922000


class ContextWindowMeaningTests(unittest.TestCase):
    """``context_window`` is one thing on every GPT row: a request's input budget, the page's window less its
    maximum output. Each row states the page it was read from, when, and both numbers, so the budget is derived
    the same way for every model, and the team connection's pinned windows agree with the table."""

    def test_every_gpt_row_derives_its_input_budget_from_its_own_page(self):
        rows = {identity: facts for identity, facts in model_profiles.MODELS.items() if facts["source"] == "openai"}
        self.assertTrue(rows)
        for identity, facts in rows.items():
            with self.subTest(model=identity):
                self.assertGreater(facts["window"], facts["max_output"])
                self.assertEqual(facts["context_window"], facts["window"] - facts["max_output"])
                self.assertEqual(facts["page"], "https://developers.openai.com/api/docs/models/" + identity)
                self.assertRegex(facts["read_on"], r"^\d{4}-\d{2}-\d{2}$")

    def test_the_team_connection_pins_the_tables_input_budget(self):
        for model in provider_registry.SPECS["codex_team"]["models"]:
            with self.subTest(model=model["id"]):
                self.assertEqual(model["context_window"], model_profiles.MODELS[model["id"]]["context_window"])


class PublishedFactsTests(unittest.TestCase):
    def test_the_6_2_rows_carry_openais_published_rates(self):
        expected = {ASTRA: ("strong", {"input": 8.0, "output": 40.0, "cache_read": 0.40, "cache_write": 10.0}),
                    SOL: ("balanced", {"input": 2.0, "output": 10.0, "cache_read": 0.10, "cache_write": 2.50})}
        for identity, (tier, rates) in expected.items():
            with self.subTest(model=identity):
                facts = model_profiles.MODELS[identity]
                self.assertEqual((facts["runtime"], facts["tier"], facts["source"]), ("codex", tier, "openai"))
                self.assertTrue(facts["images"], "text and image input")
                self.assertEqual(facts["context_window"], INPUT_BUDGET)
                # One 30-minute write rate: the 1-hour TTL a session asks for is charged at it too.
                self.assertEqual(model_profiles.rates(identity, "1h"), rates)
                self.assertEqual(model_profiles.rates(identity, "5m"), rates)

    def test_every_gpt_row_steps_up_above_272k_input_tokens(self):
        # Each model's page: a request of more than 272K input tokens costs 2x input and cache, 1.5x output.
        gpt = [identity for identity, facts in model_profiles.MODELS.items() if facts["source"] == "openai"]
        self.assertGreaterEqual(set(gpt), {ASTRA, SOL} | set(EARLIER))
        for identity in gpt:
            with self.subTest(model=identity):
                base = model_profiles.rates(identity, "1h", prompt_tokens=272000)
                above = model_profiles.rates(identity, "1h", prompt_tokens=272001)
                self.assertEqual(base, model_profiles.rates(identity, "1h"))
                self.assertAlmostEqual(base["cache_write"], base["input"] * 1.25)
                for key, factor in (("input", 2), ("cache_read", 2), ("cache_write", 2), ("output", 1.5)):
                    self.assertAlmostEqual(above[key], base[key] * factor, msg=key)

    def test_the_earlier_models_stay_selectable(self):
        for identity in EARLIER:
            with self.subTest(model=identity):
                self.assertEqual(model_profiles.resolve(identity), identity)

    def test_a_catalog_offering_both_generations_profiles_each_under_its_own_tier(self):
        catalog = [{"id": identity} for identity in (ASTRA, SOL) + EARLIER]
        profile = model_profiles.capability_profile("c1", "codex", "SUBSCRIPTION", catalog, effort="high")
        tiers = {model["id"]: model["tier"] for model in profile["models"]}
        self.assertEqual(tiers, {ASTRA: "strong", SOL: "balanced", "gpt-6-astra": "strong", "gpt-6.1-sol": "balanced"})


class ShippedDefaultTests(unittest.TestCase):
    def team_models(self):
        return provider_registry.SPECS["codex_team"]["models"]

    def test_a_team_openai_connection_defaults_to_6_2_sol_and_offers_every_launch_model(self):
        models = self.team_models()
        self.assertEqual([m["id"] for m in models if m["default"]], [SOL])
        self.assertEqual(models[0]["id"], SOL, "the launch reads the first model as the connection's own")
        self.assertEqual({m["id"] for m in models}, {ASTRA, SOL} | set(EARLIER))
        names = {m["id"]: m["name"] for m in models}
        self.assertEqual((names[ASTRA], names[SOL]), ("GPT-6.2 Astra", "GPT-6.2 Sol"))

    def test_every_pinned_gpt_model_agrees_with_its_profile(self):
        # The registry's pinned rows and model_profiles describe the same model: one may never claim images or a
        # context window the other lacks (a custom endpoint mapped to the model inherits the registry's).
        for entry in self.team_models():
            with self.subTest(model=entry["id"]):
                facts = model_profiles.MODELS[entry["id"]]
                self.assertEqual(entry["images"], facts["images"])
                if entry["context_window"]:
                    self.assertEqual(entry["context_window"], facts["context_window"])
                pinned = next(m for m in provider_registry._CODEX_TEAM_MODELS if m["id"] == entry["id"])
                self.assertIs(provider_registry.profile_model(entry["id"]), pinned)

    def test_usage_names_read_as_openai_writes_them(self):
        self.assertEqual(usage_view.model_label(ASTRA), "GPT-6.2 Astra")
        self.assertEqual(usage_view.model_label(SOL), "GPT-6.2 Sol")

    def test_the_gates_of_record_run_the_shipped_models(self):
        self.assertEqual(final_runs_plan.MODELS["astra"]["model"], ASTRA)
        self.assertEqual(final_runs_plan.MODELS["sol"]["model"], SOL)
        for key in ("astra", "sol"):
            with self.subTest(gate=key):
                self.assertIn(key, final_runs_plan.GATED)
                self.assertIn(final_runs_plan.MODELS[key]["model"], model_profiles.MODELS)


if __name__ == "__main__":
    unittest.main()
