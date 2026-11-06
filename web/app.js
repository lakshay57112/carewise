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
  renderChips();
  $("mic-label").textContent = h.voice ? "Tap and speak" : "Voice is off. Type below.";
  registerWorker();
  openFromLink();
}

/* ---------- phone notifications: the service worker and what a tap opens ---------- */
function registerWorker() {
  if (!("serviceWorker" in navigator)) return;
  state.worker = navigator.serviceWorker.register("/sw.js").catch(() => null);
  // A notification tapped while Carewise is already open arrives here.
  navigator.serviceWorker.addEventListener("message", (e) => {
    if (e.data && e.data.type === "dose") showDue(e.data.sid, e.data.slot, e.data.lang);
  });
}

// A tapped notification opens /?sid=...&dose=...&lang=... : load that person's day and speak the reminder.
function openFromLink() {
  const q = new URLSearchParams(location.search);
  if (!q.get("sid")) return;
  history.replaceState(null, "", "/");
  showDue(q.get("sid"), q.get("dose"), q.get("lang"));
}

async function showDue(sid, slotKey, lang) {
  if (lang && $("lang").querySelector(`option[value="${lang}"]`)) { $("lang").value = lang; renderChips(); }
  if (state.sid !== sid) {
    const res = await fetch(`/api/state?session_id=${encodeURIComponent(sid)}`);
    if (!res.ok) return toast("That day is no longer on the server. Read your summary again.", 5000);
    state.sid = sid;
    renderAll(await res.json());
    $("hero").hidden = true;
    ["s-check", "s-schedule", "s-remind", "s-ask"].forEach((id) => ($(id).hidden = false));
  }
  if (!slotKey) return;
  const res = await post("/api/reminder/speak", { session_id: sid, slot_key: slotKey, language: $("lang").value });
  if (!res.ok) return toast("That reminder is no longer on your timetable.", 4500);
  const r = await res.json();
  state.due = { slot: slotKey, audio: r.audio_b64 };
  $("due-k").textContent = `Time for your ${r.label.toLowerCase()} medicine`;
  $("due-text").textContent = r.text;
  $("due-play").hidden = !r.audio_b64;
  $("due").hidden = r.taken;
  $("due").scrollIntoView({ behavior: "smooth", block: "start" });
  if (r.audio_b64 && !r.taken) {
    const p = $("player");
    p.src = "data:audio/wav;base64," + r.audio_b64;
    p.play().catch(() => {}); // phones often block this; the Play button is there for that
  }
}
$("due-play").addEventListener("click", () => state.due && state.due.audio && play(state.due.audio));
$("due-taken").addEventListener("click", async () => {
  if (!state.due) return;
  await post("/api/dose", { session_id: state.sid, slot_key: state.due.slot, taken: true });
  $("due").hidden = true;
  const s = await (await fetch(`/api/state?session_id=${state.sid}`)).json();
  state.data = s;
  renderTimeline(s);
  toast("Marked as taken.");
});

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
    ["s-check", "s-schedule", "s-remind", "s-ask"].forEach((id) => ($(id).hidden = false));
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
  refreshReminders();
}

function renderFacts(s) {
  const d = s.data;
  const pct = Math.round((s.stats.confidence || 0) * 100);
  $("conf").textContent = s.stats.checks ? `${s.stats.passed}/${s.stats.checks} checks · ${pct}% verified` : "";
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

/* ---------- step 4: voice reminders on Telegram ---------- */
const post = (url, body) => fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});

async function refreshReminders() {
  if (!state.sid) return;
  await Promise.all([refreshTelegram(), refreshPush()]);
  const h = state.health || {};
  $("remind-none").hidden = !!(h.telegram || h.push);
  const on = !!((state.tg && state.tg.enabled) || (state.push && state.push.here));
  $("remind-state").textContent = on ? "on" : "";
  $("remind-state").classList.toggle("on", on);
}

async function refreshTelegram() {
  const hint = $("remind-hint");
  const show = (id, on) => ($(id).hidden = !on);
  const s = await (await fetch(`/api/telegram/status?session_id=${state.sid}`)).json();
  state.tg = s;
  ["tg-connect", "tg-test", "tg-toggle"].forEach((id) => show(id, false));
  $("tg-block").hidden = !s.configured;
  if (!s.configured) return;

  if (s.blocked) {
    hint.textContent = "Confirm the highlighted items above first, so reminders use the right times.";
    return;
  }
  if (!s.linked) {
    hint.textContent = "Get a voice message on your phone at each medicine time, even when this page is closed. Tap Connect, press Start in Telegram, and come back.";
    const res = await post("/api/telegram/link", { session_id: state.sid, language: $("lang").value });
    if (res.ok) {
      const { link } = await res.json();
      $("tg-connect").href = link;
      show("tg-connect", true);
      waitForLink();
    } else {
      hint.textContent = (await res.json()).detail || "Could not start linking.";
    }
    return;
  }
  const times = s.times.join(", ");
  hint.textContent = s.enabled
    ? `You will get a voice message at ${times}. Tap Taken under it to tick the dose off here.`
    : "Reminders are paused.";
  $("tg-toggle").textContent = s.enabled ? "Turn off" : "Turn on";
  show("tg-test", s.enabled);
  show("tg-toggle", true);
  startStatePoll();
}

/* ---------- step 4b: notification on this phone ---------- */
const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
const isIPhone = () => /iPhone|iPad|iPod/.test(navigator.userAgent);
function keyBytes(b64url) {
  const b64 = (b64url + "=".repeat((4 - (b64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
}
async function thisPhone() {
  if (!pushSupported() || !state.worker || !(await state.worker)) return null;
  const reg = await navigator.serviceWorker.ready;
  return { reg, sub: await reg.pushManager.getSubscription() };
}

async function refreshPush() {
  const hint = $("push-hint");
  const show = (id, on) => ($(id).hidden = !on);
  ["push-on", "push-test", "push-off"].forEach((id) => show(id, false));
  state.push = null;
  $("push-block").hidden = !(state.health && state.health.push);
  if ($("push-block").hidden) return;

  const phone = await thisPhone();
  if (!phone) {
    hint.textContent = isIPhone()
      ? "On iPhone, tap Share, then Add to Home Screen, and open Carewise from there to turn notifications on."
      : "This browser can't show notifications from a website. Chrome on Android can.";
    return;
  }
  const res = await post("/api/push/status", { session_id: state.sid, endpoint: phone.sub ? phone.sub.endpoint : "" });
  const s = await res.json();
  state.push = s;
  if (s.blocked) {
    hint.textContent = "Confirm the highlighted items above first, so reminders use the right times.";
    return;
  }
  if (!s.here) {
    hint.textContent = Notification.permission === "denied"
      ? "Notifications are blocked for this site. Allow them in the browser's site settings, then try again."
      : "A notification at each medicine time, even when Carewise is closed. Tap it and Carewise speaks the reminder. On Android you can tap Taken right on the notification.";
    show("push-on", Notification.permission !== "denied");
    return;
  }
  hint.textContent = `This phone will get a notification at ${s.times.join(", ")}. Tap it to hear the reminder, or tap Taken.`;
  show("push-test", true);
  show("push-off", true);
  startStatePoll();
}

$("push-on").addEventListener("click", async () => {
  const btn = $("push-on");
  btn.disabled = true;
  try {
    if ((await Notification.requestPermission()) !== "granted") {
      toast("Notifications were not allowed.", 4500);
      return refreshPush();
    }
    const { reg, sub: existing } = await thisPhone();
    const { key } = await (await fetch("/api/push/config")).json();
    let sub = existing;
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(key) });
    const res = await post("/api/push/subscribe", { session_id: state.sid, subscription: sub.toJSON(), language: $("lang").value });
    if (!res.ok) throw new Error((await res.json()).detail || "Could not turn them on.");
    toast("Notifications are on for this phone.");
  } catch (err) {
    toast(err.message || "Could not turn notifications on.", 5000);
  } finally {
    btn.disabled = false;
    refreshReminders();
  }
});
$("push-test").addEventListener("click", async () => {
  $("push-test").disabled = true;
  const res = await post("/api/push/test", { session_id: state.sid, language: $("lang").value });
  $("push-test").disabled = false;
  const body = await res.json();
  toast(res.ok ? "Sent. It should appear in a few seconds." : body.detail || "Could not send it.", 4500);
});
$("push-off").addEventListener("click", async () => {
  const phone = await thisPhone();
  if (phone && phone.sub) {
    await post("/api/push/unsubscribe", { session_id: state.sid, endpoint: phone.sub.endpoint });
    await phone.sub.unsubscribe();
  }
  refreshReminders();
});

let linkTimer = null;
function waitForLink() {
  clearInterval(linkTimer);
  let tries = 0;
  linkTimer = setInterval(async () => {
    tries += 1;
    const s = await (await fetch(`/api/telegram/status?session_id=${state.sid}`)).json();
    if (s.linked) { clearInterval(linkTimer); toast("Telegram connected."); refreshReminders(); }
    else if (tries > 60) clearInterval(linkTimer); // stop after about 3 minutes
  }, 3000);
}

// Once linked, keep the timetable in step with doses ticked off on the phone.
let stateTimer = null;
function startStatePoll() {
  if (stateTimer) return;
  stateTimer = setInterval(async () => {
    try {
      const s = await (await fetch(`/api/state?session_id=${state.sid}`)).json();
      state.data = s;
      renderTimeline(s);
    } catch (e) {}
  }, 8000);
}

$("tg-test").addEventListener("click", async () => {
  $("tg-test").disabled = true;
  const res = await post("/api/telegram/test", { session_id: state.sid, language: $("lang").value });
  $("tg-test").disabled = false;
  const body = await res.json();
  if (!res.ok) return toast(body.detail || "Could not send it.", 4500);
  toast(body.voice ? "Sent. Check Telegram on your phone." : "Sent as text. Voice needs the Gnani key on the server.", 4500);
});
$("tg-toggle").addEventListener("click", async () => {
  const res = await post("/api/telegram/settings", { session_id: state.sid, enabled: !(state.tg && state.tg.enabled) });
  if (res.ok) refreshReminders();
});

/* ---------- step 5: ask ---------- */
// Quick questions in every language. Order matches CHIP_INTENTS. Each chip sends its intent
// directly, so it works in any language without keyword matching.
const CHIP_INTENTS = ["next_dose", "explain", "diet", "symptoms", "took_dose", "family", "insurance"];
const CHIP_HINTS = [
  "What is my next medicine?", "Explain my summary", "What should I eat?", "I have a fever",
  "I took my medicine", "Update my family", "My insurance claim was rejected",
];
const CHIPS = {
  "en-IN": ["What is my next medicine?", "Explain my summary", "What should I eat?", "I have a fever",
    "I took my medicine", "Update my family", "My insurance claim was rejected"],
  "hi-IN": ["अगली दवा कौन सी है?", "मेरी रिपोर्ट समझाओ", "मुझे क्या खाना चाहिए?", "मुझे बुखार है",
    "मैंने दवा ले ली", "परिवार को अपडेट दो", "बीमा क्लेम रिजेक्ट हो गया"],
  "bn-IN": ["আমার পরের ওষুধ কোনটা?", "আমার রিপোর্ট বুঝিয়ে দাও", "আমি কী খাব?", "আমার জ্বর হয়েছে",
    "আমি ওষুধ খেয়েছি", "পরিবারকে খবর দাও", "আমার বিমার দাবি বাতিল হয়েছে"],
  "gu-IN": ["મારી આગળની દવા કઈ છે?", "મારો રિપોર્ટ સમજાવો", "મારે શું ખાવું જોઈએ?", "મને તાવ છે",
    "મેં દવા લઈ લીધી", "પરિવારને અપડેટ આપો", "મારો વીમા દાવો નામંજૂર થયો"],
  "kn-IN": ["ನನ್ನ ಮುಂದಿನ ಔಷಧಿ ಯಾವುದು?", "ನನ್ನ ವರದಿಯನ್ನು ವಿವರಿಸಿ", "ನಾನು ಏನು ತಿನ್ನಬೇಕು?", "ನನಗೆ ಜ್ವರ ಇದೆ",
    "ನಾನು ಔಷಧಿ ತೆಗೆದುಕೊಂಡೆ", "ಕುಟುಂಬಕ್ಕೆ ಮಾಹಿತಿ ನೀಡಿ", "ನನ್ನ ವಿಮಾ ಕ್ಲೈಮ್ ತಿರಸ್ಕರಿಸಲಾಗಿದೆ"],
  "ml-IN": ["എന്റെ അടുത്ത മരുന്ന് ഏതാണ്?", "എന്റെ റിപ്പോർട്ട് വിശദീകരിക്കുക", "ഞാൻ എന്ത് കഴിക്കണം?", "എനിക്ക് പനി ഉണ്ട്",
    "ഞാൻ മരുന്ന് കഴിച്ചു", "കുടുംബത്തെ അറിയിക്കുക", "എന്റെ ഇൻഷുറൻസ് ക്ലെയിം നിരസിച്ചു"],
  "mr-IN": ["माझे पुढचे औषध कोणते?", "माझा रिपोर्ट समजावून सांगा", "मी काय खावे?", "मला ताप आहे",
    "मी औषध घेतले", "कुटुंबाला अपडेट द्या", "माझा विमा दावा नाकारला गेला"],
  "pa-IN": ["ਮੇਰੀ ਅਗਲੀ ਦਵਾਈ ਕਿਹੜੀ ਹੈ?", "ਮੇਰੀ ਰਿਪੋਰਟ ਸਮਝਾਓ", "ਮੈਨੂੰ ਕੀ ਖਾਣਾ ਚਾਹੀਦਾ ਹੈ?", "ਮੈਨੂੰ ਬੁਖਾਰ ਹੈ",
    "ਮੈਂ ਦਵਾਈ ਲੈ ਲਈ", "ਪਰਿਵਾਰ ਨੂੰ ਅਪਡੇਟ ਦਿਓ", "ਮੇਰਾ ਬੀਮਾ ਕਲੇਮ ਰੱਦ ਹੋ ਗਿਆ"],
  "ta-IN": ["எனது அடுத்த மருந்து எது?", "என் அறிக்கையை விளக்குங்கள்", "நான் என்ன சாப்பிட வேண்டும்?", "எனக்கு காய்ச்சல் உள்ளது",
    "நான் மருந்து எடுத்துக்கொண்டேன்", "குடும்பத்திற்குத் தகவல் கொடுங்கள்", "என் காப்பீட்டு கோரிக்கை நிராகரிக்கப்பட்டது"],
  "te-IN": ["నా తదుపరి మందు ఏది?", "నా రిపోర్ట్ వివరించండి", "నేను ఏమి తినాలి?", "నాకు జ్వరం ఉంది",
    "నేను మందు వేసుకున్నాను", "కుటుంబానికి అప్‌డేట్ ఇవ్వండి", "నా బీమా క్లెయిమ్ తిరస్కరించబడింది"],
};
function renderChips() {
  const labels = CHIPS[$("lang").value] || CHIPS["en-IN"];
  $("chips").innerHTML = labels.map((c, i) => `<button class="chip" data-i="${i}">${esc(c)}</button>`).join("");
  $("chips").querySelectorAll(".chip").forEach((b) =>
    b.addEventListener("click", () => {
      const i = Number(b.dataset.i);
      ask({ text: b.textContent, intent: CHIP_INTENTS[i], hint: CHIP_HINTS[i] });
    }));
}
$("lang").addEventListener("change", () => {
  renderChips();
  // Reminders follow the language picked here once Telegram or phone notifications are on.
  if (state.sid && state.tg && state.tg.linked) post("/api/telegram/settings", { session_id: state.sid, language: $("lang").value });
  if (state.sid && state.push && state.push.here) post("/api/push/settings", { session_id: state.sid, language: $("lang").value });
});
$("type").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("q").value.trim();
  if (q) { $("q").value = ""; ask({ text: q }); }
});

async function ask({ text = "", audio = null, intent = "", hint = "" }) {
  if (!state.sid || state.busy) return;
  state.busy = true;
  $("mic").classList.add("busy");
  $("mic-label").textContent = "Working on it…";
  const fd = new FormData();
  fd.append("session_id", state.sid);
  fd.append("language", $("lang").value);
  fd.append("now", nowHHMM());
  fd.append("text", text);
  if (intent) { fd.append("intent", intent); fd.append("hint", hint); }
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
