/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "MEM_lifetime.hh"

#include <algorithm>
#include <bit>

namespace mem_lifetime {

std::atomic<size_t> watch_count[2];
/* Hash pages, not individual pointers: frees also contain interior pointers.
 * A bitmap skips empty buckets, including for frees spanning the whole table. */
static Watch *buckets[2][bucket_count];
static std::atomic_flag bucket_locks[bucket_count];
std::atomic<uint64_t> occupied[2][bucket_count / word_bits];
std::atomic<size_t> scoped_watch_count[bucket_count];

class BucketLock {
  std::atomic_flag &flag_;

 public:
  explicit BucketLock(const size_t bucket) : flag_(bucket_locks[bucket])
  {
    while (flag_.test_and_set(std::memory_order_acquire)) {
      while (flag_.test(std::memory_order_relaxed)) {
        /* Registry operations neither allocate nor wait for Python. */
      }
    }
  }
  ~BucketLock()
  {
    flag_.clear(std::memory_order_release);
  }
};

static uintptr_t page(uintptr_t address)
{
  return address >> 12;
}

static size_t bucket_for(uintptr_t address)
{
  return page(address) % bucket_count;
}

void clear_address(Watch &watch)
{
  /* Only the registering thread unlinks. A free just marks. */
  const size_t bucket = bucket_for(watch.address);
  const size_t source = size_t(watch.source);
  const BucketLock lock(bucket);
  *watch.prev = watch.next;
  if (watch.next) {
    watch.next->prev = watch.prev;
  }
  watch.prev = nullptr;
  watch.next = nullptr;
  if (!buckets[source][bucket]) {
    occupied[source][bucket / word_bits].fetch_and(~(uint64_t(1) << (bucket % word_bits)),
                                                   std::memory_order_relaxed);
  }
  watch_count[source].fetch_sub(1, std::memory_order_relaxed);
  if (watch.scope) {
    scoped_watch_count[scope_bucket(watch.scope)].fetch_sub(1, std::memory_order_relaxed);
  }
  watch.address = 0;
  std::atomic_ref(watch.expired).store(false, std::memory_order_relaxed);
}

void set_address(Watch &watch, const void *data, const void *scope, const Source source)
{
  const uintptr_t address = uintptr_t(data);
  const size_t bucket = bucket_for(address);
  if (watch.address && bucket_for(watch.address) == bucket && watch.source == source) {
    /* A cursor often advances within one bucket. Keep its list position and
     * count instead of unregistering and registering it at every element. */
    const BucketLock lock(bucket);
    watch.address = address;
    if (watch.scope != scope) {
      if (watch.scope) {
        scoped_watch_count[scope_bucket(watch.scope)].fetch_sub(1, std::memory_order_relaxed);
      }
      if (scope) {
        scoped_watch_count[scope_bucket(scope)].fetch_add(1, std::memory_order_relaxed);
      }
    }
    watch.scope = scope;
    std::atomic_ref(watch.expired).store(false, std::memory_order_relaxed);
    return;
  }
  clear(watch);
  const BucketLock lock(bucket);
  watch.address = address;
  watch.scope = scope;
  watch.source = source;
  const size_t source_index = size_t(source);
  Watch **head = &buckets[source_index][bucket];
  const bool was_empty = *head == nullptr;
  watch.prev = head;
  watch.next = *head;
  if (*head) {
    (*head)->prev = &watch.next;
  }
  *head = &watch;
  if (was_empty) {
    occupied[source_index][bucket / word_bits].fetch_or(uint64_t(1) << (bucket % word_bits),
                                                        std::memory_order_relaxed);
  }
  watch_count[source_index].fetch_add(1, std::memory_order_relaxed);
  if (scope) {
    scoped_watch_count[scope_bucket(scope)].fetch_add(1, std::memory_order_relaxed);
  }
}

static void invalidate_buckets(const size_t first,
                               const size_t end,
                               const uintptr_t start,
                               const size_t size,
                               const void *scope,
                               const size_t source)
{
  for (size_t word = first / word_bits; word <= (end - 1) / word_bits; word++) {
    const size_t low = std::max(first, word * word_bits) - word * word_bits;
    const size_t high = std::min(end, (word + 1) * word_bits) - word * word_bits;
    const uint64_t mask = (UINT64_MAX << low) & (UINT64_MAX >> (word_bits - high));
    uint64_t bits = occupied[source][word].load(std::memory_order_relaxed) & mask;
    while (bits) {
      const size_t bucket = word * word_bits + std::countr_zero(bits);
      bits &= bits - 1;
      const BucketLock lock(bucket);
      for (Watch *watch = buckets[source][bucket]; watch; watch = watch->next) {
        if (watch->address - start < size && (!scope || watch->scope == scope)) {
          std::atomic_ref(watch->expired).store(true, std::memory_order_relaxed);
        }
      }
    }
  }
}

void invalidate_range(const void *data, size_t size, const void *scope, const bool allocation_only)
{
  if (!size) {
    return;
  }
  const uintptr_t start = uintptr_t(data);
  const uintptr_t first_page = page(start);
  const size_t count = std::min<size_t>(page(start + (size - 1)) - first_page + 1, bucket_count);
  const size_t first = first_page % bucket_count;
  const size_t end = std::min(first + count, bucket_count);
  for (size_t source = 0; source < (allocation_only ? 1 : 2); source++) {
    if (!watch_count[source].load(std::memory_order_relaxed)) {
      continue;
    }
    invalidate_buckets(first, end, start, size, scope, source);
    if (first + count > bucket_count) {
      invalidate_buckets(0, first + count - bucket_count, start, size, scope, source);
    }
  }
}

}  // namespace mem_lifetime
