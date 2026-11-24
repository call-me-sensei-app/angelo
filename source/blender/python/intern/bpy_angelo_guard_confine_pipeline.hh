/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * The pipeline libraries' path gates (doc/angelo/script_confinement_pipeline.rst), shared by the gate machinery
 * (bpy_angelo_guard_confine_wrap_pipeline.cc), its table (bpy_angelo_guard_confine_pipeline_table.cc) and the
 * libraries' special cases (bpy_angelo_guard_confine_pipeline_special.cc).
 */

#include <Python.h>

#include <string>
#include <vector>

namespace blender::angelo_guard {

/** How an entry point is held: where the gate goes and how it calls the original. */
enum class Kind : char {
  Function,    /* A module-level function. */
  Method,      /* An instance method: `self` comes first. */
  Static,      /* A static method. */
  ClassMethod, /* A class method: the class comes first. */
  Init,        /* `__init__` (a heap type's attribute, or a static type's `tp_init` slot). */
  New,         /* A static type's `tp_new` slot (`aud.Sound(filename)`). */
};

/** What an argument is. */
enum class Role : char {
  None,
  Read,        /* A file read. */
  Write,       /* A file written or made. */
  Folders,     /* Folders read: a sequence, or a string separated by `:` or `;`. */
  Native,      /* A plugin search path: refused when not empty (native code from a folder). */
  AnyRead,     /* Every string argument, read (signatures the library does not publish). */
  AnyWrite,    /* Every string argument, written. */
};

/** An argument: its position after `self` or the class (-1: none) and its keyword (null: none). */
struct Arg {
  signed char position;
  const char *keyword;
  Role role;
};

/** Library-specific handling, run before the arguments are judged (pipeline_special.cc). */
enum class Special : char {
  None,
  Refuse,           /* Refused outright while confined (native code: `Plug.Registry.RegisterPlugins`). */
  RefuseGlobal,     /* Refused outright while confined (a process-wide setting outliving the script). */
  RefuseProgram,    /* Refused outright while confined (opening it can start a program: a sound server). */
  SearchName,       /* arg 0 a name searched for in arg 1's folders (oslquery: `name`, `name.oso`). */
  MxRead,           /* MaterialX readers: arg 0/1 judged against the search path; XIncludes judged. */
  MxXInclude,       /* MaterialX: the read options' XInclude reader judged (readOptions position in args[1]). */
  MxSelf,           /* MaterialX `FilePath` methods: the path itself (`self`). */
  OiioAttribute,    /* OpenImageIO's global `attribute(name, value)`. */
  OiioCacheAttribute, /* `ImageCache`/`TextureSystem.attribute(name, value)`. */
  OiioCacheName,    /* A file name searched in `self`'s search path. */
  OiioSharedName,   /* A file name searched in the shared ImageCache's search path. */
  OiioColorConfig,  /* A color configuration file (or `$OCIO`). */
  OiioLut,          /* A LUT name (`ociofiletransform`). */
  OcioEnv,          /* `$OCIO`: the configuration a call reads from the environment. */
  OcioProcessor,    /* Configurations whose processors read LUTs: judged through an IO proxy. */
  OcioSetCurrent,   /* `SetCurrentConfig`: the process-wide configuration reads LUTs through the proxy. */
};

struct Gate {
  const char *module;   /* The extension module that defines it (its spec name). */
  const char *owner;    /* A class (dotted for nested ones), "*" for every class that defines `name`, or null. */
  const char *name;
  Kind kind;
  Arg args[3];
  Special special;
  /* Filled at install. */
  PyObject *original;   /* What is called: unbound for methods. */
  PyObject *raw;        /* The class dict's entry (a class method's descriptor, to bind it to the class). */
  void *slot;           /* A static type's original `tp_init`/`tp_new`. */
  PyObject *installed_in; /* The module object the gate was installed for (strong reference). */
  PyObject *object;       /* A module function's gate object (strong reference). */
};

/** The table (pipeline_table.cc). */
extern Gate gates[];
extern const size_t gate_count;

/** Calling a gate (wrap_pipeline.cc): `args` with `self` or the class first for methods. */
PyObject *gate_call(Gate &gate, PyObject *args, PyObject *kwargs);

/** An argument (after `self` for methods: `shift` 1), or null (borrowed). */
PyObject *gate_argument(PyObject *args, PyObject *kwargs, Py_ssize_t shift, const Arg &arg);
/** A path-like argument's text (str, bytes, os.PathLike, a MaterialX `FilePath`), or empty when it is none. */
std::string path_text(PyObject *value);
/** Folders in a sequence, a separated string or a MaterialX `FileSearchPath`. */
std::vector<std::string> folder_list(PyObject *value);
/** The last refusal judged on this thread (a library may wrap the exception in its own). */
extern thread_local const char *pending_refusal;
/** -1 with ScriptConfined set when `path` is refused (an empty path names nothing); `what` names the entry point. */
int judge_text(const std::string &path, bool write, const char *what);
/** `name`, and a relative one in each folder, judged for reading; -1 when refused. */
int judge_search(const std::string &name, const std::vector<std::string> &folders, const char *what);
/** The text of a str or bytes object (borrowed object; empty when it is neither). */
std::string text_of(PyObject *value);

/** The special cases (pipeline_special.cc): -1 with an exception set when refused; may replace `*args`/`*kwargs`
 * (new references) with what the original is called with. */
int special_before(Gate &gate, PyObject **args, PyObject **kwargs);
/** OpenColorIO (pipeline_ocio.cc): every OpenColorIO `Config` among the arguments (and `self`) replaced by a copy
 * whose LUTs are read through the judging proxy; OpenImageIO's color configuration (a file, a built-in URI or empty
 * for `$OCIO`) judged with every transform it holds, or only `lut`; `$OCIO`; a built-in URI. */
int judge_configs(const Gate &gate, PyObject **args, PyObject **kwargs);
int judge_color_config(const Gate &gate, std::string path, const char *lut);
std::string environment_config();
bool builtin_config(const std::string &config);
/** A confined OpenColorIO call failed: its "missing file" error naming a refused path becomes the refusal. */
void ocio_missing_refusal(const Gate &gate);

/** What the special cases prepare at install, before any script (OpenColorIO's audit). */
void special_prepare();
/** Patch a library's types once its module exists (MaterialX's `readXIncludeFunction` property). */
void special_install(PyObject *module, const char *module_name);

/** The gate type and its objects (wrap_pipeline.cc). */
extern PyTypeObject gate_type;
int gate_type_ready();
PyObject *gate_object_new(Gate &gate);
/** Whether a class dict entry is a gate already (directly, or inside a static or class method). */
bool gated(PyObject *raw);
/** The special case, then every argument the table names: -1 with ScriptConfined set when refused. */
int gate_check(Gate &gate, PyObject **args, PyObject **kwargs);
/** Whether USD's primary resolver is the confined one (set at install). */
extern bool usd_confined;

/** Installing (pipeline_install.cc): `_imp`'s module makers wrapped, and modules loaded already gated. */
void install_hooks();
void gate_loaded_modules();

/** The confined USD resolver's judge (any thread, no GIL). */
const char *usd_judge(const char *path, bool write);

}  // namespace blender::angelo_guard
