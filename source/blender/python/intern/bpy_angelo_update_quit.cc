/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 * SPDX-License-Identifier: GPL-2.0-or-later */
/** \file
 * \ingroup pythonintern
 * Private updater hand-over, bound to one existing delayed WM handler. No agent callback API.
 */
#include <Python.h>
#include <array>
#include <cstring>
#include <cstdlib>
#include <memory>
#include <mutex>
#ifdef _WIN32
#  include <io.h>
#  ifndef NOMINMAX
#    define NOMINMAX
#  endif
#  include <windows.h>
#else
#  include <fcntl.h>
#  include <sys/stat.h>
#  include <unistd.h>
#endif

#include "MEM_guardedalloc.h"
#include "AS_asset_catalog.hh"
#include "BLI_threads.h"
#include "BKE_context.hh"
#include "BKE_global.hh"
#include "BKE_wm_runtime.hh"
#include "DNA_windowmanager_types.h"
#include "WM_api.hh"
#include "WM_types.hh"
#include "wm_event_system.hh"
#include "bpy_capi_utils.hh"
#include "bpy_angelo_update_quit.hh"

namespace blender {
namespace {
constexpr char capsule_name[] = "angelo.private_updater_quit.v1";
constexpr char frame_prefix[] = "ANGELO_UPDATE_COMMIT:";
enum class Phase { Pending, Revoked, Committing };

struct UpdateQuit {
  uint64_t catalog_generation = 0;
  int descriptor = -1;
  Phase phase = Phase::Pending;
  bool close_uncertain = false;
  std::array<char, 33> nonce{};
  std::array<char, 96> frame{};
  int frame_size = 0;
  wmWindow *window = nullptr;  /* Weak: validate against the current manager, never dereference it. */
  int window_id = 0;
  wmEventHandler_UI *handler = nullptr;
  void *handler_data = nullptr;
  std::mutex mutex;
  bool close_locked()
  {
    if (descriptor < 0) {
      return true;
    }
    if (close_uncertain) {
      return false;
    }
#ifdef _WIN32
    const int result = _close(descriptor);
#else
    const int result = ::close(descriptor);
#endif
    if (result == 0) {
      descriptor = -1;
      return true;
    }
    /* A failed CRT/POSIX close may already have retired/reused the fd number.
     * Never retry that integer and risk closing an unrelated later descriptor.
     * Keep this invocation unproved and revoked; OS process exit is final cleanup. */
    close_uncertain = true;
    return false;
  }
  void revoke()
  {
    const std::lock_guard lock(mutex);
    if (phase != Phase::Committing) {
      phase = Phase::Revoked;
      close_locked();
    }
  }
  ~UpdateQuit()
  {
    close_locked();
  }
};
using Owner = std::shared_ptr<UpdateQuit>;

static Owner *owner_pointer(PyObject *capsule)
{
  return static_cast<Owner *>(PyCapsule_GetPointer(capsule, capsule_name));
}

static void capsule_free(PyObject *capsule)
{
  Owner *owner = owner_pointer(capsule);
  if (owner != nullptr) {
    (*owner)->revoke();
    MEM_delete(owner);
  }
  else {
    PyErr_Clear();
  }
}

#if defined(_WIN32) && defined(_MSC_VER)
/* Invalid positive CRT descriptors otherwise invoke UCRT's terminating handler.
 * Match the existing B1 asset descriptor owner: thread-local, restored, never global. */
struct CRTDescriptorGuard {
  _invalid_parameter_handler previous;
  CRTDescriptorGuard()
      : previous(_set_thread_local_invalid_parameter_handler(
            [](const wchar_t *, const wchar_t *, const wchar_t *, unsigned int, uintptr_t) {}))
  {
  }
  ~CRTDescriptorGuard() { _set_thread_local_invalid_parameter_handler(previous); }
};
#endif

static bool valid_pipe(const int descriptor)
{
#ifdef _WIN32
#  if defined(_MSC_VER)
  const CRTDescriptorGuard guard;
  const HANDLE handle = reinterpret_cast<HANDLE>(_get_osfhandle(descriptor));
  DWORD state = 0;
  return handle != INVALID_HANDLE_VALUE && GetFileType(handle) == FILE_TYPE_PIPE &&
         GetNamedPipeHandleStateW(handle, &state, nullptr, nullptr, nullptr, nullptr, 0) &&
         (state & PIPE_NOWAIT);
#  else
  return false;  /* Unsupported CRT: no unguarded integer-to-HANDLE lookup. */
#  endif
#else
  struct stat info;
  const int flags = fcntl(descriptor, F_GETFL);
  return fstat(descriptor, &info) == 0 && S_ISFIFO(info.st_mode) && flags >= 0 &&
         (flags & O_NONBLOCK);
#endif
}

static void handler_free(bContext * /*C*/, void *data)
{
  Owner *owner = static_cast<Owner *>(data);
  {
    const std::lock_guard lock((*owner)->mutex);
    (*owner)->handler = nullptr;
    (*owner)->handler_data = nullptr;
  }
  (*owner)->revoke();
  MEM_delete(owner);
}

static int handle_update_quit(bContext *C, const wmEvent *event, void *data);

static int retired_handler(bContext *, const wmEvent *, void *)
{
  return WM_UI_HANDLER_CONTINUE;
}

static void retire_handler(bContext *C, const Owner &owner)
{
  BLI_assert(BLI_thread_is_main());
  wmWindowManager *manager = C ? CTX_wm_manager(C) : nullptr;
  if (manager == nullptr) {
    return;  /* Window teardown owns any remaining holder. */
  }
  for (wmWindow &window : manager->windows) {
    if (&window != owner->window || window.winid != owner->window_id) {
      continue;
    }
    Owner *holder = nullptr;
    {
      const std::lock_guard lock(owner->mutex);
      for (wmEventHandler &base : window.runtime->modalhandlers) {
        if (&base != reinterpret_cast<wmEventHandler *>(owner->handler)) {
          continue;
        }
        wmEventHandler_UI *handler = owner->handler;
        if (handler->handle_fn != handle_update_quit || handler->remove_fn != handler_free ||
            handler->user_data != owner->handler_data)
        {
          return;
        }
        holder = static_cast<Owner *>(owner->handler_data);
        /* Postponed sweep frees only the wmEventHandler, not its remove_fn/data.
         * Retire the holder exactly once NOW, then neutralize teardown callbacks. */
        WM_event_remove_ui_handler(&window.runtime->modalhandlers,
                                   handle_update_quit, handler_free, holder, true);
        handler->handle_fn = retired_handler;
        handler->remove_fn = nullptr;
        handler->user_data = nullptr;
        owner->handler = nullptr;
        owner->handler_data = nullptr;
        break;
      }
    }
    if (holder != nullptr) {
      MEM_delete(holder);
      WM_event_add_mousemove(&window);  /* Existing loop performs the postponed unlink/free. */
    }
    return;
  }
}

static int handle_update_quit(bContext *C, const wmEvent * /*event*/, void *data)
{
  Owner owner = *static_cast<Owner *>(data);
  bool pending;
  {
    const std::lock_guard lock(owner->mutex);
    pending = owner->phase == Phase::Pending && owner->descriptor >= 0;
  }
  if (!pending) {
    retire_handler(C, owner);
    return WM_UI_HANDLER_CONTINUE;
  }
  /* The method is fixed private updater code, never a caller-provided callable.
   * Finish all Python callbacks and reference releases before committing exit. */
  const PyGILState_STATE gil = PyGILState_Ensure();
  PyObject *module = PyImport_ImportModule("angelo_agent.update_restart");
  PyObject *result = module ?
                         PyObject_CallMethod(module, "_confirm_installer_quit", "s", owner->nonce.data()) :
                         nullptr;
  const bool confirmed = result == Py_True;
  Py_XDECREF(result);
  Py_XDECREF(module);
  if (PyErr_Occurred()) {
    PyErr_Clear();
    PySys_WriteStderr("Angelo update: quit preparation failed; no install authorization\n");
  }
  PyGILState_Release(gil);
  bool window_live = false;
  if (wmWindowManager *manager = CTX_wm_manager(C)) {
    for (wmWindow &window : manager->windows) {
      if (&window == owner->window && window.winid == owner->window_id &&
          CTX_wm_window(C) == &window)
      {
        window_live = true;
        break;
      }
    }
  }
  if (!confirmed || !window_live || owner->catalog_generation == 0 ||
      asset_system::catalog_edit_generation() != owner->catalog_generation)
  {
    owner->revoke();
    retire_handler(C, owner);  /* Python may already have freed/replaced the original handler. */
    return WM_UI_HANDLER_CONTINUE;
  }

  /* Move the descriptor and frame to native-only exit storage. Window-handler
   * removal and Python finalization can no longer close this committed channel. */
  std::unique_lock lock(owner->mutex);
  if (owner->phase != Phase::Pending) {
    lock.unlock();
    retire_handler(C, owner);
    return WM_UI_HANDLER_CONTINUE;
  }
  const int descriptor = owner->descriptor;
  const std::array<char, 96> frame = owner->frame;
  const int frame_size = owner->frame_size;
  owner->descriptor = -1;
  owner->phase = Phase::Committing;
  lock.unlock();
  WM_exit_with_update_grant(C, descriptor, frame.data(), frame_size);
}
}  // namespace

PyObject *BPY_angelo_update_quit_protocol(PyObject *, PyObject *)
{
  return PyLong_FromLong(2);
}

PyObject *BPY_angelo_update_quit_catalog_generation(PyObject *, PyObject *)
{
  return PyLong_FromUnsignedLongLong(asset_system::catalog_edit_generation());
}

PyObject *BPY_angelo_update_quit_request(PyObject *, PyObject *args)
{
  int borrowed;
  PyObject *catalog_generation_object;
  PyObject *nonce_object;
  if (!BLI_thread_is_main() || G.background) {
    PyErr_SetString(PyExc_RuntimeError, "Updater quit needs the interactive main thread");
    return nullptr;
  }
  if (!PyArg_ParseTuple(args, "iUO:_update_quit_request", &borrowed, &nonce_object, &catalog_generation_object)) {
    return nullptr;
  }
  if (!PyLong_CheckExact(catalog_generation_object)) {
    PyErr_SetString(PyExc_TypeError, "Catalog generation must be an exact unsigned integer");
    return nullptr;
  }
  const uint64_t catalog_generation = PyLong_AsUnsignedLongLong(catalog_generation_object);
  if (PyErr_Occurred()) {
    return nullptr;
  }
  if (catalog_generation == 0 ||
      asset_system::catalog_edit_generation() != catalog_generation)
  {
    PyErr_SetString(PyExc_RuntimeError, "Catalogs changed while preparing restart");
    return nullptr;
  }
  Py_ssize_t size;
  const char *nonce = PyUnicode_AsUTF8AndSize(nonce_object, &size);
  if (nonce == nullptr) {
    return nullptr;
  }
  if (size != 32 || borrowed < 0) {
    PyErr_SetString(PyExc_ValueError, "Invalid updater invocation or channel");
    return nullptr;
  }
  for (int i = 0; i < 32; i++) {
    if (!((nonce[i] >= '0' && nonce[i] <= '9') || (nonce[i] >= 'a' && nonce[i] <= 'f'))) {
      PyErr_SetString(PyExc_ValueError, "Invalid updater invocation");
      return nullptr;
    }
  }
  bContext *C = BPY_context_get();
  wmWindow *window = C ? CTX_wm_window(C) : nullptr;
  if (window == nullptr || !valid_pipe(borrowed)) {
    PyErr_SetString(PyExc_RuntimeError, "Updater channel or window is unavailable");
    return nullptr;
  }

  /* Allocate every Python/holder object before acquiring the native descriptor.
   * No return-object allocation occurs after posting the handler. */
  Owner owner = std::make_shared<UpdateQuit>();
  owner->catalog_generation = catalog_generation;
  Owner *capsule_owner = MEM_new<Owner>(__func__, owner);
  PyObject *capsule = PyCapsule_New(capsule_owner, capsule_name, capsule_free);
  if (capsule == nullptr) {
    MEM_delete(capsule_owner);
    return nullptr;
  }
  Owner *handler_owner = MEM_new<Owner>(__func__, owner);
#ifdef _WIN32
#  if defined(_MSC_VER)
  {
    const CRTDescriptorGuard guard;
    owner->descriptor = _dup(borrowed);
  }
#  else
  owner->revoke();
#  endif
  if (owner->descriptor >= 0) {
#  if defined(_MSC_VER)
    const CRTDescriptorGuard guard;
    if (!SetHandleInformation(reinterpret_cast<HANDLE>(_get_osfhandle(owner->descriptor)),
                              HANDLE_FLAG_INHERIT,
                              0))
    {
      owner->revoke();
    }
#  endif
  }
#else
  owner->descriptor = dup(borrowed);
  if (owner->descriptor >= 0 && fcntl(owner->descriptor, F_SETFD, FD_CLOEXEC) < 0) {
    owner->revoke();
  }
#endif
  if (owner->descriptor < 0 || owner->phase == Phase::Revoked || !valid_pipe(owner->descriptor)) {
    owner->revoke();
    MEM_delete(handler_owner);
    return capsule;  /* Exact rejected owner remains available for checked cleanup. */
  }
  memcpy(owner->nonce.data(), nonce, 32);
  memcpy(owner->frame.data(), frame_prefix, sizeof(frame_prefix) - 1);
  memcpy(owner->frame.data() + sizeof(frame_prefix) - 1, nonce, 32);
  owner->frame_size = int(sizeof(frame_prefix) - 1) + 32;
  owner->frame[owner->frame_size++] = '\n';
  owner->window = window;
  owner->window_id = window->winid;
  owner->handler_data = handler_owner;
  owner->handler = WM_event_add_ui_handler(nullptr,  /* Window-only owner, no area/region binding. */
                         &window->runtime->modalhandlers,
                         handle_update_quit,
                         handler_free,
                         handler_owner,
                         eWM_EventHandlerFlag(0));
  WM_event_add_mousemove(window);
  return capsule;
}

PyObject *BPY_angelo_update_quit_revoke(PyObject *, PyObject *capsule)
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Updater revocation needs the main thread");
    return nullptr;
  }
  Owner *owner = owner_pointer(capsule);
  if (owner == nullptr) {
    return nullptr;
  }
  Owner retained = *owner;
  retained->revoke();
  retire_handler(BPY_context_get(), retained);
  const std::lock_guard lock(retained->mutex);
  return PyBool_FromLong((*owner)->phase == Phase::Revoked && (*owner)->descriptor < 0);
}

PyObject *BPY_angelo_update_quit_status(PyObject *, PyObject *capsule)
{
  if (!BLI_thread_is_main()) {
    PyErr_SetString(PyExc_RuntimeError, "Updater status needs the main thread");
    return nullptr;
  }
  Owner *owner = owner_pointer(capsule);
  if (owner == nullptr) {
    return nullptr;
  }
  Phase current;
  bool closed;
  {
    const std::lock_guard lock((*owner)->mutex);
    current = (*owner)->phase;
    closed = (*owner)->descriptor < 0;
  }
  const char *phase = current == Phase::Pending ? "PENDING" :
                      current == Phase::Revoked ? "REVOKED" : "COMMITTING";
  return Py_BuildValue("{s:i,s:s,s:O}",
                       "schema", 1,
                       "phase", phase,
                       "channel_closed", closed ? Py_True : Py_False);
}
}  // namespace blender
