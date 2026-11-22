import base64
import json
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from enhancement_fixture import DEMO_PASSWORD, running_server


def attachment(name, contents, mime="text/plain"):
    return {"name":name,"type":mime,"data":base64.b64encode(contents).decode()}


with running_server(RESOLV_FILE_BYTES="1024", RESOLV_VOSK_MODEL="") as (url, path):
    def call(endpoint, token=None, data=None, expect=200, binary=False):
        request = Request(url + "/api/" + endpoint, data=json.dumps(data).encode() if isinstance(data, dict) else data,
                          headers={"Authorization":token} if token else {})
        try:
            response = urlopen(request, timeout=15)
        except HTTPError as error:
            response = error
        with response:
            contents = response.read()
            assert response.status == expect, (endpoint, response.status, contents[:400])
            return contents if binary else json.loads(contents)

    users = {uid:call("login", data={"id":uid,"pw":DEMO_PASSWORD})["token"] for uid in ("E1","E2","T1","A1")}
    emp, other, tech, admin = (users[uid] for uid in users)
    meta = call("meta", emp)
    area = next(a for a in meta["campus"]["areas"] if a["id"] == "gandhi-bhavan")
    pin = {"lat":area["lat"],"lon":area["lon"],"version":meta["campus"]["version"]}
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3Z0AAAAASUVORK5CYII=")
    body = {"title":"Intercom outage","desc":"Intercom no longer connects","service":"Network","impact":2,"urgency":2,
            "category":"OTHER","location":{"area":area["id"],"pin":pin}}
    call("incidents",emp,body,expect=400)
    body["custom_category"] = "Intercom"
    for file in [attachment("data.exe",b"test"), attachment("big.txt",b"x"*1025), attachment("fake.png",b"test","image/png")]:
        call("incidents",emp,{**body,"files":[file]},expect=400)
    assert call("incidents", emp) == []
    incident = call("incidents",emp,{**body,"files":[attachment("evidence.txt",b"checked")]})
    iid = incident["id"]
    prefix = "incidents/" + iid
    assert incident["location"]["pin"] == pin
    assert incident["custom_category"] == "Intercom"
    assert call("incidents?q=intercom", emp)[0]["id"] == iid
    listing = call(prefix + "/attachments",emp)
    download = prefix + "/attachments/" + listing[0]["id"]
    assert call(download,emp,binary=True) == b"checked"
    call(download,other,expect=403)
    call(download,expect=401)
    extra = call(prefix + "/attachments",admin,{"files":[attachment("thumbnail.png",png,"image/png")]})
    assert call(prefix + "/attachments/" + extra[-1]["id"] + "?preview=1",admin,binary=True) == png
    call(prefix + "/attachments",emp,{"files":[attachment(f"{n}.txt",b"x") for n in range(4)]},expect=400)
    forbidden_tech = "T1" if incident["tech"] != "T1" else None
    if forbidden_tech:
        call(prefix + "/attachments", tech, expect=403)
    assigned = users.get(incident["tech"]) or call("login", data={"id":incident["tech"],"pw":DEMO_PASSWORD})["token"]
    assert call(download,assigned,binary=True) == b"checked"
    call(prefix + "/override",tech,{"reason":"close","changes":{"state":"CLOSED"}},expect=403)
    call(prefix + "/override",admin,{"changes":{"state":"CLOSED"}},expect=400)
    assert call(prefix + "/ack",admin,{})["state"] == "ACKNOWLEDGED"
    closed = call(prefix + "/override",admin,{"reason":"close","changes":{"state":"CLOSED","priority_override":"P2"}})
    reopened = call(prefix + "/override",admin,{"reason":"reopen","changes":{"state":"CLASSIFIED","title":"Intercom outage revised"}})
    assert reopened["closed"] == closed["closed"] and reopened["sla_started"] >= closed["closed"]
    assert reopened["priority_override"] == "P2"
    audit = call("audit?inc=" + iid,admin)
    closure = next(e for e in audit if e["action"] == "CLOSED")
    event = "audit/" + str(closure["id"])
    call(event,other,{"operation":"hide","reason":"test"},expect=403)
    call(event,admin,{"operation":"hide","reason":"hide while reviewing"})
    assert closure["id"] not in {row["id"] for row in call("audit?inc=" + iid,admin)}
    assert any(row["id"] == closure["id"] for row in call("audit/export",admin))
    hidden = call("audit?visibility=hidden&action=closed",admin)
    assert hidden[0]["id"] == closure["id"]
    call(event,admin,{"operation":"correct","reason":"Correction appended"})
    call(event,admin,{"operation":"restore","reason":"restored"})
    assert [row["id"] for row in call("audit?inc=" + iid + "&sort=newest",admin)] == sorted(
        [row["id"] for row in call("audit?inc=" + iid,admin)],reverse=True)
    missing = call("incidents",other,{**body,"title":"Campus printer","desc":"Printer jam on the second floor",
                                     "category":"HARDWARE","custom_category":"","location":None})
    assert missing["location"] is None
    query = urlencode({"area":area["id"],"category":"OTHER","period":"monthly","service":"Network"})
    dashboard = call("dashboard?" + query,admin)
    assert dashboard["metrics"]["total"] == dashboard["areas"][area["id"]] == dashboard["trend"][0]["count"] == 1
    assert call("dashboard?area=none",admin)["areas"]["none"] == 1
    assert call("dashboard?start=2000-01-01&end=2000-01-01",admin)["trend"] == []
    assert call("draft",emp,{"transcript":"Network is down at Gandhi Bhavan"})["area"] == area["id"]
    call("transcribe",emp,b"voice data",expect=503)
    assert call("category-usage",admin) == [{"label":"Intercom","count":1}]
    call("category-usage",tech,expect=403)
    call("draft",tech,{"transcript":"test"},expect=403)
    call("campus",other,{**area,"active":False},expect=403)
    call("campus",admin,{"id":area["id"],"active":False})
    assert call("incidents?area=" + area["id"],admin)[0]["location"]["pin"] == pin
    call("mail-status",emp,expect=403)
    assert call("mail-status",admin)["mode"] == "simulated"
    assert len(list((path / "evidence").iterdir())) == 2
    print("PASS admin override, immutable audit and export, file types and access, campus history, filtered analytics and draft authorization")
