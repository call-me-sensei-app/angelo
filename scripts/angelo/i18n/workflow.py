# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Helpers for producing a catalogue in chunks: vocabulary hints, work files, and merging results back.

A translator (human or model) gets, for one language:

* ``hints(language)``: how Blender's own catalogue renders common UI words in that
  language, so Angelo's wording agrees with the menus around it;
* ``chunks(language, directory)``: the entries still to translate, split into small
  JSON files (message, context, kind, source files);
* ``apply(language, directory)``: merges the translated JSON files back, keeping the
  English template's order and refusing entries that break a placeholder.
"""

import json
from pathlib import Path

from i18n import catalogs, languages, po, sources, validate

BLENDER_PO = sources.ROOT / "locale/po"
# English words that Blender's catalogues translate and Angelo's text uses; the hints show Blender's wording.
BASE_TERMS = ("Scene", "Preview", "Cancel", "Apply", "Undo", "Redo", "Delete", "Remove", "Settings", "Preferences", "Library",
              "Object", "Mesh", "Material", "Animation", "Armature", "Camera", "Light", "Render", "Name", "Search", "Refresh",
              "Copy", "Paste", "Close", "Open", "Save", "Import", "Export", "Project", "Generate", "Image", "Video", "Audio",
              "Texture", "Folder", "File", "Connect", "Disconnect", "Account", "Warning", "Error", "Help", "Details", "Select",
              "Accept", "Discard", "Restore", "Stop", "Continue", "Done", "Back", "Next", "Add", "Edit", "Rename", "Duplicate",
              "Model", "Modifier", "Constraint", "Bone", "Rig", "Edit Mode", "Object Mode", "Sculpt Mode", "Selection",
              "Viewport", "Workspace", "Layout", "Theme", "Language", "Keymap", "Add-ons", "Extensions", "Version", "Update",
              "Size", "Scale", "Rotation", "Location", "Dimensions", "Quality", "Resolution", "Duration", "Frame", "Keyframe",
              "Timeline", "Collection", "Layer", "Group", "Node", "Shader", "Geometry Nodes", "Particles", "Physics", "Simulation",
              "Bake", "Mask", "Smooth", "Subdivision", "UV Map", "Vertex", "Edge", "Face", "Curve", "Text", "Font", "Color",
              "Opacity", "Strength", "Radius", "Symmetry", "Falloff", "Brush", "Undo History", "Memory", "Disk", "Cache",
              "Password", "Sign In", "Log In", "Sign Out", "Log Out", "Online", "Offline", "Free", "Paid", "Total", "Usage")


def hints(language):
    """{English word: Blender's rendering} for one language, in reading order for right-to-left languages.

    A word Blender renders differently as an operator label (its "Operator" context: Close, Continue, Add) is
    given as ``{"text": ..., "button": ...}``: Blender's default-context word may come from another domain
    (a mesh's "Close", math's "Add"), so translators should prefer the button form for actions.
    """
    from angelo_agent import rtl_text  # Local import: pulls no bpy.
    _, entries = po.read(str(BLENDER_PO / f"{language.po}.po"))
    readable = (lambda text: rtl_text.to_logical(text.replace("\ufeff", ""))) if language.rtl else (lambda text: text)
    plain = {entry.id: entry.str for entry in entries if entry.str and not entry.ctxt}
    button = {entry.id: entry.str for entry in entries if entry.str and entry.ctxt == "Operator"}
    result = {}
    for term in BASE_TERMS:
        text, action = plain.get(term), button.get(term)
        if text and action and action != text:
            result[term] = {"text": readable(text), "button": readable(action)}
        elif text or action:
            result[term] = readable(text or action)
    return result


def chunks(language, directory, size=90):
    """Write the entries without a translation as numbered JSON work files; returns the file count."""
    _, template = catalogs.load_template()
    path = catalogs.catalog_path(language)
    done = {e.key for e in po.read(str(path))[1] if e.str.strip()} if path.exists() else set()
    todo = [e for e in template if e.key not in done and (not e.ctxt.startswith("plural:") or e.ctxt[7:] in language.plurals)]
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for start in range(0, len(todo), size):
        part = [{"id": e.id, "ctxt": e.ctxt, "where": sorted({s.split(":", 1)[-1].strip() for s in e.extracted})[:2]}
                for e in todo[start:start + size]]
        (directory / f"chunk-{count:02d}.json").write_text(json.dumps(part, ensure_ascii=False, indent=0), encoding="utf-8")
        count += 1
    return count


def apply(language, directory):
    """Merge ``chunk-NN.out.json`` files (a list of translated strings per chunk, same order) into the catalogue."""
    template_header, template = catalogs.load_template()
    catalogs.update(language, template, template_header)
    path = catalogs.catalog_path(language)
    header, entries = po.read(str(path))
    by_key = {e.key: e for e in entries if not e.obsolete}
    problems, applied = [], 0
    for source_file in sorted(Path(directory).glob("chunk-*.json")):
        if source_file.name.endswith(".out.json"):
            continue
        out_file = source_file.with_name(source_file.stem + ".out.json")
        if not out_file.exists():
            continue
        wanted = json.loads(source_file.read_text(encoding="utf-8"))
        given = json.loads(out_file.read_text(encoding="utf-8"))
        if len(given) != len(wanted):
            problems.append(f"{out_file.name}: {len(given)} translations for {len(wanted)} messages")
            continue
        for item, text in zip(wanted, given):
            entry = by_key.get((item["ctxt"], item["id"]))
            if entry is None or not isinstance(text, str):
                continue
            candidate = po.Entry(item["id"], text, item["ctxt"])
            errors = [i for i in validate.check_entry(language, entry, candidate) if i.severity == "error"]
            if errors:
                problems.append(f"{out_file.name}: {item['id'][:60]!r}: {errors[0].message}")
                continue
            entry.str = text
            applied += 1
    po.write(str(path), header, entries)
    return applied, problems
