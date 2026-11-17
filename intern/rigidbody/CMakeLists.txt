# SPDX-FileCopyrightText: 2006 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

add_definitions(-DBT_USE_DOUBLE_PRECISION)

set(INC
  .
)

set(INC_SYS
  ${BULLET_INCLUDE_DIRS}
)

set(SRC
  rb_bullet_api.cpp
  rb_convex_hull_api.cpp

  RBI_hull_api.h
  RBI_api.h
)

set(LIB
  ${BULLET_LIBRARIES}
)

blender_add_lib(bf_intern_rigidbody "${SRC}" "${INC}" "${INC_SYS}" "${LIB}")

if(WITH_GTESTS)
  set(TEST_SRC
    tests/rb_teardown_test.cc
  )
  set(TEST_INC
  )
  set(TEST_LIB
    PRIVATE bf_intern_rigidbody
  )
  blender_add_test_executable(rigidbody "${TEST_SRC}" "${INC};${TEST_INC}" "${INC_SYS}" "${LIB};${TEST_LIB}")
endif()
