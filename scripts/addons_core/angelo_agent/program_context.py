# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""How a staged program's bound context changed, and when that is recoverable.

An ``angelo_scene_program`` (and the program an ``angelo_apply_recipe`` expands
to) is bound to an exact context when it is approved: the document generation,
the scene, the view layer, the current frame, every declared input's
revision and fingerprint and, when declared, the scene settings revision
(``ProgramBinding``, built by ``native_programs.precondition``). Staging starts,
and commit happens, only while that binding still holds.

The current frame is the one part of the binding an artist routinely changes
without touching any object: pressing Space starts animation playback, an
arrow key steps the timeline. Before this module the host reported any change
as "The target changed after approval" and ended the request, although nothing
had been staged and no object had changed. Now:

* while animation playback runs, staging waits (the frame keeps moving, so no
  binding could hold); the call's normal expiry still bounds the wait;
* when only the frame differs from the approved binding and every declared
  input's revision and fingerprint (and the frame-free scene settings
  revision) is unchanged, the controller rebinds the same validated program
  once at the current frame and stages it from scratch. Staging and the
  commit's own checks run at that frame, so the "exact context" contract
  still holds for what is committed;
* every other difference (Undo/Redo/load, another scene or view layer, a
  changed input or scene setting) still fails closed, now with the actual
  reason.

Dependency-free: the preconditions are plain tuples, so host tests use this
module without Blender.
"""

from collections import namedtuple

from .program_jobs import ProgramError

# One recovery per approved call: a rebind at staging start or before commit.
MAX_CONTEXT_REBINDS = 1
# Staging restarts after a frame change found while polling or before commit.
MAX_STAGING_RESTARTS = 2
PLAYBACK_WAIT_NOTE = ("Animation playback is running, so the current frame keeps changing. Staging waits until "
                      "playback stops; nothing has been staged or changed.")
_KINDS = ("document", "scene", "view_layer", "inputs", "settings", "verification", "frame")
_REASONS = {
    "document": "Undo, Redo or a file load replaced the document",
    "scene": "The active scene changed",
    "view_layer": "The active view layer changed",
    "inputs": "A declared object or one of its dependencies changed",
    "settings": "The bound scene's settings changed",
    "verification": "The program's required verification changed",
}
_ADVICE = {
    "document": "Observe the scene again before asking for this change.",
    "scene": "Return to the original scene or observe the current one before retrying.",
    "view_layer": "Return to the original view layer or observe again before retrying.",
    "inputs": "Inspect the changed object before retrying.",
    "settings": "Read angelo_scene_settings again before retrying.",
    "verification": "Review and stage the program again with its complete current checks.",
    "frame": "Keep the timeline still (stop playback), then ask again.",
}

# The ``angelo_scene_program`` precondition (``native_programs.precondition``):
# ``inputs`` holds ``(handle, revision, fingerprint)`` for every declared
# object; ``settings`` is ``(scene handle, settings revision)`` when the
# program declares the scene, else None. Its producer and both consumers
# (``precondition_change`` and ``native_programs.rebind``) read this one
# definition: a field added on one side only made every frame move unrecoverable.
ProgramBinding = namedtuple("ProgramBinding", ("generation", "scene", "frame", "view_layer", "inputs", "settings",
                                               "checks_digest", "obligations"), defaults=("", None))


def program_binding(value):
    """``value`` as a ProgramBinding, or None when it is not a scene-program precondition."""
    if isinstance(value, tuple) and len(value) == len(ProgramBinding._fields):
        return ProgramBinding(*value)
    return None


def verification_identity(bundle):
    """Verification fields unaffected by a frame-only revision refresh."""
    return tuple(getattr(bundle, name, None) for name in
                 ("checks_digest", "facts_digest", "effects_encoded", "derived_flags", "requires_binding", "unknown_encoded"))


class ProgramFrameChanged(ProgramError):
    """The bound current frame moved; nothing live was written by this program.

    ``frames`` is ``(bound, current)``, each ``(frame_current, frame_subframe)``.
    """

    context_change = "frame"

    def __init__(self, bound, current):
        self.frames = (bound, current)
        super().__init__(frame_reason(bound, current) + "; the staged result was discarded, nothing changed. "
                         + _ADVICE["frame"], code="FRAME_CHANGED")


def _frame_label(frame):
    if isinstance(frame, (tuple, list)) and len(frame) == 2:
        number, subframe = frame
        return str(number) if not subframe else "%s (+%.3g)" % (number, subframe)
    return str(frame)


def frame_reason(bound, current):
    return ("The current frame changed from %s to %s (animation playback or a timeline change)"
            % (_frame_label(bound), _frame_label(current)))


def precondition_change(before, after):
    """Classify two ``angelo_scene_program`` preconditions; None when equal or not program bindings.

    Both are ProgramBinding values. Only a frame-only difference is
    ``rebindable``: the inputs and scene settings the approval showed are
    byte-for-byte the same.
    """
    before, after = program_binding(before), program_binding(after)
    if before is None or after is None or before == after:
        return None
    if before.generation != after.generation:
        kind = "document"
    elif before.scene != after.scene:
        kind = "scene"
    elif before.view_layer != after.view_layer:
        kind = "view_layer"
    elif before.inputs != after.inputs:
        kind = "inputs"
    elif before.settings != after.settings:
        kind = "settings"
    elif (before.checks_digest != after.checks_digest or
          verification_identity(before.obligations) != verification_identity(after.obligations)):
        kind = "verification"
    else:
        kind = "frame"
    reason = frame_reason(before.frame, after.frame) if kind == "frame" else _REASONS[kind]
    return {"kind": kind, "rebindable": kind == "frame", "reason": reason, "advice": _ADVICE[kind]}


def changed_message(change, moment):
    """The error for a binding that no longer holds, or None to keep the caller's generic text.

    ``moment`` completes the sentence, e.g. "after approval; nothing was staged".
    """
    if not isinstance(change, dict) or change.get("kind") not in _KINDS:
        return None
    return "%s %s. %s" % (change["reason"], moment, change["advice"])


def refusal_message(reason, moment):
    """A frame move that could not be rebound: the reason, when, and the artist action."""
    return "%s %s. %s" % (reason, moment, _ADVICE["frame"])


def change_code(change):
    """A closed refusal class from the structured binding comparison."""
    return {"frame": "FRAME_CHANGED", "inputs": "STALE_REVISION", "settings": "STALE_SETTING"}.get(
        change.get("kind") if isinstance(change, dict) else None, "STALE_CONTEXT")
