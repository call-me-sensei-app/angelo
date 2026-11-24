// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// Framing (doc/angelo/web_export.rst, "Framing"): what the page looks at. Angelo sends its focus (the selection,
// the active object and the scene camera, by name; focus.json) without exporting again; the toolbar frames the
// scene camera, the selection or everything, and Follow re-frames whenever Angelo's selection changes. Otherwise
// the person's orbit is never moved: a reload keeps it.
import * as THREE from "three";

const KEY = "angelo-web-preview-framing";
const FALLBACK_DIRECTION = new THREE.Vector3(1, 0.7, 1).normalize();
const MARGIN = 1.15;   // Room around a framed object, as a share of its bounding sphere.

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}

function save(state) {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* Storage off: the choice lasts this visit. */ }
}

export function setupFraming({ camera, controls, $ }) {
  const state = Object.assign({ follow: false }, load());
  let focus = { selected: [], active: null, camera: null, version: -1 };
  let root = null, cameras = [], mode = null, text = {}, sent = null;

  function found(names) {
    if (!root) return [];
    const objects = [];
    for (const name of names) {
      const object = root.getObjectByName(name);
      if (object) objects.push(object);
    }
    return objects;
  }

  function shot() {
    // The scene camera Angelo names, else the file's first perspective camera.
    const named = focus.camera ? cameras.find((item) => item.name === focus.camera) : null;
    return named || cameras.find((item) => item.isPerspectiveCamera) || null;
  }

  function near(radius) {
    camera.near = radius / 200;
    camera.far = radius * 200;
    camera.updateProjectionMatrix();
    controls.update();
  }

  function fitBox(box) {
    // Keep the person's direction of view and move in or out until the box's bounding sphere fits.
    const sphere = box.getBoundingSphere(new THREE.Sphere());
    const radius = sphere.radius || 1;
    const direction = camera.position.clone().sub(controls.target);
    if (direction.lengthSq() < 1e-12) direction.copy(FALLBACK_DIRECTION);
    direction.normalize();
    const half = THREE.MathUtils.degToRad(camera.fov) / 2;
    const fit = Math.min(half, Math.atan(Math.tan(half) * camera.aspect));
    const distance = (radius * MARGIN) / Math.sin(Math.max(fit, 1e-3));
    controls.target.copy(sphere.center);
    camera.position.copy(sphere.center).addScaledVector(direction, distance);
    near(radius);
  }

  function frameCamera() {
    const view = shot();
    if (!view) return false;
    view.updateWorldMatrix(true, false);
    const box = new THREE.Box3().setFromObject(root);
    const center = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
    camera.position.setFromMatrixPosition(view.matrixWorld);
    camera.quaternion.setFromRotationMatrix(view.matrixWorld);
    if (view.isPerspectiveCamera) camera.fov = view.fov;
    const ahead = new THREE.Vector3(0, 0, -1).applyQuaternion(camera.quaternion);
    controls.target.copy(camera.position).addScaledVector(ahead, Math.max(camera.position.distanceTo(center), 1e-3));
    near(box.isEmpty() ? 1 : box.getSize(new THREE.Vector3()).length() / 2 || 1);
    return true;
  }

  function frameObjects(objects) {
    const box = new THREE.Box3();
    for (const object of objects) box.expandByObject(object);
    if (box.isEmpty()) return false;
    fitBox(box);
    return true;
  }

  function frame(which) {
    // True when it framed; a mode that has nothing to frame leaves the view as it is.
    if (!root) return false;
    if (which === "camera") return frameCamera();
    if (which === "selection") return frameObjects(found(focus.selected));
    return frameObjects([root]);
  }

  function round(vector) {
    return [vector.x, vector.y, vector.z].map((value) => Math.round(value * 1e4) / 1e4);
  }

  function report() {
    // What the page frames goes back to Angelo (web_preview_frame): the agent's angelo_view WEB facts say what the
    // person's browser looks at. Sent only when it differs from the last report.
    const body = JSON.stringify({
      mode, follow: state.follow,
      framed: mode === "selection" ? found(focus.selected).map((object) => object.name) : [],
      camera: mode === "camera" && shot() ? shot().name : null,
      focus_version: focus.version, position: round(camera.position), target: round(controls.target),
    });
    if (body === sent) return;
    sent = body;
    fetch("frame", { method: "POST", headers: { "Content-Type": "application/json" }, body }).catch(() => {});
  }

  function draw() {
    report();
    const hasCamera = Boolean(shot());
    const selection = found(focus.selected);
    $("frame-camera").disabled = !hasCamera;
    $("frame-selection").disabled = selection.length === 0;
    for (const [id, which] of [["frame-camera", "camera"], ["frame-selection", "selection"], ["frame-all", "all"]])
      $(id).setAttribute("aria-pressed", String(mode === which));
    $("frame-follow").setAttribute("aria-pressed", String(state.follow));
    // Angelo has a selection the page can't find: say why the Selection button does nothing.
    const missing = focus.selected.length > 0 && selection.length === 0;
    $("frame-selection").title = missing ? text.frame_missing || "" : "";
  }

  function choose(which) {
    if (frame(which)) mode = which;
    draw();
  }

  $("frame-camera").addEventListener("click", () => choose("camera"));
  $("frame-selection").addEventListener("click", () => choose("selection"));
  $("frame-all").addEventListener("click", () => choose("all"));
  $("frame-follow").addEventListener("click", () => {
    state.follow = !state.follow;
    save(state);
    if (state.follow) choose("selection");
    else draw();
  });

  async function fetchFocus() {
    try {
      const response = await fetch("focus.json", { cache: "no-store" });
      if (response.ok) return await response.json();
    } catch { /* Angelo closed: the toolbar keeps the last focus. */ }
    return null;
  }

  return {
    setText(strings) {
      text = strings || {};
      for (const [id, key] of [["frame-camera", "frame_camera"], ["frame-selection", "frame_selection"],
        ["frame-all", "frame_all"], ["frame-follow", "frame_follow"]])
        $(id).textContent = text[key] || "";
      draw();
    },
    // A scene loaded. The first one is framed (the camera, else Angelo's selection, else everything); later ones
    // keep the person's view. True when it framed (an empty scene doesn't, so the next load is still first).
    async loaded(scene, gltf, first) {
      root = scene;
      cameras = [];
      scene.traverse((node) => { if (node.isCamera) cameras.push(node); });
      if (first) {
        const latest = await fetchFocus();
        if (latest) focus = latest;
        const order = state.follow ? ["selection", "camera", "all"] : ["camera", "selection", "all"];
        mode = order.find((which) => frame(which)) || null;
      }
      draw();
      return mode !== null;
    },
    // Angelo's focus changed (the "focus" event): Follow frames the new selection; otherwise only the toolbar.
    async focused() {
      const latest = await fetchFocus();
      if (!latest || latest.version === focus.version) return;
      focus = latest;
      if (state.follow && frame("selection")) mode = "selection";
      draw();
    },
  };
}
