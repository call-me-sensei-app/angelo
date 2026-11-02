# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The render engine switcher and AI render (1.0.8, ``render_engines``, ``ai_render``). No Blender.

On a stand-in ``bpy``, against doc/angelo/render_engines.rst. Covered: the switcher lists EEVEE and Cycles' two
presets with the scene's choice ticked, then AI render; the active preset is read back from the scene's own Cycles
values (hand-tuned values tick none); choosing Cycles applies the preset and puts a CPU scene on the GPU only when a
GPU is set up; an engine the build lacks is refused; Director's button keeps one width for every label and the
dropdown is ``flow_menu``'s ENGINES kind, each top-bar button pressed only for its own dropdown. AI render: the
camera's ratio, the accounts offered (Nano Banana, then Seedream; ready image accounts only), the remembered style,
the template (camera capture → AI render with the Style text), and the click as the approval: it starts exactly one
paid generation at the shown price, a new take when nothing changed, and refuses anything else. The running app's
proof is ``test_blender_render_engines``.
"""

import ast
import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
ADDON = ROOT / "scripts/addons_core/angelo_agent"
sys.path.insert(0, str(ROOT / "scripts/addons_core"))


class RenderEngine:
    pass


def fake_bpy():
    bpy = types.ModuleType("bpy")
    bpy.types = types.ModuleType("bpy.types")
    bpy.types.RenderEngine = RenderEngine
    bpy.types.__getattr__ = lambda name: type(name, (), {})
    bpy.props = types.ModuleType("bpy.props")
    bpy.props.__getattr__ = lambda name: (lambda *args, **kwargs: None)
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda function: function
    same = lambda text, context=None: text
    bpy.app = SimpleNamespace(translations=SimpleNamespace(pgettext_iface=same, pgettext_rpt=same, pgettext_tip=same,
                                                           locale="en_US"),
                              handlers=handlers, background=True)
    bpy.utils = SimpleNamespace(register_class=lambda cls: None, unregister_class=lambda cls: None)
    bpy.data = SimpleNamespace(node_groups=[])
    return bpy, {"bpy": bpy, "bpy.types": bpy.types, "bpy.props": bpy.props, "bpy.app": bpy.app,
                 "bpy.app.handlers": handlers}


BPY, MODULES = fake_bpy()
with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import (ai_render, ai_render_card, engine_status, flow_menu, flow_templates, flow_tree,
                              render_engines, render_glyphs)
    from angelo_agent.flow_spec import FlowError
    LOADED = {name: module for name, module in sys.modules.items() if name.split(".")[0] == "angelo_agent"}


class loaded:
    """The stand-in ``bpy`` and the modules imported with it, for code that imports a sibling when called: in
    ``sys.modules`` and as the ``angelo_agent`` package's attributes (``from . import x`` reads the attribute when
    the package already has one, so another test file's earlier import must not decide which module it gets).
    Hermetic: no vendor licence (``engine_status.VENDORS``) is known and no service answer is remembered, so nothing
    here checks this machine's own sign-in services (V-Ray's answers on a maintainer's Mac and not on CI); a test
    adds a stand-in vendor itself. Everything is put back after. ``with loaded():``, or ``start()`` and ``stop()``;
    ``extra`` replaces modules."""

    def __init__(self, **extra):
        self.extra, self.stack = extra, None

    def start(self):
        import contextlib
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.dict(sys.modules, dict(MODULES, **LOADED, **self.extra)))
        package = sys.modules["angelo_agent"]
        for name, module in LOADED.items():
            if name.startswith("angelo_agent.") and name.count(".") == 1:
                self.stack.enter_context(mock.patch.object(package, name.split(".", 1)[1], module, create=True))
        self.stack.enter_context(mock.patch.dict(engine_status.VENDORS, clear=True))
        self.stack.enter_context(mock.patch.dict(engine_status._answered, clear=True))
        self.stack.callback(engine_status.settle)
        return self

    def stop(self):
        self.stack.close()

    __enter__ = start

    def __exit__(self, *exc):
        self.stop()


def engine_module(name, path):
    module = types.ModuleType(name)
    module.__file__ = str(path)
    sys.modules[name] = module
    return name


class Cycles(RenderEngine):   # Ships with the app: its module sits under the shipped scripts.
    bl_idname, bl_label = "CYCLES", "Cycles"
    __module__ = engine_module("cycles", ADDON.parent / "cycles" / "__init__.py")


class VRay(RenderEngine):   # An add-on the person installed: its module is outside the shipped scripts.
    bl_idname, bl_label = "VRAY_RENDER_RT", "V-Ray"
    __module__ = engine_module("vray_blender.engine", Path(tempfile.gettempdir()) / "vray_blender" / "engine.py")


def scene(engine="BLENDER_EEVEE", device="CPU", **cycles):
    values = {"samples": 4096, "adaptive_threshold": 0.01, "use_adaptive_sampling": True, "use_denoising": True,
              "denoiser": "OPENIMAGEDENOISE", "preview_samples": 1024, "use_preview_denoising": False,
              "time_limit": 0.0, "denoising_use_gpu": False, "device": device}
    values.update(cycles)
    return SimpleNamespace(render=SimpleNamespace(engine=engine, resolution_x=1920, resolution_y=1080,
                                                  pixel_aspect_x=1.0, pixel_aspect_y=1.0),
                           cycles=SimpleNamespace(**values), camera=SimpleNamespace(name="Camera"))


def source(name):
    return (ADDON / name).read_text(encoding="utf-8")


class EngineTests(unittest.TestCase):
    def setUp(self):   # Their code imports bpy and its siblings when called: the stand-ins.
        patcher = loaded()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_presets_are_read_back_from_the_scene(self):
        self.assertIsNone(render_engines.preset_of(scene()), "Blender's defaults are no Angelo preset")
        for name in (render_engines.PREVIEW, render_engines.FINAL):
            values = dict(render_engines.COMMON, **render_engines.PRESETS[name])
            self.assertEqual(render_engines.preset_of(scene(**values)), name)
            self.assertIsNone(render_engines.preset_of(scene(**dict(values, samples=values["samples"] + 1))),
                              "hand-tuned: no preset ticked")
        self.assertIsNone(render_engines.preset_of(SimpleNamespace()), "no Cycles: no preset")

    def test_choosing_cycles_applies_the_preset_and_the_gpu_when_one_is_set_up(self):
        with loaded():
            from angelo_agent import render_device
        for gpu in (True, False):
            with self.subTest(gpu=gpu):
                target = scene()
                facts = {"device": "CPU", "gpu_available": "GPU (Metal: Apple M4 Pro)"} if gpu else {"device": "CPU"}
                with loaded(), mock.patch.object(render_device, "setup") as setup, \
                        mock.patch.object(render_device, "facts", return_value=facts), \
                        mock.patch.object(render_engines, "device_line", return_value=("On the GPU", False)):
                    engine, preset, _line = render_engines.apply(target, "CYCLES", render_engines.FINAL)
                setup.assert_called_once()
                self.assertEqual((engine, preset, target.render.engine), ("CYCLES", "FINAL", "CYCLES"))
                self.assertEqual(target.cycles.samples, 1024)
                self.assertTrue(target.cycles.use_denoising and target.cycles.denoising_use_gpu)
                self.assertEqual(target.cycles.device, "GPU" if gpu else "CPU",
                                 "the GPU only when Angelo set one up; no GPU keeps the CPU (the row says why)")
                self.assertEqual(render_engines.preset_of(target), "FINAL")

    def test_a_preset_change_keeps_a_cycles_scenes_own_device(self):
        from angelo_agent import render_device
        on_cpu = scene("CYCLES")
        with mock.patch.object(render_device, "setup"), mock.patch.object(
                render_device, "facts", return_value={"device": "CPU", "gpu_available": "GPU (Metal: M4)"}), \
                mock.patch.object(render_engines, "device_line", return_value=None):
            render_engines.apply(on_cpu, "CYCLES", render_engines.FINAL)
        self.assertEqual(on_cpu.cycles.device, "CPU", "a CPU chosen on a Cycles scene is the person's")

    def test_cycles_without_a_preset_starts_on_preview_and_keeps_one_already_chosen(self):
        with loaded():
            from angelo_agent import render_device
        with loaded(), mock.patch.object(render_device, "setup"), \
                mock.patch.object(render_device, "facts", return_value={}), \
                mock.patch.object(render_engines, "device_line", return_value=None):
            fresh = scene()
            self.assertEqual(render_engines.apply(fresh, "CYCLES")[1], "PREVIEW")
            final = scene(**dict(render_engines.COMMON, **render_engines.PRESETS["FINAL"]))
            self.assertEqual(render_engines.apply(final, "CYCLES")[1], "FINAL")
            eevee = scene("CYCLES")
            self.assertEqual(render_engines.apply(eevee, "BLENDER_EEVEE"), ("BLENDER_EEVEE", None, None))

    def test_an_engine_the_build_lacks_is_refused(self):
        with self.assertRaises(ValueError):
            render_engines.apply(scene(), "NOT_INSTALLED")

    def test_the_switcher_lists_eevee_cycles_presets_then_ai_render(self):
        final = scene("CYCLES", **dict(render_engines.COMMON, **render_engines.PRESETS["FINAL"]))
        with mock.patch.object(render_engines, "device_line", return_value=("On the GPU: M4 (Metal)", False)):
            rows = render_engines.menu_rows(SimpleNamespace(scene=final))
        self.assertEqual(rows[0]["kind"], "header")
        actionable = [row for row in rows if row["kind"] == "row"]
        self.assertEqual([row["value"] for row in actionable],
                         ["engine:BLENDER_EEVEE", "engine:CYCLES:PREVIEW", "engine:CYCLES:FINAL",
                          "engine:BLENDER_WORKBENCH", "engine:VRAY_RENDER_RT", "ai_render"],
                         "every registered engine: Blender's, then the add-on's, then AI render")
        self.assertEqual([row.get("selected", False) for row in actionable], [False, False, True, False, False, False])
        self.assertEqual(actionable[-1]["operator"], "angelo.ai_render")
        vray = actionable[4]
        self.assertEqual((vray["label"], vray["translate"], vray["subtitle"]),
                         ("V-Ray", False, (render_engines.ADDON_WHY, {"module": "vray_blender"})),
                         "named as its add-on names it, with the add-on, never translated")
        # A vendor licence (a stand-in, never this machine's service) whose sign-in service did not answer.
        engine_status.VENDORS["vray_blender"] = engine_status.Vendor("Chaos", lambda: (True, engine_status.PAID),
                                                                     "vray.account", service=lambda: False)
        engine_status._answered["vray_blender"] = (time.monotonic(), False)
        with mock.patch.object(render_engines, "device_line", return_value=("On the GPU: M4 (Metal)", False)):
            noted = [row for row in render_engines.menu_rows(SimpleNamespace(scene=final)) if row["kind"] == "row"]
        self.assertEqual(noted[4]["subtitle"], (engine_status.ROW_SERVICE, {"vendor": "Chaos"}),
                         "a vendor's silent service is said in its row")
        self.assertEqual(dict(actionable[2]["properties"]), {"engine": "CYCLES", "preset": "FINAL"})
        self.assertIn("On the GPU", actionable[1]["subtitle"][1]["device"], "a Cycles row says where it renders")
        for row in actionable:
            self.assertIn(row["glyph"], render_glyphs.GLYPHS)

    def test_the_button_keeps_one_width_and_names_the_choice(self):
        labels = render_engines.every_label()
        for target in (scene(), scene("CYCLES", **dict(render_engines.COMMON, **render_engines.PRESETS["PREVIEW"]))):
            self.assertIn(render_engines.current_label(target), labels)
        body = source("render_engines.py")
        self.assertIn('fitted_row(layout, context, "engine", label, also=() if compact else every_label())', body)
        self.assertIn("slot.alignment = 'LEFT'", body, "the button hugs its label at the slot's left")
        self.assertIn("flow_menu.is_open(context.window, flow_menu.ENGINES)", body)
        director = source("director.py")
        self.assertIn("render_engines.draw_top_bar(self.layout, context, level >= top_bar_fit.STUDIO_ICONS)",
                      director)

    def test_the_dropdown_is_flow_menus_engines_kind_and_buttons_press_for_their_own(self):
        window = SimpleNamespace(scene=scene(), as_pointer=lambda: 7)
        with loaded(), mock.patch.object(render_engines, "device_line", return_value=None):
            self.assertEqual(flow_menu.rows(flow_menu.ENGINES, window)[0]["value"], "engines:header")
        with mock.patch.object(flow_menu, "_open", {"window": 7, "menu": flow_menu.ENGINES}):
            self.assertTrue(flow_menu.is_open(window, flow_menu.ENGINES))
            self.assertFalse(flow_menu.is_open(window, flow_menu.FLOWS), "Flows is not pressed for the engines")
            self.assertTrue(flow_menu.is_open(window))
        self.assertIn("if is_open(window, self.menu):", source("flow_menu.py"), "another button replaces the menu")
        self.assertIn("return {'CANCELLED'} if 'CANCELLED' in result else {'FINISHED'}", source("render_engines.py"),
                      "the opener ends at once: the dropdown's own operator is the modal one")

    def test_glyphs_bake_and_draw_every_choice(self):
        for glyph in render_glyphs.GLYPHS:
            pixels = render_glyphs.pixels(glyph, size=16)
            self.assertEqual(len(pixels), 16 * 16 * 4)
            self.assertGreater(sum(pixels[3::4]), 4, glyph + " draws something")
            view = SimpleNamespace(calls=[])
            view.shape = lambda *args: view.calls.append("shape")
            view.stroke = lambda *args: view.calls.append("stroke")
            self.assertTrue(render_glyphs.draw_tile(view, glyph, 0, 0))
        self.assertFalse(render_glyphs.draw_tile(SimpleNamespace(), "flow", 0, 0))

    def test_render_menu_and_registration(self):
        body = source("render_engines.py")
        self.assertIn("bpy.types.TOPBAR_MT_render.prepend(render_menu)", body)
        self.assertIn("bpy.types.TOPBAR_MT_render.remove(render_menu)", body)
        init = source("__init__.py")
        self.assertIn("render_engines.register()", init)
        self.assertIn("render_engines.unregister()", init)


GEMINI, SEEDREAM, OPENAI = ("00000000-0000-4000-8000-00000000000%d" % index for index in (1, 2, 3))


def entry(connection, model, mode, kind="IMAGE", ready=True, name=""):
    return {"connection": connection, "id": model, "mode": mode, "kind": kind, "ready": ready, "name": name or model,
            "account": connection}


NANO = entry(GEMINI, "gemini-nano-banana-2.1", "gemini_images_api")
NANO2 = entry(GEMINI, "gemini-3.1-flash-image", "gemini_images_api")
DREAM = entry(SEEDREAM, "dola-seedream-5-0-pro-260628", "seedream_api")
SUNBURST = entry(OPENAI, "gpt-image-2.5-sunburst", "openai_images_api")


class InstalledEngineTests(unittest.TestCase):
    """The installed-engines branch: any registered engine, and Auto asks before an add-on's."""
    def setUp(self):   # Their code imports bpy and its siblings when called: the stand-ins.
        patcher = loaded()
        patcher.start()
        self.addCleanup(patcher.stop)


    def test_add_on_engines_are_told_from_blenders_own(self):
        with loaded():
            from angelo_agent import render_extensions
            found = render_extensions.addon_engines()
        self.assertEqual(set(found), {"VRAY_RENDER_RT"})
        self.assertIn("V-Ray (vray_blender.engine)", found["VRAY_RENDER_RT"])
        self.assertEqual([item[0] for item in render_engines.others()], ["BLENDER_WORKBENCH", "VRAY_RENDER_RT"])
        self.assertIn("V-Ray", render_engines.every_label(), "the button is wide enough for an add-on's name too")

    def test_choosing_an_add_on_engine_sets_it_and_no_cycles_setting(self):
        target = scene()
        self.assertEqual(render_engines.apply(target, "VRAY_RENDER_RT"), ("VRAY_RENDER_RT", None, None))
        self.assertEqual((target.render.engine, target.cycles.samples, target.cycles.device),
                         ("VRAY_RENDER_RT", 4096, "CPU"))
        self.assertEqual(render_engines.current_label(target), "V-Ray")

    def test_auto_asks_before_an_edit_sets_an_add_on_engine(self):
        with loaded():
            from angelo_agent import render_extensions

        def program(engine, nested=False):
            operation = {"kind": "property_set", "target": "scn:1:1", "parameters": {"values": {"render.engine": engine,
                                                                                                 "cycles.samples": 64}}}
            return {"steps": [{"actions": [operation]}]} if nested else {"operations": [operation, [1.0, 2.0] * 50]}
        self.assertEqual(set(render_extensions.engine_edits(program("VRAY_RENDER_RT"))), {"VRAY_RENDER_RT"})
        self.assertEqual(render_extensions.engine_edits(program("VRAY_RENDER_RT", nested=True)), {},
                         "only a scene program's own operations list carries property_set")
        for engine in ("CYCLES", "BLENDER_EEVEE", "BLENDER_WORKBENCH", "NOT_INSTALLED"):
            self.assertEqual(render_extensions.engine_edits(program(engine)), {}, engine)
        self.assertEqual(render_extensions.engine_edits({"operations": []}), {})
        self.assertIn('return call.name != "angelo_scene_program" or not render_extensions.engine_edits(call.arguments)',
                      source("controller.py"), "only a scene program carries property_set")

    def test_the_engine_check_reads_a_programs_operations_once_never_a_strokes_points(self):
        with loaded():
            from angelo_agent import render_extensions
        stroke = {"operations": [{"kind": "property_set", "parameters": {"values": {"render.engine": "CYCLES"}}}],
                  "points": [[float(i), 0.0, 0.0] for i in range(20000)]}
        began = time.perf_counter()
        for _ in range(50):
            self.assertEqual(render_extensions.engine_edits(stroke), {})
        self.assertLess(time.perf_counter() - began, 0.05, "points are never walked")

    def test_the_add_on_list_is_cached_until_an_engine_registers_or_goes(self):
        with loaded():
            from angelo_agent import render_extensions
            render_extensions.addon_engines()
            with mock.patch.object(render_extensions, "_shipped_roots", side_effect=AssertionError("walked")):
                self.assertIn("VRAY_RENDER_RT", render_extensions.addon_engines(), "the same classes: cached")

            class Lux(RenderEngine):
                bl_idname, bl_label = "LUXCORE", "LuxCore"
                __module__ = engine_module("BlendLuxCore", Path(tempfile.gettempdir()) / "BlendLuxCore" / "x.py")
            try:
                self.assertIn("LUXCORE", render_extensions.addon_engines(), "a new engine is seen at once")
            finally:
                Lux.is_registered = False
            self.assertNotIn("LUXCORE", render_extensions.addon_engines(), "and a removed one goes")

    def test_blenders_other_shipped_engines_are_listed_as_its_own(self):
        class Storm(RenderEngine):
            bl_idname, bl_label = "HYDRA_STORM", "Hydra Storm"
            __module__ = engine_module("hydra_storm", ADDON.parent / "hydra_storm" / "__init__.py")
        try:
            listed = render_engines.others()
            self.assertEqual([item[0] for item in listed], ["BLENDER_WORKBENCH", "HYDRA_STORM", "VRAY_RENDER_RT"])
            self.assertEqual(listed[1][2], "", "shipped: not an add-on")
        finally:
            Storm.is_registered = False

    def test_the_agents_render_engine_accepts_any_registered_engine(self):
        from angelo_agent import action_properties
        self.assertIs(action_properties.PROPERTY_PATHS["render.engine"][3], action_properties.DYNAMIC,
                      "RNA decides: Blender's engines and every installed add-on's")
        self.assertNotIn("installed_engines", source("agent_guidance.py"), "no prompt growth: only the settings "
                         "read names them, and only when an add-on engine is installed")
        body = source("scene_settings.py")
        self.assertIn("**_installed_engines()", body)
        self.assertIn("} if found else {}", body, "a stock install reads nothing more")


class AiRenderTests(unittest.TestCase):
    def test_the_camera_ratio_is_the_nearest_image_ratio(self):
        cases = {(1920, 1080): "16:9", (1080, 1920): "9:16", (1000, 1000): "1:1", (2390, 1000): "16:9",
                 (1440, 1080): "4:3", (1080, 1440): "3:4", (0, 10): "1:1"}
        for (width, height), ratio in cases.items():
            self.assertEqual(ai_render.aspect(width, height), ratio, (width, height))
        self.assertEqual(ai_render.camera_aspect(scene()), "16:9")

    def test_accounts_offer_nano_banana_then_seedream_and_only_ready_image_ones(self):
        offered = ai_render.accounts([SUNBURST, DREAM, entry(GEMINI, "x", "gemini_images_api", ready=False), NANO2,
                                      NANO, entry("m", "meshy-7", "meshy_api", kind="3D")])
        self.assertEqual([e["id"] for e in offered], [NANO["id"], DREAM["id"], NANO2["id"], SUNBURST["id"]])
        self.assertIs(ai_render.chosen(offered, {"connection": SEEDREAM, "model": DREAM["id"]}), DREAM)
        self.assertIs(ai_render.chosen(offered, {"connection": "gone", "model": "gone"}), NANO)
        self.assertIsNone(ai_render.chosen([], {}))

    def test_the_last_style_is_remembered_per_person(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ai_render.FILE
            self.assertEqual(ai_render.remembered(path), {"style": "", "connection": "", "model": ""})
            ai_render.remember("ink wash, dusk" + "x" * 5000, GEMINI, NANO["id"], path=path)
            found = ai_render.remembered(path)
            self.assertTrue(found["style"].startswith("ink wash, dusk"))
            self.assertEqual(len(found["style"]), ai_render.MAX_STYLE)
            self.assertEqual((found["connection"], found["model"]), (GEMINI, NANO["id"]))
            path.write_text("{broken", encoding="utf-8")
            self.assertEqual(ai_render.remembered(path)["style"], "", "a damaged file starts fresh")

    def test_the_template_captures_the_camera_and_restyles_it(self):
        flow, unresolved = flow_templates.instantiate("ai_render", [DREAM, NANO])
        self.assertEqual(unresolved, [])
        nodes = {node["id"]: node for node in flow["nodes"]}
        capture, step = nodes["Camera"], nodes["AI render"]
        self.assertEqual((capture["type"], capture["framing"], capture["shading"]), ("SCENE_CAPTURE", "CAMERA", "MATERIAL"))
        self.assertEqual((step["type"], step["model"], step["options"]["resolution"]),
                         ("GENERATE_IMAGE", NANO["id"], "2K"), "Nano Banana first, at 2K")
        self.assertEqual(step["prompt"], flow_templates.RESTYLE_PROMPT)
        links = {(link["from"], link["to"], link["input"]) for link in flow["links"]}
        self.assertEqual(links, {("Camera", "AI render", "references"), ("Style", "AI render", "prompt")})
        self.assertEqual(flow_templates.instantiate("ai_render", [DREAM])[0]["nodes"][2]["model"], DREAM["id"])

    def test_the_price_is_the_flow_tables(self):
        self.assertAlmostEqual(ai_render.price(DREAM, "16:9"), 0.09)
        self.assertAlmostEqual(ai_render.price(NANO2, "1:1"), 0.101)
        self.assertEqual(ai_render.options_for(DREAM, "9:16")["aspect"], "9:16")


def line(node="AI render", count=1, price=0.09, action="GENERATE"):
    return {"node": node, "count": count, "price_each": price, "action": action, "review": False, "label": node}


class StartTests(unittest.TestCase):
    def setUp(self):
        self.tree = SimpleNamespace(name="AI render", nodes={"AI render": SimpleNamespace(take=0)})
        self.plans = []
        self.started = []
        for name, value in (("ensure_tree", lambda *args: self.tree), ("configure", lambda *args: None),
                            ("remember", lambda *args, **kwargs: None)):
            patcher = mock.patch.object(ai_render, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = loaded(bpy=SimpleNamespace(data=None))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.written = []
        for name, value in (("tree_flow", lambda tree: "the flow before"),
                            ("write_flow", lambda tree, flow: self.written.append(flow))):
            patcher = mock.patch.object(flow_tree, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.active = []

    def plan(self, *results):
        queue = list(results)

        def plan(tree, window):
            self.plans.append(tree.nodes["AI render"].take)
            return {"nodes": [], "links": []}, {"lines": queue.pop(0)}
        return plan

    def start(self, plan, shown=0.09, camera=True):
        window = SimpleNamespace(scene=scene() if camera else SimpleNamespace(camera=None))
        return ai_render.start(window, "ink", DREAM, shown, plan=plan, active_runs=lambda name: self.active,
                               start_run=lambda tree, selection: self.started.append(selection) or "run")

    def test_the_click_starts_exactly_the_shown_generation(self):
        self.assertEqual(self.start(self.plan([line()])), "run")
        self.assertEqual(len(self.started), 1)
        self.assertEqual([l["node"] for l in self.started[0]["now"]], ["AI render"])

    def test_render_again_with_nothing_changed_is_a_new_take(self):
        self.start(self.plan([line(action="REUSE")], [line()]))
        self.assertEqual(self.plans, [0, 1], "planned again after advancing the take")

    def test_anything_but_the_shown_generation_is_refused(self):
        for lines, shown in (([line(), line("Extra", price=0.5)], 0.09), ([line(price=0.5)], 0.09),
                             ([line(count=2)], 0.09), ([line("Other")], 0.09)):
            with self.subTest(lines=lines), self.assertRaises(FlowError):
                self.start(self.plan(lines), shown)
        self.assertEqual(self.started, [])

    def test_a_blocked_or_running_step_is_refused_without_a_new_take(self):
        for action in ("BLOCKED", "RUNNING"):
            with self.subTest(action=action), self.assertRaises(FlowError):
                self.start(self.plan([dict(line(action=action), reason="no account")]))
        self.assertEqual(self.plans, [0, 0], "no take advanced")
        self.assertEqual(self.started, [])

    def test_a_refusal_puts_the_flow_and_its_take_back(self):
        with self.assertRaises(FlowError):
            self.start(self.plan([line(action="REUSE")], [line(price=0.5)]))   # A new take, then the price differs.
        self.assertEqual(self.tree.nodes["AI render"].take, 0, "the take is as it was")
        self.assertEqual(self.written, ["the flow before"], "the steps are as they were")
        self.start(self.plan([line()]))
        self.assertEqual(self.written, ["the flow before"], "a started run keeps its configuration")

    def test_a_run_of_this_flow_still_going_refuses_another(self):
        self.active.append("queued behind a full library")
        with self.assertRaises(FlowError) as raised:
            self.start(self.plan([line()]))
        self.assertEqual(str(raised.exception), ai_render.RUNNING_ALREADY)
        self.assertEqual((self.plans, self.started), ([], []))

    def test_no_camera_is_refused_before_anything_runs(self):
        with self.assertRaises(FlowError) as raised:
            self.start(self.plan([line()]), camera=False)
        self.assertEqual(str(raised.exception), ai_render.NO_CAMERA)
        self.assertEqual(self.plans, [])

    def test_unknown_prices_match_only_unknown(self):
        self.assertTrue(ai_render._same_price(None, None))
        self.assertFalse(ai_render._same_price(None, 0.1))
        self.assertTrue(ai_render._same_price(0.101, 0.1012))

    def test_result_reads_the_run(self):
        running = SimpleNamespace(finished=False, steps={"AI render": {"assets": []}})
        done = SimpleNamespace(finished=True, steps={"AI render": {"assets": ["a1"], "chosen": None}})
        failed = SimpleNamespace(finished=True, steps={"AI render": {"assets": [], "error": "quota"}})
        self.assertEqual(ai_render.result(running)[0], "running")
        self.assertEqual(ai_render.result(done), ("done", "a1", None))
        self.assertEqual(ai_render.result(failed), ("failed", None, "quota"))
        self.assertEqual(ai_render.result(None)[0], "failed")


class WiringTests(unittest.TestCase):
    def test_the_card_approves_only_the_flow_it_describes(self):
        flow, _ = flow_templates.instantiate("ai_render", [NANO])
        self.assertTrue(ai_render.wired_as_shown(flow))
        other = {"id": "Other", "type": "IMAGE", "source": None}
        changes = (
            lambda f: f["links"].append({"from": "Other", "to": "AI render", "input": "references"}),
            lambda f: f["links"].remove(next(l for l in f["links"] if l["from"] == "Camera")),
            lambda f: next(n for n in f["nodes"] if n["id"] == "AI render").update(prompt="something else"),
            lambda f: next(n for n in f["nodes"] if n["id"] == "Camera").update(framing="VIEWPORT"),
            lambda f: f["nodes"].remove(next(n for n in f["nodes"] if n["id"] == "Style")))
        for change in changes:
            changed = json.loads(json.dumps(dict(flow, nodes=flow["nodes"] + [other])))
            change(changed)
            self.assertFalse(ai_render.wired_as_shown(changed))


class Event(SimpleNamespace):
    def __init__(self, kind, unicode=""):
        super().__init__(type=kind, value='PRESS', unicode=unicode, ctrl=False, oskey=False, shift=False, alt=False)


class CardTests(unittest.TestCase):
    def setUp(self):
        patcher = loaded()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.card = ai_render_card.AiRenderCard(scene(), [DREAM], {"style": "ink", "connection": "", "model": ""})
        self.host = SimpleNamespace(redraw=lambda: None, close=lambda how="": None, after=lambda *a: None,
                                    window=lambda: None)

    def test_return_in_the_style_ends_typing_and_never_renders(self):
        with mock.patch.object(ai_render, "start") as start:
            self.assertTrue(self.card.key_event(Event('A', "x"), self.host))
            self.assertTrue(self.card.key_event(Event('RET'), self.host))
        start.assert_not_called()
        self.assertEqual((self.card.style.text, self.card.style.editing), ("inkx", False))

    def test_render_starts_once_and_a_repeat_never_starts_another(self):
        run = SimpleNamespace(id="r1")
        timers = SimpleNamespace(is_registered=lambda f: False, register=mock.Mock())
        fake = SimpleNamespace(ops=SimpleNamespace(ed=SimpleNamespace(undo_push=mock.Mock())),
                               app=SimpleNamespace(timers=timers))
        with mock.patch.object(ai_render, "start", return_value=run) as start, \
                mock.patch.object(ai_render_card, "bpy", fake):
            self.card.act("render", None, self.host)
            self.card.act("render", None, self.host)
        self.assertEqual(start.call_count, 1)
        self.assertEqual(self.card.phase, "running")
        timers.register.assert_called_once()   # The closed card's watcher starts with the run, not with the card.
        fake.ops.ed.undo_push.assert_called_once()


class CardSourceTests(unittest.TestCase):
    def test_card_is_registered_in_the_plus_menu_and_starts_through_ai_render(self):
        body = source("ai_render_card.py")
        tree = ast.parse(body)
        calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)}
        self.assertIn("start", calls)
        self.assertIn('"ai_render", TITLE', body)
        self.assertIn("ai_render_card.register()", source("__init__.py"))


if __name__ == "__main__":
    unittest.main()
