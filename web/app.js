/* Carewise front end. No build step, no framework. */
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const state = { sid: null, health: null, photo: null, recording: false, busy: false };

/* ---------- small helpers ---------- */
function toast(msg, ms = 3200) {
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), ms);
}
const nowHHMM = () => {
  const d = new Date();
  return String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
};
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ---------- theme ---------- */
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("cw-theme", t); } catch (e) {}
}
(function initTheme() {
  let t = null;
  try { t = localStorage.getItem("cw-theme"); } catch (e) {}
  if (t) document.documentElement.dataset.theme = t;
})();
$("theme").addEventListener("click", () => {
  const dark = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() === "#0a0a0a";
  setTheme(dark ? "light" : "dark");
});

/* ---------- agent trace panel ---------- */
async function showTrace(events) {
  const ol = $("trace");
  ol.innerHTML = "";
  document.querySelector(".col-trace").classList.add("has");
  let total = 0;
  for (const e of events) {
    total += e.ms || 0;
    const li = document.createElement("li");
    li.className = e.status === "done" ? "live" : e.status;
    li.innerHTML =
      `<span class="ms">${e.ms ? e.ms + " ms" : ""}</span><div class="agent">${esc(e.agent)}</div>` +
      `<div class="title">${esc(e.title)}</div>` + (e.detail ? `<div class="detail">${esc(e.detail)}</div>` : "");
    ol.appendChild(li);
    await sleep(170);
    li.classList.remove("live");
  }
  $("trace-total").textContent = total ? `${(total / 1000).toFixed(1)} s` : "";
}

/* ---------- boot ---------- */
async function boot() {
  try {
    state.health = await (await fetch("/api/health")).json();
  } catch (e) {
    toast("Can't reach the server.");
    return;
  }
  const h = state.health;
  $("pill-voice").textContent = h.voice ? "voice on" : "voice off";
  $("pill-voice").classList.toggle("on", h.voice);
  $("pill-reader").textContent = h.reader === "model" ? "reader: ai" : "reader: basic";
  $("pill-reader").classList.toggle("on", h.reader === "model");
  const sel = $("lang");
  sel.innerHTML = Object.entries(h.languages).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  sel.value = "hi-IN";
  $("mic-label").textContent = h.voice ? "Tap and speak" : "Voice is off. Type below.";
}

/* ---------- step 1: read the document ---------- */
$("sample").addEventListener("click", async () => {
  const r = await (await fetch("/api/sample")).json();
  $("text").value = r.text;
  state.photo = null;
  $("file-name").textContent = "";
  toast("Sample loaded. It is a made-up patient.");
});
$("photo").addEventListener("change", (e) => {
  state.photo = e.target.files[0] || null;
  $("file-name").textContent = state.photo ? state.photo.name : "";
});

$("read").addEventListener("click", async () => {
  const text = $("text").value.trim();
  if (!text && !state.photo) {
    $("read-hint").textContent = "Paste the summary, upload a photo, or try the sample.";
    $("read-hint").className = "hint err";
    return;
  }
  $("read-hint").textContent = "";
  const btn = $("read");
  btn.disabled = true;
  btn.textContent = "Reading…";
  const fd = new FormData();
  fd.append("text", text);
  if (state.photo) fd.append("image", state.photo);
  try {
    const res = await fetch("/api/document", { method: "POST", body: fd });
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || "Could not read that.");
    state.sid = body.session_id;
    renderAll(body);
    showTrace(body.trace);
    $("hero").hidden = true;
    ["s-check", "s-schedule", "s-ask"].forEach((id) => ($(id).hidden = false));
    $("s-check").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    $("read-hint").textContent = err.message;
    $("read-hint").className = "hint err";
  } finally {
    btn.disabled = false;
    btn.textContent = "Read it";
  }
});

/* ---------- step 2 and 3: render what was understood ---------- */
function renderAll(s) {
  state.data = s;
  renderFacts(s);
  renderFlags(s);
  renderTimeline(s);
}

function renderFacts(s) {
  const d = s.data;
  const pct = Math.round((s.stats.confidence || 0) * 100);
  $("conf").textContent = `${s.stats.passed}/${s.stats.checks} checks · ${pct}% verified`;
  const flagged = new Set(s.flags.map((f) => f.value));
  $("facts").innerHTML = [
    ["Patient", d.patient_name || "Not written"],
    ["Diagnosis", d.diagnosis || "Not written"],
    ["Next visit", d.follow_up.date || "Not written"],
  ].map(([k, v]) => `<div class="fact"><div class="k">${k}</div><div class="v">${esc(v)}</div></div>`).join("");

  const rows = d.medicines.map((m) => {
    const label = `${m.name} ${m.dose}`.trim();
    const often = m.times_per_day ? `${m.times_per_day}× a day` : "unclear";
    const sub = [m.timing.join(", "), m.food !== "any" ? m.food + " food" : ""].filter(Boolean).join(" · ");
    return `<tr class="${flagged.has(label) ? "warn" : ""}"><td class="name">${esc(m.name)}</td><td>${esc(m.dose)}</td>` +
      `<td>${often}<span class="sub">${esc(sub)}</span></td><td>${m.duration_days ? m.duration_days + " days" : "—"}</td></tr>`;
  }).join("");
  $("meds").innerHTML = `<thead><tr><th>Medicine</th><th>Dose</th><th>How often</th><th>For</th></tr></thead><tbody>${rows}</tbody>`;
}

function renderFlags(s) {
  const pending = new Set(s.pending);
  const box = $("flags-box");
  box.hidden = s.flags.length === 0;
  $("flags").innerHTML = s.flags.map((f) => {
    const open = pending.has(f.id);
    return `<li class="${open ? "" : "done"}"><div class="txt"><div class="val">${esc(f.value || f.field)}</div>` +
      `<div class="why">${esc(f.issue)}</div></div>` +
      (open ? `<button class="btn small ghost" data-flag="${esc(f.id)}">It's right</button>` : `<span class="note">Confirmed</span>`) + `</li>`;
  }).join("");
  $("confirm-all").hidden = pending.size === 0;
  $("flags").querySelectorAll("[data-flag]").forEach((b) =>
    b.addEventListener("click", () => confirmFlags([b.dataset.flag])));
}

async function confirmFlags(ids) {
  const res = await fetch("/api/confirm", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sid, flag_ids: ids }),
  });
  renderAll(await res.json());
}
$("confirm-all").addEventListener("click", () => confirmFlags(null));

const TICK = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10"/></svg>';
function renderTimeline(s) {
  const locked = s.pending.length > 0;
  $("lock-hint").hidden = !locked;
  const ol = $("timeline");
  ol.classList.toggle("locked", locked);
  ol.innerHTML = s.schedule.map((slot) => {
    const taken = !!s.adherence[slot.key];
    const items = slot.items.map((i) => `<div>${esc(i.medicine)} ${esc(i.dose)}<span>${esc(i.note)}</span></div>`).join("");
    return `<li class="slot ${taken ? "taken" : ""}" data-key="${slot.key}"><div><div class="time">${slot.time}</div>` +
      `<div class="label">${esc(slot.label)}</div></div><div class="items">${items}</div>` +
      `<button class="tick" aria-label="Mark as taken">${TICK}</button></li>`;
  }).join("");
  ol.querySelectorAll(".slot").forEach((li) =>
    li.querySelector(".tick").addEventListener("click", async () => {
      const taken = !li.classList.contains("taken");
      li.classList.toggle("taken", taken);
      state.data.adherence[li.dataset.key] = taken;
      await fetch("/api/dose", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: state.sid, slot_key: li.dataset.key, taken }),
      });
    }));
}

/* ---------- step 4: ask ---------- */
const CHIPS = [
  "Agli dawai kaun si hai?", "Meri report samjhao", "Kya khana chahiye?", "Mujhe bukhar hai",
  "Maine dawai le li", "Family ko update do", "Insurance claim reject hua",
];
$("chips").innerHTML = CHIPS.map((c) => `<button class="chip">${esc(c)}</button>`).join("");
$("chips").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => ask({ text: b.textContent })));
$("type").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (q) { $("q").value = ""; ask({ text: q }); }
});

async function ask({ text = "", audio = null }) {
  if (!state.sid || state.busy) return;
  state.busy = true;
  $("mic").classList.add("busy");
  $("mic-label").textContent = "Working on it…";
  const fd = new FormData();
  fd.append("session_id", state.sid);
  fd.append("language", $("lang").value);
  fd.append("now", nowHHMM());
  fd.append("text", text);
  if (audio) fd.append("audio", audio, "speech.wav");
  try {
    const res = await fetch("/api/ask", { method: "POST", body: fd });
    const body = await res.json();
    if (body.trace) showTrace(body.trace);
    if (!res.ok) throw new Error(body.error || body.detail || "Something went wrong.");
    $("reply").hidden = false;
    $("said").textContent = body.transcript ? `You: ${body.transcript}` : "";
    $("reply-text").textContent = body.reply;
    $("reply-note").textContent = body.note || body.voice_error || "";
    if (body.state) { state.data = body.state; renderTimeline(body.state); }
    if (body.audio_b64) {
      state.lastAudio = body.audio_b64;
      $("replay").hidden = false;
      play(body.audio_b64);
    } else {
      $("replay").hidden = true;
    }
  } catch (err) {
    toast(err.message, 4500);
  } finally {
    state.busy = false;
    $("mic").classList.remove("busy");
    $("mic-label").textContent = state.health && state.health.voice ? "Tap and speak" : "Voice is off. Type below.";
  }
}

function play(b64) {
  const p = $("player");
  p.src = "data:audio/wav;base64," + b64;
  p.play().catch(() => toast("Tap “Play again” to hear the reply."));
}
$("replay").addEventListener("click", () => state.lastAudio && play(state.lastAudio));

/* ---------- microphone: record 16 kHz mono WAV in the browser ---------- */
let rec = null;
function encodeWav(samples, rate) {
  const buf = new ArrayBuffer(44 + samples.length * 2);
  const v = new DataView(buf);
  const w = (o, s) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, "RIFF"); v.setUint32(4, 36 + samples.length * 2, true); w(8, "WAVE"); w(12, "fmt ");
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  w(36, "data"); v.setUint32(40, samples.length * 2, true);
  let o = 44;
  for (let i = 0; i < samples.length; i++, o += 2) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([v], { type: "audio/wav" });
}

async function startRec() {
  const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 16000 });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  proc.onaudioprocess = (e) => chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
  src.connect(proc);
  proc.connect(ctx.destination);
  rec = { stream, ctx, proc, src, chunks, timer: setTimeout(stopRec, 25000) };
  state.recording = true;
  $("mic").classList.add("rec");
  $("mic-label").textContent = "Listening… tap to send";
}

function stopRec() {
  if (!rec) return;
  const { stream, ctx, proc, src, chunks, timer } = rec;
  clearTimeout(timer);
  proc.disconnect(); src.disconnect();
  stream.getTracks().forEach((t) => t.stop());
  const rate = ctx.sampleRate;
  ctx.close();
  rec = null;
  state.recording = false;
  $("mic").classList.remove("rec");
  const len = chunks.reduce((n, c) => n + c.length, 0);
  const all = new Float32Array(len);
  let off = 0;
  for (const c of chunks) { all.set(c, off); off += c.length; }
  if (len < rate * 0.4) { toast("That was too short. Try again."); $("mic-label").textContent = "Tap and speak"; return; }
  ask({ audio: encodeWav(all, rate) });
}

$("mic").addEventListener("click", async () => {
  if (!state.health || !state.health.voice) { toast("Voice needs a Gnani key. Type your question below."); return; }
  if (state.recording) return stopRec();
  try { await startRec(); }
  catch (e) { toast("Microphone is blocked. Allow it in the browser, or type below."); }
});

boot();
