# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Keys Angelo never accepts in a verified TLS chain, whatever root store vouched for them.

A root store says which anchors to trust; it cannot say "except these". When a certificate authority's keys
are compromised, the operating systems block the bad certificates in their own verifiers (Apple's revocation
service, Microsoft's Disallowed store), but Python's ``ssl`` uses OpenSSL with plain anchors: the bundled
certifi file (macOS, and Windows through ``SSL_CERT_FILE``) plus, on Windows, every certificate of the
system's ROOT and CA stores, and none of those block lists. A cross-signed intermediate also reaches the
same key through a second root (the eMudhra keys of October 2026 chain to emSign ECC Root CA - G3 and,
through Sectigo cross-signs, to AAA Certificate Services, which the Windows and macOS stores still hold).

So ``distrusted_keys.json`` lists the subjectPublicKeyInfo SHA-256 of every key Angelo refuses, and
``install`` (at add-on registration) makes every SSL context in the process refuse a handshake whose verified
chain holds one, as a certificate verification failure (``ssl.SSLCertVerificationError``); ``checked_context``
does it for one context (``team_http.tls_context``, also outside the app). The same list is what the release check
(``scripts/angelo/trust_store.py``) requires to be absent from every trust store the app ships, so the bundled
roots and this check agree. Add a key here when a CA key is compromised or a root is withdrawn for
misissuance; never remove one while a certificate with it can still be valid.

Standard library only; no bpy.
"""

import hashlib
import json
import os
import ssl

LIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "distrusted_keys.json")
_KEYS = None


def _read_tlv(data, offset):
    """``(tag, start of contents, end of contents)`` of the DER element at ``offset``."""
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        if not 0 < count <= 4:
            raise ValueError("unsupported DER length")
        length = int.from_bytes(data[offset:offset + count], "big")
        offset += count
    end = offset + length
    if end > len(data):
        raise ValueError("truncated DER")
    return tag, offset, end


def spki_der(certificate_der):
    """The DER subjectPublicKeyInfo of an X.509 certificate (RFC 5280, 4.1)."""
    _, start, _ = _read_tlv(certificate_der, 0)            # Certificate
    tag, start, end = _read_tlv(certificate_der, start)    # tbsCertificate
    if tag != 0x30:
        raise ValueError("not a certificate")
    offset = start
    fields = []
    while offset < end and len(fields) < 7:
        tag, contents, element_end = _read_tlv(certificate_der, offset)
        fields.append((tag, offset, element_end))
        offset = element_end
    if fields and fields[0][0] == 0xA0:                    # [0] version is optional
        fields = fields[1:]
    if len(fields) < 6 or fields[5][0] != 0x30:
        raise ValueError("no subjectPublicKeyInfo")
    _, element_start, element_end = fields[5]
    return certificate_der[element_start:element_end]


def subject_name(certificate_der):
    """The contents of an X.509 certificate's subject Name (its RDNs, without the outer SEQUENCE): what a
    compiled-in trust anchor (webpki-roots) stores beside the key, and what any copy of the certificate holds."""
    _, start, _ = _read_tlv(certificate_der, 0)
    _, start, end = _read_tlv(certificate_der, start)
    offset, fields = start, []
    while offset < end and len(fields) < 7:
        tag, contents, element_end = _read_tlv(certificate_der, offset)
        fields.append((tag, contents, element_end))
        offset = element_end
    if fields and fields[0][0] == 0xA0:
        fields = fields[1:]
    if len(fields) < 6 or fields[4][0] != 0x30:
        raise ValueError("no subject")
    _, contents, element_end = fields[4]
    return certificate_der[contents:element_end]


def spki_sha256(certificate_der):
    return hashlib.sha256(spki_der(certificate_der)).hexdigest()


def key_bits(spki):
    """The key bytes inside a DER subjectPublicKeyInfo's BIT STRING: what a compiled-in trust anchor holds."""
    _, start, _ = _read_tlv(spki, 0)
    _, _, algorithm_end = _read_tlv(spki, start)
    tag, bits_start, bits_end = _read_tlv(spki, algorithm_end)
    if tag != 0x03:
        raise ValueError("no subjectPublicKey")
    return spki[bits_start + 1:bits_end]                   # Skip the unused-bits byte.


def entries(path=LIST):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)["keys"]


def distrusted(path=None):
    """The set of distrusted SPKI SHA-256 hex digests (read once)."""
    global _KEYS
    if path is not None:
        return frozenset(entry["spki_sha256"] for entry in entries(path))
    if _KEYS is None:
        _KEYS = frozenset(entry["spki_sha256"] for entry in entries())
    return _KEYS


def chain_problem(chain_der, keys=None):
    """The subject index and digest of the first certificate in ``chain_der`` whose key is distrusted, or None."""
    keys = distrusted() if keys is None else keys
    for index, certificate in enumerate(chain_der):
        try:
            digest = spki_sha256(certificate)
        except (ValueError, IndexError):
            return index, None                             # Unreadable: never vouch for it.
        if digest in keys:
            return index, digest
    return None


def check_connection(connection):
    """Refuse ``connection`` (an SSLSocket or SSLObject that just finished its handshake) when its verified chain
    holds a distrusted key; raises ``ssl.SSLCertVerificationError``."""
    if connection.context.verify_mode == ssl.CERT_NONE or connection.server_side:
        return                                             # A server's client certificates are its own policy.
    if getattr(connection, "session_reused", False):
        return                                             # Its chain was checked when the session was made.
    verified_chain = getattr(connection, "get_verified_chain", None)
    if verified_chain is None:
        return                                             # Python before 3.13 cannot tell (never Angelo's own).
    try:
        chain = [certificate if isinstance(certificate, bytes) else certificate.public_bytes(ssl.ENCODING_DER)
                 for certificate in verified_chain()]
    except (AttributeError, ValueError, ssl.SSLError):
        chain = None
    problem = chain_problem(chain) if chain else (0, None)   # An unreadable chain is never vouched for.
    if problem is not None:
        error = ssl.SSLCertVerificationError(
            "certificate verify failed: the chain holds a distrusted key (tls_distrust)")
        error.verify_code = 0
        error.verify_message = "distrusted key"
        error.reason, error.library = "CERTIFICATE_VERIFY_FAILED", "SSL"   # As OpenSSL's own failures carry.
        raise error


class DistrustCheckingSocket(ssl.SSLSocket):
    """An ``SSLSocket`` that refuses a verified chain holding a distrusted key, right after the handshake."""

    def do_handshake(self, block=False):
        super().do_handshake(block)
        try:
            check_connection(self)
        except ssl.SSLCertVerificationError:
            self.close()
            raise


class DistrustCheckingObject(ssl.SSLObject):
    """The same check for memory-BIO connections (asyncio, ``wrap_bio``)."""

    def do_handshake(self):
        super().do_handshake()
        check_connection(self)


def checked_context(context):
    """``context`` made to refuse distrusted keys (every connection it makes); returns it."""
    context.sslsocket_class = DistrustCheckingSocket
    context.sslobject_class = DistrustCheckingObject
    return context


def install():
    """Every SSL context in this process refuses distrusted keys: Blender's extension downloads, add-ons,
    ``urllib``, ``http.client``, urllib3 / requests and asyncio, not only Angelo's own ``tls_context``. On
    Windows those contexts also hold the system's ROOT and CA stores as plain anchors. The bundled
    ``sitecustomize.py`` calls it after the first ``import ssl`` of every bundled interpreter (Blender's own
    included, children too); the add-on's ``register`` calls it again; repeating it is harmless. A handshake
    left to the first read or write (``do_handshake_on_connect=False`` without ``do_handshake()``) is not
    checked; nothing in Angelo or Blender connects that way."""
    ssl.SSLContext.sslsocket_class = DistrustCheckingSocket
    ssl.SSLContext.sslobject_class = DistrustCheckingObject
