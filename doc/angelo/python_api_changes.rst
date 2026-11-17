Python API changes
==================

What Angelo changes in Blender's Python API, by release, for add-on and script authors. The person-facing release
notes (``CHANGELOG.md``) carry only what a person notices; the exact names are here.

2.0.0
-----

* Passing None to a pointer parameter that cannot take it (for example WindowManager.fileselect_add, collection
  unlink, a node's poll) now raises a TypeError instead of crashing; the tracking and Grease Pencil layer-group
  active properties accept None to clear; Mesh/Curves/Lattice/GreasePencil.unit_test_compare and popup_menu_pie's
  event are required.
