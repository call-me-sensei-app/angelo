/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The stat family, which raises no audit event: a confined thread's lookup never reaches the network
 * (break-it-confine-reparse-b7d499c M7; doc/angelo/script_confinement.rst, "Network"). `os.stat`, `lstat`, `access`,
 * `readlink`, `statvfs`, `pathconf`, Windows' `nt._path_*` (`os.path.isdir`, `isfile`, `exists`, `islink`,
 * `lexists`, `isjunction`, `isdevdrive`), `_getfinalpathname`, `_getvolumepathname`, `_getdiskusage`, and
 * `os.DirEntry`'s `is_dir`, `is_file` and `stat`, each with its own following of the last component
 * (`follow_symlinks`, `lstat`, `islink`), are refused as the network when the name is remote by its text, when a link
 * on the way leads to a share nobody connected (Windows: the link walk), or when resolving it steps into a folder of
 * the automounter's maps of any host (POSIX). Nothing else changes: these lookups judge no private folder, as before
 * (`os.path.exists` of a key stays what it was). Through these wrappers `os.path`, `pathlib`, `glob`, `shutil` and
 * `os.walk` (which asks each entry `is_dir`) are covered. Blender's C lookups (`BLI_exists`, `BLI_stat`, `BLI_is_dir`,
 * `BLI_is_file`) ask the same check through the file gate (`lookup_refused`).
 */

#include <Python.h>

#include <atomic>
#include <cstdint>
#include <cctype>
#include <cstring>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>


#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_made.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_remote.hh"

namespace blender::angelo_guard {

namespace {

thread_local int looking_up = 0; /* A lookup the check itself makes (resolve's own calls): never asked again. */

#ifndef _WIN32
/* POSIX: the real path of each absolute folder `resolve` walked to safely, per generation (break-it-confine-reparse
 * M7 read, SHOULD: a resolve per lookup, one `lstat` per component, cost 10-20 us a stat). A region's start and
 * every change that can put a link where a folder was (a judged move, removal or link from Python, or Blender's own
 * move: its C makes no link) start a new generation, as the Windows link walk's cache does; what another process
 * changes meanwhile is the documented race of checks before a call (doc/angelo/script_confinement.rst). Only folders
 * resolved without the network are kept. */
std::mutex resolved_lock;
std::unordered_map<std::string, std::string> resolved_folders;
std::atomic<uint64_t> lookup_generation{1};
uint64_t resolved_generation = 0; /* Under `resolved_lock`. */
constexpr size_t RESOLVED_MAX = 4096;

std::optional<std::string> resolve_folder(const std::string &folder, const int dir_fd)
{
  const bool keyed = dir_fd < 0 && !folder.empty() && folder[0] == '/';
  const uint64_t generation = lookup_generation.load(std::memory_order_acquire);
  if (keyed) {
    std::lock_guard<std::mutex> hold(resolved_lock);
    if (resolved_generation != generation) {
      resolved_folders.clear();
      resolved_generation = generation;
    }
    const auto found = resolved_folders.find(folder);
    if (found != resolved_folders.end()) {
      resolve_reached_network = false;
      return found->second;
    }
  }
  std::optional<std::string> resolved = resolve(folder, dir_fd);
  if (keyed && resolved && !resolve_reached_network) {
    std::lock_guard<std::mutex> hold(resolved_lock);
    if (resolved_generation == generation) { /* Nothing changed while it resolved. */
      if (resolved_folders.size() >= RESOLVED_MAX) {
        resolved_folders.clear();
      }
      resolved_folders.emplace(folder, *resolved);
    }
  }
  return resolved;
}
#endif

}  // namespace

void lookup_cache_forget()
{
#ifndef _WIN32
  lookup_generation.fetch_add(1, std::memory_order_acq_rel);
#endif
}

Refusal network_lookup(const std::string &path, const int dir_fd, const bool follow_last)
{
  if (path.empty() || looking_up > 0) {
    return Refusal::None;
  }
  looking_up++;
  network_refusal_begin(path);
  judged_unreadable = false;
  Refusal refusal = Refusal::None;
#ifndef _WIN32
  /* A name relative to a folder descriptor (`dir_fd=`, an entry of `os.scandir(fd)`, what `os.fwalk` and `glob`'s
   * `dir_fd` ask) is looked up inside that folder, not the working one: judged as the joined path (break-it-confine-
   * reparse head 14, MUST-3). A descriptor whose folder can't be told is refused: where the name leads is unknown. */
  std::string joined;
  if (dir_fd >= 0 && path[0] != '/') {
    const std::optional<std::string> folder = descriptor_path(dir_fd);
    if (!folder) {
      judged_unreadable = true;
      looking_up--;
      return Refusal::Private;
    }
    joined = (*folder == "/" ? std::string() : *folder) + "/" + path;
  }
  const std::string &named = joined.empty() ? path : joined;
#else
  const std::string &named = path;
#endif
  if (names_a_remote_file(named)) {
    refusal = Refusal::Network;
  }
  else {
#ifdef _WIN32
    (void)dir_fd;
    std::wstring wide;
    if (!utf8_to_wide(path, wide)) {
      refusal = Refusal::Network;
    }
    else if (strict_lookups()) { /* Strict script protection: the links on the way (Ruling 17). */
      switch (links_walk(wide, follow_last)) {
        case links::Verdict::Network:
          refusal = Refusal::Network;
          break;
        case links::Verdict::Unreadable:
          judged_unreadable = true;
          refusal = Refusal::Private; /* Where it leads can't be told: fail closed. */
          break;
        case links::Verdict::Safe:
          break;
      }
    }
#else
    /* The folders are followed either way; the last component only when the lookup follows it (an lstat of a link
     * into /net reads the link, it mounts nothing). The last name itself is judged by its text in its real folder. */
    const size_t cut = named.find_last_of('/');
    const bool split = !follow_last && cut != std::string::npos && cut + 1 < named.size();
    const std::string folder = split ? (cut == 0 ? std::string("/") : named.substr(0, cut)) : named;
    const std::optional<std::string> resolved = resolve_folder(folder, joined.empty() ? dir_fd : -1);
    if (!resolved) {
      refusal = resolve_reached_network ? Refusal::Network : Refusal::None;
    }
    else if (split && names_a_remote_file((*resolved == "/" ? std::string() : *resolved) + named.substr(cut))) {
      refusal = Refusal::Network;
    }
#endif
  }
  looking_up--;
  return refusal;
}

bool lookup_refused_by_gate(const char *path, const bool follow_last)
{
  /* Blender's own lookups for a script: with Strict script protection only (Ruling 17, `strict_lookups`). */
  return path != nullptr && confined_now() && strict_lookups() &&
         network_lookup(path, -1, follow_last) != Refusal::None;
}

namespace {

/* -------------------------------------------------------------------- */
/** \name Module functions (`os.stat`, `nt._path_isdir`, ...)
 * \{ */

struct LookupWrap {
  const char *modules[2]; /* Where the name lives (os's are posix's or nt's); null: none. */
  const char *name;
  bool follows;        /* Follows the last component by default. */
  bool follow_keyword; /* Takes `follow_symlinks=`. */
  bool predicate;      /* Answers true or false and never raises (`nt._path_*`): a refusal answers false. */
  PyObject *original;
  /* Windows' name lookups that also read a folder's names (a wildcard lists it, a long or short name spells a name
   * in it): judged as a read too, so a private folder's names stay private; `*` and `?` refused. */
  bool reads_names = false;
  bool local_pipe_only = false; /* A pipe waited for is this machine's (`\\.\pipe\`): another host's sends NTLM. */
  PyObject *wrapper = nullptr;         /* Installed: what stands for `original`, and in which module. */
  const char *installed_in = nullptr;
};

LookupWrap lookup_wraps[] = {
    {{"posix", "nt"}, "stat", true, true, false, nullptr},
    {{"posix", "nt"}, "lstat", false, false, false, nullptr},
    {{"posix", "nt"}, "access", true, true, false, nullptr},
    {{"posix", "nt"}, "readlink", false, false, false, nullptr},
    {{"posix", nullptr}, "statvfs", true, false, false, nullptr},
    {{"posix", nullptr}, "pathconf", true, false, false, nullptr},
    {{"nt", nullptr}, "_path_isdir", true, false, true, nullptr},
    {{"nt", nullptr}, "_path_isfile", true, false, true, nullptr},
    {{"nt", nullptr}, "_path_exists", true, false, true, nullptr},
    {{"nt", nullptr}, "_path_isdevdrive", true, false, true, nullptr},
    {{"nt", nullptr}, "_path_islink", false, false, true, nullptr},
    {{"nt", nullptr}, "_path_lexists", false, false, true, nullptr},
    {{"nt", nullptr}, "_path_isjunction", false, false, true, nullptr},
    {{"nt", nullptr}, "_getfinalpathname", true, false, false, nullptr},
    {{"nt", nullptr}, "_getvolumepathname", true, false, false, nullptr},
    {{"nt", nullptr}, "_getdiskusage", true, false, false, nullptr},
    /* Windows' own: a long or short name is read from the folder (a share's name connects), and a pipe waited for
     * may be another host's (`\\host\pipe\name`). */
    {{"_winapi", nullptr}, "GetLongPathName", true, false, false, nullptr, true},
    {{"_winapi", nullptr}, "GetShortPathName", true, false, false, nullptr, true},
    {{"_winapi", nullptr}, "WaitNamedPipe", true, false, false, nullptr, false, true},
    {{"nt", nullptr}, "_findfirstfile", true, false, false, nullptr, true}, /* ntpath's realpath, not strict. */
    {{"nt", nullptr}, "listmounts", true, false, false, nullptr}, /* A volume's folders, read from its name. */
    /* realpath, strict or not (and pathlib's `resolve`, which calls `os.path.realpath`): the whole path is asked
     * before its first `lstat`, so a link to the network is refused whatever exists on the way (a missing `/net`
     * made the strict one raise FileNotFoundError before any check: iso-mac on 1a17d20). Each module's own. */
    {{"posixpath", nullptr}, "realpath", true, false, false, nullptr},
    {{"ntpath", nullptr}, "realpath", true, false, false, nullptr},
};
constexpr size_t lookup_count = sizeof(lookup_wraps) / sizeof(lookup_wraps[0]);
PyMethodDef lookup_definitions[lookup_count];

/** The path a call names: its first argument or `path=`; nothing for a descriptor (an int) or no argument. */
bool named_path(PyObject *args, PyObject *kwargs, std::string &out)
{
  PyObject *value = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) :
                    kwargs                      ? PyDict_GetItemString(kwargs, "path") :
                                                  nullptr;
  if (value == nullptr || PyLong_Check(value)) {
    return false;
  }
  PyObject *bytes = nullptr;
  if (!PyUnicode_FSConverter(value, &bytes)) {
    PyErr_Clear();
    return false; /* The original raises its own error for what it can't take. */
  }
  out.assign(PyBytes_AS_STRING(bytes), size_t(PyBytes_GET_SIZE(bytes)));
  Py_DECREF(bytes);
  return true;
}

int keyword_int(PyObject *kwargs, const char *name, const int otherwise)
{
  PyObject *value = kwargs ? PyDict_GetItemString(kwargs, name) : nullptr;
  if (value == nullptr || value == Py_None || !PyLong_Check(value)) {
    return otherwise;
  }
  const long number = PyLong_AsLong(value);
  if (PyErr_Occurred()) {
    PyErr_Clear();
    return otherwise;
  }
  return int(number);
}

int refuse_lookup(const Refusal refusal, const char *what)
{
  return refuse(refusal == Refusal::Network ? NETWORK : PRIVATE, what);
}

/** A local pipe's name: `\\.\pipe\` then a name with no `/` and no `..` (a device path is normalized: `..` would
 * climb out to `\\.\UNC\`). */
bool local_pipe_name(const std::string &path)
{
  static const std::string prefix = "\\\\.\\pipe\\";
  if (path.size() <= prefix.size()) {
    return false;
  }
  for (size_t i = 0; i < prefix.size(); i++) {
    if (tolower((unsigned char)path[i]) != tolower((unsigned char)prefix[i])) {
      return false;
    }
  }
  const std::string rest = path.substr(prefix.size());
  return rest.find('/') == std::string::npos && rest.find("..") == std::string::npos;
}

PyObject *lookup_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const LookupWrap &wrap = lookup_wraps[PyLong_AsSsize_t(index)];
  std::string path;
  const bool named = confined_now() && named_path(args, kwargs, path);
  if (confined_now() && wrap.local_pipe_only && (!named || !local_pipe_name(path))) {
    refuse_lookup(Refusal::Network, wrap.name); /* Not named, or not this machine's: refused. */
    return nullptr;
  }
  if (named && wrap.reads_names) {
    if (path.find_first_of("*?") != std::string::npos) {
      refuse_lookup(Refusal::Private, wrap.name); /* A wildcard lists a folder: refused whatever folder. */
      return nullptr;
    }
    PyObject *value = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) :
                                                   PyDict_GetItemString(kwargs, "path");
    if (value != nullptr && judge_entry_path(value, false) != 0) {
      return nullptr; /* A private folder's names: refused as a read of it (ScriptConfined set). */
    }
  }
  /* A local pipe's name, checked above, names no file. The network check: with Strict script protection only. */
  if (named && !wrap.local_pipe_only && strict_lookups()) {
    bool follow = wrap.follows;
    if (wrap.follow_keyword && kwargs != nullptr) {
      if (PyObject *value = PyDict_GetItemString(kwargs, "follow_symlinks")) {
        follow = PyObject_IsTrue(value) == 1;
      }
    }
    const Refusal refusal = network_lookup(path, keyword_int(kwargs, "dir_fd", -1), follow);
    if (refusal != Refusal::None) {
      if (wrap.predicate) {
        Py_RETURN_FALSE; /* As for a name that isn't there: these never raise. */
      }
      refuse_lookup(refusal, wrap.name);
      return nullptr;
    }
  }
  return PyObject_Call(wrap.original, args, kwargs);
}

/** The arguments of a vectorcall as a tuple and a keyword dict (null without keywords), for the checks above. */
bool as_tuple_and_dict(PyObject *const *args,
                       const Py_ssize_t nargs,
                       PyObject *kwnames,
                       PyObject **r_tuple,
                       PyObject **r_dict)
{
  *r_dict = nullptr;
  *r_tuple = PyTuple_New(nargs);
  if (*r_tuple == nullptr) {
    return false;
  }
  for (Py_ssize_t i = 0; i < nargs; i++) {
    PyTuple_SET_ITEM(*r_tuple, i, Py_NewRef(args[i]));
  }
  const Py_ssize_t keywords = kwnames ? PyTuple_GET_SIZE(kwnames) : 0;
  if (keywords > 0) {
    *r_dict = PyDict_New();
    for (Py_ssize_t i = 0; *r_dict != nullptr && i < keywords; i++) {
      if (PyDict_SetItem(*r_dict, PyTuple_GET_ITEM(kwnames, i), args[nargs + i]) != 0) {
        Py_CLEAR(*r_dict);
      }
    }
    if (*r_dict == nullptr) {
      Py_CLEAR(*r_tuple);
      return false;
    }
  }
  return true;
}

/** The wrapper Python calls: straight to the original (vectorcall, no tuple) unless a confined call has a check to
 * run. Without Strict script protection that is only a pipe waited for and a name lookup that reads a folder's names;
 * with it, every call that names a path (Ruling 17: the stat family costs as in 2.0.1 by default). */
PyObject *lookup_fastcall(PyObject *index, PyObject *const *args, const Py_ssize_t nargs, PyObject *kwnames)
{
  const LookupWrap &wrap = lookup_wraps[PyLong_AsSsize_t(index)];
  /* The switch first (an atomic load): a copy of a strict-only wrapper something kept after the switch turned off
   * then costs nothing more, never a frame walk (Head 20 read SHOULD). */
  if ((!wrap.local_pipe_only && !wrap.reads_names && !strict_lookups()) || !confined_now()) {
    return PyObject_Vectorcall(wrap.original, args, size_t(nargs), kwnames);
  }
  PyObject *tuple, *dict;
  if (!as_tuple_and_dict(args, nargs, kwnames, &tuple, &dict)) {
    return nullptr;
  }
  PyObject *result = lookup_call(index, tuple, dict);
  Py_DECREF(tuple);
  Py_XDECREF(dict);
  return result;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Changes without an audit event (`os.mkfifo`, `os.mknod`, `os.chroot`)
 *
 * CPython raises no event for them (break-it-confine-reparse M7 read, MUST-1; the inventory's probe finds none), so
 * the hook never saw them: each wrapper raises one of Angelo's own (`angelo.os.*`, judged by the hook's table like
 * `os.mkdir`) with the path and `dir_fd`, then calls the original. Raised whether confined or not: the hook judges
 * only a confined thread.
 * \{ */

struct AuditWrap {
  const char *module; /* Where it lives (also put in `os`). */
  const char *name;
  const char *event;
  PyObject *original;
};

AuditWrap audit_wraps[] = {{"posix", "mkfifo", "angelo.os.mkfifo", nullptr},
                           {"posix", "mknod", "angelo.os.mknod", nullptr},
                           {"posix", "chroot", "angelo.os.chroot", nullptr}};
constexpr size_t audit_count = sizeof(audit_wraps) / sizeof(audit_wraps[0]);
PyMethodDef audit_definitions[audit_count];

PyObject *audit_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const AuditWrap &wrap = audit_wraps[PyLong_AsSsize_t(index)];
  PyObject *path = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) :
                   kwargs                      ? PyDict_GetItemString(kwargs, "path") :
                                                 nullptr;
  PyObject *dir_fd = kwargs ? PyDict_GetItemString(kwargs, "dir_fd") : nullptr;
  /* No path: the original raises its own TypeError. */
  if (path != nullptr && PySys_Audit(wrap.event, "OO", path, dir_fd ? dir_fd : Py_None) < 0) {
    return nullptr;
  }
  return PyObject_Call(wrap.original, args, kwargs);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name What a script may never do, and CPython raises no event for (`os.closerange`, `os.login_tty`, ...)
 *
 * break-it-confine-reparse head-12 read (private-file class) and the inventory's descriptor functions: a range closes
 * descriptors by number, Angelo's own among them (a later open would reuse a private file's number); `login_tty`
 * makes a new session and puts a descriptor in place of Angelo's standard streams; `tcsetpgrp` changes which process
 * group a terminal serves; `setns` moves the process into another namespace. Refused while confined; `os.close` and
 * `os.dup2` judge the one descriptor they name (bpy_angelo_guard_confine_wrap_io.cc).
 * \{ */

struct RefusedWrap {
  const char *name;
  const char *what;
  bool process; /* The process's state (PROCESS); else a descriptor (PRIVATE). */
  PyObject *original;
};

RefusedWrap refused_wraps[] = {
    {"closerange", "os.closerange (close each descriptor the script opened instead)", false, nullptr},
    {"login_tty", "os.login_tty", true, nullptr},
    {"tcsetpgrp", "os.tcsetpgrp", true, nullptr},
    {"setns", "os.setns", true, nullptr},
};
constexpr size_t refused_count = sizeof(refused_wraps) / sizeof(refused_wraps[0]);
PyMethodDef refused_definitions[refused_count];

PyObject *refused_call(PyObject *index, PyObject *args, PyObject *kwargs)
{
  const RefusedWrap &wrap = refused_wraps[PyLong_AsSsize_t(index)];
  if (confined_now()) {
    refuse(wrap.process ? PROCESS : PRIVATE, wrap.what);
    return nullptr;
  }
  return PyObject_Call(wrap.original, args, kwargs);
}

/** \} */


/** Whether `wrap` exists only for the network check, which is Strict script protection's (Ruling 17): installed
 * only once the switch is on, so with it off `os.stat` and the rest are CPython's own and cost what they did in 2.0.1
 * (iso-linux on b55b844: the pass-through wrapper alone was +0.08 us a call, +2.7% on a stat, script or not). A pipe
 * waited for and a lookup that reads a folder's names keep their checks either way: always installed. */
bool strict_only(const LookupWrap &wrap)
{
  return !wrap.local_pipe_only && !wrap.reads_names;
}

/** The stat family's wrappers: those of `strict_part` (`strict_only`) or the others; each once. */
void install_lookup_wraps(const bool strict_part)
{
  for (size_t i = 0; i < lookup_count; i++) {
    LookupWrap &wrap = lookup_wraps[i];
    if (strict_only(wrap) != strict_part) {
      continue;
    }
    lookup_definitions[i] = {
        wrap.name, (PyCFunction)(void (*)(void))lookup_fastcall, METH_FASTCALL | METH_KEYWORDS, nullptr};
    for (const char *module_name : wrap.modules) {
      if (module_name == nullptr || wrap.wrapper != nullptr) {
        continue;
      }
      PyObject *module = PyImport_ImportModule(module_name);
      PyObject *current = module ? PyObject_GetAttrString(module, wrap.name) : nullptr;
      Py_XDECREF(module);
      PyErr_Clear();
      if (current == nullptr) {
        continue; /* Not on this system (`statvfs` on Windows, `_path_isdevdrive` before 3.12). */
      }
      if (wrap.original == nullptr) {
        wrap.original = current; /* CPython's own, the first time: kept for the life of the process. */
      }
      else {
        /* Installed again: never adopt what the attribute holds now (a profiler's or pyfakefs's wrapper around the
         * old one would call itself: Head 20 read SHOULD). Every reference to CPython's original is replaced;
         * a foreign function standing in the module is left as it is. */
        Py_DECREF(current);
      }
      PyObject *index = PyLong_FromSize_t(i);
      PyObject *wrapper = index ? PyCFunction_NewEx(&lookup_definitions[i], index, nullptr) : nullptr;
      Py_XDECREF(index);
      if (wrapper != nullptr) {
        put_in(module_name, wrap.name, wrapper, wrap.original); /* And every copy (`ntpath.isdir`). */
        put_in("os", wrap.name, wrapper, wrap.original);
        wrap.wrapper = Py_NewRef(wrapper);
        wrap.installed_in = module_name;
      }
      Py_XDECREF(wrapper);
    }
  }
}

}  // namespace

void remove_strict_lookups()
{
  replace_references_begin(); /* One walk over the loaded modules puts every original back. */
  for (LookupWrap &wrap : lookup_wraps) {
    if (!strict_only(wrap) || wrap.wrapper == nullptr) {
      continue;
    }
    put_back(wrap.installed_in, wrap.name, wrap.wrapper, wrap.original);
    put_back("os", wrap.name, wrap.wrapper, wrap.original);
    replace_references(wrap.wrapper, wrap.original); /* Every copy the install replaced (`ntpath.isdir`). */
    Py_CLEAR(wrap.wrapper); /* `original` stays: a copy of the wrapper something kept still calls through to it. */
    wrap.installed_in = nullptr;
  }
  remove_wrappers_entry();
  remove_strict_installs(); /* The ownership layer's (wrap_io.cc, made.cc). */
  replace_references_end();
  PyErr_Clear();
}

void install_strict_lookups()
{
  replace_references_begin(); /* One walk over the loaded modules for them all. */
  install_lookup_wraps(true);
  install_wrappers_entry(); /* `os.DirEntry` (bpy_angelo_guard_confine_wrap_lookup_entry.cc). */
  install_strict_io();      /* The descriptor-ownership layer (wrap_io.cc, R-4). */
  install_strict_makers();  /* And what notes the numbers a script makes (made.cc). */
  replace_references_end();
  PyErr_Clear();
}

int install_wrappers_lookup()
{
  install_lookup_wraps(false);
  if (strict_lookups()) {
    install_lookup_wraps(true); /* Strict script protection already on: its wrappers too (else when it turns on). */
    install_wrappers_entry();
  }
  for (size_t i = 0; i < audit_count; i++) {
    AuditWrap &wrap = audit_wraps[i];
    PyObject *module = wrap.original == nullptr ? PyImport_ImportModule(wrap.module) : nullptr;
    PyObject *current = module ? PyObject_GetAttrString(module, wrap.name) : nullptr;
    Py_XDECREF(module);
    PyErr_Clear();
    if (current == nullptr) {
      continue; /* Not on this system (Windows). */
    }
    audit_definitions[i] = {wrap.name, (PyCFunction)(void (*)(void))audit_call, METH_VARARGS | METH_KEYWORDS,
                            nullptr};
    wrap.original = current;
    PyObject *index = PyLong_FromSize_t(i);
    PyObject *wrapper = index ? PyCFunction_NewEx(&audit_definitions[i], index, nullptr) : nullptr;
    Py_XDECREF(index);
    if (wrapper != nullptr) {
      put_in(wrap.module, wrap.name, wrapper, wrap.original);
      put_in("os", wrap.name, wrapper, wrap.original);
    }
    Py_XDECREF(wrapper);
  }
  for (size_t i = 0; i < refused_count; i++) {
    RefusedWrap &wrap = refused_wraps[i];
    for (const char *module_name : {"posix", "nt"}) {
      PyObject *module = wrap.original == nullptr ? PyImport_ImportModule(module_name) : nullptr;
      PyObject *current = module ? PyObject_GetAttrString(module, wrap.name) : nullptr;
      Py_XDECREF(module);
      PyErr_Clear();
      if (current == nullptr) {
        continue; /* Not on this system. */
      }
      refused_definitions[i] = {wrap.name, (PyCFunction)(void (*)(void))refused_call, METH_VARARGS | METH_KEYWORDS,
                                nullptr};
      wrap.original = current; /* Kept for the life of the process. */
      PyObject *index = PyLong_FromSize_t(i);
      PyObject *wrapper = index ? PyCFunction_NewEx(&refused_definitions[i], index, nullptr) : nullptr;
      Py_XDECREF(index);
      if (wrapper != nullptr) {
        put_in(module_name, wrap.name, wrapper, current);
        put_in("os", wrap.name, wrapper, current);
      }
      Py_XDECREF(wrapper);
    }
  }
  PyErr_Clear();
  return 0;
}

}  // namespace blender::angelo_guard
