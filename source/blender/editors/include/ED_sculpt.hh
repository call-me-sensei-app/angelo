/* SPDX-FileCopyrightText: 2008 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup editors
 */

#pragma once

#include <cstddef>
#include <memory>

namespace blender {

struct Depsgraph;
struct Main;
struct Mesh;
struct Object;
struct RegionView3D;
struct ReportList;
struct Scene;
struct UndoType;
struct UndoStep;
struct UndoStack;
struct bContext;
struct wmKeyConfig;
struct wmOperator;

namespace ed::sculpt_paint {

void object_sculpt_mode_enter(Main &bmain,
                              Depsgraph &depsgraph,
                              Scene &scene,
                              Object &ob,
                              bool force_dyntopo,
                              ReportList *reports);
void object_sculpt_mode_enter(bContext *C, Depsgraph &depsgraph, ReportList *reports);
void object_sculpt_mode_exit(Main &bmain, Depsgraph &depsgraph, Scene &scene, Object &ob);
void object_sculpt_mode_exit(bContext *C, Depsgraph &depsgraph);

/* `sculpt.cc` */

/**
 * Checks if the currently active shape key is able to be sculpted on.
 *
 * If the active shape key is either muted or locked, an error message will be reported, unless
 * \a reports is null.
 *
 * \return false if the shape key cannot be modified.
 */
bool shape_key_check(const Object &ob, ReportList *reports);

void operatortypes_sculpt();

void keymap_sculpt(wmKeyConfig *keyconf);

/* `sculpt_transform.cc` */

void update_modal_transform(bContext *C, Object &ob);
void cancel_modal_transform(bContext *C, Object &ob);
void init_transform(bContext *C, Object &ob, const float mval_fl[2], const char *undo_name);
void end_transform(bContext *C, Object &ob);

/* `sculpt_undo.cc` */

namespace undo {

void register_type(UndoType *ut);

/** Runtime state that Main serialization omits, owned only by typed MemFile boundaries. */
struct MainUndoState;
MainUndoState *main_undo_state_capture(Main &bmain, const char **r_error);
bool main_undo_state_restore(bContext *C, const MainUndoState &state, const char **r_error);
/** Leave safe empty sessions after a failed restore; never flush the state being abandoned. */
bool main_undo_state_discard_runtime(Main &bmain, const char **r_error);
/** After admission, prevent old evaluated grid caches from writing into the replacing Main. */
bool main_undo_state_suppress_old_grid_writeback(Main &bmain, const char **r_error);
/** Hold old Dyntopo meshes across an admitted private MemFile read, without writing to Mesh IDs.
 * End consumes the hold and returns meshes only to exact surviving runtime owners. */
struct MainUndoBMeshRead;
MainUndoBMeshRead *main_undo_bmesh_read_begin(Main &bmain, const char **r_error);
bool main_undo_bmesh_read_end(Main &bmain, MainUndoBMeshRead *read, const char **r_error);
#ifdef WITH_GTESTS
struct MainUndoTestFault;
std::shared_ptr<MainUndoTestFault> main_undo_state_test_fault_arm(MainUndoState &state,
                                                                  Main *source_main,
                                                                  UndoStack *source_stack,
                                                                  unsigned int object_index,
                                                                  const char **r_error,
                                                                  int grid_owner_index = -1);
std::shared_ptr<MainUndoTestFault> main_undo_test_fault_arm_index(bContext *C,
                                                                  int step_index,
                                                                  unsigned int object_index,
                                                                  const char **r_error,
                                                                  int grid_owner_index = -1);
void main_undo_state_test_fault_begin_decode(const MainUndoState &state,
                                             Main *source_main,
                                             UndoStack *source_stack);
void main_undo_state_test_fault_disarm(MainUndoTestFault &fault);
bool main_undo_state_test_fault_consumed(const MainUndoTestFault &fault);
unsigned int main_undo_state_test_fault_object_uid(const MainUndoTestFault &fault);
unsigned int main_undo_state_test_fault_scene_uid(const MainUndoTestFault &fault);
#endif
size_t main_undo_state_memory_size(const MainUndoState &state);
/** Conservative exact equality of the captured artist state; false includes unproved equality. */
bool main_undo_state_equals(const MainUndoState &a, const MainUndoState &b);
/** Decoder pin; main-thread ownership, independent of its raw-allocated MemFile step. */
MainUndoState *main_undo_state_retain(MainUndoState *state);
bool main_undo_state_has_grids(const MainUndoState *state);
void main_undo_state_free(MainUndoState *state);
/** A hidden Main baseline can finish at this Sculpt step only in its original active object. */
bool main_undo_step_matches_object(const UndoStep &step, const Object &object);

/**
 * Pushes an undo step using the operator name. This is necessary for
 * redo panels to work; operators that do not support that may use
 * #geometry_begin_ex instead if so desired.
 */
void geometry_begin(const Scene &scene, Object &ob, const wmOperator *op);
void geometry_begin_ex(const Scene &scene, Object &ob, const char *name);
void geometry_end(Object &ob);

/**
 * Undo for changes happening on a base mesh for multires sculpting.
 * if there is no multi-res sculpt active regular undo is used.
 */
void push_multires_mesh_begin(bContext *C, const char *str);
void push_multires_mesh_end(bContext *C, const char *str);

size_t step_memory_size_get(UndoStep *step);

}  // namespace undo

namespace face_set {

int find_next_available_id(const Mesh &object);
int find_next_available_id(Object &object);
void initialize_none_to_id(Mesh *mesh, int new_id);
int active_update_and_get(bContext *C, Object &ob, const float mval_fl[2]);

}  // namespace face_set

/**
 * Fills the entire object's active color attribute layer with the fill color.
 *
 * \return #true if successful.
 */
bool object_active_color_init(Object &ob, const float fill_color[4]);

/**
 * Fully replace the sculpt mesh with a mesh outside of #Main. This implements various checks to
 * avoid pushing full geometry-type undo steps when possible, allowing for better performance.
 *
 * \warning To avoid false negatives when detecting mesh changes, it is critical that the caller
 * adds an owner to the attribute data arrays before modifying the original object's mesh. This
 * allows constant time checks for whether the mesh has changed.
 */
void store_mesh_from_eval(const wmOperator &op,
                          const Scene &scene,
                          const Depsgraph &depsgraph,
                          const RegionView3D *rv3d,
                          Object &object,
                          Mesh *new_mesh);

}  // namespace ed::sculpt_paint

}  // namespace blender
