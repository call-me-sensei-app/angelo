# SPDX-FileCopyrightText: 2026 Angelo Contributors
# SPDX-License-Identifier: GPL-2.0-or-later
"""Closed observations for one selected browser download; not an authorization or import certificate.

Protocol source: ChromeDevTools/devtools-protocol json/browser_protocol.json, Browser download events.
URL and filePath fields are deliberately discarded. A future account/control owner must authorize and bind
an actual chosen variant before enabling browser downloads. Exact regular-file size/hash checks remain with
the landing/cache/import owners. No request, browser command, file operation or purchase occurs here.
"""
from dataclasses import dataclass
import math
import re

from .fab_browser_process import BrowserUnavailable
from .team_library_files import MAX_BYTES


@dataclass(frozen=True, slots=True)
class DownloadObservation:
    guid: str | None
    state: str
    received_bytes: int
    total_bytes: int | None
    cancel_requested: bool

    @property
    def complete(self):
        return self.state == 'completed' and not self.cancel_requested


def _identifier(value):
    if type(value) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,128}', value):
        raise BrowserUnavailable('DOWNLOAD_EVENT_SHAPE')
    return value


def _filename(value):
    if (type(value) is not str or not value or len(value) > 255
            or value in ('.', '..') or any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF
                                         or c in '/\\' for c in value)
            or len(value.encode('utf-8')) > 255):
        raise BrowserUnavailable('DOWNLOAD_EVENT_SHAPE')
    return value


def _bytes(value):
    if (type(value) not in (int, float) or not 0 <= value <= 2**53
            or not math.isfinite(value) or int(value) != value):
        raise BrowserUnavailable('DOWNLOAD_EVENT_SHAPE')
    return int(value)


class SelectedDownload:
    """Constant-space private sink. Matching browser observations grant no account or asset authority."""
    def __init__(self, frame, filename, *, maximum_bytes, expected_bytes=None):
        self.frame, self.filename = _identifier(frame), _filename(filename)
        if type(maximum_bytes) is not int or not 0 < maximum_bytes <= MAX_BYTES['model']:
            raise BrowserUnavailable('DOWNLOAD_LIMIT')
        if expected_bytes is not None and (type(expected_bytes) is not int or not 0 < expected_bytes <= maximum_bytes):
            raise BrowserUnavailable('DOWNLOAD_LIMIT')
        self.maximum_bytes, self.expected_bytes = maximum_bytes, expected_bytes
        self._guid, self._state, self._received, self._total = None, 'waiting', 0, None
        self._cancel_requested = False
        self._retired = False

    def cancel_requested(self):
        # A request never fabricates the browser's canceled acknowledgement.
        self._cancel_requested = True

    def observation(self):
        return DownloadObservation(self._guid, self._state, self._received, self._total, self._cancel_requested)

    def retire(self):
        if not self.observation().complete:raise BrowserUnavailable('DOWNLOAD_PROGRESS_CHANGED')
        self._retired = True

    def accept(self, method, params):
        """Accept only browser download events; no raw event, URL, file path or response is retained."""
        if method not in ('Browser.downloadWillBegin', 'Browser.downloadProgress'):
            return False
        if type(params) is not dict:
            raise BrowserUnavailable('DOWNLOAD_EVENT_SHAPE')
        guid = _identifier(params.get('guid'))
        if self._retired:
            # No retired cancellation or identity change can silently outlive its completed-file approval.
            if (method != 'Browser.downloadProgress' or guid != self._guid or params.get('state') != 'completed'
                    or _bytes(params.get('receivedBytes')) != self._received
                    or (_bytes(params.get('totalBytes')) or None) != self._total):
                raise BrowserUnavailable('DOWNLOAD_PROGRESS_CHANGED')
            return True
        if method == 'Browser.downloadWillBegin':
            frame, filename = _identifier(params.get('frameId')), _filename(params.get('suggestedFilename'))
            if self._guid is not None or frame != self.frame or filename != self.filename:
                raise BrowserUnavailable('DOWNLOAD_SELECTION_CHANGED')
            self._guid, self._state = guid, 'inProgress'
            return True
        state = params.get('state')
        if type(state) is not str or state not in ('inProgress', 'completed', 'canceled'):
            raise BrowserUnavailable('DOWNLOAD_EVENT_SHAPE')
        if guid != self._guid:
            # A late cancellation for an earlier download cannot become this selection's identity or progress.
            if state == 'canceled':
                return False
            raise BrowserUnavailable('DOWNLOAD_SELECTION_CHANGED')
        if state == 'canceled':
            self._cancel_requested = True  # Revoke immediately; terminal acknowledgement waits for validation.
        received, total = _bytes(params.get('receivedBytes')), _bytes(params.get('totalBytes'))
        if received > self.maximum_bytes or total > self.maximum_bytes:
            raise BrowserUnavailable('DOWNLOAD_LIMIT')
        if received < self._received and state != 'canceled':
            raise BrowserUnavailable('DOWNLOAD_PROGRESS_CHANGED')
        if self.expected_bytes is not None and (received > self.expected_bytes or total not in (0, self.expected_bytes)):
            raise BrowserUnavailable('DOWNLOAD_SIZE_CHANGED')
        if self._state in ('completed', 'canceled') and state == 'inProgress':
            raise BrowserUnavailable('DOWNLOAD_PROGRESS_CHANGED')
        if state == 'completed' and (received == 0 or total not in (0, received)
                                     or self.expected_bytes is not None and received != self.expected_bytes):
            raise BrowserUnavailable('DOWNLOAD_SIZE_CHANGED')
        if self._state == 'completed' and state == 'completed':
            if received != self._received or (total or None) != self._total:
                raise BrowserUnavailable('DOWNLOAD_PROGRESS_CHANGED')
        # A cancellation (including one after completion) never authorizes importing the resulting file.
        if self._state == 'canceled' or state == 'canceled':
            state = 'canceled'
        self._state, self._received, self._total = state, max(received, self._received), total or None
        return True
