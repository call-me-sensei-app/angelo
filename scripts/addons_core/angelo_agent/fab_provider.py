# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fab's deferred-download coordinator, awaiting a verified account transport (doc/angelo/fab.rst).

There is deliberately no default transport or login button. These are internal adapter contracts, not invented
Fab endpoints. Worker callers search metadata, then prepare ONE explicit variant after native approval. Before
main-thread mutation, use PreparedImport.consume with the existing library importer and scene-bound controller.
Credentials, temporary URLs and account authorization remain owned by the transport, never by model results.
"""

from collections import OrderedDict
from contextlib import ExitStack, nullcontext
from dataclasses import dataclass, field
import hashlib
import threading
import time
from typing import Protocol
import uuid

from . import fab_cache
from .fab_catalog import FabError, Item, MAX_RESULTS, text
from .team_library_files import Refused

MAX_RETAINED = 200
SELECTION_SECONDS, REQUEST_SECONDS = 1200, 900
PURCHASE_NOTE = ("purchase_required: paid Fab assets the person does not own (price as Fab shows it). Never import "
                 "one: tell the person its price and ask them to open Library > Fab, choose it and press Buy on Fab; "
                 "Angelo adds it to the scene once Fab shows it as theirs. Offer a free result when there is one.")


class Transport(Protocol):
    """An account-bound adapter. Implementation and live authentication verification are still required.

    All methods run on workers, must enforce response/time bounds and honor cancellation. The adapter's account
    cannot change in place: revoke this Session and construct a new one when the account changes or signs out.
    search returns at most limit normalized metadata rows (fab_catalog), NEVER model or full texture bytes.
    authorize checks current entitlement and applicable download permission for the exact variant/revision and
    returns True, otherwise a closed FabError. Free price is NOT authorization. It must never buy or accept terms.
    stream returns a context manager yielding byte chunks; it owns and closes any connection. Resolve a temporary
    file location only here, with normal TLS/host/redirect checks; do not return it in metadata or exception text.
    """
    def search(self, query, limit, include_paid, cancel): ...
    def authorize(self, item, variant, cancel): ...
    def stream(self, item, variant, cancel): ...


class _Stop:
    def __init__(self, session, cancel, deadline):
        self.session, self.cancel, self.deadline = session, cancel, deadline

    def is_set(self):
        return (self.session._closed.is_set() or (self.cancel is not None and self.cancel.is_set())
                or time.monotonic() >= self.deadline)

    def check(self):
        if self.session._closed.is_set():
            raise FabError('AUTH_REQUIRED')
        if self.cancel is not None and self.cancel.is_set():
            raise FabError('CANCELLED')
        if time.monotonic() >= self.deadline:
            raise FabError('EXPIRED')


@dataclass(repr=False)
class PreparedImport:
    """Checked files under a lease, consumed at most once; neither this object nor its paths go to the model."""
    item: Item
    facts: dict
    cache_hit: bool
    _lease: object = field(repr=False)
    _session: object = field(repr=False)
    _stop: object = field(repr=False)
    payload_proof: fab_cache.PayloadProof = field(repr=False)
    _used: bool = False

    def consume(self, import_callback):
        """Main-thread handoff to the existing importer. Caller owns current scene binding and approval.

        Session revocation and handoff are ordered under one lock: a completed sign-out never permits a later
        mutation. Imports already executing are atomic and finish before sign-out returns. Always release files.
        """
        try:
            with self._session._lock:
                self._stop.check()
                if self._used:
                    raise FabError('EXPIRED')
                self._used = True
                return import_callback(self.item.import_item(), dict(self.facts))
        finally:
            self.close()

    def close(self):
        with self._session._lock:
            self._used = True
            self._lease.release()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class Session:
    """One verified account connection, supplied by its future authentication owner. No global registration.

    account_scope is the transport's stable opaque account identifier, never a token/email. It is hashed solely
    to segregate local cache entries; knowledge of it grants no authority. No auth or metadata is persisted here.
    """
    def __init__(self, root, account_scope, transport, *, suggest_paid=False):
        text(account_scope, 256)
        self._scope = hashlib.sha256(account_scope.encode()).hexdigest()
        self._root, self._transport = root, transport
        self._closed, self._lock = threading.Event(), threading.RLock()
        self._items = OrderedDict()
        self.set_suggest_paid(suggest_paid)
        binder = getattr(transport, 'bind_session', None)
        if binder is not None:
            binder(self)

    def set_suggest_paid(self, enabled):
        """Person-owned preference, never a tool argument. Turning off invalidates prior paid suggestions."""
        if type(enabled) is not bool:
            raise ValueError('suggest_paid must be a boolean')
        with self._lock:
            self._suggest_paid = enabled
            if not enabled:
                self._items = OrderedDict((key, saved) for key, saved in self._items.items()
                                          if saved[2] or not saved[0].needs_purchase_suggestion())

    @property
    def active(self):
        """Cached revocation state only; no I/O and never a fresh account/entitlement observation."""
        return not self._closed.is_set()

    def disconnect(self):
        """Invalidate in-flight work and unconsumed results. The authentication owner must also revoke its client."""
        with self._lock:
            self._closed.set()
            self._items.clear()

    def search(self, query, limit=8, cancel=None):
        if not isinstance(query, str) or len(query) > 200 or any(ord(c) < 32 for c in query):
            raise FabError('INVALID_METADATA')
        if type(limit) is not int or not 1 <= limit <= MAX_RESULTS:
            raise FabError('LIMIT_EXCEEDED')
        stop = _Stop(self, cancel, time.monotonic() + REQUEST_SECONDS)
        stop.check()
        try:
            with self._lock:
                include_paid = self._suggest_paid
            rows = self._transport.search(query, limit, include_paid, stop)
            stop.check()
            if not isinstance(rows, list) or len(rows) > limit:
                raise FabError('LIMIT_EXCEEDED')
            from .fab_browser_listing_page import price_text
            paid = [dict(id='fab:' + str(uuid.UUID(row['id'])), title=row['title'], source='fab', purchase_required=True,
                         price=price_text(row.get('price')), import_ready=False)
                    for row in rows if type(row) is dict and row.get('purchase_required') is True
                    and type(row.get('title')) is str and 0 < len(row['title']) <= 200]
            items = [Item.parse(row) for row in rows if not (type(row) is dict and row.get('purchase_required') is True)]
            if len({item.identity for item in items}) != len(items):
                raise FabError('INVALID_METADATA')
            with self._lock:
                stop.check()
                items = [item for item in items if self._suggest_paid or not item.needs_purchase_suggestion()]
                for item in items:
                    key = 'fab:' + item.identity
                    explicit = self._items.get(key, (None, None, False))[2]
                    self._items[key] = (item, time.monotonic(), explicit)
                    self._items.move_to_end(key)
                while len(self._items) > MAX_RETAINED:
                    self._items.popitem(last=False)
                result = {'items': [item.compact() for item in items], 'scene_changed': False}
                if paid:   # Shown, never importable: the person buys it in Library > Fab (Buy on Fab), then it is.
                    result['purchase_required'] = paid
                    result['purchase_note'] = PURCHASE_NOTE
                return result
        except FabError:
            raise
        except Exception:
            raise FabError('UNAVAILABLE') from None

    def lookup(self, item_id, cancel=None, show=False):
        """Worker: explicitly browse one observed listing and retain its exact immutable choices.

        Human Browse is not an agent recommendation and does not change Suggest paid assets. Public discovery
        never enters selections through this route: the account transport must supply actual listing facts.
        Returned readiness is a current display observation, not authority; prepare always checks again.
        """
        if type(item_id) is not str or not item_id.startswith('fab:'):
            raise FabError('INVALID_METADATA')
        identity = item_id[4:]
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError
        except ValueError:
            raise FabError('INVALID_METADATA') from None
        stop = _Stop(self, cancel, time.monotonic() + REQUEST_SECONDS)
        stop.check()
        try:
            result = self._transport.lookup(identity, stop, show)
            stop.check()
            if type(result) is not dict or set(result) != {'item', 'download_permission', 'entitlement'}:
                raise FabError('INVALID_METADATA')
            item = Item.parse(result['item'])
            download_permission, entitlement = result['download_permission'], result['entitlement']
            if (item.identity != identity or download_permission not in ('available', 'terms_required', 'unknown')
                    or entitlement not in ('download_available', 'acquisition_required', 'purchase_required', 'unknown')):
                raise FabError('INVALID_METADATA')
            with self._lock:
                stop.check()
                self._items[item_id] = (item, time.monotonic(), True)
                self._items.move_to_end(item_id)
                while len(self._items) > MAX_RETAINED:
                    self._items.popitem(last=False)
            projection = item.compact()
            for variant, compact in zip(item.variants, projection['variants']):
                if variant.browser_choice is not None:
                    # Explicit human lookup may display the actual observed basename; no quality/label inference.
                    compact['label'] = variant.browser_choice.filename
            return dict(item=projection, download_permission=download_permission, entitlement=entitlement,
                        import_ready=download_permission == 'available' and entitlement == 'download_available',
                        scene_changed=False)
        except FabError:
            raise
        except Exception:
            raise FabError('UNAVAILABLE') from None

    def selection(self, item_id, variant_id):
        """Read-only pre-approval facts; no account request, file read or download."""
        if not isinstance(item_id, str) or not isinstance(variant_id, str):
            raise FabError('INVALID_METADATA')
        with self._lock:
            if self._closed.is_set():
                raise FabError('AUTH_REQUIRED')
            saved = self._items.get(item_id)
            if saved is None or time.monotonic() - saved[1] >= SELECTION_SECONDS:
                raise FabError('EXPIRED')
            item = saved[0]
            variant = next((v for v in item.variants if v.identity == variant_id), None)
            if variant is None:
                raise FabError('INVALID_METADATA')
            return item, variant

    def prepare(self, item_id, variant_id, *, expected, cancel=None, progress=None):
        """Worker: authorize then reuse/download one variant. expected is the tuple captured before approval.

        A concurrent metadata refresh cannot substitute another revision or size after approval. Callers must
        recheck scene ownership and policy before consuming the result on the main thread.
        """
        stop = _Stop(self, cancel, time.monotonic() + REQUEST_SECONDS)
        stop.check()
        item, variant = self.selection(item_id, variant_id)
        if (item, variant) != expected:
            raise FabError('EXPIRED')
        lease = None
        try:
            if self._transport.authorize(item, variant, stop) is not True:
                raise FabError('PURCHASE_REQUIRED' if item.access == 'purchase_required' else 'ACQUISITION_REQUIRED')
            stop.check()
            with ExitStack() as stack:
                selected_stream = lambda: self._transport.stream(item, variant, stop)
                resolved = variant
                if variant.browser_choice is not None:
                    from .fab_browser_variants import CompletedDownload
                    completed = stack.enter_context(selected_stream())
                    if type(completed) is not CompletedDownload:
                        raise FabError('CORRUPT')
                    resolved = completed.resolved_variant(variant)
                    selected_stream = lambda: nullcontext(completed)
                facts, lease, hit, payload_proof = fab_cache.prepare(
                    self._root, self._scope, item, resolved, selected_stream, stop.check, progress)
            stop.check()
            return PreparedImport(item, facts, hit, lease, self, stop, payload_proof)
        except BaseException as error:
            if lease is not None:
                lease.release()
            if isinstance(error, FabError):
                raise
            if isinstance(error, Refused):
                raise FabError('CORRUPT') from None
            if isinstance(error, OSError):
                raise FabError('CACHE_UNAVAILABLE') from None
            if isinstance(error, Exception):
                raise FabError('UNAVAILABLE') from None
            raise
