# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Node socket renames read from Blender's own file versioning (``blenloader/intern/versioning_*.cc``).

When Blender renames a socket it adds a call such as
``version_node_input_socket_name(ntree, SH_NODE_BSDF_PRINCIPLED, "Specular", "Specular IOR Level")`` to the
versioning file of that release, so older files load with the new name. That call is exact evidence of a
rename: a script that writes the old name fails in that release and later. The file names the release
(``versioning_400.cc`` is Blender 4.0); the node's type constant maps to its Python idname through the node's
registration (``sh_node_type_base(&ntype, "ShaderNodeBsdfPrincipled"_ustr, SH_NODE_BSDF_PRINCIPLED)``).

Renames within one release chain (``Clearcoat`` to ``Coat``, then ``Coat`` to ``Coat Weight`` in 4.0): the
old name maps to the release's final name, and a name that only existed between two renames of the same
release (never in a released Blender) is left out.

A call in a function that turns the node into another type (``versioning_replace_legacy_compositor_switch_node``
gives ``CMP_NODE_SWITCH`` the Switch node's socket names, then its ``idname``) is no rename: the old type, kept as
deprecated, still declares its old names.
"""

import re
from pathlib import Path

FIRST_RELEASE = (4, 0)  # Older files hold 2.x and 3.x versioning; scripts rarely carry names from then.
RENAME_CALL = re.compile(
    r"version_node_(input_|output_)?socket_name\(\s*&?\w+\s*,\s*(\w+)\s*,\s*\"([^\"]*)\"\s*,\s*\"([^\"]*)\"\s*\)")
NODE_REGISTRATION = re.compile(r"\w*node_type_base\w*\(\s*&?\w+\s*,\s*\"(\w+)\"(?:_ustr)?\s*,\s*(\w+)\s*\)")
VERSIONING_FILE = re.compile(r"versioning_(\d)(\d)(\d)\.cc$")


def node_idnames(source_root):
    """``{type constant: Python idname}`` from every node registration under ``source/blender/nodes``."""
    idnames = {}
    for path in sorted(Path(source_root, "source/blender/nodes").rglob("*.cc")):
        for idname, constant in NODE_REGISTRATION.findall(path.read_text(encoding="utf-8", errors="replace")):
            idnames.setdefault(constant, idname)
    return idnames


def release_of(path):
    """``(4, 1)`` for ``versioning_410.cc`` or ``versioning_401.cc``; None for files that are not a release's versioning.

    Up to 5.2 a release's file was ``versioning_<major><minor>0`` (``versioning_410.cc`` is 4.1). Blender 5.3 renamed
    them to its short version numbers, ``versioning_<major><two-digit minor>`` (``versioning_401.cc`` is 4.1,
    ``versioning_503.cc`` 5.3; ``0c2ca963600``), and 5.4 keeps that. A last digit of 0 is the old form, any other the
    new; ``versioning_400.cc`` is 4.0 either way. Read with the old form only, 5.3's files dated every 4.1-4.5 and
    5.1-5.3 rename to 4.0 or 5.0.
    """
    match = VERSIONING_FILE.search(Path(path).name)
    if not match:
        return None
    major, tens, units = (int(group) for group in match.groups())
    return major, tens if units == 0 else tens * 10 + units


def replaces_the_node(text, position, constant):
    """Whether the function around ``position`` of a versioning file turns ``constant``'s nodes into another type."""
    body = text[text.rfind("\n}\n", 0, position) + 1:text.find("\n}\n", position)]
    return re.search(r"type_legacy\s*==\s*%s\b[^}]*?STRNCPY\w*\(\s*\w+(?:\.|->)idname\s*," % re.escape(constant), body) is not None


def socket_renames(source_root):
    """Each rename as ``{"release": "4.0", "node": idname, "side": "inputs"|"outputs"|"both", "old", "new"}``.

    In file order within a release, with chains resolved and unreleased intermediate names dropped.
    Calls whose node constant has no registration (removed node types) are reported with ``node: None``.
    """
    idnames = node_idnames(source_root)
    renames = []
    for path in sorted(Path(source_root, "source/blender/blenloader/intern").glob("versioning_*.cc")):
        release = release_of(path)
        if release is None or release < FIRST_RELEASE:
            continue
        calls = []
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in RENAME_CALL.finditer(text):
            side, constant, old, new = match.groups()
            if replaces_the_node(text, match.start(), constant):
                continue
            calls.append({"release": "%d.%d" % release, "constant": constant, "node": idnames.get(constant),
                          "side": {"input_": "inputs", "output_": "outputs"}.get(side, "both"),
                          "old": old, "new": new})
        renames.extend(resolve_chains(calls))
    return renames


def resolve_chains(calls):
    """Within one release, ``a -> b`` then ``b -> c`` (same node and side) become ``a -> c``; ``b`` is dropped."""
    final = {}
    intermediate = set()
    for call in calls:
        key = (call["constant"], call["side"])
        for earlier_old, earlier in list(final.items()):
            if earlier_old[:2] == key and earlier["new"] == call["old"]:
                earlier["new"] = call["new"]
                intermediate.add(key + (call["old"],))
        if key + (call["old"],) not in intermediate:
            final.setdefault(key + (call["old"],), dict(call))
    return [call for old_key, call in final.items() if old_key not in intermediate and call["old"] != call["new"]]
