import io
import json
from datetime import datetime
from unittest import TestCase

from accounts import AccountAccess
from resolv import AssignmentStrategy, AuthError, CategoryWeighted, Resolv, RuleBased, SQLiteRepository
from triage import GeminiTriage, Triage


class FollowupTests(TestCase):
    def test_priority_boundaries_and_strategy_settings_survive_reload(self):
        strategy = RuleBased()
        for impact,urgency,criticality,priority in [(3,3,3,"P1"),(3,2,3,"P2"),(2,1,3,"P3"),(1,1,3,"P4")]:
            self.assertEqual(strategy.compute(impact,urgency,criticality,50),priority)
        weighted = CategoryWeighted()
        self.assertEqual(weighted.result(3,2,3,100,1.5)["score"],35)
        self.assertEqual(weighted.result(3,2,3,500,1.5)["choice"],"P1")
        repo = SQLiteRepository()
        desk = Resolv(repo)
        actor = desk.add_user(None,"ADMIN","A1","Reviewer","test-only")
        desk.set_priority_strategy(actor,"weighted")
        self.assertIsInstance(Resolv(repo).priority,CategoryWeighted)

    def test_optional_routing_failure_keeps_incident_and_audit(self):
        class Unavailable(AssignmentStrategy):
            def pick(self, cands, inc, load):
                raise RuntimeError("routing component failed")
        desk = Resolv(assignment=Unavailable())
        admin = desk.add_user(None,"ADMIN","A1","Reviewer","test-only")
        reporter = desk.add_user(admin,"EMPLOYEE","E1","Reporter","test-only")
        desk.add_service(admin,"Network",3)
        incident = desk.report(reporter,"VPN error","VPN is unavailable","Network",4,4)
        self.assertEqual(incident.state,"CLASSIFIED")
        self.assertEqual([row[2] for row in desk.repo.audit_log(incident.id)],["CREATED","CLASSIFIED","UNASSIGNED"])
        self.assertEqual(desk.repo.incidents()[0].id,incident.id)
        self.assertTrue(desk.inapp.for_user(reporter.id))

    def test_calendar_skips_weekend_and_holiday_and_snapshots_policy(self):
        now = datetime(2026,11,20,16,58)
        desk = Resolv(clock=lambda:now)
        admin = desk.add_user(None,"ADMIN","A1","Reviewer","test-only")
        desk.add_service(admin,"Network",3)
        desk.incident_service.configure_calendar(admin,dict(days=[0,1,2,3,4],start="09:00",end="17:00",holidays=["2026-11-23"]))
        incident = desk.report(admin,"VPN down","Unable to connect using VPN","Network",4,4)
        self.assertEqual(desk.deadlines(incident)[0],datetime(2026,11,24,9,3))
        desk.set_sla(admin,"P1",20,40)
        desk.incident_service.configure_calendar(admin,None)
        self.assertEqual(desk.deadlines(incident)[0],datetime(2026,11,24,9,3))
        self.assertEqual(Resolv(desk.repo).deadlines(incident),desk.deadlines(incident))

    def test_recovery_code_expires_without_changing_password(self):
        desk = Resolv()
        admin = desk.add_user(None,"ADMIN","A1","Reviewer","test-only")
        reporter = desk.add_user(admin,"EMPLOYEE","E1","Reporter","test-only")
        now = [1000]
        access = AccountAccess(desk, lambda:now[0])
        access.request("recovery",reporter.id)
        code = access.review(admin,access.list_requests(admin)[0]["id"],"approve")["code"]
        now[0] += 901
        with self.assertRaises(AuthError):
            access.complete(code,"test-only-new-password!")
        self.assertEqual(desk.repo.user(reporter.id).password_hash,reporter.password_hash)

    def test_provider_adapter_is_opt_in_and_rejects_invalid_result(self):
        called = []
        def transport(request,timeout):
            called.append(json.loads(request.data))
            result = {"category":"NETWORK","impact":2,"urgency":3,"suggestion":"Check the network connection."}
            raw = {"candidates":[{"content":{"parts":[{"text":json.dumps(result)}]}}]}
            return io.BytesIO(json.dumps(raw).encode())
        triage = Triage(GeminiTriage("test-key",transport=transport))
        messages = triage.validate([{"role":"user","text":"Wifi unavailable"}])
        self.assertEqual(triage.suggest(messages,"NETWORK",["NETWORK"],False)["source"],"rules")
        self.assertFalse(called)
        self.assertEqual(triage.suggest(messages,"NETWORK",["NETWORK"],True)["source"],"provider")
        self.assertEqual(triage.suggest(messages,"OTHER",["OTHER"],True)["source"],"rules")
        self.assertEqual(called[0]["contents"][0]["parts"][0]["text"],"Wifi unavailable")
