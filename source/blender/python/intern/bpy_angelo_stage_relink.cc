/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * ``_angelo_stage.relink``, ``unrelink``, ``links``, ``reach``, ``stand_in`` and ``copy_plain``:
 * a private stage's own links (BKE_angelo_stage_links.hh; doc/angelo/agent_execution.rst,
 * "Links between staged copies"). Main thread only; refused while Undo restores Main.
 */

#include <Python.h>

#include "MEM_guardedalloc.h"

#include "BLI_threads.h"
#include "BLI_vector.hh"

#include "BKE_angelo_stage_links.hh"
#include "BKE_global.hh"
#include "BKE_lib_id.hh"
#include "BKE_lib_query.hh"
#include "BKE_undo_system.hh"

#include "bpy_angelo_stage_relink.hh"
#include "bpy_rna.hh"

namespace blender {

static constexpr const char *journal_capsule = "angelo.stage_relink_journal";
static constexpr Py_ssize_t max_ids = 65536;

static bool allowed()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Native stage ownership requires Blender's main thread");
    return false;
  }
  /* Never while an Undo restore replaces Main; a native commit's own capture scope is the commit
   * itself (stage_links.commit relinks inside it). */
  if (BKE_undosys_restore_protects_main() && !BKE_undosys_capture_scope_active()) {
    PyErr_SetString(PyExc_RuntimeError, BKE_undosys_restore_refusal_message());
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

/** Sequence items as live IDs; false with a Python error set. */
static bool ids_from_sequence(PyObject *py_sequence, const char *message, Vector<ID *> &r_ids)
{
  PyObject *sequence = PySequence_Fast(py_sequence, message);
  if (!sequence) {
    return false;
  }
  const Py_ssize_t size = PySequence_Fast_GET_SIZE(sequence);
  if (size > max_ids) {
    Py_DECREF(sequence);
    PyErr_SetString(PyExc_ValueError, "Too many IDs in one call");
    return false;
  }
  for (Py_ssize_t i = 0; i < size; i++) {
    ID *id;
    if (!id_from_python(PySequence_Fast_GET_ITEM(sequence, i), &id)) {
      Py_DECREF(sequence);
      return false;
    }
    r_ids.append(id);
  }
  Py_DECREF(sequence);
  return true;
}

static PyObject *id_list(const Span<ID *> ids)
{
  PyObject *result = PyList_New(ids.size());
  for (const int64_t i : ids.index_range()) {
    PyList_SET_ITEM(result, i, pyrna_id_CreatePyObject(ids[i]));
  }
  return result;
}

static void journal_free(PyObject *capsule)
{
  MEM_delete(static_cast<Vector<AngeloStageRelinkSlot> *>(
      PyCapsule_GetPointer(capsule, journal_capsule)));
}

PyObject *BPY_angelo_stage_relink(PyObject * /*self*/, PyObject *args)
{
  if (!allowed()) {
    return nullptr;
  }
  PyObject *py_ids, *py_pairs;
  if (!PyArg_ParseTuple(args, "OO:relink", &py_ids, &py_pairs)) {
    return nullptr;
  }
  Vector<ID *> ids;
  if (!ids_from_sequence(py_ids, "Expected the stage's own IDs", ids)) {
    return nullptr;
  }
  PyObject *pairs_sequence = PySequence_Fast(py_pairs, "Expected (old, new) ID pairs");
  if (!pairs_sequence) {
    return nullptr;
  }
  Vector<std::pair<ID *, ID *>> pairs;
  const Py_ssize_t size = PySequence_Fast_GET_SIZE(pairs_sequence);
  if (size > max_ids) {
    Py_DECREF(pairs_sequence);
    PyErr_SetString(PyExc_ValueError, "Too many relink pairs in one call");
    return nullptr;
  }
  for (Py_ssize_t i = 0; i < size; i++) {
    Vector<ID *> pair;
    PyObject *item = PySequence_Fast_GET_ITEM(pairs_sequence, i);
    if (!ids_from_sequence(item, "Expected an (old, new) ID pair", pair) || pair.size() != 2) {
      Py_DECREF(pairs_sequence);
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_ValueError, "Expected an (old, new) ID pair");
      }
      return nullptr;
    }
    pairs.append({pair[0], pair[1]});
  }
  Py_DECREF(pairs_sequence);
  Vector<ID *> outside;
  Vector<AngeloStageRelinkSlot> *journal = MEM_new<Vector<AngeloStageRelinkSlot>>(__func__);
  std::string error;
  if (!BKE_angelo_stage_relink(*G_MAIN, ids, pairs, outside, *journal, error)) {
    MEM_delete(journal);
    PyErr_SetString(PyExc_ValueError, error.c_str());
    return nullptr;
  }
  PyObject *capsule = PyCapsule_New(journal, journal_capsule, journal_free);
  if (!capsule) {
    MEM_delete(journal);
    return nullptr;
  }
  return Py_BuildValue("(NN)", id_list(outside), capsule);
}

PyObject *BPY_angelo_stage_unrelink(PyObject * /*self*/, PyObject *capsule)
{
  if (!allowed()) {
    return nullptr;
  }
  auto *journal = static_cast<Vector<AngeloStageRelinkSlot> *>(
      PyCapsule_GetPointer(capsule, journal_capsule));
  if (!journal) {
    return nullptr;
  }
  int skipped = 0;
  const int restored = BKE_angelo_stage_unrelink(*G_MAIN, *journal, skipped);
  journal->clear(); /* A journal replays once. */
  return Py_BuildValue("(ii)", restored, skipped);
}

PyObject *BPY_angelo_stage_links(PyObject * /*self*/, PyObject *py_ids)
{
  if (!allowed()) {
    return nullptr;
  }
  Vector<ID *> ids;
  if (!ids_from_sequence(py_ids, "Expected IDs", ids)) {
    return nullptr;
  }
  PyObject *result = PyList_New(0);
  bool failed = false;
  std::string error;
  const bool walked = BKE_angelo_stage_links(
      *G_MAIN,
      ids,
      [&](ID *owner, ID *holder, ID *target, const int flags) {
        if (failed) {
          return;
        }
        PyObject *row = Py_BuildValue("(NNNi)",
                                      pyrna_id_CreatePyObject(owner),
                                      pyrna_id_CreatePyObject(holder),
                                      pyrna_id_CreatePyObject(target),
                                      flags);
        failed = row == nullptr || PyList_Append(result, row) != 0;
        Py_XDECREF(row);
      },
      error);
  if (!walked) {
    PyErr_SetString(PyExc_ValueError, error.c_str());
  }
  if (failed || !walked) {
    Py_DECREF(result);
    return nullptr;
  }
  return result;
}

PyObject *BPY_angelo_stage_reach(PyObject * /*self*/, PyObject *args)
{
  if (!allowed()) {
    return nullptr;
  }
  PyObject *py_roots, *py_exclude;
  Py_ssize_t limit;
  if (!PyArg_ParseTuple(args, "OOn:reach", &py_roots, &py_exclude, &limit)) {
    return nullptr;
  }
  Vector<ID *> roots, exclude;
  if (!ids_from_sequence(py_roots, "Expected IDs", roots) ||
      !ids_from_sequence(py_exclude, "Expected IDs", exclude))
  {
    return nullptr;
  }
  Vector<ID *> reached, animated;
  std::string error;
  if (!BKE_angelo_stage_reach(*G_MAIN, roots, exclude, limit, reached, animated, error)) {
    if (!error.empty()) {
      PyErr_SetString(PyExc_ValueError, error.c_str());
      return nullptr;
    }
    return Py_BuildValue("(ON)", Py_None, id_list(animated));
  }
  return Py_BuildValue("(NN)", id_list(reached), id_list(animated));
}

bool BPY_angelo_stage_add_link_flags(PyObject *module)
{
  const std::pair<const char *, int> flags[] = {
      {"LINK_USER", IDWALK_CB_USER},
      {"LINK_NEVER_NULL", IDWALK_CB_NEVER_NULL},
      {"LINK_NEVER_SELF", IDWALK_CB_NEVER_SELF},
      {"LINK_LOOPBACK", IDWALK_CB_LOOPBACK},
      {"LINK_EMBEDDED", IDWALK_CB_EMBEDDED},
      {"LINK_EMBEDDED_NOT_OWNING", IDWALK_CB_EMBEDDED_NOT_OWNING},
      {"LINK_DIRECT_WEAK", IDWALK_CB_DIRECT_WEAK_LINK},
      {"LINK_OVERRIDE_LIBRARY_REFERENCE", IDWALK_CB_OVERRIDE_LIBRARY_REFERENCE},
  };
  for (const std::pair<const char *, int> &flag : flags) {
    if (PyModule_AddIntConstant(module, flag.first, flag.second) != 0) {
      return false;
    }
  }
  return true;
}

static PyObject *new_id(ID *created)
{
  if (created == nullptr) {
    Py_RETURN_NONE;
  }
  return pyrna_id_CreatePyObject(created);
}

PyObject *BPY_angelo_stage_stand_in(PyObject * /*self*/, PyObject *py_like)
{
  ID *like;
  if (!allowed() || !id_from_python(py_like, &like)) {
    return nullptr;
  }
  return new_id(BKE_angelo_stage_stand_in(*G_MAIN, *like));
}

PyObject *BPY_angelo_stage_session_uid_last(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyLong_FromUnsignedLong(BKE_lib_libblock_session_uid_last());
}

PyObject *BPY_angelo_stage_copy_plain(PyObject * /*self*/, PyObject *py_id)
{
  ID *id;
  if (!allowed() || !id_from_python(py_id, &id)) {
    return nullptr;
  }
  return new_id(BKE_angelo_stage_copy_plain(*G_MAIN, *id));
}

}  // namespace blender
