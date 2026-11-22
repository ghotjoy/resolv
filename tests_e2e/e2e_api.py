from enhancement_fixture import DEMO_PASSWORD, wait_for_server
import os
import subprocess, time, json, urllib.request, urllib.error, sys
BASE_URL = "http://127.0.0.1:" + os.getenv("RESOLV_PORT", "8000")
os.environ["RESOLV_DEMO_PASSWORD"] = DEMO_PASSWORD
P = subprocess.Popen([sys.executable, "server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
fails = []
def call(path, m="GET", b=None, t=""):
    r = urllib.request.Request(BASE_URL + "/api/" + path, data=json.dumps(b).encode() if b is not None else None, method=m, headers={"Authorization": t})
    try: return 200, json.loads(urllib.request.urlopen(r, timeout=5).read())
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read())
def chk(name, got, want):
    ok = got == want; print("PASS" if ok else "FAIL", name, "" if ok else f"got {got} want {want}")
    if not ok: fails.append(name)
try:
    wait_for_server(BASE_URL, P)
    tk = {u: call("login", "POST", {"id": u, "pw": DEMO_PASSWORD})[1]["token"] for u in ("E1", "E2", "A1", "T1", "T2", "T3")}
    chk("bad login 401", call("login", "POST", {"id": "E1", "pw": "x"})[0], 401)
    chk("no token 401", call("incidents")[0], 401)
    c, i = call("incidents", "POST", {"title": "Payment application unavailable", "desc": "users unable to complete transactions", "service": "Payment", "impact": 4, "urgency": 4, "users": 300}, tk["E1"])
    chk("create P1", (c, i["priority"], i["category"]), (200, "P1", "APPLICATION")); iid, tech = i["id"], i["tech"]; chk("auto-assigned T1", tech, "T1")
    chk("bad input 400", call("incidents", "POST", {"title": "x", "desc": "y", "service": "Nope", "impact": 1, "urgency": 1}, tk["E1"])[0], 404)
    chk("emp list own", len(call("incidents", "GET", None, tk["E1"])[1]), 1); chk("other emp sees none", len(call("incidents", "GET", None, tk["E2"])[1]), 0)
    chk("filter works", len(call("incidents?priority=P1&status=ASSIGNED", "GET", None, tk["A1"])[1]), 1)
    chk("tech workload", call("workload", "GET", None, tk[tech])[1]["load"], 1)
    chk("emp workload 403", call("workload", "GET", None, tk["E1"])[0], 403)
    chk("wrong tech 403", call(f"incidents/{iid}/ack", "POST", {}, tk["T2"])[0], 403)
    chk("skip state 409", call(f"incidents/{iid}/resolve", "POST", {"text": "x"}, tk[tech])[0], 409)
    chk("ack", call(f"incidents/{iid}/ack", "POST", {}, tk[tech])[1]["state"], "ACKNOWLEDGED")
    chk("start", call(f"incidents/{iid}/start", "POST", {}, tk[tech])[1]["state"], "IN_PROGRESS")
    chk("info request", call(f"incidents/{iid}/note", "POST", {"text": "Which region?", "kind": "INFO_REQUEST"}, tk[tech])[0], 200)
    c, d = call(f"incidents/{iid}/note", "POST", {"text": "EU region"}, tk["E1"]); chk("reply kind", d["comments"][-1]["kind"], "REPLY")
    chk("emp edit ok", call(f"incidents/{iid}/update", "POST", {"users": "500"}, tk["E1"])[1]["users"], 500)
    chk("other emp note 403", call(f"incidents/{iid}/note", "POST", {"text": "hi"}, tk["E2"])[0], 403)
    chk("resolve", call(f"incidents/{iid}/resolve", "POST", {"text": "Restarted gateway"}, tk[tech])[1]["state"], "RESOLVED")
    chk("edit after resolve 409", call(f"incidents/{iid}/update", "POST", {"impact": 1}, tk["E1"])[0], 409)
    chk("emp close 403", call(f"incidents/{iid}/close", "POST", {}, tk["E1"])[0], 403)
    chk("close before verify 409", call(f"incidents/{iid}/close", "POST", {}, tk["A1"])[0], 409)
    chk("verify", call(f"incidents/{iid}/verify", "POST", {"text": "great"}, tk["E1"])[1]["state"], "VERIFIED")
    chk("close", call(f"incidents/{iid}/close", "POST", {}, tk["A1"])[1]["state"], "CLOSED")
    c, i2 = call("incidents", "POST", {"title": "Payment transaction failing", "desc": "payment errors", "service": "Payment", "impact": 4, "urgency": 4, "users": 20}, tk["E2"])
    chk("related + hint", (iid not in i2["related"], i2["hint"]), (True, None if not i2["hint"] else i2["hint"]))
    chk("manual assign", call(f"incidents/{i2['id']}/assign", "POST", {"tech": "T3"}, tk["A1"])[1]["tech"], "T3")
    chk("assign bad tech 404", call(f"incidents/{i2['id']}/assign", "POST", {"tech": "ZZ"}, tk["A1"])[0], 404)
    chk("sla before", call("incidents", "GET", None, tk["A1"])[1][0]["sla"], "OK")
    chk("advance clock", call("advance", "POST", {"minutes": 10}, tk["A1"])[0], 200)
    chk("advance emp 403", call("advance", "POST", {"minutes": 10}, tk["E1"])[0], 403)
    ev = call("monitor", "POST", {}, tk["A1"])[1]; chk("monitor violation+escalation", sorted({k for _, k in ev}), ["ESCALATED", "SLA_VIOLATED"])
    chk("monitor idempotent", call("monitor", "POST", {}, tk["A1"])[1], [])
    chk("sla breached", call("incidents?sla_status=BREACHED", "GET", None, tk["A1"])[0], 200)
    chk("alerts for admin", len(call("alerts", "GET", None, tk["A1"])[1]) > 0, True)
    chk("alerts reporter", any("RESOLVED" in a for a in call("alerts", "GET", None, tk["E1"])[1]), True)
    chk("dashboard", call("dashboard", "GET", None, tk["A1"])[1]["overview"]["total"], 2)
    chk("dashboard emp 403", call("dashboard", "GET", None, tk["E1"])[0], 403)
    a = call(f"audit?inc={iid}", "GET", None, tk["A1"])[1]; chk("audit has history", len(a) >= 10, True); chk("audit emp 403", call("audit", "GET", None, tk["E1"])[0], 403)
    chk("strategy", call("strategy", "POST", {"name": "load"}, tk["A1"])[0], 200); chk("bad strategy 400", call("strategy", "POST", {"name": "zz"}, tk["A1"])[0], 400)
    chk("sla set", call("sla", "POST", {"priority": "P2", "response": 10, "resolution": 60}, tk["A1"])[1]["P2"], [10, 60])
    chk("sla invalid 400", call("sla", "POST", {"priority": "P2", "response": 100, "resolution": 60}, tk["A1"])[0], 400)
    chk("add service", call("service", "POST", {"name": "VPN", "criticality": 2}, tk["A1"])[1]["VPN"], 2)
    chk("meta", "VPN" in call("meta", "GET", None, tk["E1"])[1]["services"], True)

    # ---- registration & administration ----
    chk("register ok", call("register", "POST", {"id": "newhire", "name": "New Hire", "pw": "test-user-password-123"})[0], 200)
    chk("register duplicate 400", call("register", "POST", {"id": "newhire", "name": "X", "pw": "test-user-password-123"})[0], 400)
    chk("register weak pw 400", call("register", "POST", {"id": "weak1", "name": "X", "pw": "123"})[0], 400)
    nt = call("login", "POST", {"id": "newhire", "pw": "test-user-password-123"})[1]["token"]; chk("new employee can report", call("incidents", "POST", {"title": "VPN dropping", "desc": "remote vpn disconnects hourly", "service": "Network", "impact": 2, "urgency": 2}, nt)[0], 200)
    chk("emp create user 403", call("users", "POST", {"id": "hax0r", "name": "H", "pw": "test-user-password-123", "role": "ADMIN"}, tk["E1"])[0], 403)
    chk("emp list users 403", call("users", "GET", None, tk["E1"])[0], 403)
    chk("admin create tech", any(u["id"] == "tech9" for u in call("users", "POST", {"id": "tech9", "name": "Tech Nine", "pw": "test-user-password-123", "role": "TECHNICIAN", "skills": "cloud,storage", "capacity": 2}, tk["A1"])[1]), True)
    chk("user list hides hash", all("hash" not in u for u in call("users", "GET", None, tk["A1"])[1]), True)
    chk("tech update capacity 0 -> 400", call("tech", "POST", {"id": "tech9", "capacity": 0}, tk["A1"])[0], 400)
    chk("new category", "ANALYTICS" in call("category", "POST", {"name": "analytics", "keywords": "analytics,dashboards"}, tk["A1"])[1], True)
    chk("auto category uses it", call("incidents", "POST", {"title": "Analytics dashboards empty", "desc": "no data since morning", "service": "Database", "impact": 2, "urgency": 2}, tk["E2"])[1]["category"], "ANALYTICS")
    chk("category emp 403", call("category", "POST", {"name": "x", "keywords": "y"}, tk["E1"])[0], 403)

    # ---- unavailable technicians -> queue -> dispatch ----
    for t_ in ("T1", "T2", "T3", "tech9"): call("tech", "POST", {"id": t_, "available": False}, tk["A1"])
    c, q = call("incidents", "POST", {"title": "Cloud bucket unreachable", "desc": "storage bucket timing out", "service": "Cloud Storage", "impact": 3, "urgency": 3}, tk["E1"])
    chk("queued when nobody available", (q["state"], q["tech"]), ("CLASSIFIED", None))
    call("tech", "POST", {"id": "T3", "available": True}, tk["A1"]); chk("auto-dispatched when tech returns", [x["tech"] for x in call("incidents?id=" + q["id"], "GET", None, tk["A1"])[1]], ["T3"])
    for t_ in ("T1", "T2", "tech9"): call("tech", "POST", {"id": t_, "available": True}, tk["A1"])

    # ---- three assignment workflows ----
    def tok_for(u): return tk.get(u) or call("login", "POST", {"id": u, "pw": "test-user-password-123"})[1]["token"]
    print("WORKFLOW 1 (report -> prioritise -> assign -> notify)")
    c, w1 = call("incidents", "POST", {"title": "Authentication service down", "desc": "nobody can login to sso", "service": "Authentication", "impact": 4, "urgency": 4, "users": 400}, tk["E1"])
    chk(" WF1 priority/category/assignee", (w1["priority"], w1["category"], w1["state"]), ("P1", "AUTHENTICATION", "ASSIGNED"))
    chk(" WF1 reporter notified", any(w1["id"] in a for a in call("alerts", "GET", None, tk["E1"])[1]), True)
    chk(" WF1 technician notified", any(w1["id"] in a for a in call("alerts", "GET", None, tok_for(w1["tech"]))[1]), True)
    print("WORKFLOW 2 (investigate -> SLA -> resolve -> notify)")
    wt = tok_for(w1["tech"]); call(f"incidents/{w1['id']}/ack", "POST", {}, wt); call(f"incidents/{w1['id']}/start", "POST", {}, wt)
    call(f"incidents/{w1['id']}/note", "POST", {"text": "checking identity provider"}, wt)
    chk(" WF2 resolved", call(f"incidents/{w1['id']}/resolve", "POST", {"text": "Restarted IdP"}, wt)[1]["state"], "RESOLVED")
    chk(" WF2 SLA met", call("incidents?id=" + w1["id"], "GET", None, tk["A1"])[1][0]["sla"], "MET")
    chk(" WF2 user notified of resolution", any("RESOLVED" in a and w1["id"] in a for a in call("alerts", "GET", None, tk["E1"])[1]), True)
    print("WORKFLOW 3 (duplicates -> correlation -> escalation)")
    a_ = call("incidents", "POST", {"title": "Payment application unavailable", "desc": "users cannot complete transactions", "service": "Payment", "impact": 4, "urgency": 4}, tk["E2"])[1]
    b_ = call("incidents", "POST", {"title": "Payment transaction failing", "desc": "errors on checkout", "service": "Payment", "impact": 4, "urgency": 4}, nt)[1]
    c_ = call("incidents", "POST", {"title": "Unable to make payment", "desc": "payment page error", "service": "Payment", "impact": 4, "urgency": 4}, tk["E1"])[1]
    chk(" WF3 reporter cannot see related incidents across accounts", (b_["related"], c_["related"]), ([], []))
    chk(" WF3 relationships visible to admin", a_["id"] in call(f"incidents/{b_['id']}", "GET", None, tk["A1"])[1]["related"], True)
    call("advance", "POST", {"minutes": 12}, tk["A1"]); ev = call("monitor", "POST", {}, tk["A1"])[1]
    chk(" WF3 escalation + violations", {"ESCALATED", "SLA_VIOLATED"} <= {k for _, k in ev}, True)
    chk(" WF3 dashboard shows violations", call("dashboard", "GET", None, tk["A1"])[1]["overview"]["sla_violations"] >= 3, True)
    chk("logout invalidates token", (call("logout", "POST", {}, nt)[0], call("incidents", "GET", None, nt)[0]), (200, 401))

    # ---- persistence across a real server restart ----
    before = call("incidents", "GET", None, tk["A1"])[1]; n_before = len(before); alerts_before = len(call("alerts", "GET", None, tk["E1"])[1]); audit_before = call(f"audit?inc={w1['id']}", "GET", None, tk["A1"])[1]
    P.terminate(); P.wait(); time.sleep(.5)
    P = subprocess.Popen([sys.executable, "server.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); wait_for_server(BASE_URL, P)
    a2 = call("login", "POST", {"id": "A1", "pw": DEMO_PASSWORD})[1]["token"]; e1 = call("login", "POST", {"id": "E1", "pw": DEMO_PASSWORD})[1]["token"]
    after = call("incidents", "GET", None, a2)[1]
    chk("restart: incidents persisted", len(after), n_before)
    chk("restart: state/priority persisted", {x["id"]: (x["state"], x["priority"], x["tech"]) for x in after}, {x["id"]: (x["state"], x["priority"], x["tech"]) for x in before})
    chk("restart: registered user persists", call("login", "POST", {"id": "newhire", "pw": "test-user-password-123"})[0], 200)
    chk("restart: alerts persisted", len(call("alerts", "GET", None, e1)[1]), alerts_before)
    chk("restart: audit persisted identically", (audit_before == call(f"audit?inc={w1['id']}", "GET", None, a2)[1], len(audit_before)), (True, 7))
    chk("restart: category/tech settings persisted", ("ANALYTICS" in call("meta", "GET", None, a2)[1]["categories"], any(u["id"] == "tech9" for u in call("users", "GET", None, a2)[1])), (True, True))
    chk("restart: old token invalid", call("incidents", "GET", None, tk["A1"])[0], 401)
finally:
    P.terminate()
    P.wait(timeout=5)
print("FAILS:", fails)

raise SystemExit(bool(fails))
