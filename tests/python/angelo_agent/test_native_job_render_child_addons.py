# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which add-on handlers keep a heavy render in the application (job_render_route._addon_handlers). No Blender needed.

A render goes to a child Angelo started with ``--factory-startup``, which loads the factory add-ons and nothing else,
so only a handler of an add-on the child does NOT load can make its copy render something else. The rule named three
modules by hand (cycles, angelo_agent, angelo_render_device); on 1.0.11 the real app kept every render in the
application because 1.0.10's game physics, Angelo code inside the factory glTF add-on
(``io_scene_gltf2.angelo_physics.fitting._on_depsgraph``), registers a depsgraph handler, which the child has too.
The factory list mirrors Blender's own (``BKE_blendfile_userdef_from_defaults``), pinned to it here.
"""

import suite_isolation  # noqa: F401  First: this test never sees a real provider login (provider_isolation.py).

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path[:0] = [str(ROOT / "scripts/addons_core"), str(HERE)]
from angelo_agent import job_render_route  # noqa: E402

BLENDFILE = ROOT / "source/blender/blenkernel/intern/blendfile.cc"


def handler(module, path):
    """A handler function whose code lives at ``path`` and whose module is ``module``."""
    namespace = {}
    exec(compile("def on_update(*args):\n    pass\n", path, "exec"), namespace)
    function = namespace["on_update"]
    function.__module__ = module
    return function


def found(*functions):
    lists = SimpleNamespace(depsgraph_update_post=list(functions), render_pre=[], render_post=[])
    fake = SimpleNamespace(app=SimpleNamespace(handlers=lists))
    with mock.patch.dict(sys.modules, {"bpy": fake}):
        return job_render_route._addon_handlers()


class FactoryAddons(unittest.TestCase):
    ADDONS = "/Applications/Angelo.app/Contents/Resources/5.2/scripts/addons_core/"

    def test_a_factory_add_ons_handler_runs_in_the_child_too(self):
        physics = handler("io_scene_gltf2.angelo_physics.fitting", self.ADDONS + "io_scene_gltf2/angelo_physics/x.py")
        fbx = handler("io_scene_fbx.export_fbx_bin", self.ADDONS + "io_scene_fbx/export_fbx_bin.py")
        self.assertEqual([], found(physics, fbx))

    def test_an_add_on_the_child_does_not_load_still_keeps_the_render_here(self):
        other = handler("rigify.utils", self.ADDONS + "rigify/utils.py")
        user = handler("my_tool", "/Users/someone/Library/Application Support/Angelo/5.2/scripts/addons/my_tool.py")
        self.assertEqual(["rigify.utils.on_update", "my_tool.on_update"], found(other, user))

    def test_angelos_own_modules_and_startup_scripts_run_in_the_child(self):
        own = handler("angelo_agent.render_device", self.ADDONS + "angelo_agent/render_device.py")
        startup = handler("bl_operators.node", "/Applications/Angelo.app/Contents/Resources/5.2/scripts/startup/x.py")
        self.assertEqual([], found(own, startup))

    def test_the_factory_list_is_blenders(self):
        text = BLENDFILE.read_text(encoding="utf-8")
        block = re.search(r"/\* Add-ons\. \*/.*?const char \*addons\[\] = \{(.*?)\};", text, re.S)
        self.assertIsNotNone(block, "BKE_blendfile_userdef_from_defaults' add-on list moved")
        self.assertEqual(set(re.findall(r'"([a-z0-9_]+)"', block.group(1))), set(job_render_route.FACTORY_ADDONS))


if __name__ == "__main__":
    unittest.main()
