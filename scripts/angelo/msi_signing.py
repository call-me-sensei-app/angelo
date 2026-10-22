# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Whether a Windows release's MSI is signed: declared by the release, recorded by its build, never inferred.

The release build (build_release_windows.ps1) writes ``<msi>.signing.json`` for exactly the MSI's bytes, as its last
gate: ``signed`` only with -Sign and after the MSI and the app it installs passed the publisher check
(release_checks.py authenticode-publisher), ``unsigned`` only without -Sign and with neither of them carrying a
signature. publish_installers.py takes the operator's ``--msi-signing unsigned|signed`` for the release and refuses
an MSI whose record, declaration and own signature stream disagree, so a release is never published as the other
kind by a missing flag, a stale file or a rebuilt MSI.

Standard library only.
"""

import json
from pathlib import Path

KIND = "angelo-msi-signing-1"
MODES = ("unsigned", "signed")


def record_path(msi):
    msi = Path(msi)
    return msi.with_name(msi.name + ".signing.json")


def _sha256(path):
    import hashlib
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write(msi, signing):
    """Write ``msi``'s record: it is ``signing`` (``unsigned`` or ``signed``). The caller has checked that it is."""
    if signing not in MODES:
        raise ValueError("signing must be one of %s, not %r" % (", ".join(MODES), signing))
    msi = Path(msi)
    data = {"kind": KIND, "installer": msi.name, "sha256": _sha256(msi), "signing": signing}
    record_path(msi).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return data


def problem(msi, declared, has_signature):
    """None when the release's ``declared`` signing, ``msi``'s record and whether ``msi`` carries an Authenticode
    signature (``has_signature``) agree; else what disagrees."""
    msi = Path(msi)
    if declared not in MODES:
        return "declare the MSI's signing with --msi-signing unsigned or --msi-signing signed (never inferred)"
    path = record_path(msi)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ("%s has no signing record (%s): the release build writes it as its last gate; publish only the "
                "build's own MSI" % (msi.name, path.name))
    if not isinstance(data, dict) or data.get("kind") != KIND or data.get("installer") != msi.name:
        return "%s is not a signing record for %s" % (path.name, msi.name)
    if data.get("sha256") != _sha256(msi):
        return "%s records other bytes than %s: the MSI changed after its build's last gate" % (path.name, msi.name)
    if data.get("signing") != declared:
        return "%s was built %s (%s), but this release declares it %s" % (msi.name, data.get("signing"), path.name,
                                                                        declared)
    if bool(has_signature) != (declared == "signed"):
        return "%s %s an Authenticode signature, but it is declared %s" % (
            msi.name, "carries" if has_signature else "lacks", declared)
    return None
