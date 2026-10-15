"use strict";
/* World tab: map with layers, places list, item finder, chest editor, overview, world settings.
   Uses helpers from app.js (S, $, el, esc, call, toast, fmt, iconUrl, slotEl, numberField, openPicker). */

const KIND_COLORS = {
  storage: "#d9b45f", station: "#e08a3c", loot: "#b98cff", grave: "#c9c9c9", other: "#9a9a9a",
  player: "#6ec3ff", pin: "#e06b5a", base: "#f2d78f",
  building: "#8fb3c9", dropped: "#7fd37a", lore: "#f0e08a", teleport: "#5ad1d1",
  resource: "#7d7d7d", trap: "#e05a5a", door: "#a07850", npc: "#d68fd6",
};
// Containers are clickable markers; the rest are drawn on a canvas.
const CONTAINER_KINDS = ["storage", "station", "loot", "grave"];
const DEFAULT_ON = ["storage", "station", "loot", "grave", "player", "pin", "base", "dropped", "building", "teleport"];

const W = {
  worlds: [], base: null, view: null, layers: null,
  edits: [], warnings: [], errors: [], history: [],
  selChest: null, selSlot: null, selObject: null,
  zoom: 1, x: 0, y: 0,
  visible: new Set(loadPref("dw.layers", DEFAULT_ON)),
  placeKinds: new Set(loadPref("dw.placeKinds", ["storage", "station"])),
  seg: "places", loaded: false, findOpen: null,
};

function loadPref(key, def) {
  try { const v = JSON.parse(localStorage.getItem(key)); return Array.isArray(v) ? v : def; } catch (e) { return def; }
}
function savePref(key, set) { try { localStorage.setItem(key, JSON.stringify([...set])); } catch (e) { /* storage off */ } }

async function worldInit() {
  if (S.status && !S.status.libraryReady) {
    mapMessage("Build the game library first (Game library, left menu). The world needs item names and the map.");
    return;
  }
  if (W.loaded) return;
  W.loaded = true;
  bindMap();
  await loadWorldList();
}

async function loadWorldList() {
  try {
    const r = await call("world_list");
    W.worlds = r.worlds;
    const sel = $("worldSelect");
    sel.innerHTML = "";
    for (const w of W.worlds) {
      const o = el("option", null, esc(w.name) + "  ·  " + esc(w.modified));
      o.value = w.file;
      sel.appendChild(o);
    }
    if (W.worlds.length) await openWorld(W.base ? W.base.file : W.worlds[0].file);
    else { mapMessage("No world saves in this save folder. Pick another folder in Saves (top)."); renderWorldOverview(); }
  } catch (e) { mapMessage(e.message); }
}

async function openWorld(file) {
  if (W.edits.length && !confirm("Discard your unsaved world changes?")) { $("worldSelect").value = W.base.file; return; }
  mapMessage("Reading world…");
  try {
    const r = await call("open_world", file);
    W.base = W.view = r.view;
    W.layers = r.view.layers;
    W.edits = []; W.warnings = []; W.errors = []; W.history = [];
    W.selChest = null; W.selSlot = null; W.selObject = null;
    $("worldSelect").value = file;
    mapMessage("");
    renderWorld(true);
    updateChangesBar();
  } catch (e) { mapMessage(e.message); }
}

function mapMessage(msg) {
  const m = $("mapEmpty");
  m.textContent = msg || "";
  m.classList.toggle("hidden", !msg);
}

/* ------------------------------------------------------------ edits */
function wEditKey(e) { return e.op === "setting" ? "setting:" + e.key : e.op + ":" + e.container + ":" + e.slot; }

function addWorldEdits(list) {
  W.history.push(JSON.stringify(W.edits));
  for (const e of list) {
    if (e.op === "remove") {
      const wasAdded = W.edits.some((x) => x.op === "add" && x.container === e.container && x.slot === e.slot);
      W.edits = W.edits.filter((x) => !(x.container === e.container && x.slot === e.slot));
      if (!wasAdded) W.edits.push(e);
      continue;
    }
    if (e.op === "add" || e.op === "replace") {
      W.edits = W.edits.filter((x) => !(x.container === e.container && x.slot === e.slot && x.op !== "add"));
      const added = W.edits.find((x) => x.op === "add" && x.container === e.container && x.slot === e.slot);
      if (added) { Object.assign(added, { itemId: e.itemId, count: e.count, durability: e.durability }); continue; }
    }
    const k = wEditKey(e);
    const i = W.edits.findIndex((x) => wEditKey(x) === k);
    if (i >= 0) W.edits[i] = e; else W.edits.push(e);
  }
  refreshWorldPreview();
}

function undoWorld() {
  if (!W.history.length) return false;
  W.edits = JSON.parse(W.history.pop());
  refreshWorldPreview();
  toast("Undid the last world change.");
  return true;
}

let wSeq = 0;
async function refreshWorldPreview() {
  const seq = ++wSeq;
  if (!W.edits.length) { W.view = W.base; W.warnings = []; W.errors = []; renderWorld(); updateChangesBar(); return; }
  try {
    const r = await call("world_preview", W.edits);
    if (seq !== wSeq) return;
    W.view = r.view; W.warnings = r.warnings; W.errors = r.errors;
  } catch (e) { toast(e.message, true); }
  renderWorld();
  updateChangesBar();
}

function describeWorld(e) {
  const chest = W.base && W.base.containers[e.container];
  const cname = chest ? chest.name : "Chest";
  const itemAt = () => {
    const it = chest && chest.items.find((x) => x.slot === e.slot);
    return it ? it.name : "slot " + e.slot;
  };
  switch (e.op) {
    case "count": return `${cname}: ${itemAt()} amount → ${fmt(e.value)}`;
    case "durability": return `${cname}: ${itemAt()} durability → ${fmt(e.value)}`;
    case "add": return `${cname}: add ${(S.byId[e.itemId] || {}).name || "item"}${e.count ? " ×" + fmt(e.count) : ""}`;
    case "replace": return `${cname}: ${itemAt()} → ${(S.byId[e.itemId] || {}).name || "item"}`;
    case "remove": return `${cname}: remove ${itemAt()}`;
    case "setting": {
      const f = W.base.settings.find((x) => x.key === e.key);
      return `World ${f ? f.label : e.key} → ${e.value === true ? "On" : e.value === false ? "Off" : e.value}`;
    }
  }
  return e.op;
}

/* ----------------------------------------------------------- helpers */
function refPoint() {
  const m = (W.view.markers || []);
  return m.find((x) => x.kind === "player") || m.find((x) => x.kind === "base") || null;
}
function distText(c) {
  const r = refPoint();
  if (!r || c.x == null) return "";
  const d = Math.hypot(c.x - r.x, c.y - r.y) / 100;  // Unreal units are cm
  return d < 1000 ? Math.round(d) + " m" : (d / 1000).toFixed(1) + " km";
}
function markerOf(id) { return (W.view.markers || []).find((m) => m.id === id); }
function editedChests() { return new Set(W.edits.filter((e) => e.container).map((e) => e.container)); }
function label(kind) { return (W.view && W.view.kindLabels && W.view.kindLabels[kind]) || kind; }

/* ----------------------------------------------------------- render */
function renderWorld(fit = false) {
  const v = W.view;
  if (!v) return;
  $("worldMeta").textContent = v.editable ? "" : "Read only: " + (v.readOnlyReason || "format not fully understood");
  const img = $("mapImg");
  if (v.map && v.map.image) {
    if (img.dataset.src !== v.map.image) {
      img.dataset.src = v.map.image;
      img.onload = () => { if (fit) flyTo(refPoint(), 0.35); };
      img.src = "/lib/" + v.map.image;
      img.width = v.map.width; img.height = v.map.height;
    } else if (fit) flyTo(refPoint(), 0.35);
  } else {
    img.removeAttribute("src");
    mapMessage("The map image isn't in the library yet. Rebuild the game library to add it.");
  }
  renderMarkers();
  renderPlaces();
  renderFind();
  renderLayerList();
  renderSettings();
  if (document.querySelector("#tab-world.active")) renderWorldDetail();
}

function renderMarkers() {
  const box = $("mapMarkers");
  box.innerHTML = "";
  const q = $("placeSearch").value.trim().toLowerCase();
  const edited = editedChests();
  for (const m of W.view.markers || []) {
    if (!W.visible.has(m.kind) || m.px == null) continue;
    const d = el("div", "marker " + m.kind);
    d.style.left = m.px + "px"; d.style.top = m.py + "px";
    d.style.setProperty("--k", KIND_COLORS[m.kind] || "#999");
    if (m.id && W.selChest === m.id) d.classList.add("sel");
    if (edited.has(m.id)) d.classList.add("edited");
    if (q && m.id && !placeMatches(W.view.containers[m.id], q)) d.classList.add("dim");
    d.addEventListener("mouseenter", () => showTip(m));
    d.addEventListener("mouseleave", hideTip);
    if (m.id) d.addEventListener("click", (ev) => { ev.stopPropagation(); selectChest(m.id, false); });
    else d.addEventListener("click", (ev) => { ev.stopPropagation(); selectObject({ name: m.name, kind: m.kind, x: m.x, y: m.y }); });
    box.appendChild(d);
  }
  applyMapTransform();
}

function showTip(m) {
  const tip = $("mapTip");
  let html = `<b>${esc(m.name)}</b>`;
  if (m.id) {
    const c = W.view.containers[m.id];
    const items = c.items.slice(0, 6).map((it) => `${esc(it.name)}${it.count > 1 ? " ×" + fmt(it.count) : ""}`);
    html += items.length ? `<div class="tl">${items.join("<br>")}${c.items.length > 6 ? `<br>+${c.items.length - 6} more` : ""}</div>` : '<div class="tl">Empty</div>';
    const d = distText(c);
    if (d) html += `<div class="tl">${d} away</div>`;
  } else if (m.kind) {
    html += `<div class="tl">${esc(label(m.kind))}</div>`;
  }
  tip.innerHTML = html;
  tip.classList.remove("hidden");
  placeTip(m.px * W.zoom + W.x, m.py * W.zoom + W.y);
}
function placeTip(sx, sy) {
  const tip = $("mapTip"), wrap = $("mapWrap");
  const x = Math.min(sx + 14, wrap.clientWidth - tip.offsetWidth - 6);
  const y = Math.min(sy + 14, wrap.clientHeight - tip.offsetHeight - 6);
  tip.style.left = Math.max(6, x) + "px"; tip.style.top = Math.max(6, y) + "px";
}
function hideTip() { $("mapTip").classList.add("hidden"); }

/* canvas: view-only objects and the cell grid, drawn in screen space */
function drawCanvas() {
  const cv = $("mapCanvas"), wrap = $("mapWrap");
  const w = wrap.clientWidth, h = wrap.clientHeight, dpr = window.devicePixelRatio || 1;
  if (cv.width !== w * dpr || cv.height !== h * dpr) { cv.width = w * dpr; cv.height = h * dpr; cv.style.width = w + "px"; cv.style.height = h + "px"; }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if (!W.view) return;
  if ($("gridToggle").checked && W.view.map && W.view.map.inverse) drawGrid(g, w, h);
  if (!W.layers) return;
  const r = W.zoom < 0.4 ? 1.6 : W.zoom < 1 ? 2.4 : 3.5;
  for (const [kind, px, py] of W.layers.rows) {
    if (!W.visible.has(kind)) continue;
    const sx = px * W.zoom + W.x, sy = py * W.zoom + W.y;
    if (sx < -5 || sy < -5 || sx > w + 5 || sy > h + 5) continue;
    g.fillStyle = KIND_COLORS[kind] || "#999";
    g.beginPath(); g.arc(sx, sy, r, 0, 6.283); g.fill();
  }
}

function drawGrid(g, w, h) {
  const inv = W.view.map.inverse;      // pixel -> world: x = a*px + c, y = e*py + f
  const cell = 12800;
  const toPx = (wx) => (wx - inv.c) / inv.a, toPy = (wy) => (wy - inv.f) / inv.e;
  const x0 = Math.floor((inv.a * ((0 - W.x) / W.zoom) + inv.c) / cell), x1 = Math.ceil((inv.a * ((w - W.x) / W.zoom) + inv.c) / cell);
  const y0 = Math.floor((inv.e * ((0 - W.y) / W.zoom) + inv.f) / cell), y1 = Math.ceil((inv.e * ((h - W.y) / W.zoom) + inv.f) / cell);
  if ((x1 - x0) * (y1 - y0) > 4000) return;  // too dense to be useful
  g.strokeStyle = "rgba(217,180,95,.28)"; g.lineWidth = 1;
  g.fillStyle = "rgba(217,180,95,.55)"; g.font = "10px sans-serif";
  for (let i = x0; i <= x1; i++) { const sx = toPx(i * cell) * W.zoom + W.x; g.beginPath(); g.moveTo(sx, 0); g.lineTo(sx, h); g.stroke(); }
  for (let j = y0; j <= y1; j++) { const sy = toPy(j * cell) * W.zoom + W.y; g.beginPath(); g.moveTo(0, sy); g.lineTo(w, sy); g.stroke(); }
  if (W.zoom > 0.6) for (let i = x0; i < x1; i++) for (let j = y0; j < y1; j++)
    g.fillText(`X${i} Y${j}`, toPx(i * cell) * W.zoom + W.x + 3, toPy(j * cell) * W.zoom + W.y + 11);
}

function nearestObject(sx, sy) {
  if (!W.layers) return null;
  let best = null, bd = 64;   // within 8 px
  for (const row of W.layers.rows) {
    if (!W.visible.has(row[0])) continue;
    const dx = row[1] * W.zoom + W.x - sx, dy = row[2] * W.zoom + W.y - sy, d = dx * dx + dy * dy;
    if (d < bd) { bd = d; best = row; }
  }
  return best;
}

/* ------------------------------------------------------- side panel */
function placeMatches(c, q) {
  return c.name.toLowerCase().includes(q) || c.items.some((it) => it.name.toLowerCase().includes(q));
}

function renderPlaces() {
  const chips = $("placeKinds"); chips.innerHTML = "";
  const counts = {};
  for (const c of Object.values(W.view.containers)) counts[c.kind] = (counts[c.kind] || 0) + 1;
  for (const k of CONTAINER_KINDS.filter((k) => counts[k])) {
    const b = el("button", "chip" + (W.placeKinds.has(k) ? " on" : ""), `<span class="dot" style="background:${KIND_COLORS[k]}"></span>${esc(label(k))} ${counts[k]}`);
    b.onclick = () => { W.placeKinds.has(k) ? W.placeKinds.delete(k) : W.placeKinds.add(k); savePref("dw.placeKinds", W.placeKinds); renderPlaces(); };
    chips.appendChild(b);
  }
  const list = $("placeList"); list.innerHTML = "";
  const q = $("placeSearch").value.trim().toLowerCase();
  const r = refPoint();
  const edited = editedChests();
  const rows = Object.values(W.view.containers)
    .filter((c) => W.placeKinds.has(c.kind) && (!q || placeMatches(c, q)))
    .sort((a, b) => (r ? Math.hypot(a.x - r.x, a.y - r.y) - Math.hypot(b.x - r.x, b.y - r.y) : a.name.localeCompare(b.name)));
  for (const c of rows.slice(0, 300)) {
    const row = el("div", "place" + (W.selChest === c.id ? " sel" : "") + (edited.has(c.id) ? " edited" : ""));
    row.innerHTML = `<span class="dot" style="background:${KIND_COLORS[c.kind]}"></span>
      <div><div class="pname">${esc(c.name)}</div><div class="psub">${c.items.length ? c.items.length + " stack(s)" : "empty"}</div></div>
      <div class="pdist">${distText(c)}</div>`;
    row.onclick = () => selectChest(c.id, true);
    list.appendChild(row);
  }
  if (!rows.length) list.appendChild(el("div", "hint", q ? "Nothing matches." : "Pick a kind above."));
  else if (rows.length > 300) list.appendChild(el("div", "hint", `Showing the nearest 300 of ${rows.length}.`));
}

function renderFind() {
  const box = $("findList"); box.innerHTML = "";
  const q = $("findItem").value.trim().toLowerCase();
  if (q.length < 2) { box.appendChild(el("div", "hint", "Type at least 2 letters.")); return; }
  const agg = new Map();
  for (const c of Object.values(W.view.containers)) {
    for (const it of c.items) {
      if (!it.name.toLowerCase().includes(q)) continue;
      const a = agg.get(it.itemId) || { it, total: 0, chests: new Map() };
      const n = it.count != null ? it.count : 1;
      a.total += n;
      a.chests.set(c.id, (a.chests.get(c.id) || 0) + n);
      agg.set(it.itemId, a);
    }
  }
  const rows = [...agg.values()].sort((a, b) => b.total - a.total);
  if (!rows.length) { box.appendChild(el("div", "hint", "Not in any chest.")); return; }
  for (const a of rows.slice(0, 60)) {
    const row = el("div", "find-item" + (W.findOpen === a.it.itemId ? " open" : ""));
    row.innerHTML = `${a.it.icon ? `<img src="${iconUrl(a.it.icon)}" alt="">` : "<div></div>"}<div>${esc(a.it.name)}<div class="psub">${a.chests.size} chest(s)</div></div><div class="pdist">×${fmt(a.total)}</div>`;
    row.onclick = () => { W.findOpen = W.findOpen === a.it.itemId ? null : a.it.itemId; renderFind(); };
    box.appendChild(row);
    if (W.findOpen === a.it.itemId) {
      const sub = el("div", "find-sub");
      for (const [cid, n] of [...a.chests.entries()].sort((x, y) => y[1] - x[1])) {
        const c = W.view.containers[cid];
        const p = el("div", "place");
        p.innerHTML = `<span class="dot" style="background:${KIND_COLORS[c.kind]}"></span><div class="pname">${esc(c.name)} ×${fmt(n)}</div><div class="pdist">${distText(c)}</div>`;
        p.onclick = () => selectChest(cid, true);
        sub.appendChild(p);
      }
      box.appendChild(sub);
    }
  }
}

function renderLayerList() {
  const box = $("layerList"); box.innerHTML = "";
  const counts = Object.assign({}, (W.layers && W.layers.counts) || {});
  for (const c of Object.values(W.view.containers)) counts[c.kind] = (counts[c.kind] || 0) + 1;
  for (const m of W.view.markers || []) if (!m.id) counts[m.kind] = (counts[m.kind] || 0) + 1;
  const order = ["player", "base", "pin", "storage", "station", "loot", "grave", "dropped", "building", "teleport", "lore", "npc", "door", "trap", "resource"];
  for (const k of order.filter((k) => counts[k])) {
    const row = el("label", "layer-row");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = W.visible.has(k);
    cb.onchange = () => { cb.checked ? W.visible.add(k) : W.visible.delete(k); savePref("dw.layers", W.visible); renderMarkers(); };
    row.append(cb, el("span", "dot"), el("span", null, esc(label(k))), el("span", "hint", fmt(counts[k])));
    row.children[1].style.background = KIND_COLORS[k];
    box.appendChild(row);
  }
}

function renderSettings() {
  const box = $("worldSettings");
  box.innerHTML = "";
  for (const f of W.view.settings || []) {
    const wrap = el("div", "field");
    wrap.appendChild(el("label", null, `<span>${esc(f.label)}</span><span>${f.editable ? "" : "view only"}</span>`));
    let input;
    if (f.type === "bool") {
      input = el("select");
      input.innerHTML = '<option value="true">On</option><option value="false">Off</option>';
      input.value = String(!!f.value);
    } else if (f.options) {
      input = el("select");
      input.innerHTML = f.options.map((o) => `<option>${esc(o)}</option>`).join("");
      input.value = f.value;
    } else {
      input = el("input"); input.type = "text"; input.value = f.value ?? "";
    }
    input.disabled = !f.editable || !W.view.editable;
    if (f.key === "friendlyFire" && !f.editable) input.title = "This world uses custom difficulty, so friendly fire can't be changed here.";
    input.addEventListener("change", () => {
      let val = input.value;
      if (f.type === "bool") val = val === "true";
      if (f.key === "name" && !val.trim()) { toast("The world name can't be empty.", true); input.value = f.value; return; }
      addWorldEdits([{ op: "setting", key: f.key, value: val }]);
    });
    wrap.appendChild(input);
    box.appendChild(wrap);
  }
}

/* ------------------------------------------------------ detail panel */
function selectChest(id, fly) {
  W.selChest = id; W.selSlot = null; W.selObject = null;
  S.selected = null;
  if (fly) flyTo(markerOf(id), Math.max(W.zoom, 1.6));
  renderMarkers();
  renderPlaces();
  renderWorldDetail();
}

function selectObject(o) {
  W.selChest = null; W.selSlot = null; W.selObject = o;
  renderMarkers(); renderPlaces(); renderWorldDetail();
}

function renderWorldDetail() {
  if (W.selChest && W.view.containers[W.selChest]) return renderChestDetail();
  if (W.selObject) return renderObjectDetail();
  renderWorldOverview();
}

function backLink(box) {
  const b = el("button", "btn ghost small", "← World overview");
  b.onclick = () => { W.selChest = null; W.selObject = null; renderMarkers(); renderPlaces(); renderWorldDetail(); };
  box.appendChild(b);
}

function renderObjectDetail() {
  const o = W.selObject, box = $("detail");
  box.innerHTML = "";
  backLink(box);
  box.appendChild(el("div", "d-banner", `<h3>${esc(o.name)}</h3><div class="sub">${esc(label(o.kind))}</div>`));
  box.appendChild(el("div", "kv", `<span>Position</span><span>X ${Math.round(o.x)}  Y ${Math.round(o.y)}</span>` +
    (refPoint() ? `<span>Distance</span><span>${distText(o)}</span>` : "")));
  box.appendChild(el("p", "hint", "This object is shown for reference and can't be edited."));
}

function renderWorldOverview() {
  const box = $("detail");
  if (!W.view) { box.innerHTML = '<div class="detail-empty">Open a world to see it here.</div>'; return; }
  const v = W.view;
  const counts = {};
  for (const c of Object.values(v.containers)) counts[c.kind] = (counts[c.kind] || 0) + 1;
  box.innerHTML = `<div class="d-banner"><h3>${esc(v.name || "World")}</h3><div class="sub">Click a marker or a place to edit it</div></div>`;
  box.appendChild(el("div", "kv",
    CONTAINER_KINDS.filter((k) => counts[k]).map((k) => `<span>${esc(label(k))}</span><span>${counts[k]}</span>`).join("") +
    (v.stats && v.stats.objects ? `<span>Other objects on map</span><span>${fmt(v.stats.objects)}</span>` : "")));

  // what's in your own storage
  const mine = Object.values(v.containers).filter((c) => c.kind === "storage" || c.kind === "station");
  const agg = new Map();
  for (const c of mine) for (const it of c.items) {
    const a = agg.get(it.itemId) || { it, n: 0 };
    a.n += it.count != null ? it.count : 1;
    agg.set(it.itemId, a);
  }
  box.appendChild(el("div", "sec-title", "Your storage"));
  const top = [...agg.values()].sort((a, b) => b.n - a.n).slice(0, 12);
  const list = el("div", "overview-list");
  for (const a of top) list.appendChild(el("div", "ov-row",
    `${a.it.icon ? `<img src="${iconUrl(a.it.icon)}" alt="">` : "<div></div>"}<span>${esc(a.it.name)}</span><span class="q">×${fmt(a.n)}</span>`));
  if (!top.length) list.appendChild(el("div", "hint", "No player storage found in this world."));
  box.appendChild(list);
  if (agg.size > 12) box.appendChild(el("div", "hint", `${agg.size - 12} more item types. Use Find item to look one up.`));

  box.appendChild(el("div", "sec-title", "Quick tools"));
  const a = el("div", "d-actions");
  const rep = el("button", "btn", "Repair everything in your storage");
  rep.disabled = !v.editable;
  rep.onclick = () => {
    const edits = [];
    for (const c of mine) if (c.editable !== false) for (const it of c.items)
      if (it.durability != null && it.maxDurability && it.durability < it.maxDurability)
        edits.push({ op: "durability", container: c.id, slot: it.slot, value: it.maxDurability });
    if (!edits.length) { toast("Everything in your storage is already at full durability."); return; }
    addWorldEdits(edits);
    toast(`${edits.length} item(s) will be repaired. Review & save when ready.`);
  };
  a.appendChild(rep);
  box.appendChild(a);
}

function renderChestDetail() {
  const box = $("detail");
  const c = W.view.containers[W.selChest];
  const editable = W.view.editable && c.editable !== false;
  box.innerHTML = "";
  backLink(box);
  box.appendChild(el("div", "d-banner", `<h3>${esc(c.name)}</h3><div class="sub">${esc(label(c.kind))} · ${c.items.length} stack(s)${distText(c) ? " · " + distText(c) + " away" : ""}</div>`));
  if (!editable) box.appendChild(el("div", "err-item", "Read only: " + esc(c.readOnlyReason || "this container can't be edited")));
  if (c.copies) box.appendChild(el("div", "hint", `The game keeps ${c.copies + 1} copies of this inventory; every change updates all of them.`));

  // slot grid, like the backpack
  const bySlot = Object.fromEntries(c.items.map((it) => [it.slot, it]));
  const last = Math.max(c.maxSlotIndex ?? -1, ...c.items.map((it) => it.slot), 7);
  const editedSlots = new Set(W.edits.filter((e) => e.container === c.id).map((e) => e.slot));
  const grid = el("div", "chest-grid");
  const slots = c.canAdd ? last + 2 : last + 1;
  for (let s = 0; s < slots; s++) {
    const it = bySlot[s];
    grid.appendChild(slotEl(it, {
      edited: editedSlots.has(s), selected: W.selSlot === s,
      onClick: () => {
        if (it) { W.selSlot = s; renderChestDetail(); }
        else if (editable && c.canAdd) openPicker("add", c.id, s, "world");
      },
    }));
  }
  box.appendChild(grid);

  const it = bySlot[W.selSlot];
  if (it) {
    box.appendChild(el("div", "sec-title", esc(it.name)));
    if (it.durability == null && it.maxStack > 1) {
      box.appendChild(numberField("Amount", it.count, 1, it.maxStack, (val) =>
        addWorldEdits([{ op: "count", container: c.id, slot: it.slot, value: val }]), editable, `max ${fmt(it.maxStack)}`));
    }
    if (it.durability != null) {
      const mx = it.maxDurability || Math.max(it.durability, 1);
      box.appendChild(numberField("Durability", it.durability, 1, mx, (val) =>
        addWorldEdits([{ op: "durability", container: c.id, slot: it.slot, value: val }]), editable,
        it.maxDurability ? `max ${fmt(it.maxDurability)}` : "max unknown"));
    }
    const a = el("div", "d-actions");
    if (it.durability != null && it.maxDurability && it.durability < it.maxDurability) {
      const r = el("button", "btn", "Repair"); r.disabled = !editable;
      r.onclick = () => addWorldEdits([{ op: "durability", container: c.id, slot: it.slot, value: it.maxDurability }]);
      a.appendChild(r);
    }
    const sw = el("button", "btn", "Swap…"); sw.disabled = !editable || !c.canAdd;
    sw.onclick = () => openPicker("replace", c.id, it.slot, "world");
    const rm = el("button", "btn danger", "Remove"); rm.disabled = !editable;
    rm.onclick = () => { W.selSlot = null; addWorldEdits([{ op: "remove", container: c.id, slot: it.slot }]); };
    a.append(sw, rm);
    box.appendChild(a);
  } else {
    box.appendChild(el("div", "hint", c.canAdd ? "Click an item to edit it, or an empty slot to add one." : "Click an item to edit it."));
  }

  // chest-wide tools
  const tools = el("div", "d-actions");
  const repairAll = el("button", "btn", "Repair all");
  const toFix = c.items.filter((x) => x.durability != null && x.maxDurability && x.durability < x.maxDurability);
  repairAll.disabled = !editable || !toFix.length;
  repairAll.onclick = () => addWorldEdits(toFix.map((x) => ({ op: "durability", container: c.id, slot: x.slot, value: x.maxDurability })));
  const fill = el("button", "btn", "Fill stacks");
  const toFill = c.items.filter((x) => x.durability == null && x.maxStack > 1 && x.count < x.maxStack);
  fill.title = "Set every stack in this chest to its maximum";
  fill.disabled = !editable || !toFill.length;
  fill.onclick = () => addWorldEdits(toFill.map((x) => ({ op: "count", container: c.id, slot: x.slot, value: x.maxStack })));
  const fly = el("button", "btn ghost", "Show on map");
  fly.onclick = () => flyTo(markerOf(c.id), Math.max(W.zoom, 2));
  tools.append(repairAll, fill, fly);
  box.appendChild(el("div", "divider"));
  box.appendChild(tools);

  const ws = el("div", "warnings");
  W.errors.forEach((w) => ws.appendChild(el("div", "err-item", esc(w))));
  W.warnings.forEach((w) => ws.appendChild(el("div", "warn-item", esc(w))));
  box.appendChild(ws);
}

/* ------------------------------------------------------- pan & zoom */
function applyMapTransform() {
  $("mapStage").style.transform = `translate(${W.x}px, ${W.y}px) scale(${W.zoom})`;
  const s = 1 / W.zoom;
  document.querySelectorAll("#mapMarkers .marker").forEach((m) => {
    m.style.transform = (m.classList.contains("pin") ? "rotate(45deg) " : "") + `scale(${s})`;
  });
  drawCanvas();
}

function mapSize() {
  const v = W.view || {};
  return [(v.map && v.map.width) || 4096, (v.map && v.map.height) || 4096];
}

function fitMap() {
  const wrap = $("mapWrap");
  const [w, h] = mapSize();
  W.zoom = Math.min(wrap.clientWidth / w, wrap.clientHeight / h) || 1;
  W.x = (wrap.clientWidth - w * W.zoom) / 2;
  W.y = (wrap.clientHeight - h * W.zoom) / 2;
  applyMapTransform();
}

function flyTo(m, zoom) {
  if (!m || m.px == null) { fitMap(); return; }
  const wrap = $("mapWrap");
  W.zoom = zoom || W.zoom;
  W.x = wrap.clientWidth / 2 - m.px * W.zoom;
  W.y = wrap.clientHeight / 2 - m.py * W.zoom;
  applyMapTransform();
}

function zoomAt(factor, cx, cy) {
  const nz = Math.max(0.08, Math.min(10, W.zoom * factor));
  W.x = cx - (cx - W.x) * (nz / W.zoom);
  W.y = cy - (cy - W.y) * (nz / W.zoom);
  W.zoom = nz;
  applyMapTransform();
}

function bindMap() {
  const wrap = $("mapWrap");
  let drag = null, moved = false;
  wrap.addEventListener("wheel", (e) => {
    e.preventDefault();
    const r = wrap.getBoundingClientRect();
    zoomAt(e.deltaY < 0 ? 1.25 : 1 / 1.25, e.clientX - r.left, e.clientY - r.top);
  }, { passive: false });
  wrap.addEventListener("mousedown", (e) => { drag = { x: e.clientX - W.x, y: e.clientY - W.y, sx: e.clientX, sy: e.clientY }; moved = false; });
  window.addEventListener("mouseup", () => { drag = null; wrap.classList.remove("dragging"); });
  wrap.addEventListener("mousemove", (e) => {
    const r = wrap.getBoundingClientRect();
    const sx = e.clientX - r.left, sy = e.clientY - r.top;
    if (drag) {
      if (Math.abs(e.clientX - drag.sx) + Math.abs(e.clientY - drag.sy) > 3) { moved = true; wrap.classList.add("dragging"); }
      W.x = e.clientX - drag.x; W.y = e.clientY - drag.y; applyMapTransform(); hideTip();
      return;
    }
    const inv = W.view && W.view.map && W.view.map.inverse;
    if (inv) {
      const px = (sx - W.x) / W.zoom, py = (sy - W.y) / W.zoom;
      $("mapCoords").textContent = `X ${Math.round(inv.a * px + inv.c)}  Y ${Math.round(inv.e * py + inv.f)}`;
    }
    if (e.target.classList.contains("marker")) return;
    const o = nearestObject(sx, sy);
    if (o) {
      const tip = $("mapTip");
      tip.innerHTML = `<b>${esc(W.layers.labels[o[3]])}</b><div class="tl">${esc(label(o[0]))}</div>`;
      tip.classList.remove("hidden");
      placeTip(sx, sy);
    } else hideTip();
  });
  wrap.addEventListener("mouseleave", hideTip);
  wrap.addEventListener("click", (e) => {
    if (moved || e.target.classList.contains("marker")) return;
    const r = wrap.getBoundingClientRect();
    const o = nearestObject(e.clientX - r.left, e.clientY - r.top);
    if (o) {
      const inv = W.view.map.inverse;
      selectObject({ name: W.layers.labels[o[3]], kind: o[0], x: inv.a * o[1] + inv.c, y: inv.e * o[2] + inv.f, actor: o[4] });
    }
  });
  wrap.addEventListener("dblclick", (e) => { const r = wrap.getBoundingClientRect(); zoomAt(2, e.clientX - r.left, e.clientY - r.top); });
  window.addEventListener("resize", () => { if (W.view) drawCanvas(); });

  const center = () => [wrap.clientWidth / 2, wrap.clientHeight / 2];
  $("zoomIn").onclick = () => zoomAt(1.5, ...center());
  $("zoomOut").onclick = () => zoomAt(1 / 1.5, ...center());
  $("zoomFit").onclick = fitMap;
  $("flyMe").onclick = () => { const m = (W.view.markers || []).find((x) => x.kind === "player"); m ? flyTo(m, 2) : toast("No character position for this world."); };
  $("flyBase").onclick = () => { const m = (W.view.markers || []).find((x) => x.kind === "base"); m ? flyTo(m, 2.5) : toast("No player storage found in this world."); };
  $("worldSelect").addEventListener("change", (e) => openWorld(e.target.value));
  $("gridToggle").addEventListener("change", drawCanvas);
  $("placeSearch").addEventListener("input", () => { renderPlaces(); renderMarkers(); });
  $("findItem").addEventListener("input", () => { W.findOpen = null; renderFind(); });
  document.querySelectorAll("#worldSeg button").forEach((b) => b.addEventListener("click", () => {
    W.seg = b.dataset.seg;
    document.querySelectorAll("#worldSeg button").forEach((x) => x.classList.toggle("on", x === b));
    ["places", "find", "layers"].forEach((s) => $("seg-" + s).classList.toggle("hidden", s !== W.seg));
    if (W.seg === "find") $("findItem").focus();
  }));
}
