// Syncify UI. Talks to Python through window.pywebview.api (see syncify/api.py).
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let state = null;
let current = null; // { result, prepared }
let editing = null;

async function call(name, ...args) {
  const res = await window.pywebview.api[name](...args);
  if (!res.ok) throw new Error(res.error);
  return res.data;
}

function toast(msg, ms = 3200) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

function ago(ts) {
  if (!ts) return "";
  const s = Math.round(Date.now() / 1000 - ts);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

// ---------- navigation ----------
$$(".nav-btn").forEach((b) =>
  b.addEventListener("click", () => {
    $$(".nav-btn").forEach((x) => x.classList.toggle("active", x === b));
    $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${b.dataset.view}`));
    if (b.dataset.view !== "search") refresh();
  })
);

// ---------- state ----------
async function refresh() {
  state = await call("state");
  $("#lib-count").textContent = state.songs.length || "";
  renderSync(state.last_sync);
  renderSetup(state.setup);
  renderLibrary();
  renderSettings();
}

function renderSync(s) {
  if (!s) return;
  $("#sync-status").textContent = s.at ? `${s.ok === false ? "⚠ " : ""}Synced ${ago(s.at)}` : s.message;
  $("#sync-status").title = s.message;
  $("#sync-detail").textContent = s.at ? `Last sync ${ago(s.at)}: ${s.message}` : s.message || "";
}

let setupTimer = null;
function renderSetup(st) {
  const box = $("#setup-box");
  const show = st.running || st.error || (st.missing.length && st.message);
  box.classList.toggle("hidden", !show);
  if (!show) return;
  $("#setup-msg").textContent = st.error ? st.error : `First-time setup: ${st.message}`;
  $("#setup-bar").style.width = `${st.progress || 0}%`;
  $("#setup-retry").classList.toggle("hidden", !st.error);
  if (st.running && !setupTimer) {
    setupTimer = setInterval(async () => {
      const s = await call("state");
      renderSetup(s.setup);
      if (!s.setup.running) {
        clearInterval(setupTimer);
        setupTimer = null;
        refresh();
      }
    }, 1000);
  }
}
$("#setup-retry").addEventListener("click", async () => {
  await call("retry_setup");
  setTimeout(refresh, 300);
});

// ---------- search ----------
$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (!q) return;
  const box = $("#results");
  box.innerHTML = `<p class="empty">Searching…</p>`;
  try {
    const results = await call("search", q);
    if (!results.length) return (box.innerHTML = `<p class="empty">Nothing found.</p>`);
    box.innerHTML = results
      .map(
        (r, i) => `
      <div class="result" data-i="${i}">
        <img class="thumb" src="${esc(r.thumbnail)}" loading="lazy" alt="">
        <div class="info">
          <div class="title">${esc(r.title)}</div>
          <div class="muted small">${esc(r.channel)}${r.duration ? " · " + esc(r.duration) : ""}${r.views ? " · " + Number(r.views).toLocaleString() + " views" : ""}</div>
          ${r.in_library ? `<span class="badge">In library</span>` : ""}
        </div>
      </div>`
      )
      .join("");
    $$(".result", box).forEach((el) => el.addEventListener("click", () => select(results[+el.dataset.i], el)));
    if (results.length === 1) select(results[0], $(".result", box));
  } catch (err) {
    box.innerHTML = `<p class="empty">Search failed: ${esc(err.message)}</p>`;
  }
});

async function select(result, el) {
  $$(".result").forEach((x) => x.classList.toggle("selected", x === el));
  current = { result, prepared: null };
  const panel = $("#panel");
  panel.classList.remove("hidden");
  $("#player").src = `https://www.youtube-nocookie.com/embed/${encodeURIComponent(result.id)}?autoplay=1`;
  $("#yt-link").href = result.url;
  $("#panel-title").textContent = result.title;
  $("#meta-form").classList.add("hidden");
  $("#panel-loading").classList.remove("hidden");
  try {
    const prepared = await call("prepare", result.id);
    if (current?.result !== result) return; // user clicked something else meanwhile
    current.prepared = prepared;
    const f = $("#meta-form");
    for (const k of ["title", "artist", "album", "year"]) f.elements[k].value = prepared.meta[k] || "";
    updateFilename();
    f.classList.remove("hidden");
  } catch (err) {
    toast(`Couldn't read that video: ${err.message}`);
  } finally {
    $("#panel-loading").classList.add("hidden");
  }
}

function updateFilename() {
  const t = $("#meta-form").elements.title.value.replace(/[<>:"/\\|?*]/g, "").trim() || "Untitled";
  $("#filename-preview").textContent = `${t}.mp3`;
}
$("#meta-form").elements.title.addEventListener("input", updateFilename);

$("#meta-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  if (!current?.prepared) return;
  const f = e.target;
  const meta = Object.fromEntries(["title", "artist", "album", "year"].map((k) => [k, f.elements[k].value.trim()]));
  const p = current.prepared;
  try {
    await call("download", p.video_id, p.url, meta, p.thumbnail);
    pollJobs();
  } catch (err) {
    toast(err.message);
  }
});

// ---------- download jobs ----------
let polling = false;
async function pollJobs() {
  if (polling) return;
  polling = true;
  try {
    while (true) {
      const jobs = await call("jobs");
      $("#jobs").innerHTML = jobs
        .map(
          (j) => `
        <div class="job ${j.error ? "error" : ""}">
          <div><b>${esc(j.title)}</b></div>
          <div class="small muted">${esc(j.error || j.stage)}</div>
          <div class="bar"><div style="width:${j.error ? 100 : j.progress}%"></div></div>
        </div>`
        )
        .join("");
      if (jobs.every((j) => j.done)) {
        if (jobs.some((j) => !j.error)) refresh();
        setTimeout(async () => {
          await call("clear_finished_jobs");
          $("#jobs").innerHTML = "";
        }, 6000);
        break;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  } finally {
    polling = false;
  }
}

// ---------- library ----------
$("#lib-filter").addEventListener("input", renderLibrary);
$("#open-folder").addEventListener("click", () => call("open_folder"));

function renderLibrary() {
  if (!state) return;
  const q = $("#lib-filter").value.toLowerCase();
  const songs = state.songs.filter((s) => !q || `${s.title} ${s.artist} ${s.album}`.toLowerCase().includes(q));
  const box = $("#library");
  if (!songs.length) {
    box.innerHTML = `<p class="empty">${state.songs.length ? "No matches." : "Nothing here yet. Go find something in Search."}</p>`;
    return;
  }
  box.innerHTML = songs
    .map(
      (s) => `
    <div class="song" data-id="${esc(s.id)}">
      <img src="${s.video_id ? `https://i.ytimg.com/vi/${esc(s.video_id)}/default.jpg` : ""}" alt="">
      <div><div class="t">${esc(s.title)}</div><div class="sub small muted">${esc(s.filename)}</div></div>
      <div class="sub muted">${esc(s.artist)}${s.album ? " · " + esc(s.album) : ""}</div>
      <div class="muted small">${esc(s.year)} ${s.present ? "" : `<div class="missing">not on this device yet</div>`}</div>
      <div class="actions">
        <button class="ghost" data-act="edit">Edit</button>
        <button class="ghost danger" data-act="delete">Delete</button>
      </div>
    </div>`
    )
    .join("");
  $$(".song", box).forEach((row) => {
    const song = state.songs.find((s) => s.id === row.dataset.id);
    $("[data-act=edit]", row).addEventListener("click", () => openEdit(song));
    $("[data-act=delete]", row).addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      if (btn.dataset.confirm !== "1") {
        btn.dataset.confirm = "1";
        btn.textContent = "Sure?";
        setTimeout(() => { btn.dataset.confirm = ""; btn.textContent = "Delete"; }, 3000);
        return;
      }
      await call("delete_song", song.id);
      toast(`Deleted "${song.title}" (also removes it from your other devices on next sync)`);
      refresh();
    });
  });
}

function openEdit(song) {
  editing = song;
  const f = $("#edit-form");
  for (const k of ["title", "artist", "album", "year"]) f.elements[k].value = song[k] || "";
  $("#edit-dialog").showModal();
}

$("#edit-dialog").addEventListener("close", async () => {
  if ($("#edit-dialog").returnValue !== "save" || !editing) return;
  const f = $("#edit-form");
  const meta = Object.fromEntries(["title", "artist", "album", "year"].map((k) => [k, f.elements[k].value.trim()]));
  try {
    await call("update_song", editing.id, meta);
    toast("Saved");
    refresh();
  } catch (err) {
    toast(err.message);
  }
});

// ---------- settings ----------
function renderSettings() {
  if (!state) return;
  const acc = state.account;
  $("#account-line").innerHTML = !acc.configured
    ? `<span class="bad small">This build has no Google client ID, so sync is off (see README).</span>`
    : acc.signed_in ? `Signed in as <b>${esc(acc.email || "your Google account")}</b>` : `<span class="muted">Not signed in. Songs stay on this computer only.</span>`;
  const ab = $("#account-btn");
  ab.textContent = acc.signed_in ? "Sign out" : "Sign in with Google";
  ab.classList.toggle("hidden", !acc.configured);
  const dn = $("#device-name");
  if (document.activeElement !== dn) dn.value = state.device_name;
  $("#lib-root").textContent = state.library_root || "Not set";
  const st = $("#startup");
  st.checked = state.startup.enabled;
  st.disabled = !state.startup.supported;
  const t = state.tools;
  const js = t.deno ? ["Deno", t.deno] : t.node ? ["Node", t.node] : null;
  $("#tools").innerHTML = [
    ["yt-dlp", t.yt_dlp, `version ${t.yt_dlp}`],
    ["ffmpeg", t.ffmpeg, t.ffmpeg || (state.setup.running ? "downloading…" : "missing")],
    ["JS runtime", js, js ? `${js[0]}: ${js[1]}` : (state.setup.running ? "downloading…" : "missing (YouTube needs it)")],
  ]
    .map(([name, ok, detail]) => `<li><span>${name}</span><span class="small ${ok ? "ok" : "bad"}">${esc(detail)}</span></li>`)
    .join("");
}

async function signIn(btn) {
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = "Finish signing in in your browser…";
  try {
    const email = await call("sign_in");
    toast(`Signed in as ${email}`);
    await refresh();
    return email;
  } catch (err) {
    toast(`Sign in failed: ${err.message}`, 6000);
    return null;
  } finally {
    btn.disabled = false;
    btn.textContent = label;
  }
}

$("#account-btn").addEventListener("click", async (e) => {
  if (state.account.signed_in) {
    await call("sign_out");
    toast("Signed out. Songs on this computer stay put.");
    return refresh();
  }
  signIn(e.currentTarget);
});

$("#device-name").addEventListener("change", (e) => call("set_device_name", e.target.value).then(refresh));
$("#open-folder-2").addEventListener("click", () => call("open_folder"));
$("#change-folder").addEventListener("click", async () => {
  try {
    const path = await call("choose_music_folder");
    if (!path) return;
    const r = await call("set_music_folder", path);
    toast(r.moved ? `Moved ${r.moved} song(s) to the new folder. Point Spotify at it too.` : "Music folder updated. Point Spotify at it too.", 6000);
    refresh();
  } catch (err) {
    toast(err.message);
  }
});
$("#startup").addEventListener("change", async (e) => {
  try {
    e.target.checked = await call("set_startup", e.target.checked);
  } catch (err) {
    toast(err.message);
  }
});

async function doSync() {
  const btn = $("#sync-btn");
  btn.disabled = true;
  btn.textContent = "Syncing…";
  try {
    const r = await call("sync_now");
    toast(r.message, 5000);
    await refresh();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Sync now";
  }
}
$("#sync-btn").addEventListener("click", doSync);

// ---------- first run ----------
let pickedFolder = null;

function renderOnboarding() {
  const acc = state.account;
  const accountDone = acc.signed_in || !acc.configured || onboardSkipped;
  $("#step-account").classList.toggle("done", accountDone);
  $("#ob-account-done").classList.toggle("hidden", !acc.signed_in);
  $("#ob-account-done").textContent = acc.signed_in ? `Signed in as ${acc.email}` : "";
  $("#ob-signin").parentElement.classList.toggle("hidden", accountDone);
  $("#step-folder").classList.toggle("locked", !accountDone);
  $("#step-folder").classList.toggle("done", !!state.library_root);
  $("#ob-folder").textContent = state.library_root || pickedFolder || state.suggested_folder;
  $("#ob-use-folder").parentElement.classList.toggle("hidden", !!state.library_root);
  $("#step-spotify").classList.toggle("locked", !state.library_root);
}
let onboardSkipped = false;

$("#ob-signin").addEventListener("click", async (e) => {
  await signIn(e.currentTarget);
  renderOnboarding();
});
$("#ob-skip").addEventListener("click", () => {
  onboardSkipped = true;
  renderOnboarding();
});
$("#ob-choose-folder").addEventListener("click", async () => {
  const path = await call("choose_music_folder");
  if (path) {
    pickedFolder = path;
    renderOnboarding();
  }
});
$("#ob-use-folder").addEventListener("click", async () => {
  try {
    await call("set_music_folder", pickedFolder || state.suggested_folder);
    await refresh();
    renderOnboarding();
  } catch (err) {
    toast(err.message);
  }
});
$("#ob-done").addEventListener("click", () => {
  $("#onboard").classList.add("hidden");
  refresh();
});

// ---------- boot ----------
window.addEventListener("pywebviewready", async () => {
  await refresh();
  if (!state.library_root) {
    renderOnboarding();
    $("#onboard").classList.remove("hidden");
  }
  setInterval(refresh, 30000); // picks up the background sync's results
});
