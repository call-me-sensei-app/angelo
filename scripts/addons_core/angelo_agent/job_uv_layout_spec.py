# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The UV_LAYOUT job's request and receipt. No Blender dependency (doc/angelo/uv_layout.rst).

The job lays out its target's UVs as the ``uv_layout`` operation does, with the same parameters (``layout``, checked
against that operation's own schema), one pack at a time on Pack Islands' window-manager job (``job_uv_layout``).
It is where Quality layouts run: the Concave packer's time does not grow with the input, so no bound keeps one
inside a turn.
"""

from . import read_only, uv_layout_ops

KIND = "UV_LAYOUT"
# The longest development layout took 75 s on the pool VM; a layout over many UDIM tiles packs once per tile.
DEFAULT_MINUTES = 30
MAX_LISTED_TILES = 32   # More are counted, not listed.
_PARAMETERS = uv_layout_ops.PARAMETERS["properties"]

# What angelo_job_start declares, on every turn: each of the operation's fields by its type or enum only. Its
# description, default and range are the operation's (validate enforces the range and names it in a refusal).
SCHEMA = {"layout": {"type": "object", "additionalProperties": False, "properties": {
    name: {"enum": field["enum"]} if "enum" in field else {"type": field["type"]}
    for name, field in _PARAMETERS.items()}}}
DESCRIPTION = "UV_LAYOUT's layout: the uv_layout operation's parameters. "   # The first sentence names the kind.
# The checks below read a read-only copy, taken as the kind registers: the operation's schema stays the plain data
# a model is sent, which an agent script could change in place (read_only.py).
read_only.seal(globals(), "_PARAMETERS")


def _problem(name, value):
    """Why ``value`` is not one ``name`` takes, or None."""
    field = _PARAMETERS[name]
    if "enum" in field:
        return None if isinstance(value, str) and value in field["enum"] else "one of " + ", ".join(field["enum"])
    if field["type"] == "boolean":
        return None if isinstance(value, bool) else "true or false"
    whole = field["type"] == "integer"
    if isinstance(value, bool) or not isinstance(value, int if whole else (int, float)):
        return "a whole number" if whole else "a number"
    if not field["minimum"] <= value <= field["maximum"]:
        return "from %s to %s" % (field["minimum"], field["maximum"])
    return None


def validate(args, request, refuse):
    """Check ``layout`` and put it in ``request`` with every parameter (defaults filled), still in the operation's
    own form, so a stored request validates again unchanged; the runner maps it (``uv_layout_ops.to_layout``).
    ``refuse(message)`` raises the refusal."""
    layout = args.get("layout", {})
    if not isinstance(layout, dict):
        refuse("layout is an object of the uv_layout operation's parameters")
    unknown = sorted(set(layout) - set(_PARAMETERS))
    if unknown:
        refuse("layout does not take " + ", ".join(unknown) + "; it takes " + ", ".join(_PARAMETERS))
    for name, value in layout.items():
        problem = _problem(name, value)
        if problem:
            refuse("layout's %s is %s" % (name, problem))
    # Every default filled but mips: a margin nobody set stays missing, so the layout may lower it to leave room.
    request["layout"] = dict({name: field["default"] for name, field in _PARAMETERS.items() if name != "mips"},
                             **layout)


def receipt(result):
    """What the layout measured, as the agent reads it: counts and figures, no paths."""
    tiles = list(result.get("tiles") or ())
    out = {key: result.get(key) for key in ("objects", "islands", "stacked", "efficiency", "density_px_per_m",
                                            "margin_px", "gap_px", "mips_kept", "texture_size", "search", "packer",
                                            "room", "margin_lowered")}   # uv_layout.rst, "Too little room".
    out["tiles"] = tiles[:MAX_LISTED_TILES]
    if len(tiles) > MAX_LISTED_TILES:
        out["tile_count"] = len(tiles)
    out["seconds"] = round((result.get("milliseconds") or 0.0) / 1000.0, 1)
    return out
