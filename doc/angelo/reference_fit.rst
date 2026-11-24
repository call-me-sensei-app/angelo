.. SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
.. SPDX-License-Identifier: GPL-2.0-or-later

=====================================
The reference board and measured fit
=====================================

A character's drawings (front, side, back, top or any named view), each solved to a camera, and how well a model
fits them, view by view, as facts the agent reads instead of judging renders by eye. Angelo 2.1.0. Owners:
``ref_board.py`` (the board) and ``ref_fit.py`` (the score), both NumPy only, built on ``view_outline`` (the outline
maths ``angelo_view``'s compare uses) and ``view_silhouette`` (the model drawn alone at a camera). Hair fitting
calls the same score.

.. contents::
   :local:

The board
=========

``Board.from_dict(data, images)`` checks a board (``schema`` 1; ``unit_mm``, millimetres in one world unit; board-wide
``defaults``; ``views``) and takes each view's pixels (RGB or RGBA, rows top first, the view's ``size``). A view has
an ``id`` (1-32 of ``a-z 0-9 - _``), its ``size`` in pixels, its ``camera`` (the dict ``view_silhouette.draw``
takes: ``view_matrix``, ``projection_matrix``, ``details``), optional ``image`` (project path and sha256) and
optional ``thresholds``. ``to_dict`` gives the board back without pixels. Once made, a board never changes, so any
thread may read it.

* ``view_ids()``, ``camera(view_id)`` (a copy), ``size(view_id)``, ``unit_mm``, ``thresholds(view_id)`` (the view's
  over the defaults).
* ``landmarks(view_id)``: named points on the drawing in pixels (``{name: (column, row)}``, names of
  ``a-z 0-9 _``, at most 64), which the camera solve and callers such as hair fitting read; ``{}`` when none.
* ``reference_outline(view_id)``: the drawing's ``normalized`` outline, made once per view and read-only, so a fit
  that scores many times pays for it once.
* ``mask(view_id)``: the drawing filled: a cut-out's alpha, else its subject against the plain background
  (``view_outline.subject_mask``).
* ``strokes(view_id)``: the drawing's lines before any fill, 0 (the ground) to 1 (black): how much darker each pixel
  is than the ground, a cut-out laid on white first. A filled outline can't tell a mirrored fringe from the right
  one, or show which way hair flows; the strokes can.

The score
=========

``score_views(board, silhouettes)``, with ``silhouettes`` ``{view_id: (mask, depth or None)}`` drawn at each view's
camera and size, returns per view: ``iou``; the outline distance ``mean``, ``p95`` and ``max`` in pixels and
millimetres; ``centre_offset`` (right and down are positive) in both; ``size_ratio``; ``mm_per_px``; ``gaps``
(with depth only: where the outlines are farthest apart, ``IN`` where the model reaches past the drawing and ``OUT``
where the drawing reaches past the model, each with its 3D point and in-plane offset); ``passed`` and ``reasons``.
Both outlines are ``view_outline.normalized`` the same way first.

Millimetres are measured at the outline's own depth when depth is given, else from an orthographic camera's scale;
a perspective view without depth has none (reason ``no_scale``).

A view is judged only against the thresholds set for it: ``iou_min`` (reason ``iou_below``) and ``p95_mm_max``
(``p95_over``). With neither, ``passed`` is None (``no_threshold``), never a pass. A view that can't be compared says
why (``not_compared:no_silhouette``, ``no_picture``, ``size_mismatch``, ``no_subject``, ``not_in_view``) and is
never skipped. The board passes only when every view passes; any failing view is listed in ``failed``.

Default thresholds are chosen on held-out characters that differ from each other, never tuned to one, and the
choice is recorded with its results.
