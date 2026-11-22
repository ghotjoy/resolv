import json
from datetime import datetime, timedelta
from pathlib import Path
import smtplib
import socketserver
import sqlite3
from tempfile import TemporaryDirectory
from contextlib import ExitStack, closing
from threading import Thread
import unittest

from enhancements import CampusLocations, IncidentDraftBuilder, analytics
from resolv import Event, Resolv, InvalidTransition, MissingSLA, SQLiteRepository, Unauthorized, ValidationError
from mailer import EVENTS, SMTPNotifier


class EnhancementTests(unittest.TestCase):
    def setUp(self):
        self.time = datetime(2026, 11, 22, 12)
        self.s = Resolv(clock=lambda: self.time)
        self.admin = self.s.add_user(None, "ADMIN", "A1", "Admin", "pw")
        self.reporter = self.s.add_user(self.admin, "EMPLOYEE", "E1", "Reporter", "pw")
        self.t1 = self.s.add_user(self.admin, "TECHNICIAN", "T1", "Technician", "pw", skills=["NETWORK"])
        self.t2 = self.s.add_user(self.admin, "TECHNICIAN", "T2", "Second technician", "pw")
        self.s.add_service(self.admin, "Network", 3)
        self.campus = CampusLocations(self.s.repo, self.s.now)

    def report(self, **fields):
        return self.s.report(self.reporter, "VPN error", "VPN is unavailable", "Network", 2, 2, **fields)

    def test_override_validates_role_state_and_reason_before_mutating(self):
        incident = self.report()
        with self.assertRaises(Unauthorized):
            self.s.override(self.t1, incident.id, "reason", {"state": "CLOSED"})
        with self.assertRaises(InvalidTransition):
            self.s.start(self.t1, incident.id)
        for reason, changes in [(" ", {"state":"CLOSED"}), ("reason", {"state":"missing"}),
                                ("reason", {"title":"Updated", "tech":"E1"})]:
            with self.assertRaises(ValidationError):
                self.s.override(self.admin, incident.id, reason, changes)
        self.assertEqual((incident.state, incident.title), ("ASSIGNED", "VPN error"))
        self.s.acknowledge(self.admin, incident.id)
        self.assertEqual(incident.state, "ACKNOWLEDGED")

    def test_closed_override_preserves_history_and_restarts_sla(self):
        incident = self.report()
        self.s.override(self.admin, incident.id, "close stale incident", {"state":"CLOSED"})
        old_closure = incident.closed
        self.time += timedelta(days=1)
        self.s.override(self.admin, incident.id, "service failed again",
                        {"state":"ASSIGNED","tech":"T2","priority_override":"P1","description":"Reappeared"})
        self.assertEqual((incident.tech, incident.state, incident.priority), ("T2","ASSIGNED","P1"))
        self.assertEqual((incident.closed, incident.sla_started, incident.responded), (old_closure,self.time,None))
        self.assertEqual(self.s.deadlines(incident)[0], self.time + timedelta(minutes=5))
        self.s.update(self.reporter, incident.id, impact=1, urgency=1)
        self.assertEqual(incident.priority, "P1")
        self.s.override(self.admin, incident.id, "use score", {"priority_override":None})
        self.assertEqual(incident.priority, "P4")
        events = self.s.repo.audit_records(inc=incident.id)
        self.assertEqual(sum(e["action"] == "CLOSED" for e in events), 1)
        change = json.loads(next(e["desc"] for e in events if "service failed again" in e["desc"]))
        self.assertEqual(change["changes"]["tech"], ["T1","T2"])
        self.assertEqual(change["changes"]["state"], ["CLOSED","ASSIGNED"])
        self.assertEqual(self.s.repo.incidents()[0].closed, old_closure)

    def test_resolution_breach_escalates_one_event_for_each_level(self):
        self.s.set_sla(self.admin, "P3", 1, 2)
        incident = self.report()
        self.s.acknowledge(self.admin, incident.id)
        self.time += timedelta(minutes=7)
        self.assertEqual(self.s.monitor().count((incident.id, "ESCALATED")), 3)
        self.assertIn("SLA_VIOLATED_res", incident.flags)
        self.assertNotIn("SLA_VIOLATED_resp", incident.flags)
        self.assertEqual(self.s.monitor(), [])
        self.assertEqual(incident.escalation_level, 3)

    def test_missing_sla_rejects_intake_without_writing_incident_or_events(self):
        self.s.sla.clear()
        with self.assertRaises(MissingSLA):
            self.report()
        self.assertEqual(self.s.repo.incidents(), [])
        self.assertEqual(self.s.repo.audit_log(), [])
        self.assertEqual(self.s.inapp.for_user(self.reporter.id), [])

    def test_audit_annotations_and_append_only_storage(self):
        incident = self.report()
        event = self.s.repo.audit_records(inc=incident.id)[0]
        with self.assertRaises(Unauthorized):
            self.s.report_service.annotate_audit(self.reporter, event["id"], "hide", "note", self.time)
        for op in ("hide","correct"):
            self.s.report_service.annotate_audit(self.admin, event["id"], op, "note", self.time)
        hidden, = self.s.repo.audit_records(visibility="hidden", user="e1", q="Created")
        self.assertEqual(hidden["id"], event["id"])
        self.assertNotIn(event["id"], [row["id"] for row in self.s.repo.audit_records()])
        with self.assertRaises(sqlite3.Error):
            self.s.repo.c.execute("DELETE FROM audit WHERE id=?", (event["id"],))
        self.s.repo.c.rollback()
        self.s.report_service.annotate_audit(self.admin, event["id"], "restore", "review complete", self.time)
        self.assertEqual(self.s.repo.audit_records(inc=incident.id)[0], event)
        annotations = [row for row in self.s.repo.audit_records(visibility="all") if row["target"]]
        self.assertEqual([row["action"] for row in annotations], ["AUDIT_HIDDEN","AUDIT_CORRECTION","AUDIT_RESTORED"])

    def test_legacy_audit_ids_survive_migration_and_vacuum(self):
        with TemporaryDirectory() as tmp, ExitStack() as cleanup:
            path = Path(tmp) / "legacy.db"
            with closing(sqlite3.connect(path)) as legacy, legacy:
                legacy.execute("CREATE TABLE audit(ts TEXT,user TEXT,action TEXT,inc TEXT,prev TEXT,new TEXT,descr TEXT)")
                legacy.execute("INSERT INTO audit VALUES('2026-11-22','E1','CREATED','INC1001',NULL,'NEW','reported')")
            repo = SQLiteRepository(path)
            cleanup.callback(repo.c.close)
            repo.audit_annotation(1, "2026-11-22", "A1", "AUDIT_HIDDEN", "reviewed")
            repo.c.execute("VACUUM")
            reopened = SQLiteRepository(path)
            cleanup.callback(reopened.c.close)
            event, = reopened.audit_records(visibility="hidden")
            self.assertEqual((event["id"],event["desc"]), (1,"reported"))

    def test_custom_category_validation_and_search(self):
        with self.assertRaises(ValidationError):
            self.report(category="OTHER", custom_category=" ")
        incident = self.report(category="Other", custom_category="Audio mixer")
        self.assertEqual(self.s.search(q="mixer"), [incident])
        self.assertEqual((incident.category, incident.custom_category), ("OTHER","Audio mixer"))
        self.s.add_category(self.admin, "Audio mixer", ["mixer"])
        with self.assertRaises(ValidationError):
            self.s.add_category(self.admin, " AUDIO MIXER ", [])
        self.assertEqual(self.s.repo.incidents()[0].custom_category, "Audio mixer")

    def test_location_inventory_filter_and_draft(self):
        self.assertEqual(len(self.campus.areas), 54)
        self.assertEqual(sum(area["group"] == "Hostels" for area in self.campus.areas.values()), 12)
        area = self.campus.areas["gandhi-bhavan"]
        pin = {"lat":area["lat"],"lon":area["lon"],"version":self.campus.version}
        loc = self.campus.validate({"area":area["id"],"pin":pin})
        report = self.report(category="HARDWARE", location=loc)
        self.assertEqual(report.category,"HARDWARE")
        self.campus.update(self.admin, {"id":area["id"],"active":False})
        with self.assertRaises(ValidationError):
            self.campus.validate(loc)
        self.assertEqual(self.s.search(area=area["id"]), [report])
        draft = IncidentDraftBuilder(self.s, self.campus).build("network error near Ganga Bhavan")
        self.assertEqual((draft["area"],draft["category"],draft["service"]), ("ganga-bhavan","NETWORK","Network"))
        self.assertNotIn("impact", draft)

    def test_analytics_uses_filtered_unique_incidents_and_distinct_sla_states(self):
        pre = self.report()
        self.time += timedelta(minutes=61)
        self.assertEqual(self.s.sla_status(pre), "BREACHED")
        self.time += timedelta(days=1)
        fresh = self.s.report(self.reporter, "WAN outage", "WAN gate down", "Network", 4, 4)
        self.time += timedelta(minutes=4)
        self.assertEqual(self.s.sla_status(fresh), "AT_RISK")
        data = analytics(self.s,self.admin,{},"daily")
        self.assertEqual((data["metrics"]["at_risk"], data["metrics"]["breached"]), (1,1))
        self.s.override(self.admin,pre.id,"closing",{"state":"CLOSED"})
        self.s.override(self.admin,pre.id,"reopening",{"state":"ASSIGNED"})
        data = analytics(self.s,self.admin,{"start":fresh.created.date().isoformat()},"weekly")
        self.assertEqual((data["metrics"]["total"], data["areas"]["none"], sum(p["count"] for p in data["trend"])), (1,1,1))


class MailTests(unittest.TestCase):
    def test_smtp_events_delivery_and_retry_diagnostics(self):
        received = []

        class Inbox(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"220 local mail test\r\n")
                while line := self.rfile.readline():
                    command = line.split()[0].upper()
                    if command == b"DATA":
                        self.wfile.write(b"354 send message\r\n")
                        lines = iter(self.rfile.readline, b".\r\n")
                        received.append(b"".join(lines))
                    if command == b"QUIT":
                        self.wfile.write(b"221 bye\r\n")
                        return
                    self.wfile.write(b"250 ok\r\n")

        with TemporaryDirectory() as tmp, socketserver.TCPServer(("127.0.0.1",0), Inbox) as inbox, ExitStack() as cleanup:
            thread = Thread(target=inbox.serve_forever, daemon=True)
            thread.start()
            try:
                path = str(Path(tmp) / "mail.db")
                repo = SQLiteRepository(path)
                cleanup.callback(repo.c.close)
                smtp = SMTPNotifier(repo, path, {"RESOLV_SMTP_HOST":"127.0.0.1","RESOLV_SMTP_FROM":"desk@example.test",
                    "RESOLV_SMTP_PORT":str(inbox.server_address[1]),"RESOLV_SMTP_TLS":"none",
                    "RESOLV_SMTP_PASSWORD":"private-test-secret","RESOLV_SMTP_RECIPIENTS":'{"E1":"reporter@example.test"}'})
                system = Resolv(repo, extra_notifier=smtp)
                admin = system.add_user(None,"ADMIN","A1","Admin","pw")
                user = system.add_user(admin,"EMPLOYEE","E1","Reporter","pw")
                system.add_service(admin,"Network",1)
                incident = system.report(user,"VPN failure","VPN is down","Network",1,1)
                for kind in EVENTS - {"CREATED"}:
                    smtp.send_event("E1",Event(kind, incident, admin.id, incident.state, incident.state, "event", system.now()))
                smtp.send_event("T1",Event("INFO_REQUEST", incident, admin.id, incident.state, incident.state,"details",system.now()))
                smtp.process_queue(repo.c)
                self.assertEqual(len(received), len(EVENTS))
                self.assertTrue(all(b"reporter@example.test" in message and b"INC1001" in message for message in received))
                smtp.send("E1", "queued")
                def temporary_failure(*args):
                    raise smtplib.SMTPDataError(451,b"private-test-secret")
                smtp.deliver = temporary_failure
                for _ in range(3):
                    repo._run("UPDATE email_queue SET next=0 WHERE status='pending'")
                    smtp.process_queue(repo.c)
                failed = smtp.status()[0]
                self.assertEqual((failed["status"],failed["attempts"],failed["error"]), ("failed",3,"SMTPDataError (451)"))
                self.assertTrue(system.inapp.for_user(user.id))
                self.assertEqual(len(repo.incidents()),1)
            finally:
                inbox.shutdown()
                thread.join()


if __name__ == "__main__":
    unittest.main()
