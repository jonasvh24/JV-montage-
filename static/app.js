/* nullmontage UI */
const $ = (s) => document.querySelector(s);
const api = async (path, opts = {}) => {
  const r = await fetch(path, opts);
  if (!r.ok) { let t = await r.text(); try { t = JSON.parse(t).detail || t; } catch {} throw new Error(t); }
  return r.json();
};
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

let job = null, meta = null, params = null, analysis = null, edl = null, es = null;

// ---- field definitions ----------------------------------------------------
const FIELDS = {
  rhythm: [
    ["intro_card", "Intro card (always first; empty = none)", "text"],
    ["intro_hold_beats", "Intro card hold (beats)", "number", [1, 32, 1]],
    ["pacing", "Pacing", "select", { constant: "Constant", accelerate: "Accelerate (climax)", decelerate: "Decelerate", follow_energy: "Follow energy", pulse: "Pulse (long-short)" }],
    ["accel_curve", "Acceleration curve (gentle → brutal)", "range", [0, 1, 0.05]],
    ["song_speed_ramp", "Song speeds up to × by the end (accelerate pacing; 1 = off)", "number", [1, 2, 0.05]],
    ["min_shot_ms", "Min shot (ms)", "number", [40, 5000, 10]],
    ["start_offset_beats", "Start offset (beats)", "number", [0, 64, 1]],
    ["end_on_downbeat", "End on a downbeat", "check"],
  ],
  structure: [
    ["order", "Photo order", "select", { as_uploaded: "As uploaded", shuffle: "Shuffle (seeded)", reverse: "Reverse", alternate_halves: "A/B collision (alternate halves)", brightness_sort: "Dark → light" }],
    ["motif_repeat", "Motif: first photo every N bars (0 off)", "number", [0, 3, 1]],
    ["hold_first_beats", "Hold first shot (+beats)", "number", [0, 32, 1]],
    ["hold_last_beats", "Hold last shot (+beats)", "number", [0, 32, 1]],
    ["seed", "Seed", "number", [0, 99999, 1]],
  ],
  effects: [
    ["effects.punch_zoom", "Punch zoom on cut", "range", [0, 1, 0.05]],
    ["effects.ken_burns", "Ken Burns drift", "range", [0, 1, 0.05]],
    ["effects.flash_white", "White flash", "select", { off: "Off", hard_beats: "Hard beats only (strongest hits / drops)", downbeats: "On downbeats", every_cut: "Every cut", climax_only: "Climax only" }],
    ["effects.flash_black", "Black flash", "select", { off: "Off", hard_beats: "Hard beats only (strongest hits / drops)", downbeats: "On downbeats", every_cut: "Every cut", climax_only: "Climax only" }],
    ["effects.hard_beat_threshold", "Hard-beat threshold (higher = fewer, harder hits)", "range", [0, 1, 0.05]],
    ["effects.flash_frames", "Flash length (frames)", "number", [1, 4, 1]],
    ["effects.strobe", "Strobe finale", "check"],
    ["effects.strobe_section_pct", "Strobe: last % of timeline", "range", [0, 1, 0.05]],
    ["effects.shake", "Shake on accents", "range", [0, 1, 0.05]],
    ["effects.bw", "Black & white", "check"],
    ["effects.contrast", "Contrast crush", "range", [0, 1, 0.05]],
    ["effects.grain", "Film grain", "range", [0, 1, 0.05]],
    ["effects.vignette", "Vignette", "range", [0, 1, 0.05]],
    ["effects.invert_on_accent", "Invert on strongest downbeats", "check"],
    ["effects.dissolve_ms", "Dissolve (ms, 0 = hard cuts)", "number", [0, 1000, 10]],
  ],
  intertitles: [
    ["intertitles.enabled", "Intertitles on", "check"],
    ["intertitles.cards", "Cards (one per line)", "textarea"],
    ["intertitles.placement", "Placement", "select", { opening: "Opening", closing: "Closing", opening_and_closing: "Opening + closing", between_sections: "Between sections", every_n_bars: "Every N bars" }],
    ["intertitles.every_n_bars", "N bars", "number", [1, 64, 1]],
    ["intertitles.card_hold_beats", "Card hold (beats)", "number", [1, 32, 1]],
    ["intertitles.font_style", "Card style", "select", { soviet_constructivist: "Constructivist (red/cream/black)", plain: "Plain white on black" }],
  ],
  output: [
    ["__length", "How long should the montage be?", "length"],
    ["__fill", "Filling that time with your shots", "fill"],
    ["cut_unit", "Pictures per beat", "select", { bar: "¼ – one picture every bar (4 beats)", two_beats: "½ – one picture every 2 beats", beat: "1 – one picture per beat", half_beat: "2 – two pictures per beat", quarter_beat: "4 – four pictures per beat" }],
    ["output.aspect", "Frame", "select", { "9:16": "9:16 vertical 1080x1920", "16:9": "16:9 landscape 1920x1080", "4:5": "4:5 portrait 1080x1350", "1:1": "1:1 square 1080x1080" }],
    ["output.container", "Container", "select", { mp4: "MP4", mov: "MOV" }],
    ["output.codec", "Codec", "select", { h264: "H.264 (mp4/mov)", prores_422: "ProRes 422 (mov only)" }],
    ["output.fps", "Frame rate", "select", { 30: "30 fps", 25: "25 fps", 24: "24 fps" }],
    ["output.crf", "H.264 quality (CRF, lower = better)", "number", [10, 35, 1]],
    ["output.audio_fade_out_ms", "Audio fade-out (ms, when the song is trimmed)", "number", [0, 10000, 100]],
    ["output.audio_start_s", "Song from (s)", "number", [0, 7200, 0.1]],
    ["output.audio_end_s", "Song until (s, 0 = to the end)", "number", [0, 7200, 0.1]],
    ["output.speed", "Song speed × (1 = normal, 1.5 = 50% faster)", "number", [0.5, 3, 0.05]],
    ["output.keep_pitch", "Keep pitch when speeding up", "check"],
  ],
};
const get = (o, p) => p.split(".").reduce((a, k) => (a == null ? a : a[k]), o);
const set = (o, p, v) => { const ks = p.split("."); let n = o; ks.slice(0, -1).forEach((k) => (n = n[k])); n[ks.at(-1)] = v; };

function buildPanels() {
  for (const [panel, fields] of Object.entries(FIELDS)) {
    const host = $(`#panel-${panel}`);
    host.innerHTML = "";
    for (const [path, label, type, opt] of fields) {
      const lab = document.createElement("label");
      const id = "f_" + path.replace(/\./g, "_");
      let ctl;
      if (type === "length") { host.appendChild(buildLengthPicker(lab, label)); continue; }
      if (type === "fill") { host.appendChild(buildFillPicker(lab, label)); continue; }
      if (type === "select") {
        ctl = document.createElement("select");
        for (const [v, t] of Object.entries(opt)) { const o = document.createElement("option"); o.value = v; o.textContent = t; ctl.appendChild(o); }
      } else if (type === "range" || type === "number") {
        ctl = document.createElement("input"); ctl.type = type; [ctl.min, ctl.max, ctl.step] = opt;
      } else if (type === "check") {
        ctl = document.createElement("input"); ctl.type = "checkbox"; lab.classList.add("inline");
      } else if (type === "textarea") {
        ctl = document.createElement("textarea"); ctl.rows = 3;
      } else if (type === "text") {
        ctl = document.createElement("input"); ctl.type = "text";
      }
      ctl.id = id; ctl.dataset.path = path; ctl.dataset.type = type;
      const span = document.createElement("span"); span.className = "lbl"; span.textContent = label;
      if (type === "range") { const v = document.createElement("span"); v.className = "val"; v.id = id + "_v"; span.appendChild(v); }
      if (type === "check") { lab.appendChild(ctl); lab.appendChild(span); } else { lab.appendChild(span); lab.appendChild(ctl); }
      host.appendChild(lab);
      ctl.addEventListener("input", () => { readForm(); if (type === "range") $("#" + id + "_v").textContent = ctl.value; });
      ctl.addEventListener("change", () => {
        readForm();
        saveLocal();
      });
    }
  }
}

// ---- montage length picker (Output panel) ---------------------------------
const LENGTH_PRESETS = [15, 30, 45, 60, 90, 120, 180];
function buildLengthPicker(lab, label) {
  lab.classList.add("length");
  const span = document.createElement("span"); span.className = "lbl"; span.textContent = label;
  const sel = document.createElement("select"); sel.id = "f_length";
  const add = (v, t) => { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o); };
  add("photos", "Until every photo has been shown once (song trimmed)");
  add("song", "The whole song");
  for (const n of LENGTH_PRESETS) add(String(n), n < 60 ? `${n} seconds` : n % 60 ? `${Math.floor(n / 60)} min ${n % 60} s` : `${n / 60} minute${n > 60 ? "s" : ""}`);
  add("custom", "Custom… (type the seconds)");
  const row = document.createElement("div"); row.className = "lenrow";
  const num = document.createElement("input"); num.type = "number"; num.id = "f_length_s"; num.min = 3; num.max = 1800; num.step = 1;
  const unit = document.createElement("span"); unit.className = "val"; unit.textContent = "seconds";
  row.appendChild(num); row.appendChild(unit);
  lab.appendChild(span); lab.appendChild(sel); lab.appendChild(row);
  sel.addEventListener("change", () => {
    if (sel.value === "photos") params.fit_mode = "fit_to_photos";
    else if (sel.value === "song") params.fit_mode = "fit_to_song";
    else {
      params.fit_mode = "fit_to_duration";
      if (sel.value !== "custom") params.target_duration_s = +sel.value;
    }
    syncLength(); saveLocal();
    if (sel.value === "custom") num.focus();
  });
  const onNum = () => {
    const v = Math.min(1800, Math.max(3, +num.value || 0));
    if (!num.value) return;
    params.fit_mode = "fit_to_duration"; params.target_duration_s = v;
    sel.value = LENGTH_PRESETS.includes(v) ? String(v) : "custom";
    row.classList.remove("hidden"); saveLocal();
  };
  num.addEventListener("input", onNum);
  num.addEventListener("change", () => { onNum(); syncLength(); });
  return lab;
}
// ---- how the set length is filled -----------------------------------------
const FILL_MODES = {
  every: "Every shot exactly once, spread over the time (★ shots and key moments hold longer)",
  repeat: "Repeat shots as needed (shots flagged 1× are never repeated)",
  hold: "Each shot once, holding longer across cuts (no repeats, song fully covered)",
};
function buildFillPicker(lab, label) {
  const span = document.createElement("span"); span.className = "lbl"; span.textContent = label;
  const sel = document.createElement("select"); sel.id = "f_fill";
  for (const [v, t] of Object.entries(FILL_MODES)) { const o = document.createElement("option"); o.value = v; o.textContent = t; sel.appendChild(o); }
  lab.appendChild(span); lab.appendChild(sel);
  sel.addEventListener("change", () => {
    params.use_every_shot = sel.value === "every";
    params.allow_repeats = sel.value !== "hold";
    saveLocal();
  });
  return lab;
}
function syncFill() {
  const sel = $("#f_fill"); if (!sel) return;
  sel.value = params.use_every_shot ? "every" : params.allow_repeats === false ? "hold" : "repeat";
  sel.parentElement.classList.toggle("hidden", params.fit_mode === "fit_to_photos");
}
function syncLength() {
  const sel = $("#f_length"), num = $("#f_length_s"), row = num.parentElement;
  if (!sel) return;
  if (params.fit_mode === "fit_to_photos") sel.value = "photos";
  else if (params.fit_mode === "fit_to_song") sel.value = "song";
  else sel.value = LENGTH_PRESETS.includes(+params.target_duration_s) ? String(+params.target_duration_s) : "custom";
  num.value = params.target_duration_s ?? 30;
  row.classList.toggle("hidden", params.fit_mode !== "fit_to_duration");
  syncFill();
}

function fillForm() {
  syncLength();
  document.querySelectorAll("[data-path]").forEach((ctl) => {
    const v = get(params, ctl.dataset.path);
    const t = ctl.dataset.type;
    if (t === "check") ctl.checked = !!v;
    else if (t === "textarea") ctl.value = (v || []).join("\n");
    else if (t === "text") ctl.value = v ?? "";
    else ctl.value = v;
    if (t === "range") $("#" + ctl.id + "_v").textContent = v;
  });
  $("#preset").value = params.style_preset;
  $("#presetDesc").textContent = meta.descriptions[params.style_preset] || "";
}

function readForm() {
  document.querySelectorAll("[data-path]").forEach((ctl) => {
    const t = ctl.dataset.type;
    let v;
    if (t === "check") v = ctl.checked;
    else if (t === "text") v = ctl.value;
    else if (t === "textarea") v = ctl.value.split("\n").map((s) => s.trim()).filter(Boolean);
    else if (t === "select") { v = ctl.value; if (/^\d+$/.test(v) && ctl.dataset.path === "output.fps") v = +v; }
    else v = +ctl.value;
    set(params, ctl.dataset.path, v);
  });
  if (params.output.codec === "prores_422") { params.output.container = "mov"; $("#f_output_container").value = "mov"; }
}

function saveLocal() { try { localStorage.setItem("nullmontage.params", JSON.stringify(params)); } catch {} }

// ---- job + uploads --------------------------------------------------------
async function ensureJob() {
  const url = new URL(location.href);
  const jid = url.searchParams.get("job");
  if (jid) { try { job = await api(`/api/jobs/${jid}`); } catch { job = null; } }
  if (!job) { job = await api("/api/jobs", { method: "POST" }); url.searchParams.set("job", job.id); history.replaceState(null, "", url); }
  $("#jobId").textContent = job.id;
  await loadShotNotes();
  renderThumbs();
  if (job.audio) { $("#audioInfo").textContent = job.audio; await songLoaded(); }
  if (job.status === "rendering") watch();
  if (job.output) showOutput();
}

function renderThumbs() {
  $("#photoCount").textContent = job.photos.length;
  const host = $("#thumbs"); host.innerHTML = "";
  host.classList.toggle("picking", picking);
  $("#pickOrder").textContent = picking ? "apply this order" : "pick order by clicking";
  $("#pickCancel").classList.toggle("hidden", !picking);
  if (picking) $("#orderStatus").textContent = `click shots in the order you want them: ${picked.length} of ${job.photos.length} picked` + (picked.length ? " · click a picked shot to unpick it" : "");
  job.photos.forEach((name, i) => {
    const d = document.createElement("div"); d.className = "thumb"; d.draggable = !picking; d.dataset.name = name;
    const isVid = /\.(mov|mp4|m4v|avi|mkv|webm|mts|m2ts)$/i.test(name);
    const pk = picked.indexOf(name);
    if (pk >= 0) d.classList.add("picked");
    d.innerHTML = `<img src="/api/jobs/${job.id}/thumbs/${encodeURIComponent(name)}" loading="lazy" decoding="async">`
      + `<span class="n" title="click to type a position">${pk >= 0 ? pk + 1 : i + 1}</span>`
      + (isVid ? '<span class="v">CLIP</span>' : "")
      + `<button class="x" title="remove">×</button>`
      + `<span class="mv"><button class="l" title="move earlier">◀</button><button class="r" title="move later">▶</button></span>`
      + tagsHtml(name, isVid);
    const note = shotNotes[name];
    if (note) { d.title = [note.description, note.key_why, `importance ${note.importance}/5`].filter(Boolean).join(" · "); if (note.must_include) d.classList.add("must"); }
    for (const b of d.querySelectorAll(".tags button")) b.onclick = (e) => { e.stopPropagation(); tagClick(d, name, b.dataset.tag); };
    d.querySelector(".x").onclick = async (e) => { e.stopPropagation(); job = await api(`/api/jobs/${job.id}/photos/${encodeURIComponent(name)}`, { method: "DELETE" }); picked = picked.filter((p) => p !== name); renderThumbs(); };
    d.querySelector(".l").onclick = (e) => { e.stopPropagation(); moveTo(name, i - 1); };
    d.querySelector(".r").onclick = (e) => { e.stopPropagation(); moveTo(name, i + 1); };
    d.querySelector(".n").onclick = (e) => { e.stopPropagation(); if (picking) togglePick(name); else editPosition(d, name, i); };
    d.onclick = () => { if (picking) togglePick(name); };
    d.addEventListener("dragstart", () => { d.classList.add("dragging"); dragSrc = name; });
    d.addEventListener("dragend", () => d.classList.remove("dragging"));
    d.addEventListener("dragover", (e) => { e.preventDefault(); d.classList.add("over"); });
    d.addEventListener("dragleave", () => d.classList.remove("over"));
    d.addEventListener("drop", async (e) => {
      e.preventDefault(); d.classList.remove("over");
      if (!dragSrc || dragSrc === name) return;
      const order = job.photos.filter((p) => p !== dragSrc);
      order.splice(order.indexOf(name), 0, dragSrc);
      await applyOrder(order, "moved by drag");
    });
    host.appendChild(d);
  });
}
let dragSrc = null;

// ---- AI shot analysis: key moments, must-include, once ---------------------
let shotNotes = {}, shotSummary = null;
async function loadShotNotes() {
  try { const r = await api(`/api/jobs/${job.id}/shots`); shotNotes = r.shots || {}; shotSummary = r.source ? r : null; } catch { shotNotes = {}; }
  showShotSummary();
}
function tagsHtml(name, isVid) {
  const n = shotNotes[name] || {};
  const key = isVid ? `<button data-tag="key" class="${n.key_at != null ? "on" : ""}" title="${n.key_why ? n.key_why + " — " : ""}key moment: click to type the second">${n.key_at != null ? "key " + (+n.key_at).toFixed(1) + "s" : "key ?"}</button>` : "";
  return `<span class="tags"><button data-tag="must" class="${n.must_include ? "on" : ""}" title="must be in the final cut">★</button><button data-tag="once" class="${n.once ? "on" : ""}" title="never repeat this shot">1×</button>${key}</span>`;
}
async function patchShot(name, body) {
  const r = await api(`/api/jobs/${job.id}/shots/${encodeURIComponent(name)}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  shotNotes[name] = r;
}
function tagClick(d, name, tag) {
  const n = shotNotes[name] || {};
  if (tag === "must") return patchShot(name, { must_include: !n.must_include }).then(renderThumbs);
  if (tag === "once") return patchShot(name, { once: !n.once }).then(renderThumbs);
  // key moment: inline seconds input
  const tags = d.querySelector(".tags");
  const inp = document.createElement("input"); inp.type = "number"; inp.min = 0; inp.step = 0.1; inp.className = "keyin"; inp.value = n.key_at != null ? (+n.key_at).toFixed(1) : ""; inp.placeholder = "sec";
  if (n.duration_s) inp.max = n.duration_s;
  tags.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const finish = async (ok) => { if (done) return; done = true; if (ok && inp.value !== "") await patchShot(name, { key_at: +inp.value }); else if (ok) await patchShot(name, { clear_key: true }); renderThumbs(); };
  inp.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); e.stopPropagation(); };
  inp.onblur = () => finish(true);
  inp.onclick = (e) => e.stopPropagation();
}
async function analyzeShots() {
  if (!job.photos.length) { $("#shotsStatus").textContent = "upload photos or clips first"; return; }
  readForm();
  $("#aiShots").disabled = true;
  $("#shotsStatus").textContent = meta.claude ? "Claude is looking at every photo and clip…" : "finding each clip's busiest moment (no Anthropic key)…";
  try {
    const r = await post(`/api/jobs/${job.id}/photos/analyze`, { notes: $("#notes").value, style_preset: params.style_preset });
    shotNotes = r.shots || {}; shotSummary = r;
    $("#shotsStatus").textContent = r.source === "claude" ? `analysed by ${r.model}` : "motion heuristic (no AI judgement)";
    showShotSummary(); renderThumbs();
  } catch (e) { $("#shotsStatus").textContent = "error: " + e.message; }
  $("#aiShots").disabled = false;
}
function showShotSummary() {
  const box = $("#shotsResult");
  if (!shotSummary || !Object.keys(shotNotes).length) { box.classList.add("hidden"); return; }
  const names = job.photos.filter((n) => shotNotes[n]);
  const must = names.filter((n) => shotNotes[n].must_include);
  const keys = names.filter((n) => shotNotes[n].key_at != null);
  const once = names.filter((n) => shotNotes[n].once);
  const pos = (n) => job.photos.indexOf(n) + 1;
  const li = keys.map((n) => `<li>#${pos(n)} ${shotNotes[n].kind === "clip" ? "clip" : ""} key moment at ${(+shotNotes[n].key_at).toFixed(1)} s${shotNotes[n].key_why ? ": " + shotNotes[n].key_why : ""}${shotNotes[n].description ? " — " + shotNotes[n].description : ""}</li>`).join("");
  box.innerHTML = `<p>${shotSummary.summary || ""}</p><p><b>${must.length}</b> must-include (★${must.length ? ": #" + must.map(pos).join(", #") : ""}) · <b>${keys.length}</b> key moments · <b>${once.length}</b> never repeated (1×)</p>` + (li ? `<ul class="shotlist">${li}</ul>` : "");
  box.classList.remove("hidden");
}

// ---- manual shot order -------------------------------------------------------
let picking = false, picked = [];
async function applyOrder(order, what) {
  const prev = job.photos.slice();
  if (order.every((n, i) => n === prev[i])) return;
  job = await post(`/api/jobs/${job.id}/photos/order`, { photos: order });
  orderBackup = prev;
  $("#undoOrder").classList.remove("hidden"); $("#orderResult").classList.add("hidden");
  $("#orderStatus").textContent = what;
  renderThumbs();
}
function moveTo(name, pos) {
  pos = Math.max(0, Math.min(job.photos.length - 1, pos));
  const order = job.photos.filter((p) => p !== name);
  order.splice(pos, 0, name);
  return applyOrder(order, `moved to position ${pos + 1}`);
}
function editPosition(d, name, i) {
  const badge = d.querySelector(".n");
  const inp = document.createElement("input"); inp.type = "number"; inp.min = 1; inp.max = job.photos.length; inp.value = i + 1; inp.className = "pos";
  badge.replaceWith(inp); inp.focus(); inp.select();
  let done = false;
  const finish = (ok) => { if (done) return; done = true; const v = parseInt(inp.value, 10); if (ok && v >= 1 && v <= job.photos.length && v !== i + 1) moveTo(name, v - 1); else renderThumbs(); };
  inp.onkeydown = (e) => { if (e.key === "Enter") finish(true); if (e.key === "Escape") finish(false); e.stopPropagation(); };
  inp.onblur = () => finish(true);
  inp.onclick = (e) => e.stopPropagation();
}
function togglePick(name) {
  const k = picked.indexOf(name);
  if (k >= 0) picked.splice(k, 1); else picked.push(name);
  renderThumbs();
}
async function pickOrderClick() {
  if (!picking) {
    if (job.photos.length < 2) { $("#orderStatus").textContent = "upload at least two photos or clips first"; return; }
    picking = true; picked = []; renderThumbs(); return;
  }
  const rest = job.photos.filter((p) => !picked.includes(p));
  const order = picked.concat(rest);
  const n = picked.length;
  picking = false; picked = [];
  if (n === 0) { $("#orderStatus").textContent = "nothing picked, order unchanged"; renderThumbs(); return; }
  await applyOrder(order, n === job.photos.length ? "order set by your picks" : `first ${n} set by your picks, the rest follow in their old order`);
  renderThumbs();
}
function pickCancel() { picking = false; picked = []; $("#orderStatus").textContent = "pick cancelled"; renderThumbs(); }

function uploadWithProgress(url, fd, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", url);
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => (x.status < 300 ? resolve(JSON.parse(x.responseText)) : reject(new Error(x.responseText)));
    x.onerror = () => reject(new Error("upload failed"));
    x.send(fd);
  });
}
async function uploadPhotos(files) {
  const list = [...files].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  if (!list.length) return;
  const st = $("#uploadStatus");
  // upload in batches of 10 so huge sets don't sit in one giant request
  const rejected = [];
  for (let i = 0; i < list.length; i += 10) {
    const batch = list.slice(i, i + 10);
    const fd = new FormData(); batch.forEach((f) => fd.append("files", f));
    const mb = (batch.reduce((a, f) => a + f.size, 0) / 1048576).toFixed(1);
    try {
      job = await uploadWithProgress(`/api/jobs/${job.id}/photos`, fd, (p) => { st.textContent = `uploading ${i + 1}–${Math.min(i + 10, list.length)} of ${list.length} (${mb} MB) ${(p * 100).toFixed(0)}%`; });
    } catch (e) { st.textContent = "upload error: " + e.message; return; }
    rejected.push(...(job.rejected || []));
    renderThumbs();
  }
  st.textContent = `${list.length - rejected.length} file(s) added`;
  if (rejected.length) st.textContent += ` · ${rejected.length} rejected: ` + rejected.map((r) => `${r.file} (${r.reason})`).join("; ");
  if (list.length > rejected.length) analyzeShots();
}
async function uploadAudio(file) {
  const fd = new FormData(); fd.append("file", file);
  $("#audioInfo").textContent = "uploading…";
  job = await api(`/api/jobs/${job.id}/audio`, { method: "POST", body: fd });
  $("#audioInfo").textContent = job.audio;
  fullAnalysis = null;
  await songLoaded();
}
async function loadAnalysis() {
  $("#audioStats").innerHTML = "analyzing beats…";
  try {
    const o = params.output;
    analysis = await api(`/api/jobs/${job.id}/analysis?audio_start_s=${o.audio_start_s || 0}&audio_end_s=${o.audio_end_s || 0}&speed=${o.speed || 1}&keep_pitch=${o.keep_pitch !== false}`);
    const s = analysis.summary;
    $("#audioStats").innerHTML = `<span><b>${s.bpm}</b> BPM</span><span><b>${s.duration_s}</b> s</span><span><b>${s.n_beats}</b> beats</span><span><b>${s.n_bars}</b> bars</span><span>peak section <b>${s.sections.peak[0]}–${s.sections.peak[1]} s</b></span>`;
    drawTimeline();
  } catch (e) { $("#audioStats").textContent = "analysis failed: " + e.message; }
}

// ---- which part of the song ----------------------------------------------------
let fullAnalysis = null, stripDrag = null, playingPart = false, rangeTimer = null;
const fmt = (t) => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, "0")}.${Math.floor((t % 1) * 10)}`;
const songDur = () => (fullAnalysis ? fullAnalysis.duration : ($("#player").duration || 0));
const songRange = () => { const d = songDur(); const a = Math.max(0, +params.output.audio_start_s || 0); let b = +params.output.audio_end_s || 0; if (!(b > a) || (d && b > d)) b = d || 0; return [Math.min(a, d || a), b]; };
async function songLoaded() {
  // whole-song analysis for the picker strip (cached per job server-side), then the chosen part for the plan
  const pl = $("#player"); pl.src = `/api/jobs/${job.id}/audio?v=${Date.now()}`;
  $("#songPick").classList.remove("hidden");
  try { fullAnalysis = await api(`/api/jobs/${job.id}/analysis?audio_start_s=0&audio_end_s=0&speed=1`); } catch { fullAnalysis = null; }
  if (fullAnalysis && (params.output.audio_start_s >= fullAnalysis.duration - 1 || params.output.audio_end_s > fullAnalysis.duration)) {
    // a range saved for a previous (longer) song does not apply to this one
    params.output.audio_start_s = 0; params.output.audio_end_s = 0; fillForm(); saveLocal();
  }
  syncRangeUI();
  await loadAnalysis();
}
function setRange(a, b, src) {
  const d = songDur();
  a = Math.max(0, Math.round(a * 10) / 10); b = Math.round(b * 10) / 10;
  if (d) { a = Math.min(a, Math.max(0, d - 1)); if (b >= d - 0.05) b = 0; }
  if (b && b <= a + 0.5) { if (src === "start") b = 0; else a = Math.max(0, b - 0.5); }
  params.output.audio_start_s = a; params.output.audio_end_s = b;
  fillForm(); saveLocal(); syncRangeUI();
  clearTimeout(rangeTimer); rangeTimer = setTimeout(() => job.audio && loadAnalysis(), 400);
}
function syncRangeUI() {
  const [a, b] = songRange(), d = songDur();
  $("#songFrom").value = a; $("#songTo").value = params.output.audio_end_s || "";
  $("#songTo").placeholder = d ? d.toFixed(1) : "";
  $("#rangeInfo").textContent = d ? (a === 0 && !params.output.audio_end_s ? `using the whole song (${fmt(d)})` : `using ${fmt(a)} – ${fmt(b)} of ${fmt(d)} (${(b - a).toFixed(1)} s)`) : "";
  drawStrip();
}
function drawStrip() {
  const c = $("#songStrip"); if (!c.clientWidth) return;
  const W = c.clientWidth, H = 56; c.width = W * devicePixelRatio; c.height = H * devicePixelRatio;
  const g = c.getContext("2d"); g.scale(devicePixelRatio, devicePixelRatio);
  g.fillStyle = "#0d0b0b"; g.fillRect(0, 0, W, H);
  const d = songDur(); if (!d) return;
  const x = (t) => (t / d) * W;
  const [a, b] = songRange();
  if (fullAnalysis) {
    const beats = fullAnalysis.beats, energy = fullAnalysis.energy, bw = Math.max(1, W / beats.length);
    beats.forEach((t, i) => { const h = 4 + 40 * (energy[i] || 0); g.fillStyle = t >= a && t < b ? "#d9d9d9" : "#333"; g.fillRect(x(t), H - 8 - h, bw, h); });
    g.fillStyle = "#8c8c8c"; g.font = "10px sans-serif";
    for (const [name, [s0]] of Object.entries(fullAnalysis.sections)) { if (name === "peak") continue; g.fillRect(x(s0), 0, 1, 6); g.fillText(name, x(s0) + 2, 7); }
  }
  g.fillStyle = "rgba(255,255,255,.08)"; g.fillRect(x(a), 0, x(b) - x(a), H);
  g.fillStyle = "#fff"; g.fillRect(x(a), 0, 2, H); g.fillRect(x(b) - 2, 0, 2, H);
  const pl = $("#player"); if (pl.currentTime) { g.fillStyle = "#fff"; g.fillRect(x(pl.currentTime), 0, 1, H); }
  for (let t = 0; t < d; t += d > 300 ? 60 : d > 90 ? 30 : 10) { g.fillStyle = "#555"; g.fillRect(x(t), H - 6, 1, 6); g.fillStyle = "#8c8c8c"; g.font = "9px sans-serif"; g.fillText(fmt(t).replace(/\.\d$/, ""), x(t) + 2, H - 1); }
}
function initSongPicker() {
  const c = $("#songStrip"), pl = $("#player");
  const tAt = (e) => { const r = c.getBoundingClientRect(); return Math.max(0, Math.min(songDur(), ((e.clientX - r.left) / r.width) * songDur())); };
  c.addEventListener("pointerdown", (e) => { if (!songDur()) return; c.setPointerCapture(e.pointerId); stripDrag = { x0: e.clientX, t0: tAt(e), moved: false }; });
  c.addEventListener("pointermove", (e) => {
    if (!stripDrag) return;
    if (Math.abs(e.clientX - stripDrag.x0) > 3) stripDrag.moved = true;
    if (!stripDrag.moved) return;
    const t1 = tAt(e); const a = Math.min(stripDrag.t0, t1), b = Math.max(stripDrag.t0, t1);
    params.output.audio_start_s = Math.round(a * 10) / 10; params.output.audio_end_s = Math.round(b * 10) / 10; syncRangeUI();
  });
  c.addEventListener("pointerup", (e) => {
    if (!stripDrag) return;
    const dr = stripDrag; stripDrag = null;
    if (dr.moved) { const t1 = tAt(e); setRange(Math.min(dr.t0, t1), Math.max(dr.t0, t1), "drag"); }
    else { pl.currentTime = dr.t0; drawStrip(); }
  });
  $("#markStart").onclick = () => setRange(pl.currentTime, songRange()[1], "start");
  $("#markEnd").onclick = () => setRange(songRange()[0], pl.currentTime, "end");
  $("#wholeSong").onclick = () => setRange(0, 0, "whole");
  $("#playPart").onclick = () => { pl.currentTime = songRange()[0]; playingPart = true; pl.play(); };
  $("#songFrom").onchange = () => setRange(+$("#songFrom").value || 0, +$("#songTo").value || 0, "start");
  $("#songTo").onchange = () => setRange(+$("#songFrom").value || 0, +$("#songTo").value || 0, "end");
  pl.addEventListener("timeupdate", () => { const [, b] = songRange(); if (playingPart && params.output.audio_end_s && pl.currentTime >= b) { pl.pause(); playingPart = false; } drawStrip(); });
  pl.addEventListener("pause", () => { playingPart = false; });
  pl.addEventListener("loadedmetadata", syncRangeUI);
  for (const id of ["f_output_audio_start_s", "f_output_audio_end_s"]) $("#" + id).addEventListener("change", () => { readForm(); setRange(params.output.audio_start_s, params.output.audio_end_s, id.endsWith("end_s") ? "end" : "start"); });
  window.addEventListener("resize", drawStrip);
}

function dropzone(el, input, handler) {
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("over"); });
  el.addEventListener("dragleave", () => el.classList.remove("over"));
  el.addEventListener("drop", (e) => { e.preventDefault(); el.classList.remove("over"); handler(e.dataTransfer.files); });
  input.addEventListener("change", () => { handler(input.files); input.value = ""; });
}

// ---- direction ------------------------------------------------------------
// ---- AI shot order ----------------------------------------------------------
let orderBackup = null;
async function aiOrder() {
  if (job.photos.length < 2) { $("#orderStatus").textContent = "upload at least two photos or clips first"; return; }
  readForm();
  $("#aiOrder").disabled = true;
  $("#orderStatus").textContent = meta.claude ? "Claude is looking at every shot…" : "ordering by visual flow (no Anthropic key)…";
  $("#orderResult").classList.add("hidden");
  try {
    const r = await post(`/api/jobs/${job.id}/photos/ai-order`, { notes: $("#notes").value, style_preset: params.style_preset });
    orderBackup = r.previous;
    job = r;
    renderThumbs();
    // the planner must play the shots as they are now arranged
    if (params.order !== "as_uploaded") { params.order = "as_uploaded"; fillForm(); saveLocal(); }
    const changed = r.previous.some((n, i) => n !== r.photos[i]);
    $("#orderStatus").textContent = r.source === "claude" ? `ordered by ${r.model}` : "ordered by visual-flow heuristic";
    const extra = [r.opening && `<b>Opens:</b> ${r.opening}`, r.closing && `<b>Closes:</b> ${r.closing}`,
                   r.climax_shots && r.climax_shots.length && `<b>On the peak:</b> shots ${r.climax_shots.map((i) => i + 1).join(", ")}`].filter(Boolean);
    $("#orderResult").innerHTML = `<p>${r.rationale}${changed ? "" : " (your order was already right)"}</p>` + (extra.length ? `<p>${extra.join(" · ")}</p>` : "");
    $("#orderResult").classList.remove("hidden");
    $("#undoOrder").classList.toggle("hidden", !changed);
  } catch (e) { $("#orderStatus").textContent = "error: " + e.message; }
  $("#aiOrder").disabled = false;
}
async function undoOrder() {
  if (!orderBackup) return;
  job = await post(`/api/jobs/${job.id}/photos/order`, { photos: orderBackup });
  orderBackup = null; renderThumbs();
  $("#undoOrder").classList.add("hidden"); $("#orderResult").classList.add("hidden"); $("#orderStatus").textContent = "previous order restored";
}

async function direct() {
  readForm();
  const notes = $("#notes").value;
  if (!notes.trim()) { $("#directSource").textContent = "write some notes first"; return; }
  $("#direct").disabled = true; $("#directSource").textContent = meta.claude ? "asking Claude…" : "applying keyword rules…";
  try {
    const r = await post(`/api/jobs/${job.id}/direct`, { params, notes });
    params = r.params; fillForm(); saveLocal();
    $("#directSource").textContent = r.source === "claude" ? `interpreted by ${r.model}` : r.source === "keywords" ? "keyword rules (no Claude)" : "";
    const box = $("#directResult"); box.classList.remove("hidden");
    box.textContent = (r.rationale ? r.rationale + "\n" : "") + (r.changes.length ? "Changed: " + r.changes.join(", ") : "No changes.");
  } catch (e) { $("#directSource").textContent = "error: " + e.message; }
  $("#direct").disabled = false;
}

// ---- plan + timeline ------------------------------------------------------
async function makePlan() {
  readForm();
  if (!job.photos.length) { $("#planStatus").textContent = "upload photos first"; return; }
  if (!job.audio) { $("#planStatus").textContent = "upload music first"; return; }
  $("#plan").disabled = true; $("#planStatus").textContent = "planning…";
  try {
    edl = await post(`/api/jobs/${job.id}/plan`, { params });
    if (edl.skipped && edl.skipped.length) { job = await api(`/api/jobs/${job.id}`); renderThumbs(); $("#uploadStatus").textContent = `skipped ${edl.skipped.length} unreadable file(s): ` + edl.skipped.map((r) => `${r.file} (${r.reason})`).join("; "); }
    const s = edl.stats;
    $("#planStats").innerHTML = `<span><b>${s.n_shots}</b> shots</span><span><b>${s.n_cards}</b> cards</span><span><b>${s.photos_used}/${s.photos_total}</b> photos</span><span><b>${s.duration_s}</b> s</span><span>shots <b>${s.shortest_shot_ms}–${s.longest_shot_ms}</b> ms (mean ${s.mean_shot_ms})</span><span>max cut offset <b>${s.max_cut_offset_ms}</b> ms</span><span><b>${s.flashes}</b> flashes</span><span><b>${s.hard_beats}</b> hard beats</span>${edl.song_ramp ? `<span>song speed <b>×${edl.song_ramp[0]} → ×${edl.song_ramp[1]}</b></span>` : ""}<span><b>${s.strobe_shots}</b> strobe shots</span><span><b>${s.key_moments || 0}</b> key moments</span>${(s.must_missing || []).length ? `<span class="warn">★ not in the cut: #${s.must_missing.map((i) => i + 1).join(", #")}</span>` : ""}`;
    $("#planStatus").textContent = "planned";
    drawTimeline();
  } catch (e) { $("#planStatus").textContent = "error: " + e.message; }
  $("#plan").disabled = false;
}

function drawTimeline() {
  const c = $("#timeline"); const W = c.clientWidth || 800; c.width = W * devicePixelRatio; c.height = 140 * devicePixelRatio;
  const g = c.getContext("2d"); g.scale(devicePixelRatio, devicePixelRatio);
  g.fillStyle = "#0d0b0b"; g.fillRect(0, 0, W, 140);
  if (!analysis) return;
  const dur = edl ? Math.max(edl.audio_end, edl.shots.at(-1).end) : analysis.duration;
  const x = (t) => (t / dur) * W;
  // energy
  const beats = edl ? edl.beats : analysis.beats, energy = analysis.energy;
  g.fillStyle = "#222";
  beats.forEach((b, i) => { const h = 30 * (energy[i] || 0); g.fillRect(x(b), 110 - h, Math.max(1, W / beats.length), h); });
  // shots
  if (edl) {
    edl.shots.forEach((s, i) => {
      const x0 = x(s.start), x1 = x(Math.min(s.end, dur));
      if (s.card_text != null) { g.fillStyle = "#ffffff"; }
      else { const l = 28 + ((s.photo_idx * 37) % 50); g.fillStyle = `hsl(0 0% ${s.strobe ? l * 0.6 : l}%)`; }
      g.fillRect(x0, 40, Math.max(1, x1 - x0 - 0.5), 50);
      if (s.strobe) { g.fillStyle = "rgba(0,0,0,.5)"; for (let xx = x0; xx < x1; xx += 4) g.fillRect(xx, 40, 2, 50); }
      if (s.flash) { g.fillStyle = s.flash === "white" ? "#fff" : "#000"; g.fillRect(x0, 36, 2, 58); }
    });
  }
  // beats + downbeats
  const downs = new Set((edl ? edl.downbeats : analysis.downbeats).map((d) => d.toFixed(4)));
  beats.forEach((b) => { const d = downs.has(b.toFixed(4)); g.fillStyle = d ? "#ffffff" : "#555"; g.fillRect(x(b), d ? 14 : 24, d ? 2 : 1, d ? 24 : 12); });
  // sections
  g.fillStyle = "#8c8c8c"; g.font = "11px sans-serif";
  for (const [name, [s0]] of Object.entries(analysis.sections)) { if (name === "peak") continue; g.fillText(name, x(s0) + 3, 128); g.fillRect(x(s0), 118, 1, 14); }
  if (edl && edl.audio_end < dur) { g.fillStyle = "rgba(255,255,255,.12)"; g.fillRect(x(edl.audio_end), 0, W - x(edl.audio_end), 140); }
  g.fillStyle = "#8c8c8c"; g.fillText("beats / downbeats (bright)", 4, 10);
}

// ---- render ---------------------------------------------------------------
async function startRender(preview) {
  readForm();
  if (!job.photos.length || !job.audio) { $("#renderStatus").textContent = "upload photos and music first"; return; }
  $("#render").disabled = $("#preview").disabled = true;
  $("#result").classList.add("hidden"); $("#error").classList.add("hidden");
  $("#barFill").style.width = "0";
  try {
    job = await post(`/api/jobs/${job.id}/render`, { params, preview });
    watch();
  } catch (e) { $("#renderStatus").textContent = "error: " + e.message; $("#render").disabled = $("#preview").disabled = false; }
}
function watch() {
  if (es) es.close();
  $("#render").disabled = $("#preview").disabled = true;
  es = new EventSource(`/api/jobs/${job.id}/events`);
  es.onmessage = (ev) => {
    job = JSON.parse(ev.data);
    $("#barFill").style.width = (job.progress * 100).toFixed(1) + "%";
    $("#renderStatus").textContent = `${job.status}: ${job.message || ""}`;
    if (job.status === "done") { es.close(); showOutput(); $("#render").disabled = $("#preview").disabled = false; }
    if (job.status === "error") { es.close(); $("#error").textContent = job.error; $("#error").classList.remove("hidden"); $("#render").disabled = $("#preview").disabled = false; }
  };
  es.onerror = () => { es.close(); $("#render").disabled = $("#preview").disabled = false; };
}
function showOutput() {
  const url = `/api/jobs/${job.id}/output?t=${Date.now()}`;
  $("#video").src = url;
  $("#download").href = `/api/jobs/${job.id}/output?download=1`; $("#download").textContent = `Download ${job.output}`;
  $("#result").classList.remove("hidden");
}

// ---- init -----------------------------------------------------------------
(async function init() {
  meta = await api("/api/presets");
  params = meta.defaults;
  try { if (localStorage.getItem("nullmontage.v") !== "4") { localStorage.removeItem("nullmontage.params"); localStorage.setItem("nullmontage.v", "4"); }
    const saved = JSON.parse(localStorage.getItem("nullmontage.params")); if (saved && saved.effects) params = { ...params, ...saved, effects: { ...params.effects, ...saved.effects }, intertitles: { ...params.intertitles, ...saved.intertitles }, output: { ...params.output, ...saved.output } }; } catch {}
  buildPanels();
  const sel = $("#preset");
  for (const k of Object.keys(meta.presets)) { const o = document.createElement("option"); o.value = k; o.textContent = k.replace(/_/g, " "); sel.appendChild(o); }
  sel.onchange = () => {
    readForm();
    const patch = meta.presets[sel.value] || {};
    const deep = (b, p) => { for (const [k, v] of Object.entries(p)) { if (v && typeof v === "object" && !Array.isArray(v)) deep(b[k], v); else b[k] = v; } };
    deep(params, patch); params.style_preset = sel.value; fillForm(); saveLocal();
  };
  fillForm();
  $("#directSource").textContent = meta.claude ? "Claude available for notes" : "no Anthropic key: notes use keyword rules";
  await ensureJob();
  dropzone($("#photoDrop"), $("#photoInput"), uploadPhotos);
  dropzone($("#audioDrop"), $("#audioInput"), (files) => files[0] && uploadAudio(files[0]));
  $("#direct").onclick = direct;
  $("#aiOrder").onclick = aiOrder;
  $("#aiShots").onclick = analyzeShots;
  $("#undoOrder").onclick = undoOrder;
  $("#pickOrder").onclick = pickOrderClick;
  $("#pickCancel").onclick = pickCancel;
  $("#plan").onclick = makePlan;
  $("#preview").onclick = () => startRender(true);
  $("#render").onclick = () => startRender(false);
  $("#newJob").onclick = () => { location.href = location.pathname; };
  for (const id of ["f_output_speed", "f_output_keep_pitch"]) $("#" + id).addEventListener("change", () => job.audio && loadAnalysis());
  initSongPicker();
  window.addEventListener("resize", drawTimeline);
})();
