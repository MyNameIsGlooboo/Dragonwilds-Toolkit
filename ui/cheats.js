"use strict";
/* Live cheats tab. Switch cheats live in wanted.txt and the in-game mod keeps them applied;
   actions are one-shot commands. All of these were tested in game (2026-10-15). */

const C = { status: null, fns: [], wanted: {}, lastSeq: 0, timer: null };

const LIVE = [
  ["Player", [
    { type: "switch", key: "god", label: "Unlimited health and stamina", desc: "Max health 999, healed to full all the time, can't die, stamina always full." },
    { type: "number", key: "damage", label: "Damage multiplier", desc: "1 = normal, 10 = ten times the damage.", def: 5, min: 0, max: 100 },
    { type: "number", key: "falldamage", label: "Fall damage multiplier", desc: "0 = no fall damage.", def: 0, min: 0, max: 5 },
    { type: "action", fn: "dw.heal", label: "Full health and stamina now" },
  ]],
  ["Survival", [
    { type: "switch", key: "nohunger", label: "No hunger, thirst or tiredness", desc: "Food, water and rest stop going down." },
    { type: "action", fn: "dw.refill", label: "Refill food, water and rest now" },
  ]],
  ["Items", [
    { type: "switch", key: "durability", label: "Unlimited durability", desc: "Tools, weapons and armour stop wearing out." },
    { type: "number", key: "carry", label: "Carry weight limit", desc: "Normal is 400. Num 2 sets 99999.", def: 99999, min: 1, max: 999999 },
    { type: "give" },
  ]],
  ["Skills", [
    { type: "number", key: "xp", label: "XP multiplier", desc: "1 = normal, 3 = triple XP.", def: 3, min: 0, max: 100 },
  ]],
  ["Movement", [
    { type: "number", key: "walk", label: "Speed multiplier", desc: "Walking, sprinting, mount and flying speed together. 1 = normal.", def: 2, min: 0.1, max: 10 },
    { type: "switch", key: "fly", label: "Flying", desc: "Look up or down to rise or sink. Switching off: no fall damage for 10 seconds." },
    { type: "number", key: "jump", label: "Jump height multiplier", desc: "1 = normal.", def: 3, min: 0.1, max: 10 },
    { type: "action", fn: "dw.grave", label: "Teleport to my latest gravestone" },
    { type: "action", fn: "dw.return", label: "Go back to where I was before teleporting" },
    { type: "home" },
  ]],
  ["World", [
    { type: "action", fn: "dw.revealmap", label: "Reveal the whole map" },
    { type: "note", text: "Teleport: right-click any spot on the World tab's map and choose \"Teleport there now\"." },
  ]],
];

async function renderCheats() {
  await refreshCheatStatus();
  await loadWanted();
  await loadHome();
  await loadFunctions();
  drawCheatGroups();
  drawAllCommands();
  if (!C.timer) C.timer = setInterval(cheatTick, 1500);
}

async function cheatTick() {
  if (!document.querySelector("#tab-cheats.active")) { clearInterval(C.timer); C.timer = null; return; }
  const wasReady = ready();
  const before = JSON.stringify(C.wanted);
  await refreshCheatStatus();
  await loadWanted();  // numpad keys in game change switches too
  if (ready() !== wasReady || JSON.stringify(C.wanted) !== before) drawCheatGroups();
  try {
    const r = await call("cheat_results", C.lastSeq);
    for (const x of r.results) {
      C.lastSeq = Math.max(C.lastSeq, x.seq);
      cheatLog((x.ok ? "✓ " : "✗ ") + x.message);
      if (!x.ok) toast(friendlyCheatError(x.message), true);
    }
  } catch (e) { /* ignore */ }
}

// Messages from the game, in plain words
function friendlyCheatError(msg) {
  if (/command not found: dw\./.test(msg)) return "The game is running an older cheat script. Restart the game, then try again.";
  return "In game: " + msg.replace(/^.*main\.lua:\d+: /, "");
}

function cheatLog(line) {
  const log = $("cheatLog");
  log.textContent = (new Date().toLocaleTimeString() + "  " + line + "\n" + log.textContent).slice(0, 4000);
}

async function refreshCheatStatus() {
  let st;
  try { st = await call("cheat_status"); } catch (e) { $("cheatStatus").textContent = e.message; return; }
  C.status = st;
  const running = S.status && S.status.gameRunning;
  const setup = $("cheatSetup");
  const dots = [
    [st.installed, st.installed ? "Installed in game folder" : "Not installed"],
    [running, running ? "Game running" : "Game closed"],
    [st.modAlive, st.modAlive ? "Mod connected" : "Mod not connected"],
    [st.player, st.player ? "In a world" : "Not in a world"],
  ];
  $("cheatStatus").textContent = ready() ? "Ready: switches apply within a second." : st.installed ? "Start the game and load a world." : "Install once to use live cheats.";
  setup.innerHTML = `<div class="status-dots">${dots.map(([ok, t]) => `<span class="${ok ? "ok" : ""}">${esc(t)}</span>`).join("")}</div>`;
  if (st.modOutdated) {
    setup.appendChild(el("div", "warn-item restart-note", "Restart the game to use everything in this version. The game is still running the older cheat script "
      + "(it only reads a new one when it starts), so newer buttons such as \"Send to their gravestone\" won't work until then."));
  }
  const row = el("div", "row-gap");
  if (!st.gameFound) row.appendChild(el("span", "err-item", "The game folder isn't set yet. Open Game library (bottom left), pick your RuneScape: Dragonwilds folder and build the library first."));
  else if (!st.installed) {
    const b = el("button", "btn gold", "Install cheats");
    b.disabled = running;
    b.title = running ? "Quit the game first" : "";
    b.onclick = async () => {
      if (!confirm("This adds UE4SS (an open-source mod loader, about 9 MB from GitHub) and the editor's cheat script to your game folder.\n\nYour saves are not changed. You can remove it any time with Uninstall.\n\nContinue?")) return;
      b.disabled = true; b.textContent = "Installing…";
      try { await call("cheat_install"); toast("Cheats installed. Start the game and load a world."); } catch (e) { toast(e.message, true); }
      renderCheats();
    };
    row.appendChild(b);
  } else {
    const u = el("button", "btn ghost", "Uninstall cheats");
    u.disabled = running;
    u.title = running ? "Quit the game first" : "Removes UE4SS and the cheat script from the game folder";
    u.onclick = async () => {
      if (!confirm("Remove UE4SS and the cheat script from the game folder?")) return;
      try { await call("cheat_uninstall"); toast("Cheats removed from the game folder."); } catch (e) { toast(e.message, true); }
      renderCheats();
    };
    row.appendChild(u);
    const fix = el("button", "btn ghost", "Fix stuck values");
    fix.disabled = running;
    fix.title = running ? "Quit the game first" : "Forgets the normal speeds and stats the cheat mod remembered, so it learns them again";
    fix.onclick = async () => {
      if (!confirm("Use this if a cheat left something wrong after turning it off (for example walking with no animation).\n\nAll cheats are switched off and the mod learns your normal values again next time the game starts. Continue?")) return;
      try { await call("cheat_reset_values"); C.wanted = {}; toast("Done. Start the game; everything is back to the game's own values."); } catch (e) { toast(e.message, true); }
      renderCheats();
    };
    row.appendChild(fix);
  }
  if (running && st.installed) {
    const procs = (S.status && S.status.gameProcesses) || [];
    row.appendChild(el("span", "warn-item", esc("Uninstall and Fix are off while the game runs. Still running: "
      + (procs.join(", ") || "the game") + ". If you already closed the game, wait a minute or end it in Task Manager.")));
  }
  row.appendChild(el("span", "hint", "Works on your character when you host. Cheats break Jagex's rules: use them in your own co-op only."));
  setup.appendChild(row);
}

function ready() { return !!(C.status && C.status.modAlive && C.status.player); }

async function loadHome() {
  try { C.home = (await call("cheat_home")).home; } catch (e) { C.home = null; }
}

async function loadWanted() {
  try { C.wanted = (await call("cheat_wanted")).wanted || {}; } catch (e) { C.wanted = {}; }
}

async function loadFunctions() {
  try { C.fns = (await call("cheat_functions")).functions; } catch (e) { C.fns = []; }
}

async function setWanted(key, value) {
  try {
    C.wanted = (await call("cheat_set", key, value)).wanted;
    cheatLog(value == null ? `${key} off` : `${key} = ${value}`);
    if (!ready()) toast("Saved. It applies as soon as you're in a world with the game running.");
  } catch (e) { toast(e.message, true); }
}

async function sendCheat(fn, args, label) {
  if (!ready()) { toast("Start the game and load a world first.", true); return; }
  try { await call("cheat_send", fn, args); cheatLog(`→ ${label || fn}`); }
  catch (e) { toast(e.message, true); }
}

function itemObjectPath(it) {
  // RSDragonwilds/Content/X/ITEM_Y.uasset -> /Game/X/ITEM_Y.ITEM_Y ; plugins -> /<Plugin>/X/...
  let p = (it.path || "").replace(/\.uasset$/, "");
  const name = p.split("/").pop();
  if (p.startsWith("RSDragonwilds/Content/")) p = "/Game/" + p.slice("RSDragonwilds/Content/".length);
  else {
    const m = p.match(/^RSDragonwilds\/Plugins\/GameFeatures\/([^/]+)\/Content\/(.*)$/);
    if (m) p = `/${m[1]}/${m[2]}`;
  }
  return `${p}.${name}`;
}

function giveItemDialog(preset, target, take) {
  const list = S.items.filter((i) => i.path).sort((a, b) => a.name.localeCompare(b.name));
  const title = take ? `Take an item from ${target}` : target ? `Give ${target} an item` : "Give item now (in game)";
  showModal(title, `
    <div class="picker-tools"><input class="search" id="giveSearch" placeholder="Search items…" value="${esc(preset || "")}"></div>
    <div class="picker-grid" id="giveGrid"></div>`, []);
  const draw = () => {
    const q = $("giveSearch").value.trim().toLowerCase();
    const grid = $("giveGrid"); grid.innerHTML = "";
    for (const it of list.filter((i) => !q || i.name.toLowerCase().includes(q)).slice(0, 300)) {
      const p = el("div", "pick", (it.icon ? `<img src="${iconUrl(it.icon)}" alt="" loading="lazy">` : '<div class="noimg">?</div>') + esc(it.name));
      p.onclick = () => {
        const max = it.maxDurability ? 1 : (it.maxStack || 99);
        const n = Number(prompt(take ? `How many ${it.name} to take away?` : `How many ${it.name}?`, String(max)));
        if (!n || n < 1) return;
        hideModal();
        if (target) playerDo(take ? "dw.p.take" : "dw.p.give", target, [itemObjectPath(it), String(Math.round(n))],
          `${take ? "take" : "give"} ${n} × ${it.name} ${take ? "from" : "to"} ${target}`);
        else sendCheat("dw.additem", [itemObjectPath(it), String(Math.round(n)), "1"], `give ${n} × ${it.name}`);
      };
      grid.appendChild(p);
    }
  };
  $("giveSearch").addEventListener("input", draw);
  draw();
  $("giveSearch").focus();
}

function controlFor(c) {
  const row = el("div", "cheat");
  if (c.type === "note") { row.innerHTML = `<div class="cs">${esc(c.text)}</div>`; return row; }
  if (c.type === "home") {
    row.appendChild(el("div", null, `<div class="cl">Home</div><div class="cs" id="homeText">${C.home ? `X ${Math.round(C.home[0])}  Y ${Math.round(C.home[1])}` : "Not set. Use the buttons, Num /, or right-click the World map."}</div>`));
    const ci = el("div", "ci");
    const go = el("button", "btn small", "Go home");
    go.disabled = !ready() || !C.home;
    go.onclick = () => sendCheat("dw.home", [], "teleport home");
    const here = el("button", "btn small ghost", "Set to where I am");
    here.disabled = !ready();
    here.onclick = async () => { await sendCheat("dw.sethome", [], "set home here"); setTimeout(async () => { await loadHome(); drawCheatGroups(); }, 1500); };
    ci.append(go, here); row.appendChild(ci);
    return row;
  }
  if (c.type === "give") {
    row.appendChild(el("div", null, '<div class="cl">Give item</div><div class="cs">Puts any item straight into your inventory.</div>'));
    const b = el("button", "btn small", "Choose…");
    b.onclick = () => giveItemDialog();
    const ci = el("div", "ci"); ci.appendChild(b); row.appendChild(ci);
    return row;
  }
  row.appendChild(el("div", null, `<div class="cl">${esc(c.label)}</div>${c.desc ? `<div class="cs">${esc(c.desc)}</div>` : ""}`));
  const ci = el("div", "ci");
  if (c.type === "switch") {
    const on = C.wanted[c.key] === 1;
    const sw = el("div", "switch" + (on ? " on" : ""));
    sw.onclick = async () => {
      const now = !(C.wanted[c.key] === 1);
      sw.classList.toggle("on", now);
      await setWanted(c.key, now ? 1 : null);
    };
    ci.appendChild(sw);
  } else if (c.type === "number") {
    const cur = C.wanted[c.key];
    const i = el("input"); i.type = "number"; i.min = c.min; i.max = c.max; i.step = "any";
    i.value = cur != null ? cur : c.def;
    const set = el("button", "btn small" + (cur != null ? " on" : ""), cur != null ? "On" : "Set");
    set.onclick = async () => {
      const v = Math.max(c.min, Math.min(c.max, Number(i.value)));
      await setWanted(c.key, v); drawCheatGroups();
    };
    const off = el("button", "btn small ghost", "Normal");
    off.disabled = cur == null;
    off.onclick = async () => { await setWanted(c.key, null); drawCheatGroups(); };
    ci.append(i, set, off);
  } else if (c.type === "action") {
    const b = el("button", "btn small", "Do it");
    b.disabled = !ready();
    b.onclick = () => sendCheat(c.fn, [], c.label);
    ci.appendChild(b);
  }
  row.appendChild(ci);
  return row;
}

const HOTKEY_HELP = [
  ["Num 1", "Unlimited health + stamina on/off"], ["Num 2", "Carry limit 99999 on/off"], ["Num 3", "Refill food, water, rest"],
  ["Num 4", "Full health and stamina"], ["Num 5", "Unlimited durability on/off"], ["Num 6", "No hunger/thirst/tiredness on/off"],
  ["Num 7", "Speed ×2 on/off"], ["Num 8", "Jump ×3 on/off"], ["Num 9", "Teleport to my latest gravestone"], ["Num .", "Go back after a teleport"], ["Num 0", "All cheats back to normal"], ["Num +", "Flying on/off"], ["Num −", "Teleport home"], ["Num /", "Set home where you stand"], ["Num *", "Top bar show/hide"],
];

function drawCheatGroups() {
  const box = $("cheatGroups");
  box.innerHTML = "";
  const top = el("div", "cheat-group");
  top.innerHTML = "<h3>Quick</h3>";
  const reset = el("button", "btn gold", "All back to normal");
  reset.title = "Turns every cheat off and puts all values back to the game's own";
  reset.onclick = async () => {
    try { C.wanted = (await call("cheat_reset_all")).wanted; cheatLog("all cheats back to normal"); drawCheatGroups(); toast("All cheats are back to normal."); }
    catch (e) { toast(e.message, true); }
  };
  top.appendChild(el("div", "row-gap", "")).appendChild(reset);
  const ovRow = el("div", "cheat");
  ovRow.appendChild(el("div", null, `<div class="cl">In-game overlay</div><div class="cs">See-through bar along the top of the game screen: every numpad key and whether it's on.</div>`));
  const ovSw = el("div", "switch" + (C.wanted.hideoverlay === 1 ? "" : " on"));
  ovSw.onclick = async () => { await setWanted("hideoverlay", C.wanted.hideoverlay === 1 ? null : 1); drawCheatGroups(); };
  const ovCi = el("div", "ci"); ovCi.appendChild(ovSw); ovRow.appendChild(ovCi);
  top.appendChild(ovRow);
  top.appendChild(el("div", "sec-title", "Numpad keys in game"));
  top.appendChild(el("div", "kv", HOTKEY_HELP.map(([k, t]) => `<span>${esc(k)}</span><span>${esc(t)}</span>`).join("")));
  top.appendChild(el("p", "hint", "Num Lock must be on. Switches you change with keys show up here too."));
  box.appendChild(top);
  for (const [title, list] of LIVE) {
    const g = el("div", "cheat-group", `<h3>${esc(title)}</h3>`);
    for (const c of list) g.appendChild(controlFor(c));
    box.appendChild(g);
  }
}

/* The game's own developer commands: kept for the curious; most are switched off in the release build. */
function drawAllCommands() {
  const box = $("cheatAll");
  box.innerHTML = "";
  const q = $("cheatSearch").value.trim().toLowerCase();
  const BLOCKED = /Crash|Reset|RemoveAll|KillAllPlayer|SpamChat|LoadBuilding|RemoveLoadedBuildings|SaveBuildings|Profanity|Framewach|RecordGameplay|QuickLoad|DestroyAi|BindKey|KeyBind|KeyboardMouseSuppressed|OutputAvailable|OutputCached|StartRecord/;
  const list = C.fns.filter((f) => !BLOCKED.test(f.name) && f.name.startsWith("dom") && (!q || f.name.toLowerCase().includes(q)));
  for (const f of list.slice(0, 120)) {
    const row = el("div", "cheat");
    row.appendChild(el("div", null, `<div class="cl">${esc(f.name.replace(/^dom/, "").replace(/([a-z])([A-Z])/g, "$1 $2"))}</div><div class="cs">${esc(f.params.map((p) => p.name).join(", "))}</div>`));
    const ci = el("div", "ci");
    const inputs = f.params.map((p) => { const i = el("input"); i.type = "text"; i.placeholder = p.name; ci.appendChild(i); return i; });
    const b = el("button", "btn small", "Run");
    b.onclick = () => sendCheat(f.name, inputs.map((i) => i.value.trim()), f.name);
    ci.appendChild(b);
    row.appendChild(ci);
    box.appendChild(row);
  }
  if (!list.length) box.appendChild(el("div", "hint", C.fns.length ? "No commands match." : "Not loaded yet."));
}

window.addEventListener("DOMContentLoaded", () => {
  $("cheatSearch").addEventListener("input", drawAllCommands);
  $("cheatReapply").remove();
});
