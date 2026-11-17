// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The bridge's bookkeeping with a fake link and fake tabs: URL mapping, acknowledgements summed over tabs, the
// capture relay's one-outstanding and deadline rules.

import { describe, expect, it, vi } from "vitest";
import { Bridge } from "../src/vite/bridge.js";

const GLB = Buffer.concat([Buffer.from("glTF"), Buffer.alloc(16)]);
const PNG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

function setup(options = {}) {
  const sent = [];
  const link = { live: true, state: "live", message: "", session: 1, send: vi.fn((header, payload) => {
    sent.push({ header, payload });
    return true;
  }) };
  const events = [];
  const tab = (name) => ({ name, send: (event, data) => events.push({ to: name, event, data }) });
  const hot = { broadcast: (event, data) => events.push({ to: "*", event, data }), sendTo: (client, event, data) => client.send(event, data) };
  const bridge = new Bridge({ link, hot, project: "/p", projectName: "app", routeBase: "/@angelo-live/T/",
    toProject: (url) => (url.startsWith("/models/") ? `public${url}` : null), timing: { preview: 50, save: 50, capture: 50 },
    ...options });
  return { bridge, link, sent, events, tab };
}

const hello = (bridge, id, client, tracked = [], extra = {}) => bridge.fromTab("angelo:hello", { tab: id, tracked, ...extra }, client);

describe("tabs and assets", () => {
  it("answers hello with the state and maps tracked URLs to project paths", () => {
    const { bridge, events, tab } = setup();
    const a = tab("a");
    hello(bridge, "A", a, ["/models/chair.glb", "/elsewhere.glb"]);
    expect(events[0]).toMatchObject({ to: "a", event: "angelo:state", data: { link: "live", session: 1 } });
    expect(events.find((e) => e.event === "angelo:status")).toMatchObject({ data: { asset: "/elsewhere.glb", code: "outside" } });
    expect(bridge.assets.get("public/models/chair.glb").urls.has("/models/chair.glb")).toBe(true);
  });

  it("turns edit into open, and refuses locally when Angelo isn't there", () => {
    const { bridge, sent, events, tab, link } = setup();
    const a = tab("a");
    hello(bridge, "A", a, ["/models/chair.glb"]);
    bridge.fromTab("angelo:edit", { tab: "A", url: "/models/chair.glb", node: "Seat" }, a);
    expect(sent[0].header).toMatchObject({ type: "open", project: "/p", project_name: "app", asset: "public/models/chair.glb", node: "Seat" });
    expect(sent[0].header.id).toMatch(/^[0-9a-f]{24}$/);
    bridge.fromAngelo({ type: "refused", id: sent[0].header.id, code: "declined", message: "No" }, Buffer.alloc(0));
    expect(events.at(-1)).toMatchObject({ event: "angelo:refused", data: { asset: "/models/chair.glb", code: "declined" } });
    link.live = false;
    link.message = "Angelo isn't running";
    bridge.fromTab("angelo:edit", { tab: "A", url: "/models/chair.glb" }, a);
    expect(events.at(-1)).toMatchObject({ event: "angelo:refused", data: { code: "offline" } });
  });

  it("ignores messages for a tab id from another socket", () => {
    const { bridge, sent, tab } = setup();
    hello(bridge, "A", tab("a"), ["/models/chair.glb"]);
    bridge.fromTab("angelo:edit", { tab: "A", url: "/models/chair.glb" }, tab("intruder"));
    expect(sent).toHaveLength(0);
  });

  it("serves only the newest scene and tells the tracking tabs", () => {
    const { bridge, events, tab } = setup();
    hello(bridge, "A", tab("a"), ["/models/chair.glb"]);
    hello(bridge, "B", tab("b"), []);
    bridge.fromAngelo({ type: "opened", asset: "public/models/chair.glb" }, Buffer.alloc(0));
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 3 }, GLB);
    const scene = events.find((e) => e.event === "angelo:scene");
    expect(scene).toMatchObject({ to: "a", data: { asset: "/models/chair.glb", version: 3, session: 1 } });
    expect(events.some((e) => e.to === "b" && e.event === "angelo:scene")).toBe(false);
    const id = bridge.assets.get("public/models/chair.glb").id;
    expect(bridge.sceneBytes(id, 3)).toBe(GLB);
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 4 }, GLB);
    expect(bridge.sceneBytes(id, 3)).toBeNull();
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 5 }, Buffer.from("nope nope nope"));
    expect(bridge.sceneBytes(id, 4)).toBe(GLB);
  });

  it("sends a late-tracking tab the open asset and its newest scene", () => {
    const { bridge, events, tab } = setup();
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 1 }, GLB);
    hello(bridge, "A", tab("a"), ["/models/chair.glb"]);
    expect(events.filter((e) => e.to === "a").map((e) => e.event)).toEqual(["angelo:state", "angelo:opened", "angelo:scene"]);
  });

  it("keeps Vite quiet for linked assets and a while after", () => {
    const { bridge } = setup();
    bridge.fromAngelo({ type: "opened", asset: "public/models/chair.glb" }, Buffer.alloc(0));
    expect(bridge.quietPaths()).toEqual(["public/models/chair.glb"]);
    bridge.fromAngelo({ type: "ended", asset: "public/models/chair.glb", reason: "angelo" }, Buffer.alloc(0));
    expect(bridge.quietPaths()).toEqual(["public/models/chair.glb"]);
    expect(bridge.quietPaths(Date.now() + 60000)).toEqual([]);
  });
});

describe("acknowledgements", () => {
  it("sum instances over the tabs and answer once", () => {
    const { bridge, sent, tab } = setup();
    const a = tab("a");
    const b = tab("b");
    hello(bridge, "A", a, ["/models/chair.glb"]);
    hello(bridge, "B", b, ["/models/chair.glb"]);
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 7 }, GLB);
    bridge.fromTab("angelo:ack", { tab: "A", asset: "/models/chair.glb", code: "previewed", version: 7, instances: 2 }, a);
    expect(sent).toHaveLength(0);
    bridge.fromTab("angelo:ack", { tab: "B", asset: "/models/chair.glb", code: "previewed", version: 6, instances: 9 }, b);
    expect(sent).toHaveLength(0);
    bridge.fromTab("angelo:ack", { tab: "B", asset: "/models/chair.glb", code: "previewed", version: 7, instances: 3 }, b);
    expect(sent.map((s) => s.header)).toEqual([{ type: "status", asset: "public/models/chair.glb", code: "previewed", version: 7, instances: 5 }]);
  });

  it("answer with what arrived when a tab stays silent, and fail only when every answer failed", async () => {
    const { bridge, sent, tab } = setup();
    const a = tab("a");
    hello(bridge, "A", a, ["/models/chair.glb"]);
    hello(bridge, "B", tab("b"), ["/models/chair.glb"]);
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 1 }, GLB);
    bridge.fromTab("angelo:ack", { tab: "A", asset: "/models/chair.glb", code: "preview_failed", version: 1,
      message: "Couldn't load http://localhost:5173/x.glb" }, a);
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(sent[0].header).toEqual({ type: "status", asset: "public/models/chair.glb", code: "preview_failed", version: 1,
      message: "Couldn't load (a URL)" });
  });

  it("answer at once with no tab: 0 instances, and the saved file as reimported", () => {
    const { bridge, sent } = setup();
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 2 }, GLB);
    bridge.fromAngelo({ type: "saved", asset: "public/models/chair.glb", save: 4, files: [] }, Buffer.alloc(0));
    expect(sent.map((s) => s.header.code)).toEqual(["previewed", "reimported"]);
    expect(sent[0].header.instances).toBe(0);
    expect(sent[1].header.save).toBe(4);
  });

  it("count a tab that said bye out", () => {
    const { bridge, sent, tab } = setup();
    const a = tab("a");
    hello(bridge, "A", a, ["/models/chair.glb"]);
    hello(bridge, "B", a, ["/models/chair.glb"]); // A second live link on the same page (React StrictMode).
    bridge.fromTab("angelo:bye", { tab: "A" }, a);
    bridge.fromAngelo({ type: "scene", asset: "public/models/chair.glb", version: 3 }, GLB);
    bridge.fromTab("angelo:ack", { tab: "B", asset: "/models/chair.glb", code: "previewed", version: 3, instances: 1 }, a);
    expect(sent.map((s) => s.header.code)).toEqual(["previewed"]);
  });

  it("count a closed tab out", () => {
    const { bridge, sent, tab } = setup();
    const a = tab("a");
    const b = tab("b");
    hello(bridge, "A", a, ["/models/chair.glb"]);
    hello(bridge, "B", b, ["/models/chair.glb"]);
    bridge.fromAngelo({ type: "saved", asset: "public/models/chair.glb", save: 1 }, Buffer.alloc(0));
    bridge.fromTab("angelo:ack", { tab: "A", asset: "/models/chair.glb", code: "reimported", save: 1, instances: 1 }, a);
    bridge.tabClosed(b);
    expect(sent.map((s) => s.header)).toEqual([{ type: "status", asset: "public/models/chair.glb", code: "reimported", save: 1 }]);
  });
});

describe("captures", () => {
  const capture = (bridge, id = "c1") => bridge.fromAngelo({ type: "capture", id, asset: "public/models/chair.glb", max: 640 }, Buffer.alloc(0));

  it("relay the first PNG in time, once", () => {
    const { bridge, sent, events, tab } = setup();
    hello(bridge, "A", tab("a"), ["/models/chair.glb"]);
    hello(bridge, "B", tab("b"), ["/models/chair.glb"]);
    capture(bridge);
    const asks = events.filter((e) => e.event === "angelo:capture");
    expect(asks.map((e) => e.to)).toEqual(["a", "b"]);
    const pageId = asks[0].data.id;
    expect(pageId).not.toBe("c1");
    expect(bridge.expectsCapture(pageId)).toBe(true);
    expect(bridge.captured(pageId, PNG, { width: 641, height: 10 })).toBe(false); // Larger than asked.
    expect(bridge.captured(pageId, PNG, { width: 640, height: 360 })).toBe(true);
    expect(bridge.captured(pageId, PNG, { width: 640, height: 360 })).toBe(false);
    expect(sent).toHaveLength(1);
    expect(sent[0].header).toEqual({ type: "status", asset: "public/models/chair.glb", code: "captured", id: "c1" });
    expect(sent[0].payload).toBe(PNG);
  });

  it("refuse a second capture while one is outstanding, and answer failed after the deadline", async () => {
    const { bridge, sent, events, tab } = setup();
    hello(bridge, "A", tab("a"), ["/models/chair.glb"]);
    capture(bridge, "c1");
    capture(bridge, "c2");
    expect(sent[0].header).toMatchObject({ code: "capture_failed", id: "c2", message: "Another capture is waiting" });
    const pageId = events.find((e) => e.event === "angelo:capture").data.id;
    await new Promise((resolve) => setTimeout(resolve, 80));
    expect(sent[1].header).toMatchObject({ code: "capture_failed", id: "c1" });
    expect(bridge.expectsCapture(pageId)).toBe(false);
    expect(bridge.captured(pageId, PNG, { width: 10, height: 10 })).toBe(false); // Late: never sent to Angelo.
    expect(sent).toHaveLength(2);
  });

  it("answer failed when every tab failed, or capture is off, or nothing tracks the asset", () => {
    const off = setup({ capture: false });
    capture(off.bridge);
    expect(off.sent[0].header).toMatchObject({ code: "capture_failed", message: "capture is off on this page" });

    const none = setup();
    capture(none.bridge);
    expect(none.sent[0].header.code).toBe("capture_failed");

    const pageOff = setup();
    hello(pageOff.bridge, "A", pageOff.tab("a"), ["/models/chair.glb"], { capture: false });
    capture(pageOff.bridge);
    expect(pageOff.sent[0].header.message).toBe("capture is off on this page");

    const failing = setup();
    const a = failing.tab("a");
    hello(failing.bridge, "A", a, ["/models/chair.glb"]);
    capture(failing.bridge);
    const pageId = failing.events.find((e) => e.event === "angelo:capture").data.id;
    failing.bridge.fromTab("angelo:capture_failed", { tab: "A", id: "stale", message: "x" }, a);
    expect(failing.sent).toHaveLength(0);
    failing.bridge.fromTab("angelo:capture_failed", { tab: "A", id: pageId, message: "The tab is hidden" }, a);
    expect(failing.sent[0].header).toMatchObject({ code: "capture_failed", id: "c1", message: "The tab is hidden" });
  });
});
