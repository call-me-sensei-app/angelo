/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

/** \file
 * \ingroup bke
 * What Blender's render writers build their output paths from (Angelo, `BLI_angelo_writers.hh`): each input is a
 * property of data in a data-block; the first is the property the path is read from (the tag).
 */

#include "BLI_angelo_writers.hh"
#include "BLI_vector.hh"

namespace blender {

struct ID;
struct NodesModifierData;
struct Object;
struct Scene;
struct bNode;
struct bNodeTree;

using AngeloWriterInputs = Vector<angelo_writers::Input, 16>;

/** The render output of `scene`: its output path (the tag), extension and format, view suffixes and, when the path
 * holds templates, the values they expand to (the scene's and its cameras' names, resolution, frame rate). */
AngeloWriterInputs BKE_angelo_render_inputs(const Scene &scene);

/** A File Output node's paths: its folder (the tag), file name, item names and format, and the scene values its
 * templates and view suffixes expand to. */
AngeloWriterInputs BKE_angelo_file_output_inputs(const Scene &scene, const bNodeTree &tree, const bNode &node);

/** The external file of `id`'s custom data (a mesh's multires displacements, written on every save and autosave):
 * the `filepath` of each Multires modifier on an object that uses `id` (where a script sets it, the tag), and `id`
 * itself (judged when a script made it). */
AngeloWriterInputs BKE_angelo_custom_data_external_inputs(const ID &id);

/** A geometry-nodes bake's folders (its bakes, their packed files unpacked): the modifier's bake folder (the tag), a
 * bake's own folder and whether it uses it, and the names the default folder is made of. */
AngeloWriterInputs BKE_angelo_bake_inputs(const Object &object, const NodesModifierData &nmd);

}  // namespace blender
