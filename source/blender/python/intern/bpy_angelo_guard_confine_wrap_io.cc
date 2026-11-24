/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * More entry points no audit event covers, replaced by C functions whose originals stay in C
 * (doc/angelo/script_confinement.rst; bpy_angelo_guard_confine_wrap.cc has the first set):
 *
 * - `builtins.compile`: code it makes while confined is the agent's, by object (provenance, not names);
 * - `builtins.__build_class__`: a class statement with the script's body makes the script's class;
 * - `_imp._fix_co_filename`: renames code; only importlib's own bytecode loader may, while confined;
 * - the descriptor I/O of `os` (`read`, `write`, `pread`, `sendfile`, `splice`, `ftruncate`, …): a descriptor
 *   Angelo holds open on a private file or a socket is neither read, written nor sent on by a confined thread;
 * - `sys.monitoring`'s tools and callbacks: a confined thread cannot leave a callback the interpreter calls later;
 * - `socket.socketpair`: a connected pair inside this process (asyncio's event loop wakes itself through one) is
 *   allowed, with the socket events it raises itself (on Windows it pairs through the loopback); no other socket;
 * - native file entry points and SQLite: bpy_angelo_guard_confine_wrap_native.cc.
 */

#include <Python.h>

#include <cstdint>
#include <cstring>
#include <algorithm>
#include <optional>
#include <string>
#include <vector>

#include <sys/stat.h>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_made.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_remote.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name A table of originals, called by index (the wrapper's `__self__` is that index: nothing to call)
 * \{ */

struct IoWrap {
  const char *modules[2]; /* Where the name lives (os's are posix's or nt's). */
  const char *name;
  signed char reads[2];  /* Positions of descriptors it reads (-1: none). */
  signed char writes[2]; /* Positions of descriptors it writes or changes. */
  PyObject *original;
  /* Positions of descriptors it closes, replaces, moves or locks: only one the script owns (a number a maker handed
   * to confined code, bpy_angelo_guard_confine_made), or else one whose file it may write (head-16 read SHOULD-3). */
  signed char changes[2] = {-1, -1};
  /* Only the number-ownership layer needs it (head-16 read): installed only with Strict script protection on
   * (`strict_lookups`; Head 20 read R-4, Ruling 17). Off, the descriptors are judged as in 2.0.1. */
  bool ownership = false;
};

static IoWrap io_wraps[] = {
    {{"posix", "nt"}, "read", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "readv", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "pread", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "preadv", {0, -1}, {-1, -1}, nullptr},
    {{"posix", "nt"}, "write", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "writev", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "pwrite", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "pwritev", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "sendfile", {1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "splice", {0, -1}, {1, -1}, nullptr},
    {{"posix", "nt"}, "copy_file_range", {0, -1}, {1, -1}, nullptr},
    {{"posix", "nt"}, "ftruncate", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "fchmod", {-1, -1}, {0, -1}, nullptr},
    {{"posix", "nt"}, "fchown", {-1, -1}, {0, -1}, nullptr},
    /* break-it-confine-reparse head-12 read, private-file class: macOS's descriptor copy (shutil's fast path), a
     * file's space reserved; and (head-16 read) what closes, replaces, moves or locks a descriptor, or changes how
     * another holder reads it (its position, blocking, whether a program inherits it): only the script's own number,
     * or one whose file it may write; a closed or replaced number is the script's no longer (io_call). */
    {{"posix", nullptr}, "_fcopyfile", {0, -1}, {1, -1}, nullptr},
    {{"posix", nullptr}, "posix_fallocate", {-1, -1}, {0, -1}, nullptr},
    /* Its copy is the script's (made.cc): judged first. */
    {{"posix", "nt"}, "dup", {0, -1}, {-1, -1}, nullptr, {-1, -1}, true},
    {{"posix", "nt"}, "dup2", {0, -1}, {-1, -1}, nullptr, {1, -1}, true},
    {{"posix", "nt"}, "close", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"posix", "nt"}, "lseek", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"posix", "nt"}, "set_blocking", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"posix", "nt"}, "set_inheritable", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"posix", nullptr}, "lockf", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"msvcrt", nullptr}, "setmode", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"msvcrt", nullptr}, "locking", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    /* An eventfd's or a timerfd's (Linux): no file, so only the script's own (head-16 read SHOULD-4). */
    {{"posix", nullptr}, "eventfd_read", {0, -1}, {-1, -1}, nullptr, {-1, -1}, true},
    {{"posix", nullptr}, "eventfd_write", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"posix", nullptr}, "timerfd_gettime", {0, -1}, {-1, -1}, nullptr, {-1, -1}, true},
    {{"posix", nullptr}, "timerfd_gettime_ns", {0, -1}, {-1, -1}, nullptr, {-1, -1}, true},
    {{"posix", nullptr}, "timerfd_settime", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
    {{"posix", nullptr}, "timerfd_settime_ns", {-1, -1}, {-1, -1}, nullptr, {0, -1}, true},
};

/** A descriptor's use by a confined thread: a socket never (raw sends are the network), a private file never, a
 * startup or tool-output file not for a change. */
static int judge_descriptor(PyObject *value, bool write)
{
  if (value == nullptr || !PyLong_Check(value)) {
    return 0;
  }
  const int fd = int(PyLong_AsLong(value));
  if (PyErr_Occurred()) {
    PyErr_Clear();
    return 0;
  }
#ifndef _WIN32
  struct stat info;
  if (fstat(fd, &info) == 0 && S_ISSOCK(info.st_mode) && !(ownership_tracked() && made_descriptor(fd))) {
    /* One of a pair the script made is its own: tracked with Strict script protection only (R-4). */
    return refuse(NETWORK, "a socket's descriptor");
  }
#endif
  const std::optional<std::string> path = descriptor_path(fd);
  if (!path) {
    /* No file (a pipe, a socket, a terminal, an event): only one confined code made (bpy_angelo_guard_confine_made):
     * never Angelo's own (its stdio to the agent runtime, its IPC pipes), which a number can name. Numbers are
     * tracked with Strict script protection only (Head 20 read R-4): off, a pipe or terminal passes, as in 2.0.1. */
    if (!ownership_tracked() || !descriptor_is_open(fd) || made_descriptor(fd)) {
      return 0;
    }
    return refuse(PRIVATE, "a descriptor the script didn't make (one of Angelo's pipes or streams)");
  }
  switch (folders().judge(*path, write)) {
    case Refusal::Private:
      return refuse(PRIVATE, path->c_str());
    case Refusal::Startup:
      return refuse(STARTUP, path->c_str());
    case Refusal::ToolOutput:
      return refuse(TOOL_OUTPUT, path->c_str());
    case Refusal::Network:
      return refuse(NETWORK, path->c_str());
    case Refusal::None:
      break;
  }
  return 0;
}

/** A descriptor a confined thread closes, replaces, moves or locks: its own number; or, unless `strict` (a close or a
 * dup2's target: a script closes or replaces only numbers handed to it), one whose file it may write. */
static int judge_change(PyObject *value, const bool strict)
{
  if (value == nullptr || !PyLong_Check(value)) {
    return 0;
  }
  const int fd = int(PyLong_AsLong(value));
  if (PyErr_Occurred()) {
    PyErr_Clear();
    return 0;
  }
  if (!descriptor_is_open(fd) || made_descriptor(fd)) {
    return 0; /* Not open (the original's own error), or the script's. */
  }
  if (strict || !descriptor_path(fd)) {
    return refuse(PRIVATE, "a descriptor the script didn't make (one of Angelo's pipes or streams)");
  }
  return judge_descriptor(value, true);
}

static int descriptor_argument(PyObject *args, const signed char position)
{
  if (position < 0 || position >= PyTuple_GET_SIZE(args) || !PyLong_Check(PyTuple_GET_ITEM(args, position))) {
    return -1;
  }
  const long fd = PyLong_AsLong(PyTuple_GET_ITEM(args, position));
  PyErr_Clear();
  return int(fd);
}

static PyObject *io_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const IoWrap &wrap = io_wraps[PyLong_AsSsize_t(index)];
  /* The ownership layer's (`wrap.ownership`): only with Strict script protection, the switch read first, so a copy
   * kept after it turned off costs nothing more (Head 20 read R-4). */
  if (wrap.ownership && !ownership_tracked()) {
    return PyObject_Call(wrap.original, args, kwargs);
  }
  if (confined_now()) {
    const bool strict = strcmp(wrap.name, "close") == 0 || strcmp(wrap.name, "dup2") == 0;
    for (const signed char position : wrap.changes) {
      if (position >= 0 && position < PyTuple_GET_SIZE(args) &&
          judge_change(PyTuple_GET_ITEM(args, position), strict) != 0)
      {
        return nullptr;
      }
    }
    for (const signed char position : wrap.reads) {
      if (position >= 0 && position < PyTuple_GET_SIZE(args) &&
          judge_descriptor(PyTuple_GET_ITEM(args, position), false) != 0)
      {
        return nullptr;
      }
    }
    for (const signed char position : wrap.writes) {
      if (position >= 0 && position < PyTuple_GET_SIZE(args) &&
          judge_descriptor(PyTuple_GET_ITEM(args, position), true) != 0)
      {
        return nullptr;
      }
    }
  }
  if (!wrap.ownership) {
    return PyObject_Call(wrap.original, args, kwargs); /* 2.0.1's descriptor judges: nothing to forget. */
  }
  /* A number closed or replaced is the script's no longer; the target of a dup2 of its own is (made.cc). */
  const bool closes = strcmp(wrap.name, "close") == 0, replaces = strcmp(wrap.name, "dup2") == 0;
  const int closed = closes ? descriptor_argument(args, 0) : replaces ? descriptor_argument(args, 1) : -1;
  const bool source_owned = replaces && made_descriptor(descriptor_argument(args, 0));
  const intptr_t closed_handle = closed >= 0 ? descriptor_handle(closed) : -1; /* Windows: read before it closes. */
  PyObject *result = PyObject_Call(wrap.original, args, kwargs);
  if (result != nullptr && closed >= 0) {
    if (closed_handle != -1) {
      forget_handle(closed_handle); /* The handle closed with it (Head 20 read residual). */
    }
    forget_descriptor(closed);
    if (source_owned) {
      note_made_descriptor(closed);
    }
  }
  return result;
}

static PyMethodDef io_definition = {
    "descriptor_io", (PyCFunction)(void (*)(void))io_call, METH_VARARGS | METH_KEYWORDS, nullptr};

/* Windows' raw handles by number (break-it-confine-reparse head-12 read): one of Angelo's own private files' handles
 * read, written, closed (a later open reuses the number), duplicated or bound to a completion port. A handle that
 * isn't a file's (a pipe, a process, an event) is passed; a file's whose name can't be told is refused. */
struct HandleWrap {
  const char *module;
  const char *name;
  signed char positions[2]; /* Of the handles. */
  bool write;
  PyObject *original;
};

static HandleWrap handle_wraps[] = {
    {"_winapi", "ReadFile", {0, -1}, false, nullptr},
    {"_winapi", "WriteFile", {0, -1}, true, nullptr},
    {"_winapi", "CloseHandle", {0, -1}, false, nullptr},
    {"_winapi", "DuplicateHandle", {1, -1}, false, nullptr},
    {"_winapi", "PeekNamedPipe", {0, -1}, false, nullptr},
    {"_winapi", "ConnectNamedPipe", {0, -1}, false, nullptr},
    {"_winapi", "SetNamedPipeHandleState", {0, -1}, false, nullptr},
    {"_winapi", "SetEvent", {0, -1}, true, nullptr},
    {"_winapi", "ResetEvent", {0, -1}, true, nullptr},
    {"_winapi", "ReleaseMutex", {0, -1}, true, nullptr},
    {"msvcrt", "open_osfhandle", {0, -1}, false, nullptr}, /* Its descriptor is then the script's (made.cc). */
    /* Whether a program Angelo starts inherits the handle: `set_inheritable`'s handle form, so as it is (a change,
     * only the script's own, or a file's it may write; build3 head 25b's inventory). os has nt's own (below). */
    {"nt", "set_handle_inheritable", {0, -1}, true, nullptr},
    {"_overlapped", "CreateIoCompletionPort", {0, 1}, false, nullptr},
    /* A completion port's completions taken or forged, an event set, a wait on an object (head-16 read SHOULD-4). */
    {"_overlapped", "GetQueuedCompletionStatus", {0, -1}, false, nullptr},
    {"_overlapped", "PostQueuedCompletionStatus", {0, -1}, true, nullptr},
    {"_overlapped", "SetEvent", {0, -1}, true, nullptr},
    {"_overlapped", "ResetEvent", {0, -1}, true, nullptr},
    {"_overlapped", "RegisterWaitWithQueue", {0, 1}, false, nullptr},
    {"_overlapped", "UnregisterWait", {0, -1}, false, nullptr},
    {"_overlapped", "UnregisterWaitEx", {0, 1}, false, nullptr},
};

static int judge_handle(PyObject *value, const bool write)
{
  if (value == nullptr || !PyLong_Check(value)) {
    return 0; /* Not a number: the original refuses it. */
  }
  const intptr_t handle = intptr_t(PyLong_AsVoidPtr(value));
  if (PyErr_Occurred()) {
    PyErr_Clear();
    return 0;
  }
  const std::optional<std::string> path = handle_path(handle);
  if (!path) {
    /* Not a file's handle (a pipe, a socket, an event, a process): only one confined code made, never one of
     * Angelo's by number (bpy_angelo_guard_confine_made). */
    if (!ownership_tracked() || !handle_is_open(handle) || made_handle(handle)) {
      return 0; /* Tracked with Strict script protection only (R-4). */
    }
    return refuse(PRIVATE, "a handle the script didn't make (one of Angelo's pipes or objects)");
  }
  if (path->empty()) {
    return refuse(PRIVATE, "a file handle whose file can't be told"); /* Fail closed. */
  }
  switch (folders().judge(*path, write)) {
    case Refusal::Private:
      return refuse(PRIVATE, path->c_str());
    case Refusal::Startup:
      return refuse(STARTUP, path->c_str());
    case Refusal::ToolOutput:
      return refuse(TOOL_OUTPUT, path->c_str());
    case Refusal::Network:
      return refuse(NETWORK, path->c_str());
    case Refusal::None:
      break;
  }
  return 0;
}

static PyObject *handle_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const HandleWrap &wrap = handle_wraps[PyLong_AsSsize_t(index)];
  if (!ownership_tracked()) {
    /* Untracked this run, or a copy kept after the switch turned off (R-4, S10). */
    return PyObject_Call(wrap.original, args, kwargs);
  }
  if (confined_now()) {
    for (const signed char position : wrap.positions) {
      if (position >= 0 && position < PyTuple_GET_SIZE(args) &&
          judge_handle(PyTuple_GET_ITEM(args, position), wrap.write) != 0)
      {
        return nullptr;
      }
    }
  }
  /* A handle closed is the script's no longer, nor a source DuplicateHandle closes (DUPLICATE_CLOSE_SOURCE). */
  PyObject *closed = nullptr;
  if (strcmp(wrap.name, "CloseHandle") == 0 && PyTuple_GET_SIZE(args) > 0) {
    closed = PyTuple_GET_ITEM(args, 0);
  }
  else if (strcmp(wrap.name, "DuplicateHandle") == 0 && PyTuple_GET_SIZE(args) > 5 &&
           (PyLong_AsUnsignedLongMask(PyTuple_GET_ITEM(args, 5)) & 0x1) != 0)
  {
    closed = PyTuple_GET_ITEM(args, 1);
  }
  PyErr_Clear();
  const intptr_t closed_handle = closed && PyLong_Check(closed) ? intptr_t(PyLong_AsVoidPtr(closed)) : 0;
  PyErr_Clear();
  PyObject *result = PyObject_Call(wrap.original, args, kwargs);
  if (result != nullptr && closed_handle != 0) {
    forget_handle(closed_handle);
  }
  return result;
}

static PyMethodDef handle_definition = {
    "handle_io", (PyCFunction)(void (*)(void))handle_call, METH_VARARGS | METH_KEYWORDS, nullptr};

/* `_overlapped.Overlapped`'s methods take a raw handle too (asyncio's proactor on its own pipes and sockets): each
 * handle they name is judged as a handle is (above), before the original method. `_overlapped` raises no audit
 * event, so a method that accepts, connects, sends to an address or makes a socket ready for reuse (DisconnectEx's
 * TF_REUSE_SOCKET) is the network itself: refused while confined (break-it-confine-reparse 36bcdd2 read MUST-3; a
 * confined asyncio loop reads and writes its own pipes, never these). */
struct OverlappedWrap {
  const char *name;
  signed char handles[2]; /* Positions of the handles (a file's, a socket's, a pipe's). */
  bool write;
  PyObject *original; /* The method descriptor. */
  bool network = false; /* Refused while confined, whatever its handles. */
};

static OverlappedWrap overlapped_wraps[] = {
    {"ReadFile", {0, -1}, false, nullptr},
    {"ReadFileInto", {0, -1}, false, nullptr},
    {"WSARecv", {0, -1}, false, nullptr},
    {"WSARecvInto", {0, -1}, false, nullptr},
    {"WSARecvFrom", {0, -1}, false, nullptr},
    {"WSARecvFromInto", {0, -1}, false, nullptr},
    {"WriteFile", {0, -1}, true, nullptr},
    {"WSASend", {0, -1}, true, nullptr},
    {"WSASendTo", {0, -1}, true, nullptr, true},
    {"AcceptEx", {0, 1}, false, nullptr, true},
    {"ConnectEx", {0, -1}, false, nullptr, true},
    {"DisconnectEx", {0, -1}, false, nullptr, true},
    {"TransmitFile", {0, 1}, false, nullptr},
    {"ConnectNamedPipe", {0, -1}, false, nullptr},
};
constexpr size_t overlapped_count = sizeof(overlapped_wraps) / sizeof(overlapped_wraps[0]);

static PyObject *overlapped_call(const size_t index, PyObject *self, PyObject *args, PyObject *kwargs)
{
  const OverlappedWrap &wrap = overlapped_wraps[index];
  if (confined_now() && wrap.network) {
    std::string what = std::string("_overlapped.Overlapped.") + wrap.name;
    refuse(NETWORK, what.c_str());
    return nullptr;
  }
  if (ownership_tracked() && confined_now()) { /* Handles: Strict script protection's, latched per run (R-4, S10). */
    for (const signed char position : wrap.handles) {
      if (position >= 0 && position < PyTuple_GET_SIZE(args) &&
          judge_handle(PyTuple_GET_ITEM(args, position), wrap.write) != 0)
      {
        return nullptr;
      }
    }
  }
  PyObject *bound = PyTuple_New(PyTuple_GET_SIZE(args) + 1); /* The original descriptor takes `self` first. */
  if (bound == nullptr) {
    return nullptr;
  }
  PyTuple_SET_ITEM(bound, 0, Py_NewRef(self));
  for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(args); i++) {
    PyTuple_SET_ITEM(bound, i + 1, Py_NewRef(PyTuple_GET_ITEM(args, i)));
  }
  PyObject *result = PyObject_Call(wrap.original, bound, kwargs);
  Py_DECREF(bound);
  return result;
}

template<int I> static PyObject *overlapped_method(PyObject *self, PyObject *args, PyObject *kwargs)
{
  return overlapped_call(size_t(I), self, args, kwargs);
}

static PyMethodDef overlapped_definitions[] = {
    {"ReadFile", (PyCFunction)(void (*)(void))overlapped_method<0>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"ReadFileInto", (PyCFunction)(void (*)(void))overlapped_method<1>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"WSARecv", (PyCFunction)(void (*)(void))overlapped_method<2>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"WSARecvInto", (PyCFunction)(void (*)(void))overlapped_method<3>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"WSARecvFrom", (PyCFunction)(void (*)(void))overlapped_method<4>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"WSARecvFromInto", (PyCFunction)(void (*)(void))overlapped_method<5>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"WriteFile", (PyCFunction)(void (*)(void))overlapped_method<6>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"WSASend", (PyCFunction)(void (*)(void))overlapped_method<7>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"WSASendTo", (PyCFunction)(void (*)(void))overlapped_method<8>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"AcceptEx", (PyCFunction)(void (*)(void))overlapped_method<9>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"ConnectEx", (PyCFunction)(void (*)(void))overlapped_method<10>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"DisconnectEx", (PyCFunction)(void (*)(void))overlapped_method<11>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"TransmitFile", (PyCFunction)(void (*)(void))overlapped_method<12>, METH_VARARGS | METH_KEYWORDS, nullptr},
    {"ConnectNamedPipe", (PyCFunction)(void (*)(void))overlapped_method<13>, METH_VARARGS | METH_KEYWORDS, nullptr},
};

/* `_overlapped`'s own functions that bind, connect or open a pipe by name (BindLocal, WSAConnect, ConnectPipe: no
 * audit event either; a pipe's name may be another machine's or Angelo's own): refused while confined (MUST-3). */
struct OverlappedRefused {
  const char *name;
  PyObject *original;
};
static OverlappedRefused overlapped_refused[] = {{"BindLocal", nullptr}, {"WSAConnect", nullptr}, {"ConnectPipe", nullptr}};
constexpr size_t overlapped_refused_count = sizeof(overlapped_refused) / sizeof(overlapped_refused[0]);
static PyMethodDef overlapped_refused_definitions[overlapped_refused_count];

static PyObject *overlapped_refused_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const OverlappedRefused &wrap = overlapped_refused[PyLong_AsSsize_t(index)];
  if (confined_now()) {
    std::string what = std::string("_overlapped.") + wrap.name;
    refuse(NETWORK, what.c_str());
    return nullptr;
  }
  return PyObject_Call(wrap.original, args, kwargs);
}

/** `_overlapped`'s: the network refusals always (`strict_part` false); the handle judges of the other methods only with
 * Strict script protection (R-4). */
static void install_overlapped_methods(const bool strict_part)
{
  for (size_t i = 0; !strict_part && i < overlapped_refused_count; i++) {
    OverlappedRefused &wrap = overlapped_refused[i];
    PyObject *module = wrap.original == nullptr ? PyImport_ImportModule("_overlapped") : nullptr;
    PyObject *current = module ? PyObject_GetAttrString(module, wrap.name) : nullptr;
    Py_XDECREF(module);
    PyErr_Clear();
    if (current == nullptr) {
      continue; /* Not on this system (no `_overlapped` but on Windows). */
    }
    overlapped_refused_definitions[i] = {wrap.name, (PyCFunction)(void (*)(void))overlapped_refused_call,
                                         METH_VARARGS | METH_KEYWORDS, nullptr};
    wrap.original = current; /* Kept for the life of the process. */
    PyObject *index = PyLong_FromSize_t(i);
    PyObject *wrapper = index ? PyCFunction_NewEx(&overlapped_refused_definitions[i], index, nullptr) : nullptr;
    Py_XDECREF(index);
    if (wrapper != nullptr) {
      put_in("_overlapped", wrap.name, wrapper, current);
    }
    Py_XDECREF(wrapper);
  }
  PyObject *module = PyImport_ImportModule("_overlapped");
  PyObject *type = module ? PyObject_GetAttrString(module, "Overlapped") : nullptr;
  Py_XDECREF(module);
  PyErr_Clear();
  PyObject *names = type && PyType_Check(type) ? ((PyTypeObject *)type)->tp_dict : nullptr;
  for (size_t i = 0; names != nullptr && i < overlapped_count; i++) {
    OverlappedWrap &wrap = overlapped_wraps[i];
    if (wrap.network == strict_part) {
      continue; /* A network refusal is installed always; a handle judge only with the switch. */
    }
    PyObject *raw = PyDict_GetItemString(names, wrap.name); /* Borrowed. */
    if (raw == nullptr || (wrap.original != nullptr && raw != wrap.original)) {
      continue; /* Installed already, or something else stands there: CPython's first original only. */
    }
    if (wrap.original == nullptr) {
      wrap.original = Py_NewRef(raw); /* Kept for the life of the process. */
    }
    PyObject *guarded = PyDescr_NewMethod((PyTypeObject *)type, &overlapped_definitions[i]);
    if (guarded != nullptr) {
      note_install_site("_overlapped"); /* Never made again while confined (wrap_owners.cc). */
      if (strict_part) {
        set_method_strict((PyTypeObject *)type, wrap.name, guarded, wrap.original);
      }
      else {
        PyDict_SetItemString(names, wrap.name, guarded); /* Immutable to Python; changed here, in C. */
        PyType_Modified((PyTypeObject *)type);
      }
    }
    Py_XDECREF(guarded);
  }
  Py_XDECREF(type);
  PyErr_Clear();
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name compile, _fix_co_filename, sys.monitoring
 * \{ */

static PyObject *original_compile = nullptr;
static PyObject *original_fix_co_filename = nullptr;

static PyObject *wrapped_compile(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *code = PyObject_Call(original_compile, args, kwargs);
  installed_bytes_verified = false; /* The verification covered this compile only (review-confine-fixes N-1). */
  if (code != nullptr && PyCode_Check(code) && armed.load(std::memory_order_relaxed) && confined_now()) {
    /* The hook let an installed name through only for that file's own source: that code is installed. */
    const char *file = PyUnicode_AsUTF8(((PyCodeObject *)code)->co_filename);
    PyErr_Clear();
    if ((file == nullptr || file[0] == '<' || !folders().trusted(file)) && installed_import_globals() == nullptr) {
      /* (Unless an installed import compiles it as it loads afresh, unseeded: journal.cc.) */
      if (!taint(code)) {
        Py_DECREF(code);
        return PyErr_NoMemory(); /* Not all of it could be made agent code: no code object at all. */
      }
    }
  }
  return code;
}

/** Whether any code nested in `code` (constants, tuples, frozensets) carries a file name other than `code`'s own.
 * Read after importlib's rename, which gives the new name only to nested code that had the root's old one: any other
 * name left is one the bytes carried. */
static bool mixed_names(PyObject *code)
{
  PyObject *name = ((PyCodeObject *)code)->co_filename;
  std::vector<PyObject *> stack{((PyCodeObject *)code)->co_consts};
  while (!stack.empty()) {
    PyObject *item = stack.back();
    stack.pop_back();
    if (item == nullptr) {
      continue;
    }
    if (PyCode_Check(item)) {
      if (PyObject_RichCompareBool(((PyCodeObject *)item)->co_filename, name, Py_EQ) != 1) {
        PyErr_Clear();
        return true;
      }
      stack.push_back(((PyCodeObject *)item)->co_consts);
    }
    else if (PyTuple_Check(item)) {
      for (Py_ssize_t i = 0; i < PyTuple_GET_SIZE(item); i++) {
        stack.push_back(PyTuple_GET_ITEM(item, i));
      }
    }
    else if (PyFrozenSet_Check(item)) {
      PyObject *iterator = PyObject_GetIter(item);
      while (iterator != nullptr) {
        PyObject *member = PyIter_Next(iterator);
        if (member == nullptr) {
          break;
        }
        stack.push_back(member); /* The frozenset keeps it alive. */
        Py_DECREF(member);
      }
      Py_XDECREF(iterator);
      PyErr_Clear();
    }
  }
  return false;
}

static PyObject *wrapped_fix_co_filename(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  if (confined_now() && !in_bytecode_loader()) {
    refuse(GUARD, "_imp._fix_co_filename");
    return nullptr;
  }
  PyObject *result = PyObject_Call(original_fix_co_filename, args, kwargs);
  /* The only way live code changes its name: code renamed to an agent name is agent code now, so it is counted
   * now (nested constants too), as the watcher counted code made under that name. Inside a confined call the
   * thread's own depth holds the count until then. */
  if (result != nullptr && PyTuple_Check(args) && PyTuple_GET_SIZE(args) > 0) {
    PyObject *code = PyTuple_GET_ITEM(args, 0);
    /* While confined: a tree whose nested code carries another name than its root's is never the compiler's (it
     * gives one file's code one name), so it is refused; a root that is agent code takes its whole tree; a root that
     * is not (an installed file's own bytes, born exempt) is renamed as importlib does, and only what is agent code
     * by name counts (BREAKIT break-it-confine-fixes-b31e34840ca MUST-1, MUST-A of the depth re-read). */
    bool whole = true;
    if (confined_now() && PyCode_Check(code)) {
      if (mixed_names(code)) {
        Py_DECREF(result);
        refuse(GUARD, "_imp._fix_co_filename (nested code under another name)");
        return nullptr;
      }
      whole = agent_code((PyCodeObject *)code) ? taint(code) : count_agent_code_tree(code);
    }
    else {
      whole = count_agent_code_tree(code);
    }
    if (!whole) {
      Py_DECREF(result);
      return PyErr_NoMemory();
    }
  }
  return result;
}

static PyObject *monitoring_originals = nullptr; /* dict: name -> original */

static PyObject *wrapped_monitoring(PyObject *name, PyObject *args, PyObject *kwargs)
{
  if (confined_now()) {
    refuse(GUARD, "sys.monitoring");
    return nullptr;
  }
  PyObject *original = PyDict_GetItemWithError(monitoring_originals, name); /* Borrowed. */
  return original ? PyObject_Call(original, args, kwargs) : nullptr;
}

PyObject *monitoring_original(const char *name)
{
  PyObject *original = monitoring_originals ? PyDict_GetItemString(monitoring_originals, name) : nullptr;
  if (original == nullptr) { /* Not wrapped (yet): the module's own. */
    PyObject *monitoring = PySys_GetObject("monitoring"); /* Borrowed. */
    PyObject *held = monitoring ? PyObject_GetAttrString(monitoring, name) : nullptr;
    original = held;
    Py_XDECREF(held); /* The module keeps it alive. */
  }
  PyErr_Clear();
  return original;
}

static PyMethodDef compile_definition = {
    "compile", (PyCFunction)(void (*)(void))wrapped_compile, METH_VARARGS | METH_KEYWORDS, nullptr};
static PyMethodDef fix_definition = {"_fix_co_filename",
                                     (PyCFunction)(void (*)(void))wrapped_fix_co_filename,
                                     METH_VARARGS | METH_KEYWORDS,
                                     nullptr};
static PyMethodDef monitoring_definition = {
    "monitoring", (PyCFunction)(void (*)(void))wrapped_monitoring, METH_VARARGS | METH_KEYWORDS, nullptr};

/** \} */

/* `builtins.__build_class__`: a class statement whose body is the script's code makes the script's class, whatever
 * metaclass (Python code, installed: `ABCMeta`, `EnumType`) allocates it (bpy_angelo_guard_confine_types.cc). */
static PyObject *original_build_class = nullptr;

static PyObject *wrapped_build_class(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyObject *body = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  const bool script = body != nullptr && PyFunction_Check(body) && confined_now() &&
                      agent_code((PyCodeObject *)PyFunction_GetCode(body));
  script_class_statement += script ? 1 : 0;
  PyObject *made = PyObject_Call(original_build_class, args, kwargs);
  script_class_statement -= script ? 1 : 0;
  return made;
}

static PyMethodDef build_class_definition = {"__build_class__",
                                             (PyCFunction)(void (*)(void))wrapped_build_class,
                                             METH_VARARGS | METH_KEYWORDS,
                                             nullptr};

static PyObject *original_socketpair = nullptr;
thread_local int making_socketpair = 0;

/** The frame that called socketpair (borrowed: alive while it runs) and the file socketpair's own code is in. */
static thread_local PyFrameObject *socketpair_caller = nullptr;
static std::string socketpair_file;

static bool loopback(PyObject *address)
{
  PyObject *host = (address && PyTuple_Check(address) && PyTuple_GET_SIZE(address) >= 2) ?
                       PyTuple_GET_ITEM(address, 0) :
                       nullptr;
  const char *text = (host && PyUnicode_Check(host)) ? PyUnicode_AsUTF8(host) : nullptr;
  PyErr_Clear();
  return text != nullptr && (strcmp(text, "127.0.0.1") == 0 || strcmp(text, "::1") == 0);
}

bool socketpair_event_allowed(const char *name, PyObject *args)
{
  if (making_socketpair <= 0 || socketpair_file.empty()) {
    return false;
  }
  const bool made = strcmp(name, "socket.__new__") == 0;
  const bool addressed = strcmp(name, "socket.bind") == 0 || strcmp(name, "socket.connect") == 0;
  if (!made && !addressed) {
    return false; /* Anything else (a name lookup, a send to an address, ...) is never the pair's. */
  }
  if (addressed && !(PyTuple_GET_SIZE(args) > 1 && loopback(PyTuple_GET_ITEM(args, 1)))) {
    return false;
  }
  /* Every frame up to the call: socket.py's own, none the agent's (a finalizer, a gc callback or a signal handler
   * the script made would be one). The caller's frame must be found, else nothing is allowed. */
  PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
  Py_XINCREF(frame);
  bool allowed = false;
  while (frame != nullptr) {
    if (frame == socketpair_caller) {
      allowed = true;
      break;
    }
    PyCodeObject *code = PyFrame_GetCode(frame);
    const char *file = PyUnicode_Check(code->co_filename) ? PyUnicode_AsUTF8(code->co_filename) : nullptr;
    const bool own = !agent_code(code) && file != nullptr && socketpair_file == file;
    Py_DECREF(code);
    if (!own) {
      break;
    }
    PyFrameObject *back = PyFrame_GetBack(frame); /* New reference. */
    Py_DECREF(frame);
    frame = back;
  }
  Py_XDECREF(frame);
  PyErr_Clear();
  return allowed;
}

static PyObject *wrapped_socketpair(PyObject * /*self*/, PyObject *args, PyObject *kwargs)
{
  PyFrameObject *caller = socketpair_caller;
  socketpair_caller = PyEval_GetFrame(); /* Borrowed: the caller's frame outlives the call. */
  making_socketpair++;
  PyObject *result = PyObject_Call(original_socketpair, args, kwargs);
  making_socketpair--;
  socketpair_caller = caller;
  note_made_socket_pair(result); /* The script's own pair (bpy_angelo_guard_confine_made). */
  return result;
}

static PyMethodDef socketpair_definition = {
    "socketpair", (PyCFunction)(void (*)(void))wrapped_socketpair, METH_VARARGS | METH_KEYWORDS, nullptr};

void put_in(const char *module_name, const char *name, PyObject *wrapper, PyObject *original)
{
  note_install_site(module_name); /* Never made again while confined (wrap_owners.cc). */
  PyObject *module = PyImport_ImportModule(module_name);
  if (module == nullptr) {
    PyErr_Clear();
    return;
  }
  PyObject *held = PyObject_GetAttrString(module, name);
  PyErr_Clear();
  if (held == original) {
    PyObject_SetAttrString(module, name, wrapper);
  }
  Py_XDECREF(held);
  Py_DECREF(module);
  replace_references(original, wrapper);
}

void put_back(const char *module_name, const char *name, PyObject *wrapper, PyObject *original)
{
  PyObject *module = PyImport_ImportModule(module_name);
  if (module == nullptr) {
    PyErr_Clear();
    return;
  }
  PyObject *held = PyObject_GetAttrString(module, name);
  PyErr_Clear();
  if (held == wrapper) {
    PyObject_SetAttrString(module, name, original);
    PyErr_Clear();
  }
  Py_XDECREF(held);
  Py_DECREF(module);
}

namespace {

/* What Strict script protection installed (Head 20 read R-4): undone when it turns off, so 2.0.1's functions stand
 * again and cost what they did. A module attribute (and every reference `put_in` replaced), or a type's method. */
struct StrictInstall {
  std::string module, name;
  PyObject *wrapper, *original;
  PyTypeObject *type; /* A type's method, or null. */
};
std::vector<StrictInstall> strict_installs;

}  // namespace

void put_in_strict(const char *module_name, const char *name, PyObject *wrapper, PyObject *original)
{
  put_in(module_name, name, wrapper, original);
  strict_installs.push_back({module_name, name, Py_NewRef(wrapper), Py_NewRef(original), nullptr});
}

void set_method_strict(PyTypeObject *type, const char *name, PyObject *method, PyObject *original)
{
  note_wrapped_owner(original); /* Its module is never made again while confined. */
  PyDict_SetItemString(type->tp_dict, name, method); /* Immutable to Python; changed here, in C. */
  PyType_Modified(type);
  strict_installs.push_back(
      {std::string(), name, Py_NewRef(method), Py_NewRef(original), (PyTypeObject *)Py_NewRef((PyObject *)type)});
}

bool installed_by_strict(PyObject *object)
{
  return std::any_of(strict_installs.begin(), strict_installs.end(), [object](const StrictInstall &install) {
    return install.wrapper == object;
  });
}

void remove_strict_installs()
{
  /* Last installed first: a wrapper installed over another of this switch's (a maker over a descriptor wrap,
   * `os.dup`) is taken off before the one beneath it, so each `put_back` finds its own wrapper standing there. */
  for (auto it = strict_installs.rbegin(); it != strict_installs.rend(); ++it) {
    StrictInstall &install = *it;
    if (install.type != nullptr) {
      note_wrapped_owner(install.original); /* As at the install: its module is never made again while confined. */
      if (PyDict_GetItemString(install.type->tp_dict, install.name.c_str()) == install.wrapper) { /* Borrowed. */
        PyDict_SetItemString(install.type->tp_dict, install.name.c_str(), install.original);
        PyType_Modified(install.type);
      }
      Py_DECREF((PyObject *)install.type);
    }
    else {
      put_back(install.module.c_str(), install.name.c_str(), install.wrapper, install.original);
      replace_references(install.wrapper, install.original);
    }
    Py_DECREF(install.wrapper);
    Py_DECREF(install.original);
  }
  strict_installs.clear();
  PyErr_Clear();
}

/** `module_name.name` holds CPython's first original (kept in `*original` from the first look), so a wrapper may stand
 * there: false when it is missing, already wrapped, or something else stands there now (no recursion through a
 * profiler's or pyfakefs's wrapper: Head 20 read SHOULD). */
static bool still_original(const char *module_name, const char *name, PyObject **original)
{
  PyObject *module = PyImport_ImportModule(module_name);
  PyObject *current = module ? PyObject_GetAttrString(module, name) : nullptr;
  Py_XDECREF(module);
  PyErr_Clear();
  if (current == nullptr) {
    return false;
  }
  if (*original == nullptr) {
    *original = current; /* Kept for the life of the process. */
    return true;
  }
  const bool same = current == *original;
  Py_DECREF(current);
  return same;
}

/** The descriptor wraps: 2.0.1's judges (`strict_part` false) or the ownership layer's (true, Strict only). */
static void install_io_wraps(const bool strict_part)
{
  for (size_t i = 0; i < sizeof(io_wraps) / sizeof(io_wraps[0]); i++) {
    IoWrap &wrap = io_wraps[i];
    if (wrap.ownership != strict_part) {
      continue;
    }
    for (const char *module_name : wrap.modules) {
      if (module_name == nullptr || !still_original(module_name, wrap.name, &wrap.original)) {
        continue; /* One module only (`_fcopyfile`, `lockf`: POSIX's), or nothing (more) to wrap. */
      }
      PyObject *index = PyLong_FromSize_t(i);
      PyObject *wrapper = index ? PyCFunction_NewEx(&io_definition, index, nullptr) : nullptr;
      Py_XDECREF(index);
      if (wrapper != nullptr) {
        if (strict_part) {
          put_in_strict(module_name, wrap.name, wrapper, wrap.original);
          put_in_strict("os", wrap.name, wrapper, wrap.original);
        }
        else {
          put_in(module_name, wrap.name, wrapper, wrap.original);
          put_in("os", wrap.name, wrapper, wrap.original);
        }
      }
      Py_XDECREF(wrapper);
    }
  }
}

void install_strict_io()
{
  install_io_wraps(true);
  for (size_t i = 0; i < sizeof(handle_wraps) / sizeof(handle_wraps[0]); i++) {
    HandleWrap &wrap = handle_wraps[i];
    if (!still_original(wrap.module, wrap.name, &wrap.original)) {
      continue; /* Not Windows, or installed already. */
    }
    PyObject *index = PyLong_FromSize_t(i);
    PyObject *wrapper = index ? PyCFunction_NewEx(&handle_definition, index, nullptr) : nullptr;
    Py_XDECREF(index);
    if (wrapper != nullptr) {
      put_in_strict(wrap.module, wrap.name, wrapper, wrap.original);
      if (strcmp(wrap.module, "nt") == 0) {
        put_in_strict("os", wrap.name, wrapper, wrap.original); /* os's copy of nt's (as the descriptor wraps). */
      }
    }
    Py_XDECREF(wrapper);
  }
  install_overlapped_methods(true); /* Windows only: no `_overlapped` elsewhere. */
  PyErr_Clear();
}

int install_wrappers_io()
{
  install_io_wraps(false);
  if (strict_lookups()) {
    install_strict_io(); /* Strict script protection already on: the ownership layer too (else when it turns on). */
  }
  install_overlapped_methods(false); /* Windows only: no `_overlapped` elsewhere. */
  if (original_compile == nullptr) {
    PyObject *builtins = PyEval_GetBuiltins(); /* Borrowed. */
    original_compile = Py_XNewRef(PyDict_GetItemString(builtins, "compile"));
    if (PyObject *wrapper = original_compile ? PyCFunction_NewEx(&compile_definition, nullptr, nullptr) : nullptr) {
      PyDict_SetItemString(builtins, "compile", wrapper);
      replace_references(original_compile, wrapper);
      Py_DECREF(wrapper);
    }
  }
  if (original_build_class == nullptr) {
    PyObject *builtins = PyEval_GetBuiltins(); /* Borrowed. */
    original_build_class = Py_XNewRef(PyDict_GetItemString(builtins, "__build_class__"));
    if (PyObject *wrapper = original_build_class ?
                                PyCFunction_NewEx(&build_class_definition, nullptr, nullptr) :
                                nullptr)
    {
      PyDict_SetItemString(builtins, "__build_class__", wrapper);
      replace_references(original_build_class, wrapper);
      Py_DECREF(wrapper);
    }
  }
  if (original_fix_co_filename == nullptr) {
    PyObject *imp = PyImport_ImportModule("_imp");
    original_fix_co_filename = imp ? PyObject_GetAttrString(imp, "_fix_co_filename") : nullptr;
    Py_XDECREF(imp);
    if (PyObject *wrapper = original_fix_co_filename ? PyCFunction_NewEx(&fix_definition, nullptr, nullptr) :
                                                       nullptr)
    {
      put_in("_imp", "_fix_co_filename", wrapper, original_fix_co_filename);
      Py_DECREF(wrapper);
    }
  }
  if (monitoring_originals == nullptr) {
    PyObject *monitoring = PySys_GetObject("monitoring"); /* Borrowed. */
    monitoring_originals = PyDict_New();
    for (const char *name : {"use_tool_id", "free_tool_id", "clear_tool_id", "register_callback", "set_events",
                             "set_local_events", "restart_events"})
    {
      PyObject *original = monitoring ? PyObject_GetAttrString(monitoring, name) : nullptr;
      PyObject *key = PyUnicode_FromString(name);
      PyObject *wrapper = original && key ? PyCFunction_NewEx(&monitoring_definition, key, nullptr) : nullptr;
      if (wrapper != nullptr) {
        PyDict_SetItem(monitoring_originals, key, original);
        PyObject_SetAttrString(monitoring, name, wrapper);
      }
      Py_XDECREF(wrapper);
      Py_XDECREF(key);
      Py_XDECREF(original);
      PyErr_Clear();
    }
  }
  if (original_socketpair == nullptr) {
    PyObject *socket_module = PyImport_ImportModule("socket");
    original_socketpair = socket_module ? PyObject_GetAttrString(socket_module, "socketpair") : nullptr;
    /* The file socketpair's own code runs in (socket.py), as its code objects name it. */
    PyObject *code = original_socketpair ? PyObject_GetAttrString(original_socketpair, "__code__") : nullptr;
    PyObject *file = code ? PyObject_GetAttrString(code, "co_filename") : nullptr;
    if (file != nullptr && PyUnicode_Check(file)) {
      socketpair_file = PyUnicode_AsUTF8(file);
    }
    Py_XDECREF(file);
    Py_XDECREF(code);
    PyErr_Clear();
    Py_XDECREF(socket_module);
    if (PyObject *wrapper = original_socketpair ? PyCFunction_NewEx(&socketpair_definition, nullptr, nullptr) :
                                                  nullptr)
    {
      put_in("socket", "socketpair", wrapper, original_socketpair);
      Py_DECREF(wrapper);
    }
    PyErr_Clear();
  }
  install_native_entries();
  install_process_wrappers();
  provenance_install();
  PyErr_Clear();
  return 0;
}

}  // namespace blender::angelo_guard
