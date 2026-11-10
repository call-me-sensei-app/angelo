# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The signed update feed: envelope, signature, payload rules and the offer.

Contract and release procedure: doc/angelo/updates_and_reports.rst. The feed
is one small JSON file signed with an Ed25519 key that never leaves Google Cloud
KMS (``scripts/angelo/sign_update_feed.py``); the matching public keys ship in
``update_keys.json``. A build that ships none refuses every feed.

Envelope (``stable.json``, served at https://angelo.studio/updates/v1/stable.json)::

    {"format": "angelo-update-feed", "version": 1, "key_id": "2026-10",
     "payload": "<base64 of the payload's exact UTF-8 bytes>",
     "signature": "<base64 Ed25519 signature of SIGNING_CONTEXT + payload bytes>"}

The signature covers bytes, not a re-serialized object, so there is no
canonicalization to get wrong. Everything below is **fail closed**: an unknown
key, a bad signature, an older ``sequence`` than one already accepted (rollback),
an expired feed, an unknown field type, or a URL that is not https on an
Angelo host is an error, never a partial offer. A feed that parses is the
release manager's word; the host check is defence in depth.

A platform's download may list **deltas**: smaller packages that turn one named
older version's installed app into this one (``update_delta.py``). They are
optional: a client uses one only when its ``from`` is the running version and
its ``format`` is one it knows, and falls back to the full download on any
problem. ``latest.automatic_install`` (default true) lets a release say that
Angelo must not install it by itself (the person is then sent to the download,
as before); a later feed can lift that without a new build.

``lines`` lists the newest release of each other release line (MAJOR.MINOR).
``latest`` stays the newest release of the line older builds are on, because
the builds before 1.0.9 read only ``latest``: they keep updating to it. A build
that reads ``lines`` takes its own line's release as an ordinary update and
always presents a newer line beside it as a choice (``Offer.later``): nothing of
that line is downloaded until the person says so. A line's ``recommended`` (default
false) marks it as the recommended move; it never installs anything by itself.

Standard library only; no bpy. Called on the update worker thread.
"""

import base64
import binascii
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import app_version, ed25519

KEYS_FILE = Path(__file__).resolve().parent / "update_keys.json"
FORMAT = "angelo-update-feed"
SIGNING_CONTEXT = b"angelo-update-feed-v1\n"
MAX_ENVELOPE = 64 * 1024
MAX_PAYLOAD = 32 * 1024
# Where a feed may point the person: angelo.studio and its subdomains, and github.com itself (release assets).
# A leading dot means "this domain and its subdomains"; otherwise the host must match exactly.
LINK_HOSTS = (".angelo.studio", "github.com")
# Where crash reports may be sent: Angelo's own services only.
SERVICE_HOSTS = (".angelo.studio",)
# Where Angelo installs an update from by itself: only the release downloads host (the bucket the release tools
# publish to). A link elsewhere (a GitHub release asset, which anyone can publish) is offered as a download only.
INSTALL_HOSTS = ("downloads.angelo.studio",)
PLATFORMS = ("macos-arm64", "macos-x64", "windows-x64", "windows-arm64", "linux-x64")
MAX_HIGHLIGHTS = 5
MAX_DELTAS = 8
MAX_LINES = 4
_FORMAT_NAME = re.compile(r"[a-z0-9][a-z0-9.-]{0,39}")
_HEX64 = re.compile(r"[0-9a-f]{64}")
_UNSAFE = re.compile(r"[\x00-\x1f\x7f  ‪-‮⁦-⁩]")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class FeedError(Exception):
    """A feed that must not be used. ``code`` is stable (logs, tests); the message is for logs only."""

    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code


@dataclass(frozen=True)
class Delta:
    """A package that turns the installed ``source`` version into the release (``update_delta``)."""

    source: str     # The "from" version, MAJOR.MINOR.PATCH.
    url: str
    sha256: str
    size: int
    format: str


@dataclass(frozen=True)
class Download:
    url: str
    sha256: str
    size: int
    deltas: tuple = ()   # (Delta, …) in the feed's order.

    def delta_from(self, version, formats):
        """The delta that starts from ``version`` in one of ``formats``, or None."""
        return next((d for d in self.deltas if d.source == version and d.format in formats), None)


@dataclass(frozen=True)
class Release:
    version: str
    title: str
    summary: str
    highlights: tuple
    notes_url: str
    critical: bool
    released: str
    downloads: tuple   # ((platform, Download), …)
    automatic_install: bool = True   # False: never installed by Angelo itself; the person downloads it.
    recommended: bool = False        # In ``lines``: the signed feed recommends moving to this line.

    def download_for(self, platform):
        return dict(self.downloads).get(platform)


@dataclass(frozen=True)
class Feed:
    sequence: int
    issued: str
    expires: str
    channel: str
    latest: Release
    minimum_supported: str
    crash_reports_url: str
    lines: tuple = ()   # (Release, …): the newest release of each line other than ``latest``'s.


@dataclass(frozen=True)
class Offer:
    """What the person is told: a newer version, and whether this one is no longer supported."""

    release: Release
    download: object   # Download for this platform, or None (the notes page is then the link)
    critical: bool
    unsupported: bool
    choice: bool = False   # A newer line: the person decides; Angelo fetches nothing until they do.
    own_skipped: bool = False   # "Skip this version" on the running line's update, with a newer line still to show.
    later: object = None   # With an update of the running line, the newer line (an Offer with ``choice``) beside it.

    @property
    def newer_line(self):
        """The newer line this offer presents, or None."""
        return self if self.choice else self.later


def line_of(version):
    """The release line of a ``MAJOR.MINOR.PATCH`` version: ``1.0`` for 1.0.9."""
    return ".".join(version.split(".")[:2])


def platform_key(system=None, machine=None):
    """This computer's feed platform key, or "" for a platform the feed has no builds for."""
    system = (system or sys.platform).lower()
    if machine is None:
        import platform
        machine = platform.machine()
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x64", "amd64": "x64"}.get(machine.lower(), "")
    name = "macos" if system == "darwin" else "windows" if system.startswith(("win", "cygwin")) else "linux"
    key = name + "-" + arch
    return key if key in PLATFORMS else ""


def load_keys(path=KEYS_FILE):
    """``{key_id: public key bytes}`` of the usable keys; revoked ones are left out. Empty: no key is configured."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        entries = document["keys"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    keys = {}
    for entry in entries if isinstance(entries, list) else ():
        try:
            key = base64.b64decode(entry["public_key"], validate=True)
            if entry.get("status", "active") != "revoked" and len(key) == ed25519.KEY_BYTES:
                keys[str(entry["id"])] = key
        except (KeyError, TypeError, ValueError, binascii.Error):
            continue
    return keys


def _b64(value, field):
    try:
        return base64.b64decode(value, validate=True)
    except (TypeError, ValueError, binascii.Error):
        raise FeedError("malformed", "%s is not base64" % field) from None


def verify_envelope(raw, keys):
    """The payload bytes of a correctly signed envelope; raises ``FeedError``."""
    if not keys:
        raise FeedError("no_keys", "No update signing key is configured in this build")
    if not isinstance(raw, bytes) or len(raw) > MAX_ENVELOPE:
        raise FeedError("too_large", "The feed is larger than %d bytes" % MAX_ENVELOPE)
    try:
        envelope = json.loads(raw)
    except (ValueError, UnicodeError):
        raise FeedError("malformed", "The feed is not JSON") from None
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT or envelope.get("version") != 1:
        raise FeedError("malformed", "Not an Angelo update feed (version 1)")
    key = keys.get(envelope.get("key_id"))
    if key is None:
        raise FeedError("unknown_key", "The feed names a key this build does not trust")
    payload, signature = _b64(envelope.get("payload"), "payload"), _b64(envelope.get("signature"), "signature")
    if len(payload) > MAX_PAYLOAD:
        raise FeedError("too_large", "The payload is larger than %d bytes" % MAX_PAYLOAD)
    if not ed25519.verify(key, SIGNING_CONTEXT + payload, signature):
        raise FeedError("bad_signature", "The signature does not match")
    return payload


def _text(value, field, limit, required=True):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > limit or _UNSAFE.search(value):
        raise FeedError("invalid", "%s is not usable text (at most %d characters)" % (field, limit))
    return value.strip()


def _url(value, field, hosts):
    text = _text(value, field, 2048)
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if (parts.scheme != "https" or parts.username or parts.password or parts.port not in {None, 443}
            or not any(allowed_host(host, h) for h in hosts) or " " in text):
        raise FeedError("invalid", "%s must be an https link on %s" % (field, ", ".join(h.lstrip(".") for h in hosts)))
    return text


def installable(url):
    """True when Angelo may install the file at ``url`` itself (``INSTALL_HOSTS``; the feed checked the rest)."""
    return (urlsplit(url).hostname or "").lower() in INSTALL_HOSTS


def install_source(url, mirror=""):
    """Where Angelo downloads the installable file the feed names at ``url``.

    Angelo's own download host, or, with the policy's ``UpdateFeedURL`` mirror, the same path beside the mirrored
    feed (``https://it.example/angelo/stable.json`` and ``/1.0.10/Angelo.dmg`` give
    ``https://it.example/angelo/1.0.10/Angelo.dmg``), so a network that reaches only the mirror still updates. The
    feed's size and SHA-256 and the installer's code signature are checked whichever host served it.
    """
    if not mirror or not installable(url):
        return url
    parts = urlsplit(url)
    return mirror.rsplit("/", 1)[0] + "/" + parts.path.lstrip("/") + ("?" + parts.query if parts.query else "")


def allowed_host(host, rule):
    """``rule`` ``.angelo.studio`` admits angelo.studio and its subdomains; ``github.com`` only itself."""
    if rule.startswith("."):
        return host == rule[1:] or host.endswith(rule)
    return host == rule


def _stamp(value, field):
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        raise FeedError("invalid", "%s must look like 2026-10-20T09:00:00Z" % field)
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _version(value, field):
    if app_version.parse(value) is None:
        raise FeedError("invalid", "%s must be MAJOR.MINOR.PATCH" % field)
    return value


def _artifact(value, field):
    """``(url, sha256, size)`` of a described file; raises for any bad field."""
    if not isinstance(value, dict):
        raise FeedError("invalid", "%s must be an object" % field)
    size = value.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= 8 * 1024 ** 3:
        raise FeedError("invalid", "%s.size" % field)
    digest = value.get("sha256")
    if not isinstance(digest, str) or not _HEX64.fullmatch(digest):
        raise FeedError("invalid", "%s.sha256" % field)
    return _url(value.get("url"), "%s.url" % field, LINK_HOSTS), digest, size


def _deltas(value, platform, version):
    field = "downloads.%s.deltas" % platform
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > MAX_DELTAS:
        raise FeedError("invalid", "%s is a list of at most %d packages" % (field, MAX_DELTAS))
    deltas, sources = [], set()
    for index, item in enumerate(value):
        where = "%s[%d]" % (field, index)
        url, digest, size = _artifact(item, where)
        source = _version(item.get("from"), where + ".from")
        if app_version.parse(source) >= app_version.parse(version) or source in sources:
            raise FeedError("invalid", "%s.from must be an older version, listed once" % where)
        kind = item.get("format")
        if not isinstance(kind, str) or not _FORMAT_NAME.fullmatch(kind):
            raise FeedError("invalid", where + ".format")
        sources.add(source)
        deltas.append(Delta(source, url, digest, size, kind))
    return tuple(deltas)


def _download(value, platform, version):
    url, digest, size = _artifact(value, "downloads.%s" % platform)
    return Download(url, digest, size, _deltas(value.get("deltas"), platform, version))


def _release(value):
    if not isinstance(value, dict):
        raise FeedError("invalid", "latest must be an object")
    version = _version(value.get("version"), "latest.version")
    highlights = value.get("highlights", [])
    if not isinstance(highlights, list) or len(highlights) > MAX_HIGHLIGHTS:
        raise FeedError("invalid", "latest.highlights is at most %d lines" % MAX_HIGHLIGHTS)
    downloads = value.get("downloads", {})
    if not isinstance(downloads, dict) or any(key not in PLATFORMS for key in downloads):
        raise FeedError("invalid", "latest.downloads names an unknown platform")
    critical = value.get("critical", False)
    if not isinstance(critical, bool):
        raise FeedError("invalid", "latest.critical must be true or false")
    automatic = value.get("automatic_install", True)
    if not isinstance(automatic, bool):
        raise FeedError("invalid", "latest.automatic_install must be true or false")
    recommended = value.get("recommended", False)
    if not isinstance(recommended, bool):
        raise FeedError("invalid", "recommended must be true or false")
    released = value.get("released", "")
    if released:
        _stamp(str(released) + "T00:00:00Z", "latest.released")
    return Release(
        version=version, title=_text(value.get("title", "Angelo " + app_version.display(version)), "latest.title", 80),
        summary=_text(value.get("summary"), "latest.summary", 300, required=False),
        highlights=tuple(_text(line, "latest.highlights", 140) for line in highlights),
        notes_url=_url(value.get("notes_url"), "latest.notes_url", LINK_HOSTS), critical=critical,
        released=released, automatic_install=automatic, recommended=recommended,
        downloads=tuple((key, _download(item, key, version)) for key, item in sorted(downloads.items())))


def _lines(value, latest):
    if value is None:
        return ()
    if not isinstance(value, list):
        raise FeedError("invalid", "lines is a list")
    releases = []
    for item in value:
        try:
            releases.append(_release(item))
        except FeedError:
            continue   # A later line may use what this build can't read yet; its own updates must not stop for that.
    seen = [line_of(latest.version)]
    for release in releases:
        line = line_of(release.version)
        if line in seen:
            raise FeedError("invalid", "lines has more than one release of %s" % line)
        seen.append(line)
    return tuple(sorted(releases, key=lambda item: app_version.parse(item.version))[-MAX_LINES:])


def parse_payload(payload, now, last_sequence=0):
    """A ``Feed`` from verified payload bytes; raises ``FeedError`` for rollback, expiry or any bad field."""
    try:
        document = json.loads(payload)
    except (ValueError, UnicodeError):
        raise FeedError("malformed", "The payload is not JSON") from None
    if not isinstance(document, dict) or document.get("schema") != 1:
        raise FeedError("malformed", "Unsupported payload schema")
    if document.get("channel") != "stable":
        raise FeedError("invalid", "Only the stable channel exists in 1.0")
    sequence = document.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise FeedError("invalid", "sequence must be a positive integer")
    if sequence < last_sequence:
        raise FeedError("rollback", "Feed %d is older than the accepted feed %d" % (sequence, last_sequence))
    _stamp(document.get("issued"), "issued")
    expires = document.get("expires", "")
    if expires and _stamp(expires, "expires") < now:
        raise FeedError("expired", "The feed expired on " + expires)
    services = document.get("services", {})
    if not isinstance(services, dict):
        raise FeedError("invalid", "services must be an object")
    crash = services.get("crash_reports")
    latest = _release(document.get("latest"))
    return Feed(sequence=sequence, issued=document["issued"], expires=expires, channel="stable",
                latest=latest, lines=_lines(document.get("lines"), latest),
                minimum_supported=_version(document.get("minimum_supported", "1.0.0"), "minimum_supported"),
                crash_reports_url=_url(crash, "services.crash_reports", SERVICE_HOSTS) if crash else "")


def open_feed(raw, keys, now=None, last_sequence=0):
    """Verify and parse a downloaded feed in one step (the only way the app reads one)."""
    return parse_payload(verify_envelope(raw, keys), now or datetime.now(timezone.utc), last_sequence)


def line_key(line):
    """``(major, minor)`` of a release line, or None."""
    parsed = app_version.parse(str(line) + ".0")
    return parsed and parsed[:2]


def offer_for(feed, current, platform, accepted_line="", above=""):
    """The ``Offer`` for a running version, or None when it is up to date (or newer than the feed).

    A newer ``latest`` or a newer release of the running line is an ordinary update. A release of a newer line is a
    choice: the offer itself when the running line has nothing newer, else in ``Offer.later`` beside the update, so
    the person always sees both. Of several newer lines that is the one they said yes to (``accepted_line``), else
    the recommended one, else the newest; with ``above``, only lines newer than that one (they stay off it).
    """
    running = app_version.parse(current)
    if running is None:
        return None
    newer = [release for release in (feed.latest, *feed.lines) if app_version.parse(release.version) > running]
    ordinary = [release for release in newer
                if release is feed.latest or line_of(release.version) == line_of(current)]
    other = [release for release in newer if not any(release is item for item in ordinary)]

    def newest(releases):
        return max(releases, key=lambda item: app_version.parse(item.version)) if releases else None

    unsupported = running < app_version.parse(feed.minimum_supported)
    update = newest(ordinary)
    other = [release for release in other if (not above or line_key(line_of(release.version)) > line_key(above)) and (
        update is None or app_version.parse(release.version) > app_version.parse(update.version))]
    accepted = [release for release in other if line_of(release.version) == accepted_line]
    choice = newest(accepted or [release for release in other if release.recommended] or other)
    choice = choice and Offer(choice, choice.download_for(platform), unsupported, unsupported, True)
    if update is None:
        return choice
    return Offer(update, update.download_for(platform), update.critical or unsupported, unsupported, later=choice)
