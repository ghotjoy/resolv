function setMarkup(target, markup) {
  const range = document.createRange();
  range.selectNodeContents(target);
  const fragment = range.createContextualFragment(markup);
  for (const element of fragment.querySelectorAll("script, iframe, object, embed, base, meta, link, foreignObject")) {
    element.remove();
  }
  for (const element of fragment.querySelectorAll("*")) {
    for (const attr of [...element.attributes]) {
      const name = attr.name.toLowerCase();
      const url = attr.value.replace(/[\u0000-\u0020]/g, "");
      if (name.startsWith("on") || name === "srcdoc" || name === "formaction" ||
          (["src","href","xlink:href","action"].includes(name) && /^(javascript|vbscript|data):/i.test(url))) {
        element.removeAttribute(attr.name);
      }
    }
  }
  target.replaceChildren(fragment);
}

/* Features shared by the three service-desk workspaces. */
let dashboardFilters = {}, chartPeriod = "daily", dashboardSection = "overview";
let auditFilters = {sort:"newest", visibility:"visible"}, drilled = false;
let reportLocation = null, mapSession = null;
let evidenceFiles = {report:[], detail:[]};
let note = null;
const imageUrls = new Set();
const timelineActions = ["CREATED","CLASSIFIED","ASSIGNED","ACKNOWLEDGED","IN_PROGRESS","RESOLVED","VERIFIED",
  "CLOSED","PRIORITY_CHANGED","ADMIN_OVERRIDE","ATTACHMENTS_ADDED","NOTE","INFO_REQUEST","REPLY",
  "SLA_WARNING","SLA_VIOLATED","ESCALATED","AUDIT_HIDDEN","AUDIT_RESTORED","AUDIT_CORRECTION"];

const themeKey = "resolv-theme";
function initializeTheme() {
  let style = null;
  try { style = localStorage.getItem(themeKey); } catch {}
  document.documentElement.dataset.theme = style === "dark" || style === "light"
    ? style : matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
initializeTheme();
function themeControl() {
  const dark = document.documentElement.dataset.theme === "dark";
  return `<button class="theme-switch" type="button" aria-label="Dark mode" aria-pressed="${dark}"
    data-ui-click="toggleTheme" data-ui-args="@element">${dark ? "Light mode" : "Dark mode"}</button>`;
}
function applyAccountTheme() {
  try {
    const saved = localStorage.getItem(themeKey + "-" + me.id);
    if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  } catch {}
}
function toggleTheme(button) {
  const theme = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(themeKey, theme);
    if (me) localStorage.setItem(themeKey + "-" + me.id, theme);
  } catch {}
  button.setAttribute("aria-pressed", theme === "dark");
  button.textContent = theme === "dark" ? "Light mode" : "Dark mode";
}
function resetEnhancements() {
  dashboardFilters = {};
  chartPeriod = "daily";
  dashboardSection = "overview";
  auditFilters = {sort:"newest", visibility:"visible"};
  drilled = false;
  cleanDrawer();
  cleanSecondary();
}
function cleanDrawer() {
  disposeVoice();
  if (typeof browserSpeech !== "undefined" && browserSpeech) browserSpeech.abort();
  for (const url of imageUrls) URL.revokeObjectURL(url);
  imageUrls.clear();
  evidenceFiles = {report:[], detail:[]};
}
function cleanSecondary() { mapSession = null; }
function modal(title, body) {
  const dialog = $("#secondary");
  setMarkup(dialog, `<div class="drawer-head"><h2 id="secondary-title">${esc(title)}</h2>
    <form method="dialog"><button>Close</button></form></div><div class="drawer-body">${body}</div>`);
  if (!dialog.open) dialog.showModal();
  return dialog;
}
function options(values, current, formatter = String) {
  return values.map(value => `<option value="${esc(value)}" ${current === value ? "selected" : ""}>
    ${esc(formatter(value))}</option>`).join("");
}
function campusAreas() { return meta.campus?.areas || []; }
function areaChoices() { return [...campusAreas().map(area => area.id), "pin_only", "none"]; }
function areaName(id) {
  if (id === "pin_only") return "Pin without named area";
  return campusAreas().find(area => area.id === id)?.name || "Location not provided";
}
function locationLabel(location) {
  if (!location) return "Location not provided";
  const label = areaName(location.area || "pin_only");
  return location.pin && location.area ? label + " · pin" : label;
}
function categoryName(incident) {
  return incident.custom_category ? "Other · " + incident.custom_category : words(incident.category);
}
function beginReport() {
  cleanDrawer();
  reportLocation = null;
  note = {};
}
function wireReport() {
  const form = $("#drawer form.drawer-body");
  const custom = form.elements.custom_category;
  const update = () => {
    const other = form.elements.category.value === "OTHER";
    $("#custom-wrap").hidden = !other;
    custom.disabled = !other;
    custom.required = other;
  };
  form.addEventListener("change", event => { if (event.target.name === "category") update(); });
  let refresh, sequence = 0;
  function suggest() {
    clearTimeout(refresh);
    const current = ++sequence;
    refresh = setTimeout(async () => {
      const target = form.querySelector("#suggestion");
      if (!target.isConnected || !$("#drawer").open) return;
      const text = [form.elements.title.value, form.elements.desc.value].join(" ").trim();
      if (!text) { target.textContent = ""; return; }
      try {
        const data = await api("category-suggestion", "POST", {text}, true);
        if (current !== sequence || !target.isConnected) return;
        target.textContent = data.category === "OTHER" ? "No matching category suggested." : "Suggested category: " + words(data.category);
      } catch { if (current === sequence) target.textContent = ""; }
    }, 250);
  }
  form.elements.title.addEventListener("input", suggest);
  form.elements.desc.addEventListener("input", suggest);
  wireFilePicker("report");
}
function setReportLocation(value) {
  reportLocation = value;
  $("#report-location").textContent = locationLabel(value);
  $("#clear-location").hidden = !value;
}

function fileSize(bytes) {
  if (bytes >= 1024 * 1024) return (bytes / 1024 / 1024).toFixed(1) + " MB";
  return (bytes / 1024).toFixed(1) + " KB";
}
function attachmentPicker(context) {
  return `<section class="file-picker ${context}" data-picker="${context}">
    <label>${context === "report" ? "Attach photos or documents" : "Add evidence"}
      <input type="file" multiple accept=".jpg,.jpeg,.png,.webp,.pdf,.docx,.txt"></label>
    <p class="m">Up to ${meta.max_files} files per issue, ${fileSize(meta.max_file_bytes)} each.</p>
    <div class="pending-files"></div>
    <p class="m file-status" aria-live="polite"></p>
    ${context === "detail" ? '<button type="button" class="send-files" disabled>Upload selected files</button>' : ""}
    </section>`;
}
function wireFilePicker(context) {
  const picker = $('[data-picker="' + context + '"]');
  if (!picker) return;
  const input = picker.querySelector("input");
  const status = picker.querySelector(".file-status");
  const send = picker.querySelector(".send-files");
  const redraw = () => {
    const list = picker.querySelector(".pending-files");
    setMarkup(list, evidenceFiles[context].map((file, index) => `<div class="row">
      <span>${esc(file.name)} · ${fileSize(file.size)}</span><button type="button" data-remove="${index}"
        aria-label="Remove ${esc(file.name)}">Remove</button></div>`).join(""));
    if (send) send.disabled = !evidenceFiles[context].length;
  };
  input.addEventListener("change", () => {
    const accepted = Array.from(input.files);
    const stored = Number(picker.dataset.stored || 0);
    if (stored + evidenceFiles[context].length + accepted.length > meta.max_files) {
      status.textContent = "This issue can accept at most " + meta.max_files + " attachments.";
    } else if (accepted.some(file => !file.size || file.size > meta.max_file_bytes || !/\.(jpe?g|png|webp|pdf|docx|txt)$/i.test(file.name))) {
      status.textContent = "Select supported, nonempty files within the size limit.";
    } else {
      evidenceFiles[context].push(...accepted);
      status.textContent = "";
      redraw();
    }
    input.value = "";
  });
  picker.addEventListener("click", event => {
    const button = event.target.closest("[data-remove]");
    if (!button) return;
    evidenceFiles[context].splice(Number(button.dataset.remove), 1);
    status.textContent = "";
    redraw();
  });
  if (send) send.onclick = async () => {
    picker.querySelectorAll("button,input").forEach(control => control.disabled = true);
    try {
      const files = await encodeFiles(context, status);
      await uploadEvidence("incidents/" + picker.dataset.incident + "/attachments", {files}, status);
      evidenceFiles.detail = [];
      status.textContent = "Evidence uploaded.";
      loadAttachments(picker.dataset.incident);
      loadTimeline(picker.dataset.incident);
    } catch (error) {
      status.textContent = error.message;
    } finally {
      input.disabled = false;
      redraw();
    }
  };
  redraw();
}
async function encodeFiles(context, status) {
  const files = [...evidenceFiles[context]];
  const payload = [];
  for (const [index, file] of files.entries()) {
    status.textContent = `Preparing file ${index + 1} of ${files.length}…`;
    const raw = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(",", 2)[1]);
      reader.onerror = () => reject(new Error("Could not read " + file.name));
      reader.readAsDataURL(file);
    });
    payload.push({name:file.name, type:file.type, data:raw});
  }
  return payload;
}
function uploadEvidence(endpoint, payload, status) {
  status.textContent = "Uploading evidence…";
  return new Promise((resolve, reject) => {
    const upload = new XMLHttpRequest();
    upload.open("POST", "/api/" + endpoint);
    const token = tok;
    upload.setRequestHeader("Authorization", token);
    upload.setRequestHeader("Content-Type", "application/json");
    upload.responseType = "json";
    upload.upload.onprogress = event => {
      if (event.lengthComputable) status.textContent = event.loaded < event.total
        ? "Uploading evidence · " + Math.floor(100 * event.loaded / event.total) + "%"
        : "Saving issue and evidence…";
    };
    upload.onload = () => {
      if (upload.status >= 200 && upload.status < 300 && upload.response) resolve(upload.response);
      else {
        sessionExpired(upload.status, token);
        reject(requestError(upload.response, "Could not save evidence."));
      }
    };
    upload.onerror = () => reject(new Error("The upload was interrupted. Try again."));
    upload.send(JSON.stringify(payload));
  });
}
async function downloadBlob(endpoint) {
  const token = tok;
  const response = await fetch("/api/" + endpoint, {headers:{Authorization:token}});
  if (!response.ok) {
    sessionExpired(response.status, token);
    throw requestError(await response.json(), "Could not download the file.");
  }
  return await response.blob();
}
function saveBlob(blob, filename) {
  const anchor = document.createElement("a");
  const url = URL.createObjectURL(blob);
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function loadAttachments(id) {
  const target = $("#ticket-files");
  if (!target) return;
  try {
    const files = await api("incidents/" + id + "/attachments", "GET", undefined, true);
    if (!target.isConnected || target !== $("#ticket-files")) return;
    const picker = $('[data-picker="detail"]');
    if (picker) Object.assign(picker.dataset, {stored:files.length, incident:id});
    setMarkup(target, files.map(file => `<article class="activity evidence-row"><b>${esc(file.name)}</b>
      <p class="m">${fileSize(file.size)} · ${esc(file.uploader)} · ${stamp(file.uploaded,true)}</p>
      <div class="actions">${file.type.startsWith("image/") ? '<button type="button" data-preview>Preview</button>' : ""}
      <button type="button" data-download>Download</button></div><p class="m evidence-message" aria-live="polite"></p>
      <img class="evidence-preview" hidden alt="${esc(file.name)}"></article>`).join("")
      || '<p class="m">No attachments.</p>');
    for (const [index, row] of Array.from(target.querySelectorAll("article")).entries()) {
      const file = files[index];
      const endpoint = "incidents/" + id + "/attachments/" + file.id;
      row.querySelector("[data-download]").onclick = async () => {
        try { saveBlob(await downloadBlob(endpoint), file.name); }
        catch (error) { row.querySelector(".evidence-message").textContent = error.message; }
      };
      const button = row.querySelector("[data-preview]");
      if (button) button.onclick = async () => {
        const image = row.querySelector("img");
        button.disabled = true;
        try {
          if (!image.src) {
            const blob = await downloadBlob(endpoint + "?preview=1");
            if (!row.isConnected || !$("#drawer").open) return;
            const url = URL.createObjectURL(blob);
            imageUrls.add(url);
            image.src = url;
          }
          image.hidden = !image.hidden;
          button.textContent = image.hidden ? "Preview" : "Hide preview";
        } catch (error) {
          row.querySelector(".evidence-message").textContent = error.message;
        } finally { button.disabled = false; }
      };
    }
  } catch (error) { if (target.isConnected) target.textContent = error.message; }
}
async function loadTimeline(id, action = "") {
  const target = $("#activity-timeline");
  target.textContent = "Loading events…";
  try {
    const rows = await api("incidents/" + id + "/activity?" + new URLSearchParams(action ? {action} : {}), "GET", undefined, true);
    if (!target.isConnected) return;
    if (!action && $("#timeline-count")) $("#timeline-count").textContent = rows.length;
    setMarkup(target, rows.map(row => `<div class="activity"><b>${esc(words(row.action))}</b>
      <p class="m">${stamp(row.ts,true)}${row.user ? " · " + esc(row.user) : ""}
      ${row.prev && row.prev !== row.new ? " · " + esc(words(row.prev)) + " → " + esc(words(row.new)) : ""}</p>
      ${row.desc ? '<p class="prose">' + esc(auditDescription(row)) + '</p>' : ""}</div>`).join("")
      || '<p class="m">No matching events.</p>');
  } catch (error) { if (target.isConnected) target.textContent = error.message; }
}

const mapGroups = ["Hostels","Academic","Administration","Amenities","Sports","Residential"];
const mapWidth = 1000, mapHeight = 920;
function mapPoint(lat, lon) {
  const [west, south, east, north] = meta.campus.bounds;
  return [(lon-west) * mapWidth / (east-west), (north-lat) * mapHeight / (north-south)];
}
function pinAt(x, y) {
  const [west, south, east, north] = meta.campus.bounds;
  return {lon:west + x / mapWidth * (east-west), lat:north - y / mapHeight * (north-south), version:meta.campus.version};
}
function mapLegend() {
  return '<div class="actions map-legend">' + mapGroups.map((group, index) =>
    `<span><i class="map-group marker-${index}" aria-hidden="true"></i> ${group}</span>`).join("") + "</div>";
}
function mapMarkup(areas, {selected = null, pin = null, counts = null} = {}) {
  const maximum = Math.max(1, ...Object.values(counts || {}));
  const overlap = new Map();
  const marks = areas.map(area => {
    const [x,y] = mapPoint(area.lat, area.lon);
    const coordinate = x.toFixed(1) + "," + y.toFixed(1);
    const ordinal = overlap.get(coordinate) || 0;
    overlap.set(coordinate, ordinal + 1);
    const offset = ordinal ? (ordinal % 2 ? 1 : -1) * Math.ceil(ordinal / 2) * 25 : 0;
    const group = mapGroups.indexOf(area.group);
    const count = counts ? counts[area.id] || 0 : null;
    const label = area.name + (counts ? " (" + count + ")" : "");
    const display = area.name.length > 21 ? area.name.substring(0, 19) + "…" : area.name;
    const size = counts && count ? 9 + 17 * Math.sqrt(count / maximum) : 7;
    return `<g class="map-marker marker-${group} ${selected === area.id ? "selected" : ""}
      ${counts ? "heat" : ""} ${counts && count ? "hot" : ""}" data-area="${esc(area.id)}"
      role="button" tabindex="0" aria-label="${esc(label)}" transform="translate(${x} ${y+offset})">
      <title>${esc(label)}</title>${offset ? `<path d="M0 ${-offset}V0" />` : ""}
      <circle r="${size}"></circle>${counts && count ? `<text class="hotspot-number" text-anchor="middle" y="4">${count}</text>` : ""}
      <text class="map-label" x="${size + 5}" y="4">${esc(display)}</text></g>`;
  }).join("");
  const [px,py] = pin ? mapPoint(pin.lat, pin.lon) : [0,0];
  return `<div class="map-frame"><div class="map-scroll" tabindex="0" aria-label="Scrollable campus map">
    <svg class="campus-map" viewBox="0 0 1000 920" aria-label="BITS Pilani Hyderabad campus, north up">
      <image href="/assets/campus-base.svg" width="1000" height="920"/>
      <g class="north" transform="translate(38 58)" aria-hidden="true"><path d="M0 -18 L-8 5 L0 1 L8 5 Z"/><text y="-25" text-anchor="middle">N</text></g>
      ${marks}${pin ? `<g class="issue-pin" transform="translate(${px} ${py})" aria-label="Issue pin">
      <path d="M0 0 C-34 -36 34 -36 0 0Z"/><circle cy="-17" r="3"/></g>` : ""}
    </svg></div></div>
    <div class="actions map-tools"><button type="button" data-zoom="-1" aria-label="Zoom out">−</button>
      <button type="button" data-zoom="1" aria-label="Zoom in">+</button>
      <span class="m">Scroll to pan · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a></span></div>`;
}
function wireMap(container, select, click, initialZoom = 1) {
  const map = container.querySelector(".campus-map");
  const scroll = container.querySelector(".map-scroll");
  let zoom = initialZoom;
  map.style.width = (zoom * 100) + "%";
  container.querySelectorAll("[data-zoom]").forEach(button => button.onclick = () => {
    const old = Math.max(1, map.clientWidth);
    zoom = Math.min(4, Math.max(1, zoom + Number(button.dataset.zoom) * .5));
    map.style.width = zoom * 100 + "%";
    const ratio = map.clientWidth / old;
    scroll.scrollLeft = (scroll.scrollLeft + scroll.clientWidth / 2) * ratio - scroll.clientWidth / 2;
    scroll.scrollTop = (scroll.scrollTop + scroll.clientHeight / 2) * ratio - scroll.clientHeight / 2;
  });
  map.addEventListener("click", event => {
    if (click && click(event, map)) return;
    const mark = event.target.closest("[data-area]");
    if (mark) select(mark.dataset.area);
  });
  map.addEventListener("keydown", event => {
    const mark = event.target.closest("[data-area]");
    if (mark && ["Enter", " "].includes(event.key)) {
      event.preventDefault();
      select(mark.dataset.area);
    }
  });
}
function viewTicketLocation(id) { pickCampus(true, C[id].location); }
function pickCampus(readOnly = false, current = reportLocation) {
  mapSession = {area:current?.area || null, pin:current?.pin || null, selecting:false, readOnly};
  modal(readOnly ? "Incident location" : "Select campus location", `<div id="map-grid"></div>
    ${mapLegend()}<p class="m" id="map-status" aria-live="polite"></p>
    ${!readOnly ? `<label>Find a campus area<input type="search" id="area-search" data-ui-input="filterCampusList" placeholder="Search by name"></label>
      <label>Campus area<select id="area-list" aria-label="Campus area" data-ui-change="chooseMapArea" data-ui-args="@value"></select></label>
      <div class="actions"><button type="button" id="pin-mode" aria-pressed="false" data-ui-click="togglePinMode">Place or move pin</button>
      <button type="button" data-ui-click="pinAtSelected">Pin selected area / map centre</button>
      <button type="button" data-ui-click="clearMapPin">Clear pin</button></div>
      <div class="actions"><button class="p" type="button" data-ui-click="saveMap">Use location</button>
      <button type="button" data-ui-click="closeDialog" data-ui-args="secondary">Cancel</button></div>` : ""}`);
  refreshPicker();
  if (!readOnly) filterCampusList();
}
function filterCampusList() {
  const words = $("#area-search").value.toLowerCase().trim();
  const areas = campusAreas().filter(area => area.active && (area.name.toLowerCase().includes(words) || area.id === mapSession.area));
  setMarkup($("#area-list"), '<option value="">No named area</option>' + options(areas.map(area => area.id), mapSession.area, areaName));
}
function chooseMapArea(id) {
  if (mapSession.readOnly) { $("#map-status").textContent = areaName(id); return; }
  mapSession.area = id || null;
  filterCampusList();
  refreshPicker();
}
function togglePinMode() {
  mapSession.selecting = !mapSession.selecting;
  updateMapStatus();
}
function pinAtSelected() {
  const area = campusAreas().find(area => area.id === mapSession.area);
  const [x,y] = area ? mapPoint(area.lat, area.lon) : [mapWidth / 2, mapHeight / 2];
  mapSession.pin = pinAt(x,y);
  mapSession.selecting = false;
  refreshPicker();
}
function clearMapPin() {
  mapSession.pin = null;
  mapSession.selecting = false;
  refreshPicker();
}
function saveMap() {
  const {area,pin} = mapSession;
  setReportLocation(area || pin ? {area,pin} : null);
  $("#secondary").close();
}
function updateMapStatus() {
  $("#map-status").textContent = mapSession.selecting ? "Choose any point on the map to place the pin."
    : locationLabel(mapSession.area || mapSession.pin ? mapSession : null);
  $("#pin-mode")?.setAttribute("aria-pressed", mapSession.selecting);
  $("#map-grid").classList.toggle("placing-pin", mapSession.selecting);
}
function refreshPicker() {
  const container = $("#map-grid");
  const previous = container.querySelector(".map-scroll");
  const zoom = (parseFloat(previous?.querySelector("svg").style.width) || 100) / 100;
  const left = previous?.scrollLeft || 0, top = previous?.scrollTop || 0;
  setMarkup(container, mapMarkup(campusAreas().filter(area => area.active || area.id === mapSession.area),
    {selected:mapSession.area, pin:mapSession.pin}));
  wireMap(container, id => {
    chooseMapArea(id);
    container.querySelector('[data-area="' + CSS.escape(id) + '"]')?.focus({preventScroll:true});
  }, (event, map) => {
    if (!mapSession.selecting) return false;
    const point = new DOMPoint(event.clientX,event.clientY).matrixTransform(map.getScreenCTM().inverse());
    if (point.x >= 0 && point.x <= mapWidth && point.y >= 0 && point.y <= mapHeight) {
      mapSession.pin = pinAt(point.x, point.y);
      mapSession.selecting = false;
      refreshPicker();
    }
    return true;
  }, zoom);
  if (previous) {
    const scroll = container.querySelector(".map-scroll");
    scroll.scrollLeft = left;
    scroll.scrollTop = top;
  }
  updateMapStatus();
}

function voiceControls() {
  return `<section class="voice-panel"><div class="actions">
    <button id="voice-record" type="button" data-ui-click="startRecording">Record issue</button>
    <button type="button" data-ui-click="useBrowserSpeech">Dictate description</button>
    <button id="voice-stop" type="button" data-ui-click="stopRecording" hidden>Stop recording</button>
    <button id="voice-discard" type="button" data-ui-click="discardVoice" hidden>Discard recording</button></div>
    <p class="m" id="voice-status" aria-live="polite">${meta.voice
      ? "English audio is transcribed locally and is discarded after processing."
      : "Transcription is unavailable. You can still enter the issue manually."}</p>
    <audio id="voice-preview" controls hidden></audio>
    <button id="voice-transcribe" type="button" data-ui-click="transcribeNote" hidden>Transcribe recording</button>
    <div id="voice-review" hidden>
      <label>Transcript<textarea id="voice-text" rows="4" maxlength="5000"></textarea></label>
      <button type="button" data-ui-click="fillVoiceDraft">Use transcript in report</button>
    </div></section>`;
}
function disposeVoice() {
  const current = note;
  note = null;
  if (!current) return;
  clearTimeout(current.timer);
  if (current.recorder?.state === "recording") current.recorder.stop();
  current.stream?.getTracks().forEach(track => track.stop());
  current.abort?.abort();
  if (current.url) URL.revokeObjectURL(current.url);
}
async function startRecording() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    $("#voice-status").textContent = "Microphone recording is unavailable in this browser. You can still report manually.";
    return;
  }
  const record = $("#voice-record");
  disposeVoice();
  const current = note = {};
  $("#voice-preview").removeAttribute("src");
  for (const id of ["voice-preview","voice-transcribe","voice-review","voice-discard"]) $("#" + id).hidden = true;
  record.disabled = true;
  $("#voice-status").textContent = "Requesting microphone permission…";
  try {
    const stream = await navigator.mediaDevices.getUserMedia({audio:true});
    if (note !== current || !$("#drawer")?.open) {
      stream.getTracks().forEach(track => track.stop());
      return;
    }
    current.stream = stream;
    current.recorder = new MediaRecorder(stream);
    const chunks = [];
    current.recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    current.recorder.onstop = () => {
      stream.getTracks().forEach(track => track.stop());
      if (note !== current || !$("#drawer")?.open) return;
      current.blob = new Blob(chunks, {type:current.recorder.mimeType});
      current.url = URL.createObjectURL(current.blob);
      const preview = $("#voice-preview");
      preview.src = current.url;
      $("#voice-stop").hidden = true;
      record.disabled = false;
      record.textContent = "Record again";
      $("#voice-transcribe").hidden = !meta.voice;
      $("#voice-discard").hidden = false;
      preview.hidden = false;
      $("#voice-status").textContent = "Recording ready. Listen, discard, or transcribe it.";
    };
    current.recorder.start(250);
    current.timer = setTimeout(stopRecording, 60000);
    $("#voice-stop").hidden = false;
    $("#voice-status").textContent = "Recording · up to 60 seconds";
  } catch {
    current.stream?.getTracks().forEach(track => track.stop());
    if (note === current && record.isConnected) {
      record.disabled = false;
      $("#voice-status").textContent = "Microphone permission was denied or the device is unavailable. You can still report manually.";
    }
  }
}
function stopRecording() {
  clearTimeout(note?.timer);
  if (note?.recorder?.state === "recording") note.recorder.stop();
}
function discardVoice() {
  disposeVoice();
  note = {};
  for (const id of ["voice-preview","voice-transcribe","voice-review","voice-discard","voice-stop"]) $("#" + id).hidden = true;
  $("#voice-preview").removeAttribute("src");
  $("#voice-text").value = "";
  $("#voice-record").textContent = "Record issue";
  $("#voice-record").disabled = false;
  $("#voice-status").textContent = "Recording discarded.";
}
async function transcribeNote() {
  const current = note;
  if (!current?.blob) return;
  const button = $("#voice-transcribe");
  const status = $("#voice-status");
  current.abort = new AbortController();
  button.disabled = true;
  status.textContent = "Transcribing recording…";
  try {
    const token = tok;
    const response = await fetch("/api/transcribe", {method:"POST", headers:{Authorization:token},
      body:current.blob, signal:current.abort.signal});
    const data = await response.json();
    if (note !== current) return;
    if (!response.ok) {
      sessionExpired(response.status, token);
      throw requestError(data, "Transcription failed.");
    }
    $("#voice-text").value = data.transcript;
    $("#voice-review").hidden = false;
    $("#voice-text").focus();
    status.textContent = "Review and edit the transcript before using it.";
  } catch (error) {
    if (note === current) status.textContent = error.message + " You can still report manually.";
  } finally {
    if (note === current) {
      URL.revokeObjectURL(current.url);
      current.blob = current.url = null;
      $("#voice-preview").removeAttribute("src");
      $("#voice-preview").hidden = true;
      button.hidden = true;
      button.disabled = false;
    }
  }
}
async function fillVoiceDraft() {
  const form = $("#drawer form.drawer-body");
  const status = $("#voice-status");
  try {
    const draft = await api("draft","POST",{transcript:$("#voice-text").value},true);
    if (!form.isConnected || !$("#drawer").open) return;
    if ((form.elements.title.value || form.elements.desc.value) && !confirm("Replace the current title and description with this draft?")) return;
    form.elements.title.value = draft.title;
    form.elements.desc.value = draft.description;
    form.elements.desc.dispatchEvent(new Event("input", {bubbles:true}));
    if (draft.service) form.elements.service.value = draft.service;
    if (!form.elements.category.value && draft.category && draft.category !== "OTHER") {
      form.elements.category.value = draft.category;
      form.elements.category.dispatchEvent(new Event("change", {bubbles:true}));
    }
    if (draft.area && !reportLocation) setReportLocation({area:draft.area,pin:null});
    status.textContent = "Draft added. Review each field and select Submit issue when ready.";
  } catch (error) { status.textContent = error.message; }
}

const overrideFields = {
  state:"Status", tech:"Assigned technician", priority_override:"Priority", title:"Title", description:"Description",
  category:"Category", custom_category:"Custom category", service:"Affected service", impact:"Impact", urgency:"Urgency", users:"Users affected",
};
function openOverride(id) {
  const incident = C[id];
  const fields = Object.entries(overrideFields).map(([key,label]) => {
    const select = {
      state:states, tech:[incident.tech,...meta.techs].filter((tech,index,all) => tech && all.indexOf(tech) === index),
      priority_override:["","P1","P2","P3","P4"], category:meta.categories, service:Object.keys(meta.services),
      impact:["1","2","3","4"], urgency:["1","2","3","4"],
    }[key];
    const value = String(incident[key] ?? "");
    const formatter = val => key === "priority_override" && !val ? "Automatic (clear override)" : key === "state" ? words(val) : val;
    const input = select ? `<select aria-label="Proposed ${label}" name="${key}">
      ${key === "tech" && !value ? '<option value="">Select a technician</option>' : ""}
      ${options(select,value,formatter)}</select>` : key === "description"
      ? `<textarea aria-label="Proposed ${label}" name="${key}" maxlength="5000">${esc(value)}</textarea>`
      : `<input aria-label="Proposed ${label}" name="${key}" value="${esc(value)}"
        ${key === "users" ? 'type="number" min="1"' : key === "title" ? 'maxlength="200"' : 'maxlength="100"'}>`;
    return `<div class="override-row"><label for="override-${key}">${label}</label>
      <span class="m">Current: ${esc(key === "priority_override" ? incident.priority + (incident.priority_override ? " (override)" : " (automatic)") : value ? formatter(value) : "—")}</span>
      ${input.replace(" name=", ' id="override-' + key + '" name=')}</div>`;
  }).join("");
  modal("Admin Override", `<form id="override-form" class="feature-form" data-ui-submit="submitOverride" data-ui-args="@event|${id}">
    <p class="m">Change the fields that need an override. Reopening a closed incident starts a new SLA cycle.</p>
    ${fields}
    <label>Reason for override<textarea name="reason" required maxlength="1000"></textarea></label>
    <p class="m" id="override-status" aria-live="polite"></p><button class="p" type="submit">Review and apply override</button></form>`);
}
async function submitOverride(event, id) {
  event.preventDefault();
  const form = event.currentTarget;
  const incident = C[id];
  const fields = Object.fromEntries(new FormData(form));
  const {reason} = fields;
  delete fields.reason;
  const changes = {};
  for (const [key,value] of Object.entries(fields)) {
    if (String(incident[key] ?? "") === value) continue;
    changes[key] = ["impact","urgency","users"].includes(key) ? Number(value) : value || null;
  }
  const status = $("#override-status");
  if (!Object.keys(changes).length || !reason.trim()) {
    status.textContent = "Change at least one field and enter a reason.";
    return;
  }
  const comparison = Object.entries(changes).map(([key,value]) =>
    overrideFields[key] + ": " + (key === "priority_override" ? incident.priority : incident[key] ?? "—") + " → "
      + (value ?? (key === "priority_override" ? "Automatic" : "—"))).join("\n");
  if (!confirm("Apply these changes to " + id + "?\n\n" + comparison + "\n\nReason: " + reason.trim())) return;
  event.submitter.disabled = true;
  status.textContent = "Applying override…";
  try {
    C[id] = await api("incidents/" + id + "/override","POST",{changes,reason},true);
    $("#secondary").close();
    const result = $("#results");
    if (result) await refreshTickets();
    showTicket(id);
    const feedback = document.createElement("p");
    feedback.setAttribute("role","status");
    feedback.className = "m";
    feedback.textContent = "Override applied.";
    $("#drawer-title").after(feedback);
  } catch (error) { status.textContent = error.message; }
  finally { event.submitter.disabled = false; }
}

function auditDescription(record) {
  if (record.action !== "ADMIN_OVERRIDE") return record.desc;
  try {
    const data = JSON.parse(record.desc);
    return data.reason + "\n" + Object.entries(data.changes).map(([key,[before,after]]) =>
      (overrideFields[key] || words(key)) + ": " + (before ?? "—") + " → " + (after ?? "—")).join("\n");
  } catch { return record.desc; }
}
async function vAudit() {
  const view = $("#v");
  const input = (name,label,type = "text") => `<label>${label}<input name="${name}" type="${type}" value="${esc(auditFilters[name])}"></label>`;
  setMarkup(view, `<form class="panel" id="audit-form" data-ui-submit="filterAudit" data-ui-args="@event">
    <div class="form-grid audit-filters">
      <label>Preset<select id="audit-preset" data-ui-change="auditPreset" data-ui-args="@value">
        <option value="">Choose a preset</option><option value="newest">Newest first</option><option value="oldest">Oldest first</option>
        <option value="inc">One incident</option><option value="user">One user</option>
        <option value="action">One action type</option><option value="hidden">Hidden records</option></select></label>
      <label>Sort by<select name="order_by">${options(["ts","user","action","inc","prev","new","visibility"],
        auditFilters.order_by || "ts", key => ({ts:"Timestamp",user:"Actor",action:"Action type",inc:"Incident ID",
          prev:"Previous state",new:"New state",visibility:"Visibility"})[key])}</select></label>
      <label>Sort direction<select name="sort">${options(["newest","oldest"],auditFilters.sort,
        value => value === "newest" ? "Descending" : "Ascending")}</select></label>
      <label>Visibility<select name="visibility">${options(["visible","hidden","all"],auditFilters.visibility,words)}</select></label>
      ${input("inc","Incident ID")}${input("user","Actor")}${input("action","Action type")}
      ${input("prev","Previous state")}${input("new","New state")}${input("from","From timestamp","datetime-local")}
      ${input("to","Through timestamp","datetime-local")}${input("q","Description contains")}
    </div><div class="actions"><button class="p" type="submit">Apply audit filters</button>
      <button type="button" aria-pressed="${auditFilters.visibility === "all"}" data-ui-click="toggleHiddenAudit">Show hidden records</button>
      <button type="button" data-ui-click="exportAudit">Export complete audit</button></div>
    <p class="m" id="audit-status" aria-live="polite"></p></form><div id="audit-rows" aria-live="polite">Loading audit entries…</div>`);
  const target = $("#audit-rows");
  try {
    const rows = await api("audit?" + new URLSearchParams(auditFilters),"GET",undefined,true);
    if (!target.isConnected) return;
    setMarkup(target, rows.length ? '<div class="queue record-list">' + rows.map(a => `<article class="panel">
      <div><b class="mono">#${a.id}</b> · ${esc(a.inc)} · <b>${esc(a.action)}</b> ${a.hidden ? '<span class="badge">Hidden</span>' : ""}</div>
      <p class="m">${stamp(a.ts,true)} · ${esc(a.user)} · ${esc(a.prev || "—")} → ${esc(a.new || "—")}
        ${a.target ? " · linked to audit #" + a.target : ""}</p>
      <p class="prose">${esc(auditDescription(a))}</p>
      <div class="actions"><button data-ui-click="auditAction" data-ui-args="${a.id}|${a.hidden ? "restore" : "hide"}">
        ${a.hidden ? "Restore" : "Hide"} entry</button><button data-ui-click="auditAction" data-ui-args="${a.id}|correct">Append correction</button></div>
      </article>`).join("") + '</div>' : '<p class="queue empty m">No audit entries match these filters.</p>');
  } catch (error) { if (target.isConnected) target.textContent = error.message; }
}
function filterAudit(event) {
  event.preventDefault();
  auditFilters = Object.fromEntries([...new FormData(event.currentTarget)].filter(([,value]) => value.trim()));
  return vAudit();
}
function auditPreset(value) {
  if (["inc","user","action"].includes(value)) {
    const form = $("#audit-form");
    for (const key of ["inc","user","action","prev","new","from","to","q"]) form.elements[key].value = "";
    form.elements[value].focus();
    return;
  }
  auditFilters = {sort:value === "oldest" ? "oldest" : "newest", visibility:value === "hidden" ? "hidden" : "visible"};
  vAudit();
}
function toggleHiddenAudit() {
  auditFilters.visibility = auditFilters.visibility === "all" ? "visible" : "all";
  vAudit();
}
async function auditAction(id, operation) {
  const action = operation === "correct" ? "Append correction to" : words(operation);
  modal(action + " audit #" + id, `<form class="feature-form" data-ui-submit="submitAuditAction" data-ui-args="@event|${id}|${operation}">
    <label>Reason or correction note<textarea name="reason" maxlength="1000" required></textarea></label>
    <p class="m" id="audit-action-status" aria-live="polite"></p>
    <button class="p" type="submit">Confirm ${operation === "correct" ? "correction" : operation}</button></form>`);
}
async function submitAuditAction(event, id, operation) {
  event.preventDefault();
  const status = $("#audit-action-status");
  event.submitter.disabled = true;
  try {
    await api("audit/" + id,"POST",{operation,reason:event.currentTarget.elements.reason.value},true);
    $("#secondary").close();
    await vAudit();
    $("#audit-status").textContent = operation === "correct" ? "Correction appended." : operation === "hide" ? "Audit entry hidden." : "Audit entry restored.";
  } catch (error) { status.textContent = error.message; }
  finally { event.submitter.disabled = false; }
}
async function exportAudit() {
  try { saveBlob(await downloadBlob("audit/export"), "audit.json"); }
  catch (error) { $("#audit-status").textContent = error.message; }
}

const dashboardLabels = {start:"From",end:"Through",status:"Status",priority:"Priority",category:"Category",
  service:"Service",tech:"Technician",area:"Campus area",sla_status:"SLA",scope:"Scope", date:"Created date", id:"Incident ID"};
function activeFilters(filters) {
  return Object.entries(filters).filter(([,value]) => value).map(([key,value]) =>
    (dashboardLabels[key] || words(key)) + ": " + (key === "area" ? areaName(value) : value)).join(" · ") || "No active filters";
}
function dashboardSelector(key, label, values, format = String) {
  return `<label>${label}<select aria-label="${label}" name="${key}"><option value="">All</option>
    ${options(values,dashboardFilters[key],format)}</select></label>`;
}
async function vDash() {
  const view = $("#v");
  setMarkup(view, `<form class="panel" id="dash-filters" data-ui-submit="applyDashboard" data-ui-args="@event">
    <h2>Dashboard filters</h2>
    <div class="dash-filter-fields">
      <label>From date<input name="start" type="date" value="${esc(dashboardFilters.start)}"></label>
      <label>Through date<input name="end" type="date" value="${esc(dashboardFilters.end)}"></label>
      ${dashboardSelector("status","Status",states,words)}
      ${dashboardSelector("priority","Priority",["P1","P2","P3","P4"])}
      ${dashboardSelector("category","Category",meta.categories,words)}
      ${dashboardSelector("service","Affected service",Object.keys(meta.services))}
      ${dashboardSelector("tech","Assigned technician",meta.techs)}
      ${dashboardSelector("area","Campus area",areaChoices(),areaName)}
    </div>
    <div class="actions"><button class="p" type="submit">Apply filters</button><button type="button"
      data-ui-click="clearDashboardFilters">Clear filters</button>
      <p class="m">${esc(activeFilters(dashboardFilters))}</p></div></form>
    <div class="actions dash-tabs" aria-label="Analytics sections">
      <button aria-pressed="${dashboardSection === "overview"}" data-ui-click="selectDashboard" data-ui-args="overview">Overview</button>
      <button aria-pressed="${dashboardSection === "campus"}" data-ui-click="selectDashboard" data-ui-args="campus">Campus heat map</button>
      ${me.can_export_csv ? '<button type="button" data-ui-click="exportIncidents" data-ui-args="dashboard">Export CSV</button>' : ""}
      ${me.role === "ADMIN" ? '<button class="p" data-ui-click="mon">Run SLA monitor now</button>' : ""}</div>
    <div id="dashboard-content" aria-live="polite" aria-busy="true">Loading analytics…</div>`);
  const target = $("#dashboard-content");
  try {
    const query = new URLSearchParams({...dashboardFilters,period:chartPeriod});
    const data = await api((me.role === "ADMIN" ? "dashboard?" : "my-dashboard?") + query,"GET",undefined,true);
    if (!target.isConnected) return;
    if (dashboardSection === "campus") renderCampusDashboard(data, target);
    else summaryDashboard(data, target);
  } catch (error) {
    if (target.isConnected) setMarkup(target, `<p class="panel" role="alert">Could not load analytics. ${esc(error.message)}
      <button data-ui-click="vDash">Retry</button></p>`);
  } finally { if (target.isConnected) target.removeAttribute("aria-busy"); }
}
function applyDashboard(event) {
  event.preventDefault();
  dashboardFilters = Object.fromEntries([...new FormData(event.currentTarget)].filter(([,value]) => value));
  vDash();
}
function drill(extra = {}) {
  filters = {...dashboardFilters, ...extra};
  drilled = true;
  tab = "inc";
  render();
}
function drillButton(label, count, key, value, max, level = "") {
  return `<button class="distribution-row ${level}" data-drill-key="${esc(key)}" data-drill-value="${esc(value)}">
    <span>${esc(label)}</span><div class="distribution-track" aria-hidden="true"><span style="width:${count * 100 / max}%"></span></div>
    <b>${count}</b></button>`;
}
function renderCampusDashboard(data, target) {
  const metricDefs = [
    ["total","Total incidents","Unique incident total", "", ""],
    ["open","Open incidents","Before resolution", "scope","open"],
    ["urgent","High / critical","Priority P1 or P2", "scope","urgent"],
    ["at_risk","SLA at risk","Deadline and workload forecast", "sla_status","AT_RISK"],
    ["breached","SLA breached","Response or resolution deadline missed", "sla_status","BREACHED"],
  ];
  const metrics = `<div class="metric-grid">${metricDefs.map(([key,label,definition,filter,value]) =>
    `<button class="metric" data-drill-key="${filter}" data-drill-value="${value}">
      <span>${label}</span><b>${data.metrics[key]}</b><small>${definition}</small></button>`).join("")}</div>`;
    const areas = campusAreas().filter(area => area.active || data.areas[area.id]);
    const counts = Object.entries(data.areas).filter(([id]) => areas.some(area => area.id === id)).sort((a,b) => b[1]-a[1]);
    const max = Math.max(1, ...Object.values(data.areas));
    setMarkup(target, metrics + `<section class="panel"><h2>Campus incident volume</h2>
      <p class="m">Unique incidents by named area. Larger numbered markers indicate higher counts.</p>
      <div id="heat-map">${mapMarkup(areas,{counts:data.areas})}</div>
      <p class="m">Marker size scale: 0–${max} incidents. Select a marker to view its issue list.</p>
      ${counts.map(([id,count]) => drillButton(areaName(id),count,"area",id,max)).join("")}
      ${drillButton("Location not provided",data.areas.none || 0,"area","none",max)}
      ${data.areas.pin_only ? drillButton(areaName("pin_only"),data.areas.pin_only,"area","pin_only",max) : ""}
      ${!data.metrics.total ? '<p class="m">No incidents match this selection.</p>' : ""}
      </section>`);
    wireMap(target.querySelector("#heat-map"), area => drill({area}));
  target.querySelectorAll("[data-drill-key]").forEach(button => button.onclick = () => {
    const {drillKey,drillValue} = button.dataset;
    const extra = drillKey ? {[drillKey]:drillValue} : {};
    if (drillKey === "tech") extra.scope = "workload";
    drill(extra);
  });
}

function settingsExtensions() {
  const panel = $("#feature-config");
  if (!panel) return;
  setMarkup(panel, `<section class="panel"><h3>Campus area inventory</h3>
    <label>Area<select id="manage-area" data-ui-change="loadAreaEditor" data-ui-args="@value">
      <option value="">Add campus area</option>${options(campusAreas().map(area=>area.id),"",areaName)}</select></label>
    <form id="area-editor" class="feature-form" data-ui-submit="saveCampusArea" data-ui-args="@event"></form></section>
    <section class="panel"><h3>Email delivery</h3><div id="mail-diagnostics" aria-live="polite">Loading delivery status…</div></section>`);
  loadAreaEditor("");
  loadMailStatus();
}
function loadAreaEditor(id) {
  const area = campusAreas().find(area => area.id === id);
  setMarkup($("#area-editor"), `<div class="form-grid">
    <label>Area ID<input name="id" required pattern="[a-z][a-z0-9\\-]{1,79}" value="${esc(id)}" ${id ? "readonly" : ""}></label>
    <label>Display name<input name="name" required maxlength="100" value="${esc(area?.name)}"></label>
    <label>Group<select name="group">${options(mapGroups,area?.group)}</select></label>
    <label>Status<select name="active">${options(["true","false"],String(area?.active ?? true),value => value === "true" ? "Active" : "Inactive")}</select></label>
    <label>Latitude<input name="lat" type="number" step="any" required value="${esc(area?.lat)}"></label>
    <label>Longitude<input name="lon" type="number" step="any" required value="${esc(area?.lon)}"></label></div>
    <button type="submit">Save campus area</button><p class="m" id="campus-admin-status" aria-live="polite"></p>`);
}
async function saveCampusArea(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const status = $("#campus-admin-status");
  const fields = Object.fromEntries(new FormData(form));
  Object.assign(fields, {lat:Number(fields.lat),lon:Number(fields.lon),active:fields.active === "true"});
  event.submitter.disabled = true;
  try {
    await api("campus","POST",fields,true);
    meta = await api("meta");
    settingsExtensions();
    $("#manage-area").value = fields.id;
    loadAreaEditor(fields.id);
    $("#campus-admin-status").textContent = "Campus area saved.";
  } catch (error) { status.textContent = error.message; }
  finally { event.submitter.disabled = false; }
}
async function loadMailStatus() {
  const target = $("#mail-diagnostics");
  try {
    const data = await api("mail-status","GET",undefined,true);
    if (!target.isConnected) return;
    setMarkup(target, `<p class="m">${data.mode === "smtp" ? "SMTP enabled" : "Simulated email"}
      ${data.error ? " · " + esc(data.error) : ""}</p>
      ${data.deliveries.map(row => `<p class="activity">${esc(row.incident)} · ${esc(row.event)} · ${esc(row.status)}
        · ${row.attempts} attempts ${row.error ? " · " + esc(row.error) : ""}</p>`).join("")}`);
  } catch (error) { if (target.isConnected) target.textContent = error.message; }
}

function promotionChoices(labels) {
  if (!labels.length) return "";
  return "<h4>Other labels reported</h4>" + labels.map(({label,count}) => {
    const configured = meta.categories.includes(label.toUpperCase());
    return '<div class="row"><span>' + esc(label) + " · " + count + ' reports</span>'
      + (configured ? '<span class="m">Configured</span>' : '<button type="button" data-label="' + esc(label)
        + '" data-ui-click="preparePromotion" data-ui-args="@label">Use as category name</button>') + '</div>';
  }).join("");
}
function preparePromotion(label) {
  $("#cn").value = label;
  $("#ck").focus();
}
