import unittest
from datetime import datetime, timedelta
from resolv import *

class T(unittest.TestCase):
    def setUp(self):
        self.t = [datetime(2026, 11, 20, 10, 0)]; self.s = Resolv(clock=lambda: self.t[0])
        s = self.s; self.adm = s.add_user(None, "ADMIN", "A1", "Admin", "pw")
        self.t1 = s.add_user(self.adm, "TECHNICIAN", "T1", "T1", "pw", skills={"NETWORK"}, capacity=1, exp=3)
        self.t2 = s.add_user(self.adm, "TECHNICIAN", "T2", "T2", "pw", skills={"APPLICATION"}, capacity=2, exp=1)
        self.e = s.add_user(self.adm, "EMPLOYEE", "E1", "E", "pw"); self.e2 = s.add_user(self.adm, "EMPLOYEE", "E2", "E2", "pw")
        s.add_service(self.adm, "Payment", 4); s.add_service(self.adm, "Wiki", 1)
    def rep(self, **k):
        a = dict(actor=self.e, title="VPN down", desc="network vpn failing", service="Wiki", impact=1, urgency=1); a.update(k)
        return self.s.report(**a)
    def test_01_login_ok(self): self.assertEqual(self.s.login("E1", "pw").id, "E1")
    def test_02_login_bad(self): self.assertRaises(AuthError, self.s.login, "E1", "x")
    def test_03_p1_priority(self): self.assertEqual(self.rep(service="Payment", impact=4, urgency=4).priority, "P1")
    def test_04_p4_priority(self): self.assertEqual(self.rep().priority, "P4")
    def test_05_boundary_users_bump(self):
        self.assertEqual(WeightedScore().compute(3, 3, 3, 1), "P2"); self.assertEqual(WeightedScore().compute(3, 3, 4, 100), "P1")
    def test_06_invalid_input(self): self.assertRaises(ValidationError, self.rep, impact=9)
    def test_07_unknown_service(self): self.assertRaises(NotFound, self.rep, service="Nope")
    def test_08_auto_category_and_skill_assign(self):
        i = self.rep(); self.assertEqual((i.category, i.tech), ("NETWORK", "T1"))
    def test_09_invalid_transition(self):
        i = self.rep(); self.assertRaises(InvalidTransition, i.move, "CLOSED", self.t[0])
    def test_10_unauthorized_close(self):
        i = self.rep(); self.assertRaises(Unauthorized, self.s.close, self.e, i.id)
    def test_11_wrong_tech_blocked(self):
        i = self.rep(); self.assertRaises(Unauthorized, self.s.acknowledge, self.t2, i.id)
    def test_12_full_lifecycle_and_audit(self):
        i = self.rep(); s = self.s; s.acknowledge(self.t1, i.id); s.start(self.t1, i.id)
        s.resolve(self.t1, i.id, "reset vpn"); s.verify(self.e, i.id); s.close(self.adm, i.id)
        self.assertEqual(i.state, "CLOSED"); self.assertGreaterEqual(len(s.repo.audit_log(i.id)), 8)
    def test_13_workload_exceeded(self):
        self.rep(); j = self.rep(title="bucket", desc="storage bucket full")
        self.assertNotEqual(j.tech, "T1"); self.assertRaises(WorkloadExceeded, self.s.assign, self.adm, j.id, "T1")
    def test_14_duplicate_same_reporter(self): self.rep(); self.assertRaises(DuplicateIncident, self.rep)
    def test_15_related_detection(self):
        a = self.rep(service="Payment", impact=4, urgency=4, title="Payment application unavailable", desc="transactions fail")
        b = self.rep(actor=self.e2, service="Payment", impact=4, urgency=4, title="Payment transaction failing", desc="unable to pay")
        self.assertIn(a.id, b.related)
    def test_16_sla_violation_and_escalation(self):
        i = self.rep(service="Payment", impact=4, urgency=4); self.t[0] += timedelta(minutes=11)
        ev = self.s.monitor(); self.assertIn((i.id, "SLA_VIOLATED"), ev); self.assertIn((i.id, "ESCALATED"), ev)
        self.assertEqual(self.s.sla_status(i), "BREACHED")
    def test_17_search_filters(self):
        i = self.rep(); self.assertEqual(self.s.search(priority="P4", category="network", tech="T1"), [i])
        self.assertRaises(ValidationError, self.s.search, bogus=1)
    def test_18_persistence_reload(self):
        i = self.rep(); s2 = Resolv(repo=self.s.repo, clock=lambda: self.t[0]); self.assertEqual(s2.incidents[i.id].tech, "T1")
    def test_19_employee_cannot_add_service(self): self.assertRaises(Unauthorized, self.s.add_service, self.e, "X", 2)
    def test_20_comments_permissions(self):
        i = self.rep(); s = self.s
        s.comment(self.t1, i.id, "checking logs"); s.comment(self.t1, i.id, "which site?", "INFO_REQUEST")
        self.assertRaises(Unauthorized, s.comment, self.e, i.id, "x", "INFO_REQUEST")
        self.assertRaises(Unauthorized, s.comment, self.e2, i.id, "not mine")
        self.assertEqual(s.comment(self.e, i.id, "HQ").comments[-1]["kind"], "REPLY")
        self.assertRaises(ValidationError, s.comment, self.e, i.id, "  ")
    def test_21_update_recomputes_priority(self):
        i = self.rep(service="Payment"); self.assertEqual(i.priority, "P4")
        self.s.update(self.e, i.id, impact=4, urgency=4); self.assertEqual(i.priority, "P1")
        self.assertIn("PRIORITY_CHANGED", [r[2] for r in self.s.repo.audit_log(i.id)])
    def test_22_update_rules(self):
        i = self.rep(); self.assertRaises(Unauthorized, self.s.update, self.e2, i.id, impact=2)
        self.assertRaises(ValidationError, self.s.update, self.e, i.id, status="X")
        s = self.s; s.acknowledge(self.t1, i.id); s.start(self.t1, i.id); s.resolve(self.t1, i.id, "ok")
        self.assertRaises(InvalidTransition, s.update, self.e, i.id, impact=2)
    def test_23_sla_config_persisted(self):
        self.s.set_sla(self.adm, "P1", 1, 10); s2 = Resolv(repo=self.s.repo, clock=lambda: self.t[0])
        self.assertEqual(tuple(s2.sla["P1"]), (1, 10)); self.assertRaises(ValidationError, self.s.set_sla, self.adm, "P1", 20, 10)
        self.assertRaises(Unauthorized, self.s.set_sla, self.e, "P2", 1, 2)
    def test_24_demo_clock(self):
        a = self.s.now(); self.s.advance(self.adm, 30); self.assertEqual(self.s.now() - a, timedelta(minutes=30))
        self.assertRaises(Unauthorized, self.s.advance, self.e, 5)
    # ---- audit-driven additions ----
    def test_25_abstractions_enforced(self):
        self.assertRaises(TypeError, User, "x", "y", "pw"); self.assertRaises(TypeError, Repository)
        self.assertIsInstance(self.s.repo, Repository)
    def test_26_failed_resolve_does_not_mutate(self):
        i = self.rep(); self.assertRaises(InvalidTransition, self.s.resolve, self.t1, i.id, "premature")
        self.assertIsNone(i.resolution); self.assertEqual(i.state, "ASSIGNED")
    def _p1(self): return self.rep(service="Payment", impact=4, urgency=4)
    def test_27_sla_boundaries(self):
        i = self._p1(); t0 = self.t[0]  # P1: respond 5 min
        self.t[0] = t0 + timedelta(minutes=3); self.assertEqual(self.s.sla_status(i), "OK")
        self.t[0] = t0 + timedelta(minutes=4); self.assertEqual(self.s.sla_status(i), "AT_RISK")      # 80% of response window
        self.t[0] = t0 + timedelta(minutes=5); self.assertEqual(self.s.sla_status(i), "AT_RISK")      # exactly at deadline: not yet breached
        self.assertNotIn((i.id, "SLA_VIOLATED"), self.s.monitor()); self.assertIn("SLA_WARNING_resp", i.flags)
        self.t[0] = t0 + timedelta(minutes=5, seconds=1); self.assertEqual(self.s.sla_status(i), "BREACHED")
        self.assertIn((i.id, "SLA_VIOLATED"), self.s.monitor())
    def test_28_late_response_counts_as_breach(self):
        i = self._p1(); t0 = self.t[0]; self.t[0] = t0 + timedelta(minutes=10); tech = self.s.users[i.tech]
        self.s.acknowledge(tech, i.id); self.s.start(tech, i.id); self.t[0] = t0 + timedelta(minutes=20); self.s.resolve(tech, i.id, "fix")
        self.assertEqual(self.s.sla_status(i), "BREACHED")
        j = self.rep(actor=self.e2, service="Payment", impact=4, urgency=4, title="Checkout offline", desc="storefront down"); tech = self.s.users[j.tech]; self.s.acknowledge(tech, j.id); self.s.start(tech, j.id); self.s.resolve(tech, j.id, "fix")
        self.assertEqual(self.s.sla_status(j), "MET")
    def test_29_categorisation(self):
        self.assertEqual(self.s.suggest_category("Zoom audio broken"), "OTHER")
        self.s.add_category(self.adm, "analytics", ["analytics"]); self.assertEqual(self.s.suggest_category("analytics dashboard empty"), "ANALYTICS")
        self.assertEqual(self.s.suggest_category("cannot login to SSO"), "AUTHENTICATION")
    def test_30_duplicate_detection_assignment_example(self):
        a = self.rep(title="Payment application unavailable", desc="users cannot complete transactions", service="Payment")
        b = self.rep(actor=self.e2, title="Payment transaction failing", desc="errors on checkout", service="Payment")
        e3 = self.s.add_user(self.adm, "EMPLOYEE", "E3", "E3", "pw")
        c = self.rep(actor=e3, title="Unable to make payment", desc="payment page error", service="Payment")
        self.assertIn(a.id, b.related); self.assertTrue({a.id, b.id} & set(c.related))
    def test_31_unrelated_ignored_and_cross_service_match_is_related(self):
        self.s.add_service(self.adm, "Printing", 1)
        a = self.rep(service="Printing", title="Printer jam", desc="paper stuck")
        b = self.rep(actor=self.e2, service="Printing", title="Printer toner low", desc="needs new toner")
        self.assertEqual(b.related, [])
        c = self.rep(actor=self.e2, service="Wiki", title="Printer jam", desc="paper stuck"); self.assertEqual(c.related, [a.id]); self.assertEqual(self.s.incident_service.relationships(c)[0]["kind"], "RELATED")
    def test_32_unavailable_technicians_and_dispatch(self):
        s = self.s; s.update_technician(self.adm, "T1", available=False)
        self.assertEqual(self.rep().tech, "T2")                       # falls back to an available technician
        s.update_technician(self.adm, "T2", available=False)
        q = self.rep(actor=self.e2, title="wifi dead", desc="network wifi down")
        self.assertEqual((q.state, q.tech), ("CLASSIFIED", None)); self.assertIn("UNASSIGNED", [r[2] for r in s.repo.audit_log(q.id)])
        s.update_technician(self.adm, "T2", available=True); self.assertEqual(q.state, "ASSIGNED")   # queued incident auto-dispatched
    def test_33_registration_and_user_admin(self):
        s = self.s; s.register("newbie", "New B", "test-user-password-123"); self.assertEqual(s.login("newbie", "test-user-password-123").role, "EMPLOYEE")
        self.assertRaises(ValidationError, s.register, "newbie", "x", "test-user-password-123"); self.assertRaises(ValidationError, s.register, "a", "x", "test-user-password-123")
        self.assertRaises(ValidationError, s.register, "okid", "x", "123")
        self.assertRaises(Unauthorized, s.create_user, self.e, "TECHNICIAN", "t9", "T9", "test-user-password-123")
        s.create_user(self.adm, "TECHNICIAN", "tech9", "T9", "test-user-password-123", skills={"cloud"}, capacity=3); self.assertIn("CLOUD", s.users["tech9"].skills)
        self.assertTrue(all("hash" not in u for u in s.list_users(self.adm))); self.assertRaises(Unauthorized, s.list_users, self.e)
        self.assertRaises(ValidationError, s.update_technician, self.adm, "T1", capacity=0)
    def test_34_failing_notification_channel_is_isolated(self):
        def boom(env): raise RuntimeError("smtp down")
        self.s.email.gateway.deliver = boom; i = self.rep()
        self.assertEqual(i.state, "ASSIGNED"); self.assertTrue(self.s.inapp.for_user("E1"))
    def test_35_database_failure_and_notification_persistence(self):
        i = self.rep(); s2 = Resolv(repo=self.s.repo, clock=lambda: self.t[0]); self.assertTrue(s2.inapp.for_user("E1"))
        self.s.repo.c.close(); self.assertRaises(PersistenceError, self.rep, actor=self.e2, title="x y", desc="z w")
    def test_36_input_limits(self):
        self.assertRaises(ValidationError, self.rep, title="t" * 201); self.assertRaises(ValidationError, self.rep, desc="d" * 5001)
    def test_37_all_strategies_interchangeable(self):
        texts = [("vpn tunnel dropped", "remote staff lose connection"), ("wifi router rebooting", "floor three offline"), ("internet gateway latency", "pages load slowly")]
        for st, (ti, de) in zip((SkillBased(), LoadBased(), RoundRobin()), texts):
            self.s.set_assignment(self.adm, st); i = self.rep(actor=self.e2, title=ti, desc=de)
            self.assertIn(i.tech, ("T1", "T2", None)); self.assertEqual(type(self.s.assignment), type(st))
    def test_38_observer_contract(self):
        self.assertRaises(TypeError, self.s.bus.subscribe, object()); self.assertRaises(TypeError, Observer)
        seen = []
        class Spy(Observer):
            def update(self, e): seen.append(e.kind)
        self.s.bus.subscribe(Spy()); self.rep(); self.assertIn("CREATED", seen)
    # ---- refactoring additions: encapsulation, consistent state rules, polymorphism, swappable repository ----
    def test_39_encapsulation(self):
        i = self.rep()
        for attr, val in (("tech", "x"), ("priority", "P1"), ("resolution", "x"), ("state", "CLOSED"), ("title", "x")):
            with self.assertRaises(AttributeError): setattr(i, attr, val)
        i.comments.append("tamper"); i.related.append("INCX"); self.assertEqual((i.comments, i.related), ([], []))      # views are copies
        for attr in ("capacity", "available", "skills", "experience"):
            with self.assertRaises(AttributeError): setattr(self.t1, attr, 1)
        self.assertIsInstance(self.t1.skills, frozenset); self.assertNotIn("hash", self.t1.profile())
    def test_40_state_rules_are_consistent(self):
        i = self.rep(); s = self.s; s.acknowledge(self.t1, i.id); s.start(self.t1, i.id); s.resolve(self.t1, i.id, "ok")
        self.assertRaises(InvalidTransition, s.assign, self.adm, i.id, "T2")                       # resolved: no reassignment
        self.assertRaises(InvalidTransition, s.update, self.e, i.id, impact=2)
        s.verify(self.e, i.id); s.close(self.adm, i.id)
        self.assertRaises(InvalidTransition, s.assign, self.adm, i.id, "T2")                       # closed: nothing allowed
        self.assertRaises(InvalidTransition, s.comment, self.adm, i.id, "late note")
        self.assertRaises(InvalidTransition, s.acknowledge, self.adm, i.id)
        j = self.rep(actor=self.e2, title="vpn again", desc="tunnel drops hourly"); self.assertEqual(s.incident_service.get(j.id).state, "ASSIGNED")
        s.assign(self.adm, j.id, "T2"); self.assertEqual(j.tech, "T2")                              # reassignment while open is fine
    def test_41_polymorphic_ownership_rules(self):
        i = self.rep(); s = self.s
        self.assertTrue(self.e.acts_as_reporter_for(i)); self.assertFalse(self.e2.acts_as_reporter_for(i)); self.assertTrue(self.adm.acts_as_reporter_for(i))
        self.assertTrue(self.t1.can_work_on(i)); self.assertFalse(self.t2.can_work_on(i)); self.assertFalse(self.e.can_work_on(i)); self.assertTrue(self.adm.can_work_on(i))
        self.assertEqual(self.t1.comment_kinds(i), {"NOTE", "INFO_REQUEST"}); self.assertEqual(self.e2.comment_kinds(i), set())
        self.assertIn("skills", self.t1.profile()); self.assertNotIn("skills", self.e.profile())
    def test_42_updates_are_atomic(self):
        before = (self.t1.skills, self.t1.capacity)
        self.assertRaises(ValidationError, self.t1.update_profile, skills=["cloud"], capacity=0)
        self.assertEqual((self.t1.skills, self.t1.capacity), before)                                # nothing changed on failure
        i = self.rep(service="Payment"); snap = (i.title, i.impact, i.priority)
        self.assertRaises(ValidationError, i.revise, "new title", "d", 9, 1, 1, "P1"); self.assertEqual((i.title, i.impact, i.priority), snap)
        self.assertRaises(ValidationError, Incident, "X", "E1", "t", "d", "Wiki", "OTHER", 5, 1, 1, "P4", self.t[0])
    def test_43_repository_is_swappable(self):
        class MemoryRepository(Repository):
            def __init__(s): s._u, s._i, s._c, s._a, s._n = {}, {}, {}, [], []
            def save_user(s, u): s._u[u.id] = u
            def save_inc(s, i): s._i[i.id] = i
            def save_cfg(s, k, v): s._c[k] = v
            def audit(s, *row): s._a.append(row)
            def users(s): return list(s._u.values())
            def incidents(s): return list(s._i.values())
            def cfg(s, k): return s._c.get(k)
            def audit_log(s, inc=None): return [r for r in s._a if inc is None or r[3] == inc]
            def save_notification(s, user, ts, msg): s._n.append((user, ts, msg))
            def notifications(s, user): return [(ts, m) for u, ts, m in reversed(s._n) if u == user]
        m = Resolv(repo=MemoryRepository(), clock=lambda: self.t[0]); a = m.add_user(None, "ADMIN", "A1", "A", "pw"); e = m.add_user(a, "EMPLOYEE", "E1", "E", "pw")
        tc = m.add_user(a, "TECHNICIAN", "T1", "T", "pw", skills={"NETWORK"}); m.add_service(a, "Wiki", 1)
        i = m.report(e, "VPN down", "network vpn failing", "Wiki", 1, 1); m.acknowledge(tc, i.id); m.start(tc, i.id); m.resolve(tc, i.id, "reset"); m.verify(e, i.id); m.close(a, i.id)
        self.assertEqual(i.state, "CLOSED"); self.assertGreaterEqual(len(m.audit_log(a, i.id)), 8); self.assertTrue(m.inapp.for_user("E1"))
        m2 = Resolv(repo=m.repo, clock=lambda: self.t[0]); self.assertEqual(m2.incidents[i.id].state, "CLOSED")   # reload through the interface only
    def test_44_services_have_single_responsibilities(self):
        s = self.s
        for name in ("user_service", "config_service", "incident_service", "report_service", "sla_engine", "analyzer", "clock"): self.assertTrue(hasattr(s, name))
        self.assertIs(s.incident_service.assignment, s.assignment)
        s.set_assignment(self.adm, LoadBased()); self.assertIsInstance(s.incident_service.assignment, LoadBased)
        self.assertRaises(Unauthorized, s.report_service.audit_log, self.e); self.assertRaises(Unauthorized, s.report_service.technician_workload, self.e)
        self.assertEqual(s.technician_workload(self.t1)["capacity"], 1)
if __name__ == "__main__": unittest.main()
