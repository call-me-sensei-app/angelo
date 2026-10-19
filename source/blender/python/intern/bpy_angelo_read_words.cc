/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#include "bpy_angelo_read_words.hh"

namespace blender::angelo_read_words {

static constexpr const char *texts[WORDS_NUM] = {
    "size", "channels", "depth", "is_float", "resolution", "file_format", "frame_duration"};

static PyObject **words(PyObject *module)
{
  return static_cast<PyObject **>(PyModule_GetState(module));
}

int traverse_state(PyObject *module, visitproc visit, void *arg)
{
  if (PyObject **state = words(module)) {
    for (int i = 0; i < WORDS_NUM; i++) {
      Py_VISIT(state[i]);
    }
  }
  return 0;
}

int clear_state(PyObject *module)
{
  if (PyObject **state = words(module)) {
    for (int i = 0; i < WORDS_NUM; i++) {
      Py_CLEAR(state[i]);
    }
  }
  return 0;
}

void free_state(void *module)
{
  clear_state(static_cast<PyObject *>(module));
}

bool init(PyObject *module)
{
  PyObject **state = words(module);
  if (state == nullptr) {
    PyErr_SetString(PyExc_SystemError, "Native read module has no state");
    return false;
  }
  for (int i = 0; i < WORDS_NUM; i++) {
    state[i] = PyUnicode_InternFromString(texts[i]);
    if (state[i] == nullptr) {
      return false;
    }
  }
  return true;
}

PyObject *get(PyObject *module, const Word word)
{
  return words(module)[word];
}

bool set_item(PyObject *dict, PyObject *module, const Word key, PyObject *value)
{
  if (value == nullptr) {
    return false;
  }
  const int status = PyDict_SetItem(dict, get(module, key), value);
  Py_DECREF(value);
  return status == 0;
}

}  // namespace blender::angelo_read_words
