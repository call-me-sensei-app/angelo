/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: which modal operators a person is working with. Angelo holds its own edits of the
 * document while a person's tool runs. A running modal operator's type does not say whether it is
 * such a tool: an add-on's status display or timer-driven worker is a modal handler of the window
 * exactly like an add-on's edit tool. What tells them apart is what they do with the person's
 * input, and the event system sees that: an observer passes every input event on
 * (#OPERATOR_PASS_THROUGH), a tool keeps the ones it works with.
 *
 * So #wm_handler_operator_call reports each answer of a modal handler here, and the handler's
 * operator (the one `Window.modal_operators` lists; for a macro, the macro) carries two runtime
 * marks that are never cleared (operators are not written to files):
 *
 * - #OP_HAS_ANSWERED_INPUT (`Operator.has_answered_input`): its modal handler was given a
 *   person's input event and returned, whatever it answered. Before that nothing is known about
 *   the operator: its `invoke()` may already have edited, and a tool waiting for its first click
 *   reads the same as an operator that will only ever watch. A reader that must not act under a
 *   running tool treats an operator without this mark as one.
 *   The mark closes the time from the operator's start to its first input event, not to the
 *   first event it keeps: a tool that passes pointer moves through and keeps only its click
 *   reads as answered and not consumed, like an observer, between its first move and that click.
 *   An operator started with no person at the machine stays unanswered until a person's input
 *   reaches it.
 * - #OP_HAS_CONSUMED_INPUT (`Operator.has_consumed_input`): it kept a person's input event, so no
 *   later handler got it. #wmOperator.consumed_input_time is when it last did
 *   (`Operator.consumed_input_age`): the mark alone cannot tell a tool in use from a panel's
 *   long-lived operator that took one click an hour ago.
 *
 * Both are set inside the handler call that returns the operator's answer, before any other
 * handler, timer or script runs: nothing on the main thread sees an answered event with the marks
 * unset.
 *
 * - A person's input is judged by the event's type before a modal key map renames it to
 *   #EVT_MODAL_MAP, and by exclusion (#wm_angelo_event_type_is_person_input).
 * - "Kept" follows what Blender does with the answer: a result without #OPERATOR_PASS_THROUGH,
 *   and exactly `OPERATOR_RUNNING_MODAL | OPERATOR_PASS_THROUGH`. Blender calls that one "modal
 *   unhandled" and still hands the event to no later handler, so the person's input was taken
 *   from everything else. Blender's extension installer answers so to every key but Escape (which
 *   it keeps) and to every pointer move while it downloads, and is marked by the first of them.
 * - A pointer move Blender sends itself (#WM_event_add_mousemove: after a layout change, an undo,
 *   a finished download) has the type of a person's move and counts as one. An operator that
 *   keeps pointer moves is marked by it without the person touching anything; one that passes
 *   every event through is still never marked consumed.
 */

#include "DNA_windowmanager_types.h"

#include "wm_angelo_consumed_input.hh"

namespace blender {

bool wm_angelo_event_type_is_person_input(const wmEventType event_type)
{
  if (event_type <= EVENT_NONE || event_type == WINDEACTIVATE || ISTIMER(event_type)) {
    return false;
  }
  /* Blender's own events start at the action zones; the XR controller's action lies among them. */
  if (event_type >= EVT_ACTIONZONE_AREA) {
    return event_type == EVT_XR_ACTION;
  }
  return true;
}

bool wm_angelo_modal_result_keeps_event(const wmOperatorStatus retval)
{
  /* As the end of #wm_handler_operator_call: these are the answers it breaks the handler loop
   * for. */
  return (retval & OPERATOR_PASS_THROUGH) == 0 ||
         retval == (OPERATOR_RUNNING_MODAL | OPERATOR_PASS_THROUGH);
}

void wm_angelo_operator_modal_answered(wmOperator *op,
                                       const wmEventType event_type,
                                       const wmOperatorStatus retval,
                                       const double now)
{
  if (!wm_angelo_event_type_is_person_input(event_type)) {
    return;
  }
  op->flag |= OP_HAS_ANSWERED_INPUT;
  if (wm_angelo_modal_result_keeps_event(retval)) {
    op->flag |= OP_HAS_CONSUMED_INPUT;
    op->consumed_input_time = now;
  }
}

}  // namespace blender
