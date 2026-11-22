/* Workspace navigation, intake and operational reporting. */
let navCompact = false, consoleSection = "all";
let detailedView = "lifecycle";
const navigationIcons = {inc:"▤",dash:"◫",wl:"▥",topology:"⌘",routing:"⇢",alerts:"◈",audit:"≡",usr:"♙",cfg:"⚙",account:"♙",triage:"?"};
function shell(title) {
  const initials = me.name.trim().split(/\s+/).slice(0,2).map(word => word[0]).join("");
  return `<div class="workspace-shell ${navCompact ? "rail-compact" : ""}">
    <aside id="workspace-rail" class="workspace-rail" aria-label="Workspace navigation">
      <div class="rail-brand">${brand}<button type="button" class="rail-close" aria-label="Close menu" data-ui-click="toggleMenu">×</button></div>
      <nav aria-label="Workspace">${tabs().map(([key,label]) => `<button class="rail-item ${tab === key ? "on" : ""}"
        aria-label="${label}" title="${label}" ${tab === key ? 'aria-current="page"' : ""} data-ui-click="navigate" data-ui-args="${key}">
        <span class="rail-icon" aria-hidden="true">${navigationIcons[key]}</span><span class="rail-label">${label}</span>
        ${key === "inc" ? '<small data-summary="all"></small>' : ""}</button>`).join("")}
        <section class="rail-attention"><h2>Needs attention</h2>
        ${[["critical","Critical open","!"],["in_progress","In progress","↗"],["breached","SLA breached","◷"]].map(([key,label,icon]) =>
          `<button class="rail-item" title="${label}" aria-label="${label}" data-ui-click="attention" data-ui-args="${key}">
            <span class="rail-icon" aria-hidden="true">${icon}</span><span class="rail-label">${label}</span><small data-summary="${key}"></small>
          </button>`).join("")}</section>
      </nav>
      <div class="rail-account"><span class="avatar" aria-hidden="true">${esc(initials)}</span>
        <div class="rail-label"><b>${esc(me.name)}</b><small>${words(me.role)}</small></div></div>
      <div class="rail-actions"><button type="button" title="Sign out" data-ui-click="signOut">Logout</button>
      <button type="button" class="rail-collapse" aria-label="Toggle compact navigation" aria-pressed="${navCompact}" data-ui-click="toggleRail">⇤</button></div>
    </aside>
    <div class="workspace-main"><header class="workspace-top">
      <button type="button" class="menu-trigger" aria-label="Open menu" aria-controls="workspace-rail" aria-expanded="false" data-ui-click="toggleMenu">☰</button>
      <img class="mobile-emblem" alt="BITS Pilani emblem" src="/assets/bits-pilani-logo.png" width="130" height="120">
      <button type="button" class="search-launch" aria-label="Search incidents" data-ui-click="focusQueueSearch"><span aria-hidden="true">⌕</span> <span>Search incidents</span> <kbd>Ctrl K</kbd></button>
      <div class="top-controls">${themeControl()}
      <div class="bell-wrap"><button type="button" id="alert-bell" aria-label="Notifications" aria-expanded="false"
        aria-controls="alert-popover" data-ui-click="toggleNotifications"><span class="bell-icon" aria-hidden="true"></span><span id="unread-dot" hidden></span></button>
      <section class="alert-popover panel" id="alert-popover" aria-label="Notifications" hidden></section></div>
      ${me.role === "EMPLOYEE" ? '<button class="p" data-ui-click="vNew">+ Raise Issue</button>' : ""}</div>
    </header>
    <main class="workspace"><div class="page-heading"><h1>${title}</h1></div><div id="v"></div></main></div>
    <button type="button" class="rail-backdrop" aria-label="Close navigation" data-ui-click="toggleMenu" hidden></button></div>
    <dialog id="drawer" aria-labelledby="drawer-title"></dialog><dialog id="secondary" aria-labelledby="secondary-title"></dialog>`;
}
function navigate(key) { tab = key; render(); }
function toggleMenu() {
  const root = $(".workspace-shell");
  const open = root.classList.toggle("menu-open");
  $(".menu-trigger").setAttribute("aria-expanded", open);
  $(".rail-backdrop").hidden = !open;
  $(".workspace-main").inert = open;
  if (open) $(".rail-close").focus();
  else $(".menu-trigger").focus();
}
function toggleRail() {
  const root = $(".workspace-shell");
  navCompact = root.classList.toggle("rail-compact");
  $(".rail-collapse").setAttribute("aria-pressed",navCompact);
  try {localStorage.setItem("resolv-compact",navCompact ? "yes" : "no");} catch {}
}
async function signOut() {
  try { await api("logout","POST",{},true); }
  finally { if (tok) login(); }
}
function attention(key) {
  drilled = false;
  filters = {critical:{scope:"critical_open"}, in_progress:{status:"IN_PROGRESS"}, breached:{sla_status:"BREACHED"}}[key];
  navigate("inc");
}
function resetQueue() { drilled = false; filters = {}; vInc(); }
async function focusQueueSearch() {
  if (tab !== "inc") { tab = "inc"; await render(); }
  $("#search")?.focus();
}
function bindShell() {
  const root = $(".workspace-shell");
  refreshBell();
  startNotificationStream();
  api("queue-summary","GET",undefined,true).then(data => {
    if (!root.isConnected) return;
    root.querySelectorAll("[data-summary]").forEach(count => count.textContent = data[count.dataset.summary]);
  }).catch(() => {});
}
async function refreshBell() {
  const bell = $("#alert-bell");
  const current = tok;
  if (!bell || !current) return;
  try {
    const next = await api("notifications","GET",undefined,true);
    if (!bell.isConnected || tok !== current) return;
    if (lastAlert !== null && next[0]?.id > lastAlert && next[0].unread) {
      bell.classList.remove("new-alert");
      requestAnimationFrame(() => { if (bell.isConnected) bell.classList.add("new-alert"); });
    }
    lastAlert = next[0]?.id || lastAlert || 0;
    alertFeed = next;
    $("#unread-dot").hidden = !alertFeed.some(item => item.unread);
    if (!$("#alert-popover").hidden) drawNotifications();
  } catch {}
}
function toggleNotifications() {
  const panel = $("#alert-popover");
  panel.hidden = !panel.hidden;
  $("#alert-bell").setAttribute("aria-expanded", !panel.hidden);
  if (!panel.hidden) {
    drawNotifications();
    refreshBell();
  }
}
function drawNotifications() {
  const panel = $("#alert-popover");
  setMarkup(panel, `<div class="actions alert-top"><h2>Notifications</h2>
    <button type="button" data-ui-click="markNotifications" ${alertFeed.some(item=>item.unread) ? "" : "disabled"}>Mark all read</button></div>
    <div class="alert-list">${alertFeed.map(item => {
      const fact = item.message.match(/^\[(INC\d+)\]\s+([^:]+):\s*(.*)/);
      return `<article class="alert-item ${item.unread ? "unread" : ""}">
        <span aria-hidden="true">◈</span><div><b>${esc(fact ? words(fact[2]) : "Desk update")}</b>
        <p class="m">${esc(fact ? fact[1] + " · " + fact[3] : item.message)}</p></div>${stamp(item.ts,true)}</article>`;
    }).join("") || '<p class="m">No notifications.</p>'}</div><p id="bell-status" role="status"></p>`);
}
async function markNotifications() {
  try {
    const current = alertFeed[0]?.id || 0;
    alertFeed = await api("notifications","POST",{through:current},true);
    $("#unread-dot").hidden = !alertFeed.some(item=>item.unread);
    drawNotifications();
  } catch (error) { $("#bell-status").textContent = error.message; }
}
document.addEventListener("keydown", event => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k" && me) {
    event.preventDefault();
    if (!$("dialog[open]")) focusQueueSearch();
  }
  if (event.key === "Escape" && $(".menu-open")) toggleMenu();
  else if (event.key === "Escape" && $("#alert-popover") && !$("#alert-popover").hidden) toggleNotifications();
});
document.addEventListener("click", event => {
  const row = event.target.closest("tr[data-ticket]");
  if (row && !event.target.closest("button,a")) showTicket(row.dataset.ticket);
  if ($("#alert-popover") && !$("#alert-popover").hidden && !event.target.closest(".bell-wrap")) toggleNotifications();
});
function startNotificationStream() {
  if (!tok || notificationStream) return;
  const controller = notificationStream = new AbortController();
  const session = tok;
  async function listen() {
    let cursor = 0;
    while (tok === session && !controller.signal.aborted) {
      try {
        const response = await fetch("/api/notifications/events", {signal:controller.signal,
          headers:{Authorization:session,"Last-Event-ID":String(cursor)}});
        if (!response.ok) { sessionExpired(response.status, session); break; }
        const events = [...(await response.text()).matchAll(/^id: (\d+)$/gm)];
        if (events.length) {
          cursor = Number(events.at(-1)[1]);
          await refreshBell();
        }
      } catch {
        if (controller.signal.aborted) break;
        await new Promise(resolve => setTimeout(resolve, 4000));
      }
    }
    if (notificationStream === controller) notificationStream = null;
  }
  listen();
}
try {navCompact = localStorage.getItem("resolv-compact") === "yes";} catch {}

function registration(uid) {
  setMarkup(app, `<main class="login-wrap"><form class="login-panel" data-ui-submit="registerEmployee" data-ui-args="@event">
    ${brand}${themeControl()}<h1>Register as employee</h1>
    <label>Full name<input name="name" autocomplete="name" maxlength="120" required></label>
    <label>User ID<input name="id" autocomplete="username" maxlength="20" minlength="2" required value="${esc(uid)}"></label>
    <label>Password<input name="pw" type="password" autocomplete="new-password" minlength="12" maxlength="256" required></label>
    <p id="registration-status" role="status"></p>
    <button class="p" type="submit">Create account</button><button type="button" data-ui-click="login">Back to sign in</button>
    </form></main>`);
}
async function registerEmployee(event) {
  event.preventDefault();
  const material = Object.fromEntries(new FormData(event.currentTarget));
  const status = $("#registration-status");
  event.submitter.disabled = true;
  try {
    await api("register","POST",material,true);
    login();
    $("#u").value = material.id;
    $("#signin-status").textContent = "Account created. Sign in to report an issue.";
    $("#p").focus();
  } catch (error) { status.textContent = error.message; }
  finally { event.submitter.disabled = false; }
}

function wireIntakePreview() {
  const form = $(".report-form");
  const status = $("#priority-preview");
  const matches = $("#duplicate-preview");
  let latest = 0, wait;
  const update = () => {
    clearTimeout(wait);
    const revision = ++latest;
    wait = setTimeout(async () => {
      if (!form.isConnected || !$("#drawer").open) return;
      const values = Object.fromEntries(new FormData(form));
      const {title,desc,service,impact,urgency,users,category} = values;
      if (!service || !users) return;
      try {
        const preview = await api("intake-preview","POST",{title,desc,service,impact,urgency,users,category},true);
        if (!form.isConnected || revision !== latest) return;
        setMarkup(status, `<span class="badge ${preview.priority}">${preview.priority}</span>
          <b>Response ${preview.response} min · Resolution ${preview.resolution} min</b>
          <small>${esc(preview.reason)} ${preview.score === null ? "" : "Score " + preview.score + "."}</small>
          <small>Suggested category · ${esc(preview.category)} ${preview.alternatives[0] ? " · " + esc(preview.alternatives[0].reason) : ""}</small>
          ${preview.recommendations.length ? `<small>Routing · ${esc(preview.recommendations[0].choice)} · ${esc(preview.recommendations[0].reason)}</small>` : ""}`);
        setMarkup(matches, preview.matches.length
          ? "<b>Similar incidents you can access</b><ul>" + preview.matches.map(item =>
            `<li>${esc(item.id)} · ${esc(item.title)} · ${words(item.kind)}</li>`).join("") + "</ul>" : "");
      } catch (error) { if (form.isConnected && revision === latest) status.textContent = error.message; }
    }, 240);
  };
  form.addEventListener("input", update);
  form.addEventListener("change", update);
  update();
}
function switchTicket(view) {
  document.querySelectorAll(".ticket-section").forEach(section => section.hidden = section.dataset.section !== view);
  document.querySelectorAll(".drawer-switch button").forEach((button,index) =>
    button.setAttribute("aria-pressed",["workflow","insights","timeline"][index] === view));
}
async function openIncident(id) {
  try {
    C[id] = await api("incidents/" + id,"GET",undefined,true);
    showTicket(id);
  } catch (error) { alert(error.message); }
}
async function loadInsights(id) {
  const target = $("#ticket-insights");
  try {
    const result = await api("incidents/" + id + "/insights","GET",undefined,true);
    if (!target.isConnected) return;
    setMarkup(target, `<p class="m">${result.parent ? "Parent " + esc(result.parent) : "No parent linked"}</p>
      ${result.root_cause ? `<p><b>Possible shared dependency</b> · ${esc(result.root_cause.service)}
        <span class="m">${result.root_cause.active} open at the upstream service</span></p>` : ""}
      ${result.related.map(item => `<p><button type="button" class="ticket-link" data-ui-click="openIncident" data-ui-args="${item.id}">${esc(item.id)} · ${esc(item.title)}</button></p>`).join("")}
      ${me.role === "ADMIN" ? relationshipControls(id,result.relationships) : ""}
      ${me.role === "ADMIN" ? `<form class="actions" data-ui-submit="setParent" data-ui-args="@event|${id}">
        <label>Parent incident ID<input name="parent" placeholder="Leave empty to clear" maxlength="32" value="${esc(result.parent)}"></label>
        <button type="submit">Save parent</button><p id="parent-status" role="status"></p></form>` : ""}`);
  } catch (error) { if (target.isConnected) target.textContent = error.message; }
}
async function setParent(event,id) {
  event.preventDefault();
  const value = event.currentTarget.elements.parent.value.trim().toUpperCase();
  try {
    C[id] = await api("incidents/" + id + "/parent","POST",{parent:value || null},true);
    await loadInsights(id);
    $("#parent-status").textContent = "Parent link saved.";
  } catch (error) { $("#parent-status").textContent = error.message; }
}

function vRouting() {
  setMarkup($("#v"), `<div class="routing-grid">
    <section class="panel"><h2>Classify</h2><p>Keywords suggest a category. A reporter can choose a configured category or describe an issue under Other.</p></section>
    <section class="panel"><h2>Calculate priority</h2><p>The active priority strategy sets response and resolution targets. The brief formula uses impact, urgency, and service criticality. Weighted strategies also factor in the number of people affected.
      </p><p class="m">Current priority strategy · ${esc(meta.priority_strategy)}</p></section>
    <section class="panel"><h2>Assign</h2><p>The desk applies the current routing strategy to available technicians with capacity. Incidents without a match stay in the assignment queue.</p>
      <p class="m">Current strategy · ${esc(meta.strategy)}</p></section>
    <section class="panel"><h2>Monitor</h2><p>Workload, category and priority can bring forward an SLA warning. Missed deadlines trigger escalation through the support chain.</p></section>
    </div>`);
}
async function vTopology() {
  const target = $("#v");
  target.textContent = "Loading service topology…";
  try {
    const nodes = await api("topology","GET",undefined,true);
    if (!target.isConnected) return;
    setMarkup(target, `<div class="topology-grid"><section class="panel"><h2>Services</h2>
      <div id="topology-nodes" class="service-nodes"></div><p class="m">Incident counts follow your workspace permissions.</p></section>
      <section class="panel" id="topology-details"></section></div>
      ${me.role === "ADMIN" && nodes.length ? `<section class="panel"><h2>Configure dependencies</h2>
      <form class="feature-form" data-ui-submit="saveDependency" data-ui-args="@event">
      <div class="form-grid"><label>Dependent service<select name="service">${options(nodes.map(x=>x.name),"")}</select></label>
      <label>Upstream service<select name="upstream">${options(nodes.map(x=>x.name),"")}</select></label></div>
      <label>Action<select name="action"><option value="add">Add relationship</option><option value="remove">Remove relationship</option></select></label>
      <button type="submit">Save relationship</button><p id="dependency-status" role="status"></p>
      </form></section>` : ""}`);
    const rails = target.querySelector("#topology-nodes");
    if (!nodes.length) { rails.textContent = "No services configured."; return; }
    function show(node) {
      rails.querySelectorAll("button").forEach((button,index) => button.setAttribute("aria-pressed",nodes[index] === node));
      const links = items => items.map(name => `<button type="button" data-service="${esc(name)}">${esc(name)}</button>`).join("")
        || '<span class="m">None configured</span>';
      const details = $("#topology-details");
      setMarkup(details, `<h2>${esc(node.name)}</h2><p><b>${node.open}</b> open issues</p>
        <div class="dependency-graph"><div><h3>Downstream</h3>${links(node.downstream)}</div>
        <div class="dependency-node"><span aria-hidden="true">→</span><b>${esc(node.name)}</b><span aria-hidden="true">→</span></div>
        <div><h3>Upstream</h3>${links(node.upstream)}</div></div>
        <p class="m">Arrows point from a dependent service to its dependency.</p>`);
      details.querySelectorAll("[data-service]").forEach(button => button.onclick =
        () => show(nodes.find(item => item.name === button.dataset.service)));
    }
    for (const node of nodes) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = node.name + " · " + node.open;
      button.addEventListener("click",()=>show(node));
      rails.append(button);
    }
    show(nodes[0]);
  } catch (error) { if (target.isConnected) target.textContent = error.message; }
}
async function saveDependency(event) {
  event.preventDefault();
  const {service,upstream,action} = Object.fromEntries(new FormData(event.currentTarget));
  try {
    await api("topology","POST",{service,upstream,remove:action === "remove"},true);
    await vTopology();
    $("#dependency-status").textContent = "Dependency saved.";
  } catch (error) { $("#dependency-status").textContent = error.message; }
}
async function vConsole() {
  if (consoleSection === "categories" || consoleSection === "users") await vUsers();
  else await vCfg();
  if (tab !== "cfg") return;
  const target = $("#v");
  target.prepend(Object.assign(document.createElement("p"),{className:"m config-description",
    textContent:"Manage routing, services, categories, response targets and user access."}));
  const selections = [["all","Overview"],["services","IT Services"],["categories","Incident Categories"],
    ["sla","SLA Policies"],["users","User Directory"]];
  const control = document.createElement("div");
  control.className = "actions console-tabs";
  for (const [key,label] of selections) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.setAttribute("aria-pressed",key === consoleSection);
    button.onclick = () => { consoleSection = key; vConsole(); };
    control.append(button);
  }
  target.prepend(control);
  if (consoleSection !== "all") {
    const selection = {services:["Add IT service"],sla:["SLA policies (minutes)"],categories:["Add / extend incident category"],
      users:["Add user"]}[consoleSection];
    target.querySelectorAll(":scope > .panel, :scope > .record-list, #feature-config").forEach(element => {
      element.hidden = !selection.includes(element.querySelector("h3")?.textContent)
        && !(consoleSection === "users" && element.classList.contains("record-list"));
    });
    $("#v .panel:not([hidden]) input")?.focus({preventScroll:true});
  }
}

function summaryDashboard(data, target) {
  const m = data.metrics;
  const now = new Date();
  const greeting = now.getHours() < 12 ? "Good morning" : now.getHours() < 18 ? "Good afternoon" : "Good evening";
  const rate = m.total ? Math.round(100 * m.resolved / m.total) + "%" : "—";
  const tiles = [
    ["Open incidents",m.open,m.total + " incident total","scope","open"],
    ["Critical open",m.critical_open,"P1 incidents before resolution","scope","critical_open"],
    ["Past SLA",m.breached,m.at_risk + " at risk","sla_status","BREACHED"],
    ["Resolution rate",rate,m.resolved + " of " + m.total + " resolved","scope","resolved"],
  ];
  const priorityMax = Math.max(1,...Object.values(data.priority));
  setMarkup(target, `<header class="overview-greeting"><div><p class="m">${stamp(now.toISOString())}</p>
    <h2>${greeting}, ${esc(me.name.split(/\s+/)[0])}</h2>
    <p class="m">${m.open} open · ${m.critical_open} critical · ${m.breached} past SLA</p></div>
    ${me.role === "ADMIN" ? '<button type="button" data-ui-click="navigate" data-ui-args="cfg">Routing settings</button>' : ""}</header>
    <section class="kpi-strip" aria-label="Incident metrics">${tiles.map(([label,value,context,key,filter]) =>
      `<button type="button" data-drill-key="${key}" data-drill-value="${filter}">
        <span>${label}</span><b>${value}</b><small>${context}</small></button>`).join("")}</section>
    <div class="overview-panels">
      <section class="panel"><div class="panel-title"><h3>Recent incidents</h3><button type="button" data-drill-key="">All incidents</button></div>
        ${data.recent.map(i => `<button class="compact-item" type="button" data-ui-click="openIncident" data-ui-args="${i.id}">
          <span class="badge ${i.priority}">${i.priority}</span><span><b>${esc(i.title)}</b>
          <small>${i.id} · ${esc(i.service)}</small></span>${statusBadge(i)}</button>`).join("") || '<p class="m">No incidents match these filters.</p>'}
      </section>
      <section class="panel"><div class="panel-title"><h3>Closest to deadline</h3>
        <button type="button" data-drill-key="sla_status" data-drill-value="AT_RISK">SLA at risk</button></div>
        ${data.deadlines.map(i => `<button class="compact-item deadline-item" type="button" data-ui-click="openIncident" data-ui-args="${i.id}">
          <span class="badge ${i.priority}">${i.priority}</span><span><b>${esc(i.title)}</b><small>${stamp(i.deadline,true)}</small>
          <progress value="${i.progress}" max="100" aria-label="Share of SLA window elapsed"></progress></span>
          <span class="${i.sla}">${words(i.sla)}</span></button>`).join("") || '<p class="m">No open P1 or P2 incidents.</p>'}</section>
      <section class="panel"><h3>Priority mix</h3>
        ${["P1","P2","P3","P4"].map(priority => drillButton(priority,data.priority[priority] || 0,"priority",priority,priorityMax,priority)).join("")}</section>
      <section class="panel"><h3>Service health</h3>
        ${Object.entries(data.service_health).map(([name,service]) =>
          `<button class="compact-item service-item" data-drill-key="service" data-drill-value="${esc(name)}">
            <span><b>${esc(name)}</b><small>${service.resolved}/${service.total} resolved</small></span>
            <span>${Math.round(100 * (service.total-service.breached) / service.total)}% within SLA</span></button>`).join("")
            || '<p class="m">No service data for these filters.</p>'}</section>
    </div>
    <details class="panel advanced-analytics"><summary>Detailed analytics</summary><div id="advanced-content"></div></details>`);
  drawAnalyticsDetails(data, target);
  bindDashboardDrill(target);
}
function bindDashboardDrill(target) {
  target.querySelectorAll("[data-drill-key]").forEach(button => button.onclick = () => {
    const {drillKey,drillValue} = button.dataset;
    drill(drillKey ? {[drillKey]:drillValue} : {});
  });
}
function drawAnalyticsDetails(data, parent) {
  const target = parent.querySelector("#advanced-content");
  const choices = [["lifecycle","Lifecycle & capacity"],["hourly","Hourly workload"],["services","Services & activity"]];
  setMarkup(target, `<div class="actions detail-tabs" aria-label="Detailed analytics views"></div><div id="detail-chart"></div>`);
  for (const [id,label] of choices) {
    const button = document.createElement("button");
    button.type = "button"; button.textContent = label;
    button.setAttribute("aria-pressed",detailedView === id);
    button.onclick = () => { detailedView = id; drawAnalyticsDetails(data,parent); };
    target.firstElementChild.append(button);
  }
  const output = target.querySelector("#detail-chart");
  if (detailedView === "lifecycle") {
    const palette = ["#4b6ed3","#418688","#b67835","#766ba0","#b05d63","#56824e","#647384","#928353"];
    let offset = 0;
    const max = Math.max(1,data.metrics.total);
    const segments = Object.entries(data.status).map(([label,count],index) => {
      const start = offset; offset += 100 * count / max;
      return {label,count,color:palette[index % palette.length],start,end:offset};
    });
    setMarkup(output, `<div class="overview-panels"><section class="panel"><h3>Incident lifecycle</h3>
      ${segments.length ? `<div class="donut" style="--segments:${segments.map(x=>x.color+' '+x.start+'% '+x.end+'%').join(',')}"><b>${data.metrics.total}</b></div>
      <div class="chart-legend">${segments.map(x => `<button data-drill-key="status" data-drill-value="${x.label}">
        <i style="--legend-color:${x.color}" aria-hidden="true"></i>${words(x.label)} · ${x.count}</button>`).join("")}</div>` : '<p class="m">No incident lifecycle data.</p>'}
      </section><section class="panel"><h3>Technician capacity</h3>
      ${Object.entries(data.capacity).map(([id,capacity]) => {
        const load = data.workload[id] || 0;
        return `<button class="capacity-item" data-drill-key="tech" data-drill-value="${esc(id)}">
          <span>${esc(id)}</span><progress value="${Math.min(load,capacity)}" max="${capacity}" aria-label="Active workload"></progress>
          <small>${load} active · ${Math.max(0,capacity-load)} remaining</small></button>`;
      }).join("") || '<p class="m">No technicians configured.</p>'}</section></div>`);
  } else if (detailedView === "hourly") {
    const rows = Object.entries(data.hourly);
    setMarkup(output, `<section class="panel"><h3>Reports by hour</h3><p class="m">Creation hour in server time, grouped by current technician.</p>
      <div class="hourly-scroll">${rows.length ? `<table class="hourly-table"><thead><tr><th>Technician</th>
        ${Array.from({length:24},(_,hour)=>`<th>${String(hour).padStart(2,"0")}</th>`).join("")}</tr></thead><tbody>
        ${rows.map(([id,hours]) => `<tr><th scope="row">${esc(id)}</th>${hours.map(count=>
          `<td class="hour-band band-${Math.min(count,4)}">${count || "·"}</td>`).join("")}</tr>`).join("")}</tbody></table>`
          : '<p class="m">No assigned reports match these filters.</p>'}</div></section>`);
  } else {
    const max = Math.max(1,...Object.values(data.services));
    const peak = Math.max(1,...data.trend.map(item => item.count));
    setMarkup(output, `<div class="overview-panels"><section class="panel"><h3>Incidents by service</h3>
      ${Object.entries(data.service_health).map(([name,service]) => drillButton(name+" · "+service.critical+" P1",service.total,"service",name,max)).join("")
        || '<p class="m">No service activity.</p>'}</section>
      <section class="panel"><h3>Recent activity</h3>${data.activity.map(a => `<button class="compact-item service-item" data-ui-click="openIncident" data-ui-args="${a.id}">
        <span><b>${a.id} · ${words(a.action)}</b></span><small>${relativeTime(a.ts)}</small></button>`).join("") || '<p class="m">No recent activity.</p>'}</section></div>
      <section class="panel"><div class="panel-title"><h3>Incident volume</h3>
      <label>Group by<select data-ui-change="setTrendPeriod" data-ui-args="@value">${options(["daily","weekly","monthly"],chartPeriod,words)}</select></label></div>
      ${data.trend.length ? `<div class="trend-scroll"><div class="trend-series">${data.trend.map(item =>
        `<button class="trend-point" data-ui-click="drillPeriod" data-ui-args="${item.start}|${item.end}"><b>${item.count}</b>
        <span class="trend-column" style="height:${Math.max(2,100*item.count/peak)}px"></span><small>${item.start}</small></button>`).join("")}</div></div>` : '<p class="m">No reports in this range.</p>'}</section>`);
  }
  bindDashboardDrill(output);
}
function setTrendPeriod(value) { chartPeriod = value; vDash(); }
function drillPeriod(start,end) {
  drill({start:[dashboardFilters.start,start].filter(Boolean).sort().at(-1),
         end:[dashboardFilters.end,end].filter(Boolean).sort()[0]});
}
function relativeTime(timestamp) {
  const minutes = Math.round((new Date(timestamp).getTime() - Date.now()) / 60000);
  const fmt = new Intl.RelativeTimeFormat(undefined,{numeric:"auto"});
  return Math.abs(minutes) < 60 ? fmt.format(minutes,"minute")
    : Math.abs(minutes) < 1440 ? fmt.format(Math.round(minutes/60),"hour") : fmt.format(Math.round(minutes/1440),"day");
}

let browserSpeech;
function useBrowserSpeech() {
  const status = $("#voice-status");
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) { status.textContent = "Live dictation is unavailable in this browser."; return; }
  if (!confirm("Browser dictation may send audio to your browser provider. Continue?")) return;
  if (browserSpeech) browserSpeech.abort();
  const recognition = browserSpeech = new Recognition();
  recognition.lang = "en-IN";
  recognition.onstart = () => status.textContent = "Listening…";
  recognition.onresult = event => {
    const target = $("#d");
    if (!target?.isConnected || !$("#drawer")?.open) return;
    target.value += (target.value ? " " : "") + event.results[0][0].transcript;
    target.dispatchEvent(new Event("input",{bubbles:true}));
    status.textContent = "Review the text in Description before submitting.";
  };
  recognition.onerror = () => status.textContent = "Dictation failed. You can use a local voice note or type the description.";
  recognition.onend = () => { if (browserSpeech === recognition) browserSpeech = null; };
  try { recognition.start(); }
  catch { status.textContent = "Dictation could not start. You can still type the description."; }
}

function closeDialog(id) { document.getElementById(id)?.close(); }
function clearDashboardFilters() { dashboardFilters = {}; vDash(); }
function selectDashboard(section) { dashboardSection = section; vDash(); }
async function submitLogin(event) {
  event.preventDefault();
  const status = $("#signin-status");
  const button = event.submitter;
  if (button) button.disabled = true;
  try { await doLogin(); }
  catch (error) { if (status.isConnected) status.textContent = error.message; }
  finally { if (button) button.disabled = false; }
}
const uiHandlers = {
  accountForm, submitAccountForm, reviewAccountRequest, exportIncidents, manualEscalate, reviewRelationship,
  selectPriorityStrategy, configureIntelligence, configureSlaCalendar, submitTriage,
  act, addCat, addUser, adv, applyDashboard, attention, auditAction, auditPreset, chooseMapArea, clearDashboardFilters,
  clearMapPin, closeDialog, discardVoice, doReg, drillPeriod, exportAudit, fillVoiceDraft, filterAudit, filterCampusList,
  focusQueueSearch, loadAreaEditor, loadTimeline, login, markNotifications, mon, navigate, openIncident, openOverride,
  pickCampus, pinAtSelected, preparePromotion, registerEmployee, resetQueue, saveCampusArea, saveDependency, saveMap,
  selectDashboard, setFilter, setParent, setReportLocation, setTrendPeriod, showTicket, signOut, slaSave,
  startRecording, stopRecording, strat, sub, submitAuditAction, submitLogin, submitOverride, svc, switchTicket, tog,
  toggleHiddenAudit, toggleMenu, toggleNotifications, togglePinMode, toggleRail, toggleTheme, transcribeNote,
  updateRows, useBrowserSpeech, vDash, vNew, viewTicketLocation,
};
for (const type of ["click","change","input","submit"]) {
  const attr = "data-ui-" + type;
  document.addEventListener(type, event => {
    const control = event.target.closest("[" + attr + "]");
    const name = control?.getAttribute(attr);
    if (!name || !Object.hasOwn(uiHandlers,name)) return;
    const context = {
      currentTarget:control, submitter:event.submitter,
      preventDefault:() => event.preventDefault(),
    };
    function argument(value) {
      switch (value) {
        case "@event": return context;
        case "@element": return control;
        case "@value": return control.value;
        case "@upper": return control.value.trim().toUpperCase();
        case "@label": return control.dataset.label;
        case "null": return null;
        case "true": return true;
        case "false": return false;
        default: return /^\d+$/.test(value) ? Number(value) : value;
      }
    }
    const args = control.dataset.uiArgs?.split("|").map(argument) || [];
    Promise.resolve(uiHandlers[name](...args)).catch(() => {});
  });
}

async function catalogList(kind) {
  const target = $("#" + kind + "-catalog");
  if (!target) return;
  try {
    const current = (await api("catalog","GET",undefined,true))[kind];
    if (!target.isConnected) return;
    setMarkup(target, `${Object.entries(current).filter(([name])=>name !== "OTHER").map(([name,value]) => `<div class="catalog-row">
      <span><b>${esc(name)}</b><small>${kind === "services" ? "Criticality " + value : esc(value.join(", ") || "No keywords")}</small></span>
      <button type="button" data-edit>Edit</button><button type="button" data-remove>Remove</button></div>`).join("")}`);
    Object.entries(current).filter(([name])=>name !== "OTHER").forEach(([name,value],index) => {
      const row = target.children[index];
      row.querySelector("[data-edit]").onclick = () => editCatalog(kind,name,value);
      row.querySelector("[data-remove]").onclick = async () => {
        if (!confirm("Remove " + name + "? Entries referenced by incidents or dependencies are retained.")) return;
        try { await api("catalog","POST",{kind,name,action:"remove"},true); refreshAdminView(); }
        catch (error) { alert(error.message); }
      };
    });
  } catch (error) { if (target.isConnected) target.textContent = error.message; }
}
function editCatalog(kind,name,value) {
  const selector = kind === "services" ? `<label>Criticality<select name="value">${options([1,2,3,4],value)}</select></label>`
    : `<label>Keywords<input name="value" maxlength="4000" value="${esc(value.join(", "))}"></label>`;
  const dialog = modal("Edit " + name, `<form class="feature-form">${selector}
    <p id="catalog-status" role="status"></p><button type="submit">Save changes</button></form>`);
  dialog.querySelector(".feature-form").onsubmit = async event => {
    event.preventDefault();
    const raw = event.currentTarget.elements.value.value;
    try {
      await api("catalog","POST",{kind,name,action:"update",value:kind === "services" ? Number(raw) : raw},true);
      dialog.close();
      refreshAdminView();
    } catch (error) { $("#catalog-status").textContent = error.message; }
  };
}
function refreshAdminView() { return tab === "cfg" ? vConsole() : vUsers(); }
