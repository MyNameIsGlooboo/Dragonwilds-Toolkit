"use strict";
/* Unlocks tab: recipes, buildings, spells, journal, mounts and map regions. */

const U = { family: "recipes", rows: [], sel: new Set(), q: "", showLocked: true };
const FAMILIES = [
  ["recipes", "Recipes", "Some recipes come from quests, vendors or new areas; unlocking them can skip progress."],
  ["buildings", "Buildings", "Building pieces you can place. Deluxe Edition pieces are left out."],
  ["spells", "Spells", "Spells are normally given by skill perks. Unlocking one without its perk may be undone by the game."],
  ["journal", "Journal", "Lore and journal entries. Low risk."],
  ["mounts", "Mounts", "Mounts you can call."],
  ["map", "Map", "Reveal regions of the world map."],
];

// one change per family selection, not one shared slot
const _editKey = editKey;
editKey = function (e) {
  if (e.op === "unlock") return "unlock:" + e.family + ":" + (e.all ? "all" : (e.ids || []).join(",") + (e.asset || "") + (e.value ?? ""));
  return _editKey(e);
};

async function renderUnlocks() {
  const body = $("unlockBody");
  if (!S.view) { body.innerHTML = '<p class="hint">Open a character first.</p>'; return; }
  body.innerHTML = "";
  const seg = el("div", "seg unlock-seg");
  for (const [k, label] of FAMILIES) {
    const b = el("button", U.family === k ? "on" : "", esc(label));
    b.onclick = () => { U.family = k; U.sel.clear(); renderUnlocks(); };
    seg.appendChild(b);
  }
  body.appendChild(seg);
  const fam = FAMILIES.find((f) => f[0] === U.family);
  $("unlockNote").textContent = fam[2];

  let r;
  try { r = await call("unlocks_view", U.family); } catch (e) { body.appendChild(el("p", "err-item", esc(e.message))); return; }
  if (U.family === "map") return renderRegions(body, r);

  U.rows = r.rows;
  const done = r.rows.filter((x) => x.unlocked).length;
  const tools = el("div", "row-gap unlock-tools");
  const search = el("input"); search.className = "search"; search.placeholder = "Search…"; search.value = U.q;
  search.oninput = () => { U.q = search.value.toLowerCase(); drawGrid(); };
  const lockedOnly = el("label", "hint"); const cb = el("input"); cb.type = "checkbox"; cb.checked = U.showLocked;
  cb.onchange = () => { U.showLocked = cb.checked; drawGrid(); };
  lockedOnly.append(cb, document.createTextNode(" Only locked"));
  const unlockSel = el("button", "btn", "Unlock selected");
  unlockSel.onclick = () => {
    if (!U.sel.size) { toast("Tick some locked entries first.", true); return; }
    const ids = [...U.sel];
    if (U.family === "mounts") addEdits(ids.map((a) => ({ op: "unlock", family: "mounts", asset: a })));
    else addEdits([{ op: "unlock", family: U.family, ids }]);
    toast(`${ids.length} will be unlocked. Review & save.`); U.sel.clear(); drawGrid();
  };
  const unlockAll = el("button", "btn gold", "Unlock all");
  unlockAll.onclick = () => {
    if (!confirm(`Unlock every ${fam[1].toLowerCase().replace(/s$/, "")} in the base game and released areas?\n\n${fam[2]}`)) return;
    addEdits([{ op: "unlock", family: U.family, all: true }]);
    toast(`All ${fam[1].toLowerCase()} will be unlocked. Review & save.`);
  };
  unlockSel.disabled = unlockAll.disabled = !S.view.editable;
  tools.append(search, lockedOnly, unlockSel, unlockAll, el("span", "hint", `${done} of ${r.rows.length} unlocked`));
  body.appendChild(tools);
  const grid = el("div", "enc-grid"); grid.id = "unlockGrid";
  body.appendChild(grid);
  drawGrid();
}

function drawGrid() {
  const grid = $("unlockGrid");
  if (!grid) return;
  grid.innerHTML = "";
  const pending = new Set(S.edits.filter((e) => e.op === "unlock" && e.family === U.family).flatMap((e) => e.ids || (e.asset ? [e.asset] : [])));
  const all = S.edits.some((e) => e.op === "unlock" && e.family === U.family && e.all);
  const rows = U.rows.filter((x) => (!U.showLocked || !x.unlocked) && (!U.q || (x.name || "").toLowerCase().includes(U.q)))
    .sort((a, b) => (a.category || "").localeCompare(b.category || "") || (a.name || "").localeCompare(b.name || ""));
  if (!U.nameIcons) U.nameIcons = Object.fromEntries(S.items.filter((i) => i.icon).map((i) => [i.name.toLowerCase(), i.icon]));
  for (const x of rows.slice(0, 600)) {
    if (!x.icon) x.icon = U.nameIcons[(x.name || "").toLowerCase()] || null;
    const on = x.unlocked || pending.has(x.id) || all;
    const d = el("div", "enc" + (U.sel.has(x.id) ? " sel" : ""));
    d.innerHTML = (x.icon ? `<img src="${iconUrl(x.icon)}" alt="" loading="lazy">` : '<div class="noimg">?</div>') +
      esc(x.name) + (on ? `<span class="own">${x.unlocked ? "✓" : "+"}</span>` : "");
    d.title = (x.category || "") + (x.dlc && x.dlc !== "Base" ? " · " + x.dlc : "");
    if (!x.unlocked) d.onclick = () => { U.sel.has(x.id) ? U.sel.delete(x.id) : U.sel.add(x.id); drawGrid(); };
    else d.style.opacity = ".55";
    grid.appendChild(d);
  }
  if (!rows.length) grid.appendChild(el("div", "hint", U.showLocked ? "Everything here is already unlocked." : "Nothing matches."));
}

function renderRegions(body, r) {
  const pend = S.edits.find((e) => e.op === "unlock" && e.family === "map");
  const list = el("div", "overview-list pad0");
  const ticked = new Set(r.regions.filter((x) => x.revealed).map((x) => x.id));
  if (pend && Array.isArray(pend.value)) pend.value.forEach((v) => ticked.add(v));
  for (const reg of r.regions) {
    const row = el("label", "layer-row");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = ticked.has(reg.id) || (pend && !Array.isArray(pend.value));
    cb.disabled = reg.revealed || !S.view.editable;
    cb.onchange = () => { cb.checked ? ticked.add(reg.id) : ticked.delete(reg.id); };
    row.append(cb, el("span", "dot"), el("span", null, esc(reg.name)), el("span", "hint", reg.revealed ? "revealed" : ""));
    row.children[1].style.background = reg.revealed ? "#7fbf6a" : "#555";
    list.appendChild(row);
  }
  const tools = el("div", "row-gap unlock-tools");
  const apply = el("button", "btn", "Reveal ticked regions");
  apply.onclick = () => { addEdits([{ op: "unlock", family: "map", value: [...ticked] }]); toast("Map regions will be revealed. Review & save."); };
  const all = el("button", "btn gold", "Reveal whole map");
  all.onclick = () => { addEdits([{ op: "unlock", family: "map", value: r.all }]); toast("Whole map will be revealed. Review & save."); };
  apply.disabled = all.disabled = !S.view.editable;
  tools.append(apply, all, el("span", "hint", `${r.regions.filter((x) => x.revealed).length} of ${r.regions.length} revealed`));
  body.append(tools, list);
  body.appendChild(el("p", "hint", "The region order is decoded from the game files but not yet confirmed in game; try one region first."));
}
