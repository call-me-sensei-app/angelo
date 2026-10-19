# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded exact base-topology bindings for planned post-edit selections.

This is an integrity precondition, not an authorization or a revision cache.
It includes coordinates and index order, but never object names or paths.
"""

import hashlib
import math
import struct

from .action_schema_types import MAX_VERTICES, MAX_EDGES, MAX_FACES
from .refusal_codes import RefusalError

MAX_CORNERS = MAX_EDGES * 4


def bounded_topology(mesh):
    counts = (len(mesh.vertices), len(mesh.edges), len(mesh.polygons), len(mesh.loops))
    if any(count > limit for count, limit in zip(counts, (MAX_VERTICES, MAX_EDGES, MAX_FACES, MAX_CORNERS))):
        raise RefusalError('Planned topology selection exceeds the bounded base-mesh limits', code='LIMIT_EXCEEDED')
    return counts


def topology_digest(mesh):
    """Hash native float32 coordinates and exact vertex/edge/face index order."""
    counts = bounded_topology(mesh)
    digest = hashlib.sha256(b'ANGELO_BASE_TOPOLOGY_V1\0')
    digest.update(struct.pack('<4I', *counts))
    for vertex in mesh.vertices:
        coordinates = tuple(vertex.co)
        if len(coordinates) != 3 or any(not math.isfinite(v) or abs(v) > 100000 for v in coordinates):
            raise RefusalError('Planned topology selection requires finite bounded coordinates', code='UNSUPPORTED')
        digest.update(struct.pack('<3f', *coordinates))
    for edge in mesh.edges:
        indexes = tuple(edge.vertices)
        if len(indexes) != 2 or any(not 0 <= i < counts[0] for i in indexes):
            raise RefusalError('Planned topology selection requires valid edge indexes', code='UNSUPPORTED')
        digest.update(struct.pack('<2I', *indexes))
    for face in mesh.polygons:
        indexes = tuple(face.vertices)
        if len(indexes) < 3 or any(not 0 <= i < counts[0] for i in indexes):
            raise RefusalError('Planned topology selection requires valid face indexes', code='UNSUPPORTED')
        digest.update(struct.pack('<I', len(indexes)))
        digest.update(struct.pack('<' + 'I' * len(indexes), *indexes))
    return digest.hexdigest()
