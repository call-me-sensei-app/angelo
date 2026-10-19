/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup pythonintern
 *
 * Names Blender 4.x or 5.x renamed or removed that scripts still write, and the sentence that
 * the error such a name raises gains: the replacement in Blender 5.2.
 *
 * Error text only. There are no aliases: every listed name fails exactly as in stock Blender,
 * with the same exception type at the same point, so a script that runs here runs in stock
 * Blender too. The table is consulted only after a lookup has already failed, so a script
 * that never uses a listed name pays nothing.
 *
 * Each row is checked against this build by `test_blender_renamed_api_errors.py`: the old name
 * fails and the replacement exists. A row needs recorded agent scripts that used the old name,
 * or must be an exact rename that is trivially safe to name (see the per-row comments).
 */

#include <Python.h>

#include "BLI_string.h"
#include "BLI_utildefines.h"

#include "RNA_access.hh"

#include "bpy_angelo_renamed_api.hh"

namespace blender {

namespace {

struct RenamedName {
  BPyRenamedUse use;
  /** RNA struct identifier of the type that had the name; subtypes match too. */
  const char *type;
  /** `EnumItem` and `CollectionKey`: identifier of the property the name was used in. */
  const char *property;
  /** The old name, exactly as a script writes it. */
  const char *name;
  /** Appended to the error after ". ". */
  const char *hint;
};

/* clang-format off */
const RenamedName renamed_names[] = {
    /* Recorded agent scripts (October 2026 benchmark runs, Angelo and Blender + MCP, three
     * models): these four were the names of 36 of 93 scripts that raised. */
    {BPyRenamedUse::EnumItem, "ShaderNodeTexSky", "sky_type", "NISHITA",
     "Blender 5.0 renamed 'NISHITA' to 'SINGLE_SCATTERING' (the same model: files saved with "
     "'NISHITA' open with it); the new default is 'MULTIPLE_SCATTERING'"},
    {BPyRenamedUse::EnumItem, "RenderSettings", "engine", "BLENDER_EEVEE_NEXT",
     "Blender 5.0 renamed 'BLENDER_EEVEE_NEXT' to 'BLENDER_EEVEE'"},
    {BPyRenamedUse::Attribute, "ShaderNodeTexSky", nullptr, "dust_density",
     "Blender 5.0 renamed 'dust_density' to 'aerosol_density'"},
    {BPyRenamedUse::Attribute, "Action", nullptr, "fcurves",
     "Blender 5.0 removed Action.fcurves: F-Curves are in a slot's channelbag, for example "
     "bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot).fcurves, where slot is "
     "the animated ID's animation_data.action_slot"},
    /* No recorded use: removed in the same change as `fcurves`, with the same replacement. */
    {BPyRenamedUse::Attribute, "Action", nullptr, "groups",
     "Blender 5.0 removed Action.groups: F-Curve groups are in a slot's channelbag, for example "
     "bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot).groups, where slot is "
     "the animated ID's animation_data.action_slot"},
    /* No recorded use: Blender 4.0's exact renames of Principled BSDF inputs (the same renames
     * its file versioning applies). Inputs that were removed or only changed type are not listed. */
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Subsurface",
     "Blender 4.0 renamed the Principled BSDF input 'Subsurface' to 'Subsurface Weight'"},
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Specular",
     "Blender 4.0 renamed the Principled BSDF input 'Specular' to 'Specular IOR Level'"},
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Transmission",
     "Blender 4.0 renamed the Principled BSDF input 'Transmission' to 'Transmission Weight'"},
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Clearcoat",
     "Blender 4.0 renamed the Principled BSDF input 'Clearcoat' to 'Coat Weight'"},
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Clearcoat Roughness",
     "Blender 4.0 renamed the Principled BSDF input 'Clearcoat Roughness' to 'Coat Roughness'"},
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Clearcoat Normal",
     "Blender 4.0 renamed the Principled BSDF input 'Clearcoat Normal' to 'Coat Normal'"},
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Sheen",
     "Blender 4.0 renamed the Principled BSDF input 'Sheen' to 'Sheen Weight'"},
    {BPyRenamedUse::CollectionKey, "ShaderNodeBsdfPrincipled", "inputs", "Emission",
     "Blender 4.0 renamed the Principled BSDF input 'Emission' to 'Emission Color'"},
};
/* clang-format on */

/** Whether `type` is the struct named `identifier` or derives from it. */
bool struct_is_or_derives(StructRNA *type, const char *identifier)
{
  for (StructRNA *base = type; base; base = RNA_struct_base(base)) {
    if (STREQ(RNA_struct_identifier(base), identifier)) {
      return true;
    }
  }
  return false;
}

}  // namespace

const char *BPY_angelo_renamed_api_hint(const BPyRenamedUse use,
                                        StructRNA *type,
                                        const char *property_id,
                                        const char *name)
{
  if (type == nullptr || name == nullptr) {
    return nullptr;
  }
  for (const RenamedName &row : renamed_names) {
    if (row.use != use || !STREQ(row.name, name)) {
      continue;
    }
    if (row.property && !(property_id && STREQ(row.property, property_id))) {
      continue;
    }
    if (struct_is_or_derives(type, row.type)) {
      return row.hint;
    }
  }
  return nullptr;
}

void BPY_angelo_renamed_api_attribute_error(StructRNA *type, const char *name)
{
  if (!PyErr_ExceptionMatches(PyExc_AttributeError)) {
    return;
  }
  const char *hint = BPY_angelo_renamed_api_hint(BPyRenamedUse::Attribute, type, nullptr, name);
  if (hint == nullptr) {
    return;
  }

  /* Rewrite the pending exception in place, so its type, traceback and `name` stay those of the
   * failed lookup. Clearing `obj` stops Python from appending its closest-spelling guess, which
   * for `dust_density` is the unrelated `air_density`. */
  PyObject *error = PyErr_GetRaisedException();
  bool rewritten = false;
  if (PyObject *text = PyObject_Str(error)) {
    if (PyObject *message = PyUnicode_FromFormat("%U. %s", text, hint)) {
      if (PyObject *args = PyTuple_Pack(1, message)) {
        rewritten = PyObject_SetAttrString(error, "args", args) == 0 &&
                    PyObject_SetAttrString(error, "obj", Py_None) == 0;
        Py_DECREF(args);
      }
      Py_DECREF(message);
    }
    Py_DECREF(text);
  }
  if (!rewritten) {
    /* Out of memory or similar while extending the text: keep the original error. */
    PyErr_Clear();
  }
  PyErr_SetRaisedException(error);
}

}  // namespace blender
