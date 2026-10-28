# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What a person reads before and while a model works: the model's name and a script's reach.

Pure host tests of the composer chip's model lookup and of the approval card
of a reviewed script that uses application preferences. No Blender or model.
"""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
from angelo_agent.approvals import approval_message
from angelo_agent.chat_popovers import model_name
from angelo_agent.chat_settings import normalize_models, selected_model
from angelo_agent.fallback_categories import fallback_categories, uses_preferences

# Claude Code's catalog lists aliases; a chat can still name the full model ID
# (a connection's Model field, a harness, a chat saved by another build).
CLAUDE = normalize_models([
    {"value": "default", "displayName": "Default (recommended)", "supportedEffortLevels": ["low", "medium", "high"]},
    {"value": "opus", "displayName": "Opus 5.5", "supportedEffortLevels": ["low", "medium", "high"]},
    {"value": "sonnet", "displayName": "Sonnet 5.5", "supportedEffortLevels": ["low", "medium", "high"]}], "claude")
CODEX = normalize_models([{"model": "gpt-6.1-sol", "displayName": "GPT-6.1-Sol", "isDefault": True}])


def chat(models, model, setting=""):
    return SimpleNamespace(state={"options": {"model": model}}, settings={"model": setting}, models=models,
                           selection_settings={"model": setting}, selection_models=models)


class ModelChipTests(unittest.TestCase):
    def test_a_full_model_id_shows_the_name_its_alias_has_in_the_catalog(self):
        model = selected_model(chat(CLAUDE, "claude-opus-5-5"))
        self.assertEqual((model["id"], model_name(model)), ("opus", "Opus 5.5"))
        self.assertEqual(selected_model(chat(CLAUDE, "", setting="claude-sonnet-5-5"))["id"], "sonnet")

    def test_a_turn_reads_the_same_entry_for_its_capabilities(self):
        controller = chat(CLAUDE, "")
        model = selected_model(controller, {"model": "claude-opus-5-5"})
        self.assertEqual((model["id"], model["fast_tier"], model["images"]), ("opus", "native-fast", True))

    def test_exact_ids_win_and_unknown_ids_are_never_guessed(self):
        listed = CLAUDE + normalize_models([{"value": "claude-opus-5-5", "displayName": "Opus 5.5 (pinned)"}], "claude")
        self.assertEqual(selected_model(chat(listed, "claude-opus-5-5"))["name"], "Opus 5.5 (pinned)")
        self.assertIsNone(selected_model(chat(CLAUDE, "claude-opus-4-1")), "Not a known alias: the raw ID is shown")
        self.assertIsNone(selected_model(chat(CODEX, "claude-opus-5-5")), "Another runtime's catalog has no such model")
        self.assertEqual(selected_model(chat(CODEX, "gpt-6.1-sol"))["id"], "gpt-6.1-sol")
        self.assertEqual(selected_model(chat(CLAUDE, ""))["id"], "default")


RUN = """import bpy
cprefs = bpy.context.preferences.addons["cycles"].preferences
if cprefs.compute_device_type == 'NONE':
    cprefs.compute_device_type = 'METAL'
cprefs.refresh_devices()
for device in cprefs.devices:
    device.use = True
bpy.context.scene.cycles.device = 'GPU'
"""


class PreferenceScriptTests(unittest.TestCase):
    def test_scripts_that_use_preferences_are_recognized_and_others_are_not(self):
        for code in (RUN, "bpy.ops.preferences.addon_enable(module='node_wrangler')", "bpy.ops.wm.save_userpref()",
                     "context.preferences.view.language = 'ja_JP'", "bpy.ops.wm.read_factory_userpref()",
                     "prefs = bpy.context.preferences\nprefs.edit.undo_steps = 8"):
            with self.subTest(code=code[:40]):
                self.assertTrue(uses_preferences(code))
        for code in ("scene.cycles.device = 'GPU'", "obj.modifiers.new('Bevel', 'BEVEL')", "result = 'preferences'",
                     "def broken(:", "note = 'bpy.context.preferences'"):
            with self.subTest(code=code[:40]):
                self.assertFalse(uses_preferences(code))
        self.assertEqual(fallback_categories(RUN), ("render_world",), "The receipt's closed categories are unchanged")

    def test_the_approval_card_says_what_undo_and_snapshots_do_not_cover(self):
        card = approval_message("angelo_execute_python", {"description": "Enable the GPU", "code": RUN})
        self.assertIn("preferences", card["warning"])
        self.assertIn("neither Undo nor a snapshot", card["warning"])
        self.assertTrue(card["warning"].startswith("A script can do anything Angelo can"), "Added to the usual warning")
        plain = approval_message("angelo_execute_python", {"description": "Bevel", "code": "obj.modifiers.new('B', 'BEVEL')"})
        self.assertNotIn("preferences", plain["warning"])


if __name__ == "__main__":
    unittest.main()
