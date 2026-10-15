"use strict";
/* "Add items" window: pick any items with amounts and add them to the backpack.
   Live (game running + Live cheats connected): straight into the game.
   Otherwise: save edits into the right pouch, split into full stacks. */

const ADD = { cart: [], cat: "", q: "" };

// Player inventory pages (from the game files, see dw/capacity.py)
const PAGES = { backpack: [0, 31], rune: [32, 55], ammo: [56, 79], quest: [80, 151] };

function pageFor(it) {
  const asset = (it.path || "").split("/").pop() || "";
  if (/ITEM_Rune_/.test(asset) && !/Essence/.test(asset)) return "rune";
  if (it.slot === "Ammo" && /(Arrow|Bolt)/.test(asset)) return "ammo";
  if (it.slot === "Quest" || (it.category || "").includes("Quest")) return "quest";
  return "backpack";
}

function liveReady() {
  return typeof C !== "undefined" && C.status && C.status.modAlive && C.status.player;
}

async function openAddItems() {
  if (!S.view) { toast("Open a character first.", true); return; }
  try { if (typeof refreshCheatStatus === "function") await refreshCheatStatus(); } catch (e) { /* not installed */ }
  const live = liveReady();
  const cats = [...new Set(S.items.map((i) => i.category).filter(Boolean))].sort();
  showModal("Add items to your backpack", `
    <p class="hint">${live ? "<b>Live:</b> the game is running with Live cheats, so items go straight into your inventory." :
      "Items are added to your save (Review & save to write them). Runes go to the rune pouch, arrows and bolts to the ammo pouch."}</p>
    <div class="picker">
      <div>
        <div class="picker-tools">
          <input class="search" id="addSearch" placeholder="Search items…">
          <select id="addCat"><option value="">All categories</option>${cats.map((c) => `<option>${esc(c)}</option>`).join("")}</select>
        </div>
        <div class="picker-grid" id="addGrid"></div>
      </div>
      <div class="picker-side">
        <div class="sec-title" style="margin-top:0">Your list</div>
        <div class="overview-list" id="addCart"></div>
        <div class="d-actions"><button class="btn ghost small" id="addClear">Clear</button></div>
      </div>
    </div>`, [addAddButton(live)]);
  $("addSearch").addEventListener("input", addDraw);
  $("addCat").addEventListener("change", addDraw);
  $("addClear").onclick = () => { ADD.cart = []; addDrawCart(); };
  addDraw(); addDrawCart();
  $("addSearch").focus();
}

function addAddButton(live) {
  const b = el("button", "btn gold", live ? "Give in game now" : "Add to backpack");
  b.id = "addGo";
  b.onclick = () => (live ? addGiveLive() : addAddToSave());
  return b;
}

function addDraw() {
  const q = $("addSearch").value.trim().toLowerCase();
  const cat = $("addCat").value;
  const grid = $("addGrid"); grid.innerHTML = "";
  const list = S.items.filter((i) => (!cat || i.category === cat) && (!q || i.name.toLowerCase().includes(q)))
    .sort((a, b) => a.name.localeCompare(b.name)).slice(0, 400);
  for (const it of list) {
    const p = el("div", "pick" + (ADD.cart.some((c) => c.it.id === it.id) ? " sel" : ""),
      (it.icon ? `<img src="${iconUrl(it.icon)}" alt="" loading="lazy">` : '<div class="noimg">?</div>') + esc(it.name));
    p.title = it.maxDurability ? "Gear: added at full durability" : `Stacks up to ${fmt(it.maxStack || 9999)}`;
    p.onclick = () => {
      const have = ADD.cart.find((c) => c.it.id === it.id);
      if (have) ADD.cart = ADD.cart.filter((c) => c !== have);
      else ADD.cart.push({ it, n: it.maxDurability ? 1 : (it.maxStack || 99) });
      addDraw(); addDrawCart();
    };
    grid.appendChild(p);
  }
  if (!list.length) grid.appendChild(el("div", "hint", "No items match."));
}

function addDrawCart() {
  const box = $("addCart"); box.innerHTML = "";
  if (!ADD.cart.length) box.appendChild(el("div", "hint", "Click items on the left to add them here."));
  for (const c of ADD.cart) {
    const row = el("div", "ov-row");
    row.innerHTML = `${c.it.icon ? `<img src="${iconUrl(c.it.icon)}" alt="">` : "<div></div>"}<span>${esc(c.it.name)}</span>`;
    const n = el("input"); n.type = "number"; n.min = 1; n.value = c.n; n.style.width = "70px";
    n.title = c.it.maxDurability ? "How many (each one takes a slot)" : `Amount (a full stack is ${fmt(c.it.maxStack || 9999)})`;
    n.onchange = () => { c.n = Math.max(1, Math.round(Number(n.value) || 1)); n.value = c.n; };
    row.appendChild(n);
    box.appendChild(row);
  }
  const go = $("addGo");
  if (go) go.disabled = !ADD.cart.length;
}

async function addGiveLive() {
  let sent = 0;
  for (const c of ADD.cart) {
    try {
      await call("cheat_send", "dw.additem", [itemObjectPath(c.it), String(c.n), "1"]);
      sent++;
    } catch (e) { toast(e.message, true); }
  }
  hideModal();
  toast(`Sent ${sent} item type(s) to the game. Check the Live cheats log if any didn't fit.`);
  ADD.cart = [];
}

function addAddToSave() {
  if (!S.view.editable) { toast("This save can't be edited.", true); return; }
  const used = new Set(S.view.inventory.map((x) => x.slot));
  const edits = [];
  const skipped = [];
  const freeIn = (page) => {
    const [a, b] = PAGES[page];
    for (let s = a; s <= b; s++) if (!used.has(s)) { used.add(s); return s; }
    return null;
  };
  for (const c of ADD.cart) {
    const page = pageFor(c.it);
    let left = c.n;
    const per = c.it.maxDurability ? 1 : Math.max(1, c.it.maxStack || 9999);
    while (left > 0) {
      const slot = freeIn(page);
      if (slot == null) { skipped.push(c.it.name); break; }
      const take = Math.min(per, left);
      const e = { op: "add", container: "Inventory", slot, itemId: c.it.id };
      if (c.it.maxDurability) e.durability = c.it.maxDurability; else e.count = take;
      edits.push(e);
      left -= take;
    }
  }
  hideModal();
  if (edits.length) addEdits(edits);
  ADD.cart = [];
  toast(`${edits.length} stack(s) added to your changes${skipped.length ? `; no room for ${[...new Set(skipped)].join(", ")}` : ""}. Review & save to write them.`, !!skipped.length);
}

window.addEventListener("DOMContentLoaded", () => {
  $("addItemsBtn").addEventListener("click", openAddItems);
});
