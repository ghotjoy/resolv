"""Configurable SMTP notifications with a durable queue and bounded retry."""
from contextlib import closing
from datetime import datetime
from email.message import EmailMessage
import json
import os
import smtplib
import sqlite3
import ssl
from threading import Event, Thread
import time

from resolv import Notifier

EVENTS = {"CREATED", "ASSIGNED", "PRIORITY_CHANGED", "ADMIN_OVERRIDE", "SLA_WARNING", "SLA_VIOLATED",
          "ESCALATED", "RESOLVED", "CLOSED", "INFO_REQUEST"}


class SMTPNotifier(Notifier):
    def __init__(self, repo, database, env=os.environ):
        self.repo, self.database = repo, database
        self.host = env.get("RESOLV_SMTP_HOST")
        self.sender = env.get("RESOLV_SMTP_FROM")
        self.encryption = env.get("RESOLV_SMTP_TLS", "starttls").lower()
        self.port = int(env.get("RESOLV_SMTP_PORT", 465 if self.encryption == "ssl" else 587))
        self.username, self.password = env.get("RESOLV_SMTP_USER"), env.get("RESOLV_SMTP_PASSWORD")
        self.recipients = json.loads(env.get("RESOLV_SMTP_RECIPIENTS", "{}"))
        self.support = env.get("RESOLV_SMTP_SUPPORT")
        self.base_url = env.get("RESOLV_BASE_URL", "").rstrip("/")
        self.enabled = set(env.get("RESOLV_SMTP_EVENTS", ",".join(EVENTS)).upper().split(",")) & EVENTS
        self.stop = Event()
        if not self.sender or self.encryption not in ("starttls", "ssl", "none"):
            raise ValueError("SMTP requires a sender and a valid TLS mode")

    def send(self, to, msg):
        self.enqueue(to, "Resolv notification", msg, None, None)

    def send_event(self, to, event):
        if event.kind not in self.enabled:
            return
        incident = event.inc
        if event.kind == "INFO_REQUEST" and to != incident.reporter:
            return
        category = event.kind.replace("_", " ").title()
        ref = f"{self.base_url}/#incident={incident.id}" if self.base_url else incident.id
        body = (f"Incident: {incident.id}\nEvent: {category}\nSummary: {incident.title}\n"
                f"Status: {incident.state}\nPriority: {incident.priority}\n"
                f"Time: {event.now.isoformat(timespec='seconds')}\nReference: {ref}")
        self.enqueue(to, f"[{incident.id}] {category}", body, incident.id, event.kind)

    def enqueue(self, user, subject, body, incident, event):
        recipient = self.recipients.get(user)
        if not recipient and user in ("support",):
            recipient = self.support
        if not recipient:
            return
        self.repo._run("INSERT INTO email_queue(recipient,subject,body,incident,event) VALUES(?,?,?,?,?)",
                       recipient, subject, body, incident, event)

    def start(self):
        Thread(target=self.work, name="resolv-smtp", daemon=True).start()

    def deliver(self, recipient, subject, body):
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = self.sender, recipient, subject
        message.set_content(body)
        if self.encryption == "ssl":
            connection = smtplib.SMTP_SSL(self.host, self.port, timeout=5, context=ssl.create_default_context())
        else:
            connection = smtplib.SMTP(self.host, self.port, timeout=5)
        with connection as smtp:
            if self.encryption == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if self.username:
                smtp.login(self.username, self.password or "")
            smtp.send_message(message)

    def work(self):
        with closing(sqlite3.connect(self.database)) as database:
            while not self.stop.is_set():
                try:
                    self.process_queue(database)
                except sqlite3.Error:
                    # A busy database can be retried on the next poll.
                    pass
                self.stop.wait(1)

    def process_queue(self, database):
        rows = database.execute("""SELECT id,recipient,subject,body,attempts FROM email_queue
            WHERE status='pending' AND next<=? ORDER BY id LIMIT 20""", (time.time(),)).fetchall()
        for key, recipient, subject, body, attempts in rows:
            attempts += 1
            status, diagnostic, retry_at = "sent", None, 0
            try:
                self.deliver(recipient, subject, body)
            except (OSError, ValueError, smtplib.SMTPException) as ex:
                code = getattr(ex, "smtp_code", None)
                if isinstance(ex, smtplib.SMTPRecipientsRefused):
                    code = next(iter(ex.recipients.values()))[0] if ex.recipients else None
                temporary = isinstance(ex, (OSError, smtplib.SMTPServerDisconnected)) or (code and 400 <= code < 500)
                if isinstance(ex, smtplib.SMTPResponseException) or isinstance(ex, smtplib.SMTPRecipientsRefused):
                    temporary = bool(code and 400 <= code < 500)
                status = "pending" if attempts < 3 and temporary else "failed"
                diagnostic = type(ex).__name__ + (f" ({code})" if code else "")
                retry_at = time.time() + attempts * 20 if status == "pending" else 0
            with database:
                database.execute("UPDATE email_queue SET status=?,error=?,attempts=?,next=? WHERE id=?",
                                 (status, diagnostic, attempts, retry_at, key))

    def status(self):
        rows = self.repo._run("""SELECT incident,event,status,attempts,error FROM email_queue
            ORDER BY id DESC LIMIT 100""")
        return [dict(zip(("incident", "event", "status", "attempts", "error"), row)) for row in rows]
