#!/bin/sh
# SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Builds and runs the grant authority core's GTests on their own, with no Blender build: the core
# (source/blender/python/intern/bpy_angelo_guard_authority_core.*) uses the standard library only.
# The same tests also run in Blender's python_intern GTest suite. Usage: build_core_tests.sh [OUT]
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${1:-$(mktemp -d)}
mkdir -p "$OUT/shim/testing"
printf '#pragma once\n#include "gtest/gtest.h"\n' > "$OUT/shim/testing/testing.h"
G=$ROOT/extern/gtest
INTERN=$ROOT/source/blender/python/intern
${CXX:-c++} -std=c++20 -O1 -pthread -Wall -Wextra -I"$G/include" -I"$G" -I"$OUT/shim" -I"$INTERN" \
  "$G/src/gtest-all.cc" "$G/src/gtest_main.cc" \
  "$INTERN/bpy_angelo_guard_authority_core.cc" "$INTERN/bpy_angelo_guard_authority_core_test.cc" \
  -o "$OUT/core_tests"
"$OUT/core_tests"
