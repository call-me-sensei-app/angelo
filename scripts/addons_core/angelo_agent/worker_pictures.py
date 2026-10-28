# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The pictures a worker's view or thumbnail draws, carried back to Angelo as PNG bytes (moved out of ``worker_host``).

``pictures_in`` finds the generated pictures a result names; ``carry`` puts each one's bytes in the result and removes
the worker's file, refusing, with nothing kept, when they would not fit in one answer (``worker_channel``).
"""

import os


def carry(result, store):
    """A view's pictures carried in its answer as PNG bytes (Angelo stores them in the chat's own references,
    ``remote_executor``), the worker's files removed from ``store``; refused when they would not fit in one answer."""
    import base64
    from .worker_channel import RESPONSE_LIMIT
    pictures = list(pictures_in(result))
    sizes = [os.path.getsize(reference["path"]) for reference in pictures]
    if sum(sizes) * 4 // 3 > RESPONSE_LIMIT * 3 // 4:
        for reference in pictures:
            store.discard_observation(reference)
        from .worker_host import _refused
        raise _refused("These pictures are too large to bring back from a worker (%.1f MiB); ask for fewer views or a "
                       "smaller resolution" % (sum(sizes) / 1048576))
    for reference in pictures:
        with open(reference["path"], "rb") as stream:
            reference["png_b64"] = base64.b64encode(stream.read()).decode("ascii")
        store.discard_observation(reference)


def pictures_in(value):
    """The generated pictures a view result names (``ReferenceStore.new_observation`` records)."""
    if isinstance(value, dict):
        if value.get("kind") == "image" and isinstance(value.get("path"), str) and value["path"].endswith(".png"):
            yield value
            return
        for item in value.values():
            yield from pictures_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from pictures_in(item)
