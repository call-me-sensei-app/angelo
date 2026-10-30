// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: GPL-2.0-or-later
// The report column beside the scene: folded to its title, or hidden whole with one small button left in the corner
// (it carries the live state's dot) to bring it back; I toggles it from the keyboard. The person's choice stays in
// this page's storage, so live updates and browser reloads keep it. Storage can be off (a private window, blocked
// site data), so every access is guarded and the page works without it.
const KEY = "angelo.web-preview.report";
const $ = (id) => document.getElementById(id);

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || {}; } catch { return {}; }
}

function save(state) {
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* Storage off: the choice lasts this visit. */ }
}

function editable(target) {
  return target instanceof Element && Boolean(target.closest("input, textarea, select, [contenteditable]"));
}

export function setupPanel() {
  const panel = $("panel"), fold = $("fold"), hide = $("hide"), reopen = $("reopen");
  const saved = load();
  const state = { folded: saved.folded === true, hidden: saved.hidden === true };
  let text = {};

  function label(button, words) {
    button.setAttribute("aria-label", words || "");
    button.title = words || "";
  }

  function draw() {
    panel.classList.toggle("folded", state.folded);
    panel.classList.toggle("away", state.hidden);
    panel.inert = state.hidden;   // Hidden whole: out of the tab order and the accessibility tree.
    reopen.hidden = !state.hidden;
    fold.setAttribute("aria-expanded", String(!state.folded));
    hide.setAttribute("aria-expanded", String(!state.hidden));
    reopen.setAttribute("aria-expanded", String(!state.hidden));
    label(fold, state.folded ? text.unfold : text.fold);
    label(hide, text.hide && text.hide + " (I)");
    label(reopen, text.show && text.show + " (I)");
  }

  function setHidden(hidden) {
    const focused = document.activeElement;
    state.hidden = hidden;
    save(state);
    draw();
    // Keep the keyboard where the person is: on the control that undoes what they just did.
    if (hidden && (panel.contains(focused) || focused === reopen)) reopen.focus();
    if (!hidden && focused === reopen) hide.focus();
  }

  fold.addEventListener("click", () => { state.folded = !state.folded; save(state); draw(); });
  hide.addEventListener("click", () => setHidden(true));
  reopen.addEventListener("click", () => setHidden(false));
  window.addEventListener("keydown", (event) => {
    if (event.key !== "i" && event.key !== "I") return;
    if (event.ctrlKey || event.metaKey || event.altKey || event.repeat || editable(event.target)) return;
    event.preventDefault();
    setHidden(!state.hidden);
  });
  draw();
  document.documentElement.dataset.report = "ready";   // The column shows only once its saved state is applied.

  return {
    text(strings) { text = strings || {}; draw(); },
    status(kind) { reopen.dataset.state = kind; },
  };
}
