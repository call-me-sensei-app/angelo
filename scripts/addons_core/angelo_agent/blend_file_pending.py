# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Workspace metadata on the existing StudioPending lifecycle; no live Blender access on workers."""

import threading
import time

from . import blend_file_listing as listing
from .studio_agent import StudioPending

MAX_ACTIVE = 2
_active, _lock = set(), threading.Lock()


class _Scan:
    def __init__(self, args, workspace, api):
        self.api, self.token = api, api.metadata_cancel_token(listing.DEADLINE_SECONDS)
        self.done, self.value = threading.Event(), None
        self.started = time.monotonic()
        with _lock:
            if len(_active) >= MAX_ACTIVE:
                raise ValueError("busy")
            _active.add(self)
        try:
            threading.Thread(target=self._run, args=(dict(args), workspace), daemon=True,
                             name="AngeloBlendMetadata").start()
        except Exception:
            with _lock:
                _active.discard(self)
            raise

    def _run(self, args, workspace):
        try:
            self.value = listing.read(None, args, workspace, api=self.api, token=self.token)
        except Exception:
            self.value = listing._refused("UNREADABLE", "The metadata read could not finish; no scene data was loaded.")
        finally:
            self.value.update(read_only=True, scene_changed=False,
                              native_exec_ms=round((time.monotonic() - self.started) * 1000, 3))
            with _lock:
                _active.discard(self)
            self.done.set()

    def cancel(self):
        self.api.metadata_cancel(self.token)

    def poll(self):
        return self.value if self.done.is_set() else None


def begin(args, workspace):
    if workspace is None:
        return listing._refused("NO_WORKSPACE", "No file workspace is selected for this chat.")
    api = listing.scanner_api()
    if api is None:
        return listing._refused(listing.NATIVE_REQUIRED, listing.NATIVE_MESSAGE, file=args["file"])
    try:
        scan = _Scan(args, workspace, api)
    except ValueError:
        return listing._refused("BUSY", "Wait for a workspace metadata read to finish; two can run at once.")
    pending = StudioPending(scan.poll, scan.cancel, cleanup=lambda: None)
    pending.status_text = "Reading file metadata"
    pending.activity_note = "Metadata is read on a bounded worker; Stop cancels at the next read boundary."
    pending.timeout = listing.DEADLINE_SECONDS + 5.0
    return pending
