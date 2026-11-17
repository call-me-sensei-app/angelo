.. SPDX-FileCopyrightText: 2026 Angelo Contributors
.. SPDX-License-Identifier: GPL-2.0-or-later

###################
Rigid body teardown
###################

`Blender #91959 <https://projects.blender.org/blender/blender/issues/91959>`_ (open;
`#164309 <https://projects.blender.org/blender/blender/issues/164309>`_ was closed as its duplicate): Blender
crashes now and then on Undo in a file with Rigid Body Constraints. It is a use of freed memory, and Undo is only one of
the ways to reach it.

A Bullet body, a constraint and a world are owned through different Blender data: a body through an object's rigid
body settings, a constraint through another object's (usually an Empty), a world through a scene. Each is freed
when its owner is, in whatever order that happens:

====================================================== ==============================================================
What a person or a script does                         The order, and what stock Blender 5.3.0 did
====================================================== ==============================================================
Undo, Redo, open or load a file, New                   The old file's objects are freed one by one, a body before a
                                                       constraint made with it. The constraint, freed later, told the
                                                       freed body to forget it.
Remove the constraint's object as a data-block         The constraint is freed while its world still lists it; the next
(``bpy.data.objects.remove``)                          frame change steps the world over it.
Remove a body's object as a data-block                 The body is freed while a constraint made with it is in the
                                                       world; the next frame change solves that constraint.
Any object free, in a file with two scenes that both   An object freed without a known world asks every scene's world
simulate; an object that is in both scenes             to give its body up. Bullet, asked by a world the body is not
                                                       in, destroys the body's place in the wrong broad-phase and
                                                       drops that world's body of the same index.
====================================================== ==============================================================

In a plain build the freed block usually still reads as an empty list, so nothing shows until the allocator has
reused it: the intermittent crash of the reports. With freed memory filled (``MallocScribble=1`` on macOS) the first
three stop official Blender 5.3.0 with a segmentation fault every time (measured 2026-11-15, 8 of 8 runs each, one
and 200 body pairs). ``rigidbody.cc`` itself says of its order of frees: "Memory management needs redesign here,
this is just a dirty workaround".

Contract
========

``intern/rigidbody/rb_bullet_api.cpp`` is the one file that calls Bullet for the rigid body simulation; nothing in
``blenkernel`` changes. The API now records who is where, and every free detaches first:

* A body knows the world it is in and every existing constraint made with it. A constraint knows the world it is
  in and its two bodies (``rbConstraintLinks``, reached through Bullet's user pointer of the constraint; every
  ``RB_constraint_new_*`` returns through ``constraint_register``). A world export writes Bullet's default user
  value, as before.
* ``RB_body_delete`` takes the body's constraints out of their world while Bullet can still visit both bodies, makes
  the other body let go of each, and leaves its own world. A constraint that lost a body stays allocated (its owner
  frees it) and is not added to a world again.
* ``RB_constraint_delete`` leaves its world first and visits only the bodies that still exist.
* ``RB_dworld_delete`` makes its bodies and constraints forget it, without taking them out one by one (a rebuild
  frees the world at the first frame of every playback: that stays linear in the number of bodies and constraints).
* A constraint leaves a world by its place in the world's list (``rbBulletWorld::removeConstraintAt``; the last
  constraint takes the freed place), not by Bullet's search of that list: a file's objects are freed one by one,
  each taking a constraint out, which with the search made closing a file quadratic in its constraints.
* A constraint leaves each of its bodies' lists by its place too (``body_index``, ``body_forget_constraint``), so
  a body shared by many constraints, a wall everything is pinned to, frees them in linear time. The first version
  searched the body's list for each one: 80,000 constraints on one body took 1.39 s to free against 0.02 s before
  the fix (an external review's measurement, 2026-11-15). One cost is Bullet's own and unchanged: with collisions
  between the linked bodies disabled, each body keeps a reference to the constraint and Bullet searches that list
  for each constraint that leaves, in Blender's own code as much as here.
* A body or a constraint is in at most one world. Removing it from a world it is not in does nothing; adding it to
  a world takes it out of the one it was in.

Simulation results are unchanged: nothing here runs during a step.

Upstream
========

As of 2026-11-15 #91959 is open and upstream ``main`` (``2e131281d77``) has no fix; the last change to
``rb_bullet_api.cpp``'s frees is ``5ee7d028400`` (#91369), which made a constraint tell its bodies to forget it and
is the line that reads the freed body. The change is contained in the API file and its header so it can be offered
upstream as it is.

Validation
==========

* C++: ``rigidbody_test`` (``intern/rigidbody/tests/rb_teardown_test.cc``): each order above and a rebuild's
  (world first), for every constraint constructor with collisions between the linked bodies on and off, stepping
  the worlds in between; 96 constraints leaving one world and 240 leaving one shared body in a scrambled order;
  40,000 constraints of the same two bodies freed in at most eight times the time of the same number shared out
  over 32 pairs of bodies in 32 worlds (a search of a list per constraint takes up to 32 times). It fills and keeps
  every block Bullet frees, this API's own record of a constraint included, so a use of freed memory stops it on
  every platform; without the change it stops in its first test.
* Background: ``test_blender_rigid_body_teardown.py``: Undo and Redo, file load, both removals and two scenes, each
  in its own process of the app with the system allocator filling freed memory where it can (macOS, glibc).
* Host: ``test_native_rigid_body_teardown.py`` reads the API file for the rules above.
