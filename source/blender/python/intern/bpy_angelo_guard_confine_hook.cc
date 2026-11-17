/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * The confinement's audit hook (PEP 578), a C callback registered before Python starts
 * (doc/angelo/script_confinement.rst). Until the first agent script it returns after one comparison; afterwards
 * every event it judges (files, programs, native code, the network, introspection, thread starts) is refused on a
 * confined thread, and code made or run there becomes agent code.
 */

#include <Python.h>

#include <cctype>
#include <cstring>
#include <optional>
#include <string>
#include <vector>
#include <string_view>
#include <unordered_map>

#include "bpy_angelo_guard_confine_intern.hh"
#include "bpy_angelo_guard_confine_journal.hh"
#include "bpy_angelo_guard_confine_paths.hh"
#include "bpy_angelo_guard_confine_winpath.hh"

namespace blender::angelo_guard {

enum class Kind {
  File,
  Process,
  Native,
  Guard,
  Thread,
  Import,
  SetAttr,
  Network,
  Startup,
  ProcessState,
  Environment,
  Signal
};

/** A path argument: its index, how it is used, and the index of its folder descriptor (-1: none). */
struct PathArg {
  int index;
  char role; /* 'o': open (read or write by its mode), 'r', 'w': its bytes written, 'm': made, removed or its
              * mode, owner, times or flags changed (judged as a write, never recorded), 'v': something moved here,
              * 'l': a link made here, 's': a link's source, 't': a link's target, 'f': a descriptor, 'g': a glob
              * pattern's folder, 'd': an sqlite database. What the record keeps of each: changes.hh. */
  int dir_fd;
};

struct Event {
  Kind kind;
  PathArg paths[2] = {{-1, 0, -1}, {-1, 0, -1}};
};

static const std::unordered_map<std::string_view, Event> &events()
{
  static const std::unordered_map<std::string_view, Event> table = {
      {"open", {Kind::File, {{0, 'o', -1}}}},
      {"os.rename", {Kind::File, {{0, 'm', 2}, {1, 'v', 3}}}},
      {"os.remove", {Kind::File, {{0, 'm', 1}}}},
      {"os.rmdir", {Kind::File, {{0, 'm', 1}}}},
      {"os.mkdir", {Kind::File, {{0, 'm', 2}}}},
      {"os.chmod", {Kind::File, {{0, 'm', 2}}}},
      {"os.chown", {Kind::File, {{0, 'm', 3}}}},
      {"os.utime", {Kind::File, {{0, 'm', 3}}}},
      {"os.truncate", {Kind::File, {{0, 'w', -1}}}},
      {"os.chflags", {Kind::File, {{0, 'm', -1}}}},
      {"os.lchflags", {Kind::File, {{0, 'm', -1}}}},
      {"os.link", {Kind::File, {{0, 's', 2}, {1, 'l', 3}}}},
      {"os.symlink", {Kind::File, {{0, 't', -1}, {1, 'l', 2}}}},
      {"os.listdir", {Kind::File, {{0, 'r', -1}}}},
      {"os.scandir", {Kind::File, {{0, 'r', -1}}}},
      {"os.chdir", {Kind::File, {{0, 'r', -1}}}},
      {"os.fwalk", {Kind::File, {{0, 'r', 4}}}},
      {"os.listxattr", {Kind::File, {{0, 'r', -1}}}},
      {"os.getxattr", {Kind::File, {{0, 'r', -1}}}},
      {"os.setxattr", {Kind::File, {{0, 'm', -1}}}},
      {"os.removexattr", {Kind::File, {{0, 'm', -1}}}},
      {"shutil.copyfile", {Kind::File, {{0, 'r', -1}, {1, 'w', -1}}}},
      {"shutil.copymode", {Kind::File, {{0, 'r', -1}, {1, 'm', -1}}}},
      {"shutil.copystat", {Kind::File, {{0, 'r', -1}, {1, 'm', -1}}}},
      {"shutil.copytree", {Kind::File, {{0, 'r', -1}, {1, 'm', -1}}}},
      {"shutil.move", {Kind::File, {{0, 'm', -1}, {1, 'm', -1}}}},
      {"shutil.rmtree", {Kind::File, {{0, 'm', 1}}}},
      {"shutil.chown", {Kind::File, {{0, 'm', -1}}}},
      {"shutil.make_archive", {Kind::File, {{0, 'w', -1}, {2, 'r', -1}}}},
      {"shutil.unpack_archive", {Kind::File, {{0, 'r', -1}, {1, 'm', -1}}}},
      {"sqlite3.connect", {Kind::File, {{0, 'd', -1}}}},
      {"glob.glob", {Kind::File, {{0, 'g', -1}}}},
      {"glob.glob/2", {Kind::File, {{0, 'g', 3}}}},
      {"tempfile.mkstemp", {Kind::File, {{0, 'w', -1}}}},
      {"tempfile.mkdtemp", {Kind::File, {{0, 'm', -1}}}},
      {"mmap.__new__", {Kind::File, {{0, 'f', -1}}}},
      {"_winapi.CreateJunction", {Kind::File, {{0, 't', -1}, {1, 'l', -1}}}},
      /* Windows' raw handles (`_winapi.ReadFile` reads one with no descriptor to judge): judged as a write, its
       * strictest use, whatever access it asks for. */
      {"_winapi.CreateFile", {Kind::File, {{0, 'w', -1}}}},
      {"subprocess.Popen", {Kind::Process}},
      {"os.system", {Kind::Process}},
      {"os.exec", {Kind::Process}},
      {"os.spawn", {Kind::Process}},
      {"os.posix_spawn", {Kind::Process}},
      {"os.fork", {Kind::Process}},
      {"os.forkpty", {Kind::Process}},
      {"os.startfile", {Kind::Process}},
      {"os.startfile/2", {Kind::Process}},
      {"_winapi.CreateProcess", {Kind::Process}},
      /* Other processes: a handle to one (to end it or read what it holds), and WMI queries, which read every
       * process's command line (where tokens are passed). */
      {"_winapi.OpenProcess", {Kind::Process}},
      {"_winapi.TerminateProcess", {Kind::Process}},
      {"_wmi.exec_query", {Kind::Process}},
      /* A signal: refused while confined; Angelo's own code reaches only itself and its descendants (_owned.cc). */
      {"os.kill", {Kind::Signal}},
      {"os.killpg", {Kind::Signal}},
      /* A named pipe server other processes connect to. */
      {"_winapi.CreateNamedPipe", {Kind::Network}},
      {"pty.spawn", {Kind::Process}},
      {"webbrowser.open", {Kind::Process}},
      {"os.add_dll_directory", {Kind::Native}},
      {"sys.addaudithook", {Kind::Guard}},
      {"sys.settrace", {Kind::Guard}},
      {"sys.setprofile", {Kind::Guard}},
      {"sys.monitoring.register_callback", {Kind::Guard}},
      {"sys._current_frames", {Kind::Guard}},
      {"gc.get_objects", {Kind::Guard}},
      {"gc.get_referrers", {Kind::Guard}},
      {"gc.get_referents", {Kind::Guard}},
      {"function.__new__", {Kind::Guard}},
      {"sys.set_asyncgen_hooks_firstiter", {Kind::Guard}},
      {"sys.set_asyncgen_hooks_finalizer", {Kind::Guard}},
      {"sqlite3.enable_load_extension", {Kind::Native}},
      {"sqlite3.load_extension", {Kind::Native}},
      {"_thread.start_new_thread", {Kind::Thread}},
      {"_thread.start_joinable_thread", {Kind::Thread}},
      {"import", {Kind::Import}},
      {"object.__setattr__", {Kind::SetAttr}},
      {"object.__delattr__", {Kind::SetAttr}},
      {"urllib.Request", {Kind::Network}},
      /* The Windows registry: its Run keys start programs at login. Reads stay; every change is refused. */
      {"winreg.SetValue", {Kind::Startup}},
      {"winreg.CreateKey", {Kind::Startup}},
      {"winreg.DeleteKey", {Kind::Startup}},
      {"winreg.DeleteValue", {Kind::Startup}},
      {"winreg.LoadKey", {Kind::Startup}},
      {"winreg.SaveKey", {Kind::Startup}},
      {"winreg.ConnectRegistry", {Kind::Network}},
      /* The whole process's limits: they can't be raised back, and every program Angelo starts inherits them
       * (bpy_angelo_guard_confine_process.cc has the rest of the process's state). */
      /* The environment and the working folder: every program Angelo starts and every relative path it opens
       * (refused inside a region too, whatever installed code makes the change). `os.chdir` is judged as a path
       * first. */
      {"os.putenv", {Kind::Environment}},
      {"os.unsetenv", {Kind::Environment}},
      {"resource.setrlimit", {Kind::ProcessState}},
      {"resource.prlimit", {Kind::ProcessState}},
  };
  return table;
}

/** Event families refused whole on a confined thread: the network and foreign code. */
static std::optional<Kind> family(const char *event)
{
  static const std::pair<const char *, Kind> prefixes[] = {
      {"socket.", Kind::Network},   {"http.client.", Kind::Network}, {"ftplib.", Kind::Network},
      {"smtplib.", Kind::Network},  {"imaplib.", Kind::Network},     {"poplib.", Kind::Network},
      {"nntplib.", Kind::Network},  {"telnetlib.", Kind::Network},   {"ctypes.", Kind::Native},
      {"_ctypes.", Kind::Native},   {"cffi.", Kind::Native},
  };
  for (const auto &[prefix, kind] : prefixes) {
    if (strncmp(event, prefix, strlen(prefix)) == 0) {
      return kind;
    }
  }
  return std::nullopt;
}

static constexpr long WRITE_FLAGS = 0x1 | 0x2 | 0x40 | 0x200 | 0x400 | 0x8 | 0x100 | 0x800;

/** A path argument as UTF-8 text, a descriptor (`r_fd`), or nothing (None or another type: no file). */
static std::optional<std::string> path_text(PyObject *value, int *r_fd)
{
  *r_fd = -1;
  if (value == nullptr || value == Py_None) {
    return std::nullopt;
  }
  if (PyLong_Check(value)) {
    *r_fd = int(PyLong_AsLong(value));
    PyErr_Clear();
    return std::nullopt;
  }
  if (PyUnicode_Check(value)) {
    Py_ssize_t size = 0;
    const char *text = PyUnicode_AsUTF8AndSize(value, &size);
    if (text != nullptr) {
      return std::string(text, size_t(size));
    }
    PyErr_Clear(); /* Surrogates: the system's own encoding. */
    PyObject *encoded = PyUnicode_EncodeFSDefault(value);
    if (encoded == nullptr) {
      PyErr_Clear();
      return std::string("\x01unresolvable"); /* Refused: fail closed. */
    }
    std::string result(PyBytes_AS_STRING(encoded), size_t(PyBytes_GET_SIZE(encoded)));
    Py_DECREF(encoded);
    return result;
  }
  if (PyBytes_Check(value)) {
    return std::string(PyBytes_AS_STRING(value), size_t(PyBytes_GET_SIZE(value)));
  }
  return std::nullopt;
}

static int judge_path(const Event &event, const char *name, PyObject *args, const PathArg &arg)
{
  const Py_ssize_t count = PyTuple_GET_SIZE(args);
  if (arg.index < 0 || arg.index >= count) {
    return 0;
  }
  int dir_fd = -1;
  if (arg.dir_fd >= 0 && arg.dir_fd < count) {
    PyObject *value = PyTuple_GET_ITEM(args, arg.dir_fd);
    if (PyLong_Check(value)) {
      dir_fd = int(PyLong_AsLong(value));
      PyErr_Clear();
    }
  }
  int fd = -1;
  std::optional<std::string> path = path_text(PyTuple_GET_ITEM(args, arg.index), &fd);
  if (path && !path->empty() && (*path)[0] == '\x01') {
    return refuse(PRIVATE, "unresolvable path"); /* Fail closed. */
  }
  char role = arg.role;
  if (role == 'o') {
    PyObject *mode = count > 1 ? PyTuple_GET_ITEM(args, 1) : nullptr;
    PyObject *flags = count > 2 ? PyTuple_GET_ITEM(args, 2) : nullptr;
    bool write = false;
    if (mode != nullptr && PyUnicode_Check(mode)) {
      const char *text = PyUnicode_AsUTF8(mode);
      write = text && strpbrk(text, "wax+") != nullptr;
    }
    else if (flags != nullptr && PyLong_Check(flags)) {
      write = (PyLong_AsLong(flags) & WRITE_FLAGS) != 0;
    }
    PyErr_Clear();
    role = write ? 'w' : 'r';
  }
  if (role == 'd' && path) {
    if (*path == "" || *path == ":memory:") {
      return 0;
    }
    if (path->rfind("file:", 0) == 0) {
      if (path->find("vfs=") != std::string::npos) {
        return refuse(NATIVE, "sqlite3 vfs"); /* Another file system's code, outside every check. */
      }
      std::string rest = path->substr(5);
      rest = rest.substr(0, rest.find('?'));
      if (rest.rfind("//", 0) == 0) {
        rest = rest.substr(2);
      }
      path = rest;
    }
    role = 'w';
  }
  if (role == 'g' && path) {
    const size_t cut = path->find_first_of("*?[");
    std::string folder = path->substr(0, cut);
    const size_t sep = folder.find_last_of("/\\");
    path = sep == std::string::npos ? std::string(".") : folder.substr(0, sep + 1);
    role = 'r';
  }
  if (!path) {
    if (fd < 0) {
      return 0;
    }
    const std::optional<std::string> open_path = descriptor_path(fd);
    if (!open_path) {
      return 0; /* A pipe or a socket: no file. */
    }
    path = open_path;
  }
  const bool write = role == 'w' || role == 's' || role == 'm' || role == 'v' || role == 'l';
  const Change change = role == 'v' ? Change::Moved :
                        role == 'l' ? Change::Linked :
                        (role == 'm' || role == 's') ? Change::Metadata :
                                                      Change::Content;
  const Refusal refusal = folders().judge(*path, write, dir_fd, role == 't', change);
  if (refusal == Refusal::Private) {
    return refuse(PRIVATE, path->c_str());
  }
  if (refusal == Refusal::Startup) {
    return refuse(STARTUP, path->c_str());
  }
  if (refusal == Refusal::ToolOutput) {
    return refuse(TOOL_OUTPUT, path->c_str());
  }
  (void)event;
  (void)name;
  return 0;
}

/** Links a confined script makes: what Angelo's own code later writes through one lands where the link points (a
 * junction, symbolic link or WSL link in a folder Angelo writes, aimed at the person's Documents), and C's record
 * can't follow every route to it. So no confined code makes one: a symbolic link or a junction anywhere
 * (`os.symlink`, `_winapi.CreateJunction`, and what calls them: `shutil.copytree(symlinks=True)`, a tar member),
 * and no hard link to, or move of, something that is itself a link (`os.link`, `os.rename` / `os.replace`,
 * `shutil.move`): a moved or linked link is the same link elsewhere. A link the person made is read as before. */
static int link_refused(const char *name, PyObject *args)
{
  if (strcmp(name, "os.symlink") == 0 || strcmp(name, "_winapi.CreateJunction") == 0) {
    return refuse(GUARD, "a symbolic link or junction: Angelo's own writes would follow it");
  }
  if ((strcmp(name, "os.link") != 0 && strcmp(name, "os.rename") != 0) || PyTuple_GET_SIZE(args) < 1) {
    return 0;
  }
  int fd = -1;
  const std::optional<std::string> source = path_text(PyTuple_GET_ITEM(args, 0), &fd);
  int dir_fd = -1;
  if (PyTuple_GET_SIZE(args) > 2 && PyLong_Check(PyTuple_GET_ITEM(args, 2))) {
    dir_fd = int(PyLong_AsLong(PyTuple_GET_ITEM(args, 2)));
    PyErr_Clear();
  }
  if (source && !source->empty() && last_component_links(*source, dir_fd)) {
    return refuse(GUARD, "a link to, or move of, a symbolic link or junction: Angelo's own writes would follow it");
  }
  return 0;
}

/** Whether the Python frame that raised the event runs agent code. */
static bool innermost_is_agent()
{
  PyFrameObject *frame = PyEval_GetFrame(); /* Borrowed. */
  if (frame == nullptr) {
    return false;
  }
  PyCodeObject *code = PyFrame_GetCode(frame);
  const bool agent = agent_code(code);
  Py_DECREF(code);
  return agent;
}

/** Modules that open files (or run code) natively, out of every check: refused on import while confined. The in-app
 * inventory (test_blender_confinement_native_openers) classifies every bundled extension module against this. */
static bool native_file_module(PyObject *name)
{
  const char *text = name && PyUnicode_Check(name) ? PyUnicode_AsUTF8(name) : nullptr;
  if (text == nullptr) {
    PyErr_Clear();
    return false;
  }
  for (const char *module : NATIVE_FILE_MODULES) {
    const size_t size = strlen(module);
    if (strncmp(text, module, size) == 0 && (text[size] == '\0' || text[size] == '.')) {
      return true;
    }
  }
  return false;
}

/** Whether `os.add_dll_directory`'s folder (its first argument, a path) is a trusted one. */
static bool trusted_folder(PyObject *args)
{
  if (PyTuple_GET_SIZE(args) < 1) {
    return false;
  }
  PyObject *path = PyOS_FSPath(PyTuple_GET_ITEM(args, 0));
  if (path == nullptr) {
    PyErr_Clear();
    return false;
  }
  const char *text = PyUnicode_Check(path) ? PyUnicode_AsUTF8(path) : nullptr;
  const bool trusted = text != nullptr && folders().trusted(text);
  if (text == nullptr) {
    PyErr_Clear();
  }
  Py_DECREF(path);
  return trusted;
}

/* Windows' pipe pair a process makes for itself (`multiprocessing.connection.Pipe`, every pool's change notifier),
 * judged by its arguments, whoever calls: `_winapi.CreateNamedPipe` is wrapped (local_pipe_created, in
 * bpy_angelo_guard_confine_wrap_native.cc) and lets through only a fresh, single-instance pipe in this machine's
 * local namespace (`\\.\pipe\`); its name is remembered on this thread, and one `_winapi.CreateFile` of exactly
 * that name (the process connecting to itself) passes; every other pipe, remote (`\\server\pipe\`, `\\?\UNC\`)
 * or created elsewhere, is judged as before: refused. */
static thread_local std::vector<std::string> pipes_to_connect;
static thread_local bool creating_local_pipe = false;

void local_pipe_creating(bool creating)
{
  creating_local_pipe = creating;
}

void local_pipe_created(const std::string &name)
{
  pipes_to_connect.push_back(name);
}

std::string pipe_key(const char *name)
{
  std::string key(name);
  for (char &c : key) {
    c = char(tolower((unsigned char)c));
    if (c == '/') {
      c = '\\';
    }
  }
  return key;
}

static bool local_pipe_event(const char *name, PyObject *args)
{
  if (strcmp(name, "_winapi.CreateNamedPipe") == 0) {
    return creating_local_pipe; /* The wrapper judged its arguments. */
  }
  if (strcmp(name, "_winapi.CreateFile") != 0 || pipes_to_connect.empty() || PyTuple_GET_SIZE(args) < 1) {
    return false;
  }
  PyObject *file = PyTuple_GET_ITEM(args, 0);
  const char *text = PyUnicode_Check(file) ? PyUnicode_AsUTF8(file) : nullptr;
  if (text == nullptr) {
    PyErr_Clear();
    return false;
  }
  const std::string key = pipe_key(text);
  for (auto it = pipes_to_connect.begin(); it != pipes_to_connect.end(); ++it) {
    if (*it == key) {
      pipes_to_connect.erase(it); /* Once: the process connecting to the pipe it just made. */
      return true;
    }
  }
  return false;
}

static int judge(const char *name, const Event &event, PyObject *args)
{
  if (local_pipe_event(name, args)) {
    return 0;
  }
  switch (event.kind) {
    case Kind::File:
      for (const PathArg &arg : event.paths) {
        if (arg.role != 0 && judge_path(event, name, args, arg) != 0) {
          return -1;
        }
      }
      if (link_refused(name, args) != 0) {
        return -1; /* After the path rules: a link into a private folder is refused as that. */
      }
      return strcmp(name, "os.chdir") == 0 ?
                 process_change_refused("os.chdir (use absolute paths instead)", true) :
                 0;
    case Kind::Environment:
      /* An installed library setting a variable as it loads is a developer tool's setup (USD's schema generator
       * disables prim definitions process-wide): it is refused too, and says so. */
      return process_change_refused(
          in_installed_import() ?
              "the environment, set by a library as it loads (a developer tool, such as USD's schema generator: run "
              "it outside Angelo)" :
              "the environment (Angelo's, inherited by every program it starts: pass a setting to the code that "
              "needs it instead)",
          true);
    case Kind::Process:
      return refuse(PROGRAMS, name);
    case Kind::Signal:
      return signal_refused(name, args, true);
    case Kind::Native:
      /* A DLL folder an installed package adds as it imports (pxr's `Tf.WindowsImportWrapper` adds the app's own
       * shared library folder): only a trusted folder, which no script can write, so nothing is planted there. */
      if (strcmp(name, "os.add_dll_directory") == 0 && trusted_folder(args)) {
        return 0;
      }
      return refuse(NATIVE, name);
    case Kind::Network:
      return making_socketpair > 0 ? 0 : refuse(NETWORK, name);
    case Kind::Guard:
      return refuse(GUARD, name);
    case Kind::Startup:
      return refuse(STARTUP, name);
    case Kind::ProcessState:
      return refuse(PROCESS, name);
    case Kind::Thread: {
      PyObject *function = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
      /* The wrapped starts (install_wrappers) give a confined thread's function the trampoline: any other start
       * from a confined thread is refused. */
      if (function == nullptr || Py_TYPE(function) != &deferred_type) {
        return refuse(GUARD, name);
      }
      return 0;
    }
    case Kind::Import: {
      if (native_file_module(PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr)) {
        return refuse(NATIVE, "a module that opens files natively");
      }
      PyObject *file = PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) : nullptr;
      if (file == nullptr || !PyUnicode_Check(file)) {
        return 0;
      }
      const char *text = PyUnicode_AsUTF8(file);
      if (text == nullptr) {
        PyErr_Clear();
        return 0;
      }
      std::string lower = text;
      for (char &c : lower) {
        c = char(tolower((unsigned char)c));
      }
      for (const char *suffix : {".so", ".pyd", ".dylib", ".dll"}) {
        const size_t size = strlen(suffix);
        if (lower.size() >= size && lower.compare(lower.size() - size, size, suffix) == 0 &&
            !folders().trusted(text))
        {
          return refuse(NATIVE, text);
        }
      }
      return 0;
    }
    case Kind::SetAttr: {
      PyObject *attribute = PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) : nullptr;
      if (attribute == nullptr || !PyUnicode_Check(attribute)) {
        return 0;
      }
      /* A function's code and defaults: the agent may change its own functions' (`namedtuple`'s defaults on the
       * `__new__` it made); an installed function's only installed code may change (`types.coroutine`), never the
       * agent's own frame. Classes are journaled by their setattr (bpy_angelo_guard_confine_types.cc). */
      PyObject *subject = PyTuple_GET_ITEM(args, 0);
      PyObject *value = PyTuple_GET_SIZE(args) > 2 ? PyTuple_GET_ITEM(args, 2) : nullptr;
      if (PyUnicode_CompareWithASCIIString(attribute, "__code__") == 0 && value != nullptr && PyCode_Check(value) &&
          !agent_code((PyCodeObject *)value) && PyFunction_Check(subject) &&
          agent_code((PyCodeObject *)PyFunction_GetCode(subject)))
      {
        return refuse(GUARD, "__code__"); /* The agent's own function made to run installed code: laundering. */
      }
      for (const char *guarded : {"__code__", "__defaults__", "__kwdefaults__", "__globals__", "__closure__"}) {
        if (PyUnicode_CompareWithASCIIString(attribute, guarded) == 0) {
          const bool own = PyFunction_Check(subject) &&
                           agent_code((PyCodeObject *)PyFunction_GetCode(subject));
          return own || !innermost_is_agent() ? 0 : refuse(GUARD, guarded);
        }
      }
      return 0;
    }
  }
  return 0;
}

static bool generic_name(PyObject *name)
{
  for (const char *text : {"<string>", "<unknown>", "<stdin>", "<expression>", "<frozen>", "<fstring>"}) {
    if (PyUnicode_CompareWithASCIIString(name, text) == 0) {
      return true;
    }
  }
  return false;
}

static bool trusted_name(PyObject *name)
{
  const char *text = PyUnicode_AsUTF8(name);
  if (text == nullptr) {
    PyErr_Clear();
    return false;
  }
  return folders().trusted(text);
}

static int on_exec(PyObject *args)
{
  PyObject *code = PyTuple_GET_SIZE(args) > 0 ? PyTuple_GET_ITEM(args, 0) : nullptr;
  if (code == nullptr || !PyCode_Check(code) || busy) {
    return 0;
  }
  PyCodeObject *object = (PyCodeObject *)code;
  if (region_seeded && confined_now() && !agent_code(object) && installed_body(object)) {
    /* An installed module's top level, in a region after the script put its own code into installed modules: a
     * top level could call what it chose. Every import reaches here (an import statement, `importlib`, a loader
     * called directly); the import system removes the module again. */
    return refuse_seeded_import();
  }
  busy = true;
  if (depth == 0 && PyUnicode_Check(object->co_filename) &&
      PyUnicode_CompareWithASCIIString(object->co_filename, MARK) == 0 && !frames_confined())
  {
    /* An agent script run by plain `exec`, not `run_confined` (the executor was rebound): confined until the next
     * `run_confined` ends, failing closed. */
    armed.store(true);
    pending = true;
  }
  else if (armed.load(std::memory_order_relaxed) && !agent_code(object) && confined_now() &&
           !trusted_name(object->co_filename) && installed_import_globals() == nullptr)
  {
    /* (Code an installed import makes as it loads afresh, unseeded, is its own definition: `@dataclass`'s and
     * `namedtuple`'s generated methods, journal.cc.) */
    taint(code);
  }
  busy = false;
  return 0;
}

static int on_compile(PyObject *args)
{
  PyObject *name = PyTuple_GET_SIZE(args) > 1 ? PyTuple_GET_ITEM(args, 1) : nullptr;
  if (name == nullptr || !PyUnicode_Check(name) || busy || generic_name(name)) {
    return 0;
  }
  busy = true;
  if (confined_now() && !trusted_name(name) && installed_import_globals() == nullptr) {
    taint_name(name);
  }
  busy = false;
  return 0;
}

/* -------------------------------------------------------------------- */
/** \name Code a script wrote, loaded by Angelo itself
 *
 * Code Angelo loads from a file a confined thread wrote (or renamed, linked, or made the folder of), or from tool
 * output, is the agent's whoever imports it: a script can steer a later import of Angelo's to such a file through
 * data it leaves (`sys.path`, `sys.path_importer_cache`, `sys.meta_path` entries), so the load itself is judged.
 * Python reads every module's source or bytecode, and every zip archive it imports from, through `io.open_code`
 * (the hook below); native extensions arrive through the `import` event. Unconfined, such a load is refused: the
 * import fails rather than run the script's code as Angelo's. A confined thread loads its own files as before (its
 * code is confined).
 * \{ */

static PyObject *open_code_hook(PyObject *path, void * /*user_data*/)
{
  static PyObject *io_open = nullptr;
  if (io_open == nullptr) {
    PyObject *io = PyImport_ImportModule("_io");
    io_open = io ? PyObject_GetAttrString(io, "open") : nullptr;
    Py_XDECREF(io);
    if (io_open == nullptr) {
      return nullptr;
    }
  }
  if (armed.load(std::memory_order_relaxed) && PyUnicode_Check(path) && !confined_now()) {
    const char *text = PyUnicode_AsUTF8(path);
    if (text != nullptr && folders().agent_made(text)) {
      refuse(GUARD, text);
      return nullptr;
    }
    PyErr_Clear();
  }
  return PyObject_CallFunction(io_open, "Os", path, "rb");
}

void install_open_code_hook()
{
  if (PyFile_SetOpenCodeHook(open_code_hook, nullptr) != 0) {
    PyErr_Clear(); /* Another embedder's hook: doc/angelo/script_confinement.rst lists it. */
  }
}

/** An extension module (or a library `ctypes` loads) Angelo itself loads from a file a script wrote: refused. */
static int agent_native_load(const char *event, PyObject *args)
{
  const bool import = strcmp(event, "import") == 0;
  if (!import && strcmp(event, "ctypes.dlopen") != 0) {
    return 0;
  }
  PyObject *file = PyTuple_GET_SIZE(args) > (import ? 1 : 0) ? PyTuple_GET_ITEM(args, import ? 1 : 0) : nullptr;
  if (file == nullptr || !PyUnicode_Check(file)) {
    return 0;
  }
  const char *text = PyUnicode_AsUTF8(file);
  if (text == nullptr) {
    PyErr_Clear();
    return 0;
  }
  return folders().agent_made(text) ? refuse(NATIVE, text) : 0;
}

/** \} */

int hook(const char *event, PyObject *args, void * /*user_data*/)
{
  if (event[0] == 'e' && strcmp(event, "exec") == 0) {
    return on_exec(args);
  }
  if (!armed.load(std::memory_order_relaxed) || busy) {
    return 0;
  }
  if (event[0] == 's' && (strcmp(event, "sys.unraisablehook") == 0 || strcmp(event, "sys.excepthook") == 0)) {
    /* The interpreter about to call a hook in `sys` (callbacks.cc): Angelo's own runs instead when the script set
     * it and the caller isn't confined. Not busy: Angelo's hook is judged like any of its code. */
    return callback_refused(event, args);
  }
  if (event[0] == 'c' && strcmp(event, "compile") == 0) {
    busy = true;
    const int refused = confined_now() ? provenance_compile(args) : 0;
    busy = false;
    return refused != 0 ? refused : on_compile(args);
  }
  if (strcmp(event, "code.__new__") == 0 || strncmp(event, "marshal.load", 12) == 0) {
    busy = true;
    int result = 0;
    if (confined_now()) {
      result = event[0] == 'c' ? provenance_code_new(args) : provenance_marshal(event, args);
    }
    busy = false;
    return result;
  }
  const auto &table = events();
  const auto found = table.find(std::string_view(event));
  std::optional<Event> entry;
  if (found != table.end()) {
    entry = found->second;
  }
  else if (const std::optional<Kind> kind = family(event)) {
    entry = Event{*kind};
  }
  if (!entry) {
    return 0;
  }
  busy = true;
  int result = 0;
  if (entry->kind == Kind::Thread) {
    trace_hooks_know();
    trace_hooks_put_back(); /* Every new thread runs Angelo's own tracer and profiler (trace_hooks.cc). */
    callbacks_thread_starting(args); /* Its uncaught exception goes through C (callbacks.cc). */
  }
  if (confined_now()) {
    result = judge(event, *entry, args);
  }
  else if (entry->kind == Kind::Guard) {
    result = trace_hook_refused(event); /* A new thread's `sys.settrace` from `threading` (trace_hooks.cc). */
  }
  else if (entry->kind == Kind::Import || entry->kind == Kind::Native) {
    result = agent_native_load(event, args);
  }
  else if (entry->kind == Kind::Signal) {
    result = signal_refused(event, args, false); /* Outside a script too: targets from Python state are checked. */
  }
  busy = false;
  return result;
}

}  // namespace blender::angelo_guard
