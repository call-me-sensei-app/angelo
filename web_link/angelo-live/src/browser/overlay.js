// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The page's only UI: a small corner dot for the link's state, and the "Edit <file> in Angelo" chip an Alt-click
// shows. Plain DOM with inline styles, so nothing depends on the developer's CSS or framework.

const COLORS = { off: "#8b8b8b", waiting: "#8b8b8b", live: "#3fb950", linked: "#3fb950", updating: "#d29922",
  lost: "#f85149" };

export class Overlay {
  constructor({ showDot }) {
    this.showDot = showDot;
    this.dot = null;
    this.chip = null;
    this.hideChip = this.hideChip.bind(this);
    this.onKey = (event) => {
      if (event.key === "Escape") this.hideChip();
    };
    this.onOutside = (event) => {
      if (this.chip && event.target !== this.chip) this.hideChip();
    };
  }

  /** state: off | waiting | live | linked | updating | lost; `title` says it in words (hover). */
  setState(state, title) {
    if (!this.showDot || typeof document === "undefined") return;
    if (!this.dot) {
      this.dot = document.createElement("div");
      this.dot.setAttribute("role", "status");
      Object.assign(this.dot.style, {
        position: "fixed", right: "10px", bottom: "10px", width: "10px", height: "10px", borderRadius: "50%",
        zIndex: "2147483646", pointerEvents: "auto", boxShadow: "0 0 0 2px rgba(0,0,0,0.35)", transition:
          "background-color 120ms linear",
      });
      document.body.appendChild(this.dot);
    }
    this.dot.style.backgroundColor = COLORS[state] ?? COLORS.off;
    this.dot.title = title;
    this.dot.setAttribute("aria-label", title);
    this.dot.dataset.state = state;
  }

  /** Show the chip at a viewport point; `onClick` runs when it is clicked. */
  showChip(x, y, label, onClick) {
    if (typeof document === "undefined") return;
    this.hideChip();
    const chip = document.createElement("button");
    chip.type = "button";
    chip.textContent = label;
    Object.assign(chip.style, {
      position: "fixed", left: `${Math.round(x + 8)}px`, top: `${Math.round(y + 8)}px`, zIndex: "2147483647",
      font: "12px/1.4 system-ui, -apple-system, Segoe UI, sans-serif", color: "#f0f0f0", background: "#1f2328",
      border: "1px solid #3d444d", borderRadius: "6px", padding: "4px 10px", cursor: "pointer",
      boxShadow: "0 4px 12px rgba(0,0,0,0.35)", whiteSpace: "nowrap",
    });
    chip.addEventListener("click", (event) => {
      event.stopPropagation();
      this.hideChip();
      onClick();
    });
    document.body.appendChild(chip);
    this.chip = chip;
    this.chipTimer = setTimeout(this.hideChip, 8000);
    setTimeout(() => {
      if (this.chip !== chip) return;
      document.addEventListener("pointerdown", this.onOutside, true);
      document.addEventListener("keydown", this.onKey, true);
    }, 0);
  }

  hideChip() {
    clearTimeout(this.chipTimer);
    if (typeof document !== "undefined") {
      document.removeEventListener("pointerdown", this.onOutside, true);
      document.removeEventListener("keydown", this.onKey, true);
    }
    this.chip?.remove();
    this.chip = null;
  }

  remove() {
    this.hideChip();
    this.dot?.remove();
    this.dot = null;
  }
}
