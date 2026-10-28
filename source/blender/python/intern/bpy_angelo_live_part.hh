/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * A live part as `_bpy._angelo_live` holds it (`bpy_angelo_live.cc` reads and queries it, `bpy_angelo_live_draw.cc`
 * draws it; `bpy_angelo_live_hold.cc` holds the 3D view it covers). Private to those files.
 */

#pragma once

#include <Python.h>

namespace blender {

struct Depsgraph;
struct Main;
struct Object;
struct Scene;
struct ViewLayer;

struct LivePart {
  Main *main = nullptr;
  Scene *scene = nullptr;
  ViewLayer *view_layer = nullptr;
  Depsgraph *depsgraph = nullptr;
  bool evaluated = false;
};

/** False with a Python error set when not on Blender's main thread. */
bool live_require_main_thread();
/** The open, evaluated part of a capsule, or null with a Python error set (never drawn unevaluated). */
LivePart *live_evaluated_part(PyObject *capsule);
/** Whether \a object is a live part's capsule (whatever its state). */
bool live_is_part(PyObject *object);
/** `draw(...)`: see the module's method table. */
PyObject *live_draw(PyObject *self, PyObject *args, PyObject *kwds);
PyObject *live_shader_state(PyObject *self, PyObject *args);
PyObject *live_shown_state(PyObject *self, PyObject *args);
/** The input hold and the window capture (`bpy_angelo_live_hold.cc`): see the module's method table. */
PyObject *live_hold(PyObject *self, PyObject *args);
PyObject *live_release_holds(PyObject *self, PyObject *args);
PyObject *live_allow(PyObject *self, PyObject *idnames);
PyObject *live_held(PyObject *self, PyObject *args);
PyObject *live_window_pixels(PyObject *self, PyObject *window);
PyObject *live_text_info_end(PyObject *self, PyObject *region);
/** The world box of every visible object and instance of an evaluated \a depsgraph, helper geometry left out (Boolean
 * operands, wire or bounds display, hidden from renders); false when there is none. */
bool live_depsgraph_bounds(Depsgraph *depsgraph, float r_min[3], float r_max[3], bool helpers_alone = true);
/** Whether a view with perspective matrix \a persmat sees geometry the Main picture counts (its frustum meets one
 * object's world box, as ``live_depsgraph_bounds`` without helpers alone counts them). */
bool live_depsgraph_sees(Depsgraph *depsgraph, const float persmat[4][4]);
/** `depsgraph_size(...)`: see the module's method table. */
PyObject *live_depsgraph_size(PyObject *self, PyObject *depsgraph);
/** `depsgraph_bounds(...)`: see the module's method table. */
PyObject *live_depsgraph_bounds_py(PyObject *self, PyObject *args);
/** `depsgraph_sees(...)`: see the module's method table. */
PyObject *live_depsgraph_sees_py(PyObject *self, PyObject *args);
/** Whether \a ob is geometry as a part's size counts it: as evaluated, a curve's bevel, text, geometry nodes. */
bool live_is_geometry(const Object *ob);
/** The world box of the geometry \a ob owns, merged into \a r_min / \a r_max; false when it owns none. */
bool live_merge_owned_bounds(const Object *ob, float r_min[3], float r_max[3]);
/** Whether \a view_layer shows \a original: a base enabled for \a mode as the depsgraph's builder pulls it in. */
bool live_shown_here(ViewLayer *view_layer, Object *original, int mode);
/** `instance_bounds(...)`: see the module's method table. */
PyObject *live_instance_bounds(PyObject *self, PyObject *args);
/** `update_count(...)`: see the module's method table. */
PyObject *live_update_count(PyObject *self, PyObject *depsgraph);

}  // namespace blender
