"""Resolv: Intelligent IT Service Management & Incident Resolution System.
Patterns: State, Strategy (priority/assignment/notifier), Observer (audit+notify),
Factory (users), Repository (SQLite). Intelligence: duplicate detection, auto-categorisation,
smart assignment, SLA risk + auto-escalation, suggested resolution."""
import sqlite3, hashlib, hmac, os, json, re, uuid
from abc import ABC, abstractmethod
from collections import Counter
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import datetime, timedelta

from schema import migrate
from sla_calendar import add_minutes, elapsed_seconds, normalize_schedule

# ---------- exceptions ----------
class ITSMError(Exception): pass
class AuthError(ITSMError): pass
class Unauthorized(ITSMError): pass
class NotFound(ITSMError): pass
class InvalidTransition(ITSMError): pass
class WorkloadExceeded(ITSMError): pass
class DuplicateIncident(ITSMError):
    def __init__(self, incident_id):
        self.incident_id = incident_id
        super().__init__(f"You already reported a similar issue. Open {incident_id} to add information.")
class MissingSLA(ITSMError): pass
class ValidationError(ITSMError): pass
class PersistenceError(ITSMError): pass

# ---------- users (inheritance + polymorphism + factory) ----------
PASSWORD_ROUNDS = 600_000

def _hash(pw, salt=None, rounds=PASSWORD_ROUNDS):
    salt = salt or os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), rounds).hex()
    return f"{rounds}${salt}${digest}"

def password_matches(pw, encoded):
    fields = encoded.split("$")
    rounds, salt = (int(fields[0]), fields[1]) if len(fields) == 3 else (20_000, fields[0])
    return hmac.compare_digest(_hash(pw, salt, rounds).rsplit("$", 1)[1], fields[-1])

DUMMY_PASSWORD_HASH = _hash(os.urandom(24).hex())

class User(ABC):
    """Shared account data and default permissions for each concrete user role."""

    can_export_csv = False
    minimum_escalation_tier = None

    @property
    @abstractmethod
    def role(self): ...
    def __init__(self, uid, name, pw=None, pwhash=None):
        if not isinstance(uid, str) or not isinstance(name, str) or not uid or not 1 <= len(name) <= 120:
            raise ValidationError("id and name required, name must be at most 120 characters")
        self._id, self._name = uid, name
        self._hash = pwhash or _hash(pw)
    id = property(lambda s: s._id)
    name = property(lambda s: s._name)
    password_hash = property(lambda s: s._hash)          # read by the repository only
    def check(self, pw): return password_matches(pw, self._hash)
    def set_password(self, password): self._hash = _hash(password)
    def profile(self): return dict(id=self._id, name=self._name, role=self.role)   # public view, never the hash
    # polymorphic authorisation rules (overridden per role; default = no access)
    def can_view_incident(self, incident):
        """Apply the role's ownership rules or its minimum escalation tier."""
        tier = self.minimum_escalation_tier
        return (self.can_work_on(incident) or self.acts_as_reporter_for(incident)
                or (tier is not None and incident.escalation_level >= tier))

    def can_work_on(self, inc): return False
    def acts_as_reporter_for(self, inc): return False
    def comment_kinds(self, inc): return set()

class Employee(User):
    role = "EMPLOYEE"
    def acts_as_reporter_for(self, inc): return inc.reporter == self._id
    def comment_kinds(self, inc): return {"INFO", "REPLY"} if inc.reporter == self._id else set()
class Administrator(User):
    role = "ADMIN"
    can_export_csv = True
    def can_work_on(self, inc): return True
    def acts_as_reporter_for(self, inc): return True
    def comment_kinds(self, inc): return {"NOTE", "INFO_REQUEST", "INFO", "REPLY"}
class TeamLead(User):
    role = "TEAM_LEAD"
    minimum_escalation_tier = 1
    can_export_csv = True


class ITManager(User):
    role = "IT_MANAGER"
    minimum_escalation_tier = 3
    can_export_csv = True


class Technician(User):
    role = "TECHNICIAN"
    can_export_csv = True
    def __init__(self, uid, name, pw=None, pwhash=None, skills=(), capacity=5, experience=1, available=True):
        super().__init__(uid, name, pw, pwhash)
        self._skills, self._capacity, self._experience, self._available = set(), 5, 1, True
        self.update_profile(skills=skills, capacity=capacity, experience=experience, available=available)
    skills = property(lambda s: frozenset(s._skills))
    capacity = property(lambda s: s._capacity)
    experience = property(lambda s: s._experience)
    available = property(lambda s: s._available)
    def has_skill(self, category): return category in self._skills
    def can_accept(self, current_load): return self._available and current_load < self._capacity
    def update_profile(self, skills=None, capacity=None, experience=None, available=None):
        """All-or-nothing update: validate everything first, then change state."""
        if capacity is not None and capacity < 1: raise ValidationError("capacity must be >= 1")
        if experience is not None and experience < 0: raise ValidationError("experience must be >= 0")
        if skills is not None: self._skills = {x.strip().upper() for x in skills if x.strip()}
        if capacity is not None: self._capacity = capacity
        if experience is not None: self._experience = experience
        if available is not None: self._available = bool(available)
    def can_work_on(self, inc): return inc.tech == self._id
    def comment_kinds(self, inc): return {"NOTE", "INFO_REQUEST"} if inc.tech == self._id else set()
    def profile(self):
        d = super().profile(); d.update(skills=sorted(self._skills), capacity=self._capacity, exp=self._experience, available=self._available); return d

class UserFactory:
    @staticmethod
    def create(role, uid, name, pw=None, **kw):
        if role == "TECHNICIAN":
            return Technician(uid, name, pw, kw.get("pwhash"), kw.get("skills", ()), kw.get("capacity", 5), kw.get("exp", kw.get("experience", 1)), kw.get("available", True))
        cls = {"EMPLOYEE": Employee, "ADMIN": Administrator, "TEAM_LEAD": TeamLead, "IT_MANAGER": ITManager}.get(role)
        if not cls: raise ValidationError(f"unknown role {role}")
        return cls(uid, name, pw, kw.get("pwhash"))

# ---------- incident + State pattern ----------
class State(ABC):
    """Each lifecycle state owns its transitions and its capabilities (State pattern)."""
    allowed = ()               # legal next states
    open_phase = False         # not yet resolved: editable and tracked by the SLA monitor
    assignable = False         # a technician may be (re)assigned in this state
    awaiting_assignment = False# queued for dispatch
    counts_as_workload = False # counts against a technician's capacity
    commentable = True
    duplicate_candidate = True # later incidents may be linked to it
    def on_enter(self, inc, now): pass
class New(State): allowed = ("CLASSIFIED",); open_phase = True
class Classified(State): allowed = ("ASSIGNED",); open_phase = True; assignable = True; awaiting_assignment = True
class Assigned(State):
    allowed = ("ACKNOWLEDGED",); open_phase = True; assignable = True; counts_as_workload = True
    def on_enter(self, inc, now): inc.mark_assigned(now)
class Acknowledged(State):
    allowed = ("IN_PROGRESS",); open_phase = True; assignable = True; counts_as_workload = True
    def on_enter(self, inc, now): inc.mark_responded(now)
class InProgress(State): allowed = ("RESOLVED",); open_phase = True; assignable = True; counts_as_workload = True
class Resolved(State):
    allowed = ("VERIFIED", "IN_PROGRESS")
    def on_enter(self, inc, now): inc.mark_resolved(now)
class Verified(State):
    allowed = ("CLOSED",); duplicate_candidate = False
    def on_enter(self, inc, now): inc.mark_verified(now)


class Closed(State):
    commentable = False; duplicate_candidate = False
    def on_enter(self, inc, now): inc.mark_closed(now)
STATES = {"NEW": New(), "CLASSIFIED": Classified(), "ASSIGNED": Assigned(), "ACKNOWLEDGED": Acknowledged(),
          "IN_PROGRESS": InProgress(), "RESOLVED": Resolved(), "VERIFIED": Verified(), "CLOSED": Closed()}

class Incident:
    """Aggregate root. Fields are private; every change goes through a method that enforces the rules."""
    @staticmethod
    def check_content(title, description, impact, urgency, users):
        if not str(title).strip() or not str(description).strip(): raise ValidationError("title/description required")
        if len(title) > 200 or len(description) > 5000: raise ValidationError("title max 200 / description max 5000 characters")
        if not (1 <= impact <= 4 and 1 <= urgency <= 4 and 1 <= users <= 1_000_000): raise ValidationError("impact and urgency must be 1–4, users affected must be 1–1,000,000")
    def __init__(self, id, reporter, title, description, service, category, impact, urgency, users,
                 priority, created, state="NEW", tech=None, responded=None, resolved=None,
                 resolution=None, feedback=None, esc=0, related=None, flags=None, comments=None, custom_category=None,
                 location=None, priority_override=None, sla_started=None, closed=None, parent=None, feedback_id=None,
                 assigned_at=None, verified_at=None, response_due=None, resolution_due=None, sla_cycle=None, sla_risk=None, sla_schedule=None):
        Incident.check_content(title, description, impact, urgency, users)
        self._id, self._reporter, self._service, self._category, self._created = id, reporter, service, category, created
        self._title, self._description, self._impact, self._urgency, self._users, self._priority = title, description, impact, urgency, users, priority
        self._state, self._tech, self._responded, self._resolved = state, tech, responded, resolved
        self._resolution, self._feedback, self._esc = resolution, feedback, esc
        self._custom_category, self._location = custom_category, location
        self._priority_override, self._sla_started, self._closed = priority_override, sla_started or created, closed
        self._parent, self._feedback_id = parent, feedback_id
        self._assigned_at, self._verified_at = assigned_at, verified_at
        self._response_due, self._resolution_due = response_due, resolution_due
        self._sla_cycle, self._sla_risk = sla_cycle or self._sla_started.isoformat(), sla_risk
        self._sla_schedule = sla_schedule
        self._related, self._flags, self._comments = list(related or []), set(flags or []), list(comments or [])
    # read-only views
    id = property(lambda s: s._id); reporter = property(lambda s: s._reporter); service = property(lambda s: s._service)
    category = property(lambda s: s._category); created = property(lambda s: s._created); title = property(lambda s: s._title)
    description = property(lambda s: s._description); impact = property(lambda s: s._impact); urgency = property(lambda s: s._urgency)
    users = property(lambda s: s._users); priority = property(lambda s: s._priority); state = property(lambda s: s._state)
    tech = property(lambda s: s._tech); responded = property(lambda s: s._responded); resolved = property(lambda s: s._resolved)
    resolution = property(lambda s: s._resolution); feedback = property(lambda s: s._feedback); escalation_level = property(lambda s: s._esc)
    related = property(lambda s: list(s._related)); flags = property(lambda s: frozenset(s._flags)); comments = property(lambda s: list(s._comments))
    custom_category = property(lambda s: s._custom_category)
    location = property(lambda s: deepcopy(s._location))
    priority_override = property(lambda s: s._priority_override)
    sla_started = property(lambda s: s._sla_started)
    closed = property(lambda s: s._closed)
    parent = property(lambda s: s._parent)
    feedback_id = property(lambda s: s._feedback_id)
    assigned_at = property(lambda s: s._assigned_at)
    verified_at = property(lambda s: s._verified_at)
    response_due = property(lambda s: s._response_due)
    resolution_due = property(lambda s: s._resolution_due)
    sla_cycle = property(lambda s: s._sla_cycle)
    sla_risk = property(lambda s: s._sla_risk)
    def link_parent(self, parent): self._parent = parent
    sla_schedule = property(lambda s: deepcopy(s._sla_schedule))

    def apply_sla_policy(self, policy, schedule=None):
        response, resolution = policy
        self._sla_schedule = deepcopy(schedule)
        self._response_due = add_minutes(self._sla_started,response,schedule)
        self._resolution_due = add_minutes(self._sla_started,resolution,schedule)
        self._sla_risk = None

    def sla_fraction(self, measured, deadline):
        minutes = elapsed_seconds(self._sla_started,deadline,self._sla_schedule)
        return elapsed_seconds(self._sla_started,measured,self._sla_schedule) / minutes

    def business_minutes_overdue(self, deadline, measured):
        return max(0, int(elapsed_seconds(deadline,measured,self._sla_schedule) // 60))

    def mark_sla_risk(self, risk): self._sla_risk = risk
    def mark_assigned(self, now): self._assigned_at = now
    def mark_verified(self, now): self._verified_at = now
    def mark_closed(self, now): self._closed = now
    # capabilities, answered by the current State object
    _st = property(lambda s: STATES[s._state])
    is_open_for_dup = lambda s: s._st.duplicate_candidate
    sla_tracked = editable = property(lambda s: s._st.open_phase)
    awaiting_assignment = property(lambda s: s._st.awaiting_assignment)
    in_workload = property(lambda s: s._st.counts_as_workload)
    def move(self, target, now):
        if target not in self._st.allowed: raise InvalidTransition(f"{self._state} -> {target} not allowed")
        self.force_state(target, now)

    def force_state(self, target, now):
        if target not in STATES:
            raise ValidationError("unknown lifecycle state")
        if self.state == "CLOSED" and STATES[target].open_phase:
            self._sla_started = now
            self._sla_cycle = uuid.uuid4().hex
            self._assigned_at = now if self._tech and STATES[target].counts_as_workload else None
            self._response_due = self._resolution_due = self._sla_risk = None
            self._responded = self._resolved = self._resolution = self._feedback = None
            self._esc = 0
            self._flags.clear()
        elif STATES[target].open_phase:
            self._resolved = self._resolution = self._feedback = None
        if STATES[target].open_phase:
            self._feedback_id = self._verified_at = None
        if target == "IN_PROGRESS" and not self._responded:
            self._responded = now
        self._state = target
        if target in ("RESOLVED", "VERIFIED", "CLOSED") and not self._resolved:
            self._resolved = now
        STATES[target].on_enter(self, now)

    def apply_override(self, fields, priority, priority_override, now):
        for key, value in fields.items():
            if key not in ("state", "priority_override"):
                setattr(self, "_" + key, value)
        self._priority, self._priority_override = priority, priority_override
        if "state" in fields and fields["state"] != self.state:
            self.force_state(fields["state"], now)

    # controlled mutators (each validates before changing anything)
    def assign_to(self, tech_id, now):
        if not self._st.assignable: raise InvalidTransition(f"cannot assign in state {self._state}")
        if self._st.awaiting_assignment: self.move("ASSIGNED", now)
        if tech_id != self._tech:
            self.mark_assigned(now)
        self._tech = tech_id
    def revise(self, title, description, impact, urgency, users, priority):
        if not self._st.open_phase: raise InvalidTransition("cannot edit after resolution")
        Incident.check_content(title, description, impact, urgency, users)
        self._title, self._description, self._impact, self._urgency, self._users, self._priority = title, description, impact, urgency, users, priority
    def add_comment(self, ts, user, kind, text):
        if not self._st.commentable: raise InvalidTransition("incident is closed")
        if not text.strip() or len(text) > 5000: raise ValidationError("text must contain 1–5,000 characters")
        self._comments.append(dict(ts=ts, user=user, kind=kind, text=text))
    def record_resolution(self, text): self._resolution = text
    def record_feedback(self, text):
        if text is not None and len(text) > 2000:
            raise ValidationError("feedback must be at most 2,000 characters")
        self._feedback = text
        self._feedback_id = uuid.uuid4().hex if text else None
    def mark_responded(self, now): self._responded = now
    def mark_resolved(self, now): self._resolved = now
    def raise_flag(self, key):
        if key in self._flags: return False
        self._flags.add(key); return True
    def escalate_to(self, level):
        if level <= self._esc: return False
        self._esc = level; return True
    def has_info_request(self): return any(c["kind"] == "INFO_REQUEST" for c in self._comments)

# ---------- Strategies ----------
class PriorityStrategy(ABC):
    @abstractmethod
    def compute(self, impact, urgency, criticality, users): ...

    def result(self, impact, urgency, criticality, users, category_weight=1):
        return dict(choice=self.compute(impact, urgency, criticality, users), score=None,
                    reason=type(self).__name__)


def priority_result(score, thresholds, reason):
    result = next((priority for priority, threshold in zip(("P1","P2","P3"), thresholds) if score >= threshold), "P4")
    return dict(choice=result, score=round(score, 2), reason=reason)


class RuleBased(PriorityStrategy):
    def result(self, impact, urgency, criticality, users, category_weight=1):
        impact, urgency = min(impact, 3), min(urgency, 3)
        criticality = 5 if criticality == 4 else criticality
        return priority_result(impact * urgency * criticality, (27,15,6),
                               f"Brief formula: impact {impact} × urgency {urgency} × criticality {criticality}.")

    def compute(self, impact, urgency, criticality, users):
        return self.result(impact, urgency, criticality, users)["choice"]


class WeightedScore(PriorityStrategy):
    """Historical formula kept as an explicit strategy for existing installations."""
    def result(self, impact, urgency, criticality, users, category_weight=1):
        multiplier = 1.25 if users >= 100 else 1
        return priority_result(impact * urgency * criticality * multiplier, (36,18,6),
                               f"Historical weighted formula: severity product × {multiplier} for affected users.")

    def compute(self, impact, urgency, criticality, users):
        return self.result(impact, urgency, criticality, users)["choice"]


class CategoryWeighted(PriorityStrategy):
    def result(self, impact, urgency, criticality, users, category_weight=1):
        bonus = next((points for minimum, points in ((500,12),(100,8),(25,4),(5,2)) if users >= minimum), 0)
        return priority_result(impact * urgency * criticality * category_weight + bonus, (36,18,7),
                               f"Severity product × category weight {category_weight:g} + {bonus} for affected users.")

    def compute(self, impact, urgency, criticality, users):
        return self.result(impact, urgency, criticality, users)["choice"]


class AssignmentStrategy(ABC):
    @abstractmethod
    def pick(self, cands, inc, load): ...

    def recommend(self, cands, inc, load):
        return [dict(choice=t.id, score=None, reason="Eligible technician") for t in cands]


class RankedAssignment(AssignmentStrategy):
    @abstractmethod
    def rank(self, tech, incident, active): ...

    def recommend(self, candidates, incident, load):
        ranked = [(tech, load(tech.id)) for tech in candidates]
        return [dict(choice=t.id, score=self.rank(t, incident, n)[0],
                     reason=f"{'Category skill match' if t.has_skill(incident.category) else 'General coverage'} · "
                            f"{n}/{t.capacity} active · experience {t.experience}")
                for t,n in sorted(ranked, key=lambda pair: self.rank(pair[0], incident, pair[1]), reverse=True)]

    def pick(self, cands, inc, load):
        best = max(cands, key=lambda t: self.rank(t, inc, load(t.id)), default=None)
        return best


class SkillBased(RankedAssignment):
    def rank(self, tech, incident, active):
        return (int(tech.has_skill(incident.category)), tech.experience, -active)


class LoadBased(RankedAssignment):
    def rank(self, tech, incident, active):
        return (-active,)


class ExperienceBased(RankedAssignment):
    def rank(self, tech, incident, active):
        return (tech.experience, int(tech.has_skill(incident.category)), -active)


class RoundRobin(AssignmentStrategy):
    def __init__(self): self.i = 0
    def pick(self, cands, inc, load):
        if not cands: return None
        t = cands[self.i % len(cands)]; self.i += 1; return t

    def recommend(self, cands, inc, load):
        if not cands:
            return []
        start = self.i % len(cands)
        return [dict(choice=t.id,score=len(cands)-position,reason=f"Rotation position {position+1}")
                for position,t in enumerate(cands[start:] + cands[:start])]


ASSIGNMENT_STRATEGIES = {"skill":SkillBased, "load":LoadBased, "roundrobin":RoundRobin, "experience":ExperienceBased}
PRIORITY_STRATEGIES = {"rule":RuleBased, "weighted":CategoryWeighted, "legacy":WeightedScore}


class Notifier(ABC):
    @abstractmethod
    def send(self, to, msg): ...
class InAppNotifier(Notifier):
    def __init__(self, repo, clock): self.repo, self.clock = repo, clock
    def send(self, to, msg): self.repo.save_notification(to, self.clock().isoformat(timespec="seconds"), msg)
    def for_user(self, uid): return [f"{ts[11:16]} {m}" for ts, m in self.repo.notifications(uid)]
class ExternalMailGateway:
    """Stand-in for a third-party mail SDK whose interface differs from our Notifier contract."""
    def __init__(self): self.sent = []
    def deliver(self, envelope): self.sent.append(envelope); return True
class EmailAdapter(Notifier):
    """ADAPTER: makes ExternalMailGateway.deliver(envelope) usable as Notifier.send(to, msg)."""
    def __init__(self, gateway=None): self.gateway = gateway or ExternalMailGateway()
    def send(self, to, msg): self.gateway.deliver({"to": f"{to}@corp.example", "subject": "Resolv notification", "body": msg})
    @property
    def outbox(self): return [(e["to"], e["body"]) for e in getattr(self.gateway, "sent", [])]
EmailNotifier = EmailAdapter

# ---------- Observer ----------
class Event:
    def __init__(self, kind, inc, actor, prev, new, desc, now):
        self.kind, self.inc, self.actor, self.prev, self.new, self.desc, self.now = kind, inc, actor, prev, new, desc, now
class Observer(ABC):
    @abstractmethod
    def update(self, e): ...
class EventBus:
    def __init__(self): self._obs = []
    def subscribe(self, o):
        if not isinstance(o, Observer): raise TypeError("observer must implement Observer")
        self._obs.append(o)
    def publish(self, e):
        for o in self._obs: o.update(e)
class AuditObserver(Observer):
    def __init__(self, repo): self.repo = repo
    def update(self, e):
        self.repo.audit(e.now.isoformat(timespec="seconds"), e.actor, e.kind, e.inc.id, e.prev, e.new, e.desc)
class NotificationObserver(Observer):
    def __init__(self, admin_ids, notifiers, tier_ids=None):
        self.admin_ids, self.notifiers = admin_ids, notifiers
        self.tier_ids = tier_ids or (lambda level: ())
    def update(self, e):
        to = {e.inc.reporter, e.inc.tech} - {None}
        if e.kind == "ESCALATED":
            to |= set(self.tier_ids(e.inc.escalation_level) or self.admin_ids())
        if e.kind in ("SLA_WARNING", "SLA_VIOLATED", "UNASSIGNED") or e.kind == "ADMIN_OVERRIDE" or (e.kind == "CREATED" and e.inc.priority == "P1"):
            to |= set(self.admin_ids())
        self.failures = getattr(self, "failures", [])
        for n in self.notifiers:
            recipients = to
            if hasattr(n, "send_event") and e.kind in ("SLA_WARNING", "SLA_VIOLATED", "ESCALATED", "ADMIN_OVERRIDE"):
                recipients = to | {"support"}
            for u in recipients:
                try:
                    if hasattr(n, "send_event"):
                        n.send_event(u, e)
                    else:
                        n.send(u, f"[{e.inc.id}] {e.kind}: {e.desc}")
                except Exception as ex:
                    if isinstance(n, InAppNotifier):
                        raise
                    self.failures.append((type(n).__name__, type(ex).__name__))

# ---------- Repository ----------
class Repository(ABC):
    """Persistence contract. The domain never sees SQL, rows or JSON; it gets and gives domain objects."""
    @abstractmethod
    def save_user(self, u): ...
    @abstractmethod
    def save_inc(self, i): ...
    @abstractmethod
    def save_cfg(self, k, v): ...
    def transaction(self): return nullcontext()
    def savepoint(self): return self.transaction()
    def record_sla_event(self, *args): return True
    def next_incident_id(self, incidents):
        return f"INC{1 + max([1000, *(int(key[3:]) for key in incidents)])}"
    def insert_inc(self, incident): self.save_inc(incident)
    def insert_user(self, user): self.save_user(user)
    @abstractmethod
    def audit(self, *row): ...
    @abstractmethod
    def users(self): ...          # -> list of User
    @abstractmethod
    def incidents(self): ...      # -> list of Incident
    @abstractmethod
    def cfg(self, k): ...
    @abstractmethod
    def audit_log(self, inc=None): ...
    @abstractmethod
    def save_notification(self, user, ts, msg): ...
    @abstractmethod
    def notifications(self, user): ...

class SQLiteRepository(Repository):
    """Maps domain objects to rows (JSON documents) and back. All storage details live here."""
    def __init__(self, path=":memory:"):
        try:
            self.c = sqlite3.connect(path, check_same_thread=False)
            self._transaction = False
            self.c.execute("PRAGMA recursive_triggers=ON")
            self.c.create_function("casefold", 1, lambda value: (value or "").casefold(), deterministic=True)
            migrate(self.c)
        except sqlite3.Error as ex: raise PersistenceError(str(ex))
    def _run(self, sql, *a):
        try:
            if self._transaction:
                return self.c.execute(sql, a).fetchall()
            with self.c:
                return self.c.execute(sql, a).fetchall()
        except sqlite3.Error as ex: raise PersistenceError(str(ex))
    @contextmanager
    def transaction(self):
        if self._transaction:
            yield
            return
        self._transaction = True
        try:
            with self.c:
                self.c.execute("BEGIN IMMEDIATE")
                yield
        except sqlite3.Error as ex:
            raise PersistenceError(str(ex)) from ex
        finally:
            self._transaction = False

    @contextmanager
    def savepoint(self):
        self._run("SAVEPOINT automatic_assignment")
        try:
            yield
        except BaseException:
            self._run("ROLLBACK TO automatic_assignment")
            raise
        finally:
            self._run("RELEASE automatic_assignment")

    def record_sla_event(self, incident, key, now, actor, detail, deadline=None, overdue=0):
        rows = self._run("""INSERT INTO sla_events VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(incident,cycle,event_key) DO NOTHING RETURNING event_key""",
            incident.id, incident.sla_cycle, key, self._iso(now), self._iso(deadline), overdue, actor, detail)
        return bool(rows)

    def sla_history(self, incident):
        rows = self._run("""SELECT cycle,event_key,happened_at,deadline,minutes_overdue,actor,detail
            FROM sla_events WHERE incident=? ORDER BY happened_at,rowid""", incident)
        names = ("cycle","event","timestamp","deadline","minutes_overdue","actor","detail")
        return [dict(zip(names,row)) for row in rows]

    @staticmethod
    def _iso(d): return d.isoformat() if d else None
    @staticmethod
    def _dt(v): return datetime.fromisoformat(v) if v else None
    def save_user(self, u):
        d = u.profile(); d["hash"] = u.password_hash
        self._run("UPDATE users SET data=? WHERE id=?", json.dumps(d), u.id)
    def insert_user(self, u):
        self._run("INSERT INTO users VALUES(?,?)", u.id, json.dumps(dict(u.profile(), hash=u.password_hash)))
    @staticmethod
    def _user(raw):
        d = json.loads(raw)
        return UserFactory.create(d["role"], d["id"], d["name"], pwhash=d["hash"], skills=d.get("skills", ()),
                                  capacity=d.get("capacity", 5), exp=d.get("exp", 1), available=d.get("available", True))
    def user(self, uid):
        rows = self._run("SELECT data FROM users WHERE id=?", uid)
        return self._user(rows[0][0]) if rows else None

    def matching_users(self, uid):
        return [self._user(raw) for (raw,) in
                self._run("SELECT data FROM users WHERE id = ? COLLATE NOCASE", uid)]
    def users(self):
        return [self._user(raw) for (raw,) in self._run("SELECT data FROM users")]

    def next_incident_id(self, incidents):
        floor = self._run("SELECT max(1000,coalesce(max(CAST(substr(id,4) AS INTEGER)),0)) FROM incidents")[0][0]
        row = self._run("""INSERT INTO sequences VALUES('incident',?) ON CONFLICT(name)
            DO UPDATE SET value=max(sequences.value+1,excluded.value) RETURNING value""", floor+1)
        return f"INC{row[0][0]}"

    def save_inc(self, incident):
        self._run("UPDATE incidents SET status=?,data=? WHERE id=?",
                  incident.state, self._incident_data(incident), incident.id)

    def insert_inc(self, incident):
        self._run("INSERT INTO incidents VALUES(?,?,?)", incident.id, incident.state, self._incident_data(incident))

    def _incident_data(self, i):
        d = dict(id=i.id, reporter=i.reporter, title=i.title, description=i.description, service=i.service, category=i.category,
                 impact=i.impact, urgency=i.urgency, users=i.users, priority=i.priority, created=self._iso(i.created), state=i.state,
                 tech=i.tech, responded=self._iso(i.responded), resolved=self._iso(i.resolved), resolution=i.resolution,
                 feedback=i.feedback, esc=i.escalation_level, related=i.related, flags=sorted(i.flags), comments=i.comments,
                 custom_category=i.custom_category, location=i.location, priority_override=i.priority_override,
                 sla_started=self._iso(i.sla_started), closed=self._iso(i.closed), parent=i.parent, feedback_id=i.feedback_id,
                 assigned_at=self._iso(i.assigned_at), verified_at=self._iso(i.verified_at),
                 response_due=self._iso(i.response_due), resolution_due=self._iso(i.resolution_due),
                 sla_cycle=i.sla_cycle, sla_risk=i.sla_risk, sla_schedule=i.sla_schedule)
        return json.dumps(d)
    def incidents(self):
        out = []
        for (raw,) in self._run("SELECT data FROM incidents"):
            d = json.loads(raw)
            for k in ("created", "responded", "resolved", "sla_started", "closed", "assigned_at", "verified_at", "response_due", "resolution_due"):
                d[k] = self._dt(d.get(k))
            if d.get("feedback") and not d.get("feedback_id"):
                d["feedback_id"] = uuid.uuid5(uuid.NAMESPACE_URL, "resolv:feedback:" + d["id"]).hex
            if d["state"] == "CLOSED" and not d["closed"]:
                history = self.audit_log(d["id"])
                d["closed"] = next((self._dt(row[0]) for row in reversed(history) if row[2] == "CLOSED"), None)
            out.append(Incident(**d))
        return out
    def add_relationship(self, first, other, score, kind):
        a, b = sorted((first, other))
        self._run("""INSERT INTO incident_relationships(incident_a,incident_b,score,kind) VALUES(?,?,?,?)
            ON CONFLICT(incident_a,incident_b) DO UPDATE SET score=excluded.score,kind=excluded.kind
            WHERE excluded.score > incident_relationships.score""", a,b,score,kind)

    def relationships(self, incident):
        return [dict(zip(("id","score","kind","decision"), row)) for row in self._run("""
            SELECT CASE WHEN incident_a=? THEN incident_b ELSE incident_a END,score,kind,decision
            FROM incident_relationships WHERE incident_a=? OR incident_b=? ORDER BY score DESC, 1""",
            incident,incident,incident)]

    def review_relationship(self, first, other, decision, actor, now):
        a, b = sorted((first, other))
        return bool(self._run("""UPDATE incident_relationships SET decision=?,reviewer=?,reviewed_at=?
            WHERE incident_a=? AND incident_b=? AND decision<>? RETURNING incident_a""",
            decision,actor,self._iso(now),a,b,decision))

    def search_incident_ids(self, filters, actor):
        created = "substr(json_extract(data,'$.created'),1,10)"
        expressions = {
            "id": "id", "status": "status", **{key: f"json_extract(data,'$.{key}')"
                for key in ("priority", "category", "service", "tech")},
            "date": created, "start": created, "end": created,
            "area": """coalesce(json_extract(data,'$.location.area'),
                CASE WHEN json_type(data,'$.location.pin')='object' THEN 'pin_only' ELSE 'none' END)""",
        }
        clauses, params = [], []
        for key, value in filters.items():
            if key == "q":
                fields = ("id", "json_extract(data,'$.title')", "json_extract(data,'$.custom_category')")
                clauses.append("(" + " OR ".join(f"instr(casefold({field}), ?) > 0" for field in fields) + ")")
                params.extend([value.casefold()] * len(fields))
            elif key in expressions:
                comparator = {"start": ">=", "end": "<="}.get(key, "=")
                clauses.append(f"{expressions[key]} {comparator} ?")
                params.append(value.upper() if key == "category" else value)
        if actor:
            if actor.role in ("EMPLOYEE", "TECHNICIAN"):
                key = "reporter" if actor.role == "EMPLOYEE" else "tech"
                clauses.append(f"json_extract(data,'$.{key}') = ?")
                params.append(actor.id)
            elif actor.minimum_escalation_tier is not None:
                clauses.append("coalesce(json_extract(data,'$.esc'),0) >= ?")
                params.append(actor.minimum_escalation_tier)
            elif actor.role != "ADMIN":
                return []
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [row[0] for row in self._run("SELECT id FROM incidents" + where, *params)]

    def save_cfg(self, k, v): self._run("INSERT OR REPLACE INTO config VALUES(?,?)", k, json.dumps(v))
    def audit(self, *row):
        return self._run("INSERT INTO audit(ts,user,action,inc,prev,new,descr) VALUES(?,?,?,?,?,?,?) RETURNING id", *row)[0][0]

    def audit_records(self, *, sort="oldest", order_by="ts", visibility="visible", **filters):
        columns = {"inc": "inc", "user": "user", "action": "action", "prev": "prev", "new": "new"}
        ordering = {"ts": "a.ts", "visibility": "coalesce(v.hidden,0)",
                    **{field: f"a.{column}" for field, column in columns.items()}}
        if sort not in ("newest", "oldest") or visibility not in ("visible", "hidden", "all") or order_by not in ordering:
            raise ValidationError("invalid audit sort or visibility")
        clauses, params = [], []
        if visibility != "all":
            clauses.append("coalesce(v.hidden,0)=?")
            params.append(int(visibility == "hidden"))
        for key, value in filters.items():
            if key in columns:
                clauses.append(f"a.{columns[key]}=? COLLATE NOCASE")
                params.append(value)
            elif key in ("from", "to"):
                timestamp = datetime.fromisoformat(value)
                date_only = len(value) == 10
                clauses.append(("substr(a.ts,1,10)" if date_only else "a.ts") + (">=?" if key == "from" else "<=?"))
                params.append(value if date_only else timestamp.isoformat(timespec="seconds"))
            elif key == "q":
                clauses.append("instr(lower(a.descr), lower(?))>0")
                params.append(value)
            else:
                raise ValidationError(f"unknown audit filter {key}")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        direction = "DESC" if sort == "newest" else "ASC"
        rows = self._run("""SELECT a.*, coalesce(v.hidden,0), l.target
            FROM audit a LEFT JOIN audit_visibility v ON v.event_id=a.rowid
            LEFT JOIN audit_links l ON l.event_id=a.rowid""" + where +
            f" ORDER BY {ordering[order_by]} COLLATE NOCASE {direction}, a.id {direction}", *params)
        return [dict(zip("id ts user action inc prev new desc hidden target".split(), row)) for row in rows]

    def audit_annotation(self, event_id, ts, actor, action, reason):
        with self.transaction():
            event = self._run("SELECT inc FROM audit WHERE rowid=?", event_id)
            if not event:
                raise NotFound("audit event")
            if action != "AUDIT_CORRECTION":
                hidden = int(action == "AUDIT_HIDDEN")
                current = self._run("SELECT hidden FROM audit_visibility WHERE event_id=?", event_id)
                if (current[0][0] if current else 0) == hidden:
                    raise ValidationError("audit event already has this visibility")
                self._run("INSERT OR REPLACE INTO audit_visibility VALUES(?,?)", event_id, hidden)
            link = self.audit(ts, actor, action, event[0][0], None, None, reason)
            self._run("INSERT INTO audit_links VALUES(?,?)", link, event_id)
            return link

    def add_attachments(self, incident, files):
        for file in files:
            self._run("INSERT INTO attachments VALUES(?,?,?)", file["id"], incident, json.dumps(file))

    def attachments(self, incident):
        return [json.loads(row[0]) for row in self._run("SELECT data FROM attachments WHERE incident=? ORDER BY rowid", incident)]

    def cfg(self, k):
        r = self._run("SELECT data FROM config WHERE k=?", k); return json.loads(r[0][0]) if r else None
    def audit_log(self, inc=None):
        return self._run("SELECT ts,user,action,inc,prev,new,descr FROM audit WHERE inc=? ORDER BY id", inc) if inc else self._run("SELECT ts,user,action,inc,prev,new,descr FROM audit ORDER BY id")
    def save_notification(self, user, ts, msg): self._run("INSERT INTO notifications VALUES(?,?,?)", user, ts, msg)
    def notifications(self, user): return self._run("SELECT ts,msg FROM notifications WHERE user=? ORDER BY rowid DESC LIMIT 50", user)
    def notification_feed(self, user):
        rows = self._run("""SELECT n.rowid,n.ts,n.msg,n.rowid > coalesce(s.last_id,0)
            FROM notifications n LEFT JOIN notification_seen s ON s.user=n.user
            WHERE n.user=? ORDER BY n.rowid DESC LIMIT 50""", user)
        return [dict(zip(("id","ts","message","unread"), row)) for row in rows]
    def mark_notifications_read(self, user, through):
        last = self._run("SELECT coalesce(max(rowid),0) FROM notifications WHERE user=? AND rowid<=?", user, through)[0][0]
        self._run("""INSERT INTO notification_seen VALUES(?,?) ON CONFLICT(user)
            DO UPDATE SET last_id=max(last_id,excluded.last_id)""", user, last)

# ---------- Service facade ----------
STOP = {"the", "a", "an", "is", "are", "to", "of", "in", "on", "and", "for", "my", "we", "user", "users", "make", "can", "t", "please", "issue", "problem", "not", "have", "has", "with"}
SYNONYMS = {"failure": {"unavailable", "down", "failing", "failed", "fail", "error", "erroring", "errors", "unable", "cannot", "outage", "broken", "crash", "crashing", "timeout"},
            "payment": {"payment", "payments", "pay", "transaction", "transactions", "checkout", "billing"}}
SYN_MAP = {w: k for k, ws in SYNONYMS.items() for w in ws}
def stem(w): return SYN_MAP.get(w) or (w[:-1] if len(w) > 4 and w.endswith("s") else w)
def tokens(t): return {stem(w) for w in re.findall(r"[a-z]+", t.lower())} - STOP
def jaccard(a, b):
    a, b = tokens(a), tokens(b); return len(a & b) / len(a | b) if a | b else 0

class IncidentAnalyzer:
    """Rule-based text intelligence (no ML): categorisation, related-incident detection, resolution suggestion."""
    THRESHOLD = 0.2
    def __init__(self, incidents, categories): self.incidents, self.categories = incidents, categories
    def category_options(self, text):
        terms = tokens(text)
        lowered = text.casefold()
        options = []
        for category, keywords in self.categories.items():
            matched = []
            for keyword in keywords:
                phrase = keyword.strip().casefold()
                if phrase and ((stem(phrase) in terms) if " " not in phrase
                               else re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", lowered)):
                    matched.append(keyword)
            if matched:
                options.append(dict(choice=category, score=sum(2 if " " in k else 1 for k in matched),
                                    reason="Matched " + ", ".join(matched)))
        return sorted(options,key=lambda item: -item["score"])

    def suggest_category(self, text):
        return next(iter(self.category_options(text)), {"choice":"OTHER"})["choice"]

    def matches(self, text, service, category, thresholds, exclude=None):
        related, duplicate = thresholds
        matches = []
        for item in self.incidents.values():
            if item.id == exclude or not item.is_open_for_dup():
                continue
            similarity = jaccard(text, item.title + " " + item.description)
            shared_service = item.service == service
            score = 0.72 * similarity + 0.20 * shared_service + 0.08 * (item.category == category)
            if similarity > 0 and score >= related:
                kind = "POSSIBLE_DUPLICATE" if shared_service and score >= duplicate else "RELATED"
                matches.append(dict(id=item.id,score=round(score,3),kind=kind,similarity=similarity))
        return sorted(matches,key=lambda item: (-item["score"],item["id"]))

    def find_related(self, text, service, exclude=None):
        return [(row["id"],row["score"]) for row in
                self.matches(text,service,self.suggest_category(text),(.4,.72),exclude)]

    def suggest_resolution(self, text, category, visible=None, service=None):
        candidates = []
        for item in self.incidents.values():
            if not item.resolution or (visible is not None and item.id not in visible):
                continue
            overlap = jaccard(text,item.title + " " + item.description)
            if overlap <= 0:
                continue
            score = .72 * overlap + .20 * (item.service == service) + .08 * (item.category == category)
            candidates.append((score,item.resolution))
        return max(candidates,key=lambda entry:entry[0],default=(0,None))[1]


class SLAEngine:
    """Deadlines, status, warnings, violations and escalation. Decoupled from Resolv via a publish callback."""
    WARN = 0.8
    def __init__(self, policies, incidents, now, publish, users=None, load=None, repo=None):
        self.policies, self.incidents, self.now, self.publish = policies, incidents, now, publish
        self.repo = repo
        self.users, self.load = users if users is not None else {}, load or (lambda _: 0)

    def score(self, inc, ratio):
        tech = self.users.get(inc.tech)
        workload = self.load(tech.id) / tech.capacity if isinstance(tech, Technician) else 0
        early = {"P1": .05, "P2": .02}.get(inc.priority, 0)
        if inc.category in {"SECURITY", "NETWORK", "DATABASE", "AUTHENTICATION"}:
            early += .02
        return ratio + early + min(.08, workload * .08) if ratio >= .5 else ratio
    def deadlines(self, inc):
        if inc.response_due and inc.resolution_due:
            return inc.response_due, inc.resolution_due
        if inc.priority not in self.policies: raise MissingSLA(inc.priority)
        r, s = self.policies[inc.priority]; return inc.sla_started + timedelta(minutes=r), inc.sla_started + timedelta(minutes=s)
    def status(self, inc):
        rd, sd = self.deadlines(inc); now = self.now()
        completed_response = inc.responded or inc.resolved
        late_resp = completed_response is not None and completed_response > rd
        if inc.resolved: return "BREACHED" if inc.resolved > sd or late_resp else "MET"
        if now > sd or late_resp or (inc.responded is None and now > rd): return "BREACHED"
        ratios = [inc.sla_fraction(now,sd)]
        if inc.responded is None: ratios.append(inc.sla_fraction(now,rd))
        return "AT_RISK" if self.score(inc, max(ratios)) >= self.WARN else "OK"
    def emit_once(self, event_key, kind, updated, description, now, actor="system", deadline=None, overdue=0):
        with self.repo.transaction():
            if not self.repo.record_sla_event(updated, event_key, now, actor, description, deadline, overdue):
                return False
            self.publish(kind, updated, description, actor)
        return True

    def advance_level(self, incident, now, actor="system", reason=None):
        updated = deepcopy(incident)
        level = incident.escalation_level + 1
        if level >= len(CHAIN):
            raise InvalidTransition("incident is already at the highest escalation tier")
        updated.escalate_to(level)
        description = f"escalated to {CHAIN[level]}" + (f" · {reason}" if reason else "")
        emitted = self.emit_once(f"ESCALATED_{level}", "ESCALATED", updated, description, now, actor)
        incident.__dict__.update(updated.__dict__)
        return emitted

    def manual_escalate(self, actor, incident, reason):
        require_role(actor, "ADMIN", "TEAM_LEAD", "IT_MANAGER")
        if not actor.can_view_incident(incident):
            raise Unauthorized("no access to this incident")
        if not incident.sla_tracked:
            raise InvalidTransition("only open incidents can be escalated")
        if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 500:
            raise ValidationError("add an escalation reason (1–500 characters)")
        self.advance_level(incident, self.now(), actor.id, reason.strip())
        return incident

    def monitor(self):
        out, now = [], self.now()
        for inc in list(self.incidents.values()):
            if not inc.sla_tracked and not inc.resolved:
                continue
            rd, sd = self.deadlines(inc)
            breach_ratio = 0
            for tag, deadline, completed in (("resp", rd, inc.responded or inc.resolved), ("res", sd, inc.resolved)):
                measured = completed or now
                ratio = inc.sla_fraction(measured,deadline)
                if measured > deadline:
                    kind = "SLA_VIOLATED"
                    breach_ratio = max(breach_ratio, ratio)
                elif completed is None and self.score(inc, ratio) >= self.WARN:
                    kind = "SLA_WARNING"
                else:
                    continue
                updated = deepcopy(inc)
                key = f"{kind}_{tag}"
                if updated.raise_flag(key):
                    overdue = inc.business_minutes_overdue(deadline,measured)
                    if self.emit_once(key, kind, updated, f"{tag} deadline {deadline:%H:%M}", now,
                                      deadline=deadline, overdue=overdue):
                        inc.__dict__.update(updated.__dict__)
                        out.append((inc.id, kind))
            if inc.sla_tracked:
                target = min(len(CHAIN) - 1, int(breach_ratio))
                while inc.escalation_level < target:
                    if self.advance_level(inc, now):
                        out.append((inc.id, "ESCALATED"))
            risk = self.status(inc)
            if risk != inc.sla_risk:
                updated = deepcopy(inc)
                updated.mark_sla_risk(risk)
                self.repo.save_inc(updated)
                inc.__dict__.update(updated.__dict__)
        return out

CHAIN = ["Technician", "Team Lead", "System Administrator", "IT Manager"]

def require_role(actor, *roles):
    if actor.role not in roles: raise Unauthorized(f"{actor.role} may not do this")

class Clock:
    """Current time, plus a demo offset so SLA violations can be shown without waiting."""
    def __init__(self, source=datetime.now): self._source, self._offset = source, timedelta(0)
    def now(self): return self._source() + self._offset
    def advance(self, minutes):
        if not 0 < minutes <= 10080: raise ValidationError("minutes must be 1-10080")
        self._offset += timedelta(minutes=minutes)
    offset_minutes = property(lambda s: int(s._offset.total_seconds() // 60))

class UserService:
    """Accounts: login, registration, administration of users and technicians."""
    def __init__(self, users, repo, now): self.users, self.repo, self.now = users, repo, now
    @staticmethod
    def _check_credentials(uid, pw):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,20}", uid if isinstance(uid, str) else ""): raise ValidationError("id must be 2-20 letters, digits or . _ -")
        if not isinstance(pw, str) or not 12 <= len(pw) <= 256: raise ValidationError("password must contain 12–256 characters")
    def _audit(self, admin, kind, desc): self.repo.audit(self.now().isoformat(timespec="seconds"), admin.id, kind, "-", None, None, desc)
    def login(self, uid, pw):
        if not isinstance(uid, str) or not isinstance(pw, str) or len(uid) > 80 or len(pw) > 256:
            raise AuthError("invalid credentials")
        uid = uid.strip()
        u = self.users.get(uid)
        if u is None:
            matches = [account for key, account in self.users.items() if key.casefold() == uid.casefold()]
            u = matches[0] if len(matches) == 1 else None
        encoded = u.password_hash if u else DUMMY_PASSWORD_HASH
        current = encoded.startswith(f"{PASSWORD_ROUNDS}$")
        if not password_matches(pw, encoded) or not u:
            if not current:
                password_matches(pw, DUMMY_PASSWORD_HASH)
            raise AuthError("invalid credentials")
        if not current:
            u.set_password(pw)
            self.repo.save_user(u)
        return u
    def add(self, admin, role, uid, name, pw, **kw):
        if admin is not None: require_role(admin, "ADMIN")
        if any(other.casefold() == uid.casefold() for other in self.users):
            raise ValidationError("This user ID is already in use.")
        u = UserFactory.create(role, uid, name, pw, **kw); self.repo.insert_user(u); self.users[uid] = u; return u
    def register(self, uid, name, pw):
        uid = uid.strip() if isinstance(uid, str) else uid
        self._check_credentials(uid, pw); return self.add(None, "EMPLOYEE", uid, (name or "").strip(), pw)
    def create(self, admin, role, uid, name, pw, **kw):
        require_role(admin, "ADMIN")
        uid = uid.strip() if isinstance(uid, str) else uid
        self._check_credentials(uid, pw)
        u = self.add(admin, role, uid, (name or "").strip(), pw, **kw); self._audit(admin, "USER_CREATED", f"{role} {uid}"); return u
    def update_technician(self, admin, tid, **changes):
        require_role(admin, "ADMIN"); t = self.users.get(tid)
        if not t or t.role != "TECHNICIAN": raise NotFound(f"technician {tid}")
        t.update_profile(**changes)                      # the Technician validates and applies atomically
        self.repo.save_user(t); self._audit(admin, "TECHNICIAN_UPDATED", f"{tid}: skills={sorted(t.skills)} cap={t.capacity} available={t.available}"); return t
    def list_profiles(self, admin): require_role(admin, "ADMIN"); return [u.profile() for u in self.users.values()]
    def admin_ids(self): return [u.id for u in self.users.values() if u.role == "ADMIN"]
    def technician_ids(self): return [u.id for u in self.users.values() if u.role == "TECHNICIAN"]
    def escalation_recipients(self, level):
        role = {1:"TEAM_LEAD", 2:"ADMIN", 3:"IT_MANAGER"}.get(level)
        return [u.id for u in self.users.values() if u.role == role]

class ConfigService:
    """Admin-managed reference data: IT services, incident categories, SLA policies."""
    def __init__(self, services, categories, sla, repo): self.services, self.categories, self.sla, self.repo = services, categories, sla, repo
    def add_service(self, admin, name, criticality):
        require_role(admin, "ADMIN")
        name = name.strip()
        if not 1 <= len(name) <= 100 or not 1 <= criticality <= 4:
            raise ValidationError("service name must contain 1–100 characters and criticality must be 1–4")
        updated = dict(self.services, **{name: criticality})
        self.repo.save_cfg("services", updated)
        self.services.update(updated)
    def add_category(self, admin, name, keywords):
        require_role(admin, "ADMIN")
        key = name.strip().upper()
        if len(keywords) > 50 or any(len(word) > 80 for word in keywords):
            raise ValidationError("enter up to 50 keywords of 80 characters each")
        if not re.fullmatch(r"[\w][\w .&/+()-]{0,79}", key) or key in self.categories:
            raise ValidationError("invalid or duplicate category name")
        updated = dict(self.categories, **{key: keywords})
        self.repo.save_cfg("categories", updated)
        self.categories.update(updated)
    def manage_catalog(self, admin, kind, name, action, value, now):
        require_role(admin, "ADMIN")
        if kind not in ("services", "categories") or action not in ("update", "remove"):
            raise ValidationError("unknown catalog operation")
        source = self.services if kind == "services" else self.categories
        if name not in source:
            raise NotFound("catalog entry")
        updated = dict(source)
        if action == "remove":
            if kind == "services":
                dependencies = self.repo.cfg("dependencies") or {}
                required = dependencies.get(name) or any(name in edges for edges in dependencies.values())
                required = required or any(i.service == name for i in self.repo.incidents())
            else:
                required = name == "OTHER" or any(i.category == name for i in self.repo.incidents())
            if required:
                raise ValidationError("this entry is in use and must be retained")
            del updated[name]
        elif kind == "services":
            if type(value) is not int or not 1 <= value <= 4:
                raise ValidationError("criticality must be 1–4")
            updated[name] = value
        else:
            keywords = [word.strip().lower() for word in value.split(",") if word.strip()]
            if name == "OTHER" or len(keywords) > 50 or any(len(word) > 80 for word in keywords):
                raise ValidationError("enter up to 50 keywords of 80 characters each for a configured category")
            updated[name] = keywords
        with self.repo.transaction():
            self.repo.save_cfg(kind, updated)
            self.repo.audit(now.isoformat(timespec="seconds"), admin.id, "CATALOG_UPDATED", "-", None, None,
                            json.dumps({"action":action,"kind":kind,"name":name,"before":source[name],"after":updated.get(name)}))
        source.clear()
        source.update(updated)

    def set_sla(self, admin, prio, resp, res):
        require_role(admin, "ADMIN")
        if prio not in ("P1", "P2", "P3", "P4") or not 0 < resp <= res <= 525_600: raise ValidationError("SLA must satisfy 0 < response ≤ resolution ≤ 525,600 minutes")
        self.sla[prio] = (resp, res); self.repo.save_cfg("sla", self.sla)

class IncidentService:
    """The incident lifecycle: report, assign, work, collaborate, edit, dispatch. Publishes events; never touches SQL."""
    def __init__(self, incidents, users, services, sla, analyzer, priority, assignment, bus, repo, now):
        self.incidents, self.users, self.services, self.sla, self.analyzer = incidents, users, services, sla, analyzer
        self.priority, self.assignment, self.bus, self.repo, self.now = priority, assignment, bus, repo, now
        self.settings = repo.cfg("intelligence_settings") or dict(related=.4, duplicate=.72, weights={})
        self.sla_schedule = repo.cfg("sla_schedule")

    def configure_calendar(self, admin, value):
        require_role(admin,"ADMIN")
        try:
            schedule = normalize_schedule(value)
        except (ValueError,TypeError) as error:
            raise ValidationError(str(error)) from error
        self.repo.save_cfg("sla_schedule",schedule)
        self.sla_schedule = schedule

    def configure_intelligence(self, admin, related, duplicate, weights):
        require_role(admin, "ADMIN")
        if not 0 < related <= duplicate <= 1 or not isinstance(weights, dict):
            raise ValidationError("thresholds must be ordered and greater than zero and at most 1")
        if any(c not in self.analyzer.categories or type(w) not in (float,int) or not .25 <= w <= 3
               for c,w in weights.items()):
            raise ValidationError("category weights must be between 0.25 and 3")
        settings = dict(related=related,duplicate=duplicate,weights=weights)
        self.repo.save_cfg("intelligence_settings",settings)
        self.settings = settings

    def priority_result(self, impact, urgency, service, users, category):
        return self.priority.result(impact,urgency,self.services[service],users,
                                    self.settings["weights"].get(category,1))

    def relationships(self, incident):
        if hasattr(self.repo,"relationships"):
            return self.repo.relationships(incident.id)
        return [dict(id=key,score=0,kind="RELATED",decision="PENDING") for key in incident.related]

    def review_related(self, admin, iid, other, decision):
        require_role(admin,"ADMIN")
        self.get(iid)
        self.get(other)
        if decision not in ("CONFIRMED","DISMISSED","PENDING"):
            raise ValidationError("choose a relationship decision")
        with self.repo.transaction():
            if not self.repo.review_relationship(iid,other,decision,admin.id,self.now()):
                raise InvalidTransition("relationship is missing or already has that decision")
            incident = self.get(iid)
            self.bus.publish(Event("RELATIONSHIP_REVIEWED",incident,admin.id,incident.state,incident.state,
                                   f"{other}: {decision}",self.now()))

    def get(self, iid):
        if iid not in self.incidents: raise NotFound(f"incident {iid}")
        return self.incidents[iid]
    def emit(self, kind, inc, actor, prev, desc):
        with self.repo.transaction():
            self.repo.save_inc(inc)
            self.bus.publish(Event(kind, inc, actor, prev, inc.state, desc, self.now()))
    def load(self, tid): return sum(1 for i in self.incidents.values() if i.tech == tid and i.in_workload)
    def _candidates(self): return [t for t in self.users.values() if t.role == "TECHNICIAN" and t.can_accept(self.load(t.id))]
    def category_fields(self, category, custom_category):
        key = (category or "").strip().upper()
        if key not in self.analyzer.categories:
            raise ValidationError("unknown category")
        label = (custom_category or "").strip()
        if key == "OTHER" and not 1 <= len(label) <= 100:
            raise ValidationError("describe the Other category (1–100 characters)")
        if key != "OTHER" and label:
            raise ValidationError("custom category requires Other")
        return key, label or None

    def report(self, actor, title, desc, service, impact, urgency, users=1, category=None,
               custom_category=None, location=None, attachments=()):
        with self.repo.transaction():
            inc = self._report(actor, title, desc, service, impact, urgency, users,
                               category, custom_category, location, attachments)
        self.incidents[inc.id] = inc
        return inc

    def _report(self, actor, title, desc, service, impact, urgency, users,
                category, custom_category, location, attachments):
        require_role(actor, "EMPLOYEE", "ADMIN")
        Incident.check_content(title, desc, impact, urgency, users)
        if service not in self.services: raise NotFound(f"service {service}")
        text = title + " " + desc
        key, label = (self.category_fields(category, custom_category) if category or custom_category
                      else (self.analyzer.suggest_category(text), None))
        matches = self.analyzer.matches(text,service,key,(self.settings["related"],self.settings["duplicate"]))
        for match in matches:
            if match["similarity"] >= .8 and self.incidents[match["id"]].reporter == actor.id and self.incidents[match["id"]].service == service:
                raise DuplicateIncident(match["id"])
        result = self.priority_result(impact,urgency,service,users,key)
        prio = result["choice"]
        if prio not in self.sla: raise MissingSLA(prio)
        iid = self.repo.next_incident_id(self.incidents)
        inc = Incident(iid, actor.id, title, desc, service, key, impact, urgency, users, prio, self.now(),
                       related=[match["id"] for match in matches], custom_category=label, location=location)
        inc.apply_sla_policy(self.sla[prio],self.sla_schedule)
        inc.mark_sla_risk("OK")
        created = Event("CREATED",deepcopy(inc),actor.id,None,inc.state,
                        f"created by {actor.id}; priority {prio} · {result['reason']}",self.now())
        inc.move("CLASSIFIED",self.now())
        self.repo.insert_inc(inc)
        if attachments:
            self.repo.add_attachments(inc.id,attachments)
        if hasattr(self.repo,"add_relationship"):
            for match in matches:
                self.repo.add_relationship(inc.id,match["id"],match["score"],match["kind"])
        self.bus.publish(created)
        self.bus.publish(Event("CLASSIFIED",inc,"system","NEW",inc.state,f"category {inc.category}",self.now()))
        if matches:
            self.bus.publish(Event("RELATED",inc,"system",inc.state,inc.state,
                                   f"{len(matches)} potentially related incident(s)",self.now()))
        assigned = deepcopy(inc)
        failure = "no technician available; queued for dispatch"
        try:
            with self.repo.savepoint():
                technician, recommendation = self.choose_assignment(assigned)
                if technician:
                    self._assign(assigned,technician,"system",recommendation)
        except (ITSMError, RuntimeError, ValueError, TypeError, AttributeError):
            failure = "automatic assignment failed; queued for dispatch"
        else:
            if assigned.tech:
                return assigned
        self.emit("UNASSIGNED",inc,"system",inc.state,failure)
        return inc
    def preview(self, actor, title, desc, service, impact, urgency, users, category=None):
        require_role(actor, "EMPLOYEE", "ADMIN")
        if service not in self.services:
            raise NotFound("service")
        impact, urgency, users = int(impact), int(urgency), int(users)
        Incident.check_content("draft", "draft", impact, urgency, users)
        if len(title) > 200 or len(desc) > 5000:
            raise ValidationError("draft exceeds the text limits")
        text = title + " " + desc
        alternatives = self.analyzer.category_options(text)
        chosen = category or next(iter(alternatives),{"choice":"OTHER"})["choice"]
        if chosen not in self.analyzer.categories:
            raise ValidationError("unknown category")
        result = self.priority_result(impact,urgency,service,users,chosen)
        priority = result["choice"]
        if priority not in self.sla:
            raise MissingSLA(priority)
        visible = {item.id for item in self.incidents.values() if self.can_view(actor,item)}
        matches = self.analyzer.matches(text,service,chosen,(self.settings["related"],self.settings["duplicate"]))
        references = [dict(id=row["id"], title=self.incidents[row["id"]].title,score=row["score"],kind=row["kind"])
                      for row in matches if row["id"] in visible][:5]
        response,resolution = self.sla[priority]
        draft = Incident("preview",actor.id,title or "Draft",desc or "Draft",service,chosen,impact,urgency,users,priority,self.now())
        recommendations = self.assignment.recommend(self._candidates(),draft,self.load)[:3]
        return dict(priority=priority, score=result["score"], reason=result["reason"], category=chosen,
                    alternatives=alternatives[:3], criticality=self.services[service], strategy=type(self.priority).__name__,
                    response=response,resolution=resolution,matches=references,
                    correlated=[dict(id=i.id,title=i.title) for i in self.incidents.values()
                                if i.id in visible and i.service == service and i.is_open_for_dup()][:5],
                    recommendations=recommendations,
                    suggested_resolution=self.analyzer.suggest_resolution(text,chosen,visible,service))

    def set_parent(self, admin, iid, parent):
        require_role(admin, "ADMIN")
        inc = self.get(iid)
        ancestor = self.get(parent) if parent else None
        while ancestor:
            if ancestor.id == iid:
                raise ValidationError("incident links must not form a cycle")
            ancestor = self.get(ancestor.parent) if ancestor.parent else None
        updated = deepcopy(inc)
        updated.link_parent(parent)
        return self._save_change(inc, updated, "PARENT_UPDATED", admin.id,
                                 f"Parent changed from {inc.parent or 'none'} to {parent or 'none'}")

    def _save_change(self, incident, updated, kind, actor, description):
        self.emit(kind, updated, actor, incident.state, description)
        incident.__dict__.update(updated.__dict__)
        return incident

    def choose_assignment(self, incident):
        candidates = self._candidates()
        recommendations = self.assignment.recommend(candidates,incident,self.load)
        technician = self.assignment.pick(candidates,incident,self.load)
        if technician is None:
            return None,None
        if technician not in candidates:
            raise ValidationError("strategy selected an ineligible technician")
        reason = next((entry["reason"] for entry in recommendations if entry["choice"] == technician.id),
                      "Selected an eligible technician")
        return technician, reason

    def _assign(self, inc, tech, who, reason="manual assignment"):
        if self.load(tech.id) >= tech.capacity: raise WorkloadExceeded(tech.id)
        updated = deepcopy(inc)
        updated.assign_to(tech.id, self.now())
        self._save_change(inc, updated, "ASSIGNED", who, f"assigned to {tech.id} · {reason}")
    def can_view(self, actor, inc):
        return actor.can_view_incident(inc)

    def accessible(self, actor, iid):
        inc = self.get(iid)
        if not self.can_view(actor, inc):
            raise Unauthorized("no access to this incident")
        return inc

    def override(self, admin, iid, reason, changes):
        require_role(admin, "ADMIN")
        reason = (reason or "").strip()
        if not 1 <= len(reason) <= 1000:
            raise ValidationError("override reason required (1–1000 characters)")
        inc = self.get(iid)
        fields = dict(changes)
        allowed = {"state", "tech", "priority_override", "title", "description", "category",
                   "custom_category", "service", "impact", "urgency", "users"}
        if not fields or fields.keys() - allowed:
            raise ValidationError("select valid override fields")
        value = lambda key: fields.get(key, getattr(inc, key))
        state, tech, service = value("state"), value("tech"), value("service")
        if state not in STATES:
            raise ValidationError("unknown lifecycle state")
        if inc.state == "CLOSED" and state != "CLOSED" and not STATES[state].open_phase:
            raise ValidationError("reopen a closed incident into an active state")
        if "tech" in fields:
            technician = self.users.get(tech)
            if not isinstance(technician, Technician) or not technician.available:
                raise ValidationError("choose an available technician")
            if inc.tech != tech and self.load(tech) >= technician.capacity:
                raise WorkloadExceeded(tech)
            if inc.awaiting_assignment and "state" not in fields:
                fields["state"] = state = "ASSIGNED"
        if STATES[state].counts_as_workload and not tech:
            raise ValidationError("select a technician for this state")
        if STATES[state].counts_as_workload and not inc.in_workload and "tech" not in fields:
            technician = self.users.get(tech)
            if not isinstance(technician, Technician) or not technician.can_accept(self.load(tech)):
                raise ValidationError("select an eligible technician for this state")
        if state in ("NEW", "CLASSIFIED") and state != inc.state:
            if "tech" in fields:
                raise ValidationError("choose an assigned state when setting a technician")
            fields["tech"] = None
        if service not in self.services:
            raise ValidationError("unknown service")
        if {"category", "custom_category"} & fields.keys():
            label = fields.get("custom_category", inc.custom_category if value("category") == "OTHER" else None)
            fields["category"], fields["custom_category"] = self.category_fields(value("category"), label)
        Incident.check_content(*(value(key) for key in ("title", "description", "impact", "urgency", "users")))
        manual = value("priority_override")
        priority = manual if manual is not None else self.priority_result(
            value("impact"),value("urgency"),service,value("users"),value("category"))["choice"]
        if priority not in self.sla:
            raise ValidationError("invalid override priority")
        updated = deepcopy(inc)
        updated.apply_override(fields, priority, manual, self.now())
        if updated.priority != inc.priority or updated.sla_cycle != inc.sla_cycle:
            updated.apply_sla_policy(self.sla[priority],self.sla_schedule if updated.sla_cycle != inc.sla_cycle else inc.sla_schedule)
        if updated.tech and updated.tech != inc.tech:
            updated.mark_assigned(self.now())
        diff = {k: [getattr(inc, k), getattr(updated, k)] for k in fields.keys() | {"priority"}
                if getattr(inc, k) != getattr(updated, k)}
        if not diff:
            raise ValidationError("no changes to apply")
        with self.repo.transaction():
            if updated.state != inc.state:
                self.emit(updated.state, updated, admin.id, inc.state, "Lifecycle transition via Admin Override")
            if updated.tech != inc.tech and updated.tech:
                self.emit("ASSIGNED", updated, admin.id, inc.state, f"{inc.tech or 'Unassigned'} → {updated.tech}")
            if updated.priority != inc.priority:
                self.emit("PRIORITY_CHANGED", updated, admin.id, inc.state, f"{inc.priority} → {updated.priority}")
            self.emit("ADMIN_OVERRIDE", updated, admin.id, inc.state,
                      json.dumps({"reason": reason, "changes": diff}, sort_keys=True, ensure_ascii=False))
        inc.__dict__.update(updated.__dict__)
        return inc

    def assign(self, admin, iid, tid):
        require_role(admin, "ADMIN"); t = self.users.get(tid)
        if not t or t.role != "TECHNICIAN": raise NotFound(f"technician {tid}")
        self._assign(self.get(iid), t, admin.id)
    def dispatch_pending(self):
        """Assign queued incidents, most urgent first, whenever technicians become available."""
        done = []
        for inc in sorted((i for i in self.incidents.values() if i.awaiting_assignment), key=lambda i: (i.priority, i.created)):
            tech, reason = self.choose_assignment(inc)
            if not tech:
                break
            self._assign(inc,tech,"system",reason)
            done.append(inc.id)
        return done
    def _step(self, actor, iid, target, desc, rule=None, record=None):
        """The single path for every lifecycle change: authorise, validate the transition, then apply and publish."""
        inc = self.get(iid)
        if rule and not rule[0](actor, inc): raise Unauthorized(rule[1])
        updated = deepcopy(inc)
        updated.move(target, self.now())
        if record: record(updated)
        return self._save_change(inc, updated, target, actor.id, desc)
    WORK = (lambda a, i: a.can_work_on(i), "not your incident")
    REPORTER = (lambda a, i: a.acts_as_reporter_for(i), "only reporter verifies")
    def acknowledge(self, tech, iid): return self._step(tech, iid, "ACKNOWLEDGED", "acknowledged", self.WORK)
    def start(self, tech, iid): return self._step(tech, iid, "IN_PROGRESS", "investigation started", self.WORK)
    def resolve(self, tech, iid, resolution):
        if not resolution.strip() or len(resolution) > 5000: raise ValidationError("resolution must contain 1–5,000 characters")
        inc = self._step(tech, iid, "RESOLVED", f"resolved: {resolution}", self.WORK, lambda i: i.record_resolution(resolution))
        self.dispatch_pending(); return inc
    def verify(self, user, iid, feedback=None): return self._step(user, iid, "VERIFIED", "verified by reporter", self.REPORTER, lambda i: i.record_feedback(feedback))
    def close(self, admin, iid): require_role(admin, "ADMIN"); return self._step(admin, iid, "CLOSED", "closed")
    def comment(self, actor, iid, text, kind=None):
        inc = self.get(iid)
        if not text.strip(): raise ValidationError("text required")
        ok = actor.comment_kinds(inc)
        if not ok: raise Unauthorized("no access to this incident")
        kind = kind or ("NOTE" if "NOTE" in ok else "REPLY" if inc.has_info_request() else "INFO")
        if kind not in ok: raise Unauthorized(f"{actor.role} cannot post {kind}")
        updated = deepcopy(inc)
        updated.add_comment(self.now().isoformat(timespec="seconds"), actor.id, kind, text)
        return self._save_change(inc, updated, kind, actor.id, text[:80])
    def update(self, actor, iid, **f):
        inc = self.get(iid)
        if not actor.acts_as_reporter_for(inc): raise Unauthorized("only reporter or admin may edit")
        if not inc.editable: raise InvalidTransition("cannot edit after resolution")
        new = dict(title=inc.title, description=inc.description, impact=inc.impact, urgency=inc.urgency, users=inc.users)
        for k, v in f.items():
            if k not in new: raise ValidationError(f"cannot update {k}")
            new[k] = v
        Incident.check_content(new["title"], new["description"], new["impact"], new["urgency"], new["users"])
        prio = inc.priority_override or self.priority_result(new["impact"],new["urgency"],inc.service,
                                                               new["users"],inc.category)["choice"]
        if prio not in self.sla: raise MissingSLA(prio)
        updated = deepcopy(inc)
        updated.revise(new["title"], new["description"], new["impact"], new["urgency"], new["users"], prio)
        if prio != inc.priority:
            updated.apply_sla_policy(self.sla[prio],inc.sla_schedule)
        kind = "PRIORITY_CHANGED" if prio != inc.priority else "UPDATED"
        return self._save_change(inc, updated, kind, actor.id, f"updated {sorted(f)}; priority {inc.priority}->{prio}")

class ReportService:
    """Read-only views: search, dashboards, workload, audit."""
    def __init__(self, incidents, users, sla_engine, load, repo): self.incidents, self.users, self.sla_engine, self.load, self.repo = incidents, users, sla_engine, load, repo
    def search(self, actor=None, **f):
        keys = dict(id=lambda i, v: i.id == v, status=lambda i, v: i.state == v, priority=lambda i, v: i.priority == v,
                    category=lambda i, v: i.category == v.upper(), service=lambda i, v: i.service == v, tech=lambda i, v: i.tech == v,
                    sla_status=lambda i, v: self.sla_engine.status(i) == v, date=lambda i, v: i.created.date().isoformat() == v,
                    start=lambda i, v: i.created.date().isoformat() >= v, end=lambda i, v: i.created.date().isoformat() <= v,
                    area=lambda i, v: ((i.location or {}).get("area") or ("pin_only" if (i.location or {}).get("pin") else "none")) == v,
                    q=lambda i, v: any(v.casefold() in text.casefold() for text in
                                       (i.id, i.title, i.custom_category or "")),
                    scope=lambda i, v: {"open": i.sla_tracked, "urgent": i.priority in ("P1", "P2"),
                                        "workload": i.in_workload, "critical_open": i.sla_tracked and i.priority == "P1", "resolved": not i.sla_tracked}.get(v, False))
        for key in f:
            if key not in keys:
                raise ValidationError(f"unknown filter {key}")
        if hasattr(self.repo, "search_incident_ids"):
            ids = self.repo.search_incident_ids(f, actor)
            res = [self.incidents[key] for key in ids if key in self.incidents]
        else:
            res = [i for i in self.incidents.values() if actor is None or actor.can_view_incident(i)]
        for k, v in f.items():
            res = [i for i in res if keys[k](i, v)]
        return res
    def overview(self):
        a = list(self.incidents.values()); open_ = sum(i.sla_tracked for i in a)
        return dict(total=len(a), open=open_, critical=sum(i.priority == "P1" for i in a), resolved=len(a) - open_,
                    sla_violations=sum(self.sla_engine.status(i) == "BREACHED" for i in a))
    def workload(self): return {t.id: self.load(t.id) for t in self.users.values() if t.role == "TECHNICIAN"}
    def by_service(self):
        c = {}
        for i in self.incidents.values(): c[i.service] = c.get(i.service, 0) + 1
        return c
    def technician_workload(self, actor, tech_id=None):
        require_role(actor, "TECHNICIAN", "ADMIN"); tid = actor.id if actor.role == "TECHNICIAN" else tech_id
        t = self.users.get(tid)
        if not t or t.role != "TECHNICIAN": raise NotFound(f"technician {tid}")
        return dict(tech=tid, load=self.load(tid), capacity=t.capacity, skills=sorted(t.skills),
                    incidents=[i for i in self.incidents.values() if i.tech == tid and i.in_workload])
    def audit_log(self, admin, inc=None): require_role(admin, "ADMIN"); return self.repo.audit_log(inc)

    def other_category_usage(self, admin):
        require_role(admin, "ADMIN")
        labels, counts = {}, Counter()
        for incident in self.incidents.values():
            if incident.custom_category:
                key = incident.custom_category.casefold()
                labels.setdefault(key, incident.custom_category)
                counts[key] += 1
        return [dict(label=labels[key], count=count)
                for key, count in counts.most_common()]

    def audit_records(self, admin, **filters):
        require_role(admin, "ADMIN")
        return self.repo.audit_records(**filters)

    def annotate_audit(self, admin, event_id, operation, reason, now):
        require_role(admin, "ADMIN")
        action = {"hide": "AUDIT_HIDDEN", "restore": "AUDIT_RESTORED", "correct": "AUDIT_CORRECTION"}.get(operation)
        reason = (reason or "").strip()
        if not action or not 1 <= len(reason) <= 1000:
            raise ValidationError("an audit action and note (1–1000 characters) are required")
        return self.repo.audit_annotation(event_id, now.isoformat(timespec="seconds"), admin.id, action, reason)

class Resolv:
    """FACADE and composition root. Wires the services together and keeps one stable public API.
    It holds no business rules of its own; each method delegates to the service that owns the responsibility."""
    def __init__(self, repo=None, clock=datetime.now, priority=None, assignment=None, notifiers=None, extra_notifier=None):
        self.repo = repo or SQLiteRepository(); self.clock = Clock(clock)
        self.users, self.incidents = {}, {}
        self.services = self.repo.cfg("services") or {}
        self.categories = self.repo.cfg("categories") or {
            "NETWORK": ["network", "vpn", "wifi", "internet"], "DATABASE": ["database", "sql", "query"],
            "APPLICATION": ["payment", "application", "app", "transaction"], "AUTHENTICATION": ["login", "password", "sso"],
            "HARDWARE": ["laptop", "printer", "disk"], "STORAGE": ["storage", "bucket"], "SECURITY": ["phishing", "breach", "malware"], "OTHER": []}
        self.categories.setdefault("OTHER", [])
        saved_sla = self.repo.cfg("sla")
        self.sla = saved_sla if saved_sla is not None else {"P1": (5, 30), "P2": (15, 120), "P3": (60, 480), "P4": (240, 1440)}
        self.analyzer = IncidentAnalyzer(self.incidents, self.categories)
        self.user_service = UserService(self.users, self.repo, self.now)
        self.config_service = ConfigService(self.services, self.categories, self.sla, self.repo)
        self.bus = EventBus()
        self.incident_service = IncidentService(self.incidents, self.users, self.services, self.sla, self.analyzer,
                                                priority or PRIORITY_STRATEGIES[self.repo.cfg("priority_strategy") or "rule"](),
                                                assignment or ASSIGNMENT_STRATEGIES[self.repo.cfg("assignment_strategy") or "skill"](),
                                                self.bus,self.repo,self.now)
        self.sla_engine = SLAEngine(self.sla, self.incidents, self.now, lambda k, inc, d, actor: self.incident_service.emit(k, inc, actor, inc.state, d),
                                    self.users, self.incident_service.load, self.repo)
        self.report_service = ReportService(self.incidents, self.users, self.sla_engine, self.incident_service.load, self.repo)
        for u in self.repo.users(): self.users[u.id] = u
        with self.repo.transaction():
            for i in self.repo.incidents():
                if not i.response_due and i.priority in self.sla:
                    i.apply_sla_policy(self.sla[i.priority])
                    self.repo.save_inc(i)
                self.incidents[i.id] = i
        self.inapp, self.email = InAppNotifier(self.repo, self.now), EmailAdapter()
        self.bus.subscribe(AuditObserver(self.repo))
        self.bus.subscribe(NotificationObserver(self.user_service.admin_ids, notifiers or [self.inapp, extra_notifier or self.email],
                                                self.user_service.escalation_recipients))
    # strategies are held by IncidentService; exposed here so the admin can swap them
    assignment = property(lambda s: s.incident_service.assignment)
    priority = property(lambda s: s.incident_service.priority)
    def set_assignment(self, admin, strat):
        require_role(admin,"ADMIN")
        for key, cls in ASSIGNMENT_STRATEGIES.items():
            if type(strat) is cls:
                self.repo.save_cfg("assignment_strategy",key)
                break
        self.incident_service.assignment = strat

    def set_priority_strategy(self, admin, key):
        require_role(admin,"ADMIN")
        if key not in PRIORITY_STRATEGIES:
            raise ValidationError("unknown priority strategy")
        self.repo.save_cfg("priority_strategy",key)
        self.incident_service.priority = PRIORITY_STRATEGIES[key]()
    # clock
    def now(self): return self.clock.now()
    def advance(self, admin, minutes): require_role(admin, "ADMIN"); self.clock.advance(minutes)
    # accounts
    def login(self, uid, pw): return self.user_service.login(uid, pw)
    def add_user(self, admin, role, uid, name, pw, **kw): return self.user_service.add(admin, role, uid, name, pw, **kw)
    def register(self, uid, name, pw): return self.user_service.register(uid, name, pw)
    def create_user(self, admin, role, uid, name, pw, **kw): return self.user_service.create(admin, role, uid, name, pw, **kw)
    def list_users(self, admin): return self.user_service.list_profiles(admin)
    def get_user(self, uid): return self.users.get(uid)
    def technician_ids(self): return self.user_service.technician_ids()
    def update_technician(self, admin, tid, skills=None, capacity=None, available=None, experience=None):
        t = self.user_service.update_technician(admin, tid, skills=skills, capacity=capacity, available=available, experience=experience)
        self.incident_service.dispatch_pending(); return t       # orchestration: a returning technician may unblock queued incidents
    # configuration
    def add_service(self, admin, name, criticality): self.config_service.add_service(admin, name, criticality)
    def add_category(self, admin, name, keywords): self.config_service.add_category(admin, name, keywords)
    def set_sla(self, admin, prio, resp, res): self.config_service.set_sla(admin, prio, resp, res)
    # incident lifecycle
    def report(self, actor, title, desc, service, impact, urgency, users=1, category=None, **options):
        return self.incident_service.report(actor, title, desc, service, impact, urgency, users, category, **options)
    def override(self, admin, iid, reason, changes): return self.incident_service.override(admin, iid, reason, changes)
    def assign(self, admin, iid, tid): self.incident_service.assign(admin, iid, tid)
    def acknowledge(self, tech, iid): return self.incident_service.acknowledge(tech, iid)
    def start(self, tech, iid): return self.incident_service.start(tech, iid)
    def resolve(self, tech, iid, resolution): return self.incident_service.resolve(tech, iid, resolution)
    def verify(self, user, iid, feedback=None): return self.incident_service.verify(user, iid, feedback)
    def close(self, admin, iid): return self.incident_service.close(admin, iid)
    def comment(self, actor, iid, text, kind=None): return self.incident_service.comment(actor, iid, text, kind)
    def update(self, actor, iid, **fields): return self.incident_service.update(actor, iid, **fields)
    def dispatch_pending(self): return self.incident_service.dispatch_pending()
    def load(self, tid): return self.incident_service.load(tid)
    # SLA and intelligence
    def deadlines(self, inc): return self.sla_engine.deadlines(inc)
    def sla_status(self, inc): return self.sla_engine.status(inc)
    def monitor(self):
        """Run periodically: SLA warnings/violations/escalation, then dispatch queued incidents."""
        out = self.sla_engine.monitor(); self.incident_service.dispatch_pending(); return out
    def suggest_category(self, text): return self.analyzer.suggest_category(text)
    def find_related(self, text, service, exclude=None): return self.analyzer.find_related(text, service, exclude)
    def suggest_resolution(self, text, category): return self.analyzer.suggest_resolution(text, category)
    # reports
    def search(self, **f): return self.report_service.search(**f)
    def overview(self): return self.report_service.overview()
    def workload(self): return self.report_service.workload()
    def by_service(self): return self.report_service.by_service()
    def technician_workload(self, actor, tech_id=None): return self.report_service.technician_workload(actor, tech_id)
    def audit_log(self, admin, inc=None): return self.report_service.audit_log(admin, inc)

def demo():
    t = [datetime(2026, 11, 20, 10, 0)]; s = Resolv(clock=lambda: t[0])
    adm = s.add_user(None, "ADMIN", "A1", "Admin", "pw")
    for i, sk in enumerate([{"APPLICATION"}, {"NETWORK"}], 1): s.add_user(adm, "TECHNICIAN", f"T{i}", f"Tech{i}", "pw", skills=sk, exp=i)
    e = s.add_user(adm, "EMPLOYEE", "E102", "Emp", "pw"); e2 = s.add_user(adm, "EMPLOYEE", "E103", "Emp2", "pw")
    s.add_service(adm, "Payment", 4)
    print("WF1: report -> prioritise -> assign -> notify")
    i1 = s.report(e, "Payment application unavailable", "users unable to complete transactions", "Payment", 4, 4, 300)
    print(i1.id, i1.category, i1.priority, i1.state, i1.tech, "| inbox T:", s.inapp.for_user(i1.tech))
    print("WF2: SLA + resolve")
    t[0] += timedelta(minutes=2); tech = s.users[i1.tech]; s.acknowledge(tech, i1.id); s.start(tech, i1.id)
    t[0] += timedelta(minutes=20); print("SLA:", s.sla_status(i1), s.monitor())
    s.resolve(tech, i1.id, "Restarted payment gateway pod"); s.verify(e, i1.id, "thanks"); s.close(adm, i1.id)
    print("WF3: duplicates + escalation")
    i2 = s.report(e, "Payment transaction failing", "payment gateway errors", "Payment", 4, 4, 50)
    i3 = s.report(e2, "Unable to make payment", "payment transaction failing", "Payment", 4, 4, 5)
    print(i3.id, "related:", i3.related, "| suggested fix:", s.suggest_resolution("payment failing", "APPLICATION"))
    t[0] += timedelta(minutes=12); print(s.monitor())
    print(s.overview(), s.workload(), s.by_service())
    for r in s.repo.audit_log(i1.id): print(r[0][11:], r[1], r[2], r[3], r[4], "->", r[5])

if __name__ == "__main__": demo()
