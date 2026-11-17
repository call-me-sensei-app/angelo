/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The guard's SHA-256 against the standard's test vectors (FIPS 180-4 examples, and lengths around the padding
 * boundary against a long known message).
 */

#include "testing/testing.h"

#include <string>

#include "bpy_angelo_guard_sha256.hh"

namespace blender::angelo_guard::tests {

static std::string hex(const std::string &text)
{
  uint8_t digest[32];
  sha256(text.data(), text.size(), digest);
  static const char *digits = "0123456789abcdef";
  std::string result;
  for (const uint8_t byte : digest) {
    result += digits[byte >> 4];
    result += digits[byte & 15];
  }
  return result;
}

TEST(angelo_guard_sha256, vectors)
{
  EXPECT_EQ(hex(""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
  EXPECT_EQ(hex("abc"), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  EXPECT_EQ(hex("abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");
  EXPECT_EQ(hex(std::string(1000000, 'a')),
            "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");
  /* 55, 56 and 64 bytes: the padding's one-block and two-block cases. */
  EXPECT_EQ(hex(std::string(55, 'a')), "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318");
  EXPECT_EQ(hex(std::string(56, 'a')), "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a");
  EXPECT_EQ(hex(std::string(64, 'a')), "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb");
}

}  // namespace blender::angelo_guard::tests
