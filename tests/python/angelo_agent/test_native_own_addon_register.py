# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Angelo's own add-on registers everything in a window and nothing in a background Angelo child.

A background process started with ``ANGELO_HEADLESS=1`` (a worker Blender, ``worker_process``) may list
``angelo_agent`` in the person's preferences, so Blender enables it at startup; the add-on's early return keeps the full
add-on from running there. Without Blender: the add-on's ``register()``/``unregister()`` against stubbed modules.
Kept from ``test_native_headless_preferences.py`` (removed with the headless MCP child in 2.0.1) because the early
return still guards every worker.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).
import platform_env

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts/addons_core"))
import angelo_agent  # noqa: E402


class Recorder:
    """Every module ``register()``/``unregister()`` reaches, recording each call (``name.function``)."""

    def __init__(self, calls, name):
        self._calls, self._name = calls, name

    def __getattr__(self, attribute):
        if attribute.startswith("__"):
            raise AttributeError(attribute)
        return lambda *arguments, **options: self._calls.append(self._name + "." + attribute)


class OwnAddonTests(unittest.TestCase):
    """Break-it HIGH (2026-10-23): a person's preferences list ``angelo_agent``, so Blender enables it at a background
    child's startup; the full add-on must not run there. The early return is inert in a window: pinned for every
    branch."""

    MODULES = ("qa_environment", "tls_distrust", "data_migration", "product_env", "state_migration", "console", "studio", "ui",
               "about_card", "app_notices", "font_watch", "job_ui", "edit_tool_status", "refused_saves", "prefs_reload",
               "pycache_retention",
               "settings_import_ui", "studio_assets_host", "asset_shelf_add", "chat_launcher", "chat_toggle",
               "media_shelf", "studio_toggle", "shelf_empty", "export_baked", "web_export_ui", "web_shader_ui",
               "engine_link_ui", "game_physics_ui", "media_shelf_cover",
               "shot_capture_ui", "codex_runtime", "session_rail_input", "timer_jobs", "layout_hold", "document_identity",
               "render_engines", "ai_render_card", "bake_maps_card", "render_region", "photo_drop", "library_ui",
               "tension_map", "look_ui", "scatter_ui", "uv_checker", "retopology_ui", "skeleton_check_ui", "engine_suggest_ui", "director_select_through",
               "feedback_ui", "change_clock", "restored_document", "tool_specs", "write_turn", "fab_convert",
               "version_carry_over", "learn_ui", "script_confinement_blender", "grant_hints", "markup_ui", "rigging_ops",
               "ik_controls", "loops_menu", "uv_layout_ui", "shape_cut_tool", "planning_ui", "context_folder_ui", "rules_ui",
               "splat_mesh", "collision_fit", "wrapped_ops",
               "headless_window", "headless_window_app", "headless_window_lock", "desktop_linux")

    def run_add_on(self, background, environ):
        calls = []
        fake = SimpleNamespace(app=SimpleNamespace(background=background),
                               utils=SimpleNamespace(user_resource=lambda kind: "/config"))
        stubs = {name: Recorder(calls, name) for name in self.MODULES}
        stubs["product_env"].deprecated_in_use = lambda: []
        saved = dict(os.environ)
        with patch.dict(sys.modules, {"bpy": fake, "_console_angelo": None, "_console_angelo_genai": None,
                                      **{"angelo_agent." + name: stub for name, stub in stubs.items()}}), \
                platform_env.cleared(environ):
            missing = object()
            before = {name: getattr(angelo_agent, name, missing) for name in stubs}
            for name, stub in stubs.items():
                setattr(angelo_agent, name, stub)
            try:
                angelo_agent.register()
                registered = list(calls)
                del calls[:]
                angelo_agent.unregister()
            finally:
                for name, value in before.items():
                    if value is missing:
                        delattr(angelo_agent, name)
                    else:
                        setattr(angelo_agent, name, value)
        self.assertEqual(dict(os.environ), saved)
        return registered, list(calls)

    def test_a_window_registers_everything_whatever_the_variable_says(self):
        for environ in ({}, {"ANGELO_HEADLESS": "1"}):
            registered, unregistered = self.run_add_on(False, environ)
            for expected in ("tls_distrust.install", "timer_jobs.register", "layout_hold.register", "ui.register",
                             "document_identity.register",
                             "app_notices.register",
                             "job_ui.register", "edit_tool_status.register", "refused_saves.register",
                             "pycache_retention.schedule", "studio_assets_host.register",
                             "shelf_empty.register", "export_baked.register", "web_shader_ui.register",
                             "web_export_ui.register",
                             "shot_capture_ui.register", "photo_drop.register", "tension_map.register", "look_ui.register", "scatter_ui.register",
                             "uv_checker.register", "retopology_ui.register",
                             "skeleton_check_ui.register", "engine_suggest_ui.register",
                             "director_select_through.register", "fab_convert.register", "learn_ui.register",
                             "planning_ui.register", "rigging_ops.register", "ik_controls.register",
                             "context_folder_ui.register", "rules_ui.register",
                             "version_carry_over.carry_over_on_startup", "wrapped_ops.registry"):
                self.assertIn(expected, registered, environ)
            for expected in ("shelf_empty.unregister", "web_export_ui.unregister", "shot_capture_ui.unregister",
                             "ui.unregister", "refused_saves.unregister", "photo_drop.unregister",
                             "tension_map.unregister", "look_ui.unregister", "scatter_ui.unregister", "uv_checker.unregister",
                             "retopology_ui.unregister", "edit_tool_status.unregister",
                             "skeleton_check_ui.unregister",
                             "codex_runtime.close_all", "layout_hold.unregister", "timer_jobs.unregister",
                             "engine_suggest_ui.unregister", "director_select_through.unregister",
                             "learn_ui.unregister", "planning_ui.unregister", "rigging_ops.unregister", "ik_controls.unregister",
                             "context_folder_ui.unregister", "rules_ui.unregister"):
                self.assertIn(expected, unregistered, environ)

    def test_a_background_process_without_the_variable_registers_normally(self):
        registered, unregistered = self.run_add_on(True, {"ANGELO_HEADLESS": "0"})
        self.assertIn("ui.register", registered)
        self.assertIn("ui.unregister", unregistered)

    def test_every_module_register_reaches_is_stubbed(self):
        """A module register() imports but this list misses runs for real against the stub bpy (VM CI 6d0728a:
        fab_convert read bpy.app.binary_path)."""
        import ast
        tree = ast.parse((Path(angelo_agent.__file__)).read_text(encoding="utf-8"))
        reached = {alias.name for function in tree.body if isinstance(function, ast.FunctionDef)
                   and function.name in ("register", "unregister") for node in ast.walk(function)
                   if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None
                   for alias in node.names}
        self.assertEqual(sorted(reached - set(self.MODULES)), [])

    def test_the_headless_child_registers_nothing(self):
        self.assertEqual(self.run_add_on(True, {"ANGELO_HEADLESS": "1"}), ([], []))


if __name__ == "__main__":
    unittest.main()
