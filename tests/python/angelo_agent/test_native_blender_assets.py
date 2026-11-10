# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Essentials listing trust boundaries and routing. Native data loading is exercised by test_blender_essentials.py."""

import suite_isolation  # noqa: F401  First: never expose real provider logins.


import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts/addons_core'))
from angelo_agent import blender_asset_index as index, blender_assets as assets, blender_assets_agent as agent
from angelo_agent import library_agent, library_approvals
from angelo_agent.library_spec import SEARCH, IMPORT, validate_library
from angelo_agent.refusal_codes import RefusalError
from test_native_library_tools import fake_window, wait_for

URL = 'https://cdn.extensions.blender.org/asset-libraries/essentials/'


def file_record():
    return {'path': 'materials/example.blend', 'size_in_bytes': 7, 'hash': 'SHA256:' + hashlib.sha256(b'payload').hexdigest(),
            'blender_version': '5.2'}


def asset_record():
    return {'name': 'Example surface', 'id_type': 'MATERIAL', 'files': ['materials/example.blend'],
            'bl_versions': {'min': '5.2', 'until': '5.4'},
            'meta': {'description': 'A ceramic surface', 'license': 'CC0', 'tags': ['ceramic']}}


class IndexTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.file = index.file_record(file_record(), self.root, URL)

    def write_listing(self, raw=None):
        page = json.dumps({'assets': [raw or asset_record()], 'files': [file_record()]}).encode()
        folder = self.root / '_v1'
        folder.mkdir(exist_ok=True)
        (folder / 'assets-00000.json').write_bytes(page)
        (folder / 'asset-index.processed.json').write_text(json.dumps({'pages': [
            {'url': '_v1/assets-00000.json', 'hash': 'SHA256:' + hashlib.sha256(page).hexdigest()}]}))

    def test_search_reads_metadata_without_payload(self):
        self.write_listing()
        rows = index.remote_rows(self.root, URL, (5, 3))
        self.assertEqual(len(rows), 1)
        found, total = index.search(rows, 'ceramic', 'material', 8)
        self.assertEqual(total, 1)
        result = index.compact(found[0])
        self.assertFalse(result['downloaded'])
        self.assertNotIn('file', result)
        self.assertNotIn(str(self.root), json.dumps(result))
        self.assertFalse((self.root / 'materials').exists())

    def test_versions_have_exclusive_upper_bound(self):
        self.write_listing()
        self.assertEqual(len(index.remote_rows(self.root, URL, (5, 2))), 1)
        self.assertFalse(index.remote_rows(self.root, URL, (5, 4)))
        self.assertFalse(index.remote_rows(self.root, URL, (5, 1)))

    def test_catalog_content_changes_rejected(self):
        self.write_listing()
        (self.root / '_v1/assets-00000.json').write_text('{}')
        with self.assertRaises(ValueError):
            index.remote_rows(self.root, URL, (5, 3))

    def test_index_bounds(self):
        self.write_listing()
        with patch.object(index, 'MAX_JSON', 10), self.assertRaises(ValueError):
            index.remote_rows(self.root, URL, (5, 3))
        with patch.object(index, 'MAX_PAGES', 0), self.assertRaises(ValueError):
            index.remote_rows(self.root, URL, (5, 3))

    def test_paths_and_origins_cannot_escape(self):
        for path in ('../x.blend', '/tmp/x.blend', 'C:/x.blend', 'a\\b.blend', 'a/./b.blend', 'a//b.blend'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                index.file_record(dict(file_record(), path=path), self.root, URL)
        for url in ('https://evil.example/x.blend', 'file:///tmp/x.blend', '../x.blend',
                    '%2e%2e/x.blend', '/unrelated/x.blend', 'x.blend?token=secret'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                index.file_record(dict(file_record(), url=url), self.root, URL)

    def test_existing_symlink_is_refused(self):
        (self.root / 'materials').symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            index.file_record(file_record(), self.root, URL)

    def test_asset_payload_integrity_rechecked_on_every_use(self):
        path = Path(self.file['path'])
        path.parent.mkdir()
        path.write_bytes(b'payload')
        self.assertTrue(index.verified(self.file))
        path.write_bytes(b'changed')
        self.assertFalse(index.verified(self.file))
        path.write_bytes(b'payload')
        stop = threading.Event()
        stop.set()
        self.assertFalse(index.verified(self.file, stop))

    def test_revision_changes_selection_identity(self):
        row = index.record(asset_record(), self.file, 'online')
        changed = index.record(asset_record(), dict(self.file, sha256='b' * 64), 'online')
        self.assertNotEqual(row['id'], changed['id'])

    def test_geometry_modifier_traits_are_explicit(self):
        raw = asset_record()
        raw['id_type'] = 'NODETREE'
        raw['meta']['properties'] = [{'name': 'type', 'type': 'IDP_INT', 'value': 3},
                                     {'name': 'geometry_node_asset_traits_flag', 'type': 'IDP_INT', 'value': 64}]
        row = index.compact(index.record(raw, self.file, 'online'))
        self.assertEqual(row['node_tree_type'], 'GeometryNodeTree')
        self.assertTrue(row['can_apply_as_modifier'])
        raw['meta']['properties'][1]['value'] = 1
        self.assertFalse(index.record(raw, self.file, 'online')['is_modifier'])

    def test_bad_metadata_and_unsupported_dependencies_are_skipped(self):
        for field, value in [('files', ['a.blend', 'b.blend']), ('id_type', 'TEXT'), ('name', 'bad\nname')]:
            raw = asset_record()
            raw[field] = value
            self.write_listing(raw)
            self.assertFalse(index.remote_rows(self.root, URL, (5, 3)))
        for value in ('not a list', {'x': 1}):
            raw = asset_record()
            raw['meta']['tags'] = value
            self.write_listing(raw)
            self.assertFalse(index.remote_rows(self.root, URL, (5, 3)))


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(assets.reset)
        self.row = index.record(asset_record(), index.file_record(file_record(), Path('/private/tmp'), URL), 'online')
        assets.publish([], [self.row])
        self.service = library_agent.LibraryService(object(), fake_window())
        self.config = (Path('/private/tmp'), Path('/private/tmp'), URL, (5, 3), True, True)

    def test_schema_exposes_source_without_breaking_old_calls(self):
        self.assertEqual(validate_library(SEARCH, {'query': 'stone'}), {'query': 'stone'})
        self.assertEqual(validate_library(SEARCH, {'query': 'x', 'source': 'blender', 'kind': 'brush'})['source'], 'blender')
        for source in ('fab', 'file:///tmp', True):
            with self.assertRaises(ValueError):
                validate_library(SEARCH, {'query': '', 'source': source})

    def test_search_routes_through_existing_library_service(self):
        with patch.object(agent, 'search', return_value={'items': []}) as call:
            self.assertEqual(self.service.execute(SEARCH, {'query': 'x', 'source': 'blender'}), {'items': []})
            call.assert_called_once()

    def test_import_and_precondition_route_by_observed_id(self):
        args = {'item_id': self.row['id']}
        with patch.object(agent, 'precondition', return_value=('approval',)) as pre:
            self.assertEqual(self.service.precondition(IMPORT, args), ('approval',))
            pre.assert_called_once()
        with patch.object(agent, 'use', return_value={'success': True}) as use:
            self.assertTrue(self.service.execute(IMPORT, args)['success'])
            use.assert_called_once()

    def test_off_switch_invalidates_retained_online_selection(self):
        with patch.object(assets, 'configuration', return_value=(*self.config[:4], False, True)):
            with self.assertRaises(RefusalError):
                assets.get(self.row['id'])

    def test_approval_identifies_source_and_effect(self):
        with patch.object(assets, 'configuration', return_value=self.config):
            card = library_approvals.message(IMPORT, {'item_id': self.row['id']})
        self.assertIn('Blender Essentials', card['body'])
        self.assertIn('selected meshes', card['body'])
        self.assertNotIn('ToonLab', card['warning'])

    def test_repeated_poll_never_repeats_import(self):
        calls = []
        pending = agent._pending(lambda: calls.append(1) or {'success': True}, status_text='', activity_note='', timeout=1)
        self.assertTrue(pending.poll()['success'])
        self.assertTrue(pending.poll()['success'])
        self.assertEqual(calls, [1])

    def test_cancel_before_or_after_worker_never_imports(self):
        calls = []
        pending = agent._pending(lambda: calls.append(1) or {'success': True}, status_text='', activity_note='', timeout=1)
        pending.cancel()
        self.assertEqual(pending.poll()['error_code'], 'CANCELLED')
        self.assertFalse(calls)

    def test_unobserved_selection_refused(self):
        with self.assertRaises(RefusalError):
            assets.get('blender:' + '0' * 64)

    def test_native_api_compatibility_never_supplies_account_credentials(self):
        def old_api(remote_url, local_path):
            return remote_url, local_path

        def new_api(remote_url, auth_token, local_path):
            return remote_url, auth_token, local_path
        args = dict(remote_url=URL, local_path='/cache')
        self.assertEqual(assets.native_call(old_api, **args), (URL, '/cache'))
        self.assertEqual(assets.native_call(new_api, **args), (URL, '', '/cache'))

    def test_dependency_revision_invalidates_selection(self):
        first = index.record(asset_record(), dict(self.row['file'], dependencies=['/dependency.blend']), 'bundled')
        raw = dict(asset_record(), name='Dependency')
        second = index.record(raw, dict(self.row['file'], path='/dependency.blend', relative='dependency.blend'), 'bundled')
        assets.bind_dependencies([first, second])
        original = first['id']
        self.assertEqual(len(first['files']), 2)
        second['file']['sha256'] = 'b' * 64
        assets.bind_dependencies([first, second])
        self.assertNotEqual(first['id'], original)
        accepted, omitted = assets.bind_dependencies([first])
        self.assertEqual((accepted, omitted), ([], 1))


class AuditRegressionTests(unittest.TestCase):
    setUp = IndexTests.setUp
    write_listing = IndexTests.write_listing
    def test_each_page_is_reachable_without_duplicates(self):
        rows = [index.record(dict(asset_record(), name=f'Surface {i:03}'), self.file, 'online') for i in range(47)]
        collected = []
        for offset in (0, 20, 40):
            page, total = index.search(rows, '', 'material', 20, offset)
            self.assertEqual(total, 47)
            collected.extend(row['id'] for row in page)
        self.assertEqual(len(set(collected)), 47)

    def test_source_kind_and_offset_validation(self):
        for source, kind in [('blender', 'texture'), ('blender', 'hdri'), ('angelo', 'brush')]:
            with self.subTest(source=source, kind=kind), self.assertRaisesRegex(ValueError, 'supports kind'):
                validate_library(SEARCH, dict(query='', source=source, kind=kind))
        for offset in (-1, True, 1.5, 1000001):
            with self.assertRaises(ValueError):
                validate_library(SEARCH, dict(query='', offset=offset))
        self.assertEqual(validate_library(SEARCH, dict(query='', offset=40))['offset'], 40)

    def test_invalid_dependency_does_not_hide_other_sources_or_assets(self):
        self.addCleanup(assets.reset)
        good = index.record(asset_record(), self.file, 'bundled')
        bad_file = dict(self.file, path='/bad.blend', relative='bad.blend', dependencies=['/missing.blend'])
        bad = index.record(dict(asset_record(), name='Bad'), bad_file, 'bundled')
        dependent = index.record(dict(asset_record(), name='Dependent'),
                                 dict(self.file, path='/dependent.blend', dependencies=['/bad.blend']), 'bundled')
        remote = index.record(dict(asset_record(), name='Online'), self.file, 'online')
        assets.publish([good, bad, dependent], [remote])
        self.assertEqual({r['name'] for r in assets._rows.values()}, {'Example surface', 'Online'})
        self.assertTrue(assets._bundled_notes)

    def test_dependency_file_needs_no_asset_marked_ids(self):
        dependency = dict(self.file, path='/helper.blend', relative='helper.blend')
        file = dict(self.file, dependencies=['/helper.blend'])
        row = index.record(asset_record(), file, 'bundled')
        accepted, omitted = assets.bind_dependencies([row], [file, dependency])
        self.assertEqual((len(accepted), omitted, len(row['files'])), (1, 0, 2))

    def test_ready_selection_does_not_wait_for_shared_queue(self):
        path = Path(self.file['path'])
        path.parent.mkdir()
        path.write_bytes(b'payload')
        queue_done, cancel = threading.Event(), threading.Event()
        self.assertTrue(index.wait_for_file(self.file, cancel, queue_done))
        self.assertFalse(queue_done.is_set(), 'Another download is still running')
        path.write_bytes(b'corrupt')
        queue_done.set()
        self.assertFalse(index.wait_for_file(self.file, cancel, queue_done))
        cancel.set()
        self.assertFalse(index.wait_for_file(self.file, cancel, threading.Event()))

    def test_thumbnail_hash_and_image_type_are_checked(self):
        from angelo_agent import blender_asset_previews as previews
        png = b'\x89PNG\r\n\x1a\n' + b'bounded fixture'
        path = self.root / 'preview.png'
        path.write_bytes(png)
        output = self.root / 'out'
        output.mkdir()
        row = {'id': 'one', 'thumbnail': {'path': str(path), 'hash': hashlib.sha256(png).hexdigest()}}
        found = previews._copy([row], output, threading.Event(), False)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1].read_bytes(), png)
        path.write_bytes(b'changed')
        self.assertFalse(previews._copy([row], output, threading.Event(), False))

    def test_thumbnail_metadata_is_optional_and_bounded(self):
        raw = asset_record()
        raw['thumbnail'] = {'url': 'images/preview.webp', 'hash': 'SHA256:' + 'c' * 64}
        self.write_listing(raw)
        self.assertTrue(index.compact(index.remote_rows(self.root, URL, (5, 3))[0])['preview_available'])
        for url in ('../outside.webp', 'asset.blend', '%2e%2e/image.webp', 'image.webp?secret=1'):
            raw['thumbnail']['url'] = url
            self.write_listing(raw)
            row = index.remote_rows(self.root, URL, (5, 3))[0]
            self.assertFalse(index.compact(row)['preview_available'])


if __name__ == '__main__':
    unittest.main()
