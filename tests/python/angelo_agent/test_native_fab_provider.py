# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fab's transport-independent boundaries. Fake transport, real file checks/cache; NOT live authentication."""

import suite_isolation  # noqa: F401  First: never expose real provider logins.


from contextlib import contextmanager
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import team_library_fixtures as f
from angelo_agent import fab_cache, fab_catalog, fab_provider, library_fetch
from angelo_agent.fab_catalog import FabError

IDENTITY = 'b46eb0db-14de-442c-900f-86b957ea481c'


def metadata(size):
    return {'id': IDENTITY, 'title': 'Test model', 'author': 'Example author', 'license': 'Fab Standard',
            'kind': 'model', 'access': 'free', 'variants': [
                {'id': quality, 'revision': 'v1', 'format': 'glb', 'quality': quality, 'size': size}
                for quality in ('low', 'medium', 'high')]}


class FakeTransport:
    def __init__(self, payload):
        self.payload = payload
        self.rows = [metadata(len(payload))]
        self.calls, self.closed = [], 0
        self.allowed = True
        self.on_chunk = lambda: None

    def search(self, query, limit, include_paid, cancel):
        self.calls.append(('search', query))
        return copy.deepcopy(self.rows)

    def authorize(self, item, variant, cancel):
        self.calls.append(('authorize', variant.identity))
        return self.allowed

    @contextmanager
    def stream(self, item, variant, cancel):
        self.calls.append(('payload', variant.identity))

        def chunks():
            self.on_chunk()
            yield self.payload
        try:
            yield chunks()
        finally:
            self.closed += 1


class FabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = FakeTransport(f.glb(images=[f.png()]))
        self.session = fab_provider.Session(self.root, 'account-a', self.client)
        self.addCleanup(self.session.disconnect)
        self.identity = 'fab:' + IDENTITY

    def select(self, session=None, variant='low'):
        session = session or self.session
        session.search('surface')
        return session.selection(self.identity, variant)

    def prepare(self, session=None, variant='low', **kwargs):
        session = session or self.session
        return session.prepare(self.identity, variant, expected=self.select(session, variant), **kwargs)

    def assert_code(self, code, callback):
        with self.assertRaises(FabError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def paid(self, access='purchase_required'):
        self.client.rows[0].update(access=access, offers=[{'amount': '19.99', 'currency': 'USD', 'tier': 'Personal'}])

    def test_paid_suggestions_off_by_default_but_free_and_owned_remain(self):
        self.paid()
        self.assertEqual(self.session.search('surface')['items'], [])
        self.client.rows[0]['access'] = 'owned'
        owned = self.session.search('surface')['items']
        self.assertEqual(len(owned), 1)
        self.assertNotIn('offers', owned[0])
        self.assertNotIn('listing_url', owned[0])
        self.assertFalse(any(c[0] != 'search' for c in self.client.calls))

    def test_opt_in_shows_price_tier_link_without_authorizing_or_downloading(self):
        self.paid()
        self.session.set_suggest_paid(True)
        row = self.session.search('surface')['items'][0]
        self.assertEqual(row['offers'], [{'amount': '19.99', 'currency': 'USD', 'tier': 'Personal'}])
        self.assertEqual(row['listing_url'], 'https://www.fab.com/listings/' + IDENTITY)
        self.assertIn('checkout', row['price_note'])
        self.assertEqual([c[0] for c in self.client.calls], ['search'])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_turning_off_removes_old_paid_selections_and_future_results(self):
        self.paid()
        self.session.set_suggest_paid(True)
        expected = self.select()
        self.session.set_suggest_paid(False)
        self.assert_code('EXPIRED', lambda: self.session.prepare(self.identity, 'low', expected=expected))
        self.assertEqual(self.session.search('surface')['items'], [])

    def test_paid_opt_in_never_permits_purchase_or_unentitled_payload(self):
        self.paid()
        self.session.set_suggest_paid(True)
        self.client.allowed = False
        self.assert_code('PURCHASE_REQUIRED', self.prepare)
        self.assertFalse(any(c[0] == 'payload' for c in self.client.calls))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_purchase_then_refresh_authorization_allows_explicit_import(self):
        self.paid()
        self.session.set_suggest_paid(True)
        self.client.allowed = False
        self.assert_code('PURCHASE_REQUIRED', self.prepare)
        self.client.rows[0]['access'] = 'owned'
        self.client.allowed = True  # Simulates a purchase completed by the person on Fab, never by this provider.
        self.session.set_suggest_paid(False)
        with self.prepare() as prepared:
            self.assertFalse(prepared.cache_hit)
        self.assertEqual(sum(c[0] == 'payload' for c in self.client.calls), 1)

    def test_unknown_access_is_hidden_and_free_price_is_not_entitlement(self):
        self.paid('unknown')
        self.assertEqual(self.session.search('surface')['items'], [])
        self.client.rows[0].update(access='free', offers=[{'amount': '0.00', 'currency': 'USD', 'tier': 'Personal'}])
        self.client.allowed = False
        self.assert_code('ACQUISITION_REQUIRED', self.prepare)

    def test_preference_switched_off_during_search_filters_late_results(self):
        self.paid()
        self.session.set_suggest_paid(True)
        original = self.client.search
        def search(*args):
            rows = original(*args)
            self.session.set_suggest_paid(False)
            return rows
        with patch.object(self.client, 'search', side_effect=search):
            self.assertEqual(self.session.search('surface')['items'], [])

    def test_price_validation_refuses_nonfinite_negative_or_mixed_free_paid(self):
        self.session.set_suggest_paid(True)
        for amount in ('NaN', '-10', '1e999', '0.01 USD'):
            self.paid()
            self.client.rows[0]['offers'][0]['amount'] = amount
            self.assert_code('INVALID_METADATA', lambda: self.session.search('surface'))
        self.paid('free')
        self.assert_code('INVALID_METADATA', lambda: self.session.search('surface'))

    def test_discovery_and_preapproval_never_download_or_write_payloads(self):
        for query in ('rock', 'wood', 'chair', 'glass', 'brick', 'soil', 'cloth', 'car', 'vase', 'door'):
            result = self.session.search(query)
            self.session.selection(result['items'][0]['id'], 'medium')
        self.assertEqual([kind for kind, _ in self.client.calls], ['search'] * 10)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertFalse(result['scene_changed'])

    def test_only_selected_quality_downloads_repeat_reauthorizes_without_payload(self):
        with self.prepare(variant='medium') as first:
            self.assertFalse(first.cache_hit)
            self.assertEqual(first.facts['format'], 'glb')
        with self.prepare(variant='medium') as second:
            self.assertTrue(second.cache_hit)
        self.assertEqual([c for c in self.client.calls if c[0] == 'payload'], [('payload', 'medium')])
        self.assertEqual(sum(c[0] == 'authorize' for c in self.client.calls), 2)
        self.assertEqual(library_fetch.held(), {})

    def test_cache_never_grants_entitlement(self):
        with self.prepare():
            pass
        self.client.allowed = False
        self.assert_code('ACQUISITION_REQUIRED', self.prepare)
        self.assertEqual(sum(c[0] == 'payload' for c in self.client.calls), 1)

    def test_terms_required_and_auth_expiry_remain_closed_errors(self):
        for code in ('TERMS_REQUIRED', 'AUTH_REQUIRED'):
            with self.subTest(code=code), patch.object(self.client, 'authorize', side_effect=FabError(code)):
                self.assert_code(code, self.prepare)
        self.assertFalse(any(c[0] == 'payload' for c in self.client.calls))

    def test_account_scope_does_not_share_files(self):
        other = fab_provider.Session(self.root, 'account-b', self.client)
        with self.prepare() as first, self.prepare(other) as second:
            self.assertNotEqual(first.facts['model'], second.facts['model'])
            self.assertFalse(second.cache_hit)
        self.assertEqual(sum(c[0] == 'payload' for c in self.client.calls), 2)

    def test_changed_revision_or_size_requires_new_approval(self):
        expected = self.select()
        self.client.rows[0]['variants'][0]['revision'] = 'v2'
        self.session.search('surface')
        self.assert_code('EXPIRED', lambda: self.session.prepare(self.identity, 'low', expected=expected))
        self.assertFalse(any(c[0] == 'authorize' for c in self.client.calls))

    def test_revision_changes_do_not_reuse_previous_payload(self):
        with self.prepare() as first:
            old_path = first.facts['model']
        self.client.rows[0]['variants'][0]['revision'] = 'v2'
        with self.prepare() as second:
            self.assertNotEqual(second.facts['model'], old_path)
            self.assertFalse(second.cache_hit)

    def test_cached_corruption_is_redownloaded_and_rechecked(self):
        with self.prepare() as first:
            path = first.facts['model']
        path.write_bytes(b'x' * len(self.client.payload))
        with self.prepare() as second:
            self.assertFalse(second.cache_hit)
            self.assertEqual(path.read_bytes(), self.client.payload)

    def test_metadata_has_no_transport_secret_or_url_fields(self):
        self.client.rows[0].update(url='https://secret.example/token', account='private', cookies='private')
        result = self.session.search('surface')
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('secret.example', json.dumps(result))
        self.assertEqual(result['items'][0]['variants'][0]['bytes'], len(self.client.payload))

    def test_raw_transport_error_is_not_returned(self):
        with patch.object(self.client, 'search', side_effect=RuntimeError('Bearer secret-token')):
            error = self.assert_code('UNAVAILABLE', lambda: self.session.search('surface'))
        self.assertNotIn('secret', str(error))
        with patch.object(self.client, 'stream', side_effect=RuntimeError('signed-url-secret')):
            error = self.assert_code('UNAVAILABLE', self.prepare)
        self.assertNotIn('secret', str(error))
        self.assertEqual(list(self.root.rglob('*.part')), [])

    def test_stop_before_prepare_never_authorizes_or_downloads(self):
        stop = threading.Event()
        stop.set()
        self.assert_code('CANCELLED', lambda: self.prepare(cancel=stop))
        self.assertFalse(any(c[0] in ('authorize', 'payload') for c in self.client.calls))

    def test_stop_mid_transfer_closes_stream_and_removes_partial(self):
        stop = threading.Event()
        self.client.on_chunk = stop.set
        self.assert_code('CANCELLED', lambda: self.prepare(cancel=stop))
        self.assertEqual(self.client.closed, 1)
        self.assertEqual(list(self.root.rglob('*.part')), [])
        self.assertEqual(list(self.root.rglob('asset.glb')), [])
        self.assertEqual(library_fetch.held(), {})

    def test_signout_during_transfer_and_before_consumption_blocks_import(self):
        self.client.on_chunk = self.session.disconnect
        self.assert_code('AUTH_REQUIRED', self.prepare)
        fresh = fab_provider.Session(self.root, 'account-a', self.client)
        self.client.on_chunk = lambda: None
        prepared = self.prepare(fresh)
        fresh.disconnect()
        called = []
        self.assert_code('AUTH_REQUIRED', lambda: prepared.consume(lambda *_: called.append(True)))
        self.assertEqual(called, [])
        self.assertEqual(library_fetch.held(), {})

    def test_checked_handoff_reuses_importer_contract_and_is_single_use(self):
        prepared = self.prepare()
        result = prepared.consume(lambda item, facts: (item, facts))
        self.assertEqual(result[0]['source'], 'fab')
        self.assertEqual(result[0]['source_url'], 'https://www.fab.com/listings/' + IDENTITY)
        self.assertEqual(result[1]['format'], 'glb')
        self.assert_code('EXPIRED', lambda: prepared.consume(lambda *_: self.fail('must not import twice')))
        self.assertEqual(library_fetch.held(), {})

    def test_importer_failure_still_releases_lease(self):
        prepared = self.prepare()
        def fail(*_args):
            raise RuntimeError('import failed')
        with self.assertRaisesRegex(RuntimeError, 'import failed'):
            prepared.consume(fail)
        self.assertEqual(library_fetch.held(), {})

    def test_wrong_size_and_dangerous_glb_never_publish(self):
        cases = [b'BLENDER' + bytes(20), self.client.payload[:-8], self.client.payload + b'extra',
                 f.glb({'asset': {'version': '2.0'}, 'images': [{'uri': '../outside.png'}]}),
                 f.glb(images=[f.png_header(50000, 50000)])]
        for payload in cases:
            with self.subTest(payload=payload[:12]):
                self.client.payload = payload
                with self.assertRaises(FabError):
                    self.prepare()
                self.assertEqual(list(self.root.rglob('asset.glb')), [])
                self.assertEqual(list(self.root.rglob('*.part')), [])

    def test_external_reference_with_matching_size_is_refused_by_format_checks(self):
        self.client.payload = f.glb({'asset': {'version': '2.0'}, 'images': [{'uri': 'file:///secret.png'}]})
        self.client.rows = [metadata(len(self.client.payload))]
        self.assert_code('CORRUPT', self.prepare)
        self.assertEqual(list(self.root.rglob('asset.glb')), [])

    def test_link_in_cache_is_refused_and_target_not_touched(self):
        with self.prepare() as prepared:
            path = prepared.facts['model']
        outside = self.root / 'outside.glb'
        outside.write_bytes(self.client.payload)
        path.unlink()
        path.symlink_to(outside)
        self.assert_code('CACHE_UNAVAILABLE', self.prepare)
        self.assertTrue(path.is_symlink())
        self.assertEqual(outside.read_bytes(), self.client.payload)

    def test_linked_cache_parent_cannot_create_files_outside_cache(self):
        outside = self.root / 'outside'
        outside.mkdir()
        (self.root / 'fab').symlink_to(outside, target_is_directory=True)
        self.assert_code('CACHE_UNAVAILABLE', self.prepare)
        self.assertEqual(list(outside.iterdir()), [])

    def test_overlarge_and_empty_chunks_close_the_stream(self):
        for payload in (b'', b'x' * (fab_cache.CHUNK + 1)):
            with self.subTest(size=len(payload)):
                self.client.payload = payload
                self.assert_code('CORRUPT', self.prepare)
                self.assertEqual(list(self.root.rglob('*.part')), [])

    def test_cache_trim_preserves_a_prepared_import_until_consumed(self):
        prepared = self.prepare()
        path = prepared.facts['model']
        library_fetch.trim(path.parent.parent, 0)
        self.assertTrue(path.exists())
        prepared.close()
        library_fetch.trim(path.parent.parent, 0)
        self.assertFalse(path.exists())

    def test_stop_while_waiting_for_another_transfer_never_fetches(self):
        stop, authorized = threading.Event(), threading.Event()
        original = self.client.authorize
        def authorize(*args):
            result = original(*args)
            authorized.set()
            return result
        fab_cache._transfer_lock.acquire()
        try:
            with patch.object(self.client, 'authorize', side_effect=authorize), ThreadPoolExecutor(1) as pool:
                future = pool.submit(self.prepare, cancel=stop)
                self.assertTrue(authorized.wait(5))
                stop.set()
                self.assert_code('CANCELLED', lambda: future.result(timeout=5))
        finally:
            fab_cache._transfer_lock.release()
        self.assertFalse(any(c[0] == 'payload' for c in self.client.calls))

    def test_concurrent_imports_reuse_one_transfer_with_independent_leases(self):
        with ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(self.prepare) for _ in range(2)]
            prepared = [future.result(timeout=5) for future in futures]
        try:
            self.assertEqual(sum(c[0] == 'payload' for c in self.client.calls), 1)
            self.assertEqual(sum(p.cache_hit for p in prepared), 1)
            path = prepared[0].facts['model']
            prepared[0].close()
            library_fetch.trim(path.parent.parent, 0)
            self.assertTrue(path.exists())
        finally:
            for result in prepared:
                result.close()
        self.assertEqual(library_fetch.held(), {})

    def test_expired_selection_is_not_authorized(self):
        self.select()
        with patch.object(fab_provider.time, 'monotonic', return_value=10**20):
            self.assert_code('EXPIRED', lambda: self.session.selection(self.identity, 'low'))

    def test_catalog_limits_and_path_shaped_identifiers_are_refused(self):
        for key, value in (('id', '../elsewhere'), ('license', 'unknown'), ('kind', 'texture'), ('title', 'bad\nline')):
            with self.subTest(key=key):
                self.client.rows = [metadata(len(self.client.payload))]
                self.client.rows[0][key] = value
                self.assert_code('INVALID_METADATA', lambda: self.session.search('surface'))
        self.client.rows = [metadata(len(self.client.payload))] * 9
        self.assert_code('LIMIT_EXCEEDED', lambda: self.session.search('surface'))
        self.assert_code('LIMIT_EXCEEDED', lambda: self.session.search('surface', limit=True))

    def test_thumbnail_or_texture_bytes_never_appear_in_search_contract(self):
        row = metadata(len(self.client.payload))
        row['variants'][0]['size'] = 1 << 40
        self.client.rows = [row]
        self.assert_code('LIMIT_EXCEEDED', lambda: self.session.search('surface'))
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
