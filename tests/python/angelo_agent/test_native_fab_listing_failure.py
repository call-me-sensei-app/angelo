# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual listing owner/decoder/transport paths, browser native protocol boundary modeled only."""
import suite_isolation  # noqa: F401
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent import fab_browser_listing_owner as owner, fab_browser_listing_page as page
from angelo_agent.fab_browser_transport import BrowserTransport
from angelo_agent.fab_browser_account import AccountObservation
from angelo_agent.fab_browser_process import BrowserUnavailable
from angelo_agent.fab_catalog import FabError

ID='12345678-1234-1234-1234-123456789abc'


class Stop:
    def check(self):pass
    def is_set(self):return False


def observed():
    return {'state':'observed','listing_id':ID,'title':'Synthetic Rug','author':'Synthetic Publisher',
            'license_label':'Standard License','library_state_label':'Saved in My Library',
            'rows':[{'ordinal':0,'filename':'synthetic_low.glb','group':'Converted Files','format':'glb','quality_label':'Low quality',
                     'quality_aria':'This product is of _low. quality','display_size':'3.89 MB',
                     'button_type':'button','button_aria':page.BUTTON_ARIA,'button_disabled':False}]}


def license_pending():
    return {'state':'not_ready','reason':'LISTING_LICENSE_MISSING','ready':'complete','dialogs':1,'rows':1}


class ListingFailureTests(unittest.TestCase):
    def connection(self, *, snapshot=None, snapshots=None, opening='download_dialog_opening', navigation=None, changed=False):
        account=AccountObservation('a'*64,'b'*64,'c'*32);calls=[]
        def current(stop):
            c.account_checks+=1
            if changed and c.account_checks>2:return AccountObservation('d'*64,'b'*64,'c'*32)
            return account
        def call(method,params,**kwargs):
            calls.append(method)
            if method=='Page.navigate':return navigation or {'frameId':'frame','loaderId':'loader'}
            if method=='Page.getFrameTree':return {'frameTree':{'frame':{'id':'frame','loaderId':c.loader}}}
            if method=='Page.bringToFront':return {}
            if method=='Runtime.evaluate':
                if 'document_ready' in params['expression']:
                    value={'state':'document_ready','ready':'complete','visibility':'visible'}
                elif 'download_dialog_opening' in params['expression']:
                    c.open_calls+=1
                    value={'state':opening}
                else:
                    value=(snapshots[min(c._fixture_decodes,len(snapshots)-1)] if snapshots is not None
                           else snapshot if snapshot is not None else observed())
                    c._fixture_decodes+=1
                return {'result':{'value':value}}
            raise KeyError('SECRET_UNEXPECTED_PROTOCOL')
        c=SimpleNamespace(_lock=threading.RLock(),_browser=SimpleNamespace(call=call),_session='session',
                          _user_auth_active=False,_selected_transfer=None,_combined_stop=lambda stop:stop,
                          current_account=current,loader='loader',account_checks=0,open_calls=0)
        c._fixture_decodes=0;c._fixture_limit=len(snapshots)+1 if snapshots is not None else 2
        c.listing=lambda identity,stop:owner.listing(c,identity,stop)
        return c,account,calls
    def lookup(self, **kwargs):
        c,account,calls=self.connection(**kwargs)
        bounded=SimpleNamespace(is_set=lambda:c._fixture_decodes>=c._fixture_limit)
        with patch.object(owner,'_ProbeStop',return_value=bounded),patch.object(owner.time,'sleep',return_value=None):
            return BrowserTransport(c,account)._listing(ID,Stop())

    @contextmanager
    def clock(self, after_sleep=None):
        clock=SimpleNamespace(now=0.0)
        def sleep(seconds):
            clock.now+=seconds
            if after_sleep is not None:after_sleep()
        with patch.object(owner.time,'monotonic',lambda:clock.now),patch.object(owner.time,'sleep',sleep):
            yield clock
    def test_actual_decoder_reason_and_location_survive_transport(self):
        with self.assertRaises(FabError) as caught:self.lookup(snapshot={'state':'site_changed','reason':'LISTING_TITLE_SCOPE'})
        d=caught.exception.producer_diagnostic
        self.assertEqual((d['phase'],d['type'],d['reason']),('listing_decode','ListingObservationUnavailable','LISTING_TITLE_SCOPE'))
        self.assertTrue(any(row['module']=='fab_browser_listing_page' for row in d['trace']))
        self.assertTrue(any(row['module']=='fab_browser_listing_owner' for row in d['trace']))
    def test_success_preserves_owned_metadata_and_choice(self):
        value,item=self.lookup()
        self.assertEqual(value.identity,ID)
        self.assertEqual(item.title,'Synthetic Rug')
        self.assertEqual(value.choices[0].filename,'synthetic_low.glb')
    def test_navigation_failure_records_closed_phase_not_error_text(self):
        with self.assertRaises(FabError) as caught:self.lookup(navigation={'errorText':'SECRET_URL_TOKEN'})
        d=caught.exception.producer_diagnostic
        self.assertEqual((d['phase'],d['reason']),('listing_navigate','NAVIGATION_FAILED'))
        self.assertNotIn('SECRET',json.dumps(d))
    def test_open_scan_limit_is_distinct_without_dom_values(self):
        with self.assertRaises(FabError) as caught:self.lookup(opening='scan_limit')
        d=caught.exception.producer_diagnostic
        self.assertEqual((d['phase'],d['reason']),('listing_open_download','LISTING_SCAN_LIMIT'))
    def test_second_immediate_listing_waits_for_fresh_license_without_reopening(self):
        missing_key=observed();del missing_key['license_label']
        null_label=dict(observed(),license_label=None)
        modal_pending={'state':'not_ready','reason':'LISTING_DIALOG_MISSING','ready':'complete','dialogs':0,'rows':0}
        c,account,calls=self.connection(snapshots=[
            observed(),modal_pending,missing_key,license_pending(),null_label,observed()])
        transport=BrowserTransport(c,account)
        with self.clock():
            first=transport._listing(ID,Stop())
            second=transport._listing(ID,Stop())
        self.assertEqual(first,second)
        self.assertEqual(c._fixture_decodes,6)
        self.assertEqual(c.open_calls,2)
        self.assertEqual(calls.count('Page.navigate'),2)
        self.assertEqual(calls.count('Page.bringToFront'),0)   # The tab renders behind Angelo: never brought forward.
        self.assertEqual(calls.count('Page.getFrameTree'),6)
        self.assertEqual(c.account_checks,8)

    def test_permanent_missing_license_expires_at_original_bound(self):
        for snapshot in (license_pending(),dict(observed(),license_label=None)):
            with self.subTest(snapshot=snapshot['state']):
                c,_,calls=self.connection(snapshot=snapshot)
                with self.clock() as clock,self.assertRaises(BrowserUnavailable) as caught:
                    owner.observe_listing(c,ID,Stop())
                diagnostic=caught.exception.producer_diagnostic
                self.assertEqual((caught.exception.code,diagnostic['phase'],diagnostic['reason']),
                                 ('LISTING_PROOF_UNAVAILABLE','listing_decode','LISTING_LICENSE_MISSING'))
                if snapshot['state']=='not_ready':
                    self.assertTrue(any(row['branch']=='license_missing' for row in diagnostic['listing']['trace']))
                self.assertEqual(clock.now,25)
                self.assertGreater(c._fixture_decodes,1)
                self.assertEqual(c.open_calls,1)
                self.assertEqual(calls.count('Page.navigate'),1)
                self.assertEqual(c.account_checks,1)

    def test_explicit_wrong_or_ambiguous_license_refuses_before_more_reads(self):
        for snapshot,reason in ((dict(observed(),license_label='SECRET_WRONG_LICENSE'),'LISTING_LICENSE_INVALID'),
                                ({'state':'site_changed','reason':'LISTING_LICENSE_AMBIGUOUS'},'LISTING_LICENSE_AMBIGUOUS')):
            with self.subTest(reason=reason):
                c,account,_=self.connection(snapshots=[snapshot,observed()])
                with self.clock(),self.assertRaises(FabError) as caught:
                    BrowserTransport(c,account)._listing(ID,Stop())
                diagnostic=caught.exception.producer_diagnostic
                self.assertEqual((diagnostic['phase'],diagnostic['reason']),('listing_decode',reason))
                self.assertNotIn('SECRET',json.dumps(diagnostic))
                self.assertEqual(c._fixture_decodes,1)
                self.assertEqual(c.open_calls,1)
                self.assertEqual(c.account_checks,2)

    def test_browser_replacement_stops_license_wait(self):
        c,_,_=self.connection(snapshots=[license_pending(),observed()])
        with self.clock(lambda:setattr(c,'_browser',object())),self.assertRaises(BrowserUnavailable):
            owner.observe_listing(c,ID,Stop())
        self.assertEqual(c._fixture_decodes,1)
        self.assertEqual(c.account_checks,1)

    def test_document_replacement_refuses_later_license(self):
        c,_,calls=self.connection(snapshots=[license_pending(),observed()])
        with self.clock(lambda:setattr(c,'loader','reloaded')),self.assertRaises(BrowserUnavailable) as caught:
            owner.observe_listing(c,ID,Stop())
        self.assertEqual(caught.exception.producer_diagnostic['reason'],'LISTING_DOCUMENT_UNVERIFIED')
        self.assertEqual(calls.count('Page.getFrameTree'),3)
        self.assertEqual(c.open_calls,1)
        self.assertEqual(c.account_checks,1)

    def test_stop_during_license_wait_prevents_further_observation(self):
        c,_,_=self.connection(snapshots=[license_pending(),observed()])
        stop=threading.Event()
        with self.clock(stop.set),self.assertRaises(BrowserUnavailable):
            owner.observe_listing(c,ID,stop)
        self.assertEqual(c._fixture_decodes,1)
        self.assertEqual(c.open_calls,1)
        self.assertEqual(c.account_checks,1)
    def test_unknown_closed_reason_is_rejected_without_serializing_it(self):
        with self.assertRaises(FabError) as caught:self.lookup(snapshot={'state':'site_changed','reason':'SECRET_DOM_ACCOUNT'})
        d=caught.exception.producer_diagnostic
        self.assertEqual(d['reason'],'OBSERVATION_SHAPE');self.assertNotIn('SECRET',json.dumps(d))
    def test_legacy_failure_shape_keeps_existing_code(self):
        with self.assertRaises(page.ListingObservationUnavailable) as caught:
            page.parse_listing_observation({'state':'site_changed'},expected_listing_id=ID)
        self.assertEqual(caught.exception.code,'SITE_CHANGED');self.assertEqual(str(caught.exception),'SITE_CHANGED')
    def test_account_change_still_refuses_after_decode(self):
        with self.assertRaises(FabError) as caught:self.lookup(changed=True,snapshots=[license_pending(),observed()])
        self.assertEqual(caught.exception.code,'AUTH_REQUIRED')
        self.assertEqual(caught.exception.producer_diagnostic['phase'],'listing_account_after')



    def relation(self, **changes):
        value={'kind':'modal','attribute_present':True,'id_count':1,'resolved_count':1,'unique_count':1,
               'unambiguous_id_count':1,'inside_owner_count':0,'visible_count':1,'tag':'H2',
               'expected_label_match':True,'audit_complete':True}
        value.update(changes);return value
    def label_failure(self, **changes):
        return {'state':'site_changed','reason':'LISTING_LABEL_RELATION','relation':self.relation(**changes)}
    def test_transient_label_failure_then_strict_snapshot_succeeds_without_action_retry(self):
        from unittest.mock import patch
        c,account,calls=self.connection(snapshots=[self.label_failure(),observed()])
        bounded=SimpleNamespace(is_set=lambda:c._fixture_decodes>=3)
        with patch.object(owner,'_ProbeStop',return_value=bounded),patch.object(owner.time,'sleep',return_value=None):
            value,item=BrowserTransport(c,account)._listing(ID,Stop())
        self.assertEqual(value.identity,ID);self.assertEqual(item.title,'Synthetic Rug')
        self.assertEqual(c._fixture_decodes,2)
        self.assertEqual(calls.count('Page.navigate'),1);self.assertEqual(calls.count('Page.bringToFront'),0)
        # One readiness, one opener and two read-only decoder observations; no second opener.
        self.assertEqual(calls.count('Runtime.evaluate'),4)
    def test_permanent_region_relation_refuses_with_exact_last_facts(self):
        value=self.label_failure(kind='region',tag='BUTTON',visible_count=0)
        with self.assertRaises(FabError) as caught:self.lookup(snapshot=value)
        d=caught.exception.producer_diagnostic
        self.assertEqual((d['type'],d['reason']),('ListingObservationUnavailable','LISTING_LABEL_RELATION'))
        self.assertEqual(d['relation'],value['relation']);self.assertEqual(d['relation']['kind'],'region')
    def test_later_decoder_failure_remains_the_terminal_reason(self):
        with self.assertRaises(FabError) as caught:self.lookup(snapshots=[self.label_failure(),{'state':'site_changed','reason':'LISTING_ROW_FILE_SIZE'}])
        self.assertEqual(caught.exception.producer_diagnostic['reason'],'LISTING_ROW_FILE_SIZE')
        self.assertNotIn('relation',caught.exception.producer_diagnostic)
    def test_relation_audit_rejects_raw_or_unbounded_metadata(self):
        for changes in ({'id_count':3},{'visible_count':True},{'tag':'SECRET_DOM'},
                        {'loader_id':'SECRET_ID'},{'expected_label_match':'SECRET_TEXT'}):
            value=self.label_failure();value['relation'].update(changes)
            with self.subTest(changes=changes),self.assertRaises(FabError) as caught:self.lookup(snapshot=value)
            d=caught.exception.producer_diagnostic
            self.assertEqual(d['reason'],'OBSERVATION_SHAPE');self.assertNotIn('SECRET',json.dumps(d))
    def test_original_observer_network_failure_is_not_polled(self):
        from angelo_agent.fab_browser_process import BrowserUnavailable
        c,account,calls=self.connection()
        original=c._browser.call
        def call(method,params,**kwargs):
            if method=='Runtime.evaluate' and 'document_ready'not in params['expression'] and 'download_dialog_opening'not in params['expression']:
                raise BrowserUnavailable('TIMEOUT')
            return original(method,params,**kwargs)
        c._browser.call=call
        with self.assertRaises(FabError) as caught:BrowserTransport(c,account)._listing(ID,Stop())
        self.assertEqual(caught.exception.producer_diagnostic['reason'],'TIMEOUT')
        self.assertEqual(c._fixture_decodes,0)


class ListingReadinessTests(unittest.TestCase):
    def waiting(self, reason='LISTING_DIALOG_MISSING', **changes):
        return dict({'state':'not_ready','reason':reason,'ready':'complete','dialogs':0,'rows':0},**changes)
    def run_missing(self, sequence, *, probe_fails=False, document_old=False, readiness=None):
        from unittest.mock import patch
        from angelo_agent.fab_browser_process import BrowserUnavailable
        account=AccountObservation('a'*64,'b'*64,'c'*32)
        progress={'decode':0,'open':0,'probes':0,'ready':0,'activate':0,'events':[]}
        def call(method,params,**kwargs):
            if method=='Page.navigate':return {'frameId':'SECRET_FRAME','loaderId':'SECRET_NEW_LOADER'}
            if method=='Page.getFrameTree':
                progress['probes']+=1
                if probe_fails:raise KeyError('SECRET_PROTOCOL_DETAIL')
                return {'frameTree':{'frame':{'id':'SECRET_FRAME','loaderId':
                    'SECRET_OLD_LOADER' if document_old or progress['probes']==1 else 'SECRET_NEW_LOADER'}}}
            if method=='Page.bringToFront':
                progress['activate']+=1;progress['events'].append('activate');return {}
            if method=='Runtime.evaluate':
                if 'document_ready' in params['expression']:
                    states=readiness or ['complete'];ready=states[min(progress['ready'],len(states)-1)]
                    progress['ready']+=1;progress['events'].append('ready_'+ready)
                    value={'state':'document_ready' if ready=='complete' else 'document_loading','ready':ready,'visibility':'visible'}
                elif 'download_dialog_opening' in params['expression']:
                    progress['open']+=1;progress['events'].append('open');value={'state':'dialog_present','ready':'complete'}
                else:
                    value=sequence[progress['decode']];progress['decode']+=1
                return {'result':{'value':value}}
            raise AssertionError('unexpected modeled operation')
        c=SimpleNamespace(_lock=threading.RLock(),_browser=SimpleNamespace(call=call),_session='session',
                          _user_auth_active=False,_selected_transfer=None,_combined_stop=lambda stop:stop,
                          current_account=lambda stop:account)
        bounded=SimpleNamespace(is_set=lambda:progress['decode']>=len(sequence))
        with patch.object(owner,'_ProbeStop',return_value=bounded),patch.object(owner.time,'sleep',return_value=None):
            with self.assertRaises(BrowserUnavailable) as caught:owner.observe_listing(c,ID,Stop())
        return caught.exception,progress
    def test_not_ready_branches_are_closed_distinct_codes(self):
        for reason in page.NOT_READY_REASONS:
            with self.subTest(reason=reason),self.assertRaises(page.ListingObservationUnavailable) as caught:
                page.parse_listing_observation(self.waiting(reason),expected_listing_id=ID)
            self.assertEqual(caught.exception.code,reason)
    def test_open_waits_for_current_document_without_reclick(self):
        error,progress=self.run_missing([self.waiting(),self.waiting()])
        self.assertEqual(error.code,'LISTING_PROOF_UNAVAILABLE')
        d=error.producer_diagnostic;self.assertEqual(d['reason'],'LISTING_DIALOG_MISSING')
        trace=d['listing']['trace']
        self.assertTrue(any(r['point']=='after_navigation' and r['document']=='OLD' for r in trace))
        self.assertTrue(any(r['point']=='open' and r['document']=='MATCH' and r['branch']=='dialog_present' for r in trace))
        self.assertTrue(any(r['point']=='decode' and r['document']=='MATCH' and r['branch']=='dialog_missing' for r in trace))
        self.assertEqual(progress['open'],1);self.assertEqual(progress['probes'],3)
        self.assertEqual(progress['activate'],0)
        self.assertNotIn('SECRET',json.dumps(d))
    def test_navigation_probes_cap_at_three_on_repeated_branch_transitions(self):
        sequence=[self.waiting(),self.waiting('LISTING_ROWS_MISSING',dialogs=1),self.waiting(),
                  self.waiting('LISTING_GLB_MISSING',dialogs=1,rows=12),self.waiting(),self.waiting()]
        error,progress=self.run_missing(sequence)
        self.assertEqual(progress['probes'],3)
        self.assertEqual(error.producer_diagnostic['listing']['navigation_probes'],3)
        self.assertLessEqual(len(error.producer_diagnostic['listing']['trace']),8)
    def test_probe_failure_does_not_replace_original_failure_or_invent_match(self):
        error,progress=self.run_missing([self.waiting(),self.waiting()],probe_fails=True)
        self.assertEqual(error.code,'LISTING_PROOF_UNAVAILABLE')
        self.assertEqual(error.producer_diagnostic['reason'],'LISTING_DOCUMENT_UNVERIFIED')
        self.assertEqual(progress['open'],0);self.assertEqual(progress['activate'],0)
        self.assertTrue(all(r['document']=='UNOBSERVED' for r in error.producer_diagnostic['listing']['trace']))
        self.assertNotIn('SECRET',json.dumps(error.producer_diagnostic))
    def test_waiting_counts_and_enum_cannot_carry_observed_strings(self):
        for changes in ({'dialogs':3},{'rows':25},{'ready':'SECRET_DOM'},{'reason':'SECRET_URL'},{'rows':True},
                        {'reason':'LISTING_LICENSE_MISSING','license_label':'SECRET_DOM'},
                        {'render':{'visibility':'SECRET_DOM','raw_dialogs':0,'hidden_dialogs':0,'opacity_zero_dialogs':0}},
                        {'render':{'visibility':'hidden','raw_dialogs':3,'hidden_dialogs':0,'opacity_zero_dialogs':0}}):
            with self.subTest(changes=changes),self.assertRaises(page.ListingObservationUnavailable) as caught:
                page.parse_listing_observation(self.waiting(**changes),expected_listing_id=ID)
            self.assertEqual(caught.exception.code,'OBSERVATION_SHAPE')
    def test_diagnostic_copy_strips_unapproved_nested_fields(self):
        from angelo_agent.fab_browser_failure import mapped_failure_details
        error=RuntimeError('SECRET_EXCEPTION')
        error.listing_diagnostic={'open_result':'dialog_present','navigation_probes':1,'trace':[],
                                  'loader_id':'SECRET_LOADER'}
        d=mapped_failure_details(error,phase='listing_decode')
        self.assertNotIn('listing',d);self.assertNotIn('SECRET',json.dumps(d))

    def test_interactive_document_never_opens_before_complete_and_activation(self):
        error,progress=self.run_missing([self.waiting(),self.waiting()],readiness=['interactive','interactive','complete'])
        self.assertEqual(error.code,'LISTING_PROOF_UNAVAILABLE')
        self.assertEqual(progress['ready'],3);self.assertEqual(progress['activate'],0);self.assertEqual(progress['open'],1)
        self.assertEqual(progress['events'][:4],['ready_interactive','ready_interactive','ready_complete','open'])
    def test_old_generation_refuses_without_activation_or_opener(self):
        error,progress=self.run_missing([self.waiting()],document_old=True)
        self.assertEqual(error.producer_diagnostic['reason'],'LISTING_DOCUMENT_UNVERIFIED')
        self.assertEqual(progress['probes'],3);self.assertEqual(progress['open'],0);self.assertEqual(progress['activate'],0)
    def test_hidden_transparent_modal_facts_survive_without_new_click(self):
        value=self.waiting(render={'visibility':'hidden','raw_dialogs':1,'hidden_dialogs':0,'opacity_zero_dialogs':1})
        error,progress=self.run_missing([value,value])
        records=[row for row in error.producer_diagnostic['listing']['trace'] if row['point']=='decode']
        self.assertTrue(records)
        self.assertEqual(records[-1]['dialogs'],0)
        self.assertEqual(records[-1]['render'],value['render'])
        self.assertEqual(progress['open'],1);self.assertEqual(progress['activate'],0)


if __name__=='__main__':unittest.main()
