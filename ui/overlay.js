"use strict";
/* In-game window (F7 in the game). The toolkit shows it over the game; the cheat mod pauses the game
   while it's open. Talks to the same toolkit API as the main window. */

const O = { api: null, wanted: {}, items: [], pick: null, players: [], pwanted: {}, home: null };

const $ = (id) => document.getElementById(id);
const el = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const iconUrl = (f) => (f ? "/lib/icons/" + encodeURIComponent(f) : null);

async function call(name, ...args) {
  const res = await O.api[name](...args);
  if (res && res.error) throw new Error(res.error);
  return res;
}

function toast(msg, bad = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (bad ? " bad" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), bad ? 5000 : 2500);
}

function log(msg) { $("ovLog").textContent = new Date().toLocaleTimeString() + "  " + msg; }

// switches: wanted key, label, value when on
const SWITCHES = [
  ["god", "Unlimited health and stamina", 1], ["nohunger", "No hunger, thirst or tiredness", 1],
  ["durability", "Unlimited durability", 1], ["carry", "Carry limit 99999", 99999],
  ["damage", "Damage dealt ×10", 10], ["walk", "Speed ×2", 2],
  ["jump", "Jump ×3", 3], ["fly", "Flying", 1],
  ["xp", "XP ×3", 3], ["falldamage", "No fall damage", 0],
  ["hideoverlay", "Hide the top bar", 1],
];
// what god mode turns on (shown in gold while it's on)
const GOD_KEYS = new Set(["god", "nohunger", "durability", "carry", "damage", "walk", "xp", "falldamage"]);

const ACTIONS = [
  ["dw.heal", "Heal"], ["dw.refill", "Refill food, water, rest"],
  ["dw.home", "Teleport home"], ["dw.sethome", "Set home here"],
  ["dw.grave", "My latest gravestone"], ["dw.return", "Go back"],
  ["dw.revealmap", "Reveal the map"], ["dw.normal", "Everything back to normal"],
];

async function loadWanted() {
  try { O.wanted = (await call("cheat_wanted")).wanted || {}; } catch (e) { /* not installed */ }
}

async function setWanted(key, value) {
  try {
    O.wanted = (await call("cheat_set", key, value)).wanted || {};
    log(value == null ? `${key} off` : `${key} on`);
  } catch (e) { toast(e.message, true); }
  drawSwitches();
}

function drawSwitches() {
  const god = O.wanted.godmode != null;
  $("godSwitch").classList.toggle("on", god);
  $("godCard").classList.toggle("on", god);
  const box = $("ovSwitches");
  box.innerHTML = "";
  for (const [key, label, value] of SWITCHES) {
    const on = O.wanted[key] != null;
    const viaGod = god && GOD_KEYS.has(key) && !on;
    const row = el("div", "ov-sw" + (viaGod ? " from-god" : ""));
    row.title = viaGod ? "On through god mode" : "";
    row.append(el("div", "switch" + (on || viaGod ? " on" : "")), el("span", null, esc(label)));
    row.onclick = () => setWanted(key, on ? null : value);
    box.appendChild(row);
  }
}

function drawActions() {
  const box = $("ovActions");
  box.innerHTML = "";
  for (const [fn, label] of ACTIONS) {
    const b = el("button", "btn", esc(label));
    b.onclick = async () => {
      try { await call("cheat_send", fn, []); log(label); toast(label + "…"); }
      catch (e) { toast(e.message, true); }
      if (fn === "dw.normal") setTimeout(async () => { await loadWanted(); drawSwitches(); }, 600);
    };
    box.appendChild(b);
  }
}

/* -------------------------------------------------------------- items */
function itemObjectPath(it) {
  let p = (it.path || "").replace(/\.uasset$/, "");
  const name = p.split("/").pop();
  if (p.startsWith("RSDragonwilds/Content/")) p = "/Game/" + p.slice("RSDragonwilds/Content/".length);
  else {
    const m = p.match(/^RSDragonwilds\/Plugins\/GameFeatures\/([^/]+)\/Content\/(.*)$/);
    if (m) p = `/${m[1]}/${m[2]}`;
  }
  return `${p}.${name}`;
}

async function loadItems() {
  try {
    const r = await call("item_list");
    O.items = (r.items || []).filter((i) => i.path).sort((a, b) => a.name.localeCompare(b.name));
  } catch (e) { O.items = []; }
  drawItems();
}

function drawItems() {
  const q = $("ovSearch").value.trim().toLowerCase();
  const box = $("ovItems");
  box.innerHTML = "";
  const list = O.items.filter((i) => !q || i.name.toLowerCase().includes(q)).slice(0, 240);
  for (const it of list) {
    const p = el("div", "pick" + (O.pick === it ? " sel" : ""),
      (it.icon ? `<img src="${iconUrl(it.icon)}" alt="" loading="lazy">` : '<div class="noimg">?</div>') + esc(it.name));
    p.onclick = () => { O.pick = it; showGiveBar(); drawItems(); };
    box.appendChild(p);
  }
  if (!list.length) box.appendChild(el("div", "hint", O.items.length ? "No items match." : "The item list loads from the game library."));
}

function showGiveBar() {
  const it = O.pick;
  $("ovGiveBar").classList.toggle("hidden", !it);
  if (!it) return;
  $("ovGiveIcon").src = iconUrl(it.icon) || "";
  $("ovGiveName").textContent = it.name;
  $("ovGiveCount").value = it.maxDurability ? 1 : (it.maxStack || 99);
}

async function giveItem() {
  const it = O.pick;
  const n = Math.max(1, Math.round(Number($("ovGiveCount").value) || 1));
  if (!it) return;
  try { await call("cheat_send", "dw.additem", [itemObjectPath(it), String(n), "1"]); log(`gave ${n} × ${it.name}`); toast(`${n} × ${it.name} added`); }
  catch (e) { toast(e.message, true); }
}

/* ------------------------------------------------------------ friends */
async function loadFriends() {
  try {
    const r = await call("cheat_players");
    O.players = (r.players || []).filter((p) => !p.me && p.spawned);
    O.pwanted = r.wanted || {};
  } catch (e) { O.players = []; }
  drawFriends();
}

function drawFriends() {
  $("ovFriendsPanel").classList.toggle("hidden", !O.players.length);
  const box = $("ovFriends");
  box.innerHTML = "";
  for (const p of O.players) {
    const row = el("div", "ov-friend");
    row.appendChild(el("b", null, esc(p.name)));
    const godOn = (O.pwanted[p.name] || {}).godmode != null;
    const god = el("button", "btn small" + (godOn ? " on" : ""), godOn ? "God mode ON" : "God mode");
    god.onclick = async () => {
      try { O.pwanted = (await call("cheat_pset", p.name, "godmode", godOn ? null : 1)).wanted || {}; } catch (e) { toast(e.message, true); }
      drawFriends();
    };
    row.appendChild(god);
    for (const [fn, label] of [["dw.p.heal", "Heal"], ["dw.p.bring", "Bring to me"], ["dw.p.grave", "To their gravestone"]]) {
      const b = el("button", "btn small", label);
      b.onclick = async () => { try { await call("cheat_pdo", fn, p.name, []); log(`${label}: ${p.name}`); } catch (e) { toast(e.message, true); } };
      row.appendChild(b);
    }
    box.appendChild(row);
  }
}

/* ------------------------------------------------------------ window */
function closeOverlay() { call("overlay_close").catch(() => {}); }

async function refresh() {
  await loadWanted();
  drawSwitches();
  loadFriends();
}

// called by the toolkit each time the window is shown
window.overlayShown = function () {
  refresh();
  $("ovSearch").focus();
};

async function init(api) {
  if (O.api) return;
  O.api = api;
  drawActions();
  $("godSwitch").onclick = () => setWanted("godmode", O.wanted.godmode != null ? null : 1);
  $("ovClose").onclick = closeOverlay;
  $("ovSearch").addEventListener("input", drawItems);
  $("ovGiveGo").onclick = giveItem;
  $("ovGiveCount").addEventListener("keydown", (e) => { if (e.key === "Enter") giveItem(); });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" || e.key === "F7") { e.preventDefault(); closeOverlay(); }
  });
  await refresh();
  loadItems();
  setInterval(() => { if (document.visibilityState !== "hidden") refresh(); }, 2000);
}

window.addEventListener("pywebviewready", () => init(window.pywebview.api));
if (location.search.includes("dev")) {
  const proxy = new Proxy({}, { get: (_, name) => (...args) =>
    fetch("/api/" + name, { method: "POST", body: JSON.stringify(args) }).then((r) => r.json()) });
  window.addEventListener("DOMContentLoaded", () => init(proxy));
}
