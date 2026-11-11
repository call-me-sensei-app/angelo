# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed normal-UI selected-row activation, after separately reviewed account/permission observations."""
import json
from .fab_browser_identity import _value
from .fab_browser_process import BrowserUnavailable

_CLICK=r"""(() => {
 const expected=EXPECTED;
 if(location.origin!=='https://www.fab.com'||location.pathname!=='/listings/'+expected.listing)return {state:'changed'};
 if(!document.body)return {state:'changed'};
 let steps=200000;
 const visible=n=>{if(--steps<0||!n.getClientRects().length)return false;let d=0;
  for(let p=n;p;p=p.parentElement){if(--steps<0||++d>64)return false;const s=getComputedStyle(p);
   if(s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}return true;};
 const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT),all=[];let n;
 while((n=walk.nextNode())){if(all.length>=4000)return {state:'changed'};all.push(n);}
 const dialogs=all.filter(n=>n.tagName==='DIV'&&n.getAttribute('role')==='dialog'&&n.getAttribute('aria-modal')==='true'&&visible(n));
 if(dialogs.length!==1)return {state:'changed'};
 const modal=dialogs[0],label=modal.getAttribute('aria-labelledby');
 if(!label||label.length>256||label.trim().split(/\s+/).length!==1)return {state:'changed'};
 const h=document.getElementById(label);if(!h||!modal.contains(h)||h.tagName!=='H2'||!visible(h)||h.textContent.trim()!=='Download')return {state:'changed'};
 const titles=all.filter(n=>n.tagName==='H1'&&visible(n));
 const saved=all.filter(n=>/^H[1-6]$/.test(n.tagName)&&!modal.contains(n)&&visible(n)&&n.textContent.trim()==='Saved in My Library');
 const ccby=/^Creative Commons Attribution \(CC BY 4\.0\)( \(CC-BY\))?$/;
 const license=all.filter(n=>n.tagName==='A'&&modal.contains(n)&&visible(n)&&(n.textContent.trim()==="Standard License"||ccby.test(n.textContent.trim().replace(/\s+/g," "))));
 if(titles.length!==1||titles[0].textContent.trim()!==expected.title||saved.length!==1||license.length!==1)return {state:'changed'};
 // Same row inventory as the observation: every visible row of the modal, in document order.
 const lis=all.filter(n=>n.tagName==='LI'&&modal.contains(n)&&visible(n));
 if(!lis.length||lis.length>24)return {state:'changed'};let chosen=[];
 for(let ordinal=0;ordinal<lis.length;ordinal++){
  if(ordinal!==expected.ordinal)continue;const li=lis[ordinal];
  const parts=all.filter(n=>li.contains(n)&&visible(n));if(parts.length>80)return {state:'changed'};
  const files=parts.filter(n=>n.tagName==='DIV'&&!n.childElementCount&&n.textContent===expected.filename);
  if(files.length!==1)continue;
  const badges=parts.filter(n=>n.tagName==='DIV'&&n.getAttribute('aria-label')===
   'Listing '+expected.title+' includes the '+expected.group+' format'&&n.textContent.trim().toLowerCase()===expected.badge);
  if(badges.length!==1)return {state:'changed'};
  const sizes=parts.filter(n=>n.tagName==='DIV'&&!n.childElementCount&&n.textContent.trim()===expected.display_size);
  const qualities=parts.filter(n=>n.tagName==='DIV'&&/^This product is of _(?:high|mid|low)\. quality$/.test(n.getAttribute('aria-label')||''));
  if(sizes.length!==1||qualities.length!==(expected.quality_label===null?0:1)
    ||qualities.length&&(qualities[0].textContent.trim()!==expected.quality_label||qualities[0].getAttribute('aria-label')!==expected.quality_aria))return {state:'changed'};
  const buttons=parts.filter(n=>n.tagName==='BUTTON'&&n.getAttribute('type')==='button'
    &&n.getAttribute('aria-label')==='Download '+expected.group+' asset'&&!n.disabled&&n.getAttribute('aria-disabled')!=='true');
  if(buttons.length!==1)return {state:'changed'};chosen.push(buttons[0]);
 }
 if(steps<0||chosen.length!==1)return {state:'changed'};
 chosen[0].click();return {state:'selected_download_clicked'};
})()"""

def click(connection,page,row,stop,deadline):
 expected=dict(listing=page.listing_id,title=page.title,ordinal=row.ordinal,filename=row.filename,group=row.group,badge=row.badge,display_size=row.display_size,quality_label=row.quality_label,quality_aria={'High quality':'This product is of _high. quality','Medium quality':'This product is of _mid. quality','Low quality':'This product is of _low. quality'}.get(row.quality_label))
 value=_value(connection._browser,connection._session,_CLICK.replace('EXPECTED',json.dumps(expected)),stop,deadline)
 if value!={'state':'selected_download_clicked'}:raise BrowserUnavailable('DOWNLOAD_SELECTION_CHANGED')
