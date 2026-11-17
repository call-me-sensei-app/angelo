/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Python a project carries, which runs when the file opens or as it is evaluated or rendered
 * (doc/angelo/script_confinement.rst, "Work that outlives the script"). Blender runs Python from file data in three
 * places: a text set to run at load ("Register", bpy_angelo_guard_confine_texts.cc), a scripted driver's expression
 * (bpy_driver.cc) and a Freestyle style module (a text a view layer runs when it renders). For the last two:
 *
 * - while confined, writing one is refused: a driver expression only if it needs Python (a simple expression, math on
 *   its variables, is evaluated without Python and works), a style module's script or switching one on;
 * - when the outermost region on the main thread starts, the ones present are noted; a .blend written while confined
 *   keeps them as they were, and anything else is written inert: a driver's Python stays off ("Python restricted for
 *   security", until the person edits the expression), a style module is switched off; at the script's end the live
 *   ones are made inert the same way (appended data included);
 * - linked data the script brought in that carries a text set to run at load or a style module (whose code would
 *   come back from its library file at every open, whatever library it came through) can't be saved into a project
 *   while confined, and is removed at the script's end.
 *
 * A driver's Python, wherever it came from, always runs confined: its code is the driver's (`DRIVER`), and the first
 * evaluation arms the confinement (bpy_driver.cc), so a file opened before any script runs is covered too.
 */

#include <Python.h>

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "BLI_expr_pylike_eval.hh"
#include "BLI_threads.hh"

#include "BKE_anim_data.hh"
#include "BKE_blendfile_link_append.hh"
#include "BKE_global.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"

#include "DNA_anim_types.h"
#include "DNA_freestyle_types.h"
#include "DNA_layer_types.h"
#include "DNA_scene_types.h"
#include "DNA_text_types.h"

#include "bpy_angelo_guard_confine_intern.hh"

namespace blender::angelo_guard {

/* -------------------------------------------------------------------- */
/** \name Whether a driver needs Python
 * \{ */

static bool keyword(const std::string &word)
{
  for (const char *python : {"and", "or", "not", "if", "else", "in", "is", "lambda", "for", "None", "True",
                             "False", "import", "yield", "await"})
  {
    if (word == python) {
      return true;
    }
  }
  return false;
}

/** Whether Blender's simple-expression evaluator takes `expression` whatever the driver's variables are called: every
 * name not called as a function counts as a variable, so the order a script adds variables in doesn't matter. */
static bool simple_grammar(const char *expression)
{
  std::vector<std::string> names;
  const size_t size = strlen(expression);
  for (size_t i = 0; i < size;) {
    const unsigned char c = expression[i];
    if (std::isdigit(c) || (c == '.' && i + 1 < size && std::isdigit((unsigned char)expression[i + 1]))) {
      while (i < size && (std::isalnum((unsigned char)expression[i]) || expression[i] == '.' ||
                          expression[i] == '_'))
      {
        i++;
      }
      continue;
    }
    if (std::isalpha(c) || c == '_') {
      const size_t start = i;
      while (i < size && (std::isalnum((unsigned char)expression[i]) || expression[i] == '_')) {
        i++;
      }
      size_t next = i;
      while (next < size && std::isspace((unsigned char)expression[next])) {
        next++;
      }
      std::string word(expression + start, i - start);
      if ((next >= size || expression[next] != '(') && !keyword(word) &&
          std::find(names.begin(), names.end(), word) == names.end())
      {
        names.push_back(std::move(word));
      }
      continue;
    }
    i++;
  }
  std::vector<const char *> pointers;
  pointers.reserve(names.size());
  for (const std::string &name : names) {
    pointers.push_back(name.c_str());
  }
  ExprPyLike_Parsed *parsed = BLI_expr_pylike_parse(expression, pointers.data(), int(pointers.size()));
  const bool simple = BLI_expr_pylike_is_valid(parsed);
  BLI_expr_pylike_free(parsed);
  return simple;
}

static bool needs_python(const char *expression, int type)
{
  return type == DRIVER_TYPE_PYTHON && expression != nullptr && expression[0] != '\0' &&
         !simple_grammar(expression);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name What the region found (main thread only)
 * \{ */

static std::vector<uint64_t> drivers_found;
static std::vector<std::pair<uint32_t, uint64_t>> modules_found;
static std::vector<uint32_t> linked_found; /* Linked data carrying code, by session UID. */

static uint64_t mix(uint64_t hash, const char *text)
{
  for (const char *c = text; c && *c; c++) {
    hash = (hash ^ uint8_t(*c)) * 1099511628211ull;
  }
  return (hash ^ 0xff) * 1099511628211ull;
}

static uint64_t driver_key(const FCurve *fcurve)
{
  uint64_t hash = mix(1469598103934665603ull, fcurve->rna_path_ptr);
  hash = (hash ^ uint64_t(uint32_t(fcurve->array_index))) * 1099511628211ull;
  return mix(hash, fcurve->driver->expression);
}

static bool python_driver(const FCurve *fcurve)
{
  return fcurve->driver != nullptr && needs_python(fcurve->driver->expression, fcurve->driver->type);
}

static bool runs_module(const FreestyleModuleConfig *module)
{
  return module->script != nullptr && module->is_displayed;
}

static std::pair<uint32_t, uint64_t> module_key(const FreestyleModuleConfig *module)
{
  return {module->script->id.session_uid, text_content(module->script)};
}

/** `fn(owner, fcurve)`: every driver needing Python, in each data-block and the IDs embedded in it (a material's node
 * tree), with the data-block that owns it (its library is the owner's). */
template<typename Fn> static void each_python_driver(Main *bmain, Fn fn)
{
  ID *id;
  FOREACH_MAIN_ID_BEGIN (bmain, id) {
    BKE_angelo_foreach_animdata(bmain, id, [&](ID * /*self*/, AnimData *animation) {
      for (FCurve &fcurve : animation->drivers) {
        if (python_driver(&fcurve)) {
          fn(id, &fcurve);
        }
      }
    });
  }
  FOREACH_MAIN_ID_END;
}

template<typename Fn> static void each_running_module(Main *bmain, Fn fn)
{
  for (Scene &scene : bmain->scenes) {
    for (ViewLayer &view_layer : scene.view_layers) {
      for (FreestyleModuleConfig &module : view_layer.freestyle_config.modules) {
        if (runs_module(&module)) {
          fn(&scene, &module);
        }
      }
    }
  }
}

/** Linked data that carries code: a text set to run at load, and a scene with a style module switched on. */
template<typename Fn> static void each_linked_carrier(Main *bmain, Fn fn)
{
  for (Text &text : bmain->texts) {
    if (ID_IS_LINKED(&text.id) && (text.flags & TXT_ISSCRIPT)) {
      fn(&text.id);
    }
  }
  Scene *last = nullptr;
  each_running_module(bmain, [&](Scene *scene, FreestyleModuleConfig *) {
    if (ID_IS_LINKED(&scene->id) && scene != last) {
      last = scene;
      fn(&scene->id);
    }
  });
}

void carriers_snapshot()
{
  if (!BLI_thread_is_main() || G_MAIN == nullptr) {
    return;
  }
  drivers_found.clear();
  modules_found.clear();
  linked_found.clear();
  each_python_driver(G_MAIN, [](ID *, FCurve *fcurve) { drivers_found.push_back(driver_key(fcurve)); });
  each_running_module(G_MAIN, [](Scene *, FreestyleModuleConfig *module) {
    modules_found.push_back(module_key(module));
  });
  each_linked_carrier(G_MAIN, [](ID *id) { linked_found.push_back(id->session_uid); });
}

static bool driver_noted(const FCurve *fcurve)
{
  const uint64_t key = driver_key(fcurve);
  return std::find(drivers_found.begin(), drivers_found.end(), key) != drivers_found.end();
}

static bool module_noted(const FreestyleModuleConfig *module)
{
  return std::find(modules_found.begin(), modules_found.end(), module_key(module)) != modules_found.end();
}

bool driver_kept(const void *fcurve)
{
  const FCurve *curve = static_cast<const FCurve *>(fcurve);
  return !confined_now() || !python_driver(curve) || driver_noted(curve);
}

bool style_module_kept(const void *module)
{
  const FreestyleModuleConfig *config = static_cast<const FreestyleModuleConfig *>(module);
  return !confined_now() || !runs_module(config) || module_noted(config);
}

static bool linked_noted(const ID *id)
{
  return std::find(linked_found.begin(), linked_found.end(), id->session_uid) != linked_found.end();
}

const char *new_library_refusal()
{
  if (!BLI_thread_is_main() || G_MAIN == nullptr || !confined_now()) {
    return nullptr;
  }
  bool found = false;
  each_linked_carrier(G_MAIN, [&](ID *id) { found = found || !linked_noted(id); });
  return found ? AUTORUN : nullptr;
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name The script's end: what it brought in is made inert
 * \{ */

static void finding(PyObject *findings, const char *text)
{
  if (findings != nullptr) {
    if (PyObject *line = PyUnicode_FromString(text)) {
      PyList_Append(findings, line);
      Py_DECREF(line);
    }
  }
  PyErr_Clear();
}

void carriers_sweep(PyObject *findings)
{
  if (!BLI_thread_is_main() || G_MAIN == nullptr) {
    return;
  }
  int drivers = 0, modules = 0;
  /* (A linked driver can't be changed in its library: it runs confined like every driver's Python.) */
  each_python_driver(G_MAIN, [&](ID *owner, FCurve *fcurve) {
    if (!driver_noted(fcurve) && !(fcurve->driver->flag & DRIVER_FLAG_ANGELO_UNTRUSTED) && !ID_IS_LINKED(owner)) {
      fcurve->driver->flag |= DRIVER_FLAG_ANGELO_UNTRUSTED;
      drivers++;
    }
  });
  each_running_module(G_MAIN, [&](Scene *scene, FreestyleModuleConfig *module) {
    if (!module_noted(module) && !ID_IS_LINKED(&scene->id)) {
      module->is_displayed = 0;
      modules++;
    }
  });
  if (drivers > 0) {
    finding(findings, "driver expressions that run Python, from the script: their Python no longer runs");
  }
  if (modules > 0) {
    finding(findings, "style modules the script set: switched off");
  }
  /* Linked data the script brought in that carries code (it would come back from its library at every open):
   * removed. */
  std::vector<ID *> brought;
  each_linked_carrier(G_MAIN, [&](ID *id) {
    if (!linked_noted(id)) {
      brought.push_back(id);
    }
  });
  if (!brought.empty()) {
    BKE_main_id_tag_all(G_MAIN, ID_TAG_DOIT, false);
    for (ID *id : brought) {
      id->tag |= ID_TAG_DOIT;
    }
    BKE_id_multi_tagged_delete(G_MAIN);
    finding(findings, "linked data the script brought in, with code that runs when a file opens: removed");
  }
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Writing them, refused while confined
 * \{ */

const char *driver_refusal(const void *driver, const char *expression, int type)
{
  if (!confined_now()) {
    return nullptr;
  }
  const ChannelDriver *channel = static_cast<const ChannelDriver *>(driver);
  return needs_python(expression ? expression : channel->expression, type >= 0 ? type : channel->type) ?
             DRIVER_CODE :
             nullptr;
}

/** \} */

}  // namespace blender::angelo_guard
