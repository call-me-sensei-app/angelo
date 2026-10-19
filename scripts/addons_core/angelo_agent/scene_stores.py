# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded admitted stores for scene queries: observation baselines and cursors.

``scene_queries`` owns both stores' lifetimes and clears them whenever the
document epoch changes. Neither store retains a Blender datablock.
"""

import json
import uuid
from collections import OrderedDict

from .scene_read_values import SceneQueryError, dump


class ObservationStore:
    """Compressed admitted baselines in independent per-coverage LRU pools.

    Whole-scene baselines are large and few. Object-scoped ones (details,
    relationships, discovery pages) are small and numerous, so they get their own
    pool and can never evict a scene baseline, nor be evicted by one. Limits are
    passed per admission so callers can tune them without rebuilding the store.
    """

    def __init__(self):
        self.pools = {}

    def admit(self, token, coverage, payload, count_limit, byte_limit):
        """Store a payload and return how many older ones were evicted.

        A payload that can never fit is rejected before it evicts anything.
        """
        if len(payload) > byte_limit:
            raise SceneQueryError("Scene observation is too large to admit safely; reduce scene complexity")
        pool = self.pools.setdefault(coverage, OrderedDict())
        pool[token] = payload
        evicted = 0
        while len(pool) > 1 and (len(pool) > count_limit or sum(map(len, pool.values())) > byte_limit):
            del pool[next(iter(pool))]
            evicted += 1
        return evicted

    def get(self, token):
        """The admitted payload, refreshed as most recently used, or None."""
        for pool in self.pools.values():
            payload = pool.get(token)
            if payload is not None:
                pool.move_to_end(token)
                return payload
        return None

    def values(self):
        return [payload for pool in self.pools.values() for payload in pool.values()]

    def __len__(self):
        return sum(map(len, self.pools.values()))

    def size(self):
        return sum(map(len, self.values()))

    def clear(self):
        self.pools.clear()


class CursorStore:
    """A cursor binds the scope, exact query and observed fingerprint being paged.

    Any difference fails closed instead of skipping or repeating entries.
    """

    def __init__(self, limit):
        self.limit = limit
        self.entries = OrderedDict()

    def __len__(self):
        return len(self.entries)

    def clear(self):
        self.entries.clear()

    def resume(self, cursor, scope, query, fingerprint):
        """A validated cursor's (offset, stored token), or (0, None) for a first page."""
        if not cursor:
            return 0, None
        stored = self.entries.get(cursor)
        if stored is None:
            raise SceneQueryError("Pagination cursor is unadmitted, expired or evicted; query the first page again")
        page = json.loads(stored)
        if page["scope"] != scope or page["query"] != query:
            raise SceneQueryError("Pagination cursor belongs to another scope or query")
        if page["fingerprint"] != fingerprint:
            raise SceneQueryError("Scene changed during pagination; query the first page again")
        return page["offset"], page["token"]

    def page(self, limit, scope, query, total, selected, offset, fingerprint, token, metadata):
        """Shape one page and admit a cursor for the next one when entries remain."""
        result = dict(metadata, observation_token=token, next_cursor=None,
                      page={"offset": offset, "limit": limit, "returned": len(selected), "total": total},
                      _items=selected)
        if offset + len(selected) < total:
            cursor = f"page:{uuid.uuid4().hex}"
            self.entries[cursor] = dump({"scope": scope, "query": query, "fingerprint": fingerprint,
                                         "offset": offset + len(selected), "token": token})
            while len(self.entries) > self.limit:
                self.entries.popitem(last=False)
            result["next_cursor"] = cursor
        return result
