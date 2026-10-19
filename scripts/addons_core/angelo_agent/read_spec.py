# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The two live read tools: one data path, and a .blend file as a file. No Blender dependency.

``angelo_read_data`` reads one path of the open document through the closed
grammar of ``data_path`` and the rules of ``data_read``. ``angelo_blend_file``
summarizes the open file (``blend_file``) or lists a workspace file's
datablocks without opening it (``blend_file_listing``). Both are read-only:
they need no approval and stay available in a read-only chat
(doc/angelo/scene_intelligence.rst, "Live data paths" and "The file as a file").

Only shape and bounds are validated here. A path that does not parse, an
unknown property, a file outside the workspace and a file that is not a .blend
are answers (``found`` or ``readable`` false, with a stable ``code`` and what to
do next), never tool errors, so a wrong guess costs one read and not the turn.
"""

from .data_path import MAX_PATH_CHARS

MAX_ROWS = 64
DEFAULT_ROWS = 32
MAX_OFFSET = 10000000
MAX_FILE_CHARS = 1024
# Sections of angelo_blend_file a ``list`` argument pages; any other value names a datablock type (meshes ...).
SECTIONS = ("UNUSED", "FILES", "LIBRARIES", "UNRESOLVED")
MAX_LIST_CHARS = 32

# Bounds the host enforces are stated in the descriptions too: Codex's code-mode declarations show none.
_PAGE = {"offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_ROWS}}
_READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}

READ_TOOLS = [
    {
        "name": "angelo_read_data",
        "description": (
            "Read one data path of the open document, read-only and without approval: a struct's properties with "
            "current values, a page of a collection (names; node socket and modifier input identifiers), or one "
            "property with its enum choices. Covers add-on settings (Cycles). Nothing is called or evaluated. Up "
            "to 64 rows (default 32) and 12,000 characters per call; truncated gives next_offset. File paths, "
            "scripts, driver expressions and socket text are withheld. A wrong name returns found=false with the "
            "valid names."),
        "inputSchema": {"type": "object", "properties": {
            "path": {"type": "string", "maxLength": MAX_PATH_CHARS,
                     "description": 'From scene or data (bpy.data): .name, [index], ["name"] only, e.g. '
                                    'data.node_groups["Rock"].nodes[0].inputs'},
            **_PAGE,
            "depth": {"type": "integer", "minimum": 1, "maximum": 2, "description": "2 adds each row's own values"}},
            "required": ["path"], "additionalProperties": False},
        "annotations": dict(_READ_ONLY),
    },
    {
        "name": "angelo_blend_file",
        "description": (
            "Summarize a .blend as a file, read-only and without approval. No file: the open file's datablock "
            "counts by type with unused (zero-user) data, linked libraries, external files as packed, present or "
            "missing, and unresolved references. file (a .blend path relative to the selected file workspace; no "
            "link escapes): that file's datablock names and counts, without opening or linking it. list pages one "
            "part: a datablock type (meshes ...) or UNUSED, FILES, LIBRARIES, UNRESOLVED. Up to 64 rows a page "
            "(default 32). Files are named workspace-relative or by base name, never absolutely. An unreadable "
            "file returns readable=false."),
        "inputSchema": {"type": "object", "properties": {
            "file": {"type": "string", "maxLength": MAX_FILE_CHARS},
            "list": {"type": "string", "maxLength": MAX_LIST_CHARS},
            **_PAGE},
            "required": [], "additionalProperties": False},
        "annotations": dict(_READ_ONLY),
    },
]
READ_NAMES = frozenset(tool["name"] for tool in READ_TOOLS)
_SCHEMAS = {tool["name"]: tool["inputSchema"] for tool in READ_TOOLS}


def _whole(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("%s is a whole number from %d to %d" % (label, low, high))
    return value


def _printable(value, limit, label, *, allow_empty=False):
    """A bounded printable string. No schema a runtime checks carries these lengths (plan_schema.without_lengths,
    strict_schema), so the limit is enforced here, in one line that names it and the length it got."""
    if (type(value) is not str or len(value) > limit or (not allow_empty and not value.strip())
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("%s is printable text of at most %d characters (this one has %d)"
                         % (label, limit, len(value) if type(value) is str else 0))
    return value


def validate_read(name, args):
    """Shape and bounds only; returns the arguments with their defaults applied."""
    if type(name) is not str or name not in READ_NAMES or type(args) is not dict:
        raise ValueError("Unknown read tool or non-object arguments")
    schema = _SCHEMAS[name]
    if set(args) - set(schema["properties"]) or set(schema["required"]) - set(args):
        raise ValueError("Unexpected or missing read argument")
    result = {"offset": _whole(args.get("offset", 0), 0, MAX_OFFSET, "offset"),
              "limit": _whole(args.get("limit", DEFAULT_ROWS), 1, MAX_ROWS, "limit")}
    if name == "angelo_read_data":
        # The grammar is checked when the path is read, so a path that does not parse is an answer.
        result["path"] = _printable(args["path"], MAX_PATH_CHARS, "path")
        result["depth"] = _whole(args.get("depth", 1), 1, 2, "depth")
        return result
    result["file"] = _printable(args.get("file", ""), MAX_FILE_CHARS, "file", allow_empty=True).strip()
    if "list" in args:
        result["list"] = _printable(args["list"], MAX_LIST_CHARS, "list").strip()
    return result
