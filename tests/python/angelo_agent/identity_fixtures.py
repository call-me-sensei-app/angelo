# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The application identity for tests (scripts/modules/angelo_identity.json), plus the macOS plist values
the build resolves from it, so tests assert against the single source instead of repeating its values."""

import json
import plistlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
IDENTITY_FILE = ROOT / "scripts/modules/angelo_identity.json"
IDENTITY = json.loads(IDENTITY_FILE.read_text(encoding="utf-8"))

APP_PLIST = ROOT / "release/darwin/Blender.app/Contents/Info.plist"
THUMBNAILER_PLIST = ROOT / "release/darwin/Blender.app/Contents/PlugIns/blender-thumbnailer.appex/Contents/Info.plist"


def bundle_values(thumbnailer=False):
    """What source/creator/CMakeLists.txt sets as MACOSX_BUNDLE_* target properties (test_native_app_identity
    asserts the CMake side), keyed by the variable name an Info.plist uses."""
    product, vendor = IDENTITY["product"]["name"], IDENTITY["vendor"]["name"]
    bundle = IDENTITY["bundle"]
    return {
        "MACOSX_BUNDLE_GUI_IDENTIFIER": bundle["macos_id"] + (".thumbnailer" if thumbnailer else ""),
        "MACOSX_BUNDLE_BUNDLE_NAME": product + (" Thumbnailer" if thumbnailer else ""),
        "MACOSX_BUNDLE_COPYRIGHT": IDENTITY["product"]["copyright"],
        # Plain identity variables (angelo_identity.cmake), visible to the plist's configure step.
        "ANGELO_VENDOR_NAME": vendor,
        "ANGELO_MACOS_IMPORTED_DOCUMENT_UTI": bundle["macos_imported_document_uti"],
        "ANGELO_MACOS_MIN_VERSION": bundle["macos_min_version"],
    }


def resolved_plist(path=APP_PLIST, thumbnailer=False):
    """The source Info.plist with the ``${MACOSX_BUNDLE_*}`` values the identity provides filled in.
    Version and executable variables are left as they are (the build sets them)."""
    values = bundle_values(thumbnailer)

    def resolve(node):
        if isinstance(node, str):
            return re.sub(r"\$\{((?:MACOSX_BUNDLE|ANGELO)_[A-Z_]+)\}", lambda m: values.get(m.group(1), m.group(0)), node)
        if isinstance(node, list):
            return [resolve(item) for item in node]
        if isinstance(node, dict):
            return {key: resolve(value) for key, value in node.items()}
        return node

    return resolve(plistlib.loads(Path(path).read_bytes()))
