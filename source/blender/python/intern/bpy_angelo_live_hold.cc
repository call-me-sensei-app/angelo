/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The live view's input hold and the lead's own window capture (`_bpy._angelo_live.hold`, `allow`, `held`,
 * `window_pixels`): see `wm_angelo_input_hold.cc`; a worker's own size of its part (`depsgraph_size`) and
 * whether a view sees the Main picture's geometry (`depsgraph_sees`); and where a
 * 3D view's own text info ends (`text_info_end`, `ED_view3d_angelo.hh`). Main thread only.
 */

#include <Python.h>

#include <string>

#include "BLI_vector.hh"

#include "MEM_guardedalloc.h"

#include "ED_view3d_angelo.hh"

#include "WM_angelo_input_hold.hh"

#include "../generic/py_capi_utils.hh"
#include "../mathutils/mathutils.hh"

#include "bpy_angelo_live_part.hh"
#include "bpy_capi_utils.hh"

namespace blender {

PyObject *live_hold(PyObject * /*self*/, PyObject *args)
{
  PyObject *py_window, *py_area;
  double seconds;
  if (!PyArg_ParseTuple(args, "OOd:hold", &py_window, &py_area, &seconds) || !live_require_main_thread()) {
    return nullptr;
  }
  const wmWindow *win = static_cast<const wmWindow *>(PyC_RNA_AsPointer(py_window, "Window"));
  if (win == nullptr) {
    return nullptr;
  }
  const ScrArea *area = nullptr;
  if (py_area != Py_None) {
    area = static_cast<const ScrArea *>(PyC_RNA_AsPointer(py_area, "Area"));
    if (area == nullptr) {
      return nullptr;
    }
  }
  WM_angelo_input_hold_set(win, area, area != nullptr ? seconds : 0.0);
  Py_RETURN_NONE;
}

PyObject *live_release_holds(PyObject * /*self*/, PyObject * /*args*/)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  WM_angelo_input_hold_clear();
  Py_RETURN_NONE;
}

PyObject *live_allow(PyObject * /*self*/, PyObject *idnames)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  PyObject *items = PySequence_Fast(idnames, "allow(idnames): expected a sequence of operator names");
  if (items == nullptr) {
    return nullptr;
  }
  Vector<std::string> names;
  const Py_ssize_t count = PySequence_Fast_GET_SIZE(items);
  for (Py_ssize_t index = 0; index < count; index++) {
    const char *name = PyUnicode_AsUTF8(PySequence_Fast_GET_ITEM(items, index));
    if (name == nullptr) {
      Py_DECREF(items);
      return nullptr;
    }
    names.append(name);
  }
  Py_DECREF(items);
  WM_angelo_input_hold_allow(names);
  Py_RETURN_NONE;
}

PyObject *live_held(PyObject * /*self*/, PyObject *args)
{
  PyObject *py_window, *py_area;
  if (!PyArg_ParseTuple(args, "OO:held", &py_window, &py_area) || !live_require_main_thread()) {
    return nullptr;
  }
  const wmWindow *win = static_cast<const wmWindow *>(PyC_RNA_AsPointer(py_window, "Window"));
  const ScrArea *area = win != nullptr ? static_cast<const ScrArea *>(PyC_RNA_AsPointer(py_area, "Area")) :
                                         nullptr;
  if (area == nullptr) {
    return nullptr;
  }
  return PyBool_FromLong(WM_angelo_input_held(win, area));
}

PyObject *live_depsgraph_size(PyObject * /*self*/, PyObject *py_depsgraph)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  float min[3], max[3];
  if (!live_depsgraph_bounds(depsgraph, min, max)) {
    Py_RETURN_NONE;
  }
  return Py_BuildValue("(fff)", max[0] - min[0], max[1] - min[1], max[2] - min[2]);
}

PyObject *live_depsgraph_bounds_py(PyObject * /*self*/, PyObject *args)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  PyObject *py_depsgraph;
  int helpers_alone = 1;
  if (!PyArg_ParseTuple(args, "O|p:depsgraph_bounds", &py_depsgraph, &helpers_alone)) {
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  float min[3], max[3];
  if (!live_depsgraph_bounds(depsgraph, min, max, helpers_alone != 0)) {
    Py_RETURN_NONE;
  }
  return Py_BuildValue("((fff)(fff))", min[0], min[1], min[2], max[0], max[1], max[2]);
}

PyObject *live_depsgraph_sees_py(PyObject * /*self*/, PyObject *args)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  PyObject *py_depsgraph;
  MatrixObject *persmat;
  if (!PyArg_ParseTuple(args, "OO&:depsgraph_sees", &py_depsgraph, Matrix_Parse4x4, &persmat)) {
    return nullptr;
  }
  Depsgraph *depsgraph = static_cast<Depsgraph *>(PyC_RNA_AsPointer(py_depsgraph, "Depsgraph"));
  if (depsgraph == nullptr) {
    return nullptr;
  }
  return PyBool_FromLong(
      live_depsgraph_sees(depsgraph, reinterpret_cast<const float (*)[4]>(persmat->matrix)));
}

PyObject *live_window_pixels(PyObject * /*self*/, PyObject *py_window)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  wmWindow *win = static_cast<wmWindow *>(PyC_RNA_AsPointer(py_window, "Window"));
  if (win == nullptr) {
    return nullptr;
  }
  bContext *C = BPY_context_get();
  int size[2];
  uint8_t *pixels = WM_angelo_window_pixels_redrawn(C, win, size);
  if (pixels == nullptr) {
    PyErr_SetString(PyExc_RuntimeError, "The window could not be drawn offscreen");
    return nullptr;
  }
  PyObject *data = PyBytes_FromStringAndSize(reinterpret_cast<const char *>(pixels),
                                             Py_ssize_t(size[0]) * size[1] * 4);
  MEM_delete(pixels);
  if (data == nullptr) {
    return nullptr;
  }
  return Py_BuildValue("(iiN)", size[0], size[1], data);
}

PyObject *live_text_info_end(PyObject * /*self*/, PyObject *py_region)
{
  if (!live_require_main_thread()) {
    return nullptr;
  }
  const ARegion *region = static_cast<const ARegion *>(PyC_RNA_AsPointer(py_region, "Region"));
  if (region == nullptr) {
    return nullptr;
  }
  const std::optional<int> end = ED_view3d_angelo_text_info_end(region);
  if (!end) {
    Py_RETURN_NONE;
  }
  return PyLong_FromLong(*end);
}

}  // namespace blender
