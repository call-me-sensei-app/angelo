/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "testing/testing.h"

#include "BLI_set.hh"

#include "DNA_windowmanager_types.h"

#include "RNA_enum_types.hh"

#include "WM_types.hh"

#include "wm_angelo_consumed_input.hh"

namespace blender::wm::angelo::tests {

/** One event type of every kind a person's input device sends. */
static const wmEventType PERSON_INPUT[] = {
    /* Mouse buttons, moves and wheel. */
    LEFTMOUSE,
    MIDDLEMOUSE,
    RIGHTMOUSE,
    BUTTON4MOUSE,
    BUTTON7MOUSE,
    MOUSEMOVE,
    INBETWEEN_MOUSEMOVE,
    WHEELUPMOUSE,
    WHEELDOWNMOUSE,
    WHEELINMOUSE,
    WHEELOUTMOUSE,
    WHEELLEFTMOUSE,
    WHEELRIGHTMOUSE,
    /* Trackpad gestures; smart zoom lies outside the mouse range. A tablet's pen and eraser. */
    MOUSEPAN,
    MOUSEZOOM,
    MOUSEROTATE,
    MOUSESMARTZOOM,
    TABLET_STYLUS,
    TABLET_ERASER,
    /* Keyboard: letters, modifiers, navigation and function keys; an input method. */
    EVT_AKEY,
    EVT_ZKEY,
    EVT_ZEROKEY,
    EVT_LEFTCTRLKEY,
    EVT_LEFTSHIFTKEY,
    EVT_OSKEY,
    EVT_ESCKEY,
    EVT_RETKEY,
    EVT_SPACEKEY,
    EVT_TABKEY,
    EVT_F1KEY,
    EVT_F24KEY,
    EVT_PAD0,
    WM_IME_COMPOSITE_START,
    WM_IME_COMPOSITE_EVENT,
    WM_IME_COMPOSITE_END,
    /* NDOF motion and buttons; an XR controller's action. */
    NDOF_MOTION,
    NDOF_BUTTON_MENU,
    NDOF_BUTTON_SAVE_V3,
    EVT_XR_ACTION,
};

/** Every kind of event that is not a person's input. Explicit, so that a type a later Blender
 * adds is input until someone puts it here (#EveryEventTypeBlenderNamesIsDecided). */
static const wmEventType NOT_PERSON_INPUT[] = {
    EVENT_NONE,
    WINDEACTIVATE,
    TIMER,
    TIMER0,
    TIMER1,
    TIMER2,
    TIMERJOBS,
    TIMERAUTOSAVE,
    TIMERREPORT,
    TIMERREGION,
    TIMERNOTIFIER,
    EVT_ACTIONZONE_AREA,
    EVT_ACTIONZONE_REGION,
    EVT_ACTIONZONE_REGION_QUAD,
    EVT_ACTIONZONE_FULLSCREEN,
    EVT_FILESELECT,
    EVT_BUT_OPEN,
    EVT_MODAL_MAP,
    EVT_DROP,
    EVT_BUT_CANCEL,
    EVT_GIZMO_UPDATE,
};

/** Every result with which a modal handler keeps its event from later handlers, and the others. */
static const wmOperatorStatus KEPT[] = {
    OPERATOR_RUNNING_MODAL,
    OPERATOR_FINISHED,
    OPERATOR_CANCELLED,
    OPERATOR_INTERFACE,
    OPERATOR_RUNNING_MODAL | OPERATOR_HANDLED,
    /* Blender's "modal unhandled, break": said to be unhandled, given to no later handler. */
    OPERATOR_RUNNING_MODAL | OPERATOR_PASS_THROUGH,
};
static const wmOperatorStatus PASSED_ON[] = {
    OPERATOR_PASS_THROUGH,
    OPERATOR_FINISHED | OPERATOR_PASS_THROUGH,
    OPERATOR_CANCELLED | OPERATOR_PASS_THROUGH,
};

static wmOperator answered(const wmEventType event_type, const wmOperatorStatus retval)
{
  wmOperator op;
  wm_angelo_operator_modal_answered(&op, event_type, retval, 12.5);
  return op;
}

static bool has(const wmOperator &op, const eOperator_Flag flag)
{
  return (op.flag & flag) != 0;
}

TEST(wm_angelo_consumed_input, InputDevicesAreAPersonsInput)
{
  for (const wmEventType event_type : PERSON_INPUT) {
    EXPECT_TRUE(wm_angelo_event_type_is_person_input(event_type)) << "event type " << event_type;
  }
}

TEST(wm_angelo_consumed_input, TimersAndBlendersOwnEventsAreNot)
{
  for (const wmEventType event_type : NOT_PERSON_INPUT) {
    EXPECT_FALSE(wm_angelo_event_type_is_person_input(event_type)) << "event type " << event_type;
  }
}

TEST(wm_angelo_consumed_input, EveryEventTypeBlenderNamesIsDecided)
{
  /* Every event type Blender's own list names is input, or is in the table above. A type a later
   * Blender adds fails here until someone decides which it is. */
  Set<int> not_input;
  for (const wmEventType event_type : NOT_PERSON_INPUT) {
    not_input.add(int(event_type));
  }
  /* A key map's "text input", never an event's type. */
  not_input.add(int(KM_TEXTINPUT));
  int named = 0;
  for (const EnumPropertyItem *item = rna_enum_event_type_items; item->identifier; item++) {
    if (item->identifier[0] == '\0') {
      continue; /* A separator. */
    }
    named++;
    const bool expected = !not_input.contains(item->value);
    EXPECT_EQ(wm_angelo_event_type_is_person_input(wmEventType(item->value)), expected)
        << item->identifier << " (" << item->value << ")";
  }
  EXPECT_GT(named, 150) << "the list of event types was not walked";
  /* The two ends of the ranges decided by exclusion. */
  EXPECT_TRUE(wm_angelo_event_type_is_person_input(wmEventType(EVT_ACTIONZONE_AREA - 1)));
  EXPECT_FALSE(wm_angelo_event_type_is_person_input(wmEventType(EVT_XR_ACTION + 1)));
  EXPECT_FALSE(wm_angelo_event_type_is_person_input(wmEventType(-1)));
}

TEST(wm_angelo_consumed_input, TheResultsThatKeepAnEventFromLaterHandlers)
{
  for (const wmOperatorStatus retval : KEPT) {
    EXPECT_TRUE(wm_angelo_modal_result_keeps_event(retval)) << "result " << int(retval);
  }
  for (const wmOperatorStatus retval : PASSED_ON) {
    EXPECT_FALSE(wm_angelo_modal_result_keeps_event(retval)) << "result " << int(retval);
  }
}

TEST(wm_angelo_consumed_input, AnOperatorStartsWithNeitherMark)
{
  const wmOperator op;
  EXPECT_FALSE(has(op, OP_HAS_ANSWERED_INPUT));
  EXPECT_FALSE(has(op, OP_HAS_CONSUMED_INPUT));
  EXPECT_EQ(op.consumed_input_time, 0.0);
}

TEST(wm_angelo_consumed_input, KeepingAPersonsInputMarksAnsweredAndConsumed)
{
  for (const wmEventType event_type : PERSON_INPUT) {
    for (const wmOperatorStatus retval : KEPT) {
      const wmOperator op = answered(event_type, retval);
      EXPECT_TRUE(has(op, OP_HAS_ANSWERED_INPUT)) << event_type << ", result " << int(retval);
      EXPECT_TRUE(has(op, OP_HAS_CONSUMED_INPUT)) << event_type << ", result " << int(retval);
      EXPECT_EQ(op.consumed_input_time, 12.5) << event_type << ", result " << int(retval);
    }
  }
}

TEST(wm_angelo_consumed_input, PassingAPersonsInputOnMarksAnsweredOnly)
{
  /* An observer: known to have seen input, and known not to keep it. */
  for (const wmEventType event_type : PERSON_INPUT) {
    for (const wmOperatorStatus retval : PASSED_ON) {
      const wmOperator op = answered(event_type, retval);
      EXPECT_TRUE(has(op, OP_HAS_ANSWERED_INPUT)) << event_type << ", result " << int(retval);
      EXPECT_FALSE(has(op, OP_HAS_CONSUMED_INPUT)) << event_type << ", result " << int(retval);
      EXPECT_EQ(op.consumed_input_time, 0.0) << event_type << ", result " << int(retval);
    }
  }
}

TEST(wm_angelo_consumed_input, ATimerOrBlendersOwnEventMarksNothing)
{
  /* A worker that keeps its timer's events, a file selector's answer, a renamed modal key map
   * event: whatever the answer, nothing is learned about the operator and a person's input. */
  for (const wmEventType event_type : NOT_PERSON_INPUT) {
    for (const wmOperatorStatus retval : KEPT) {
      const wmOperator op = answered(event_type, retval);
      EXPECT_EQ(op.flag, eOperator_Flag(0)) << event_type << ", result " << int(retval);
      EXPECT_EQ(op.consumed_input_time, 0.0) << event_type << ", result " << int(retval);
    }
    for (const wmOperatorStatus retval : PASSED_ON) {
      EXPECT_EQ(answered(event_type, retval).flag, eOperator_Flag(0)) << event_type;
    }
  }
}

TEST(wm_angelo_consumed_input, TheMarksStayTheTimeMovesAndOtherFlagsAreLeftAlone)
{
  wmOperator op;
  const eOperator_Flag before = OP_IS_INVOKE | OP_IS_MODAL_GRAB_CURSOR;
  op.flag = before;
  wm_angelo_operator_modal_answered(&op, TIMER, OPERATOR_RUNNING_MODAL, 1.0);
  EXPECT_EQ(op.flag, before);
  wm_angelo_operator_modal_answered(&op, MOUSEMOVE, OPERATOR_PASS_THROUGH, 2.0);
  EXPECT_EQ(op.flag, before | OP_HAS_ANSWERED_INPUT);
  EXPECT_EQ(op.consumed_input_time, 0.0);
  wm_angelo_operator_modal_answered(&op, LEFTMOUSE, OPERATOR_RUNNING_MODAL, 3.0);
  EXPECT_EQ(op.flag, before | OP_HAS_ANSWERED_INPUT | OP_HAS_CONSUMED_INPUT);
  EXPECT_EQ(op.consumed_input_time, 3.0);
  /* Later events it passes on and timers it keeps take no mark away and leave the time. */
  wm_angelo_operator_modal_answered(&op, MOUSEMOVE, OPERATOR_PASS_THROUGH, 4.0);
  wm_angelo_operator_modal_answered(&op, TIMER, OPERATOR_RUNNING_MODAL, 5.0);
  wm_angelo_operator_modal_answered(
      &op, EVT_ESCKEY, OPERATOR_CANCELLED | OPERATOR_PASS_THROUGH, 6.0);
  EXPECT_EQ(op.flag, before | OP_HAS_ANSWERED_INPUT | OP_HAS_CONSUMED_INPUT);
  EXPECT_EQ(op.consumed_input_time, 3.0);
  /* The next input it keeps is the new last one. */
  wm_angelo_operator_modal_answered(&op, EVT_XKEY, OPERATOR_RUNNING_MODAL, 7.0);
  EXPECT_EQ(op.consumed_input_time, 7.0);
}

}  // namespace blender::wm::angelo::tests
