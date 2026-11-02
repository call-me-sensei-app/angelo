# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What an action kind's schema cannot say, and where each model reads it. No bpy.

Three surfaces state a kind: an exact lookup (``action_catalog.catalog``: its ``note``), Claude's plan for a core
kind (``plan_schema.core_operation``: the description of its parameters, or of the field that holds the mode) and
the Codex program reference for its kinds (``codex_reference``: a line of its own, since TypeScript drops
descriptions). Each reads ``note`` here, so the three say the same.

Notes are what a number means (batch_spec.PLACEMENT_NOTE, action_schemas.BEND_NOTE) and, for a kind whose flat
schema offers every field to every mode while its validator refuses another mode's, which fields each mode takes
(``MODE_FIELDS``, stated by ``mode_fields.statement`` from the table the validator reads).
"""

from . import anim_spec, anim_structure_spec, node_edit_spec
from .action_camera_light_spec import settings_statement, type_table
from .action_schemas import BEND_NOTE, ORIGIN_FIELDS
from .batch_spec import PLACEMENT_NOTE
from .mode_fields import statement

# kind -> (field that holds the mode table's key, None for parameters itself; key; table; whether every mode also
# takes fields the table leaves out). The validators read these same tables (mode_fields.problem); node_edit states
# its own in its step schema (node_edit_spec.step_table), the field ``edits``.
MODE_FIELDS = {
    'keyframe_edit': (None, 'operation', anim_spec.EDIT_FIELDS, True),
    **{kind: (None, key, table, False) for kind, (key, table) in anim_structure_spec.TABLES.items()},
    'origin_set': (None, 'mode', ORIGIN_FIELDS, False),
    **{kind: ('settings',) + type_table(kind.split('_')[0])[:2] + (True,)
       for kind in ('camera_create', 'camera_configure', 'light_create', 'light_configure')},
    'node_edit': ('edits', 'op', node_edit_spec.step_table(node_edit_spec._VARIANTS), False),
}


def mode_statement(kind):
    """The fields-by-mode statement of a MODE_FIELDS kind, as every surface states it."""
    field, key, table, common = MODE_FIELDS[kind]
    if field == 'settings':
        return settings_statement(kind.split('_')[0])
    if field == 'edits':
        return statement('node_edit edits fields', key, table, common)
    return statement(kind + ' fields', key, table, common)


def note(kind):
    """The text a kind's schema cannot say, or None. node_edit's is its step schema's own description."""
    if kind == 'batch':
        return PLACEMENT_NOTE
    if kind == 'mesh_bend':
        return BEND_NOTE
    if kind in MODE_FIELDS and MODE_FIELDS[kind][0] != 'edits':
        return mode_statement(kind)
    return None


def note_field(kind):
    """Where Claude's plan puts a kind's note: None for its parameters, else the field holding its mode."""
    return MODE_FIELDS[kind][0] if kind in MODE_FIELDS else None
