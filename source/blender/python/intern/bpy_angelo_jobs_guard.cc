/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Test seams for the grant guard's arming (doc/angelo/grant_operators.rst), only in an app started with
 * --enable-event-simulate, on the main thread. A GUI suite brings a window forward exactly as the operating system's
 * activation does (`WM_window_activate_simulate`), so a scenario no host and no single platform can order on demand
 * (an activation while a popup is open) runs anywhere; and it reads what the arming reads, so a failure says why.
 * Nothing here decides anything: the guard and the buttons are read and the window manager is driven, as a person's
 * operating system would.
 */

#include <Python.h>

#include "BLI_threads.hh"
#include "BLI_vector.hh"

#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_wm_runtime.hh"

#include "DNA_screen_types.h"
#include "DNA_windowmanager_types.h"

#include "UI_interface_c.hh"

#include "WM_api.hh"
#include "WM_types.hh"

#include "bpy_angelo_guard_authority.hh"
#include "bpy_angelo_guard_confine.hh"
#include "bpy_angelo_jobs_guard.hh"
#include "bpy_capi_utils.hh"

namespace blender {

namespace {

bool require_seam(const char *function)
{
  if (!BLI_thread_is_main()) {
    PyErr_Format(PyExc_RuntimeError, "%s requires Blender's main thread", function);
    return false;
  }
  if ((G.f & G_FLAG_EVENT_SIMULATE) == 0) {
    PyErr_Format(PyExc_RuntimeError, "%s needs --enable-event-simulate", function);
    return false;
  }
  if (BPY_angelo_guard_thread_confined()) { /* An agent script drives no window and reads no arming. */
    PyErr_Format(PyExc_PermissionError, "%s is not for an agent script", function);
    return false;
  }
  if (G.background) { /* No windows on screen, no operating system to bring one forward. */
    PyErr_Format(PyExc_RuntimeError, "%s needs a window on screen (not -b)", function);
    return false;
  }
  return true;
}

/** The open window `pointer` names (`Window.as_pointer()`), or null with a Python error set. */
wmWindow *window_from(PyObject *pointer, const char *function)
{
  void *wanted = PyLong_AsVoidPtr(pointer);
  if (PyErr_Occurred()) {
    return nullptr;
  }
  for (wmWindow &win : CTX_wm_manager(BPY_context_get())->windows) {
    if (&win == wanted) {
      return &win;
    }
  }
  PyErr_Format(PyExc_ValueError, "%s: no open window at that pointer", function);
  return nullptr;
}

PyObject *guard_window_activate_simulate(PyObject * /*self*/, PyObject *pointer)
{
  if (!require_seam("window_activate_simulate")) {
    return nullptr;
  }
  wmWindow *win = window_from(pointer, "window_activate_simulate");
  if (win == nullptr) {
    return nullptr;
  }
  if (!WM_window_activate_simulate(CTX_wm_manager(BPY_context_get()), win)) {
    PyErr_SetString(PyExc_RuntimeError, "window_activate_simulate: that window has no system window");
    return nullptr;
  }
  Py_RETURN_NONE;
}

PyObject *guard_window_guard(PyObject * /*self*/, PyObject *pointer)
{
  if (!require_seam("window_guard")) {
    return nullptr;
  }
  wmWindow *win = window_from(pointer, "window_guard");
  if (win == nullptr) {
    return nullptr;
  }
  PyObject *state = BPY_angelo_guard_readout(win);
  if (state == nullptr || state == Py_None) {
    return state;
  }
  if (win->runtime == nullptr || win->runtime->eventstate == nullptr) {
    return state; /* No event state yet: the guard's record alone. */
  }
  const int *xy = win->runtime->eventstate->xy;
  PyObject *event_xy = Py_BuildValue("(ii)", xy[0], xy[1]);
  if (event_xy == nullptr || PyDict_SetItemString(state, "event_xy", event_xy) != 0) {
    Py_XDECREF(event_xy);
    Py_DECREF(state);
    return nullptr;
  }
  Py_DECREF(event_xy);
  return state;
}

PyObject *guard_popup_button_guards(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!require_seam("popup_button_guards")) {
    return nullptr;
  }
  PyObject *list = PyList_New(0);
  if (list == nullptr) {
    return nullptr;
  }
  for (wmWindow &win : CTX_wm_manager(BPY_context_get())->windows) {
    const bScreen *screen = WM_window_get_active_screen(&win);
    if (screen == nullptr) {
      continue;
    }
    for (const ui::PopupButtonPlace &place : ui::popup_button_places(screen)) {
      PyObject *item = Py_BuildValue(
          "{s:N,s:s#,s:O,s:O,s:(iiii),s:(ffff),s:K,s:K,s:d,s:d}",
          "window", PyLong_FromVoidPtr(&win),
          "text", place.text.data(), Py_ssize_t(place.text.size()),
          "armed", place.armed ? Py_True : Py_False,
          "placed", place.placed ? Py_True : Py_False,
          "place", place.place_xmin, place.place_ymin, place.place_xmax, place.place_ymax,
          "rect", place.xmin, place.ymin, place.xmax, place.ymax,
          "epoch", static_cast<unsigned long long>(place.epoch),
          "frame", static_cast<unsigned long long>(place.frame),
          "since", place.since,
          "shown", BPY_angelo_guard_shown(&win, place.frame, place.since));
      if (item == nullptr || PyList_Append(list, item) != 0) {
        Py_XDECREF(item);
        Py_DECREF(list);
        return nullptr;
      }
      Py_DECREF(item);
    }
  }
  PyObject *result = PyList_AsTuple(list);
  Py_DECREF(list);
  return result;
}

PyObject *guard_pointer_region(PyObject * /*self*/, PyObject *args)
{
  if (!require_seam("pointer_region")) {
    return nullptr;
  }
  PyObject *pointer = nullptr;
  int xy[2] = {0, 0};
  if (!PyArg_ParseTuple(args, "Oii:pointer_region", &pointer, &xy[0], &xy[1])) {
    return nullptr;
  }
  wmWindow *win = window_from(pointer, "pointer_region");
  if (win == nullptr) {
    return nullptr;
  }
  const ARegion *region = WM_angelo_pointer_region(win, xy);
  if (region == nullptr) {
    Py_RETURN_NONE;
  }
  return PyLong_FromVoidPtr(const_cast<ARegion *>(region));
}

PyMethodDef methods[] = {
    {"window_activate_simulate",
     guard_window_activate_simulate,
     METH_O,
     "window_activate_simulate(window): (tests, --enable-event-simulate only, never an agent "
     "script, never -b) the window manager's handling of the operating system's activation of "
     "that window (its as_pointer()), by the very same function: the event state re-read from "
     "the system pointer, the guard's activation, the synthetic pointer move. Only the "
     "activation: a deactivation before it (which sets the guard's `returning`) is not "
     "simulated."},
    {"pointer_region",
     guard_pointer_region,
     METH_VARARGS,
     "pointer_region(window, x, y): (tests, --enable-event-simulate only) the region (its "
     "as_pointer()) a pointer event at (x, y) window pixels reaches, by the steps Blender's own "
     "handlers take and the grant guard's input feed asks, or None."},
    {"window_guard",
     guard_window_guard,
     METH_O,
     "window_guard(window): (tests, --enable-event-simulate only) what the grant guard's arming "
     "reads for that window now, as a dict (focus epoch, frames presented, the last real move, "
     "the pointer, the window manager's event position), or None with no record of it. Failure "
     "details only: nothing decides by it."},
    {"popup_button_guards",
     guard_popup_button_guards,
     METH_NOARGS,
     "popup_button_guards(): (tests, --enable-event-simulate only) every button of every open "
     "popup with its arming record: armed, placed, where it was placed and its rect (window "
     "pixels), its focus epoch, the frame and time it counts from and when that frame was "
     "shown (inf while it has not been)."},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace

PyMethodDef *BPY_angelo_jobs_guard_methods()
{
  return methods;
}

}  // namespace blender
