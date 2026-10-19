/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Python admission and value conversion for the descriptor-only metadata scanner. The caller
 * owns workspace admission, descriptor lifetime/identity checks and its StudioPending request.
 * No live RNA, Main, paths, Python callbacks or account state enter the native scan.
 */

#include <Python.h>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <new>
#include <string>

#include "MEM_guardedalloc.h"

#include "BLO_metadata_scan.hh"

#include "bpy_angelo_metadata.hh"

namespace blender {
namespace {

using ScanClock = std::chrono::steady_clock;
static constexpr const char *cancel_capsule_name = "angelo.metadata_cancel_token";

struct MetadataCancelToken {
  std::atomic<bool> cancelled{false};
  std::atomic<bool> timed_out{false};
  /** Polls since the clock was last read (#scan_cancelled). */
  std::atomic<uint32_t> polls{0};
  const ScanClock::time_point deadline;

  explicit MetadataCancelToken(const ScanClock::time_point deadline) : deadline(deadline) {}
};

/** Stop, or the deadline, now. All accesses here are native and need no GIL. */
static bool scan_stopped_now(MetadataCancelToken &token)
{
  if (token.cancelled.load(std::memory_order_relaxed)) {
    return true;
  }
  if (ScanClock::now() >= token.deadline) {
    token.timed_out.store(true, std::memory_order_relaxed);
    return true;
  }
  return false;
}

/**
 * The scanner's poll, at every block and read (with Python's GIL released). Stop is seen at the
 * next poll; the clock is read every 32nd poll, since reading it at every block cost a sixth of a
 * plain scan. Between two clock reads the scanner decodes at most 32 chunks of 64 KiB, so the
 * deadline is overrun by milliseconds at most.
 */
static bool scan_cancelled(void *userdata)
{
  MetadataCancelToken &token = *static_cast<MetadataCancelToken *>(userdata);
  if (token.cancelled.load(std::memory_order_relaxed)) {
    return true;
  }
  if (token.polls.fetch_add(1, std::memory_order_relaxed) % 32 != 0) {
    return false;
  }
  return scan_stopped_now(token);
}

static void cancel_token_free(PyObject *capsule)
{
  if (PyCapsule_IsValid(capsule, cancel_capsule_name)) {
    MEM_delete(
        static_cast<MetadataCancelToken *>(PyCapsule_GetPointer(capsule, cancel_capsule_name)));
  }
}

static PyObject *metadata_cancel_token(PyObject * /*self*/, PyObject *seconds_object)
{
  if (!PyFloat_CheckExact(seconds_object) && !PyLong_CheckExact(seconds_object)) {
    PyErr_SetString(PyExc_TypeError, "Metadata timeout must be a number, not bool");
    return nullptr;
  }
  const double seconds = PyFloat_AsDouble(seconds_object);
  if (PyErr_Occurred()) {
    return nullptr;
  }
  if (!std::isfinite(seconds) || seconds <= 0.0 || seconds > 300.0) {
    PyErr_SetString(PyExc_ValueError,
                    "Metadata timeout must be finite and within (0, 300] seconds");
    return nullptr;
  }
  const auto duration = std::chrono::duration_cast<ScanClock::duration>(
      std::chrono::duration<double>(seconds));
  MetadataCancelToken *token = MEM_new<MetadataCancelToken>(__func__, ScanClock::now() + duration);
  PyObject *capsule = PyCapsule_New(token, cancel_capsule_name, cancel_token_free);
  if (capsule == nullptr) {
    MEM_delete(token);
  }
  return capsule;
}

static PyObject *metadata_cancel(PyObject * /*self*/, PyObject *capsule)
{
  MetadataCancelToken *token = static_cast<MetadataCancelToken *>(
      PyCapsule_GetPointer(capsule, cancel_capsule_name));
  if (token == nullptr) {
    return nullptr;
  }
  token->cancelled.store(true, std::memory_order_relaxed);
  Py_RETURN_NONE;
}

template<size_t Size> static bool exact_fields(PyObject *object, const char *const (&fields)[Size])
{
  if (!PyDict_CheckExact(object)) {
    PyErr_SetString(PyExc_TypeError, "Metadata request and limits must be plain dictionaries");
    return false;
  }
  if (PyDict_Size(object) != Py_ssize_t(Size)) {
    PyErr_SetString(PyExc_ValueError, "Metadata request or limits has missing or extra fields");
    return false;
  }
  PyObject *key, *value;
  Py_ssize_t position = 0;
  while (PyDict_Next(object, &position, &key, &value)) {
    if (!PyUnicode_CheckExact(key)) {
      PyErr_SetString(PyExc_TypeError, "Metadata field names must be plain strings");
      return false;
    }
    if (PyUnicode_GET_LENGTH(key) > 32) {
      PyErr_SetString(PyExc_ValueError, "Unknown metadata request or limit field");
      return false;
    }
    Py_ssize_t length;
    const char *name = PyUnicode_AsUTF8AndSize(key, &length);
    if (name == nullptr) {
      return false;
    }
    bool found = false;
    for (const char *field : fields) {
      if (size_t(length) == strlen(field) && memcmp(name, field, length) == 0) {
        found = true;
        break;
      }
    }
    if (!found) {
      PyErr_SetString(PyExc_ValueError, "Unknown metadata request or limit field");
      return false;
    }
  }
  return true;
}

template<typename T> static bool integer_value(PyObject *object, T &value, bool positive)
{
  if (object == nullptr || !PyLong_CheckExact(object)) {
    PyErr_SetString(PyExc_TypeError, "Metadata integer fields must be plain integers, not bool");
    return false;
  }
  const unsigned long long number = PyLong_AsUnsignedLongLong(object);
  if (PyErr_Occurred()) {
    return false;
  }
  if ((positive && number == 0) || number > std::numeric_limits<T>::max()) {
    PyErr_SetString(PyExc_ValueError, "Metadata integer field is outside its supported range");
    return false;
  }
  value = T(number);
  return true;
}

template<typename T>
static bool integer_field(PyObject *dictionary, const char *field, T &value, bool positive = true)
{
  return integer_value(PyDict_GetItemString(dictionary, field), value, positive);
}

static bool parse_request(PyObject *object, BLOMetadataScanRequest &request)
{
  static const char *const fields[] = {
      "list", "offset", "limit", "summary_names", "libraries_limit"};
  if (!exact_fields(object, fields)) {
    return false;
  }
  PyObject *list = PyDict_GetItemString(object, "list");
  if (!PyUnicode_CheckExact(list)) {
    PyErr_SetString(PyExc_TypeError, "Metadata list must be a plain string");
    return false;
  }
  if (PyUnicode_GET_LENGTH(list) > 128) {
    PyErr_SetString(PyExc_ValueError, "Metadata list identifier is too long");
    return false;
  }
  Py_ssize_t length;
  const char *name = PyUnicode_AsUTF8AndSize(list, &length);
  if (name == nullptr) {
    return false;
  }
  /* List identifiers are short native type names, never filenames or arbitrary expressions. */
  if (length > 128 || memchr(name, '\0', length) != nullptr) {
    PyErr_SetString(PyExc_ValueError, "Metadata list identifier is too long or contains NUL");
    return false;
  }
  request.list.assign(name, size_t(length));
  return integer_field(object, "offset", request.offset, false) &&
         integer_field(object, "limit", request.limit) &&
         integer_field(object, "summary_names", request.summary_names) &&
         integer_field(object, "libraries_limit", request.libraries_limit);
}

static bool parse_limits(PyObject *object, BLOMetadataScanLimits &limits)
{
  static const char *const fields[] = {"decoded_bytes",
                                       "input_bytes",
                                       "block_count",
                                       "block_bytes",
                                       "dna_bytes",
                                       "dna_entries",
                                       "decoder_bytes",
                                       "zstd_window_bytes",
                                       "result_names"};
  if (!exact_fields(object, fields)) {
    return false;
  }
  return integer_field(object, "decoded_bytes", limits.decoded_bytes) &&
         integer_field(object, "input_bytes", limits.input_bytes) &&
         integer_field(object, "block_count", limits.block_count) &&
         integer_field(object, "block_bytes", limits.block_bytes) &&
         integer_field(object, "dna_bytes", limits.dna_bytes) &&
         integer_field(object, "dna_entries", limits.dna_entries) &&
         integer_field(object, "decoder_bytes", limits.decoder_bytes) &&
         integer_field(object, "zstd_window_bytes", limits.zstd_window_bytes) &&
         integer_field(object, "result_names", limits.result_names);
}

/** Restores Python's thread state on normal return and C++ exception unwinding. */
class ScopedReleaseGIL {
  PyThreadState *thread_state_;

 public:
  ScopedReleaseGIL() : thread_state_(PyEval_SaveThread()) {}
  ~ScopedReleaseGIL()
  {
    PyEval_RestoreThread(thread_state_);
  }
  ScopedReleaseGIL(const ScopedReleaseGIL &) = delete;
  ScopedReleaseGIL &operator=(const ScopedReleaseGIL &) = delete;
};

static const char *status_name(const BLOMetadataScanResult &result,
                               const MetadataCancelToken &token)
{
  switch (result.status) {
    case BLOMetadataScanStatus::OK:
      return "OK";
    case BLOMetadataScanStatus::INVALID:
      return "INVALID";
    case BLOMetadataScanStatus::LIMIT_EXCEEDED:
      return "LIMIT_EXCEEDED";
    case BLOMetadataScanStatus::CANCELLED:
      return token.timed_out.load(std::memory_order_relaxed) ? "TIME_LIMIT" : "CANCELLED";
    case BLOMetadataScanStatus::IO_ERROR:
      return "IO_ERROR";
  }
  return "INVALID";
}

static PyObject *strings_to_list(const std::vector<std::string> &strings)
{
  if (strings.size() > size_t(PY_SSIZE_T_MAX)) {
    return PyErr_NoMemory();
  }
  PyObject *list = PyList_New(Py_ssize_t(strings.size()));
  if (list == nullptr) {
    return nullptr;
  }
  for (size_t index = 0; index < strings.size(); index++) {
    const std::string &string = strings[index];
    /* Invalid name/path bytes cannot enter a UTF-8 model response. The host reports malformed
     * text as an unreadable file, as the existing library loader does, without echoing its bytes. */
    PyObject *value = PyUnicode_DecodeUTF8(
        string.data(), Py_ssize_t(string.size()), "strict");
    if (value == nullptr) {
      Py_DECREF(list);
      return nullptr;
    }
    PyList_SET_ITEM(list, Py_ssize_t(index), value);
  }
  return list;
}

static PyObject *result_to_python(const BLOMetadataScanResult &result,
                                  const MetadataCancelToken &token)
{
  PyObject *types = PyDict_New();
  if (types == nullptr) {
    return nullptr;
  }
  if (result.status == BLOMetadataScanStatus::OK) {
    for (const BLOMetadataScanType &type : result.types) {
      PyObject *names = strings_to_list(type.names);
      if (names == nullptr) {
        Py_DECREF(types);
        return nullptr;
      }
      PyObject *row = Py_BuildValue(
          "{s:K,s:N}", "total", static_cast<unsigned long long>(type.total), "names", names);
      if (row == nullptr) {
        Py_DECREF(types);
        return nullptr;
      }
      const int assigned = PyDict_SetItemString(types, type.list_name.c_str(), row);
      Py_DECREF(row);
      if (assigned < 0) {
        Py_DECREF(types);
        return nullptr;
      }
    }
  }
  PyObject *libraries = result.status == BLOMetadataScanStatus::OK ?
                            strings_to_list(result.library_paths) :
                            PyList_New(0);
  if (libraries == nullptr) {
    Py_DECREF(types);
    return nullptr;
  }
  const char *status = status_name(result, token);
  const char *message = strcmp(status, "TIME_LIMIT") == 0 ? "Metadata scan time limit exceeded" :
                                                            result.message.c_str();
  PyObject *output = Py_BuildValue("{s:s,s:s,s:N,s:N,s:K,s:K,s:K,s:I}",
                                   "status",
                                   status,
                                   "message",
                                   message,
                                   "types",
                                   types,
                                   "libraries",
                                   libraries,
                                   "decoded_bytes",
                                   static_cast<unsigned long long>(result.decoded_bytes),
                                   "input_bytes",
                                   static_cast<unsigned long long>(result.input_bytes),
                                   "blocks",
                                   static_cast<unsigned long long>(result.blocks),
                                   "passes",
                                   static_cast<unsigned int>(result.passes));
  if (output && result.status == BLOMetadataScanStatus::OK) {
    PyObject *version = Py_BuildValue(
        "(iii)", result.file_version / 100, result.file_version % 100, result.subversion);
    if (version == nullptr || PyDict_SetItemString(output, "version", version) < 0) {
      Py_XDECREF(version);
      Py_DECREF(output);
      return nullptr;
    }
    Py_DECREF(version);
  }
  return output;
}

static PyObject *metadata_scan(PyObject * /*self*/, PyObject *args)
{
  PyObject *descriptor_object, *request_object, *limits_object, *capsule;
  if (!PyArg_ParseTuple(args,
                        "OOOO:metadata_scan",
                        &descriptor_object,
                        &request_object,
                        &limits_object,
                        &capsule))
  {
    return nullptr;
  }
  MetadataCancelToken *token = static_cast<MetadataCancelToken *>(
      PyCapsule_GetPointer(capsule, cancel_capsule_name));
  if (token == nullptr) {
    return nullptr;
  }
  try {
    int descriptor;
    BLOMetadataScanRequest request;
    BLOMetadataScanLimits limits;
    if (!integer_value(descriptor_object, descriptor, false) ||
        !parse_request(request_object, request) || !parse_limits(limits_object, limits))
    {
      return nullptr;
    }
    BLOMetadataScanResult result;
    {
      /* The call's args tuple strongly owns the capsule for this entire scope, including
       * exception unwinding. The private descriptor is borrowed, never closed or reopened.
       * All Python parsing is finished; only copied C++ values and atomics enter the scanner. */
      ScopedReleaseGIL release_gil;
      result = BLO_metadata_scan_from_descriptor(
          descriptor, request, limits, scan_cancelled, token);
    }
    /* Stop/deadline may arrive after the scanner's final check while this worker waits for
     * the GIL. Retain measured counters, but never publish that late success as metadata. */
    if (result.status == BLOMetadataScanStatus::OK && scan_stopped_now(*token)) {
      result.status = BLOMetadataScanStatus::CANCELLED;
      result.message = "Metadata scan cancelled";
      result.file_version = 0;
      result.subversion = 0;
      result.types.clear();
      result.library_paths.clear();
    }
    return result_to_python(result, *token);
  }
  catch (const std::bad_alloc &) {
    return PyErr_NoMemory();
  }
  catch (...) {
    /* A diagnostic from an exception could contain file bytes or paths. */
    PyErr_SetString(PyExc_RuntimeError, "Native metadata scan failed");
    return nullptr;
  }
}

static PyMethodDef methods[] = {
    {"metadata_cancel_token",
     metadata_cancel_token,
     METH_O,
     "metadata_cancel_token(seconds): create an atomic cancellation token with a bounded "
     "deadline."},
    {"metadata_cancel",
     metadata_cancel,
     METH_O,
     "metadata_cancel(token): request cancellation; does not close any descriptor."},
    {"metadata_scan",
     metadata_scan,
     METH_VARARGS,
     "metadata_scan(fd, request, limits, token): scan a caller-owned descriptor with the GIL "
     "released."},
    {nullptr, nullptr, 0, nullptr},
};

}  // namespace

PyMethodDef *BPY_angelo_metadata_methods()
{
  return methods;
}

}  // namespace blender
