# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""What the approval card says for ``angelo_engine_fix`` (``engine_check_spec``): one line per fix, in the person's
language, with the object, action or image it changes."""

from .engine_rules import DISPLAY, FIXES
from .ui_text import iface_, n_

# One per typed fix (engine_fixes.FIX_FUNCTIONS): what it does to its subject.
FIX_LINES = {
    "apply_transform": n_("Apply rotation and scale of {subject} and the meshes it deforms"),
    "apply_scale": n_("Apply the scale of {subject}"),
    "rename_armature": n_("Rename armature {subject} to Armature"),
    "stash_action": n_("Keep action {subject} in a muted NLA track"),
    "loop_name": n_("Rename action {subject} to {subject}-loop"),
    "apply_modifier": n_("Apply the shading modifier of {subject}"),
    "link_data": n_("Make the copies of {subject} share its mesh"),
    "collision_mesh": n_("Replace empty {subject} with a collision mesh"),
    "hide_collider": n_("Hide {subject} in the viewport"),
    "limit_influences": n_("Limit and normalize the bone weights of {subject}"),
    "colour_space": n_("Set the colour space of image {subject}"),
}
if set(FIX_LINES) != set(FIXES.values()):
    raise RuntimeError("Every typed fix needs its card line")


def message(name, args, usage_label=""):
    """Title, body and warning of the card."""
    engine = DISPLAY[args["target"]]
    lines = [iface_(FIX_LINES[FIXES[item["code"]]], subject=item["subject"]) for item in args.get("fixes", ())]
    return {"title": iface_("Fix the scene for {engine}", engine=engine),
            "body": "\n".join(lines),
            "warning": iface_("One Undo step undoes all of these. The scene is checked again first; a fix whose "
                              "finding is gone is refused.")}
