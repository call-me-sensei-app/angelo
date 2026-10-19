# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ed25519 signatures (RFC 8032) in plain Python, for the signed update feed.

Blender's bundled Python has no ``cryptography`` package, and the update feed
(``update_feed.py``) must be verified without adding a binary dependency.
The app only needs :func:`verify`; :func:`public_key` and :func:`sign` exist
for the feed tool (``scripts/angelo/sign_update_feed.py``, local rehearsal keys
only; production signs in Cloud KMS) and the tests, which check this module
against the RFC's published vectors.

``verify`` is the strict form: it rejects a non-canonical signature scalar or
point encoding and a public key of small order, and compares the encoded
point, so a signature has exactly one valid encoding. It takes about 10 ms, so
callers run it on a worker thread like every other network step. ``sign`` is
not constant time: it is meant for tests and rehearsals, never for the app.

Standard library only; no bpy.
"""

import hashlib

_P = 2 ** 255 - 19
_Q = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)  # A square root of -1.
KEY_BYTES = 32
SIGNATURE_BYTES = 64


def _inverse(value):
    return pow(value, _P - 2, _P)


def _recover_x(y, sign):
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inverse(_D * y * y + 1) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * _I % _P
    if (x * x - x2) % _P:
        return None
    if x == 0 and sign:
        return None
    return _P - x if (x & 1) != sign else x


_BY = 4 * _inverse(5) % _P
_BX = _recover_x(_BY, 0)
_BASE = (_BX, _BY, 1, _BX * _BY % _P)  # Extended coordinates (X, Y, Z, T).
_IDENTITY = (0, 1, 1, 0)


def _add(a, b):
    a1 = (a[1] - a[0]) * (b[1] - b[0]) % _P
    b1 = (a[1] + a[0]) * (b[1] + b[0]) % _P
    c1 = 2 * a[3] * b[3] * _D % _P
    d1 = 2 * a[2] * b[2] % _P
    e, f, g, h = b1 - a1, d1 - c1, d1 + c1, b1 + a1
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _multiply(scalar, point):
    result = _IDENTITY
    while scalar:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _negate(point):
    return ((-point[0]) % _P, point[1], point[2], (-point[3]) % _P)


def _equal(a, b):
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _encode(point):
    zi = _inverse(point[2])
    x, y = point[0] * zi % _P, point[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(data):
    if len(data) != 32:
        return None
    number = int.from_bytes(data, "little")
    y, sign = number & ((1 << 255) - 1), number >> 255
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _hash_scalar(*parts):
    return int.from_bytes(hashlib.sha512(b"".join(parts)).digest(), "little")


def _expand(seed):
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little")
    scalar &= (1 << 254) - 8
    scalar |= 1 << 254
    return scalar, digest[32:]


def public_key(seed):
    """The 32-byte public key of a 32-byte private seed."""
    if len(seed) != KEY_BYTES:
        raise ValueError("An Ed25519 seed is 32 bytes")
    return _encode(_multiply(_expand(seed)[0], _BASE))


def sign(seed, message):
    """The 64-byte signature of ``message`` (release tooling and tests only)."""
    scalar, prefix = _expand(seed)
    public = _encode(_multiply(scalar, _BASE))
    r = _hash_scalar(prefix, message) % _Q
    commitment = _encode(_multiply(r, _BASE))
    s = (r + _hash_scalar(commitment, public, message) % _Q * scalar) % _Q
    return commitment + s.to_bytes(32, "little")


def verify(public, message, signature):
    """True only for a valid, canonical signature by ``public``; never raises on malformed input."""
    if not isinstance(public, bytes) or not isinstance(signature, bytes) or not isinstance(message, bytes):
        return False
    if len(public) != KEY_BYTES or len(signature) != SIGNATURE_BYTES:
        return False
    point = _decode(public)
    commitment = signature[:32]
    s = int.from_bytes(signature[32:], "little")
    if point is None or s >= _Q or _decode(commitment) is None:
        return False
    if _equal(_multiply(8, point), _IDENTITY):
        return False  # A key of small order verifies for many messages.
    h = _hash_scalar(commitment, public, message) % _Q
    candidate = _add(_multiply(s, _BASE), _negate(_multiply(h, point)))
    return _encode(candidate) == commitment
