# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pin the synchronization contract; native stress tests prove actual Undo."""

from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[3] / 'source/blender/editors/sculpt_paint/mesh/sculpt_undo.cc'


class MultiresUndoSynchronizationTests(unittest.TestCase):
    @staticmethod
    def function(signature):
        source = SOURCE.read_text()
        start = source.index(signature)
        end = source.index('\n}', start) + 2
        return source[start:end]

    def test_grid_restore_awaits_compression_before_reading_either_array(self):
        source = SOURCE.read_text()
        start = source.index('static void restore_position_grids(')
        end = source.index('\nstatic ', start + 1)
        function = source[start:end]
        barrier = function.index('undo_data.ensure_compression_complete();')
        self.assertLess(barrier, function.index('undo_data.compressed_indices'))
        self.assertLess(barrier, function.index('undo_data.compressed_positions'))

    def test_compression_completion_keeps_acquire_release_and_task_join(self):
        source = SOURCE.read_text()
        start = source.index('void ensure_compression_complete()')
        end = source.index('static void compress_fn', start)
        self.assertIn('compression_ready.load(std::memory_order_acquire)', source[start:end])
        self.assertIn('BLI_task_pool_work_and_wait(compression_task_pool)', source[start:end])
        self.assertLess(source.index('data->compressed_positions = std::move(compressed_data);'),
                        source.index('data->compression_ready.store(true, std::memory_order_release);'))

    def test_background_compression_does_not_write_main_thread_accounting(self):
        source = SOURCE.read_text()
        start = source.index('static void compress_fn(')
        end = source.index('\n};', start)
        self.assertNotIn('undo_size', source[start:end])
        self.assertNotIn('owner_step_data', source)
        self.assertNotIn('data_size', source[start:end])

    def test_current_compressed_bytes_are_counted_only_after_join(self):
        source = SOURCE.read_text()
        start = source.index('size_t memory_size_get()')
        end = source.index('static void compress_fn', start)
        function = source[start:end]
        self.assertLess(function.index('ensure_compression_complete();'),
                        function.index('compressed_indices.index_range()'))
        self.assertIn('compressed_indices[i].as_span().size_in_bytes()', function)
        self.assertIn('compressed_positions[i].as_span().size_in_bytes()', function)

    def test_encoder_and_memory_ui_publish_the_same_current_accounting(self):
        function = self.function('static size_t step_data_memory_size_get(')
        self.assertIn('position_step_storage->memory_size_get()', function)
        self.assertIn('step_data_memory_size_get(*data.angelo_mask_undo)', function)
        self.assertIn('us->step.data_size = step_data_memory_size_get(us->data);',
                      self.function('static bool step_encode('))
        self.assertIn('step->data_size = step_data_memory_size_get(sculpt_step->data);',
                      self.function('size_t step_memory_size_get('))

    def test_undo_and_redo_refresh_size_after_recompression(self):
        for signature in ('static void step_decode_undo_impl(', 'static void step_decode_redo_impl('):
            with self.subTest(signature=signature):
                function = self.function(signature)
                self.assertLess(function.index('restore_list('),
                                function.index('us->step.data_size = step_data_memory_size_get(us->data);'))

    def test_native_accounting_fixture_has_exact_restoration_and_explicit_exhaustion(self):
        source = Path(__file__).with_name('test_blender_sculpt_undo_accounting.py').read_text()
        self.assertNotIn('np.allclose', source)
        for comparison in ('restored, before', 'restored_masks, before_masks',
                           'redone, applied', 'redone_masks, applied_masks'):
            self.assertIn('np.array_equal(' + comparison + ')', source)
        self.assertIn('oversized_history_has_no_eligible_predecessor', source)
        self.assertIn('oversized_no_destination_leaves_history_unchanged', source)
        self.assertIn('base_history_retains_visible_sculpt_predecessor', source)
        self.assertIn('_pruner_uses_exact_encoded_byte_sum', source)


if __name__ == '__main__':
    unittest.main()
