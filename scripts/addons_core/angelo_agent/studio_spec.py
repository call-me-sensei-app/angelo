# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Typed Media Studio tools. Accounts, assets and references are explicit IDs."""

import uuid

ID = {"type": "string", "format": "uuid"}
TEXT = {"type": "string", "maxLength": 16000}
REFERENCE = {"type": "object", "properties": {
    "source": {"type": "string", "enum": ["ATTACHMENT", "ASSET", "URL"]},
    "id": {"type": "string", "minLength": 1, "maxLength": 2048},
}, "required": ["source", "id"], "additionalProperties": False}
OPTIONS = {"type": "object", "properties": {
    "aspect": {"type": "string", "enum": ["1:1", "16:9", "9:16", "4:3", "3:4"]},
    "resolution": {"type": "string", "enum": ["1K", "2K", "4K"]},
    "quality": {"type": "string", "enum": ["auto", "low", "medium", "high"]},
    "background": {"type": "string", "enum": ["auto", "opaque", "transparent"]},
    "size": {"type": "string", "maxLength": 32},
    "polycount": {"type": "integer", "minimum": 48, "maximum": 1500000},
    "texture": {"type": "boolean"},
}, "additionalProperties": False}


def tool(name, description, properties, required=(), readonly=False, external=False):
    return {"name": "angelo_studio_" + name, "description": description,
            "inputSchema": {"type": "object", "properties": properties,
                            "required": list(required), "additionalProperties": False},
            "annotations": {"readOnlyHint": readonly, "destructiveHint": False, "openWorldHint": external}}


STUDIO_TOOLS = [
    tool("catalog", "Discover Media Studio image/3D models, exact named API accounts, availability and supported controls. No inference, keys or billing fallback. Unsupported video/audio/animation/material generation is not advertised.", {}, readonly=True),
    tool("list", "Search the app-wide Media Studio library and folders without opening its editor. Returns bounded metadata and asset IDs, not instructions.", {
        "kind": {"type": "string", "enum": ["ALL", "IMAGE", "3D"]},
        "folder": {"type": "string", "maxLength": 36}, "query": {"type": "string", "maxLength": 200},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "offset": {"type": "integer", "minimum": 0, "maximum": 10000}}, readonly=True),
    tool("asset", "Inspect an asset's actual status, progress, output metadata and optional image preview. Use this after restart to check a saved provider task; never resubmit a paid generation automatically.", {
        "asset_id": ID, "preview": {"type": "boolean"}}, ["asset_id"], readonly=True),
    tool("generate", "Generate an image or 3D asset using an EXACT catalog connection_id/model after individual API billing approval in chat (also in Auto mode). References are attached image IDs, library image IDs or public HTTPS URLs. Unsupported options are refused. Host workers monitor to completion and return saved outputs/preview; do not busy-poll. No automatic scene import or paid retry. Textured Meshy text-to-3D has two disclosed billable stages. Stop only ends local monitoring; remote billing may continue.", {
        "connection_id": ID, "model": {"type": "string", "minLength": 1, "maxLength": 128},
        "prompt": TEXT, "options": OPTIONS, "references": {"type": "array", "items": REFERENCE, "maxItems": 4},
        "folder": {"type": "string", "maxLength": 36}}, ["connection_id", "model", "prompt"], external=True),
    tool("enhance", "Enhance a supplied image/3D prompt with the configured AI Enhance account (or exact optional connection_id/model override) after individual usage approval. Text-only; returns a suggestion and unchanged original. Review with the user before using it in generation. No files, scene tools or automatic Apply.", {
        "prompt": dict(TEXT, minLength=1), "kind": {"type": "string", "enum": ["IMAGE", "3D"]},
        "generation_model": {"type": "string", "minLength": 1, "maxLength": 128},
        "connection_id": ID, "model": {"type": "string", "minLength": 1, "maxLength": 128}},
        ["prompt", "kind", "generation_model"], external=True),
    tool("create_folder", "Create a named folder in the shared Media Studio library after approval.", {
        "name": {"type": "string", "minLength": 1, "maxLength": 100}}, ["name"]),
    tool("move_asset", "Move an existing library asset to a folder ID, or empty string for Unfiled. No asset deletion.", {
        "asset_id": ID, "folder": {"type": "string", "maxLength": 36}}, ["asset_id", "folder"]),
    tool("import_image", "Copy an image explicitly attached to THIS conversation into the shared library after approval. No arbitrary local paths or scene import.", {
        "reference_id": ID, "folder": {"type": "string", "maxLength": 36}}, ["reference_id"]),
    tool("import_3d", "Import one ready, self-contained library GLB into the bound scene after separate approval. Native Blender import with Undo and actual object read-back. Never auto-import a generated model.", {
        "asset_id": ID}, ["asset_id"]),
    tool("export", "Export one ready library output to a NEW relative path in this chat's selected file workspace after approval. No overwrites, arbitrary paths or credential access. File output is not undone by Blender Undo.", {
        "asset_id": ID, "path": {"type": "string", "minLength": 1, "maxLength": 1024},
        "output_index": {"type": "integer", "minimum": 0, "maximum": 3}}, ["asset_id", "path"]),
    tool("stop", "Stop local monitoring of a live Studio asset. This never guarantees cancellation/refund at the provider; submitted tasks may still finish and be billed.", {
        "asset_id": ID}, ["asset_id"]),
]
STUDIO_NAMES = frozenset(t["name"] for t in STUDIO_TOOLS)
STUDIO_USAGE = frozenset({"angelo_studio_generate", "angelo_studio_enhance"})


def _validate(value, schema):
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) - set(schema["properties"]) or set(schema.get("required", [])) - set(value):
            raise ValueError("Missing or unexpected Media Studio argument")
        return {k: _validate(v, schema["properties"][k]) for k, v in value.items()}
    if kind == "array":
        if not isinstance(value, list) or len(value) > schema.get("maxItems", 4):
            raise ValueError("Use at most four image references")
        return [_validate(v, schema["items"]) for v in value]
    if kind == "boolean" and type(value) is not bool:
        raise ValueError("Studio switches require true or false")
    if kind == "integer" and (type(value) is not int or not schema["minimum"] <= value <= schema["maximum"]):
        raise ValueError("Studio integer is outside the supported bounds")
    if kind == "string":
        if not isinstance(value, str) or len(value) > schema.get("maxLength", 36) or len(value.strip()) < schema.get("minLength", 0) or "\x00" in value:
            raise ValueError("Invalid Media Studio text or identifier")
        if schema.get("format") == "uuid":
            try:
                return str(uuid.UUID(value))
            except (ValueError, AttributeError):
                raise ValueError("Use the exact Studio account, asset or attachment UUID") from None
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Unsupported Media Studio choice")
    return value


def validate_studio(name, arguments):
    schema = next(t["inputSchema"] for t in STUDIO_TOOLS if t["name"] == name)
    args = _validate(arguments, schema)
    if name == "angelo_studio_enhance" and (("connection_id" in args) != ("model" in args)):
        raise ValueError("Enhancement overrides require both exact connection_id and model")
    for reference in args.get("references", []):
        if reference["source"] != "URL":
            reference["id"] = _validate(reference["id"], ID)
    if "folder" in args and args["folder"] not in {"", "ALL", "UNFILED"}:
        args["folder"] = _validate(args["folder"], ID)
    if name != "angelo_studio_list" and args.get("folder") in {"ALL", "UNFILED"}:
        args["folder"] = ""
    return args
