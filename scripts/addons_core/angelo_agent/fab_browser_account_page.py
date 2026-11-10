# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed observations of the actually inspected Epic account UI; never a login/session authority.

Epic's account page exposes these fixed observed selectors. The browser owner alone chooses
the target/profile and binds generations. Parsing a separate IAB page or synthetic fixture does not verify a
product-owned Chrome session. No cookies, form fields, storage, tokens, navigation or requests are read here.
Historical Fab legal documents do not establish the latest agreement or any listing entitlement.
"""
from dataclasses import dataclass
import hashlib
import re

ACCOUNT_ORIGIN = 'https://accounts.epicgames.com'
MAX_SCAN, MAX_TEXT = 4000, 240  # Existing Connection DOM-scan and visible-text bounds.
HISTORY_INTRO = 'View the legal documents you’ve agreed to. Some may not appear in this list.'

# The owner evaluates this fixed operation only in its own explicitly selected account target. Generated CSS
# classes are deliberately irrelevant; only the observed stable IDs and adjacent visible P elements are read.
SCRIPT = r"""(() => {
 if(location.origin!=='https://accounts.epicgames.com'||location.pathname!=='/account/personal')
  return {state:'outside_account_page'};
 const view=document.getElementById('personal-view'),heading=document.getElementById('account-info-title');
 const visible=n=>!!n&&n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden'
  &&getComputedStyle(n).display!=='none'&&getComputedStyle(n).opacity!=='0';
 if(!view||!heading||!view.contains(heading)||!visible(view)||!visible(heading)
  ||heading.textContent.trim()!=='Account information')return {state:'account_page_not_ready'};
 const walk=document.createTreeWalker(view,NodeFilter.SHOW_ELEMENT),candidates=[];let n,k=0;
 while((n=walk.nextNode())){
  if(++k>4000)return {state:'scan_limit'};
  if(n.tagName!=='P'||!visible(n)||n.textContent.trim()!=='ID:')continue;
  const next=n.nextElementSibling;
  candidates.push({label:n.textContent.trim(),sibling_tag:next?next.tagName:'',
   value:next&&next.tagName==='P'&&visible(next)?next.textContent.trim().slice(0,241):''});
  if(candidates.length>1)return {state:'ambiguous_account_id'};
 }
 return {state:'account_page',origin:location.origin,path:location.pathname,view_id:view.id,
  heading_id:heading.id,heading:heading.textContent.trim(),scanned:k,candidates};
})()"""


class AccountPageUnavailable(ValueError):
    """Closed diagnostics contain no observed account identifiers, legal dates or document URLs."""
    CODES = frozenset({'PAGE_CHANGED', 'OUTSIDE_ACCOUNT_PAGE', 'ACCOUNT_PAGE_NOT_READY',
                       'ACCOUNT_ID_AMBIGUOUS', 'ACCOUNT_ID_INVALID', 'SCAN_LIMIT', 'LEGAL_HISTORY_INVALID'})

    def __init__(self, code):
        self.code = code if code in self.CODES else 'PAGE_CHANGED'
        super().__init__('The account page observation is unavailable: ' + self.code)


@dataclass(frozen=True, repr=False, slots=True)
class AccountPageIdentity:
    opaque_account: str


@dataclass(frozen=True, repr=False, slots=True)
class HistoricalFabDocument:
    version: int
    revision: int
    language: str


@dataclass(frozen=True, repr=False, slots=True)
class FabAgreementHistory:
    documents: tuple


def decode_account_page(value):
    """Decode only the fixed observer's private result, discarding the raw ID after domain-separated hashing.

    The return value says which account the observed page describes, not whether the calling connection owns
    that session or may download an asset. Profile/runtime/target ownership remains with the browser owner.
    """
    if type(value) is not dict:
        raise AccountPageUnavailable('PAGE_CHANGED')
    states = {'outside_account_page': 'OUTSIDE_ACCOUNT_PAGE', 'account_page_not_ready': 'ACCOUNT_PAGE_NOT_READY',
              'scan_limit': 'SCAN_LIMIT', 'ambiguous_account_id': 'ACCOUNT_ID_AMBIGUOUS'}
    state = value.get('state')
    if type(state) is not str:
        raise AccountPageUnavailable('PAGE_CHANGED')
    if state in states:
        raise AccountPageUnavailable(states[state])
    if (set(value) != {'state', 'origin', 'path', 'view_id', 'heading_id', 'heading', 'scanned', 'candidates'}
            or state != 'account_page' or value['origin'] != ACCOUNT_ORIGIN or value['path'] != '/account/personal'
            or value['view_id'] != 'personal-view' or value['heading_id'] != 'account-info-title'
            or value['heading'] != 'Account information'):
        raise AccountPageUnavailable('PAGE_CHANGED')
    if type(value['scanned']) is not int or not 0 <= value['scanned'] <= MAX_SCAN:
        raise AccountPageUnavailable('SCAN_LIMIT')
    rows = value['candidates']
    if type(rows) is not list or len(rows) != 1:
        raise AccountPageUnavailable('ACCOUNT_ID_AMBIGUOUS' if type(rows) is list and len(rows) > 1
                                     else 'ACCOUNT_ID_INVALID')
    row = rows[0]
    if (type(row) is not dict or set(row) != {'label', 'sibling_tag', 'value'}
            or type(row['label']) is not str or len(row['label']) > MAX_TEXT or row['label'].strip() != 'ID:'
            or row['sibling_tag'] != 'P' or type(row['value']) is not str or len(row['value']) > MAX_TEXT
            or re.fullmatch('[0-9a-fA-F]{32}', row['value'].strip()) is None):
        raise AccountPageUnavailable('ACCOUNT_ID_INVALID')
    canonical = row['value'].strip().lower().encode('ascii')
    return AccountPageIdentity(hashlib.sha256(b'epic-account-id\0' + canonical).hexdigest())


def _text(value):
    if (type(value) is not str or not 0 < len(value) <= MAX_TEXT or not value.strip()
            or any(ord(c) < 32 for c in value)):
        raise AccountPageUnavailable('LEGAL_HISTORY_INVALID')
    return value.strip()


def decode_legal_history(value):
    """Pure decoder for an owner's bounded legal-table observations; no legal-page extraction/navigation.

    Owner inputs are actual visible rows, never a supplied accepted/current flag. Dates and document IDs/paths
    are validated as bounded observations then discarded. An empty or older history cannot authorize anything.
    """
    if (type(value) is not dict or set(value) != {'origin', 'path', 'view_id', 'intro', 'columns', 'rows'}
            or value['origin'] != ACCOUNT_ORIGIN or value['path'] != '/account/eula-history'
            or value['view_id'] != 'eula-history-view' or value['intro'] != HISTORY_INTRO
            or value['columns'] != ['Title', 'Language', 'Date'] or type(value['rows']) is not list
            or len(value['rows']) > MAX_SCAN):
        raise AccountPageUnavailable('LEGAL_HISTORY_INVALID')
    documents = []
    for row in value['rows']:
        if (type(row) is not dict or set(row) != {'title', 'language', 'date', 'download_origin', 'download_path'}):
            raise AccountPageUnavailable('LEGAL_HISTORY_INVALID')
        title, language, _date = (_text(row[key]) for key in ('title', 'language', 'date'))
        if not title.startswith('Fab End User License Agreement_'):
            continue
        label = re.fullmatch(r'Fab End User License Agreement_en \(v\. ([1-9][0-9]*)\)', title)
        path = _text(row['download_path'])
        link = re.fullmatch(r'/eulatracking-download/fab_eula/en/v([1-9][0-9]*)/r([1-9][0-9]*)/[^/?#]+\.pdf', path)
        if (label is None or language != 'English' or row['download_origin'] != 'https://cdn1.epicgames.com'
                or link is None or label[1] != link[1]):
            raise AccountPageUnavailable('LEGAL_HISTORY_INVALID')
        documents.append(HistoricalFabDocument(int(label[1]), int(link[2]), language))
    return FabAgreementHistory(tuple(documents))
