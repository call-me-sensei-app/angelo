/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * A yielded stage may own a separate Main, never hidden IDs in the artist Main.
 * Admission is deliberately conservative: only a closed, local ID graph with
 * no incoming references can move. Other stages use the existing bundle path.
 * Python owns binding verification, work budgets, the writer lease and Undo.
 */

#include <Python.h>

#include "MEM_guardedalloc.h"

#include "BLI_memory_utils.hh"
#include "BLI_set.hh"
#include "BLI_threads.h"
#include "BLI_vector.hh"

#include "ANIM_action.hh"
#include "BKE_global.hh"
#include "BKE_idprop.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_query.hh"
#include "BKE_main.hh"
#include "BKE_main_idmap.hh"
#include "BKE_scene.hh"
#include "BKE_undo_system.hh"

#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "../../editors/undo/angelo_commit_rights.hh"

#include "bpy_angelo_stage.hh"
#include "bpy_angelo_stage_layers.hh"
#include "bpy_angelo_undo.hh"
#include "bpy_rna.hh"

namespace blender {

static constexpr const char *capsule_name = "angelo.private_stage_main";

struct PrivateStage {
  Main main;
  Main *source_main = nullptr;
  Scene *anchor = nullptr;
  uint anchor_uid = 0;
  bool attached = false;
};

static bool require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Native stage ownership requires Blender's main thread");
    return false;
  }
  return true;
}

static bool id_from_python(PyObject *object, ID **id)
{
  if (BPy_StructRNA_Check(object)) {
    if (pyrna_struct_validity_check(reinterpret_cast<BPy_StructRNA *>(object)) == -1) {
      return false;
    }
    if (pyrna_id_FromPyObject(object, id) && *id) {
      return true;
    }
  }
  PyErr_SetString(PyExc_TypeError, "Expected a live Blender ID");
  return false;
}

static void capsule_free(PyObject *capsule)
{
  PrivateStage *stage = static_cast<PrivateStage *>(PyCapsule_GetPointer(capsule, capsule_name));
  /* The executor releases these on the main thread, including load/undo/unregister.
   * Main's destructor also invalidates Python RNA wrappers for every owned ID. */
  MEM_delete(stage);
}

static bool is_stage_scene(const ID *id)
{
  if (GS(id->name) != ID_SCE) {
    return false;
  }
  const IDProperty *tag = IDP_GetPropertyFromGroup_null(id->properties, "_angelo_program_stage");
  return tag && ((tag->type == IDP_STRING && tag->len > 1) ||
                 (ELEM(tag->type, IDP_BOOLEAN, IDP_INT) && IDP_int_or_bool_get(tag)));
}

static bool supported_id(const ID *id)
{
  /* Avoid resource, editor, library and simulation lifetimes. They remain fully
   * supported by file-backed staging. Embedded IDs are checked with their owner. */
  if (GS(id->name) == ID_OB) {
    const Object &object = *reinterpret_cast<const Object *>(id);
    /* Simulation caches contain non-ID runtime pointers outside foreach-ID's
     * ownership proof. Keep those objects, and interactive edit state, on the
     * existing serialized path. */
    if (object.mode != OB_MODE_OBJECT || object.soft || object.rigidbody_object ||
        object.rigidbody_constraint || !object.particlesystem.is_empty())
    {
      return false;
    }
  }
  return ELEM(GS(id->name),
              ID_SCE,
              ID_GR,
              ID_OB,
              ID_ME,
              ID_MA,
              ID_NT,
              ID_AC,
              ID_AR,
              ID_KE,
              ID_CA,
              ID_LA) &&
         !ID_IS_LINKED(id) && !ID_IS_OVERRIDE_LIBRARY(id) &&
         !(id->tag & (ID_TAG_NO_MAIN | ID_TAG_NO_USER_REFCOUNT | ID_TAG_TEMP_MAIN));
}

static void move_ids(Main &source, Main &destination, const Span<ID *> ids)
{
  BKE_main_relations_free(&source);
  BKE_main_relations_free(&destination);
  animrig::Slot::users_invalidate(source);
  animrig::Slot::users_invalidate(destination);
  for (ID *id : ids) {
    if (source.id_map) {
      BKE_main_idmap_remove_id(source.id_map, id);
    }
    BKE_libblock_management_main_remove(&source, id);
  }
  for (ID *id : ids) {
    BKE_libblock_management_main_add(&destination, id);
    if (destination.id_map) {
      BKE_main_idmap_insert_id(destination.id_map, id);
    }
  }
  /* No references cross this boundary, so user counts and collection links stay
   * exact. No artist depsgraph uses these IDs; its topology is unchanged. */
}

static PyObject *stage_detach(PyObject * /*self*/, PyObject *args)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  if (BKE_undosys_restore_protects_main()) {
    PyErr_SetString(PyExc_RuntimeError, BKE_undosys_restore_refusal_message());
    return nullptr;
  }
  PyObject *py_scene, *py_ids, *py_anchor;
  if (!PyArg_ParseTuple(args, "OOO:detach", &py_scene, &py_ids, &py_anchor)) {
    return nullptr;
  }
  ID *scene_id, *anchor_id;
  if (!id_from_python(py_scene, &scene_id) || !id_from_python(py_anchor, &anchor_id)) {
    return nullptr;
  }
  if (!is_stage_scene(scene_id) || GS(anchor_id->name) != ID_SCE || anchor_id == scene_id) {
    PyErr_SetString(PyExc_ValueError, "Expected a private stage and a distinct artist scene");
    return nullptr;
  }
  PyObject *sequence = PySequence_Fast(py_ids, "Expected owned stage IDs");
  if (!sequence) {
    return nullptr;
  }
  const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
  if (size < 1 || size > 2048) {
    Py_DECREF(sequence);
    Py_RETURN_NONE;
  }
  Set<ID *> owned;
  for (Py_ssize_t i = 0; i < size; i++) {
    ID *id;
    if (!id_from_python(PySequence_Fast_GET_ITEM(sequence, i), &id)) {
      Py_DECREF(sequence);
      return nullptr;
    }
    if (!supported_id(id) || (GS(id->name) == ID_SCE && id != scene_id)) {
      Py_DECREF(sequence);
      Py_RETURN_NONE;
    }
    owned.add(id);
  }
  Py_DECREF(sequence);
  if (!owned.contains(scene_id) || owned.contains(anchor_id)) {
    Py_RETURN_NONE;
  }

  Main *source = G_MAIN;
  Vector<ID *> ids;
  bool has_anchor = false;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (source, id) {
    if (owned.contains(id)) {
      ids.append(id);
    }
    has_anchor |= id == anchor_id;
  }
  FOREACH_MAIN_ID_END;
  if (ids.size() != owned.size() || !has_anchor) {
    PyErr_SetString(PyExc_ValueError, "Stage IDs and artist scene must belong to current Main");
    return nullptr;
  }

  const auto walk_flags = IDWALK_READONLY | IDWALK_INCLUDE_UI |
                          IDWALK_DO_INTERNAL_RUNTIME_POINTERS | IDWALK_DO_LIBRARY_POINTER;
  /* Include owned embedded node trees and the master collection. Non-owning
   * embedded references must pass the same closure test as any other link. */
  Set<ID *> closure = owned;
  for (ID *item : ids) {
    BKE_library_foreach_ID_link(
        source,
        item,
        [&closure](LibraryIDLinkCallbackData *data) {
          if ((data->cb_flag & IDWALK_CB_EMBEDDED) && *data->id_pointer) {
            closure.add(*data->id_pointer);
          }
          return IDWALK_RET_NOP;
        },
        nullptr,
        walk_flags);
  }
  bool closed = true;
  FOREACH_MAIN_ID_BEGIN (source, id) {
    const bool inside = owned.contains(id);
    BKE_library_foreach_ID_link(
        source,
        id,
        [&](LibraryIDLinkCallbackData *data) {
          ID *target = *data->id_pointer;
          if (target && closure.contains(target) != inside) {
            closed = false;
            return IDWALK_RET_STOP_ITER;
          }
          return IDWALK_RET_NOP;
        },
        nullptr,
        walk_flags);
    if (!closed) {
      break;
    }
  }
  FOREACH_MAIN_ID_END;
  if (!closed) {
    Py_RETURN_NONE; /* No mutation: use the existing serialized path. */
  }

  PrivateStage *stage = MEM_new<PrivateStage>(__func__);
  PyObject *capsule = PyCapsule_New(stage, capsule_name, capsule_free);
  if (!capsule) {
    MEM_delete(stage);
    return nullptr;
  }
  stage->source_main = source;
  stage->anchor = reinterpret_cast<Scene *>(anchor_id);
  stage->anchor_uid = anchor_id->session_uid;
  BKE_main_init_from_reference(stage->main, *source);
  /* Evaluated IDs and graphs belong to their original Main. Release them before
   * moving the original stage IDs; resume rebuilds after the usual preflight. */
  BKE_scene_free_depsgraph_hash(reinterpret_cast<Scene *>(scene_id));
  move_ids(*source, stage->main, ids);
  return capsule;
}

static PyObject *stage_attach(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PyObject *capsule;
  PyObject *operation_scope = PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) : Py_None;
  if (kwargs) {
    Py_ssize_t position = 0;
    PyObject *key, *value;
    while (PyDict_Next(kwargs, &position, &key, &value)) {
      if (PyUnicode_Check(key) && PyUnicode_CompareWithASCIIString(key, "commit_scope") == 0) {
        operation_scope = value;
        break;
      }
    }
  }
  /* Native tuple/Unicode reads above do not invoke caller conversions. Own the operation before
   * normal keyword parsing, including possible equality methods of unusual keyword objects. */
  if (operation_scope != Py_None && !BPY_angelo_undo_operation_begin(operation_scope)) {
    return nullptr;
  }
  Py_INCREF(operation_scope);
  BLI_SCOPED_DEFER([&]() {
    if (operation_scope != Py_None) {
      BPY_angelo_undo_operation_end(operation_scope);
    }
    Py_DECREF(operation_scope);
  });
  PyObject *commit_scope = Py_None;
  static const char *keywords[] = {"stage_capsule", "commit_scope", nullptr};
  if (!PyArg_ParseTupleAndKeywords(
          args, kwargs, "O|O:attach", const_cast<char **>(keywords), &capsule, &commit_scope))
  {
    return nullptr;
  }
  if (commit_scope != operation_scope) {
    PyErr_SetString(PyExc_RuntimeError, "The native stage commit scope changed during parsing");
    return nullptr;
  }
  if (commit_scope != Py_None &&
      !BPY_angelo_undo_register_stage_ids(commit_scope, G_MAIN, {}))
  {
    return nullptr;
  }
  PrivateStage *stage = static_cast<PrivateStage *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!stage) {
    return nullptr;
  }
  if (stage->attached) {
    PyErr_SetString(PyExc_RuntimeError, "The native stage has already been consumed");
    return nullptr;
  }
  bool same_document = false;
  if (stage->source_main == G_MAIN) {
    for (Scene &scene : G_MAIN->scenes) {
      if (&scene == stage->anchor && scene.id.session_uid == stage->anchor_uid) {
        same_document = true;
        break;
      }
    }
  }
  if (!same_document) {
    PyErr_SetString(PyExc_RuntimeError, "The native stage belongs to a previous document");
    return nullptr;
  }
  Vector<ID *> ids;
  Vector<AngeloCommitIDIdentity> native_identities;
  ID *id;
  FOREACH_MAIN_ID_BEGIN (&stage->main, id) {
    ids.append(id);
    if (commit_scope != Py_None) {
      /* Capture from the actually owned private Main before the move consumes it. These rows
       * remain provenance observations even if later admission refuses deletion authority. */
      native_identities.append({id, id->session_uid, short(GS(id->name))});
    }
  }
  FOREACH_MAIN_ID_END;
  move_ids(stage->main, *G_MAIN, ids);
  stage->attached = true;
  /* This is the actual detached Main inventory, including non-linkable Main IDs such as Keys.
   * Never reconstruct authority from the caller's old list, stage name or custom-property tag.
   * Registration retains these original observations before checking deletion eligibility. A
   * refusal after move leaves the stage attached; it cannot be retried as another transfer. */
  if (commit_scope != Py_None &&
      !BPY_angelo_undo_register_stage_ids(commit_scope, G_MAIN, native_identities))
  {
    PyObject *registration_error = PyErr_GetRaisedException();
    PyErr_SetString(PyExc_RuntimeError,
                    "Native stage IDs were attached but cleanup ownership registration failed; "
                    "the stage remains attached to Main");
    PyObject *error = PyErr_GetRaisedException();
    if (registration_error) {
      PyException_SetCause(error, registration_error); /* Steals the original exception. */
    }
    PyErr_SetRaisedException(error);
    return nullptr;
  }
  Py_RETURN_NONE;
}

static PyObject *stage_discard(PyObject * /*self*/, PyObject *capsule)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  PrivateStage *stage = static_cast<PrivateStage *>(PyCapsule_GetPointer(capsule, capsule_name));
  if (!stage) {
    return nullptr;
  }
  BKE_main_clear(stage->main);
  stage->attached = true;
  Py_RETURN_NONE;
}

#ifdef __GNUC__
#  ifdef __clang__
#    pragma clang diagnostic push
#    pragma clang diagnostic ignored "-Wcast-function-type"
#  else
#    pragma GCC diagnostic push
#    pragma GCC diagnostic ignored "-Wcast-function-type"
#  endif
#endif

static PyMethodDef methods[] = {
    {"detach",
     stage_detach,
     METH_VARARGS,
     "Move a closed private stage out of Main, or return None without modifying it."},
    {"attach",
     reinterpret_cast<PyCFunction>(stage_attach),
     METH_VARARGS | METH_KEYWORDS,
     "Consume private ownership and restore the stage to Main, optionally binding commit ownership."},
    {"discard",
     stage_discard,
     METH_O,
     "Free detached IDs; never free IDs already restored to Main."},
    {"layer_flags",
     BPY_angelo_stage_layer_flags,
     METH_VARARGS,
     "(depth, name, exclude (1) and previously-excluded (2) bits, linked, session UID) of each "
     "layer collection, depth first."},
    {"set_layer_flags",
     BPY_angelo_stage_set_layer_flags,
     METH_VARARGS,
     "Write back every layer collection's exclude and previously-excluded bits, depth first."},
    {nullptr, nullptr, 0, nullptr},
};

#ifdef __GNUC__
#  ifdef __clang__
#    pragma clang diagnostic pop
#  else
#    pragma GCC diagnostic pop
#  endif
#endif

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_stage",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_stage_module()
{
  return PyModule_Create(&module);
}

}  // namespace blender
