/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

namespace mem_lifetime {

/* C++ descriptors have explicit retirement notifications and are not guarded
 * allocations. Keep them out of allocator-free scans in the same registry. */
enum class Source : uint8_t { Allocation = 0, Explicit = 1 };

/* Intrusive: registration and invalidation cannot allocate (including on worker threads).
 * Only the registering thread sets/clears this watch. Clear before destroying its storage.
 * The atomic flag is accessed via atomic_ref so a zero-initialized C/Python object can
 * embed a watch without calling a C++ constructor. Never move or copy a registered watch. */
struct Watch {
  Watch **prev;
  Watch *next;
  uintptr_t address;
  const void *scope;
  bool expired;
  Source source;
};
static_assert(sizeof(Watch) == 5 * sizeof(void *));

void set_address(Watch &watch, const void *address, const void *scope, Source source);
void clear_address(Watch &watch);
inline void clear(Watch &watch)
{
  if (watch.address) {
    clear_address(watch);
  }
}
inline void set(Watch &watch,
                const void *address,
                const void *scope = nullptr,
                Source source = Source::Allocation)
{
  if (address) {
    set_address(watch, address, scope, source);
  }
  else {
    clear(watch);
  }
}
inline bool expired(const Watch &watch)
{
  return std::atomic_ref(const_cast<bool &>(watch.expired)).load(std::memory_order_relaxed);
}

/* Destruction or replacement of [data, data + size), prior to address reuse. */
extern std::atomic<size_t> watch_count[2];
inline constexpr size_t bucket_count = 1024;
inline constexpr size_t word_bits = 64;
extern std::atomic<uint64_t> occupied[2][bucket_count / word_bits];
/* Hash collisions conservatively request a scan; they can never suppress one. */
extern std::atomic<size_t> scoped_watch_count[bucket_count];
inline size_t scope_bucket(const void *scope)
{
  return (uintptr_t(scope) >> 4) % bucket_count;
}
inline bool has_scope(const void *scope)
{
  return scope && scoped_watch_count[scope_bucket(scope)].load(std::memory_order_relaxed) != 0;
}
void invalidate_range(const void *data, size_t size, const void *scope, bool allocation_only);
inline void invalidate_allocation(const void *data, size_t size)
{
  if (!size) {
    return;
  }
  const uintptr_t address = uintptr_t(data);
  if ((address >> 12) == ((address + size - 1) >> 12)) {
    /* Most frees fit one page. An empty bucket needs no out-of-line call,
     * range-mask construction, or global watch-count access. */
    const size_t bucket = (address >> 12) % bucket_count;
    if (!(occupied[0][bucket / word_bits].load(std::memory_order_relaxed) &
          (uint64_t(1) << (bucket % word_bits))))
    {
      return;
    }
  }
  else if (!watch_count[0].load(std::memory_order_relaxed)) {
    return;
  }
  invalidate_range(data, size, nullptr, true);
}
inline void invalidate(const void *data, size_t size, const void *scope = nullptr)
{
  if (!size) {
    return;
  }
  const uintptr_t address = uintptr_t(data);
  if ((address >> 12) == ((address + size - 1) >> 12)) {
    const size_t bucket = (address >> 12) % bucket_count;
    const uint64_t mask = uint64_t(1) << (bucket % word_bits);
    if (!((occupied[0][bucket / word_bits].load(std::memory_order_relaxed) |
           occupied[1][bucket / word_bits].load(std::memory_order_relaxed)) &
          mask))
    {
      return;
    }
  }
  else if (!watch_count[0].load(std::memory_order_relaxed) &&
           !watch_count[1].load(std::memory_order_relaxed))
  {
    return;
  }
  invalidate_range(data, size, scope, false);
}

}  // namespace mem_lifetime
