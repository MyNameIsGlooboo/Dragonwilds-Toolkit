"use strict";
/* "A new version is out" bar. Checks GitHub once when the toolkit starts; notice only. */

const UPD = { info: null };

async function checkForUpdate() {
  try { UPD.info = await call("update_check"); } catch (e) { return; }
  $("updateToggle").checked = UPD.info.enabled !== false;
  drawUpdateBar();
}

function drawUpdateBar() {
  const bar = $("updateBar");
  const u = UPD.info;
  if (!u || !u.available) { bar.classList.add("hidden"); return; }
  bar.innerHTML = "";
  bar.appendChild(el("span", "update-dot"));
  bar.appendChild(el("span", "update-text",
    `<b>Version ${esc(u.latest)} is out.</b> You have ${esc(u.current)}. Download it, unzip it and use the new exe; your library, backups and settings carry over.`));
  const notes = el("button", "btn small ghost", "What's new");
  notes.onclick = () => {
    const go = el("button", "btn gold", "Download");
    go.onclick = () => { hideModal(); call("open_release_page", u.url); };
    showModal(u.title || "Version " + u.latest,
      `<pre class="release-notes">${esc(u.notes || "No notes for this release.")}</pre>`, [go]);
  };
  const dl = el("button", "btn small gold", "Download");
  dl.onclick = () => call("open_release_page", u.url);
  const skip = el("button", "btn small ghost", "Skip this version");
  skip.onclick = async () => { await call("update_prefs", null, u.latest); UPD.info.available = false; drawUpdateBar(); };
  const x = el("button", "x", "×");
  x.title = "Hide until next start";
  x.onclick = () => bar.classList.add("hidden");
  bar.append(notes, dl, skip, x);
  bar.classList.remove("hidden");
}

window.addEventListener("DOMContentLoaded", () => {
  $("updateToggle").addEventListener("change", async (e) => {
    try {
      await call("update_prefs", e.target.checked, null);
      toast(e.target.checked ? "The toolkit will tell you when a new version is out." : "Update notices are off.");
      if (e.target.checked) checkForUpdate(); else $("updateBar").classList.add("hidden");
    } catch (err) { toast(err.message, true); }
  });
  // wait for the app to connect, then check once
  const start = setInterval(() => { if (S.api) { clearInterval(start); setTimeout(checkForUpdate, 1500); } }, 300);
});
