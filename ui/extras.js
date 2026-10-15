"use strict";
/* Batch 2 features: teleport, map pins, chest organizer, backpack <-> chest transfers,
   presets, item encyclopedia, share (host switch), backups timeline.
   Builds on app.js (S, call, addEdits, ...) and world.js (W, renderMarkers, ...). */

const X = { selectMode: false, picked: new Set(), presets: null, encSel: null, cmp: new Set() };

/* ------------------------------------------------------------ helpers */
function freeBackpackSlot(skip = []) {
  const used = new Set((S.view ? S.view.inventory : []).map((x) => x.slot).concat(skip));
  for (let s = 0; s < 32; s++) if (!used.has(s)) return s;
  return null;
}
function freeChestSlot(c, skip = []) {
  const used = new Set(c.items.map((x) => x.slot).concat(skip));
  const cap = c.capacity || Math.max((c.maxSlotIndex ?? -1) + 1, 16);
  for (let s = 0; s < cap; s++) if (!used.has(s)) return s;
  return null;
}
function worldToPx(x, y) {
  const inv = W.view && W.view.map && W.view.map.inverse;
  return inv ? [(x - inv.c) / inv.a, (y - inv.f) / inv.e] : [null, null];
}
function pendingCharEdit(op) { return S.edits.find((e) => e.op === op); }
function currentPins() {
  const pe = pendingCharEdit("pins");
  if (pe) return pe.pins.map((p) => ({ ...p }));
  return (W.base ? W.base.markers : []).filter((m) => m.kind === "pin").map((m) => ({ type: 0, x: m.x, y: m.y }));
}
function needChar() {
  if (!S.view) { toast("Open a character first (top bar).", true); return false; }
  return true;
}

/* describe new change types in the changes bar / review list */
const _describe = describe;
describe = function (e) {
  if (e.op === "teleport") return `Move ${S.view ? S.view.character : "character"} to X ${Math.round(e.x)} Y ${Math.round(e.y)}`;
  if (e.op === "pins") return `Map pins → ${e.pins.length}`;
  if (e.op === "put") return `Backpack slot ${e.slot}: put ${(S.byId[e.entry.ItemData] || {}).name || "item"}${e.entry.Count > 1 ? " ×" + fmt(e.entry.Count) : ""}`;
  if (e.op === "unlock") return `Unlock ${e.family}${e.ids ? ": " + e.ids.length : " (all)"}`;
  return _describe(e);
};
const _describeWorld = describeWorld;
describeWorld = function (e) {
  const c = W.base && W.base.containers[e.container];
  if (e.op === "put") return `${c ? c.name : "Chest"}: put ${(S.byId[e.entry.ItemData] || {}).name || "item"}${e.entry.Count > 1 ? " ×" + fmt(e.entry.Count) : ""}`;
  if (e.op === "setslots") return `${c ? c.name : "Chest"}: reorganized`;
  if (e.op === "move") return `Move ${e.name || "object"} next to you`;
  if (e.op === "removeActor") return `Remove ${e.name || "dropped item"}`;
  return _describeWorld(e);
};

/* world changes that aren't about one chest slot get their own key */
const _wEditKey = wEditKey;
wEditKey = function (e) {
  if (e.op === "playerpos") return "playerpos:" + e.charGuid;
  if (e.op === "move") return "move:" + e.actor;
  if (e.op === "removeActor" || e.op === "collect") return e.op + ":" + (e.container || "") + ":" + e.actors.join(",");
  return _wEditKey(e);
};
const _describeWorld2 = describeWorld;
describeWorld = function (e) {
  if (e.op === "playerpos") return "Your position in this world (teleport)";
  if (e.op === "collect") return `Collect ${e.actors.length} dropped item(s) into ${(W.base.containers[e.container] || {}).name || "chest"}`;
  if (e.op === "removeActor") return `Delete ${e.actors.length} dropped item(s)`;
  return _describeWorld2(e);
};

/* show pending teleport / pin changes on the map */
const _renderMarkers = renderMarkers;
renderMarkers = function () {
  if (W.view && W.base) {
    const base = W.base.markers.filter((m) => m.kind !== "player" && m.kind !== "pin");
    const tp = pendingCharEdit("teleport");
    let player = W.base.markers.find((m) => m.kind === "player");
    if (tp) {
      const [px, py] = worldToPx(tp.x, tp.y);
      player = { id: null, kind: "player", name: "Your character (after save)", x: tp.x, y: tp.y, px, py };
    }
    const pins = currentPins().map((p, i) => {
      const [px, py] = worldToPx(p.x, p.y);
      return { id: null, kind: "pin", name: "Map pin " + (i + 1), x: p.x, y: p.y, px, py, pinIndex: i };
    });
    W.view.markers = base.filter((m) => !m.id || W.view.containers[m.id]).concat(player ? [player] : [], pins);
  }
  _renderMarkers();
  document.querySelectorAll("#mapMarkers .marker").forEach((d) => {
    const m = (W.view.markers || []).find((x) => d.style.left === x.px + "px" && d.style.top === x.py + "px" && d.classList.contains(x.kind));
    if (m && m.id && X.picked.has(m.id)) d.classList.add("picked");
  });
};

/* keep the map in step with character changes (teleport, pins) */
const _renderAll = renderAll;
renderAll = function () {
  _renderAll();
  if (W.view && document.querySelector("#tab-world.active")) renderMarkers();
};

/* pins: detail with delete */
const _renderObjectDetail = renderObjectDetail;
renderObjectDetail = function () {
  _renderObjectDetail();
  const o = W.selObject;
  if (o && o.kind === "dropped" && o.actor) dropActions($("detail"), [{ id: o.actor, label: o.name }]);
  if (o && o.kind === "pin") {
    const del = el("button", "btn danger", "Delete this pin");
    del.onclick = () => {
      if (!needChar()) return;
      const pins = currentPins().filter((p) => !(Math.abs(p.x - o.x) < 1 && Math.abs(p.y - o.y) < 1));
      addEdits([{ op: "pins", pins }]);
      W.selObject = null; renderWorldDetail(); renderMarkers();
    };
    const a = el("div", "d-actions"); a.appendChild(del); $("detail").appendChild(a);
  }
};

/* ------------------------------------------------- map context menu */
function showCtx(sx, sy, wx, wy) {
  const ctx = $("mapCtx");
  ctx.innerHTML = `<div class="ctx-note">X ${Math.round(wx)}  Y ${Math.round(wy)}</div>`;
  const tp = el("button", null, "Move my character here");
  tp.onclick = async () => {
    hideCtx();
    if (!needChar()) return;
    try {
      const g = await call("teleport_target", wx, wy);
      addEdits([{ op: "teleport", x: wx, y: wy, z: g.z }]);
      // the world file keeps its own copy of where you are; change it too
      if (W.base && S.view.charGuid) addWorldEdits([{ op: "playerpos", charGuid: S.view.charGuid, x: wx, y: wy, z: g.z }]);
      toast(`Teleport set (ground from nearby ${g.nearest.replace(/^BP_|_C$/g, "")}). Review & save, then load the game.`);
      renderMarkers();
    } catch (e) { toast(e.message, true); }
  };
  const pin = el("button", null, "Add a map pin here");
  pin.onclick = () => {
    hideCtx();
    if (!needChar()) return;
    addEdits([{ op: "pins", pins: currentPins().concat([{ type: 0, x: wx, y: wy }]) }]);
    renderMarkers();
  };
  ctx.append(tp, pin);
  // live teleport while the game runs (needs Live cheats)
  if (typeof C !== "undefined" && C.status && C.status.modAlive && C.status.player) {
    const live = el("button", null, "Teleport there now (in game)");
    live.onclick = async () => {
      hideCtx();
      let z = "";
      try { z = String((await call("teleport_target", wx, wy)).z - 150); } catch (e) { /* unknown height: keep current */ }
      await call("cheat_send", "dw.teleport", [String(wx), String(wy), z]);
      toast(z ? "Teleporting… (Live cheats tab has \"go back\")" : "Teleporting at your current height; nothing saved nearby to find the ground.");
    };
    ctx.insertBefore(live, tp);
  }
  if (typeof C !== "undefined" && C.status && C.status.installed) {
    const home = el("button", null, "Set home here");
    home.onclick = async () => {
      hideCtx();
      try { await call("cheat_set_home", wx, wy, null); toast("Home set. Num − (or Live cheats → Go home) takes you there."); if (typeof loadHome === "function") loadHome(); }
      catch (err) { toast(err.message, true); }
    };
    ctx.appendChild(home);
  }
  if (W.selObject && W.selObject.kind === "pin") {
    const mv = el("button", null, "Move the selected pin here");
    mv.onclick = () => {
      hideCtx();
      const o = W.selObject;
      const pins = currentPins().map((p) => (Math.abs(p.x - o.x) < 1 && Math.abs(p.y - o.y) < 1 ? { ...p, x: wx, y: wy } : p));
      addEdits([{ op: "pins", pins }]);
      W.selObject = { ...o, x: wx, y: wy };
      renderMarkers();
    };
    ctx.appendChild(mv);
  }
  ctx.classList.remove("hidden");
  const wrap = $("mapWrap");
  ctx.style.left = Math.min(sx, wrap.clientWidth - 230) + "px";
  ctx.style.top = Math.min(sy, wrap.clientHeight - 130) + "px";
}
function hideCtx() { $("mapCtx").classList.add("hidden"); }

/* -------------------------------------------- box select + organizer */
function bindExtrasMap() {
  const wrap = $("mapWrap");
  wrap.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    const inv = W.view && W.view.map && W.view.map.inverse;
    if (!inv) return;
    const r = wrap.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    const px = (sx - W.x) / W.zoom, py = (sy - W.y) / W.zoom;
    showCtx(sx, sy, inv.a * px + inv.c, inv.e * py + inv.f);
  });
  document.addEventListener("click", (e) => { if (!e.target.closest("#mapCtx")) hideCtx(); });

  let box = null;
  wrap.addEventListener("mousedown", (e) => {
    if (!X.selectMode || e.button !== 0) return;
    e.stopImmediatePropagation();
    const r = wrap.getBoundingClientRect();
    box = { x0: e.clientX - r.left, y0: e.clientY - r.top, x1: e.clientX - r.left, y1: e.clientY - r.top };
    drawBox(box);
  }, true);
  wrap.addEventListener("mousemove", (e) => {
    if (!box) return;
    e.stopImmediatePropagation();
    const r = wrap.getBoundingClientRect();
    box.x1 = e.clientX - r.left; box.y1 = e.clientY - r.top;
    drawBox(box);
  }, true);
  window.addEventListener("mouseup", (e) => {
    if (!box) return;
    const b = box; box = null;
    $("mapBox").classList.add("hidden");
    const [ax, bx] = [Math.min(b.x0, b.x1), Math.max(b.x0, b.x1)], [ay, by] = [Math.min(b.y0, b.y1), Math.max(b.y0, b.y1)];
    if (!e.shiftKey) { X.picked.clear(); X.drops = []; }
    for (const m of W.view.markers) {
      if (!m.id || !["storage", "station"].includes(m.kind) || !W.visible.has(m.kind)) continue;
      const sx = m.px * W.zoom + W.x, sy = m.py * W.zoom + W.y;
      if (sx >= ax && sx <= bx && sy >= ay && sy <= by) X.picked.add(m.id);
    }
    // dropped items inside the box (canvas layer)
    if (W.layers && W.visible.has("dropped")) {
      X.drops = X.drops || [];
      for (const row of W.layers.rows) {
        if (row[0] !== "dropped" || !row[4]) continue;
        const sx = row[1] * W.zoom + W.x, sy = row[2] * W.zoom + W.y;
        if (sx >= ax && sx <= bx && sy >= ay && sy <= by && !X.drops.some((d) => d.id === row[4]))
          X.drops.push({ id: row[4], label: W.layers.labels[row[3]] });
      }
    }
    W.selChest = null; W.selObject = null;
    renderMarkers();
    renderSelection();
  }, true);
  wrap.addEventListener("click", (e) => { if (X.selectMode) e.stopImmediatePropagation(); }, true);

  $("selectTool").addEventListener("click", () => {
    X.selectMode = !X.selectMode;
    $("selectTool").classList.toggle("on", X.selectMode);
    wrap.classList.toggle("selecting", X.selectMode);
    if (X.selectMode) toast("Drag a box around your chests. Hold Shift to add more.");
    else { X.picked.clear(); renderMarkers(); renderWorldDetail(); }
  });
}
function drawBox(b) {
  const d = $("mapBox");
  d.style.left = Math.min(b.x0, b.x1) + "px"; d.style.top = Math.min(b.y0, b.y1) + "px";
  d.style.width = Math.abs(b.x1 - b.x0) + "px"; d.style.height = Math.abs(b.y1 - b.y0) + "px";
  d.classList.remove("hidden");
}

function dropActions(box, drops) {
  if (!drops || !drops.length) return;
  box.appendChild(el("div", "sec-title", `${drops.length} dropped item(s)`));
  const counts = {};
  drops.forEach((d) => { counts[d.label] = (counts[d.label] || 0) + 1; });
  box.appendChild(el("div", "hint", Object.entries(counts).slice(0, 8).map(([k, n]) => `${esc(k)}${n > 1 ? " (" + n + ")" : ""}`).join(", ")));
  const a = el("div", "d-actions");
  const col = el("button", "btn gold", "Collect into a chest…");
  col.disabled = !W.view.editable;
  col.onclick = () => {
    const r = refPoint();
    const chests = Object.values(W.view.containers).filter((c) => c.kind === "storage" && c.editable !== false)
      .sort((p, q) => (r ? Math.hypot(p.x - r.x, p.y - r.y) - Math.hypot(q.x - r.x, q.y - r.y) : 0)).slice(0, 40);
    const rows = chests.map((c) => `<div class="place" data-id="${esc(c.id)}"><span class="dot" style="background:${KIND_COLORS.storage}"></span><div class="pname">${esc(c.name)} <span class="psub">${c.items.length}${c.capacity ? "/" + c.capacity : ""}</span></div><div class="pdist">${distText(c)}</div></div>`).join("");
    showModal("Collect into…", `<p class="hint">Stacks are merged where possible. If the chest is too small, nothing is changed.</p><div class="place-list">${rows}</div>`, []);
    document.querySelectorAll("#modalBody .place").forEach((p) => p.addEventListener("click", () => {
      hideModal();
      addWorldEdits([{ op: "collect", container: p.dataset.id, actors: drops.map((d) => d.id) }]);
      toast(`${drops.length} dropped item(s) will go into the chest. Review & save.`);
    }));
  };
  const del = el("button", "btn danger", "Delete them");
  del.disabled = !W.view.editable;
  del.onclick = () => {
    if (!confirm(`Delete ${drops.length} dropped item(s) from the world?`)) return;
    addWorldEdits([{ op: "removeActor", actors: drops.map((d) => d.id) }]);
  };
  a.append(col, del);
  box.appendChild(a);
}

function renderSelection() {
  const box = $("detail");
  const ids = [...X.picked];
  box.innerHTML = `<div class="d-banner"><h3>${ids.length} chest(s) selected</h3><div class="sub">Storage, stations and dropped items inside the box</div></div>`;
  dropActions(box, X.drops);
  if (!ids.length) { box.appendChild(el("p", "hint", "No chests selected. Drag a box around chests on the map (Shift adds more).")); return; }
  const stacks = ids.reduce((n, id) => n + W.view.containers[id].items.length, 0);
  const cap = ids.reduce((n, id) => n + (W.view.containers[id].capacity || 0), 0);
  box.appendChild(el("div", "kv", `<span>Stacks now</span><span>${stacks}</span><span>Known capacity</span><span>${cap || "unknown"}</span>`));
  const list = el("div", "overview-list");
  for (const id of ids) {
    const c = W.view.containers[id];
    const row = el("div", "place", `<span class="dot" style="background:${KIND_COLORS[c.kind]}"></span><div class="pname">${esc(c.name)}</div><div class="pdist">${c.items.length}${c.capacity ? "/" + c.capacity : ""}</div>`);
    row.onclick = () => { X.picked.delete(id); renderMarkers(); renderSelection(); };
    row.title = "Click to leave this one out";
    list.appendChild(row);
  }
  box.appendChild(list);
  const a = el("div", "d-actions");
  const org = el("button", "btn gold", "Organize");
  org.title = "Merge stacks of the same item and group items by kind across these chests";
  org.disabled = !W.view.editable;
  org.onclick = async () => {
    try {
      const r = await call("organize", ids.filter((id) => W.view.containers[id].kind === "storage"));
      addWorldEdits(r.edits);
      toast(`Organized: ${r.summary.stacksBefore} stacks → ${r.summary.stacksAfter} across ${r.summary.chests} chests. Review & save.`);
    } catch (e) { toast(e.message, true); }
  };
  const clr = el("button", "btn ghost", "Clear selection");
  clr.onclick = () => { X.picked.clear(); renderMarkers(); renderWorldDetail(); };
  a.append(org, clr);
  box.appendChild(a);
  box.appendChild(el("p", "hint", "Only storage chests are reorganized; crafting stations keep their contents."));
}

/* ------------------------------------------- chest panel additions */
const _renderChestDetail = renderChestDetail;
renderChestDetail = function () {
  _renderChestDetail();
  const c = W.view.containers[W.selChest];
  if (!c) return;
  const box = $("detail");
  const editable = W.view.editable && c.editable !== false;
  const pending = new Set(W.edits.filter((e) => e.container === c.id).map((e) => e.slot));
  const it = c.items.find((x) => x.slot === W.selSlot);
  const tools = el("div", "d-actions");
  if (it) {
    const toBag = el("button", "btn", "Move to backpack");
    toBag.disabled = !editable || !S.view;
    toBag.onclick = () => moveToBackpack(c, [it]);
    tools.appendChild(toBag);
  }
  if (c.kind === "grave" && c.items.length) {
    const all = el("button", "btn gold", "Bring everything to my backpack");
    all.disabled = !editable || !S.view;
    all.onclick = () => moveToBackpack(c, c.items);
    tools.appendChild(all);
  }
  if (c.kind === "grave" && c.class === "BP_PlayerGravestone_C") {
    const mv = el("button", "btn", "Move gravestone next to me");
    mv.title = "Puts the gravestone beside your character's saved position. New: check it in game.";
    mv.disabled = !editable || !c.actor;
    mv.onclick = async () => {
      const me = (W.view.markers || []).find((m) => m.kind === "player");
      if (!me) { toast("Your character's position isn't known for this world.", true); return; }
      try {
        const g = await call("teleport_target", me.x + 150, me.y);
        addWorldEdits([{ op: "move", actor: c.actor, name: c.name, x: me.x + 150, y: me.y, z: g.z - 150 }]);
        toast("Gravestone will be moved next to you. Review & save.");
      } catch (e) { toast(e.message, true); }
    };
    tools.appendChild(mv);
  }
  if (c.canAdd) {
    const fromBag = el("button", "btn", "Put from backpack…");
    fromBag.disabled = !editable || !S.view;
    fromBag.onclick = () => pickFromBackpack(c);
    tools.appendChild(fromBag);
    const sel = el("select");
    sel.innerHTML = '<option value="">Fill with preset…</option>';
    loadPresets().then((p) => { for (const x of p.builtin.concat(p.user)) sel.insertAdjacentHTML("beforeend", `<option value="${esc(x.id)}">${esc(x.name)}</option>`); });
    sel.disabled = !editable;
    sel.onchange = async () => {
      const id = sel.value; sel.value = "";
      if (!id) return;
      try {
        const r = await call("preset_edits", id, "world", c.id);
        if (!r.edits.length) { toast("Nothing fits in this chest.", true); return; }
        addWorldEdits(r.edits);
        toast(`${r.name}: ${r.edits.length} change(s)` + (r.skipped.length ? `; no room for ${r.skipped.join(", ")}` : "") + ".");
      } catch (e) { toast(e.message, true); }
    };
    tools.appendChild(sel);
    const sp = el("button", "btn ghost", "Save as preset");
    sp.onclick = async () => {
      const name = prompt("Name for this preset:", c.name + " kit");
      if (!name) return;
      try { await call("save_preset", name, "world", c.id); X.presets = null; toast("Preset saved."); renderPresets(); } catch (e) { toast(e.message, true); }
    };
    tools.appendChild(sp);
  }
  if (c.capacity) box.querySelector(".d-banner .sub").insertAdjacentText("beforeend", ` · ${c.capacity} slots`);
  box.appendChild(el("div", "sec-title", "Move & fill"));
  box.appendChild(tools);
  if (pending.size) box.appendChild(el("p", "hint", "Items with unsaved changes can't be moved until you save."));
};

async function moveToBackpack(c, items) {
  if (!needChar()) return;
  const pending = new Set(W.edits.filter((e) => e.container === c.id).map((e) => e.slot));
  const taken = [];
  const wEdits = [], cEdits = [];
  for (const it of items) {
    if (pending.has(it.slot)) continue;
    const slot = freeBackpackSlot(taken);
    if (slot == null) { toast("Your backpack is full.", true); break; }
    const r = await call("chest_entry", c.id, it.slot);
    taken.push(slot);
    wEdits.push({ op: "remove", container: c.id, slot: it.slot });
    cEdits.push({ op: "put", container: "Inventory", slot, entry: r.entry });
  }
  if (!wEdits.length) return;
  addWorldEdits(wEdits);
  addEdits(cEdits);
  toast(`${wEdits.length} item(s) will move to your backpack. Review & save.`);
}

function pickFromBackpack(c) {
  const used = new Set(S.edits.filter((e) => e.container === "Inventory").map((e) => e.slot));
  const items = S.view.inventory.filter((x) => x.itemId && !used.has(x.slot));
  const grid = items.map((x) => `<div class="pick" data-slot="${x.slot}">${x.icon ? `<img src="${iconUrl(x.icon)}" alt="">` : '<div class="noimg">?</div>'}${esc(x.name)}${x.count > 1 ? " ×" + fmt(x.count) : ""}</div>`).join("");
  showModal(`Put into ${c.name}`, `<p class="hint">Click items to move them from your backpack into this chest.</p><div class="picker-grid">${grid || '<div class="hint">Backpack is empty.</div>'}</div>`, []);
  const taken = [];
  document.querySelectorAll("#modalBody .pick").forEach((p) => p.addEventListener("click", async () => {
    const bslot = Number(p.dataset.slot);
    const slot = freeChestSlot(c, taken);
    if (slot == null) { toast("This chest is full.", true); return; }
    try {
      const r = await call("backpack_entry", bslot);
      taken.push(slot);
      addEdits([{ op: "remove", container: "Inventory", slot: bslot }]);
      addWorldEdits([{ op: "put", container: c.id, slot, entry: r.entry }]);
      p.remove();
    } catch (e) { toast(e.message, true); }
  }));
}

/* backpack item -> chest (Inventory detail) */
const _renderDetail = renderDetail;
renderDetail = function () {
  _renderDetail();
  const entry = currentEntry();
  if (!entry || !S.selected || S.selected.container !== "Inventory" || !S.view.editable) return;
  const b = el("button", "btn", "Send to a chest…");
  b.onclick = () => sendToChest(S.selected.slot, entry);
  const a = $("detail").querySelector(".d-actions");
  if (a) a.appendChild(b);
};

async function sendToChest(bslot, entry) {
  if (!W.view) { await worldInit(); }
  if (!W.view) { toast("Open a world first (World tab).", true); return; }
  const r = refPoint();
  const chests = Object.values(W.view.containers).filter((c) => c.canAdd && c.kind === "storage" && c.editable !== false)
    .sort((a, b) => (r ? Math.hypot(a.x - r.x, a.y - r.y) - Math.hypot(b.x - r.x, b.y - r.y) : 0)).slice(0, 40);
  const rows = chests.map((c) => `<div class="place" data-id="${esc(c.id)}"><span class="dot" style="background:${KIND_COLORS[c.kind]}"></span><div class="pname">${esc(c.name)} <span class="psub">${c.items.length}${c.capacity ? "/" + c.capacity : ""} stacks</span></div><div class="pdist">${distText(c)}</div></div>`).join("");
  showModal(`Send ${entry.name} to…`, `<div class="place-list">${rows || '<div class="hint">No storage chests in this world.</div>'}</div>`, []);
  document.querySelectorAll("#modalBody .place").forEach((p) => p.addEventListener("click", async () => {
    const c = W.view.containers[p.dataset.id];
    const slot = freeChestSlot(c);
    if (slot == null) { toast("That chest is full.", true); return; }
    try {
      const rr = await call("backpack_entry", bslot);
      hideModal();
      addEdits([{ op: "remove", container: "Inventory", slot: bslot }]);
      addWorldEdits([{ op: "put", container: c.id, slot, entry: rr.entry }]);
      toast(`${entry.name} will move to ${c.name}. Review & save.`);
    } catch (e) { toast(e.message, true); }
  }));
}

/* -------------------------------------------------------------- presets */
async function loadPresets() {
  if (!X.presets) X.presets = await call("presets");
  return X.presets;
}

async function renderPresets() {
  const box = $("presetList");
  if (!box) return;
  const p = await loadPresets();
  box.innerHTML = "";
  for (const x of p.builtin.concat(p.user)) {
    const card = el("div", "preset", `<h3>${esc(x.name)}</h3><p>${esc(x.desc || "")}</p>`);
    if (x.items) {
      const icons = el("div", "icons");
      for (const row of x.items.slice(0, 12)) { const it = S.byId[row.itemId]; if (it && it.icon) icons.insertAdjacentHTML("beforeend", `<img src="${iconUrl(it.icon)}" title="${esc(it.name)}" alt="">`); }
      card.appendChild(icons);
    }
    const a = el("div", "row-gap");
    const fill = el("button", "btn gold", "Fill backpack");
    fill.onclick = async () => {
      if (!needChar()) return;
      try {
        const r = await call("preset_edits", x.id, "char");
        if (!r.edits.length) { toast("No room in your backpack.", true); return; }
        addEdits(r.edits);
        toast(`${r.name}: ${r.edits.length} change(s)` + (r.skipped.length ? `; no room for ${r.skipped.join(", ")}` : "") + ".");
      } catch (e) { toast(e.message, true); }
    };
    a.appendChild(fill);
    if (x.items) {
      const del = el("button", "btn ghost", "Delete");
      del.onclick = async () => { if (!confirm(`Delete preset "${x.name}"?`)) return; await call("delete_preset", x.id); X.presets = null; renderPresets(); };
      a.appendChild(del);
    }
    card.appendChild(a);
    box.appendChild(card);
  }
  const save = el("div", "preset", "<h3>Save my backpack</h3><p>Make a preset from what you carry now, to refill a chest or backpack later.</p>");
  const sb = el("button", "btn", "Save as preset");
  sb.onclick = async () => {
    const name = prompt("Name for this preset:", "My kit");
    if (!name) return;
    try { await call("save_preset", name, "char"); X.presets = null; renderPresets(); toast("Preset saved."); } catch (e) { toast(e.message, true); }
  };
  save.appendChild(sb);
  box.appendChild(save);
}

/* --------------------------------------------------------- encyclopedia */
function ownedMap() {
  const m = new Map();
  const add = (id, n, where) => { const o = m.get(id) || { n: 0, where: [] }; o.n += n; o.where.push(where); m.set(id, o); };
  if (S.view) {
    for (const x of S.view.inventory.concat(S.view.equipment)) if (x.itemId) add(x.itemId, x.count || 1, { kind: "bag", label: "Backpack", n: x.count || 1 });
  }
  if (W.view) {
    for (const c of Object.values(W.view.containers)) if (c.kind === "storage" || c.kind === "station")
      for (const it of c.items) add(it.itemId, it.count || 1, { kind: "chest", id: c.id, label: c.name, n: it.count || 1 });
  }
  return m;
}

function renderEncyclopedia() {
  const grid = $("encGrid");
  const q = $("itemSearch").value.trim().toLowerCase();
  const cat = $("itemCat").value;
  const onlyOwned = $("itemOwned").checked;
  const owned = ownedMap();
  grid.innerHTML = "";
  const list = S.items.filter((i) => (!q || i.name.toLowerCase().includes(q)) && (!cat || i.category === cat) && (!onlyOwned || owned.has(i.id)))
    .sort((a, b) => a.name.localeCompare(b.name)).slice(0, 500);
  for (const it of list) {
    const o = owned.get(it.id);
    const d = el("div", "enc" + (X.encSel === it.id ? " sel" : ""), (it.icon ? `<img src="${iconUrl(it.icon)}" alt="" loading="lazy">` : '<div class="noimg">?</div>') + esc(it.name) + (o ? `<span class="own">×${fmt(o.n)}</span>` : ""));
    d.onclick = () => { X.encSel = it.id; renderEncyclopedia(); renderItemDetail(it, owned.get(it.id)); };
    grid.appendChild(d);
  }
  if (!list.length) grid.appendChild(el("div", "hint", "No items match."));
}

function renderItemDetail(it, o) {
  const box = $("detail");
  box.innerHTML = `<div class="d-banner"><h3>${esc(it.name)}</h3><div class="sub">${esc(it.category || "")}</div></div>
    ${it.icon ? `<img class="d-icon" src="${iconUrl(it.icon)}" alt="">` : ""}
    <p class="d-desc">${esc(it.description || "")}</p>`;
  const kv = [];
  if (it.powerLevel) kv.push(["Power Level", it.powerLevel]);
  if (it.skill) kv.push(["Skill", it.skill]);
  if (it.maxDurability) kv.push(["Max durability", fmt(it.maxDurability)]);
  else kv.push(["Max stack", fmt(it.maxStack || 9999)]);
  box.appendChild(el("div", "kv", kv.map(([a, b]) => `<span>${esc(a)}</span><span>${esc(b)}</span>`).join("")));
  box.appendChild(el("div", "sec-title", "You have"));
  if (!o) box.appendChild(el("p", "hint", W.view ? "None in your backpack or your storage." : "None in your backpack. Open the World tab to include chests."));
  else {
    const list = el("div", "overview-list");
    for (const w of o.where) {
      const row = el("div", "place", `<span class="dot" style="background:${w.kind === "bag" ? "#6ec3ff" : KIND_COLORS.storage}"></span><div class="pname">${esc(w.label)}</div><div class="pdist">×${fmt(w.n)}</div>`);
      if (w.kind === "chest") row.onclick = () => { showTab("world"); selectChest(w.id, true); };
      else row.onclick = () => showTab("inventory");
      list.appendChild(row);
    }
    box.appendChild(list);
  }
  const add = el("button", "btn gold", "Add to backpack");
  add.disabled = !S.view || !S.view.editable;
  add.onclick = () => {
    const slot = freeBackpackSlot(S.edits.filter((e) => e.container === "Inventory").map((e) => e.slot));
    if (slot == null) { toast("Your backpack is full.", true); return; }
    const e = { op: "add", container: "Inventory", slot, itemId: it.id };
    if (it.maxDurability) e.durability = it.maxDurability; else e.count = it.maxStack || 1;
    addEdits([e]);
    toast(`${it.name} will be added to your backpack. Review & save.`);
  };
  const a = el("div", "d-actions"); a.appendChild(add);
  if (typeof C !== "undefined" && C.status && C.status.modAlive && C.status.player && it.path) {
    const give = el("button", "btn", "Give in game now");
    give.title = "Live cheats: puts it straight into your inventory while you play";
    give.onclick = () => {
      const n = Number(prompt(`How many ${it.name}?`, String(it.maxDurability ? 1 : (it.maxStack || 99))));
      if (n > 0) call("cheat_send", "dw.additem", [itemObjectPath(it), String(Math.round(n)), "1"]).then(() => toast(`Sent: ${n} × ${it.name}`));
    };
    a.appendChild(give);
  }
  box.appendChild(a);
}

/* ---------------------------------------------------------------- share */
async function renderShare() {
  try {
    const r = await call("world_list");
    const sel = $("shareWorld");
    sel.innerHTML = r.worlds.map((w) => `<option value="${esc(w.file)}">${esc(w.name)} · ${esc(w.modified)}</option>`).join("");
    $("packBtn").disabled = !r.worlds.length;
  } catch (e) { toast(e.message, true); }
}

/* --------------------------------------------------------- backups timeline */
loadBackups = async function () {
  try {
    const r = await call("backups");
    const box = $("backupList"); box.innerHTML = "";
    if (!r.groups.length) box.appendChild(el("div", "hint", "Open a character or world first."));
    for (const g of r.groups) {
      const head = el("div", "row-gap");
      head.appendChild(el("h3", "backup-group", esc(g.label)));
      const cmp = el("button", "btn small", "Compare 2 ticked");
      cmp.onclick = () => {
        const ids = [...X.cmp].filter((k) => k.startsWith(g.key + "|")).map((k) => k.split("|")[1]).sort();
        if (ids.length !== 2) { toast("Tick exactly two backups of this save.", true); return; }
        showCompare(g, ids[0], ids[1]);
      };
      head.appendChild(cmp);
      box.appendChild(head);
      const out = el("div", "compare-out hidden"); out.id = "cmp-" + g.key.replace(/\W/g, "_");
      box.appendChild(out);
      if (!g.backups.length) box.appendChild(el("div", "hint", "No backups yet."));
      for (const b of g.backups.slice(0, 80)) {
        const row = el("div", "backup");
        const cb = el("input"); cb.type = "checkbox"; cb.checked = X.cmp.has(g.key + "|" + b.id);
        cb.onchange = () => { cb.checked ? X.cmp.add(g.key + "|" + b.id) : X.cmp.delete(g.key + "|" + b.id); };
        const label = { game: "game save", auto: "before edit", manual: "manual" }[b.reason] || b.reason;
        const left = el("div", null, `<span class="when">${esc(b.when)}</span><span class="tagr ${esc(b.reason)}">${esc(label)}</span>`);
        left.prepend(cb);
        const acts = el("div", "row-gap");
        const since = el("button", "btn small ghost", "Changes since");
        since.title = "What changed between this backup and the save now";
        since.onclick = () => showCompare(g, b.id, "current");
        const res = el("button", "btn small", "Restore");
        res.onclick = () => confirmRestore(g, b);
        acts.append(since, res);
        row.append(left, acts);
        box.appendChild(row);
      }
    }
    const st = await call("watcher_status");
    $("watchToggle").checked = st.enabled;
  } catch (e) { /* nothing open yet */ }
};

async function showCompare(g, a, b) {
  const out = $("cmp-" + g.key.replace(/\W/g, "_"));
  out.classList.remove("hidden");
  out.textContent = "Comparing…";
  try {
    const r = await call("compare", g.key, a, b);
    out.textContent = `${a.replace("_", " ")} → ${b === "current" ? "now" : b.replace("_", " ")}\n\n` + r.lines.join("\n");
  } catch (e) { out.textContent = e.message; }
}

/* --------------------------------------------------------------- wiring */
const _showTab = showTab;
showTab = function (name) {
  _showTab(name);
  if (name === "items") {
    const cats = [...new Set(S.items.map((i) => i.category).filter(Boolean))].sort();
    const sel = $("itemCat");
    if (sel.options.length <= 1) sel.insertAdjacentHTML("beforeend", cats.map((c) => `<option>${esc(c)}</option>`).join(""));
    renderEncyclopedia();
    if (!W.view && S.status && S.status.libraryReady) worldInit().then(renderEncyclopedia);
  }
  if (name === "bundles") renderPresets();
  if (name === "share") renderShare();
  if (name === "unlocks" && typeof renderUnlocks === "function") renderUnlocks();
  if (name === "cheats" && typeof renderCheats === "function") renderCheats();
  if (name === "world" && X.picked.size) renderSelection();
};

// Keep the Live cheats connection known everywhere (World map teleport, Items "Give in game")
async function refreshCheatLink() {
  if (typeof C === "undefined" || !S.api) return;
  try { C.status = await call("cheat_status"); } catch (e) { /* not installed */ }
  const on = !!(C.status && C.status.modAlive && C.status.player);
  $("liveDot").classList.toggle("on", on);
  $("livePill").classList.toggle("hidden", !on);
}
setInterval(refreshCheatLink, 4000);

window.addEventListener("DOMContentLoaded", () => {
  bindExtrasMap();
  $("itemSearch").addEventListener("input", renderEncyclopedia);
  $("itemCat").addEventListener("change", renderEncyclopedia);
  $("itemOwned").addEventListener("change", renderEncyclopedia);
  $("watchToggle").addEventListener("change", async (e) => {
    try { await call("set_watcher", e.target.checked); toast(e.target.checked ? "Every game save will be backed up while the editor is open." : "Game-save backups are off."); } catch (err) { toast(err.message, true); }
  });
  $("packBtn").addEventListener("click", async () => {
    try { const r = await call("pack_world", $("shareWorld").value); $("packResult").textContent = "Saved: " + r.path; toast("World packed on your Desktop."); } catch (e) { toast(e.message, true); }
  });
  $("installBtn").addEventListener("click", async () => {
    try {
      const r = await call("install_world");
      if (r.cancelled) return;
      $("installResult").textContent = "Installed: " + r.installed.join(", ");
      toast("World installed. Open it in the World tab or in the game.");
      W.loaded = false; W.base = null;
    } catch (e) { toast(e.message, true); }
  });
});
