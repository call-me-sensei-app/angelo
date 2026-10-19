# SPDX-FileCopyrightText: 2023 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

set(INC
  ../include
  ../../makesrna
  # RNA_prototypes.hh
  ${CMAKE_BINARY_DIR}/source/blender/makesrna
)

set(INC_SYS
)

set(SRC
  angelo_commit_rights.cc
  angelo_undo.cc
  ed_undo.cc
  memfile_undo.cc
  undo_system_types.cc

  angelo_commit_rights.hh
  undo_intern.hh
)

set(LIB
  PRIVATE bf::asset_system
  PRIVATE bf::blenkernel
  PRIVATE bf::blenlib
  PRIVATE bf::blenloader
  PRIVATE bf::blentranslation
  PRIVATE bf::bmesh
  PRIVATE bf::dna
  PRIVATE bf::imbuf
  PRIVATE bf::intern::clog
  PRIVATE bf::intern::guardedalloc
  PRIVATE bf::windowmanager
)

if(WITH_GTESTS)
  set_source_files_properties(angelo_undo.cc memfile_undo.cc PROPERTIES COMPILE_DEFINITIONS WITH_GTESTS)
endif()

blender_add_lib(bf_editor_undo "${SRC}" "${INC}" "${INC_SYS}" "${LIB}")

# RNA_prototypes.hh
add_dependencies(bf_editor_undo bf_rna)

if(WITH_GTESTS)
  set(TEST_SRC
    angelo_checkpoint_undo_test.cc
  )
  set_source_files_properties(angelo_checkpoint_undo_test.cc PROPERTIES COMPILE_DEFINITIONS WITH_GTESTS)
  set(TEST_LIB
    ${LIB}
    PRIVATE bf::depsgraph
    bf_rna  # RNA_prototypes.hh
  )
  blender_add_test_suite_lib(editor_undo "${TEST_SRC}" "${INC}" "${INC_SYS}" "${TEST_LIB}")
endif()
