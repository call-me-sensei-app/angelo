/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pygen
 */

#pragma once

#include <Python.h>

#include "py_capi_dependent.hh"

namespace blender {

struct BPy_IDGroup_Iter;
struct ID;
struct IDProperty;

extern PyTypeObject BPy_IDArray_Type;
extern PyTypeObject BPy_IDGroup_Type;

extern PyTypeObject BPy_IDGroup_ViewKeys_Type;
extern PyTypeObject BPy_IDGroup_ViewValues_Type;
extern PyTypeObject BPy_IDGroup_ViewItems_Type;

extern PyTypeObject BPy_IDGroup_IterKeys_Type;
extern PyTypeObject BPy_IDGroup_IterValues_Type;
extern PyTypeObject BPy_IDGroup_IterItems_Type;

#define BPy_IDArray_Check(v) (PyObject_TypeCheck(v, &BPy_IDArray_Type))
#define BPy_IDArray_CheckExact(v) (Py_TYPE(v) == &BPy_IDArray_Type)
#define BPy_IDGroup_Check(v) (PyObject_TypeCheck(v, &BPy_IDGroup_Type))
#define BPy_IDGroup_CheckExact(v) (Py_TYPE(v) == &BPy_IDGroup_Type)

#define BPy_IDGroup_ViewKeys_Check(v) (PyObject_TypeCheck(v, &BPy_IDGroup_ViewKeys_Type))
#define BPy_IDGroup_ViewKeys_CheckExact(v) (Py_TYPE(v) == &BPy_IDGroup_ViewKeys_Type)
#define BPy_IDGroup_ViewValues_Check(v) (PyObject_TypeCheck(v, &BPy_IDGroup_ViewValues_Type))
#define BPy_IDGroup_ViewValues_CheckExact(v) (Py_TYPE(v) == &BPy_IDGroup_ViewValues_Type)
#define BPy_IDGroup_ViewItems_Check(v) (PyObject_TypeCheck(v, &BPy_IDGroup_ViewItems_Type))
#define BPy_IDGroup_ViewItems_CheckExact(v) (Py_TYPE(v) == &BPy_IDGroup_ViewItems_Type)

#define BPy_IDGroup_IterKeys_Check(v) (PyObject_TypeCheck(v, &BPy_IDGroup_IterKeys_Type))
#define BPy_IDGroup_IterKeys_CheckExact(v) (Py_TYPE(v) == &BPy_IDGroup_IterKeys_Type)
#define BPy_IDGroup_IterValues_Check(v) (PyObject_TypeCheck(v, &BPy_IDGroup_IterValues_Type))
#define BPy_IDGroup_IterValues_CheckExact(v) (Py_TYPE(v) == &BPy_IDGroup_IterValues_Type)
#define BPy_IDGroup_IterItems_Check(v) (PyObject_TypeCheck(v, &BPy_IDGroup_IterItems_Type))
#define BPy_IDGroup_IterItems_CheckExact(v) (Py_TYPE(v) == &BPy_IDGroup_IterItems_Type)

struct BPy_IDProperty {
  PyObject_HEAD
  struct ID *owner_id;     /* can be NULL */
  struct IDProperty *prop; /* must be second member */
  struct IDProperty *parent;
  /**
   * Angelo: invalidated (`prop` cleared) when the owner ID is freed, so the wrapper raises
   * ReferenceError instead of reading freed memory (`bpy_rna.cc`, "Dependent Wrappers").
   */
  BPyRNA_DependentLink dependent;
};

struct BPy_IDArray {
  PyObject_HEAD
  struct ID *owner_id;     /* can be NULL */
  struct IDProperty *prop; /* must be second member */
  /** Angelo: as #BPy_IDProperty::dependent. */
  BPyRNA_DependentLink dependent;
};

struct BPy_IDGroup_Iter {
  PyObject_HEAD
  BPy_IDProperty *group;
  struct IDProperty *cur;
  mem_lifetime::Watch cursor_watch;
  /** Use for detecting manipulation during iteration (which is not allowed). */
  int len_init;
  /** Iterate in the reverse direction. */
  bool reversed;
};

/** Use to implement `IDPropertyGroup.keys/values/items` */
struct BPy_IDGroup_View {
  PyObject_HEAD
  /** This will be NULL when accessing keys on data that has no ID properties. */
  BPy_IDProperty *group;
  bool reversed;
};

/** Angelo: a new group wrapper, invalidated when `id` is freed (`id` may be null). */
[[nodiscard]] PyObject *BPy_IDGroup_CreatePyObject(ID *id, IDProperty *prop, IDProperty *parent);
[[nodiscard]] PyObject *BPy_Wrap_GetKeys(IDProperty *prop);
[[nodiscard]] PyObject *BPy_Wrap_GetValues(ID *id, IDProperty *prop);
[[nodiscard]] PyObject *BPy_Wrap_GetItems(ID *id, IDProperty *prop);

[[nodiscard]] PyObject *BPy_Wrap_GetKeys_View_WithID(ID *id, IDProperty *prop);
[[nodiscard]] PyObject *BPy_Wrap_GetValues_View_WithID(ID *id, IDProperty *prop);
[[nodiscard]] PyObject *BPy_Wrap_GetItems_View_WithID(ID *id, IDProperty *prop);

[[nodiscard]] int BPy_Wrap_SetMapItem(IDProperty *prop, PyObject *key, PyObject *val);

/**
 * For simple, non nested types this is the same as #BPy_IDGroup_WrapData.
 */
[[nodiscard]] PyObject *BPy_IDGroup_MapDataToPy(IDProperty *prop);
[[nodiscard]] PyObject *BPy_IDGroup_WrapData(ID *id, IDProperty *prop, IDProperty *parent);
/**
 * \note group can be a pointer array or a group.
 * assume we already checked key is a string.
 *
 * \return success.
 */
[[nodiscard]] bool BPy_IDProperty_Map_ValidateAndCreate(PyObject *key,
                                                        IDProperty *group,
                                                        PyObject *ob);
/**
 * Angelo: tag the depsgraph relations when `prop` holds a data-block pointer, itself or in any
 * nested group or array. For a Python write that removes `prop` or changes the path to it (a
 * driver path may resolve through it); call it before freeing. Main thread only, else nothing.
 */
void BPy_IDProperty_tag_relations_if_holds_id(const IDProperty *prop);

void IDProp_Init_Types();

[[nodiscard]] PyObject *BPyInit_idprop();

/**
 * Create an IDProperty from a Python object.
 *
 * \param prop_exist: pre-existing IDProperty to populate with the value. Can be `nullptr` to
 * allocate a new IDProperty.
 * \param name: the name of the IDProperty. Only used when creating a new IDProperty.
 * \param ob: the Python object to convert.
 * \param do_conversion: when there is a pre-existing IDProperty, whether the Python object's value
 * should be converted to its type (if not the same type already).
 * \param can_create: whether the function is allowed to create a new property. If this is `false`
 * and `prop_exists` is `nullptr`, this function is a no-op.
 *
 * \return the existing/created IDProperty if the value was set on it, and `nullptr` otherwise.
 */
IDProperty *BPy_IDProperty_FromPyObject(
    IDProperty *prop_exist, const char *name, PyObject *ob, bool do_conversion, bool can_create);

}  // namespace blender
