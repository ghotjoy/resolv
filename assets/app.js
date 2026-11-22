const app = document.querySelector("#app");
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "").replace(/[&<>"']/g, char =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char])
);
let tok = null, me = null, meta = {}, tab = "inc", filters = {}, tickets = [];
let request = 0;
let notificationStream, alertFeed = [], lastAlert = null;
const C = Object.create(null);
const states = ["NEW","CLASSIFIED","ASSIGNED","ACKNOWLEDGED","IN_PROGRESS","RESOLVED","VERIFIED","CLOSED"];
const brand = '<div class="brand"><img class="bits-logo" src="/assets/bits-pilani-logo.png" alt="BITS Pilani emblem" width="130" height="120">Resolv</div>';
const labels = {wl:"Workload", alerts:"Alerts", dash:"Analytics", audit:"Audit log", usr:"Users", cfg:"Admin"};
const words = value => String(value).toLowerCase().replaceAll("_", " ").replace(/^./, c => c.toUpperCase());
const formatDate = new Intl.DateTimeFormat(undefined, {month:"short", day:"2-digit", year:"numeric"});
const formatTime = new Intl.DateTimeFormat(undefined, {hour:"2-digit", minute:"2-digit"});
const stamp = (value, withTime = false) => {
  if (!value) return "—";
  const date = new Date(value);
  return '<time datetime="' + esc(value) + '">' + formatDate.format(date)
    + (withTime ? " · " + formatTime.format(date) : "") + '</time>';
};
function sessionExpired(status, token) {
  if (status !== 401 || !token || tok !== token) return false;
  login();
  $("#signin-status").textContent = "Your session expired. Sign in again to continue.";
  return true;
}

function requestError(data, fallback) {
  const error = new Error(data?.error || fallback);
  error.existingIncident = data?.existing_incident;
  return error;
}

async function api(path, method = "GET", body, quiet = false) {
  const token = tok;
  const response = await fetch("/api/" + path, {
    method, headers: {Authorization: token || "", "Content-Type": "application/json"},
    body: method === "GET" ? undefined : JSON.stringify(body || {}),
  });
  const data = await response.json();
  if (!response.ok) {
    const error = requestError(data, "The request failed. Try again.");
    const expired = sessionExpired(response.status, token);
    if (!quiet && !expired) alert(error.message);
    throw error;
  }
  return data;
}

function login() {
  notificationStream?.abort();
  notificationStream = null;
  alertFeed = [];
  lastAlert = null;
  tok = null;
  me = null;
  filters = {};
  resetEnhancements();
  request++;
  setMarkup(app, '<main class="login-wrap"><form class="login-panel" data-ui-submit="submitLogin" data-ui-args="@event">'
    + brand + themeControl() + '<h1>Sign in</h1>'
    + '<label>User ID<input id="u" autocomplete="username"></label>'
    + '<label>Password<input id="p" type="password" autocomplete="current-password"></label>'
    + '<button class="p" type="submit">Login</button>'
    + '<button type="button" id="open-registration" data-ui-click="doReg">Register as employee</button>'
    + '<div class="actions"><button type="button" data-ui-click="accountForm" data-ui-args="access">Request access</button>'
    + '<button type="button" data-ui-click="accountForm" data-ui-args="recovery">Forgot password?</button>'
    + '<button type="button" data-ui-click="accountForm" data-ui-args="complete">Use account code</button></div>'
    + '<p id="signin-status" role="status"></p></form></main>');
  const registration = $("#open-registration");
  api("public-config","GET",undefined,true).then(config => {
    if (registration.isConnected) registration.hidden = !config.open_registration;
  }).catch(() => {});
}

function queueTitle() {
  return me.role === "EMPLOYEE" ? "My Complaints" : me.role === "TECHNICIAN" ? "Assigned Issues" : me.role === "ADMIN" ? "All Issues" : "Escalated Issues";
}

function tabs() {
  const list = [["inc", queueTitle()], ["topology","Service topology"], ["routing","How routing works"], ["alerts", labels.alerts]];
  if (me.role === "TECHNICIAN") list.push(["wl", labels.wl]);
  list.push(["dash", me.role === "ADMIN" ? labels.dash : "My analytics"]);
  if (["EMPLOYEE","TECHNICIAN","ADMIN"].includes(me.role)) list.push(["triage","Triage assistant"]);
  if (me.role === "ADMIN") list.push(...["audit","usr","cfg"].map(key => [key,labels[key]]),["account","Account requests"]);
  return list;
}

async function render() {
  request++;
  tickets = [];
  const title = tab === "inc" ? queueTitle() : tabs().find(([key]) => key === tab)?.[1];
  setMarkup(app, shell(title));
  $("#drawer").addEventListener("close", cleanDrawer);
  $("#secondary").addEventListener("close", cleanSecondary);
  bindShell();
  await ({usr:vUsers,wl:vWl,inc:vInc,alerts:vAlerts,dash:vDash,audit:vAudit,cfg:vConsole,topology:vTopology,routing:vRouting,account:showAccountRequests,triage:showTriage})[tab]();
}

function selectFilter(key, label, values, formatter = value => value) {
  return `<select aria-label="${label}" data-ui-change="setFilter" data-ui-args="${key}|@value">
    <option value="">${label}</option>${values.map(value =>
      `<option value="${esc(value)}" ${filters[key] === value ? "selected" : ""}>${esc(formatter(value))}</option>`).join("")}
    </select>`;
}

function vInc() {
  setMarkup($("#v"), `<section class="queue" aria-label="Issue queue">
    <div class="filters">
      <input type="search" id="search" aria-label="Search by incident ID, title, or custom category" placeholder="Search ID, title, or custom category"
        data-ui-input="updateRows">
      ${selectFilter("status","Any status",states,words)}
      ${selectFilter("category","Any category",meta.categories)}
      ${selectFilter("service","Any service",Object.keys(meta.services))}
      <button type="button" data-ui-click="resetQueue">Reset filters</button>
      ${me.can_export_csv ? '<button type="button" data-ui-click="exportIncidents" data-ui-args="queue">Export CSV</button>' : ""}
      <details><summary>More filters</summary><div class="extra">
        ${selectFilter("priority","Any priority",["P1","P2","P3","P4"])}
        ${selectFilter("sla_status","Any SLA",["OK","AT_RISK","BREACHED","MET"],words)}
        ${me.role === "ADMIN" ? selectFilter("tech","Any technician",meta.techs) : ""}
        <label>Exact incident ID<input placeholder="Incident ID e.g. INC1001" value="${esc(filters.id)}"
          data-ui-change="setFilter" data-ui-args="id|@upper"></label>
        <label>Created date<input type="date" value="${esc(filters.date)}" data-ui-change="setFilter" data-ui-args="date|@value"></label>
        ${me.role === "ADMIN" ? selectFilter("area","Any campus area",areaChoices(),areaName) : ""}
        <label>Order<select id="queue-sort" data-ui-change="updateRows"><option value="newest">Newest first</option><option value="oldest">Oldest first</option></select></label>
      </div></details>
    </div>${drilled ? `<div class="actions drill-context"><button data-ui-click="navigate" data-ui-args="dash">Back to Analytics</button><span class="m">${esc(activeFilters(filters))}</span><button data-ui-click="resetQueue">Clear issue filters</button></div>` : ""}
    <div id="results" aria-busy="true"><p class="empty m">Loading issues…</p></div></section>`);
  return refreshTickets();
}

function setFilter(key, value) {
  filters[key] = value;
  return refreshTickets();
}

async function refreshTickets(openId) {
  const current = ++request;
  const view = $("#v");
  const activeTab = tab;
  const results = $("#results");
  if (results) results.setAttribute("aria-busy", "true");
  try {
    const data = await api(activeTab === "wl" ? "workload" : "incidents?" + new URLSearchParams(filters));
    if (current !== request || view !== $("#v")) return;
    tickets = activeTab === "wl" ? data.incidents : data;
    for (const ticket of tickets) C[ticket.id] = ticket;
    if (activeTab === "wl") {
      setMarkup($("#load"), `<b>${data.load} / ${data.capacity}</b> active incidents
        <p class="m">Skills: ${data.skills.map(esc).join(", ") || "—"}</p>`);
    }
    updateRows();
    if (openId && tickets.some(ticket => ticket.id === openId)) showTicket(openId);
    else if (openId) $("#drawer").close();
  } finally {
    if (current === request && results) results.removeAttribute("aria-busy");
  }
}

function updateRows() {
  const query = ($("#search")?.value || "").trim().toLowerCase().replace(/^#/, "");
  const results = tickets.filter(i => [i.id, i.title, i.custom_category || "", locationLabel(i.location)].some(value => value.toLowerCase().includes(query)));
  if ($("#queue-sort")?.value === "oldest") results.reverse();
  const active = Object.values(filters).filter(Boolean).length + Number(Boolean(query));
  setMarkup($("#results"), `<div class="count m" role="status">${results.length} ${results.length === 1 ? "issue" : "issues"}
    ${active ? " · " + active + (active === 1 ? " active filter" : " active filters") : ""}</div>`
    + (results.length ? `<div class="table-scroll"><table><thead><tr>${["Ticket ID","Issue title","Category","Service","Location","Status","Priority","Assignee","SLA","Created"].map(label =>
      `<th scope="col">${label}</th>`).join("")}</tr></thead><tbody>
    ${results.map(i => `<tr data-ticket="${i.id}">
      <td class="mono" data-label="Ticket ID">#${i.id}<small class="m table-context">${esc(i.reporter)}</small></td>
      <td class="title" data-label="Issue title"><button class="ticket-link" data-ui-click="showTicket" data-ui-args="${i.id}">${esc(i.title)}</button></td>
      <td data-label="Category">${esc(categoryName(i))}</td>
      <td data-label="Service">${esc(i.service)}</td>
      <td data-label="Location">${esc(locationLabel(i.location))}</td>
      <td data-label="Status">${statusBadge(i)}</td>
      <td data-label="Priority"><span class="badge ${i.priority}">${i.priority}</span></td>
      <td data-label="Assignee">${esc(i.tech || "Unassigned")}</td>
      <td data-label="SLA"><span class="${i.sla}">${words(i.sla)}</span></td>
      <td data-label="Created">${stamp(i.created,true)}</td>
    </tr>`).join("")}</tbody></table></div>` : '<p class="empty m">No issues match this view.</p>'));
}

function statusBadge(ticket) {
  return `<span class="badge status ${ticket.state}">${words(ticket.state)}</span>`;
}

function openDrawer(id, title, body) {
  const drawer = $("#drawer");
  drawer.classList.remove("report-dialog");
  setMarkup(drawer, `<div class="drawer-head"><div>${id ? `<p class="mono m">#${id}</p>` : ""}
    <h2 id="drawer-title">${esc(title)}</h2></div><form method="dialog"><button>Close<span class="sr-only"> drawer</span></button></form></div>
    ${body}`);
  if (!drawer.open) drawer.showModal();
  drawer.scrollTop = 0;
  drawer.querySelector(".drawer-head button").focus({preventScroll:true});
}

function showTicket(id) {
  const i = C[id];
  const facts = [
    ["Category", esc(categoryName(i))], ["Affected service", esc(i.service)],
    ["Campus location", esc(locationLabel(i.location))], ["Closed", stamp(i.closed, true)],
    ["Assigned at", stamp(i.assigned_at, true)], ["Responded", stamp(i.responded, true)],
    ["Resolved", stamp(i.resolved, true)], ["Verified at", stamp(i.verified_at, true)],
    ["SLA cycle started", stamp(i.sla_started, true)],
    ["Reporter", esc(i.reporter)], ["Assigned technician", esc(i.tech || "Unassigned")],
    ["Created", stamp(i.created, true)], ["SLA deadline", stamp(i.due, true)],
    ["Impact / urgency", i.impact + " / " + i.urgency], ["Users affected", i.users],
  ];
  openDrawer(id, i.title, `<div class="ticket-drawer drawer-body">
    <div class="actions">${statusBadge(i)}<span class="badge ${i.priority}">${i.priority}</span>
    ${i.priority_override ? '<span class="m">Admin priority override</span>' : ""}
    <span class="m">SLA <b class="${i.sla}">${words(i.sla)}</b></span>
    ${i.esc ? '<span class="badge">Escalation ' + i.esc + '</span>' : ""}</div>
    <p class="m">${esc(i.service)} · ${esc(categoryName(i))} · ${esc(i.reporter)} → ${esc(i.tech || "Unassigned")}</p>
    <div class="actions drawer-switch" aria-label="Incident details">
      <button type="button" data-ui-click="switchTicket" data-ui-args="workflow" aria-pressed="true">Workflow</button>
      <button type="button" data-ui-click="switchTicket" data-ui-args="insights" aria-pressed="false">Insights</button>
      <button type="button" data-ui-click="switchTicket" data-ui-args="timeline" aria-pressed="false">Timeline <span id="timeline-count" class="badge"></span></button>
    </div>
    <div class="ticket-section" data-section="workflow">
    <div class="actions">${ticketActions(i)}
      ${me.role === "ADMIN" ? `<button data-ui-click="openOverride" data-ui-args="${i.id}">Admin Override</button>` : ""}</div>
    ${canManualEscalate(i) ? `<button type="button" data-ui-click="manualEscalate" data-ui-args="${i.id}">Escalate one tier</button>` : ""}
    <section><h3>Description</h3><p class="prose">${esc(i.description)}</p></section>
    <dl class="meta">${facts.map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join("")}</dl>
    ${i.resolution ? `<section><h3>Resolution</h3><p class="prose">${esc(i.resolution)}</p></section>` : ""}
    ${i.location?.pin ? `<button type="button" data-ui-click="viewTicketLocation" data-ui-args="${i.id}">View campus pin</button>` : ""}
    <section><h3>Attachments</h3><div id="ticket-files" aria-live="polite">Loading attachments…</div>
      ${i.state !== "CLOSED" && ["EMPLOYEE","TECHNICIAN","ADMIN"].includes(me.role) ? attachmentPicker("detail") : ""}</section>
    ${i.comments.length ? `<section><h3>Notes and comments</h3>${i.comments.map(c => `<div class="activity">
      <b>${esc(c.kind.replaceAll("_"," "))}</b><div class="m">${esc(c.user)} · ${stamp(c.ts,true)}</div>
      <p class="prose">${esc(c.text)}</p></div>`).join("")}</section>` : ""}
    </div><div class="ticket-section" data-section="insights" hidden>
    <section><h3>Correlation</h3><div id="ticket-insights" aria-live="polite">Loading…</div></section>
    ${i.hint ? `<section><h3>Past fix</h3><p class="prose">${esc(i.hint)}</p></section>` : ""}
    </div><div class="ticket-section" data-section="timeline" hidden>
    <section><h3>Activity timeline</h3>
      <label>Event type<select data-ui-change="loadTimeline" data-ui-args="${i.id}|@value"><option value="">All events</option>
      ${timelineActions.map(action => `<option value="${action}">${words(action)}</option>`).join("")}</select></label>
      <div id="activity-timeline" aria-live="polite"></div></section>
    <section><h3>SLA and escalation history</h3><div id="sla-history" aria-live="polite">Loading…</div></section>
    </div></div>`);
  wireFilePicker("detail");
  loadInsights(id);
  loadAttachments(id);
  loadTimeline(id);
  loadSlaHistory(id);
}
function doReg(){ registration($("#u").value); }
async function doLogin(){const d=await api("login","POST",{id:$("#u").value,pw:$("#p").value},true);tok=d.token;me=d;applyAccountTheme();meta=await api("meta");tab="inc";await render();
 const target = location.hash.match(/^#incident=(INC[0-9]+)$/);
 if(target){ try { C[target[1]] = await api("incidents/"+target[1],"GET",undefined,true); showTicket(target[1]); }
 catch(error) { alert(error.message); } }
}
function ticketActions(i) {
 const b=[];
 if(me.role=="TECHNICIAN"||me.role=="ADMIN"){if(i.state=="ASSIGNED")b.push(["ack","Acknowledge"]);if(i.state=="ACKNOWLEDGED")b.push(["start","Start work"]);if(i.state=="IN_PROGRESS")b.push(["resolve","Resolve",1])}
 if(me.role!="TECHNICIAN"&&i.state=="RESOLVED"&&(me.role=="ADMIN"||i.reporter==me.id))b.push(["verify","Verify + feedback",1]);
 if(me.role=="ADMIN"){if(i.state=="VERIFIED")b.push(["close","Close"]);if(["CLASSIFIED","ASSIGNED","ACKNOWLEDGED","IN_PROGRESS"].includes(i.state))b.push(["assign","Assign…",2])}
 const live=i.state!=="CLOSED"&&(me.role==="ADMIN"||i.state!=="VERIFIED");
 if((me.role=="TECHNICIAN"||me.role=="ADMIN")&&live){b.push(["note","Add note",3]);if(i.state!="RESOLVED"||me.role==="ADMIN")b.push(["inforeq","Request info",4])}
 if((me.role=="ADMIN"||i.reporter==me.id)&&live)b.push(["reply","Reply / add info",5]);
 if((me.role=="ADMIN"||i.reporter==me.id)&&!["RESOLVED","VERIFIED","CLOSED"].includes(i.state))b.push(["edit","Edit",6]);
 return b.map(([action,label,p]) => `<button data-ui-click="act" data-ui-args="${i.id}|${action}|${p||0}">${label}</button>`).join("");
}
async function act(id,a,p){let body={};
 if(p==1){const t=prompt(a=="resolve"?"Resolution details":"Feedback");if(t===null)return;body.text=t}
 if(p==2){const t=prompt("Technician ID ("+meta.techs.join(", ")+")");if(!t)return;body.tech=t}
 if(p==3||p==4||p==5){const t=prompt(p==4?"What information do you need from the reporter?":p==5?"Your reply / additional information":"Investigation note");if(!t)return;body.text=t;if(p==5)body.kind="REPLY";a=p==4?"needs-info":"note"}
 if(p==6){const c=C[id];const d=prompt("Description",c.description);if(d===null)return;const im=prompt("Impact 1-4",c.impact);if(im===null)return;const ur=prompt("Urgency 1-4",c.urgency);if(ur===null)return;
  body={description:d,impact:im,urgency:ur};a="update"}
 await api(`incidents/${id}/${a}`,"POST",body);await refreshTickets(id)}

function segments(name, title, options) {
  return `<fieldset><legend>${title}</legend><div class="segments">${options.map(([value,label], index) =>
    `<label><input type="radio" name="${name}" value="${esc(value)}" ${index === 0 ? "checked" : ""}><span>${esc(label)}</span></label>`).join("")}</div></fieldset>`;
}

function vNew() {
  const scale = [[1,"Low"],[2,"Medium"],[3,"High"],[4,"Critical"]];
  beginReport();
  openDrawer("", "New incident", `<form class="drawer-body report-form" data-ui-submit="sub" data-ui-args="@event">
    <p class="m">The desk will classify your report, calculate priority and find an available technician.</p>
    <div id="duplicate-preview" class="m" aria-live="polite"></div>
    <label>Issue title<input id="t" name="title" maxlength="200" required></label>
    <div class="form-grid">
      <label>Affected service<select id="s" name="service">${Object.keys(meta.services).map(s => `<option>${esc(s)}</option>`).join("")}</select></label>
      <label>Category<select id="report-category" name="category"><option value="">Auto-detect</option>${options(meta.categories,"",words)}</select></label>
    </div>
    <p class="m" id="suggestion" aria-live="polite"></p>
    <label id="custom-wrap" hidden>Describe the Other category<input name="custom_category" maxlength="100" disabled></label>
    <div class="form-grid severity-grid">
    ${segments("impact","Impact",scale)}
    ${segments("urgency","Urgency",scale)}
    <label>Users affected<input id="n" name="users" type="number" min="1" max="1000000" value="1" required></label></div>
    <div id="priority-preview" class="preview-panel" aria-live="polite">Calculating priority and SLA…</div>
    <label>Description<textarea id="d" name="desc" rows="4" maxlength="5000" required
      placeholder="What stopped working and when did it start?"></textarea></label>
    ${voiceControls()}
    <section><div class="actions"><button type="button" data-ui-click="pickCampus">Select campus location</button>
      <button type="button" id="clear-location" data-ui-click="setReportLocation" data-ui-args="null" hidden>Clear location</button></div>
      <p class="m" id="report-location" aria-live="polite">Location not provided</p></section>
    ${attachmentPicker("report")}
    <p id="submit-status" class="m" aria-live="polite"></p>
    <div class="actions report-footer"><button class="p" type="submit">Submit issue</button><button type="button" data-ui-click="closeDialog" data-ui-args="drawer">Cancel</button></div>
    </form>`);
  $("#drawer").classList.add("report-dialog");
  wireReport();
  wireIntakePreview();
}

async function sub(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const button = event.submitter;
  const status = $("#submit-status");
  button.disabled = true;
  try {
    const data = Object.fromEntries(new FormData(form));
    data.location = reportLocation;
    data.files = await encodeFiles("report", status);
    status.textContent = "Submitting issue…";
    const i = data.files.length ? await uploadEvidence("incidents", data, status)
      : await api("incidents","POST",data,true);
    alert(`${i.id} created: ${i.priority}, ${i.category}, assigned to ${i.tech || "queue"}`
      + (i.related.length ? `\nPossibly related: ${i.related}` : ""));
    $("#drawer").close();
    tab = "inc";
    await render();
  } catch (error) {
    if (!status.isConnected) return;
    status.textContent = error.message;
    if (error.existingIncident) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Open " + error.existingIncident;
      button.addEventListener("click", () => openIncident(error.existingIncident));
      status.append(" ", button);
    }
  } finally {
    button.disabled = false;
  }
}

async function showRecords(endpoint, renderRecord, empty, reverse = false) {
  const view = $("#v");
  const data = await api(endpoint);
  if (view !== $("#v")) return;
  if (reverse) data.reverse();
  setMarkup(view, data.length ? `<div class="queue record-list">${data.map(renderRecord).join("")}</div>`
    : `<p class="queue empty m">${empty}</p>`);
}

function vAlerts() {
  return showRecords("alerts", a => `<p class="panel">${esc(a)}</p>`, "No alerts.");
}

function vWl() {
  setMarkup($("#v"), '<section class="panel" id="load"></section><section class="queue"><div id="results"></div></section>');
  return refreshTickets();
}
async function mon(){const r=await api("monitor","POST");alert(r.length?r.map(x=>x.join(": ")).join("\n"):"No new SLA events");vDash()}
async function vCfg(){const view=$("#v");const next=await api("meta");if(view!==$("#v"))return;meta=next;setMarkup(view, `<div class=panel><h3>Assignment strategy</h3><p class=m>Current: ${esc(meta.strategy)}</p>
 ${[["skill","Skill-based"],["load","Load-based"],["roundrobin","Round-robin"],["experience","Experience-based"]].map(([k,l])=>`<button data-ui-click="strat" data-ui-args="${k}">${l}</button>`).join(" ")}</div>
 <div class=panel><h3>Priority strategy</h3><p class=m>Current · ${esc(meta.priority_strategy)}</p>
 ${[["rule","Brief formula"],["weighted","Category-weighted"],["legacy","Historical weighted"]].map(([k,label]) =>
   `<button data-ui-click="selectPriorityStrategy" data-ui-args="${k}">${label}</button>`).join("")}
 <button data-ui-click="configureIntelligence">Relationship thresholds and category weights</button></div>
 <div class=panel><h3>SLA policies (minutes)</h3>${Object.entries(meta.sla).map(([k,v])=>`<div class=row><b>${k}</b><span>respond ${v[0]}</span><span>resolve ${v[1]}</span></div>`).join("")}
 <div class=row><label>Priority<select id=sp>${Object.keys(meta.sla).map(k=>`<option>${k}</option>`).join("")}</select></label><label>Response time (min)<input id=sr type=number placeholder="Response"></label><label>Resolution time (min)<input id=ss type=number placeholder="Resolution"></label></div><button class=p data-ui-click="slaSave">Save SLA</button><button type="button" data-ui-click="configureSlaCalendar">SLA working hours</button></div>
 <div class=panel><h3>Add IT service</h3><label>Service name<input id=sn placeholder="Service name"></label><label>Criticality<select id=sc><option value=1>Criticality 1</option><option value=2>2</option><option value=3>3</option><option value=4>4 (critical)</option></select></label><button class=p data-ui-click="svc">Add</button><div id="services-catalog"></div></div>
 <div class=panel><h3>Demo clock</h3><p class=m>Skip time forward to demonstrate SLA warnings, violations and escalation. Offset now: ${meta.offset_min} min</p>
 <div class=row><button data-ui-click="adv" data-ui-args="5">+5 min</button><button data-ui-click="adv" data-ui-args="30">+30 min</button><button data-ui-click="adv" data-ui-args="120">+2 h</button></div></div><div id="feature-config"></div>`); settingsExtensions(); catalogList("services")}
async function vUsers(){const view=$("#v");const [L,next,customLabels]=await Promise.all([api("users"),api("meta"),api("category-usage")]);if(view!==$("#v"))return;meta=next;
 setMarkup(view, `<div class=panel><h3>Add user</h3><label>User ID<input id=ui placeholder="User ID (2-20 chars)"></label><label>Full name<input id=un placeholder="Full name"></label><label>Password<input id=up type=password placeholder="Password (min 12)"></label>
 <label>Role<select id=ur><option>EMPLOYEE</option><option>TECHNICIAN</option><option>ADMIN</option><option>TEAM_LEAD</option><option>IT_MANAGER</option></select></label><label>Technician skills<input id=us placeholder="Skills (technician), comma separated e.g. NETWORK,DATABASE"></label><label>Technician capacity<input id=uc type=number placeholder="Capacity (technician)"></label>
 <button class=p data-ui-click="addUser">Create</button></div>
 <div class=panel><h3>Add / extend incident category</h3><label>Category name<input id=cn placeholder="Category name"></label><label>Keywords<input id=ck placeholder="Keywords, comma separated"></label><button class=p data-ui-click="addCat">Save</button><p class=m>Existing: ${meta.categories.map(esc).join(", ")}</p>${promotionChoices(customLabels)}</div>`
 +`<div class="queue record-list">`+L.map(x=>`<div class=panel><b>${esc(x.id)}</b> · ${esc(x.name)} <span class=m>${x.role}</span>${x.role=="TECHNICIAN"?`<div class=m>skills ${x.skills.map(esc).join(", ")||"—"} · capacity ${x.capacity} · exp ${x.exp} · ${x.available?"available":"UNAVAILABLE"}</div>
 <button data-ui-click="tog" data-ui-args="${x.id}|${!x.available}">${x.available?"Mark unavailable":"Mark available"}</button>`:""}</div>`).join("")+"</div>"); catalogList("categories")}
async function addUser(){await api("users","POST",{id:$("#ui").value,name:$("#un").value,pw:$("#up").value,role:$("#ur").value,skills:$("#us").value,capacity:$("#uc").value});refreshAdminView()}
async function tog(id,v){await api("tech","POST",{id,available:v});refreshAdminView()}
async function addCat(){await api("category","POST",{name:$("#cn").value,keywords:$("#ck").value});refreshAdminView()}
async function slaSave(){await api("sla","POST",{priority:$("#sp").value,response:$("#sr").value,resolution:$("#ss").value});vConsole()}
async function adv(m){await api("advance","POST",{minutes:m});vConsole()}
async function strat(k){await api("strategy","POST",{name:k});vConsole()}
async function svc(){await api("service","POST",{name:$("#sn").value,criticality:$("#sc").value});vConsole();alert("Service added")}

login();
