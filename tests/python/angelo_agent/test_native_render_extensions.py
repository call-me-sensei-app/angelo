# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Renders and bakes that would run add-on or script Python need the person's render approval, bound to that code.

The rules: a preview that executes custom Python handlers or
scripted drivers requires the existing render approval, and ordinary previews
remain automatic; an approval covers the identified render in the current
document, whose engine, handlers and drivers are rechecked before dispatch and
need fresh approval when they changed while queued; opaque extension internals
alone never ask twice.
"""

import functools
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from animation_fakes import Curve, driver  # noqa: E402
from rendered_view_case import LookCase  # noqa: E402  (puts the add-on on the path)
from rendered_view_fakes import msgbus_listing  # noqa: E402
from angelo_agent import render_extensions as policy  # noqa: E402
from angelo_agent.job_spec import JobRequestError  # noqa: E402


def code_from(filename, name="handler", qualname=None):
    """A function whose code lives in ``filename``, as a module's would."""
    namespace = {"__name__": "addon_module"}
    exec(compile("def %s(*args):\n    return None\n" % name, filename, "exec"), namespace)
    function = namespace[name]
    function.__qualname__ = qualname or name
    return function


class Scene(dict):
    """A scene: custom properties as a mapping (the restore tag), settings as attributes."""

    def __init__(self):
        super().__init__()
        self.name, self.render = "Scene", SimpleNamespace(engine="CYCLES")


class Stand(unittest.TestCase):
    """A stand-in ``bpy`` with handler lists, render engines and data-blocks, and a shipped scripts folder."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name).resolve()
        self.shipped, self.user = root / "shipped", root / "user"
        self.handlers = SimpleNamespace(**{name: [] for names in policy.HANDLERS.values() for name in names},
                                        load_post=[])
        self.data = SimpleNamespace(objects=[], materials=[])

        class RenderEngine:
            pass
        self.RenderEngine = RenderEngine
        bpy = ModuleType("bpy")
        bpy.app = SimpleNamespace(handlers=self.handlers)
        bpy.utils = SimpleNamespace(system_resource=lambda kind: str(self.shipped))
        bpy.types = SimpleNamespace(RenderEngine=RenderEngine)
        bpy.data = self.data
        # Stopped as a cleanup, never in tearDown: tearDown runs before every cleanup, so a sys.modules patch a
        # subclass starts later with addCleanup (MessageBusTests' _bpy) would stop after this one and put back the
        # sys.modules it saw, this stand-in bpy included, for every later test file. Cleanups unwind in reverse.
        modules = patch.dict(sys.modules, {"bpy": bpy})
        modules.start()
        self.addCleanup(modules.stop)
        self.addCleanup(self.temp.cleanup)
        self.scene = Scene()

    def engine(self, identifier, folder, module):
        source = ModuleType(module)
        source.__file__ = str(folder / module / "__init__.py")
        sys.modules[module] = source
        self.addCleanup(sys.modules.pop, module, None)
        engine = type("Engine" + module, (self.RenderEngine,), {"bl_idname": identifier, "bl_label": module.title(),
                                                                 "__module__": module})
        self.addCleanup(lambda: engine)  # Held, as Blender holds a registered class: __subclasses__ is weak.
        return engine

    def owner(self, name, curves, uid=1):
        return SimpleNamespace(name=name, session_uid=uid, animation_data=SimpleNamespace(drivers=list(curves)))


class InspectionTests(Stand):
    def test_only_code_outside_the_shipped_scripts_counts(self):
        shipped = code_from(str(self.shipped / "addons_core" / "cycles" / "__init__.py"), "shipped")
        own = policy.host_handler(code_from(str(self.user / "fixture.py"), "measure"))
        user = code_from(str(self.user / "addon" / "__init__.py"), "on_render", "Addon.on_render")
        self.handlers.render_pre += [shipped, own, user]
        self.handlers.frame_change_post.append(functools.partial(print))  # Its code cannot be located.
        self.handlers.load_post.append(user)  # Not fired by a render.
        report = policy.inspect(self.scene, policy.RENDER)
        self.assertEqual(report.handlers, [("render_pre", "addon_module.Addon.on_render"),
                                           ("frame_change_post", "functools.partial")])
        self.assertTrue(report.any)

    def test_each_job_kind_checks_the_lists_it_fires(self):
        user = code_from(str(self.user / "addon.py"))
        self.handlers.object_bake_complete.append(user)
        self.assertFalse(policy.inspect(self.scene, policy.RENDER).handlers)
        self.assertTrue(policy.inspect(self.scene, policy.TEXTURE_BAKE).handlers)
        self.assertFalse(policy.inspect(self.scene, policy.FRAME_BAKE).handlers)
        self.handlers.frame_change_pre.append(user)
        self.assertTrue(policy.inspect(self.scene, policy.FRAME_BAKE).handlers)

    def test_an_add_on_engine_counts_and_shipped_or_native_engines_do_not(self):
        self.engine("CYCLES", self.shipped / "addons_core", "cycles")
        self.engine("LUX", self.user, "luxaddon")
        self.assertIsNone(policy.inspect(self.scene, policy.RENDER).engine_label)
        self.scene.render.engine = "LUX"
        self.assertEqual(policy.inspect(self.scene, policy.RENDER).engine_label, "Luxaddon (luxaddon)")
        self.assertIsNone(policy.inspect(self.scene, policy.TEXTURE_BAKE).engine_label, "a bake runs on Cycles")
        self.scene.render.engine = "BLENDER_EEVEE"
        self.assertFalse(policy.inspect(self.scene, policy.RENDER).any)
        self.scene.render.engine = "UNREGISTERED"  # Blender falls back to a native engine.
        self.assertFalse(policy.inspect(self.scene, policy.RENDER).any)

    def test_only_drivers_that_need_python_count(self):
        python = Curve("location", index=2, driver=driver("my_func(frame)"))
        self_driver = Curve("scale", driver=driver("self.x", simple=True, use_self=True))
        simple = Curve("rotation_euler", driver=driver("frame / 10", simple=True))
        average = Curve("hide_render", driver=driver(kind="AVERAGE"))
        muted = Curve("location", driver=driver("bpy.data"), mute=True)
        self.data.objects.append(self.owner("Cube", [python, self_driver, simple, average, muted]))
        tree = SimpleNamespace(is_embedded_data=True, animation_data=SimpleNamespace(drivers=[
            Curve('nodes["Mix"].inputs[0].default_value', driver=driver("noise()"))]))
        self.data.materials.append(SimpleNamespace(name="Wood", session_uid=2, animation_data=None, node_tree=tree))
        report = policy.inspect(self.scene, policy.RENDER)
        self.assertEqual([label for _identity, label in report.drivers],
                         ["Cube: location[2]", "Cube: scale", 'Wood (node tree): nodes["Mix"].inputs[0].default_value'])
        facts = report.facts()
        self.assertEqual((facts["driver_count"], len(facts["drivers"])), (3, 3))
        self.assertNotIn("handlers", facts)


class ShippedHandlerTests(Stand):
    def test_blenders_and_angelos_own_handlers_never_count_in_any_list(self):
        from angelo_agent import render_drivers, ui_text
        blender_own = code_from(str(self.shipped / "startup" / "bl_operators" / "wm.py"), "blender_own")
        for name in {name for names in policy.HANDLERS.values() for name in names}:
            getattr(self.handlers, name).extend([ui_text.iface_, render_drivers.document_loaded, blender_own])
        for kind in policy.HANDLERS:
            with self.subTest(kind=kind):
                self.assertEqual(policy.inspect(self.scene, kind).handlers, [])

    def test_shipped_namespace_entries_are_recognised(self):
        import math
        from angelo_agent import render_drivers
        roots = policy._shipped_roots()
        self.assertTrue(policy._shipped_object(render_drivers.document_loaded, roots), "a function of the add-on")
        self.assertTrue(policy._shipped_object(render_drivers, roots), "a module of the add-on")
        self.assertFalse(policy._shipped_object(code_from(str(self.user / "addon.py")), roots))
        self.assertFalse(policy._shipped_object(math.sin, roots), "a builtin vouches for nothing by its path")


class DepsgraphHandlerTests(Stand):
    """Depsgraph-update handlers run whenever a view's or job's own writes or frame changes dirty the depsgraph."""

    def setUp(self):
        super().setUp()
        self.on_update = code_from(str(self.user / "community_addon" / "autosmooth.py"), "on_update")
        self.handlers.depsgraph_update_post.append(self.on_update)

    def test_every_kind_finds_them(self):
        for kind in policy.HANDLERS:
            with self.subTest(kind=kind):
                report = policy.inspect(self.scene, kind)
                self.assertEqual(report.depsgraph_handlers, ["addon_module.on_update"])
                self.assertIn(("depsgraph_update_post", "addon_module.on_update"), report.handlers)

    def test_views_refuse_them_in_read_only_and_ask_and_auto_lets_them_run(self):
        for mode in ("READ_ONLY", "ASK"):
            with self.subTest(mode=mode), self.assertRaises(JobRequestError) as caught:
                policy.refuse_look(self.scene, mode)
            self.assertIn("addon_module.on_update (depsgraph_update_post)", str(caught.exception))
            self.assertEqual("This chat is Read-only" in str(caught.exception), mode == "READ_ONLY")
        report = policy.refuse_look(self.scene, "AUTO_BOUNDED")
        self.assertEqual(report.depsgraph_handlers, ["addon_module.on_update"])
        self.handlers.render_pre.append(code_from(str(self.user / "a.py"), "on_render"))
        with self.assertRaises(JobRequestError):
            policy.refuse_look(self.scene, "AUTO_BOUNDED")  # Every other kind of extension code refuses in Auto too.

    def test_jobs_list_them_and_a_swap_while_queued_needs_a_new_approval(self):
        from angelo_agent import job_approvals
        runner = SimpleNamespace(scene=self.scene, request={"kind": "RENDER_STILL"})
        policy.bind(runner)
        facts = policy.facts(runner)["extensions"]
        self.assertEqual(facts["handlers"], ["addon_module.on_update"])
        body = job_approvals.message("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png",
                                                          "_facts": {"extensions": facts}})["body"]
        self.assertIn("1 handler: addon_module.on_update", body)
        policy.recheck(runner)
        self.handlers.depsgraph_update_post[:] = [code_from(str(self.user / "other_addon.py"), "on_change")]
        with self.assertRaises(JobRequestError) as caught:
            policy.recheck(runner)
        self.assertEqual(caught.exception.code, "STALE_SETTING")
        for kind in ("BAKE_TEXTURE", "BAKE_PHYSICS", "BAKE_SIMULATION", "RENDER_ANIMATION"):
            with self.subTest(kind=kind):
                runner = SimpleNamespace(scene=self.scene, request={"kind": kind})
                policy.bind(runner)
                self.assertEqual(policy.facts(runner)["extensions"]["handlers"], ["addon_module.on_change"])


class FingerprintTests(Stand):
    def test_the_fingerprint_follows_configuration_not_internals(self):
        python = Curve("location", driver=driver("f(frame)"))
        self.data.objects.append(self.owner("Cube", [python]))
        first = policy.inspect(self.scene, policy.RENDER).fingerprint
        self.assertEqual(policy.inspect(self.scene, policy.RENDER).fingerprint, first)
        python.driver.expression = "g(frame)"
        changed_driver = policy.inspect(self.scene, policy.RENDER).fingerprint
        self.assertNotEqual(changed_driver, first)
        self.handlers.render_post.append(code_from(str(self.user / "a.py"), "after"))
        with_handler = policy.inspect(self.scene, policy.RENDER).fingerprint
        self.assertNotEqual(with_handler, changed_driver)
        self.handlers.render_post[0] = code_from(str(self.user / "a.py"), "after")  # Reloaded: same name, new body.
        self.assertEqual(policy.inspect(self.scene, policy.RENDER).fingerprint, with_handler)
        self.scene.render.engine = "BLENDER_EEVEE"
        self.assertNotEqual(policy.inspect(self.scene, policy.RENDER).fingerprint, with_handler)

    def test_a_job_binds_its_approval_and_a_queued_change_needs_a_new_one(self):
        runner = SimpleNamespace(scene=self.scene, request={"kind": "RENDER_STILL"})
        policy.bind(runner)
        self.assertEqual(policy.facts(runner), {}, "nothing to disclose: the approval is unchanged")
        policy.recheck(runner)
        self.handlers.render_pre.append(code_from(str(self.user / "late.py"), "late"))
        with self.assertRaises(JobRequestError) as caught:
            policy.recheck(runner)
        self.assertEqual((caught.exception.code, str(caught.exception)), ("STALE_SETTING", policy.CHANGED))
        policy.bind(runner)
        facts = policy.facts(runner)["extensions"]
        self.assertEqual((facts["handlers"], facts["handler_count"]), (["addon_module.late"], 1))
        self.assertEqual(len(facts["fingerprint"]), 64)
        policy.recheck(runner)

    def test_exports_are_not_bound(self):
        self.handlers.frame_change_pre.append(code_from(str(self.user / "a.py")))
        runner = SimpleNamespace(scene=self.scene, request={"kind": "EXPORT_USD"})
        self.assertIsNone(policy.bind(runner))
        self.assertEqual(policy.facts(runner), {})
        policy.recheck(runner)


class ApprovalTextTests(unittest.TestCase):
    def test_the_approval_names_what_runs_and_that_a_change_asks_again(self):
        from angelo_agent import job_approvals
        facts = {"resolution": [64, 64], "extensions": {
            "engine": "Lux (luxaddon)", "handlers": ["addon.on_render"], "handler_count": 1,
            "drivers": ["Cube: location[2]", "Cube: scale"], "driver_count": 2, "fingerprint": "f" * 64}}
        body = job_approvals.message("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png", "_facts": facts})["body"]
        for text in ("Runs Python from add-ons or scripts, which Angelo can't check:", "Render engine Lux (luxaddon)",
                     "1 handler: addon.on_render", "2 scripted drivers: Cube: location[2], Cube: scale",
                     "If the engine, handlers or drivers change before it starts, it asks again."):
            self.assertIn(text, body)
        plain = job_approvals.message("angelo_job_start", {"kind": "RENDER_STILL", "output": "a.png", "_facts": {}})
        self.assertNotIn("Python", plain["body"])


class JobLifecycleTests(Stand):
    def test_start_rechecks_before_anything_is_applied(self):
        from angelo_agent import job_runners
        runner = job_runners.Runner.__new__(job_runners.Runner)
        runner.window = SimpleNamespace(scene=self.scene)
        runner.scene, runner.request, runner.output, runner.overrides = self.scene, {"kind": "RENDER_STILL"}, None, None
        runner.staging, started = None, []
        runner._start = lambda: started.append(True)
        runner.abandon = lambda: None
        policy.bind(runner)
        self.handlers.composite_post.append(code_from(str(self.user / "c.py")))
        with patch.object(job_runners, "native", lambda: None), self.assertRaises(JobRequestError) as caught:
            runner.start()
        self.assertEqual(caught.exception.code, "STALE_SETTING")
        self.assertEqual(started, [])
        self.assertFalse(runner.startup_effects_possible, "refused before any effect: nothing to restore")

    def test_the_precondition_carries_the_extension_facts(self):
        from angelo_agent import job_host
        runner = SimpleNamespace(scene=self.scene, request={"kind": "RENDER_STILL"}, describe=lambda: {"samples": 8})
        self.handlers.render_init.append(code_from(str(self.user / "i.py"), "init"))
        policy.bind(runner)
        manager = SimpleNamespace(admit=lambda request: None)
        job_ui = ModuleType("angelo_agent.job_ui")
        job_ui.manager = lambda: manager
        window = SimpleNamespace(scene=SimpleNamespace(session_uid=7))
        with patch.object(job_host, "_admit", lambda controller, window, arguments: ({"kind": "RENDER_STILL"}, runner)), \
                patch.dict(sys.modules, {"angelo_agent.job_ui": job_ui}), \
                patch("angelo_agent.job_ui", job_ui, create=True):
            precondition = job_host.precondition(None, window, "angelo_job_start", {})
        self.assertIn('"handlers": ["addon_module.init"]', precondition[2])
        self.assertIn('"samples": 8', precondition[2])


class MessageBusTests(Stand):
    """``bpy.msgbus`` subscribers to the settings a view writes: Python cannot list them, the application can."""

    def setUp(self):
        super().setUp()
        self.msgbus = []
        native = ModuleType("_bpy")
        native._angelo_jobs = SimpleNamespace(msgbus_subscribers=msgbus_listing(self.msgbus))
        modules = patch.dict(sys.modules, {"_bpy": native})
        modules.start()
        self.addCleanup(modules.stop)
        self.on_resize = code_from(str(self.user / "community_addon" / "watch.py"), "on_resize")
        self.written = {("RenderSettings", "resolution_x"), ("RenderSettings", "filepath")}

    def test_only_python_outside_the_shipped_scripts_on_a_written_setting_or_its_struct_counts(self):
        shipped = code_from(str(self.shipped / "startup" / "ui.py"), "shipped")
        own = policy.host_handler(code_from(str(self.user / "fixture.py"), "measure"))
        self.msgbus += [("RenderSettings", "resolution_x", self.on_resize), ("RenderSettings", None, self.on_resize),
                        ("RenderSettings", "engine", code_from(str(self.user / "b.py"), "on_engine")),  # Not written.
                        ("View3DShading", "type", code_from(str(self.user / "c.py"), "on_shading")),
                        ("RenderSettings", "filepath", shipped), ("RenderSettings", "filepath", own)]
        self.assertEqual(policy.subscribers(self.written), [
            "addon_module.on_resize (message bus: RenderSettings.resolution_x)",
            "addon_module.on_resize (message bus: RenderSettings.*)"])
        self.assertEqual(policy.inspect(self.scene, policy.RENDER).subscribers, [], "a job's report lists none")

    def test_views_refuse_them_in_read_only_and_ask_and_auto_lets_them_run(self):
        self.msgbus.append(("RenderSettings", "resolution_x", self.on_resize))
        for mode in ("READ_ONLY", "ASK"):
            with self.subTest(mode=mode), self.assertRaises(JobRequestError) as caught:
                policy.refuse_look(self.scene, mode, self.written)
            self.assertIn("1 message-bus subscriber(s) to settings it sets: addon_module.on_resize",
                          str(caught.exception))
        report = policy.refuse_look(self.scene, "AUTO_BOUNDED", self.written)
        self.assertEqual(report.view_python, ["addon_module.on_resize (message bus: RenderSettings.resolution_x)"])
        self.assertEqual(policy.refuse_look(self.scene, "ASK").subscribers, [], "a look writing nothing else")

    def test_a_build_that_cannot_list_them_leaves_them_unknown_and_refuses_nothing(self):
        del sys.modules["_bpy"]._angelo_jobs.msgbus_subscribers
        self.msgbus.append(("RenderSettings", "resolution_x", self.on_resize))
        report = policy.refuse_look(self.scene, "READ_ONLY", self.written)
        self.assertEqual((report.subscribers, report.view_python), (None, []))


class LookTests(LookCase):
    def test_a_look_that_would_run_script_python_starts_nothing_and_names_the_approved_route(self):
        def on_render(*_args):
            return None
        self.blender.bpy.app.handlers = SimpleNamespace(render_pre=[on_render])
        before = self.blender.scene.snapshot()
        with self.assertRaisesRegex(RuntimeError, "angelo_job_start RENDER_STILL") as caught:
            self.begin()
        self.assertIn("on_render", str(caught.exception))
        self.assertEqual(self.blender.invoked, [])
        self.assertEqual(self.blender.scene.snapshot(), before)
        self.assertEqual(self.reference_files(), [])

    def test_ordinary_looks_and_angelos_own_measurements_stay_automatic(self):
        self.blender.bpy.app.handlers = SimpleNamespace(render_stats=[policy.host_handler(lambda *args: None)])
        _pending, result = self.run_look()
        self.assertTrue(result["success"])

    def test_a_handler_added_after_admission_stops_the_look_at_its_start(self):
        from angelo_agent.view_render import LookRunner
        handlers = SimpleNamespace(render_pre=[])
        self.blender.bpy.app.handlers = handlers
        runner = LookRunner(self.blender.window, {"kind": "RENDER_PREVIEW", "queue": False, "minutes": 1.0,
                                                  "format": "PNG", "resolution": [64, 36], "frame_start": 12,
                                                  "frame_end": 12, "samples": 8},
                            Path(self.references.directory) / "look.png", 120)
        handlers.render_pre.append(lambda *args: None)
        with self.assertRaisesRegex(JobRequestError, "runs without the person's approval"):
            runner.start()
        self.assertEqual(self.blender.invoked, [])


if __name__ == "__main__":
    unittest.main()
