# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual account producer/transport paths; only browser and native profile boundaries modeled."""
import suite_isolation  # noqa: F401
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'scripts/addons_core'))
from angelo_agent import fab_browser_identity as identity, fab_browser_transport as transport
from angelo_agent.fab_browser_failure import mapped_failure_details
from angelo_agent.fab_browser_process import BrowserUnavailable
from angelo_agent.fab_catalog import FabError


class Stop:
    def check(self):
        pass
    def is_set(self):
        return False


class AccountFailureTests(unittest.TestCase):
    def connection(self, *, account_id='1'*32, close_error=None):
        calls=[]
        def call(method, params, **kwargs):
            calls.append(method)
            if method=='Target.createTarget':return {'targetId':'target'}
            if method=='Target.attachToTarget':return {'sessionId':'session'}
            if method=='Page.navigate':return {}
            if method=='Target.closeTarget':
                if close_error:raise close_error
                return {'success':True}
            if method=='Runtime.evaluate':
                if params['expression']==identity.FAB_MENU_SCRIPT:
                    value={'state':'account_settings_available'}
                else:
                    value={'state':'account_page','origin':'https://accounts.epicgames.com',
                           'path':'/account/personal','view_id':'personal-view',
                           'heading_id':'account-info-title','heading':'Account information','scanned':4,
                           'candidates':[{'label':'ID:','sibling_tag':'P','value':account_id}]}
                return {'result':{'value':value}}
            raise AssertionError('unexpected modeled browser operation')
        browser=SimpleNamespace(call=call,_closed=False)
        c=SimpleNamespace(_lock=threading.RLock(),_browser=browser,_user_auth_active=False,
                          _account_probe_target=None,_account_witness=None,_combined_stop=lambda stop:stop)
        c.current_account=lambda stop:identity.read_current_account(c,stop)
        return c,calls
    def binding(self):
        return patch.object(identity.platform_owner,'account_binding',return_value=(object(),'a'*64,'b'*32))
    def test_auth_required_mapping_is_truthful(self):
        error=transport._browser_error(BrowserUnavailable('AUTH_REQUIRED'))
        self.assertEqual(error.code,'AUTH_REQUIRED')
        self.assertEqual(error.producer_diagnostic['reason'],'AUTH_REQUIRED')
    def test_decoder_failure_keeps_closed_reason_and_cleans_real_probe(self):
        c,calls=self.connection(account_id='SECRET_ACCOUNT_NOT_AN_ID')
        with self.binding():
            with self.assertRaises(FabError) as caught:transport.BrowserTransport.observe(c,Stop())
        d=caught.exception.producer_diagnostic
        self.assertEqual((d['phase'],d['reason']),('decode_account','ACCOUNT_ID_INVALID'))
        self.assertEqual(d['type'],'AccountPageUnavailable')
        self.assertTrue(any(row['module']=='fab_browser_account_page' for row in d['trace']))
        self.assertNotIn('SECRET',json.dumps(d));self.assertEqual(calls.count('Target.closeTarget'),1)
        self.assertIsNone(c._account_probe_target);self.assertIsNone(c._account_witness)
    def test_initial_binding_python_failure_has_closed_location_without_text(self):
        c,calls=self.connection()
        with patch.object(identity.platform_owner,'account_binding',side_effect=AttributeError('SECRET_TOKEN_URL')):
            with self.assertRaises(FabError) as caught:transport.BrowserTransport.observe(c,Stop())
        d=caught.exception.producer_diagnostic
        self.assertEqual((d['phase'],d['type']),('initial_binding','AttributeError'))
        self.assertTrue(any(row['module']=='fab_browser_identity' for row in d['trace']))
        self.assertNotIn('SECRET',json.dumps(d));self.assertEqual(calls,[])
    def test_cleanup_failure_overrides_account_result_without_witness(self):
        c,calls=self.connection(close_error=BrowserUnavailable('TIMEOUT'))
        with self.binding():
            with self.assertRaises(FabError) as caught:transport.BrowserTransport.observe(c,Stop())
        d=caught.exception.producer_diagnostic
        self.assertEqual((d['phase'],d['reason']),('close_target','ACCOUNT_TARGET_CLEANUP_UNPROVED'))
        self.assertEqual(c._account_probe_target,'target');self.assertIsNone(c._account_witness)
    def test_success_still_requires_actual_decoder_and_cleanup(self):
        c,calls=self.connection()
        with self.binding():record=identity.read_current_account(c,Stop())
        self.assertEqual(len(record.opaque_account),64);self.assertIsNotNone(c._account_witness)
        self.assertEqual(calls.count('Target.closeTarget'),1);self.assertIsNone(c._account_probe_target)
    def test_transport_unexpected_exception_is_closed_and_not_auth_success(self):
        owner=SimpleNamespace(current_account=lambda stop:(_ for _ in ()).throw(KeyError('SECRET_ACCOUNT')))
        with self.assertRaises(FabError) as caught:transport.BrowserTransport.observe(owner,Stop())
        self.assertEqual(caught.exception.code,'UNAVAILABLE')
        self.assertEqual(caught.exception.producer_diagnostic['type'],'KeyError')
        self.assertNotIn('SECRET',json.dumps(caught.exception.producer_diagnostic))
    def test_forged_diagnostic_and_unhashable_code_cannot_leak_or_mask_failure(self):
        error=RuntimeError('SECRET');error.code=[]
        error.producer_diagnostic={'schema':1,'phase':[],'type':'SECRET','reason':'SECRET','trace':[]}
        result=mapped_failure_details(error)
        self.assertEqual(result['reason'],'OTHER');self.assertNotIn('SECRET',json.dumps(result))
    def test_expired_account_menu_remains_auth_required_not_connected(self):
        c,calls=self.connection()
        with self.binding(),patch.object(identity,'_ProbeStop',return_value=SimpleNamespace(is_set=lambda:True)):
            with self.assertRaises(FabError) as caught:transport.BrowserTransport.observe(c,Stop())
        self.assertEqual(caught.exception.code,'AUTH_REQUIRED')
        self.assertIsNone(c._account_witness);self.assertEqual(calls.count('Target.closeTarget'),1)


if __name__=='__main__':unittest.main()
