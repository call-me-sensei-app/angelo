# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: MIT
"""What an Unreal asset is to the link, without Unreal: which assets Angelo can edit and the ``open`` request for one.

An asset is a mesh asset (StaticMesh or SkeletalMesh) under a project's content, identified by its package path
(``/Game/Props/SM_Crate``) and the source file its import data names. Angelo edits a working .blend and writes that
source file back on save; this editor then re-imports the asset with the asset's own import settings. Angelo checks
everything again on its side; these checks only keep the menu honest.
"""

import os

KINDS = (".glb",)   # Source formats Angelo writes back (unreal_link_assets.KINDS); .fbx once its profile is recorded.
CLASSES = ("StaticMesh", "SkeletalMesh")
REFUSED = {   # code -> English shown in the editor.
    "class": "{name} is a {cls}; Angelo edits static and skeletal meshes",
    "no_source": "{name} has no source file to edit. Re-import it from a .glb first",
    "kind": "{name} was imported from {kind}; Angelo edits assets imported from .glb for now",
    "outside": "{name}'s source file is outside the project folder. Move it into the project and re-import first",
    "several": "{name} was imported from several files; Angelo edits single-file assets",
    "split": "{name}'s source file was imported as {count} separate assets; Angelo writes a file back into one asset, so "
             "it edits a source imported as one (Combine Static Meshes)",
}


def refusal(description, project_root):
    """(code, English) when Angelo can't edit the described asset, else None. ``description`` is editor.describe's."""
    name, cls, sources = description.get("name", "?"), description.get("class", ""), description.get("sources") or []
    if cls not in CLASSES:
        return "class", REFUSED["class"].format(name=name, cls=cls or "?")
    if not sources or not sources[0]:
        return "no_source", REFUSED["no_source"].format(name=name)
    if len([source for source in sources if source]) > 1:
        return "several", REFUSED["several"].format(name=name)
    kind = os.path.splitext(sources[0])[1].lower()
    if kind not in KINDS:
        return "kind", REFUSED["kind"].format(name=name, kind=kind or "a file without an extension")
    if not inside(sources[0], project_root):
        return "outside", REFUSED["outside"].format(name=name)
    count = description.get("assets_from_source", 1)
    if count > 1:
        return "split", REFUSED["split"].format(name=name, count=count)
    return None


def inside(path, root):
    """True when ``path`` resolves inside the folder ``root`` (links followed, case per platform)."""
    try:
        path, root = os.path.normcase(os.path.realpath(path)), os.path.normcase(os.path.realpath(root))
        return os.path.commonpath((path, root)) == root
    except ValueError:   # Different drives.
        return False


def open_request(request_id, description, project_root, project_name, actor=""):
    """The ``open`` request for Angelo. ``asset`` is the package path, the identity both sides use."""
    source = description["sources"][0]
    return {"type": "open", "id": str(request_id), "engine": "unreal", "project": project_root,
            "project_name": project_name, "asset": description["package"], "object": description.get("path", ""),
            "source": source, "kind": os.path.splitext(source)[1].lower(), "class": description.get("class", ""),
            "node": actor}
