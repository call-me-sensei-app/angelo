# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fab results show their own picture, an asset not in the person's library says how to add it, Search resumes a
kept sign-in, and the managed browser stays behind Angelo. Host-only: no browser, account or network."""
import suite_isolation  # noqa: F401
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import team_library_fixtures  # noqa: F401
from angelo_agent import fab_browser_background, fab_catalog, fab_host_state, fab_ui
from angelo_agent.fab_browser_listing_page import normalize_links

LISTING = 'f4748cb1-faba-4415-8fab-5a23f5437ea4'
PICTURE = ('https://media.fab.com/image_previews/gallery_images/66a890ed-55d8-4c6e-8d3c-ab26dff0c684/'
           '86affea1-995c-402e-96cf-9f82a5b526b4.jpg')


class ResultPictureTests(unittest.TestCase):
    def test_only_a_plain_picture_on_fabs_image_host_is_kept(self):
        self.assertEqual(fab_catalog.thumb_url(PICTURE), PICTURE)
        for other in ('http://media.fab.com/image_previews/a/b.jpg', 'https://media.fab.com.evil.test/image_previews/a.jpg',
                      'https://www.fab.com/image_previews/a.jpg', PICTURE + '?sig=1', 'https://media.fab.com/../x.jpg',
                      'https://media.fab.com/image_previews/a/b.exe', None, 7, 'x' * 400):
            self.assertIsNone(fab_catalog.thumb_url(other), other)

    def test_a_search_card_keeps_its_picture_and_drops_a_foreign_one(self):
        href = 'https://www.fab.com/listings/' + LISTING
        kept, = normalize_links([{'href': href, 'title': 'Red Chair', 'image': PICTURE}], 8)
        self.assertEqual(kept.compact()['thumb'], PICTURE)
        dropped, = normalize_links([{'href': href, 'title': 'Red Chair', 'image': 'https://tracker.test/p.jpg'}], 8)
        self.assertNotIn('thumb', dropped.compact())
        bare, = normalize_links([{'href': href, 'title': 'Red Chair'}], 8)
        self.assertNotIn('thumb', bare.compact())

    def test_the_picture_reaches_the_result_card_and_never_changes_the_listing_facts(self):
        variant = {'id': 'v' * 16, 'revision': 'r' * 16, 'format': 'glb', 'quality': 'high', 'size': 1024}
        row = {'id': LISTING, 'title': 'Chair', 'author': 'Maker', 'license': 'Fab Standard', 'kind': 'model',
               'access': 'unknown', 'variants': [variant], 'thumb': PICTURE}
        item = fab_catalog.Item.parse(row)
        self.assertEqual(item.compact()['thumb'], PICTURE)
        self.assertEqual(fab_catalog.Item.parse(dict(row, thumb='https://tracker.test/p.jpg')).thumb, None)
        shown = fab_host_state.discoveries({'items': [dict(item.compact(), id='fab:' + LISTING)]})
        self.assertEqual(shown[0]['thumb'], PICTURE)


class AddToLibraryTests(unittest.TestCase):
    def test_not_in_the_library_says_add_it_before_permission_unknown(self):
        from angelo_agent.fab_browser_listing_owner import listing_from_page
        row = SimpleNamespace(disabled=False, control_fingerprint='c' * 32, filename='chair.glb', quality_label=None)
        page = SimpleNamespace(listing_id=LISTING, title='Chair', author='Maker', license_label='Standard License',
                               choices=(row,), library_state_label=None)
        observed = listing_from_page(page)
        self.assertEqual((observed.entitlement, observed.download_permission), ('acquisition_required', 'unknown'))
        saved = listing_from_page(SimpleNamespace(**dict(vars(page), library_state_label='Saved in My Library')))
        self.assertEqual((saved.entitlement, saved.download_permission), ('download_available', 'available'))
        self.assertEqual(fab_ui.reason('ACQUISITION_REQUIRED'),
                         fab_ui.iface_('Add this asset to your Fab library before importing it.'))



class AddInsideAngeloTests(unittest.TestCase):
    def connection(self, states):
        import threading
        calls = []
        browser = SimpleNamespace(call=lambda method, params, **kw: calls.append((method, params.get('url'))) or {})
        def evaluate(script, stop=None, seconds=None):
            calls.append(('evaluate', 'Add to My Library' in script))
            return {'state': states.pop(0)}
        return SimpleNamespace(_lock=threading.RLock(), _browser=browser, _session='s1', _evaluate=evaluate,
                               _combined_stop=lambda stop: stop), calls

    def test_the_accepted_license_adds_it_hidden_and_waits_for_saved(self):
        from angelo_agent import fab_browser_library_add as add
        owner, calls = self.connection(['loading', 'clicked', 'not_saved', 'saved'])
        with mock.patch.object(add.time, 'sleep'):
            self.assertTrue(add.add_to_library(owner, LISTING))
        self.assertEqual(calls[0], ('Page.navigate', 'https://www.fab.com/listings/' + LISTING))
        self.assertEqual([c for c in calls if c[0] == 'evaluate'], [('evaluate', True)] * 2 + [('evaluate', False)] * 2)

    def test_no_single_add_button_leaves_it_to_the_person(self):
        from angelo_agent import fab_browser_library_add as add
        for states in (['ambiguous'], ['missing'] * 3):
            owner, _calls = self.connection(list(states))
            with mock.patch.object(add.time, 'sleep'), mock.patch.object(add.time, 'monotonic',
                                                                         side_effect=[0, 0, 0, 0, 99, 99, 99]):
                self.assertFalse(add.add_to_library(owner, LISTING, seconds=5))

    def test_the_press_is_only_fabs_own_visible_button_outside_a_dialog(self):
        from angelo_agent import fab_browser_library_add as add
        self.assertIn("n.textContent.trim()==='Add to My Library'", add._ADD_SCRIPT)
        self.assertIn("!dialogs.some(d=>d.contains(n))", add._ADD_SCRIPT)
        self.assertIn("found.length===1", add._ADD_SCRIPT)



class PaidListingTests(unittest.TestCase):
    def test_a_shown_price_is_a_currency_and_a_number_or_free(self):
        from angelo_agent.fab_browser_listing_page import price_text
        for shown in ("Free", "$12.99", "¥1,264", "12,99 €", "US$ 5", "R$ 49,90", "kr 99"):
            self.assertEqual(price_text(shown), shown)
        for other in ("3D", "4K", "by Max3d", "", "$" + "9" * 40, None, 12):
            self.assertIsNone(price_text(other))

    def test_a_paid_card_keeps_its_price_and_is_marked_paid(self):
        from angelo_agent import fab_browser_listing_page as page
        href = page.ORIGIN + "/listings/" + LISTING
        found = page.normalize_links([{"href": href, "title": "Chair", "price": "$12.99"}], 8)
        row = found[0].compact()
        self.assertEqual((row["price"], row["paid"]), ("$12.99", True))
        self.assertNotIn("price", page.normalize_links([{"href": href, "title": "Chair", "price": "3D"}], 8)[0].compact())

    def test_agent_search_names_a_paid_listing_instead_of_failing(self):
        from angelo_agent.fab_catalog import FabError
        from angelo_agent.fab_browser_transport import BrowserTransport

        def listing(identity, stop):
            raise FabError("PURCHASE_REQUIRED")
        owner = SimpleNamespace(public_search=lambda q, n, stop: {"items": [
            {"id": "fab:" + LISTING, "title": "Chair", "price": "$12.99"}]})
        transport = BrowserTransport.__new__(BrowserTransport)
        transport._owner = owner
        transport._check = lambda stop: None
        transport._listing = listing
        stop = SimpleNamespace(check=lambda: None)
        with mock.patch("angelo_agent.fab_browser_background.place"):
            self.assertEqual(transport.search("chair", 8, True, stop),
                             [{"id": LISTING, "title": "Chair", "price": "$12.99", "purchase_required": True}])
            self.assertEqual(transport.search("chair", 8, False, stop), [])


class SearchResumesTests(unittest.TestCase):
    def tearDown(self):
        fab_ui._PENDING.clear()

    def test_search_while_disconnected_connects_then_runs_once_connected(self):
        calls = []
        host = SimpleNamespace(fab_connect=lambda window: calls.append('connect') or 1,
                               fab_search=lambda window, query, limit=8: calls.append(('search', query)) or 2)
        view = {'status': 'disconnected', 'can_connect': True, 'generation': 'g' * 32, 'request_id': 0}
        with mock.patch.dict(sys.modules, {'angelo_agent.library_host': host}), \
                mock.patch('angelo_agent.library_host', host, create=True), \
                mock.patch.object(fab_ui, 'snapshot', side_effect=lambda: view), \
                mock.patch.object(fab_ui, 'epoch', return_value=('g' * 32, 0)):
            self.assertTrue(fab_ui.can_start(view))
            self.assertTrue(fab_ui.search(None, 'chair', ('g' * 32, 0)))
            self.assertEqual(calls, ['connect'])
            self.assertFalse(fab_ui.resume_pending())   # Still connecting: nothing runs yet.
            view.update(status='connected', can_search=True)
            self.assertTrue(fab_ui.resume_pending())
            self.assertFalse(fab_ui.resume_pending())   # It runs once.
        self.assertEqual(calls, ['connect', ('search', 'chair')])

    def test_a_failed_connection_drops_the_waiting_search(self):
        fab_ui._PENDING[:] = ['chair']
        with mock.patch.object(fab_ui, 'snapshot', return_value={'status': 'error'}):
            self.assertFalse(fab_ui.resume_pending())
        self.assertEqual(fab_ui._PENDING, [])


    def test_an_expired_sign_in_clears_then_opens_a_fresh_sign_in(self):
        calls = []
        host = SimpleNamespace(fab_disconnect=lambda window: calls.append('disconnect') or 3,
                               fab_connect=lambda window: calls.append('connect') or 4)
        view = {'status': 'error', 'reason_code': 'ACCOUNT_UNVERIFIED'}
        try:
            with mock.patch.dict(sys.modules, {'angelo_agent.library_host': host}), \
                    mock.patch('angelo_agent.library_host', host, create=True), \
                    mock.patch.object(fab_ui, 'snapshot', side_effect=lambda: view), \
                    mock.patch.object(fab_ui, 'epoch', return_value=('g' * 32, 0)):
                self.assertTrue(fab_ui.expired(view))
                self.assertTrue(fab_ui.sign_in_again(None, ('g' * 32, 0)))
                self.assertFalse(fab_ui.resume_pending())   # Still disconnecting.
                view.update(status='disconnected', reason_code=None, can_connect=True)
                self.assertTrue(fab_ui.resume_pending())
                self.assertFalse(fab_ui.resume_pending())
        finally:
            fab_ui._RECONNECT.clear()
        self.assertEqual(calls, ['disconnect', 'connect'])


class BackgroundBrowserTests(unittest.TestCase):
    def test_off_macos_or_without_a_browser_nothing_is_touched(self):
        with mock.patch.object(fab_browser_background.sys, 'platform', 'win32'):
            self.assertFalse(fab_browser_background.background(1234))
            self.assertFalse(fab_browser_background.foreground(1234))
        for pid in (None, 0, -1, True, '12'):
            self.assertFalse(fab_browser_background.background(pid))

    def test_showing_a_listing_reopens_that_listing_then_brings_the_browser_forward(self):
        navigated = []
        browser = SimpleNamespace(_pid=4321, call=lambda method, params, session=None: navigated.append(
            (method, params['url'], session)) or {})
        owner = SimpleNamespace(_browser=browser, _session='s1')
        with mock.patch.object(fab_browser_background, 'foreground', return_value=True) as front, \
                mock.patch.object(fab_browser_background, 'background', return_value=True) as back:
            self.assertTrue(fab_browser_background.place(owner, True, LISTING))
            self.assertTrue(fab_browser_background.place(owner, False))
        self.assertEqual(navigated, [('Page.navigate', 'https://www.fab.com/listings/' + LISTING, 's1')])
        front.assert_called_once_with(4321)
        back.assert_called_once_with(4321)


if __name__ == '__main__':
    unittest.main()
