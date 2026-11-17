# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Every live preview that re-exports a scene after a change hears every way a change arrives (``scene_changes``;
doc/angelo/engine_link.rst, "Live scenes"). Read from the source, no Blender.

Found by the Godot showcase (2026-11-15): a script ending in ``scene.frame_set()`` was never sent over the engine link.
Blender evaluates a frame change with ``BKE_scene_graph_update_for_newframe_ex``, which flushes the edits tagged
before it and runs only ``frame_change_post``; the link and the web preview listened on ``depsgraph_update_post``
alone. So:

* a handler that asks ``scene_changes.matters`` is registered through ``scene_changes.watch`` (every list a change
  arrives on, ``frame_change_post`` among them), never on one handler list by hand;
* every such preview also hears ``scene_changes.edited``: what Angelo's executor says after a scene-editing tool or
  script, for an edit Blender never tags (a custom property set from Python) and so no handler sees;
* the executor says it after every tool that edits the scene.

``test_blender_engine_link_frame_changes`` runs it in Blender.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import ast
import unittest
from pathlib import Path

ADDON = Path(__file__).resolve().parents[3] / "scripts/addons_core/angelo_agent"


def tree(name):
    return ast.parse((ADDON / (name + ".py")).read_text(encoding="utf-8"))


def calls(node, attribute):
    """The calls in ``node`` whose function ends in ``.attribute`` (or is named ``attribute``)."""
    return [sub for sub in ast.walk(node) if isinstance(sub, ast.Call)
            and getattr(sub.func, "attr", getattr(sub.func, "id", None)) == attribute]


def consumers():
    """{module: handler names} of every function in the add-on that asks ``scene_changes.matters``."""
    found = {}
    for path in sorted(ADDON.glob("*.py")):
        if path.stem == "scene_changes":
            continue
        module = ast.parse(path.read_text(encoding="utf-8"))
        for function in module.body:
            if isinstance(function, ast.FunctionDef) and any(
                    ast.unparse(call.func) == "scene_changes.matters" for call in calls(function, "matters")):
                found.setdefault(path.stem, set()).add(function.name)
    return found


class Watch(unittest.TestCase):
    def test_scene_changes_watches_every_list_a_change_arrives_on(self):
        source = (ADDON / "scene_changes.py").read_text(encoding="utf-8")
        for name in ("def watch(", "def unwatch(", "depsgraph_update_post", "frame_change_post", "def edited(",
                     "def on_edit(", "def off_edit("):
            self.assertIn(name, source, "scene_changes has no %s" % name)

    def test_every_preview_handler_is_registered_through_watch(self):
        found = consumers()
        self.assertTrue(found, "nothing asks scene_changes.matters: the scan reads the wrong thing")
        for module, handlers in found.items():
            body = tree(module)
            watched = {ast.unparse(call.args[0]) for call in calls(body, "watch") if call.args}
            by_hand = [ast.unparse(node) for node in ast.walk(body) if isinstance(node, ast.Attribute)
                       and node.attr in ("depsgraph_update_post", "frame_change_post")]
            with self.subTest(module=module):
                self.assertTrue(handlers <= watched, "%s: %s not registered through scene_changes.watch"
                                % (module, sorted(handlers - watched)))
                self.assertFalse(by_hand, "%s registers on one handler list by hand: %s" % (module, by_hand))

    def test_every_preview_hears_the_executors_edit_notice(self):
        for module in consumers():
            with self.subTest(module=module):
                self.assertTrue(calls(tree(module), "on_edit"), "%s does not hear scene_changes.edited" % module)
                self.assertTrue(calls(tree(module), "off_edit"), "%s never stops hearing it" % module)

    def test_the_executor_says_so_after_every_scene_edit(self):
        executor = next(node for node in ast.walk(tree("blender_tools")) if isinstance(node, ast.ClassDef)
                        and node.name == "BlenderExecutor")
        execute = next(item for item in executor.body if isinstance(item, ast.FunctionDef) and item.name == "execute")
        told = calls(execute, "edited")
        self.assertTrue(told, "BlenderExecutor.execute never calls scene_changes.edited")
        guarded = [node for node in ast.walk(execute) if isinstance(node, ast.If)
                   and any(call in list(ast.walk(node)) for call in told)]
        self.assertTrue(any("mutation" in ast.unparse(node.test) for node in guarded),
                        "scene_changes.edited is said for scene edits (the executor's own ``mutation``)")


if __name__ == "__main__":
    unittest.main()
