# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Editor-picker presentation. Coding Agent is not the Python Console option."""

CODING_AGENT = "CODING_AGENT"
GENERATIVE_AI = "GENERATIVE_AI"
EDITOR_GROUPS = (
    ("General", (
        ("VIEW_3D", "3D Viewport", "VIEW3D"),
        ("IMAGE_EDITOR", "Image Editor", "IMAGE"),
        ("UV", "UV Editor", "UV"),
        ("GeometryNodeTree", "Geometry Node Editor", "GEOMETRY_NODES"),
        ("CompositorNodeTree", "Compositor", "NODE_COMPOSITING"),
        ("ShaderNodeTree", "Shader Editor", "NODE_MATERIAL"),
        ("TextureNodeTree", "Texture Node Editor", "NODE_TEXTURE"),
        ("SEQUENCE_EDITOR", "Video Sequencer", "SEQUENCE"),
        ("CLIP_EDITOR", "Movie Clip Editor", "TRACKER"),
    )),
    ("Animation", (
        ("DOPESHEET", "Dope Sheet", "ACTION"),
        ("TIMELINE", "Timeline", "TIME"),
        ("FCURVES", "Graph Editor", "GRAPH"),
        ("DRIVERS", "Drivers", "DRIVER"),
        ("NLA_EDITOR", "Nonlinear Animation", "NLA"),
    )),
    ("Scripting", (
        ("TEXT_EDITOR", "Text Editor", "TEXT"),
        ("CONSOLE", "Python Console", "CONSOLE"),
        ("INFO", "Info", "INFO"),
    )),
    ("AI", ((CODING_AGENT, "Coding Agent", "COMMUNITY"),
            (GENERATIVE_AI, "Generative AI", "IMAGE"))),
    ("Data", (
        ("OUTLINER", "Outliner", "OUTLINER"),
        ("PROPERTIES", "Properties", "PROPERTIES"),
        ("FILES", "File Browser", "FILEBROWSER"),
        ("ASSETS", "Asset Browser", "ASSET_MANAGER"),
        ("SPREADSHEET", "Spreadsheet", "SPREADSHEET"),
        ("PREFERENCES", "Preferences", "PREFERENCES"),
    )),
)
EDITORS = {identity: (label, icon) for _, entries in EDITOR_GROUPS for identity, label, icon in entries}


def editor_identity(area):
    if area.type == "CONSOLE":
        return {"angelo": CODING_AGENT, "angelo_genai": GENERATIVE_AI}.get(area.spaces.active.language, "CONSOLE")
    return area.ui_type


def cached_editor_identity(space):
    """Resolve a cached Space to a complete Area.ui_type without activating it."""
    if space.type == "NODE_EDITOR":
        return space.tree_type
    if space.type == "GRAPH_EDITOR":
        return space.mode
    if space.type == "DOPESHEET_EDITOR":
        return "TIMELINE" if space.mode == "TIMELINE" else "DOPESHEET"
    if space.type == "IMAGE_EDITOR":
        # ui_mode deliberately excludes UV and returns an empty enum there.
        return "UV" if space.mode == "UV" else "IMAGE_EDITOR"
    if space.type == "FILE_BROWSER":
        return space.browse_mode
    return space.type
