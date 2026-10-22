/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Platforms without a native notification implementation (see `bpy_angelo_desktop.hh`).
 */

#include "bpy_angelo_desktop.hh"

#ifndef __APPLE__

namespace blender {

const char *angelo_desktop_permission()
{
  return "unsupported";
}

void angelo_desktop_request_permission() {}

const char *angelo_desktop_notify(const char * /*identifier*/,
                                  const char * /*title*/,
                                  const char * /*body*/)
{
  return "unsupported";
}

const char *angelo_desktop_attention()
{
  return "unsupported";
}

}  // namespace blender

#endif
