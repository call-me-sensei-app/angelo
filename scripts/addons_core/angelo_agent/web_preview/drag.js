// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// Every drag on the preview page is the page's own, never the browser's. A press held and moved over a picture or a
// canvas has default actions of its own: the browser drags the picture out as a file (a ghost image follows the
// pointer, and the browser cancels the page's pointer, so the page stops hearing the moves), selects the text it
// passes over, or, on a touch screen, scrolls and zooms. 1.0.9 on Windows: the Compare Wipe divider barely moved,
// because Chrome started dragging Angelo's picture out instead. So every surface the page drags turns those off where
// the press starts, and follows the pointer with pointer events (mouse, touch and pen alike) captured on the surface,
// so the drag keeps going outside it. The stylesheet does the rest: no selection on the stage, pictures never
// draggable, touch-action:none on every drag surface (app.css).

// No native drag-and-drop from these elements: not draggable, and a drag the browser starts anyway is cancelled.
export function noNativeDrag(...elements) {
  for (const element of elements) {
    element.draggable = false;
    element.addEventListener("dragstart", (event) => event.preventDefault());
  }
}

// A surface another controller drags (OrbitControls on the scene's canvas, which captures the pointer itself): a press
// on it starts no native drag, selection or middle-button autoscroll. The controller still gets every pointer event.
export function ownPresses(surface) {
  noNativeDrag(surface);
  surface.addEventListener("pointerdown", (event) => { if (event.isPrimary) event.preventDefault(); });
}

// A drag the page follows itself: ``move(event)`` on the press and on every move of that pointer until it is
// released or cancelled, then ``end(event)``. ``accept(event)`` can turn a press down (a button over the surface keeps
// its click and focus). Only the primary pointer's main button drags: the first finger, a pen's tip, a mouse's left
// button; a second finger never takes over.
export function followDrag(surface, { accept = () => true, move, end = () => {} }) {
  noNativeDrag(surface);
  let pointer = null;
  surface.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0 || !accept(event)) return;
    event.preventDefault();
    pointer = event.pointerId;   // A new primary press always starts afresh: the previous one has ended.
    try {
      surface.setPointerCapture(pointer);
    } catch {
      // A pointer the browser no longer tracks: moves still arrive while it is over the surface.
    }
    move(event);
  });
  surface.addEventListener("pointermove", (event) => { if (event.pointerId === pointer) move(event); });
  const finish = (event) => {
    if (event.pointerId !== pointer) return;
    pointer = null;
    end(event);
  };
  for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) surface.addEventListener(type, finish);
}
