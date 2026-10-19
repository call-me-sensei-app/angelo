# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ed25519 (ed25519.py) against RFC 8032's published vectors and malformed input.

The module verifies the signed update feed, so the checks are about what must
NOT verify as much as what does: flipped message/signature/key bits, a
non-canonical scalar, a small-order key and wrong lengths.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts/addons_core"))
from angelo_agent import ed25519  # noqa: E402

# RFC 8032 section 7.1, TEST 1 and TEST 2.
VECTORS = (
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
)


class Ed25519Tests(unittest.TestCase):
    def test_the_rfc_vectors_sign_and_verify(self):
        for seed, public, message, signature in VECTORS:
            with self.subTest(message=message):
                seed, public, message, signature = (bytes.fromhex(x) for x in (seed, public, message, signature))
                self.assertEqual(ed25519.public_key(seed), public)
                self.assertEqual(ed25519.sign(seed, message), signature)
                self.assertTrue(ed25519.verify(public, message, signature))

    def test_a_changed_message_signature_or_key_never_verifies(self):
        seed = bytes(range(32))
        public, message = ed25519.public_key(seed), b"signed feed bytes"
        signature = ed25519.sign(seed, message)
        self.assertTrue(ed25519.verify(public, message, signature))
        self.assertFalse(ed25519.verify(public, message + b".", signature))
        for index in (0, 31, 32, 63):
            flipped = bytearray(signature)
            flipped[index] ^= 1
            self.assertFalse(ed25519.verify(public, message, bytes(flipped)), index)
        other = ed25519.public_key(bytes(reversed(range(32))))
        self.assertFalse(ed25519.verify(other, message, signature))

    def test_random_keys_round_trip(self):
        for _ in range(3):
            seed, message = os.urandom(32), os.urandom(200)
            signature = ed25519.sign(seed, message)
            self.assertTrue(ed25519.verify(ed25519.public_key(seed), message, signature))

    def test_malformed_input_is_refused_not_raised(self):
        seed = bytes(32)
        public, signature = ed25519.public_key(seed), ed25519.sign(seed, b"m")
        for bad_public in (b"", public[:-1], public + b"\0", None, "text"):
            self.assertFalse(ed25519.verify(bad_public, b"m", signature))
        for bad_signature in (b"", signature[:-1], signature + b"\0", None, "text"):
            self.assertFalse(ed25519.verify(public, b"m", bad_signature))
        self.assertFalse(ed25519.verify(public, "m", signature))
        with self.assertRaises(ValueError):
            ed25519.public_key(b"short")

    def test_a_non_canonical_scalar_is_refused(self):
        seed = bytes(range(32))
        public, signature = ed25519.public_key(seed), ed25519.sign(seed, b"m")
        s = int.from_bytes(signature[32:], "little")
        malleable = signature[:32] + (s + ed25519._Q).to_bytes(32, "little")
        self.assertFalse(ed25519.verify(public, b"m", malleable))

    def test_the_identity_key_of_small_order_is_refused(self):
        identity = (1).to_bytes(32, "little")  # y = 1, x = 0: the neutral point.
        signature = identity + bytes(32)
        self.assertFalse(ed25519.verify(identity, b"anything", signature))


if __name__ == "__main__":
    unittest.main()
