/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <variant>
#include <vector>

#include "RNA_types.hh"

namespace blender {
struct Main;
struct Scene;
struct ViewLayer;
}  // namespace blender

namespace blender::angelo_read_geometry {
class Scope;
}

namespace blender::angelo_read_scalar {

using Family = uint16_t;
enum : Family {
  NONE = 0,
  RENDER_THREADS,
  RENDER_PASS_RECT,
  DRIVER_SIMPLE_EXPRESSION,
  POSE_MATRIX_BASIS,
  POSE_IN_IK_CHAIN,
  ACTION_FRAME_RANGE,
  ACTION_CURVE_FRAME_RANGE,
  NODE_CRYPTOMATTE_MATTE,
  FLUID_DENSITY,
  FLUID_VELOCITY,
  FLUID_FLAME,
  FLUID_COLOR,
  FLUID_HEAT,
  FLUID_TEMPERATURE,
  NODE_SOCKET_MENU_ITEMS,
  NODE_INPUT_MENU_ITEMS,
  ANNOTATION_LAYER_ITEMS,
  ACTION_SLOT_ID_TYPE_ITEMS,
  OBJECT_MATRIX_LOCAL,
  COLORSPACE_INDEX,
  NODE_FROM_COLORSPACE,
  NODE_TO_COLORSPACE,
  PARTICLE_DUPLI_WEIGHT_NAME,
  PARTICLE_TARGET_NAME,
  NODE_ITEM_SOCKET_TYPE_ITEMS,
  NODE_IMPLICIT_SOCKET_TYPE_ITEMS,
  NODE_INTERFACE_SOCKET_TYPE_ITEMS,
  NODE_IMPLICIT_SOCKET_TYPE,
  NODE_INTERFACE_SOCKET_TYPE,
  COLORSPACE_ITEMS,
  OBJECT_BOUND_BOX,
  OBJECT_DIMENSIONS,
  PARTICLE_EDIT_EDITABLE,
  PARTICLE_EDIT_HAIR,
  STRIP_DURATION,
  STRIP_CONTENT_DURATION,
  STRIP_CONTENT_END,
  STRIP_LEFT_HANDLE,
  STRIP_RIGHT_HANDLE,
  STRIP_RETIMING_KEY_FRAME,
  STRIP_RETIMING_SELECTED,
  STRIP_LOCK,
  PARTICLE_HAIR_LOCATION,
  POINTCACHE_INFO,
  POINTCACHE_ACTIVE_INDEX,
  STRIP_FPS,
  COLOR_VIEW_ITEMS,
  DATA_TRANSFER_SOURCE_ITEMS,
  DATA_TRANSFER_DESTINATION_ITEMS,
};

enum class Status {
  OK,
  INVALID_STORAGE,
  UNSUPPORTED,
  RESOURCE_LIMIT,
  /** The value would run add-on (Python) code, for example an add-on node tree's socket rule. */
  EXTENSION_CODE,
  /** The value needs an evaluation Blender has not made; it is never evaluated for a read. */
  NOT_EVALUATED,
  /** The value would read a file (a movie or a disk cache); nothing is loaded. */
  LOADS_FILE,
};

/* Families bind the current native RNA declaration and selected callback source identity:
 * these are the catalogued built-in getters (doc/angelo/scene_intelligence.rst) that load files,
 * mutate data or caches, or run add-on code, and whose value this module computes instead. */
Family value_family(const StructRNA &owner_type, const PropertyRNA &property);
/** Whether any of this declaration's read callbacks is one this module replaces. The core
 * withholds such a property when no family matched it exactly, rather than run the callback. */
bool catalogued(const PropertyRNA &property);
Family enum_family(const StructRNA &owner_type, const PropertyRNA &property);

struct Value {
  Status status = Status::UNSUPPORTED;
  std::variant<std::monostate,
               bool,
               int,
               float,
               std::string,
               std::vector<bool>,
               std::vector<int>,
               std::vector<float>> data;
  int64_t actual_length = 0;
};

struct EnumItem {
  int value;
  std::string identifier;
  std::string name;
};
struct EnumResult {
  Status status = Status::UNSUPPORTED;
  std::vector<EnumItem> items;
};

/* Main-thread borrowed storage is kept only in this native request scope. Returned values own
 * all encoded data. Numeric arrays copy at most64 values, retaining the complete source length. */
/** The bound window's document, scene and view layer, resolved by the core without context
 * callbacks. `view_layer` is null when the window's layer name does not resolve. */
struct ReadWindow {
  const Main *main = nullptr;
  const Scene *scene = nullptr;
  const ViewLayer *view_layer = nullptr;
};

class Scope {
 public:
  Scope();
  /* Caller owns geometry and destroys this scalar Scope first. All scalar geometry borrows
   * reuse that operation-wide owner. */
  explicit Scope(angelo_read_geometry::Scope &geometry);
  ~Scope();
  Scope(const Scope &) = delete;
  Scope &operator=(const Scope &) = delete;

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
  friend Value value(Scope &, Family, const PointerRNA &, const PropertyRNA &);
  friend EnumResult enum_items(
      Scope &, Family, const PointerRNA &, const PropertyRNA &, const ReadWindow *, bool);
};

Value value(Scope &scope,
            Family family,
            const PointerRNA &owner,
            const PropertyRNA &property);
/* `window` is the bound window for a value's choices, null for a declaration's choices.
 * No Python callback, context callback or wrapper runs while selecting or copying items. */
EnumResult enum_items(Scope &scope,
                      Family family,
                      const PointerRNA &owner,
                      const PropertyRNA &property,
                      const ReadWindow *window,
                      bool declaration);

}  // namespace blender::angelo_read_scalar
