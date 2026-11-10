# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The script precondition's per-object guard as one fingerprint (``BlenderExecutor.precondition``).

``angelo_execute_python`` binds its approval to a conservative structural/transform guard: every scene object's
identity, world matrix, data and modifier count, in scene order. A precondition is compared only for equality
(``stale_binding.same``: at the run, a worker's approved check, ``worker_values``), so one digest of the same facts guards
exactly as the tuple of them did, without building, comparing and, for a worker, encoding about twenty values an
object on every check (two or three checks a call: about 30,000 values at 1,500 objects, 1.0.6 concurrent edit).
Bulk reads (``foreach_get``) keep the read in C.

Float equality is kept: -0.0 reads as 0.0, since they compared equal. A NaN element is hashed by its place (a mask
beside the matrices, NaN itself read as 0.0), so a scene holding one binds like any other. It used to make a fresh
``unmatched:<uuid>`` digest, so every script of such a scene was refused stale at its run, Auto's included, however
often it was asked again (break-it C7b M-4). Change detection is unchanged: every other element is hashed as before,
the mask tells a NaN from a real 0.0, and a NaN slot holds no value a comparison could tell apart.
"""

import hashlib

import numpy as np


def objects_fingerprint(scene):
    objects = scene.objects
    count = len(objects)
    matrices = np.empty(16 * count, np.float32)
    uids = np.empty(count, np.int64)
    objects.foreach_get("matrix_world", matrices)
    objects.foreach_get("session_uid", uids)
    unknown = np.isnan(matrices)
    if unknown.any():
        matrices[unknown] = 0.0
    matrices += np.float32(0.0)   # -0.0 + 0.0 is 0.0: values that compared equal hash equal.
    digest = hashlib.blake2b(digest_size=20)
    digest.update(count.to_bytes(8, "little"))
    if unknown.any():
        digest.update(b"nan" + np.packbits(unknown).tobytes())   # A scene without NaN hashes as it always did.
    digest.update(uids.tobytes())
    digest.update(matrices.tobytes())
    digest.update(repr(tuple((getattr(o.data, "session_uid", None), len(o.modifiers)) for o in objects)).encode())
    return digest.hexdigest()
