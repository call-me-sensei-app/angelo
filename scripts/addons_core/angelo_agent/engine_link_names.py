# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How the engine link tool's OPEN finds the asset it is asked for (doc/angelo/engine_link.rst, "The agent"): the
engine's own name, or the file name a person says when exactly one asset of a connected project has it
(``engine_link_assets.named``); and the bounded reasons an OPEN refusal names. No Blender dependency."""

from . import engine_link_assets as assets

UNSURE = frozenset({"several", "incomplete"})   # AssetError codes of a by-name lookup that can't pick one.
REASONS_BUDGET = 900   # Characters of reasons one OPEN refusal carries; whole reasons only, the rest counted.


def resolve(adapter, project, name):
    """The asset ``name`` names in ``project``: the engine's own name for it, else the file's plain name when exactly
    one asset there has it, as a person says "the barrel" (``engine_link_assets.named``). Raises AssetError; a
    lookup by name that found several or stopped at its bound raises one whose code is in ``UNSURE``."""
    request = {"project": project, "asset": name}
    try:
        return adapter.resolve(request)
    except assets.AssetError as error:
        if error.code != "outside" or any(mark in name for mark in "/\\:") or not adapter.is_project(project):
            raise
    idents, complete = assets.named(adapter, project, name)
    if not complete:   # One it found may not be the only one.
        raise assets.AssetError("incomplete", name, "the project couldn't be read whole to find {asset} by its file "
                                "name (more than %d entries, or some Angelo can't read or name)" % assets.NAME_SCAN)
    if len(idents) == 1:
        return adapter.resolve(dict(request, asset=idents[0]))
    if idents:
        listed, size = [], 0
        for ident in idents:
            size += len(ident) + 2
            if size > assets.NAME_LISTED:
                break
            listed.append(ident.replace("{", "{{").replace("}", "}}"))
        more = len(idents) - len(listed)
        raise assets.AssetError("several", name, "several assets are named {asset}: %s%s"
                                % (", ".join(listed), " and %d more" % more if more else ""))
    raise assets.AssetError("outside", name, "no asset of the project is named {asset}")


def joined(reasons):
    """``reasons`` joined whole within ``REASONS_BUDGET``, the rest counted: never one cut mid-name."""
    if reasons and len(reasons[0]) + 2 > REASONS_BUDGET:   # One alone too long: its start, never nothing.
        reasons = [reasons[0][:REASONS_BUDGET - 5] + "..."] + list(reasons[1:])   # 5: the "..." and a separator.
    kept, size = [], 0
    for reason in reasons:
        size += len(reason) + 2
        if size > REASONS_BUDGET:
            break
        kept.append(reason)
    more = len(reasons) - len(kept)
    return "; ".join(kept) + ("; and %d more" % more if more else "")
