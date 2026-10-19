/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#pragma once

#include <Python.h>
#include <cstdint>

namespace blender {

[[nodiscard]] PyObject *BPY_app_handlers_struct();
/** Under the GIL, observe handler failures even though ordinary handlers print/clear errors.
 * This grants no callback authority and changes no normal handler behavior. */
uint64_t BPY_app_handlers_error_count();

}  // namespace blender
