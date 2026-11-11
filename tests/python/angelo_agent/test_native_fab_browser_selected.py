# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synchronous selected-transfer owner fixtures; browser/file boundaries supplied independently, no live auth."""
import suite_isolation  # noqa: F401
import hashlib
import sys,threading
from pathlib import Path
from contextlib import contextmanager
from dataclasses import replace
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from team_library_fixtures import glb
from angelo_agent import fab_browser_selected as selected, fab_cache
from angelo_agent.fab_browser_account import AccountObservation
from angelo_agent.fab_browser_connection import Connection
from angelo_agent.fab_browser_listing_owner import listing_from_page
from angelo_agent.fab_browser_listing_page import BUTTON_ARIA, ListingObservationUnavailable, parse_listing_observation
from angelo_agent.fab_browser_process import BrowserUnavailable
from angelo_agent.fab_browser_transport import BrowserTransport
from angelo_agent.fab_browser_variants import BrowserChoice
from angelo_agent.fab_catalog import FabError
from angelo_agent.fab_host_jobs import Stop
from angelo_agent.fab_provider import Session
ID='05d847c0-7ab9-435c-8ca6-d18216b01c17';GUID='selected-guid'


def listing_row(ordinal, filename='actual-low.glb'):
    return dict(ordinal=ordinal, filename=filename, group='Converted Files', format='glb', quality_label='Low quality',
                quality_aria='This product is of _low. quality', display_size='1 KB',
                button_type='button', button_aria=BUTTON_ARIA, button_disabled=False)


def listing_page(*rows):
    listing = dict(state='observed', listing_id=ID, title='Fixture model', author='Fixture author',
                   license_label='Standard License', library_state_label='Saved in My Library', rows=list(rows))
    return parse_listing_observation(listing, expected_listing_id=ID)


PAGE = listing_page(listing_row(0))
ROW = PAGE.choices[0]
APPROVED_ITEM = listing_from_page(PAGE).item()
CHOICE = APPROVED_ITEM.variants[0].browser_choice

class SelectedTests(unittest.TestCase):
 @contextmanager
 def fixture(self,*,cancel=False,allow_error=False,deny_error=False,file_error=False,payload=b'12345678'):
  events=[];stop=threading.Event();account=AccountObservation('account', 'profile', 'runtime')
  row=SimpleNamespace(control_fingerprint=ROW.control_fingerprint, disabled=False, format=ROW.format,
                      filename=ROW.filename, quality_label=ROW.quality_label)
  page=SimpleNamespace(listing_id=PAGE.listing_id,title=PAGE.title,author=PAGE.author,
                       library_state_label=PAGE.library_state_label,license_label=PAGE.license_label,choices=(row,))
  browser=SimpleNamespace(_closed=False,sink=None)
  def call(method,params=None,**kwargs):
   events.append((method,params))
   if method=='Page.getFrameTree':return {'frameTree':{'frame':{'id':'actual-frame'}}}
   if method=='Browser.setDownloadBehavior':
    if allow_error and params['behavior']=='allowAndName':raise BrowserUnavailable('TIMEOUT')
    if deny_error and params['behavior']=='deny':raise BrowserUnavailable('TIMEOUT')
   return {}
  def close():events.append(('browser-drained',None));browser._closed=True
  def poll(tracker,**kwargs):
   if tracker.observation().guid is None:
    tracker.accept('Browser.downloadWillBegin',{'guid':GUID,'frameId':'actual-frame','suggestedFilename':'actual-low.glb'})
   state = 'canceled' if cancel or tracker.observation().cancel_requested else 'completed'
   tracker.accept('Browser.downloadProgress', {'guid': GUID, 'state': state,
                                              'receivedBytes': len(payload), 'totalBytes': len(payload)})
   return tracker.observation()
  browser.call=call;browser.close=close;browser._poll_selected_download=poll
  browser._set_download_sink=lambda sink:setattr(browser,'sink',sink)
  browser._advance_download_sink=lambda prior,current:browser._set_download_sink(current.accept)
  connection=SimpleNamespace(_lock=threading.RLock(),_browser=browser,_session='actual-session',_target='actual-target',
   _selected_transfer=None,_download_cleanup=None,_combined_stop=lambda value:value,current_account=lambda value:account)
  class Landing:
   path=Path('/private/tmp/fixture-private-landing')
   def remove(self):events.append(('landing-removed',None));self.path=None
  class File:
   size = len(payload)
   sha256 = hashlib.sha256(payload).hexdigest()
   def __init__(self,*args):
    events.append(('file-verified',None))
    if file_error:
     error=BrowserUnavailable('DOWNLOAD_FILE_CLOSE_UNPROVED');error.owner=self;raise error
   def chunks(self):return iter((payload,))
   def retire(self):
    events.append(('file-retired',None));self.close()
   def close(self):
    events.append(('file-closed',None))
    if file_error:raise BrowserUnavailable('DOWNLOAD_FILE_CLOSE_UNPROVED')
  with patch.object(selected,'observe_listing_for_transfer',return_value=(account,page)),patch.object(selected,'click')as click,patch.object(selected.platform_owner,'file_types',return_value=(Landing,File)):
   yield connection,account,page,events,stop,click
 def test_actual_file_retires_but_landing_and_observer_survive_until_browser_drain(self):
  with self.fixture()as(c,a,p,events,stop,click):
   with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024)as completed:
    self.assertEqual(b''.join(completed.chunks),b'12345678');self.assertIsNotNone(c._selected_transfer)
    self.assertNotIn(('landing-removed',None),events);self.assertIsNotNone(c._browser.sink)
   self.assertEqual(events[-2:],[('file-retired',None),('file-closed',None)])
   self.assertNotIn(('landing-removed',None),events);self.assertIsNotNone(c._download_landing)
   self.assertIsNone(c._selected_transfer);self.assertEqual(c._download_cleanup,(c._download_landing,None))
   self.assertTrue(c._download_tracker._retired);self.assertIsNotNone(c._browser.sink);click.assert_called_once()
 def test_second_transfer_early_account_rejection_keeps_prior_landing_until_native_drain(self):
  with self.fixture()as(c,a,p,events,stop,click):
   with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024)as completed:
    self.assertEqual(b''.join(completed.chunks),b'12345678')
   retained=c._download_landing;self.assertIsNotNone(retained)
   with patch.object(selected,'observe_listing_for_transfer',return_value=(object(),p)),self.assertRaisesRegex(BrowserUnavailable,'AUTH_REQUIRED'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):pass
   self.assertIn(('browser-drained',None),events);self.assertIn(('landing-removed',None),events)
   self.assertIn(('browser-drained',None),events)
   self.assertLess(events.index(('browser-drained',None)),events.index(('landing-removed',None)))
   self.assertIsNone(c._download_landing);self.assertIsNone(c._download_cleanup);self.assertIsNone(retained.path)
 def test_existing_owner_is_not_cleared_by_nested_busy_attempt(self):
  with self.fixture()as(c,a,p,events,stop,click):
   prior=object();c._selected_transfer=prior
   with self.assertRaisesRegex(BrowserUnavailable,'BUSY'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):pass
   self.assertIs(c._selected_transfer,prior);self.assertEqual(events,[])
 def test_current_account_mismatch_refuses_before_allow_or_click(self):
  with self.fixture()as(c,a,p,events,stop,click):
   with self.assertRaisesRegex(BrowserUnavailable,'AUTH_REQUIRED'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,object(),stop,256*1024*1024):pass
   self.assertFalse(any(m=='Browser.setDownloadBehavior'for m,v in events));click.assert_not_called()
 def test_disabled_selection_refuses_before_allow_or_click(self):
  with self.fixture()as(c,a,p,events,stop,click):
   p.choices[0].disabled=True
   with self.assertRaisesRegex(BrowserUnavailable,'DOWNLOAD_SELECTION_CHANGED'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):pass
   click.assert_not_called();self.assertFalse(any(m=='Browser.setDownloadBehavior'for m,v in events))
 def test_actual_canceled_terminal_never_verifies_or_yields_file(self):
  with self.fixture(cancel=True)as(c,a,p,events,stop,click):
   with self.assertRaisesRegex(BrowserUnavailable,'CANCELLED'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):self.fail('canceled file yielded')
   self.assertNotIn(('file-verified',None),events);self.assertEqual(events[-1],('landing-removed',None))
 def test_lost_allow_ack_without_terminal_drains_before_landing_remove(self):
  with self.fixture(allow_error=True)as(c,a,p,events,stop,click):
   c._browser._poll_selected_download=lambda *a,**k:(_ for _ in()).throw(BrowserUnavailable('TIMEOUT'))
   with self.assertRaisesRegex(BrowserUnavailable,'TIMEOUT'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):pass
   self.assertIn(('browser-drained',None),events)
   self.assertLess(events.index(('browser-drained',None)),events.index(('landing-removed',None)))
   self.assertIsNone(c._browser);click.assert_not_called()
 def test_deny_ack_loss_drains_browser_before_cleanup(self):
  with self.fixture(deny_error=True)as(c,a,p,events,stop,click):
   with self.assertRaisesRegex(BrowserUnavailable,'TIMEOUT'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):pass
   self.assertIn(('browser-drained',None),events)
   self.assertLess(events.index(('browser-drained',None)),events.index(('landing-removed',None)))
 def test_foreign_protocol_uncertainty_drains_even_after_selected_completed(self):
  with self.fixture()as(c,a,p,events,stop,click):
   original=c._browser.call;browser=c._browser
   def call(method,params=None,**kwargs):
    if method=='Browser.setDownloadBehavior'and params['behavior']=='deny'and not getattr(browser,'_download_uncertain',False):
     browser._download_uncertain=True;raise BrowserUnavailable('DOWNLOAD_SELECTION_CHANGED')
    return original(method,params,**kwargs)
   browser.call=call
   with self.assertRaisesRegex(BrowserUnavailable,'DOWNLOAD_SELECTION_CHANGED'):
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):pass
   self.assertIn(('browser-drained',None),events)
   self.assertLess(events.index(('browser-drained',None)),events.index(('landing-removed',None)))
 def test_partial_file_cleanup_owner_retained_and_landing_not_removed(self):
  with self.fixture(file_error=True)as(c,a,p,events,stop,click):
   with self.assertRaises(BrowserUnavailable)as caught:
    with selected.open_selected(c,APPROVED_ITEM,CHOICE,a,stop,256*1024*1024):pass
   self.assertEqual(caught.exception.code,'DOWNLOAD_CLEANUP_UNPROVED');self.assertIs(caught.exception.owner,c)
   self.assertIsNotNone(c._selected_transfer);self.assertIsNotNone(c._download_cleanup)
   self.assertNotIn(('landing-removed',None),events)

 @contextmanager
 def transport_fixture(self, approved_page=PAGE):
  payload = glb()
  with self.fixture(payload=payload) as (connection, account, page, events, cancel, click), TemporaryDirectory() as root:
   page.choices += approved_page.choices[1:]
   connection.listing = Mock(return_value=listing_from_page(approved_page))
   connection._assert_download_clean = lambda: Connection._assert_download_clean(connection)
   connection.open_selected = Mock(side_effect=lambda *args: Connection.open_selected(connection, *args))
   transport = BrowserTransport(connection, account)
   session = Session(Path(root), transport.account_scope, transport)
   identity = 'fab:' + ID
   session.lookup(identity)
   expected = session.selection(identity, CHOICE.local_id)
   connection.listing.reset_mock()
   connection.current_account = Mock(wraps=connection.current_account)
   yield SimpleNamespace(connection=connection, account=account, page=page, events=events, cancel=cancel,
                         click=click, transport=transport, session=session, expected=expected, root=Path(root),
                         prepare=lambda: session.prepare(identity, CHOICE.local_id, expected=expected, cancel=cancel))

 def assert_prepare_refused(self, fixture, code):
  native = Mock()
  with patch.object(fab_cache, 'prepare', wraps=fab_cache.prepare) as cache, self.assertRaises(FabError) as caught:
   with fixture.prepare() as prepared:
    prepared.consume(native)
  self.assertEqual(caught.exception.code, code)
  cache.assert_not_called()
  native.assert_not_called()
  fixture.click.assert_not_called()
  self.assertFalse(any(method == 'Browser.setDownloadBehavior' for method, _ in fixture.events))
  self.assertEqual(list(fixture.root.iterdir()), [])

 def test_prepare_authorizes_once_then_transfers_one_selected_choice(self):
  with self.transport_fixture() as fixture:
   def observe(connection, item_id, stop):
    # Two reads bracket the full authorize, then one checks the account before owner entry.
    self.assertEqual(connection.current_account.call_count, 3)
    return fixture.account, fixture.page
   with patch.object(selected, 'observe_listing_for_transfer', side_effect=observe) as observed:
    with fixture.prepare() as prepared:
     self.assertFalse(prepared.cache_hit)
     self.assertEqual(prepared.facts['model'].read_bytes(), glb())
   observed.assert_called_once()
   fixture.connection.listing.assert_called_once()
   fixture.connection.open_selected.assert_called_once()
   item, variant = fixture.expected
   self.assertIs(fixture.connection.open_selected.call_args.args[0], item)
   self.assertIs(fixture.connection.open_selected.call_args.args[1], variant.browser_choice)
   fixture.click.assert_called_once()

 def test_pre_owner_account_change_precedes_navigation_or_modal_failure(self):
  observe = selected.observe_listing_for_transfer
  for failure in ('navigation', 'modal'):
   for changed in (False, True):
    with self.subTest(failure=failure, changed=changed), self.transport_fixture() as fixture:
     account = fixture.account
     observed_account = replace(account, opaque_account='different-account') if changed else account
     fixture.connection.current_account.side_effect = [account, account, observed_account, account]

     def browser_call(method, params=None, **kwargs):
      fixture.events.append((method, params))
      if method == 'Page.navigate':
       if failure == 'navigation':
        return {'errorText': 'fixture failure'}
       return {'frameId': 'frame', 'loaderId': 'loader'}
      if method == 'Page.getFrameTree':
       return {'frameTree': {'frame': {'id': 'frame', 'loaderId': 'loader'}}}
      if method == 'Page.bringToFront':
       return {}
      if method == 'Runtime.evaluate':
       expression = params['expression']
       if 'document_ready' in expression:
        value = {'state': 'document_ready', 'ready': 'complete', 'visibility': 'visible'}
       elif 'download_dialog_opening' in expression:
        value = {'state': 'download_dialog_opening', 'ready': 'complete'}
       else:
        value = {'state': 'site_changed', 'reason': 'LISTING_LICENSE_AMBIGUOUS'}
       return {'result': {'value': value}}
      self.fail(f'Unexpected selected browser command {method}')

     browser = fixture.connection._browser
     browser.call = Mock(side_effect=browser_call)
     producer_errors = []
     def record_observation(*args):
      try:
       return observe(*args)
      except (BrowserUnavailable, ListingObservationUnavailable) as error:
       producer_errors.append(error.code)
       raise
     with patch.object(selected, 'observe_listing_for_transfer', side_effect=record_observation) as observation:
      self.assert_prepare_refused(fixture, 'AUTH_REQUIRED' if changed else 'UNAVAILABLE')
     fixture.connection.listing.assert_called_once()
     self.assertIs(fixture.session.active, not changed)
     self.assertIs(fixture.transport._revoked.is_set(), changed)
     self.assertIsNone(fixture.connection._selected_transfer)
     if changed:
      self.assertEqual(fixture.connection.current_account.call_count, 3)
      fixture.connection.open_selected.assert_not_called()
      observation.assert_not_called()
      self.assertEqual(producer_errors, [])
      browser.call.assert_not_called()
      self.assertIs(fixture.connection._browser, browser)
      self.assertEqual(fixture.events, [])
     else:
      self.assertEqual(fixture.connection.current_account.call_count, 4)
      fixture.connection.open_selected.assert_called_once()
      observation.assert_called_once()
      self.assertIsNone(fixture.connection._browser)
      self.assertEqual(producer_errors,
                       ['NAVIGATION_FAILED' if failure == 'navigation' else 'LISTING_LICENSE_AMBIGUOUS'])
      self.assertEqual(fixture.events[0][0], 'Page.navigate')
      self.assertEqual(fixture.events[-1], ('browser-drained', None))
      self.assertEqual(sum(method == 'Runtime.evaluate' for method, _ in fixture.events),
                       0 if failure == 'navigation' else 3)

 def test_cancel_during_pre_owner_account_read_precedes_account_change(self):
  observe = selected.observe_listing_for_transfer
  with self.transport_fixture() as fixture:
   browser = fixture.connection._browser
   def current_account(stop):
    if fixture.connection.current_account.call_count == 3:
     fixture.cancel.set()
     return replace(fixture.account, opaque_account='different-account')
    return fixture.account
   fixture.connection.current_account.side_effect = current_account
   with patch.object(selected, 'observe_listing_for_transfer', wraps=observe) as observation:
    self.assert_prepare_refused(fixture, 'CANCELLED')
   self.assertEqual(fixture.connection.current_account.call_count, 3)
   fixture.connection.open_selected.assert_not_called()
   observation.assert_not_called()
   self.assertTrue(fixture.session.active)
   self.assertFalse(fixture.transport._revoked.is_set())
   self.assertIs(fixture.connection._browser, browser)
   self.assertEqual(fixture.events, [])

 def test_browser_cache_hit_still_reauthorizes_and_refuses_later_entitlement_loss(self):
  with self.transport_fixture() as fixture:
   for expected_hit in (False, True):
    with fixture.prepare() as prepared:
     self.assertIs(prepared.cache_hit, expected_hit)
   self.assertEqual(fixture.connection.listing.call_count, 2)
   self.assertEqual(fixture.connection.open_selected.call_count, 2)
   self.assertEqual(fixture.click.call_count, 2)

   listing = fixture.connection.listing.return_value
   fixture.connection.listing.return_value = replace(listing, entitlement='acquisition_required')
   fixture.connection.open_selected.reset_mock()
   fixture.click.reset_mock()
   fixture.events.clear()
   with patch.object(fab_cache, 'prepare', wraps=fab_cache.prepare) as cache, self.assertRaises(FabError) as caught:
    fixture.prepare()
   self.assertEqual(caught.exception.code, 'ACQUISITION_REQUIRED')
   cache.assert_not_called()
   self.assertEqual(fixture.connection.listing.call_count, 3)
   fixture.connection.open_selected.assert_not_called()
   fixture.click.assert_not_called()
   self.assertEqual(fixture.events, [])

 def test_selected_owner_rechecks_drift_after_full_authorization(self):
  cases = [('account', 'opaque_account', 'different-account', 'AUTH_REQUIRED'),
           ('page', 'license_label', None, 'UNAVAILABLE'),
           ('page', 'library_state_label', None, 'UNAVAILABLE'),
           ('row', 'filename', 'different-low.glb', 'UNAVAILABLE'),
           ('row', 'quality_label', 'Medium quality', 'UNAVAILABLE'),
           ('row', 'disabled', True, 'UNAVAILABLE')]
  for target, field, value, code in cases:
   with self.subTest(field=field), self.transport_fixture() as fixture:
    account = fixture.account
    if target == 'account':
     account = replace(account, **{field: value})
    else:
     setattr(fixture.page if target == 'page' else fixture.page.choices[0], field, value)
    with patch.object(selected, 'observe_listing_for_transfer', return_value=(account, fixture.page)):
     self.assert_prepare_refused(fixture, code)
    fixture.connection.listing.assert_called_once()
    fixture.connection.open_selected.assert_called_once()

 def test_selected_owner_refuses_reordered_or_ambiguous_row_before_download(self):
  reordered = listing_page(listing_row(1)).choices
  for change, rows in (('reordered', reordered), ('ambiguous', (ROW, ROW))):
   with self.subTest(change=change), self.transport_fixture() as fixture:
    fixture.page.choices = rows
    self.assert_prepare_refused(fixture, 'UNAVAILABLE')
    fixture.connection.listing.assert_called_once()
    fixture.connection.open_selected.assert_called_once()

 def test_selected_owner_expires_full_item_drift_with_the_selected_row_unchanged(self):
  chosen, extra, another = listing_row(0), listing_row(1, 'extra.glb'), listing_row(2, 'another.glb')
  approved = listing_page(chosen, extra, another)
  cases = {
      'title': replace(approved, title='Changed title'),
      'author': replace(approved, author='Changed author'),
      'unselected added': listing_page(chosen, extra, another, listing_row(3, 'added.glb')),
      'unselected removed': listing_page(chosen, another),
      'unselected reordered': listing_page(chosen, dict(another, ordinal=1), dict(extra, ordinal=2)),
      'unselected changed': listing_page(chosen, dict(extra, display_size='2 KB'), another),
  }
  for change, page in cases.items():
   with self.subTest(change=change), self.transport_fixture(approved) as fixture:
    self.assertEqual(page.choices[0], approved.choices[0])
    with patch.object(selected, 'observe_listing_for_transfer', return_value=(fixture.account, page)) as observed:
     self.assert_prepare_refused(fixture, 'EXPIRED')
    fixture.connection.listing.assert_called_once()
    observed.assert_called_once()
    fixture.connection.open_selected.assert_called_once()
    self.assertEqual(fixture.events, [('browser-drained', None)])
    self.assertIsNone(fixture.connection._selected_transfer)
    self.assertIsNone(fixture.connection._browser)

 def test_prepare_refuses_changed_listing_or_permission_before_selected_owner(self):
  cases = [('title', 'Changed title', 'EXPIRED'),
           ('download_permission', 'terms_required', 'TERMS_REQUIRED')]
  for field, value, code in cases:
   with self.subTest(field=field), self.transport_fixture() as fixture:
    listing = fixture.connection.listing
    listing.return_value = replace(listing.return_value, **{field: value})
    self.assert_prepare_refused(fixture, code)
    listing.assert_called_once()
    fixture.connection.open_selected.assert_not_called()

 def test_stream_refuses_revocation_and_cancellation_before_selected_owner(self):
  for cause, code in (('revoked', 'AUTH_REQUIRED'), ('canceled', 'CANCELLED')):
   with self.subTest(cause=cause), self.transport_fixture() as fixture:
    if cause == 'revoked':
     fixture.transport.revoke()
    else:
     fixture.cancel.set()
    with self.assertRaises(FabError) as caught:
     with fixture.transport.stream(*fixture.expected, Stop(fixture.cancel)):
      self.fail('Ineligible transport yielded a selected transfer')
    self.assertEqual(caught.exception.code, code)
    fixture.connection.listing.assert_not_called()
    fixture.connection.open_selected.assert_not_called()
    fixture.connection.current_account.assert_not_called()
    fixture.click.assert_not_called()
    self.assertEqual(fixture.events, [])

 def test_stream_rejects_unbound_and_malformed_local_choices(self):
  with self.transport_fixture() as fixture:
   item, variant = fixture.expected
   foreign = replace(CHOICE, listing_id='06d847c0-7ab9-435c-8ca6-d18216b01c17').variant()
   malformed = replace(variant, browser_choice=object())
   changed = replace(variant, revision='changed')
   cases = [(item, replace(variant, identity='other-variant')),
            (replace(item, variants=(foreign,)), foreign),
            (replace(item, variants=(malformed,)), malformed),
            (replace(item, variants=(changed,)), changed)]
   for selected_item, selected_variant in cases:
    with self.assertRaises(FabError) as caught:
     with fixture.transport.stream(selected_item, selected_variant, Stop(fixture.cancel)):
      self.fail('Unbound choice yielded a selected transfer')
    self.assertEqual(caught.exception.code, 'EXPIRED')
   fixture.connection.listing.assert_not_called()
   fixture.connection.open_selected.assert_not_called()
   fixture.click.assert_not_called()

if __name__=='__main__':unittest.main()
