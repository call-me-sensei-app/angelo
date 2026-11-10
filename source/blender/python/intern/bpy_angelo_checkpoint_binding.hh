/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

/** \file
 * \ingroup pythonintern
 *
 * The checkpoint capture's content hasher: a native object Python feeds the bytes it reads from a
 * published component, compared inside native with the binding the capture's writer recorded
 * (#WMCheckpointContentBinding). It holds no digest Python can read, and the binding never leaves
 * native code: Python learns only whether the two are equal.
 */

#include <Python.h>

#include <atomic>

#include "../../windowmanager/intern/wm_checkpoint.hh"

namespace blender {

/** One feeder at a time for a content hasher. ``update`` holds it while it hashes, from any thread
 * and with the GIL released (a Restore's safety capture reads its outputs back on a worker), and
 * ``capture_bind`` refuses a hasher while it is held: nothing compares a hash still being fed. */
class CheckpointHasherUse {
  std::atomic<bool> held_{false};

 public:
  /** False when another feeder holds it. */
  bool try_acquire()
  {
    bool expected = false;
    return held_.compare_exchange_strong(expected, true, std::memory_order_acq_rel);
  }
  void release()
  {
    held_.store(false, std::memory_order_release);
  }
  bool held() const
  {
    return held_.load(std::memory_order_acquire);
  }
};

/** A new hasher fixed to ``capsule`` (a strong reference) and ``role``. Not constructible from
 * Python; its one method is ``update(buffer)``, which one thread at a time may call (any thread:
 * #CheckpointHasherUse). */
PyObject *BPY_checkpoint_hasher_new(PyObject *capsule, WMCheckpointRole role);

/** Spend ``hasher``: check it belongs to ``capsule`` and ``role`` and consumed exactly
 * ``binding.bytes``, finalise it once and compare with ``binding`` (memcmp). Returns nullptr when
 * they are equal, otherwise why not (no Python error is set). Refused while ``update`` feeds it.
 * Any call after the first for the same hasher refuses. Main thread, with the GIL. */
const char *BPY_checkpoint_hasher_bind(PyObject *hasher,
                                       PyObject *capsule,
                                       WMCheckpointRole role,
                                       const WMCheckpointContentBinding &binding);

}  // namespace blender
