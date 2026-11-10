/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * One transfer per paste (#GHOST_ClipboardTransfer): Wayland's size probe and read ask for the
 * same offer's bytes, and the sending application (GTK, Firefox) encodes a PNG afresh for every
 * request, on Blender's main thread's time. The fetch below stands in for that transfer.
 */

#include "testing/testing.h"

#include <cstdlib>
#include <cstring>
#include <memory>
#include <string>

#include "GHOST_ClipboardImage.hh"

namespace ghost_clipboard_image::tests {

/** A transfer that counts itself and hands over \a text as `malloc` bytes. */
struct CountedFetch {
  int count = 0;
  const char *text = "picture";
  char *operator()(size_t *r_size)
  {
    count++;
    if (text == nullptr) {
      return nullptr;
    }
    *r_size = strlen(text);
    char *data = static_cast<char *>(malloc(*r_size));
    memcpy(data, text, *r_size);
    return data;
  }
};

TEST(GHOST_ClipboardImage, OnePasteTransfersOnce)
{
  GHOST_ClipboardTransfer transfer;
  CountedFetch fetch;
  auto ask = [&](const uint64_t selection, const char *type) {
    size_t size = 0;
    const char *data = transfer.get(selection, type, &size, [&](size_t *r) { return fetch(r); });
    return data ? std::string(data, size) : std::string();
  };

  /* The size probe, then the read: one transfer. */
  EXPECT_EQ(ask(1, "image/png"), "picture");
  EXPECT_EQ(ask(1, "image/png"), "picture");
  EXPECT_EQ(fetch.count, 1);

  /* The read frees the bytes: the next paste transfers again, once. */
  transfer.clear();
  ask(1, "image/png");
  ask(1, "image/png");
  EXPECT_EQ(fetch.count, 2);

  /* A new selection or another type is another transfer. */
  ask(2, "image/png");
  EXPECT_EQ(fetch.count, 3);
  ask(2, "text/uri-list");
  EXPECT_EQ(fetch.count, 4);

  /* A failed transfer is not kept: the next ask tries again. */
  fetch.text = nullptr;
  EXPECT_EQ(ask(3, "image/png"), "");
  EXPECT_EQ(ask(3, "image/png"), "");
  EXPECT_EQ(fetch.count, 6);
}

TEST(GHOST_ClipboardImage, AbandonedPasteReleasedAfterSelectionChanges)
{
  GHOST_ClipboardTransfer transfer;
  CountedFetch fetch;
  size_t size = 0;
  auto ask = [&]() {
    return transfer.get(1, "image/png", &size, [&](size_t *r) { return fetch(r); });
  };

  /* A refused size probe has no read to clear its transfer. A main-thread event pump for
   * the same selection must preserve the bytes for a later read, without another transfer. */
  ASSERT_NE(ask(), nullptr);
  transfer.clear_if_selection_changed(1);
  ASSERT_NE(ask(), nullptr);
  EXPECT_EQ(fetch.count, 1);

  /* Replacing the selection (including clearing it or copying only text) discards the old
   * transfer without fetching a new one. Asking for the old key again must now fetch anew. */
  transfer.clear_if_selection_changed(2);
  EXPECT_EQ(fetch.count, 1);
  ASSERT_NE(ask(), nullptr);
  EXPECT_EQ(fetch.count, 2);

  /* Empty and already-cleared caches can be invalidated repeatedly. */
  transfer.clear();
  transfer.clear_if_selection_changed(3);
  transfer.clear_if_selection_changed(4);
  EXPECT_EQ(fetch.count, 2);
}

TEST(GHOST_ClipboardImage, LocalOwnerKeepsTransferAliveAcrossFetch)
{
  auto owner = std::make_shared<GHOST_ClipboardTransfer>();
  const std::weak_ptr<GHOST_ClipboardTransfer> lifetime = owner;
  CountedFetch fetch;
  size_t size = 0;
  {
    /* The operation holds its own owner before a callback can detach the original owner. */
    const auto retained = owner;
    const char *data = retained->get(1, "image/png", &size, [&](size_t *r_size) {
      owner.reset();
      EXPECT_FALSE(lifetime.expired());
      return fetch(r_size);
    });
    ASSERT_NE(data, nullptr);
    EXPECT_EQ(std::string(data, size), "picture");
    EXPECT_FALSE(lifetime.expired());
  }
  EXPECT_TRUE(lifetime.expired());
}

TEST(GHOST_ClipboardImage, SelectionChangeDuringFetchDiscardsResult)
{
  GHOST_ClipboardTransfer transfer;
  CountedFetch fetch;
  std::atomic<uint64_t> serial = 1;
  size_t size = 99;
  const char *data = transfer.get_current(serial, "image/png", &size, [&](size_t *r_size) {
    serial++;
    return fetch(r_size);
  });
  EXPECT_EQ(data, nullptr);
  EXPECT_EQ(size, 0);
  EXPECT_EQ(fetch.count, 1);

  /* The discarded result is not cached under either the old or the new serial. */
  ASSERT_NE(transfer.get_current(
                serial, "image/png", &size, [&](size_t *r_size) { return fetch(r_size); }),
            nullptr);
  EXPECT_EQ(fetch.count, 2);
  ASSERT_NE(transfer.get_current(
                serial, "image/png", &size, [&](size_t *r_size) { return fetch(r_size); }),
            nullptr);
  EXPECT_EQ(fetch.count, 2);
}

}  // namespace ghost_clipboard_image::tests
