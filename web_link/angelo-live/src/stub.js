// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// What production builds get: the same API, doing nothing. No network, no DOM, no strings that name a route.

const handle = Object.freeze({ untrack() {} });

const live = Object.freeze({
  track() {
    return handle;
  },
  untrack() {},
  edit() {},
  on() {
    return () => {};
  },
  stop() {
    return Promise.resolve();
  },
  get state() {
    return { link: "off", message: "", session: 0, assets: [] };
  },
});

export function createAngeloLive() {
  return live;
}
