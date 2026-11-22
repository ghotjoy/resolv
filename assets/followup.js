/* Account review, export, triage suggestions and escalation controls. */
function accountForm(kind) {
  const complete = kind === "complete";
  const heading = {access:"Request access",recovery:"Request password recovery",complete:"Use account code"}[kind];
  setMarkup(app, `<main class="login-wrap"><form class="login-panel" data-ui-submit="submitAccountForm" data-ui-args="@event|${kind}">
    ${brand}${themeControl()}<h1>${heading}</h1>
    ${complete ? `<label>One-time account code<input name="code" required maxlength="80" autocomplete="one-time-code"></label>
      <label>New password<input type="password" name="password" required minlength="12" maxlength="256" autocomplete="new-password"></label>`
      : `<label>User ID<input name="id" required maxlength="20" minlength="2" autocomplete="username"></label>
      ${kind === "access" ? '<label>Full name<input name="name" required maxlength="120" autocomplete="name"></label>' : ""}
      <label>Note for the administrator<textarea name="note" maxlength="500"></textarea></label>
      <p class="m">The desk will verify your identity and provide a code if the request is approved.</p>`}
    <button class="p" type="submit">${complete ? "Set password" : "Send request"}</button>
    <button type="button" data-ui-click="login">Back to sign in</button><p id="account-status" role="status"></p>
    </form></main>`);
}

async function submitAccountForm(event,kind) {
  event.preventDefault();
  const status = $("#account-status");
  try {
    const data = Object.fromEntries(new FormData(event.currentTarget));
    if (kind === "complete") {
      await api("account-complete","POST",data,true);
      login();
      $("#signin-status").textContent = "Password set. You can sign in.";
    } else {
      await api("account-request","POST",{...data,kind},true);
      status.textContent = "Request received. Contact the service desk to verify your identity.";
      event.submitter.disabled = true;
    }
  } catch (error) { status.textContent = error.message; }
}

async function showAccountRequests() {
  const view = $("#v");
  view.textContent = "Loading account requests…";
  try {
    const rows = await api("account-requests","GET",undefined,true);
    if (!view.isConnected) return;
    setMarkup(view, `<p class="m">Verify identity with the requester before approving. Share approved codes privately.</p>
      ${rows.map(row => `<section class="panel"><h3>${esc(row.uid)} · ${words(row.kind)}</h3>
        <p class="m">${stamp(row.requested_at,true)} · ${words(row.status)}${row.reviewer ? " · reviewed by " + esc(row.reviewer) : ""}</p>
        ${row.name ? `<p>${esc(row.name)}</p>` : ""}${row.note ? `<p class="prose">${esc(row.note)}</p>` : ""}
        ${row.status === "PENDING" ? `<div class="actions">
          <button type="button" data-ui-click="reviewAccountRequest" data-ui-args="${row.id}|approve">Approve</button>
          <button type="button" data-ui-click="reviewAccountRequest" data-ui-args="${row.id}|deny">Deny</button>
        </div>` : ""}</section>`).join("") || "<p>No account requests.</p>"}`);
  } catch (error) { if (view.isConnected) view.textContent = error.message; }
}

async function reviewAccountRequest(id,decision) {
  if (!confirm(decision === "approve" ? "Confirm that you verified this person's identity." : "Deny this request?")) return;
  try {
    const result = await api("account-requests/" + id,"POST",{decision},true);
    await showAccountRequests();
    if (result.code) modal("One-time code",`<p>Share this code privately with the verified requester. It expires in 15 minutes and can be used once.</p>
      <p><code>${esc(result.code)}</code></p>`);
  } catch (error) { alert(error.message); }
}

async function exportIncidents(source) {
  const selection = source === "dashboard" ? {...dashboardFilters} : {...filters,q:$("#search")?.value.trim()};
  const query = new URLSearchParams(Object.entries(selection).filter(([,value]) => value));
  try { saveBlob(await downloadBlob("incidents/export?" + query),"incidents.csv"); }
  catch (error) { alert(error.message); }
}

function canManualEscalate(incident) {
  return ["ADMIN","TEAM_LEAD","IT_MANAGER"].includes(me.role) && incident.esc < 3
    && !["CLOSED","RESOLVED","VERIFIED"].includes(incident.state);
}
async function manualEscalate(id) {
  const reason = prompt("Reason for escalating this incident one tier");
  if (!reason?.trim()) return;
  try {
    C[id] = await api("incidents/" + id + "/escalate","POST",{reason},true);
    showTicket(id);
  } catch (error) { alert(error.message); }
}
async function loadSlaHistory(id) {
  const output = $("#sla-history");
  try {
    const rows = await api("incidents/" + id + "/sla-history","GET",undefined,true);
    if (!output.isConnected) return;
    setMarkup(output,rows.map(row => `<article class="activity"><b>${words(row.event)}</b>
      <p>${esc(row.detail)}</p><small>${stamp(row.timestamp,true)} · ${esc(row.actor)}
      ${row.minutes_overdue ? " · " + row.minutes_overdue + " minutes overdue" : ""}</small></article>`).join("")
      || '<p class="m">No recorded SLA or escalation events.</p>');
  } catch (error) { if (output.isConnected) output.textContent = error.message; }
}

function relationshipControls(id,rows) {
  if (!rows?.length) return "";
  return `<section><h3>Suggested relationships</h3>${rows.map(row => `<div class="activity">
    <p><b>${esc(row.id)} · ${esc(row.title)}</b></p><p class="m">${words(row.kind)} · ${words(row.decision)}</p>
    <div class="actions">${[["CONFIRMED","Confirm link"],["DISMISSED","Dismiss link"],["PENDING","Reopen review"]]
      .filter(([status]) => status !== row.decision)
      .map(([status,label]) => `<button type="button" data-ui-click="reviewRelationship"
        data-ui-args="${id}|${row.id}|${status}">${label}</button>`).join("")}</div></div>`).join("")}</section>`;
}
async function reviewRelationship(id,other,decision) {
  try {
    await api("incidents/" + id + "/relationship","POST",{other,decision},true);
    await loadInsights(id);
  } catch (error) { alert(error.message); }
}

async function selectPriorityStrategy(name) {
  try { await api("priority-strategy","POST",{name},true); await vConsole(); }
  catch (error) { alert(error.message); }
}
async function configureIntelligence() {
  try {
    const settings = await api("intelligence-settings","GET",undefined,true);
    const dialog = modal("Intelligence settings", `<form class="feature-form">
      <label>Related match score<input name="related" type="number" min=".01" max="1" step=".01" required value="${settings.related}"></label>
      <label>Possible duplicate score<input name="duplicate" type="number" min=".01" max="1" step=".01" required value="${settings.duplicate}"></label>
      <h3>Category weights</h3><div class="form-grid">
      ${meta.categories.map((name,index) => `<label>${esc(name)}<input name="weight${index}" type="number" required
        min=".25" max="3" step=".05" value="${settings.weights[name] ?? 1}"></label>`).join("")}</div>
      <button type="submit">Save intelligence settings</button><p id="intelligence-status" role="status"></p></form>`);
    dialog.querySelector("form.feature-form").addEventListener("submit",async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const weights = Object.fromEntries(meta.categories.map((name,index) => [name,Number(form.get("weight"+index))]));
      try {
        await api("intelligence-settings","POST",{related:Number(form.get("related")),duplicate:Number(form.get("duplicate")),weights},true);
        dialog.close();
      } catch (error) { $("#intelligence-status").textContent = error.message; }
    });
  } catch (error) { alert(error.message); }
}

let triageMessages = [];
function showTriage() {
  triageMessages = [];
  setMarkup($("#v"), `<form class="panel" id="triage-form" data-ui-submit="submitTriage" data-ui-args="@event">
    <p class="m">Describe the issue to get category, severity and troubleshooting suggestions. Keep credentials and personal data out of your messages.</p>
    <label>Issue or follow-up<textarea name="text" required maxlength="2000" rows="4"></textarea></label>
    ${meta.triage_provider ? '<label><input type="checkbox" name="provider"> Share these messages and IT category names with Gemini</label>' : ""}
    <button class="p" type="submit">Get suggestions</button><p id="triage-status" role="status"></p>
    </form><section id="triage-result" class="panel" aria-live="polite" hidden></section>`);
}
async function submitTriage(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const status = $("#triage-status");
  const messages = [...triageMessages.slice(-10),{role:"user",text:form.elements.text.value}];
  event.submitter.disabled = true;
  try {
    const response = await api("triage","POST",{messages,use_provider:form.elements.provider?.checked === true},true);
    if (!form.isConnected) return;
    triageMessages = [...messages,{role:"assistant",text:response.suggestion}];
    form.elements.text.value = "";
    const output = $("#triage-result");
    output.hidden = false;
    setMarkup(output,`<h2>Suggested triage</h2><p>${esc(response.category)} · Impact ${response.impact} · Urgency ${response.urgency}</p>
      <p>${esc(response.suggestion)}</p><p class="m">${response.source === "provider" ? "Provider suggestion" : "Local rules"} · Review before filing.</p>`);
    status.textContent = "";
  } catch (error) { if (status.isConnected) status.textContent = error.message; }
  finally { event.submitter.disabled = false; }
}

async function configureSlaCalendar() {
  try {
    const {schedule} = await api("sla-calendar","GET",undefined,true);
    const days = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
    const dialog = modal("SLA working hours", `<form class="feature-form">
      <label>SLA clock<select name="mode"><option value="continuous">24/7</option>
        <option value="working" ${schedule ? "selected" : ""}>Working hours</option></select></label>
      <fieldset><legend>Days of the week</legend><div class="segments">${days.map((day,index) =>
        `<label><input type="checkbox" name="days" value="${index}"
          ${(schedule?.days || [0,1,2,3,4]).includes(index) ? "checked" : ""}><span>${day}</span></label>`).join("")}</div></fieldset>
      <div class="form-grid"><label>Opening time<input type="time" name="start" required value="${schedule?.start || "09:00"}"></label>
        <label>Closing time<input type="time" name="end" required value="${schedule?.end || "17:00"}"></label></div>
      <label>Holidays (YYYY-MM-DD, comma separated)<textarea name="holidays" rows="3">${esc(schedule?.holidays?.join(", "))}</textarea></label>
      <p class="m">Hours use server time. Changes apply to new SLA cycles.</p>
      <button type="submit">Save calendar</button><p id="calendar-status" role="status"></p></form>`);
    dialog.querySelector("form.feature-form").addEventListener("submit",async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const schedule = form.get("mode") === "working" ? {
        days:form.getAll("days").map(Number),start:form.get("start"),end:form.get("end"),
        holidays:form.get("holidays").split(",").map(value=>value.trim()).filter(Boolean),
      } : null;
      try {
        await api("sla-calendar","POST",{schedule},true);
        dialog.close();
      } catch (error) { $("#calendar-status").textContent = error.message; }
    });
  } catch (error) { alert(error.message); }
}
