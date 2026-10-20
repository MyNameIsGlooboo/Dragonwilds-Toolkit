"use strict";
/* Players in your world (Live cheats tab + World map). When you host, the game on your PC is
   the server, so the cheat mod can change other players' characters and their game shows it.
   The mod lists everyone in players.txt; per-player switches live in pwanted.txt. */

const P = { players: [], wanted: {}, sig: "" };

const PLAYER_SWITCHES = [
  { key: "godmode", label: "God mode (everything)", value: 1, gold: true },
  { key: "god", label: "Unlimited health and stamina", value: 1 },
  { key: "nohunger", label: "No hunger, thirst or tiredness", value: 1 },
  { key: "durability", label: "Unlimited durability", value: 1 },
  { key: "carry", label: "Carry limit 99999", value: 99999 },
  { key: "xp", label: "XP ×3", value: 3 },
  { key: "damage", label: "Damage dealt ×5", value: 5 },
  { key: "falldamage", label: "No fall damage", value: 0 },
  { key: "walk", label: "Speed ×2 (test)", value: 2, test: true },
  { key: "fly", label: "Flying (test)", value: 1, test: true },
  { key: "freeze", label: "Freeze in place", value: 1, warn: true },
];

// [action, label, needs home, highlighted]
const PLAYER_BUTTON_ROWS = [
  ["Teleport", [
    ["pick", "Send to a map spot…", false, true],
    ["dw.p.grave", "Send to their gravestone", false, true],
    ["dw.p.bring", "Bring to me"],
    ["dw.p.home", "Send to my home", true],
    ["dw.p.back", "Send back"],
    ["dw.p.goto", "Go to them"],
    ["dw.p.gotograve", "Go to their gravestone"],
  ]],
  ["Help", [
    ["dw.p.heal", "Heal"],
    ["dw.p.refill", "Refill food/water/rest"],
    ["dw.p.revealmap", "Reveal their map"],
  ]],
];

const EVERYONE = [
  ["grave", "Send all to their gravestones"], ["bring", "Bring all to me"], ["home", "Send all to my home", true],
  ["back", "Send all back"], ["heal", "Heal all"], ["refill", "Refill all"],
];

async function loadPlayers() {
  try {
    const r = await call("cheat_players");
    P.players = r.players || [];
    P.wanted = r.wanted || {};
  } catch (e) { P.players = []; P.wanted = {}; }
}

function friends() { return P.players.filter((p) => !p.me); }

async function playerDo(fn, name, args, label) {
  if (!ready()) { toast("Start the game and load a world first.", true); return; }
  try { await call("cheat_pdo", fn, name, args || []); cheatLog(`→ ${label}`); toast(label + "…"); }
  catch (e) { toast(e.message, true); }
}

async function everyoneDo(action, label) {
  if (!ready()) { toast("Start the game and load a world first.", true); return; }
  try { await call("cheat_all", action); cheatLog(`→ ${label}`); toast(label + "…"); }
  catch (e) { toast(e.message, true); }
}

async function playerSet(name, key, value) {
  try {
    P.wanted = (await call("cheat_pset", name, key, value)).wanted || {};
    const sw = PLAYER_SWITCHES.find((s) => s.key === key);
    cheatLog(`${name}: ${sw ? sw.label : key} ${value == null ? "off" : "on"}`);
  } catch (e) { toast(e.message, true); }
}

function playerDistance(p) {
  const me = P.players.find((x) => x.me);
  if (!me || p.me || !p.spawned || !me.spawned) return "";
  const m = Math.hypot(p.x - me.x, p.y - me.y, p.z - me.z) / 100;
  return m < 1000 ? `${Math.round(m)} m away` : `${(m / 1000).toFixed(1)} km away`;
}

function healthText(p) {
  if (!p.spawned) return "Not spawned (loading or dead)";
  if (p.health < 0) return "";
  return `Health ${Math.round(p.health)} / ${Math.round(p.maxHealth)}`;
}

function everyoneBar() {
  const bar = el("div", "pl-everyone");
  bar.appendChild(el("span", "pl-row-label", "Everyone"));
  for (const [action, label, needsHome] of EVERYONE) {
    const b = el("button", "btn small" + (action === "grave" ? " gold-line" : ""), esc(label));
    b.disabled = needsHome && !C.home;
    if (b.disabled) b.title = "Set your home first (Movement group below, or Num /)";
    b.onclick = () => everyoneDo(action, label);
    bar.appendChild(b);
  }
  return bar;
}

function friendCard(p) {
  const card = el("div", "pl-card");
  card.dataset.player = p.name;
  card.appendChild(el("div", "pl-head",
    `<span class="pl-name">${esc(p.name)}</span><span class="tag">Friend</span>`
    + `<span class="pl-hp hint">${esc(healthText(p))}</span><span class="pl-dist hint">${esc(playerDistance(p))}</span>`));
  const w = P.wanted[p.name] || {};
  const sws = el("div", "pl-switches");
  for (const s of PLAYER_SWITCHES) {
    const on = w[s.key] != null;
    const row = el("label", "pl-sw" + (s.warn ? " warn" : "") + (s.test ? " test" : "") + (s.gold ? " gold" : ""));
    if (s.test) row.title = "New: works from your side only if their game accepts it. Tell me what happens.";
    const sw = el("div", "switch" + (on ? " on" : ""));
    sw.onclick = async (ev) => {
      ev.preventDefault();
      const now = !(P.wanted[p.name] && P.wanted[p.name][s.key] != null);
      sw.classList.toggle("on", now);
      await playerSet(p.name, s.key, now ? s.value : null);
    };
    row.append(sw, el("span", null, esc(s.label)));
    sws.appendChild(row);
  }
  card.appendChild(sws);
  for (const [title, buttons] of PLAYER_BUTTON_ROWS) {
    const row = el("div", "pl-actions");
    row.appendChild(el("span", "pl-row-label", esc(title)));
    for (const [fn, label, needsHome, gold] of buttons) {
      const b = el("button", "btn small" + (gold ? " gold-line" : ""), esc(label));
      b.disabled = !p.spawned || (needsHome && !C.home);
      if (needsHome && !C.home) b.title = "Set your home first (Movement group below, or Num /)";
      b.onclick = fn === "pick" ? () => startPickSpot(p.name) : () => playerDo(fn, p.name, [], `${label}: ${p.name}`);
      row.appendChild(b);
    }
    card.appendChild(row);
  }
  const items = el("div", "pl-actions");
  items.appendChild(el("span", "pl-row-label", "Items"));
  const give = el("button", "btn small", "Give item…");
  give.disabled = !p.spawned;
  give.onclick = () => giveItemDialog("", p.name, false);
  const take = el("button", "btn small", "Take item…");
  take.disabled = !p.spawned;
  take.onclick = () => giveItemDialog("", p.name, true);
  items.append(give, take);
  card.appendChild(items);
  const more = el("div", "pl-actions");
  more.appendChild(el("span", "pl-row-label", ""));
  const normal = el("button", "btn small ghost", "All back to normal");
  normal.title = "Turns off every switch for this player and puts their values back";
  normal.onclick = async () => { await playerDo("dw.p.normal", p.name, [], `${p.name}: all back to normal`); P.wanted[p.name] = {}; P.sig = ""; drawPlayers(); };
  const details = el("button", "btn small ghost", "Details");
  details.title = "Saves everything about this player to inspect_player.txt in the mod folder (for troubleshooting)";
  details.onclick = () => playerDo("dw.p.inspect", p.name, [], `details for ${p.name}`);
  more.append(normal, details);
  card.appendChild(more);
  return card;
}

function drawPlayers() {
  const box = $("playerList");
  if (!box) return;
  // Rebuild only when the list or switches change; health and distance update in place
  const sig = JSON.stringify([P.players.map((p) => [p.name, p.me, p.spawned]), P.wanted, ready(), !!C.home]);
  if (sig === P.sig) {
    for (const p of P.players) {
      const card = box.querySelector(`[data-player="${CSS.escape(p.name)}"]`);
      if (!card) continue;
      card.querySelector(".pl-hp").textContent = healthText(p);
      card.querySelector(".pl-dist").textContent = playerDistance(p);
    }
    return;
  }
  P.sig = sig;
  box.innerHTML = "";
  if (!ready()) {
    box.appendChild(el("div", "hint pad", "Start the game and load your world to see who's in it."));
    return;
  }
  if (!P.players.length) {
    box.appendChild(el("div", "hint pad", "Looking for players… (the list updates every few seconds)"));
    return;
  }
  const me = P.players.find((p) => p.me);
  if (me) {
    const card = el("div", "pl-card me");
    card.dataset.player = me.name;
    card.appendChild(el("div", "pl-head",
      `<span class="pl-name">${esc(me.name)}</span><span class="tag gold">You (host)</span>`
      + `<span class="pl-hp hint">${esc(healthText(me))}</span><span class="pl-dist hint"></span>`));
    card.appendChild(el("div", "hint", "Your own cheats are in the groups below and on your numpad. Right-click the World map to send a friend somewhere."));
    box.appendChild(card);
  }
  const list = friends().sort((a, b) => a.name.localeCompare(b.name));
  if (!list.length) {
    box.appendChild(el("div", "hint pad", "No friends connected right now. When someone joins your world they show up here."));
    return;
  }
  if (list.length > 1) box.appendChild(everyoneBar());
  for (const p of list) box.appendChild(friendCard(p));
}

/* ------------------------------------------------- World map: live players */
function drawLiveMarkers() {
  const stage = $("mapStage");
  if (!stage || typeof W === "undefined" || !W.view || !W.view.map || typeof worldToPx !== "function") return;
  let layer = $("liveMarkers");
  if (!layer) { layer = el("div"); layer.id = "liveMarkers"; stage.appendChild(layer); }
  layer.innerHTML = "";
  if (!ready()) return;
  for (const p of P.players) {
    if (!p.spawned) continue;
    const [px, py] = worldToPx(p.x, p.y);
    const d = el("div", "live-marker" + (p.me ? " me" : ""), `<span>${esc(p.name)}</span>`);
    d.style.left = px + "px"; d.style.top = py + "px";
    d.style.transform = `scale(${1 / W.zoom})`;
    d.title = `${p.name} (live)${p.me ? "" : " · right-click the map to send them somewhere"}`;
    layer.appendChild(d);
  }
}

const _applyMapTransformLive = applyMapTransform;
applyMapTransform = function () {
  _applyMapTransformLive();
  document.querySelectorAll("#liveMarkers .live-marker").forEach((m) => { m.style.transform = `scale(${1 / W.zoom})`; });
};

// Map right-click: "Send <friend> here" for every friend in the world
function addSendHere(ctx, wx, wy) {
  if (!ready()) return;
  const list = friends().filter((p) => p.spawned);
  let after = ctx.firstChild;  // the coordinates line
  for (const p of list) {
    const b = el("button", null, `Send ${esc(p.name)} here (in game)`);
    b.onclick = async () => {
      hideCtx();
      let z = "";
      try { z = String((await call("teleport_target", wx, wy)).z - 150); } catch (e) { /* unknown height */ }
      playerDo("dw.p.tp", p.name, [String(wx), String(wy), z], `send ${p.name} to the map spot`);
    };
    ctx.insertBefore(b, after ? after.nextSibling : null);
    after = b;
  }
}

// "Send to a map spot…": open the World map; the next click there picks the spot
function startPickSpot(name) {
  P.pickFor = name;
  showTab("world");
  const wrap = $("mapWrap");
  let b = $("pickBanner");
  if (!b) { b = el("div", "pick-banner"); b.id = "pickBanner"; wrap.appendChild(b); }
  b.innerHTML = `Click the map where to send <b>${esc(name)}</b> · drag to move the map · Esc or right-click cancels`;
  b.classList.remove("hidden");
  wrap.classList.add("picking");
}

function stopPickSpot() {
  P.pickFor = null;
  const b = $("pickBanner"); if (b) b.classList.add("hidden");
  $("mapWrap").classList.remove("picking");
}

function bindPickSpot() {
  const wrap = $("mapWrap");
  let down = null;
  wrap.addEventListener("mousedown", (e) => { down = [e.clientX, e.clientY]; }, true);
  wrap.addEventListener("click", async (e) => {
    if (!P.pickFor) return;
    e.stopPropagation(); e.preventDefault();
    if (down && Math.abs(e.clientX - down[0]) + Math.abs(e.clientY - down[1]) > 3) return;  // was a drag
    const inv = W.view && W.view.map && W.view.map.inverse;
    if (!inv) return;
    const r = wrap.getBoundingClientRect();
    const px = (e.clientX - r.left - W.x) / W.zoom, py = (e.clientY - r.top - W.y) / W.zoom;
    const wx = inv.a * px + inv.c, wy = inv.e * py + inv.f;
    const name = P.pickFor;
    stopPickSpot();
    let z = "";
    try { z = String((await call("teleport_target", wx, wy)).z - 150); } catch (err) { /* unknown height */ }
    playerDo("dw.p.tp", name, [String(wx), String(wy), z], `send ${name} to the map spot`);
  }, true);
  wrap.addEventListener("contextmenu", (e) => {
    if (!P.pickFor) return;
    e.stopPropagation(); e.preventDefault();
    stopPickSpot();
  }, true);
  window.addEventListener("keydown", (e) => { if (e.key === "Escape" && P.pickFor) stopPickSpot(); });
}
window.addEventListener("DOMContentLoaded", bindPickSpot);

const _showCtxPlayers = showCtx;
showCtx = function (sx, sy, wx, wy) {
  _showCtxPlayers(sx, sy, wx, wy);
  addSendHere($("mapCtx"), wx, wy);
};

// Hook into the Live cheats tab: load with it and refresh on its timer
const _renderCheats = renderCheats;
renderCheats = async function () {
  await _renderCheats();
  await loadPlayers();
  P.sig = "";
  drawPlayers();
};
const _cheatTick = cheatTick;
cheatTick = async function () {
  await _cheatTick();
  if (!document.querySelector("#tab-cheats.active")) return;
  await loadPlayers();
  drawPlayers();
};

// Keep players fresh for the World map while the game is connected
setInterval(async () => {
  if (!S.api || !ready() || !document.querySelector("#tab-world.active")) {
    const layer = $("liveMarkers"); if (layer && !ready()) layer.innerHTML = "";
    return;
  }
  await loadPlayers();
  drawLiveMarkers();
}, 2000);
