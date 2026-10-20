#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where Angelo's installers are published: the download layout every release tool shares.

The installers and their checksums are served from one public Cloudflare R2 bucket, reached only through its
custom domain, one folder per version whose files never change once written::

    https://downloads.angelo.studio/<version>/Angelo-<version>-macos-arm64.dmg
    https://downloads.angelo.studio/<version>/Angelo-<version>-windows-x64.msi
    https://downloads.angelo.studio/<version>/SHA256SUMS
    https://downloads.angelo.studio/<version>/Angelo-<older version>-to-<version>-macos-arm64.delta.tar.xz   (optional)
    https://downloads.angelo.studio/<version>/<installer>.sha256   (a platform added after the first publish)

``SHA256SUMS`` covers the files of the version's first publish. A platform published later gets
``<installer>.sha256`` beside its installer, listing the installer and the deltas published with it, so no
published file, ``SHA256SUMS`` included, is rewritten.

``publish_installers.py`` uploads them with the headers below, ``sign_update_feed.py`` names them in the update
feed and ``sign_notarize_macos.py`` records the disk image's address in its receipt. The product name in the file
names is the application identity's (``scripts/modules/angelo_identity.json``). Runbook:
doc/angelo/releasing.rst, "Publishing the installers". Standard library only; no bpy.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import app_version  # noqa: E402  (standard library only)

IDENTITY_FILE = "scripts/modules/angelo_identity.json"
HOST = "downloads.angelo.studio"
BASE_URL = "https://" + HOST
BUCKET = "angelo-downloads"
SUMS = "SHA256SUMS"
# The checksum file of a platform added to a version after its SHA256SUMS: ``<installer>.sha256``, in SHA256SUMS's
# format, listing the installer and the deltas published with it.
CHECKSUM_SUFFIX = ".sha256"
# Every published file is immutable: a version's folder is written once and never changed, so caches may keep
# it for a year, and browsers save it rather than open it.
CACHE_CONTROL = "public, max-age=31536000, immutable"
CONTENT_DISPOSITION = "attachment"
SUMS_CONTENT_TYPE = "text/plain"
# The update feed's platform names (update_feed.PLATFORMS) that have an installer, with its suffix and type.
INSTALLERS = {
    "macos-arm64": (".dmg", "application/x-apple-diskimage"),
    "windows-x64": (".msi", "application/x-msi"),
}
# Platforms whose app can be updated from a delta (update_delta.py, make_update_delta.py), and its suffix and type.
DELTAS = {
    "macos-arm64": (".delta.tar.xz", "application/x-xz"),
}
# The one spelling of a version anything here publishes: ASCII digits, no leading zero (app_version.parse alone
# also takes "01.0.0" or Unicode digits, other spellings of the same version).
VERSION_PATTERN = r"(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]{0,2})"
_CANONICAL = re.compile(VERSION_PATTERN, re.ASCII)


class LayoutError(ValueError):
    pass


def load_identity(path):
    """``(product name, version label)`` from the identity file at ``path``; None for a field it lacks, so a
    malformed file fails only what needs that field, with LayoutError naming it, never at import."""
    try:
        identity = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        identity = {}

    def field(section, name):
        value = identity.get(section) if isinstance(identity, dict) else None
        value = value.get(name) if isinstance(value, dict) else None
        return value if isinstance(value, str) and value else None

    return field("product", "name"), field("version", "label")


PRODUCT, CHECKOUT_VERSION = load_identity(ROOT / IDENTITY_FILE)


def product():
    """The product name the file names carry; LayoutError when the identity file has none."""
    if PRODUCT is None:
        raise LayoutError("%s has no product.name, which every published file name carries" % IDENTITY_FILE)
    return PRODUCT


def checkout_version():
    """The version this checkout builds (the identity's ``version.label``): its release line is the only one that
    publishes it. LayoutError when the identity file has none, or not as MAJOR.MINOR.PATCH."""
    if CHECKOUT_VERSION is None:
        raise LayoutError("%s has no version.label, the version this checkout builds" % IDENTITY_FILE)
    return check_version(CHECKOUT_VERSION)


def check_version(version):
    """``version`` when it is ``MAJOR.MINOR.PATCH`` in ASCII digits with no leading zero; LayoutError otherwise."""
    if not isinstance(version, str) or not _CANONICAL.fullmatch(version) or app_version.parse(version) is None:
        raise LayoutError("the version must be MAJOR.MINOR.PATCH (ASCII digits, no leading zero), as the app "
                          "reports it: %r" % (version,))
    return version


def installer_name(version, platform):
    """``Angelo-<version>-<platform><suffix>``: the only name an installer is published under."""
    if platform not in INSTALLERS:
        raise LayoutError("no installer is published for %r (only %s)" % (platform, ", ".join(INSTALLERS)))
    return "%s-%s-%s%s" % (product(), check_version(version), platform, INSTALLERS[platform][0])


def delta_name(from_version, version, platform):
    """``Angelo-<from>-to-<version>-<platform>.delta.tar.xz``: the name a delta is published under."""
    if platform not in DELTAS:
        raise LayoutError("no delta is published for %r (only %s)" % (platform, ", ".join(DELTAS)))
    if app_version.parse(check_version(from_version)) >= app_version.parse(check_version(version)):
        raise LayoutError("a delta goes from an older version to a newer one, not %s to %s" % (from_version, version))
    return "%s-%s-to-%s-%s%s" % (product(), from_version, version, platform, DELTAS[platform][0])


def is_delta_of(name, version, platform):
    """True when ``name`` is exactly ``delta_name(<some older version>, version, platform)``."""
    if platform not in DELTAS:
        return False
    head, tail = "%s-" % product(), "-to-%s-%s%s" % (version, platform, DELTAS[platform][0])
    if not (name.startswith(head) and name.endswith(tail)):
        return False
    try:
        return delta_name(name[len(head):-len(tail)], version, platform) == name
    except LayoutError:
        return False


def object_key(version, name):
    """The object's key in the bucket: ``<version>/<name>``."""
    return "%s/%s" % (check_version(version), name)


def url(version, name):
    """The public address of a published file."""
    return "%s/%s" % (BASE_URL, object_key(version, name))


def installer_url(version, platform):
    return url(version, installer_name(version, platform))


def _payload_type(name):
    """The type of an installer or a delta, from its suffix; None for any other name."""
    types = {suffix: kind for suffix, kind in list(INSTALLERS.values()) + list(DELTAS.values())}
    return next((kind for suffix, kind in types.items() if name.endswith(suffix)), None)


def _is_installer(name):
    return any(name.endswith(suffix) for suffix, _ in INSTALLERS.values())


def checksum_name(name):
    """``<installer>.sha256``: the checksum file of a platform added to a version already published."""
    if not _is_installer(name):
        raise LayoutError("only an installer has its own checksum file, not %s" % name)
    return name + CHECKSUM_SUFFIX


def headers(name):
    """The headers a published file is stored and served with (its name decides its type)."""
    base = name[:-len(CHECKSUM_SUFFIX)] if name.endswith(CHECKSUM_SUFFIX) else None
    if name == SUMS or (base is not None and _is_installer(base)):
        content_type = SUMS_CONTENT_TYPE
    else:
        content_type = _payload_type(name)
        if content_type is None:
            raise LayoutError("%s is neither an installer, a delta, %s nor a file's checksum" % (name, SUMS))
    return {"Content-Type": content_type, "Cache-Control": CACHE_CONTROL, "Content-Disposition": CONTENT_DISPOSITION}


def sums_text(entries):
    """``SHA256SUMS`` for ``{name: sha256}``: ``<hex>  <name>`` lines sorted by name, as ``shasum -a 256`` writes
    them, so ``shasum -a 256 -c SHA256SUMS`` checks a download. An ``<installer>.sha256`` has the same form."""
    return "".join("%s  %s\n" % (entries[name], name) for name in sorted(entries))


def parse_sums(text):
    """``{name: sha256}`` from text exactly as ``sums_text`` writes it; LayoutError for anything else (another
    form, a name twice, a line that is not ``<64 lower-case hex>  <name>``)."""
    entries = {}
    for line in text.split("\n")[:-1]:
        digest, separator, name = line.partition("  ")
        if (not separator or len(digest) != 64 or digest.strip("0123456789abcdef") or not name
                or name != name.strip() or "/" in name or name in entries):
            raise LayoutError("%r is not a checksum line (<SHA-256>  <name>)" % line[:200])
        entries[name] = digest
    if not entries or sums_text(entries) != text:
        raise LayoutError("not a checksum file as sums_text writes it (sorted lines, each ending in a line feed)")
    return entries
