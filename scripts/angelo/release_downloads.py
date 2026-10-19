#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Where Angelo's installers are published: the download layout every release tool shares.

The installers and their checksums are served from one public Cloudflare R2 bucket, reached only through its
custom domain, one folder per version that never changes once written::

    https://downloads.angelo.studio/<version>/Angelo-<version>-macos-arm64.dmg
    https://downloads.angelo.studio/<version>/Angelo-<version>-windows-x64.msi
    https://downloads.angelo.studio/<version>/SHA256SUMS
    https://downloads.angelo.studio/<version>/Angelo-<older version>-to-<version>-macos-arm64.delta.tar.xz   (optional)

``publish_installers.py`` uploads them with the headers below, ``sign_update_feed.py`` names them in the update
feed and ``sign_notarize_macos.py`` records the disk image's address in its receipt. The product name in the file
names is the application identity's (``scripts/modules/angelo_identity.json``). Runbook:
doc/angelo/releasing.rst, "Publishing the installers". Standard library only; no bpy.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import app_version  # noqa: E402  (standard library only)

PRODUCT = json.loads((ROOT / "scripts/modules/angelo_identity.json").read_text(encoding="utf-8"))["product"]["name"]
HOST = "downloads.angelo.studio"
BASE_URL = "https://" + HOST
BUCKET = "angelo-downloads"
SUMS = "SHA256SUMS"
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


class LayoutError(ValueError):
    pass


def check_version(version):
    """``version`` when it is ``MAJOR.MINOR.PATCH``, as the app and the feed require; LayoutError otherwise."""
    if app_version.parse(version) is None:
        raise LayoutError("the version must be MAJOR.MINOR.PATCH, as the app reports it: %r" % (version,))
    return version


def installer_name(version, platform):
    """``Angelo-<version>-<platform><suffix>``: the only name an installer is published under."""
    if platform not in INSTALLERS:
        raise LayoutError("no installer is published for %r (only %s)" % (platform, ", ".join(INSTALLERS)))
    return "%s-%s-%s%s" % (PRODUCT, check_version(version), platform, INSTALLERS[platform][0])


def delta_name(from_version, version, platform):
    """``Angelo-<from>-to-<version>-<platform>.delta.tar.xz``: the name a delta is published under."""
    if platform not in DELTAS:
        raise LayoutError("no delta is published for %r (only %s)" % (platform, ", ".join(DELTAS)))
    if app_version.parse(check_version(from_version)) >= app_version.parse(check_version(version)):
        raise LayoutError("a delta goes from an older version to a newer one, not %s to %s" % (from_version, version))
    return "%s-%s-to-%s-%s%s" % (PRODUCT, from_version, version, platform, DELTAS[platform][0])


def object_key(version, name):
    """The object's key in the bucket: ``<version>/<name>``."""
    return "%s/%s" % (check_version(version), name)


def url(version, name):
    """The public address of a published file."""
    return "%s/%s" % (BASE_URL, object_key(version, name))


def installer_url(version, platform):
    return url(version, installer_name(version, platform))


def headers(name):
    """The headers a published file is stored and served with (its name decides its type)."""
    if name == SUMS:
        content_type = SUMS_CONTENT_TYPE
    else:
        types = {suffix: kind for suffix, kind in list(INSTALLERS.values()) + list(DELTAS.values())}
        content_type = next((kind for suffix, kind in types.items() if name.endswith(suffix)), None)
        if content_type is None:
            raise LayoutError("%s is neither an installer, a delta nor %s" % (name, SUMS))
    return {"Content-Type": content_type, "Cache-Control": CACHE_CONTROL, "Content-Disposition": CONTENT_DISPOSITION}


def sums_text(entries):
    """``SHA256SUMS`` for ``{name: sha256}``: ``<hex>  <name>`` lines sorted by name, as ``shasum -a 256`` writes
    them, so ``shasum -a 256 -c SHA256SUMS`` checks a download."""
    return "".join("%s  %s\n" % (entries[name], name) for name in sorted(entries))
