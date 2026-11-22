"""Web layer: REST API + static UI over the Resolv domain. Run: python3 server.py -> http://localhost:8000"""
import ipaddress, json, logging, os, secrets, time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event as StopEvent, Lock, Thread
from urllib.parse import urlparse, parse_qs, quote
from enhancements import ASSETS, Attachments, CampusLocations, IncidentDraftBuilder, LocalAttachmentStorage, LocalVosk, analytics
from mailer import SMTPNotifier
from topology import ServiceTopology
from accounts import AccountAccess
from reporting import incident_csv
from triage import GeminiTriage, Triage
from resolv import *

HOST, PORT = os.getenv("RESOLV_HOST", "127.0.0.1"), int(os.getenv("RESOLV_PORT", "8000"))
DEMO_PASSWORD = os.getenv("RESOLV_DEMO_PASSWORD") or None
ADMIN_PASSWORD = os.getenv("RESOLV_ADMIN_PASSWORD") or None
ORIGINS = {origin.strip() for origin in os.getenv("RESOLV_CORS_ORIGINS", "").split(",") if origin.strip()}
TRUSTED_PROXIES = {ipaddress.ip_address(address.strip()) for address in os.getenv("RESOLV_TRUSTED_PROXIES", "").split(",") if address.strip()}
if DEMO_PASSWORD and HOST not in ("127.0.0.1", "::1", "localhost"):
    raise ValueError("demo accounts require a loopback listener")
for password in (DEMO_PASSWORD, ADMIN_PASSWORD):
    if password is not None and not 12 <= len(password) <= 256:
        raise ValueError("configured bootstrap passwords must contain 12–256 characters")

DB_PATH = os.environ.get("RESOLV_DATABASE", "resolv.db")
REPO = SQLiteRepository(DB_PATH)
MAIL = None
MAIL_ERROR = None
if os.environ.get("RESOLV_SMTP_HOST"):
    try:
        MAIL = SMTPNotifier(REPO, DB_PATH)
    except (ValueError, TypeError):
        MAIL_ERROR = "Invalid SMTP configuration"
S = Resolv(REPO, extra_notifier=MAIL)
CAMPUS = CampusLocations(REPO, S.now)
TOPOLOGY = ServiceTopology(S)
ACCOUNTS = AccountAccess(S)
AI_KEY = os.getenv("RESOLV_GEMINI_API_KEY")
OPEN_REGISTRATION = os.getenv("RESOLV_OPEN_REGISTRATION","1") != "0"
TRIAGE = Triage(GeminiTriage(AI_KEY) if AI_KEY else None)
DRAFTS = IncidentDraftBuilder(S, CAMPUS)
MODEL = os.environ.get("RESOLV_VOSK_MODEL")
SPEECH = LocalVosk(MODEL) if MODEL else None
EVIDENCE = Attachments(LocalAttachmentStorage(os.environ.get("RESOLV_ATTACHMENTS", "evidence")),
                       S.incident_service, REPO, S.now,
                       max_count=int(os.environ.get("RESOLV_FILE_COUNT", "5")),
                       max_bytes=int(os.environ.get("RESOLV_FILE_BYTES", str(10 * 1024 * 1024))))
STATE_LOCK = Lock()
SPEECH_LOCK = Lock()
CSP = ("default-src 'self'; script-src 'self'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
       "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
       "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
MAX_AUDIO = 8 * 1024 * 1024
MAX_JSON = 128 * 1024
SESS = {}
TTL = 8 * 3600
STATIC = {"bits-pilani-logo.png": "image/png", "campus-base.svg": "image/svg+xml",
          "enhancements.js": "text/javascript; charset=utf-8", "operations.js": "text/javascript; charset=utf-8", "followup.js": "text/javascript; charset=utf-8", "app.js": "text/javascript; charset=utf-8",
          "operations.css": "text/css; charset=utf-8", "enhancements.css": "text/css; charset=utf-8"}
STRATS = ASSIGNMENT_STRATEGIES
DEMO_IDENTITIES = {
    "A1": ("ADMIN", "Ada Admin"),
    "T1": ("TECHNICIAN", "Tara"), "T2": ("TECHNICIAN", "Ravi"), "T3": ("TECHNICIAN", "Meena"),
    "E1": ("EMPLOYEE", "Eli"), "E2": ("EMPLOYEE", "Esha"),
}


def update_demo_credentials():
    launcher = os.getenv("RESOLV_DEMO_LAUNCHER") == "1" and DEMO_PASSWORD
    reset = launcher and os.getenv("RESOLV_RESET_DEMO_PASSWORD") == "1"
    seeded = {uid: S.users[uid] for uid, (role, name) in DEMO_IDENTITIES.items()
              if uid in S.users and (S.users[uid].role, S.users[uid].name) == (role, name)}
    legacy = [user for user in seeded.values() if user.check("pw")]
    if reset and S.users and len(seeded) != len(DEMO_IDENTITIES):
        raise SystemExit("Demo reset requires the six original demo identities. Use account recovery for custom accounts.")
    if any(not (DEMO_PASSWORD or (user.id == "A1" and ADMIN_PASSWORD)) for user in legacy):
        raise SystemExit("Legacy demo accounts need new passwords. Restart with RESOLV_DEMO_PASSWORD on loopback.")
    changes = list(seeded.values()) if reset else legacy
    if changes:
        with REPO.transaction():
            for user in changes:
                replacement = (ADMIN_PASSWORD if user.id == "A1" else None) or DEMO_PASSWORD
                user.set_password(replacement)
                REPO.save_user(user)
    if launcher and S.users and any(uid not in seeded or not seeded[uid].check(DEMO_PASSWORD)
                                    for uid in ("A1", "T1", "E1")):
        raise SystemExit("The demo database and password file disagree. Keep them together or run "
                         "python start_demo.py --reset-demo-password to reset the seeded accounts.")


update_demo_credentials()
if DEMO_PASSWORD and not S.users:
    admin = S.add_user(None, "ADMIN", "A1", "Ada Admin", ADMIN_PASSWORD or DEMO_PASSWORD)
    for uid, name, skills, exp in [("T1","Tara",{"APPLICATION","DATABASE"},3), ("T2","Ravi",{"NETWORK"},2),
                                   ("T3","Meena",{"AUTHENTICATION","STORAGE"},1)]:
        S.add_user(admin,"TECHNICIAN",uid,name,DEMO_PASSWORD,skills=skills,exp=exp,capacity=5)
    for uid,name in [("E1","Eli"),("E2","Esha")]:
        S.add_user(admin,"EMPLOYEE",uid,name,DEMO_PASSWORD)
    for name, criticality in [("Payment",4),("Database",4),("Network",3),("Authentication",3),("Cloud Storage",2)]:
        S.add_service(admin,name,criticality)
elif ADMIN_PASSWORD and not any(user.role == "ADMIN" for user in S.users.values()):
    admin_id = os.getenv("RESOLV_ADMIN_ID", "administrator")
    S.user_service._check_credentials(admin_id, ADMIN_PASSWORD)
    S.add_user(None, "ADMIN", admin_id, "Administrator", ADMIN_PASSWORD)


class RateLimited(ITSMError):
    pass


class LoginAttempts:
    def __init__(self):
        self.entries = {}
        self.cleanup_at = 0

    def attempt(self, key, limit):
        now = time.monotonic()
        if now >= self.cleanup_at:
            self.entries = {k: q for k, q in self.entries.items() if q[-1] > now - 300}
            self.cleanup_at = now + 60
        if len(self.entries) >= 20_000 and key not in self.entries:
            raise RateLimited("too many attempts, try again later")
        times = self.entries.setdefault(key, deque())
        while times and times[0] <= now - 300:
            times.popleft()
        if len(times) >= limit:
            raise RateLimited("too many attempts, try again later")
        times.append(now)

    def refund(self, key):
        times = self.entries.get(key)
        if times:
            times.pop()
            if not times:
                self.entries.pop(key, None)


LOGIN_ATTEMPTS = LoginAttempts()

def iso(d): return d.isoformat() if d else None
def ser(i, actor):
    """API view of an incident (presentation concern, so it lives here and not in the domain class)."""
    visible = {incident.id for incident in TOPOLOGY.visible(actor)}
    response_due, resolution_due = S.deadlines(i)
    due = response_due if not i.responded and i.sla_tracked else resolution_due
    return dict(id=i.id, reporter=i.reporter, title=i.title, description=i.description, service=i.service, category=i.category,
                impact=i.impact, urgency=i.urgency, users=i.users, priority=i.priority, created=iso(i.created), state=i.state,
                tech=i.tech, responded=iso(i.responded), resolved=iso(i.resolved), resolution=i.resolution, feedback=i.feedback,
                esc=i.escalation_level, related=[row["id"] for row in S.incident_service.relationships(i)
                                               if row["id"] in visible and row["decision"] != "DISMISSED"], comments=i.comments,
                sla=S.sla_status(i), due=iso(resolution_due), next_deadline=iso(due),
                recorded_sla_risk=i.sla_risk, assigned_at=iso(i.assigned_at), verified_at=iso(i.verified_at),
                parent=i.parent if i.parent in visible else None, feedback_id=i.feedback_id,
                custom_category=i.custom_category, location=i.location, priority_override=i.priority_override,
                closed=iso(i.closed), sla_started=iso(i.sla_started),
                hint=S.analyzer.suggest_resolution(i.title + " " + i.description, i.category, visible, i.service) if i.in_workload else None)

class H(BaseHTTPRequestHandler):
    timeout = 25
    def log_message(self, *a): pass

    def client_ip(self):
        address = ipaddress.ip_address(self.client_address[0])
        if address not in TRUSTED_PROXIES:
            return str(address)
        try:
            chain = [ipaddress.ip_address(value.strip()) for value in self.headers.get("X-Forwarded-For", "").split(",")]
        except ValueError:
            return str(address)
        return str(next((hop for hop in reversed(chain) if hop not in TRUSTED_PROXIES), address))

    def authenticate(self):
        token = self.headers.get("Authorization")
        entry = SESS.get(token)
        if entry is None or entry[1] < time.time():
            SESS.pop(token, None)
            raise AuthError("login required or session expired")
        actor = REPO.user(entry[0])
        if not actor or actor.password_hash != entry[2]:
            SESS.pop(token, None)
            raise AuthError("login required or session expired")
        S.users[actor.id] = actor
        return actor

    def do_OPTIONS(self):
        origin = self.headers.get("Origin")
        if origin not in ORIGINS:
            return self.send(403, {"error":"origin not allowed"})
        self.send(204, b"", headers={"Access-Control-Allow-Methods":"GET, POST", "Access-Control-Allow-Headers":"Authorization, Content-Type"})
    def send(self, code, obj, ct="application/json", headers=None):
        b = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if not headers or "Content-Security-Policy" not in headers:
            self.send_header("Content-Security-Policy", CSP)
        if os.getenv("RESOLV_HTTPS_ONLY") == "1":
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        if self.path.startswith("/api/") and (not headers or "Cache-Control" not in headers):
            self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin")
        if origin in ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(b)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def read_body(self, limit):
        size = int(self.headers.get("Content-Length") or 0)
        if not 0 <= size <= limit:
            raise ValidationError("request exceeds upload limit")
        return self.rfile.read(size)

    def notification_events(self, actor, after):
        deadline = time.monotonic() + 5
        while True:
            with STATE_LOCK:
                self.authenticate()
                events = [record for record in REPO.notification_feed(actor.id) if record["id"] > after]
            if events or time.monotonic() >= deadline:
                lines = ["retry: 3000\n"]
                for event in reversed(events):
                    lines.append(f"id: {event['id']}\nevent: notification\ndata: {json.dumps(event)}\n\n")
                return 200, ("".join(lines) + "\n").encode(), "text/event-stream; charset=utf-8", {"X-Accel-Buffering":"no"}
            time.sleep(.25)

    def route(self, method):
        p = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        if p.path == "/" and method == "GET":
            return 200, ASSETS.parent.joinpath("index.html").read_bytes(), "text/html; charset=utf-8"
        if method == "GET" and p.path.startswith("/assets/") and p.path[8:] in STATIC:
            name = p.path[8:]
            return 200, ASSETS.joinpath(name).read_bytes(), STATIC[name]
        if not p.path.startswith("/api/"):
            raise NotFound("endpoint")
        origin = self.headers.get("Origin")
        if origin and origin not in ORIGINS and origin != "http://" + self.headers.get("Host", "") and origin != "https://" + self.headers.get("Host", ""):
            raise Unauthorized("origin not allowed")
        r = p.path[5:].split("/")
        u = None
        if r == ["public-config"] and method == "GET":
            return 200, {"open_registration":OPEN_REGISTRATION}
        if r not in (["login"], ["register"], ["account-request"], ["account-complete"]):
            if r in (["transcribe"], ["notifications","events"], ["triage"]):
                with STATE_LOCK:
                    u = self.authenticate()
            else:
                u = self.authenticate()
        if r == ["transcribe"] and method == "POST":
            require_role(u, "EMPLOYEE")
            if SPEECH is None:
                return 503, {"error": "Transcription is unavailable. You can still report the issue manually."}
            audio = self.read_body(MAX_AUDIO)
            with SPEECH_LOCK:
                return 200, {"transcript": SPEECH.transcribe(audio)}
        body = {}
        if method == "POST":
            incident_upload = r == ["incidents"] or (len(r) == 3 and r[0] == "incidents" and r[2] == "attachments")
            limit = MAX_JSON + (EVIDENCE.max_count * ((EVIDENCE.max_bytes + 2) // 3) * 4 if incident_upload else 0)
            body = json.loads(self.read_body(limit) or b"{}")
            if not isinstance(body, dict):
                raise ValidationError("request body must be an object")
        if r == ["login"] and method == "POST":
            supplied = body.get("id", "")
            uid = supplied.strip() if isinstance(supplied, str) and len(supplied) <= 80 else ""
            address = self.client_ip()
            source_key = ("ip", address)
            account_key = ("login", address, uid.casefold())
            LOGIN_ATTEMPTS.attempt(source_key, 40)
            try:
                LOGIN_ATTEMPTS.attempt(account_key, 8)
            except RateLimited:
                LOGIN_ATTEMPTS.refund(source_key)
                raise
            matches = REPO.matching_users(uid)
            for key in tuple(S.users):
                if key.casefold() == uid.casefold():
                    S.users.pop(key)
            S.users.update((account.id, account) for account in matches)
            u = S.login(uid, body.get("pw", ""))
            LOGIN_ATTEMPTS.refund(source_key)
            LOGIN_ATTEMPTS.entries.pop(account_key, None)
            for token, entry in list(SESS.items()):
                if entry[1] < time.time():
                    SESS.pop(token, None)
            t = secrets.token_hex(16)
            SESS[t] = (u.id, time.time() + TTL, u.password_hash)
            return 200, {"token": t, "id": u.id, "name": u.name, "role": u.role,
                         "can_export_csv": u.can_export_csv}
        if r == ["register"] and method == "POST":
            if not OPEN_REGISTRATION:
                raise Unauthorized("ask the service desk to approve an access request")
            LOGIN_ATTEMPTS.attempt(("register", self.client_ip()), 15)
            S.register(body.get("id", ""), body.get("name", ""), body.get("pw", ""))
            return 200, {"ok": True}
        if r == ["account-request"] and method == "POST":
            LOGIN_ATTEMPTS.attempt(("account-request", self.client_ip()), 15)
            ACCOUNTS.request(body.get("kind"),body.get("id"),body.get("name",""),body.get("note",""))
            return 202, {"ok":True}
        if r == ["account-complete"] and method == "POST":
            LOGIN_ATTEMPTS.attempt(("account-complete", self.client_ip()), 15)
            ACCOUNTS.complete(body.get("code"),body.get("password"))
            return 200, {"ok":True}
        if r == ["triage"] and method == "POST":
            require_role(u,"EMPLOYEE","TECHNICIAN","ADMIN")
            messages = TRIAGE.validate(body.get("messages"))
            with STATE_LOCK:
                LOGIN_ATTEMPTS.attempt(("triage",u.id),20)
                categories = tuple(S.categories)
                category = S.analyzer.suggest_category(messages[-1]["text"])
            return 200, TRIAGE.suggest(messages,category,categories,body.get("use_provider",False))
        if r == ["logout"] and method == "POST":
            SESS.pop(self.headers.get("Authorization"), None)
            return 200, {"ok": True}
        if r == ["draft"] and method == "POST":
            require_role(u, "EMPLOYEE")
            return 200, DRAFTS.build(body.get("transcript", ""))
        if r == ["category-suggestion"] and method == "POST":
            require_role(u, "EMPLOYEE", "ADMIN")
            return 200, {"category": S.suggest_category(body.get("text", "")[:5200])}
        if r == ["intake-preview"] and method == "POST":
            return 200, S.incident_service.preview(u, body.get("title",""), body.get("desc",""), body.get("service",""),
                                                   body.get("impact",1), body.get("urgency",1), body.get("users",1), body.get("category"))
        if r == ["notifications","events"] and method == "GET":
            return self.notification_events(u, int(self.headers.get("Last-Event-ID") or q.get("after", 0)))
        if r == ["notifications"]:
            if method == "POST":
                REPO.mark_notifications_read(u.id, int(body.get("through", 0)))
            return 200, REPO.notification_feed(u.id)
        if r == ["queue-summary"] and method == "GET":
            visible = TOPOLOGY.visible(u)
            return 200, dict(all=len(visible), critical=sum(i.sla_tracked and i.priority == "P1" for i in visible),
                             in_progress=sum(i.state == "IN_PROGRESS" for i in visible),
                             breached=sum(S.sla_status(i) == "BREACHED" for i in visible))
        if r == ["topology"] and method == "GET":
            return 200, TOPOLOGY.view(u)
        if r == ["topology"] and method == "POST":
            TOPOLOGY.configure(u, body.get("service"), body.get("upstream"), body.get("remove") is True)
            return 200, TOPOLOGY.view(u)
        if r == ["incidents","export"] and method == "GET":
            if not u.can_export_csv:
                raise Unauthorized("Your account does not have permission to export incidents.")
            reports = S.report_service.search(actor=u,**{k:v for k,v in q.items() if v})
            return 200, incident_csv(S,reports), "text/csv; charset=utf-8", {"Content-Disposition":"attachment; filename=incidents.csv"}
        if r == ["my-dashboard"] and method == "GET":
            period = q.pop("period","daily")
            return 200, analytics(S,u,q,period,personal=True)
        if len(r) >= 2 and r[0] == "incidents":
            iid = r[1]
            if len(r) == 2 and method == "GET":
                return 200, ser(S.incident_service.accessible(u, iid), u)
            if len(r) == 3 and r[2] == "insights" and method == "GET":
                return 200, TOPOLOGY.insights(u, S.incident_service.accessible(u, iid))
            if len(r) == 3 and r[2] == "sla-history" and method == "GET":
                S.incident_service.accessible(u,iid)
                return 200, REPO.sla_history(iid)
            if len(r) == 3 and r[2] == "relationship" and method == "POST":
                S.incident_service.review_related(u,iid,body.get("other"),body.get("decision"))
                return 200, TOPOLOGY.insights(u,S.incident_service.accessible(u,iid))
            if len(r) == 3 and r[2] == "escalate" and method == "POST":
                require_role(u,"ADMIN","TEAM_LEAD","IT_MANAGER")
                incident = S.incident_service.accessible(u,iid)
                return 200, ser(S.sla_engine.manual_escalate(u,incident,body.get("reason")),u)
            if len(r) == 3 and r[2] == "parent" and method == "POST":
                return 200, ser(S.incident_service.set_parent(u, iid, body.get("parent")), u)
            if len(r) == 3 and r[2] == "override" and method == "POST":
                return 200, ser(S.override(u, iid, body.get("reason"), body.get("changes", {})), u)
            if len(r) == 3 and r[2] == "activity" and method == "GET":
                S.incident_service.accessible(u, iid)
                if u.role == "ADMIN":
                    return 200, S.report_service.audit_records(u, inc=iid, **{k:v for k,v in q.items() if k != "inc"})
                visible = ("CREATED", "CLASSIFIED", "ASSIGNED", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED",
                           "VERIFIED", "CLOSED", "PRIORITY_CHANGED", "ATTACHMENTS_ADDED")
                return 200, [dict(ts=ts, action=action, prev=prev, new=new) for ts, _, action, _, prev, new, _
                             in REPO.audit_log(iid) if action in visible and (not q.get("action") or q["action"] == action)]
            if len(r) == 3 and r[2] == "attachments":
                if method == "GET":
                    S.incident_service.accessible(u, iid)
                    return 200, REPO.attachments(iid)
                require_role(u,"EMPLOYEE","TECHNICIAN","ADMIN")
                return 200, EVIDENCE.add(u, iid, body.get("files", []))
            if len(r) == 4 and r[2] == "attachments" and method == "GET":
                file, data = EVIDENCE.download(u, iid, r[3])
                disposition = "inline" if q.get("preview") == "1" and file["type"].startswith("image/") else "attachment"
                return 200, data, file["type"], {
                    "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(file['name'], safe='')}",
                    "Cache-Control": "private, no-store", "Content-Security-Policy": "default-src 'none'; sandbox",
                }
        if r == ["campus"] and method == "POST":
            return 200, CAMPUS.update(u, body)
        if r == ["mail-status"] and method == "GET":
            require_role(u, "ADMIN")
            return 200, dict(mode="smtp" if MAIL else "simulated", error=MAIL_ERROR, deliveries=MAIL.status() if MAIL else [])
        if r == ["audit", "export"] and method == "GET":
            rows = S.report_service.audit_records(u, visibility="all")
            return 200, json.dumps(rows).encode(), "application/json", {"Content-Disposition": "attachment; filename=audit.json"}
        if len(r) == 2 and r[0] == "audit" and method == "POST":
            require_role(u, "ADMIN")
            return 200, {"id": S.report_service.annotate_audit(u, int(r[1]), body.get("operation"), body.get("reason"), S.now())}
        if r == ["incidents"] and method == "GET":
            L = S.report_service.search(actor=u, **{k:v for k,v in q.items() if v})
            return 200, [ser(i, u) for i in sorted(L, key=lambda i: (i.created, i.id), reverse=True)]
        if r == ["incidents"] and method == "POST":
            require_role(u, "EMPLOYEE", "ADMIN")
            location = CAMPUS.validate(body.get("location"))
            with EVIDENCE.stage(u, body.get("files", [])) as files:
                i = S.report(u, body.get("title", ""), body.get("desc", ""), body.get("service", ""),
                             int(body.get("impact", 0)), int(body.get("urgency", 0)), int(body.get("users", 1)),
                             body.get("category") or None, custom_category=body.get("custom_category"),
                             location=location, attachments=files)
            return 200, ser(i, u)
        if len(r) == 3 and r[0] == "incidents" and method == "POST":
            _, i, act = r
            if act == "ack": S.acknowledge(u, i)
            elif act == "start": S.start(u, i)
            elif act == "resolve": S.resolve(u, i, body.get("text", ""))
            elif act == "verify": S.verify(u, i, body.get("text"))
            elif act == "close": S.close(u, i)
            elif act == "assign": S.assign(u, i, body.get("tech", ""))
            elif act == "needs-info":
                require_role(u, "TECHNICIAN", "ADMIN")
                S.comment(u, i, body.get("text", ""), "INFO_REQUEST")
            elif act == "note": S.comment(u, i, body.get("text", ""), body.get("kind"))
            elif act == "update": S.update(u, i, **{k: (int(v) if k in ("impact", "urgency", "users") else v) for k, v in body.items()})
            else: raise NotFound(act)
            return 200, ser(S.search(id=i)[0], u)
        if r == ["meta"]:
            return 200, dict(services=S.services, categories=list(S.categories), strategy=type(S.assignment).__name__,
                             techs=S.technician_ids(),
                             sla=S.sla, offset_min=S.clock.offset_minutes, campus=CAMPUS.public(),
                             priority_strategy=type(S.priority).__name__, triage_provider=bool(TRIAGE.provider),
                             voice=bool(SPEECH), max_files=EVIDENCE.max_count, max_file_bytes=EVIDENCE.max_bytes)
        if r == ["workload"]:
            w = S.technician_workload(u, q.get("tech")); w["incidents"] = [ser(i, u) for i in w["incidents"]]; return 200, w
        if r == ["alerts"]: return 200, S.inapp.for_user(u.id)
        require_role(u, "ADMIN")
        if r == ["dashboard"] and method == "GET":
            period = q.pop("period", "daily")
            return 200, analytics(S, u, q, period)
        if r == ["account-requests"] and method == "GET":
            return 200, ACCOUNTS.list_requests(u)
        if len(r) == 2 and r[0] == "account-requests" and method == "POST":
            return 200, ACCOUNTS.review(u,r[1],body.get("decision"))
        if r == ["priority-strategy"] and method == "POST":
            S.set_priority_strategy(u, body.get("name"))
            return 200, {"name": type(S.priority).__name__}
        if r == ["sla-calendar"]:
            if method == "POST":
                S.incident_service.configure_calendar(u,body.get("schedule"))
            return 200, {"schedule":S.incident_service.sla_schedule}
        if r == ["intelligence-settings"]:
            if method == "POST":
                S.incident_service.configure_intelligence(u,float(body["related"]),float(body["duplicate"]),body["weights"])
            return 200, S.incident_service.settings
        if r == ["catalog"]:
            if method == "POST":
                S.config_service.manage_catalog(u, body.get("kind"), body.get("name"),
                                                body.get("action"), body.get("value"), S.now())
            return 200, dict(services=S.services, categories=S.categories)
        if r == ["category-usage"] and method == "GET":
            return 200, S.report_service.other_category_usage(u)
        if r == ["audit"] and method == "GET":
            return 200, S.report_service.audit_records(u, **q)
        if r == ["monitor"] and method == "POST": return 200, S.monitor()
        if r == ["strategy"] and method == "POST":
            if body.get("name") not in STRATS: raise ValidationError("unknown strategy")
            S.set_assignment(u, STRATS[body["name"]]()); return 200, {"ok": True}
        if r == ["sla"] and method == "POST": S.set_sla(u, body.get("priority"), int(body["response"]), int(body["resolution"])); return 200, S.sla
        if r == ["advance"] and method == "POST": S.advance(u, int(body["minutes"])); return 200, {"offset_min": S.clock.offset_minutes}
        if r == ["users"] and method == "GET": return 200, S.list_users(u)
        if r == ["users"] and method == "POST":
            S.create_user(u, body.get("role", ""), body.get("id", ""), body.get("name", ""), body.get("pw", ""),
                          skills=[x for x in str(body.get("skills", "")).split(",") if x.strip()], capacity=int(body.get("capacity") or 5), exp=int(body.get("experience") or 1))
            return 200, S.list_users(u)
        if r == ["tech"] and method == "POST":
            sk = body.get("skills"); S.update_technician(u, body.get("id", ""), skills=None if sk is None else str(sk).split(","),
                capacity=None if body.get("capacity") in (None, "") else int(body["capacity"]), available=body.get("available"),
                experience=None if body.get("experience") in (None, "") else int(body["experience"]))
            return 200, S.list_users(u)
        if r == ["category"] and method == "POST":
            kws = [x.strip().lower() for x in str(body.get("keywords", "")).split(",") if x.strip()]
            if not str(body.get("name", "")).strip(): raise ValidationError("category name required")
            S.add_category(u, body["name"], kws); return 200, list(S.categories)
        if r == ["service"] and method == "POST": S.add_service(u, body["name"], int(body["criticality"])); return 200, S.services
        raise NotFound(p.path)
    def handle_any(self, m):
        try:
            if urlparse(self.path).path in ("/api/transcribe", "/api/notifications/events", "/api/triage") or not self.path.startswith("/api/"):
                response = self.route(m)
            else:
                with STATE_LOCK:
                    response = self.route(m)
            self.send(*response)
        except ITSMError as e:
            status = {AuthError:401, Unauthorized:403, NotFound:404, PersistenceError:503,
                      MissingSLA:503, InvalidTransition:409, RateLimited:429}.get(type(e),400)
            detail = "Storage temporarily unavailable" if isinstance(e, PersistenceError) else str(e)
            payload = {"error": detail}
            if isinstance(e, DuplicateIncident):
                payload["existing_incident"] = e.incident_id
            self.send(status, payload, headers={"Retry-After":"300"} if status == 429 else None)
        except (ValueError, KeyError, TypeError, AttributeError):
            self.send(400, {"error": "Invalid input"})
        except OSError:
            self.send(503, {"error": "Local storage is temporarily unavailable"})
    do_GET = lambda s: s.handle_any("GET")
    do_POST = lambda s: s.handle_any("POST")

if __name__ == "__main__":
    interval = max(0, int(os.getenv("RESOLV_MONITOR_SECONDS", "30")))
    stop = StopEvent()

    def monitor():
        while not stop.wait(interval):
            try:
                with STATE_LOCK:
                    S.monitor()
            except ITSMError:
                logging.warning("SLA monitoring was unable to refresh an incident")

    with ThreadingHTTPServer((HOST, PORT), H) as httpd:
        if MAIL:
            MAIL.start()
        if interval:
            Thread(target=monitor, daemon=True, name="sla-monitor").start()
        print(f"Resolv on http://localhost:{PORT}", flush=True)
        if not S.user_service.admin_ids():
            print("No administrator exists. Set RESOLV_ADMIN_PASSWORD (12+ characters) and restart, "
                  "or use python start_demo.py for a seeded local demo.", flush=True)
        try:
            httpd.serve_forever()
        finally:
            stop.set()
            if MAIL:
                MAIL.stop.set()
