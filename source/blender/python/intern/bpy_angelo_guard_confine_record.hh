/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * A set of addresses for the confinement's provenance (bpy_angelo_guard_confine_record.cc): no Python, and no
 * allocation while it is consulted, so the object allocator's own hook can use it.
 */

#include <algorithm>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>

namespace blender::angelo_guard {

/** An open-addressing set of allocation addresses (no allocation of its own while it is consulted on free). */
class PointerSet {
 public:
  bool empty() const
  {
    return count_ == 0;
  }
  bool contains(const void *pointer) const
  {
    if (count_ == 0) {
      return false;
    }
    for (size_t i = slot(pointer);; i = (i + 1) & (slots_.size() - 1)) {
      const uintptr_t value = slots_[i];
      if (value == EMPTY) {
        return false;
      }
      if (value == uintptr_t(pointer)) {
        return true;
      }
    }
  }
  void insert(const void *pointer)
  {
    if ((count_ + tombstones_ + 1) * 2 > slots_.size()) {
      rehash(slots_.empty() ? 1024 : (count_ * 4 > slots_.size() ? slots_.size() * 2 : slots_.size()));
    }
    size_t target = SIZE_MAX;
    for (size_t i = slot(pointer);; i = (i + 1) & (slots_.size() - 1)) {
      const uintptr_t value = slots_[i];
      if (value == uintptr_t(pointer)) {
        return;
      }
      if (value == TOMBSTONE && target == SIZE_MAX) {
        target = i;
      }
      if (value == EMPTY) {
        if (target == SIZE_MAX) {
          target = i;
        }
        else {
          tombstones_--;
        }
        break;
      }
    }
    slots_[target] = uintptr_t(pointer);
    count_++;
  }
  bool erase(const void *pointer)
  {
    if (count_ == 0) {
      return false;
    }
    for (size_t i = slot(pointer);; i = (i + 1) & (slots_.size() - 1)) {
      const uintptr_t value = slots_[i];
      if (value == EMPTY) {
        return false;
      }
      if (value == uintptr_t(pointer)) {
        slots_[i] = TOMBSTONE;
        count_--;
        tombstones_++;
        return true;
      }
    }
  }
  void clear()
  {
    std::fill(slots_.begin(), slots_.end(), EMPTY);
    count_ = tombstones_ = 0;
  }

 private:
  static constexpr uintptr_t EMPTY = 0, TOMBSTONE = 1;
  size_t slot(const void *pointer) const
  {
    uintptr_t x = uintptr_t(pointer) >> 4;
    x ^= x >> 17;
    x *= 0x9E3779B97F4A7C15ull;
    return size_t(x >> 7) & (slots_.size() - 1);
  }
  void rehash(size_t size)
  {
    std::vector<uintptr_t> old;
    old.swap(slots_);
    slots_.assign(size, EMPTY);
    count_ = tombstones_ = 0;
    for (const uintptr_t value : old) {
      if (value != EMPTY && value != TOMBSTONE) {
        insert((const void *)value);
      }
    }
  }
  std::vector<uintptr_t> slots_;
  size_t count_ = 0, tombstones_ = 0;
};

/** The confined regions running now. */
extern std::atomic<int> active_regions; /* Read without the GIL (a USD resolver thread). */

}  // namespace blender::angelo_guard
