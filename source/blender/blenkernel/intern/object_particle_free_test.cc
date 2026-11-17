/* SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * Freeing all particle systems of an object that stays (converting it to a mesh, copying another
 * object's modifiers onto it) leaves it no particle system drawing objects or collections, so it
 * must not keep #OB_DUPLIPARTS: with "Show Instancer" off, it would stay hidden. Stock cleared it
 * as the last system went; freeing a system no longer reads settings, so freeing them all clears
 * it (#BKE_object_free_particlesystems).
 */

#include "testing/testing.h"

#include "BKE_gtest_base.hh"
#include "BKE_lib_id.hh"
#include "BKE_main.hh"
#include "BKE_object.hh"

#include "BLI_listbase.hh"

#include "DNA_object_types.h"
#include "DNA_particle_types.h"

namespace blender::bke::tests {

class ObjectParticleFreeTest : public BlenderGTestBase {};

TEST_F(ObjectParticleFreeTest, FreeingEverySystemOfAnObjectThatStaysClearsItsDupliFlag)
{
  Main *bmain = BKE_main_new();
  Object *ob = BKE_id_new<Object>(bmain, "Emitter");
  for (int index = 0; index < 2; index++) {
    BLI_addtail(&ob->particlesystem, MEM_new<ParticleSystem>(__func__));
  }
  ob->transflag |= OB_DUPLIPARTS; /* As an emitter drawing objects has it (and old files store). */

  BKE_object_free_particlesystems(ob);

  EXPECT_TRUE(BLI_listbase_is_empty(&ob->particlesystem));
  EXPECT_EQ(ob->transflag & OB_DUPLIPARTS, 0) << "no system left draws instances";
  BKE_main_free(bmain);
}

}  // namespace blender::bke::tests
