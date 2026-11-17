# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""A modal operator says what it has done with a person's input: ``Operator.has_answered_input``,
``Operator.has_consumed_input`` and ``Operator.consumed_input_age`` (2.0.0).

An operator's flags do not say whether a running Python modal is a tool the person is using or an observer that only
watches (a status display, a timer-driven worker): both are a modal handler of the window. The event system knows
what each did with input (``wm_angelo_consumed_input.cc``). The handler's operator is marked *answered* once its
``modal()`` was given a person's input event (keyboard, mouse, trackpad, NDOF, XR controller) and returned, whatever
the result; and *consumed* once it kept such an event from every later handler (a result without ``PASS_THROUGH``, or
``{'RUNNING_MODAL', 'PASS_THROUGH'}``, which Blender hands to no later handler either). Neither is ever cleared;
``consumed_input_age`` says how long ago the operator last kept one (checked against this script's own clock: it
counts from the kept event and grows while later events are passed on). Timers and window events set nothing.

Real modal operators in the window's real handler stack, driven with ``Window.event_simulate`` and read from
``Window.modal_operators``, in both directions:

* started and before any input, every operator is unanswered and unconsumed (age below zero);
* an observer that passes every event through and a worker that keeps only its timer's events become answered by
  the first pointer move or key and stay unconsumed;
* an operator that answers ``{'RUNNING_MODAL', 'PASS_THROUGH'}`` (Blender's "modal unhandled, break"; Blender's own
  extension installer answers so while it downloads) is consumed by the first input it gets;
* a tool that keeps one key is answered but unconsumed while it passes pointer moves and other keys through,
  consumed by the first press of its key, and stays so while it passes later events through;
* a macro: the window's modal operator is the macro, and the macro carries the marks of what its step did;
* one of Blender's own modal tools (Scale, on an object this suite adds and selects) starts unanswered, and its
  first event, a key its modal key map renames (X, the axis), makes it answered and consumed: an event counts by
  the type it had before the key map renamed it. An observer started in front of Scale reads Scale's marks as each
  event arrives, so the marks are known to come from that key and not from a pointer move Blender sent after it.

The rules themselves (which event types are a person's input, which results keep one) are covered for every event
type Blender names by ``wm_angelo_consumed_input_test.cc``.
"""

import os, sys  # noqa: E401  The suite's own folder, for its first import:
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suite_isolation  # noqa: E402,F401  First: no real provider login (provider_isolation.py).

import json
import tempfile
import time
import traceback
from pathlib import Path

import bpy

import qa_boot  # noqa: E402  (Angelo's start-up is over: the shared boot gate)
import qa_waits  # noqa: E402

OUT = Path(os.environ.get("ANGELO_QA_DIR") or Path(tempfile.gettempdir(), "angelo-consumed-input-qa"))
OUT.mkdir(parents=True, exist_ok=True)
RECEIPT = OUT / "consumed-input.json"
LIMIT = 30.0 * qa_waits.timeout_scale()   # Seconds one phase may wait for its events to arrive.
# Keys no key map binds: passing one on changes nothing in the app. The tool keeps one, the macro's step another.
PASSED_KEY, KEPT_KEY, MACRO_KEY = 'F13', 'F14', 'F15'

seen = {}    # Operator name -> {event type: times its modal() got one}.
state = {"phase": "boot", "checks": {}, "marks": {}, "witnessed": [],
         "fixture": "Python modal operators and Blender's Scale with synthetic events; no providers or inference"}
phase_started = [time.monotonic()]
worker_timer = []


class _Probe:
    """A modal operator that counts the events it gets; ``answer`` is the result of its ``modal()``."""
    bl_options = {'INTERNAL'}

    def invoke(self, context, _event):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, _context, event):
        counts = seen.setdefault(self.bl_label, {})
        counts[event.type] = counts.get(event.type, 0) + 1
        return self.answer(event)


class QA_OT_consumed_input_observer(_Probe, bpy.types.Operator):
    bl_idname, bl_label = "qa.consumed_input_observer", "observer"

    def answer(self, _event):
        return {'PASS_THROUGH'}


class QA_OT_consumed_input_timer_worker(_Probe, bpy.types.Operator):
    """Keeps its timer's events, as a worker that redraws or polls on a timer does."""
    bl_idname, bl_label = "qa.consumed_input_timer_worker", "timer_worker"

    def invoke(self, context, event):
        worker_timer.append(context.window_manager.event_timer_add(0.05, window=context.window))
        return super().invoke(context, event)

    def answer(self, event):
        return {'RUNNING_MODAL'} if event.type == 'TIMER' else {'PASS_THROUGH'}


class QA_OT_consumed_input_both(_Probe, bpy.types.Operator):
    bl_idname, bl_label = "qa.consumed_input_both", "running_and_pass_through"

    def answer(self, _event):
        return {'RUNNING_MODAL', 'PASS_THROUGH'}


class QA_OT_consumed_input_one_key(_Probe, bpy.types.Operator):
    """An edit tool in small: it keeps the presses of one key and passes everything else on."""
    bl_idname, bl_label = "qa.consumed_input_one_key", "one_key"

    def answer(self, event):
        return {'RUNNING_MODAL'} if event.type == KEPT_KEY else {'PASS_THROUGH'}


class QA_OT_consumed_input_macro_step(_Probe, bpy.types.Operator):
    """The modal step of the macro below: it keeps its own key."""
    bl_idname, bl_label = "qa.consumed_input_macro_step", "macro_step"

    def answer(self, event):
        return {'RUNNING_MODAL'} if event.type == MACRO_KEY else {'PASS_THROUGH'}


class QA_OT_consumed_input_witness(_Probe, bpy.types.Operator):
    """Started after Blender's Scale, so it gets each event first: it passes every one on and notes what Scale's
    marks say at that moment, which is what the events before it left."""
    bl_idname, bl_label = "qa.consumed_input_witness", "witness"

    def answer(self, event):
        state["witnessed"].append([event.type, list(marks().get(SCALE, ()))])
        return {'PASS_THROUGH'}


class QA_OT_consumed_input_macro(bpy.types.Macro):
    """A macro of one modal step: the window's modal handler holds the macro, not the step."""
    bl_idname, bl_label = "qa.consumed_input_macro", "macro"
    bl_options = {'INTERNAL'}


# Started in this order, and a window's newest modal handler gets an event first: the macro, the one-key tool, the
# worker, the observer, then the operator that answers with both. That one is last because Blender hands an event
# answered ``{'RUNNING_MODAL', 'PASS_THROUGH'}`` to no later handler ("modal unhandled, break",
# wm_handler_operator_call):
# behind it are only the app's own handlers, which the passed-on key and pointer moves then never reach.
PROBES = (QA_OT_consumed_input_both, QA_OT_consumed_input_observer, QA_OT_consumed_input_timer_worker,
          QA_OT_consumed_input_one_key, QA_OT_consumed_input_macro)
BOTH, OBSERVER, WORKER, ONE_KEY, MACRO = (probe.bl_idname for probe in PROBES)
SCALE = "transform.resize"
LABELS = ("running_and_pass_through", "observer", "timer_worker", "one_key", "macro_step")   # In ``seen``.


def window():
    return bpy.context.window_manager.windows[0]


def name_of(operator):
    """An operator's name as ``bpy.ops`` spells it, whichever spelling ``bl_idname`` gives ("QA_OT_x" or "qa.x")."""
    return operator.bl_idname.replace("_OT_", ".").lower()


def marks():
    """What every modal operator of the window says, read from the window's own collection:
    name -> (has_answered_input, has_consumed_input, consumed_input_age >= 0)."""
    return {name_of(operator): (operator.has_answered_input, operator.has_consumed_input,
                                operator.consumed_input_age >= 0.0) for operator in window().modal_operators}


UNKNOWN, WATCHES, KEEPS = (False, False, False), (True, False, False), (True, True, True)
CLOCKS = 0.25   # Seconds two clocks (Blender's, and this script's) may disagree over one of these phases.


def age(probe):
    """``consumed_input_age`` of ``probe``'s operator: seconds since it last kept an input event."""
    return next(operator.consumed_input_age for operator in window().modal_operators
                if name_of(operator) == probe.bl_idname)


def got(label, *kinds):
    return all(seen.get(label, {}).get(kind) for kind in kinds)


def press_and_release(key):
    """A key as a person presses it: down and up. A press left held would make every later pointer move a drag
    (test_native_qa_input's lint reads it so, and so does Blender)."""
    target = window()
    target.event_simulate(type=key, value='PRESS')
    target.event_simulate(type=key, value='RELEASE')


def pointer_and_key(key):
    target = window()
    target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=target.width // 2, y=target.height // 2)
    target.event_simulate(type=key, value='PRESS')
    target.event_simulate(type=key, value='RELEASE')


def enter(phase):
    state["phase"] = phase
    phase_started[0] = time.monotonic()


def waiting(what):
    """Poll again, or fail once the phase has waited ``LIMIT`` seconds for ``what``."""
    assert time.monotonic() - phase_started[0] < LIMIT, "%s: %s did not happen in %.0f s; seen %r" % (
        state["phase"], what, LIMIT, seen)
    return 0.05


def finish(success, **extra):
    state.update(success=success, seen=seen, **extra)
    for timer in worker_timer:
        bpy.context.window_manager.event_timer_remove(timer)
    RECEIPT.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print("ANGELO_CONSUMED_INPUT_GUI", json.dumps(state), flush=True)
    bpy.ops.wm.quit_blender()


def tick():
    checks = state["checks"]
    try:
        if state["phase"] == "boot":
            for probe in (QA_OT_consumed_input_macro_step, QA_OT_consumed_input_witness, *PROBES):
                bpy.utils.register_class(probe)
            QA_OT_consumed_input_macro.define("QA_OT_consumed_input_macro_step")
            for probe in PROBES:
                with bpy.context.temp_override(window=window()):
                    result = getattr(bpy.ops.qa, probe.bl_idname.split(".")[1])('INVOKE_DEFAULT')
                assert result == {'RUNNING_MODAL'}, (probe.bl_idname, result)
            found = state["marks"]["started"] = marks()
            checks["every_probe_is_a_modal_handler_of_the_window"] = all(probe.bl_idname in found for probe in PROBES)
            checks["the_macro_is_the_handlers_operator_not_its_step"] = (
                MACRO in found and QA_OT_consumed_input_macro_step.bl_idname not in found)
            checks["before_any_input_nothing_is_known_of_an_operator"] = all(
                found[probe.bl_idname] == UNKNOWN for probe in PROBES)
            pointer_and_key(PASSED_KEY)
            enter("passed_on")
            return 0.05
        if state["phase"] == "passed_on":
            if not (all(got(label, 'MOUSEMOVE', PASSED_KEY) for label in LABELS) and got("timer_worker", 'TIMER')):
                return waiting("every probe getting the pointer move and the key, and the worker its timer")
            found = state["marks"]["passed_on"] = marks()
            checks["an_observer_that_passes_input_through_is_answered_and_unconsumed"] = found[OBSERVER] == WATCHES
            checks["keeping_only_timer_events_is_answered_and_unconsumed"] = found[WORKER] == WATCHES
            checks["a_tool_is_answered_and_unconsumed_while_it_passes_input_through"] = found[ONE_KEY] == WATCHES
            checks["a_macro_whose_step_passes_input_through_is_answered_and_unconsumed"] = found[MACRO] == WATCHES
            checks["running_modal_with_pass_through_keeps_the_event_and_is_consumed"] = found[BOTH] == KEEPS
            press_and_release(KEPT_KEY)
            state["key_sent"] = time.monotonic()
            enter("kept")
            return 0.05
        if state["phase"] == "kept":
            if seen.get("one_key", {}).get(KEPT_KEY, 0) < 2:
                return waiting("the one-key tool getting its key's press and release")
            found = state["marks"]["kept"] = marks()
            checks["the_first_input_event_a_tool_keeps_marks_it_consumed"] = found[ONE_KEY] == KEEPS
            # The age counts from the kept key: no older than the time since this script sent it.
            state["age_kept"], state["age_read"] = age(QA_OT_consumed_input_one_key), time.monotonic()
            checks["the_age_counts_from_the_kept_event"] = (
                0.0 <= state["age_kept"] <= state["age_read"] - state["key_sent"] + CLOCKS)
            checks["a_kept_event_marks_only_the_operator_that_kept_it"] = (
                found[OBSERVER] == WATCHES and found[WORKER] == WATCHES and found[MACRO] == WATCHES)
            state["moves_before"] = seen["one_key"].get('MOUSEMOVE', 0)
            target = window()
            target.event_simulate(type='MOUSEMOVE', value='NOTHING', x=target.width // 2 + 8, y=target.height // 2)
            press_and_release(MACRO_KEY)
            enter("kept_then_passed")
            return 0.05
        if state["phase"] == "kept_then_passed":
            if (seen["one_key"].get('MOUSEMOVE', 0) <= state["moves_before"]
                    or seen.get("macro_step", {}).get(MACRO_KEY, 0) < 2):
                return waiting("the one-key tool passing a later pointer move through, and the macro's step its key")
            found = state["marks"]["kept_then_passed"] = marks()
            checks["the_marks_stay_once_set"] = found[ONE_KEY] == KEEPS
            # Only passed-on events since: the age has grown by the time that went by, and is not reset.
            later, now = age(QA_OT_consumed_input_one_key), time.monotonic()
            state["age_later"] = later
            checks["the_age_grows_while_nothing_more_is_kept"] = (
                later > state["age_kept"]
                and abs((later - state["age_kept"]) - (now - state["age_read"])) <= CLOCKS)
            checks["a_macro_carries_what_its_step_kept"] = found[MACRO] == KEEPS
            view = next(area for area in window().screen.areas if area.type == 'VIEW_3D')
            region = next(region for region in view.regions if region.type == 'WINDOW')
            with bpy.context.temp_override(window=window(), area=view, region=region):
                # Its own object, added selected and active: nothing of the start-up file is relied on.
                assert bpy.ops.mesh.primitive_cube_add(size=1.0) == {'FINISHED'}
                state["scaled"] = bpy.context.active_object.name
                state["scale_before"] = tuple(bpy.context.active_object.scale)
                result = bpy.ops.transform.resize('INVOKE_DEFAULT')
            assert result == {'RUNNING_MODAL'}, result
            with bpy.context.temp_override(window=window()):
                assert bpy.ops.qa.consumed_input_witness('INVOKE_DEFAULT') == {'RUNNING_MODAL'}
            checks["blenders_own_tool_starts_with_nothing_known"] = marks()[SCALE] == UNKNOWN
            # Its first event, queued now and so ahead of any pointer move Blender may send itself: X, which the
            # transform modal key map renames to "constrain to the X axis".
            press_and_release('X')
            enter("native_tool")
            return 0.05
        if state["phase"] == "native_tool":
            found = marks()
            assert SCALE in found, "Scale ended before its first event"
            witnessed = list(state["witnessed"])
            at = next((index for index, (kind, _) in enumerate(witnessed) if kind == 'X'), None)
            if at is None or found[SCALE] != KEEPS:
                return waiting("Blender's Scale keeping the key its modal key map renames")
            # The witness is in front of Scale: what it read when X arrived is what earlier events left, and what
            # it read at the next event (a timer, or a pointer move Blender sent itself) is what X alone left.
            # With no next event yet, nothing but X has reached Scale and the marks read above are X's.
            checks["the_renamed_key_was_scales_first_input"] = witnessed[at][1] == list(UNKNOWN)
            checks["an_event_a_modal_key_map_renames_counts_by_its_own_type"] = (
                witnessed[at + 1][1] == list(KEEPS) if len(witnessed) > at + 1 else True)
            press_and_release('ESC')
            enter("native_tool_ended")
            return 0.05
        if state["phase"] == "native_tool_ended":
            if SCALE in marks():
                return waiting("Escape ending Blender's Scale")
            checks["the_cancelled_tool_changed_nothing"] = (
                tuple(bpy.data.objects[state["scaled"]].scale) == tuple(state["scale_before"]))
            state["marks"]["end"] = marks()
            finish(all(checks.values()))
            return None
    except Exception as error:   # The receipt carries the failure; the runner reads it.
        print(traceback.format_exc(), flush=True)
        finish(False, error=repr(error), traceback=traceback.format_exc())
        return None
    return 0.05


bpy.app.timers.register(qa_boot.gated(tick), first_interval=0.5)
