/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup wm
 *
 * Angelo: what a modal operator has done with a person's input (`wm_angelo_consumed_input.cc`,
 * #OP_HAS_ANSWERED_INPUT, #OP_HAS_CONSUMED_INPUT, `Operator.has_answered_input`,
 * `Operator.has_consumed_input`, `Operator.consumed_input_age`).
 */

#pragma once

#include "DNA_windowmanager_enums.h"

#include "wm_event_types.hh"

namespace blender {

struct wmOperator;

/** Whether an event of \a event_type comes from a person's input device: the keyboard (an input
 * method's composition included), a mouse, trackpad or tablet (buttons, moves, wheel, gestures),
 * an NDOF device or an XR controller. Decided by exclusion below #EVT_ACTIONZONE_AREA (0x5000),
 * so a type a later Blender adds there counts as input until someone decides otherwise. From
 * 0x5000 up, where Blender keeps its internal events, only #EVT_XR_ACTION is input: a type
 * added there is not, and the GTest that walks every named type fails until it is listed. Not
 * input are no event at all, timers, the window losing focus, and the events Blender makes to
 * talk to itself (file selector, drop, action zone, gizmo, a button's own, a renamed modal key
 * map event). A pointer move Blender sends
 * itself (#WM_event_add_mousemove, after a layout change or an undo) has the type of a person's
 * move and counts as one. */
bool wm_angelo_event_type_is_person_input(wmEventType event_type);

/** Whether a modal handler that answered \a retval kept its event from every later handler:
 * every result without #OPERATOR_PASS_THROUGH, and exactly `OPERATOR_RUNNING_MODAL |
 * OPERATOR_PASS_THROUGH`, which #wm_handler_operator_call treats as "modal unhandled, break". */
bool wm_angelo_modal_result_keeps_event(wmOperatorStatus retval);

/** Note what \a op's modal handler answered (\a retval) to an event of \a event_type, the type
 * the event had before a modal key map renamed it, at time \a now (#BLI_time_now_seconds). For a
 * person's input the operator is marked #OP_HAS_ANSWERED_INPUT; when it also kept the event,
 * #OP_HAS_CONSUMED_INPUT, and #wmOperator.consumed_input_time becomes \a now. */
void wm_angelo_operator_modal_answered(wmOperator *op,
                                       wmEventType event_type,
                                       wmOperatorStatus retval,
                                       double now);

}  // namespace blender
