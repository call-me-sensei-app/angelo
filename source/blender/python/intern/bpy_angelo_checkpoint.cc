/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
/** Private synchronous checkpoint capability. A Python dictionary never certifies bytes. */
#include <Python.h>
#include <array>
#include <climits>
#include <cstring>
#include <memory>
#include <limits>
#include <string>
#include <utility>
#include <sys/stat.h>
#ifdef WIN32
#  include <io.h>
#  include <windows.h>
#else
#  include <unistd.h>
#endif
#include "MEM_guardedalloc.h"
#include "BLI_threads.h"
#include "BLI_memory_utils.hh"
#include "BLI_vector.hh"
#include "BLI_fileops.h"
#include "BKE_context.hh"
#include "BKE_callbacks.hh"
#include "BKE_global.hh"
#include "BKE_report.hh"
#include "BKE_main.hh"
#include "BLO_readfile.hh"
#include "DNA_windowmanager_types.h"
#include "WM_api.hh"
#include "bpy_capi_utils.hh"
#include "bpy_app_handlers.hh"
#include "bpy_angelo_checkpoint.hh"
#include "../../windowmanager/intern/wm_checkpoint.hh"

namespace blender {
namespace {
constexpr const char *capture_name = "angelo.native_checkpoint_capture";
struct FileStamp {
  uint64_t device = 0;
  std::array<uint8_t, 16> inode{};
  uint64_t bytes = 0, modified = 0, changed = 0;
  uint32_t modified_ns = 0, changed_ns = 0;
  bool operator==(const FileStamp &other) const = default;
};
static bool file_stamp(const int fd, FileStamp &stamp)
{
#ifdef WIN32
  const HANDLE handle = reinterpret_cast<HANDLE>(_get_osfhandle(fd));
  FILE_ID_INFO identity{};
  FILE_STANDARD_INFO standard{};
  FILE_BASIC_INFO basic{};
  if (handle == INVALID_HANDLE_VALUE ||
      !GetFileInformationByHandleEx(handle, FileIdInfo, &identity, sizeof(identity)) ||
      !GetFileInformationByHandleEx(handle, FileStandardInfo, &standard, sizeof(standard)) ||
      !GetFileInformationByHandleEx(handle, FileBasicInfo, &basic, sizeof(basic)) ||
      standard.Directory || standard.NumberOfLinks != 1 || standard.EndOfFile.QuadPart < 0)
  {
    return false;
  }
  stamp.device = identity.VolumeSerialNumber;
  std::memcpy(stamp.inode.data(), identity.FileId.Identifier, stamp.inode.size());
  stamp.bytes = uint64_t(standard.EndOfFile.QuadPart);
  stamp.modified = uint64_t(basic.LastWriteTime.QuadPart);
  stamp.changed = uint64_t(basic.ChangeTime.QuadPart);
#else
  struct stat info;
  if (fstat(fd, &info) != 0 || !S_ISREG(info.st_mode) || info.st_nlink != 1 || info.st_size < 0) {
    return false;
  }
  stamp.device = uint64_t(info.st_dev);
  const uint64_t inode = uint64_t(info.st_ino);
  std::memcpy(stamp.inode.data(), &inode, sizeof(inode));
  stamp.bytes = uint64_t(info.st_size);
#  ifdef __APPLE__
  stamp.modified = uint64_t(info.st_mtimespec.tv_sec);
  stamp.modified_ns = info.st_mtimespec.tv_nsec;
  stamp.changed = uint64_t(info.st_ctimespec.tv_sec);
  stamp.changed_ns = info.st_ctimespec.tv_nsec;
#  else
  stamp.modified = uint64_t(info.st_mtim.tv_sec);
  stamp.modified_ns = info.st_mtim.tv_nsec;
  stamp.changed = uint64_t(info.st_ctim.tv_sec);
  stamp.changed_ns = info.st_ctim.tv_nsec;
#  endif
#endif
  return true;
}
static bool same_identity(const FileStamp &a, const FileStamp &b)
{
  return a.device == b.device && a.inode == b.inode;
}
struct NativeCapture {
  WMCheckpointCapture *capture = nullptr;
  Main *source_main = nullptr;
  PyObject *owner = nullptr;
  std::string capture_id;
  int base = -1, runtime = -1;
  FileStamp base_stamp, runtime_stamp;
  bool complete = false, post_attempted = false, post_failed = false, active = false;
  bool terminal_safety = false;
  bool safety_consumed = false;
  bool resources_released = false;
  bool consuming = false;
  WMCheckpointContent *content = nullptr;
};
/** One synchronous main-thread consumer owns every borrowed native pointer until callbacks and
 * their witnesses unwind. A strong reference also prevents capsule destruction during dispatch. */
class NativeCapsuleLease {
  bool *consuming_ = nullptr;
  PyObject *capsule_ = nullptr;
 public:
  NativeCapsuleLease(PyObject *capsule, bool &consuming)
  {
    if (consuming) {
      PyErr_SetString(PyExc_RuntimeError, "The native checkpoint capsule is currently in use");
      return;
    }
    consuming = true;
    consuming_ = &consuming;
    capsule_ = Py_NewRef(capsule);
  }
  NativeCapsuleLease(const NativeCapsuleLease &) = delete;
  NativeCapsuleLease &operator=(const NativeCapsuleLease &) = delete;
  ~NativeCapsuleLease()
  {
    if (consuming_) {
      *consuming_ = false;
      Py_DECREF(capsule_);
    }
  }
  explicit operator bool() const { return consuming_ != nullptr; }
};
/** Raise ``summary`` with the native refusal reasons ``reports`` collected, while the scoped
 * list still lives. Each reason is one recorded refusal; nothing is inferred from its text. */
static void raise_with_reports(PyObject *type, const char *summary, ReportList &reports,
                               const char *detail = nullptr)
{
  char *native = BKE_reports_string(&reports, RPT_ERROR);
  std::string reasons;
  for (const char *c = native; c && *c; c++) {
    if (*c == '\n') {
      if (c[1]) { reasons += "; "; }
    }
    else {
      reasons += *c;
    }
  }
  if (native) { MEM_delete(native); }
  std::string message = summary;
  if (detail && detail[0]) { message += std::string(": ") + detail; }
  if (!reasons.empty()) { message += " (" + reasons + ")"; }
  PyErr_SetString(type, message.c_str());
}
static bool require_main_thread()
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Checkpoint capture requires Blender's main thread");
    return false;
  }
  return true;
}
static bool exact_owner(PyObject *owner)
{
  PyObject *module = PyImport_ImportModule("angelo_agent.checkpoint_capture");
  if (!module) {
    return false;
  }
  PyObject *type = PyObject_GetAttrString(module, "CaptureOwner");
  Py_DECREF(module);
  if (!type) {
    return false;
  }
  const bool exact = reinterpret_cast<PyObject *>(Py_TYPE(owner)) == type;
  Py_DECREF(type);
  if (!exact) {
    PyErr_SetString(PyExc_TypeError, "An exact live checkpoint CaptureOwner is required");
  }
  return exact;
}
static bool parse_capture_id(const char *text, ed::sculpt_paint::checkpoint::CaptureID &id)
{
  if (std::strlen(text) != 36) {
    return false;
  }
  int nibble = 0;
  for (int i = 0; i < 36; i++) {
    if (i == 8 || i == 13 || i == 18 || i == 23) {
      if (text[i] != '-') {
        return false;
      }
      continue;
    }
    const int value = text[i] >= '0' && text[i] <= '9' ? text[i] - '0' :
                      text[i] >= 'a' && text[i] <= 'f' ? text[i] - 'a' + 10 : -1;
    if (value < 0) {
      return false;
    }
    if (nibble % 2 == 0) {
      id[nibble / 2] = uint8_t(value << 4);
    }
    else {
      id[nibble / 2] |= uint8_t(value);
    }
    nibble++;
  }
  return nibble == 32;
}
static void release(NativeCapture &capture)
{
  PyObject *prior_type, *prior_value, *prior_traceback;
  PyErr_Fetch(&prior_type, &prior_value, &prior_traceback);
  WM_checkpoint_capture_close(capture.capture);
  capture.capture = nullptr;
  if (capture.active) {
    capture.active = false;
    PyObject *result = PyObject_CallMethod(capture.owner, "_native_end", nullptr);
    Py_XDECREF(result);
  }
  if (prior_type) {
    if (PyErr_Occurred()) {
      PyObject *cleanup_type, *cleanup_value, *cleanup_traceback;
      PyErr_Fetch(&cleanup_type, &cleanup_value, &cleanup_traceback);
      PyErr_NormalizeException(&cleanup_type, &cleanup_value, &cleanup_traceback);
      PyErr_NormalizeException(&prior_type, &prior_value, &prior_traceback);
      PyException_SetContext(cleanup_value, prior_value);
      Py_XDECREF(prior_type);
      Py_XDECREF(prior_traceback);
      PyErr_Restore(cleanup_type, cleanup_value, cleanup_traceback);
    }
    else {
      PyErr_Restore(prior_type, prior_value, prior_traceback);
    }
  }
}
static void capsule_free(PyObject *capsule)
{
  auto *capture = static_cast<NativeCapture *>(PyCapsule_GetPointer(capsule, capture_name));
  if (!capture) {
    PyErr_Clear();
    return;
  }
  PyObject *type, *value, *traceback;
  PyErr_Fetch(&type, &value, &traceback);
  release(*capture);
  WM_checkpoint_content_free(capture->content);
  capture->content = nullptr;
  if (!capture->resources_released) {
    capture->resources_released = true;
    PyObject *result = PyObject_CallMethod(capture->owner, "abandon", nullptr);
    Py_XDECREF(result);
  }
  if (PyErr_Occurred()) {
    PyErr_WriteUnraisable(capsule);
  }
  Py_DECREF(capture->owner);
  delete capture;
  PyErr_Restore(type, value, traceback);
}
static NativeCapture *admit(PyObject *capsule, PyObject *owner, bool require_complete)
{
  if (!require_main_thread() || !PyCapsule_CheckExact(capsule)) {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_TypeError, "An opaque native capture result is required");
    }
    return nullptr;
  }
  auto *capture = static_cast<NativeCapture *>(PyCapsule_GetPointer(capsule, capture_name));
  if (!capture) {
    return nullptr;
  }
  if (capture->owner != owner || !capture->active || !capture->capture ||
      !WM_checkpoint_capture_validate(*capture->capture) ||
      (require_complete && !capture->complete))
  {
    PyErr_SetString(PyExc_ValueError, "The native capture result has no matching live owner");
    return nullptr;
  }
  FileStamp base, runtime;
  if (!file_stamp(capture->base, base) || !same_identity(base, capture->base_stamp) ||
      base.bytes != capture->base_stamp.bytes || base.modified != capture->base_stamp.modified ||
      base.modified_ns != capture->base_stamp.modified_ns ||
      (capture->runtime >= 0 &&
       (!file_stamp(capture->runtime, runtime) || !same_identity(runtime, capture->runtime_stamp) ||
        runtime.bytes != capture->runtime_stamp.bytes ||
        runtime.modified != capture->runtime_stamp.modified ||
        runtime.modified_ns != capture->runtime_stamp.modified_ns)))
  {
    /* Link-based no-overwrite publication changes ctime, but never content mtime/size/identity. */
    PyErr_SetString(PyExc_ValueError, "The retained native checkpoint output changed");
    return nullptr;
  }
  return capture;
}
static bool borrow_output(NativeCapture &capture, const char *role, int &fd, FileStamp &stamp)
{
  PyObject *result = PyObject_CallMethod(capture.owner, "_native_output", "s", role);
  if (!result) {
    return false;
  }
  if (!PyLong_CheckExact(result)) {
    Py_DECREF(result);
    PyErr_SetString(PyExc_TypeError, "The capture owner did not lend an exact descriptor");
    return false;
  }
  const long descriptor = PyLong_AsLong(result);
  Py_DECREF(result);
  if (PyErr_Occurred() || descriptor < 0 || descriptor > INT_MAX) {
    return false;
  }
  fd = int(descriptor);
  if (!file_stamp(fd, stamp) || stamp.bytes != 0) {
    PyErr_SetString(PyExc_ValueError, "The native output is not a fresh owned regular file");
    return false;
  }
  return true;
}
static PyObject *capture_call(PyObject *, PyObject *owner)
{
  if (!require_main_thread() || !exact_owner(owner)) {
    return nullptr;
  }
  auto capture = std::make_unique<NativeCapture>();
  capture->owner = Py_NewRef(owner);
  PyObject *binding = PyObject_CallMethod(owner, "_native_begin", nullptr);
  if (!binding) {
    Py_DECREF(capture->owner);
    return nullptr;
  }
  capture->active = true;
  const char *capture_id, *logical_path;
  ed::sculpt_paint::checkpoint::CaptureID id;
  bool success = PyArg_ParseTuple(binding, "ss", &capture_id, &logical_path) &&
                 parse_capture_id(capture_id, id);
  if (success) {
    capture->capture_id = capture_id;
    capture->capture = WM_checkpoint_capture_begin(BPY_context_get(), id);
    if (capture->capture) {
      capture->source_main = CTX_data_main(BPY_context_get());
    }
    success = capture->capture && borrow_output(*capture, "BASE", capture->base, capture->base_stamp);
  }
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BLI_SCOPED_DEFER([&]() { BKE_reports_free(&reports); });
  /* An add-on handler that raises is reported (printed) like an ordinary save, never a failure. */
  FileStamp encoded_base, encoded_runtime;
  const char *failure_stage = "native owner admission";
  if (success) {
    failure_stage = "BASE capture/serialization";
    success = WM_checkpoint_capture_write(*capture->capture, logical_path, capture->base, &reports);
    if (success) {
      failure_stage = "BASE descriptor identity/bytes";
      success = file_stamp(capture->base, encoded_base) && encoded_base.bytes &&
                same_identity(encoded_base, capture->base_stamp);
    }
    if (success && WM_checkpoint_capture_owner_count(*capture->capture)) {
      failure_stage = "RUNTIME descriptor admission";
      success = borrow_output(*capture, "RUNTIME", capture->runtime, capture->runtime_stamp);
      if (success) {
        failure_stage = "RUNTIME capture/verification";
        success = WM_checkpoint_capture_runtime_write(*capture->capture, capture->runtime, &reports,
                    [&]() { return file_stamp(capture->runtime, encoded_runtime) && encoded_runtime.bytes &&
                                   same_identity(encoded_runtime, capture->runtime_stamp); });
      }
    }
    if (success) {
      failure_stage = "capture purpose admission";
      PyObject *purpose = PyObject_CallMethod(owner, "_native_purpose", nullptr);
      success = purpose && PyUnicode_CheckExact(purpose);
      if (success && PyUnicode_CompareWithASCIIString(purpose, "safety") == 0) {
        failure_stage = "safety content witness";
        capture->content = WM_checkpoint_capture_content(*capture->capture, &reports);
        success = capture->content != nullptr;
      }
      Py_XDECREF(purpose);
    }
  }
  Py_DECREF(binding);
  FileStamp base, runtime;
  if (success) {
    failure_stage = "final output identity/bytes";
    success = file_stamp(capture->base, base) && base == encoded_base && base.bytes &&
              (capture->runtime < 0 ||
               (file_stamp(capture->runtime, runtime) && runtime == encoded_runtime && runtime.bytes));
    if (success) {
      capture->base_stamp = base;
      capture->runtime_stamp = runtime;
      capture->complete = true;
    }
  }
  if (!success) {
    PyObject *type, *value, *traceback;
    PyErr_Fetch(&type, &value, &traceback);
    if (capture->capture) {
      WM_checkpoint_capture_post(*capture->capture, false);
    }
    release(*capture);
    WM_checkpoint_content_free(capture->content);
    capture->content = nullptr;
    PyErr_Clear();
    Py_DECREF(capture->owner);
    if (type) {
      PyErr_Restore(type, value, traceback);
    }
    else {
      /* Preserve the actual native refusal before the scoped ReportList is freed. This does
       * not change capture admission or certify any partial output. Existing Python exceptions
       * still take precedence and are restored above without stringifying their payload. */
      char *native_error = BKE_reports_string(&reports, RPT_ERROR);
      PyErr_Format(PyExc_RuntimeError,
                   "Checkpoint capture failed; partial outputs remain unproved (stage: %s)%s%s",
                   failure_stage,
                   native_error ? ": " : "",
                   native_error ? native_error : "");
      MEM_delete(native_error);
    }
    return nullptr;
  }
  PyObject *capsule = PyCapsule_New(capture.get(), capture_name, capsule_free);
  if (!capsule) {
    release(*capture);
    WM_checkpoint_content_free(capture->content);
    capture->content = nullptr;
    Py_DECREF(capture->owner);
    return nullptr;
  }
  capture.release();
  return capsule;
}
static PyObject *facts_call(PyObject *, PyObject *args)
{
  PyObject *capsule, *owner;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &owner)) {
    return nullptr;
  }
  NativeCapture *capture = admit(capsule, owner, true);
  if (!capture) {
    return nullptr;
  }
  const uint64_t owners = WM_checkpoint_capture_owner_count(*capture->capture);
  return Py_BuildValue("{s:s,s:K,s:K,s:K,s:i,s:s}",
                       "capture_id", capture->capture_id.c_str(),
                       "scene_bytes", capture->base_stamp.bytes,
                       "runtime_bytes", capture->runtime < 0 ? uint64_t(0) : capture->runtime_stamp.bytes,
                       "owner_count", owners, "format_version", owners ? 1 : 0,
                       "post_state", !capture->post_attempted ? "pending" : capture->post_failed ? "failed" : "complete");
}
static void post_admission_failed(NativeCapture &capture)
{
  PyObject *type, *value, *traceback;
  PyErr_Fetch(&type, &value, &traceback);
  capture.post_attempted = capture.post_failed = true;
  WM_checkpoint_capture_post(*capture.capture, false);
  PyErr_Clear();
  PyErr_Restore(type, value, traceback);
}
static PyObject *post_call(PyObject *, PyObject *args)
{
  PyObject *capsule, *owner;
  if (!PyArg_ParseTuple(args, "OO", &capsule, &owner)) {
    return nullptr;
  }
  if (!require_main_thread()) { return nullptr; }
  auto *pending = static_cast<NativeCapture *>(PyCapsule_GetPointer(capsule, capture_name));
  if (!pending) { return nullptr; }
  NativeCapsuleLease lease(capsule, pending->consuming);
  if (!lease) { return nullptr; }
  NativeCapture *capture = admit(capsule, owner, true);
  if (!capture) {
    if (pending->owner == owner && pending->active && pending->capture &&
        !pending->post_attempted && WM_checkpoint_capture_validate(*pending->capture)) {
      post_admission_failed(*pending);
    }
    return nullptr;
  }
  if (capture->post_attempted) {
    PyErr_SetString(PyExc_ValueError, "Checkpoint POST was already attempted");
    return nullptr;
  }
  PyObject *publication = PyObject_CallMethod(owner, "_native_publication", nullptr);
  if (!publication) {
    post_admission_failed(*capture);
    return nullptr;
  }
  Py_DECREF(publication);
  capture->post_attempted = true;
  /* Like an ordinary save, a SAVE_POST handler that raises is reported, never a POST failure. */
  capture->post_failed = !WM_checkpoint_capture_post(*capture->capture, true);
  if (capture->post_failed) {
    PyErr_SetString(PyExc_RuntimeError, "Checkpoint was published but SAVE_POST failed");
    return nullptr;
  }
  Py_RETURN_NONE;
}
static PyObject *abort_call(PyObject *, PyObject *args)
{
  PyObject *capsule, *owner;
  if (!require_main_thread() || !PyArg_ParseTuple(args, "OO", &capsule, &owner)) {
    return nullptr;
  }
  auto *capture = static_cast<NativeCapture *>(PyCapsule_GetPointer(capsule, capture_name));
  if (!capture) { return nullptr; }
  NativeCapsuleLease lease(capsule, capture->consuming);
  if (!lease) { return nullptr; }
  if (!capture || capture->owner != owner || !capture->active || !capture->capture ||
      !WM_checkpoint_capture_validate(*capture->capture) || capture->post_attempted)
  {
    if (!PyErr_Occurred()) {
      PyErr_SetString(PyExc_ValueError, "There is no pending matching native capture to abort");
    }
    return nullptr;
  }
  /* Failed publication may have ambiguous links/metadata; output readability must not prevent
   * delivery of the one owed FAIL callback while the exact live Main owner is still valid. */
  capture->post_attempted = capture->post_failed = true;
  if (!WM_checkpoint_capture_post(*capture->capture, false)) {
    PyErr_SetString(PyExc_RuntimeError, "Checkpoint SAVE_POST_FAIL owner changed");
    return nullptr;
  }
  Py_RETURN_NONE;
}
static PyObject *close_call(PyObject *, PyObject *capsule)
{
  if (!require_main_thread()) {
    return nullptr;
  }
  auto *capture = static_cast<NativeCapture *>(PyCapsule_GetPointer(capsule, capture_name));
  if (!capture) {
    return nullptr;
  }
  NativeCapsuleLease lease(capsule, capture->consuming);
  if (!lease) { return nullptr; }
  bool safety_checked = false;
  WMCheckpointContentMatch safety_match = WMCheckpointContentMatch::Unverified;
  if (capture->active && capture->complete && capture->post_attempted && !capture->post_failed) {
    PyObject *terminal = PyObject_CallMethod(capture->owner, "_native_terminal", nullptr);
    if (terminal && PyDict_CheckExact(terminal)) {
      PyObject *identity = PyDict_GetItemString(terminal, "id");
      PyObject *purpose = PyDict_GetItemString(terminal, "purpose");
      PyObject *state = PyDict_GetItemString(terminal, "state");
      capture->terminal_safety = identity && purpose && state &&
          PyUnicode_CheckExact(identity) && PyUnicode_CheckExact(purpose) && PyUnicode_CheckExact(state) &&
          PyUnicode_CompareWithASCIIString(identity, capture->capture_id.c_str()) == 0 &&
          PyUnicode_CompareWithASCIIString(purpose, "safety") == 0 &&
          PyUnicode_CompareWithASCIIString(state, "READY") == 0 &&
          PyDict_GetItemString(terminal, "protected") == Py_True;
    }
    Py_XDECREF(terminal);
    safety_checked = capture->terminal_safety;
    if (capture->terminal_safety && capture->content && capture->capture &&
        WM_checkpoint_capture_validate(*capture->capture)) {
      ReportList reports;
      BKE_reports_init(&reports, RPT_STORE);
      BLI_SCOPED_DEFER([&]() { BKE_reports_free(&reports); });
      safety_match = WM_checkpoint_content_matches(*capture->source_main, *capture->content, &reports);
      capture->terminal_safety = safety_match == WMCheckpointContentMatch::Match;
    }
    else {
      capture->terminal_safety = false;
    }
  }
  release(*capture);
  if (PyErr_Occurred()) {
    return nullptr;
  }
  if (safety_checked && safety_match != WMCheckpointContentMatch::Match) {
    PyErr_SetString(safety_match == WMCheckpointContentMatch::Different ? PyExc_ValueError : PyExc_RuntimeError,
        safety_match == WMCheckpointContentMatch::Different ?
            "SAVE_POST changed current contents; complete a fresh safety capture" :
            "Current safety contents could not be verified within available resources/runtime");
    return nullptr;
  }
  Py_RETURN_NONE;
}
constexpr const char *restore_name = "angelo.native_checkpoint_restore";
struct NativeRestore {
  PyObject *resource = nullptr;
  bool active = false, attempted = false, consuming = false;
  std::string identity, logical_path;
  int base = -1, runtime = -1;
  FileStamp base_stamp, runtime_stamp;
  std::unique_ptr<ed::sculpt_paint::checkpoint::DecodePlan> plan;
  Vector<BLOCheckpointIDToken> ids;
  Vector<BLOCheckpointLayerToken> layers;
  BlendFileData *base_data = nullptr;
  WMCheckpointColdInstall *cold = nullptr;
};
static void release_restore(NativeRestore &restore)
{
  WM_checkpoint_cold_free(restore.cold);
  restore.cold = nullptr;
  if (restore.base_data) {
    BLO_blendfiledata_free(restore.base_data);
    restore.base_data = nullptr;
  }
  restore.plan.reset();
  if (restore.active) {
    restore.active = false;
    PyObject *result = PyObject_CallMethod(restore.resource, "_native_end", nullptr);
    Py_XDECREF(result);
  }
}
static void restore_capsule_free(PyObject *capsule)
{
  auto *restore = static_cast<NativeRestore *>(PyCapsule_GetPointer(capsule, restore_name));
  if (!restore) { PyErr_Clear(); return; }
  PyObject *type, *value, *traceback;
  PyErr_Fetch(&type, &value, &traceback);
  release_restore(*restore);
  if (PyErr_Occurred()) { PyErr_WriteUnraisable(capsule); }
  Py_DECREF(restore->resource);
  delete restore;
  PyErr_Restore(type, value, traceback);
}
static bool exact_recovery(PyObject *resource)
{
  PyObject *module = PyImport_ImportModule("angelo_agent.checkpoint_recovery");
  if (!module) { return false; }
  PyObject *type = PyObject_GetAttrString(module, "CheckpointRecovery");
  Py_DECREF(module);
  if (!type) { return false; }
  const bool exact = reinterpret_cast<PyObject *>(Py_TYPE(resource)) == type;
  Py_DECREF(type);
  if (!exact) { PyErr_SetString(PyExc_TypeError, "An exact anchored CheckpointRecovery is required"); }
  return exact;
}
static PyObject *prepare_restore_call(PyObject *, PyObject *resource)
{
  using namespace ed::sculpt_paint::checkpoint;
  if (!require_main_thread() || !exact_recovery(resource)) { return nullptr; }
  auto restore = std::make_unique<NativeRestore>();
  restore->resource = Py_NewRef(resource);
  PyObject *binding = PyObject_CallMethod(resource, "_native_begin", nullptr);
  if (!binding) { Py_DECREF(restore->resource); return nullptr; }
  restore->active = true;
  const char *identity, *logical_path;
  CaptureID capture_id;
  bool success = PyArg_ParseTuple(binding, "ssii", &identity, &logical_path,
                                   &restore->base, &restore->runtime) &&
                 parse_capture_id(identity, capture_id);
  if (success) {
    restore->identity = identity;
    restore->logical_path = logical_path;
    success = file_stamp(restore->base, restore->base_stamp) && restore->base_stamp.bytes &&
              file_stamp(restore->runtime, restore->runtime_stamp) && restore->runtime_stamp.bytes;
  }
  Py_DECREF(binding);
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BLI_SCOPED_DEFER([&]() { BKE_reports_free(&reports); });
  CodecLimits limits{};
  size_t base_backing_bytes = 0;
  success = success && WM_checkpoint_memory_limits(limits, restore->base_stamp.bytes, &reports, &base_backing_bytes);
  const char *error = nullptr;
  if (success) {
    restore->plan = companion_decode_prepare(restore->runtime_stamp.bytes, capture_id, limits,
        [&](const uint64_t offset, MutableSpan<std::byte> bytes) {
          if (offset > uint64_t(INT64_MAX) || BLI_lseek(restore->runtime, int64_t(offset), SEEK_SET) < 0) {
            return false;
          }
          while (!bytes.is_empty()) {
            const int64_t count = BLI_read(restore->runtime, bytes.data(), size_t(bytes.size()));
            if (count <= 0) { return false; }
            bytes = bytes.drop_front(count);
          }
          return true;
        }, &error);
    success = bool(restore->plan) && companion_decode_geometry(*restore->plan, &error);
  }
  if (success) {
    auto add_id = [&](const uint64_t token, const short code) {
      if (!token) { return; }
      for (const auto &row : restore->ids) {
        if (row.token == token && row.id_code == code) { return; }
      }
      restore->ids.append({token, code, nullptr});
    };
    for (const auto &object : restore->plan->objects) {
      add_id(object.binding.object_token, ID_OB);
      add_id(object.binding.mesh_token, ID_ME);
      add_id(object.binding.key_token, ID_KE);
    }
    for (const auto &grid : restore->plan->grids) {
      add_id(grid.scene_token, ID_SCE);
      restore->layers.append({grid.scene_token, grid.view_layer_token, nullptr, nullptr});
    }
    BLOCheckpointReadTokens tokens{restore->ids.as_mutable_span(), restore->layers.as_mutable_span()};
    tokens.max_data_bytes = base_backing_bytes;
    BlendFileReadReport read_report{};
    read_report.reports = &reports;
    restore->base_data = BLO_read_checkpoint_from_descriptor(restore->logical_path.c_str(),
                                                            restore->base, &read_report, tokens);
    success = bool(restore->base_data) && tokens.complete;
    if (success) {
      restore->cold = WM_checkpoint_cold_prepare(*restore->base_data->main, *restore->plan, tokens, &reports);
      success = restore->cold != nullptr;
    }
  }
  FileStamp base, runtime;
  success = success && file_stamp(restore->base, base) && base == restore->base_stamp &&
            file_stamp(restore->runtime, runtime) && runtime == restore->runtime_stamp;
  if (!success) {
    PyObject *type, *value, *traceback;
    PyErr_Fetch(&type, &value, &traceback);
    release_restore(*restore);
    PyErr_Clear();
    Py_DECREF(restore->resource);
    if (type) { PyErr_Restore(type, value, traceback); }
    else { raise_with_reports(PyExc_ValueError, "The complete checkpoint pair failed native preparation", reports, error); }
    return nullptr;
  }
  PyObject *capsule = PyCapsule_New(restore.get(), restore_name, restore_capsule_free);
  if (!capsule) {
    release_restore(*restore);
    Py_DECREF(restore->resource);
    return nullptr;
  }
  restore.release();
  return capsule;
}
static bool safety_content_valid(NativeCapture &safety, Main &main,
                                 const WMCheckpointContentScope scope = WMCheckpointContentScope::Full)
{
  PyObject *safety_valid = PyObject_CallMethod(safety.owner, "_native_safety", nullptr);
  if (!safety_valid) { return false; }
  Py_DECREF(safety_valid);
  if (CTX_data_main(BPY_context_get()) != &main || G_MAIN != &main) { return false; }
  ReportList content_reports;
  BKE_reports_init(&content_reports, RPT_STORE);
  BLI_SCOPED_DEFER([&]() { BKE_reports_free(&content_reports); });
  const WMCheckpointContentMatch match = WM_checkpoint_content_matches(main, *safety.content, &content_reports, scope);
  if (match == WMCheckpointContentMatch::Unverified) {
    PyErr_SetString(PyExc_RuntimeError, "Current safety contents could not be verified within available resources/runtime");
    return false;
  }
  FileStamp safety_base, safety_runtime;
  if (match != WMCheckpointContentMatch::Match || !file_stamp(safety.base, safety_base) ||
      !same_identity(safety_base, safety.base_stamp) || safety_base.bytes != safety.base_stamp.bytes ||
      safety_base.modified != safety.base_stamp.modified || safety_base.modified_ns != safety.base_stamp.modified_ns ||
      (safety.runtime >= 0 && (!file_stamp(safety.runtime, safety_runtime) ||
       !same_identity(safety_runtime, safety.runtime_stamp) || safety_runtime.bytes != safety.runtime_stamp.bytes ||
       safety_runtime.modified != safety.runtime_stamp.modified || safety_runtime.modified_ns != safety.runtime_stamp.modified_ns))) {
    PyErr_SetString(PyExc_ValueError, "Safety contents or durable resources changed; complete a fresh safety capture");
    return false;
  }
  return true;
}
/** A Restore that failed after replacing the document: the failed document and the identity
 * of the safety capture that may be reloaded into it. Valid only within one Restore operator. */
struct FailedRestore {
  Main *main = nullptr;
  std::string safety_id;
};
static FailedRestore failed_restore;
/** Add-on handler errors (load_pre, load_post, ...) raised during the latest Restore or reload. Like
 * File > Open, they are printed and reported, never a Restore failure: the callback observer
 * still validates every effect a handler had on the restored data. */
static uint64_t restore_handler_errors = 0;
/** One native Restore of ``restore``'s prepared pair, consuming its detached BASE.
 * ``validate_source`` admits the current document for replacement; ``r_replaced`` reports
 * whether the previous document was freed. */
static bool run_native_restore(NativeRestore &restore, ReportList &reports,
                               FunctionRef<bool(Main &, WMCheckpointContentScope)> validate_source,
                               bool &r_replaced)
{
  const uint64_t errors_before = BPY_app_handlers_error_count();
  BLI_SCOPED_DEFER([&]() { restore_handler_errors = BPY_app_handlers_error_count() - errors_before; });
  BlendFileData *base_data = std::exchange(restore.base_data, nullptr);
  struct CallbackBridge { NativeRestore *restore; ReportList *reports; } callback_bridge{&restore, &reports};
  ScopedCheckpointCallbackObserver callback_observer(base_data->main, &callback_bridge,
      [](void *data, Main *main) -> void * {
        auto &bridge = *static_cast<CallbackBridge *>(data);
        return WM_checkpoint_cold_callback_begin(*main, *bridge.restore->cold, bridge.reports);
      },
      [](void *data, void *witness, Main *main) -> bool {
        auto &bridge = *static_cast<CallbackBridge *>(data);
        return WM_checkpoint_cold_callback_end(main, *bridge.restore->cold,
            static_cast<WMCheckpointColdCallback *>(witness), bridge.reports);
      });
  const bool success = WM_checkpoint_restore(BPY_context_get(), base_data, restore.logical_path.c_str(),
      validate_source,
      [&](Main &main) {
        return callback_observer.valid() && WM_checkpoint_cold_validate(main, *restore.cold, true, &reports);
      },
      [&](Main &main, CheckpointCaptureOwner *owner) {
        return callback_observer.valid() &&
               WM_checkpoint_cold_runtime_begin(main, *restore.cold, owner, &reports);
      },
      [&](Main &main, CheckpointCaptureOwner *owner) {
        return callback_observer.valid() &&
               WM_checkpoint_cold_install(BPY_context_get(), main, *restore.cold, owner, &reports);
      },
      [&](Main &main, CheckpointCaptureOwner *owner) {
        return WM_checkpoint_cold_preserve_callback_edits(main, *restore.cold, owner, &reports);
      },
      [&]() { WM_checkpoint_cold_runtime_end(*restore.cold); }, &reports, &r_replaced);
  return success && callback_observer.valid();
}
static PyObject *restore_call(PyObject *, PyObject *args)
{
  PyObject *capsule, *safety_capsule;
  if (!require_main_thread() || !PyArg_ParseTuple(args, "OO", &capsule, &safety_capsule)) { return nullptr; }
  auto *restore = static_cast<NativeRestore *>(PyCapsule_GetPointer(capsule, restore_name));
  if (!restore) { return nullptr; }
  auto *safety = static_cast<NativeCapture *>(PyCapsule_GetPointer(safety_capsule, capture_name));
  if (!safety) { return nullptr; }
  /* Acquire both before any Python resource validation. Keep these outside the callback
   * observer so its tokens are disposed before either public close can become admissible. */
  NativeCapsuleLease restore_lease(capsule, restore->consuming);
  if (!restore_lease) { return nullptr; }
  NativeCapsuleLease safety_lease(safety_capsule, safety->consuming);
  if (!safety_lease) { return nullptr; }
  if (!restore->active || restore->attempted || !restore->cold || !restore->base_data ||
      !safety->complete || !safety->post_attempted || safety->post_failed || safety->active ||
      !safety->terminal_safety || safety->safety_consumed ||
      safety->resources_released || !safety->content ||
      safety->source_main != CTX_data_main(BPY_context_get()))
  {
    PyErr_SetString(PyExc_ValueError, "Restore needs its prepared pair and completed protected native safety capture");
    return nullptr;
  }
  if (!safety_content_valid(*safety, *safety->source_main)) { return nullptr; }
  PyObject *validation = PyObject_CallMethod(restore->resource, "_validate", nullptr);
  if (!validation) { return nullptr; }
  Py_DECREF(validation);
  FileStamp base, runtime;
  if (!file_stamp(restore->base, base) || !(base == restore->base_stamp) ||
      !file_stamp(restore->runtime, runtime) || !(runtime == restore->runtime_stamp))
  {
    PyErr_SetString(PyExc_ValueError, "The prepared checkpoint recovery files changed during safety capture");
    return nullptr;
  }
  restore->attempted = true;
  safety->safety_consumed = true;
  failed_restore = {};
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BLI_SCOPED_DEFER([&]() { BKE_reports_free(&reports); });
  PyObject *source_error = nullptr;
  bool replaced = false;
  const bool success = run_native_restore(*restore, reports,
      [&](Main &main, const WMCheckpointContentScope scope) {
        const bool valid = safety_content_valid(*safety, main, scope);
        /* Refusal repairs still-live screen runtime, whose ordinary region polls may call
         * Python. Preserve the primary safety exception without running them under it. */
        if (!valid && scope == WMCheckpointContentScope::RetainedUI) {
          source_error = PyErr_GetRaisedException();
        }
        return valid;
      },
      replaced);
  if (source_error) { PyErr_SetRaisedException(source_error); }
  if (!success) {
    if (replaced) {
      /* The previous document is already freed. Admit one reload of exactly this Restore's
       * protected safety capture into the failed document (reload_failed_restore). */
      failed_restore = {CTX_data_main(BPY_context_get()), safety->capture_id};
    }
    if (!PyErr_Occurred()) {
      raise_with_reports(PyExc_RuntimeError,
                         replaced ? "Checkpoint Restore failed after replacing the document; reload its "
                                    "protected safety checkpoint" :
                                    "Checkpoint Restore was refused before replacing the document",
                         reports);
    }
    return nullptr;
  }
  Py_RETURN_TRUE;
}
/** The safety capture identity whose reload the current failed document admits, or None. */
static PyObject *failed_restore_safety_call(PyObject *, PyObject *)
{
  if (!require_main_thread()) { return nullptr; }
  Main *main = CTX_data_main(BPY_context_get());
  if (!failed_restore.main || failed_restore.main != main || G_MAIN != main) { Py_RETURN_NONE; }
  return PyUnicode_FromString(failed_restore.safety_id.c_str());
}
/** Stop every window-manager job (icon and material previews, renders) before a Restore's safety
 * capture, with the GIL released: a job writing ID previews into Main between the capture and the
 * replacement would make the document differ from its safety capture, and an ending job thread can
 * need the GIL this Python call holds. Reading the file stops them anyway (File > Open does too). */
static PyObject *quiesce_jobs_call(PyObject *, PyObject *)
{
  if (!require_main_thread()) { return nullptr; }
  Main *main = CTX_data_main(BPY_context_get());
  Py_BEGIN_ALLOW_THREADS;
  for (wmWindowManager &wm : main->wm) {
    WM_jobs_kill_all(&wm);
  }
  Py_END_ALLOW_THREADS;
  Py_RETURN_NONE;
}
/** ``begin_restore()``: remember the document open now, just before a Restore (or Undo restore)
 * replaces it: its path, relative paths and compression (#WM_angelo_restore_begin). */
static PyObject *begin_restore_call(PyObject *, PyObject *)
{
  if (!require_main_thread()) { return nullptr; }
  WM_angelo_restore_begin(CTX_data_main(BPY_context_get()));
  Py_RETURN_NONE;
}
/** ``adopt_restored_document()``: after a Restore (or Undo restore, or a safety reload) opened its
 * private working copy, make the document remembered by ``begin_restore()`` the document the
 * person sees and saves (#WM_angelo_adopt_restored_document). It takes no path: nothing a snapshot
 * recorded can reach it. Raises ``ValueError`` when the open document is not a working copy or
 * the remembered document is not a project file. */
static PyObject *adopt_restored_document_call(PyObject *, PyObject *)
{
  if (!require_main_thread()) { return nullptr; }
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BLI_SCOPED_DEFER([&]() { BKE_reports_free(&reports); });
  if (!WM_angelo_adopt_restored_document(BPY_context_get(), &reports)) {
    raise_with_reports(PyExc_ValueError, "The restored document kept its working copy's path", reports);
    return nullptr;
  }
  Py_RETURN_TRUE;
}
/** ``is_recovery_copy(path)``: whether ``path`` is a Restore's private working copy. */
static PyObject *is_recovery_copy_call(PyObject *, PyObject *args)
{
  PyObject *bytes = nullptr;
  if (!require_main_thread() || !PyArg_ParseTuple(args, "O&", PyUnicode_FSConverter, &bytes)) {
    return nullptr;
  }
  const bool copy = WM_angelo_recovery_copy(PyBytes_AS_STRING(bytes));
  Py_DECREF(bytes);
  return PyBool_FromLong(copy);
}
/** ``set_state_root(path)``: Angelo's state folder, whose ``checkpoints`` hold Restore's working
 * copies (#WM_angelo_recovery_copy); only a file there is ever treated as one. */
static PyObject *set_state_root_call(PyObject *, PyObject *args)
{
  const char *root;
  if (!require_main_thread() || !PyArg_ParseTuple(args, "s", &root)) { return nullptr; }
  WM_angelo_state_root_set(root);
  Py_RETURN_NONE;
}
/** Whether the open document is a restored one whose first Save over its original still asks. */
static PyObject *restored_save_pending_call(PyObject *, PyObject *)
{
  if (!require_main_thread()) { return nullptr; }
  return PyBool_FromLong(WM_angelo_restored_save_pending(CTX_data_main(BPY_context_get())));
}
/** How many add-on handler errors the latest Restore or reload printed (reported, not a failure). */
static PyObject *restore_handler_errors_call(PyObject *, PyObject *)
{
  if (!require_main_thread()) { return nullptr; }
  return PyLong_FromUnsignedLongLong(restore_handler_errors);
}
/** The Restore operator ends: a later document never inherits the reload admission. */
static PyObject *clear_failed_restore_call(PyObject *, PyObject *)
{
  if (!require_main_thread()) { return nullptr; }
  failed_restore = {};
  Py_RETURN_NONE;
}
/** Reload the protected safety pair of a Restore that failed after replacing the document.
 *
 * The failed document holds nothing of the person's: it is this Restore's own partial install.
 * So no new safety capture of it is required, but only that exact safety capture may be
 * reloaded, into that exact failed document, once. Everything else is ordinary native Restore:
 * the same verified working copies, decoding, install and initial Undo. */
static PyObject *reload_failed_restore_call(PyObject *, PyObject *capsule)
{
  if (!require_main_thread()) { return nullptr; }
  auto *restore = static_cast<NativeRestore *>(PyCapsule_GetPointer(capsule, restore_name));
  if (!restore) { return nullptr; }
  NativeCapsuleLease lease(capsule, restore->consuming);
  if (!lease) { return nullptr; }
  Main *main = CTX_data_main(BPY_context_get());
  const FailedRestore admitted = std::exchange(failed_restore, {});
  if (!admitted.main || admitted.main != main || G_MAIN != main || restore->identity != admitted.safety_id ||
      !restore->active || restore->attempted || !restore->cold || !restore->base_data)
  {
    PyErr_SetString(PyExc_ValueError,
                    "Only the protected safety checkpoint of a Restore that failed after replacing the "
                    "document can be reloaded into it");
    return nullptr;
  }
  PyObject *validation = PyObject_CallMethod(restore->resource, "_validate", nullptr);
  if (!validation) { return nullptr; }
  Py_DECREF(validation);
  FileStamp base, runtime;
  if (!file_stamp(restore->base, base) || !(base == restore->base_stamp) ||
      !file_stamp(restore->runtime, runtime) || !(runtime == restore->runtime_stamp))
  {
    PyErr_SetString(PyExc_ValueError, "The prepared safety recovery files changed");
    return nullptr;
  }
  restore->attempted = true;
  ReportList reports;
  BKE_reports_init(&reports, RPT_STORE);
  BLI_SCOPED_DEFER([&]() { BKE_reports_free(&reports); });
  bool replaced = false;
  const bool success = run_native_restore(*restore, reports,
      [&](Main &source, const WMCheckpointContentScope) { return &source == main && G_MAIN == main; },
      replaced);
  if (!success) {
    if (replaced) {
      failed_restore = {CTX_data_main(BPY_context_get()), restore->identity};
    }
    if (!PyErr_Occurred()) {
      raise_with_reports(PyExc_RuntimeError,
                         replaced ? "Reloading the safety checkpoint failed after replacing the document" :
                                    "Reloading the safety checkpoint was refused",
                         reports);
    }
    return nullptr;
  }
  Py_RETURN_TRUE;
}
static PyObject *close_restore_call(PyObject *, PyObject *capsule)
{
  if (!require_main_thread()) { return nullptr; }
  auto *restore = static_cast<NativeRestore *>(PyCapsule_GetPointer(capsule, restore_name));
  if (!restore) { return nullptr; }
  NativeCapsuleLease lease(capsule, restore->consuming);
  if (!lease) { return nullptr; }
  release_restore(*restore);
  if (PyErr_Occurred()) { return nullptr; }
  Py_RETURN_NONE;
}
static PyObject *capture_release_call(PyObject *, PyObject *capsule)
{
  if (!require_main_thread()) { return nullptr; }
  auto *capture = static_cast<NativeCapture *>(PyCapsule_GetPointer(capsule, capture_name));
  if (!capture) { return nullptr; }
  NativeCapsuleLease lease(capsule, capture->consuming);
  if (!lease) { return nullptr; }
  if (capture->active) {
    PyErr_SetString(PyExc_ValueError, "Close native ownership before releasing safety resources");
    return nullptr;
  }
  WM_checkpoint_content_free(capture->content);
  capture->content = nullptr;
  capture->terminal_safety = false;
  if (!capture->resources_released) {
    capture->resources_released = true;
    PyObject *result = PyObject_CallMethod(capture->owner, "abandon", nullptr);
    if (!result) { return nullptr; }
    Py_DECREF(result);
  }
  Py_RETURN_NONE;
}
static PyMethodDef methods[] = {
    {"capture", capture_call, METH_O, nullptr},
    {"capture_facts", facts_call, METH_VARARGS, nullptr},
    {"capture_post", post_call, METH_VARARGS, nullptr},
    {"capture_close", close_call, METH_O, nullptr},
    {"capture_abort", abort_call, METH_VARARGS, nullptr},
    {"capture_release", capture_release_call, METH_O, nullptr},
    {"prepare_restore", prepare_restore_call, METH_O, nullptr},
    {"restore", restore_call, METH_VARARGS, nullptr},
    {"close_restore", close_restore_call, METH_O, nullptr},
    {"failed_restore_safety", failed_restore_safety_call, METH_NOARGS, nullptr},
    {"clear_failed_restore", clear_failed_restore_call, METH_NOARGS, nullptr},
    {"reload_failed_restore", reload_failed_restore_call, METH_O, nullptr},
    {"restore_handler_errors", restore_handler_errors_call, METH_NOARGS, nullptr},
    {"quiesce_jobs", quiesce_jobs_call, METH_NOARGS, nullptr},
    {"begin_restore", begin_restore_call, METH_NOARGS, nullptr},
    {"adopt_restored_document", adopt_restored_document_call, METH_NOARGS, nullptr},
    {"is_recovery_copy", is_recovery_copy_call, METH_VARARGS, nullptr},
    {"restored_save_pending", restored_save_pending_call, METH_NOARGS, nullptr},
    {"set_state_root", set_state_root_call, METH_VARARGS, nullptr},
    {nullptr, nullptr, 0, nullptr},
};
static PyModuleDef module = {PyModuleDef_HEAD_INIT, "_angelo_checkpoint", nullptr, -1, methods};
}  // namespace
PyObject *BPY_angelo_checkpoint_module()
{
  PyObject *mod = PyModule_Create(&module);
  /* WM_checkpoint_capture_write never packs external files into the live document, so the
   * add-on need not hold Automatically Pack Resources off around a capture
   * (checkpoint_images.capture_packs), and the snapshot keeps the artist's setting. */
  if (mod && PyModule_AddObjectRef(mod, "capture_never_packs", Py_True) < 0) {
    Py_DECREF(mod);
    return nullptr;
  }
  return mod;
}
}  // namespace blender
