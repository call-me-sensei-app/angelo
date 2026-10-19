# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""The words a review's Details use for its keys ("Kind", "Target", "Material identity"), translatable.

``agent_workspace_ui.readable_label`` turns a control's key into words; a key listed here is then shown in the
interface language. Only the key is translated, at display time: values, names and numbers stay exactly as
submitted, so Details remain the complete, exact review. A key not listed stays as readable English words.
The keys carry the "Angelo" message context: Blender's catalogue is asked first and knows several of these words
with another meaning ("Root" as a square root, "Handle" as a curve handle, "Space" as a gap), so only a context it
does not know reaches the review's wording (ui_text).
"""

from .ui_text import iface_, n_

KEYS = frozenset((
    n_("Kind", "Angelo"), n_("Target", "Angelo"), n_("Parameters", "Angelo"), n_("Name", "Angelo"),
    n_("Alias", "Angelo"), n_("Location", "Angelo"), n_("Rotation", "Angelo"), n_("Scale", "Angelo"),
    n_("Primitive", "Angelo"), n_("Size", "Angelo"), n_("Material", "Angelo"), n_("Base color", "Angelo"),
    n_("Roughness", "Angelo"), n_("Metallic", "Angelo"), n_("Tolerance", "Angelo"), n_("Expected", "Angelo"),
    n_("Property", "Angelo"), n_("Space", "Angelo"), n_("Step", "Angelo"), n_("Action", "Angelo"),
    n_("Material identity", "Angelo"), n_("Created by", "Angelo"), n_("Copied by", "Angelo"),
    n_("Isolated by", "Angelo"), n_("Original material", "Angelo"), n_("Same material as", "Angelo"),
    n_("Slot", "Angelo"), n_("Selected slot", "Angelo"), n_("Absent", "Angelo"), n_("Links", "Angelo"),
    n_("Unlinked", "Angelo"), n_("Material effects", "Angelo"), n_("Expected checks", "Angelo"),
    n_("Operations", "Angelo"), n_("Mode", "Angelo"), n_("Read set", "Angelo"), n_("Write set", "Angelo"),
    n_("Face selector", "Angelo"), n_("All faces", "Angelo"), n_("Inputs", "Angelo"), n_("Value", "Angelo"),
    n_("Values", "Angelo"), n_("Reason", "Angelo"), n_("Affected", "Angelo"), n_("Root", "Angelo"),
    n_("Revision", "Angelo"), n_("Handle", "Angelo"), n_("Shared edits", "Angelo"),
    n_("Unverified effects", "Angelo"), n_("Recipe", "Angelo"), n_("Modifier", "Angelo"), n_("Type", "Angelo"),
    n_("Object", "Angelo"), n_("Objects", "Angelo"), n_("Frame", "Angelo"), n_("Frames", "Angelo"),
    n_("Path", "Angelo"), n_("Count", "Angelo"),
    # What a resolved orbit was measured from (camera_guards.FIELD), shown with the program it approves, and the
    # unkeyed animated values a commit's frame update replaced.
    n_("Measured from", "Angelo"), n_("Scene camera", "Angelo"), n_("Cameras", "Angelo"), n_("Camera", "Angelo"),
    n_("Unkeyed values replaced", "Angelo"),
))


def translated(label):
    """``label`` in the interface language when it is a known key, else unchanged."""
    return iface_(label) if label in KEYS else label
