"use strict";

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) { window.location = "/login"; return null; }
  if (!res.ok) throw new Error(await res.text());
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

// --- tabs ---
document.querySelectorAll(".sidebar nav a").forEach((a) => {
  a.addEventListener("click", (e) => {
    e.preventDefault();
    const tab = a.dataset.tab;
    document.querySelectorAll(".sidebar nav a").forEach((x) => x.classList.remove("active"));
    a.classList.add("active");
    document.querySelectorAll(".tab").forEach((s) => s.classList.remove("active"));
    document.getElementById("tab-" + tab).classList.add("active");
    loadTab(tab);
  });
});

function buildBanner(s) {
  // Decide the single most important thing to tell the user right now.
  let cls = "good", icon = "✅", msg = "";
  if (s.kill_switch) {
    cls = "bad"; icon = "🛑";
    msg = "<b>EMERGENCY STOP is ON.</b> Nothing will post until you clear it (press Emergency stop again).";
  } else if (!s.has_x_credentials && !s.dry_run) {
    cls = "bad"; icon = "🔌";
    msg = "<b>X account not connected.</b> Add your API keys to start posting.";
  } else if (s.paused) {
    cls = "warn"; icon = "⏸";
    msg = "<b>Paused.</b> The bot is idle. Press <b>Start posting</b> to run.";
  } else if (s.dry_run) {
    cls = "warn"; icon = "🧪";
    msg = `<b>TEST MODE (dry-run).</b> Finding &amp; scoring memes but <b>not</b> actually posting. ` +
          `Posted today: ${s.posted_today}/${s.posts_per_day} (simulated).`;
  } else if (s.mode === "auto") {
    cls = "good"; icon = "🤖";
    msg = `<b>LIVE &amp; automatic.</b> Posting to @icr8meme by itself. ` +
          `Today: ${s.posted_today}/${s.posts_per_day} posts.`;
  } else {
    cls = "warn"; icon = "🙋";
    msg = `<b>LIVE &mdash; approval mode.</b> Waiting for you to approve posts in <b>Review</b>. ` +
          `${s.awaiting} awaiting.`;
  }
  const b = document.getElementById("statusBanner");
  b.className = "banner " + cls;
  b.innerHTML = `<span style="font-size:1.3rem">${icon}</span><span>${msg}</span>`;
}

async function loadStats() {
  const s = await api("/api/stats");
  if (!s) return;
  buildBanner(s);

  const badge = document.getElementById("badgeReview");
  if (badge) badge.textContent = s.awaiting ? s.awaiting : "";

  // Toggle Start/Pause button visibility for clarity.
  const resume = document.getElementById("btnResume");
  const pause = document.getElementById("btnPause");
  if (resume && pause) {
    resume.style.display = s.paused ? "" : "none";
    pause.style.display = s.paused ? "none" : "";
  }

  const cards = [
    ["Posted today", `${s.posted_today} / ${s.posts_per_day}`],
    ["Awaiting review", s.awaiting],
    ["Lined up", s.queued],
    ["Total posted", s.posted],
    ["Blocked (safety)", s.safety_rejected],
    ["Taste examples", s.taste_exemplars],
  ];
  document.getElementById("statCards").innerHTML = cards
    .map(([l, n]) => `<div class="card"><div class="num">${n}</div><div class="label">${l}</div></div>`)
    .join("");
}

function memeCard(c, kind) {
  const er = c.metric_impressions ? `${(c.engagement_rate * 100).toFixed(1)}% ER` : "";
  let actions = "";
  if (kind === "review") {
    actions = `
      <button class="btn small green" onclick="act(${c.id},'approve')">Approve</button>
      <button class="btn small danger" onclick="act(${c.id},'disable')">Disable</button>`;
  } else if (kind === "posted") {
    actions = `<button class="btn small danger" onclick="delPost(${c.id})">Delete from X</button>`;
  }
  const stats = kind === "posted"
    ? `<div>${c.metric_impressions} imp · ${c.metric_likes}♥ · ${c.metric_reposts}↻ · ${c.metric_bookmarks}🔖 ${er}</div>`
    : `<div class="muted">${c.source}/${c.subject} · ${c.source_score} pts</div>`;
  return `<div class="meme">
    <img loading="lazy" src="${c.image_url}" onerror="this.style.opacity=0.2" />
    <div class="meta">
      <span class="q">Q ${c.quality_score}</span>
      ${stats}
      <span class="muted" title="${(c.status_reason||'').replace(/"/g,'')}">${c.status}</span>
    </div>
    <div class="actions">${actions}</div>
  </div>`;
}

async function loadReview() {
  const awaiting = await api("/api/candidates?status=awaiting_approval&limit=100");
  const queued = await api("/api/candidates?status=queued&limit=100");
  const all = [...(awaiting || []), ...(queued || [])];
  document.getElementById("reviewGrid").innerHTML =
    all.length ? all.map((c) => memeCard(c, "review")).join("") : '<p class="muted">Nothing to review.</p>';
}

async function loadPosted() {
  const posted = await api("/api/candidates?status=posted&limit=100");
  document.getElementById("postedGrid").innerHTML =
    (posted && posted.length) ? posted.map((c) => memeCard(c, "posted")).join("") : '<p class="muted">Nothing posted yet.</p>';
}

async function act(id, action) {
  await api(`/api/candidates/${id}/${action}`, { method: "POST" });
  loadReview();
  loadStats();
}
async function delPost(id) {
  if (!confirm("Delete this post from X?")) return;
  await api(`/api/candidates/${id}/delete_post`, { method: "POST" });
  loadPosted();
  loadStats();
}

const SETTING_FIELDS = [
  ["posts_per_day", "Target posts / day", "number"],
  ["max_posts_per_day", "Hard daily cap", "number"],
  ["min_minutes_between_posts", "Min minutes between posts", "number"],
  ["active_hours_start", "Active hours start (0-23)", "number"],
  ["active_hours_end", "Active hours end (0-23)", "number"],
  ["schedule_jitter_minutes", "Schedule jitter (min)", "number"],
  ["min_quality_score", "Min quality score (0-100)", "number"],
  ["min_source_score", "Min source score", "number"],
  ["max_content_age_hours", "Max meme age (hours, freshness)", "number"],
  ["safety_strictness", "Safety strictness", "select:low,medium,high"],
  ["reject_on_uncertainty", "Reject on uncertainty", "bool"],
  ["caption_mode", "Caption mode", "select:none,ai,title"],
  ["dedup_hamming_threshold", "Dedup sensitivity (hamming)", "number"],
  ["max_same_source_per_day", "Max same source / day", "number"],
  ["max_same_subject_per_day", "Max same subject / day", "number"],
  ["queue_ttl_hours", "Drop queued memes older than (hours)", "number"],
  ["max_queue_size", "Max queue backlog size", "number"],
  ["max_consecutive_errors", "Auto-shutdown after N errors", "number"],
  ["discovery_interval_minutes", "Discovery interval (min)", "number"],
  ["metrics_refresh_hours", "Metrics poll every (hours)", "number"],
  ["metrics_max_age_hours", "Stop tracking posts after (hours)", "number"],
  ["allowed_subjects", "Allowed subjects (comma sep, blank=all)", "csv"],
];

async function loadSettings() {
  const s = await api("/api/settings");
  if (!s) return;
  const form = document.getElementById("settingsForm");
  form.innerHTML = SETTING_FIELDS.map(([key, label, type]) => {
    let input;
    if (type === "bool") {
      input = `<select data-key="${key}"><option value="true"${s[key] ? " selected" : ""}>true</option><option value="false"${!s[key] ? " selected" : ""}>false</option></select>`;
    } else if (type.startsWith("select:")) {
      const opts = type.split(":")[1].split(",");
      input = `<select data-key="${key}">${opts.map((o) => `<option${s[key] === o ? " selected" : ""}>${o}</option>`).join("")}</select>`;
    } else if (type === "csv") {
      input = `<input data-key="${key}" data-type="csv" value="${(s[key] || []).join(", ")}" />`;
    } else {
      input = `<input data-key="${key}" data-type="number" value="${s[key]}" />`;
    }
    return `<div class="field"><label>${label}</label>${input}</div>`;
  }).join("");
}

document.getElementById("saveSettings").addEventListener("click", async () => {
  const payload = {};
  document.querySelectorAll("#settingsForm [data-key]").forEach((el) => {
    const key = el.dataset.key;
    const t = el.dataset.type;
    let v = el.value;
    if (t === "number") v = parseFloat(v);
    else if (t === "csv") v = v.split(",").map((x) => x.trim()).filter(Boolean);
    else if (v === "true") v = true;
    else if (v === "false") v = false;
    payload[key] = v;
  });
  await api("/api/settings", { method: "POST", body: JSON.stringify(payload) });
  document.getElementById("settingsSaved").textContent = "Saved ✓";
  setTimeout(() => (document.getElementById("settingsSaved").textContent = ""), 2000);
});

async function loadBlocklist() {
  const rows = await api("/api/blocklist");
  const t = document.getElementById("blockTable");
  t.innerHTML = "<tr><th>Kind</th><th>Value</th><th></th></tr>" +
    (rows || []).map((r) => `<tr><td><span class="tag">${r.kind}</span></td><td>${r.value}</td>
      <td><button class="btn small danger" onclick="rmBlock(${r.id})">Remove</button></td></tr>`).join("");
}
document.getElementById("addBlock").addEventListener("click", async () => {
  const kind = document.getElementById("blockKind").value;
  const value = document.getElementById("blockValue").value;
  if (!value) return;
  await api("/api/blocklist", { method: "POST", body: JSON.stringify({ kind, value }) });
  document.getElementById("blockValue").value = "";
  loadBlocklist();
});
async function rmBlock(id) {
  await api(`/api/blocklist/${id}`, { method: "DELETE" });
  loadBlocklist();
}

document.getElementById("addTaste").addEventListener("click", async () => {
  const image_url = document.getElementById("tasteUrl").value;
  const label = document.getElementById("tasteLabel").value;
  if (!image_url) return;
  const msg = document.getElementById("tasteMsg");
  msg.textContent = "Analyzing…";
  const r = await api("/api/taste", { method: "POST", body: JSON.stringify({ image_url, label }) });
  msg.textContent = r && r.ok ? "Added exemplar ✓" : "Could not add (bad image?)";
  document.getElementById("tasteUrl").value = "";
  loadStats();
});

function simCard(p) {
  const dt = p.when.replace("T", " ").slice(0, 16);
  const badge = p.category === "animal"
    ? '<span class="tag" style="background:#123">🐾 animal</span>'
    : '<span class="tag" style="background:#231">😂 meme</span>';
  const cap = p.caption
    ? `<div class="tweet-text">${p.caption.replace(/</g, "&lt;")}</div>`
    : `<div class="tweet-text muted">(no caption — image only)</div>`;
  return `<div class="tweet">
    <div class="tweet-head">
      <div class="avatar"></div>
      <div>
        <div class="tweet-name">your account <span class="muted">· ${dt}</span></div>
        <div class="muted" style="font-size:.75rem">${badge} r/${p.subject} · Q ${p.quality_score}</div>
      </div>
    </div>
    ${cap}
    <img class="tweet-img" loading="lazy" src="${p.image_url}" onerror="this.style.opacity=.15" />
  </div>`;
}

async function runSimulation() {
  const days = document.getElementById("simDays").value;
  document.getElementById("simSummary").textContent = "Simulating…";
  const r = await api(`/api/simulate?days=${days}`);
  if (!r) return;
  const named = r.posts.filter((p) => p.has_name_caption).length;
  document.getElementById("simSummary").textContent =
    `${r.count} posts planned from ${r.total_available} available · ${named} animal name-captions` +
    (r.notes.length ? ` · ${r.notes[0]}` : "");
  document.getElementById("simFeed").innerHTML =
    r.count ? r.posts.map(simCard).join("") : '<p class="muted">Nothing to simulate yet — let discovery run first.</p>';
}
document.getElementById("runSim").addEventListener("click", runSimulation);

async function loadActivity() {
  const rows = await api("/api/activity?limit=200");
  const t = document.getElementById("activityTable");
  t.innerHTML = "<tr><th>Time</th><th>Level</th><th>Event</th><th>Message</th></tr>" +
    (rows || []).map((r) => `<tr>
      <td class="muted">${(r.ts || "").replace("T", " ").slice(0, 19)}</td>
      <td class="lvl-${r.level}">${r.level}</td>
      <td><span class="tag">${r.event}</span></td>
      <td>${(r.message || "").slice(0, 160)}</td></tr>`).join("");
}

// --- master controls ---
document.getElementById("btnPause").onclick = async () => { await api("/api/control/pause", { method: "POST", body: JSON.stringify({ paused: true }) }); loadStats(); };
document.getElementById("btnResume").onclick = async () => { await api("/api/control/pause", { method: "POST", body: JSON.stringify({ paused: false }) }); loadStats(); };
document.getElementById("btnKill").onclick = async () => {
  if (!confirm("Engage EMERGENCY STOP? Nothing will post until you clear it.")) return;
  await api("/api/control/kill", { method: "POST", body: JSON.stringify({ active: true }) }); loadStats();
};
document.getElementById("btnPurge").onclick = async () => {
  if (!confirm("Disable ALL queued/awaiting content immediately?")) return;
  const r = await api("/api/queue/purge", { method: "POST" });
  alert(`Purged ${r.purged} items.`); loadStats(); loadReview();
};
document.getElementById("btnMode").onclick = async () => {
  const s = await api("/api/stats");
  const next = s.mode === "auto" ? "approval" : "auto";
  if (next === "auto" && !confirm("Switch to FULLY AUTOMATIC posting?")) return;
  await api("/api/control/mode", { method: "POST", body: JSON.stringify({ mode: next }) });
  loadStats();
};

function loadTab(tab) {
  if (tab === "dashboard") loadStats();
  else if (tab === "preview") runSimulation();
  else if (tab === "review") loadReview();
  else if (tab === "posted") loadPosted();
  else if (tab === "settings") loadSettings();
  else if (tab === "blocklist") loadBlocklist();
  else if (tab === "activity") loadActivity();
}

loadStats();
setInterval(loadStats, 15000);
