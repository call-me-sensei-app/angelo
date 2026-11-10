/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pygen
 */

#pragma once

#include <Python.h>

#include "py_capi_dependent.hh"

namespace blender {

struct IDProperty;

extern PyTypeObject BPy_IDPropertyUIManager_Type;

struct BPy_IDPropertyUIManager {
  PyObject_HEAD
  IDProperty *property;
  /** Angelo: invalidated (`property` cleared) when the owner ID is freed. */
  BPyRNA_DependentLink dependent;
};

void IDPropertyUIData_Init_Types();

}  // namespace blender
