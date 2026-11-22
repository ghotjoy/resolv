"""Regression checks for evaluator login and actionable duplicate errors."""
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from enhancement_fixture import DEMO_PASSWORD, running_server


with running_server(RESOLV_TRUSTED_PROXIES="127.0.0.1") as (origin, _):
    def post(path, body, expected=200, token="", address="192.0.2.10"):
        req = Request(f"{origin}/api/{path}", json.dumps(body).encode(),
                      {"Authorization": token, "X-Forwarded-For": address}, method="POST")
        try:
            with urlopen(req, timeout=5) as response:
                status, payload = response.status, json.load(response)
        except HTTPError as error:
            status, payload = error.code, json.load(error)
        assert status == expected, (path, status, payload)
        return payload

    for attempt in range(9):
        post("login", {"id": "a1 ", "pw": "wrong"}, 401 if attempt < 8 else 429)
    credentials = {"id": " A1 ", "pw": DEMO_PASSWORD}
    post("login", credentials, 429)
    admin = post("login", credentials, address="192.0.2.11")["token"]
    for _ in range(41):
        post("login", {"id": "t1", "pw": DEMO_PASSWORD})
    post("users", {"id": "T4", "name": "New tech", "role": "TECHNICIAN", "pw": "short"}, 400, admin)
    post("users", {"id": "T4", "name": "New tech", "role": "TECHNICIAN", "pw": DEMO_PASSWORD}, token=admin)
    post("login", {"id": "t4", "pw": DEMO_PASSWORD})
    employee = post("login", {"id": "e1", "pw": DEMO_PASSWORD})["token"]
    draft = {"title": "Library network down", "desc": "Library Wi-Fi is unavailable to students",
             "service": "Network", "impact": 2, "urgency": 2}
    original = post("incidents", draft, token=employee)
    duplicate = post("incidents", draft, 400, employee)
    assert duplicate["existing_incident"] == original["id"]
    assert "DuplicateIncident" not in duplicate["error"]
    print("PASS login normalization, source-scoped throttling, password and ID policy, and duplicate guidance")
