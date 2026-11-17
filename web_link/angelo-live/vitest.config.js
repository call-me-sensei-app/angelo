// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT

export default {
  test: {
    include: ["test/**/*.test.js"],
    testTimeout: 30000,
    hookTimeout: 30000,
    fileParallelism: false, // The contract and HTTP tests start servers; one file at a time keeps them apart.
  },
};
