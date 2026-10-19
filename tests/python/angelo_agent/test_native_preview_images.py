# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Unbilled preview lifecycle checks; native fixture proves actual Image cleanup."""

import ast
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import native_preview_images as images


def preview(identity):
    return {'changes': {'views': [{'id': identity, 'path': '/private/preview/' + identity + '.png'}]}}


class PreviewImageLifecycleTests(unittest.TestCase):
    def setUp(self):
        images.clear()
        self.descriptors = [preview('first'), preview('second')]
        self.controller = SimpleNamespace(state={'id': 'chat'}, version=0,
            native_previews=lambda: self.descriptors, references=SimpleNamespace(checked_path=Mock(return_value=Path('/private/preview/image.png'))))
        self.image = SimpleNamespace(size=(2, 2), colorspace_settings=SimpleNamespace(name='sRGB'),
            pixels=SimpleNamespace(foreach_get=Mock()))
        self.bpy = SimpleNamespace(data=SimpleNamespace(images=SimpleNamespace(load=Mock(return_value=self.image), remove=Mock())))

    def tearDown(self):
        images.clear()

    def test_one_decode_per_tick_and_no_retained_blender_image_id(self):
        with patch.dict(sys.modules, {'bpy': self.bpy}):
            self.assertEqual(images.prepare([self.controller]), 1)
            self.assertTrue(images.prepared(self.descriptors[-1], self.controller))
            self.assertFalse(images.prepared(self.descriptors[0], self.controller))
            self.assertEqual(self.bpy.data.images.load.call_count, 1)
            self.bpy.data.images.remove.assert_called_once_with(self.image)
            self.assertEqual(self.image.colorspace_settings.name, 'Non-Color')
            self.assertEqual(images.prepare([self.controller]), 1)
            self.assertEqual(images.prepare([self.controller]), 0)
            self.assertEqual(self.bpy.data.images.load.call_count, 2)
            self.assertEqual(self.bpy.data.images.remove.call_count, 2)
        self.assertEqual(self.controller.version, 2)

    def test_decode_failure_removes_image_and_does_not_retry_each_redraw(self):
        self.descriptors = self.descriptors[:1]
        self.image.pixels.foreach_get.side_effect = RuntimeError('Injected decode failure')
        with patch.dict(sys.modules, {'bpy': self.bpy}):
            self.assertEqual(images.prepare([self.controller]), 1)
            self.assertFalse(images.prepared(self.descriptors[0], self.controller))
            self.bpy.data.images.remove.assert_called_once_with(self.image)
            self.assertEqual(images.prepare([self.controller]), 0)
            self.assertEqual(self.bpy.data.images.load.call_count, 1)

    def test_oversized_image_is_removed_and_budget_bounds_both_caches(self):
        self.descriptors = [preview(str(index)) for index in range(20)]
        self.image.size = (769, 768)
        with patch.dict(sys.modules, {'bpy': self.bpy}):
            for _ in range(20):
                images.prepare([self.controller])
        self.assertEqual(len(images._entries), images.CACHE_LIMIT)
        self.assertEqual(self.bpy.data.images.load.call_count, images.CACHE_LIMIT)
        self.assertEqual(self.bpy.data.images.remove.call_count, images.CACHE_LIMIT)
        self.assertFalse(any(images.prepared(item, self.controller) for item in self.descriptors))
        self.descriptors.clear()
        images.prepare([self.controller])
        self.assertFalse(images._entries)

    def test_worker_cannot_touch_blender_or_decode_pixels(self):
        errors = []
        def run():
            try:
                images.prepare([self.controller])
            except RuntimeError as error:
                errors.append(str(error))
        worker = threading.Thread(target=run)
        worker.start()
        worker.join()
        self.assertEqual(len(errors), 1)
        self.assertIn('main thread', errors[0])
        self.controller.references.checked_path.assert_not_called()

    def test_draw_uploads_cached_pixels_without_disk_or_rna_access(self):
        descriptor = self.descriptors[0]
        self.descriptors = [descriptor]
        with patch.dict(sys.modules, {'bpy': self.bpy}):
            images.prepare([self.controller])
        self.controller.references.checked_path.side_effect = AssertionError('Disk access in draw')
        self.bpy.data.images.load.side_effect = AssertionError('RNA access in draw')
        gpu = SimpleNamespace(types=SimpleNamespace(GPUTexture=Mock(return_value='texture'), Buffer=Mock()),
            shader=SimpleNamespace(from_builtin=Mock(return_value=Mock())))
        batch = SimpleNamespace(batch_for_shader=Mock(return_value=Mock()))
        with patch.dict(sys.modules, {'bpy': self.bpy, 'gpu': gpu, 'gpu_extras.batch': batch}):
            self.assertTrue(images.draw(descriptor, (0, 0, 100, 100), self.controller))
            self.assertTrue(images.draw(descriptor, (0, 0, 100, 100), self.controller))
            self.assertFalse(images.draw(preview('unprepared'), (0, 0, 100, 100), self.controller))
        gpu.types.GPUTexture.assert_called_once()
        batch.batch_for_shader.return_value.draw.assert_called_with(images._shader)
        self.assertEqual(self.bpy.data.images.load.call_count, 1)
        images.clear()
        self.assertFalse(images._entries)
        self.assertIsNone(images._shader)

    def test_source_guard_prepares_in_pump_and_draw_has_no_bpy_or_file_io(self):
        module = ast.parse(Path(images.__file__).read_text())
        draw = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == 'draw')
        source = ast.unparse(draw)
        for forbidden in ('bpy', '_decode(', 'checked_path', 'open(', 'read_bytes', 'images.load', 'images.remove'):
            self.assertNotIn(forbidden, source)
        root = Path(images.__file__).parent
        ui_source = (root / 'ui.py').read_text()
        self.assertIn('from .native_preview_images import prepare', ui_source)
        for name in ('document_load_pre', 'invalidate_workspace_state'):
            function = next(node for node in ast.parse(ui_source).body if isinstance(node, ast.FunctionDef) and node.name == name)
            self.assertIn('from .native_preview_images import clear', ast.unparse(function))
        self.assertIn('from .native_preview_images import clear', (root / 'agent_workspace_ui.py').read_text())

    def test_file_load_rebinds_fonts_and_clears_invalid_wrapping_outside_draw(self):
        root = Path(images.__file__).parent
        source = (root / 'chat_view.py').read_text()
        function = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'reload_fonts')
        namespace = {'Path': Mock(return_value=SimpleNamespace(is_file=lambda: True)),
            'blf': SimpleNamespace(load=Mock(side_effect=(5, 6))),
            'wrap': SimpleNamespace(cache_clear=Mock()), '_font': 99, '_mono': 100,
            'bpy': SimpleNamespace(utils=SimpleNamespace(system_resource=Mock(return_value=''))),
            'chat_replies': SimpleNamespace(_layout=SimpleNamespace(cache_clear=Mock()))}
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(root / 'chat_view.py'), 'exec'), namespace)
        namespace['reload_fonts']()
        self.assertEqual((namespace['_font'], namespace['_mono']), (5, 6))
        namespace['wrap'].cache_clear.assert_called_once()
        namespace['chat_replies']._layout.cache_clear.assert_called_once()  # Reply layouts hold font ids.
        ui_source = (root / 'ui.py').read_text()
        self.assertIn('bpy.app.handlers.load_post.append(document_load_post)', ui_source)
        self.assertIn('bpy.app.handlers.load_post.remove(document_load_post)', ui_source)
        post = next(node for node in ast.parse(ui_source).body if isinstance(node, ast.FunctionDef) and node.name == 'document_load_post')
        self.assertIn('chat_view.reload_fonts()', ast.unparse(post))
        draw = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == 'draw')
        self.assertNotIn('reload_fonts(', ast.unparse(draw))


if __name__ == '__main__':
    unittest.main()
