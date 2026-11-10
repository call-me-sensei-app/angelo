/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include <array>
#include <thread>

#include "MEM_lifetime.hh"
#include "guardedalloc_test_base.h"

namespace {

void check_wrapped_allocation()
{
  using namespace mem_lifetime;
  constexpr size_t size = 3 * 4096;
  char *block = MEM_new_array_uninitialized<char>(size, __func__);
  Watch first{}, last{}, alias{}, outside{};
  set(first, block);
  set(last, block + size - 1);
  set(alias, block + size - 1);
  set(outside, block + size);
  std::thread worker([&] { MEM_delete(block); });
  worker.join();
  EXPECT_TRUE(expired(first));
  EXPECT_TRUE(expired(last));
  EXPECT_TRUE(expired(alias));
  EXPECT_FALSE(expired(outside));
  clear(first);
  clear(last);
  clear(alias);
  clear(outside);
}

TEST_F(LockFreeAllocatorTest, WorkerFreeInvalidatesInteriorAliases)
{
  check_wrapped_allocation();
}

TEST_F(GuardedAllocatorTest, WorkerFreeInvalidatesInteriorAliases)
{
  check_wrapped_allocation();
}

TEST_F(LockFreeAllocatorTest, SharedStorageDetachOnlyExpiresItsOwner)
{
  using namespace mem_lifetime;
  int a = 0, b = 0;
  int shared[2] = {};
  Watch first{}, second{};
  set(first, &shared[1], &a);
  set(second, &shared[1], &b);
  invalidate(shared, sizeof(shared), &a);
  EXPECT_TRUE(expired(first));
  EXPECT_FALSE(expired(second));
  clear(first);
  clear(second);
}

TEST_F(LockFreeAllocatorTest, LargeRangeVisitsEveryOccupiedBucket)
{
  using namespace mem_lifetime;
  constexpr size_t size = 9 * 1024 * 1024;
  char *block = MEM_new_array_uninitialized<char>(size, __func__);
  std::array<Watch, 97> watches{};
  for (size_t i = 0; i < watches.size(); i++) {
    set(watches[i], block + i * (size - 1) / (watches.size() - 1));
  }
  invalidate(block + 1, size - 2);
  EXPECT_FALSE(expired(watches.front()));
  EXPECT_FALSE(expired(watches.back()));
  for (size_t i = 1; i + 1 < watches.size(); i++) {
    EXPECT_TRUE(expired(watches[i]));
    clear(watches[i]);
  }
  /* Clearing other nodes must preserve each remaining occupied bit. */
  std::thread worker([&] { MEM_delete(block); });
  worker.join();
  EXPECT_TRUE(expired(watches.front()));
  EXPECT_TRUE(expired(watches.back()));
  clear(watches.front());
  clear(watches.back());
}

TEST_F(LockFreeAllocatorTest, CursorRearmReplacesAddressAndScope)
{
  using namespace mem_lifetime;
  char *block = MEM_new_array_uninitialized<char>(3 * 4096, __func__);
  char *page_start = reinterpret_cast<char *>((uintptr_t(block) + 4095) & ~uintptr_t(4095));
  int a = 0, b = 0;
  Watch cursor{};
  const size_t before = watch_count[0].load();
  set(cursor, page_start + 1, &a);
  set(cursor, page_start + 2, &b);
  EXPECT_EQ(watch_count[0].load(), before + 1);
  invalidate(page_start + 1, 1);
  EXPECT_FALSE(expired(cursor));
  invalidate(page_start, 4096, &a);
  EXPECT_FALSE(expired(cursor));
  invalidate(page_start, 4096, &b);
  EXPECT_TRUE(expired(cursor));
  set(cursor, page_start + 4096, &a);
  EXPECT_FALSE(expired(cursor));
  EXPECT_EQ(watch_count[0].load(), before + 1);
  invalidate(page_start, 4096);
  EXPECT_FALSE(expired(cursor));
  clear(cursor);
  EXPECT_EQ(watch_count[0].load(), before);
  MEM_delete(block);
}

TEST_F(LockFreeAllocatorTest, ExplicitDescriptorsIgnoreAllocatorNotifications)
{
  using namespace mem_lifetime;
  char *block = MEM_new_array_uninitialized<char>(32, __func__);
  Watch allocation{}, descriptor{};
  set(allocation, block);
  set(descriptor, block, nullptr, Source::Explicit);
  MEM_delete(block);
  EXPECT_TRUE(expired(allocation));
  EXPECT_FALSE(expired(descriptor));
  invalidate(block, 32);
  EXPECT_TRUE(expired(descriptor));
  clear(allocation);
  clear(descriptor);
}

TEST_F(LockFreeAllocatorTest, RearmCanChangeRetirementSource)
{
  using namespace mem_lifetime;
  int value = 0;
  Watch watch{};
  const size_t allocations_before = watch_count[0].load();
  const size_t explicit_before = watch_count[1].load();
  set(watch, &value, nullptr, Source::Explicit);
  EXPECT_EQ(watch_count[1].load(), explicit_before + 1);
  set(watch, &value);
  EXPECT_EQ(watch_count[1].load(), explicit_before);
  EXPECT_EQ(watch_count[0].load(), allocations_before + 1);
  invalidate_allocation(&value, sizeof(value));
  EXPECT_TRUE(expired(watch));
  clear(watch);
  EXPECT_EQ(watch_count[0].load(), allocations_before);
}

TEST_F(LockFreeAllocatorTest, ScopePresenceSurvivesAliasesAndRearming)
{
  using namespace mem_lifetime;
  char scopes[128] = {};
  int value = 0;
  const void *first_scope = &scopes[0];
  const void *second_scope = &scopes[64];
  Watch first{}, second{};
  EXPECT_FALSE(has_scope(first_scope));
  set(first, &value, first_scope);
  set(second, &value, first_scope, Source::Explicit);
  EXPECT_TRUE(has_scope(first_scope));
  clear(first);
  EXPECT_TRUE(has_scope(first_scope));
  set(second, &value, second_scope, Source::Explicit);
  EXPECT_FALSE(has_scope(first_scope));
  EXPECT_TRUE(has_scope(second_scope));
  invalidate(&value, sizeof(value));
  EXPECT_TRUE(expired(second));
  EXPECT_TRUE(has_scope(second_scope));
  clear(second);
  EXPECT_FALSE(has_scope(second_scope));
}

}  // namespace
