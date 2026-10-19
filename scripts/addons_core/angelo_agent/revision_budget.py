# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Which revisions a cooperative budget may leave out, and how long a read-back after an edit may take.

A revision the host leaves out of an observation cannot be bound by inference, so a program that edits that
object is refused until the model looks it up: a model round spent on the host's own budget. Recorded (GPT
6.1 Sol): a reviewed script's read-back spent its 25 ms on two meshes of about 90,000
vertices, left the scene settings' revision out, and the next program, which set a scene setting, was refused.
So:

* the scene settings' revision is never left out (``turn_context._scene_settings``): measured 2 ms on a plain
  scene, 5 ms with 500 collections, 300 markers and 8 view layers, and 33 ms with a 300-node World graph;
* a small revision may draw on an allowance past the budget (``SMALL_ALLOWANCE_SECONDS``, as long as the turn
  context's whole budget): an object without geometry (empty, light, camera, speaker, light probe) or a mesh of at
  most ``SMALL_REVISION_VERTICES`` vertices, either without a parent, constraint, modifier or camera focus
  object, any of which can bring another object's geometry into its revision: a cube shrinkwrapped to a
  95,000-vertex terrain costs 15 ms, not 0.2 ms. The allowance bounds what a small
  revision's other contents (a large material graph) can add: at most one budget more in all;
* a read-back after an edit (a reviewed script's ``after``, a committed program's post-commit revisions) gives
  each object the edit changed a revision, up to its row cap, within ``READ_BACK_SECONDS``: 24 rows at the typed
  programs' 100,000-vertex input limit measure about 0.4 s, and the edit that made them cost more than that.

``object_revision`` on plain grid meshes (``test_blender_rc10_gaps``): about 0.2 ms at 100 vertices, 0.8 ms at
4,096, 1.7 ms at 10,000 and 14 ms at 90,000. A revision that is left out still states why (``BUDGET``), and a
refusal that needs it names that reason and the lookup that returns it (``binding_refusals``).
"""

SMALL_REVISION_VERTICES = 4096
READ_BACK_SECONDS = 0.5
SMALL_ALLOWANCE_SECONDS = 0.025  # turn_context.REVISION_BUDGET_SECONDS: past any budget, small ones get this more.
_GEOMETRY_FREE = frozenset({"EMPTY", "LIGHT", "CAMERA", "SPEAKER", "LIGHT_PROBE"})


def small(obj):
    """Whether ``obj``'s revision is cheap: no geometry or a small mesh, and nothing that can bring another object's
    geometry into it (a parent, a constraint, a modifier, a camera's focus object)."""
    try:
        if not (obj.type in _GEOMETRY_FREE or (obj.type == "MESH" and obj.data is not None
                                                and len(obj.data.vertices) <= SMALL_REVISION_VERTICES)):
            return False
        return (obj.parent is None and not len(obj.constraints) and not len(obj.modifiers)
                and getattr(getattr(obj.data, "dof", None), "focus_object", None) is None)
    except (AttributeError, TypeError, ReferenceError):
        return False


def left_out(clock, started, budget, obj):
    """Whether a cooperative budget leaves ``obj``'s revision out: the budget is spent, and so is the allowance a
    small revision may draw on past it."""
    spent = clock() - started
    return spent >= budget and not (spent < budget + SMALL_ALLOWANCE_SECONDS and small(obj))
