# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The Asset Browser library's writer when something goes wrong (``studio_assets_convert``, ``studio_assets_host``).

A writer process that ends early names the item it ended on, with its exit code and its last output lines (a Windows
run failed with nothing but "failed to open blend file" because the child's output was thrown away); a batch Angelo
stops on purpose (the person's render, the library turned off) fails no item. Each item is reported written before
its preview render, so a render that fails, or ends the process, on a machine without a usable GPU context never
loses the model: the file stands without a preview, no item after it is blamed (a Windows run lost a model that
never started to the render before it), and the rest of the session writes without a render.
"""

import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
sys.path.insert(0, str(ROOT / "scripts/addons_core/angelo_agent"))


def fake_bpy():
    """A stand-in ``bpy`` and ``mathutils`` (built here, never imported from another test module)."""
    bpy = types.ModuleType("bpy")
    bpy.app = SimpleNamespace(background=True, binary_path=sys.executable, timers=SimpleNamespace(
        register=lambda *a, **k: None, is_registered=lambda f: False, unregister=lambda f: None),
        handlers=SimpleNamespace(load_factory_preferences_post=[], depsgraph_update_post=[], save_pre=[],
                                 load_post=[], blend_import_post=[], persistent=lambda f: f),
        translations=SimpleNamespace(pgettext_iface=lambda text, context=None: text))
    bpy.data = SimpleNamespace()
    handlers = types.ModuleType("bpy.app.handlers")
    handlers.persistent = lambda f: f
    mathutils = types.ModuleType("mathutils")
    mathutils.Vector = mathutils.Matrix = object
    return {"bpy": bpy, "bpy.app": bpy.app, "bpy.app.handlers": handlers, "mathutils": mathutils}


MODULES = fake_bpy()
with mock.patch.dict(sys.modules, MODULES):
    from angelo_agent import studio_assets_host as host
    import studio_assets_convert as convert


class StoppedWriterTests(unittest.TestCase):
    def test_a_writer_that_dies_names_its_item_with_the_reason(self):
        # Python stands in for the app: it refuses Blender's arguments and exits 2, printing why, as a writer that
        # cannot start or crashes does.
        batch = [{"id": "first"}, {"id": "second"}]
        with mock.patch.dict(sys.modules, MODULES):   # Inside: its lazy imports never stay in sys.modules.
            results = host.convert_batch(sys.executable, batch, threading.Event())
        self.assertEqual(list(results), ["first"])
        reason = results["first"]["error"]
        self.assertTrue(reason.startswith(host.STOPPED + " (exit code 2)"), reason)
        self.assertIn("usage", reason.lower(), "the child's own output says why")

    @unittest.skipIf(sys.platform == "win32", "the stand-in writer is a script run through its #! line")
    def test_a_writer_that_dies_rendering_a_preview_blames_no_item(self):
        # The stand-in reports the first item written with its render running, then ends as a crashed writer does.
        with tempfile.TemporaryDirectory() as folder:
            writer = Path(folder) / "writer"
            writer.write_text(f"#!{sys.executable}\nimport json, sys\nprint('Writing: angelo.crash.txt')\n"
                              "open(sys.argv[-1], 'w').write(json.dumps({'first': {'ok': True, 'preview': "
                              f"{host.RENDERING!r}}}}}))\nsys.exit(3)\n")
            writer.chmod(0o700)
            with mock.patch.dict(sys.modules, MODULES), mock.patch("sys.stderr") as console:
                results = host.convert_batch(str(writer), [{"id": "first"}, {"id": "second"}], threading.Event())
        self.assertEqual(list(results), ["first"], "the item that never started is not blamed")
        self.assertTrue(results["first"]["ok"])
        self.assertTrue(results["first"]["preview"].startswith(host.PREVIEW_STOPPED + " (exit code 3)"), results)
        self.assertIn("angelo.crash.txt", results["first"]["preview"])
        printed = "".join(str(c.args[0]) for c in console.write.call_args_list)
        self.assertIn("angelo.crash.txt", printed, "the writer's own output reaches the console")

    def test_a_preview_crash_blames_no_item_in_the_sync(self):
        items = [{"id": name, "fingerprint": name, "blend": name + ".blend"} for name in ("a", "b", "c")]
        batches = []

        def run(binary, batch, stop, yield_to):
            batches.append([item["id"] for item in batch])
            if len(batches) == 1:   # The first preview render ends the writer; b and c never started.
                return {"a": {"ok": True, "preview": host.PREVIEW_STOPPED + " (exit code 3)"}}
            return {item["id"]: {"ok": True} for item in batch}
        manifest = {}
        studio = SimpleNamespace(assets={}, folders={})
        with mock.patch.dict(sys.modules, MODULES), \
                mock.patch.object(host.studio_assets, "write_catalogs", return_value=False), \
                mock.patch.object(host.studio_assets, "remove_stale_temporaries"), \
                mock.patch.object(host.studio_assets, "read_manifest", return_value=manifest), \
                mock.patch.object(host.studio_assets, "plan", return_value=(items, [])), \
                mock.patch.object(host.studio_assets, "write_manifest"), \
                mock.patch.object(host, "halted", return_value=False), \
                mock.patch("angelo_agent.studio_state.StudioLibrary", return_value=studio):
            changed = host._sync_locked("library", "directory", {}, "binary", "one", threading.Event(), run,
                                        lambda: False)
        self.assertTrue(changed)
        self.assertEqual(batches, [["a", "b", "c"], ["b", "c"]], "the items after it go to a fresh writer")
        self.assertFalse(any(entry.get("failed") for entry in manifest.values()), manifest)
        self.assertEqual(sorted(manifest), ["a", "b", "c"])

    def test_no_second_try_for_a_written_model_while_renders_are_off(self):
        items = [{"id": "retry", "fingerprint": "r", "blend": "r.blend", "preview_only": True},
                 {"id": "new", "fingerprint": "n", "blend": "n.blend"}]
        batches = []

        def run(binary, batch, stop, yield_to):
            batches.append([item["id"] for item in batch])
            return {item["id"]: {"ok": True} for item in batch}
        studio = SimpleNamespace(assets={}, folders={})
        with mock.patch.dict(sys.modules, MODULES), mock.patch.dict(host._state, previews=False), \
                mock.patch.object(host.studio_assets, "write_catalogs", return_value=False), \
                mock.patch.object(host.studio_assets, "remove_stale_temporaries"), \
                mock.patch.object(host.studio_assets, "read_manifest", return_value={}), \
                mock.patch.object(host.studio_assets, "plan", return_value=(items, [])), \
                mock.patch.object(host.studio_assets, "write_manifest"), \
                mock.patch.object(host, "halted", return_value=False), \
                mock.patch("angelo_agent.studio_state.StudioLibrary", return_value=studio):
            host._sync_locked("library", "directory", {}, "binary", "one", threading.Event(), run, lambda: False)
            items.pop()   # Only the retry left: no writer starts at all.
            host._sync_locked("library", "directory", {}, "binary", "one", threading.Event(), run, lambda: False)
        self.assertEqual(batches, [["new"]])

    @unittest.skipIf(sys.platform == "win32", "the stand-in writer is a script run through its #! line")
    def test_every_writer_after_a_preview_crash_renders_nothing(self):
        # Every caller of convert_batch (the sync, a drag's write at once) gets the session's switch: the first
        # writer ends rendering a preview, the next is asked for no render (the stand-in echoes each item's flag).
        with tempfile.TemporaryDirectory() as folder:
            writer = Path(folder) / "writer"
            writer.write_text(f"#!{sys.executable}\nimport json, sys\n"
                              "batch = json.load(open(sys.argv[-2]))\n"
                              "if batch[0]['id'] == 'first':\n"
                              f"    json.dump({{'first': {{'ok': True, 'preview': {host.RENDERING!r}}}}}, "
                              "open(sys.argv[-1], 'w'))\n    sys.exit(3)\n"
                              "json.dump({i['id']: {'ok': True, 'render': i.get('render', True)} for i in batch}, "
                              "open(sys.argv[-1], 'w'))\n")
            writer.chmod(0o700)
            with mock.patch.dict(sys.modules, MODULES), mock.patch.dict(host._state, previews=True), \
                    mock.patch("sys.stderr"):
                first = host.convert_batch(str(writer), [{"id": "first"}], threading.Event())
                later = host.convert_batch(str(writer), [{"id": "later"}], threading.Event())
                previews = host._state["previews"]
        self.assertTrue(first["first"]["preview"].startswith(host.PREVIEW_STOPPED), first)
        self.assertFalse(previews)
        self.assertEqual(later, {"later": {"ok": True, "render": False}})

    def test_the_writer_and_host_share_the_render_marker(self):
        self.assertEqual(convert.RENDERING, host.RENDERING)

    def test_a_batch_angelo_stops_fails_no_item(self):
        stop = threading.Event()
        stop.set()
        with mock.patch.dict(sys.modules, MODULES):
            results = host.convert_batch(sys.executable, [{"id": "first"}], stop)
        self.assertEqual(results, {})


class Target:
    """An asset datablock (hashable, as an ID is)."""


class PreviewAfterWriteTests(unittest.TestCase):
    def item(self, root):
        return {"id": "m", "kind": "3D", "blend": str(Path(root) / "models" / "m.blend"), "thumbnail": ""}

    def test_the_item_is_reported_written_before_its_preview_render(self):
        order = []
        target = Target()
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(convert, "_ids", return_value={}), \
                mock.patch.object(convert, "model_asset", return_value=({target}, (target, []))), \
                mock.patch.object(convert, "_save", side_effect=lambda item, targets: order.append("save")), \
                mock.patch.object(convert, "render_preview", side_effect=lambda *a: order.append("render") or True), \
                mock.patch.object(convert, "set_preview", return_value=True):
            result = convert.write(self.item(root), root, report=lambda result: order.append("report"))
        self.assertTrue(result["ok"] and "preview" not in result, result)
        self.assertEqual(order, ["save", "report", "report", "render", "save"],
                         "written and reported, its render reported as running, then the preview")

    def test_no_render_when_the_host_turned_renders_off(self):
        target = Target()
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(convert, "_ids", return_value={}), \
                mock.patch.object(convert, "model_asset", return_value=({target}, (target, []))), \
                mock.patch.object(convert, "_save"), \
                mock.patch.object(convert, "render_preview") as render:
            result = convert.write(dict(self.item(root), render=False), root)
        self.assertTrue(result["ok"] and result["preview"] == convert.NOT_RENDERED, result)
        render.assert_not_called()

    def test_a_forced_failure_fails_only_the_preview(self):
        target = Target()
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.dict(convert.os.environ, {convert.FORCE_FAILURE: "raise"}), \
                mock.patch.object(convert, "_ids", return_value={}), \
                mock.patch.object(convert, "model_asset", return_value=({target}, (target, []))), \
                mock.patch.object(convert, "_save"), \
                mock.patch.object(convert, "render_preview") as render:
            result = convert.write(self.item(root), root)
        self.assertTrue(result["ok"] and "forced by " + convert.FORCE_FAILURE in result["preview"], result)
        render.assert_not_called()

    def test_a_render_with_nothing_to_show_is_not_retried(self):
        target = Target()
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(convert, "_ids", return_value={}), \
                mock.patch.object(convert, "model_asset", return_value=({target}, (target, []))), \
                mock.patch.object(convert, "_save"), \
                mock.patch.object(convert, "render_preview", return_value=False):
            result = convert.write(self.item(root), root)
        self.assertTrue(result["ok"] and "preview" not in result, result)

    def test_a_failing_render_costs_only_the_preview(self):
        target = Target()
        reported = []
        with tempfile.TemporaryDirectory() as root, \
                mock.patch.object(convert, "_ids", return_value={}), \
                mock.patch.object(convert, "model_asset", return_value=({target}, (target, []))), \
                mock.patch.object(convert, "_save"), \
                mock.patch.object(convert, "render_preview", side_effect=RuntimeError("no GPU context")):
            result = convert.write(self.item(root), root, report=reported.append)
        self.assertTrue(result["ok"] and reported and reported[0]["ok"])
        self.assertIn("no GPU context", result["preview"])


if __name__ == "__main__":
    unittest.main()
