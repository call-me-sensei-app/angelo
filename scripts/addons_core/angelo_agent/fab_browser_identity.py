# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned-browser account probe using normal observed Fab/Epic UI, never cookies or a transferred IAB session.

The short-lived inspection target leaves the selected Fab listing/download frame untouched. Account IDs stay
inside the private decoder and become a domain-separated hash; nothing here returns account values to tools.
"""
import hashlib
import json
import time
from dataclasses import dataclass

from .fab_browser_process import BrowserUnavailable
from . import fab_browser_platform as platform_owner
from .fab_browser_account import AccountObservation
from .fab_browser_account_page import SCRIPT, decode_account_page, AccountPageUnavailable

_CREATING_TARGET=object()
FAB='https://www.fab.com/'
ACCOUNT_SETTINGS='https://www.epicgames.com/account'
FAB_MENU_SCRIPT=r"""(() => {
 if(location.origin!=='https://www.fab.com')return {state:'outside_fab'};
 if(!document.body)return {state:'account_control_unavailable'};
 const visible=n=>!!n&&n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden';
 const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT);let n,k=0,buttons=[],links=0;
 while((n=walk.nextNode())){
  if(++k>4000)return {state:'scan_limit'};
  if(n.tagName==='BUTTON'&&n.getAttribute('aria-label')==='Open the account menu'
    &&n.getAttribute('aria-haspopup')==='true'&&visible(n)&&!n.disabled)buttons.push(n);
  if(n.tagName==='A'&&n.getAttribute('role')==='menuitem'&&n.href==='https://www.epicgames.com/account'
    &&n.parentElement&&n.parentElement.tagName==='LI'&&n.parentElement.getAttribute('role')==='none'&&visible(n))links++;
 }
 if(buttons.length!==1||links>1)return {state:'account_control_unavailable'};
 if(links===1)return {state:'account_settings_available'};
 if(buttons[0].getAttribute('aria-expanded')==='false'){buttons[0].click();return {state:'menu_opening'};}
 return {state:'account_settings_unavailable'};
})()"""

def _profile_generation(lease):
    return platform_owner.profile_generation(lease)

@dataclass(frozen=True,repr=False,slots=True)
class _AccountWitness:
    connection: object
    browser: object
    lease: object
    record: AccountObservation

    def valid_for_retention(self,connection):
        browser=getattr(connection,'_browser',None)
        if (connection is not self.connection or browser is not self.browser
                or getattr(browser, '_profile_lease', None) is not self.lease
                or self.lease._browser is not browser or browser._closed
                or getattr(connection, '_user_auth_active', False) or getattr(browser, '_download_uncertain', False)
                or type(self.record) is not AccountObservation):
            return False
        lease, profile, runtime = platform_owner.account_binding(browser)
        return (lease is self.lease and self.record.profile_generation == profile
                and self.record.runtime_generation == runtime)

class _ProbeStop:
    def __init__(self,stop,deadline):self.stop,self.deadline=stop,deadline
    def is_set(self):return (self.stop is not None and self.stop.is_set()) or time.monotonic()>=self.deadline

def _value(browser,session,script,stop,deadline,*,maximum_bytes=8192):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
    reply=browser.call('Runtime.evaluate',{'expression':script,'returnByValue':True,'awaitPromise':False},
                       session=session,stop=stop,seconds=min(2,remaining))
    remote=reply.get('result')
    if 'exceptionDetails' in reply or type(remote) is not dict or type(remote.get('value')) is not dict:
        raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
    value=remote['value']
    try:size=len(json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf8'))
    except (ValueError,UnicodeError):raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')from None
    if size>maximum_bytes:raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
    return value

def _read_current_account(connection,stop,phase):
    """Worker-only actual producer, bounded30s plus independent≤2s target cleanup; no cached active-bit proof."""
    with connection._lock:
        browser=getattr(connection,'_browser',None)
        if browser is None:raise BrowserUnavailable('CLOSED')
        if getattr(connection,'_user_auth_active',False):raise BrowserUnavailable('USER_SIGN_IN_PENDING')
        connection._account_witness=None
        phase[0]='initial_binding'
        lease,profile,runtime=platform_owner.account_binding(browser)
        pending=getattr(connection,'_account_probe_target',None)
        if pending is not None:raise BrowserUnavailable('ACCOUNT_TARGET_CLEANUP_UNPROVED')
        deadline=time.monotonic()+30;bounded=_ProbeStop(connection._combined_stop(stop),deadline)
        target=None;session=None
        # A sent creation may succeed even when cancellation loses its acknowledgment.
        connection._account_probe_target=_CREATING_TARGET
        try:
            phase[0]='create_target'
            created=browser.call('Target.createTarget',{'url':'about:blank','background':True},stop=bounded,seconds=2)
            candidate=created.get('targetId')
            if type(candidate) is not str or not 1<=len(candidate)<=128:raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
            target=candidate
            connection._account_probe_target=target
            phase[0]='attach_target'
            attached=browser.call('Target.attachToTarget',{'targetId':target,'flatten':True},stop=bounded,seconds=2)
            session=attached.get('sessionId')
            if type(session) is not str or not 1<=len(session)<=128:raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
            phase[0]='navigate_fab'
            result=browser.call('Page.navigate',{'url':FAB},session=session,stop=bounded,seconds=min(15,max(.01,deadline-time.monotonic())))
            if result.get('errorText'):raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
            while not bounded.is_set():
                phase[0]='fab_account_menu'
                menu=_value(browser,session,FAB_MENU_SCRIPT,bounded,deadline)
                if menu=={'state':'account_settings_available'}:break
                if menu.get('state') not in ('outside_fab','menu_opening','account_settings_unavailable','account_control_unavailable'):
                    raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
                time.sleep(min(.1,max(0,deadline-time.monotonic())))
            else:raise BrowserUnavailable('AUTH_REQUIRED')
            phase[0]='navigate_account'
            result=browser.call('Page.navigate',{'url':ACCOUNT_SETTINGS},session=session,stop=bounded,seconds=min(15,max(.01,deadline-time.monotonic())))
            if result.get('errorText'):raise BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
            observed=None
            while not bounded.is_set():
                phase[0]='decode_account'
                value=_value(browser,session,SCRIPT,bounded,deadline)
                try:observed=decode_account_page(value);break
                except AccountPageUnavailable as error:
                    if error.code not in ('OUTSIDE_ACCOUNT_PAGE','ACCOUNT_PAGE_NOT_READY'):
                        refused=BrowserUnavailable('ACCOUNT_PROOF_UNAVAILABLE')
                        refused.account_page_reason=error.code
                        from .fab_browser_failure import failure_details
                        refused.producer_diagnostic=failure_details(error,'decode_account')
                        raise refused from None
                time.sleep(min(.1,max(0,deadline-time.monotonic())))
            if observed is None:raise BrowserUnavailable('AUTH_REQUIRED')
            phase[0]='binding_before_close'
            current_lease,current_profile,current_runtime=platform_owner.account_binding(browser)
            if (current_lease is not lease or current_profile!=profile or current_runtime!=runtime
                    or browser is not connection._browser or browser._closed or bounded.is_set()):
                raise BrowserUnavailable('AUTH_REQUIRED')
            record=AccountObservation(observed.opaque_account,profile,runtime)
        finally:
            if target is None:
                # No returned ID is not evidence of no created target. Only whole-browser drain clears this.
                phase[0]='close_target'
                error=BrowserUnavailable('ACCOUNT_TARGET_CLEANUP_UNPROVED');error.owner=connection
                raise error from None
            if target is not None:
                try:
                    closed=browser.call('Target.closeTarget',{'targetId':target},seconds=2)
                    if closed.get('success') is not True:raise BrowserUnavailable('ACCOUNT_TARGET_CLEANUP_UNPROVED')
                    if connection._account_probe_target!=target:
                        raise BrowserUnavailable('ACCOUNT_TARGET_CLEANUP_UNPROVED')
                    connection._account_probe_target=None
                except BaseException:
                    # The parent still owns the exact browser/profile. No account record escapes failed cleanup.
                    phase[0]='close_target'
                    error=BrowserUnavailable('ACCOUNT_TARGET_CLEANUP_UNPROVED');error.owner=connection
                    raise error from None

        # Closing the owned inspection target is another browser call. Recheck after that final live operation.
        phase[0]='binding_after_close'
        current_lease,current_profile,current_runtime=platform_owner.account_binding(browser)
        if (current_lease is not lease or current_profile!=profile or current_runtime!=runtime
                or browser is not connection._browser or browser._closed or bounded.is_set()):
            raise BrowserUnavailable('AUTH_REQUIRED')
        connection._account_witness=_AccountWitness(connection,browser,lease,record)
        return record


def read_current_account(connection,stop=None):
    """Preserve closed producer failure facts before the transport removes exception chaining."""
    from .fab_browser_failure import mapped_failure_details, failure_details
    phase=['initial_binding']
    try:
        return _read_current_account(connection,stop,phase)
    except Exception as error:
        if hasattr(error,'producer_diagnostic'):
            error.producer_diagnostic=mapped_failure_details(error)
        else:
            error.producer_diagnostic=failure_details(error,phase[0])
        raise
