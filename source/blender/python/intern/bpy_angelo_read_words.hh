/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * The keys of `_bpy._angelo_read.image_metadata`'s record, interned once per module object and
 * kept in its module state.
 *
 * Why: the helper runs thousands of times per check. Building its record with
 * `Py_BuildValue("{s:...}")` allocated and hashed every key again on each call, which cost more
 * than the read itself; interned keys also let Python's own `record["size"]` lookups match by
 * identity. The record's content and shape are unchanged.
 */

#pragma once

#include <Python.h>

namespace blender::angelo_read_words {

enum Word : int {
  SIZE,
  CHANNELS,
  DEPTH,
  IS_FLOAT,
  RESOLUTION,
  FILE_FORMAT,
  FRAME_DURATION,
  WORDS_NUM,
};

/** The module state size and lifecycle callbacks of a module definition that uses the words. */
constexpr Py_ssize_t state_size()
{
  return Py_ssize_t(sizeof(PyObject *) * WORDS_NUM);
}
int traverse_state(PyObject *module, visitproc visit, void *arg);
int clear_state(PyObject *module);
void free_state(void *module);

/** Create the words in a newly created module's state; false with a Python error on failure. */
bool init(PyObject *module);

/** A borrowed word of \a module, the `self` of a module-level function. */
PyObject *get(PyObject *module, Word word);

/**
 * Store \a value (a new reference, stolen even on failure) under the interned \a key.
 * False with a Python error on failure.
 */
bool set_item(PyObject *dict, PyObject *module, Word key, PyObject *value);

}  // namespace blender::angelo_read_words
