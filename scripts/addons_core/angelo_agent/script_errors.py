# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A reviewed script that raised: what the model reads, what the host undoes and how often a request corrects one.

Reviewed Python is not transactional, and a script that raised used to end the
turn with ``str(error)`` and whatever it had changed still in the scene. The
executor (``blender_tools``) now answers a raise with four things
(doc/angelo/agent_execution.rst, "Reviewed Python that raises"):

* **Where.** ``describe`` adds the exception's type, the failing line, its
  source and the script's own traceback frames. Frames of Blender, of Python's
  library and of the host are left out: they are not the script's to fix, and
  their paths are not the model's to read.
* **What state the scene is in.** Before a script runs, ``restore_point``
  makes one Undo step that holds the whole file as it is, with a one-time
  marker inside that step and nowhere else. When the script raises, ``failed``
  records the half-applied state and steps back until the scene carries that
  marker: only then was the file reloaded from the snapshot Blender took
  immediately before the script, and only then is ``main_data_restored`` true.
  A read-back of names, transforms and mesh positions cannot prove that (a
  light, a camera, a world or a render setting could stay changed and still
  match), and an Undo does not always restore: in Edit or Sculpt Mode it
  covers that mode's data only, and it can return FINISHED without changing
  anything. So where no restore point can be made (those modes), where the
  marker is not reached, or where the script names or leaves a sign of what
  Undo does not hold (image pixels, the Undo history, another file), nothing
  is undone or what the script left is put back, and the result says that the
  changes remain. Files, preferences, image pixels, the window layout and
  anything else outside Blender's Undo are never claimed undone. A restored
  Main is still partial recovery: ``rolled_back`` is false, image pixels and
  external effects are unverified, and the result never says the whole scene
  is unchanged. Dirty flags and packed sizes can detect some writes, not
  prove that no pixels changed.
* **How many Undo steps.** A script that leaves the Undo history intact
  replaces its restore point with its own step (``grouped``): one Undo step,
  and no empty one before it. Arbitrary scripts can change that history, so
  successful-script coverage is unverified even when Undo is available.
  A script that was rolled back leaves its restore point: one step that
  changes nothing.
* **How often.** ``ScriptBudget`` counts the scripts that raised in one
  request, for both runtimes, at the one place every script is dispatched
  (``routing.RuntimeContext.request_tool``). The model may correct
  ``SCRIPT_CORRECTIONS`` of them; after the next one no further script runs in
  that request. A corrected script is a new call: it is validated, approved
  in the conversation's approval mode and run like any other.

``read_back`` bounds a finished script's ``result``. Nothing here retries
anything, and nothing is sent to a model except through a tool result.
"""

import ast
import json
import re
import traceback
import uuid

from .script_watch import INTERFACE, INTERFACE_NOTE, LINKED, HistoryWatch, linked_state, linked_unchanged
from .ui_text import n_, notice
from .script_source import SCRIPT_FILE  # One name for every compile of a reviewed script.

# What a reviewed script can raise. SystemExit (sys.exit, exit, quit) and KeyboardInterrupt are BaseExceptions:
# unhandled, they would end Angelo or leave a half-applied script with no result. GeneratorExit is not a raise.
SCRIPT_FAILURES = (Exception, SystemExit, KeyboardInterrupt)
SCRIPT_CORRECTIONS = 5
MAX_ERROR, MAX_SOURCE, MAX_FRAMES = 2000, 200, 8
RESULT_LIMIT, RESULT_HEAD = 12000, 11400
# A script that touches the Undo history or replaces the file leaves no step the host can rely on.
_HISTORY = frozenset({"undo", "redo", "undo_push", "undo_history", "undo_redo", "open_mainfile", "revert_mainfile",
                      "read_homefile", "read_factory_settings", "recover_last_session", "recover_auto_save"})
# What writes an image's pixels or its file. Blender's Undo keeps an image's settings, not its pixel buffer.
_PIXEL_VALUES = frozenset({"pixels"})
_PIXEL_CALLS = frozenset({"pack", "unpack", "reload", "scale", "save", "save_render", "bake", "bake_image"})
_PIXEL_OPERATORS = frozenset({"image", "paint"})
# Modes whose Undo steps hold the whole file. Edit and Sculpt Mode keep steps of their own data only.
WHOLE_FILE_MODES = frozenset({"OBJECT", "POSE", "PAINT_WEIGHT", "PAINT_VERTEX"})
RESTORE, MAX_UNDO = "angelo_restore_point", 16
# The name of a script's Undo step and of its restore point: the label of both operators that apply a change (ui.py).
STEP, STOPPED = "Apply Approved Angelo Change", "Angelo script (stopped with an error)"
SCRIPT_TOOL, APPLY, APPLY_SCRIPT = "angelo_execute_python", "apply_approved_tool", "apply_approved_script"
KEPT = ("What the script changed before the error is still in the scene. This result's change list covers objects only: "
        "settings of lights, cameras, materials, the world, the scene and images are not in it.")
OUTSIDE = ("Image pixels, files, preferences, the window layout and other effects outside Blender's Undo are not "
           "undone.")
# What the model does next. Restored: Blender's data is as it was before the script, so a corrected script cannot
# apply anything twice; "Do not automatically retry this script" here made a model drop a working approach.
# It still never says the whole scene is unchanged: pixels and external effects are unverified.
RECOVERY_RESTORED = ("Blender restored the scene's data to how it was before this script, so sending a corrected "
                     "script is safe; the same script unchanged fails the same way. Image pixels and files it wrote "
                     "are not restored.")
RECOVERY = ("What this script changed before the error is still in the scene. Inspect it, and restore the pre-request "
            "snapshot if needed, before changing the scene again: a corrected script written for the scene as it was "
            "would apply those changes twice. External effects cannot be undone.")
# A worker chat's (parallel edit): Restore refuses its request snapshots (``checkpoint_restore_ui``), so its model is
# never sent to one (``in_worker``). The same facts otherwise, of its part.
RECOVERY_IN_WORKER = ("What this script changed before the error is still in your part. Inspect it before changing the "
                      "part again: a corrected script written for the part as it was would apply those changes twice. "
                      "A worker chat's request snapshots cannot be restored, and external effects cannot be undone.")
CALL_RECOVERY_IN_WORKER = ("What this call changed before it stopped may still be in your part. Inspect it before "
                           "changing the part again: a call written for the part as it was could apply those changes "
                           "twice. A worker chat's request snapshots cannot be restored, and external effects cannot be "
                           "undone.")
_SNAPSHOT_RESTORE = re.compile(r"\bpre-request (snapshot|checkpoint)", re.IGNORECASE)   # Every host recovery naming one.
RESTORED = ("Blender Undo reloaded Main data from the restore point the host made immediately before this script. "
            "Pixel buffers and external effects remain unverified: this is partial recovery. Earlier handles are stale. "
            + INTERFACE_NOTE + " ")
NO_POINT = ("Blender was in %s, where Undo holds only that mode's own data, so the host could make no restore point "
            "and undid nothing.")
NO_HISTORY = "Blender's Undo history held no restore point, so the host undid nothing."
OWN_HISTORY = "The script used the Undo history or loaded a file itself, so the host undid nothing."
PIXELS = ("The script wrote or could write image pixels or image files, which Blender's Undo does not restore, so the "
          "host undid nothing.")
NOT_REACHED = "Blender's Undo did not reach the host's restore point, so the host put back what the script left."
DIFFERENT = "The restore point did not read back as the scene before the script, so the host put back what the script left."
NOT_UNDOABLE = ("Blender is in %s, where Undo holds only that mode's own data: what this script changed elsewhere is "
                "not an Undo step.")
UNDO_UNVERIFIED = "Script Undo history is unverified."
UNDO_UNAVAILABLE = "Undo is currently unavailable; script history is unverified."


def _source(lines, number):
    return lines[number - 1].strip()[:MAX_SOURCE] if 0 < number <= len(lines) else ""


def describe(error, code):
    """``error``, and ``line``, ``source`` and ``traceback`` when the script's own frames are on the stack.

    ``line`` is the innermost line of the script: where it raised, or where
    it called into Blender or a library that did.
    """
    frames = [(number, frame.f_code.co_name) for frame, number in traceback.walk_tb(error.__traceback__)
              if frame.f_code.co_filename == SCRIPT_FILE]
    text, where = str(error), " (line %d)" % frames[-1][0] if frames else ""
    message = type(error).__name__ + (": " + text if text else "")
    failure = {"error": message[:MAX_ERROR - len(where)] + where}
    if frames:
        lines = code.splitlines()
        failure.update(line=frames[-1][0], source=_source(lines, frames[-1][0]),
                       traceback=["line %d, in %s: %s" % (number, name[:64], _source(lines, number))
                                  for number, name in frames[-MAX_FRAMES:]])
    return failure


def read_back(value):
    """A finished script's ``result`` for its tool result: the data, or its head when it is over the limit.

    A ``result`` that is not JSON data does not fail a script that ran to its
    end: the scene has its changes, and the host's own change list follows.
    """
    try:
        text = json.dumps(value, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError, RecursionError) as error:
        return {"result": None, "result_error": "The script ran to its end, but its result is not plain JSON data (%s). "
                                                "Set result to numbers, strings, lists and dicts." % str(error)[:200]}
    if len(text) <= RESULT_LIMIT:
        return {"result": json.loads(text)}
    head = text[:RESULT_HEAD]
    while len(json.dumps(head, ensure_ascii=False)) > RESULT_HEAD:  # Quoted as a string, JSON text grows.
        head = head[:len(head) * 9 // 10]
    return {"result": {"truncated": True, "head": head, "characters": len(text), "omitted_characters": len(text) - len(head),
                       "note": "The result was %d characters of JSON. Only its first %d are kept, as text in head. "
                               "Return counts, names or a sample instead." % (len(text), len(head))}}


def beyond_undo(code):
    """Why Blender's Undo cannot take this script back (``OWN_HISTORY`` or ``PIXELS``), or None.

    Read from the names the script uses (an attribute, a call or a string,
    never a comment): an Undo-history or file-replacing operator, an image's
    ``pixels``, a call that writes or reloads an image, or an image or paint
    operator. A name is not proof of a write; the host errs towards saying
    that the changes remain. Image metadata catches some hidden writes, but
    neither this scan nor unchanged metadata proves the absence of a write.
    """
    try:
        nodes = list(ast.walk(ast.parse(code, SCRIPT_FILE, "exec")))
    except (SyntaxError, ValueError, RecursionError):
        return OWN_HISTORY
    for node in nodes:
        name = node.attr if isinstance(node, ast.Attribute) else node.value if isinstance(node, ast.Constant) else None
        if not isinstance(name, str):
            continue
        if name in _HISTORY:
            return OWN_HISTORY
        if name in _PIXEL_VALUES:
            return PIXELS
        if (isinstance(node, ast.Attribute) and name in _PIXEL_OPERATORS and isinstance(node.value, ast.Attribute)
                and node.value.attr == "ops"):
            return PIXELS
    for node in nodes:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _PIXEL_CALLS:
            return PIXELS
    return None


def _same(before, after):
    return before["objects"] == after["objects"] and before["count"] == after["count"]


def _image_facts(bpy):
    """Cheap evidence of some pixel writes: dirty transitions or changed packed sizes.

    No pixel is read and no file is loaded. Already-dirty buffers and equal-size
    repacks can change without changing these facts; they never prove recovery.
    """
    return {image.session_uid: (image.is_dirty, image.packed_file.size if image.packed_file else -1)
            for image in bpy.data.images}


def _images_touched(before, now):
    """Whether an image that existed before the script shows a pixel write, or is gone with unsaved pixels."""
    return any(now.get(uid, (False, facts[1])) != facts for uid, facts in before.items())


def _marked(bpy, point):
    """The scene that carries this restore point's marker, or None."""
    for scene in bpy.data.scenes:
        if scene.session_uid == point[1]:
            return scene if getattr(scene, RESTORE, "") == point[0] else None
    return None


def restore_point(bpy, window):
    """Before a script: one Undo step that holds the whole file as it is. ``(marker, scene UID, image facts)`` or None.

    The marker is a host-defined scene property (not a custom property, so no
    panel shows it and no script that clears custom properties removes it). It
    is set, the step is pushed, and it is unset again: only that step carries
    it. Reaching a scene with the marker therefore means the file was reloaded
    from that step, whatever the script did to the Undo history in between.
    None in a mode whose Undo steps do not hold the whole file.
    """
    if bpy.context.mode not in WHOLE_FILE_MODES:
        return None
    scene, marker = window.scene, uuid.uuid4().hex
    try:
        if not hasattr(bpy.types.Scene, RESTORE):
            setattr(bpy.types.Scene, RESTORE, bpy.props.StringProperty(options={"HIDDEN"}))
        setattr(scene, RESTORE, marker)
        made = "FINISHED" in bpy.ops.ed.undo_push(message=STEP)
    except Exception:  # No Undo history in this context: nothing is claimed later.
        made = False
    finally:
        try:
            scene.property_unset(RESTORE)
        except Exception:
            pass
    # Linked data and editable assets are not in the step: their state is compared after a reload (script_watch).
    return (marker, scene.session_uid, _image_facts(bpy), linked_state(bpy)) if made else None


def watch_history(bpy, point):
    """While the script runs: notice Undo, Redo and file loads (``script_watch.HistoryWatch``); None without a point."""
    return HistoryWatch(bpy).start() if point is not None else None


def grouped(operator, name=APPLY_SCRIPT):
    """``operator`` again as the one that applies a script: its Undo step replaces the script's restore point.

    Blender's grouped Undo push removes the active step when that step has
    the operator's name and then pushes its own. The restore point has that
    name, so a script that leaves its restore point active is one Undo step
    with no empty step before it. A script that was rolled back cancels the operator and pushes nothing:
    its restore point stays as one step that changes nothing.

    A sibling with the same ``execute``, not a subclass: registering a
    subclass of a registered operator takes the Python class from its parent.
    """
    return type("ANGELO_OT_" + name, operator.__bases__, {
        "bl_idname": "angelo." + name, "bl_label": STEP, "bl_options": {"UNDO_GROUPED"},
        "execute": operator.execute, "__annotations__": dict(operator.__annotations__)})


def apply_operator(bpy, window, name):
    """The operator that applies an approved call: the grouped one only where a script gets a restore point.

    Without a restore point the grouped push would find the step of the
    change before it under its own name and replace that instead.
    """
    if name != SCRIPT_TOOL:
        return APPLY
    with bpy.context.temp_override(window=window):
        return APPLY_SCRIPT if bpy.context.mode in WHOLE_FILE_MODES else APPLY


def undo_facts(bpy, point, mode):
    """Availability at script completion, separate from unverified Undo coverage.

    Arbitrary Python can push or replace history, even through computed names.
    The outer operator publishes its grouped step after this function returns.
    Neither the original restore point nor Undo's poll proves that one later
    Undo will restore this script's pre-state. Do not claim that coverage.
    Availability only records the current configuration and operator poll;
    reading them neither changes preferences nor loads pixels or history.
    """
    now = bpy.context.mode
    try:
        preferences = bpy.context.preferences.edit
        available = bool(preferences.use_global_undo and preferences.undo_steps > 0 and bpy.ops.ed.undo.poll())
    except (AttributeError, ReferenceError, RuntimeError):
        available = False
    facts = {"undoable": False, "main_data_undoable": False, "undo_scope": "MAIN_DATA",
             "image_pixels_undoable": False, "undo_available": available,
             "undo": UNDO_UNVERIFIED if available else UNDO_UNAVAILABLE}
    blocked = now if now not in WHOLE_FILE_MODES else mode
    if blocked not in WHOLE_FILE_MODES:
        facts["undo"] = NOT_UNDOABLE % blocked
    return facts


def undo_to(bpy, window, code, point, before, mode, signature, moved=False):
    """Reload the file from ``point`` after a script raised. ``(True, None)`` only when its marker was reached.

    ``moved``: the script moved the Undo history or loaded a file (``script_watch``). So does a
    marker still in the live scene: the script brought the restore point's state back itself.
    """
    if point is None:
        return False, NO_POINT % mode if mode not in WHOLE_FILE_MODES else NO_HISTORY
    reason = beyond_undo(code)
    if reason is not None:
        return False, reason
    if moved or any(getattr(scene, RESTORE, "") == point[0] for scene in bpy.data.scenes):
        return False, OWN_HISTORY
    steps, scene, linked = 0, None, False
    try:
        if _images_touched(point[2], _image_facts(bpy)):
            return False, PIXELS
        if bpy.context.mode != mode and bpy.context.mode not in WHOLE_FILE_MODES:
            bpy.ops.object.mode_set(mode="OBJECT")  # The half-applied state must be a whole-file step too.
        # The half-applied state becomes one Undo step (Redo shows it again), then the history is stepped back.
        if "FINISHED" not in bpy.ops.ed.undo_push(message=STOPPED):
            return False, NO_HISTORY
        while scene is None and steps < MAX_UNDO and "FINISHED" in bpy.ops.ed.undo():
            steps += 1
            scene = _marked(bpy, point)
    except RuntimeError:  # An operator refused in this context.
        scene = None
    try:
        if scene is not None:
            scene.property_unset(RESTORE)
            # Two more proofs on top of the marker: the object read-back, and the linked data Undo does not reload.
            if bpy.context.mode == mode and (before is None or _same(before, signature(window.scene))):
                if linked_unchanged(point[3], linked_state(bpy)):
                    return True, None
                linked = True
        for _ in range(steps):
            bpy.ops.ed.redo()
    except RuntimeError:
        pass
    return False, NOT_REACHED if scene is None else LINKED if linked else DIFFERENT


def failed(bpy, window, code, error, output, before, mode, signature, point=None, watch=None):
    """The tool result of a script that raised, after the host tried to reload the file from its restore point.

    ``point`` is ``restore_point``'s, ``before`` the executor's object
    signature from before the script, ``mode`` the context mode then and
    ``signature(scene)`` the same read now. ``watch`` is ``watch_history``'s,
    stopped here before the host's own Undo.
    """
    result = dict(describe(error, code), output=output, script_error=True)
    moved = watch.stop() if watch is not None else False
    main_restored, why = undo_to(bpy, window, code, point, before, mode, signature, moved)
    result.update(main_data_restored=main_restored, rollback_scope="MAIN_DATA", rolled_back=False,
                  rollback_scope_excludes=list(INTERFACE),
                  partial_changes_possible=True, image_pixels_restored=False, external_effects_restored=False,
                  recovery=RECOVERY_RESTORED if main_restored else RECOVERY)
    if main_restored:
        result["rollback"] = RESTORED + OUTSIDE
    else:
        result["rollback"] = why + " " + KEPT + " " + OUTSIDE
    return result


def in_worker(result):
    """``result`` for a worker chat's model: a ``recovery`` that names the pre-request snapshot, which Restore refuses
    for a worker chat, says the worker's own facts instead (a script that raised: ``RECOVERY_IN_WORKER``)."""
    recovery = result.get("recovery") if isinstance(result, dict) else None
    if isinstance(recovery, str) and _SNAPSHOT_RESTORE.search(recovery):
        result["recovery"] = RECOVERY_IN_WORKER if recovery == RECOVERY else CALL_RECOVERY_IN_WORKER
    return result


def raised(result):
    """Whether a tool result is the executor's account of a script that raised."""
    return isinstance(result, dict) and result.get("script_error") is True and isinstance(result.get("error"), str)


def row(result):
    """What the person reads when a script raised: the error, and what became of its changes."""
    if result.get("main_data_restored"):
        return notice("The script stopped with an error. Scene data was restored, but image pixels and external "
                      "effects were not verified:\n{error}", error=result["error"])
    return notice("The script stopped with an error. Some changes may remain; inspect the scene before continuing:\n{error}",
                  error=result["error"])


USED_UP = n_("This request's script corrections are used up; nothing more was tried.")
REFUSED = {"error": "This request has used its %d script corrections, so no further script runs in it. Tell the person "
                    "what failed and what state the scene is in." % SCRIPT_CORRECTIONS,
           "scene_changed": False, "partial_changes_possible": False, "script_corrections_used": True}


class ScriptBudget:
    """One request's scripts that raised. The model may correct ``SCRIPT_CORRECTIONS`` of them."""

    def __init__(self):
        self.raised = 0

    @property
    def used(self):
        return self.raised > SCRIPT_CORRECTIONS

    def admit(self, emit, callback):
        """The callback to dispatch a script with, or None after answering that the budget is used.

        ``emit(kind, data)`` shows the person what happened to each script
        that raised; the result passed on is marked once no correction is left.
        """
        if self.used:
            callback(dict(REFUSED))
            return None

        def receive(result):
            if raised(result):
                self.raised += 1
                emit("message", dict(row(result), role="system"))
                if self.used:
                    emit("message", dict(notice(USED_UP), role="system"))
                    result = dict(result, script_corrections_used=True)
            callback(result)
        return receive
