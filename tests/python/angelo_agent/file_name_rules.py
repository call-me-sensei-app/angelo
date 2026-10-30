# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which spellings of a file name name the same file on the volume a folder is on, read from the volume itself.

``test_blender_restore_keeps_file`` checks that no other spelling of the person's file writes it before the person
confirms, and that every spelling naming another file is written as usual. Which spellings are the file is the
volume's rule, never the platform's: APFS (by default) folds case and Unicode normalization, NTFS folds case only, a
case-sensitive APFS or ext4 volume folds neither. The suite first assumed macOS's rule, and its NFC/NFD checks failed
on Windows, where ``café`` written as NFD is another file (the 1.0.7 candidate on build4). Each rule is probed with a
real file in the folder, so the expectation matches the volume the test runs on. Standard library only; host-tested
by ``test_native_file_name_rules``.
"""

import os
import unicodedata
import uuid


def _folds(folder, written, other):
    """Whether a file written as ``written`` in ``folder`` is found as ``other`` (the same file)."""
    probe = os.path.join(str(folder), written)
    with open(probe, "wb"):
        pass
    try:
        alias = os.path.join(str(folder), other)
        return os.path.exists(alias) and os.path.samefile(probe, alias)
    finally:
        os.unlink(probe)


def folds_case(folder):
    """Whether names differing only in case are one file on this volume (non-ASCII letters included)."""
    tag = uuid.uuid4().hex[:8]
    return _folds(folder, "probe-é-%s.blend" % tag, "PROBE-É-%s.blend" % tag)


def folds_normalization(folder):
    """Whether NFC and NFD spellings of a name are one file on this volume."""
    tag = uuid.uuid4().hex[:8]
    name = "probe-é-%s.blend" % tag
    return _folds(folder, unicodedata.normalize("NFC", name), unicodedata.normalize("NFD", name))


def spellings(path):
    """Other spellings of ``path``'s name in its folder, as ``{label: (path, same_file)}``: ``same_file`` is whether
    this volume resolves that spelling to ``path``'s file."""
    folder, name = os.path.split(str(path))
    stem, extension = os.path.splitext(name)
    variants = {"nfd": unicodedata.normalize("NFD", name), "nfc": unicodedata.normalize("NFC", name),
                "case": stem.upper() + extension}
    case, normalization = folds_case(folder), folds_normalization(folder)
    rule = {"nfd": normalization, "nfc": normalization, "case": case}
    return {label: (os.path.join(folder, spelled), rule[label]) for label, spelled in variants.items()
            if spelled != name}
