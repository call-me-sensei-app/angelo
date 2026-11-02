# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The words a texture-set bake shows a person: channel and packing names, and its approval and card lines.

Shared by the Bake Maps card (``bake_maps_card``), the approval (``job_approvals``)
and the job's chat card (``job_chat``), so each says a channel the same way.
Constants are ``n_``-marked and translated where they are drawn; the model reads
the English identifiers (``bake_maps_spec``), never these.
"""

from .ui_text import iface_, join, plural_, n_

CHANNEL_LABELS = {"BASE_COLOR": n_("Base color"), "METALLIC": n_("Metallic"), "ROUGHNESS": n_("Roughness"),
                  "NORMAL": n_("Normal"), "EMISSION": n_("Emission"), "ALPHA": n_("Alpha"),
                  "AO": n_("Ambient occlusion"), "HEIGHT": n_("Height")}
PACKING_LABELS = {"SEPARATE": n_("Separate maps"), "ORM": n_("ORM packed"), "GLTF": n_("glTF metallic-roughness"),
                  "UNREAL": n_("Unreal Engine"), "UNITY": n_("Unity")}
FORMAT_LABELS = {"PNG": "PNG", "OPEN_EXR": "OpenEXR"}   # i18n: file format names, never translated


def channel_list(channels):
    return join([iface_(CHANNEL_LABELS[channel]) for channel in channels if channel in CHANNEL_LABELS])


def title(facts):
    names = ", ".join(facts.get("targets") or [])
    return plural_(len(facts.get("targets") or [1]), "Bake the texture set of {names}",
                   "Bake the texture sets of {names}", names=names)


def approval_lines(facts, request):
    """What a texture-set bake will bake and write, from its admission facts."""
    lines = []
    for name, channels in (facts.get("channels") or {}).items():
        lines.append(iface_("{object}: {maps}", object=name, maps=channel_list(channels)))
    lines.append(iface_("Packing: {packing}; {format} files", packing=iface_(PACKING_LABELS.get(
        request.get("packing", "SEPARATE"), "")), format=FORMAT_LABELS.get(request.get("format", "PNG"), "")))
    if facts.get("files"):
        lines.append(plural_(facts["files"], "Writes {count} map", "Writes {count} maps"))
    for name, tiles in (facts.get("tiles") or {}).items():
        lines.append(iface_("{object}: UDIM tiles {tiles}", object=name, tiles=", ".join(str(n) for n in tiles)))
    if facts.get("approximated"):
        lines.append(iface_("Read approximately (not a BSDF Angelo knows): {nodes}",
                            nodes=", ".join(facts["approximated"])))
    if facts.get("material"):
        lines.append(iface_("Makes a new baked material per object; nothing is assigned."))
    lines.append(iface_("Bakes into temporary images; your images and materials stay as they are."))
    return lines


def facts_line(record):
    """The chat card's one-line summary: how many maps, of which objects."""
    detail = record["detail"]
    parts = []
    if detail.get("files"):
        parts.append(plural_(detail["files"], "{count} map", "{count} maps"))
    if detail.get("targets"):
        parts.append(", ".join(detail["targets"]))
    if detail.get("packing"):
        parts.append(iface_(PACKING_LABELS.get(detail["packing"], "")))
    return parts


def receipt_lines(receipt):
    lines = []
    if receipt.get("maps"):
        lines.append(iface_("Maps written: {count}", count=receipt["maps"]))
    names = [name for name in receipt.get("materials") or [] if isinstance(name, str)]
    if names:
        lines.append(iface_("Baked materials: {names}", names=", ".join(names)))
    return lines
