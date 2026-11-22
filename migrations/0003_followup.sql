CREATE INDEX IF NOT EXISTS incidents_status ON incidents(status);
CREATE INDEX IF NOT EXISTS incidents_priority ON incidents(json_extract(data,'$.priority'));
CREATE INDEX IF NOT EXISTS incidents_service ON incidents(json_extract(data,'$.service'));
CREATE INDEX IF NOT EXISTS incidents_category ON incidents(json_extract(data,'$.category'));
CREATE INDEX IF NOT EXISTS incidents_technician ON incidents(json_extract(data,'$.tech'));
CREATE INDEX IF NOT EXISTS incidents_reporter ON incidents(json_extract(data,'$.reporter'));
CREATE INDEX IF NOT EXISTS incidents_creation_day ON incidents(substr(json_extract(data,'$.created'),1,10));
CREATE INDEX IF NOT EXISTS audit_incident ON audit(inc,id);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user);

CREATE TABLE IF NOT EXISTS sla_events (
    incident TEXT NOT NULL,
    cycle TEXT NOT NULL,
    event_key TEXT NOT NULL,
    happened_at TEXT NOT NULL,
    deadline TEXT,
    minutes_overdue INTEGER NOT NULL DEFAULT 0,
    actor TEXT NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (incident, cycle, event_key)
);
CREATE TABLE IF NOT EXISTS incident_relationships (
    incident_a TEXT NOT NULL,
    incident_b TEXT NOT NULL,
    score REAL NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('RELATED','POSSIBLE_DUPLICATE')),
    decision TEXT NOT NULL DEFAULT 'PENDING' CHECK(decision IN ('PENDING','CONFIRMED','DISMISSED')),
    reviewer TEXT,
    reviewed_at TEXT,
    CHECK(incident_a < incident_b),
    PRIMARY KEY (incident_a, incident_b)
);
CREATE INDEX IF NOT EXISTS relationship_reverse ON incident_relationships(incident_b);
CREATE TABLE IF NOT EXISTS account_requests (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('access','recovery')),
    uid TEXT NOT NULL,
    name TEXT NOT NULL,
    note TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('PENDING','APPROVED','DENIED','COMPLETED')),
    requested_at TEXT NOT NULL,
    reviewer TEXT,
    reviewed_at TEXT,
    token_hash TEXT UNIQUE,
    expires_at REAL
);
CREATE UNIQUE INDEX IF NOT EXISTS account_request_pending
ON account_requests(kind,uid) WHERE status = 'PENDING';

INSERT OR IGNORE INTO incident_relationships(incident_a,incident_b,score,kind)
SELECT min(i.id, other.id), max(i.id, other.id), 0, 'RELATED'
FROM incidents i JOIN json_each(i.data,'$.related') AS related
JOIN incidents other ON other.id = related.value
WHERE i.id <> other.id;
