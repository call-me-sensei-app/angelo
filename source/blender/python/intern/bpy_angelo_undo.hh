/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include "BLI_span.hh"

struct _object;

namespace blender {
struct Main;
struct AngeloCommitIDIdentity;
_object *BPY_angelo_undo_module();
/** Native stage/loader provenance only. The operation guard precedes Python conversion/callbacks. */
bool BPY_angelo_undo_operation_begin(_object *capsule);
void BPY_angelo_undo_operation_end(_object *capsule);
bool BPY_angelo_undo_register_stage_ids(_object *capsule, Main *bmain, Span<AngeloCommitIDIdentity> ids,
                                         const char *provenance_error = nullptr);
}
