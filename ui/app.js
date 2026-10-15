"use strict";

const S = {
  api: null,
  status: null,
  base: null,       // view of the save as it is on disk
  view: null,       // view with pending edits applied
  edits: [],
  warnings: [],
  errors: [],
  items: [],
  byId: {},
  curve: [],
  bundles: [],
  selected: null,   // {container, slot}
  pickerSel: null,
  gameDir: null,
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const iconUrl = (f) => (f ? "/lib/icons/" + encodeURIComponent(f) : null);
const fmt = (n) => (n == null ? "?" : Number(n).toLocaleString("en-US"));

async function call(name, ...args) {
  const res = await S.api[name](...args);
  if (res && res.error) throw new Error(res.error);
  return res;
}

function toast(msg, bad = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), bad ? 6000 : 3500);
}

/* ------------------------------------------------------------ startup */
window.addEventListener("pywebviewready", () => init(window.pywebview.api));
if (location.search.includes("dev")) {
  // Developer mode (app.py --serve): call the Api over HTTP.
  const proxy = new Proxy({}, { get: (_, name) => (...args) =>
    fetch("/api/" + name, { method: "POST", body: JSON.stringify(args) }).then((r) => r.json()) });
  window.addEventListener("DOMContentLoaded", () => init(proxy));
}

async function init(api) {
  if (S.api) return;
  S.api = api;
  bindStatic();
  await pollStatus();
  setInterval(pollStatus, 2000);
  loadRoots();
  if (!S.status.libraryReady) {
    // First run: find the game (quick look, then every drive) and build the library without being asked
    S.firstRun = true;
    showTab("setup");
    await loadDirs();
    if (!S.foundDirs.length) await loadDirs(true);
    S.firstRun = false;
    if (S.foundDirs.length && !S.status.building) build();
    return;
  }
  await loadLibrary();
  await openFirstCharacter();
}

async function loadRoots() {
  try {
    const r = await call("save_roots");
    const sel = $("rootSelect");
    sel.innerHTML = "";
    for (const x of r.roots) {
      const o = el("option", null, esc(`${x.kind} · ${x.characters.length} char · ${x.worlds.length} world`));
      o.value = x.path; o.title = x.path;
      sel.appendChild(o);
    }
    if (r.current) sel.value = r.current;
    sel.title = r.current || "";
  } catch (e) { /* keep default folder */ }
}

async function changeRoot(path) {
  if ((S.edits.length || W.edits.length) && !confirm("Discard your unsaved changes?")) { loadRoots(); return; }
  try {
    await call("set_save_root", path);
    S.edits = []; W.edits = [];
    S.base = S.view = null; W.base = W.view = null; W.loaded = false;
    $("rootSelect").title = path;
    await pollStatus();
    await openFirstCharacter();
    if (document.querySelector("#tab-world.active")) worldInit();
  } catch (e) { toast(e.message, true); }
}

async function loadLibrary() {
  const r = await call("item_list");
  S.items = r.items;
  S.byId = Object.fromEntries(S.items.map((i) => [i.id, i]));
  S.curve = r.xpCurve || [];
  S.bundles = (await call("bundles")).bundles;
  renderBundles();
}

async function openFirstCharacter() {
  const chars = S.status.characters;
  fillCharSelect();
  if (!chars.length) {
    banner("No characters found yet. Start RuneScape: Dragonwilds, create a character and play for a minute so the game saves, "
      + "then click Reload (top right). If your saves are in a different folder, pick it in Saves at the top. Looked in: " + S.status.saveDir, "warn");
    return;
  }
  const pick = S.status.lastCharacter && chars.find((c) => c.file === S.status.lastCharacter)
    ? S.status.lastCharacter : chars[0].file;
  await openCharacter(pick);
}

function fillCharSelect() {
  const sel = $("charSelect");
  sel.innerHTML = "";
  for (const c of S.status.characters) {
    const o = el("option", null, esc(c.name) + "  ·  " + esc(c.modified));
    o.value = c.file;
    sel.appendChild(o);
  }
  if (S.status.current) sel.value = S.status.current;
}

async function openCharacter(file) {
  if (S.edits.length && !confirm("Discard your unsaved changes?")) {
    $("charSelect").value = S.status.current;
    return;
  }
  try {
    const r = await call("open_character", file);
    S.base = S.view = r.view;
    S.edits = []; S.warnings = []; S.errors = [];
    S.selected = null;
    S.status.current = file;
    $("charSelect").value = file;
    renderAll();
    loadBackups();
  } catch (e) { toast(e.message, true); }
}

/* -------------------------------------------------------------- status */
async function pollStatus() {
  try {
    const st = await call("status");
    const prevRunning = S.status && S.status.gameRunning;
    S.status = Object.assign(S.status || {}, st);
    if (st.version && !$("appVersion").textContent) {
      $("appVersion").textContent = "v" + st.version;
      document.title = "Dragonwilds Toolkit v" + st.version;
    }
    const pill = $("gamePill");
    if (st.gameRunning) {
      pill.textContent = "Game running · view only";
      pill.className = "pill bad";
    } else {
      pill.textContent = "Game closed · editing on";
      pill.className = "pill ok";
    }
    if (prevRunning && !st.gameRunning && S.status.current) {
      toast("The game was closed. Reloading the latest save.");
      if (!S.edits.length) await openCharacter(S.status.current);
    }
    if (st.libraryStale) banner("Your game was updated. Open Game library (bottom left) and click Build library so new items show correctly.", "warn");
    updateChangesBar();
    if (document.querySelector("#tab-setup.active")) renderSetupChecks();
  } catch (e) { /* app is closing */ }
}

function banner(msg, kind) {
  const b = $("banner");
  if (!msg) { b.classList.add("hidden"); return; }
  b.textContent = msg;
  b.className = "banner " + kind;
}

/* ---------------------------------------------------------------- edits */
function editKey(e) {
  if (e.op === "xp") return "xp:" + e.skillId;
  if (e.op === "vital") return "vital:" + e.key;
  return e.op + ":" + e.container + ":" + e.slot;
}

function undoChar() {
  if (!S.history || !S.history.length) return false;
  S.edits = JSON.parse(S.history.pop());
  refreshPreview();
  toast("Undid the last change.");
  return true;
}

function addEdits(list) {
  (S.history = S.history || []).push(JSON.stringify(S.edits));
  for (const e of list) {
    const k = editKey(e);
    if (e.op === "remove") {
      const wasAdded = S.edits.some((x) => x.op === "add" && x.container === e.container && x.slot === e.slot);
      S.edits = S.edits.filter((x) => !(x.container === e.container && x.slot === e.slot));
      if (!wasAdded) S.edits.push(e);
      continue;
    }
    if (e.op === "replace" || e.op === "add") {
      S.edits = S.edits.filter((x) => !(x.container === e.container && x.slot === e.slot && x.op !== "add"));
      if (e.op === "replace") {
        const added = S.edits.find((x) => x.op === "add" && x.container === e.container && x.slot === e.slot);
        if (added) { Object.assign(added, { itemId: e.itemId, count: e.count, durability: e.durability }); continue; }
      }
    }
    const i = S.edits.findIndex((x) => editKey(x) === k);
    if (i >= 0) S.edits[i] = e; else S.edits.push(e);
  }
  refreshPreview();
}

let previewSeq = 0;
async function refreshPreview() {
  const seq = ++previewSeq;
  if (!S.edits.length) {
    S.view = S.base; S.warnings = []; S.errors = [];
    renderAll();
    return;
  }
  try {
    const r = await call("preview", S.edits);
    if (seq !== previewSeq) return;
    S.view = r.view; S.warnings = r.warnings; S.errors = r.errors;
  } catch (e) { toast(e.message, true); }
  renderAll();
}

function editedSlots() {
  const set = new Set();
  for (const e of S.edits) if (e.container) set.add(e.container + ":" + e.slot);
  return set;
}

function describe(e) {
  const nameAt = (c, s) => {
    const list = c === "Inventory" ? S.base.inventory : S.base.equipment;
    const v = list.find((x) => x.slot === s);
    return v ? v.name : "slot " + s;
  };
  switch (e.op) {
    case "count": return `${nameAt(e.container, e.slot)}: amount → ${fmt(e.value)}`;
    case "durability": return `${nameAt(e.container, e.slot)}: durability → ${fmt(e.value)}`;
    case "add": return `Add ${(S.byId[e.itemId] || {}).name || "item"}${e.count ? " ×" + fmt(e.count) : ""} to ${e.container === "Inventory" ? "backpack slot " + e.slot : "equipment"}`;
    case "replace": return `${nameAt(e.container, e.slot)} → ${(S.byId[e.itemId] || {}).name || "item"}`;
    case "remove": return `Remove ${nameAt(e.container, e.slot)}`;
    case "xp": {
      const sk = S.base.skills.find((x) => x.id === e.skillId);
      return `${sk ? sk.name : "Skill"} XP → ${fmt(e.value)} (level ${levelFor(e.value)})`;
    }
    case "vital": return `${e.key} → ${e.value}`;
  }
  return e.op;
}

function allChangeTexts() {
  const wl = (typeof W !== "undefined" && W.edits) ? W.edits.map(describeWorld) : [];
  return S.edits.map(describe).concat(wl);
}

function updateChangesBar() {
  const texts = allChangeTexts();
  const n = texts.length;
  $("changeCount").textContent = n ? `${n} unsaved change${n > 1 ? "s" : ""}` : "No changes";
  $("changes").classList.toggle("has", !!n);
  $("changeSummary").textContent = n ? texts.join("  ·  ") : "";
  $("discardBtn").disabled = !n;
  const running = S.status && S.status.gameRunning;
  $("reviewBtn").disabled = !n || running;
  $("reviewBtn").title = running ? "Quit the game first" : "";
}

/* ------------------------------------------------------------- render */
function renderAll() {
  if (!S.view) return;
  const v = S.view;
  banner(v.editable ? (v.versionWarning ? `This save is version ${v.version}; the editor was tested on version 83. Check edits in game.` : "") :
    "This save's format is not fully understood, so editing is turned off for it.", v.editable ? "warn" : "bad");
  renderInventory();
  renderSkills();
  renderCharacter();
  renderDetail();
  updateChangesBar();
}

function slotEl(entry, opts = {}) {
  const d = el("div", "slot");
  d.tabIndex = 0;
  if (!entry || entry.itemId == null) {
    d.classList.add("empty");
    d.appendChild(el("div", "plus", "+"));
  } else {
    const url = iconUrl(entry.icon);
    if (url) { const img = el("img"); img.src = url; img.alt = entry.name; d.appendChild(img); }
    else d.appendChild(el("div", "unknown", "?"));
    d.title = entry.name;
    if (entry.durability != null && entry.maxDurability) {
      const pct = Math.max(0, Math.min(100, (entry.durability / entry.maxDurability) * 100));
      const bar = el("div", "dur"); const i = el("i", pct < 25 ? "low" : pct < 60 ? "mid" : "");
      i.style.width = pct + "%"; bar.appendChild(i); d.appendChild(bar);
    } else if (entry.count != null && (entry.count > 1 || entry.maxStack > 1)) {
      d.appendChild(el("div", "qty", fmt(entry.count)));
    }
  }
  if (opts.num != null) d.appendChild(el("div", "num", opts.num));
  if (opts.edited) d.classList.add("edited");
  if (opts.selected) d.classList.add("selected");
  if (opts.onClick) {
    d.addEventListener("click", opts.onClick);
    d.addEventListener("keydown", (ev) => { if (ev.key === "Enter") opts.onClick(); });
  }
  return d;
}

const EQUIP_LABELS = { 0: "Head", 1: "Body", 2: "Legs", 3: "Cape", 4: "Amulet" };

function renderInventory() {
  const v = S.view;
  const edited = editedSlots();
  const isSel = (c, s) => S.selected && S.selected.container === c && S.selected.slot === s;

  const eq = $("equipRow"); eq.innerHTML = "";
  for (const slot of [0, 1, 2, 3, 4]) {
    const entry = v.equipment.find((x) => x.slot === slot);
    const w = el("div", "slot-wrap");
    w.appendChild(slotEl(entry, {
      edited: edited.has("Loadout:" + slot), selected: isSel("Loadout", slot),
      onClick: () => select("Loadout", slot),
    }));
    w.appendChild(el("div", "slot-label", EQUIP_LABELS[slot]));
    eq.appendChild(w);
  }

  const hb = $("hotbarRow"); hb.innerHTML = "";
  for (const slot of [5, 6, 7, 8, 9]) {
    const ref = v.hotbar.find((x) => x.slot === slot);
    const target = ref ? v.inventory.find((x) => x.slot === ref.ref) : null;
    const w = el("div", "slot-wrap");
    const s = slotEl(target, { onClick: target ? () => select("Inventory", target.slot) : null });
    if (target) s.appendChild(el("div", "link", "↗"));
    w.appendChild(s);
    w.appendChild(el("div", "slot-label", String(slot - 4)));
    hb.appendChild(w);
  }

  const wrap = $("invGrid"); wrap.innerHTML = "";
  const q = $("invSearch").value.trim().toLowerCase();
  const bySlot = Object.fromEntries(v.inventory.map((x) => [x.slot, x]));
  const maxSlot = Math.max(-1, ...v.inventory.map((x) => x.slot));
  const pages = INV_PAGES.slice();
  if (maxSlot > 151) pages.push({ name: "Other slots", from: 152, to: maxSlot, hint: "Slots the editor doesn't recognise" });
  for (const p of pages) {
    const used = v.inventory.filter((x) => x.slot >= p.from && x.slot <= p.to).length;
    let to = p.to;
    // Quest page is big and mostly empty: show only up to the last used row
    if (p.compact) to = Math.min(p.to, p.from + Math.max(11, Math.ceil((maxSlot - p.from + 2) / 12) * 12 - 1));
    if (p.compact && !used) to = p.from + 11;
    const sec = el("div", "inv-page");
    sec.appendChild(el("div", "inv-page-head",
      `<span class="inv-page-name">${esc(p.name)}</span><span class="inv-page-count">${used} / ${p.to - p.from + 1}</span><span class="hint">${esc(p.hint)}</span>`));
    const grid = el("div", "grid");
    for (let s = p.from; s <= to; s++) {
      const entry = bySlot[s];
      const d = slotEl(entry, {
        num: s - p.from + 1, edited: edited.has("Inventory:" + s), selected: isSel("Inventory", s),
        onClick: () => (entry ? select("Inventory", s) : openPicker("add", "Inventory", s)),
      });
      d.title = (entry ? entry.name + " · " : "") + `${p.name} slot ${s - p.from + 1}`;
      if (q && !(entry && entry.name.toLowerCase().includes(q))) d.classList.add("dim");
      grid.appendChild(d);
    }
    sec.appendChild(grid);
    wrap.appendChild(sec);
  }
}

const INV_PAGES = [
  { name: "Backpack", from: 0, to: 31, hint: "Your main bag" },
  { name: "Rune pouch", from: 32, to: 55, hint: "Runes only" },
  { name: "Ammo pouch", from: 56, to: 79, hint: "Arrows and bolts" },
  { name: "Quest items", from: 80, to: 151, hint: "Key and quest items", compact: true },
];

function select(container, slot) {
  S.selected = { container, slot };
  if (typeof W !== "undefined") W.selChest = null;
  renderInventory();
  renderDetail();
}

function currentEntry() {
  if (!S.selected || !S.view) return null;
  const list = S.selected.container === "Inventory" ? S.view.inventory : S.view.equipment;
  return list.find((x) => x.slot === S.selected.slot) || null;
}

function renderDetail() {
  const box = $("detail");
  const entry = currentEntry();
  const editable = S.view && S.view.editable;
  if (!S.selected) { box.innerHTML = '<div class="detail-empty">Select an item to see and edit it.</div>'; return; }
  const { container, slot } = S.selected;
  if (!entry) {
    box.innerHTML = "";
    const where = container === "Inventory" ? `Backpack slot ${slot}` : `${EQUIP_LABELS[slot]} slot`;
    box.appendChild(el("div", "d-banner", `<h3>Empty</h3><div class="sub">${esc(where)}</div>`));
    const b = el("button", "btn gold", "Add item…");
    b.disabled = !editable;
    b.onclick = () => openPicker("add", container, slot);
    const a = el("div", "d-actions"); a.appendChild(b); box.appendChild(a);
    return;
  }
  const info = S.byId[entry.itemId] || {};
  const where = container === "Inventory" ? `Backpack slot ${slot}` : `Equipped · ${EQUIP_LABELS[slot]}`;
  box.innerHTML = `
    <div class="d-banner"><h3>${esc(entry.name)}</h3><div class="sub">${esc(where)}</div></div>
    ${entry.icon ? `<img class="d-icon" src="${iconUrl(entry.icon)}" alt="">` : ""}
    <p class="d-desc">${esc(entry.description || "")}</p>
    <div class="d-tags">
      ${entry.category ? `<span class="tag">${esc(entry.category)}</span>` : ""}
      ${entry.powerLevel ? `<span class="tag gold">Power Level ${entry.powerLevel}</span>` : ""}
      ${entry.skill ? `<span class="tag">${esc(entry.skill)}${skillLevelText(entry.skill)}</span>` : ""}
    </div>`;

  if (entry.count != null && entry.durability == null && entry.maxStack > 1) {
    box.appendChild(numberField("Amount", entry.count, 1, entry.maxStack, (val) =>
      addEdits([{ op: "count", container, slot, value: val }]), editable, `max ${fmt(entry.maxStack)}`));
  }
  if (entry.durability != null) {
    const mx = entry.maxDurability || Math.max(entry.durability, 1);
    box.appendChild(numberField("Durability", entry.durability, 1, mx, (val) =>
      addEdits([{ op: "durability", container, slot, value: val }]), editable,
      entry.maxDurability ? `max ${fmt(entry.maxDurability)}` : "max unknown"));
  }

  const slotWarn = S.warnings.filter((w) => w.includes(entry.name));
  const slotErr = S.errors.filter((w) => w.includes(entry.name));
  if (slotWarn.length || slotErr.length) {
    const ws = el("div", "warnings");
    slotErr.forEach((w) => ws.appendChild(el("div", "err-item", esc(w))));
    slotWarn.forEach((w) => ws.appendChild(el("div", "warn-item", esc(w))));
    box.appendChild(ws);
  }

  const actions = el("div", "d-actions");
  if (entry.durability != null && entry.maxDurability && entry.durability < entry.maxDurability) {
    const r = el("button", "btn", "Repair to full");
    r.disabled = !editable;
    r.onclick = () => addEdits([{ op: "durability", container, slot, value: entry.maxDurability }]);
    actions.appendChild(r);
  }
  const sw = el("button", "btn", "Swap item…");
  sw.disabled = !editable;
  sw.onclick = () => openPicker("replace", container, slot);
  const rm = el("button", "btn danger", "Remove");
  rm.disabled = !editable;
  rm.onclick = () => { addEdits([{ op: "remove", container, slot }]); };
  actions.append(sw, rm);
  box.appendChild(actions);
}

function skillLevelText(skillName) {
  const s = S.view.skills.find((x) => x.name === skillName);
  return s && s.level ? ` · your level ${s.level}` : "";
}

function numberField(label, value, min, max, onCommit, enabled, note) {
  const f = el("div", "field");
  f.appendChild(el("label", null, `<span>${esc(label)}</span><span>${esc(note || "")}</span>`));
  const row = el("div", "inputs");
  const n = el("input"); n.type = "number"; n.min = min; n.max = max; n.value = value; n.disabled = !enabled;
  const r = el("input"); r.type = "range"; r.min = min; r.max = max; r.value = value; r.disabled = !enabled;
  const commit = () => {
    let val = Math.round(Number(n.value));
    if (!Number.isFinite(val)) return;
    val = Math.max(min, Math.min(max, val));
    n.value = r.value = val;
    if (val !== value) onCommit(val);
  };
  r.addEventListener("input", () => { n.value = r.value; });
  r.addEventListener("change", commit);
  n.addEventListener("change", commit);
  n.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
  row.append(n, r);
  f.appendChild(row);
  return f;
}

/* -------------------------------------------------------------- skills */
function levelFor(xp) {
  let lvl = 1;
  S.curve.forEach((need, i) => { if (xp >= need) lvl = i + 1; });
  return lvl;
}

function renderSkills() {
  const g = $("skillsGrid"); g.innerHTML = "";
  const edited = new Set(S.edits.filter((e) => e.op === "xp").map((e) => e.skillId));
  const editable = S.view.editable;
  for (const s of S.view.skills) {
    const lvl = s.level || 1;
    const cur = S.curve[lvl - 1] || 0, next = S.curve[lvl] || cur;
    const pct = next > cur ? ((s.xp - cur) / (next - cur)) * 100 : 100;
    const card = el("div", "skill" + (edited.has(s.id) ? " edited" : ""));
    card.innerHTML = `
      ${s.icon ? `<img src="${iconUrl(s.icon)}" alt="">` : "<div></div>"}
      <div class="name"><span>${esc(s.name)}</span><span class="lvl">Lv ${lvl}</span></div>
      <div class="hint">${fmt(s.xp)} XP${next > cur ? ` · ${fmt(next - s.xp)} to level ${lvl + 1}` : " · max level"}</div>
      <div class="bar"><i style="width:${Math.max(0, Math.min(100, pct))}%"></i></div>`;
    const line = el("div", "xpline");
    const xpIn = el("input"); xpIn.type = "number"; xpIn.min = 0; xpIn.max = S.curve[S.curve.length - 1] || 1000000; xpIn.value = s.xp; xpIn.disabled = !editable;
    const lvSel = el("select"); lvSel.disabled = !editable;
    S.curve.forEach((need, i) => { const o = el("option", null, "Level " + (i + 1)); o.value = need; if (i + 1 === lvl) o.selected = true; lvSel.appendChild(o); });
    const setXp = (val) => { val = Math.max(0, Math.min(Number(xpIn.max), Math.round(val))); if (val !== s.xp) addEdits([{ op: "xp", skillId: s.id, value: val }]); };
    xpIn.addEventListener("change", () => setXp(Number(xpIn.value)));
    lvSel.addEventListener("change", () => setXp(Number(lvSel.value)));
    line.append(el("span", null, "XP"), xpIn, lvSel);
    card.appendChild(line);
    g.appendChild(card);
  }
}

/* ----------------------------------------------------------- character */
function renderCharacter() {
  const vb = $("vitals"); vb.innerHTML = "";
  for (const v of S.view.vitals) {
    if (!v.max) {
      const f = el("div", "field"); f.innerHTML = `<label><span>${esc(v.key)}</span><span>${v.value}</span></label><div class="hint">Shown only; the game sets its maximum.</div>`;
      vb.appendChild(f); continue;
    }
    vb.appendChild(numberField(v.key, Math.round(v.value), 0, v.max, (val) => addEdits([{ op: "vital", key: v.key, value: val }]), S.view.editable, `max ${v.max}`));
  }
  const facts = $("facts"); facts.innerHTML = "";
  const p = S.view.progress;
  const rows = [
    [S.view.playtimeHours + " h", "Play time"],
    [S.view.quests, "Quests started"],
    [p.RecipesUnlocked, "Recipes unlocked"],
    [p.BuildingsUnlocked, "Buildings unlocked"],
    [p.SpellsUnlocked, "Spells unlocked"],
    [p.ShrinesUnlocked, "Shrines unlocked"],
    [p.KilledOnceAIs, "Creature types defeated"],
    [p.ItemsPickedUp, "Item types found"],
    [S.view.mounts.length, "Mounts unlocked"],
  ];
  for (const [n, label] of rows) if (n != null) facts.appendChild(el("div", "fact", `<b>${esc(n)}</b><span>${esc(label)}</span>`));
}

/* ------------------------------------------------------------- bundles */
function renderBundles() {
  const b = $("bundles"); b.innerHTML = "";
  for (const x of S.bundles) {
    const card = el("div", "bundle");
    card.innerHTML = `<h3>${esc(x.name)}</h3><p>${esc(x.desc)}</p>${x.experimental ? '<div class="exp">Adds new items to your save</div>' : ""}`;
    const row = el("div", "row-gap");
    let amount = null;
    if (x.amount != null) {
      amount = el("input"); amount.type = "number"; amount.min = 1; amount.max = 9999; amount.value = x.amount; amount.style.width = "90px";
      row.append(el("span", "hint", "Amount"), amount);
    }
    const go = el("button", "btn gold", "Add to changes");
    go.onclick = async () => {
      try {
        const r = await call("bundle_edits", x.id, amount ? Number(amount.value) : null);
        if (!r.edits.length) { toast("Nothing to change for this bundle."); return; }
        addEdits(r.edits);
        toast(`${r.edits.length} change${r.edits.length > 1 ? "s" : ""} added. Review them before saving.`);
      } catch (e) { toast(e.message, true); }
    };
    row.appendChild(go);
    card.appendChild(row);
    b.appendChild(card);
  }
}

/* -------------------------------------------------------------- picker */
async function openPicker(mode, container, slot, target = "char") {
  if (target === "char" && !S.view.editable) return;
  S.pickerSel = null;
  const equipOnly = target === "char" && container === "Loadout" ? { 0: "Head", 1: "Body", 2: "Legs", 3: "Cape", 4: "Trinket" }[slot] : null;
  const cats = [...new Set(S.items.filter((i) => !equipOnly || i.slot === equipOnly).map((i) => i.category).filter(Boolean))].sort();
  showModal(mode === "add" ? "Add item" : "Swap item", `
    <div class="picker">
      <div>
        <div class="picker-tools">
          <input class="search" id="pkSearch" placeholder="Search items…">
          <select id="pkCat"><option value="">All categories</option>${cats.map((c) => `<option>${esc(c)}</option>`).join("")}</select>
        </div>
        <div class="picker-grid" id="pkGrid"></div>
      </div>
      <div class="picker-side" id="pkSide"><div class="hint">Pick an item on the left.</div></div>
    </div>`, []);
  const draw = () => {
    const q = $("pkSearch").value.trim().toLowerCase();
    const cat = $("pkCat").value;
    const grid = $("pkGrid"); grid.innerHTML = "";
    const list = S.items.filter((i) => (!equipOnly || i.slot === equipOnly) && (!cat || i.category === cat) &&
      (!q || i.name.toLowerCase().includes(q))).sort((a, b) => a.name.localeCompare(b.name)).slice(0, 400);
    for (const it of list) {
      const p = el("div", "pick" + (S.pickerSel === it.id ? " sel" : ""));
      p.innerHTML = (it.icon ? `<img src="${iconUrl(it.icon)}" alt="" loading="lazy">` : '<div class="noimg">?</div>') + esc(it.name);
      p.onclick = () => { S.pickerSel = it.id; draw(); pickerSide(mode, container, slot, target); };
      grid.appendChild(p);
    }
    if (!list.length) grid.appendChild(el("div", "hint", "No items match."));
  };
  $("pkSearch").addEventListener("input", draw);
  $("pkCat").addEventListener("change", draw);
  draw();
  $("pkSearch").focus();
}

async function pickerSide(mode, container, slot, target) {
  const it = S.byId[S.pickerSel];
  const side = $("pkSide");
  side.innerHTML = `
    ${it.icon ? `<img class="d-icon" src="${iconUrl(it.icon)}" alt="">` : ""}
    <div class="d-banner"><h3>${esc(it.name)}</h3><div class="sub">${esc(it.category || "")}${it.powerLevel ? " · Power Level " + it.powerLevel : ""}</div></div>
    <p class="d-desc">${esc(it.description || "")}</p>`;
  let amount = 1, dur = it.maxDurability;
  if (it.maxDurability) {
    side.appendChild(numberField("Durability", it.maxDurability, 1, it.maxDurability, (v) => { dur = v; }, true, `max ${fmt(it.maxDurability)}`));
  } else {
    const mx = it.maxStack || 9999;
    side.appendChild(numberField("Amount", 1, 1, mx, (v) => { amount = v; }, true, `max ${fmt(mx)}`));
  }
  const warnBox = el("div", "warnings"); side.appendChild(warnBox);
  try {
    const r = target === "world" ? await call("gear_check", it.id, null, null) : await call("gear_check", it.id, container, slot);
    r.errors.forEach((w) => warnBox.appendChild(el("div", "err-item", esc(w))));
    r.warnings.forEach((w) => warnBox.appendChild(el("div", "warn-item", esc(w))));
    const ok = el("button", "btn gold", mode === "add" ? "Add" : "Swap");
    ok.disabled = r.errors.length > 0;
    ok.onclick = () => {
      // read the latest typed value even if "change" has not fired yet
      side.querySelectorAll("input[type=number]").forEach((n) => n.dispatchEvent(new Event("change")));
      const edit = { op: mode, container, slot, itemId: it.id };
      if (it.maxDurability) edit.durability = dur; else edit.count = amount;
      hideModal();
      if (target === "world") { W.selSlot = slot; addWorldEdits([edit]); return; }
      S.selected = { container, slot };
      addEdits([edit]);
    };
    const a = el("div", "d-actions"); a.appendChild(ok); side.appendChild(a);
  } catch (e) { warnBox.appendChild(el("div", "err-item", esc(e.message))); }
}

/* -------------------------------------------------------------- review */
async function openReview() {
  let r = { warnings: [], errors: [], diff: [] }, wr = { warnings: [], errors: [], diff: [] };
  try {
    if (S.edits.length) r = await call("preview", S.edits);
    if (W.edits.length) wr = await call("world_preview", W.edits);
  } catch (e) { toast(e.message, true); return; }
  const list = allChangeTexts().map((t) => `<div class="review-item">${esc(t)}</div>`).join("");
  const warns = r.warnings.concat(wr.warnings).map((w) => `<div class="warn-item">${esc(w)}</div>`).join("");
  const errs = r.errors.concat(wr.errors).map((w) => `<div class="err-item">${esc(w)}</div>`).join("");
  const diff = r.diff.concat(wr.diff || []).map(([a, b]) => `<span class="diff-old">- ${esc(a)}</span>\n<span class="diff-new">+ ${esc(b)}</span>`).join("\n");
  r = { errors: r.errors.concat(wr.errors) };
  const saveBtn = el("button", "btn gold", "Save to game");
  saveBtn.disabled = r.errors.length > 0 || S.status.gameRunning;
  saveBtn.onclick = commit;
  const cancel = el("button", "btn ghost", "Keep editing");
  cancel.onclick = hideModal;
  showModal("Review changes", `
    <p class="hint">A backup of every save file being changed is made automatically before writing.</p>
    <div class="review-list">${list}</div>
    ${errs || warns ? `<div class="warnings">${errs}${warns}</div>` : ""}
    <details class="raw"><summary>Exact lines that will change in the save file</summary><pre>${diff || "(none)"}</pre></details>`,
    [cancel, saveBtn]);
}

async function commit() {
  try {
    if (S.edits.length) {
      const r = await call("commit", S.edits);
      S.base = S.view = r.view;
      S.edits = []; S.warnings = []; S.errors = [];
    }
    if (W.edits.length) {
      const r = await call("world_commit", W.edits);
      W.base = W.view = r.view;
      W.edits = []; W.warnings = []; W.errors = [];
      renderWorld();
    }
    hideModal();
    renderAll();
    loadBackups();
    toast("Saved. Start the game and check your changes.");
  } catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------- backups */
async function loadBackups() {
  try {
    const r = await call("backups");
    const box = $("backupList"); box.innerHTML = "";
    if (!r.groups.length) box.appendChild(el("div", "hint", "Open a character or world first."));
    for (const g of r.groups) {
      box.appendChild(el("h3", "backup-group", esc(g.label)));
      if (!g.backups.length) box.appendChild(el("div", "hint", "No backups yet."));
      for (const b of g.backups) {
        const row = el("div", "backup");
        row.innerHTML = `<div><span class="when">${esc(b.when)}</span><span class="why">${esc(b.reason)} · ${b.files.length} file(s)</span></div>`;
        const btn = el("button", "btn small", "Restore");
        btn.onclick = () => confirmRestore(g, b);
        row.appendChild(btn);
        box.appendChild(row);
      }
    }
  } catch (e) { /* nothing open yet */ }
}

function confirmRestore(g, b) {
  const go = el("button", "btn gold", "Restore this backup");
  go.disabled = S.status.gameRunning;
  go.onclick = async () => {
    try {
      const r = await call("restore", g.key, b.id);
      hideModal();
      if (r.view) { S.base = S.view = r.view; S.edits = []; }
      if (r.worldView) { W.base = W.view = r.worldView; W.edits = []; renderWorld(); }
      renderAll(); loadBackups();
      toast("Backup restored. Your previous save was backed up first.");
    } catch (e) { toast(e.message, true); }
  };
  const cancel = el("button", "btn ghost", "Cancel"); cancel.onclick = hideModal;
  showModal("Restore backup?", `<p>This replaces <b>${esc(g.label)}</b> with the backup from <b>${esc(b.when)}</b>. The current file is backed up first, so you can undo this.</p>${S.status.gameRunning ? '<div class="err-item">Quit the game first.</div>' : ""}`, [cancel, go]);
}
/* --------------------------------------------------------------- setup */
async function loadDirs(deep = false) {
  const box = $("dirList");
  box.innerHTML = "";
  box.appendChild(el("div", "hint", deep ? "Scanning all drives…" : "Looking for the game…"));
  let r;
  try { r = await call("game_dirs", deep); } catch (e) { box.innerHTML = ""; toast(e.message, true); return; }
  box.innerHTML = "";
  S.foundDirs = r.dirs;
  if (!r.dirs.length) {
    box.appendChild(el("div", deep ? "err-item" : "hint", deep
      ? "RuneScape: Dragonwilds wasn't found on any drive. The toolkit needs the game installed on this PC, because it reads item names and pictures from it. Install the game, or if it's in an unusual place click Browse and pick its folder."
      : "Not found in the usual places. Scanning all drives…"));
  }
  r.dirs.forEach((d, i) => addDirOption(d, i === 0));
  renderSetupChecks();
}

function setupMsg(text, kind) {
  const box = $("setupMsg"); box.innerHTML = "";
  if (text) box.appendChild(el("div", kind === "bad" ? "err-item" : "warn-item", esc(text)));
}

function renderSetupChecks() {
  const box = $("setupChecks"); if (!box) return;
  const st = S.status || {};
  const dirs = S.foundDirs || [];
  const chars = (st.characters || []).length;
  const rows = [
    [dirs.length > 0 || st.libraryReady, "Game installed",
      dirs.length ? "Found: " + dirs[0].path : st.libraryReady ? "Found earlier" : "Not found yet"],
    [st.libraryReady, "Game library built",
      st.building ? "Building now…" : st.libraryReady ? "Ready" : "Not built yet"],
    [chars > 0, "Character saves",
      chars ? `${chars} found` : "None yet: play the game once so it saves a character"],
  ];
  box.innerHTML = "";
  for (const [ok, name, note] of rows) {
    box.appendChild(el("div", "check " + (ok ? "ok" : "todo"),
      `<span class="mark">${ok ? "✓" : "!"}</span><b>${esc(name)}</b><span class="hint">${esc(note)}</span>`));
  }
}

function addDirOption(d, checked) {
  if (typeof d === "string") d = { path: d, kind: "Picked", notes: [] };
  const box = $("dirList");
  const lab = el("label");
  const radio = el("input"); radio.type = "radio"; radio.name = "gdir"; radio.value = d.path; radio.checked = checked;
  radio.onchange = () => { S.gameDir = d.path; $("buildBtn").disabled = false; };
  const notes = d.notes && d.notes.length ? " · " + d.notes.join(", ") : "";
  lab.append(radio, document.createTextNode(d.path), el("span", "tag", esc(d.kind + notes)));
  box.appendChild(lab);
  d = d.path;
  if (checked) { S.gameDir = d; $("buildBtn").disabled = false; }
}

async function build() {
  $("buildBtn").disabled = true;
  setupMsg("");
  try { await call("build", S.gameDir); } catch (e) { $("buildBtn").disabled = false; setupMsg(e.message, "bad"); toast(e.message, true); return; }
  S.status.building = true; renderSetupChecks();
  const timer = setInterval(async () => {
    const r = await call("build_progress");
    $("buildLog").textContent = r.log.join("\n");
    if (!r.building) {
      clearInterval(timer);
      $("buildBtn").disabled = false;
      const last = r.log[r.log.length - 1] || "";
      if (last.startsWith("ERROR: ")) { setupMsg(last.slice(7), "bad"); S.status.building = false; renderSetupChecks(); }
      if (last === "DONE") {
        toast("Game library ready.");
        await loadLibrary();
        await pollStatus();
        if (!S.view) await openFirstCharacter(); else await openCharacter(S.status.current);
        showTab("inventory");
      }
    }
  }, 700);
}

/* --------------------------------------------------------------- modal */
function showModal(title, html, buttons) {
  $("modalTitle").textContent = title;
  $("modalBody").innerHTML = html;
  const foot = $("modalFoot"); foot.innerHTML = "";
  buttons.forEach((b) => foot.appendChild(b));
  foot.classList.toggle("hidden", !buttons.length);
  $("modal").classList.remove("hidden");
}
function hideModal() { $("modal").classList.add("hidden"); }

function showTab(name) {
  document.querySelectorAll(".sidenav button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  // Only these tabs use the right-hand detail panel; the rest get the full width
  $("app").classList.toggle("no-detail", !["inventory", "world", "items"].includes(name));
  if (name === "setup" && !S.firstRun) loadDirs();
  if (name === "backups") loadBackups();
  if (name === "world") { worldInit(); if (W.view) { renderWorldDetail(); setTimeout(applyMapTransform, 0); } }
  if (name === "inventory") renderDetail();
}

function bindStatic() {
  document.querySelectorAll(".sidenav button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
  $("charSelect").addEventListener("change", (e) => openCharacter(e.target.value));
  $("rootSelect").addEventListener("change", (e) => changeRoot(e.target.value));
  $("reloadBtn").addEventListener("click", async () => {
    if (S.status.current) return openCharacter(S.status.current);
    await pollStatus(); loadRoots();
    if (S.status.libraryReady) await openFirstCharacter();
  });
  $("invSearch").addEventListener("input", renderInventory);
  $("discardBtn").addEventListener("click", () => { S.edits = []; refreshPreview(); if (W.edits.length) { W.edits = []; refreshWorldPreview(); } });
  $("reviewBtn").addEventListener("click", openReview);
  $("modalClose").addEventListener("click", hideModal);
  $("modal").addEventListener("click", (e) => { if (e.target.id === "modal") hideModal(); });
  document.addEventListener("keydown", (e) => {
    const typing = /INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName);
    if (e.key === "Escape") {
      if (!$("modal").classList.contains("hidden")) { hideModal(); return; }
      if (document.querySelector("#tab-world.active") && (W.selChest || W.selObject)) {
        W.selChest = null; W.selObject = null; renderMarkers(); renderPlaces(); renderWorldDetail();
      }
    }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z" && !typing) {
      e.preventDefault();
      if (document.querySelector("#tab-world.active")) { if (!undoWorld()) undoChar(); }
      else if (!undoChar()) undoWorld();
    }
  });
  $("backupNowBtn").addEventListener("click", async () => {
    try { await call("backup_now"); toast("Backup made."); loadBackups(); } catch (e) { toast(e.message, true); }
  });
  $("openBackupsBtn").addEventListener("click", () => call("open_folder", "backups"));
  $("browseBtn").addEventListener("click", async () => {
    try { const r = await call("browse_game"); if (r.dir) addDirOption(r.dir, true); } catch (e) { toast(e.message, true); }
  });
  $("buildBtn").addEventListener("click", build);
  $("scanBtn").addEventListener("click", () => loadDirs(true));
}
