/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup intern_rigidbody
 *
 * A body, a constraint and a world are freed in any order.
 *
 * Blender owns each through different data: a body through one object's rigid body settings, a
 * constraint through another object's, a world through a scene. Each is freed when its owner is,
 * so undo and file load free a body before the constraint made with it, removing an object's
 * data-block frees a constraint its world still lists, and a rebuild frees the world first
 * (#91959, #164309). The tests free them in each of those orders and step the worlds in between.
 *
 * Every block Bullet frees is filled with 0x55 and kept. A use of a freed body, constraint or
 * world then reads sizes and pointers no process has, on every platform and with any system
 * allocator, where the plain build usually reads the stale block and carries on.
 */

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <string>
#include <tuple>
#include <vector>

#include "testing/testing.h"

#include <LinearMath/btAlignedAllocator.h>

#include "RBI_api.h"

namespace {

/* -------------------------------------------------------------------- */
/** \name Freed Bullet memory is poisoned and kept
 * \{ */

struct BlockHeader {
  void *base;
  size_t size;
};

std::vector<void *> &freed_blocks()
{
  static std::vector<void *> blocks;
  return blocks;
}

void *poisoning_alloc(size_t size, int alignment)
{
  const size_t align = std::max(size_t(alignment), alignof(BlockHeader));
  char *base = static_cast<char *>(malloc(sizeof(BlockHeader) + align + size));
  char *block = base + sizeof(BlockHeader);
  block += (align - reinterpret_cast<uintptr_t>(block) % align) % align;
  const BlockHeader header = {base, size};
  memcpy(block - sizeof(BlockHeader), &header, sizeof(BlockHeader));
  return block;
}

void poisoning_free(void *block)
{
  BlockHeader header;
  memcpy(&header, static_cast<char *>(block) - sizeof(BlockHeader), sizeof(BlockHeader));
  memset(block, 0x55, header.size);
  freed_blocks().push_back(header.base);
}

class PoisonFreedBulletMemory : public testing::Environment {
 public:
  void SetUp() override
  {
    btAlignedAllocSetCustomAligned(poisoning_alloc, poisoning_free);
  }
  void TearDown() override
  {
    for (void *base : freed_blocks()) {
      free(base);
    }
    freed_blocks().clear();
  }
};

[[maybe_unused]] const testing::Environment *const poison_environment =
    testing::AddGlobalTestEnvironment(new PoisonFreedBulletMemory);

/** \} */

/* -------------------------------------------------------------------- */
/** \name A small simulation
 * \{ */

const float GRAVITY[3] = {0.0f, 0.0f, -9.81f};
const float STEP = 1.0f / 60.0f;

rbRigidBody *new_body(
    rbDynamicsWorld *world, rbCollisionShape *shape, float x, float z, float mass)
{
  const float location[3] = {x, 0.0f, z};
  const float rotation[4] = {1.0f, 0.0f, 0.0f, 0.0f};
  rbRigidBody *body = RB_body_new(shape, location, rotation);
  RB_body_set_mass(body, mass);
  RB_body_set_activation_state(body, false);
  if (world) {
    RB_dworld_add_body(world, body, 1);
  }
  return body;
}

float height(rbRigidBody *body)
{
  float position[3];
  RB_body_get_position(body, position);
  return position[2];
}

void step(rbDynamicsWorld *world, int steps)
{
  for (int i = 0; i < steps; i++) {
    RB_dworld_step_simulation(world, STEP, 1, STEP);
  }
}

/** How Blender frees an object's body when it knows no world: every scene's world is asked. */
void free_body(const std::vector<rbDynamicsWorld *> &worlds, rbRigidBody *body)
{
  for (rbDynamicsWorld *world : worlds) {
    RB_dworld_remove_body(world, body);
  }
  RB_body_delete(body);
}

using NewConstraint = rbConstraint *(*)(float pivot[3],
                                        float orientation[4],
                                        rbRigidBody *rb1,
                                        rbRigidBody *rb2);

rbConstraint *new_point(float pivot[3],
                        float /*orientation*/[4],
                        rbRigidBody *rb1,
                        rbRigidBody *rb2)
{
  return RB_constraint_new_point(pivot, rb1, rb2);
}

struct ConstraintKind {
  const char *name;
  NewConstraint create;
  /** With nothing set on it, this kind keeps a body that hangs from a fixed one where it is. */
  bool holds;
};

/* Every constructor of the API: each one has to make its constraint known to its two bodies. */
const ConstraintKind KINDS[] = {
    {"point", new_point, true},
    {"fixed", RB_constraint_new_fixed, true},
    {"hinge", RB_constraint_new_hinge, true},
    {"slider", RB_constraint_new_slider, false},
    {"piston", RB_constraint_new_piston, false},
    {"6dof", RB_constraint_new_6dof, false},
    {"6dof_spring", RB_constraint_new_6dof_spring, false},
    {"6dof_spring2", RB_constraint_new_6dof_spring2, false},
    {"motor", RB_constraint_new_motor, false},
};

/** A fixed body with a body hung under it by one constraint, and a free body that only falls. */
struct Hanging {
  rbDynamicsWorld *world;
  rbCollisionShape *shape;
  rbRigidBody *anchor, *hung, *witness;
  rbConstraint *constraint;

  Hanging(const ConstraintKind &kind, bool disable_collisions, bool in_world = true)
  {
    world = RB_dworld_new(GRAVITY);
    shape = RB_shape_new_box(0.25f, 0.25f, 0.25f);
    anchor = new_body(world, shape, 0.0f, 4.0f, 0.0f);
    hung = new_body(world, shape, 0.0f, 2.0f, 1.0f);
    witness = new_body(world, shape, 5.0f, 2.0f, 1.0f);
    float pivot[3] = {0.0f, 0.0f, 4.0f};
    float orientation[4] = {1.0f, 0.0f, 0.0f, 0.0f};
    constraint = kind.create(pivot, orientation, anchor, hung);
    if (in_world) {
      RB_dworld_add_constraint(world, constraint, disable_collisions);
    }
  }

  /** The rest, freed as Blender frees a file: the objects first, the scene's world last. */
  void free_rest(const std::vector<rbRigidBody *> &bodies)
  {
    for (rbRigidBody *body : bodies) {
      free_body({world}, body);
    }
    RB_dworld_delete(world);
    RB_shape_delete(shape);
  }
};

class RigidBodyTeardown : public testing::TestWithParam<std::tuple<int, bool>> {
 protected:
  const ConstraintKind &kind() const
  {
    return KINDS[std::get<0>(GetParam())];
  }
  bool disable_collisions() const
  {
    return std::get<1>(GetParam());
  }
};

std::string kind_name(const testing::TestParamInfo<std::tuple<int, bool>> &info)
{
  return std::string(KINDS[std::get<0>(info.param)].name) +
         (std::get<1>(info.param) ? "_no_collisions" : "_collisions");
}

INSTANTIATE_TEST_SUITE_P(EveryConstraint,
                         RigidBodyTeardown,
                         testing::Combine(testing::Range(0, int(std::size(KINDS))),
                                          testing::Bool()),
                         kind_name);

/** \} */

/* -------------------------------------------------------------------- */
/** \name The orders Blender frees in
 * \{ */

/* Undo, redo, file load, quit: the old file's objects are freed one by one, a body before the
 * constraint made with it, and nothing is taken out of the world first. */
TEST_P(RigidBodyTeardown, BodiesFreedBeforeTheirConstraint)
{
  for (const bool in_world : {true, false}) {
    for (const bool anchor_first : {true, false}) {
      Hanging scene(kind(), disable_collisions(), in_world);
      step(scene.world, 10);
      free_body({scene.world}, anchor_first ? scene.anchor : scene.hung);
      free_body({scene.world}, anchor_first ? scene.hung : scene.anchor);
      RB_constraint_delete(scene.constraint);
      const float before = height(scene.witness);
      step(scene.world, 30);
      EXPECT_LT(height(scene.witness), before - 0.5f) << "the rest of the world still simulates";
      scene.free_rest({scene.witness});
    }
  }
}

/* The constraint's object is removed as a data-block: the constraint is freed while the world
 * still lists it, and the next frame steps the world. */
TEST_P(RigidBodyTeardown, ConstraintFreedWhileInItsWorld)
{
  Hanging scene(kind(), disable_collisions());
  step(scene.world, 60);
  if (kind().holds) {
    EXPECT_GT(height(scene.hung), 1.0f) << "held by the constraint for a second";
  }
  RB_constraint_delete(scene.constraint);
  const float before = height(scene.hung);
  step(scene.world, 60);
  EXPECT_LT(height(scene.hung), before - 2.5f) << "nothing holds the body any more: it falls";
  scene.free_rest({scene.hung, scene.anchor, scene.witness});
}

/* A body's object is removed as a data-block: the body is freed while a constraint made with it
 * is in the world, the next frame steps the world, and the constraint is freed some time later
 * (taken out of the world first, as Blender does when it finds the constraint has lost a body). */
TEST_P(RigidBodyTeardown, BodyFreedWhileItsConstraintIsInTheWorld)
{
  for (const bool free_anchor : {true, false}) {
    Hanging scene(kind(), disable_collisions());
    step(scene.world, 10);
    free_body({scene.world}, free_anchor ? scene.anchor : scene.hung);
    rbRigidBody *left = free_anchor ? scene.hung : scene.anchor;
    const float before = height(left);
    step(scene.world, 60);
    if (free_anchor) {
      EXPECT_LT(height(left), before - 2.5f) << "what it hung from is gone: it falls";
    }
    else {
      EXPECT_EQ(height(left), before) << "a fixed body stays";
    }
    RB_dworld_remove_constraint(scene.world, scene.constraint);
    RB_constraint_delete(scene.constraint);
    step(scene.world, 10);
    scene.free_rest({left, scene.witness});
  }
}

/* A constraint that lost a body is not solved again, whoever asks for it to be added. */
TEST_P(RigidBodyTeardown, ConstraintWithoutABodyIsNotAddedBack)
{
  Hanging scene(kind(), disable_collisions());
  step(scene.world, 10);
  free_body({scene.world}, scene.anchor);
  RB_dworld_add_constraint(scene.world, scene.constraint, disable_collisions());
  const float before = height(scene.hung);
  step(scene.world, 60);
  EXPECT_LT(height(scene.hung), before - 2.5f);
  RB_constraint_delete(scene.constraint);
  scene.free_rest({scene.hung, scene.witness});
}

/* A rebuild (the first frame of every playback, a changed setting): the world is freed first,
 * a new one made, the old constraints taken "out of" the new world and freed, then the bodies. */
TEST_P(RigidBodyTeardown, WorldFreedFirst)
{
  for (const bool bodies_first : {false, true}) {
    Hanging scene(kind(), disable_collisions());
    step(scene.world, 10);
    RB_dworld_delete(scene.world);
    scene.world = RB_dworld_new(GRAVITY);
    if (bodies_first) {
      RB_body_delete(scene.hung);
      RB_body_delete(scene.anchor);
    }
    RB_dworld_remove_constraint(scene.world, scene.constraint);
    RB_constraint_delete(scene.constraint);
    if (!bodies_first) {
      RB_body_delete(scene.hung);
      RB_body_delete(scene.anchor);
    }
    RB_body_delete(scene.witness);

    rbRigidBody *fresh = new_body(scene.world, scene.shape, 0.0f, 2.0f, 1.0f);
    step(scene.world, 30);
    EXPECT_LT(height(fresh), 1.5f) << "the new world simulates";
    scene.free_rest({fresh});
  }
}

/* The same body on both sides (a person may pick one object twice): the constraint is known to
 * that body once and forgotten once. Not added to a world, which Bullet's debug build refuses. */
TEST_P(RigidBodyTeardown, OneBodyOnBothSides)
{
  rbDynamicsWorld *world = RB_dworld_new(GRAVITY);
  rbCollisionShape *shape = RB_shape_new_box(0.25f, 0.25f, 0.25f);
  rbRigidBody *body = new_body(world, shape, 0.0f, 2.0f, 1.0f);
  float pivot[3] = {0.0f, 0.0f, 2.0f};
  float orientation[4] = {1.0f, 0.0f, 0.0f, 0.0f};
  rbConstraint *first = kind().create(pivot, orientation, body, body);
  rbConstraint *second = kind().create(pivot, orientation, body, body);
  RB_constraint_delete(first);
  step(world, 5);
  free_body({world}, body);
  RB_constraint_delete(second);
  RB_dworld_delete(world);
  RB_shape_delete(shape);
}

/** \} */

/* -------------------------------------------------------------------- */
/** \name Two scenes that both simulate
 * \{ */

/* An object freed without a known world asks every scene's world to give its body up
 * (BKE_rigidbody_free_object). Only the world the body is in may: Bullet, asked by another,
 * destroys the body's place in the wrong broadphase and drops that world's body of the same
 * index. */
TEST(RigidBodyTeardownWorlds, BodyRemovedFromAWorldItIsNotIn)
{
  rbCollisionShape *shape = RB_shape_new_box(0.25f, 0.25f, 0.25f);
  rbDynamicsWorld *first = RB_dworld_new(GRAVITY);
  rbDynamicsWorld *second = RB_dworld_new(GRAVITY);
  rbRigidBody *in_first = new_body(first, shape, 0.0f, 2.0f, 1.0f);
  rbRigidBody *in_second = new_body(second, shape, 0.0f, 2.0f, 1.0f);
  rbRigidBody *also_in_second = new_body(second, shape, 3.0f, 2.0f, 1.0f);

  RB_dworld_remove_body(first, in_second);
  step(first, 30);
  step(second, 30);
  EXPECT_LT(height(in_first), 1.5f) << "the first world kept its own body";
  EXPECT_LT(height(in_second), 1.5f) << "the body is still in the world it was added to";
  EXPECT_LT(height(also_in_second), 1.5f);

  free_body({first, second}, in_second);
  step(first, 10);
  step(second, 10);
  EXPECT_LT(height(in_first), 1.0f);
  EXPECT_LT(height(also_in_second), 1.0f);
  free_body({first, second}, in_first);
  free_body({first, second}, also_in_second);
  RB_dworld_delete(first);
  RB_dworld_delete(second);
  RB_shape_delete(shape);
}

/* The same for a constraint: taken "out of" a world it is not in, it still holds in its own. */
TEST(RigidBodyTeardownWorlds, ConstraintRemovedFromAWorldItIsNotIn)
{
  Hanging scene(KINDS[1], true);
  rbDynamicsWorld *other = RB_dworld_new(GRAVITY);
  RB_dworld_remove_constraint(other, scene.constraint);
  step(scene.world, 60);
  EXPECT_NEAR(height(scene.hung), 2.0f, 0.1f) << "still held in its own world";
  RB_constraint_delete(scene.constraint);
  scene.free_rest({scene.hung, scene.anchor, scene.witness});
  RB_dworld_delete(other);
}

/* A body moved to another world leaves the first, and each world frees cleanly after. */
TEST(RigidBodyTeardownWorlds, BodyAddedToASecondWorldLeavesTheFirst)
{
  rbCollisionShape *shape = RB_shape_new_box(0.25f, 0.25f, 0.25f);
  rbDynamicsWorld *first = RB_dworld_new(GRAVITY);
  rbDynamicsWorld *second = RB_dworld_new(GRAVITY);
  rbRigidBody *body = new_body(first, shape, 0.0f, 2.0f, 1.0f);
  rbRigidBody *stays = new_body(first, shape, 3.0f, 2.0f, 1.0f);
  RB_dworld_add_body(second, body, 1);
  RB_dworld_delete(first);
  step(second, 30);
  EXPECT_LT(height(body), 1.5f) << "simulated by the world it was moved to";
  EXPECT_NEAR(height(stays), 2.0f, 1e-4f) << "its world is gone: nothing moves it";
  free_body({second}, body);
  free_body({second}, stays);
  RB_dworld_delete(second);
  RB_shape_delete(shape);
}

/* Many constraints leave one world in a scrambled order, each of the three ways: the world's
 * list stays right for the ones that remain, which still hold. */
TEST(RigidBodyTeardownWorlds, ManyConstraintsLeaveInAnyOrder)
{
  const int count = 96;
  rbCollisionShape *shape = RB_shape_new_box(0.25f, 0.25f, 0.25f);
  rbDynamicsWorld *world = RB_dworld_new(GRAVITY);
  std::vector<rbRigidBody *> anchors(count), hung(count);
  std::vector<rbConstraint *> constraints(count);
  std::vector<int> order(count);
  for (int i = 0; i < count; i++) {
    anchors[i] = new_body(world, shape, 2.0f * i, 4.0f, 0.0f);
    hung[i] = new_body(world, shape, 2.0f * i, 2.0f, 1.0f);
    float pivot[3] = {2.0f * i, 0.0f, 4.0f};
    float orientation[4] = {1.0f, 0.0f, 0.0f, 0.0f};
    constraints[i] = RB_constraint_new_fixed(pivot, orientation, anchors[i], hung[i]);
    RB_dworld_add_constraint(world, constraints[i], i % 2);
    order[i] = i;
  }
  uint32_t state = 12345;
  for (int i = count - 1; i > 0; i--) {
    state = state * 1664525u + 1013904223u;
    std::swap(order[i], order[(state >> 8) % uint32_t(i + 1)]);
  }

  std::vector<bool> joined(count, true);
  for (int done = 0; done < count; done++) {
    const int i = order[done];
    switch (i % 3) {
      case 0: /* The constraint is freed while the world lists it. */
        RB_constraint_delete(constraints[i]);
        constraints[i] = nullptr;
        break;
      case 1: /* Its hung body is freed first; the constraint later, with the rest. */
        free_body({world}, hung[i]);
        hung[i] = nullptr;
        break;
      default: /* Taken out of the world, then freed. */
        RB_dworld_remove_constraint(world, constraints[i]);
        RB_constraint_delete(constraints[i]);
        constraints[i] = nullptr;
        break;
    }
    joined[i] = false;
    if (done % 8 != 7) {
      continue;
    }
    std::vector<float> before(count, 0.0f);
    for (int j = 0; j < count; j++) {
      before[j] = hung[j] ? height(hung[j]) : 0.0f;
    }
    step(world, 30);
    for (int j = 0; j < count; j++) {
      if (hung[j] == nullptr) {
        continue;
      }
      if (joined[j]) {
        EXPECT_NEAR(height(hung[j]), 2.0f, 0.1f) << "pair " << j << " is still joined";
      }
      else {
        EXPECT_LT(height(hung[j]), before[j] - 0.5f) << "pair " << j << " was released";
      }
    }
  }

  for (int i = 0; i < count; i++) {
    if (hung[i]) {
      free_body({world}, hung[i]);
    }
    free_body({world}, anchors[i]);
    if (constraints[i]) {
      RB_constraint_delete(constraints[i]);
    }
  }
  RB_dworld_delete(world);
  RB_shape_delete(shape);
}

/* One body shared by many constraints (a wall that everything is pinned to), in a world, and the
 * constraints leave in a scrambled order in each of the three ways. What the hub knows of its
 * constraints has to stay right as they go: then the hub itself is freed while a third of them
 * still exist, and those are freed after it. A hub that kept a freed constraint, or forgot a live
 * one, reads poisoned memory here. */
TEST(RigidBodyTeardownWorlds, ManyConstraintsOnOneBody)
{
  const int count = 240;
  rbCollisionShape *shape = RB_shape_new_box(0.25f, 0.25f, 0.25f);
  rbDynamicsWorld *world = RB_dworld_new(GRAVITY);
  rbRigidBody *hub = new_body(world, shape, 0.0f, 4.0f, 0.0f);
  std::vector<rbRigidBody *> hung(count);
  std::vector<rbConstraint *> constraints(count);
  std::vector<int> order(count);
  for (int i = 0; i < count; i++) {
    hung[i] = new_body(world, shape, 2.0f * (i + 1), 2.0f, 1.0f);
    float pivot[3] = {2.0f * (i + 1), 0.0f, 4.0f};
    float orientation[4] = {1.0f, 0.0f, 0.0f, 0.0f};
    constraints[i] = RB_constraint_new_fixed(pivot, orientation, hub, hung[i]);
    RB_dworld_add_constraint(world, constraints[i], i % 2);
    order[i] = i;
  }
  uint32_t state = 2026;
  for (int i = count - 1; i > 0; i--) {
    state = state * 1664525u + 1013904223u;
    std::swap(order[i], order[(state >> 8) % uint32_t(i + 1)]);
  }

  std::vector<bool> joined(count, true);
  const int first_round = count * 2 / 3;
  for (int done = 0; done < first_round; done++) {
    const int i = order[done];
    switch (i % 3) {
      case 0:
        RB_constraint_delete(constraints[i]);
        constraints[i] = nullptr;
        break;
      case 1:
        free_body({world}, hung[i]);
        hung[i] = nullptr;
        break;
      default:
        RB_dworld_remove_constraint(world, constraints[i]);
        RB_constraint_delete(constraints[i]);
        constraints[i] = nullptr;
        break;
    }
    joined[i] = false;
    if (done % 40 != 39) {
      continue;
    }
    step(world, 30);
    for (int j = 0; j < count; j++) {
      if (hung[j] && joined[j]) {
        EXPECT_NEAR(height(hung[j]), 2.0f, 0.1f) << "pair " << j << " is still joined to the hub";
      }
    }
  }

  std::vector<float> before(count, 0.0f);
  for (int j = 0; j < count; j++) {
    before[j] = hung[j] ? height(hung[j]) : 0.0f;
  }
  free_body({world}, hub);
  step(world, 30);
  for (int j = 0; j < count; j++) {
    if (hung[j]) {
      EXPECT_LT(height(hung[j]), before[j] - 0.5f) << "the hub is gone: body " << j << " falls";
    }
  }
  for (int i = 0; i < count; i++) {
    if (constraints[i]) {
      RB_constraint_delete(constraints[i]);
    }
    if (hung[i]) {
      free_body({world}, hung[i]);
    }
  }
  RB_dworld_delete(world);
  RB_shape_delete(shape);
}

/**
 * Seconds to free `count` constraints shared out evenly over the first `groups` of `worlds`, where
 * each world holds one pair of bodies that all of its constraints join; the best of `turns`. The
 * worlds are left empty.
 */
double seconds_to_free(const std::vector<rbDynamicsWorld *> &worlds,
                       const int groups,
                       const int count,
                       const bool in_world,
                       const bool disable_collisions = false,
                       const int turns = 3)
{
  const float pivot[3] = {1.5f, 0.0f, 4.0f};
  rbCollisionShape *shape = RB_shape_new_box(0.25f, 0.25f, 0.25f);
  double best = 1e30;
  for (int turn = 0; turn < turns; turn++) {
    std::vector<rbRigidBody *> bodies;
    for (int group = 0; group < groups; group++) {
      bodies.push_back(new_body(worlds[group], shape, 0.0f, 4.0f, 0.0f));
      bodies.push_back(new_body(worlds[group], shape, 3.0f, 4.0f, 0.0f));
    }
    std::vector<rbConstraint *> constraints(count);
    for (int i = 0; i < count; i++) {
      const int group = int(int64_t(i) * groups / count);
      constraints[i] = RB_constraint_new_point(pivot, bodies[2 * group], bodies[2 * group + 1]);
      if (in_world) {
        RB_dworld_add_constraint(worlds[group], constraints[i], disable_collisions);
      }
    }
    /* The list of kept blocks gets its room before the clock starts: its growth copies every
     * block freed so far in this program, which is no part of what is timed. */
    freed_blocks().reserve(freed_blocks().size() + 4 * size_t(count));
    const auto start = std::chrono::steady_clock::now();
    for (rbConstraint *constraint : constraints) {
      RB_constraint_delete(constraint);
    }
    const std::chrono::duration<double> taken = std::chrono::steady_clock::now() - start;
    best = std::min(best, taken.count());
    for (int group = 0; group < groups; group++) {
      free_body({worlds[group]}, bodies[2 * group]);
      free_body({worlds[group]}, bodies[2 * group + 1]);
    }
  }
  RB_shape_delete(shape);
  return best;
}

/* Freeing the constraints of a body costs no more for each one the more of them the body has. The
 * same 40,000 constraints are freed twice: all on one pair of bodies in one world, and on 32 pairs
 * in 32 worlds with 1,250 each. Where a constraint leaves its bodies' lists and its world's list
 * by its place, the two take about the same time. Where a list is searched for each one, the
 * single pair takes up to 32 times as long, its lists being 32 times as long: the first version
 * of this fix searched the body's list (80,000 constraints on one pair: 1.39 s against 0.02 s, an
 * external review, 2026-11-15). The bound of eight lies between the two.
 *
 * Both runs free the same number of blocks of the same sizes in the same order, so the allocator
 * and the caches cost both the same. Comparing two counts instead, the first form of this test
 * (80,000 within eight times 20,000), does not hold that still: correct code measured 8.5 times
 * on macOS, 0.45 ms against 3.8 ms, and 4.2 times on Windows.
 *
 * Collisions between the linked bodies stay enabled here: with them disabled Bullet searches its
 * own list of references per constraint, which this API does not own. */
TEST(RigidBodyTeardownWorlds, FreeingABodysManyConstraintsIsLinear)
{
  const int count = 40000;
  const int groups = 32;
  std::vector<rbDynamicsWorld *> worlds(groups);
  for (rbDynamicsWorld *&world : worlds) {
    world = RB_dworld_new(GRAVITY);
  }
  for (const bool in_world : {false, true}) {
    const double spread = seconds_to_free(worlds, groups, count, in_world);
    const double together = seconds_to_free(worlds, 1, count, in_world);
    printf("RB_TEARDOWN_ONE_BODY %s: %d constraints on one pair %.4f s, on %d pairs %.4f s\n",
           in_world ? "in a world" : "in no world",
           count,
           together,
           groups,
           spread);
    EXPECT_LT(together, 8.0 * std::max(spread, 1e-4))
        << (in_world ? "in a world" : "in no world") << ": " << count
        << " constraints on one pair of bodies " << together << " s, on " << groups << " pairs "
        << spread << " s";
  }
  for (rbDynamicsWorld *world : worlds) {
    RB_dworld_delete(world);
  }
}

/* Not a check: how the time to free one pair's constraints grows with their number, and the one
 * case the test above leaves out. With collisions between the linked bodies disabled, Bullet
 * keeps a reference to the constraint in each body and searches that list for each constraint
 * that leaves (`btRigidBody::removeConstraintRef`), in Blender's own rigid body code as much as
 * here. The lines are for a person comparing builds. */
TEST(RigidBodyTeardownWorlds, TimesOfFreeingManyConstraints)
{
  const std::vector<rbDynamicsWorld *> worlds = {RB_dworld_new(GRAVITY)};
  for (const bool disable_collisions : {false, true}) {
    for (const bool in_world : {false, true}) {
      if (disable_collisions && !in_world) {
        continue; /* Bullet takes its references when a constraint enters a world. */
      }
      const double few = seconds_to_free(worlds, 1, 10000, in_world, disable_collisions, 1);
      const double many = seconds_to_free(worlds, 1, 40000, in_world, disable_collisions, 1);
      printf(
          "RB_TEARDOWN_TIMES collisions %s, %s: "
          "10000 constraints %.4f s, 40000 constraints %.4f s\n",
          disable_collisions ? "disabled" : "enabled",
          in_world ? "in a world" : "in no world",
          few,
          many);
    }
  }
  RB_dworld_delete(worlds[0]);
}

/** \} */

}  // namespace
