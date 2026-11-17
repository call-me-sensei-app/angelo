// SPDX-FileCopyrightText: 2026 Hyperbond Studio Pte. Ltd.
// SPDX-License-Identifier: MIT
//
// The end-to-end scenario for an app that tracks a static asset: open by Alt-click, live frames (rename, delete,
// add, a material and a geometry change, a delta, a delta the page must refuse), a reconnect and an Angelo restart
// mid-session, a save, a capture, and the end. After every step: what changed is exactly what Angelo changed, the
// developer's material and child stay, nothing is uploaded for unchanged objects, and the page never reloads.

import { Session } from "./harness.mjs";
import { StandIn } from "../test/standin.js";
import { cloneSpec, deltaGlb, findNode, fullGlb, imageDigests, removeNode } from "../test/fixtures/gltf.js";

function usersOf(spec, material) {
  const found = [];
  const visit = (nodes) => nodes.forEach((node) => {
    if ((node.primitives ?? (node.mesh ? [node.mesh] : [])).some((mesh) => mesh.material === material)) found.push(node.name);
    visit(node.children ?? []);
  });
  visit(spec.nodes);
  return found;
}

function parentName(nodes, name, parent = null) {
  for (const node of nodes) {
    if (node.name === name) return parent;
    const found = parentName(node.children ?? [], name, node.name);
    if (found !== undefined) return found;
  }
  return undefined;
}

function renumber(spec, start) {
  let uid = start;
  const visit = (nodes) => nodes.forEach((node) => {
    node.uid = String(uid++);
    visit(node.children ?? []);
  });
  visit(spec.nodes);
  return spec;
}

const sum = (uploads) => Object.values(uploads).reduce((a, b) => a + b, 0);
const same = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());

export async function assetScenario(appName) {
  const session = new Session(appName);
  const check = session.check.bind(session);
  try {
    await session.start();
    const { roles, instances } = session.model;
    let spec = session.model.spec();
    const loads = await session.test("t.loads");
    const developerChildParent = await session.test(`t.object(${JSON.stringify(roles.developerChild)}).parent`);

    const opened = await session.openByAltClick(roles.pick);
    check("Alt-click shows the chip and opens the asset", opened.open.asset === session.assetPath,
      `${opened.chip}; node ${opened.open.node}; Alt-clicks ${opened.clicks}`);

    // 1. The first live frame is what the page already shows: nothing changes, nothing is uploaded.
    let before = await session.identity();
    let uploads = await session.test("t.uploads");
    let result = await session.push(fullGlb(spec));
    let after = await session.identity();
    check("first frame previewed with every tracked copy", result.ack.code === "previewed"
      && result.ack.instances === instances, `instances ${result.ack.instances}, ${result.ms} ms`);
    check("first frame changes no object", Session.changed(before, after).length === 0
      && Session.gone(before, after).length === 0, Session.changed(before, after).join(", "));
    check("first frame uploads nothing", sum(await session.test("t.uploads")) === sum(uploads));

    // 2. Rename.
    const renamed = `${roles.rename}_renamed`;
    spec = cloneSpec(spec);
    findNode(spec.nodes, roles.rename).name = renamed;
    before = await session.identity();
    result = await session.push(fullGlb(spec));
    after = await session.identity();
    const renamedIds = before.filter((item) => item.name === roles.rename).map((item) => item.id);
    check("rename keeps the objects and renames them", renamedIds.length > 0
      && renamedIds.every((id) => after.find((item) => item.id === id)?.name === renamed)
      && (await session.test("t.eventCount('renamed')")) >= 1);

    const tint = await session.test("t.info.tint ?? null");
    if (tint) {
      check("the developer's in-place material change survives an edit that doesn't change that material",
        (await session.test(`t.object(${JSON.stringify(renamed === `${tint.node}_renamed` ? renamed : tint.node)})`)).color === tint.color);
    }

    // 3. Delete a node the developer attached a child under.
    const survivor = parentName(spec.nodes, roles.remove);
    spec = cloneSpec(spec);
    const removed = removeNode(spec.nodes, roles.remove);
    const removedNames = [];
    const collect = (node) => {
      removedNames.push(node.name);
      (node.children ?? []).forEach(collect);
    };
    collect(removed);
    before = await session.identity();
    await session.push(fullGlb(spec));
    after = await session.identity();
    const child = await session.test(`t.object(${JSON.stringify(roles.developerChild)})`);
    const expectedParent = removedNames.includes(developerChildParent) ? survivor : developerChildParent;
    check("delete removes the node and keeps the developer's child", after.every((item) => !removedNames.includes(item.name))
      && child && child.parent === expectedParent, `child under ${child?.parent}, expected ${expectedParent}`);
    check("delete touches nothing else", Session.changed(before, after).length === 0);

    // 4. Add.
    spec = cloneSpec(spec);
    findNode(spec.nodes, roles.addUnder).children = [...(findNode(spec.nodes, roles.addUnder).children ?? []),
      { name: "Added_by_Angelo", uid: "9001", position: [0, 0.6, 0], mesh: { shape: "box", size: 0.25,
        material: roles.recolor } }];
    before = await session.identity();
    await session.push(fullGlb(spec));
    after = await session.identity();
    check("add creates the node under its parent", after.filter((item) => item.name === "Added_by_Angelo"
      && item.parent === roles.addUnder).length === instances);
    check("add touches nothing else", Session.changed(before, after).length === 0);

    // 5. A material colour and the geometry under the developer's own material.
    spec = cloneSpec(spec);
    spec.materials[roles.recolor].color = [1, 0.1, 0.1, 1];
    const devNode = findNode(spec.nodes, roles.developerMaterial);
    (devNode.primitives?.[0] ?? devNode.mesh).segments = 7;
    (devNode.primitives?.[0] ?? devNode.mesh).shape = "sphere";
    before = await session.identity();
    uploads = await session.test("t.uploads");
    await session.push(fullGlb(spec));
    after = await session.identity();
    const expected = new Set([...usersOf(spec, roles.recolor), roles.developerMaterial]);
    const changed = Session.changed(before, after);
    check("only the recoloured objects and the re-shaped one change", same(new Set(changed), expected)
      || changed.every((name) => expected.has(name)) && [...expected].every((name) => changed.includes(name)),
      `changed ${[...new Set(changed)].join(", ")}`);
    const devAfter = after.filter((item) => item.name === roles.developerMaterial);
    const devBefore = before.filter((item) => item.name === roles.developerMaterial);
    check("the developer's material stays while its geometry changes", devAfter.every((item, i) =>
      item.materials.join() === devBefore[i].materials.join() && item.geometry !== devBefore[i].geometry));

    // 6. A delta: one node's geometry; only it changes.
    const deltaNode = findNode(spec.nodes, roles.delta);
    spec = cloneSpec(spec);
    findNode(spec.nodes, roles.delta).mesh.size = (deltaNode.mesh.size ?? 1) * 1.4;
    before = await session.identity();
    const base = session.version;
    result = await session.push(deltaGlb(spec, [deltaNode.uid], { knownImages: imageDigests(spec) }),
      { kind: "delta", base, removed: [] });
    after = await session.identity();
    check("a delta is previewed", result.ack.code === "previewed", `${result.ms} ms`);
    check("a delta changes only its node", same(new Set(Session.changed(before, after)), new Set([roles.delta])),
      Session.changed(before, after).join(", "));

    // 7. A delta on a version the page doesn't show: refused whole, the page asks for a full frame.
    before = await session.identity();
    const resyncing = session.standin.frame((header) => header.code === "resync", 10000, false).catch(() => null);
    await session.push(deltaGlb(spec, [deltaNode.uid]), { kind: "delta", base: base - 3, removed: [] },
      { expect: null });
    const resync = await resyncing;
    check("a delta on another base is refused and asks Angelo for a full frame", resync?.header.asset === session.assetPath);
    check("the refused delta changed nothing", Session.changed(before, await session.identity()).length === 0);
    await session.push(fullGlb(spec));

    // 7b. A stub for a picture the page never had: refused whole, a full frame is asked for.
    const stubbed = cloneSpec(spec);
    stubbed.textures = { ...(stubbed.textures ?? {}), unseen: { size: 32, seed: 99 } };
    const deltaMaterial = findNode(stubbed.nodes, roles.delta).mesh.material;
    stubbed.materials[deltaMaterial] = { ...stubbed.materials[deltaMaterial], texture: "unseen" };
    before = await session.identity();
    const stubBase = session.version;
    const stubResyncing = session.standin.frame((header) => header.code === "resync", 10000, false).catch(() => null);
    await session.push(deltaGlb(stubbed, [deltaNode.uid], { knownImages: imageDigests(stubbed) }),
      { kind: "delta", base: stubBase, removed: [] }, { expect: null });
    const stubResync = await stubResyncing;
    check("a stub for an unknown picture is refused and asks for a full frame", Boolean(stubResync),
      stubResync ? "" : JSON.stringify(await session.test("t.events.slice(-4)")).slice(0, 600));
    check("the refused stub changed nothing", Session.changed(before, await session.identity()).length === 0);
    await session.push(fullGlb(spec));

    // 8. A reconnect mid-session: the same Angelo, a new connection, the same frame again.
    before = await session.identity();
    uploads = await session.test("t.uploads");
    const reconnecting = session.standin.next((event) => event.event === "frame" && event.header.type === "connected"
      && Number(event.client) !== session.client, 20000);
    session.standin.send({ cmd: "disconnect", client: session.client });
    const again = await reconnecting;
    session.client = Number(again.client);
    session.standin.send({ cmd: "send", client: session.client, header: { type: "opened", asset: session.assetPath } });
    await session.push(fullGlb(spec));
    after = await session.identity();
    check("a reconnect leaves every object as it was", Session.changed(before, after).length === 0
      && Session.gone(before, after).length === 0);
    check("a reconnect uploads nothing", sum(await session.test("t.uploads")) === sum(uploads));

    // 9. Angelo restarts mid-session (a new process: new record, new uids); the same scene comes back.
    const record = session.standin.record;
    await session.standin.stop();
    session.standin = await new StandIn(record).start();
    const reconnected = await session.standin.frame((header) => header.type === "connected", 20000);
    session.client = Number(reconnected.client);
    session.standin.send({ cmd: "send", client: session.client, header: { type: "opened", asset: session.assetPath } });
    spec = renumber(cloneSpec(spec), 5000);
    session.version = 0; // A new Angelo counts its versions again.
    before = await session.identity();
    uploads = await session.test("t.uploads");
    result = await session.push(fullGlb(spec));
    after = await session.identity();
    check("an Angelo restart leaves every object as it was", result.ack.code === "previewed"
      && Session.changed(before, after).length === 0 && Session.gone(before, after).length === 0);
    check("an Angelo restart uploads nothing", sum(await session.test("t.uploads")) === sum(uploads));

    // 10. Save: the page already shows the saved version, so nothing is fetched or changed.
    before = await session.identity();
    const saved = await session.saved(spec, 1, session.version);
    check("save is reimported", saved.code === "reimported", JSON.stringify(saved));
    check("save changes nothing the page shows", Session.changed(before, await session.identity()).length === 0);

    // 11. One more live change, a capture of it, then the end puts the saved asset back.
    const savedColour = (await session.test(`t.object(${JSON.stringify(usersOf(spec, roles.recolor)[0])})`)).color;
    spec = cloneSpec(spec);
    spec.materials[roles.recolor].color = [0.1, 0.2, 1, 1];
    await session.push(fullGlb(spec));
    const picture = await session.capture();
    check("a capture of the canvas arrives and isn't blank", picture.bytes > 0 && picture.facts?.nonblank,
      `${picture.facts?.width}x${picture.facts?.height}, ${picture.facts?.colours} colours, ${Math.round(picture.elapsed * 1000)} ms`);
    before = await session.identity();
    session.standin.send({ cmd: "send", client: session.client, header: { type: "ended", asset: session.assetPath,
      reason: "angelo" } });
    await session.page.until("__angeloLiveTest.eventCount('restored') > 0", 15000);
    after = await session.identity();
    const restoredColour = (await session.test(`t.object(${JSON.stringify(usersOf(spec, roles.recolor)[0])})`)).color;
    check("ended puts back the saved colour", restoredColour === savedColour, `${restoredColour} vs ${savedColour}`);
    check("ended keeps every object", Session.gone(before, after).length === 0);
    check("the developer's child is still there", Boolean(await session.test(`t.object(${JSON.stringify(roles.developerChild)})`)));
    check("the page never reloaded", (await session.test("t.loads")) === loads);
    if (await session.test("typeof t.info.held === 'function'")) {
      const held = await session.test("t.info.held()");
      const present = new Set((await session.identity()).map((item) => item.id));
      check("the app's own references (physics bodies) still point at objects in the scene",
        held.length > 0 && held.every((id) => present.has(id)), `${held.length} held`);
    }
  } catch (error) {
    check("scenario ran to the end", false, error?.stack ?? String(error));
    for (const line of (session.page?.console ?? []).slice(-25)) console.log(`    page ${line.type}: ${line.text}`);
    for (const event of session.standin?.events.slice(-10) ?? []) console.log(`    angelo: ${JSON.stringify(event).slice(0, 300)}`);
  }
  return session.finish();
}
