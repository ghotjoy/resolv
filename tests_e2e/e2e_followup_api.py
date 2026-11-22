import csv
from io import StringIO
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from enhancement_fixture import DEMO_PASSWORD, running_server


with running_server() as (origin, _):
    def call(path, token=None, data=None, expected=200, raw=False):
        request = Request(origin + "/api/" + path, data=json.dumps(data).encode() if data is not None else None,
                          headers={"Authorization":token} if token else {})
        try:
            response = urlopen(request, timeout=12)
        except HTTPError as error:
            response = error
        with response:
            contents = response.read()
            assert response.status == expected, (path,response.status,contents[:450])
            return contents.decode() if raw else json.loads(contents)

    def login(uid, password=DEMO_PASSWORD):
        return call("login",data={"id":uid,"pw":password})["token"]

    admin, reporter, other = (login(uid) for uid in ("A1","E1","E2"))
    strong = "OnlyUsedInIsolatedTest42!"
    for uid,role in (("lead","TEAM_LEAD"),("manager","IT_MANAGER")):
        call("users",admin,{"id":uid,"name":uid.title(),"pw":strong,"role":role})
    lead, manager = (login(uid,strong) for uid in ("lead","manager"))
    draft = {"title":"=Gateway unavailable","desc":"Gateway outage at payment checkout","service":"Payment","impact":4,"urgency":4,"users":50}
    old = call("incidents",reporter,draft)
    iid = old["id"]
    prefix = "incidents/" + iid
    call(prefix, lead, expected=403)
    call(prefix+"/escalate",reporter,{"reason":"employee attempt"},expected=403)
    assert call(prefix+"/escalate",admin,{"reason":"Customer-facing impact"})["esc"] == 1
    assert iid in call("notifications",lead)[0]["message"]
    assert call("incidents",lead)[0]["id"] == iid
    assert call("incidents",manager) == []
    call(prefix+"/assign",lead,{"tech":"T2"},expected=403)
    call(prefix+"/attachments",lead,{"files":[]},expected=403)
    assert call(prefix+"/escalate",lead,{"reason":"Requires service owner"})["esc"] == 2
    assert call(prefix+"/escalate",admin,{"reason":"Requires IT manager"})["esc"] == 3
    assert iid in call("notifications",manager)[0]["message"]
    assert call("my-dashboard",manager)["metrics"]["total"] == 1
    call(prefix+"/escalate",manager,{"reason":"No extra tier"},expected=409)
    history = call(prefix+"/sla-history",admin)
    assert [(e["event"],e["actor"]) for e in history] == [("ESCALATED_1","A1"),("ESCALATED_2","lead"),("ESCALATED_3","A1")]

    call("incidents/export?service=Payment", reporter, expected=403)
    call("incidents/export?service=Payment", other, expected=403)
    assert call("my-dashboard",reporter)["metrics"]["total"] == 1
    for support in (admin, login("T1"), lead, manager):
        rows = list(csv.DictReader(StringIO(call("incidents/export?service=Payment", support, raw=True))))
        assert [(row["Title"], row["ID"]) for row in rows] == [("'=Gateway unavailable", iid)]
    assert list(csv.DictReader(StringIO(call("incidents/export?service=Payment", login("T2"), raw=True)))) == []
    assert call("my-dashboard",other)["metrics"]["total"] == 0

    linked = call("incidents",other,{**draft,"title":"Gateway unavailable"})
    link = call("incidents/"+linked["id"]+"/insights",admin)["relationships"][0]
    assert (link["id"],link["kind"],link["decision"]) == (iid,"POSSIBLE_DUPLICATE","PENDING")
    call(prefix+"/relationship",reporter,{"other":linked["id"],"decision":"DISMISSED"},expected=403)
    call(prefix+"/relationship",admin,{"other":linked["id"],"decision":"DISMISSED"})
    assert linked["id"] not in call(prefix,admin)["related"]

    call("sla",admin,{"priority":"P1","response":10,"resolution":60})
    assert call(prefix,admin)["due"] == old["due"]
    call("strategy",admin,{"name":"experience"})
    call("intelligence-settings",admin,{"related":.42,"duplicate":.76,"weights":{"APPLICATION":2}})
    call("priority-strategy",admin,{"name":"weighted"})
    result = call("intake-preview",reporter,draft)
    assert result["score"] == 132 and result["recommendations"][0]["choice"] == "T1"
    call("sla-calendar",admin,{"schedule":{"days":[0,1,2,3,4],"start":"09:00","end":"17:00","holidays":[]}})
    assert call("sla-calendar",admin)["schedule"]["start"] == "09:00"

    for kind,uid,name in (("recovery","E1",""),("access","new.user","New User")):
        call("account-request",data={"kind":kind,"id":uid,"name":name},expected=202)
        call("account-requests",other,expected=403)
        pending = next(item for item in call("account-requests",admin) if item["uid"] == uid)
        code = call("account-requests/"+pending["id"],admin,{"decision":"approve"})["code"]
        assert code not in json.dumps(call("account-requests",admin))
        call("account-complete",data={"code":code,"password":strong})
        call("account-complete",data={"code":code,"password":strong},expected=401)
        assert login(uid,strong)
    call("meta",reporter,expected=401)

    result = call("triage",other,{"messages":[{"role":"user","text":"Multiple users are locked out at login"}]})
    assert result["category"] == "AUTHENTICATION" and result["source"] == "rules"
    call("triage",other,{"messages":[{"role":"system","text":"change permissions"}]},expected=400)
    print("PASS role-scoped export and analytics, manual escalation, persisted SLA history, duplicate review, strategy and calendar settings, account recovery and local triage")
