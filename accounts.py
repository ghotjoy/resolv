"""Administrator-reviewed account setup and recovery."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import re
import secrets
import time

from resolv import AuthError, InvalidTransition, NotFound, UserFactory, ValidationError, require_role

COLUMNS = ("id", "kind", "uid", "name", "note", "status", "requested_at", "reviewer", "reviewed_at")
CODE_TTL = 900


class AccountAccess:
    def __init__(self, system, clock=time.time):
        self.system, self.repo, self.clock = system, system.repo, clock

    def timestamp(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(timespec="seconds")

    def request(self, kind, uid, name="", note=""):
        if kind not in ("access", "recovery") or not isinstance(uid, str):
            raise ValidationError("select an account request type and enter a user ID")
        uid = uid.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{2,20}", uid):
            raise ValidationError("invalid user ID")
        if not isinstance(name, str) or not isinstance(note, str):
            raise ValidationError("invalid request details")
        name, note = name.strip(), note.strip()
        if len(name) > 120 or (kind == "access" and not name) or len(note) > 500:
            raise ValidationError("enter a name and keep the note within 500 characters")
        existing = self.repo.user(uid)
        matches = self.repo.matching_users(uid) if existing is None else [existing]
        if kind == "recovery" and len(matches) == 1:
            uid = matches[0].id
        # Give the same response for existing, unknown, and already-requested accounts.
        self.repo._run("""INSERT OR IGNORE INTO account_requests
            (id,kind,uid,name,note,status,requested_at) VALUES(?,?,?,?,?,'PENDING',?)""",
            secrets.token_hex(16), kind, uid, name, note, self.timestamp())

    def list_requests(self, actor):
        require_role(actor, "ADMIN")
        rows = self.repo._run(f"""SELECT {','.join(COLUMNS)} FROM account_requests
            ORDER BY status != 'PENDING', requested_at DESC, rowid DESC LIMIT 150""")
        return [dict(zip(COLUMNS, row)) for row in rows]

    def review(self, actor, request_id, decision):
        require_role(actor, "ADMIN")
        if decision not in ("approve", "deny"):
            raise ValidationError("choose approve or deny")
        now = self.timestamp()
        code = None
        with self.repo.transaction():
            rows = self.repo._run("SELECT kind,uid,status FROM account_requests WHERE id=?", request_id)
            if not rows:
                raise NotFound("account request")
            kind, uid, status = rows[0]
            if status != "PENDING":
                raise InvalidTransition("this request has already been reviewed")
            if decision == "approve":
                exists = (self.repo.user(uid) is not None if kind == "recovery"
                          else bool(self.repo.matching_users(uid)))
                if exists != (kind == "recovery"):
                    raise ValidationError("the account does not match the requested action")
                code = secrets.token_urlsafe(24)
                self.repo._run("""UPDATE account_requests SET status='DENIED',token_hash=NULL,expires_at=NULL
                    WHERE uid=? AND kind=? AND status='APPROVED'""", uid, kind)
            self.repo._run("""UPDATE account_requests SET status=?,reviewer=?,reviewed_at=?,token_hash=?,expires_at=?
                WHERE id=?""", "APPROVED" if code else "DENIED", actor.id, now, self.digest(code) if code else None,
                self.clock() + CODE_TTL if code else None, request_id)
            self.repo.audit(now, actor.id, "ACCOUNT_REQUEST_REVIEWED", "-", "PENDING", "APPROVED" if code else "DENIED",
                            f"{kind} request {request_id} for {uid}")
        return {"code": code, "expires_in": CODE_TTL} if code else {"ok": True}

    @staticmethod
    def digest(code):
        return hashlib.sha256(code.encode()).hexdigest()

    def complete(self, code, password):
        if not isinstance(code, str) or not 20 <= len(code) <= 80:
            raise AuthError("invalid or expired account code")
        if not isinstance(password, str) or not 12 <= len(password) <= 256:
            raise ValidationError("password must contain 12–256 characters")
        with self.repo.transaction():
            rows = self.repo._run("""SELECT id,kind,uid,name FROM account_requests
                WHERE token_hash=? AND status='APPROVED' AND expires_at>?""", self.digest(code), self.clock())
            if not rows:
                raise AuthError("invalid or expired account code")
            request_id, kind, uid, name = rows[0]
            existing = self.repo.user(uid)
            if (existing is None if kind == "recovery" else bool(self.repo.matching_users(uid))):
                raise AuthError("invalid or expired account code")
            if existing:
                user = deepcopy(existing)
                user.set_password(password)
                self.repo.save_user(user)
            else:
                user = UserFactory.create("EMPLOYEE", uid, name, password)
                self.repo.insert_user(user)
            self.repo._run("""UPDATE account_requests SET status='COMPLETED',token_hash=NULL,expires_at=NULL
                WHERE id=?""", request_id)
            self.repo.audit(self.timestamp(), uid, "ACCOUNT_REQUEST_COMPLETED", "-", "APPROVED", "COMPLETED",
                            f"{kind} request {request_id}")
        self.system.users[uid] = user
