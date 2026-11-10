# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private native exceptions cross current AssetCommit; no Python ID-removal rollback."""
import suite_isolation  # noqa: F401
from pathlib import Path
import sys
import unittest
import ast
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import library_agent
from angelo_agent.team_library_files import ImportFailed, NotImported
import test_native_asset_stock_transaction as stock


class NativeLoadPrivacyTests(unittest.TestCase):
    setUp = stock.StockAssetClientTest.setUp
    tearDown = stock.StockAssetClientTest.tearDown
    prepared = stock.StockAssetClientTest.prepared
    finish = stock.StockAssetClientTest.finish

    def test_native_load_error_is_closed_and_retains_rollback_truth_and_private_cause(self):
        self.prepared()
        private = '/private/tmp/angelo-library-SECRET/native.blend'
        original = RuntimeError('Cannot read native file: ' + private)
        self.effect = lambda *_a, **_kw: (_ for _ in ()).throw(original)
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        result = library_agent._finish_failure(caught.exception)
        self.assertNotIn(private, result['error'])
        self.assertIs(caught.exception.__cause__, original)
        self.assertTrue(caught.exception.scene_unchanged)
        self.assertFalse(result.get('partial_changes_possible'))

    def test_failed_restore_preserves_private_cause_without_false_unchanged(self):
        self.prepared()
        self.api.fail_restore = True
        private = '/private/tmp/angelo-library-SECRET/inspection.blend'
        original = ValueError('Cannot inspect ' + private)
        self.effect = lambda *_a, **_kw: (_ for _ in ()).throw(original)
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        result = library_agent._finish_failure(caught.exception)
        self.assertNotIn(private, result['error'])
        self.assertIs(caught.exception.__cause__, original)
        self.assertFalse(caught.exception.scene_unchanged)
        self.assertTrue(result.get('partial_changes_possible'))
        self.assertIsNotNone(self.payload.scope)


    def test_actual_inspection_error_never_appends_and_unchanged_requires_native_restore(self):
        self.prepared()
        source = Path(__file__).resolve().parents[3] / 'scripts/addons_core/angelo_agent/blender_asset_import.py'
        tree = ast.parse(source.read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'apply_item')
        original = RuntimeError('Cannot inspect /private/tmp/angelo-library-SECRET/source.blend')
        append = Mock()
        env = dict(inspect=Mock(side_effect=original), _append=append, NotImported=NotImported)
        exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), env)
        self.effect = lambda context, row, **kw: env['apply_item'](context, row, **kw)
        with self.assertRaises(ImportFailed) as caught:
            self.finish()
        append.assert_not_called()
        self.assertIs(caught.exception.__cause__, original)
        self.assertTrue(caught.exception.scene_unchanged)
        self.assertIn(('finish', False), self.api.calls)
        self.assertNotIn('angelo-library-SECRET', library_agent._finish_failure(caught.exception)['error'])


if __name__ == '__main__':
    unittest.main()
