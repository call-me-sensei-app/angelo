/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * `_bpy._angelo_reduce`: mesh reduction and its measurement for `angelo_game_ready_check`
 * (doc/angelo/game_ready_check.rst).
 *
 * - `status()`, `simplify()`, `weld()` and `scale()` reach the bundled meshoptimizer through
 *   `bpy_angelo_meshopt.hh`, which opens it on first use and refuses a missing or older library. A
 *   refusal raises `RuntimeError` with the reason and calls nothing. `simplify()` returns index buffers
 *   over the caller's own vertices, so a chain of reductions shares the first one's vertex buffer.
 * - `decimate()` runs Blender's Decimate (Collapse) on a copy of a mesh for comparison, on a
 *   `snapshot()` off the main thread. `convex_hull()` is Blender's convex hull operator (a build
 *   with Bullet).
 * - `surface()`, `nearest()` and `rasterize()` measure: distances from points to a triangle surface
 *   (Hausdorff), and triangle coverage of pixel centres (silhouettes, UV overlap).
 *
 * Arrays are any C-contiguous buffer (NumPy arrays): float32 for positions, attributes, weights and
 * points; uint32 or int32 for indices; uint8 for vertex locks. Every index and value is checked before a
 * call, because meshoptimizer only asserts its preconditions and a release build would read out of
 * bounds. Results are `bytes` (`numpy.frombuffer` reads them without a copy). Heavy work releases the GIL;
 * nothing here touches Blender data except `snapshot()`, `vertex_weights()` and `decimate()` given
 * a Blender mesh, which only read it (main thread).
 */

#include <Python.h>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>
#include <vector>

#include "BLI_array.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"
#include "BLI_utildefines.h"
#include "BLI_vector.hh"

#include "DNA_mesh_types.h"

#include "../generic/py_capi_utils.hh"

#include "bpy_angelo_meshopt.hh"
#include "bpy_angelo_reduce.hh"
#include "bpy_angelo_reduce_decimate.hh"
#include "bpy_angelo_reduce_geometry.hh"

namespace blender {

namespace {

/* The stable meshoptimizer 1.x options; the experimental Permissive and RegularizeLight are refused. */
constexpr unsigned int OPTION_LOCK_BORDER = 1 << 0;
constexpr unsigned int OPTION_SPARSE = 1 << 1;
constexpr unsigned int OPTION_ERROR_ABSOLUTE = 1 << 2;
constexpr unsigned int OPTION_PRUNE = 1 << 3;
constexpr unsigned int OPTION_REGULARIZE = 1 << 4;
constexpr unsigned int STABLE_OPTIONS = OPTION_LOCK_BORDER | OPTION_SPARSE | OPTION_ERROR_ABSOLUTE |
                                        OPTION_PRUNE | OPTION_REGULARIZE;
constexpr int MAX_ATTRIBUTES = 32;
constexpr int MAX_STREAMS = 16;
constexpr int MAX_RESOLUTION = 4096;
constexpr int64_t MAX_RASTER_BYTES = int64_t(256) << 20;
const char *const SURFACE_CAPSULE = "_bpy._angelo_reduce.Surface";
const char *const SNAPSHOT_CAPSULE = "_bpy._angelo_reduce.Snapshot";

enum class Kind { Float, Index, Byte, Any };

/**
 * An argument's contents, copied out of the caller's buffer before anything checks them. The copy is the
 * module's own, so a Python thread changing the array after the checks (the GIL is released while the
 * work runs) can never reach an index that was validated: everything below reads only this copy.
 */
struct Buffer {
  std::vector<uint8_t> bytes;
  int64_t itemsize = 1;
  bool held = false;
  Buffer() = default;
  Buffer(const Buffer &) = delete;
  int64_t count() const
  {
    return held ? int64_t(bytes.size()) / itemsize : 0;
  }
  const void *data() const
  {
    return bytes.data();
  }
  template<typename T> Span<T> span() const
  {
    return held ? Span<T>(reinterpret_cast<const T *>(bytes.data()), count()) : Span<T>();
  }
};

bool format_is(const char *format, const char *const *allowed)
{
  const char *f = format ? format : "B";
  while (*f == '@' || *f == '=' || *f == '<') {
    f++;
  }
  for (; *allowed; allowed++) {
    if (std::strcmp(f, *allowed) == 0) {
      return true;
    }
  }
  return false;
}

/** Hold `object`'s buffer as `kind`; false with a Python error set when it is not one. None is allowed
 * (an empty buffer) only when `optional`. */
bool get_buffer(PyObject *object, const Kind kind, const char *what, Buffer &r_buffer, bool optional = false)
{
  if (object == nullptr || object == Py_None) {
    if (optional) {
      return true;
    }
    PyErr_Format(PyExc_TypeError, "%s is required", what);
    return false;
  }
  Py_buffer view{};
  if (PyObject_GetBuffer(object, &view, PyBUF_C_CONTIGUOUS | PyBUF_FORMAT) == -1) {
    PyErr_Clear();
    PyErr_Format(PyExc_TypeError, "%s must be a C-contiguous array", what);
    return false;
  }
  static const char *const floats[] = {"f", nullptr};
  static const char *const indices[] = {"I", "i", "L", "l", nullptr};
  static const char *const bytes[] = {"B", "b", "?", "c", nullptr};
  bool ok = true;
  switch (kind) {
    case Kind::Float:
      ok = view.itemsize == 4 && format_is(view.format, floats);
      break;
    case Kind::Index:
      ok = view.itemsize == 4 && format_is(view.format, indices);
      break;
    case Kind::Byte:
      ok = view.itemsize == 1 && format_is(view.format, bytes);
      break;
    case Kind::Any:
      break;
  }
  if (ok) {
    r_buffer.itemsize = kind == Kind::Any ? 1 : view.itemsize;
    r_buffer.bytes.assign(static_cast<const uint8_t *>(view.buf), static_cast<const uint8_t *>(view.buf) + view.len);
    r_buffer.held = true;
  }
  PyBuffer_Release(&view);
  if (!ok) {
    PyErr_Format(PyExc_TypeError,
                 "%s must be %s",
                 what,
                 kind == Kind::Float ? "float32" : (kind == Kind::Index ? "uint32 or int32" : "uint8"));
    return false;
  }
  return true;
}

bool all_finite(Span<float> values, const char *what)
{
  for (const float value : values) {
    if (!std::isfinite(value)) {
      PyErr_Format(PyExc_ValueError, "%s must be finite", what);
      return false;
    }
  }
  return true;
}

/** `count` elements of `size` bytes as a new `bytes` object; `r_data` points at its storage. */
PyObject *new_bytes(const int64_t count, const size_t size, char **r_data)
{
  PyObject *bytes = PyBytes_FromStringAndSize(nullptr, Py_ssize_t(count * int64_t(size)));
  *r_data = bytes ? PyBytes_AS_STRING(bytes) : nullptr;
  return bytes;
}

/** Positions as float3s: a float32 array of 3 per vertex. */
bool get_positions(PyObject *object, Buffer &buffer, Span<float3> &r_positions, const char *what)
{
  if (!get_buffer(object, Kind::Float, what, buffer)) {
    return false;
  }
  if (buffer.count() % 3 != 0) {
    PyErr_Format(PyExc_ValueError, "%s must hold 3 floats per vertex", what);
    return false;
  }
  if (!all_finite(buffer.span<float>(), what)) {
    return false;
  }
  r_positions = Span<float3>(static_cast<const float3 *>(buffer.data()), buffer.count() / 3);
  return true;
}

/** Triangle indices: 3 per triangle, each below `vertex_count`. */
bool get_indices(PyObject *object, Buffer &buffer, const int64_t vertex_count, Span<uint32_t> &r_indices)
{
  if (!get_buffer(object, Kind::Index, "indices", buffer)) {
    return false;
  }
  if (buffer.count() % 3 != 0) {
    PyErr_SetString(PyExc_ValueError, "indices must hold 3 vertex indices per triangle");
    return false;
  }
  r_indices = buffer.span<uint32_t>();
  for (const uint32_t index : r_indices) {
    if (int64_t(index) >= vertex_count) { /* An int32 below zero reads as a large uint32. */
      PyErr_SetString(PyExc_ValueError, "indices must name existing vertices");
      return false;
    }
  }
  return true;
}

const angelo_meshopt::Api *require_meshopt()
{
  const angelo_meshopt::Api *api = angelo_meshopt::api();
  if (api == nullptr) {
    PyErr_SetString(PyExc_RuntimeError, angelo_meshopt::status().reason.c_str());
  }
  return api;
}

}  // namespace

static PyObject *reduce_status(PyObject * /*self*/, PyObject * /*args*/)
{
  const angelo_meshopt::Status status = angelo_meshopt::status();
  /* The path comes from the file system, in its own encoding. */
  PyObject *path = PyUnicode_DecodeFSDefault(status.library_path.c_str());
  if (path == nullptr) {
    return nullptr;
  }
  return Py_BuildValue("{s:O,s:s,s:N,s:i}",
                       "available",
                       status.available ? Py_True : Py_False,
                       "reason",
                       status.reason.c_str(),
                       "library_path",
                       path,
                       "header_version",
                       status.header_version);
}

static PyObject *reduce_attempted(PyObject * /*self*/, PyObject * /*args*/)
{
  return PyBool_FromLong(angelo_meshopt::attempted());
}

#ifdef ANGELO_MESHOPT_SIMPLIFY

static PyObject *reduce_simplify(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  static const char *keywords[] = {"positions",
                                   "indices",
                                   "target_index_count",
                                   "target_error",
                                   "mode",
                                   "attributes",
                                   "weights",
                                   "lock",
                                   "options",
                                   nullptr};
  PyObject *py_positions, *py_indices, *py_attributes = Py_None, *py_weights = Py_None, *py_lock = Py_None;
  Py_ssize_t target_index_count;
  float target_error;
  const char *mode = "ATTRIBUTES";
  unsigned int options = 0;
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwargs,
                                   "OOnf|$sOOOI:simplify",
                                   const_cast<char **>(keywords),
                                   &py_positions,
                                   &py_indices,
                                   &target_index_count,
                                   &target_error,
                                   &mode,
                                   &py_attributes,
                                   &py_weights,
                                   &py_lock,
                                   &options))
  {
    return nullptr;
  }
  const bool plain = STREQ(mode, "PLAIN"), attributes_mode = STREQ(mode, "ATTRIBUTES");
  const bool update = STREQ(mode, "UPDATE"), sloppy = STREQ(mode, "SLOPPY");
  if (!(plain || attributes_mode || update || sloppy)) {
    PyErr_SetString(PyExc_ValueError, "mode must be PLAIN, ATTRIBUTES, UPDATE or SLOPPY");
    return nullptr;
  }
  if ((options & ~STABLE_OPTIONS) != 0 || (sloppy && options != 0)) {
    PyErr_SetString(PyExc_ValueError,
                    "options must combine LOCK_BORDER, SPARSE, ERROR_ABSOLUTE, PRUNE and REGULARIZE "
                    "(SLOPPY takes none)");
    return nullptr;
  }
  const bool absolute = options & OPTION_ERROR_ABSOLUTE;
  if (!std::isfinite(target_error) || target_error < 0.0f || (!absolute && target_error > 1.0f)) {
    PyErr_SetString(PyExc_ValueError, "target_error must be 0 to 1 (or at least 0 with ERROR_ABSOLUTE)");
    return nullptr;
  }
  Buffer positions_buffer, indices_buffer, attributes_buffer, weights_buffer, lock_buffer;
  Span<float3> positions;
  Span<uint32_t> indices;
  if (!get_positions(py_positions, positions_buffer, positions, "positions") ||
      !get_indices(py_indices, indices_buffer, positions.size(), indices))
  {
    return nullptr;
  }
  const size_t vertex_count = size_t(positions.size()), index_count = size_t(indices.size());
  if (target_index_count < 0 || size_t(target_index_count) > index_count) {
    PyErr_SetString(PyExc_ValueError, "target_index_count must be 0 to the number of indices");
    return nullptr;
  }
  if (!get_buffer(py_weights, Kind::Float, "weights", weights_buffer, true) ||
      !get_buffer(py_attributes, Kind::Float, "attributes", attributes_buffer, true) ||
      !get_buffer(py_lock, Kind::Byte, "lock", lock_buffer, true))
  {
    return nullptr;
  }
  const int64_t attribute_count = weights_buffer.count();
  if ((plain || sloppy) && (attributes_buffer.held || weights_buffer.held)) {
    PyErr_SetString(PyExc_ValueError, "PLAIN and SLOPPY take no attributes");
    return nullptr;
  }
  if (plain && lock_buffer.held) {
    PyErr_SetString(PyExc_ValueError, "PLAIN takes no lock");
    return nullptr;
  }
  if (attribute_count > MAX_ATTRIBUTES || attributes_buffer.count() != attribute_count * int64_t(vertex_count)) {
    PyErr_SetString(PyExc_ValueError,
                    "attributes must hold one float per weight for each vertex, at most 32 per vertex");
    return nullptr;
  }
  if (!all_finite(attributes_buffer.span<float>(), "attributes") ||
      !all_finite(weights_buffer.span<float>(), "weights"))
  {
    return nullptr;
  }
  for (const float weight : weights_buffer.span<float>()) {
    if (weight < 0.0f) {
      PyErr_SetString(PyExc_ValueError, "weights must be at least 0");
      return nullptr;
    }
  }
  if (lock_buffer.held) {
    if (lock_buffer.count() != int64_t(vertex_count)) {
      PyErr_SetString(PyExc_ValueError, "lock must hold one value per vertex");
      return nullptr;
    }
    for (const uint8_t value : lock_buffer.span<uint8_t>()) {
      if (value > 1) { /* Only meshopt_SimplifyVertex_Lock: Protect needs the experimental Permissive. */
        PyErr_SetString(PyExc_ValueError, "lock values must be 0 or 1");
        return nullptr;
      }
    }
  }
  const angelo_meshopt::Api *api = require_meshopt();
  if (api == nullptr) {
    return nullptr;
  }

  std::vector<uint32_t> destination(index_count);
  std::vector<float3> new_positions;
  std::vector<float> new_attributes;
  const float *raw_positions = reinterpret_cast<const float *>(positions.data());
  const float *raw_attributes = attribute_count ? attributes_buffer.span<float>().data() : nullptr;
  const float *raw_weights = attribute_count ? weights_buffer.span<float>().data() : nullptr;
  const size_t attribute_stride = size_t(attribute_count) * sizeof(float);
  const unsigned char *raw_lock = lock_buffer.held ? lock_buffer.span<uint8_t>().data() : nullptr;
  float result_error = 0.0f;
  size_t result_count = 0;
  Py_BEGIN_ALLOW_THREADS;
  if (plain) {
    result_count = api->simplify(destination.data(),
                                 indices.data(),
                                 index_count,
                                 raw_positions,
                                 vertex_count,
                                 sizeof(float3),
                                 size_t(target_index_count),
                                 target_error,
                                 options,
                                 &result_error);
  }
  else if (attributes_mode) {
    result_count = api->simplify_with_attributes(destination.data(),
                                                 indices.data(),
                                                 index_count,
                                                 raw_positions,
                                                 vertex_count,
                                                 sizeof(float3),
                                                 raw_attributes,
                                                 attribute_stride,
                                                 raw_weights,
                                                 size_t(attribute_count),
                                                 raw_lock,
                                                 size_t(target_index_count),
                                                 target_error,
                                                 options,
                                                 &result_error);
  }
  else if (update) {
    /* Update moves vertices and attributes in place: work on copies, returned beside the indices. */
    std::copy(indices.begin(), indices.end(), destination.begin());
    new_positions.assign(positions.begin(), positions.end());
    if (attribute_count) {
      new_attributes.assign(raw_attributes, raw_attributes + attribute_count * int64_t(vertex_count));
    }
    result_count = api->simplify_with_update(destination.data(),
                                             index_count,
                                             reinterpret_cast<float *>(new_positions.data()),
                                             vertex_count,
                                             sizeof(float3),
                                             attribute_count ? new_attributes.data() : nullptr,
                                             attribute_stride,
                                             raw_weights,
                                             size_t(attribute_count),
                                             raw_lock,
                                             size_t(target_index_count),
                                             target_error,
                                             options,
                                             &result_error);
  }
  else {
    result_count = api->simplify_sloppy(destination.data(),
                                        indices.data(),
                                        index_count,
                                        raw_positions,
                                        vertex_count,
                                        sizeof(float3),
                                        raw_lock,
                                        size_t(target_index_count),
                                        target_error,
                                        &result_error);
  }
  Py_END_ALLOW_THREADS;

  char *data;
  PyObject *out_indices = new_bytes(int64_t(result_count), sizeof(uint32_t), &data);
  if (out_indices == nullptr) {
    return nullptr;
  }
  std::memcpy(data, destination.data(), result_count * sizeof(uint32_t));
  if (!update) {
    return Py_BuildValue("(Nf)", out_indices, result_error);
  }
  PyObject *out_positions = new_bytes(int64_t(vertex_count), sizeof(float3), &data);
  if (out_positions == nullptr) {
    Py_DECREF(out_indices);
    return nullptr;
  }
  std::memcpy(data, new_positions.data(), vertex_count * sizeof(float3));
  PyObject *out_attributes = new_bytes(int64_t(new_attributes.size()), sizeof(float), &data);
  if (out_attributes == nullptr) {
    Py_DECREF(out_indices);
    Py_DECREF(out_positions);
    return nullptr;
  }
  std::memcpy(data, new_attributes.data(), new_attributes.size() * sizeof(float));
  return Py_BuildValue("(NfNN)", out_indices, result_error, out_positions, out_attributes);
}

static PyObject *reduce_weld(PyObject * /*self*/, PyObject *args)
{
  Py_ssize_t count;
  PyObject *py_streams;
  if (!PyArg_ParseTuple(args, "nO:weld", &count, &py_streams)) {
    return nullptr;
  }
  PyObject *sequence = PySequence_Fast(py_streams, "streams must be a sequence of arrays");
  if (sequence == nullptr) {
    return nullptr;
  }
  const Py_ssize_t stream_count = PySequence_Fast_GET_SIZE(sequence);
  if (count <= 0 || count % 3 != 0 || stream_count < 1 || stream_count > MAX_STREAMS) {
    Py_DECREF(sequence);
    PyErr_SetString(PyExc_ValueError,
                    "weld needs a positive count of triangle corners (a multiple of 3) and 1 to 16 streams");
    return nullptr;
  }
  std::vector<std::unique_ptr<Buffer>> buffers;
  std::vector<meshopt_Stream> streams;
  for (Py_ssize_t i = 0; i < stream_count; i++) {
    buffers.push_back(std::make_unique<Buffer>());
    Buffer &buffer = *buffers.back();
    if (!get_buffer(PySequence_Fast_GET_ITEM(sequence, i), Kind::Any, "each stream", buffer)) {
      Py_DECREF(sequence);
      return nullptr;
    }
    const Py_ssize_t bytes = Py_ssize_t(buffer.bytes.size());
    if (bytes % count != 0 || bytes / count < 1 || bytes / count > 256) {
      Py_DECREF(sequence);
      PyErr_SetString(PyExc_ValueError, "each stream must hold count rows of 1 to 256 bytes");
      return nullptr;
    }
    const size_t row = size_t(bytes / count);
    streams.push_back({buffer.data(), row, row});
  }
  Py_DECREF(sequence);
  const angelo_meshopt::Api *api = require_meshopt();
  if (api == nullptr) {
    return nullptr;
  }
  char *data;
  PyObject *remap = new_bytes(int64_t(count), sizeof(uint32_t), &data);
  if (remap == nullptr) {
    return nullptr;
  }
  size_t unique = 0;
  Py_BEGIN_ALLOW_THREADS;
  unique = api->generate_vertex_remap_multi(
      reinterpret_cast<unsigned int *>(data), nullptr, size_t(count), size_t(count), streams.data(), streams.size());
  Py_END_ALLOW_THREADS;
  return Py_BuildValue("(Nn)", remap, Py_ssize_t(unique));
}

static PyObject *reduce_scale(PyObject * /*self*/, PyObject *py_positions)
{
  Buffer buffer;
  Span<float3> positions;
  if (!get_positions(py_positions, buffer, positions, "positions")) {
    return nullptr;
  }
  const angelo_meshopt::Api *api = require_meshopt();
  if (api == nullptr) {
    return nullptr;
  }
  return PyFloat_FromDouble(api->simplify_scale(
      reinterpret_cast<const float *>(positions.data()), size_t(positions.size()), sizeof(float3)));
}

#endif /* ANGELO_MESHOPT_SIMPLIFY */

namespace {

/** Triangles as int3s, each index below `vertex_count`. */
bool get_tris(PyObject *object, Buffer &buffer, const int64_t vertex_count, Span<int3> &r_tris)
{
  Span<uint32_t> indices;
  if (!get_indices(object, buffer, vertex_count, indices)) {
    return false;
  }
  if (vertex_count > int64_t(INT32_MAX)) {
    PyErr_SetString(PyExc_ValueError, "too many vertices");
    return false;
  }
  r_tris = Span<int3>(reinterpret_cast<const int3 *>(indices.data()), indices.size() / 3);
  return true;
}

void surface_free(PyObject *capsule)
{
  delete static_cast<angelo_reduce::Surface *>(PyCapsule_GetPointer(capsule, SURFACE_CAPSULE));
}

}  // namespace

static PyObject *reduce_surface(PyObject * /*self*/, PyObject *args)
{
  PyObject *py_positions, *py_tris;
  if (!PyArg_ParseTuple(args, "OO:surface", &py_positions, &py_tris)) {
    return nullptr;
  }
  Buffer positions_buffer, tris_buffer;
  Span<float3> positions;
  Span<int3> tris;
  if (!get_positions(py_positions, positions_buffer, positions, "positions") ||
      !get_tris(py_tris, tris_buffer, positions.size(), tris))
  {
    return nullptr;
  }
  angelo_reduce::Surface *surface = nullptr;
  Py_BEGIN_ALLOW_THREADS;
  surface = new angelo_reduce::Surface(positions, tris);
  Py_END_ALLOW_THREADS;
  PyObject *capsule = PyCapsule_New(surface, SURFACE_CAPSULE, surface_free);
  if (capsule == nullptr) {
    delete surface;
  }
  return capsule;
}

static PyObject *reduce_nearest(PyObject * /*self*/, PyObject *args)
{
  PyObject *py_surface, *py_points;
  if (!PyArg_ParseTuple(args, "OO:nearest", &py_surface, &py_points)) {
    return nullptr;
  }
  const auto *surface = static_cast<const angelo_reduce::Surface *>(
      PyCapsule_GetPointer(py_surface, SURFACE_CAPSULE));
  if (surface == nullptr) {
    return nullptr;
  }
  Buffer points_buffer;
  Span<float3> points;
  if (!get_positions(py_points, points_buffer, points, "points")) {
    return nullptr;
  }
  char *distance_data, *tri_data, *point_data;
  PyObject *distances = new_bytes(points.size(), sizeof(float), &distance_data);
  PyObject *tris = new_bytes(points.size(), sizeof(int), &tri_data);
  PyObject *closest = new_bytes(points.size(), sizeof(float3), &point_data);
  if (!distances || !tris || !closest) {
    Py_XDECREF(distances);
    Py_XDECREF(tris);
    Py_XDECREF(closest);
    return nullptr;
  }
  Py_BEGIN_ALLOW_THREADS;
  surface->nearest(points,
                   MutableSpan<float>(reinterpret_cast<float *>(distance_data), points.size()),
                   MutableSpan<int>(reinterpret_cast<int *>(tri_data), points.size()),
                   MutableSpan<float3>(reinterpret_cast<float3 *>(point_data), points.size()));
  Py_END_ALLOW_THREADS;
  return Py_BuildValue("(NNN)", distances, tris, closest);
}

static PyObject *reduce_rasterize(PyObject * /*self*/, PyObject *args)
{
  PyObject *py_positions, *py_tris, *py_views;
  int resolution;
  if (!PyArg_ParseTuple(args, "OOOi:rasterize", &py_positions, &py_tris, &py_views, &resolution)) {
    return nullptr;
  }
  Buffer positions_buffer, tris_buffer, views_buffer;
  Span<float3> positions;
  Span<int3> tris;
  if (!get_positions(py_positions, positions_buffer, positions, "positions") ||
      !get_tris(py_tris, tris_buffer, positions.size(), tris) ||
      !get_buffer(py_views, Kind::Float, "views", views_buffer))
  {
    return nullptr;
  }
  if (views_buffer.count() == 0 || views_buffer.count() % 8 != 0) {
    PyErr_SetString(PyExc_ValueError, "views must hold 8 floats per view (row_x, offset_x, row_y, offset_y)");
    return nullptr;
  }
  if (!all_finite(views_buffer.span<float>(), "views")) {
    return nullptr;
  }
  const int64_t view_count = views_buffer.count() / 8;
  if (resolution < 1 || resolution > MAX_RESOLUTION ||
      view_count * int64_t(resolution) * resolution > MAX_RASTER_BYTES)
  {
    PyErr_SetString(PyExc_ValueError, "resolution must be 1 to 4096 and the images at most 256 MB together");
    return nullptr;
  }
  const Span<float> raw = views_buffer.span<float>();
  Array<angelo_reduce::View> views(view_count);
  for (const int64_t v : views.index_range()) {
    const float *row = raw.data() + v * 8;
    views[v] = {float3(row[0], row[1], row[2]), row[3], float3(row[4], row[5], row[6]), row[7]};
  }
  char *data;
  const int64_t pixels = view_count * int64_t(resolution) * resolution;
  PyObject *counts = new_bytes(pixels, 1, &data);
  if (counts == nullptr) {
    return nullptr;
  }
  Py_BEGIN_ALLOW_THREADS;
  angelo_reduce::rasterize(
      positions, tris, views, resolution, MutableSpan<uint8_t>(reinterpret_cast<uint8_t *>(data), pixels));
  Py_END_ALLOW_THREADS;
  return counts;
}

/** Vertex group indices from a sequence (None for none); false with a Python error set. */
static bool get_groups(PyObject *py_groups, Vector<int> &r_groups)
{
  if (py_groups == Py_None) {
    return true;
  }
  PyObject *sequence = PySequence_Fast(py_groups, "groups must be a sequence of vertex group indices");
  if (sequence == nullptr) {
    return false;
  }
  for (Py_ssize_t i = 0; i < PySequence_Fast_GET_SIZE(sequence); i++) {
    const long group = PyLong_AsLong(PySequence_Fast_GET_ITEM(sequence, i));
    if (group < 0 || group > INT32_MAX) {
      Py_DECREF(sequence);
      if (!PyErr_Occurred()) {
        PyErr_SetString(PyExc_ValueError, "groups must be vertex group indices");
      }
      return false;
    }
    r_groups.append(int(group));
  }
  Py_DECREF(sequence);
  return true;
}

static PyObject *reduce_vertex_weights(PyObject * /*self*/, PyObject *args)
{
  PyObject *py_mesh, *py_groups;
  if (!PyArg_ParseTuple(args, "OO:vertex_weights", &py_mesh, &py_groups)) {
    return nullptr;
  }
  const Mesh *mesh = static_cast<const Mesh *>(PyC_RNA_AsPointer(py_mesh, "Mesh"));
  if (mesh == nullptr) {
    return nullptr;
  }
  Vector<int> groups;
  if (!get_groups(py_groups, groups)) {
    return nullptr;
  }
  const Array<float> weights = angelo_reduce::vertex_weights(*mesh, groups);
  char *data;
  PyObject *bytes = new_bytes(weights.size(), sizeof(float), &data);
  if (bytes != nullptr && !weights.is_empty()) {
    std::memcpy(data, weights.data(), size_t(weights.size()) * sizeof(float));
  }
  return bytes;
}

static PyObject *reduce_decimate(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  static const char *keywords[] = {"mesh", "ratio", "triangulate", "symmetry_axis", "groups", nullptr};
  PyObject *py_mesh, *py_groups = Py_None;
  float ratio;
  int triangulate = 1, symmetry_axis = -1;
  if (!PyArg_ParseTupleAndKeywords(args,
                                   kwargs,
                                   "Of|$piO:decimate",
                                   const_cast<char **>(keywords),
                                   &py_mesh,
                                   &ratio,
                                   &triangulate,
                                   &symmetry_axis,
                                   &py_groups))
  {
    return nullptr;
  }
  /* A snapshot is the caller's own (no other code can reach it), so Decimate runs on it without
   * the GIL; a Blender mesh is read on the main thread with the GIL held, as any bpy call is. */
  const bool own = PyCapsule_CheckExact(py_mesh);
  const Mesh *mesh = own ? static_cast<const Mesh *>(
                               PyCapsule_GetPointer(py_mesh, SNAPSHOT_CAPSULE)) :
                           static_cast<const Mesh *>(PyC_RNA_AsPointer(py_mesh, "Mesh"));
  if (mesh == nullptr) {
    return nullptr;
  }
  if (!(ratio > 0.0f && ratio <= 1.0f) || symmetry_axis < -1 || symmetry_axis > 2) {
    PyErr_SetString(PyExc_ValueError, "ratio must be above 0 and at most 1; symmetry_axis -1 or 0 to 2");
    return nullptr;
  }
  Vector<int> groups;
  if (!get_groups(py_groups, groups)) {
    return nullptr;
  }
  angelo_reduce::DecimateResult result;
  if (own) {
    Py_BEGIN_ALLOW_THREADS;
    result = angelo_reduce::decimate(*mesh, ratio, triangulate != 0, symmetry_axis, groups);
    Py_END_ALLOW_THREADS;
  }
  else {
    result = angelo_reduce::decimate(*mesh, ratio, triangulate != 0, symmetry_axis, groups);
  }
  auto to_bytes = [](const void *source, const int64_t count, const size_t size) {
    char *data;
    PyObject *bytes = new_bytes(count, size, &data);
    if (bytes != nullptr && count > 0) {
      std::memcpy(data, source, size_t(count) * size);
    }
    return bytes;
  };
  PyObject *positions = to_bytes(result.positions.data(), result.positions.size(), sizeof(float3));
  PyObject *tris = to_bytes(result.tris.data(), result.tris.size(), sizeof(int3));
  PyObject *normals = to_bytes(result.corner_normals.data(), result.corner_normals.size(), sizeof(float3));
  PyObject *uvs = result.corner_uvs.is_empty() ?
                      Py_NewRef(Py_None) :
                      to_bytes(result.corner_uvs.data(), result.corner_uvs.size(), sizeof(float2));
  PyObject *weights = groups.is_empty() ?
                          Py_NewRef(Py_None) :
                          to_bytes(result.weights.data(), result.weights.size(), sizeof(float));
  if (!positions || !tris || !normals || !uvs || !weights) {
    Py_XDECREF(positions);
    Py_XDECREF(tris);
    Py_XDECREF(normals);
    Py_XDECREF(uvs);
    Py_XDECREF(weights);
    return nullptr;
  }
  return Py_BuildValue("{s:N,s:N,s:N,s:N,s:N,s:s}",
                       "positions",
                       positions,
                       "tris",
                       tris,
                       "corner_uvs",
                       uvs,
                       "corner_normals",
                       normals,
                       "weights",
                       weights,
                       "uv_map",
                       result.uv_map.c_str());
}

static void snapshot_free(PyObject *capsule)
{
  angelo_reduce::snapshot_free(
      static_cast<Mesh *>(PyCapsule_GetPointer(capsule, SNAPSHOT_CAPSULE)));
}

static PyObject *reduce_snapshot(PyObject * /*self*/, PyObject *py_mesh)
{
  const Mesh *mesh = static_cast<const Mesh *>(PyC_RNA_AsPointer(py_mesh, "Mesh"));
  if (mesh == nullptr) {
    return nullptr;
  }
  Mesh *copy = angelo_reduce::snapshot(*mesh);
  PyObject *capsule = PyCapsule_New(copy, SNAPSHOT_CAPSULE, snapshot_free);
  if (capsule == nullptr) {
    angelo_reduce::snapshot_free(copy);
  }
  return capsule;
}

#ifdef WITH_BULLET
static PyObject *reduce_convex_hull(PyObject * /*self*/, PyObject *py_points)
{
  Buffer points_buffer;
  Span<float3> points;
  if (!get_positions(py_points, points_buffer, points, "points")) {
    return nullptr;
  }
  Vector<float3> positions;
  Vector<int3> tris;
  bool made;
  Py_BEGIN_ALLOW_THREADS;
  made = angelo_reduce::convex_hull(points, positions, tris);
  Py_END_ALLOW_THREADS;
  if (!made) {
    Py_RETURN_NONE;
  }
  char *data;
  PyObject *out_positions = new_bytes(positions.size(), sizeof(float3), &data);
  if (out_positions != nullptr) {
    std::memcpy(data, positions.data(), size_t(positions.size()) * sizeof(float3));
  }
  PyObject *out_tris = new_bytes(tris.size(), sizeof(int3), &data);
  if (out_tris != nullptr) {
    std::memcpy(data, tris.data(), size_t(tris.size()) * sizeof(int3));
  }
  if (out_positions == nullptr || out_tris == nullptr) {
    Py_XDECREF(out_positions);
    Py_XDECREF(out_tris);
    return nullptr;
  }
  return Py_BuildValue("(NN)", out_positions, out_tris);
}
#endif

static PyMethodDef methods[] = {
    {"status",
     reduce_status,
     METH_NOARGS,
     "status(): {available, reason, library_path, header_version}; opens the meshoptimizer library on first "
     "use and checks it (bpy_angelo_meshopt.hh)."},
    {"attempted",
     reduce_attempted,
     METH_NOARGS,
     "attempted(): whether anything has opened, or tried to open, the meshoptimizer library yet."},
#ifdef ANGELO_MESHOPT_SIMPLIFY
    {"simplify",
     (PyCFunction)(void *)reduce_simplify,
     METH_VARARGS | METH_KEYWORDS,
     "simplify(positions, indices, target_index_count, target_error, *, mode='ATTRIBUTES', attributes=None, "
     "weights=None, lock=None, options=0): meshoptimizer's simplifier. Returns (indices, error), the indices "
     "over the same vertices; mode UPDATE also returns the moved positions and attributes. Modes: PLAIN, "
     "ATTRIBUTES (attribute weights, vertex locks), UPDATE, SLOPPY. options: OPTION_* flags."},
    {"weld",
     reduce_weld,
     METH_VARARGS,
     "weld(count, streams): (remap, unique) from meshoptimizer's vertex remap over count rows of every "
     "stream; rows equal in every stream share one vertex."},
    {"scale",
     reduce_scale,
     METH_O,
     "scale(positions): the mesh extent meshoptimizer's relative errors are measured against."},
#endif
    {"surface",
     reduce_surface,
     METH_VARARGS,
     "surface(positions, tris): a triangle surface and its tree, for nearest()."},
    {"nearest",
     reduce_nearest,
     METH_VARARGS,
     "nearest(surface, points): (distances float32, triangles int32, closest points float32) for each "
     "point, computed on all cores."},
    {"rasterize",
     reduce_rasterize,
     METH_VARARGS,
     "rasterize(positions, tris, views, resolution): for each orthographic view (8 floats: row_x, offset_x, "
     "row_y, offset_y), how many triangles cover each pixel centre (uint8, saturating)."},
    {"vertex_weights",
     reduce_vertex_weights,
     METH_VARARGS,
     "vertex_weights(mesh, groups): float32 weights of each listed vertex group for every vertex, row major."},
    {"decimate",
     (PyCFunction)(void *)reduce_decimate,
     METH_VARARGS | METH_KEYWORDS,
     "decimate(mesh, ratio, *, triangulate=True, symmetry_axis=-1, groups=None): Blender's Decimate "
     "(Collapse) on a copy of the mesh, as arrays (positions, tris, corner_uvs, corner_normals, "
     "weights). mesh is a Blender mesh (main thread) or a snapshot (any thread, no GIL)."},
    {"snapshot",
     reduce_snapshot,
     METH_O,
     "snapshot(mesh): a copy of a Blender mesh in no Main (main thread), its arrays shared with "
     "the mesh, for decimate() on any thread; freed with the returned object."},
#ifdef WITH_BULLET
    {"convex_hull",
     reduce_convex_hull,
     METH_O,
     "convex_hull(points): (positions float32, tris int32) of the convex hull of float32 points "
     "from Blender's convex_hull operator (Bullet), without the GIL; None for no hull."},
#endif
    {nullptr, nullptr, 0, nullptr},
};

static PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    "_bpy._angelo_reduce",
    nullptr,
    0,
    methods,
    nullptr,
    nullptr,
    nullptr,
    nullptr,
};

PyObject *BPY_angelo_reduce_module()
{
  PyObject *mod = PyModule_Create(&module);
  if (mod == nullptr) {
    return nullptr;
  }
  PyModule_AddIntConstant(mod, "OPTION_LOCK_BORDER", OPTION_LOCK_BORDER);
  PyModule_AddIntConstant(mod, "OPTION_SPARSE", OPTION_SPARSE);
  PyModule_AddIntConstant(mod, "OPTION_ERROR_ABSOLUTE", OPTION_ERROR_ABSOLUTE);
  PyModule_AddIntConstant(mod, "OPTION_PRUNE", OPTION_PRUNE);
  PyModule_AddIntConstant(mod, "OPTION_REGULARIZE", OPTION_REGULARIZE);
  PyModule_AddIntConstant(mod, "MAX_ATTRIBUTES", MAX_ATTRIBUTES);
  return mod;
}

}  // namespace blender
