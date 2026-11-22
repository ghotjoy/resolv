from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from contextlib import closing
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from enhancement_fixture import DEMO_PASSWORD, running_server


with running_server(RESOLV_CORS_ORIGINS="https://desk.example.test") as (base, files):
    def call(path, token=None, body=None, status=200, headers=None, raw=False):
        request = Request(base + "/api/" + path, data=json.dumps(body).encode() if body is not None else None,
                          headers=dict({"Authorization":token} if token else {}, **(headers or {})))
        try:
            response = urlopen(request, timeout=12)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            assert response.status == status, (path, response.status, content[:320])
            return (response.headers,content.decode()) if raw else json.loads(content)

    tokens = {uid:call("login", body={"id":uid,"pw":DEMO_PASSWORD})["token"] for uid in ("A1","E1","E2","T1","T2")}
    admin, reporter, other, wrong_tech, assigned = tokens.values()
    draft = {"title":"VPN outage","desc":"VPN tunnel breaks during routine access","service":"Network",
             "impact":4,"urgency":4,"users":200,"priority":"P4","criticality":1}
    preview = call("intake-preview",reporter,draft)
    assert (preview["priority"],preview["response"],preview["resolution"]) == ("P1", 5, 30)
    issue = call("incidents",reporter,draft)
    iid = issue["id"]
    prefix = "incidents/" + iid
    assert (issue["category"],issue["priority"],issue["state"],issue["tech"]) == ("NETWORK","P1","ASSIGNED","T2")
    call(prefix,other,status=403)
    call(prefix+"/note",other,{"text":"unauthorized"},status=403)
    call(prefix+"/needs-info",wrong_tech,{"text":"unauthorized"},status=403)
    call(prefix+"/resolve",assigned,{"text":"skip state"},status=409)
    assert call("intake-preview",other,draft)["matches"] == []
    assert call("intake-preview",reporter,draft)["matches"][0]["id"] == iid

    for token in (reporter, other):
        headers, stream = call("notifications/events",token,raw=True)
        assert headers.get_content_type() == "text/event-stream"
        assert (iid in stream) == (token == reporter)
    notices = call("notifications",reporter)
    assert notices and all(row["unread"] for row in notices)
    assert not any(row["unread"] for row in call("notifications",reporter,{"through":notices[0]["id"]}))

    call("advance",admin,{"minutes":4})
    assert (iid,"SLA_WARNING") in [tuple(event) for event in call("monitor",admin,{})]
    assert call(prefix,admin)["sla"] == "AT_RISK"
    for minute, level in [(2,1),(5,2),(5,3)]:
        call("advance",admin,{"minutes":minute})
        assert call("monitor",admin,{}).count([iid,"ESCALATED"]) == 1
        assert call(prefix,admin)["esc"] == level
    assert [event for event in call("monitor",admin,{}) if event[0] == iid] == []

    words = ["atlas","beacon","coral","dahlia","ember","fossil","granite","harbour"]
    def create(word):
        return call("incidents",other,{"title":word,"desc":word,"service":"Payment","impact":1,"urgency":1})
    with ThreadPoolExecutor(max_workers=len(words)) as pool:
        concurrent = list(pool.map(create, words))
    assert len({item["id"] for item in concurrent}) == len(words)
    with closing(sqlite3.connect(files / "test.db")) as storage, storage:
        assert storage.execute("SELECT count(*) FROM incidents").fetchone() == (len(words)+1,)
        assert storage.execute("SELECT count(*) FROM audit WHERE action='CREATED'").fetchone() == (len(words)+1,)

    pay = concurrent[0]["id"]
    call("topology",reporter,{"service":"Payment","upstream":"Network"},status=403)
    call("topology",admin,{"service":"Payment","upstream":"Network"})
    call("topology",admin,{"service":"Network","upstream":"Payment"},status=400)
    call("incidents/"+pay+"/parent",reporter,{"parent":iid},status=403)
    call("incidents/"+pay+"/parent",admin,{"parent":iid})
    assert call("incidents/"+pay+"/insights",admin)["root_cause"]["service"] == "Network"
    assert call("incidents/"+pay+"/insights",other)["parent"] is None
    call(prefix+"/parent",admin,{"parent":pay},status=400)

    call("catalog",reporter,status=403)
    call("catalog",admin,{"kind":"services","name":"Network","action":"remove"},status=400)
    call("category",admin,{"name":"Voice Equipment","keywords":"voice"})
    assert call("catalog",admin,{"kind":"categories","name":"VOICE EQUIPMENT","action":"update","value":"voice, mixer"})[
        "categories"]["VOICE EQUIPMENT"] == ["voice","mixer"]
    assert "VOICE EQUIPMENT" not in call("catalog",admin,{"kind":"categories","name":"VOICE EQUIPMENT","action":"remove"})["categories"]

    response_headers,_ = call("meta",admin,headers={"Origin":"https://desk.example.test"},raw=True)
    assert response_headers["Access-Control-Allow-Origin"] == "https://desk.example.test"
    assert "script-src 'self';" in response_headers["Content-Security-Policy"]
    call("meta",admin,status=403,headers={"Origin":"https://other.example.test"})
    for attempt in range(9):
        call("login",body={"id":"unknown","pw":"different"},status=401 if attempt<8 else 429,
             headers={"X-Forwarded-For":f"192.0.2.{attempt}"})
    with closing(sqlite3.connect(files / "test.db")) as storage, storage:
        storage.execute("DELETE FROM users WHERE id='E2'")
    call("meta",other,status=401)
    call("admin/reset-database",admin,{},status=404)
    print("PASS intake authority, role-scoped SSE, durable notification read state, concurrent IDs, escalation, topology and route security")
