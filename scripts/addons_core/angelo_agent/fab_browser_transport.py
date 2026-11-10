# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-bound policy adapter over closed browser-owner operations, never a browser/HTTP implementation.

Session remains the sole retained selection/cache coordinator. Human public discovery belongs to the browser
owner and may show unverified, nonselectable rows separately. This adapter supplies only observed ready metadata
for agent recommendations. No default owner/producer is registered or fabricated here.
"""
from contextlib import contextmanager
import hashlib
import json
import threading
import uuid
import weakref

from .fab_browser_account import AccountObservation, current_account
from .fab_browser_listing_page import thumb_url
from .fab_browser_process import BrowserUnavailable
from .fab_browser_variants import BrowserChoice, CompletedDownload, ListingObservation
from .fab_cache import CHUNK
from .fab_catalog import FabError, Item, MAX_RESULTS, Variant
from .team_library_files import MAX_BYTES
from .fab_browser_failure import mapped_failure_details


def _browser_error(error, *, phase='transport_observe'):
    """Only reviewed owner failure codes cross into the public Fab vocabulary; never exception text."""
    failure = FabError({'AUTH_REQUIRED': 'AUTH_REQUIRED', 'ACCOUNT_PROOF_PENDING': 'AUTH_REQUIRED', 'CLOSED': 'AUTH_REQUIRED',
                     'CANCELLED': 'CANCELLED', 'LISTING_PROOF_PENDING': 'UNSUPPORTED',
                     'DOWNLOAD_PROOF_PENDING': 'UNAVAILABLE', 'DOWNLOAD_ITEM_CHANGED': 'EXPIRED',
                     'PURCHASE_REQUIRED': 'PURCHASE_REQUIRED'}.get(
                         error.code, 'UNAVAILABLE'))
    failure.producer_diagnostic = mapped_failure_details(error, phase=phase)
    return failure


class BrowserTransport:
    @classmethod
    def observe(cls, owner, stop):
        """Worker: bind a currently observed account; the facade alone creates/reuses the shared Session.

        A missing/unreviewed producer must return no observation or refuse. This method never opens login,
        reads credentials, accepts a person-owned Done flag, or promotes public UI state to authentication.
        """
        stop.check()
        try:
            account = owner.current_account(stop)
            stop.check()
            transport = cls(owner, account)
            transport._check(stop)
            return transport
        except FabError:
            raise
        except BrowserUnavailable as error:
            raise _browser_error(error) from None
        except Exception as error:
            failure = FabError('UNAVAILABLE')
            failure.producer_diagnostic = mapped_failure_details(error)
            raise failure from None

    def __init__(self, owner, account):
        if type(account) is not AccountObservation:
            raise FabError('AUTH_REQUIRED')
        self._owner, self._account = owner, account
        self._revoked, self._session = threading.Event(), None
        # Generation scoping conservatively avoids cross-runtime/profile cache reuse. An account observation
        # grants no cache authority: authorize is still repeated for every selected import, including cache hits.
        self.account_scope = hashlib.sha256(json.dumps([account.opaque_account, account.profile_generation,
                                                       account.runtime_generation]).encode()).hexdigest()

    def bind_session(self, session):
        if self._session is not None:
            raise FabError('EXPIRED')
        self._session = weakref.ref(session)
        if self._revoked.is_set():
            session.disconnect()

    def revoke(self):
        self._revoked.set()
        session = self._session() if self._session is not None else None
        if session is not None:
            session.disconnect()

    def _check(self, stop):
        stop.check()
        if self._revoked.is_set():
            raise FabError('AUTH_REQUIRED')
        try:
            return current_account(self._owner.current_account, self._account, stop)
        except FabError as error:
            if error.code == 'AUTH_REQUIRED':
                self.revoke()
            raise
        except BrowserUnavailable as error:
            failure = _browser_error(error)
            if failure.code == 'AUTH_REQUIRED':
                self.revoke()
            raise failure from None
        except Exception:
            raise FabError('UNAVAILABLE') from None

    def _listing(self, identity, stop):
        try:
            canonical = type(identity) is str and len(identity) == 36 and str(uuid.UUID(identity)) == identity
        except ValueError:
            canonical = False
        if not canonical:
            raise FabError('INVALID_METADATA')
        self._check(stop)
        try:
            value = self._owner.listing(identity, stop)
            stop.check()
            self._check(stop)
            if type(value) is not ListingObservation or value.identity != identity:
                raise FabError('INVALID_METADATA')
            return value, value.item()
        except FabError:
            raise
        except BrowserUnavailable as error:
            failure = _browser_error(error, phase='transport_listing')
            if failure.code == 'AUTH_REQUIRED':
                self.revoke()
            raise failure from None
        except Exception as error:
            # A listing whose Download dialog offers no converted GLB (only .blend, FBX, OBJ, a glTF zip): not addable.
            failure = FabError('UNSUPPORTED' if getattr(error, 'args', ())[:1] == ('LISTING_NO_GLB',) else 'UNAVAILABLE')
            failure.producer_diagnostic = mapped_failure_details(error, phase='transport_listing')
            raise failure from None

    def search(self, query, limit, include_paid, stop):
        self._check(stop)
        if type(query) is not str or not 0 < len(query) <= 200 or any(ord(c) < 32 for c in query):
            raise FabError('INVALID_METADATA')
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS or type(include_paid) is not bool:
            raise FabError('LIMIT_EXCEEDED')
        try:
            public = self._owner.public_search(query, limit, stop)
            self._check(stop)
            rows = public.get('items') if type(public) is dict else None
            if type(rows) is not list or len(rows) > limit:
                raise FabError('INVALID_METADATA')
            found, seen, thumbs = [], set(), {}
            for row in rows:
                stop.check()
                if type(row) is not dict or type(row.get('id')) is not str or not row['id'].startswith('fab:'):
                    raise FabError('INVALID_METADATA')
                identity = row['id'][4:]
                if thumb_url(row.get('thumb')):
                    thumbs[identity] = row['thumb']   # The search card's own picture: shown, never a listing fact.
                try:
                    canonical = str(uuid.UUID(identity)) == identity
                except ValueError:
                    canonical = False
                if not canonical or identity in seen:
                    raise FabError('INVALID_METADATA')
                seen.add(identity)
                try:
                    _observed, item = self._listing(identity, stop)
                except FabError as error:
                    # Missing ready facts remain human public discovery; never synthesize license/quality/bytes.
                    if error.code in ('INVALID_METADATA', 'UNSUPPORTED'):
                        continue
                    if error.code == 'PURCHASE_REQUIRED':
                        # A paid listing the person does not own: never an import, but named with its shown price
                        # when they want paid suggestions, so the agent can offer it (Buy on Fab is theirs to press).
                        if include_paid:
                            found.append(dict(id=identity, title=row.get('title'), price=row.get('price'),
                                              purchase_required=True))
                        continue
                    raise
                if include_paid or not item.needs_purchase_suggestion():
                    found.append(dict(self._metadata(item), thumb=thumbs.get(identity)) if thumbs.get(identity)
                                 else self._metadata(item))
            from .fab_browser_background import place
            place(self._owner, False)
            return found
        except FabError:
            raise
        except BrowserUnavailable as error:
            failure = _browser_error(error)
            if failure.code == 'AUTH_REQUIRED':
                self.revoke()
            raise failure from None
        except Exception as error:
            failure = FabError('UNAVAILABLE')
            failure.producer_diagnostic = mapped_failure_details(error, phase='transport_search')
            raise failure from None

    @staticmethod
    def _metadata(item):
        return dict(id=item.identity, title=item.title, author=item.author, license=item.license,
                    kind='model', access=item.access, offers=[o.compact() for o in item.offers],
                    variants=list(item.variants))

    def lookup(self, identity, stop, show=False):
        """Explicit human listing read, separate from paid-suggestion recommendations; never downloads. ``show``:
        the person acts on the listing next (Add to My Library), so its page comes forward; else it stays behind."""
        from .fab_browser_background import place
        try:
            observed, item = self._listing(identity, stop)
        except FabError as error:
            if not (show and error.code == 'PURCHASE_REQUIRED' and getattr(self._owner, '_evaluate', None) is not None):
                raise
            # Buy on Fab: that listing comes forward for the person to buy (Angelo never pays); once Fab shows it as
            # theirs, it is read again and the add continues. Not bought in time: the paid reason stands.
            from .fab_browser_library_add import wait_bought
            place(self._owner, True, identity)
            if not wait_bought(self._owner, identity, stop):
                place(self._owner, False)
                raise
            observed, item = self._listing(identity, stop)
        if show and observed.entitlement != 'download_available':
            # The person accepted the license in Angelo: add it hidden. A page that needs more of them (a purchase,
            # a further confirmation) comes forward instead, and Angelo continues once Fab shows it saved.
            from .fab_browser_library_add import add_to_library, wait_saved
            managed = getattr(self._owner, '_evaluate', None) is not None
            added = managed and add_to_library(self._owner, identity, stop)
            if managed and not added:
                place(self._owner, True, identity)
                added = wait_saved(self._owner, identity, stop)
            if added:
                observed, item = self._listing(identity, stop)
        place(self._owner, False)
        return dict(item=self._metadata(item), download_permission=observed.download_permission,
                    entitlement=observed.entitlement)

    def authorize(self, item, variant, stop):
        observed, current = self._listing(item.identity, stop)
        if observed.entitlement == 'acquisition_required' and getattr(self._owner, '_evaluate', None) is not None:
            # A free listing not yet in the person's Fab library: adding it is part of this approved import (the
            # panel's note and the agent's card say so); a paid one has no Add to My Library and stays refused.
            from .fab_browser_library_add import add_to_library
            if add_to_library(self._owner, item.identity, stop):
                observed, current = self._listing(item.identity, stop)
            from .fab_browser_background import place
            place(self._owner, False)
        if current != item or variant not in current.variants or variant.browser_choice is None:
            raise FabError('EXPIRED')
        if observed.download_permission == 'terms_required':
            raise FabError('TERMS_REQUIRED')
        if observed.download_permission != 'available':
            raise FabError('UNAVAILABLE')
        if observed.entitlement != 'download_available':
            raise FabError('PURCHASE_REQUIRED' if observed.entitlement == 'purchase_required'
                           else 'ACQUISITION_REQUIRED')
        return True

    @contextmanager
    def stream(self, item, variant, stop):
        # Check the account before entering the selected owner. Session.prepare authorizes
        # the full listing, and the owner rechecks it on the selected-transfer observation.
        self._check(stop)
        if type(item) is not Item or type(variant) is not Variant:
            raise FabError('EXPIRED')
        choice = variant.browser_choice
        if (type(choice) is not BrowserChoice or choice.listing_id != item.identity
                or variant != choice.variant() or variant not in item.variants):
            raise FabError('EXPIRED')
        try:
            with self._owner.open_selected(item, choice, self._account, stop, MAX_BYTES['model']) as completed:
                self._check(stop)
                if (type(completed) is not CompletedDownload or type(completed.account) is not AccountObservation
                        or completed.account != self._account):
                    self.revoke()
                    raise FabError('AUTH_REQUIRED')
                completed.resolved_variant(variant)
                # The browser owner proves the completed regular file. Independently verify the bytes exposed
                # across the seam; existing cache checks GLB structure and exact resolved length before publish.
                digest, count = hashlib.sha256(), 0
                def checked_chunks():
                    nonlocal count
                    for chunk in completed:
                        stop.check()
                        if type(chunk) is not bytes or not 0 < len(chunk) <= CHUNK:
                            raise FabError('CORRUPT')
                        count += len(chunk)
                        if count > completed.size:
                            raise FabError('LIMIT_EXCEEDED')
                        digest.update(chunk)
                        yield chunk
                    if count != completed.size or digest.hexdigest() != completed.sha256:
                        raise FabError('CORRUPT')
                    self._check(stop)
                yield CompletedDownload(completed.choice, completed.account, completed.size,
                                        completed.sha256, checked_chunks())
                self._check(stop)
        except FabError:
            raise
        except BrowserUnavailable as error:
            failure = _browser_error(error)
            if failure.code == 'AUTH_REQUIRED':
                self.revoke()
            raise failure from None
        except Exception:
            raise FabError('UNAVAILABLE') from None
