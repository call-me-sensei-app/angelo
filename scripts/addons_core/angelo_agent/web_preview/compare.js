// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// The Compare view: Angelo's viewport, drawn from this page's camera with the scene's lights, World and view
// transform (web_compare.py), over the page's own Three.js picture. Wipe shows Angelo left of the handle and
// Three.js right of it; Difference shows where they differ (four times brighter than the difference). Both report
// the average difference. While comparing the camera stays put; a new export or a resize draws Angelo again.
const $ = (id) => document.getElementById(id);
const AMPLIFY = 4;

export function setupCompare({ renderer, scene, camera, controls, canvas, look, text }) {
  const view = $("compare-view"), shot = $("angelo-shot"), handle = $("wipe"), difference = $("difference");
  const open = $("compare"), done = $("compare-done"), wipeMode = $("mode-wipe"), differenceMode = $("mode-difference");
  const readout = $("compare-readout");
  let active = false, mode = "wipe", split = 0.5, asked = 0, shown = false;

  function say(message) { readout.textContent = message; }

  function ask() {
    if (!active) return;
    asked += 1;
    view.classList.add("waiting");
    say(text().capturing || "");
    camera.updateMatrixWorld();
    const body = { id: asked, matrix: Array.from(camera.matrixWorld.elements), fov: camera.fov, near: camera.near,
      far: camera.far, width: canvas.clientWidth, height: canvas.clientHeight };
    fetch("compare", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .catch(() => {});
  }

  // Both pictures at the shot's size, Three.js drawn now over the page's background, as the agent's capture does.
  function pixels() {
    const width = shot.naturalWidth, height = shot.naturalHeight;
    const page = document.createElement("canvas");
    page.width = width; page.height = height;
    const pageContext = page.getContext("2d", { willReadFrequently: true });
    pageContext.fillStyle = getComputedStyle(document.body).backgroundColor;
    pageContext.fillRect(0, 0, width, height);
    renderer.render(scene, camera);
    pageContext.drawImage(canvas, 0, 0, width, height);
    const angelo = document.createElement("canvas");
    angelo.width = width; angelo.height = height;
    const angeloContext = angelo.getContext("2d", { willReadFrequently: true });
    angeloContext.drawImage(shot, 0, 0, width, height);
    return { width, height, web: pageContext.getImageData(0, 0, width, height),
      angelo: angeloContext.getImageData(0, 0, width, height) };
  }

  function measure() {
    if (!shown) return;
    const { width, height, web, angelo } = pixels();
    const out = new ImageData(width, height);
    let total = 0;
    for (let i = 0; i < web.data.length; i += 4) {
      for (let c = 0; c < 3; c += 1) {
        const delta = Math.abs(web.data[i + c] - angelo.data[i + c]);
        total += delta;
        out.data[i + c] = Math.min(255, delta * AMPLIFY);
      }
      out.data[i + 3] = 255;
    }
    const percent = (total / (width * height * 3 * 255)) * 100;
    view.dataset.difference = percent.toFixed(2);   // Read by the GUI scenario and the agent's checks.
    say((text().average_difference || "{value}%").replace("{value}", percent.toFixed(1)));
    difference.width = width; difference.height = height;
    difference.getContext("2d").putImageData(out, 0, 0);
  }

  function draw() {
    view.dataset.mode = mode;
    shot.style.clipPath = mode === "wipe" ? `inset(0 ${(1 - split) * 100}% 0 0)` : "none";
    handle.style.left = `${split * 100}%`;
    handle.hidden = mode !== "wipe" || !shown;
    difference.hidden = mode !== "difference";
    shot.hidden = mode === "difference";
    wipeMode.setAttribute("aria-pressed", String(mode === "wipe"));
    differenceMode.setAttribute("aria-pressed", String(mode === "difference"));
  }

  function start() {
    if (active) return;
    active = true;
    controls.enabled = false;
    look.force(true);
    view.hidden = false;
    open.setAttribute("aria-pressed", "true");
    $("lookbar").classList.add("comparing");
    draw();
    ask();
  }

  function stop() {
    if (!active) return;
    active = false;
    controls.enabled = true;
    look.force(false);
    view.hidden = true;
    view.classList.remove("waiting");
    open.setAttribute("aria-pressed", "false");
    $("lookbar").classList.remove("comparing");
    if (shot.src) URL.revokeObjectURL(shot.src);
    shot.removeAttribute("src");
    delete view.dataset.difference;
  }

  async function arrived() {
    if (!active) return;
    const report = await (await fetch("viewport.json", { cache: "no-store" })).json();
    if (report.request !== asked) return;   // An older request's picture: the newer one is on its way.
    if (!report.ok) {
      view.classList.remove("waiting");
      draw();
      say((text().compare_failed || "{reason}").replace("{reason}", report.error || ""));
      return;
    }
    const blob = await (await fetch("viewport.png", { cache: "no-store" })).blob();
    if (shot.src) URL.revokeObjectURL(shot.src);
    shot.src = URL.createObjectURL(blob);
    await shot.decode();
    if (report.request !== asked || !active) return;
    view.classList.remove("waiting");
    shown = true;
    draw();
    measure();
  }

  function drag(event) {
    const box = view.getBoundingClientRect();
    split = Math.min(1, Math.max(0, (event.clientX - box.left) / Math.max(box.width, 1)));
    draw();
  }

  open.addEventListener("click", () => (active ? stop() : start()));
  done.addEventListener("click", stop);
  wipeMode.addEventListener("click", () => { mode = "wipe"; draw(); });
  differenceMode.addEventListener("click", () => { mode = "difference"; draw(); measure(); });
  view.addEventListener("pointerdown", (event) => {
    if (mode !== "wipe" || event.target.closest("button")) return;
    view.setPointerCapture(event.pointerId);
    drag(event);
  });
  view.addEventListener("pointermove", (event) => { if (view.hasPointerCapture(event.pointerId)) drag(event); });
  window.addEventListener("keydown", (event) => { if (event.key === "Escape" && active) stop(); });
  new ResizeObserver(() => { if (active) ask(); }).observe(canvas);

  return {
    arrived,
    sceneLoaded() { if (active) ask(); },   // Angelo's scene changed: draw both sides again.
    get active() { return active; },
  };
}
