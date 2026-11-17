/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup bke
 */

#include <cstring>

#include "BLI_listbase_iterator.hh"

#include "DNA_modifier_types.h"
#include "DNA_node_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "BKE_angelo_writer_inputs.hh"
#include "BKE_global.hh"
#include "BKE_main.hh"

namespace blender {

using angelo_writers::Input;

/** What a scene adds to an output path: its view suffixes and, with templates, the values they expand to. */
static void scene_value_inputs(const Scene &scene, const bool templates, AngeloWriterInputs &inputs)
{
  const ID *owner = &scene.id;
  inputs.append({owner, nullptr, "SceneRenderView", "file_suffix"});
  inputs.append({owner, nullptr, "RenderSettings", "use_multiview"});
  inputs.append({owner, nullptr, "RenderSettings", "views_format"});
  inputs.append({owner, nullptr, "ImageFormatSettings", "views_format"});
  if (!templates) {
    return;
  }
  inputs.append({owner, nullptr, "ID", "name"});
  inputs.append({owner, nullptr, "Scene", "camera"});
  inputs.append({owner, nullptr, "TimelineMarker", "camera"});
  for (const char *prop : {"resolution_x", "resolution_y", "resolution_percentage", "fps", "fps_base"}) {
    inputs.append({owner, nullptr, "RenderSettings", prop});
  }
  /* `{camera_name}`: the scene's camera and every camera a marker binds (a person's render with camera markers). */
  if (scene.camera != nullptr) {
    inputs.append({&scene.camera->id, nullptr, "ID", "name"});
  }
  for (const TimeMarker &marker : scene.markers) {
    if (marker.camera != nullptr) {
      inputs.append({&marker.camera->id, nullptr, "ID", "name"});
    }
  }
}

AngeloWriterInputs BKE_angelo_render_inputs(const Scene &scene)
{
  AngeloWriterInputs inputs;
  const ID *owner = &scene.id;
  inputs.append({owner, &scene.r, "RenderSettings", "filepath"});
  inputs.append({owner, nullptr, "RenderSettings", "use_file_extension"});
  inputs.append({owner, nullptr, "ImageFormatSettings", "file_format"});
  inputs.append({owner, nullptr, "ImageFormatSettings", "media_type"});
  inputs.append({owner, nullptr, "FFmpegSettings", "format"});
  scene_value_inputs(scene, strchr(scene.r.pic, '{') != nullptr, inputs);
  return inputs;
}

AngeloWriterInputs BKE_angelo_file_output_inputs(const Scene &scene, const bNodeTree &tree, const bNode &node)
{
  AngeloWriterInputs inputs;
  const ID *owner = &tree.id;
  inputs.append({owner, &node, "CompositorNodeOutputFile", "directory"});
  inputs.append({owner, nullptr, "CompositorNodeOutputFile", "file_name"});
  inputs.append({owner, nullptr, "CompositorNodeOutputFile", "use_file_extension"});
  inputs.append({owner, nullptr, "NodeCompositorFileOutputItem", "name"}); /* An item's file or layer name. */
  inputs.append({owner, nullptr, "ImageFormatSettings", "file_format"});
  inputs.append({owner, nullptr, "ImageFormatSettings", "media_type"});
  const NodeCompositorFileOutput *storage = static_cast<const NodeCompositorFileOutput *>(node.storage);
  const bool templates = storage != nullptr &&
                         (strchr(storage->directory, '{') != nullptr ||
                          (storage->file_name != nullptr && strchr(storage->file_name, '{') != nullptr));
  scene_value_inputs(scene, templates, inputs);
  return inputs;
}

AngeloWriterInputs BKE_angelo_custom_data_external_inputs(const ID &id)
{
  AngeloWriterInputs inputs;
  /* `MultiresModifier.filepath` is kept in the mesh's custom data but set through the object's modifier: its tag is
   * the object's. Only meshes keep external data; a save writes the document in G_MAIN. */
  if (GS(id.name) == ID_ME && G_MAIN != nullptr) {
    /* By session uid, never by address: a .blend write hands its writers a temporary copy of the mesh
     * (writefile.cc's ID buffer), with the original's uid. */
    for (const Object &object : G_MAIN->objects) {
      const ID *data = static_cast<const ID *>(object.data);
      if (data == nullptr || data->session_uid != id.session_uid) {
        continue;
      }
      for (const ModifierData &md : object.modifiers) {
        if (md.type == eModifierType_Multires) {
          inputs.append({&object.id, &md, "MultiresModifier", "filepath"});
        }
      }
    }
  }
  inputs.append({&id, nullptr, nullptr, nullptr});
  return inputs;
}

AngeloWriterInputs BKE_angelo_bake_inputs(const Object &object, const NodesModifierData &nmd)
{
  AngeloWriterInputs inputs;
  inputs.append({&object.id, &nmd, "NodesModifier", "bake_directory"});
  inputs.append({&object.id, nullptr, "NodesModifier", "bake_target"});
  for (const char *prop : {"directory", "use_custom_path", "bake_target"}) {
    inputs.append({&object.id, nullptr, "NodesModifierBake", prop});
  }
  /* The default folder is named after the object and the modifier: their names only while it is the one used (no
   * folder set; the re-read of cf5befe2877, M6: a name is an input only where the path uses it). */
  if (nmd.bake_directory == nullptr || nmd.bake_directory[0] == '\0') {
    inputs.append({&object.id, nullptr, "NodesModifier", "name"});
    inputs.append({&object.id, nullptr, "ID", "name"});
  }
  return inputs;
}

}  // namespace blender
