/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The descriptor and handle numbers confined code was handed (break-it-confine-reparse head-12 read, the
 * coordinator's ruling: track, never refuse the script's own; head-16 read MUST-1/2: own the number, not the object).
 * A pipe, socket, terminal or event can't be judged by a path, and a number can be guessed: Angelo's stdio to its
 * agent runtime, its IPC pipes, anything open before the region. So a confined thread uses one with no file, and
 * closes, replaces, moves or locks any one, only while that exact number is one a maker handed to confined code and
 * nothing has ended or passed it on since: `os.close`, a `dup2` onto it and `FileIO.close` (which `io`'s finalizers
 * call too) drop it, a socket's is the script's only while that socket object lives and still holds it, and Windows'
 * `CloseHandle` drops a handle. An object's identity (POSIX `st_dev`/`st_ino`, Windows the handle's type) is kept only
 * as a second check: macOS reuses a pipe's `st_ino` at once and Linux gives every eventfd, timerfd and epoll one
 * inode, so it never proves anything alone. Nothing is held open (a duplicate would keep a pipe from ever reaching
 * its end). The numbers are dropped when the first region begins and when the last ends.
 *
 * Python's own streams (`print`, `sys.stdout`, `logging` to the console) write in C, not through these entry points:
 * they keep working, while a raw `os.write(1, ...)` into a pipe Angelo holds is refused.
 */

#include <Python.h>

#include <cstdint>
#include <atomic>
#include <cstring>
#include <mutex>
#include <unordered_map>
#include <utility>
#include <vector>

#ifdef _WIN32
#  include <io.h>
#  include <windows.h>
#else
#  include <sys/stat.h>
#endif

#include "BLI_fileops.hh"

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_made.hh"
#include "bpy_angelo_guard_confine_remote.hh"

namespace blender::angelo_guard {

namespace {

struct Owned {
  uint64_t first = 0, second = 0; /* POSIX: st_dev, st_ino. Windows: the handle's type. */
  PyObject *holder = nullptr;     /* A weak reference to the socket object that holds the number, or null. */
};

std::mutex owned_lock;
std::unordered_map<intptr_t, Owned> owned_descriptors; /* POSIX descriptors, and Windows C runtime descriptors. */
std::unordered_map<intptr_t, Owned> owned_handles;     /* Windows handles (a socket's number is one). */
/* Whether either map may hold a number: set before one is added (only while confined), cleared when both are emptied
 * at the last region's end. False, a close or a detach has nothing to forget and passes straight on, so between
 * scripts Angelo's own closes cost what they did before (Ruling 17). */
std::atomic<bool> anything_owned{false};
/* Strict script protection as the run found it (the first region's start): numbers are tracked and enforced for the
 * whole run or not at all, so turning the switch on mid-run never refuses a pipe or socket the run made before it
 * (the preference's review, S10). The next run takes the new value. */
std::atomic<bool> tracked_this_run{false};

/** What `number` refers to now, for the second check; false when it is not open. */
bool identity_of(const intptr_t number, const bool handle, Owned &r_owned)
{
#ifdef _WIN32
  intptr_t raw = number;
  if (!handle) {
    const int fd = int(number);
    if (!BLI_file_descriptor_is_open(fd)) {
      return false; /* The C runtime ends the process on a closed descriptor. */
    }
    raw = _get_osfhandle(fd);
  }
  DWORD flags = 0;
  if (raw == 0 || raw == -1 || !GetHandleInformation(HANDLE(raw), &flags)) {
    return false;
  }
  r_owned.first = uint64_t(GetFileType(HANDLE(raw)));
  r_owned.second = handle ? 0 : uint64_t(raw); /* A descriptor's handle stays its own while it is open. */
  return true;
#else
  (void)handle;
  struct stat info;
  if (number < 0 || fstat(int(number), &info) != 0) {
    return false;
  }
  r_owned.first = uint64_t(info.st_dev);
  r_owned.second = uint64_t(info.st_ino);
  return true;
#endif
}

void own(std::unordered_map<intptr_t, Owned> &numbers, const intptr_t number, const bool handle, PyObject *holder)
{
  Owned owned;
  /* Numbers are tracked with Strict script protection only (Head 20 read R-4, Ruling 17): off, nothing is owned and
   * the descriptors are judged as in 2.0.1. */
  if (!ownership_tracked() || !confined_now() || !identity_of(number, handle, owned)) {
    return;
  }
  owned.holder = holder ? PyWeakref_NewRef(holder, nullptr) : nullptr;
  PyErr_Clear();
  std::lock_guard<std::mutex> hold(owned_lock);
  anything_owned.store(true); /* Before the number is in a map: a reader that sees false has nothing to forget. */
  auto found = numbers.find(number);
  if (found != numbers.end()) {
    Py_XDECREF(found->second.holder);
    numbers.erase(found);
  }
  numbers.emplace(number, owned);
}

/** Whether the socket object `holder` names (a weak reference) is alive and still holds `number`. */
bool held_by(PyObject *holder, const intptr_t number)
{
  PyObject *object = nullptr;
  if (PyWeakref_GetRef(holder, &object) != 1) {
    PyErr_Clear();
    return false;
  }
  PyObject *fileno = PyObject_CallMethod(object, "fileno", nullptr);
  Py_DECREF(object);
  const bool held = fileno != nullptr && PyLong_Check(fileno) && intptr_t(PyLong_AsVoidPtr(fileno)) == number;
  Py_XDECREF(fileno);
  PyErr_Clear();
  return held;
}

void forget(std::unordered_map<intptr_t, Owned> &numbers, const intptr_t number)
{
  if (!anything_owned.load()) {
    return;
  }
  PyObject *holder = nullptr;
  {
    std::lock_guard<std::mutex> hold(owned_lock);
    const auto found = numbers.find(number);
    if (found == numbers.end()) {
      return;
    }
    holder = found->second.holder;
    numbers.erase(found);
  }
  Py_XDECREF(holder);
}

bool owns(std::unordered_map<intptr_t, Owned> &numbers, const intptr_t number, const bool handle)
{
  if (!anything_owned.load()) {
    return false;
  }
  Owned now;
  if (!identity_of(number, handle, now)) {
    return false;
  }
  PyObject *holder = nullptr;
  {
    std::lock_guard<std::mutex> hold(owned_lock);
    const auto found = numbers.find(number);
    if (found == numbers.end() || found->second.first != now.first || found->second.second != now.second) {
      return false; /* Never handed out, or something else is at that number now. */
    }
    holder = Py_XNewRef(found->second.holder);
  }
  const bool held = holder == nullptr || held_by(holder, number);
  Py_XDECREF(holder);
  if (!held) {
    forget(numbers, number); /* Its socket closed or detached it: no longer the script's. */
  }
  return held;
}

}  // namespace

void note_made_descriptor(const int fd)
{
  own(owned_descriptors, fd, false, nullptr);
}

bool made_descriptor(const int fd)
{
  return owns(owned_descriptors, fd, false);
}

void note_made_handle(const intptr_t handle)
{
#ifdef _WIN32
  own(owned_handles, handle, true, nullptr);
#else
  (void)handle;
#endif
}

bool made_handle(const intptr_t handle)
{
#ifdef _WIN32
  return owns(owned_handles, handle, true);
#else
  (void)handle;
  return false;
#endif
}

intptr_t descriptor_handle(const int fd)
{
#ifdef _WIN32
  if (fd >= 0 && BLI_file_descriptor_is_open(fd)) { /* The C runtime ends the process on a closed descriptor. */
    return intptr_t(_get_osfhandle(fd));
  }
#else
  (void)fd;
#endif
  return -1;
}

void forget_descriptor(const int fd)
{
  if (!anything_owned.load()) {
    return;
  }
  /* Windows: the handle under it closes with it, so it is the script's no longer either (a handle it made into this
   * descriptor, msvcrt.open_osfhandle; Head 20 read residual). Read while the descriptor is still open. */
  if (const intptr_t handle = descriptor_handle(fd); handle != -1) {
    forget(owned_handles, handle);
  }
  forget(owned_descriptors, fd);
}

void forget_handle(const intptr_t handle)
{
  forget(owned_handles, handle);
}

bool descriptor_is_open(const int fd)
{
  Owned owned;
  return identity_of(fd, false, owned);
}

bool handle_is_open(const intptr_t handle)
{
#ifdef _WIN32
  Owned owned;
  return identity_of(handle, true, owned);
#else
  (void)handle;
  return false;
#endif
}

bool ownership_tracked()
{
  return tracked_this_run.load(std::memory_order_relaxed);
}

void ownership_run_began()
{
  tracked_this_run.store(strict_lookups(), std::memory_order_relaxed);
}

void made_objects_forget()
{
  std::vector<PyObject *> holders;
  {
    std::lock_guard<std::mutex> hold(owned_lock);
    for (auto *numbers : {&owned_descriptors, &owned_handles}) {
      for (auto &[number, owned] : *numbers) {
        if (owned.holder) {
          holders.push_back(owned.holder);
        }
      }
      numbers->clear();
    }
    anything_owned.store(false); /* Under the lock: an `own` after this sets it again before adding. */
  }
  for (PyObject *holder : holders) {
    Py_DECREF(holder);
  }
}

/* -------------------------------------------------------------------- */
/** \name What makes one (`os.pipe`, `_winapi.CreatePipe`, ...)
 *
 * Each is called as it is; while confined, the descriptors or handles it returns (an int, or a tuple of them) are
 * noted. `os.dup` and `DuplicateHandle` make a second number for an object already judged by their argument (the
 * descriptor and handle judges, wrap_io.cc): POSIX follows the object anyway; Windows notes the new handle.
 * \{ */

namespace {

enum Gives { DESCRIPTORS, HANDLES };

struct MakerWrap {
  const char *module;
  const char *name;
  Gives gives;
  PyObject *original;           /* What the maker calls: what stood there when it was installed. */
  PyObject *cpython = nullptr;  /* CPython's own, from the first look: kept for the life of the process. */
};

MakerWrap maker_wraps[] = {
    {"posix", "pipe", DESCRIPTORS, nullptr},          {"posix", "pipe2", DESCRIPTORS, nullptr},
    {"posix", "openpty", DESCRIPTORS, nullptr},       {"posix", "eventfd", DESCRIPTORS, nullptr},
    {"posix", "posix_openpt", DESCRIPTORS, nullptr},  {"posix", "timerfd_create", DESCRIPTORS, nullptr},
    {"posix", "memfd_create", DESCRIPTORS, nullptr},  {"nt", "pipe", DESCRIPTORS, nullptr},
    {"_winapi", "CreatePipe", HANDLES, nullptr},      {"_winapi", "CreateNamedPipe", HANDLES, nullptr},
    {"_winapi", "CreateFile", HANDLES, nullptr},      {"_winapi", "CreateEventW", HANDLES, nullptr},
    {"_winapi", "CreateMutexW", HANDLES, nullptr},    {"_winapi", "OpenEventW", HANDLES, nullptr},
    {"_winapi", "OpenMutexW", HANDLES, nullptr},      {"_winapi", "DuplicateHandle", HANDLES, nullptr},
    {"_overlapped", "CreateEvent", HANDLES, nullptr}, {"_overlapped", "CreateIoCompletionPort", HANDLES, nullptr},
    {"msvcrt", "get_osfhandle", HANDLES, nullptr},    {"msvcrt", "open_osfhandle", DESCRIPTORS, nullptr},
    /* A file the script opened by name (judged as it opened): its own number to close, seek or lock. */
    {"posix", "open", DESCRIPTORS, nullptr},          {"nt", "open", DESCRIPTORS, nullptr},
    {"posix", "dup", DESCRIPTORS, nullptr},           {"nt", "dup", DESCRIPTORS, nullptr},
};
constexpr size_t maker_count = sizeof(maker_wraps) / sizeof(maker_wraps[0]);
PyMethodDef maker_definitions[maker_count];

void note_made(PyObject *value, const Gives gives)
{
  if (PyLong_Check(value)) {
    if (gives == DESCRIPTORS) {
      const long fd = PyLong_AsLong(value);
      if (!PyErr_Occurred()) {
        note_made_descriptor(int(fd));
      }
    }
    else {
      const intptr_t handle = intptr_t(PyLong_AsVoidPtr(value));
      if (!PyErr_Occurred()) {
        note_made_handle(handle);
      }
    }
    PyErr_Clear();
  }
  else if (PyTuple_Check(value)) {
    for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(value); i++) {
      note_made(PyTuple_GET_ITEM(value, i), gives);
    }
  }
}

PyObject *maker_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const MakerWrap &wrap = maker_wraps[PyLong_AsSsize_t(index)];
  PyObject *result = PyObject_Call(wrap.original, args, kwargs);
  if (result != nullptr && ownership_tracked() && confined_now()) { /* The latch first: a kept copy costs nothing. */
    note_made(result, wrap.gives);
    PyObject *first = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
    if (strcmp(wrap.name, "open_osfhandle") == 0 && first != nullptr && PyLong_Check(first)) {
      /* The handle is the descriptor's now: owned by that number only, so closing the descriptor ends it (Head 20
       * read residual: the handle stayed owned after the close, and Angelo's next pipe could reuse its value). */
      forget_handle(intptr_t(PyLong_AsVoidPtr(first)));
      PyErr_Clear();
    }
  }
  return result;
}

}  // namespace

void note_made_socket_pair(PyObject *pair)
{
  if (pair == nullptr || !PyTuple_Check(pair) || !confined_now()) {
    return;
  }
  for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(pair); i++) {
    PyObject *socket = PyTuple_GET_ITEM(pair, i);
    PyObject *number = PyObject_CallMethod(socket, "fileno", nullptr);
    if (number != nullptr && PyLong_Check(number)) {
      /* The script's only while this socket object lives and still holds the number (closed or detached: no). */
#ifdef _WIN32
      own(owned_handles, intptr_t(PyLong_AsVoidPtr(number)), true, socket); /* A socket's number is its handle. */
#else
      own(owned_descriptors, intptr_t(PyLong_AsLong(number)), false, socket);
#endif
    }
    Py_XDECREF(number);
    PyErr_Clear();
  }
}

/* `FileIO.close` (what every `io` object's close and finalizer reach): the number it closes is the script's no longer,
 * so a later open of Angelo's that reuses it is not. A FileIO that doesn't own its number (`closefd=False`) closes
 * nothing. */
PyObject *original_fileio_close = nullptr;

static PyObject *fileio_close(PyObject *self, PyObject * /*args*/)
{
  if (!anything_owned.load()) {
    return PyObject_CallOneArg(original_fileio_close, self); /* No script's number to forget (Ruling 17). */
  }
  int fd = -1;
  PyObject *closefd = PyObject_GetAttrString(self, "closefd");
  if (closefd != nullptr && PyObject_IsTrue(closefd) == 1) {
    PyObject *number = PyObject_CallMethod(self, "fileno", nullptr); /* Raises when closed already. */
    fd = number && PyLong_Check(number) ? int(PyLong_AsLong(number)) : -1;
    Py_XDECREF(number);
  }
  Py_XDECREF(closefd);
  PyErr_Clear();
  if (fd >= 0) {
    forget_descriptor(fd);
  }
  return PyObject_CallOneArg(original_fileio_close, self);
}

static PyMethodDef fileio_close_definition = {"close", (PyCFunction)fileio_close, METH_NOARGS, nullptr};

/** `module_name.type_name.name` replaced by `definition`, for Strict script protection only (R-4): where it still holds
 * CPython's first original (kept in `*original`), never over what something else put there. */
static void install_method(const char *module_name,
                           const char *type_name,
                           const char *name,
                           PyMethodDef *definition,
                           PyObject **original)
{
  PyObject *module = PyImport_ImportModule(module_name);
  PyObject *type = module ? PyObject_GetAttrString(module, type_name) : nullptr;
  Py_XDECREF(module);
  PyErr_Clear();
  PyObject *names = type && PyType_Check(type) ? ((PyTypeObject *)type)->tp_dict : nullptr;
  PyObject *raw = names ? PyDict_GetItemString(names, name) : nullptr; /* Borrowed. */
  if (raw != nullptr && (*original == nullptr || raw == *original)) {
    if (*original == nullptr) {
      *original = Py_NewRef(raw); /* Kept for the life of the process. */
    }
    if (PyObject *guarded = PyDescr_NewMethod((PyTypeObject *)type, definition)) {
      note_install_site(module_name); /* Never made again while confined (wrap_owners.cc). */
      set_method_strict((PyTypeObject *)type, name, guarded, *original);
      Py_DECREF(guarded);
    }
  }
  Py_XDECREF(type);
  PyErr_Clear();
}

/* `socket.detach()`: the socket gives its number up without closing it, to whatever Python code took it
 * (multiprocessing's duplex Pipe makes a Connection of each socketpair end this way): the number stays the script's,
 * as a bare number, and that code's close is seen (os.close). */
PyObject *original_socket_detach = nullptr;

static PyObject *socket_detach(PyObject *self, PyObject * /*args*/)
{
  if (!anything_owned.load()) {
    return PyObject_CallOneArg(original_socket_detach, self);
  }
  PyObject *number = PyObject_CallMethod(self, "fileno", nullptr);
  const intptr_t value = number && PyLong_Check(number) ? intptr_t(PyLong_AsVoidPtr(number)) : -1;
  Py_XDECREF(number);
  PyErr_Clear();
  PyObject *result = PyObject_CallOneArg(original_socket_detach, self);
  if (result != nullptr && value >= 0) {
    std::lock_guard<std::mutex> hold(owned_lock);
#ifdef _WIN32
    auto &numbers = owned_handles;
#else
    auto &numbers = owned_descriptors;
#endif
    const auto found = numbers.find(value);
    if (found != numbers.end() && found->second.holder != nullptr) {
      Py_CLEAR(found->second.holder); /* Owned bare from now on. */
    }
  }
  return result;
}

static PyMethodDef socket_detach_definition = {"detach", (PyCFunction)socket_detach, METH_NOARGS, nullptr};


void install_strict_makers()
{
  install_method("_io", "FileIO", "close", &fileio_close_definition, &original_fileio_close);
  install_method("_socket", "socket", "detach", &socket_detach_definition, &original_socket_detach);
  for (size_t i = 0; i < maker_count; i++) {
    MakerWrap &wrap = maker_wraps[i];
    PyObject *module = PyImport_ImportModule(wrap.module);
    PyObject *current = module ? PyObject_GetAttrString(module, wrap.name) : nullptr;
    Py_XDECREF(module);
    PyErr_Clear();
    if (current == nullptr) {
      continue; /* Not on this system. */
    }
    if (wrap.cpython == nullptr) {
      wrap.cpython = Py_NewRef(current);
    }
    /* Over CPython's own, or over a wrapper this switch installed just before it (`os.dup`'s descriptor wrap: the
     * maker notes what the judged call made); never over a foreign one (a profiler's: no recursion), and never twice
     * (its own maker wrapper standing there is the switch's too, but the maker is never its own original). */
    const bool own_maker = PyCFunction_Check(current) &&
                           PyCFunction_GET_FUNCTION(current) == (PyCFunction)(void (*)(void))maker_call;
    if ((current != wrap.cpython && !installed_by_strict(current)) || own_maker) {
      Py_DECREF(current);
      continue;
    }
    Py_XSETREF(wrap.original, current); /* What it calls: CPython's, or the judged wrapper beneath it. */
    maker_definitions[i] = {wrap.name, (PyCFunction)(void (*)(void))maker_call, METH_VARARGS | METH_KEYWORDS,
                            nullptr};
    PyObject *index = PyLong_FromSize_t(i);
    PyObject *wrapper = index ? PyCFunction_NewEx(&maker_definitions[i], index, nullptr) : nullptr;
    Py_XDECREF(index);
    if (wrapper != nullptr) {
      put_in_strict(wrap.module, wrap.name, wrapper, wrap.original);
      put_in_strict("os", wrap.name, wrapper, wrap.original);
    }
    Py_XDECREF(wrapper);
  }
}

void install_makers()
{
  if (strict_lookups()) {
    install_strict_makers(); /* Strict script protection already on (else when it turns on: wrap_lookup.cc). */
  }
}

/** \} */

}  // namespace blender::angelo_guard
