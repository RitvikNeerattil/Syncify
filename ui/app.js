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

// toast("text") or toast("text", 5000, { kind: "ok" | "bad", action: "View", onClick })
function toast(msg, ms = 3200, opts = {}) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast";
  if (opts.kind) t.classList.add(opts.kind);
  t.onclick = null;
  if (opts.onClick) {
    t.classList.add("clickable");
    if (opts.action) {
      const a = document.createElement("a");
      a.textContent = opts.action;
      t.appendChild(a);
    }
    t.onclick = () => {
      t.classList.add("hidden");
      opts.onClick();
    };
  }
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), ms);
}

function showView(name) {
  $$(".nav-btn").forEach((x) => x.classList.toggle("active", x.dataset.view === name));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
}

// One-time popup with the result of the sync that runs when the app opens.
let launchPending = true;
function announceLaunchSync() {
  if (!launchPending || !state || state.sync_progress) return;
  if (state.removed_notice) {
    launchPending = false;
    return toast(state.removed_notice, 9000, { kind: "bad", action: "Settings", onClick: () => showView("settings") });
  }
  const s = state.last_sync;
  if (!s || !s.at) return; // not synced yet this session (or not signed in)
  launchPending = false;
  if (s.ok === false) {
    toast("Sync failed.", 9000, { kind: "bad", action: "See why in Settings", onClick: () => showView("settings") });
  } else {
    const detail = s.message.startsWith("Everything") ? "everything's up to date" : s.message;
    toast(`✓ Sync worked, ${detail}`, 4500, { kind: "ok" });
  }
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
    showView(b.dataset.view);
    if (b.dataset.view !== "search") refresh();
  })
);

// ---------- state ----------
async function refresh() {
  state = await call("state");
  $("#lib-count").textContent = state.songs.length || "";
  renderSync(state.last_sync, state.sync_progress);
  announceLaunchSync();
  renderSetup(state.setup);
  renderLibrary();
  renderSettings();
}

// Two-step button: first click turns it red ("Sure?"), second click within 3s runs it.
function armConfirm(btn, onConfirm) {
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (!btn.classList.contains("confirm")) {
      const label = btn.textContent;
      btn.classList.add("confirm");
      btn.textContent = "Sure?";
      clearTimeout(btn._t);
      btn._t = setTimeout(() => {
        btn.classList.remove("confirm");
        btn.textContent = label;
      }, 3000);
      return;
    }
    clearTimeout(btn._t);
    btn.disabled = true;
    try {
      await onConfirm();
    } catch (err) {
      toast(err.message);
    }
  });
}

let syncPoll = null;
function renderSync(s, progress) {
  if (!s) return;
  const st = $("#sync-status");
  st.classList.toggle("syncing", !!progress);
  if (progress) {
    st.textContent = `Syncing: ${progress}`;
    $("#sync-detail").textContent = `Syncing: ${progress}`;
  } else {
    st.textContent = s.at ? `${s.ok === false ? "⚠ " : ""}Synced ${ago(s.at)}` : s.message;
    const problems = (s.failed || []).length ? ` Problems: ${s.failed.join("; ")}` : "";
    $("#sync-detail").textContent = (s.at ? `Last sync ${ago(s.at)}: ${s.message}` : s.message || "") + problems;
  }
  st.title = progress || s.message;
  // poll quickly while a sync is running so progress stays live
  if (progress && !syncPoll) syncPoll = setInterval(refresh, 1500);
  if (!progress && syncPoll) {
    clearInterval(syncPoll);
    syncPoll = null;
  }
}

function ago2(ts) {
  return ts ? ago(ts) : "never";
}

let devKey = "";
function renderDevices() {
  const list = $("#devices");
  const card = $("#devices-card");
  card.classList.toggle("hidden", !state.account.signed_in);
  const key = JSON.stringify(state.devices) + Math.floor(Date.now() / 60000);
  if (key === devKey) return;
  devKey = key;
  if (!state.devices.length) {
    list.innerHTML = `<li class="muted small">Shows up after the first sync.</li>`;
    return;
  }
  list.innerHTML = state.devices
    .map(
      (d) => `
    <li data-id="${esc(d.id)}">
      <div class="dev">
        <div><b>${esc(d.name)}</b>${d.this_device ? `<span class="me">this computer</span>` : ""}</div>
        <div class="small muted">${d.songs} song${d.songs === 1 ? "" : "s"} · last synced ${esc(ago2(d.synced_at))}${d.platform ? " · " + esc(d.platform) : ""}</div>
      </div>
      ${d.this_device ? "" : `<button class="ghost danger">Remove</button>`}
    </li>`
    )
    .join("");
  $$("#devices li[data-id] button").forEach((b) => {
    const id = b.closest("li").dataset.id;
    const name = state.devices.find((d) => d.id === id)?.name;
    armConfirm(b, async () => {
      await call("remove_device", id);
      toast(`Removed ${name}. It'll be signed out the next time it opens Syncify.`, 5000);
      refresh();
    });
  });
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
$("#rescan").addEventListener("click", async () => {
  try {
    const n = await call("rescan_folder");
    toast(n ? `Added ${n} song(s) from your folder` : "No new songs in your folder");
    refresh();
  } catch (err) {
    toast(err.message);
  }
});

// Swap title and artist (for uploads named "Song - Artist")
$$(".swap").forEach((b) =>
  b.addEventListener("click", () => {
    const f = b.closest("form").elements;
    [f.title.value, f.artist.value] = [f.artist.value, f.title.value];
    if (b.closest("#meta-form")) updateFilename();
  })
);

// ---- sorting ----
const SORT_DEFAULT_DIR = { added: "desc", title: "asc", artist: "asc", year: "desc", duration: "desc", size: "desc" };
let libSort = { key: "added", dir: "desc" };
try {
  libSort = JSON.parse(localStorage.getItem("syncify.sort")) || libSort;
} catch (e) {}

function setSort(key, dir) {
  libSort = { key, dir: dir || SORT_DEFAULT_DIR[key] || "asc" };
  try {
    localStorage.setItem("syncify.sort", JSON.stringify(libSort));
  } catch (e) {}
  libKey = "";
  renderLibrary();
}

function sortSongs(songs) {
  const { key, dir } = libSort;
  const val = (s) => {
    if (key === "added") return s.added_at || 0;
    if (key === "year") return parseInt(s.year) || 0;
    if (key === "duration" || key === "size") return s[key] || 0;
    return (s[key] || "").toLowerCase();
  };
  const sign = dir === "asc" ? 1 : -1;
  return [...songs].sort((a, b) => {
    const x = val(a), y = val(b);
    if (x < y) return -sign;
    if (x > y) return sign;
    return (a.title || "").localeCompare(b.title || ""); // stable tie-break
  });
}

const fmtLen = (sec) => (sec ? `${Math.floor(sec / 60)}:${String(Math.round(sec % 60)).padStart(2, "0")}` : "–");
const fmtSize = (b) => (b ? `${(b / 1048576).toFixed(1)} MB` : "–");

$("#lib-sort").addEventListener("change", (e) => setSort(e.target.value));
$("#lib-dir").addEventListener("click", () => setSort(libSort.key, libSort.dir === "asc" ? "desc" : "asc"));
$$("#lib-cols [data-sort]").forEach((h) =>
  h.addEventListener("click", () => {
    const k = h.dataset.sort;
    // clicking the active column flips direction, a new column starts at its natural direction
    if (libSort.key === k) setSort(k, libSort.dir === "asc" ? "desc" : "asc");
    else setSort(k);
  })
);

let libKey = "";
function renderLibrary() {
  if (!state) return;
  const q = $("#lib-filter").value.toLowerCase();
  const key = q + JSON.stringify(libSort) + JSON.stringify(state.songs);
  if (key === libKey) return; // unchanged: don't wipe hover/confirm state
  libKey = key;
  $("#lib-sort").value = libSort.key;
  $("#lib-dir").textContent = libSort.dir === "asc" ? "↑" : "↓";
  $("#lib-dir").title = libSort.dir === "asc" ? "Ascending (click to reverse)" : "Descending (click to reverse)";
  $$("#lib-cols [data-sort]").forEach((h) => {
    const on = h.dataset.sort === libSort.key;
    h.classList.toggle("sorted", on);
    h.textContent = h.textContent.replace(/ [↑↓]$/, "") + (on ? (libSort.dir === "asc" ? " ↑" : " ↓") : "");
  });
  const songs = sortSongs(state.songs.filter((s) => !q || `${s.title} ${s.artist} ${s.album}`.toLowerCase().includes(q)));
  const box = $("#library");
  $("#lib-cols").classList.toggle("hidden", !songs.length);
  const totalSec = songs.reduce((t, s) => t + (s.duration || 0), 0);
  const totalBytes = songs.reduce((t, s) => t + (s.size || 0), 0);
  $("#lib-total").textContent = songs.length
    ? `${songs.length} song${songs.length === 1 ? "" : "s"} · ${Math.floor(totalSec / 3600) ? Math.floor(totalSec / 3600) + " h " : ""}${Math.round((totalSec % 3600) / 60)} min · ${(totalBytes / 1073741824 >= 1 ? (totalBytes / 1073741824).toFixed(2) + " GB" : (totalBytes / 1048576).toFixed(0) + " MB")}`
    : "";
  if (!songs.length) {
    box.innerHTML = `<p class="empty">${state.songs.length ? "No matches." : "Nothing here yet. Go find something in Search."}</p>`;
    return;
  }
  box.innerHTML = songs
    .map(
      (s) => `
    <div class="song" data-id="${esc(s.id)}">
      ${s.video_id ? `<img src="https://i.ytimg.com/vi/${esc(s.video_id)}/default.jpg" alt="">` : `<div class="ph">♪</div>`}
      <div><div class="t">${esc(s.title)}</div><div class="sub small muted">${esc(s.filename)}</div></div>
      <div class="sub muted">${esc(s.artist)}${s.album ? " · " + esc(s.album) : ""}</div>
      <div class="muted small">${esc(s.year)}</div>
      <div class="muted small num">${fmtLen(s.duration)}</div>
      <div class="muted small num">${s.present ? fmtSize(s.size) : `<span class="missing">not here yet</span>`}</div>
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
    armConfirm($("[data-act=delete]", row), async () => {
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
  const ln = $("#login-notify");
  ln.checked = state.startup.notify;
  ln.disabled = !state.startup.supported || !state.startup.enabled;
  const t = state.tools;
  const js = t.deno ? ["Deno", t.deno] : t.node ? ["Node", t.node] : null;
  $("#tools").innerHTML = [
    ["yt-dlp", t.yt_dlp, `version ${t.yt_dlp}`],
    ["ffmpeg", t.ffmpeg, t.ffmpeg || (state.setup.running ? "downloading…" : "missing")],
    ["JS runtime", js, js ? `${js[0]}: ${js[1]}` : (state.setup.running ? "downloading…" : "missing (YouTube needs it)")],
  ]
    .map(([name, ok, detail]) => {
      const isPath = ok && /[\\/]/.test(detail);
      const label = !ok ? detail : isPath ? "Ready" : detail;
      const path = isPath ? detail.replace(/^(Deno|Node): /, "") : "";
      return `<li><span>${name}</span><span class="val small ${ok ? "ok" : "bad"}">${esc(label)}${path ? `<span class="path" title="${esc(path)}">${esc(path)}</span>` : ""}</span></li>`;
    })
    .join("");
  renderDevices();
  const rn = $("#removed-notice");
  rn.textContent = state.removed_notice || "";
  rn.classList.toggle("hidden", !state.removed_notice);
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
$("#login-notify").addEventListener("change", async (e) => {
  try {
    e.target.checked = await call("set_login_notify", e.target.checked);
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
$("#ob-done").addEventListener("click", async () => {
  if (state.startup.supported) {
    try {
      await call("set_startup", $("#ob-startup").checked);
    } catch (err) {
      toast(err.message);
    }
  }
  $("#onboard").classList.add("hidden");
  refresh();
});

// ---------- boot ----------
window.addEventListener("pywebviewready", async () => {
  await refresh();
  if (!state.library_root) {
    renderOnboarding();
    $("#ob-startup").checked = state.startup.enabled;
    $("#ob-startup").closest("label").classList.toggle("hidden", !state.startup.supported);
    $(".ob-startup-note").classList.toggle("hidden", !state.startup.supported);
    $("#onboard").classList.remove("hidden");
  }
  const page = await call("take_open_page");
  if (page === "settings" || page === "library") showView(page);
  setInterval(refresh, 30000); // picks up the background sync's results
});
