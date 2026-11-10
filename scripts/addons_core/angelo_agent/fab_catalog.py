# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fab metadata admitted by the future account transport, never a public mirrored index.

This is Angelo's internal contract, NOT a claimed Fab API response schema. No URLs, credentials or paths are
accepted into it. Only self-contained GLB models are enabled in this first slice; other formats need their own
live proof. See doc/angelo/fab.rst. Catalog text is untrusted data, never agent instructions.
"""

from dataclasses import dataclass
import re
from decimal import Decimal
import uuid
from urllib.parse import urlsplit

from .team_library_files import MAX_BYTES

QUALITIES = ('low', 'medium', 'high', 'raw')
MAX_RESULTS, MAX_VARIANTS = 20, 16
TOKEN = re.compile(r'[a-zA-Z0-9_-]{1,128}\Z')


class FabError(RuntimeError):
    """Closed, credential-free failures. Never wrap transport exception text in a public result."""
    MESSAGES = {
        'AUTH_REQUIRED': 'Connect your Fab account before searching or importing.',
        'PURCHASE_REQUIRED': 'This asset must be purchased on Fab before it can be imported.',
        'ACQUISITION_REQUIRED': 'Acquire this asset on Fab before importing it.',
        'TERMS_REQUIRED': 'Review the applicable license on Fab before importing this asset.',
        'EXPIRED': 'The Fab session or asset selection expired. Search again before importing.',
        'UNAVAILABLE': 'Fab is unavailable. Try again later.',
        'INVALID_METADATA': 'Fab returned unsupported or incomplete asset metadata.',
        'UNSUPPORTED': 'This Fab file is not a supported self-contained GLB model.',
        'LIMIT_EXCEEDED': 'This Fab request exceeds the supported size or result limit.',
        'CANCELLED': 'The Fab request was stopped; nothing was imported.',
        'CORRUPT': 'The Fab file failed validation; nothing was imported.',
        'CACHE_UNAVAILABLE': 'The local Fab cache could not be used.',
    }

    def __init__(self, code):
        self.code = code if code in self.MESSAGES else 'UNAVAILABLE'
        super().__init__(self.MESSAGES[self.code])


def token(value):
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise FabError('INVALID_METADATA')
    return value


def text(value, limit):
    if not isinstance(value, str) or not 1 <= len(value) <= limit or any(ord(c) < 32 for c in value):
        raise FabError('INVALID_METADATA')
    return value


@dataclass(frozen=True)
class Variant:
    identity: str
    revision: str | None
    quality: str | None
    size: int | None
    browser_choice: object = None

    @classmethod
    def parse(cls, row):
        if type(row) is cls and row.browser_choice is not None:
            from .fab_browser_variants import BrowserChoice
            choice = row.browser_choice
            if type(choice) is not BrowserChoice or row != choice.variant():
                raise FabError('INVALID_METADATA')
            return row
        if not isinstance(row, dict) or row.get('format') != 'glb' or row.get('quality') not in QUALITIES:
            raise FabError('INVALID_METADATA')
        size = row.get('size')
        if type(size) is not int or not 0 < size <= MAX_BYTES['model']:
            raise FabError('LIMIT_EXCEEDED')
        return cls(token(row.get('id')), token(row.get('revision')), row['quality'], size)

    def compact(self):
        row = {'id': self.identity, 'format': 'glb'}
        if self.quality is not None:
            row['quality'] = self.quality
        if self.size is not None:
            row['bytes'] = self.size
        if self.browser_choice is not None:
            row.update(identity_scope='local_choice', exact_size_known=self.size is not None,
                       maximum_bytes=MAX_BYTES['model'])
        return row


@dataclass(frozen=True)
class Offer:
    amount: str
    currency: str
    tier: str

    @classmethod
    def parse(cls, row):
        if not isinstance(row, dict):
            raise FabError('INVALID_METADATA')
        amount, currency = row.get('amount'), row.get('currency')
        if (not isinstance(amount, str) or not re.fullmatch(r'[0-9]{1,9}(?:\.[0-9]{1,4})?', amount)
                or not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency)):
            raise FabError('INVALID_METADATA')
        return cls(amount, currency, text(row.get('tier'), 80))

    def compact(self):
        return {'amount': self.amount, 'currency': self.currency, 'tier': self.tier}


THUMB_HOST = 'media.fab.com'
_THUMB_PATH = re.compile(r'/image_previews/[A-Za-z0-9_/-]{1,200}\.(?:jpe?g|png|webp)')


def thumb_url(value):
    """``value`` when it is a preview picture on Fab's own image host (https, a plain path, no query), else None."""
    if type(value) is not str or len(value) > 300 or any(ord(c) < 33 for c in value):
        return None
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or parsed.netloc != THUMB_HOST or parsed.query or parsed.fragment
            or not _THUMB_PATH.fullmatch(parsed.path)):
        return None
    return value


@dataclass(frozen=True)
class Item:
    identity: str
    title: str
    author: str
    license: str
    variants: tuple
    access: str
    offers: tuple
    thumb: str | None = None   # The search card's picture on Fab's image host: shown, never a listing fact.

    @classmethod
    def parse(cls, row):
        if not isinstance(row, dict):
            raise FabError('INVALID_METADATA')
        identity = row.get('id')
        try:
            if str(uuid.UUID(identity)) != identity:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise FabError('INVALID_METADATA') from None
        if row.get('license') not in ('Fab Standard', 'CC-BY-4.0') or row.get('kind') != 'model':
            raise FabError('INVALID_METADATA')
        variants = row.get('variants')
        if not isinstance(variants, list) or not 1 <= len(variants) <= MAX_VARIANTS:
            raise FabError('INVALID_METADATA')
        variants = tuple(Variant.parse(variant) for variant in variants)
        if len({variant.identity for variant in variants}) != len(variants):
            raise FabError('INVALID_METADATA')
        access = row.get('access', 'unknown')
        offers = row.get('offers', [])
        if access not in ('owned', 'free', 'purchase_required', 'unknown') or not isinstance(offers, list) or len(offers) > 8:
            raise FabError('INVALID_METADATA')
        offers = tuple(Offer.parse(offer) for offer in offers)
        if access == 'free' and any(Decimal(offer.amount) > 0 for offer in offers):
            raise FabError('INVALID_METADATA')
        return cls(identity, text(row.get('title'), 120), text(row.get('author'), 120), row['license'],
                   variants, access, offers, thumb_url(row.get('thumb')))

    def needs_purchase_suggestion(self):
        # Unknown access stays hidden by default: a missing price/entitlement is never assumed free.
        return self.access not in ('owned', 'free')

    def compact(self):
        row = {'id': 'fab:' + self.identity, 'title': self.title, 'author': self.author, 'source': 'fab', 'kind': 'model',
               'license': self.license, 'attribution_required': self.license == 'CC-BY-4.0',
               'access': self.access, 'variants': [variant.compact() for variant in self.variants]}
        if self.thumb:
            row['thumb'] = self.thumb
        if self.needs_purchase_suggestion():
            row.update(offers=[offer.compact() for offer in self.offers],
                       listing_url='https://www.fab.com/listings/' + self.identity,
                       price_note='Listed prices may change; confirm price and license at Fab checkout.')
        return row

    def import_item(self):
        """Existing library_import's input; generated source URL only, never a download URL or account identifier."""
        return dict(self.compact(), attribution=self.author,
                    source_url='https://www.fab.com/listings/' + self.identity)
