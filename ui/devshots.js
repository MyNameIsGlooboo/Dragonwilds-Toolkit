"use strict";
/* Developer mode only (app.py --serve, index.html?dev): put the app in a given state for README
   screenshots, e.g. index.html?dev&shot=world. Does nothing in the normal app. */
(function () {
  const q = new URLSearchParams(location.search);
  if (!q.has("dev") || !q.get("shot")) return;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  window.addEventListener("load", async () => {
    await wait(2500);
    const shot = q.get("shot");
    if (shot === "inventory") {
      showTab("inventory");
      const it = S.view.equipment.find((x) => x.slot === 1) || S.view.equipment[0];
      if (it) select("Loadout", it.slot);
    } else if (shot === "world") {
      showTab("world");
      await wait(2500);
      const ids = Object.keys(W.view.containers);
      const best = ids.sort((a, b) => W.view.containers[b].items.length - W.view.containers[a].items.length)[0];
      if (best) selectChest(best, true);
    } else if (shot === "review") {
      showTab("inventory");
      const rune = S.view.inventory.find((x) => x.slot >= 32 && x.slot <= 55 && x.count != null);
      const edits = [];
      if (rune) edits.push({ op: "count", container: "Inventory", slot: rune.slot, value: 999 });
      const sk = S.view.skills[0];
      if (sk && S.curve.length > 59) edits.push({ op: "xp", skillId: sk.id, value: S.curve[59] });
      addEdits(edits);
      await wait(1200);
      openReview();
    } else {
      showTab(shot);
    }
  });
})();
