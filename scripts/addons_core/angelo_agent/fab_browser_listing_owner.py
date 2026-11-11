# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Normal visible Fab listing navigation; observed controls never become a remote API or terms certificate."""
import re
import time
from .fab_browser_process import BrowserUnavailable
from .fab_browser_identity import _ProbeStop,_value
from .fab_browser_listing_page import OBSERVE_LISTING_JS,parse_listing_observation,ListingObservationUnavailable

_UUID=re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_DOWNLOAD_READINESS=r"""(() => {
 const ready=['loading','interactive','complete'].includes(document.readyState)?document.readyState:'other';
 const visibility=['visible','hidden','prerender'].includes(document.visibilityState)?document.visibilityState:'other';
 const state=location.origin!=='https://www.fab.com'||location.pathname!==EXPECTED_PATH?'outside_listing':
  !document.body?'not_ready':ready==='complete'?'document_ready':'document_loading';
 return {state,ready,visibility};
})()"""
_OPEN_DOWNLOAD=r"""(() => {
 const result=state=>({state,ready:['loading','interactive','complete'].includes(document.readyState)?document.readyState:'other'});
 if(location.origin!=='https://www.fab.com'||location.pathname!==EXPECTED_PATH)return result('outside_listing');
 if(!document.body)return result('not_ready');
 if(document.readyState!=='complete')return result('document_loading');
 const visible=n=>{if(!n.getClientRects().length)return false;let d=0;
  for(let p=n;p;p=p.parentElement){if(++d>64)return false;const s=getComputedStyle(p);
   if(s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}return true;};
 const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT);let n,k=0,buttons=[],dialogs=0,buy=0;
 while((n=walk.nextNode())){
  if(++k>4000)return result('scan_limit');
  if(n.tagName==='BUTTON'&&visible(n)&&/^(Add to cart|Buy now)$/.test(n.textContent.trim()))buy++;
  if(n.tagName==='DIV'&&n.getAttribute('role')==='dialog'&&n.getAttribute('aria-modal')==='true'&&visible(n))dialogs++;
  if(n.tagName==='BUTTON'&&n.getAttribute('type')==='button'&&!n.getAttribute('aria-label')&&visible(n)
   &&n.textContent.length<=240&&n.textContent.trim()==='Download'&&!n.disabled
   &&n.getAttribute('aria-disabled')!=='true')buttons.push(n);
 }
 if(dialogs===1)return result('dialog_present');
 if(dialogs===0&&buttons.length===0&&buy>0)return result('purchase_required');   // A paid listing: no Download yet.
 if(dialogs!==0||buttons.length!==1)return result('download_control_unavailable');
 buttons[0].click();return result('download_dialog_opening');
})()"""

def _observe_listing(connection,item_id,stop,phase,diagnostic):
 """Bounded worker-only actual account + one-item modal observations. No acquisition or agreement action.

 The separate account target preserves this main frame. Every failure leaves the existing browser owner
 responsible for its target/profile; normal close is unchanged. Other operations refuse while a download
 lease occupies this frame. Rounded sizes stay text; callers cannot turn them into exact byte counts.
 """
 if type(item_id)is not str or not _UUID.fullmatch(item_id):raise BrowserUnavailable('LISTING_ID_INVALID')
 with connection._lock:
  browser=getattr(connection,'_browser',None)
  if browser is None:raise BrowserUnavailable('CLOSED')
  if getattr(connection,'_user_auth_active',False):raise BrowserUnavailable('USER_SIGN_IN_PENDING')
  if getattr(connection,'_selected_transfer',None)is not None:raise BrowserUnavailable('BUSY')
  phase[0]='listing_account_before'
  account=connection.current_account(stop)
  deadline=time.monotonic()+25;bounded=_ProbeStop(connection._combined_stop(stop),deadline)
  path='/listings/'+item_id
  phase[0]='listing_navigate'
  result=browser.call('Page.navigate',{'url':'https://www.fab.com'+path},session=connection._session,
                      stop=bounded,seconds=15)
  if result.get('errorText'):raise BrowserUnavailable('NAVIGATION_FAILED')
  # Loader identities are private invocation-local comparison values, not diagnostic output or authority.
  navigation_frame=result.get('frameId');navigation_loader=result.get('loaderId')
  document_state='UNOBSERVED'
  def inspect_document(*, after_license_wait=False):
   nonlocal document_state
   document_state='UNOBSERVED'
   # Reserve one proof read for a late license, even if modal diagnostics used all three probes.
   limit=4 if after_license_wait else 3
   if diagnostic['navigation_probes']>=limit or bounded.is_set() or browser is not connection._browser:return
   diagnostic['navigation_probes']+=1
   try:
    remaining=deadline-time.monotonic()
    if remaining<=0:return
    tree=browser.call('Page.getFrameTree',{},session=connection._session,stop=bounded,seconds=min(1,remaining))
    frame=tree.get('frameTree',{}).get('frame',{})
    current_frame=frame.get('id');current_loader=frame.get('loaderId')
    if not all(type(v)is str and 0<len(v)<=128 for v in (navigation_frame,navigation_loader,current_frame,current_loader)):return
    document_state='FRAME_CHANGED' if current_frame!=navigation_frame else 'MATCH' if current_loader==navigation_loader else 'OLD'
   except Exception:
    # Read-only diagnostics must neither mask the producer failure nor invent a generation match.
    document_state='UNOBSERVED'
  def trace(point,branch,ready='UNOBSERVED',dialogs=0,rows=0,render=None):
   record={'point':point,'branch':branch,'ready':ready,'document':document_state,'dialogs':dialogs,'rows':rows,
           'render':render or {'visibility':'UNOBSERVED','raw_dialogs':0,'hidden_dialogs':0,'opacity_zero_dialogs':0}}
   if not diagnostic['trace'] or diagnostic['trace'][-1]!=record:
    diagnostic['trace'].append(record);del diagnostic['trace'][:-8]
  inspect_document();trace('after_navigation','not_observed')
  last_decode=None
  # Opening is idempotent: once any modal appears, no repeated click or acquisition action occurs.
  opened=False;last_reason='LISTING_NOT_READY';last_decoder=None
  waited_for_license=False
  while not bounded.is_set():
   if browser is not connection._browser:raise BrowserUnavailable('LISTING_PROOF_UNAVAILABLE')
   if not opened:
    phase[0]='listing_readiness'
    import json
    ready=_value(browser,connection._session,_DOWNLOAD_READINESS.replace('EXPECTED_PATH',json.dumps(path)),bounded,deadline)
    ready_state=ready.get('state')
    if ready_state not in ('outside_listing','not_ready','document_loading','document_ready'):
     raise BrowserUnavailable('LISTING_PROOF_UNAVAILABLE')
    visibility=ready.get('visibility')
    trace('readiness',ready_state,ready.get('ready','UNOBSERVED'),render={
     'visibility':visibility if visibility in ('visible','hidden','prerender','other') else 'UNOBSERVED',
     'raw_dialogs':0,'hidden_dialogs':0,'opacity_zero_dialogs':0})
    if ready_state!='document_ready':
     last_reason={'outside_listing':'LISTING_OUTSIDE','not_ready':'LISTING_BODY_MISSING',
                  'document_loading':'LISTING_DOCUMENT_LOADING'}[ready_state]
     time.sleep(min(.1,max(0,deadline-time.monotonic())))
     continue
    if not diagnostic['target_activated'] or document_state!='MATCH':
     inspect_document()
     if document_state!='MATCH':
      last_reason='LISTING_DOCUMENT_UNVERIFIED'
      if diagnostic['navigation_probes']>=3:
       refused=BrowserUnavailable('LISTING_PROOF_UNAVAILABLE');refused.listing_reason=last_reason
       raise refused
      time.sleep(min(.1,max(0,deadline-time.monotonic())))
      continue
     if not diagnostic['target_activated']:
      phase[0]='listing_activate'
      remaining=deadline-time.monotonic()
      if remaining<=0:break
      # No Page.bringToFront: the managed browser's one tab already renders behind Angelo (throttling off), and
      # bringing it forward took the person's focus on every read (live 2026-11-09).
      diagnostic['target_activated']=True
    phase[0]='listing_open_download'
    value=_value(browser,connection._session,_OPEN_DOWNLOAD.replace('EXPECTED_PATH',json.dumps(path)),bounded,deadline)
    state=value.get('state')
    if state in ('outside_listing','document_loading'):document_state='UNOBSERVED'
    if state in ('outside_listing','not_ready','dialog_present','download_dialog_opening','download_control_unavailable','scan_limit','document_loading'):
     diagnostic['open_result']=state
     ready=value.get('ready','UNOBSERVED')
     trace('open',state,ready if ready in ('loading','interactive','complete','other') else 'UNOBSERVED')
    last_reason={'outside_listing':'LISTING_OUTSIDE','not_ready':'LISTING_NOT_READY',
                 'download_control_unavailable':'LISTING_DOWNLOAD_CONTROL','scan_limit':'LISTING_SCAN_LIMIT',
                 'document_loading':'LISTING_DOCUMENT_LOADING'}.get(state,'LISTING_PROOF_UNAVAILABLE') if type(state)is str else 'LISTING_PROOF_UNAVAILABLE'
    if value.get('state')=='purchase_required':raise BrowserUnavailable('PURCHASE_REQUIRED')
    if value.get('state')in('dialog_present','download_dialog_opening'):opened=True
    elif value.get('state')not in('outside_listing','not_ready','download_control_unavailable','document_loading'):
     refused=BrowserUnavailable('LISTING_PROOF_UNAVAILABLE');refused.listing_reason=last_reason
     raise refused
   if opened:
    phase[0]='listing_decode'
    value=_value(browser,connection._session,OBSERVE_LISTING_JS,bounded,deadline,maximum_bytes=32768)
    reason=value.get('reason')
    waiting={'LISTING_BODY_MISSING':'body_missing','LISTING_DIALOG_MISSING':'dialog_missing',
             'LISTING_ROWS_MISSING':'rows_missing','LISTING_GLB_MISSING':'glb_missing',
             'LISTING_LICENSE_MISSING':'license_missing'}
    branch=waiting.get(reason,'not_observed') if type(reason)is str else 'not_observed'
    if value.get('state')=='not_ready' and branch!='not_observed':
     if branch=='dialog_missing' and last_decode!=branch:inspect_document()
     ready=value.get('ready','UNOBSERVED');dialogs=value.get('dialogs',0);rows=value.get('rows',0)
     if (ready in ('loading','interactive','complete','other') and type(dialogs)is int and 0<=dialogs<=2
         and type(rows)is int and 0<=rows<=24):trace('decode',branch,ready,dialogs,rows,value.get('render'))
    last_decode=branch
    try:page=parse_listing_observation(value,expected_listing_id=item_id)
    except ListingObservationUnavailable as error:
     if error.code in ('LISTING_LICENSE_INVALID','LISTING_LICENSE_AMBIGUOUS'):
      raise
     # A modal may still be hydrating after the single opener. Retry ONLY this read-only snapshot,
     # never the action/navigation/account/network operations, and never accept an invalid snapshot.
     from .fab_browser_failure import failure_details
     waited_for_license=waited_for_license or error.code=='LISTING_LICENSE_MISSING'
     last_reason=error.code
     last_decoder=failure_details(error,'listing_decode')
    else:
     if waited_for_license:
      inspect_document(after_license_wait=True)
      if document_state!='MATCH':
       refused=BrowserUnavailable('LISTING_PROOF_UNAVAILABLE');refused.listing_reason='LISTING_DOCUMENT_UNVERIFIED'
       raise refused
     if bounded.is_set()or browser is not connection._browser:raise BrowserUnavailable('LISTING_PROOF_UNAVAILABLE')
     phase[0]='listing_account_after'
     after=connection.current_account(stop)
     if after!=account:raise BrowserUnavailable('AUTH_REQUIRED')
     if bounded.is_set()or browser is not connection._browser:raise BrowserUnavailable('LISTING_PROOF_UNAVAILABLE')
     return account,page
   time.sleep(min(.1,max(0,deadline-time.monotonic())))
  refused=BrowserUnavailable('LISTING_PROOF_UNAVAILABLE');refused.listing_reason=last_reason
  if last_decoder is not None:refused.producer_diagnostic=last_decoder
  raise refused


def observe_listing(connection,item_id,stop=None):
 from .fab_browser_failure import mapped_failure_details
 phase=['listing_preflight'];diagnostic={'open_result':'not_observed','navigation_probes':0,'target_activated':False,'trace':[]}
 try:return _observe_listing(connection,item_id,stop,phase,diagnostic)
 except Exception as error:
  error.listing_diagnostic=diagnostic
  error.producer_diagnostic=mapped_failure_details(error,phase=phase[0])
  raise


def listing_from_page(page):
 """Convert actual observations into the existing Session record; permission is not current EULA acceptance."""
 from .fab_browser_variants import BrowserChoice,ListingObservation
 from .fab_catalog import MAX_VARIANTS
 item_id=page.listing_id
 licenses={'Standard License':'Fab Standard','CC BY 4.0':'CC-BY-4.0'}  # CC BY: the author is the attribution.
 if page.license_label not in licenses:raise BrowserUnavailable('LISTING_PROOF_UNAVAILABLE')
 if not 1<=len(page.choices)<=MAX_VARIANTS:raise BrowserUnavailable('LISTING_PROOF_UNAVAILABLE')
 quality={'High quality':'high','Medium quality':'medium','Low quality':'low'}
 choices=tuple(BrowserChoice(item_id,row.control_fingerprint,row.control_fingerprint,row.format,row.filename,
                            quality.get(row.quality_label))for row in page.choices if not row.disabled)
 if not choices:raise BrowserUnavailable('LISTING_PROOF_UNAVAILABLE')
 owned=page.library_state_label=='Saved in My Library'
 # Not saved in My Library: the person adds it on Fab (free or bought) before Angelo may download it. Permission
 # stays unknown; the actionable fact is the library state, which this page shows.
 return ListingObservation(item_id,page.title,page.author,licenses[page.license_label],'owned'if owned else'unknown',
                           choices,'download_available'if owned else'acquisition_required','available'if owned else'unknown')


def _listing(connection,item_id,stop=None):
 _,page=observe_listing(connection,item_id,stop)
 observed=listing_from_page(page)
 observed.item()  # Existing bounded metadata validation; no truncation, guessed size, revision or attribution.
 return observed


def listing(connection,item_id,stop=None):
 from .fab_browser_failure import mapped_failure_details
 try:return _listing(connection,item_id,stop)
 except Exception as error:
  error.producer_diagnostic=mapped_failure_details(error,phase='listing_convert')
  raise
