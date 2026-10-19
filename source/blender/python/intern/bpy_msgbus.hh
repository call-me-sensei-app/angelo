/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 */

#pragma once

#include <Python.h>

namespace blender {

struct wmMsgSubscribeValue;

[[nodiscard]] PyObject *BPY_msgbus_module();

/**
 * The function a `bpy.msgbus.subscribe_rna` subscriber calls (a borrowed reference), or null for
 * a subscriber made by C++ (Blender's own editors).
 */
[[nodiscard]] PyObject *BPY_msgbus_subscriber_function(const wmMsgSubscribeValue *subscriber);

}  // namespace blender
