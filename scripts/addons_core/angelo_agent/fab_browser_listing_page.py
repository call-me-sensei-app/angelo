# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only visible Fab public-page and converted-GLB observations, never account or download authority.

Uses public listing links, login-button metadata and the normal Download modal's visible controls. Public links
remain unverified metadata. This module does not navigate, click, inspect private account state, read headers/cookies,
export signed URLs, resolve a variant UID, or claim exact bytes/current terms acceptance.
The required author field is the visible public publisher label, not inferred personal authorship.
The existing browser owner must reobserve and bind an actual control before any separately authorized action.
"""
from dataclasses import dataclass
import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit

from .fab_browser_process import BrowserUnavailable
from .fab_catalog import thumb_url
from .fab_formats import FORMATS, for_label

# Every row label the format table answers to: a format group's name ("FBX", "Blender", "Texture Set") or, in the
# Converted Files group, the row's real format ("glb", "usdz").
SUPPORTED_LABELS = frozenset(label for row in FORMATS for label in row.labels)

MAX_LISTINGS, MAX_SCAN, MAX_TEXT = 20, 4000, 240
ORIGIN = 'https://www.fab.com'


@dataclass(frozen=True, slots=True)
class PublicListing:
    """Only publicly visible link text and the card's own preview image. No offer, license, account or variant is
    inferred; the image is a picture to show, never a fact about the listing."""
    identity: str
    title: str
    thumb: str | None = None
    price: str | None = None   # The card's shown price text, as Fab shows it to this person: display only.

    def compact(self):
        row = dict(id='fab:' + self.identity, title=self.title, source='fab',
                   listing_url=ORIGIN + '/listings/' + self.identity,
                   access='unverified', import_ready=False)
        if self.thumb:
            row['thumb'] = self.thumb
        if self.price:
            row['price'] = self.price
            row['paid'] = self.price != 'Free'
        return row


CURRENCY = r'(?:[A-Z]{1,3}\$|\$|€|£|¥|₩|₹|₽|₺|₫|฿|₱|₪|zł|kr|CHF|Kč|Ft|lei|R)'   # A shown price's currency mark.
PRICE = r'%s\s?\d[\d.,]{0,10}|\d[\d.,]{0,10}\s?%s' % (CURRENCY, CURRENCY)


def price_text(value):
    """A shown price ("Free", "$12.99", "¥1,264", "12,99 €") or None: short, printable, one number at most."""
    if type(value) is not str or not 0 < len(value) <= 24 or not value.isprintable():
        return None
    if value == 'Free':
        return value
    return value if re.fullmatch(PRICE, value) else None




def normalize_links(rows, limit):
    """Accept bounded site link observations, not script instructions or caller-supplied request locations."""
    if type(limit) is not int or not 1 <= limit <= MAX_LISTINGS:
        raise BrowserUnavailable('METADATA_LIMIT')
    if type(rows) is not list or len(rows) > MAX_LISTINGS:
        raise BrowserUnavailable('METADATA_LIMIT')
    found, seen = [], set()
    for row in rows:
        if type(row) is not dict or set(row) - {'image', 'price'} != {'href', 'title'}:
            raise BrowserUnavailable('METADATA_SHAPE')
        href, title = row['href'], row['title']
        if type(href) is not str or len(href) > 160 or type(title) is not str or not 0 < len(title) <= MAX_TEXT:
            raise BrowserUnavailable('METADATA_SHAPE')
        parsed = urlsplit(href)
        if (parsed.scheme != 'https' or parsed.netloc != 'www.fab.com' or parsed.query or parsed.fragment
                or not href.startswith(ORIGIN + '/listings/') or not parsed.path.startswith('/listings/')
                or any(ord(c) < 32 for c in title + href)):
            raise BrowserUnavailable('METADATA_SHAPE')
        identity = parsed.path[len('/listings/'):]
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError
        except (ValueError, AttributeError):
            raise BrowserUnavailable('METADATA_SHAPE') from None
        if identity not in seen:
            found.append(PublicListing(identity, title, thumb_url(row.get('image')), price_text(row.get('price'))))
            seen.add(identity)
        if len(found) > limit:
            raise BrowserUnavailable('METADATA_LIMIT')
    return tuple(found)


# The browser evaluates only these closed operations. No generic JS, URLs, requests or CDP surface is a tool.
# Read only the current Fab document's public links/buttons. Never read forms, document.cookie, storage,
# response headers, OAuth redirects, browser history, or another page's DOM.
_STATE = """(() => {
 if (location.origin !== 'https://www.fab.com') return {state:'outside_fab'};
 const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT); let n,k=0;
 while((n=walk.nextNode()) && ++k<=4000) {
  if(n.tagName==='BUTTON' && n.textContent.trim()==='Sign in') return {state:'sign_in_available'};
 }
 return {state:k>4000?'scan_limit':'site_visible'};
})()"""
_PAGE_FACTS = """(() => {
 const origin=location.origin==='https://www.fab.com'?'fab':location.href==='about:blank'?'about_blank':'other';
 const lang=(document.documentElement.lang||'').slice(0,32).toLowerCase();
 const language=lang==='en'||lang.startsWith('en-')?'english':lang==='ja'||lang.startsWith('ja-')?'japanese':lang?'other':'empty';
 const facts={origin,language,ready_state:document.readyState,body_present:!!document.body,
              sign_in_button:false,sign_in_link:false,sign_in_role:false,scan_limit:false,
              load_failure_document:location.protocol==='chrome-error:'};
 if(origin!=='fab'||!document.body)return facts;
 const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT);let n,k=0;
 while((n=walk.nextNode()) && ++k<=4000){
  const button=n.tagName==='BUTTON',link=n.tagName==='A',role=n.getAttribute('role');
  const roleControl=role==='button'||role==='link';
  if(!(button||link||roleControl)||n.textContent.trim()!=='Sign in')continue;
  if(button)facts.sign_in_button=true;
  if(link)facts.sign_in_link=true;
  if(roleControl)facts.sign_in_role=true;
 }
 facts.scan_limit=k>4000;return facts;
})()"""


def _search_script(limit, query):
    # Limit is already an exact bounded integer; no website/query text becomes executable code.
    return """(() => {
 if(location.origin!=='https://www.fab.com'||location.pathname!=='/search'||new URLSearchParams(location.search).get('q')!==QUERY) return {state:'outside_search',links:[]};
 const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT),links=[],seen=new Set();let n,k=0;
 while((n=walk.nextNode()) && ++k<=4000) {
  if(n.tagName!=='A')continue;
  const href=n.href;
  if(!/^https:\\/\\/www\\.fab\\.com\\/listings\\/[0-9a-f-]{36}$/.test(href)||seen.has(href))continue;
  let title=(n.getAttribute('aria-label')||n.innerText||'').trim();
  if(!title){const image=n.querySelector('img');if(image)title=(image.alt||'').trim();}
  if(!title||title.length>240)continue;
  let card=n,image=n.querySelector('img');
  for(let up=0;!image&&up<4&&card.parentElement;up++){card=card.parentElement;if(card.querySelectorAll('a[href*="/listings/"]').length>1)break;image=card.querySelector('img');}
  const src=image?(image.currentSrc||image.src||''):'';
  // The card's own shown price ("Free", "$12.99", "¥1,264"): display only, never an offer or entitlement fact.
  let priced=n,price=null;
  for(let up=0;up<6&&priced.parentElement&&!price;up++){priced=priced.parentElement;
   if(priced.querySelectorAll('a[href*="/listings/"]').length>2)break;
   const m=(priced.innerText||'').slice(0,600).match(/(^|\\s)(Free|(?:[A-Z]{1,3}\\$|\\$|€|£|¥|₩|₹|₽|₺|₫|฿|₱|₪|zł|kr|CHF|Kč|Ft|lei|R)\\s?\\d[\\d.,]{0,10}|\\d[\\d.,]{0,10}\\s?(?:[A-Z]{1,3}\\$|\\$|€|£|¥|₩|₹|₽|₺|₫|฿|₱|₪|zł|kr|CHF|Kč|Ft|lei|R))(?=\\s|$)/);
   if(m)price=m[2].trim();}
  const row=src&&src.length<=300?{href,title,image:src}:{href,title};if(price&&price.length<=24)row.price=price;
  seen.add(href);links.push(row);if(links.length===LIMIT)break;
 }
 return {state:k>4000?'scan_limit':'public_metadata',links};
})()""".replace('LIMIT', str(limit)).replace('QUERY', json.dumps(query, ensure_ascii=True))


MAX_ROWS = 24
MAX_LABEL = 240
CONVERTED = 'converted files'
BUTTON_ARIA = 'Download Converted Files asset'   # A converted file's row button (its group is Converted Files).
NOT_READY_REASONS = frozenset({'LISTING_BODY_MISSING', 'LISTING_DIALOG_MISSING', 'LISTING_ROWS_MISSING',
                               'LISTING_GLB_MISSING', 'LISTING_LICENSE_MISSING'})
LISTING_PAGE_REASONS = frozenset({'LISTING_LABEL_RELATION', 'LISTING_MODAL_HEADING', 'LISTING_TITLE_COUNT',
    'LISTING_TITLE_SCOPE', 'LISTING_SELLER_COUNT', 'LISTING_MODAL_TITLE', 'LISTING_NO_GLB', 'LISTING_CONVERTED_GROUP',
    'LISTING_CONVERTED_REGION', 'LISTING_ROW_BADGE', 'LISTING_ROW_FILE_SIZE', 'LISTING_ROW_QUALITY',
    'LISTING_ROW_DOWNLOAD_CONTROL', 'LISTING_LICENSE_AMBIGUOUS', 'LISTING_LIBRARY_AMBIGUOUS'})
_UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
_SIZE = re.compile(r'\d+(?:\.\d{1,3})? (?:KB|MB|GB)')
_QUALITIES = {
    'High quality': 'This product is of _high. quality',
    'Medium quality': 'This product is of _mid. quality',
    'Low quality': 'This product is of _low. quality',
}

# No generated class/id values are fixed here. aria-labelledby is resolved, not treated as stable identity.
# Work is a finite DOM-node/text inspection budget, not an OS allocation or elapsed-time guarantee.
OBSERVE_LISTING_JS = r"""(() => {
 const SUPPORTED=SUPPORTED_LABELS;
 const reject=state=>({state});
 const waiting=(reason,dialogs=0,rows=0,render={visibility:['visible','hidden','prerender'].includes(document.visibilityState)?document.visibilityState:'other',raw_dialogs:0,hidden_dialogs:0,opacity_zero_dialogs:0})=>({state:'not_ready',reason,
  ready:['loading','interactive','complete'].includes(document.readyState)?document.readyState:'other',
  dialogs:Math.min(2,dialogs),rows:Math.min(24,rows),render});
 if(location.origin!=='https://www.fab.com')return reject('outside_fab');
 const path=/^\/listings\/([0-9a-fA-F-]{36})\/?$/.exec(location.pathname);
 if(!path)return reject('not_listing');
 let work=200000, chars=24000;
 const spend=()=>{if(--work<0)throw 'scan_limit';};
 const visible=n=>{spend();if(!n.getClientRects().length)return false;
   let depth=0;for(let p=n;p;p=p.parentElement){spend();if(++depth>64)throw 'scan_limit';
     const s=getComputedStyle(p);if(s.visibility==='hidden'||s.visibility==='collapse'||Number(s.opacity)===0)return false;}
   return true;};
 const text=(n,max=240)=>{spend();const s=(n.innerText||'').trim().replace(/\s+/g,' ');
   chars-=s.length;if(s.length>max||chars<0)throw 'scan_limit';return s;};
 let labelFailure=null;
 const failLabel=(n,owner,kind,a)=>{
  const facts={kind,attribute_present:a!==null,id_count:0,resolved_count:0,unique_count:0,
   unambiguous_id_count:0,inside_owner_count:0,visible_count:0,tag:'NONE',expected_label_match:false,audit_complete:true};
  try{
   if(a===null||a.length>256){facts.audit_complete=a===null;labelFailure=facts;throw 'LISTING_LABEL_RELATION';}
   const ids=a.trim()?a.trim().split(/\s+/):[];facts.id_count=Math.min(2,ids.length);
   if(ids.length>2)facts.audit_complete=false;
   const selected=ids.slice(0,2),refs=selected.map(id=>document.getElementById(id));
   facts.resolved_count=refs.filter(Boolean).length;
   const unique=[...new Set(refs.filter(Boolean))];facts.unique_count=unique.length;
   facts.inside_owner_count=refs.filter(h=>h&&owner.contains(h)).length;
   facts.visible_count=refs.filter(h=>h&&visible(h)).length;
   facts.tag=unique.length>1?'MULTIPLE':unique.length===0?'NONE':
    ['H2','H5','BUTTON','SPAN','DIV'].includes(unique[0].tagName)?unique[0].tagName:'OTHER';
   const counts=selected.map(()=>0),walk=document.createTreeWalker(document.documentElement,NodeFilter.SHOW_ELEMENT);
   let element=document.documentElement,k=0,complete=true;
   while(element){if(++k>4000||work<=0){complete=false;break;}work--;
    for(let i=0;i<selected.length;i++)if(element.getAttribute('id')===selected[i])counts[i]=Math.min(2,counts[i]+1);
    element=walk.nextNode();}
   if(complete)facts.unambiguous_id_count=counts.filter((count,i)=>count===1&&refs[i]).length;
   else facts.audit_complete=false;
   const labels=[];
   for(const h of unique){const texts=document.createTreeWalker(h,NodeFilter.SHOW_TEXT);let node,t='',nodes=0;
    while((node=texts.nextNode())){if(++nodes>128||work<=0){facts.audit_complete=false;break;}work--;
     const raw=node.nodeValue||'';if(raw.length>240-t.length||raw.length>chars){facts.audit_complete=false;break;}
     chars-=raw.length;t+=raw;}
    labels.push(t.replace(/\s+/g,' ').trim());}
   const joined=labels.join(' ').trim();
   facts.expected_label_match=facts.audit_complete&&(kind==='modal'?joined==='Download':joined.toLowerCase()==='converted files');
  }catch(e){if(e!=='LISTING_LABEL_RELATION')facts.audit_complete=false;}
  labelFailure=facts;throw 'LISTING_LABEL_RELATION';
 };
 const labelled=(n,owner,kind)=>{const a=n.getAttribute('aria-labelledby');if(!a||a.length>256)return failLabel(n,owner,kind,a);
   const ids=a.trim().split(/\s+/);if(ids.length!==1)return failLabel(n,owner,kind,a);
   const h=document.getElementById(ids[0]);if(!h||!owner.contains(h)||!visible(h))return failLabel(n,owner,kind,a);return h;};
 try {
  if(!document.body)return waiting('LISTING_BODY_MISSING');
  const all=[], walker=document.createTreeWalker(document.body,NodeFilter.SHOW_ELEMENT);
  let n;while((n=walker.nextNode())){spend();if(all.length>=4000)throw 'scan_limit';all.push(n);}
  const rawDialogs=all.filter(n=>{spend();return n.tagName==='DIV'&&n.getAttribute('role')==='dialog'&&
    n.getAttribute('aria-modal')==='true';});
  const dialogs=rawDialogs.filter(visible);
  const render={visibility:['visible','hidden','prerender'].includes(document.visibilityState)?document.visibilityState:'other',
    raw_dialogs:Math.min(2,rawDialogs.length),hidden_dialogs:0,opacity_zero_dialogs:0};
  for(const n of rawDialogs.slice(0,2)){
   let hidden=!n.getClientRects().length,zero=false,depth=0;
   for(let p=n;p;p=p.parentElement){spend();if(++depth>64)throw 'scan_limit';const style=getComputedStyle(p);
    hidden=hidden||style.visibility==='hidden'||style.visibility==='collapse'||style.display==='none';
    zero=zero||Number(style.opacity)===0;}
   if(hidden)render.hidden_dialogs++;if(zero)render.opacity_zero_dialogs++;
  }
  if(dialogs.length!==1)return waiting('LISTING_DIALOG_MISSING',dialogs.length,0,render);
  const modal=dialogs[0], mh=labelled(modal,modal,'modal');
  if(mh.tagName!=='H2'||text(mh)!=='Download')throw 'LISTING_MODAL_HEADING';
  const headings=all.filter(n=>{spend();return n.tagName==='H1'&&visible(n);});
  if(headings.length!==1)throw 'LISTING_TITLE_COUNT';
  const title=text(headings[0]);if(!title)throw 'LISTING_TITLE_COUNT';
  const asides=all.filter(n=>{spend();return n.tagName==='ASIDE'&&n.contains(headings[0])&&visible(n);});
  if(asides.length!==1)throw 'LISTING_TITLE_SCOPE';
  const sellers=all.filter(n=>{spend();if(n.tagName!=='A'||!asides[0].contains(n)||!visible(n))return false;
    const path=n.getAttribute('href');return path!==null&&path.length<=256&&/^\/sellers\/[^/?#]+\/?$/.test(path);});
  if(sellers.length!==1)throw 'LISTING_SELLER_COUNT';
  const author=text(sellers[0]);if(!author)throw 'LISTING_SELLER_COUNT';
  const titles=all.filter(n=>{spend();return modal.contains(n)&&n.tagName==='H3'&&visible(n);});
  if(titles.length!==1||text(titles[0])!==title)throw 'LISTING_MODAL_TITLE';
  // Every row of the modal: one per downloadable file, in "Included formats" lists or their per-format groups.
  // A row names its format in its badge ("Listing <title> includes the <Name> format") and its button
  // ("Download <Name> asset"); Converted Files rows say their real format (glb, gltf, usdz) in the badge text.
  const lis=all.filter(n=>{spend();return n.tagName==='LI'&&modal.contains(n)&&visible(n);});
  if(!lis.length)return waiting('LISTING_ROWS_MISSING',1,0,render);if(lis.length>24)throw 'scan_limit';
  const prefix='Listing '+title+' includes the ',rows=[];
  for(let ordinal=0;ordinal<lis.length;ordinal++){
   const li=lis[ordinal], parts=all.filter(n=>{spend();return li.contains(n)&&visible(n);});
   if(parts.length>80)throw 'scan_limit';
   const badges=parts.filter(n=>{spend();const a=n.getAttribute('aria-label')||'';return n.tagName==='DIV'&&
     a.startsWith(prefix)&&a.endsWith(' format');});
   if(badges.length!==1)throw 'LISTING_ROW_BADGE';
   const group=badges[0].getAttribute('aria-label').slice(prefix.length,-' format'.length);
   if(!group||group.length>40)throw 'LISTING_ROW_BADGE';
   const format=text(badges[0],32).toLowerCase();
   if(!SUPPORTED.includes(group.toLowerCase()==='converted files'?format:group.toLowerCase()))continue;
   const leaves=parts.filter(n=>{spend();return n.tagName==='DIV'&&n.childElementCount===0;});
   const leafText=leaves.map(n=>text(n));
   const files=leafText.filter(s=>/^[^\\/:]{1,200}\.[A-Za-z0-9]{1,8}$/.test(s)&&!/^\d+(?:\.\d{1,3})? (?:KB|MB|GB)$/.test(s));
   const sizes=leafText.filter(s=>/^\d+(?:\.\d{1,3})? (?:KB|MB|GB)$/.test(s));
   if(files.length!==1||sizes.length!==1)throw 'LISTING_ROW_FILE_SIZE';
   const quality=parts.filter(n=>{spend();return n.tagName==='DIV'&&
     /^This product is of _(?:high|mid|low)\. quality$/.test(n.getAttribute('aria-label')||'');});
   if(quality.length>1)throw 'LISTING_ROW_QUALITY';
   const buttons=parts.filter(n=>{spend();return n.tagName==='BUTTON'&&
     n.getAttribute('type')==='button'&&n.getAttribute('aria-label')==='Download '+group+' asset';});
   if(buttons.length!==1)throw 'LISTING_ROW_DOWNLOAD_CONTROL';
   rows.push({ordinal,filename:files[0],group,format,quality_label:quality.length?text(quality[0],64):null,
     quality_aria:quality.length?quality[0].getAttribute('aria-label'):null,display_size:sizes[0],
     button_type:'button',button_aria:'Download '+group+' asset',
     button_disabled:buttons[0].disabled===true||buttons[0].getAttribute('aria-disabled')==='true'});
  }
  // Rows but none in a format Angelo reads (only Unreal, Unity, Maya...): this listing cannot be added.
  if(!rows.length)throw 'LISTING_NO_GLB';
  const ccby=/^Creative Commons Attribution \(CC BY 4\.0\)( \(CC-BY\))?$/;
  const licenses=all.filter(n=>{spend();return modal.contains(n)&&n.tagName==='A'&&visible(n)&&(text(n)==='Standard License'||ccby.test(text(n)));});
  if(licenses.length>1)throw 'LISTING_LICENSE_AMBIGUOUS';
  const saved=all.filter(n=>{spend();return !modal.contains(n)&&/^H[1-6]$/.test(n.tagName)&&visible(n)&&text(n)==='Saved in My Library';});
  if(saved.length>1)throw 'LISTING_LIBRARY_AMBIGUOUS';
  if(!licenses.length)return waiting('LISTING_LICENSE_MISSING',1,lis.length,render);
  return {state:'observed',listing_id:path[1].toLowerCase(),title,author,
    license_label:licenses.length&&ccby.test(text(licenses[0]))?'CC BY 4.0':'Standard License',
    library_state_label:saved.length?'Saved in My Library':null,rows};
 }catch(e){
  if(e==='scan_limit')return reject('scan_limit');
  const reasons=["LISTING_LABEL_RELATION", "LISTING_MODAL_HEADING", "LISTING_TITLE_COUNT", "LISTING_TITLE_SCOPE", "LISTING_SELLER_COUNT", "LISTING_MODAL_TITLE", "LISTING_NO_GLB", "LISTING_CONVERTED_GROUP", "LISTING_CONVERTED_REGION", "LISTING_ROW_BADGE", "LISTING_ROW_FILE_SIZE", "LISTING_ROW_QUALITY", "LISTING_ROW_DOWNLOAD_CONTROL", "LISTING_LICENSE_AMBIGUOUS", "LISTING_LIBRARY_AMBIGUOUS"];
  if(e==='LISTING_LABEL_RELATION'&&labelFailure)return {state:'site_changed',reason:e,relation:labelFailure};
  return reasons.includes(e)?{state:'site_changed',reason:e}:reject('site_changed');
 }
})()""".replace('SUPPORTED_LABELS', json.dumps(sorted(SUPPORTED_LABELS)))


class ListingObservationUnavailable(ValueError):
    """Closed refusal code only; DOM content never becomes an exception message."""
    CODES = LISTING_PAGE_REASONS | NOT_READY_REASONS | frozenset({'OBSERVATION_SHAPE', 'LISTING_ID_INVALID',
        'OBSERVATION_LIMIT', 'SITE_CHANGED', 'LISTING_CHANGED', 'CHOICE_AMBIGUOUS', 'LISTING_LICENSE_INVALID'})
    def __init__(self, code):
        self.code = code if type(code) is str and code in self.CODES else 'SITE_CHANGED'
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ChoiceObservation:
    ordinal: int
    filename: str
    format: str
    quality_label: str | None
    display_size: str
    disabled: bool
    control_fingerprint: str
    group: str = 'Converted Files'   # The row's format group as Fab names it ("FBX", "Blender", "Converted Files").
    badge: str = 'glb'               # The row's badge text ("fbx", "glb"; empty for an icon-only badge).


@dataclass(frozen=True, slots=True)
class ListingObservation:
    listing_id: str
    title: str
    author: str  # Observed public publisher label only.
    license_label: str
    library_state_label: str | None
    choices: tuple[ChoiceObservation, ...]


def _mapping(value, keys):
    if (type(value) is not dict or len(value) != len(keys)
            or any(type(key) is not str for key in value) or set(value) != keys):
        raise ListingObservationUnavailable('OBSERVATION_SHAPE')


def _text(value, maximum=MAX_LABEL):
    if (type(value) is not str or not 0 < len(value) <= maximum
            or any(ord(c) < 32 or ord(c) == 127 for c in value) or value.strip() != value):
        raise ListingObservationUnavailable('OBSERVATION_SHAPE')
    return value


def parse_listing_observation(value, *, expected_listing_id):
    """Strict primitive snapshot to bounded evidence. The fingerprint is local identity, not a remote variant UID.

    Rounded sizes stay text and missing RAW quality stays None. No observed label is converted to an account,
    entitlement, purchase, agreement, exact-size, selected-file or completed-download certificate.
    """
    if type(expected_listing_id) is not str or not _UUID.fullmatch(expected_listing_id):
        raise ListingObservationUnavailable('LISTING_ID_INVALID')
    if type(value) is not dict or len(value) > 8 or any(type(key) is not str for key in value):
        raise ListingObservationUnavailable('OBSERVATION_SHAPE')
    state = value.get('state')
    _text(state, 32)
    if state != 'observed':
        if state == 'not_ready' and set(value) in ({'state','reason','ready','dialogs','rows'}, {'state','reason','ready','dialogs','rows','render'}):
            if (type(value['reason']) is not str or value['reason'] not in NOT_READY_REASONS
                    or type(value['ready']) is not str or value['ready'] not in ('loading','interactive','complete','other')
                    or type(value['dialogs']) is not int or not 0<=value['dialogs']<=2
                    or type(value['rows']) is not int or not 0<=value['rows']<=MAX_ROWS):
                raise ListingObservationUnavailable('OBSERVATION_SHAPE')
            if 'render' in value:
                facts=value['render']
                if (type(facts)is not dict or set(facts)!={'visibility','raw_dialogs','hidden_dialogs','opacity_zero_dialogs'}
                    or type(facts['visibility'])is not str or facts['visibility']not in ('visible','hidden','prerender','other')
                    or any(type(facts[key])is not int or not 0<=facts[key]<=2
                           for key in ('raw_dialogs','hidden_dialogs','opacity_zero_dialogs'))):
                    raise ListingObservationUnavailable('OBSERVATION_SHAPE')
            raise ListingObservationUnavailable(value['reason'])
        if set(value) == {'state','reason','relation'}:
            from .fab_browser_failure import relation_details
            facts=relation_details(value['relation'])
            if state!='site_changed' or value['reason']!='LISTING_LABEL_RELATION' or facts is None:
                raise ListingObservationUnavailable('OBSERVATION_SHAPE')
            refused=ListingObservationUnavailable('LISTING_LABEL_RELATION');refused.listing_relation=facts
            raise refused
        if set(value) == {'state', 'reason'}:
            if state != 'site_changed' or type(value['reason']) is not str or value['reason'] not in LISTING_PAGE_REASONS:
                raise ListingObservationUnavailable('OBSERVATION_SHAPE')
            raise ListingObservationUnavailable(value['reason'])
        _mapping(value, {'state'})
        raise ListingObservationUnavailable('OBSERVATION_LIMIT' if state == 'scan_limit' else 'SITE_CHANGED')
    _mapping(value, {'state', 'listing_id', 'title', 'author', 'license_label', 'library_state_label', 'rows'})
    if type(value['listing_id']) is not str or value['listing_id'] != expected_listing_id:
        raise ListingObservationUnavailable('LISTING_CHANGED')
    title = _text(value['title'])
    author = _text(value['author'])
    license_label, library = value['license_label'], value['library_state_label']
    if license_label is None:
        raise ListingObservationUnavailable('LISTING_LICENSE_MISSING')
    if type(license_label) is not str or license_label not in ('Standard License', 'CC BY 4.0'):
        raise ListingObservationUnavailable('LISTING_LICENSE_INVALID')
    if library is not None and (type(library) is not str or library != 'Saved in My Library'):
        raise ListingObservationUnavailable('OBSERVATION_SHAPE')
    rows = value['rows']
    if type(rows) is not list or not 0 < len(rows) <= MAX_ROWS:
        raise ListingObservationUnavailable('OBSERVATION_LIMIT')
    choices, names, ordinals = [], set(), set()
    row_keys = {'ordinal', 'filename', 'group', 'format', 'quality_label', 'quality_aria', 'display_size',
                'button_type', 'button_aria', 'button_disabled'}
    for row in rows:
        _mapping(row, row_keys)
        ordinal = row['ordinal']
        if type(ordinal) is not int or not 0 <= ordinal < MAX_ROWS or ordinal in ordinals:
            raise ListingObservationUnavailable('CHOICE_AMBIGUOUS')
        filename = _text(row['filename'])
        group = _text(row['group'], 40)
        badge = row['format']
        if type(badge) is not str or len(badge) > 32:
            raise ListingObservationUnavailable('OBSERVATION_SHAPE')
        # Fab's converted files name their real format in the badge; an included format is its group.
        fmt = for_label(badge if group.lower() == CONVERTED else group)
        if fmt is None:
            raise ListingObservationUnavailable('OBSERVATION_SHAPE')
        if any(c in filename for c in '/\\:') or (group, fmt.code, filename) in names:
            raise ListingObservationUnavailable('CHOICE_AMBIGUOUS')
        if (type(row['button_type']) is not str or row['button_type'] != 'button' or
                type(row['button_aria']) is not str or row['button_aria'] != 'Download %s asset' % group or
                type(row['button_disabled']) is not bool):
            raise ListingObservationUnavailable('OBSERVATION_SHAPE')
        quality, aria = row['quality_label'], row['quality_aria']
        if quality is None:
            if aria is not None:
                raise ListingObservationUnavailable('OBSERVATION_SHAPE')
        else:
            _text(quality, 64)
            _text(aria, 64)
            if quality not in _QUALITIES or _QUALITIES[quality] != aria:
                raise ListingObservationUnavailable('OBSERVATION_SHAPE')
        size = _text(row['display_size'], 32)
        if not _SIZE.fullmatch(size):
            raise ListingObservationUnavailable('OBSERVATION_SHAPE')
        identity = [expected_listing_id, 'Download', group.lower(), ordinal, filename, fmt.code, quality, size,
                    'button', row['button_aria']]
        fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
        choices.append(ChoiceObservation(ordinal, filename, fmt.code, quality, size, row['button_disabled'],
                                         fingerprint, group, badge))
        ordinals.add(ordinal)
        names.add((group, fmt.code, filename))
    return ListingObservation(expected_listing_id, title, author, license_label, library, tuple(choices))
