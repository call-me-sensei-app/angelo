# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The agent's ``uv_layout`` operation, declared for Angelo's shared typed-operation pattern (``wrapped_ops``; the
agent-verbs design, Part E, revisions 1-6). Plain data: ``wrapped_ops.registry`` registers it (listed in
``wrapped_op_modules.MODULES``) and refuses it if it breaks the contract; the shared runner, once built, runs
``angelo.uv_layout`` with ``EXEC_DEFAULT`` on the program's stage and passes ``checks`` and ``receipt`` its snapshots. Search reaches the agent only here; the native
``uv_pack`` kind keeps Blender's fields. A Quality layout never runs in the turn: ``job_if`` sends it to the
UV_LAYOUT background job (``job_uv_layout``), which takes the same parameters (``to_layout``).

Snapshot shape these functions read (revision 2's facts; adapted to the shared code's accessors when it lands):
``{"target": facts, "created": [], "uvs": [[(u, v), ...] per face]}``, where facts hold ``"base"`` counts
(``vertices``, ``edges``, ``faces``, ``triangles``) and ``"uv_maps"``; ``uvs`` is the active map's corners, read from
``after.mesh(handle)``. Checks are pure: they read, never write.
"""

UDIM_COLUMNS = 10           # UDIM tiles run ten to a row: 1001-1010, then 1011.
TILE_TOLERANCE = 1e-6       # A corner on a tile's border still belongs to it.

PARAMETERS = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "texture_size": {"enum": [str(2 ** p) for p in range(8, 14)], "default": "2048",
                         "description": "Pixels on a side of the texture the UVs are laid out for"},
        "use_density": {"type": "boolean", "default": False,
                        "description": "Keep density on every island, using more UDIM tiles if needed"},
        "density": {"type": "number", "minimum": 1.0, "maximum": 1e6, "default": 1024.0,
                    "description": "Pixels per metre (with use_density)"},
        "mips": {"type": "integer", "minimum": 0, "maximum": 13, "default": 4,
                 "description": "Islands stay apart down to this mip level"},
        "unwrap": {"enum": ["KEEP", "SEAMS", "SMART"], "default": "KEEP"},
        "stack": {"type": "boolean", "default": False, "description": "Identical parts share one island's UVs"},
        "mirrored": {"type": "boolean", "default": False, "description": "With stack: mirror copies share too"},
        "group_by": {"enum": ["NONE", "MATERIAL", "OBJECT"], "default": "NONE",
                     "description": "Each group in its own UDIM tile"},
        "search": {"type": "integer", "minimum": 0, "maximum": 256, "default": 0,
                   "description": "Extra layouts the packer tries per tile; the tightest is kept"},
        "packer": {"enum": ["QUALITY", "FAST"], "default": "QUALITY",
                   "description": "QUALITY packs islands by their shapes (tightest); FAST by their boxes, far "
                                  "quicker with more empty space: only when the person asks for speed"},
    },
}


# Read by to_layout: a read-only copy taken as the module loads, so a default changed in PARAMETERS in place (an agent
# script could) changes nothing that runs; PARAMETERS stays the plain schema a model is sent.
# (A tuple of pairs: this module stays plain data, importing nothing.)
_DEFAULTS = tuple((name, field["default"]) for name, field in PARAMETERS["properties"].items())
NAMES = tuple(name for name, _default in _DEFAULTS)   # The parameters, in order: what the operator reads off itself.


def to_layout(parameters):
    """The layout ``uv_layout`` runs for these parameters (any left out take their defaults): what the operator's
    execute and the UV_LAYOUT job both ask for, so a call refused for the turn runs unchanged as the job."""
    values = dict(_DEFAULTS)
    values.update(parameters)
    size = int(values["texture_size"])
    layout = {"texture_size": size, "density": values["density"] if values["use_density"] else None,
              "unwrap": values["unwrap"], "stack": values["stack"], "mirrored": values["mirrored"],
              "group_by": values["group_by"], "search": values["search"], "packer": values["packer"]}
    if "mips" in parameters:   # Set: kept (a margin of more mip levels than the texture has would outgrow it).
        layout["mips"] = min(parameters["mips"], size.bit_length() - 1)
    return layout   # Without mips the layout picks the margin (uv_layout.rst, "Too little room").


def _area(corners):
    total = 0.0
    for i, (u0, v0) in enumerate(corners):
        u1, v1 = corners[(i + 1) % len(corners)]
        total += u0 * v1 - u1 * v0
    return abs(total) / 2.0


def _tile(corners):
    """The UDIM number of the whole tile holding every corner, or None."""
    u = sum(c[0] for c in corners) / len(corners)
    v = sum(c[1] for c in corners) / len(corners)
    column, row = int(u // 1), int(v // 1)
    if not 0 <= column < UDIM_COLUMNS or row < 0:
        return None
    inside = all(column - TILE_TOLERANCE <= cu <= column + 1 + TILE_TOLERANCE
                 and row - TILE_TOLERANCE <= cv <= row + 1 + TILE_TOLERANCE for cu, cv in corners)
    return 1001 + column + UDIM_COLUMNS * row if inside else None


def uv_layout_checks(parameters, before, after):
    """What the turn's verifier states after a layout: one check per fact, each ``{"name", "passed", "detail"}``."""
    counts = ("vertices", "edges", "faces", "triangles")
    old = {key: before["target"]["base"][key] for key in counts}
    new = {key: after["target"]["base"][key] for key in counts}
    outside = sum(_tile(face) is None for face in after.get("uvs", ()))
    return [
        {"name": "has_uv_map", "passed": bool(after["target"]["uv_maps"]),
         "detail": "UV maps: %s" % ", ".join(after["target"]["uv_maps"])},
        {"name": "geometry_unchanged", "passed": old == new, "detail": "only UVs change"},
        {"name": "faces_in_whole_tiles", "passed": outside == 0,
         "detail": "%d face(s) not inside one UDIM tile" % outside},
    ]


def uv_layout_receipt(after):
    """Tiles used and how much of them the faces cover, for the agent: counts only, no paths."""
    faces = list(after.get("uvs", ()))
    tiles = sorted({tile for tile in map(_tile, faces) if tile is not None})
    covered = sum(_area(face) for face in faces)
    return {"faces": len(faces), "tiles": tiles,
            "efficiency": round(covered / len(tiles), 4) if tiles else 0.0}


WRAPPED_OPS = (
    dict(kind="uv_layout", operator="angelo.uv_layout", source="angelo", target="MESH", mode="OBJECT",
         parameters=PARAMETERS, map={name: name for name in PARAMETERS["properties"]},
         reads_only=False, creates=None,
         mutates=frozenset({"mesh"}),      # UV maps belong to the mesh family.
         # FAST runs in the turn up to a bound set by wrapped_op_budget.bound_from_timings (p95 on Mac 1 and the
         # Windows VM within the one shared TURN_BUDGET_S), never guessed. QUALITY never does: the Concave packer took 4-75 s on development kits of 4
         # to 160 parts, whatever their size (coordinator ruling 2026-11-15: add a job kind, never a larger bound).
         input_bound=None,
         job_kind="UV_LAYOUT",
         job_if=("packer", frozenset({"QUALITY"})),   # Always the job, at any size (Part E, revision 5).
         levers=("search",),               # Fewer packing trials per tile: less work for the same faces.
         checks=uv_layout_checks, receipt=uv_layout_receipt, guide="uv_layout"),
)
