# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""One normal-UI selected download, with existing Session/cache owners retaining final authorization."""
from contextlib import contextmanager
import time

from .fab_browser_process import BrowserUnavailable
from .fab_browser_identity import _ProbeStop
from .fab_browser_listing_owner import listing_from_page,observe_listing
from .fab_browser_variants import BrowserChoice,CompletedDownload
from .fab_browser_download import SelectedDownload
from .fab_catalog import Item
from . import fab_browser_platform as platform_owner
from .fab_browser_selected_control import click
from .team_library_files import MAX_BYTES

@contextmanager
def open_selected(connection,approved_item,choice,expected_account,stop=None,maximum_bytes=None):
 """Exclusive frame/file lease. Event caps are observation/cancellation, not a hard browser disk/network quota.

 Completion requires actual selected frame/GUID plus regular descriptor size/hash, and fresh same-account
 observations. Unproved cancellation or command acknowledgment drains the owned browser before landing cleanup.
 The existing native importer/Undo owner remains the only native-adoption authority.
 """
 if (type(approved_item)is not Item or type(choice)is not BrowserChoice
     or approved_item.identity!=choice.listing_id or choice.variant() not in approved_item.variants):
  raise BrowserUnavailable('DOWNLOAD_SELECTION_CHANGED')
 if type(maximum_bytes)is not int or not 0<maximum_bytes<=MAX_BYTES['model']:raise BrowserUnavailable('DOWNLOAD_LIMIT')
 Landing,CompletedFile=platform_owner.file_types()
 if not connection._lock.acquire(blocking=False):raise BrowserUnavailable('BUSY')
 browser=None;landing=None;file=None;tracker=None;terminal=False;enabled=False;transfer=None;cleaned=False;success=False
 try:
  browser=getattr(connection,'_browser',None)
  if browser is None:raise BrowserUnavailable('CLOSED')
  if getattr(connection,'_selected_transfer',None)is not None:raise BrowserUnavailable('BUSY')
  transfer=object();connection._selected_transfer=transfer
  landing=getattr(connection,'_download_landing',None)  # Preserve prior lifetime owner before any second-job read.
  account,page=observe_listing_for_transfer(connection,choice.listing_id,stop)
  if account!=expected_account:raise BrowserUnavailable('AUTH_REQUIRED')
  if page.library_state_label!='Saved in My Library'or page.license_label not in('Standard License','CC BY 4.0'):
   raise BrowserUnavailable('DOWNLOAD_PERMISSION_UNAVAILABLE')
  matches=[r for r in page.choices if r.control_fingerprint==choice.control_id and not r.disabled]
  if (len(matches)!=1 or choice.local_id!=choice.control_id or matches[0].filename!=choice.filename):
   raise BrowserUnavailable('DOWNLOAD_SELECTION_CHANGED')
  row=matches[0];quality={'High quality':'high','Medium quality':'medium','Low quality':'low'}.get(row.quality_label)
  if quality!=choice.quality:raise BrowserUnavailable('DOWNLOAD_SELECTION_CHANGED')
  if listing_from_page(page).item()!=approved_item:
   raise BrowserUnavailable('DOWNLOAD_ITEM_CHANGED')
  deadline=time.monotonic()+120;bounded=_ProbeStop(connection._combined_stop(stop),deadline)
  def check():
   if browser is not connection._browser or bounded.is_set():raise BrowserUnavailable('CANCELLED')
  frame=browser.call('Page.getFrameTree',session=connection._session,stop=bounded,seconds=2)
  tree=frame.get('frameTree');node=tree.get('frame')if type(tree)is dict else None
  frame_id=node.get('id')if type(node)is dict else None
  tracker=SelectedDownload(frame_id,choice.filename,maximum_bytes=maximum_bytes,expected_bytes=choice.exact_bytes)
  landing=getattr(connection,'_download_landing',None)
  if landing is None:
   try:landing=Landing()
   except BaseException as error:
    if type(getattr(error,'owner',None))is Landing:
     landing=error.owner;connection._download_landing=landing;connection._download_cleanup=(landing,None)
    raise
   connection._download_landing=landing
  connection._download_cleanup=(landing,None)
  prior=getattr(connection,'_download_tracker',None)
  if prior is None:browser._set_download_sink(tracker.accept)
  else:browser._advance_download_sink(prior,tracker)
  connection._download_tracker=tracker
  # Mark uncertainty before sending: a lost allow acknowledgment cannot imply downloads remained disabled.
  enabled=True
  browser.call('Browser.setDownloadBehavior',{'behavior':'allowAndName','downloadPath':str(landing.path),
               'eventsEnabled':True},stop=bounded,seconds=2)
  click(connection,page,row,bounded,deadline)
  while not bounded.is_set():
   observed=browser._poll_selected_download(tracker,stop=bounded,seconds=1)
   if observed.state in('completed','canceled'):
    terminal=True;break
  else:raise BrowserUnavailable('DOWNLOAD_TIMEOUT')
  if not observed.complete:raise BrowserUnavailable('CANCELLED')
  browser.call('Browser.setDownloadBehavior',{'behavior':'deny','eventsEnabled':True},seconds=2);enabled=False
  # Keep the observer installed through all account/file work: a deny ACK can precede buffered events.
  check()
  if connection.current_account(stop)!=expected_account:raise BrowserUnavailable('AUTH_REQUIRED')
  try:file=CompletedFile(landing,observed.guid,observed,check)
  except BaseException as error:
   if type(getattr(error,'owner',None))is CompletedFile:
    file=error.owner;connection._download_cleanup=(landing,file)
   raise
  connection._download_cleanup=(landing,file)
  completed=CompletedDownload(choice,account,file.size,file.sha256,file.chunks())
  yield completed
  check()
  if connection.current_account(stop)!=expected_account:raise BrowserUnavailable('AUTH_REQUIRED')
  success=True
 finally:
  try:
   if browser is not None and transfer is not None and getattr(connection,'_selected_transfer',None)is transfer:
    if not success or getattr(browser,'_download_uncertain',False):
     # Any failed transfer or uncertain event drains all browser-wide writers. No fabricated cancel ACK.
     if tracker is not None and not getattr(browser,'_closed',False):
      tracker.cancel_requested()
      try:browser._poll_selected_download(tracker,seconds=1)
      except BrowserUnavailable:pass
     browser.close()
     if file is not None:file.close()
     if landing is not None:landing.remove()
     platform_owner.close_profile_store(connection)
     if connection._browser is browser:
      connection._browser=None;connection._session=None;connection._target=None
     connection._download_landing=None;connection._download_tracker=None;connection._download_cleanup=None
    else:
     # Keep the directory and observer through the browser lifetime. A late foreign writer cannot lose its
     # landing under it; only this exact consumed/held selected GUID file may retire now.
     file.retire();tracker.retire()
     connection._download_cleanup=(landing,None)
    cleaned=True
  except BaseException:
   error=BrowserUnavailable('DOWNLOAD_CLEANUP_UNPROVED');error.owner=connection;raise error from None
  finally:
   if cleaned and getattr(connection,'_selected_transfer',None)is transfer:connection._selected_transfer=None
   connection._lock.release()


def observe_listing_for_transfer(connection,item_id,stop):
 # The same-thread reentrant operation owns this frame; all competing operations still see BUSY.
 token=connection._selected_transfer;connection._selected_transfer=None
 try:return observe_listing(connection,item_id,stop)
 finally:connection._selected_transfer=token
