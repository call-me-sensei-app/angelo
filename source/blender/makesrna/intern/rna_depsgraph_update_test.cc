/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/* Angelo: `DepsgraphUpdate.angelo_recalc` reads an update's own recalc bits, one item per bit,
 * with its embedded node tree folded in and OTHER for every bit without an item of its own. */

#include <set>
#include <string>

#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_node.hh"
#include "BKE_object.hh"

#include "BLI_string.h"

#include "DNA_ID.h"
#include "DNA_material_types.h"
#include "DNA_mesh_types.h"
#include "DNA_node_types.h"
#include "DNA_object_types.h"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "testing/testing.h"

namespace blender::rna::tests {

class RNADepsgraphUpdateRecalcTest : public bke::BlenderGTestBase {
 public:
  Main *bmain = nullptr;

  void SetUp() override
  {
    bmain = BKE_main_new();
  }
  void TearDown() override
  {
    BKE_main_free(bmain);
  }
};

/* The recalc bits no item names, read from the property's own items (so a bit a newer Blender
 * names is never assumed unnamed here). */
static uint unnamed_bits()
{
  PointerRNA ptr = RNA_pointer_create_discrete(nullptr, RNA_DepsgraphUpdate, nullptr);
  PropertyRNA *prop = RNA_struct_find_property(&ptr, "angelo_recalc");
  if (prop == nullptr) {
    return 0;
  }
  const EnumPropertyItem *items = nullptr;
  int items_num = 0;
  bool free = false;
  RNA_property_enum_items(nullptr, &ptr, prop, &items, &items_num, &free);
  uint named = 0;
  for (int i = 0; i < items_num; i++) {
    if (!STREQ(items[i].identifier, "OTHER")) {
      named |= uint(items[i].value);
    }
  }
  return ~named;
}

static uint lowest_bit(const uint bits)
{
  return bits & (~bits + 1);
}

/* What Python's set of `update.angelo_recalc` holds for an update of `id`. */
static std::set<std::string> kinds(ID *id)
{
  PointerRNA ptr = RNA_pointer_create_id_subdata(*id, RNA_DepsgraphUpdate, id);
  PropertyRNA *prop = RNA_struct_find_property(&ptr, "angelo_recalc");
  EXPECT_NE(prop, nullptr);
  if (prop == nullptr) {
    return {};
  }
  const char *identifiers[RNA_ENUM_BITFLAG_SIZE + 1] = {};
  RNA_property_enum_bitflag_identifiers(
      nullptr, &ptr, prop, RNA_property_enum_get(&ptr, prop), identifiers);
  std::set<std::string> result;
  for (const char **identifier = identifiers; *identifier; identifier++) {
    result.insert(*identifier);
  }
  return result;
}

using Kinds = std::set<std::string>;

TEST_F(RNADepsgraphUpdateRecalcTest, one_item_per_tag)
{
  Object *object = BKE_object_add_only_object(bmain, OB_EMPTY, "Object");
  object->id.recalc = ID_RECALC_SELECT;
  EXPECT_EQ(kinds(&object->id), Kinds({"SELECT"}));
  object->id.recalc = ID_RECALC_TRANSFORM;
  EXPECT_EQ(kinds(&object->id), Kinds({"TRANSFORM"}));
  object->id.recalc = ID_RECALC_SHADING | ID_RECALC_PARAMETERS;
  EXPECT_EQ(kinds(&object->id), Kinds({"SHADING", "PARAMETERS"}));
  /* An alias reads as the one bit it is. */
  object->id.recalc = ID_RECALC_ANIMATION_NO_FLUSH;
  EXPECT_EQ(kinds(&object->id), Kinds({"SYNC_TO_EVAL"}));
  object->id.recalc = ID_RECALC_PSYS_ALL;
  EXPECT_EQ(kinds(&object->id), Kinds({"PSYS_REDO", "PSYS_RESET", "PSYS_CHILD", "PSYS_PHYS"}));
}

TEST_F(RNADepsgraphUpdateRecalcTest, unnamed_bits_and_no_bits_are_other)
{
  Object *object = BKE_object_add_only_object(bmain, OB_EMPTY, "Object");
  const uint unnamed = unnamed_bits();
  ASSERT_NE(unnamed, 0u);
  object->id.recalc = lowest_bit(unnamed);
  EXPECT_EQ(kinds(&object->id), Kinds({"OTHER"}));
  object->id.recalc = ID_RECALC_SELECT | (1u << 31);
  EXPECT_EQ(kinds(&object->id), Kinds({"SELECT", "OTHER"}));
  object->id.recalc = unnamed;
  EXPECT_EQ(kinds(&object->id), Kinds({"OTHER"}));
  /* Never empty: an update with no bits left is not a selection-only update. */
  object->id.recalc = 0;
  EXPECT_EQ(kinds(&object->id), Kinds({"OTHER"}));
  object->id.recalc = ID_RECALC_ALL;
  const Kinds all = kinds(&object->id);
  EXPECT_EQ(all.size(), 26);
  EXPECT_TRUE(all.count("OTHER"));
}

TEST_F(RNADepsgraphUpdateRecalcTest, embedded_tree_is_folded_object_data_is_not)
{
  /* Blender reports an owner whose own bits are zero when its embedded tree's output changed. */
  Material *material = BKE_id_new<Material>(bmain, "Material");
  bNodeTree *tree = material->nodetree; /* A new material owns its embedded tree. */
  ASSERT_NE(tree, nullptr);
  material->id.recalc = 0;
  tree->id.recalc = ID_RECALC_NTREE_OUTPUT;
  EXPECT_EQ(kinds(&material->id), Kinds({"NTREE_OUTPUT"}));
  material->id.recalc = ID_RECALC_SHADING;
  EXPECT_EQ(kinds(&material->id), Kinds({"SHADING", "NTREE_OUTPUT"}));

  Object *object = BKE_object_add_only_object(bmain, OB_MESH, "Object");
  Mesh *mesh = BKE_id_new<Mesh>(bmain, "Mesh");
  object->data = &mesh->id;
  object->id.recalc = ID_RECALC_SELECT;
  mesh->id.recalc = ID_RECALC_GEOMETRY;
  EXPECT_EQ(kinds(&object->id), Kinds({"SELECT"}));
  object->data = nullptr;
}

TEST_F(RNADepsgraphUpdateRecalcTest, read_only_single_bit_items)
{
  Object *object = BKE_object_add_only_object(bmain, OB_EMPTY, "Object");
  PointerRNA ptr = RNA_pointer_create_id_subdata(object->id, RNA_DepsgraphUpdate, &object->id);
  PropertyRNA *prop = RNA_struct_find_property(&ptr, "angelo_recalc");
  ASSERT_NE(prop, nullptr);
  EXPECT_EQ(RNA_property_type(prop), PROP_ENUM);
  EXPECT_TRUE(RNA_property_flag(prop) & PROP_ENUM_FLAG);
  EXPECT_FALSE(RNA_property_editable(&ptr, prop));
  EXPECT_FALSE(RNA_property_animateable(&ptr, prop));
  const EnumPropertyItem *items = nullptr;
  int items_num = 0;
  bool free = false;
  RNA_property_enum_items(nullptr, &ptr, prop, &items, &items_num, &free);
  uint seen = 0;
  for (int i = 0; i < items_num; i++) {
    const uint bit = uint(items[i].value);
    EXPECT_NE(bit, 0u) << items[i].identifier;
    EXPECT_EQ(bit & (bit - 1), 0u) << items[i].identifier;
    EXPECT_EQ(seen & bit, 0u) << items[i].identifier;
    seen |= bit;
  }
  EXPECT_EQ(items_num, 26);
  EXPECT_FALSE(free); /* Static items: nothing to free. */
}

}  // namespace blender::rna::tests
