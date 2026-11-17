# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The team account's HTTP client for the Angelo console.

One request per connection through ``http.client``: verified TLS for https,
plain http only to a loopback host (a configured development console), no
redirects (a 3xx is an answer, never followed, so a token can't be forwarded),
bounded responses and timeouts. Failures say whether the request certainly
never reached the server (``NotSent``: DNS, refused connection, TLS handshake)
or might have (``Unanswered``), because a refresh token may be presented only
once. Proxies are not used, as for Media Studio's provider calls.

Trust (``tls_context``): Python's default roots, which inside Angelo are the
bundled certifi file Blender names in ``SSL_CERT_FILE`` (Blender's own Python
has no other roots on macOS), or certifi directly when no root is loaded (a
script outside the app), plus the managed policy's ``ExtraCACertificates``
(an organization's TLS-inspection root), which is added, never a replacement.
Whatever root vouched for it, a chain holding a distrusted key (``tls_distrust``:
compromised CA keys, withdrawn roots) fails as a certificate verification error.

Standard library only; no bpy. Called only on worker threads.
"""

import http.client
import json
import os
import ssl
from urllib.parse import urlencode, urlsplit

from . import managed_policy, net_integrity, tls_distrust
from .team_endpoints import is_loopback
from .team_errors import NotSent, Unanswered

TIMEOUT = 20
MAX_RESPONSE = 1024 * 1024
USER_AGENT = "Angelo-Desktop/0.1"


def _built(policy):
    """A verifying client context: default roots (certifi when there are none) plus the policy's extra roots,
    refusing any chain that holds a distrusted key."""
    context = tls_distrust.checked_context(ssl.create_default_context())
    if not context.cert_store_stats().get("x509_ca"):
        try:
            import certifi
            context.load_verify_locations(cafile=certifi.where())
        except (ImportError, OSError, ssl.SSLError):
            pass  # The handshake then fails as NotSent, said as an unverified certificate.
    extra = (policy or managed_policy.current()).extra_ca_certificates
    if extra:
        try:
            context.load_verify_locations(cafile=extra)
        except (OSError, ssl.SSLError):
            pass  # Reported by managed_policy (problems) when the file was unusable at startup.
    context.sslsocket_class = _AnchoredSocket   # The distrust check, then the root it was built with.
    return context


def _fingerprint(context):
    """What makes a context Angelo's: verification, the protocol range, where it would log keys (its roots:
    ``_roots_kept``)."""
    return (context.verify_mode, context.check_hostname, context.verify_flags, context.options,
            context.minimum_version, context.maximum_version, context.keylog_filename, context.sslsocket_class,
            context.sslobject_class, getattr(context, "hostname_checks_common_name", None),
            getattr(context, "security_level", None), context.post_handshake_auth)


def _roots(context):
    return frozenset(context.get_ca_certs(binary_form=True))


_system_folder_roots = None


def _folder_roots():
    """The certificates in the system's certificate folder (``capath``), which OpenSSL loads into a context only as
    a handshake needs one: the only roots a context may gain after it is built."""
    global _system_folder_roots
    if _system_folder_roots is None:
        found = set()
        paths = ssl.get_default_verify_paths()
        for folder in {paths.capath, paths.openssl_capath} - {None}:
            try:
                names = os.listdir(folder)
            except OSError:
                continue
            for name in names:
                try:
                    with open(os.path.join(folder, name), encoding="ascii", errors="replace") as handle:
                        text = handle.read(1 << 20)
                except OSError:
                    continue
                found |= _pem_certificates(text)
        _system_folder_roots = frozenset(found)
    return _system_folder_roots


def _pem_certificates(text):
    """The DER form of every PEM certificate in ``text``."""
    found = set()
    for block in text.split("-----BEGIN CERTIFICATE-----")[1:]:
        try:
            found.add(ssl.PEM_cert_to_DER_cert("-----BEGIN CERTIFICATE-----" + block.split(
                "-----END CERTIFICATE-----")[0] + "-----END CERTIFICATE-----\n"))
        except ValueError:
            pass
    return frozenset(found)


def _file_roots(path):
    """The certificates in an extra-roots file, CA or not: ``get_ca_certs`` lists only CA certificates, and an
    organization may trust a self-signed server certificate directly."""
    if not path:
        return frozenset()
    try:
        with open(path, encoding="ascii", errors="replace") as handle:
            return _pem_certificates(handle.read(16 << 20))
    except OSError:
        return frozenset()


def _anchors(context):
    """The roots ``context`` was built with, when it is one of Angelo's shared contexts; otherwise None."""
    for _extra, held, _fingerprint, roots in _SHARED:
        if held is context:
            return roots
    return None


class _AnchoredSocket(tls_distrust.DistrustCheckingSocket):
    """Angelo's connections: after the handshake (and the distrust check), the verified chain must end at a root the
    context was built with, or one from the system's certificate folder. A root a script added to the context
    (``load_verify_locations``, a ``capath`` OpenSSL reads only during a handshake) can't vouch for a server before
    a request is sent."""

    def do_handshake(self, block=False):
        super().do_handshake(block)
        roots = _anchors(self.context)
        if roots is None:
            return
        try:
            root = self.get_verified_chain()[-1]   # DER bytes (Python 3.13); a certificate object elsewhere.
            if not isinstance(root, bytes):
                root = ssl.PEM_cert_to_DER_cert(root.public_bytes())   # PEM by default.
        except (AttributeError, IndexError, ValueError, ssl.SSLError):
            root = None
        if root is None or (root not in roots and root not in _folder_roots()):
            self.close()
            error = ssl.SSLCertVerificationError("certificate verify failed: its root is not one Angelo trusts")
            error.verify_code, error.verify_message = 0, "root not among Angelo's"
            error.reason, error.library = "CERTIFICATE_VERIFY_FAILED", "SSL"
            raise error


def _roots_kept(context, built):
    """Whether the context's roots are the ones it was built with, plus only what the system's folder loads."""
    gained = _roots(context) - built
    return not gained or gained <= _folder_roots()


# Angelo's context for each set of extra roots, with its fingerprint, built unconfined (before any script runs, or
# outside one). A script that rebinds these is put back when it ends; one that changes the context in place no
# longer matches the fingerprint, so the next request refuses (doc/angelo/script_confinement.rst, "Angelo's own
# I/O").
_SHARED = ()


def tls_context(policy=None):
    """Angelo's verifying client context (``_built``): one per set of extra roots, built once outside any agent
    script and checked at every use. An explicit ``policy`` (a check, a tool outside the app) gets its own."""
    global _SHARED
    if policy is not None:
        return _built(policy)
    net_integrity.check()
    extra = managed_policy.current().extra_ca_certificates
    for held_extra, context, fingerprint, roots in _SHARED:
        if held_extra == extra:
            if _fingerprint(context) != fingerprint or not _roots_kept(context, roots):
                raise net_integrity.Tampered("its TLS context")
            return context
    context = _built(None)
    if not net_integrity.confined():   # Never kept from inside a script: built from what it may have changed.
        _SHARED = _SHARED + ((extra, context, _fingerprint(context), _roots(context) | _file_roots(extra)),)
    return context


def prepare_tls():
    """Build Angelo's context before the first agent script runs (``script_confinement``)."""
    if not net_integrity.confined():
        tls_context()


def http_transport(method, url, headers=None, body=None, timeout=TIMEOUT):
    """``(status, headers with lowercase names, body bytes)`` of one request."""
    parsed = urlsplit(url)
    if parsed.scheme == "https":
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=timeout,
                                                 context=tls_context())
    elif parsed.scheme == "http" and is_loopback(parsed.hostname or ""):
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout)
    else:
        raise NotSent("Only https, or http to a loopback development console, is used")
    try:
        try:
            connection.connect()
        except ssl.SSLCertVerificationError:   # Before OSError, its base: the server is there, its certificate is not
            failure = NotSent("Couldn't verify the server's certificate")   # one Angelo trusts (team_errors.UNTRUSTED).
            failure.untrusted = True
            raise failure from None
        except (OSError, ValueError):
            raise NotSent("Couldn't connect") from None
        try:
            path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
            connection.request(method, path, body=body, headers=dict(headers or {}, **{"User-Agent": USER_AGENT}))
            response = connection.getresponse()
            data = response.read(MAX_RESPONSE + 1)
        except (OSError, ValueError, http.client.HTTPException):
            raise Unanswered("No complete answer") from None
        if len(data) > MAX_RESPONSE:
            raise Unanswered("Oversized answer")
        return response.status, {name.lower(): value for name, value in response.getheaders()}, data
    finally:
        connection.close()


def form_body(values):
    return urlencode(values).encode("ascii"), {"Content-Type": "application/x-www-form-urlencoded",
                                              "Accept": "application/json"}


def json_object(data):
    """A JSON object from a response body, or None."""
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None
