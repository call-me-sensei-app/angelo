# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The script precondition's per-object guard as one fingerprint (``BlenderExecutor.precondition``).

``angelo_execute_python`` binds its approval to a conservative structural/transform guard: every scene object's
identity, world matrix, data and modifier count, in scene order. A precondition is compared only with ``==``
(``Controller._precondition``, a worker's approved check, ``worker_values``), so one digest of the same facts guards
exactly as the tuple of them did, without building, comparing and, for a worker, encoding about twenty values an
object on every check (two or three checks a call: about 30,000 values at 1,500 objects, 1.0.6 concurrent edit).
Bulk reads (``foreach_get``) keep the read in C.

Float equality is kept: -0.0 reads as 0.0, since they compared equal, and a matrix holding NaN never matches, since
NaN never compared equal, so such a scene never passes as unchanged.
"""

import hashlib
import uuid

import numpy as np


def objects_fingerprint(scene):
    objects = scene.objects
    count = len(objects)
    matrices = np.empty(16 * count, np.float32)
    uids = np.empty(count, np.int64)
    objects.foreach_get("matrix_world", matrices)
    objects.foreach_get("session_uid", uids)
    if np.isnan(matrices).any():
        return "unmatched:" + uuid.uuid4().hex
    matrices += np.float32(0.0)   # -0.0 + 0.0 is 0.0: values that compared equal hash equal.
    digest = hashlib.blake2b(digest_size=20)
    digest.update(count.to_bytes(8, "little"))
    digest.update(uids.tobytes())
    digest.update(matrices.tobytes())
    digest.update(repr(tuple((getattr(o.data, "session_uid", None), len(o.modifiers)) for o in objects)).encode())
    return digest.hexdigest()
