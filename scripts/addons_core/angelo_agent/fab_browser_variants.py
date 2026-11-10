# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private observed browser choices and completed-file leases; not a Fab endpoint/response schema.

No producer is installed here. The browser owner must ground each field in actual post-login UI evidence.
Unknown exact size/quality/revision remain unknown. Local choice identities are explicitly not publisher IDs.
"""
from dataclasses import dataclass, replace
import re

from .fab_catalog import FabError, Item, MAX_VARIANTS, QUALITIES, Variant, text, token
from .team_library_files import MAX_BYTES


@dataclass(frozen=True, repr=False, slots=True)
class BrowserChoice:
    listing_id: str
    local_id: str
    control_id: str
    format: str
    filename: str
    quality: str | None = None
    exact_bytes: int | None = None
    publisher_revision: str | None = None

    def __post_init__(self):
        token(self.local_id)
        token(self.control_id)
        if self.format != 'glb':
            raise FabError('UNSUPPORTED')
        text(self.filename, 255)
        if (self.filename in ('.', '..') or any(c in '/\\' or ord(c) == 127
                                              or 0xD800 <= ord(c) <= 0xDFFF for c in self.filename)
                or len(self.filename.encode('utf8')) > 255):
            raise FabError('INVALID_METADATA')
        if self.quality is not None and self.quality not in QUALITIES:
            raise FabError('INVALID_METADATA')
        if self.exact_bytes is not None and (type(self.exact_bytes) is not int
                                             or not 0 < self.exact_bytes <= MAX_BYTES['model']):
            raise FabError('LIMIT_EXCEEDED')
        if self.publisher_revision is not None:
            token(self.publisher_revision)

    def variant(self):
        return Variant(self.local_id, self.publisher_revision, self.quality, self.exact_bytes, self)


@dataclass(frozen=True, repr=False, slots=True)
class ListingObservation:
    identity: str
    title: str
    author: str
    license: str
    access: str
    choices: tuple
    entitlement: str
    download_permission: str
    offers: tuple = ()

    def item(self):
        if (type(self.choices) is not tuple or not 1 <= len(self.choices) <= MAX_VARIANTS
                or any(type(c) is not BrowserChoice
                                                 or c.listing_id != self.identity for c in self.choices)
                or self.entitlement not in ('download_available', 'acquisition_required', 'purchase_required', 'unknown')
                or self.download_permission not in ('available', 'terms_required', 'unknown')
                or type(self.offers) is not tuple or len(self.offers) > 8
                or self.entitlement == 'download_available' and self.access not in ('owned', 'free')):
            raise FabError('INVALID_METADATA')
        return Item.parse(dict(id=self.identity, title=self.title, author=self.author, license=self.license,
                               access=self.access, kind='model', variants=[c.variant() for c in self.choices],
                               offers=list(self.offers)))


@dataclass(frozen=True, repr=False, slots=True)
class CompletedDownload:
    """Yielded by open_selected's context manager, whose exit owns file/transfer cleanup.

    chunks is a bounded iterator over the owner's verified regular file. No path or download URL crosses here.
    sha256 seals the actual completed content; it is never advertised as a publisher revision/signature.
    """
    choice: BrowserChoice
    account: object
    size: int
    sha256: str
    chunks: object

    def resolved_variant(self, selected):
        if (type(self.choice) is not BrowserChoice or selected.browser_choice != self.choice
                or type(self.size) is not int
                or not 0 < self.size <= MAX_BYTES['model']
                or self.choice.exact_bytes is not None and self.size != self.choice.exact_bytes
                or type(self.sha256) is not str or not re.fullmatch('[0-9a-f]{64}', self.sha256)):
            raise FabError('CORRUPT')
        # This private cache key is a content seal, never a fabricated pre-download provider revision.
        return replace(selected, size=self.size, revision=self.sha256)

    def __iter__(self):
        return iter(self.chunks)
