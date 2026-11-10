# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Add a listing to the person's Fab library once they accepted its license in Angelo, and watch for Fab's own
"Saved in My Library". Only Fab's single visible "Add to My Library" button outside a dialog is pressed; anything
else (a purchase, a further confirmation) is left to the person on that page. Reads and presses nothing else.
"""
import json
import time

from .fab_browser_listing_page import ORIGIN
from .fab_browser_process import BrowserUnavailable


def wait_saved(connection, identity, stop=None, seconds=300):
    """After Add on Fab: watch the listing page the person is on, never navigating it, until Fab shows it saved
    in their library. ``True`` when it did within ``seconds``; ``False`` when they left it or time ran out."""
    script = _SAVED_SCRIPT.replace('EXPECTED_PATH', json.dumps('/listings/' + identity))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = connection._evaluate(script, stop, seconds=2).get('state')
        if state == 'saved':
            return True
        if state == 'elsewhere':
            return False
        time.sleep(1.0)
    return False

def add_to_library(connection, identity, stop=None, seconds=20):
    """The person accepted this listing's license in Angelo: press Fab's own "Add to My Library" on that listing,
    hidden, then wait for Fab to show it saved. ``True`` once saved; ``False`` when the page offers no single
    such button (a paid listing, a changed page) or Fab does not confirm, so the caller shows the page instead."""
    with connection._lock:
        if connection._browser is None:
            raise BrowserUnavailable('CLOSED')
        reply = connection._browser.call('Page.navigate', {'url': ORIGIN + '/listings/' + identity},
                                   session=connection._session, stop=connection._combined_stop(stop))
    if reply.get('errorText'):
        return False
    script = _ADD_SCRIPT.replace('EXPECTED_PATH', json.dumps('/listings/' + identity))
    deadline, presses = time.monotonic() + seconds, 0
    while time.monotonic() < deadline:
        state = connection._evaluate(script, stop, seconds=2).get('state')
        if state == 'saved':
            return True
        if state == 'clicked':
            presses += 1
            # A press Fab did not take (the page was still being replaced) is pressed once more on the new page.
            if wait_saved(connection, identity, stop, seconds=min(8.0, max(1.0, deadline - time.monotonic()))):
                return True
            if presses >= 2:
                return False
            continue
        if state == 'ambiguous':
            return False
        time.sleep(0.5)
    return False


# Read-only: whether the current document is this listing and shows Fab's own "Saved in My Library" heading (any
# heading level: Fab renders it as an H3 on a listing a person just saved, live 2026-11-09).
_SAVED_SCRIPT = """(() => {
 if(location.origin!=='https://www.fab.com'||location.pathname!==EXPECTED_PATH)return {state:'elsewhere'};
 const saved=[...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].some(n=>n.textContent.trim()==='Saved in My Library'&&n.getClientRects().length>0);
 return {state:saved?'saved':'not_saved'};
})()"""
# Presses the one visible, enabled "Add to My Library" button outside any dialog on this listing; never another.
_ADD_SCRIPT = """(() => {
 if(location.origin!=='https://www.fab.com'||location.pathname!==EXPECTED_PATH||document.readyState!=='complete')return {state:'loading'};
 const shown=n=>n.getClientRects().length>0,dialogs=[...document.querySelectorAll('[role="dialog"]')];
 if(dialogs.some(shown))return {state:'loading'};   // The previous document's Download dialog: not the fresh page yet.
 if([...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].some(n=>shown(n)&&n.textContent.trim()==='Saved in My Library'))return {state:'saved'};
 const found=[...document.querySelectorAll('button')].filter(n=>shown(n)&&!n.disabled&&!dialogs.some(d=>d.contains(n))
   &&n.textContent.trim()==='Add to My Library');
 if(found.length===1){found[0].click();return {state:'clicked'};}
 return {state:found.length>1?'ambiguous':'missing'};
})()"""


def wait_bought(connection, identity, stop=None, seconds=840):
    """After Buy on Fab: watch the listing page the person is buying on, never navigating it, until it is theirs (Fab's
    "Saved in My Library", or a Download button with no Buy now / Add to cart left). False when they leave it or time ends."""
    script = _BOUGHT_SCRIPT.replace('EXPECTED_PATH', json.dumps('/listings/' + identity))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = connection._evaluate(script, stop, seconds=2).get('state')
        if state == 'owned':
            return True
        time.sleep(2.0)
    return False


# Read-only: whether this listing's page now shows it as the person's (a purchase may pass through checkout pages).
_BOUGHT_SCRIPT = """(() => {
 if(location.origin!=='https://www.fab.com'||location.pathname!==EXPECTED_PATH||document.readyState!=='complete')return {state:'elsewhere'};
 const shown=n=>n.getClientRects().length>0,text=n=>n.textContent.trim();
 if([...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].some(n=>shown(n)&&text(n)==='Saved in My Library'))return {state:'owned'};
 const buttons=[...document.querySelectorAll('button')].filter(shown).map(text);
 return {state:buttons.includes('Download')&&!buttons.some(t=>t==='Buy now'||t==='Add to cart')?'owned':'not_owned'};
})()"""

